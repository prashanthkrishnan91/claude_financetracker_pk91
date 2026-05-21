"""Stage 7 — Evidence explanation contract tests.

Covers the backend contract that embeds evidence_explanation into
detail_drawer_payload, and ensures no raw metric keys or internal
diagnostic field names leak into the frontend-facing payload.

Regression risks covered:
  A. _build_evidence_explanation: correct field mapping from governance result.
  B. No raw metric keys in any evidence_explanation field.
  C. BTC/XRP-style blocked state is correctly represented.
  D. Missing technical / sentiment shown as MISSING, not as supporting.
  E. Conviction cap fields are correctly populated.
  F. evidence_explanation is None when no governance_result in card_meta.
  G. _build_held_card produces evidence_explanation in detail_drawer_payload.
  H. safe_for_visible_decision=True/False round-trips correctly.
"""

from __future__ import annotations

import pytest

from app.services.intelligence.v3.snapshot_builder import (
    _build_evidence_explanation,
    _build_held_card,
    _build_synthetic_evidence_explanation,
)
from app.services.intelligence.v3.decision_contracts import (
    ActionV3,
    AxisBand,
    ConvictionV3,
    DecisionOutputV3,
    FitBand,
    PriceBand,
    RiskBand,
)
from unittest.mock import patch


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_gov_result(**overrides) -> dict:
    """Build a minimal governance result dict, matching EvidenceGovernanceResult.to_dict()."""
    base = {
        "ticker": "AAPL",
        "flag_enabled": True,
        "governance_applied": True,
        "original_evidence_quality": AxisBand.THIN.value,
        "governed_evidence_quality": AxisBand.OK.value,
        "conviction_cap_applied": True,
        "conviction_cap_reason": "ok_cap_medium",
        "evidence_governance_status": "active",
        "supported_axis_count": 1,
        "missing_axis_count": 2,
        "degraded_axis_count": 0,
        "not_applicable_axis_count": 0,
        "company_fundamentals_readiness": "LIMITED",
        "technical_signals_readiness": "MISSING",
        "sentiment_readiness": "SUPPRESSED_INCOMPLETE",
        "portfolio_macro_readiness": "LIMITED",
        "action_blocks_applied": [],
        "safe_for_visible_decision": True,
        "reason_codes": ["ok_with_cap"],
        "primary_evidence_readiness": "LIMITED",
        "auxiliary_evidence_readiness": {
            "technical_signals": "MISSING",
            "sentiment": "SUPPRESSED_INCOMPLETE",
        },
        "corroboration_gap": True,
        "governance_priority_applied": "p4b_limited_no_corroboration",
        "safe_for_visible_decision_reason": "limited_fundamentals_no_corroboration_ok_with_cap",
    }
    base.update(overrides)
    return base


def _make_decision() -> DecisionOutputV3:
    """Minimal valid DecisionOutputV3 for card building."""
    return DecisionOutputV3(
        ticker="AAPL",
        action=ActionV3.HOLD,
        conviction=ConvictionV3.MEDIUM,
        evidence_quality=AxisBand.OK,
        attractiveness=AxisBand.OK,
        price_context=PriceBand.FAIR,
        portfolio_fit=FitBand.ON_TARGET,
        risk_band=RiskBand.LOW,
        rationale_plain_english="Solid business held at target weight.",
        why_now="",
        why_not_now="",
        blockers=[],
        suppression_reasons={},
        source_signal_summary={},
        schema_version="v3.1",
    )


def _make_card_meta(**overrides) -> dict:
    base = {
        "ticker": "AAPL",
        "name": "Apple Inc.",
        "category": "stock",
        "thesis_state": "intact",
        "governance_result": None,
    }
    base.update(overrides)
    return base


# ── Section A: _build_evidence_explanation field mapping ─────────────────────

