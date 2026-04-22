"""Market cache TTL + request-coalescing tests.

Validates Portfolio Engine v2 cache invariants:
  * ``get_or_fetch`` serves the cached value on hit
  * Concurrent callers for the same key collapse to a single factory call
  * Entries older than TTL are treated as misses
  * Factory exceptions propagate (caller decides fallback)
"""

from __future__ import annotations

import asyncio
import time

import pytest


@pytest.mark.asyncio
async def test_get_or_fetch_caches_value(monkeypatch):
    from app.services.cache.market_cache import MarketCache

    cache = MarketCache()
    calls = 0

    async def factory():
        nonlocal calls
        calls += 1
        return 42

    v1 = await cache.get_or_fetch("price:AAPL", factory, ttl=60)
    v2 = await cache.get_or_fetch("price:AAPL", factory, ttl=60)

    assert v1 == v2 == 42
    assert calls == 1  # second call served from cache


@pytest.mark.asyncio
async def test_expired_entry_refetches(monkeypatch):
    from app.services.cache.market_cache import MarketCache

    cache = MarketCache()
    calls = 0

    async def factory():
        nonlocal calls
        calls += 1
        return calls

    await cache.get_or_fetch("price:TSLA", factory, ttl=0.01)
    await asyncio.sleep(0.05)
    await cache.get_or_fetch("price:TSLA", factory, ttl=0.01)

    assert calls == 2


@pytest.mark.asyncio
async def test_concurrent_callers_coalesce():
    """10 parallel callers for the same key → 1 upstream call."""
    from app.services.cache.market_cache import MarketCache

    cache = MarketCache()
    calls = 0

    async def slow_factory():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.02)
        return "value"

    results = await asyncio.gather(
        *[cache.get_or_fetch("news:AAPL", slow_factory, ttl=10) for _ in range(10)]
    )

    assert all(r == "value" for r in results)
    # All concurrent callers must collapse to exactly one factory invocation.
    assert calls == 1


@pytest.mark.asyncio
async def test_factory_exception_propagates_and_does_not_cache():
    from app.services.cache.market_cache import MarketCache

    cache = MarketCache()

    async def bad_factory():
        raise RuntimeError("upstream down")

    with pytest.raises(RuntimeError):
        await cache.get_or_fetch("price:FAIL", bad_factory, ttl=60)

    # Next call should still try — nothing was cached.
    called = False

    async def recovers():
        nonlocal called
        called = True
        return "ok"

    assert await cache.get_or_fetch("price:FAIL", recovers, ttl=60) == "ok"
    assert called is True


@pytest.mark.asyncio
async def test_none_value_is_not_cached():
    """An empty/None fetch should not poison the cache for the TTL window."""
    from app.services.cache.market_cache import MarketCache

    cache = MarketCache()
    calls = 0

    async def empty_then_value():
        nonlocal calls
        calls += 1
        return None if calls == 1 else "got it"

    v1 = await cache.get_or_fetch("price:X", empty_then_value, ttl=60)
    v2 = await cache.get_or_fetch("price:X", empty_then_value, ttl=60)

    assert v1 is None
    assert v2 == "got it"
    assert calls == 2


@pytest.mark.asyncio
async def test_default_ttl_per_family():
    from app.services.cache.market_cache import MarketCache, DEFAULT_TTL_S

    assert MarketCache.default_ttl_for("price:AAPL") == DEFAULT_TTL_S["price"]
    assert MarketCache.default_ttl_for("news:AAPL") == DEFAULT_TTL_S["news"]
    assert MarketCache.default_ttl_for("macro:snapshot") == DEFAULT_TTL_S["macro"]


def test_singleton_is_process_wide():
    from app.services.cache.market_cache import get_market_cache

    assert get_market_cache() is get_market_cache()
