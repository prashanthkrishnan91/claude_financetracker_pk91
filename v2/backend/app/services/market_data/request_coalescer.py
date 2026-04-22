"""Distributed request coalescer — deduplicate in-flight external API calls.

Problem solved:
  Concurrent workers (or concurrent requests in the same process) often fan
  out the same upstream call — e.g. two users triggering a recommendation
  run simultaneously both ask Finnhub for AAPL news. The ``MarketCache``
  already collapses calls for the same exact cache key, but it's keyed on
  data kind + ticker. This coalescer is finer-grained: it keys on the
  external-call contract ``(provider, endpoint, ticker, params_hash)`` so
  two callers hitting the same URL with the same params both wait on a
  SINGLE in-flight Future.

Scope:
  * Per-process singleton (``get_request_coalescer``). Each Railway worker
    still makes its own in-flight set — true cross-worker coalescing would
    require Redis with SETNX-style locks. For workers backed by the same
    ``MarketCache`` cache the post-fetch cache write + sub-minute TTL does
    99% of cross-worker dedupe.
  * TTL cleanup after completion — when a Future completes it's removed
    from the in-flight map so the next request goes to the live provider.
  * Failure semantics — when the upstream raises, ALL waiters see the same
    exception. The caller decides whether to fall back to cached data.

Invariant:
  For a given ``(provider, endpoint, ticker, params_hash)`` tuple, at most
  ONE external call is in flight per process at any time. Log ``violation``
  when a concurrent call would have slipped through (shouldn't happen —
  this is a defensive assertion for the global "no duplicate external
  calls" invariant in the stability spec).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)


def _hash_params(params: Optional[dict[str, Any]]) -> str:
    """Deterministic short hash of the params dict.

    Uses ``json.dumps(sort_keys=True)`` so the same params always hash to
    the same bucket regardless of insertion order. Unhashable/nested values
    fall back to ``str()`` via ``default=str``.
    """
    if not params:
        return "-"
    blob = json.dumps(params, sort_keys=True, default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]


def make_key(
    provider: str,
    endpoint: str,
    ticker: str,
    params: Optional[dict[str, Any]] = None,
) -> str:
    """Build the coalescer key for an external-API contract."""
    return f"{provider}:{endpoint}:{ticker.upper()}:{_hash_params(params)}"


# Invariant-tracking: minute-window dedup log. If the same
# (provider, ticker, minute) tuple crosses the coalescer twice as a *new*
# external call (i.e. not as a coalesced waiter) we log it — that's a
# potential duplicate-call violation the UI team flagged as SEV-2.
_MINUTE_WINDOW_S = 60.0


@dataclass
class _InFlightEntry:
    future: asyncio.Future
    started_at: float = field(default_factory=time.monotonic)


class RequestCoalescer:
    """Process-wide in-flight external-call deduplicator.

    Thread-safety model: single asyncio event loop per process. The master
    ``_map_lock`` guards the dict mutation; individual awaits on
    ``entry.future`` happen OUTSIDE that lock so coalesced callers don't
    serialize.
    """

    def __init__(self) -> None:
        self._in_flight: dict[str, _InFlightEntry] = {}
        self._map_lock = asyncio.Lock()
        # Minute-window tracker: {(provider, ticker, minute_bucket): count}.
        # Incremented when a NEW external call is dispatched. Any count > 1
        # in the same bucket is logged as a duplicate-call violation.
        self._minute_counts: dict[tuple[str, str, int], int] = {}
        self.coalesced = 0
        self.dispatched = 0
        self.violations = 0

    async def coalesce(
        self,
        key: str,
        factory: Callable[[], Awaitable[Any]],
        *,
        provider: Optional[str] = None,
        ticker: Optional[str] = None,
    ) -> Any:
        """Run ``factory()`` at most once per ``key`` while concurrent.

        The first caller for a key dispatches; subsequent callers await the
        same Future. Result (or exception) is shared across all waiters.

        ``provider`` and ``ticker`` are optional diagnostic tags used for
        the minute-window invariant logging. When both are provided and a
        new external call is dispatched, the coalescer records the hit in
        a 60s minute bucket and logs any duplicate within the same bucket.
        """
        async with self._map_lock:
            entry = self._in_flight.get(key)
            if entry is not None and not entry.future.done():
                self.coalesced += 1
                logger.debug("coalesce: waiter joined in-flight key=%s", key)
                future = entry.future
            else:
                # No in-flight — dispatch. The future is populated inside
                # the lock so a second concurrent caller sees it without
                # racing us to dispatch a duplicate.
                loop = asyncio.get_event_loop()
                future = loop.create_future()
                self._in_flight[key] = _InFlightEntry(future=future)
                entry = None  # signal "we're the dispatcher"

                if provider and ticker:
                    self._record_minute_window(provider, ticker, key)

        if entry is None:
            # Dispatcher path — run the factory and resolve the future.
            self.dispatched += 1
            try:
                value = await factory()
                if not future.done():
                    future.set_result(value)
                return value
            except BaseException as exc:
                if not future.done():
                    future.set_exception(exc)
                raise
            finally:
                # TTL cleanup — remove the entry once settled so the NEXT
                # caller gets a fresh external call (the cache layer owns
                # the result-level TTL; the coalescer only dedupes
                # in-flight, not settled, calls).
                async with self._map_lock:
                    current = self._in_flight.get(key)
                    if current is not None and current.future is future:
                        self._in_flight.pop(key, None)

        # Waiter path — the dispatcher resolves (or raises) for us.
        return await future

    def _record_minute_window(
        self, provider: str, ticker: str, key: str
    ) -> None:
        """Track dispatches per-minute for the duplicate-call invariant.

        Called synchronously under the map lock so counter increments
        never race. Any bucket count > 1 is a violation — the coalescer
        and the cache layer together should keep per-minute dispatches
        to at most 1 for the same (provider, ticker) pair.
        """
        bucket = int(time.monotonic() // _MINUTE_WINDOW_S)
        tag = (provider, ticker.upper(), bucket)
        count = self._minute_counts.get(tag, 0) + 1
        self._minute_counts[tag] = count
        # Garbage-collect old buckets (> 5 minutes stale) so the dict doesn't
        # grow unbounded in long-lived workers.
        if len(self._minute_counts) > 512:
            cutoff = bucket - 5
            self._minute_counts = {
                t: c for t, c in self._minute_counts.items() if t[2] >= cutoff
            }
        if count > 1:
            self.violations += 1
            logger.warning(
                "duplicate-call invariant violation: provider=%s ticker=%s "
                "minute_bucket=%d count=%d key=%s",
                provider, ticker.upper(), bucket, count, key,
            )

    def stats(self) -> dict[str, int]:
        return {
            "in_flight": len(self._in_flight),
            "coalesced": self.coalesced,
            "dispatched": self.dispatched,
            "violations": self.violations,
        }

    def reset(self) -> None:
        """Test hook — clear counters and in-flight map."""
        self._in_flight.clear()
        self._minute_counts.clear()
        self.coalesced = 0
        self.dispatched = 0
        self.violations = 0


# ── Module-level singleton ─────────────────────────────────────────────────

_SINGLETON: Optional[RequestCoalescer] = None


def get_request_coalescer() -> RequestCoalescer:
    """Return the process-wide coalescer instance (lazy)."""
    global _SINGLETON
    if _SINGLETON is None:
        _SINGLETON = RequestCoalescer()
    return _SINGLETON