class TestBuildEvidenceExplanation:
    def test_primary_evidence_status_mapped(self):
        gov = _make_gov_result(primary_evidence_readiness="READY")
        ex = _build_evidence_explanation(gov)
        assert ex["primary_evidence_status"] == "READY"

    def test_technical_signals_status_mapped(self):
        gov = _make_gov_result(auxiliary_evidence_readiness={"technical_signals": "MISSING", "sentiment": "SUPPRESSED"})
        ex = _build_evidence_explanation(gov)
        assert ex["technical_signals_status"] == "MISSING"

    def test_sentiment_status_mapped(self):
        gov = _make_gov_result(auxiliary_evidence_readiness={"technical_signals": "READY", "sentiment": "SUPPRESSED_INCOMPLETE"})
        ex = _build_evidence_explanation(gov)
        assert ex["sentiment_status"] == "SUPPRESSED_INCOMPLETE"

    def test_conviction_cap_fields(self):
        gov = _make_gov_result(conviction_cap_applied=True, conviction_cap_reason="ok_cap_medium")
        ex = _build_evidence_explanation(gov)
        assert ex["conviction_cap_applied"] is True
        assert ex["conviction_cap_reason"] == "ok_cap_medium"

    def test_conviction_cap_false(self):
        gov = _make_gov_result(conviction_cap_applied=False, conviction_cap_reason=None)
        ex = _build_evidence_explanation(gov)
        assert ex["conviction_cap_applied"] is False
        assert ex["conviction_cap_reason"] is None

    def test_safe_for_visible_decision_true(self):
        gov = _make_gov_result(safe_for_visible_decision=True)
        ex = _build_evidence_explanation(gov)
        assert ex["safe_for_visible_decision"] is True

    def test_safe_for_visible_decision_false(self):
        gov = _make_gov_result(safe_for_visible_decision=False)
        ex = _build_evidence_explanation(gov)
        assert ex["safe_for_visible_decision"] is False

    def test_governance_priority_mapped(self):
        gov = _make_gov_result(governance_priority_applied="p4b_limited_no_corroboration")
        ex = _build_evidence_explanation(gov)
        assert ex["governance_priority"] == "p4b_limited_no_corroboration"

    def test_corroboration_gap_mapped(self):
        gov = _make_gov_result(corroboration_gap=True)
        ex = _build_evidence_explanation(gov)
        assert ex["corroboration_gap"] is True

    def test_action_blocks_mapped(self):
        gov = _make_gov_result(action_blocks_applied=["buy_blocked_thin_evidence"])
        ex = _build_evidence_explanation(gov)
        assert ex["action_blocks"] == ["buy_blocked_thin_evidence"]

    def test_empty_auxiliary_evidence_readiness(self):
        gov = _make_gov_result(auxiliary_evidence_readiness={})
        ex = _build_evidence_explanation(gov)
        assert ex["technical_signals_status"] == "MISSING"
        assert ex["sentiment_status"] == "MISSING"

    def test_missing_auxiliary_evidence_readiness_key(self):
        gov = _make_gov_result()
        del gov["auxiliary_evidence_readiness"]
        ex = _build_evidence_explanation(gov)
        assert ex["technical_signals_status"] == "MISSING"
        assert ex["sentiment_status"] == "MISSING"


# ── Section B: No raw metric keys in evidence_explanation ────────────────────

RAW_KEYS = [
    "fcf_margin", "roic_ttm", "ev_ebitda", "peg_ratio", "gross_margin_ttm",
    "revenue_growth_ttm", "debt_to_equity", "current_ratio", "quick_ratio",
]

class TestNoRawMetricKeyLeak:
    def test_no_raw_metric_keys_in_primary_evidence_status(self):
        gov = _make_gov_result(primary_evidence_readiness="LIMITED")
        ex = _build_evidence_explanation(gov)
        for key in RAW_KEYS:
            assert key not in str(ex["primary_evidence_status"]).lower()

    def test_no_raw_metric_keys_in_safe_reason(self):
        gov = _make_gov_result(safe_for_visible_decision_reason="limited_fundamentals_no_corroboration_ok_with_cap")
        ex = _build_evidence_explanation(gov)
        for key in RAW_KEYS:
            assert key not in str(ex["safe_for_visible_decision_reason"]).lower()

    def test_evidence_explanation_keys_are_frontend_safe(self):
        gov = _make_gov_result()
        ex = _build_evidence_explanation(gov)
        # These raw internal field names must NOT appear as keys in the output.
        # They are renamed to shorter, frontend-friendly equivalents.
        internal_keys = [
            "primary_evidence_readiness",    # → primary_evidence_status
            "auxiliary_evidence_readiness",  # → technical_signals_status + sentiment_status
            "governance_priority_applied",   # → governance_priority
        ]
        for key in internal_keys:
            assert key not in ex, f"Internal key '{key}' leaked into frontend contract"


# ── Section C: BTC/XRP-style blocked state ───────────────────────────────────

