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
