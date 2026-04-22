"""System-wide operating mode — NORMAL / DEGRADED / LIGHTWEIGHT.

Contract (see v4 distributed-correctness spec, task #3):

  NORMAL        — every provider healthy; full pipeline runs.
  DEGRADED      — one provider's breaker has been open beyond the escalate
                  window; skip Polygon entirely, halve ticker batch sizes,
                  prefer cached data. LLM still runs but with a data_quality
                  hint that it's operating on thinner inputs.
  LIGHTWEIGHT   — two or more providers are open (or Polygon+Finnhub are
                  both flaky): no new external calls, LLM runs on cached
                  snapshots only, IO returns stale-but-valid data. Orchestrator
                  forces strict prompting (no speculation).

Derivation is cached for ``_MODE_CACHE_S`` seconds so a burst of lookups from
concurrent fetchers doesn't hammer Supabase or the provider breakers. The
cache invalidates automatically when any breaker state changes via
``invalidate_cache()`` — data_sources calls that on ``record_failure`` /
``record_success`` so mode transitions are near-instant.

Optional Supabase sink (``system_health`` single-row table) lets ops inspect
the current mode at any time. When the table or env is missing the manager
falls back to pure in-memory state without raising.
"""

from __future__ import annotations

import enum
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


class SystemMode(str, enum.Enum):
    NORMAL = "NORMAL"
    DEGRADED = "DEGRADED"
    LIGHTWEIGHT = "LIGHTWEIGHT"


# Cache the derived mode for this many seconds between status lookups.
# Low enough that an operator toggling a provider sees the change in
# < 15 seconds; high enough that the io_layer can query it on every request
# without adding real cost.
_MODE_CACHE_S = 10.0

# Number of providers that must be open (status != ok) to bump from
# DEGRADED → LIGHTWEIGHT. Aligns with the spec's "multiple providers OPEN"
# rule; cryptos often trip CoinGecko alone without needing the pipeline to
# go fully cache-only.
_LIGHTWEIGHT_OPEN_THRESHOLD = 2

# Providers whose failure flips the system into DEGRADED on its own.
# Polygon is enrichment-only so an open polygon breaker triggers the
# "skip Polygon" optimisation but doesn't force full lightweight mode.
_CRITICAL_PROVIDERS = {"finnhub", "coingecko"}


@dataclass
class SystemModeState:
    mode: SystemMode = SystemMode.NORMAL
    reason: str = "healthy"
    open_providers: list[str] = field(default_factory=list)
    provider_status: dict[str, str] = field(default_factory=dict)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "reason": self.reason,
            "open_providers": list(self.open_providers),
            "provider_status": dict(self.provider_status),
            "updated_at": self.updated_at,
        }


