"""Recommendation panel rationale composer — pure-function tests.

Invariants under test:
  - actions come verbatim from the Intel v3 snapshot (never recomputed)
  - HARD RULE: a recommendation with no rationale is excluded, never rendered
  - tax impact appears only for sell-side actions and never fabricates numbers
"""

from __future__ import annotations

from app.services.recommendation_rationale_v1 import build_recommendation_panel


def _card(ticker="AAPL", action="TRIM", **over):
    card = {
        "ticker": ticker,
        "name": f"{ticker} Inc",
        "action": action,
        "conviction": "MEDIUM",
        "evidence_band": "STRONG",
        "why_text": "Multiple signals support trimming toward target.",
        "portfolio_current_pct": 12.0,
    }
    card.update(over)
    return card


def _snapshot(cards):
    return {
        "snapshot_id": "snap-1",
        "generated_at": "2026-07-18T10:00:00+00:00",
        "current_holdings": cards,
    }


def _lots(gain_ok=True):
    return {
        "AAPL": [{
            "quantity": 10, "cost_basis": 1000.0, "is_long_term": True,
            "unrealized_gain": 400.0 if gain_ok else None,
            "estimated_tax_if_sold": 60.0 if gain_ok else None,
        }],
    }


def _summaries():
    return {
        "AAPL": {
            "total_cost_basis": 1000.0,
            "unrealized_gain_total": 400.0,
        },
    }


class TestPanelComposition:
    def test_action_passed_through_verbatim(self):
        panel = build_recommendation_panel(
            snapshot_payload=_snapshot([_card(action="TRIM")]),
            lots_by_ticker=_lots(), lot_summaries=_summaries(),
            target_weights_pct={"AAPL": 8.0}, profit_threshold_pct=25.0,
        )
        assert len(panel["items"]) == 1
        item = panel["items"][0]
        assert item["action"] == "TRIM"
        assert item["conviction"] == "MEDIUM"
        assert item["evidence_band"] == "STRONG"

    def test_rationale_shows_profit_tax_and_drift(self):
        panel = build_recommendation_panel(
            snapshot_payload=_snapshot([_card(action="TRIM")]),
            lots_by_ticker=_lots(), lot_summaries=_summaries(),
            target_weights_pct={"AAPL": 8.0}, profit_threshold_pct=25.0,
        )
        item = panel["items"][0]
        # 400/1000 = 40% gain, above the 25% threshold
        assert "40.0%" in item["rationale"]
        assert "25% profit threshold" in item["rationale"]
        assert "$60" in item["rationale"]          # estimated tax
        assert "4.0pp above" in item["rationale"]  # 12% current vs 8% target
        assert item["components"]["tax_impact"] is not None
        assert item["components"]["allocation_drift"] is not None

    def test_buy_action_has_no_tax_clause(self):
        panel = build_recommendation_panel(
            snapshot_payload=_snapshot([_card(action="BUY")]),
            lots_by_ticker=_lots(), lot_summaries=_summaries(),
            target_weights_pct={}, profit_threshold_pct=25.0,
        )
        assert panel["items"][0]["components"]["tax_impact"] is None

    def test_missing_price_never_fabricates_tax_estimate(self):
        panel = build_recommendation_panel(
            snapshot_payload=_snapshot([_card(action="SELL")]),
            lots_by_ticker=_lots(gain_ok=False), lot_summaries=_summaries(),
            target_weights_pct={}, profit_threshold_pct=25.0,
        )
        assert panel["items"][0]["components"]["tax_impact"] is None

    def test_no_target_means_no_drift_component(self):
        panel = build_recommendation_panel(
            snapshot_payload=_snapshot([_card()]),
            lots_by_ticker=_lots(), lot_summaries=_summaries(),
            target_weights_pct={}, profit_threshold_pct=25.0,
        )
        assert panel["items"][0]["components"]["allocation_drift"] is None


class TestHardRuleNoRationaleNoRender:
    def test_card_without_any_rationale_is_excluded(self):
        bare = _card(ticker="ZZZZ", action="HOLD", why_text="", portfolio_current_pct=None)
        panel = build_recommendation_panel(
            snapshot_payload=_snapshot([bare]),
            lots_by_ticker={}, lot_summaries={},
            target_weights_pct={}, profit_threshold_pct=25.0,
        )
        assert panel["items"] == []
        assert panel["excluded"] == [{"ticker": "ZZZZ", "reason": "no_rationale_available"}]

    def test_engine_reason_alone_is_sufficient_rationale(self):
        card = _card(ticker="VTI", action="HOLD", portfolio_current_pct=None)
        panel = build_recommendation_panel(
            snapshot_payload=_snapshot([card]),
            lots_by_ticker={}, lot_summaries={},
            target_weights_pct={}, profit_threshold_pct=25.0,
        )
        assert len(panel["items"]) == 1
        assert panel["items"][0]["rationale"] == card["why_text"]

    def test_card_without_action_is_excluded(self):
        panel = build_recommendation_panel(
            snapshot_payload=_snapshot([_card(action="")]),
            lots_by_ticker=_lots(), lot_summaries=_summaries(),
            target_weights_pct={}, profit_threshold_pct=25.0,
        )
        assert panel["items"] == []
        assert panel["excluded"][0]["reason"] == "no_action"

    def test_no_snapshot_returns_empty_honestly(self):
        panel = build_recommendation_panel(
            snapshot_payload=None,
            lots_by_ticker={}, lot_summaries={},
            target_weights_pct={}, profit_threshold_pct=25.0,
        )
        assert panel["items"] == []
        assert panel["snapshot_meta"]["status"] == "no_snapshot"

    def test_legacy_cards_key_still_accepted(self):
        snapshot = {"snapshot_id": "s", "generated_at": "g", "cards": [_card()]}
        panel = build_recommendation_panel(
            snapshot_payload=snapshot,
            lots_by_ticker=_lots(), lot_summaries=_summaries(),
            target_weights_pct={}, profit_threshold_pct=25.0,
        )
        assert len(panel["items"]) == 1
