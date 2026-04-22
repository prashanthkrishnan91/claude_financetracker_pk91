"""DistributedLock tests — cross-worker coalescing invariants.

Validates v4 distributed-correctness lock:
  * In-memory backend collapses concurrent callers to ONE factory invocation
    regardless of how many "workers" (asyncio tasks) race the lock.
  * Loser workers await the dispatcher's published result rather than
    re-running the factory — preserves the "at most one external call per
    key per time window" invariant even across Railway workers.
  * Dispatcher exceptions propagate as structured error payloads so losers
    raise locally and callers see the same failure instead of duplicating
    the upstream call.
  * Wait timeouts fall back to local dispatch — the lock is an optimisation,
    never a hard dependency that can stall the pipeline.
  * Lock auto-expiry: an orphaned row (dispatcher crashed) doesn't deadlock
    the next caller.
"""

from __future__ import annotations

import asyncio

import pytest


@pytest.mark.asyncio
async def test_in_memory_collapses_concurrent_workers():
    """5 concurrent 'workers' on the same key → ONE factory call."""
    from app.services.market_data.distributed_lock import (
        DistributedLock,
        InMemoryLockBackend,
    )

    lock = DistributedLock(backends=[InMemoryLockBackend()])
    calls = 0

    async def factory():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return "value"

    results = await asyncio.gather(
        *[lock.acquire_or_wait("k1", factory, ttl_s=5.0, wait_s=2.0) for _ in range(5)]
    )

    # Exactly one dispatcher ran the factory; the other 4 awaited.
    assert calls == 1
    assert all(r.value == "value" for r in results)
    dispatched = [r for r in results if r.source == "dispatched"]
    awaited = [r for r in results if r.source == "awaited"]
    assert len(dispatched) == 1
    assert len(awaited) == 4


@pytest.mark.asyncio
async def test_factory_error_is_published_and_raised_by_waiters():
    from app.services.market_data.distributed_lock import (
        DistributedLock,
        InMemoryLockBackend,
    )

    lock = DistributedLock(backends=[InMemoryLockBackend()])

    async def boom():
        await asyncio.sleep(0.01)
        raise RuntimeError("upstream down")

    results = await asyncio.gather(
        *[lock.acquire_or_wait("err", boom, ttl_s=5.0, wait_s=2.0) for _ in range(3)]
    )

    # Every caller sees the same error payload — dispatcher's error was
    # published and awaited by the losers.
    assert all(r.error for r in results)
    sources = {r.source for r in results}
    assert "error" in sources  # dispatcher
    assert "awaited" in sources or len(results) == 1  # waiters (if any)


@pytest.mark.asyncio
async def test_wait_timeout_falls_back_to_local_dispatch(monkeypatch):
    """If the dispatcher never publishes, a waiter falls back to local execution.

    Doesn't BLOCK the pipeline — source=local tells the caller a
    duplicate-call risk occurred so operators can investigate.
    """
    from app.services.market_data.distributed_lock import (
        DEFAULT_LOCK_TTL_S,
        DistributedLock,
        InMemoryLockBackend,
    )

    backend = InMemoryLockBackend()
    lock = DistributedLock(backends=[backend])

    # Manually acquire the lock and NEVER publish — simulates a crashed
    # dispatcher. The next caller should time out waiting then run locally.
    assert await backend.try_acquire("orphan", ttl_s=DEFAULT_LOCK_TTL_S)

    ran_local = False

    async def fallback_factory():
        nonlocal ran_local
        ran_local = True
        return "local"

    result = await lock.acquire_or_wait(
        "orphan", fallback_factory, ttl_s=DEFAULT_LOCK_TTL_S, wait_s=0.2,
    )
    assert ran_local is True
    assert result.value == "local"
    assert result.source == "local"


@pytest.mark.asyncio
async def test_expired_lock_is_re_acquirable():
    """Auto-expiry: after TTL, a new caller wins the lock without deadlocking."""
    from app.services.market_data.distributed_lock import (
        DistributedLock,
        InMemoryLockBackend,
    )

    backend = InMemoryLockBackend()
    lock = DistributedLock(backends=[backend])

    # Acquire with a tiny TTL and never publish (simulate stalled dispatcher).
    assert await backend.try_acquire("reacq", ttl_s=0.05)
    await asyncio.sleep(0.1)

    dispatched = 0

    async def factory():
        nonlocal dispatched
        dispatched += 1
        return "fresh"

    result = await lock.acquire_or_wait(
        "reacq", factory, ttl_s=1.0, wait_s=0.5,
    )
    assert result.value == "fresh"
    # The expired lock let the next caller dispatch fresh.
    assert result.source in {"dispatched", "local"}
    assert dispatched == 1


@pytest.mark.asyncio
async def test_coalescer_distributed_mode_dedupes_local():
    """The coalescer's distributed path still dedupes within one process."""
    from app.services.market_data.distributed_lock import (
        DistributedLock,
        InMemoryLockBackend,
    )
    from app.services.market_data.request_coalescer import (
        RequestCoalescer,
        make_key,
    )

    lock = DistributedLock(backends=[InMemoryLockBackend()])
    coalescer = RequestCoalescer(distributed_lock=lock)

    calls = 0

    async def factory():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return "x"

    key = make_key("finnhub", "company-news", "AAPL")
    out = await asyncio.gather(
        *[
            coalescer.coalesce(
                key, factory, provider="finnhub", ticker="AAPL",
                distributed=True,
            )
            for _ in range(8)
        ]
    )
    assert all(r == "x" for r in out)
    assert calls == 1
    stats = coalescer.stats()
    # Local coalescer collapsed 8→1; distributed_dispatched records the
    # cross-worker entry for the winning call.
    assert stats["dispatched"] == 1
    assert stats["coalesced"] == 7
    assert stats["distributed_dispatched"] == 1


@pytest.mark.asyncio
async def test_no_backend_falls_back_to_local_with_critical_log(caplog):
    """When no backend is available, the coalescer logs CRITICAL_DUPLICATE_CALL."""
    import logging

    from app.services.market_data.distributed_lock import DistributedLock
    from app.services.market_data.request_coalescer import (
        RequestCoalescer,
        make_key,
    )

    # Backend list that always fails to acquire — simulates a fully-offline
    # distributed lock (e.g. Redis + Supabase both unreachable).
    class _DeadBackend:
        def is_available(self) -> bool:
            return True
        async def try_acquire(self, key, *, ttl_s):
            return False
        async def publish_result(self, key, value, *, error, ttl_s):
            return None
        async def wait_for_result(self, key, *, timeout_s):
            return None  # always times out
        async def release(self, key):
            return None

    lock = DistributedLock(backends=[_DeadBackend()])
    coalescer = RequestCoalescer(distributed_lock=lock)
    coalescer.reset()

    async def factory():
        return "fallback"

    key = make_key("p", "e", "AAPL")
    with caplog.at_level(logging.WARNING):
        result = await coalescer.coalesce(
            key, factory, provider="finnhub", ticker="AAPL",
            distributed=True, wait_s=0.05,
        )
    assert result == "fallback"
    assert coalescer.stats()["violations"] == 1
    assert any(
        "CRITICAL_DUPLICATE_CALL" in rec.message for rec in caplog.records
    )
