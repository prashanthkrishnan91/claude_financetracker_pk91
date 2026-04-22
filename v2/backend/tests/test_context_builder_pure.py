"""Context builder purity tests — no DB, no network, no LLM.

Validates Portfolio Engine v2 invariant: the hot-path entrypoint
``build_context_from_inputs`` is a deterministic transform that produces the
same output for identical inputs without any side effects.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


def test_pure_builder_does_not_touch_db(monkeypatch):
    """``build_context_from_inputs`` must not call get_supabase_client."""
    from app.services.ai import context_builder

    def _explode():
        raise AssertionError("DB accessed from pure builder")

    monkeypatch.setattr(context_builder, "get_supabase_client", _explode)

    ctx = context_builder.build_context_from_inputs(
        positions=[
            {"ticker": "AAPL", "shares": 10, "avg_cost": 150.0, "category": "Tech"},
        ],
        latest_insights_by_ticker={
            "AAPL": {
                "sentiment_label": "bullish",
                "technical_signal": "BUY",
                "fundamental_score": 0.5,
                "suggested_action": "HOLD",
            }
        },
        live_prices={"AAPL": 170.0},
        macro_summary="neutral regime",
    )

    assert ctx["portfolio"][0]["ticker"] == "AAPL"
    assert ctx["portfolio"][0]["current_price"] == 170.0
    assert ctx["insights"][0]["sentiment"] == "bullish"
    assert ctx["macro"]["summary"] == "neutral regime"


def test_pure_builder_is_deterministic():
    """Identical inputs → identical output (no hidden time-based drift)."""
    from app.services.ai import context_builder

    inputs = dict(
        positions=[
            {"ticker": "VOO", "shares": 5, "avg_cost": 400.0, "category": "ETF"},
        ],
        latest_insights_by_ticker={},
        live_prices={"VOO": 450.0},
        macro_summary="calm",
    )

    a = context_builder.build_context_from_inputs(**inputs)
    b = context_builder.build_context_from_inputs(**inputs)
    assert a == b


def test_market_data_prices_override_legacy_live_prices():
    """When both supplied, io_layer prices win — they're freshest."""
    from app.services.ai import context_builder

    ctx = context_builder.build_context_from_inputs(
        positions=[{"ticker": "AAPL", "shares": 1, "avg_cost": 150.0, "category": "Tech"}],
        latest_insights_by_ticker={},
        live_prices={"AAPL": 150.0},  # stale
        market_data={"live_prices": {"AAPL": 175.0}},  # fresh
    )
    assert ctx["portfolio"][0]["current_price"] == 175.0


def test_market_data_news_compacted_into_context():
    from app.services.ai import context_builder

    ctx = context_builder.build_context_from_inputs(
        positions=[{"ticker": "AAPL", "shares": 1, "avg_cost": 100.0, "category": "Tech"}],
        latest_insights_by_ticker={},
        market_data={
            "news": {
                "AAPL": [
                    {"headline": "Apple unveils new chip"},
                    {"headline": "Earnings beat expectations"},
                ]
            }
        },
    )
    p = ctx["portfolio"][0]
    assert p["recent_news_count"] == 2
    assert "Apple unveils new chip" in p["recent_headlines"]


def test_blank_ticker_is_skipped():
    from app.services.ai import context_builder

    ctx = context_builder.build_context_from_inputs(
        positions=[
            {"ticker": "", "shares": 1, "avg_cost": 10.0},
            {"ticker": "AAPL", "shares": 1, "avg_cost": 100.0},
        ],
        latest_insights_by_ticker={},
    )
    assert [p["ticker"] for p in ctx["portfolio"]] == ["AAPL"]


def test_default_macro_when_none_supplied():
    from app.services.ai import context_builder

    ctx = context_builder.build_context_from_inputs(
        positions=[],
        latest_insights_by_ticker={},
        macro_summary=None,
    )
    assert "Macro context unavailable" in ctx["macro"]["summary"]


# ── Data completeness layer ────────────────────────────────────────────────


