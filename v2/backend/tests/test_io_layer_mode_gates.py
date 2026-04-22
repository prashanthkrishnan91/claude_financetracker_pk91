"""IO layer system-mode gate tests.

Validates v4 distributed-correctness tasks #3, #4, #6:
  * LIGHTWEIGHT mode: ``fetch_market_bundle`` returns a cache-only bundle
    without invoking any external fetch factory.
  * DEGRADED mode: ticker batch size is halved (load-shedding).
  * Every bundle carries a ``system_mode`` block so the orchestrator can
    echo it into the LLM context.
"""

from __future__ import annotations

import pytest

from app.services.ai import io_layer
from app.services.cache.market_cache import MarketCache
from app.services.market_data.system_mode import (
    SystemModeManager,
    _set_manager_for_testing,
)


@pytest.fixture(autouse=True)
def _reset_manager():
    """Reset the module singleton between tests so state doesn't leak."""
    _set_manager_for_testing(None)
    yield
    _set_manager_for_testing(None)


@pytest.mark.asyncio
async def test_lightweight_mode_reads_cache_only(monkeypatch):
    """LIGHTWEIGHT: no upstream factories run; cache values are surfaced."""
    # Force LIGHTWEIGHT via a stubbed status lookup.
    _set_manager_for_testing(
        SystemModeManager(status_provider=lambda: {
            "finnhub": "rate_limited",
            "coingecko": "failed",
            "polygon": "blocked",
        })
    )

    cache = MarketCache()
    await cache.set("price:AAPL", 150.0, ttl=60.0)

    # Stub the real factories so we can detect accidental invocation.
    async def _explode(*args, **kwargs):  # noqa: ARG001
        raise AssertionError("LIGHTWEIGHT mode called an external factory")

    monkeypatch.setattr(io_layer, "_fetch_prices_batch", _explode)

    # Single ticker → batch_size_factor=0.25 → max(1, 0) = 1, so AAPL survives.
    bundle = await io_layer.fetch_market_bundle(
        ["AAPL"],
        price_service=object(),  # presence triggers the live fetch path
        cache=cache,
    )

    assert bundle["system_mode"]["mode"] == "LIGHTWEIGHT"
    assert bundle["prices"] == {"AAPL": 150.0}
    # Timings still populated — contract guarantees the shape never drops fields.
    assert "timings_ms" in bundle
    assert "completeness_score" in bundle


@pytest.mark.asyncio
async def test_degraded_mode_halves_batch(monkeypatch):
    """DEGRADED halves the ticker batch before the IO layer fans out."""
    _set_manager_for_testing(
        SystemModeManager(status_provider=lambda: {
            "finnhub": "rate_limited",
            "coingecko": "ok",
            "polygon": "ok",
        })
    )

    cache = MarketCache()
    calls: list[list[str]] = []

    async def _fake_batch(tickers, *args, **kwargs):  # noqa: ARG001
        calls.append(list(tickers))
        return {}

    # ``monkeypatch`` guarantees the attribute is restored after the test
    # so concurrent / subsequent tests see the real ``_fetch_prices_batch``.
    monkeypatch.setattr(io_layer, "_fetch_prices_batch", _fake_batch)

    bundle = await io_layer.fetch_market_bundle(
        ["AAPL", "MSFT", "TSLA", "NVDA"],
        price_service=object(),
        cache=cache,
    )

    assert bundle["system_mode"]["mode"] == "DEGRADED"
    assert calls, "price batch should have been invoked"
    # batch_size_factor=0.5 → 4 tickers → 2-ticker batch.
    assert len(calls[0]) == 2


@pytest.mark.asyncio
async def test_normal_mode_preserves_batch_size(monkeypatch):
    _set_manager_for_testing(
        SystemModeManager(status_provider=lambda: {
            "finnhub": "ok", "coingecko": "ok", "polygon": "ok",
        })
    )
    cache = MarketCache()

    captured: list[list[str]] = []

    async def _fake_batch(tickers, *args, **kwargs):  # noqa: ARG001
        captured.append(list(tickers))
        return {t: 100.0 for t in tickers}

    monkeypatch.setattr(io_layer, "_fetch_prices_batch", _fake_batch)

    bundle = await io_layer.fetch_market_bundle(
        ["AAPL", "MSFT", "TSLA"],
        price_service=object(),
        cache=cache,
    )
    assert bundle["system_mode"]["mode"] == "NORMAL"
    assert captured and len(captured[0]) == 3


@pytest.mark.asyncio
async def test_bundle_always_contains_structured_keys():
    """Safe-failure contract — every bundle has the core keys regardless of mode."""
    _set_manager_for_testing(
        SystemModeManager(status_provider=lambda: {
            "finnhub": "ok", "coingecko": "ok", "polygon": "ok",
        })
    )
    cache = MarketCache()

    # No price service → live_prices stay empty; bundle must still be intact.
    bundle = await io_layer.fetch_market_bundle(["AAPL"], cache=cache)
    for key in (
        "tickers", "prices", "live_prices", "news", "fundamentals",
        "price_action", "macro", "source_status", "system_mode",
        "missing_fields", "completeness_score", "timings_ms",
    ):
        assert key in bundle, f"missing canonical key: {key}"
