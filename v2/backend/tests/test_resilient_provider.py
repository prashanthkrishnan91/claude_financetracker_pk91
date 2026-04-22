"""Resilience layer tests — circuit breakers, source_status, macro fallback.

Validates task #1–#8 acceptance criteria from tasks/todo.md:
  * 429/403 on any provider never raises into the pipeline
  * Repeated failures open the per-provider circuit breaker
  * Bundle schema is always populated (tickers, prices, source_status,
    completeness_score, missing_fields, macro.fallback=True)
  * Macro fallback object carries the documented shape
  * ResilientMarketProvider facade returns the task-spec schema
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest


# ── Circuit breakers in data_sources ────────────────────────────────────────


def test_provider_breaker_opens_after_threshold():
    from app.services.agents import data_sources as ds

    ds.reset_breakers()
    breaker = ds._BREAKERS["coingecko"]

    # Below threshold — still closed
    for _ in range(ds._BREAKER_THRESHOLD - 1):
        breaker.record_failure("429 rate_limited")
    assert not breaker.is_open()
    assert breaker.status() == "ok"

    # One more failure trips the breaker
    breaker.record_failure("429 rate_limited")
    assert breaker.is_open()
    assert breaker.status() == "rate_limited"

    # A success resets
    breaker.record_success()
    assert not breaker.is_open()
    assert breaker.status() == "ok"


def test_provider_breaker_classifies_403_as_blocked():
    from app.services.agents import data_sources as ds

    ds.reset_breakers()
    breaker = ds._BREAKERS["polygon"]
    for _ in range(ds._BREAKER_THRESHOLD):
        breaker.record_failure("403 forbidden")
    assert breaker.is_open()
    assert breaker.status() == "blocked"


@pytest.mark.asyncio
async def test_coingecko_breaker_short_circuits_when_open(monkeypatch):
    """When the CoinGecko breaker is open, the helper returns {} without a network call."""
    from app.services.agents import data_sources as ds

    ds.reset_breakers()
    # Trip the breaker manually
    for _ in range(ds._BREAKER_THRESHOLD):
        ds._BREAKERS["coingecko"].record_failure("429 rate_limited")

    # If the helper called httpx, this would blow up — assert it doesn't.
    client = MagicMock()

    async def _explode(*args, **kwargs):
        raise AssertionError("upstream was contacted despite open breaker")

    client.get = _explode
    out = await ds.fetch_coingecko_market(client, "BTC")
    assert out == {}

    # Cleanup so other tests see a healthy breaker
    ds.reset_breakers()


@pytest.mark.asyncio
async def test_finnhub_news_returns_empty_on_429(monkeypatch):
    """A 429 from Finnhub records a failure but never raises."""
    from app.services.agents import data_sources as ds

    ds.reset_breakers()
    call_count = 0

    class _Resp:
        status_code = 429

        def json(self):
            return []

    async def fake_get(url, params=None):  # noqa: ARG001
        nonlocal call_count
        call_count += 1
        return _Resp()

    client = MagicMock()
    client.get = fake_get

    out = await ds.fetch_finnhub_news(client, "AAPL", api_key="fake-key")
    assert out == []
    assert call_count == 1
    # One failure recorded, breaker still closed (threshold = 3)
    assert ds._BREAKERS["finnhub"].failures == 1
    assert not ds._BREAKERS["finnhub"].is_open()

    ds.reset_breakers()


# ── io_layer bundle schema ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bundle_always_contains_required_keys(monkeypatch):
    from app.services.ai import io_layer
    from app.services.agents import data_sources as ds
    from app.services.cache.market_cache import MarketCache

    ds.reset_breakers()
    cache = MarketCache()
    price_service = MagicMock()

    async def good_prices(tickers):
        class _Q:
            is_valid = True
            mid_price = 101.0
        return {tickers[0]: _Q()}

    price_service.fetch_prices = good_prices

    bundle = await io_layer.fetch_market_bundle(
        ["AAPL", "TSLA"], price_service=price_service, cache=cache
    )

    # Schema keys required by task acceptance criteria
    for key in (
        "tickers", "prices", "live_prices", "news", "fundamentals",
        "funds", "price_action", "macro", "source_status",
        "missing_fields", "completeness_score", "timings_ms",
    ):
        assert key in bundle, f"missing key: {key}"

    assert set(bundle["tickers"]) == {"AAPL", "TSLA"}
    assert bundle["live_prices"] == bundle["prices"]  # legacy alias
    assert 0.0 <= bundle["completeness_score"] <= 1.0
    assert bundle["macro"]["fallback"] is True
    assert bundle["macro"]["regime"] == "unknown"
    assert bundle["macro"]["sentiment"] == "neutral"
    # source_status tracks every provider even when unused this call
    assert "coingecko" in bundle["source_status"]
    assert "finnhub" in bundle["source_status"]
    assert "polygon" in bundle["source_status"]


@pytest.mark.asyncio
async def test_bundle_completeness_score_reflects_missing_prices(monkeypatch):
    from app.services.ai import io_layer
    from app.services.cache.market_cache import MarketCache

    cache = MarketCache()
    price_service = MagicMock()
    monkeypatch.setattr(io_layer, "_HTTP_BACKOFF_S", (0.001,))

    async def always_fails(tickers):
        raise RuntimeError("upstream down")

    price_service.fetch_prices = always_fails

    bundle = await io_layer.fetch_market_bundle(
        ["AAPL", "TSLA", "NVDA"], price_service=price_service, cache=cache
    )

    # No prices → prices bucket coverage = 0 → "prices" in missing_fields
    assert bundle["prices"] == {}
    assert "prices" in bundle["missing_fields"]
    assert bundle["completeness_score"] == 0.0


# ── Macro fallback shape ───────────────────────────────────────────────────


def test_macro_fallback_shape():
    from app.services.ai.io_layer import _macro_fallback

    m = _macro_fallback()
    assert m["regime"] == "unknown"
    assert m["inflation"] is None
    assert m["rates"] is None
    assert m["sentiment"] == "neutral"
    assert m["fallback"] is True
    assert "summary" in m


# ── Context builder per-bucket data_quality ────────────────────────────────


def test_context_builder_emits_per_bucket_missing_lists():
    """data_quality.missing_prices / missing_news / missing_fundamentals populated."""
    from app.services.ai import context_builder

    ctx = context_builder.build_context_from_inputs(
        positions=[
            {"ticker": "AAPL", "shares": 1, "avg_cost": 100.0},
            {"ticker": "VOO", "shares": 1, "avg_cost": 400.0},
        ],
        latest_insights_by_ticker={},
        live_prices={"AAPL": 170.0},  # VOO missing
        market_data={
            "news": {"AAPL": [{"headline": "something"}]},  # VOO missing news
            "fundamentals": {},
            "price_action": {},
        },
    )
    dq = ctx["data_quality"]
    assert dq["missing_prices"] == ["VOO"]
    assert set(dq["missing_news"]) == {"VOO"}
    assert set(dq["missing_fundamentals"]) == {"AAPL", "VOO"}
    assert set(dq["missing_technicals"]) == {"AAPL", "VOO"}


# ── ResilientMarketProvider facade ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_resilient_provider_returns_task_spec_schema(monkeypatch):
    from app.services.cache.market_cache import MarketCache
    from app.services.market_data import get_market_snapshot

    cache = MarketCache()
    price_service = MagicMock()

    async def good_prices(tickers):
        class _Q:
            is_valid = True
            mid_price = 250.5
        return {tickers[0]: _Q()}

    price_service.fetch_prices = good_prices

    snap = await get_market_snapshot(
        ["NVDA", "TSLA"],
        price_service=price_service,
        cache=cache,
    )

    # Exact schema shape from task #1
    for key in (
        "prices", "source_status", "missing_fields",
        "completeness_score", "news", "fundamentals", "macro", "tickers",
    ):
        assert key in snap, f"missing key: {key}"

    # Every requested ticker is represented (value may be a float or None)
    assert set(snap["tickers"]) == {"NVDA", "TSLA"}
    assert set(snap["prices"].keys()) == {"NVDA", "TSLA"}
    # Macro fallback carries the fields the task documents
    assert snap["macro"]["regime"] == "unknown"
    assert snap["macro"]["fallback"] is True


@pytest.mark.asyncio
async def test_resilient_provider_isolates_upstream_failures(monkeypatch):
    from app.services.ai import io_layer
    from app.services.cache.market_cache import MarketCache
    from app.services.market_data import get_market_snapshot

    cache = MarketCache()
    price_service = MagicMock()
    monkeypatch.setattr(io_layer, "_HTTP_BACKOFF_S", (0.001,))

    async def boom(tickers):
        raise RuntimeError("429 from coingecko")

    price_service.fetch_prices = boom

    snap = await get_market_snapshot(
        ["BTC"], price_service=price_service, cache=cache
    )
    # No raise, prices carry None for missing ticker
    assert snap["prices"] == {"BTC": None}
    assert snap["completeness_score"] == 0.0
    assert "prices" in snap["missing_fields"]