class TestBtcXrpBlockedState:
    def test_suppressed_fundamentals_block_reflected(self):
        gov = _make_gov_result(
            primary_evidence_readiness="SUPPRESSED",
            action_blocks_applied=["buy_blocked_suppressed_evidence"],
            safe_for_visible_decision=False,
        )
        ex = _build_evidence_explanation(gov)
        assert ex["primary_evidence_status"] == "SUPPRESSED"
        assert "buy_blocked_suppressed_evidence" in ex["action_blocks"]
        assert ex["safe_for_visible_decision"] is False

    def test_crypto_thin_reflected(self):
        gov = _make_gov_result(
            primary_evidence_readiness="LIMITED",
            governance_priority_applied="p4b_limited_no_corroboration",
            safe_for_visible_decision=False,
            action_blocks_applied=["buy_blocked_thin_crypto"],
        )
        ex = _build_evidence_explanation(gov)
        assert ex["safe_for_visible_decision"] is False
        assert len(ex["action_blocks"]) > 0


# ── Section D: Technical/sentiment shown as missing/unusable ─────────────────

class TestMissingTechSentiment:
    def test_missing_technical_not_usable(self):
        gov = _make_gov_result(auxiliary_evidence_readiness={"technical_signals": "MISSING", "sentiment": "MISSING"})
        ex = _build_evidence_explanation(gov)
        assert ex["technical_signals_status"] == "MISSING"
        assert ex["sentiment_status"] == "MISSING"

    def test_suppressed_sentiment_not_usable(self):
        gov = _make_gov_result(auxiliary_evidence_readiness={"technical_signals": "MISSING", "sentiment": "SUPPRESSED_INCOMPLETE"})
        ex = _build_evidence_explanation(gov)
        assert ex["sentiment_status"] == "SUPPRESSED_INCOMPLETE"
        # Not a "READY" or "LIMITED" value — frontend can detect non-usable
        assert ex["sentiment_status"] not in ("READY", "LIMITED")

    def test_corroboration_gap_true_when_tech_sentiment_missing(self):
        gov = _make_gov_result(corroboration_gap=True)
        ex = _build_evidence_explanation(gov)
        assert ex["corroboration_gap"] is True


# ── Section E: Conviction cap ─────────────────────────────────────────────────

class TestConvictionCap:
    def test_cap_applied_true_round_trips(self):
        gov = _make_gov_result(conviction_cap_applied=True, conviction_cap_reason="ok_cap_medium")
        ex = _build_evidence_explanation(gov)
        assert ex["conviction_cap_applied"] is True
        assert ex["conviction_cap_reason"] == "ok_cap_medium"

    def test_cap_applied_false_round_trips(self):
        gov = _make_gov_result(conviction_cap_applied=False, conviction_cap_reason=None)
        ex = _build_evidence_explanation(gov)
        assert ex["conviction_cap_applied"] is False
        assert ex["conviction_cap_reason"] is None

    def test_cap_reason_none_when_not_applied(self):
        gov = _make_gov_result(conviction_cap_applied=False, conviction_cap_reason=None)
        ex = _build_evidence_explanation(gov)
        assert ex.get("conviction_cap_reason") is None


# ── Section F: Synthetic explanation when no governance_result ───────────────

class TestSyntheticWhenNoGovernance:
    """Stage 7C: when governance is inactive, a synthetic explanation is built
    from the decision's evidence_quality band so the drawer shows structured
    sections instead of the generic fallback text."""

    def test_none_governance_result_produces_synthetic_evidence_explanation(self):
        card_meta = _make_card_meta(governance_result=None)
        decision = _make_decision()
        card = _build_held_card(
            decision=decision,
            card_meta=card_meta,
            snapshot_id="snap-001",
            run_id="run-001",
            valuation_context=None,
        )
        ex = card["detail_drawer_payload"]["evidence_explanation"]
        assert ex is not None
        assert ex["governance_priority"] == "governance_inactive"

    def test_missing_governance_result_key_produces_synthetic(self):
        card_meta = {
            "ticker": "AAPL",
            "name": "Apple Inc.",
            "category": "stock",
            "thesis_state": "intact",
            # No "governance_result" key at all
        }
        decision = _make_decision()
        card = _build_held_card(
            decision=decision,
            card_meta=card_meta,
            snapshot_id="snap-001",
            run_id="run-001",
        )
        ex = card["detail_drawer_payload"]["evidence_explanation"]
        assert ex is not None
        assert ex["governance_priority"] == "governance_inactive"

    def test_synthetic_technical_and_sentiment_are_missing(self):
        card_meta = _make_card_meta(governance_result=None)
        decision = _make_decision()
        card = _build_held_card(
            decision=decision,
            card_meta=card_meta,
            snapshot_id="snap-001",
            run_id="run-001",
        )
        ex = card["detail_drawer_payload"]["evidence_explanation"]
        assert ex["technical_signals_status"] == "MISSING"
        assert ex["sentiment_status"] == "MISSING"


