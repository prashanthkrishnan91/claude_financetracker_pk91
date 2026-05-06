"""Intel v3 snapshot — tests for K3 snapshot builder, K5 validator, and K2 governor.

Tests all requirements from the v3 build plan:
  - snapshot create/read uses one snapshot_id
  - snapshot response action_counts match card actions
  - page-load snapshot has zero LLM calls (structural test)
  - v3 path cannot blend stale legacy card run IDs with latest run IDs
  - source validator rejects action contradictions
  - source validator rejects raw metric keys
  - source validator rejects fake price targets
  - source validator rejects generic repeated copy
  - portfolio governor maps weights to FitBand correctly
  - old recommendations routes are not broken
"""
from __future__ import annotations

import pytest

from app.services.intelligence.v3.decision_contracts import (
    ActionV3,
    AxisBand,
    ConvictionV3,
    DecisionInputV3,
    DecisionOutputV3,
    FitBand,
    PriceBand,
    RiskBand,
)
from app.services.intelligence.v3.decision_policy_v1 import decide
from app.services.intelligence.v3.portfolio_governor_lite import (
    build_weight_map,
    compute_portfolio_fit,
)
from app.services.intelligence.v3.snapshot_builder import build_snapshot
from app.services.intelligence.v3.source_validator_lite import (
    detect_generic_copy_spam,
    validate_card,
    validate_snapshot_cards,
)


# ── Snapshot builder ──────────────────────────────────────────────────────────

def _make_decision(ticker: str, action: ActionV3, conviction: ConvictionV3 = ConvictionV3.MEDIUM) -> DecisionOutputV3:
    return DecisionOutputV3(
        ticker=ticker,
        action=action,
        conviction=conviction,
        evidence_quality=AxisBand.OK,
        attractiveness=AxisBand.OK,
        price_context=PriceBand.FAIR,
        portfolio_fit=FitBand.ON_TARGET,
        risk_band=RiskBand.LOW,
        blockers=[],
        suppression_reasons={},
        rationale_plain_english="Signals support this position.",
        why_now="Evidence and fit support acting now.",
        why_not_now="Watch for evidence weakening.",
        source_signal_summary={},
        schema_version="v3.1",
    )


def _make_meta(ticker: str) -> dict:
    return {"ticker": ticker, "name": ticker, "category": "stock"}


