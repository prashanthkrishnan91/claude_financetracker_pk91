"""Parallel IO layer tests — cache-first, failure isolation, no LLM calls.

Validates Portfolio Engine v2 DAG invariants:
  * ``fetch_market_bundle`` hits the cache on the 2nd call for the same ticker
  * Upstream failures never raise into the orchestrator (neutral fallback)
  * Retries happen only at the HTTP layer, not the pipeline
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest


# ── Cache-first ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_market_bundle_hits_cache_on_second_call(monkeypatch):
    from app.services.ai import io_layer
    from app.services.cache.market_cache import MarketCache

    cache = MarketCache()
    price_service = MagicMock()
    call_count = 0

    async def fake_fetch_prices(tickers):
        nonlocal call_count
        call_count += 1
        class _Q:
            is_valid = True
            mid_price = 180.0
        return {tickers[0]: _Q()}

    price_service.fetch_prices = fake_fetch_prices

    b1 = await io_layer.fetch_market_bundle(
        ["AAPL"], price_service=price_service, cache=cache
    )
    b2 = await io_layer.fetch_market_bundle(
        ["AAPL"], price_service=price_service, cache=cache
    )

    assert b1["live_prices"] == {"AAPL": 180.0}
    assert b2["live_prices"] == {"AAPL": 180.0}
    # Second call served from cache — price_service.fetch_prices called once.
    assert call_count == 1


# ── Failure isolation ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_market_bundle_isolates_upstream_failure(monkeypatch):
    from app.services.ai import io_layer
    from app.services.cache.market_cache import MarketCache

    cache = MarketCache()
    price_service = MagicMock()

    async def always_fails(tickers):
        raise RuntimeError("upstream exploded")

    price_service.fetch_prices = always_fails

    # Make retry backoff trivial so the test is fast.
    monkeypatch.setattr(io_layer, "_HTTP_BACKOFF_S", (0.001, 0.001, 0.001))

    bundle = await io_layer.fetch_market_bundle(
        ["AAPL", "TSLA"], price_service=price_service, cache=cache
    )

    # No raise, no retries triggering pipeline re-runs — just neutral output.
    assert bundle["live_prices"] == {}
    assert "timings_ms" in bundle


@pytest.mark.asyncio
async def test_empty_ticker_list_returns_empty_bundle():
    from app.services.ai import io_layer

    bundle = await io_layer.fetch_market_bundle([], price_service=None)
    # Every bundle key must exist even with no tickers — downstream
    # consumers destructure without defensive ``.get``.
    for key in (
        "tickers", "prices", "live_prices", "news", "fundamentals",
        "funds", "price_action", "macro", "source_status",
        "missing_fields", "completeness_score", "timings_ms",
    ):
        assert key in bundle, f"missing key: {key}"
    assert bundle["tickers"] == []
    assert bundle["prices"] == {}
    assert bundle["live_prices"] == {}  # legacy alias still populated
    assert bundle["completeness_score"] == 1.0  # nothing requested ⇒ nothing missing
    assert bundle["macro"]["fallback"] is True
    assert bundle["macro"]["regime"] == "unknown"


@pytest.mark.asyncio
async def test_fetch_falls_back_to_stale_cache_on_failure(monkeypatch):
    """When retries exhaust, any previously-cached value must be served."""
    from app.services.ai import io_layer
    from app.services.cache.market_cache import MarketCache

    cache = MarketCache()
    monkeypatch.setattr(io_layer, "_HTTP_BACKOFF_S", (0.001,))

    # Seed cache with a known-good value.
    await cache.set("price:NVDA", 500.0, ttl=0.01)
    await asyncio.sleep(0.05)  # entry is now "stale" (expired)

    async def broken_factory():
        raise RuntimeError("boom")

    result = await io_layer._with_retry_and_cache_fallback(
        "price:NVDA",
        broken_factory,
        cache=cache,
        ttl=0.01,
        neutral=None,
    )
    # Even though fresh fetch failed and TTL expired, stale-cache fallback
    # serves the last-known value (better than nothing for pipeline continuity).
    assert result == 500.0


# ── No LLM ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_io_layer_does_not_import_llm(monkeypatch):
    """Statically confirm the IO layer does not invoke any LLM client."""
    from app.services.ai import io_layer as io_layer_mod

    src = (io_layer_mod.__file__ or "")
    # Sentinel import check — the module intentionally doesn't import llm.
    with open(src, encoding="utf-8") as f:
        body = f.read()
    assert "from .llm" not in body
    assert "LLMClient" not in body


# ── Parallelism ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_parallel_fetch_for_multiple_tickers(monkeypatch):
    from app.services.ai import io_layer
    from app.services.cache.market_cache import MarketCache

    cache = MarketCache()
    price_service = MagicMock()
    concurrent = 0
    peak = 0

    async def fake_fetch_prices(tickers):
        nonlocal concurrent, peak
        concurrent += 1
        peak = max(peak, concurrent)
        await asyncio.sleep(0.02)
        concurrent -= 1
        class _Q:
            is_valid = True
            mid_price = 100.0
        return {tickers[0]: _Q()}

    price_service.fetch_prices = fake_fetch_prices

    bundle = await io_layer.fetch_market_bundle(
        ["AAPL", "TSLA", "GOOG"], price_service=price_service, cache=cache
    )

    assert set(bundle["live_prices"].keys()) == {"AAPL", "TSLA", "GOOG"}
    # At least some overlap in execution confirms parallelism (not strict 3 —
    # test timing can be flaky; 2 is a reliable floor).
    assert peak >= 2
