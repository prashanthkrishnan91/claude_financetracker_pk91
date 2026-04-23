"""Phase 1 — MarketSnapshot acceptance tests.

Gates covered here (see tasks/todo.md → Phase 1 acceptance):
  1. 429/403 from an upstream does NOT crash the pipeline.
  2. Every ticker in the portfolio gets a snapshot.
  3. ``data_quality_score`` varies across a mixed portfolio.
  4. ``fallback_chain`` is logged when the primary quote fails.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.services.intelligence.market_snapshot import (
    MarketSnapshot,
    build_market_snapshots,
)


# ── Deterministic builder — no IO, no randomness ───────────────────────────


def _make_bundle(
    *,
    prices=None,
    news=None,
    fundamentals=None,
    price_action=None,
    source_status=None,
    tickers=None,
):
    """Small helper — minimal bundle shape the builder expects."""
    return {
        "tickers": tickers or [],
        "prices": prices or {},
        "live_prices": prices or {},
        "news": news or {},
        "fundamentals": fundamentals or {},
        "funds": fundamentals or {},
        "price_action": price_action or {},
        "macro": {"fallback": True, "regime": "unknown"},
        "source_status": source_status or {},
        "missing_fields": [],
        "completeness_score": 0.0,
    }


def test_every_ticker_produces_a_snapshot():
    """Gate #2 — every ticker passed in gets a MarketSnapshot back."""
    bundle = _make_bundle(
        prices={"AAPL": 180.0, "TSLA": 240.0},
        news={"AAPL": [{"headline": "Apple ships new product"}]},
        fundamentals={"AAPL": {"pe": 30.0, "sector": "Technology"}},
        price_action={
            "AAPL": {"pct_5d": 2.0, "pct_30d": 6.0, "volatility_30d": 0.25},
            "TSLA": {"pct_5d": -1.5, "pct_30d": -8.0, "volatility_30d": 0.55},
        },
    )
    positions = [
        {"ticker": "AAPL", "avg_cost": 150.0, "category": "Tech"},
        {"ticker": "TSLA", "avg_cost": 220.0, "category": "Auto"},
        {"ticker": "NVDA", "avg_cost": 400.0, "category": "Tech"},
    ]
    snaps = build_market_snapshots(
        bundle,
        tickers=["AAPL", "TSLA", "NVDA"],
        positions=positions,
    )

    assert set(snaps.keys()) == {"AAPL", "TSLA", "NVDA"}
    assert all(isinstance(s, MarketSnapshot) for s in snaps.values())


def test_data_quality_score_varies_across_tickers():
    """Gate #3 — scores must differ when data coverage differs."""
    bundle = _make_bundle(
        prices={"AAPL": 180.0, "TSLA": 240.0},  # NVDA missing on purpose
        news={"AAPL": [{"headline": "h1"}, {"headline": "h2"}]},
        fundamentals={
            "AAPL": {
                "pe": 30, "profit_margin": 0.25, "dividend_yield": 0.005,
                "sector": "Technology",
            },
        },
        price_action={
            "AAPL": {"pct_5d": 2.0, "pct_30d": 6.0, "volatility_30d": 0.25},
            "TSLA": {"pct_5d": -1.5, "pct_30d": -8.0, "volatility_30d": 0.55},
        },
    )
    positions = [
        {"ticker": "AAPL", "avg_cost": 150.0},
        {"ticker": "TSLA", "avg_cost": 220.0},
        {"ticker": "NVDA", "avg_cost": 400.0},
    ]
    prior = {
        "AAPL": {"sentiment_label": "bullish", "sentiment_score": 0.4},
    }
    snaps = build_market_snapshots(
        bundle,
        tickers=["AAPL", "TSLA", "NVDA"],
        prior_insights=prior,
        positions=positions,
    )

    aapl = snaps["AAPL"].data_quality_score
    tsla = snaps["TSLA"].data_quality_score
    nvda = snaps["NVDA"].data_quality_score

    # Strictly varying: AAPL richest, TSLA in the middle, NVDA thinnest.
    assert aapl > tsla > nvda, (aapl, tsla, nvda)
    # AAPL has live + fundamentals + sentiment + news → well above 0.6.
    assert aapl >= 0.7
    # NVDA has no live price (only avg_cost), no fundamentals, no news.
    # With the additive weighting scheme that lands well below 0.3.
    assert nvda <= 0.3
    # Sanity: score is NEVER 1.0 by default — must be earned.
    assert nvda < 1.0


def test_fallback_chain_populated_when_primary_quote_fails():
    """Gate #4 — ``fallback_chain`` records every source consulted."""
    # Price missing from `prices` but present in `price_action` last-close.
    bundle = _make_bundle(
        prices={},  # primary live quote unavailable
        price_action={
            "TSLA": {"last": 238.5, "pct_5d": -1.0, "pct_30d": -8.0},
        },
        source_status={"finnhub": "rate_limited", "polygon": "blocked"},
    )
    positions = [{"ticker": "TSLA", "avg_cost": 220.0}]

    snaps = build_market_snapshots(
        bundle, tickers=["TSLA"], positions=positions,
    )
    chain = snaps["TSLA"].fallback_chain

    assert "live_failed" in chain
    assert "price_action" in chain
    # Upstream degraded providers propagate into the chain for triage.
    assert any(c.startswith("finnhub:") for c in chain)
    assert any(c.startswith("polygon:") for c in chain)
    assert snaps["TSLA"].price == 238.5
    assert snaps["TSLA"].price_source == "price_action"


