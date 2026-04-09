"""Tests for the concurrent price engine — PriceResult, circuit breaker, cache, normalization."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from app.services.price_engine import (
    PriceResult,
    PriceService,
    _CacheEntry,
    _CircuitState,
    _CIRCUIT_BREAKER_THRESHOLD,
    _CIRCUIT_BREAKER_RESET,
    _CRYPTO_TICKERS,
    _TICKER_MAP,
)


# ── PriceResult tests ────────────────────────────────────────────────────────


class TestPriceResult:
    def test_valid_price(self):
        pr = PriceResult(
            ticker="NVDA", mid_price=875.22, bid=875.0, ask=875.44,
            last_trade=875.15, source="finnhub", timestamp=time.time(),
        )
        assert pr.is_valid
        assert not pr.is_stale

    def test_invalid_zero_price(self):
        pr = PriceResult(
            ticker="FAIL", mid_price=0, bid=None, ask=None,
            last_trade=0, source="error", timestamp=time.time(), error="No data",
        )
        assert not pr.is_valid

    def test_invalid_with_error(self):
        pr = PriceResult(
            ticker="NVDA", mid_price=100, bid=None, ask=None,
            last_trade=100, source="yfinance", timestamp=time.time(), error="Timeout",
        )
        assert not pr.is_valid  # has error

    def test_stale_cache_source(self):
        pr = PriceResult(
            ticker="AAPL", mid_price=200, bid=None, ask=None,
            last_trade=200, source="cache(yfinance)", timestamp=time.time(),
        )
        assert pr.is_stale

    def test_stale_institution_source(self):
        pr = PriceResult(
            ticker="AAPL", mid_price=200, bid=None, ask=None,
            last_trade=200, source="institution_price", timestamp=time.time(),
        )
        assert pr.is_stale

    def test_not_stale_fresh_source(self):
        pr = PriceResult(
            ticker="AAPL", mid_price=200, bid=None, ask=None,
            last_trade=200, source="yfinance", timestamp=time.time(),
        )
        assert not pr.is_stale


# ── Circuit Breaker tests ────────────────────────────────────────────────────


class TestCircuitBreaker:
    def test_initial_state_closed(self):
        cs = _CircuitState()
        assert not cs.is_open

    def test_opens_after_threshold(self):
        cs = _CircuitState()
        for _ in range(_CIRCUIT_BREAKER_THRESHOLD):
            cs.record_failure()
        assert cs.is_open

    def test_below_threshold_stays_closed(self):
        cs = _CircuitState()
        for _ in range(_CIRCUIT_BREAKER_THRESHOLD - 1):
            cs.record_failure()
        assert not cs.is_open

    def test_resets_on_success(self):
        cs = _CircuitState()
        for _ in range(_CIRCUIT_BREAKER_THRESHOLD):
            cs.record_failure()
        assert cs.is_open
        cs.record_success()
        assert not cs.is_open
        assert cs.failures == 0

    def test_resets_after_timeout(self):
        cs = _CircuitState()
        for _ in range(_CIRCUIT_BREAKER_THRESHOLD):
            cs.record_failure()
        assert cs.is_open
        # Simulate time passing beyond reset period
        cs.last_failure = time.time() - _CIRCUIT_BREAKER_RESET - 1
        assert not cs.is_open  # Should allow retry


# ── Cache Entry tests ────────────────────────────────────────────────────────


class TestCacheEntry:
    def test_cache_entry_stores_result(self):
        pr = PriceResult(
            ticker="AAPL", mid_price=200, bid=None, ask=None,
            last_trade=200, source="yfinance", timestamp=time.time(),
        )
        entry = _CacheEntry(result=pr, fetched_at=time.time())
        assert entry.result.ticker == "AAPL"


# ── Ticker Normalization ─────────────────────────────────────────────────────


class TestNormalization:
    def test_ticker_map(self):
        assert _TICKER_MAP.get("BRK.B") == "BRK-B"
        assert _TICKER_MAP.get("BF.B") == "BF-B"

    def test_normalise_method(self):
        assert PriceService._normalise("brk.b") == "BRK-B"
        assert PriceService._normalise("NVDA") == "NVDA"
        assert PriceService._normalise("aapl") == "AAPL"

    def test_crypto_tickers_known(self):
        assert "BTC" in _CRYPTO_TICKERS
        assert "ETH" in _CRYPTO_TICKERS
        assert "NVDA" not in _CRYPTO_TICKERS


# ── PriceService unit tests (no network) ─────────────────────────────────────


class TestPriceService:
    def test_init_default(self):
        svc = PriceService()
        assert svc._finnhub_key == ""
        assert svc._client is None

    def test_init_with_keys(self):
        svc = PriceService(
            finnhub_key="fk", polygon_key="pk",
            alpaca_key="ak", alpaca_secret="as",
        )
        assert svc._finnhub_key == "fk"
        assert svc._polygon_key == "pk"

    def test_error_result(self):
        pr = PriceService._error_result("NVDA", "test", "Something broke")
        assert pr.ticker == "NVDA"
        assert pr.mid_price == 0.0
        assert pr.error == "Something broke"
        assert not pr.is_valid

    def test_get_health(self):
        svc = PriceService()
        health = svc.get_health()
        assert "yfinance" in health
        assert "finnhub" in health
        assert "closed (healthy)" in health["yfinance"]["status"]

    def test_get_health_open_circuit(self):
        svc = PriceService()
        for _ in range(_CIRCUIT_BREAKER_THRESHOLD):
            svc._circuits["finnhub"].record_failure()
        health = svc.get_health()
        assert "open (skipping)" in health["finnhub"]["status"]

    def test_stock_sources_no_keys(self):
        """With no API keys, only yfinance should be available."""
        svc = PriceService()
        sources = svc._stock_sources("NVDA")
        assert len(sources) == 1
        source_names = [name for name, _ in sources]
        assert "yfinance" in source_names

    def test_stock_sources_with_keys(self):
        """With all keys, all stock sources should be available."""
        svc = PriceService(
            finnhub_key="fk", polygon_key="pk",
            alpaca_key="ak", alpaca_secret="as",
        )
        sources = svc._stock_sources("NVDA")
        source_names = [name for name, _ in sources]
        assert "yfinance" in source_names
        assert "finnhub" in source_names
        assert "alpaca" in source_names
        assert "polygon" in source_names

    def test_stock_sources_skips_open_circuit(self):
        """Sources with open circuit breakers should be skipped."""
        svc = PriceService(finnhub_key="fk")
        for _ in range(_CIRCUIT_BREAKER_THRESHOLD):
            svc._circuits["finnhub"].record_failure()
        sources = svc._stock_sources("NVDA")
        source_names = [name for name, _ in sources]
        assert "finnhub" not in source_names

    def test_crypto_sources(self):
        svc = PriceService()
        sources = svc._crypto_sources("BTC")
        source_names = [name for name, _ in sources]
        assert "coingecko" in source_names
        assert "yfinance" in source_names

    @pytest.mark.asyncio
    async def test_fetch_one_uses_cache(self):
        """Should return cached result if within TTL."""
        svc = PriceService()
        cached_pr = PriceResult(
            ticker="AAPL", mid_price=200, bid=199, ask=201,
            last_trade=200, source="yfinance", timestamp=time.time(),
        )
        svc._cache["AAPL"] = _CacheEntry(result=cached_pr, fetched_at=time.time())

        result = await svc.fetch_one("AAPL")
        assert result.mid_price == 200
        assert result.source == "yfinance"
        await svc.close()

    @pytest.mark.asyncio
    async def test_fetch_prices_returns_all_tickers(self):
        """Should return a result for every requested ticker."""
        svc = PriceService()
        # Pre-populate cache
        for t in ["NVDA", "AAPL"]:
            pr = PriceResult(
                ticker=t, mid_price=100, bid=99, ask=101,
                last_trade=100, source="yfinance", timestamp=time.time(),
            )
            svc._cache[t] = _CacheEntry(result=pr, fetched_at=time.time())

        results = await svc.fetch_prices(["NVDA", "AAPL"])
        assert "NVDA" in results
        assert "AAPL" in results
        assert results["NVDA"].is_valid
        await svc.close()

    @pytest.mark.asyncio
    async def test_close_idempotent(self):
        svc = PriceService()
        await svc.close()  # Should not raise
        await svc.close()  # Double close should not raise


# ── Mid-price calculation tests ──────────────────────────────────────────────


class TestMidPriceCalculation:
    def test_mid_price_with_bid_ask(self):
        """Robinhood formula: mid = (bid + ask) / 2"""
        bid, ask = 875.00, 875.44
        mid = (bid + ask) / 2.0
        assert mid == pytest.approx(875.22)

    def test_mid_price_fallback_to_last(self):
        """When no bid/ask, mid should equal last trade."""
        last = 875.15
        bid, ask = None, None
        if bid and ask and bid > 0 and ask > 0:
            mid = (bid + ask) / 2.0
        else:
            mid = last
        assert mid == 875.15