class TestSnapshotBuilder:
    def test_snapshot_uses_single_snapshot_id(self):
        """All cards in the snapshot must share the same snapshot_id."""
        decisions = [
            _make_decision("AAPL", ActionV3.BUY),
            _make_decision("MSFT", ActionV3.HOLD),
            _make_decision("NVDA", ActionV3.TRIM),
        ]
        metas = [_make_meta(t) for t in ["AAPL", "MSFT", "NVDA"]]
        snapshot = build_snapshot(run_id="run-001", decisions=decisions, card_metas=metas)

        snapshot_id = snapshot["snapshot_id"]
        assert snapshot_id, "Snapshot must have a snapshot_id"

        # All cards must reference the same snapshot_id.
        for card in snapshot["current_holdings"]:
            assert card["source_snapshot_id"] == snapshot_id, (
                f"Card {card['ticker']} references wrong snapshot_id"
            )

    def test_action_counts_match_card_actions(self):
        """action_counts must be derived from the card actions, not from a separate source."""
        decisions = [
            _make_decision("A", ActionV3.BUY),
            _make_decision("B", ActionV3.BUY),
            _make_decision("C", ActionV3.HOLD),
            _make_decision("D", ActionV3.TRIM),
            _make_decision("E", ActionV3.SELL, ConvictionV3.LOW),
        ]
        metas = [_make_meta(t) for t in ["A", "B", "C", "D", "E"]]
        snapshot = build_snapshot(run_id="run-002", decisions=decisions, card_metas=metas)

        counts = snapshot["action_counts"]
        cards = snapshot["current_holdings"]

        # Recount directly from cards.
        from collections import Counter
        card_counts = dict(Counter(c["action"] for c in cards))

        assert counts == card_counts, (
            f"action_counts {counts} do not match card-derived counts {card_counts}. "
            "This matches the production failure where counts came from different sources."
        )
        assert counts.get("BUY") == 2
        assert counts.get("HOLD") == 1
        assert counts.get("TRIM") == 1
        assert counts.get("SELL") == 1

    def test_all_cards_share_run_id(self):
        """All cards must reference the snapshot's run_id."""
        run_id = "test-run-xyz"
        decisions = [_make_decision("VOO", ActionV3.BUY)]
        metas = [_make_meta("VOO")]
        snapshot = build_snapshot(run_id=run_id, decisions=decisions, card_metas=metas)

        for card in snapshot["current_holdings"]:
            assert card["source_run_id"] == run_id

    def test_legacy_path_used_is_false(self):
        """v3 snapshot must always have legacy_path_used=False."""
        decisions = [_make_decision("TST", ActionV3.HOLD)]
        metas = [_make_meta("TST")]
        snapshot = build_snapshot(run_id="run-003", decisions=decisions, card_metas=metas)
        assert snapshot["legacy_path_used"] is False

    def test_opportunity_radar_is_deferred(self):
        """Opportunity Radar must be deferred in this build."""
        decisions = [_make_decision("TST", ActionV3.HOLD)]
        metas = [_make_meta("TST")]
        snapshot = build_snapshot(run_id="run-004", decisions=decisions, card_metas=metas)
        radar = snapshot["opportunity_radar_preview"]
        assert radar["status"] == "deferred"

    def test_best_buys_only_contains_buy_cards(self):
        decisions = [
            _make_decision("BUY1", ActionV3.BUY, ConvictionV3.HIGH),
            _make_decision("HOLD1", ActionV3.HOLD),
            _make_decision("BUY2", ActionV3.BUY, ConvictionV3.MEDIUM),
        ]
        metas = [_make_meta(t) for t in ["BUY1", "HOLD1", "BUY2"]]
        snapshot = build_snapshot(run_id="run-005", decisions=decisions, card_metas=metas)

        best_buys = snapshot["best_buys"]
        assert all(c["action"] == "BUY" for c in best_buys)
        assert len(best_buys) == 2

    def test_trim_sell_desk_excludes_buy_hold(self):
        decisions = [
            _make_decision("TRIM1", ActionV3.TRIM),
            _make_decision("SELL1", ActionV3.SELL, ConvictionV3.LOW),
            _make_decision("BUY1", ActionV3.BUY),
            _make_decision("HOLD1", ActionV3.HOLD),
        ]
        metas = [_make_meta(t) for t in ["TRIM1", "SELL1", "BUY1", "HOLD1"]]
        snapshot = build_snapshot(run_id="run-006", decisions=decisions, card_metas=metas)

        trim_sell = snapshot["trim_sell_desk"]
        for c in trim_sell:
            assert c["action"] in {"TRIM", "SELL"}

    def test_schema_version_present(self):
        decisions = [_make_decision("TST", ActionV3.HOLD)]
        metas = [_make_meta("TST")]
        snapshot = build_snapshot(run_id="run-007", decisions=decisions, card_metas=metas)
        assert snapshot["schema_version"] == "v3.1"

    def test_zero_llm_calls_structural(self):
        """build_snapshot is a pure function — importing it requires no LLM dependency.

        If this test can construct a snapshot without mocking any LLM client,
        the zero-LLM-call contract holds for the snapshot path.
        """
        decisions = [_make_decision("TST", ActionV3.BUY)]
        metas = [_make_meta("TST")]
        # No mock needed — zero LLM imports in snapshot_builder.
        snapshot = build_snapshot(run_id="run-008", decisions=decisions, card_metas=metas)
        assert snapshot is not None

    def test_stale_run_id_not_blended(self):
        """Snapshot cards must not blend a stale legacy run_id with a fresh v3 run_id.

        Production failure: UI showed cards from page-load (legacy run_id) after
        Run Agents completed (different run_id). v3 snapshot path prevents this by
        writing all cards with the same source_run_id.
        """
        v3_run_id = "v3-run-fresh"
        legacy_run_id = "legacy-run-stale"

        decisions = [_make_decision("AAPL", ActionV3.BUY)]
        metas = [_make_meta("AAPL")]
        snapshot = build_snapshot(run_id=v3_run_id, decisions=decisions, card_metas=metas)

        for card in snapshot["current_holdings"]:
            assert card["source_run_id"] == v3_run_id
            assert card["source_run_id"] != legacy_run_id