def test_no_price_anywhere_falls_back_to_avg_cost():
    """When no quote and no price_action survive, avg_cost is the last stop."""
    bundle = _make_bundle(prices={}, price_action={})
    positions = [{"ticker": "XYZ", "avg_cost": 42.0}]
    snaps = build_market_snapshots(
        bundle, tickers=["XYZ"], positions=positions,
    )
    snap = snaps["XYZ"]
    assert snap.price == 42.0
    assert snap.price_source == "avg_cost_fallback"
    assert "avg_cost" in snap.fallback_chain
    assert "price" in snap.missing_fields


def test_completely_missing_ticker_still_gets_snapshot():
    """Gate #2 — missing prices do not drop a ticker from the result set."""
    bundle = _make_bundle()
    positions = [{"ticker": "UNK", "avg_cost": 0.0}]  # no avg_cost either
    snaps = build_market_snapshots(
        bundle, tickers=["UNK"], positions=positions,
    )
    assert "UNK" in snaps
    snap = snaps["UNK"]
    assert snap.price is None
    assert snap.price_source == "unavailable"
    assert snap.data_quality_score == 0.0


# ── End-to-end: 429 simulation against the io_layer ────────────────────────


@pytest.mark.asyncio
async def test_429_from_upstream_does_not_crash_bundle(monkeypatch):
    """Gate #1 — simulate a 429 and confirm the pipeline degrades cleanly."""
    from app.services import intelligence
    from app.services.ai import io_layer
    from app.services.cache.market_cache import MarketCache

    cache = MarketCache()
    call_log: list[str] = []

    # Simulate a price-service that 429s. Pretend it raises the way
    # upstream providers do — ``io_layer`` must catch and degrade.
    class _FakePriceService:
        async def fetch_prices(self, tickers):
            call_log.append(f"prices:{tickers}")
            raise RuntimeError("HTTP 429 rate_limited")

    # Shrink retry schedule so the test runs in <1s.
    monkeypatch.setattr(io_layer, "_HTTP_BACKOFF_S", (0.001, 0.001, 0.001))

    bundle = await io_layer.fetch_market_bundle(
        ["AAPL", "TSLA"],
        price_service=_FakePriceService(),
        cache=cache,
    )
    assert bundle["live_prices"] == {}  # degraded, not raised

    # Build snapshots from the degraded bundle — must not crash, and
    # every ticker should still appear with its fallback chain populated.
    snaps = build_market_snapshots(
        bundle,
        tickers=["AAPL", "TSLA"],
        positions=[
            {"ticker": "AAPL", "avg_cost": 150.0},
            {"ticker": "TSLA", "avg_cost": 220.0},
        ],
    )
    assert set(snaps.keys()) == {"AAPL", "TSLA"}
    for snap in snaps.values():
        assert "live_failed" in snap.fallback_chain
        assert snap.price_source in {"avg_cost_fallback", "unavailable", "price_action"}


@pytest.mark.asyncio
async def test_orchestrator_logs_fallback_chain(monkeypatch, caplog):
    """Gate #4 — orchestrator emits a structured `snapshot_fallbacks` line per ticker."""
    from app.services.agents import orchestrator as orch_mod

    mock_db = MagicMock()
    mock_db.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()
    mock_db.table.return_value.insert.return_value.execute.return_value = MagicMock()
    monkeypatch.setattr(orch_mod, "get_supabase_client", lambda: mock_db)

    orch = orch_mod.AgentOrchestrator(user_id=uuid4())

    context = {
        "portfolio": [
            {"ticker": "AAPL", "avg_cost": 150.0, "category": "Tech"},
            {"ticker": "TSLA", "avg_cost": 220.0, "category": "Auto"},
        ],
        "insights": [],
    }
    bundle = _make_bundle(
        prices={"AAPL": 180.0},  # TSLA missing → will show live_failed
        price_action={
            "AAPL": {"pct_5d": 1.0, "pct_30d": 5.0, "volatility_30d": 0.22},
            "TSLA": {"last": 238.5, "pct_5d": -1.5, "pct_30d": -8.0},
        },
        source_status={"finnhub": "rate_limited"},
    )

    caplog.set_level(logging.INFO, logger=orch_mod.__name__)
    snaps = await orch._build_and_persist_snapshots(
        run_id="test-run-id",
        context=context,
        bundle=bundle,
    )

    assert set(snaps.keys()) == {"AAPL", "TSLA"}
    # Gate: at least one log line per ticker, containing the fallback chain marker.
    messages = [r.getMessage() for r in caplog.records]
    aapl_lines = [m for m in messages if "snapshot_fallbacks" in m and "AAPL" in m]
    tsla_lines = [m for m in messages if "snapshot_fallbacks" in m and "TSLA" in m]
    assert aapl_lines and tsla_lines
    # Gate: TSLA chain must include at least two sources (live_failed + price_action),
    # demonstrating the fallback path was exercised in logs.
    assert any("live_failed" in m for m in tsla_lines)
    assert any("price_action" in m for m in tsla_lines)
