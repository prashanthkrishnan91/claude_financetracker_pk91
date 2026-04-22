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
  * Per-process coalescing: the ``_in_flight`` map collapses concurrent
    callers in the SAME event loop to a single Future. This is the fast
    path — no network, no serialisation.
  * Cross-worker coalescing (v4): when a ``DistributedLock`` backend is
    configured (Redis preferred, Supabase fallback) the first worker to
    dispatch a (provider, endpoint, ticker, params) key holds a TTL'd
    distributed lock; concurrent Railway workers block on that lock and
    receive the dispatcher's published result. If every backend is
    unavailable (local dev, tests) the lock degrades to an in-memory no-op
    and callers fall back to the process-local behaviour.
  * TTL cleanup after completion — when a Future completes it's removed
    from the in-flight map so the next request goes to the live provider.
  * Failure semantics — when the upstream raises, ALL waiters see the same
    exception. The caller decides whether to fall back to cached data.

Invariant:
  "At most ONE external API call per (provider, endpoint, ticker, params)
  per time window across ALL workers." A violation is logged as
  ``CRITICAL_DUPLICATE_CALL`` with the offending ``worker_id``/``provider``/
  ``key`` so operators can correlate log lines with upstream billing.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from .distributed_lock import (
    DEFAULT_LOCK_TTL_S,
    DEFAULT_WAIT_TIMEOUT_S,
    DistributedLock,
    get_distributed_lock,
    get_worker_id,
)

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
    """In-flight external-call deduplicator — process-local + cross-worker.

    Thread-safety model: single asyncio event loop per process. The master
    ``_map_lock`` guards the dict mutation; individual awaits on
    ``entry.future`` happen OUTSIDE that lock so coalesced callers don't
    serialize.

    When a ``DistributedLock`` is injected (or the module default singleton
    is used), dispatcher coroutines first acquire a cross-worker lock on the
    same key so a second Railway worker doesn't duplicate the external call.
    The distributed lock auto-expires (TTL ~45s by default) to bound the
    dispatcher-crashed blast radius.
    """

    def __init__(
        self, distributed_lock: Optional[DistributedLock] = None
    ) -> None:
        self._in_flight: dict[str, _InFlightEntry] = {}
        self._map_lock = asyncio.Lock()
        # Minute-window tracker: {(provider, ticker, minute_bucket): count}.
        # Incremented when a NEW external call is dispatched. Any count > 1
        # in the same bucket is logged as a duplicate-call violation.
        self._minute_counts: dict[tuple[str, str, int], int] = {}
        self._distributed_lock = distributed_lock
        self.coalesced = 0
        self.dispatched = 0
        self.violations = 0
        # Cross-worker dispatch/await counters — operators use these to
        # confirm that the distributed lock is actually collapsing calls.
        self.distributed_awaited = 0
        self.distributed_dispatched = 0

    async def coalesce(
        self,
        key: str,
        factory: Callable[[], Awaitable[Any]],
        *,
        provider: Optional[str] = None,
        ticker: Optional[str] = None,
        distributed: bool = False,
        lock_ttl_s: float = DEFAULT_LOCK_TTL_S,
        wait_s: float = DEFAULT_WAIT_TIMEOUT_S,
    ) -> Any:
        """Run ``factory()`` at most once per ``key`` while concurrent.

        The first caller for a key dispatches; subsequent callers await the
        same Future. Result (or exception) is shared across all waiters.

        ``provider`` and ``ticker`` are optional diagnostic tags used for
        the minute-window invariant logging. When both are provided and a
        new external call is dispatched, the coalescer records the hit in
        a 60s minute bucket and logs any duplicate within the same bucket.

        When ``distributed=True`` the dispatcher wraps ``factory`` in a
        cross-worker lock (Redis or Supabase). Concurrent Railway workers
        see the lock and await the dispatcher's published result so the
        upstream API sees exactly one call per key per time window across
        the entire deployment.
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
            dispatch_fn = factory
            if distributed:
                dispatch_fn = self._wrap_distributed(
                    key, factory,
                    provider=provider, ticker=ticker,
                    ttl_s=lock_ttl_s, wait_s=wait_s,
                )
            try:
                value = await dispatch_fn()
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
                # Drain any unconsumed exception on the future so asyncio
                # doesn't emit "Future exception was never retrieved"
                # warnings when no waiters existed. ``.exception()`` is
                # non-destructive for consumers that already awaited.
                if future.done() and not future.cancelled():
                    try:
                        future.exception()
                    except BaseException:
                        pass

        # Waiter path — the dispatcher resolves (or raises) for us.
        return await future

    def _wrap_distributed(
        self,
        key: str,
        factory: Callable[[], Awaitable[Any]],
        *,
        provider: Optional[str],
        ticker: Optional[str],
        ttl_s: float,
        wait_s: float,
    ) -> Callable[[], Awaitable[Any]]:
        """Return a no-arg coroutine that runs ``factory`` behind the
        cross-worker lock.

        Result-propagation contract:
          * ``source='dispatched'`` — this worker ran the factory; publish
            the real value to the shared store and return it locally.
          * ``source='awaited'``    — another worker ran the factory;
            return the shared-store value as-if we'd fetched it.
          * ``source='local'``      — backend unavailable / wait timed out;
            we ran the factory locally as a safety net. Log a
            ``CRITICAL_DUPLICATE_CALL`` because this path can let two
            workers hit the upstream if it happens alongside a real
            dispatch elsewhere.
          * ``source='error'``      — factory raised; convert the published
            error into an exception for the local waiter so the existing
            cache-fallback path kicks in.
        """
        lock = self._distributed_lock or get_distributed_lock()

        async def _runner() -> Any:
            result = await lock.acquire_or_wait(
                key, factory, ttl_s=ttl_s, wait_s=wait_s,
            )
            if result.source == "dispatched":
                self.distributed_dispatched += 1
            elif result.source == "awaited":
                self.distributed_awaited += 1
            elif result.source == "local":
                self.violations += 1
                logger.warning(
                    "CRITICAL_DUPLICATE_CALL worker=%s provider=%s ticker=%s "
                    "key=%s — distributed lock unavailable, ran locally "
                    "(risk of cross-worker duplicate upstream hit)",
                    get_worker_id(), provider or "?", ticker or "?", key,
                )
            if result.error:
                # Reraise with the same string the dispatcher saw so the
                # caller's existing cache-fallback path engages uniformly.
                raise RuntimeError(result.error)
            return result.value

        return _runner

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
            "distributed_dispatched": self.distributed_dispatched,
            "distributed_awaited": self.distributed_awaited,
        }

    def reset(self) -> None:
        """Test hook — clear counters and in-flight map."""
        self._in_flight.clear()
        self._minute_counts.clear()
        self.coalesced = 0
        self.dispatched = 0
        self.violations = 0
        self.distributed_awaited = 0
        self.distributed_dispatched = 0


# ── Module-level singleton ─────────────────────────────────────────────────

_SINGLETON: Optional[RequestCoalescer] = None


def get_request_coalescer() -> RequestCoalescer:
    """Return the process-wide coalescer instance (lazy)."""
    global _SINGLETON
    if _SINGLETON is None:
        _SINGLETON = RequestCoalescer()
    return _SINGLETON