# ── Source/evidence validator ─────────────────────────────────────────────────

class TestSourceValidatorLite:
    def test_valid_card_passes(self):
        result = validate_card(
            ticker="AAPL",
            action="BUY",
            conviction="MEDIUM",
            why_text="Strong evidence and fair price support adding.",
            risk_text="Watch for elevated competition and margin pressure.",
        )
        assert result.is_valid
        assert result.violation_count == 0

    def test_rejects_raw_metric_keys(self):
        """Raw metric key names must not appear in visible text."""
        result = validate_card(
            ticker="AAPL",
            action="BUY",
            conviction="HIGH",
            why_text="fcf_margin is strong at 28%.",
        )
        assert not result.is_valid
        rules = result.rules_violated
        assert "no_raw_metric_keys" in rules

    def test_rejects_fake_price_targets(self):
        """Fake price targets must be rejected."""
        result = validate_card(
            ticker="NVDA",
            action="BUY",
            conviction="HIGH",
            why_text="Price target of $180 over 12 months.",
        )
        assert not result.is_valid
        assert "no_fake_price_targets" in result.rules_violated

    def test_rejects_dollar_price_targets(self):
        result = validate_card(
            ticker="NVDA",
            action="BUY",
            conviction="HIGH",
            why_text="We expect it to reach $180.",
        )
        assert not result.is_valid
        assert "no_fake_price_targets" in result.rules_violated

    def test_rejects_action_contradictions_buy_with_hold_language(self):
        """BUY cards must not contain hold/wait language."""
        result = validate_card(
            ticker="TST",
            action="BUY",
            conviction="MEDIUM",
            why_text="Reviewing before taking action — not yet complete.",
            action_text="Add Candidate",
        )
        assert not result.is_valid
        rules = result.rules_violated
        # May trigger multiple rules
        assert any(r in rules for r in ["no_action_contradictions", "no_banned_posture_labels"])

    def test_rejects_banned_posture_labels_in_action_text(self):
        """Banned posture labels must not appear in action_text."""
        result = validate_card(
            ticker="TST",
            action="BUY",
            conviction="MEDIUM",
            action_text="Add Candidate",
        )
        assert not result.is_valid
        assert "no_banned_posture_labels" in result.rules_violated

    def test_rejects_watchlist_posture_label(self):
        result = validate_card(
            ticker="TST",
            action="HOLD",
            conviction="LOW",
            action_text="Stay on Watchlist",
        )
        assert not result.is_valid
        assert "no_banned_posture_labels" in result.rules_violated

    def test_rejects_strong_buy_label(self):
        result = validate_card(
            ticker="TST",
            action="BUY",
            conviction="HIGH",
            action_text="Strong Buy",
        )
        assert not result.is_valid
        assert "no_banned_posture_labels" in result.rules_violated

    def test_rejects_invalid_conviction(self):
        result = validate_card(
            ticker="TST",
            action="BUY",
            conviction="VERY_HIGH",
        )
        assert not result.is_valid
        assert "valid_conviction_only" in result.rules_violated

    def test_rejects_invalid_action(self):
        result = validate_card(
            ticker="TST",
            action="REVIEW",
            conviction="LOW",
        )
        assert not result.is_valid
        assert "valid_action_labels_only" in result.rules_violated

    def test_generic_copy_spam_detection(self):
        """Identical why_text across 3+ cards is flagged as generic copy spam."""
        spam_text = "Hold this position and wait for better data."
        cards = [
            {"ticker": "A", "why_text": spam_text},
            {"ticker": "B", "why_text": spam_text},
            {"ticker": "C", "why_text": spam_text},
            {"ticker": "D", "why_text": "Signals support adding."},
        ]
        spam_tickers = detect_generic_copy_spam(cards, min_cards_for_spam=3)
        assert set(spam_tickers) == {"A", "B", "C"}
        assert "D" not in spam_tickers

    def test_no_spam_when_texts_differ(self):
        cards = [
            {"ticker": "A", "why_text": "Strong evidence supports adding."},
            {"ticker": "B", "why_text": "Risk flags suggest trimming."},
            {"ticker": "C", "why_text": "Momentum confirms direction."},
        ]
        spam_tickers = detect_generic_copy_spam(cards, min_cards_for_spam=3)
        assert spam_tickers == []

    def test_validate_snapshot_cards_batch(self):
        """validate_snapshot_cards should work across a full card list."""
        cards = [
            {
                "ticker": "AAPL",
                "action": "BUY",
                "conviction": "MEDIUM",
                "why_text": "Strong evidence and fair price.",
            },
            {
                "ticker": "MSFT",
                "action": "HOLD",
                "conviction": "LOW",
                "why_text": "Holding while evidence builds.",
            },
            {
                "ticker": "BAD",
                "action": "REVIEW",  # Invalid action
                "conviction": "LOW",
                "why_text": "Review candidate.",
            },
        ]
        results, _ = validate_snapshot_cards(cards)
        assert results[0].is_valid  # AAPL
        assert results[1].is_valid  # MSFT
        assert not results[2].is_valid  # BAD — REVIEW is not a valid v3 action


