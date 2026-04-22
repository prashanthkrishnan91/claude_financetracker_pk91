"""Request coalescer tests — in-flight dedup + duplicate-call invariant.

Validates v3 stability-layer guarantees:
  * Concurrent callers for the same (provider, endpoint, ticker, params)
    collapse to a single upstream factory call.
  * Distinct keys are NOT coalesced — they run independently.
  * After settlement the in-flight entry is cleaned up so the NEXT call
    dispatches freshly (the cache layer owns result-level TTLs).
  * Factory exceptions propagate to all waiters.
  * Same-minute duplicates log as invariant violations.
"""

from __future__ import annotations

import asyncio

import pytest


@pytest.mark.asyncio
async def test_coalesce_collapses_concurrent_callers():
    from app.services.market_data.request_coalescer import RequestCoalescer, make_key

    rc = RequestCoalescer()
    calls = 0

    async def slow_factory():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.02)
        return "value"

    key = make_key("finnhub", "company-news", "AAPL")
    results = await asyncio.gather(
        *[
            rc.coalesce(key, slow_factory, provider="finnhub", ticker="AAPL")
            for _ in range(10)
        ]
    )

    assert all(r == "value" for r in results)
    # 10 concurrent callers → 1 factory invocation.
    assert calls == 1
    stats = rc.stats()
    assert stats["dispatched"] == 1
    assert stats["coalesced"] == 9


@pytest.mark.asyncio
async def test_distinct_keys_are_not_coalesced():
    from app.services.market_data.request_coalescer import RequestCoalescer, make_key

    rc = RequestCoalescer()
    calls = {"a": 0, "b": 0}

    async def factory_a():
        calls["a"] += 1
        return "A"

    async def factory_b():
        calls["b"] += 1
        return "B"

    res_a, res_b = await asyncio.gather(
        rc.coalesce(make_key("p", "e", "AAPL"), factory_a, provider="p", ticker="AAPL"),
        rc.coalesce(make_key("p", "e", "TSLA"), factory_b, provider="p", ticker="TSLA"),
    )
    assert res_a == "A"
    assert res_b == "B"
    assert calls == {"a": 1, "b": 1}


@pytest.mark.asyncio
async def test_entry_is_cleaned_up_after_completion():
    """A second call after the first completes must dispatch a fresh factory."""
    from app.services.market_data.request_coalescer import RequestCoalescer, make_key

    rc = RequestCoalescer()
    calls = 0

    async def factory():
        nonlocal calls
        calls += 1
        return calls

    key = make_key("p", "e", "AAPL")
    r1 = await rc.coalesce(key, factory, provider="p", ticker="AAPL")
    r2 = await rc.coalesce(key, factory, provider="p", ticker="AAPL")

    # Each completed call frees the in-flight slot → next caller dispatches.
    assert r1 == 1
    assert r2 == 2
    assert calls == 2


@pytest.mark.asyncio
async def test_factory_exception_propagates_to_all_waiters():
    from app.services.market_data.request_coalescer import RequestCoalescer, make_key

    rc = RequestCoalescer()

    async def boom():
        await asyncio.sleep(0.01)
        raise RuntimeError("upstream down")

    key = make_key("p", "e", "AAPL")

    async def call():
        try:
            await rc.coalesce(key, boom, provider="p", ticker="AAPL")
        except RuntimeError as e:
            return str(e)
        return None

    results = await asyncio.gather(*[call() for _ in range(5)])
    # All 5 concurrent waiters see the same propagated exception.
    assert all(r == "upstream down" for r in results)


@pytest.mark.asyncio
async def test_same_minute_bucket_logs_violation(caplog):
    """Sequential calls within the same minute bucket register as violations."""
    from app.services.market_data.request_coalescer import RequestCoalescer, make_key
    import logging

    rc = RequestCoalescer()

    async def factory():
        return "ok"

    with caplog.at_level(logging.WARNING):
        key = make_key("p", "e", "AAPL")
        await rc.coalesce(key, factory, provider="coingecko", ticker="BTC")
        await rc.coalesce(key, factory, provider="coingecko", ticker="BTC")

    # Second dispatch in the same 60s bucket should log a violation.
    assert rc.stats()["violations"] >= 1
    assert any("duplicate-call invariant violation" in r.message for r in caplog.records)


def test_make_key_uses_stable_params_hash():
    from app.services.market_data.request_coalescer import make_key

    k1 = make_key("finnhub", "company-news", "AAPL", {"from": "a", "to": "b"})
    k2 = make_key("finnhub", "company-news", "AAPL", {"to": "b", "from": "a"})
    assert k1 == k2  # param order must not change the key
    k3 = make_key("finnhub", "company-news", "AAPL", {"from": "a", "to": "c"})
    assert k1 != k3  # different params → different key
    k4 = make_key("finnhub", "company-news", "aapl", None)
    k5 = make_key("finnhub", "company-news", "AAPL", None)
    assert k4 == k5  # ticker casing normalised


def test_singleton_is_process_wide():
    from app.services.market_data.request_coalescer import get_request_coalescer

    assert get_request_coalescer() is get_request_coalescer()