# ── Section I: _build_synthetic_evidence_explanation ─────────────────────────

class TestBuildSyntheticEvidenceExplanation:
    """Covers the synthetic path for all AxisBand values and key properties."""

    def _decision_with_band(self, band: "AxisBand") -> "DecisionOutputV3":
        return DecisionOutputV3(
            ticker="MSFT",
            action=ActionV3.BUY,
            conviction=ConvictionV3.MEDIUM,
            evidence_quality=band,
            attractiveness=AxisBand.OK,
            price_context=PriceBand.FAIR,
            portfolio_fit=FitBand.UNDERWEIGHT,
            risk_band=RiskBand.LOW,
            rationale_plain_english="Azure growth supports a BUY.",
            why_now="",
            why_not_now="",
            blockers=[],
            suppression_reasons={},
            source_signal_summary={},
            schema_version="v3.1",
        )

    def test_strong_band_maps_to_ready(self):
        ex = _build_synthetic_evidence_explanation(self._decision_with_band(AxisBand.STRONG))
        assert ex["primary_evidence_status"] == "READY"

    def test_ok_band_maps_to_limited(self):
        ex = _build_synthetic_evidence_explanation(self._decision_with_band(AxisBand.OK))
        assert ex["primary_evidence_status"] == "LIMITED"

    def test_thin_band_maps_to_insufficient(self):
        ex = _build_synthetic_evidence_explanation(self._decision_with_band(AxisBand.THIN))
        assert ex["primary_evidence_status"] == "INSUFFICIENT"

    def test_suppressed_band_maps_to_suppressed(self):
        ex = _build_synthetic_evidence_explanation(self._decision_with_band(AxisBand.SUPPRESSED))
        assert ex["primary_evidence_status"] == "SUPPRESSED"

    def test_ok_band_has_conviction_cap(self):
        ex = _build_synthetic_evidence_explanation(self._decision_with_band(AxisBand.OK))
        assert ex["conviction_cap_applied"] is True
        assert ex["conviction_cap_reason"] == "ok_cap_medium"

    def test_thin_band_has_conviction_cap(self):
        ex = _build_synthetic_evidence_explanation(self._decision_with_band(AxisBand.THIN))
        assert ex["conviction_cap_applied"] is True
        assert ex["conviction_cap_reason"] == "band_thin"

    def test_strong_band_no_cap(self):
        ex = _build_synthetic_evidence_explanation(self._decision_with_band(AxisBand.STRONG))
        assert ex["conviction_cap_applied"] is False
        assert ex["conviction_cap_reason"] is None

    def test_ok_band_safe_for_visible_decision(self):
        ex = _build_synthetic_evidence_explanation(self._decision_with_band(AxisBand.OK))
        assert ex["safe_for_visible_decision"] is True

    def test_thin_band_not_safe(self):
        ex = _build_synthetic_evidence_explanation(self._decision_with_band(AxisBand.THIN))
        assert ex["safe_for_visible_decision"] is False

    def test_suppressed_band_not_safe(self):
        ex = _build_synthetic_evidence_explanation(self._decision_with_band(AxisBand.SUPPRESSED))
        assert ex["safe_for_visible_decision"] is False

    def test_governance_priority_is_inactive(self):
        for band in (AxisBand.STRONG, AxisBand.OK, AxisBand.THIN, AxisBand.SUPPRESSED):
            ex = _build_synthetic_evidence_explanation(self._decision_with_band(band))
            assert ex["governance_priority"] == "governance_inactive"

    def test_technical_and_sentiment_always_missing(self):
        for band in (AxisBand.STRONG, AxisBand.OK, AxisBand.THIN, AxisBand.SUPPRESSED):
            ex = _build_synthetic_evidence_explanation(self._decision_with_band(band))
            assert ex["technical_signals_status"] == "MISSING"
            assert ex["sentiment_status"] == "MISSING"

    def test_strong_band_no_corroboration_gap(self):
        ex = _build_synthetic_evidence_explanation(self._decision_with_band(AxisBand.STRONG))
        assert ex["corroboration_gap"] is False

    def test_ok_band_has_corroboration_gap(self):
        ex = _build_synthetic_evidence_explanation(self._decision_with_band(AxisBand.OK))
        assert ex["corroboration_gap"] is True

    def test_blockers_from_decision(self):
        d = self._decision_with_band(AxisBand.SUPPRESSED)
        d.blockers = ["buy_blocked_suppressed_evidence"]
        ex = _build_synthetic_evidence_explanation(d)
        assert "buy_blocked_suppressed_evidence" in ex["action_blocks"]

    def test_no_internal_keys_in_output(self):
        ex = _build_synthetic_evidence_explanation(self._decision_with_band(AxisBand.OK))
        internal_keys = ["primary_evidence_readiness", "auxiliary_evidence_readiness", "governance_priority_applied"]
        for key in internal_keys:
            assert key not in ex

    def test_msft_like_buy_partial_produces_correct_explanation(self):
        """MSFT BUY/MEDIUM/PARTIAL → LIMITED fundamentals, MISSING tech/sentiment, cap applied."""
        d = DecisionOutputV3(
            ticker="MSFT",
            action=ActionV3.BUY,
            conviction=ConvictionV3.MEDIUM,
            evidence_quality=AxisBand.OK,
            attractiveness=AxisBand.OK,
            price_context=PriceBand.FAIR,
            portfolio_fit=FitBand.UNDERWEIGHT,
            risk_band=RiskBand.MEDIUM,
            rationale_plain_english="Azure growth + capex risk.",
            why_now="",
            why_not_now="",
            blockers=[],
            suppression_reasons={},
            source_signal_summary={},
            schema_version="v3.1",
        )
        ex = _build_synthetic_evidence_explanation(d)
        assert ex["primary_evidence_status"] == "LIMITED"
        assert ex["technical_signals_status"] == "MISSING"
        assert ex["sentiment_status"] == "MISSING"
        assert ex["conviction_cap_applied"] is True
        assert ex["conviction_cap_reason"] == "ok_cap_medium"
        assert ex["safe_for_visible_decision"] is True
        assert ex["governance_priority"] == "governance_inactive"
        assert ex["corroboration_gap"] is True