def test_every_ticker_has_guaranteed_signal_fields():
    """Builder never returns a ticker missing sentiment/technical/fundamental/trend."""
    from app.services.ai import context_builder

    ctx = context_builder.build_context_from_inputs(
        positions=[{"ticker": "ABC", "shares": 1, "avg_cost": 10.0}],
        latest_insights_by_ticker={},  # nothing known — force fallbacks
    )
    p = ctx["portfolio"][0]
    assert p["sentiment_label"] == "neutral"
    assert p["technical_signal"] == "NEUTRAL"
    assert p["fundamental_score"] == 0.0
    assert p["trend"] == "flat"
    assert "confidence_score" in p
    assert 0.0 <= p["confidence_score"] <= 1.0
    assert p["confidence_label"] in {
        "high confidence", "partial signal",
        "low confidence signal", "watchlist only",
    }
    assert p["data_quality"]["fallbacks_used"] is True
    assert "sentiment" in p["data_quality"]["missing_fields"]
    assert "fundamental" in p["data_quality"]["missing_fields"]


def test_complete_ticker_scores_high_confidence():
    """A ticker with real price + all signals should land in high confidence."""
    from app.services.ai import context_builder

    ctx = context_builder.build_context_from_inputs(
        positions=[{"ticker": "AAPL", "shares": 1, "avg_cost": 100.0}],
        latest_insights_by_ticker={
            "AAPL": {
                "sentiment_label": "bullish",
                "sentiment_score": 0.4,
                "technical_signal": "BUY",
                "fundamental_score": 0.5,
            }
        },
        live_prices={"AAPL": 170.0},
        market_data={"price_action": {"AAPL": {"pct_30d": 8.0}}},
    )
    p = ctx["portfolio"][0]
    assert p["confidence_score"] >= 0.75
    assert p["confidence_label"] == "high confidence"
    assert p["trend"] == "up"
    assert p["data_quality"]["fallbacks_used"] is False


def test_portfolio_level_data_quality_aggregates_fallbacks():
    """Top-level data_quality rolls up per-ticker fallback usage."""
    from app.services.ai import context_builder

    ctx = context_builder.build_context_from_inputs(
        positions=[
            {"ticker": "AAPL", "shares": 1, "avg_cost": 100.0},
            {"ticker": "VOO", "shares": 1, "avg_cost": 400.0},
        ],
        latest_insights_by_ticker={},
        live_prices={"AAPL": 170.0},  # VOO has no price
    )
    dq = ctx["data_quality"]
    assert 0.0 <= dq["completeness_score"] <= 1.0
    assert dq["fallbacks_used"] is True
    assert "price" in dq["missing_fields"]
    assert "sentiment" in dq["missing_fields"]


def test_price_falls_back_to_avg_cost_when_live_missing():
    """Missing live price should degrade to avg_cost, never block the pipeline."""
    from app.services.ai import context_builder

    ctx = context_builder.build_context_from_inputs(
        positions=[{"ticker": "ZZZ", "shares": 1, "avg_cost": 42.5}],
        latest_insights_by_ticker={},
    )
    p = ctx["portfolio"][0]
    assert p["current_price"] == 42.5
    assert p["price_source"] == "avg_cost_fallback"


def test_sentiment_block_aggregates_portfolio():
    """Top-level sentiment block rolls up per-ticker labels."""
    from app.services.ai import context_builder

    ctx = context_builder.build_context_from_inputs(
        positions=[
            {"ticker": "A", "shares": 1, "avg_cost": 1.0},
            {"ticker": "B", "shares": 1, "avg_cost": 1.0},
        ],
        latest_insights_by_ticker={
            "A": {"sentiment_label": "bullish", "sentiment_score": 0.4},
            "B": {"sentiment_label": "bearish", "sentiment_score": -0.3},
        },
    )
    sent = ctx["sentiment"]
    assert sent["bullish_count"] == 1
    assert sent["bearish_count"] == 1
    assert sent["neutral_count"] == 0
    assert sent["average_score"] == round((0.4 + -0.3) / 2, 3)


def test_builder_never_emits_insufficient_data_strings():
    """Guard against any "insufficient data" leakage from the pure builder."""
    from app.services.ai import context_builder
    import json

    ctx = context_builder.build_context_from_inputs(
        positions=[{"ticker": "XYZ", "shares": 1, "avg_cost": 10.0}],
        latest_insights_by_ticker={},
    )
    # Serialize the whole context and sniff for the banned phrase.
    assert "insufficient data" not in json.dumps(ctx).lower()