class SystemModeManager:
    """Derive + cache + publish the current system mode.

    ``current()`` is the hot path — call it on every IO decision. It serves
    the cached mode when fresh; otherwise recomputes from
    ``data_sources.get_provider_status()`` + publishes to the
    ``system_health`` Supabase table best-effort.
    """

    TABLE = "system_health"

    def __init__(
        self,
        *,
        status_provider: Optional[Callable[[], dict[str, str]]] = None,
    ) -> None:
        # Injected provider lookup keeps the module test-friendly — unit
        # tests can pass a stub without wiring the full data_sources stack.
        self._status_provider = status_provider
        self._lock = threading.Lock()
        self._cached: Optional[SystemModeState] = None
        self._last_compute_at: float = 0.0
        self._sink_available: Optional[bool] = None
        self._last_published_mode: Optional[SystemMode] = None

    # ── Public API ────────────────────────────────────────────────────────

    def current(self, *, force_refresh: bool = False) -> SystemModeState:
        now = time.time()
        if (
            not force_refresh
            and self._cached is not None
            and now - self._last_compute_at < _MODE_CACHE_S
        ):
            return self._cached

        status_map = self._lookup_status()
        state = self._derive(status_map)

        with self._lock:
            self._cached = state
            self._last_compute_at = now

        # Publish transitions only — avoids hammering the DB when mode is
        # stable (which is the common case in healthy operation).
        if state.mode != self._last_published_mode:
            self._publish(state)
            self._last_published_mode = state.mode

        return state

    def invalidate_cache(self) -> None:
        """Force a recompute on the next ``current()`` call."""
        with self._lock:
            self._cached = None
            self._last_compute_at = 0.0

    def should_skip_polygon(self) -> bool:
        """Polygon is skipped in DEGRADED + LIGHTWEIGHT modes."""
        return self.current().mode in {SystemMode.DEGRADED, SystemMode.LIGHTWEIGHT}

    def should_skip_external_calls(self) -> bool:
        """LIGHTWEIGHT disables every external API call."""
        return self.current().mode == SystemMode.LIGHTWEIGHT

    def batch_size_factor(self) -> float:
        """Multiplier applied to ticker batch sizes.

        ``1.0`` in NORMAL, ``0.5`` in DEGRADED (halve per spec), ``0.25``
        in LIGHTWEIGHT (quartered — even cache refreshes should be small
        during a full degradation).
        """
        mode = self.current().mode
        if mode == SystemMode.DEGRADED:
            return 0.5
        if mode == SystemMode.LIGHTWEIGHT:
            return 0.25
        return 1.0

    # ── Internals ─────────────────────────────────────────────────────────

    def _lookup_status(self) -> dict[str, str]:
        if self._status_provider is not None:
            try:
                return dict(self._status_provider() or {})
            except Exception as exc:  # noqa: BLE001
                logger.debug("system_mode status provider failed: %s", exc)
                return {}
        # Default lookup — import lazily so unit tests can swap the injector
        # without pulling in the full data_sources module.
        try:
            from ..agents import data_sources as ds

            return ds.get_provider_status()
        except Exception as exc:  # noqa: BLE001
            logger.debug("system_mode default status lookup failed: %s", exc)
            return {}

    def _derive(self, status_map: dict[str, str]) -> SystemModeState:
        open_providers: list[str] = []
        for name, status in status_map.items():
            if not status or status == "ok":
                continue
            # Only track providers that materially change pipeline behaviour
            # — critical (finnhub/coingecko) or Polygon's own skip-toggle.
            if name in _CRITICAL_PROVIDERS or name == "polygon":
                open_providers.append(name)
        critical_open = [
            name for name in open_providers if name in _CRITICAL_PROVIDERS
        ]

        if len(critical_open) >= _LIGHTWEIGHT_OPEN_THRESHOLD:
            mode = SystemMode.LIGHTWEIGHT
            reason = (
                f"{len(critical_open)} critical providers degraded — "
                "skipping external calls, running on cached snapshots only"
            )
        elif critical_open or "polygon" in open_providers:
            mode = SystemMode.DEGRADED
            reason = (
                f"providers degraded: {','.join(open_providers)} — "
                "skipping Polygon, reduced batch size, cached-first fetches"
            )
        else:
            mode = SystemMode.NORMAL
            reason = "healthy"

        return SystemModeState(
            mode=mode,
            reason=reason,
            open_providers=sorted(open_providers),
            provider_status=dict(status_map),
            updated_at=time.time(),
        )

    def _publish(self, state: SystemModeState) -> None:
        """Upsert the single-row ``system_health`` table.

        Best-effort — any DB error silently disables publishing for the
        remainder of the process lifetime so a flaky table can't degrade
        the orchestrator.
        """
        if self._sink_available is False:
            return
        if os.getenv("SYSTEM_HEALTH_SINK", "auto").lower() in {"off", "disabled"}:
            self._sink_available = False
            return
        if not (os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_SERVICE_ROLE_KEY")):
            self._sink_available = False
            return
        try:
            from ...database import get_supabase_client

            client = get_supabase_client()
            client.table(self.TABLE).upsert({
                "id": 1,  # single-row table
                "mode": state.mode.value,
                "reason": state.reason[:400],
                "open_providers": state.open_providers,
                "provider_status": state.provider_status,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }, on_conflict="id").execute()
            self._sink_available = True
            logger.info(
                "system_mode transition → %s (reason=%s open=%s)",
                state.mode.value, state.reason, state.open_providers,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("system_health publish skipped: %s", exc)
            self._sink_available = False


# ── Module singleton ──────────────────────────────────────────────────────────

_SINGLETON: Optional[SystemModeManager] = None


def get_system_mode_manager() -> SystemModeManager:
    """Return the process-wide system-mode manager (lazy)."""
    global _SINGLETON
    if _SINGLETON is None:
        _SINGLETON = SystemModeManager()
    return _SINGLETON


def _set_manager_for_testing(mgr: Optional[SystemModeManager]) -> None:
    """Test hook — swap the module singleton (pass ``None`` to reset)."""
    global _SINGLETON
    _SINGLETON = mgr