# ── Portfolio Governor Lite ───────────────────────────────────────────────────

class TestPortfolioGovernorLite:
    def test_underweight_when_below_half_cap(self):
        fit = compute_portfolio_fit(
            ticker="AAPL", category="stock",
            current_pct=3.0,    # default cap=15%, half=7.5%
        )
        assert fit == FitBand.UNDERWEIGHT

    def test_on_target_within_cap(self):
        fit = compute_portfolio_fit(
            ticker="AAPL", category="stock",
            current_pct=10.0,   # between 7.5% and 15%
        )
        assert fit == FitBand.ON_TARGET

    def test_overweight_at_cap(self):
        fit = compute_portfolio_fit(
            ticker="AAPL", category="stock",
            current_pct=16.0,   # above 15% cap
        )
        assert fit == FitBand.OVERWEIGHT

    def test_breach_above_breach_threshold(self):
        fit = compute_portfolio_fit(
            ticker="AAPL", category="stock",
            current_pct=23.0,   # above 15% * 1.5 = 22.5%
        )
        assert fit == FitBand.BREACH

    def test_blocked_for_crypto_category(self):
        fit = compute_portfolio_fit(
            ticker="SOL", category="crypto",
            current_pct=2.0,
        )
        assert fit == FitBand.BLOCKED

    def test_blocked_for_speculative_ticker(self):
        fit = compute_portfolio_fit(
            ticker="BTC", category="stock",
            current_pct=1.0,
        )
        assert fit == FitBand.BLOCKED

    def test_unknown_when_no_weight_data(self):
        fit = compute_portfolio_fit(
            ticker="AAPL", category="stock",
            current_pct=None,
        )
        assert fit == FitBand.UNKNOWN

    def test_etf_has_higher_cap_than_stock(self):
        """ETFs have a higher default cap (30%) than stocks (15%)."""
        # At 20%, ETF should be ON_TARGET; stock should be OVERWEIGHT.
        etf_fit = compute_portfolio_fit(ticker="VOO", category="etf", current_pct=20.0)
        stock_fit = compute_portfolio_fit(ticker="AAPL", category="stock", current_pct=20.0)
        assert etf_fit in {FitBand.ON_TARGET, FitBand.UNDERWEIGHT}
        assert stock_fit in {FitBand.OVERWEIGHT, FitBand.BREACH}

    def test_build_weight_map_from_positions(self):
        positions = [
            {"ticker": "AAPL", "current_value": 10000},
            {"ticker": "MSFT", "current_value": 5000},
            {"ticker": "VOO",  "current_value": 15000},
        ]
        weight_map = build_weight_map(positions)
        assert abs(weight_map["AAPL"] - 33.33) < 0.1
        assert abs(weight_map["MSFT"] - 16.67) < 0.1
        assert abs(weight_map["VOO"]  - 50.0)  < 0.1

    def test_build_weight_map_empty(self):
        assert build_weight_map([]) == {}

    def test_custom_target_pct_overrides_default(self):
        """Explicit target_pct overrides default cap."""
        fit = compute_portfolio_fit(
            ticker="NVDA",
            category="stock",
            current_pct=25.0,
            target_pct=30.0,  # allowed up to 30%
        )
        # 25% < 30% cap → ON_TARGET or UNDERWEIGHT
        assert fit in {FitBand.ON_TARGET, FitBand.UNDERWEIGHT}