# ── Section G: _build_held_card produces evidence_explanation ────────────────

class TestBuildHeldCardEvidenceExplanation:
    def test_evidence_explanation_present_when_governance_result_given(self):
        gov = _make_gov_result()
        card_meta = _make_card_meta(governance_result=gov)
        card = _build_held_card(
            decision=_make_decision(),
            card_meta=card_meta,
            snapshot_id="snap-001",
            run_id="run-001",
        )
        ex = card["detail_drawer_payload"]["evidence_explanation"]
        assert ex is not None
        assert "primary_evidence_status" in ex
        assert "technical_signals_status" in ex
        assert "sentiment_status" in ex
        assert "conviction_cap_applied" in ex
        assert "safe_for_visible_decision" in ex
        assert "governance_priority" in ex
        assert "corroboration_gap" in ex
        assert "action_blocks" in ex

    def test_evidence_explanation_is_dict(self):
        gov = _make_gov_result()
        card_meta = _make_card_meta(governance_result=gov)
        card = _build_held_card(
            decision=_make_decision(),
            card_meta=card_meta,
            snapshot_id="snap-001",
            run_id="run-001",
        )
        ex = card["detail_drawer_payload"]["evidence_explanation"]
        assert isinstance(ex, dict)

    def test_evidence_explanation_has_no_internal_readiness_keys(self):
        gov = _make_gov_result()
        card_meta = _make_card_meta(governance_result=gov)
        card = _build_held_card(
            decision=_make_decision(),
            card_meta=card_meta,
            snapshot_id="snap-001",
            run_id="run-001",
        )
        ex = card["detail_drawer_payload"]["evidence_explanation"]
        assert "primary_evidence_readiness" not in ex
        assert "auxiliary_evidence_readiness" not in ex
        assert "governance_priority_applied" not in ex

    def test_action_label_in_card_is_buy_hold_trim_sell_only(self):
        gov = _make_gov_result()
        card_meta = _make_card_meta(governance_result=gov)
        card = _build_held_card(
            decision=_make_decision(),
            card_meta=card_meta,
            snapshot_id="snap-001",
            run_id="run-001",
        )
        assert card["action"] in {"BUY", "HOLD", "TRIM", "SELL"}


# ── Section H: safe_for_visible_decision round-trip ─────────────────────────

class TestSafeForVisibleDecisionRoundTrip:
    def test_safe_true_round_trips(self):
        gov = _make_gov_result(safe_for_visible_decision=True)
        ex = _build_evidence_explanation(gov)
        assert ex["safe_for_visible_decision"] is True

    def test_safe_false_round_trips(self):
        gov = _make_gov_result(safe_for_visible_decision=False)
        ex = _build_evidence_explanation(gov)
        assert ex["safe_for_visible_decision"] is False