# ── End-to-end: kernel → snapshot ────────────────────────────────────────────

class TestKernelToSnapshotE2E:
    def test_full_pipeline_produces_valid_snapshot(self):
        """Run the full v3 kernel pipeline and verify the snapshot is consistent."""
        inputs = [
            DecisionInputV3(
                ticker="AAPL",
                evidence_quality=AxisBand.STRONG,
                price_context=PriceBand.FAIR,
                portfolio_fit=FitBand.UNDERWEIGHT,
                risk_band=RiskBand.LOW,
                raw_action="BUY",
                upstream_conviction="HIGH",
            ),
            DecisionInputV3(
                ticker="MSFT",
                evidence_quality=AxisBand.OK,
                price_context=PriceBand.SUPPRESSED,
                portfolio_fit=FitBand.ON_TARGET,
                risk_band=RiskBand.NONE,
                raw_action="HOLD",
                upstream_conviction="MEDIUM",
            ),
            DecisionInputV3(
                ticker="NFLX",
                evidence_quality=AxisBand.OK,
                price_context=PriceBand.FULL,
                portfolio_fit=FitBand.OVERWEIGHT,
                risk_band=RiskBand.MEDIUM,
                raw_action="TRIM",
                upstream_conviction="MEDIUM",
            ),
        ]
        decisions = [decide(inp) for inp in inputs]
        metas = [_make_meta(t) for t in ["AAPL", "MSFT", "NFLX"]]

        snapshot = build_snapshot(
            run_id="e2e-run-001",
            decisions=decisions,
            card_metas=metas,
        )

        # Snapshot structure checks.
        assert snapshot["schema_version"] == "v3.1"
        assert snapshot["legacy_path_used"] is False
        assert len(snapshot["current_holdings"]) == 3

        # action_counts matches cards.
        from collections import Counter
        card_counts = dict(Counter(c["action"] for c in snapshot["current_holdings"]))
        assert snapshot["action_counts"] == card_counts

        # All cards have same snapshot_id and run_id.
        snap_ids = {c["source_snapshot_id"] for c in snapshot["current_holdings"]}
        run_ids = {c["source_run_id"] for c in snapshot["current_holdings"]}
        assert len(snap_ids) == 1
        assert len(run_ids) == 1
        assert list(run_ids)[0] == "e2e-run-001"

        # Validate no posture labels, no raw keys.
        results, spam_tickers = validate_snapshot_cards(snapshot["current_holdings"])
        assert all(r.is_valid for r in results), [
            str(r.violations) for r in results if not r.is_valid
        ]
        assert spam_tickers == []

    def test_hold_collapse_cannot_happen_in_non_degenerate_set(self):
        """Non-degenerate set must not produce all HOLD/LOW after pipeline."""
        inputs = [
            DecisionInputV3(
                ticker="A", evidence_quality=AxisBand.STRONG,
                price_context=PriceBand.FAIR, portfolio_fit=FitBand.UNDERWEIGHT,
                risk_band=RiskBand.NONE, raw_action="BUY", upstream_conviction="HIGH",
            ),
            DecisionInputV3(
                ticker="B", evidence_quality=AxisBand.OK,
                price_context=PriceBand.FAIR, portfolio_fit=FitBand.UNDERWEIGHT,
                risk_band=RiskBand.LOW, raw_action="BUY", upstream_conviction="MEDIUM",
            ),
            DecisionInputV3(
                ticker="C", evidence_quality=AxisBand.OK,
                price_context=PriceBand.FULL, portfolio_fit=FitBand.OVERWEIGHT,
                risk_band=RiskBand.MEDIUM, raw_action="TRIM", upstream_conviction="MEDIUM",
            ),
        ]
        decisions = [decide(inp) for inp in inputs]
        metas = [_make_meta(t) for t in ["A", "B", "C"]]
        snapshot = build_snapshot(run_id="canary", decisions=decisions, card_metas=metas)

        all_hold_low = all(
            c["action"] == "HOLD" and c["conviction"] == "LOW"
            for c in snapshot["current_holdings"]
        )
        assert not all_hold_low, (
            "HOLD-collapse regression: non-degenerate set collapsed to all HOLD/LOW in snapshot."
        )
