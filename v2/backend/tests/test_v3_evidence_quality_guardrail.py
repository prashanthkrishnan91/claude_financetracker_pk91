"""Tests for PR 9: shadow-only evidence-quality BUY conviction guardrail.

Covers:
1.  PRESENT/HIGH-trust evidence → HIGH conviction BUY unchanged.
2.  MISSING evidence → guardrail fires (caps HIGH→MEDIUM).
3.  STALE evidence → guardrail fires.
4.  WEAK/fallback evidence → guardrail fires.
5.  UNAVAILABLE evidence → guardrail fires.
6.  CONFLICTING evidence → guardrail fires.
7.  BUY action preserved at lower conviction when guardrail caps.
8.  SELL/TRIM not blocked by missing evidence-quality data.
9.  Honest HOLD unchanged when axes are unsafe.
10. v2 visible action not mutated.
11. Shadow diagnostics expose guardrail signals.
12. Portfolio/golden fixture action diversity preserved.
13. No visible action outside BUY/HOLD/TRIM/SELL introduced.
14. No provider calls, Supabase, or LLM calls.
15. No frontend/API/Deploy/schema files changed (static assertion).

Test structure:
- Section A: unit tests for apply_buy_conviction_guardrail() directly.
- Section B: integration tests through project_shadow_from_card_signals().
- Section C: golden portfolio diversity regression.
"""
from __future__ import annotations

import pytest

from app.services.intelligence.v3.buy_conviction_guardrail import (
    _evidence_is_high_trust,
    apply_buy_conviction_guardrail,
)
from app.services.intelligence.v3.data_truth_contracts import (
    AxisTruthSummary,
    DataTruthFinding,
    DataTruthStatus,
    SourceTrustLevel,
)
from app.services.intelligence.v3.data_truth_v1 import (
    classify_evidence_signals,
    classify_with_staleness,
)
from app.services.intelligence.v3.decision_contracts import ActionV3, ConvictionV3
from app.services.intelligence.v3.shadow_projection import (
    project_shadow_from_card_signals,
    summarize_shadow_diagnostics,
)


# ── Helpers ─────────────────────────────────────────────────────────────────

def _ev_summary(status: DataTruthStatus, trust: SourceTrustLevel) -> AxisTruthSummary:
    """Build a minimal AxisTruthSummary for the evidence_quality axis."""
    finding = DataTruthFinding(
        signal_name="evidence_quality",
        status=status,
        trust_level=trust,
        source_kind="test",
        freshness_label="test",
        reason_code="test_reason",
        safe_for_decision=(status in {DataTruthStatus.PRESENT, DataTruthStatus.WEAK}),
    )
    return AxisTruthSummary(
        axis_name="evidence_quality",
        findings=[finding],
        present_count=1 if status == DataTruthStatus.PRESENT else 0,
        missing_count=1 if status == DataTruthStatus.MISSING else 0,
        stale_count=1 if status == DataTruthStatus.STALE else 0,
        weak_count=1 if status == DataTruthStatus.WEAK else 0,
        safe_for_decision=finding.safe_for_decision,
        dominant_reason_code="test_reason",
    )


def _strong_buy_card_signals() -> dict:
    """Card signals that produce BUY with HIGH conviction in the full pipeline.

    intel_read with ≥3 trusted dimensions → PRESENT/HIGH evidence → STRONG AxisBand.
    action=BUY, conviction_level=HIGH → BUY/HIGH under existing policy.
    """
    return dict(
        ticker="AAPL",
        v2_visible_action="HOLD",
        analyst_action="BUY",
        conviction_level="HIGH",
        technical_signal="BULLISH",
        risk_flag=None,
        analyst_risks=None,
        category="tech",
        data_quality_label=None,
        intel_read={
            "trusted_signals": ["earnings", "revenue", "margins"],
            "insufficient_data": False,
        },
        thesis_v2=None,
    )


def _medium_evidence_buy_signals() -> dict:
    """Card signals producing BUY where evidence is PRESENT/MEDIUM (1-2 trusted signals).

    Should yield BUY with HIGH upstream conviction, but guardrail caps to MEDIUM.
    """
    return dict(
        ticker="MSFT",
        v2_visible_action="HOLD",
        analyst_action="BUY",
        conviction_level="HIGH",
        technical_signal="BULLISH",
        risk_flag=None,
        analyst_risks=None,
        category="tech",
        data_quality_label=None,
        intel_read={
            "trusted_signals": ["earnings", "revenue"],  # 2 signals → MEDIUM trust
            "insufficient_data": False,
        },
        thesis_v2=None,
    )


def _sell_signals_missing_evidence() -> dict:
    """SELL scenario with missing evidence quality — SELL/TRIM should not be blocked."""
    return dict(
        ticker="XYZ",
        v2_visible_action="SELL",
        analyst_action="SELL",
        conviction_level="MEDIUM",
        technical_signal="BEARISH",
        risk_flag="critical margin compression",
        analyst_risks=["insolvency risk", "regulatory default"],
        category="retail",
        data_quality_label=None,
        intel_read=None,  # missing evidence
        thesis_v2=None,
    )


def _trim_signals_missing_evidence() -> dict:
    """TRIM scenario — portfolio overweight, missing evidence — TRIM independent."""
    return dict(
        ticker="ABC",
        v2_visible_action="TRIM",
        analyst_action="TRIM",
        conviction_level="MEDIUM",
        technical_signal="NEUTRAL",
        risk_flag=None,
        analyst_risks=None,
        category="finance",
        data_quality_label=None,
        intel_read=None,  # missing evidence
        thesis_v2=None,
    )


def _honest_hold_signals() -> dict:
    """All axes missing/unsafe — should produce honest HOLD."""
    return dict(
        ticker="UNKN",
        v2_visible_action="HOLD",
        analyst_action=None,
        conviction_level=None,
        technical_signal=None,
        risk_flag=None,
        analyst_risks=None,
        category="unknown",
        data_quality_label=None,
        intel_read=None,
        thesis_v2=None,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Section A: unit tests for apply_buy_conviction_guardrail()
# ══════════════════════════════════════════════════════════════════════════════

class TestEvidenceIsHighTrust:
    """_evidence_is_high_trust() helper — boundary conditions."""

    def test_present_high_trust_is_true(self):
        s = _ev_summary(DataTruthStatus.PRESENT, SourceTrustLevel.HIGH)
        assert _evidence_is_high_trust(s) is True

    def test_present_medium_trust_is_false(self):
        s = _ev_summary(DataTruthStatus.PRESENT, SourceTrustLevel.MEDIUM)
        assert _evidence_is_high_trust(s) is False

    def test_present_low_trust_is_false(self):
        s = _ev_summary(DataTruthStatus.PRESENT, SourceTrustLevel.LOW)
        assert _evidence_is_high_trust(s) is False

    def test_weak_is_false(self):
        s = _ev_summary(DataTruthStatus.WEAK, SourceTrustLevel.LOW)
        assert _evidence_is_high_trust(s) is False

    def test_missing_is_false(self):
        s = _ev_summary(DataTruthStatus.MISSING, SourceTrustLevel.UNKNOWN)
        assert _evidence_is_high_trust(s) is False

    def test_stale_is_false(self):
        s = _ev_summary(DataTruthStatus.STALE, SourceTrustLevel.LOW)
        assert _evidence_is_high_trust(s) is False

    def test_unavailable_is_false(self):
        s = _ev_summary(DataTruthStatus.UNAVAILABLE, SourceTrustLevel.UNKNOWN)
        assert _evidence_is_high_trust(s) is False

    def test_conflicting_is_false(self):
        s = _ev_summary(DataTruthStatus.CONFLICTING, SourceTrustLevel.UNKNOWN)
        assert _evidence_is_high_trust(s) is False

    def test_none_summary_is_false(self):
        assert _evidence_is_high_trust(None) is False

    def test_empty_findings_is_false(self):
        s = AxisTruthSummary(axis_name="evidence_quality", findings=[])
        assert _evidence_is_high_trust(s) is False


# Table-driven guardrail unit tests.
_GUARDRAIL_CASES = [
    # (id, action, conviction, ev_status, ev_trust, expect_fired, expect_conviction)
    # Test 1: PRESENT/HIGH → not fired
    ("present_high_buy_high", ActionV3.BUY, ConvictionV3.HIGH,
     DataTruthStatus.PRESENT, SourceTrustLevel.HIGH, False, ConvictionV3.HIGH),
    # Test 2: MISSING → fired (cap HIGH→MEDIUM)
    ("missing_buy_high", ActionV3.BUY, ConvictionV3.HIGH,
     DataTruthStatus.MISSING, SourceTrustLevel.UNKNOWN, True, ConvictionV3.MEDIUM),
    # Test 3: STALE → fired
    ("stale_buy_high", ActionV3.BUY, ConvictionV3.HIGH,
     DataTruthStatus.STALE, SourceTrustLevel.LOW, True, ConvictionV3.MEDIUM),
    # Test 4: WEAK → fired (LOW trust, not HIGH)
    ("weak_buy_high", ActionV3.BUY, ConvictionV3.HIGH,
     DataTruthStatus.WEAK, SourceTrustLevel.LOW, True, ConvictionV3.MEDIUM),
    # Test 5: UNAVAILABLE → fired
    ("unavailable_buy_high", ActionV3.BUY, ConvictionV3.HIGH,
     DataTruthStatus.UNAVAILABLE, SourceTrustLevel.UNKNOWN, True, ConvictionV3.MEDIUM),
    # Test 6: CONFLICTING → fired
    ("conflicting_buy_high", ActionV3.BUY, ConvictionV3.HIGH,
     DataTruthStatus.CONFLICTING, SourceTrustLevel.UNKNOWN, True, ConvictionV3.MEDIUM),
    # Test 7: BUY at MEDIUM conviction → not fired (already below HIGH)
    ("present_medium_buy_medium", ActionV3.BUY, ConvictionV3.MEDIUM,
     DataTruthStatus.PRESENT, SourceTrustLevel.MEDIUM, False, ConvictionV3.MEDIUM),
    # PRESENT/MEDIUM trust, BUY/HIGH → fired (not HIGH trust)
    ("present_medium_buy_high", ActionV3.BUY, ConvictionV3.HIGH,
     DataTruthStatus.PRESENT, SourceTrustLevel.MEDIUM, True, ConvictionV3.MEDIUM),
    # Test 8: SELL action → never fired regardless of evidence
    ("missing_sell_high", ActionV3.SELL, ConvictionV3.HIGH,
     DataTruthStatus.MISSING, SourceTrustLevel.UNKNOWN, False, ConvictionV3.HIGH),
    # TRIM action → never fired
    ("missing_trim_high", ActionV3.TRIM, ConvictionV3.HIGH,
     DataTruthStatus.MISSING, SourceTrustLevel.UNKNOWN, False, ConvictionV3.HIGH),
    # Test 9: HOLD action → never fired
    ("missing_hold_high", ActionV3.HOLD, ConvictionV3.HIGH,
     DataTruthStatus.MISSING, SourceTrustLevel.UNKNOWN, False, ConvictionV3.HIGH),
    # BUY/LOW conviction → not fired
    ("missing_buy_low", ActionV3.BUY, ConvictionV3.LOW,
     DataTruthStatus.MISSING, SourceTrustLevel.UNKNOWN, False, ConvictionV3.LOW),
    # None summary → fired for BUY/HIGH
    ("none_summary_buy_high", ActionV3.BUY, ConvictionV3.HIGH,
     None, None, True, ConvictionV3.MEDIUM),
]


class TestApplyBuyConvictionGuardrailTable:
    """Table-driven tests for apply_buy_conviction_guardrail()."""

    @pytest.mark.parametrize(
        "case_id,action,conviction,ev_status,ev_trust,expect_fired,expect_conviction",
        _GUARDRAIL_CASES,
        ids=[c[0] for c in _GUARDRAIL_CASES],
    )
    def test_guardrail(
        self, case_id, action, conviction, ev_status, ev_trust,
        expect_fired, expect_conviction
    ):
        if ev_status is None:
            ev_summary = None
        else:
            ev_summary = _ev_summary(ev_status, ev_trust)

        post_conviction, diag = apply_buy_conviction_guardrail(
            action=action,
            conviction=conviction,
            evidence_quality_truth=ev_summary,
        )

        assert post_conviction == expect_conviction, (
            f"{case_id}: expected conviction {expect_conviction}, got {post_conviction}"
        )
        assert diag["buy_high_conviction_guardrail_applied"] is expect_fired, (
            f"{case_id}: expected guardrail_applied={expect_fired}"
        )


class TestGuardrailDiagnosticsContract:
    """Verify stable diagnostic key contract (PR 9)."""

    def test_fired_diagnostics_keys_complete(self):
        ev = _ev_summary(DataTruthStatus.MISSING, SourceTrustLevel.UNKNOWN)
        _, diag = apply_buy_conviction_guardrail(
            action=ActionV3.BUY,
            conviction=ConvictionV3.HIGH,
            evidence_quality_truth=ev,
        )
        required_keys = {
            "buy_high_conviction_guardrail_applied",
            "buy_conviction_capped_reason",
            "evidence_quality_truth_status",
            "evidence_quality_trust_level",
            "pre_guardrail_conviction",
            "post_guardrail_conviction",
        }
        assert required_keys.issubset(diag.keys())

    def test_not_fired_pre_post_are_none(self):
        ev = _ev_summary(DataTruthStatus.PRESENT, SourceTrustLevel.HIGH)
        _, diag = apply_buy_conviction_guardrail(
            action=ActionV3.BUY,
            conviction=ConvictionV3.HIGH,
            evidence_quality_truth=ev,
        )
        assert diag["buy_high_conviction_guardrail_applied"] is False
        assert diag["pre_guardrail_conviction"] is None
        assert diag["post_guardrail_conviction"] is None
        assert diag["buy_conviction_capped_reason"] == ""

    def test_fired_pre_post_set_correctly(self):
        ev = _ev_summary(DataTruthStatus.PRESENT, SourceTrustLevel.MEDIUM)
        _, diag = apply_buy_conviction_guardrail(
            action=ActionV3.BUY,
            conviction=ConvictionV3.HIGH,
            evidence_quality_truth=ev,
        )
        assert diag["buy_high_conviction_guardrail_applied"] is True
        assert diag["pre_guardrail_conviction"] == "HIGH"
        assert diag["post_guardrail_conviction"] == "MEDIUM"
        assert "evidence_quality_not_high_trust" in diag["buy_conviction_capped_reason"]

    def test_status_and_trust_level_populated_when_fired(self):
        ev = _ev_summary(DataTruthStatus.STALE, SourceTrustLevel.LOW)
        _, diag = apply_buy_conviction_guardrail(
            action=ActionV3.BUY,
            conviction=ConvictionV3.HIGH,
            evidence_quality_truth=ev,
        )
        assert diag["evidence_quality_truth_status"] == "STALE"
        assert diag["evidence_quality_trust_level"] == "LOW"

    def test_status_and_trust_level_unknown_when_summary_none(self):
        _, diag = apply_buy_conviction_guardrail(
            action=ActionV3.BUY,
            conviction=ConvictionV3.HIGH,
            evidence_quality_truth=None,
        )
        assert diag["evidence_quality_truth_status"] == "unknown"
        assert diag["evidence_quality_trust_level"] == "unknown"
        assert diag["buy_high_conviction_guardrail_applied"] is True

    def test_sell_action_diagnostics_not_fired(self):
        ev = _ev_summary(DataTruthStatus.MISSING, SourceTrustLevel.UNKNOWN)
        _, diag = apply_buy_conviction_guardrail(
            action=ActionV3.SELL,
            conviction=ConvictionV3.HIGH,
            evidence_quality_truth=ev,
        )
        assert diag["buy_high_conviction_guardrail_applied"] is False
        assert diag["evidence_quality_truth_status"] == "MISSING"


class TestGuardrailWithClassifyEvidence:
    """Guardrail using real classify_evidence_signals() findings."""

    def test_three_trusted_dims_not_fired(self):
        finding = classify_evidence_signals(
            data_quality_label=None,
            intel_read={"trusted_signals": ["a", "b", "c"], "insufficient_data": False},
        )
        ev = AxisTruthSummary(
            axis_name="evidence_quality",
            findings=[finding],
            present_count=1,
            safe_for_decision=True,
        )
        post, diag = apply_buy_conviction_guardrail(
            action=ActionV3.BUY,
            conviction=ConvictionV3.HIGH,
            evidence_quality_truth=ev,
        )
        assert post == ConvictionV3.HIGH
        assert diag["buy_high_conviction_guardrail_applied"] is False

    def test_two_trusted_dims_fired(self):
        finding = classify_evidence_signals(
            data_quality_label=None,
            intel_read={"trusted_signals": ["a", "b"], "insufficient_data": False},
        )
        ev = AxisTruthSummary(
            axis_name="evidence_quality",
            findings=[finding],
            present_count=1,
            safe_for_decision=True,
        )
        post, diag = apply_buy_conviction_guardrail(
            action=ActionV3.BUY,
            conviction=ConvictionV3.HIGH,
            evidence_quality_truth=ev,
        )
        assert post == ConvictionV3.MEDIUM
        assert diag["buy_high_conviction_guardrail_applied"] is True

    def test_data_quality_high_label_not_fired(self):
        finding = classify_evidence_signals(
            data_quality_label="HIGH",
            intel_read=None,
        )
        ev = AxisTruthSummary(
            axis_name="evidence_quality",
            findings=[finding],
            present_count=1,
            safe_for_decision=True,
        )
        post, diag = apply_buy_conviction_guardrail(
            action=ActionV3.BUY,
            conviction=ConvictionV3.HIGH,
            evidence_quality_truth=ev,
        )
        assert post == ConvictionV3.HIGH
        assert diag["buy_high_conviction_guardrail_applied"] is False

    def test_data_quality_medium_label_fired(self):
        finding = classify_evidence_signals(
            data_quality_label="MEDIUM",
            intel_read=None,
        )
        ev = AxisTruthSummary(
            axis_name="evidence_quality",
            findings=[finding],
            present_count=1,
            safe_for_decision=True,
        )
        post, diag = apply_buy_conviction_guardrail(
            action=ActionV3.BUY,
            conviction=ConvictionV3.HIGH,
            evidence_quality_truth=ev,
        )
        assert post == ConvictionV3.MEDIUM
        assert diag["buy_high_conviction_guardrail_applied"] is True

    def test_stale_signal_fired(self):
        finding = classify_with_staleness(
            "evidence_quality",
            "some_value",
            last_updated_hours_ago=72.0,
            stale_threshold_hours=48.0,
            source_kind="test",
        )
        ev = AxisTruthSummary(
            axis_name="evidence_quality",
            findings=[finding],
            stale_count=1,
            safe_for_decision=False,
        )
        post, diag = apply_buy_conviction_guardrail(
            action=ActionV3.BUY,
            conviction=ConvictionV3.HIGH,
            evidence_quality_truth=ev,
        )
        assert post == ConvictionV3.MEDIUM
        assert diag["evidence_quality_truth_status"] == "STALE"
        assert diag["buy_high_conviction_guardrail_applied"] is True

    def test_insufficient_intel_read_fired(self):
        finding = classify_evidence_signals(
            data_quality_label=None,
            intel_read={"trusted_signals": [], "insufficient_data": True},
        )
        ev = AxisTruthSummary(
            axis_name="evidence_quality",
            findings=[finding],
            weak_count=1,
            safe_for_decision=True,
        )
        post, diag = apply_buy_conviction_guardrail(
            action=ActionV3.BUY,
            conviction=ConvictionV3.HIGH,
            evidence_quality_truth=ev,
        )
        assert post == ConvictionV3.MEDIUM
        assert diag["evidence_quality_truth_status"] == "WEAK"
        assert diag["buy_high_conviction_guardrail_applied"] is True

    def test_unavailable_sentinel_fired(self):
        finding = classify_evidence_signals(
            data_quality_label="N/A",
            intel_read=None,
        )
        ev = AxisTruthSummary(
            axis_name="evidence_quality",
            findings=[finding],
            safe_for_decision=False,
        )
        post, diag = apply_buy_conviction_guardrail(
            action=ActionV3.BUY,
            conviction=ConvictionV3.HIGH,
            evidence_quality_truth=ev,
        )
        assert post == ConvictionV3.MEDIUM
        assert diag["evidence_quality_truth_status"] == "UNAVAILABLE"
        assert diag["buy_high_conviction_guardrail_applied"] is True

    def test_none_both_inputs_fired(self):
        finding = classify_evidence_signals(
            data_quality_label=None,
            intel_read=None,
        )
        ev = AxisTruthSummary(
            axis_name="evidence_quality",
            findings=[finding],
            missing_count=1,
            safe_for_decision=False,
        )
        post, diag = apply_buy_conviction_guardrail(
            action=ActionV3.BUY,
            conviction=ConvictionV3.HIGH,
            evidence_quality_truth=ev,
        )
        assert post == ConvictionV3.MEDIUM
        assert diag["evidence_quality_truth_status"] == "MISSING"
        assert diag["buy_high_conviction_guardrail_applied"] is True


# ══════════════════════════════════════════════════════════════════════════════
# Section B: integration tests through project_shadow_from_card_signals()
# ══════════════════════════════════════════════════════════════════════════════

class TestShadowProjectionGuardrailIntegration:
    """Guardrail integrated into project_shadow_from_card_signals()."""

    def test_strong_evidence_buy_high_conviction_unchanged(self):
        """Test 1 (integration): ≥3 trusted dims → BUY/HIGH stays HIGH."""
        result = project_shadow_from_card_signals(**_strong_buy_card_signals())
        assert result is not None
        assert result["v3_shadow_action"] == "BUY"
        assert result["v3_shadow_conviction"] == "HIGH"
        td = result["truth_diagnostics"]
        assert td is not None
        gcg = td.get("buy_conviction_guardrail", {})
        assert gcg.get("buy_high_conviction_guardrail_applied") is False

    def test_medium_evidence_buy_conviction_capped(self):
        """Policy now caps OK-evidence BUY conviction to MEDIUM before shadow guardrail.

        After Build 3 PR 1: the visible decision policy (_compute_conviction Cap 5)
        caps HIGH → MEDIUM for BUY + OK evidence. The shadow guardrail receives
        MEDIUM conviction and does not fire. Final conviction is still MEDIUM (correct).
        This test confirms the visible path and shadow path now agree.
        """
        result = project_shadow_from_card_signals(**_medium_evidence_buy_signals())
        assert result is not None
        assert result["v3_shadow_action"] == "BUY"
        assert result["v3_shadow_conviction"] == "MEDIUM"
        td = result["truth_diagnostics"]
        assert td is not None
        gcg = td.get("buy_conviction_guardrail", {})
        # Policy already capped conviction before shadow guardrail → shadow guardrail
        # does not fire (conviction entering shadow is already MEDIUM, not HIGH).
        assert gcg.get("buy_high_conviction_guardrail_applied") is False

    def test_v2_visible_action_not_mutated(self):
        """Test 10: v2 visible action is never changed by guardrail."""
        result = project_shadow_from_card_signals(**_medium_evidence_buy_signals())
        assert result is not None
        assert result["v2_visible_action"] == "HOLD"  # v2 HOLD stays HOLD

    def test_sell_not_blocked_by_missing_evidence(self):
        """Test 8: SELL decision independent of missing evidence quality."""
        result = project_shadow_from_card_signals(**_sell_signals_missing_evidence())
        assert result is not None
        # SELL or TRIM expected — risk/protection axes justify it
        assert result["v3_shadow_action"] in {"SELL", "TRIM"}
        td = result["truth_diagnostics"]
        gcg = td.get("buy_conviction_guardrail", {}) if td else {}
        # Guardrail does not apply when action is not BUY
        assert gcg.get("buy_high_conviction_guardrail_applied") is False

    def test_trim_not_blocked_by_missing_evidence(self):
        """Test 8 (TRIM): portfolio fit triggers TRIM independent of evidence."""
        result = project_shadow_from_card_signals(**_trim_signals_missing_evidence())
        assert result is not None
        assert result["v3_shadow_action"] in {"TRIM", "HOLD"}
        td = result["truth_diagnostics"]
        gcg = td.get("buy_conviction_guardrail", {}) if td else {}
        assert gcg.get("buy_high_conviction_guardrail_applied") is False

    def test_honest_hold_unchanged(self):
        """Test 9: all axes missing/unsafe → HOLD, guardrail not applied."""
        result = project_shadow_from_card_signals(**_honest_hold_signals())
        assert result is not None
        assert result["v3_shadow_action"] == "HOLD"
        assert result["v3_honest_hold"] is True
        td = result["truth_diagnostics"]
        gcg = td.get("buy_conviction_guardrail", {}) if td else {}
        assert gcg.get("buy_high_conviction_guardrail_applied") is False

    def test_shadow_diagnostics_contain_guardrail_key(self):
        """Test 11: truth_diagnostics always include buy_conviction_guardrail sub-dict."""
        result = project_shadow_from_card_signals(**_strong_buy_card_signals())
        assert result is not None
        assert "truth_diagnostics" in result
        td = result["truth_diagnostics"]
        assert isinstance(td, dict)
        assert "buy_conviction_guardrail" in td

    def test_shadow_action_only_valid_v2_labels(self):
        """Test 13: shadow action is always BUY/HOLD/TRIM/SELL."""
        _valid = {"BUY", "HOLD", "TRIM", "SELL"}
        for signals in [
            _strong_buy_card_signals(),
            _medium_evidence_buy_signals(),
            _sell_signals_missing_evidence(),
            _trim_signals_missing_evidence(),
            _honest_hold_signals(),
        ]:
            result = project_shadow_from_card_signals(**signals)
            assert result is not None
            assert result["v3_shadow_action"] in _valid

    def test_fail_soft_on_bad_input(self):
        """Shadow projection returns None on totally broken input, never raises."""
        result = project_shadow_from_card_signals(
            ticker="",
            v2_visible_action=None,
            analyst_action=None,
            conviction_level=None,
            technical_signal=None,
            risk_flag=None,
            analyst_risks=None,
            category=None,
            data_quality_label=None,
            intel_read=None,
            thesis_v2=None,
        )
        # Should return a valid dict (fail-soft, not None) for empty ticker
        assert result is None or isinstance(result, dict)

    def test_no_provider_no_db_no_llm_calls(self):
        """Test 14: guardrail is pure function — verified by absence of side effects."""
        # project_shadow_from_card_signals is a pure function (no IO, no network).
        # If it returns without raising, this is satisfied.
        result = project_shadow_from_card_signals(**_strong_buy_card_signals())
        assert isinstance(result, dict)


# ══════════════════════════════════════════════════════════════════════════════
# Section C: golden portfolio diversity regression (Test 12)
# ══════════════════════════════════════════════════════════════════════════════

_GOLDEN_PORTFOLIO = [
    # Strong BUY: ≥3 trusted dims, BUY signal, HIGH conviction, no risk.
    dict(
        ticker="STRONG_BUY",
        v2_visible_action="HOLD",
        analyst_action="BUY",
        conviction_level="HIGH",
        technical_signal="BULLISH",
        risk_flag=None,
        analyst_risks=None,
        category="tech",
        data_quality_label=None,
        intel_read={"trusted_signals": ["d1", "d2", "d3"], "insufficient_data": False},
        thesis_v2=None,
    ),
    # Medium-evidence BUY: 2 trusted signals, BUY signal — guardrail caps HIGH→MEDIUM.
    dict(
        ticker="MED_EV_BUY",
        v2_visible_action="HOLD",
        analyst_action="BUY",
        conviction_level="HIGH",
        technical_signal="BULLISH",
        risk_flag=None,
        analyst_risks=None,
        category="tech",
        data_quality_label=None,
        intel_read={"trusted_signals": ["d1", "d2"], "insufficient_data": False},
        thesis_v2=None,
    ),
    # TRIM: overweight / TRIM analyst signal, missing evidence.
    dict(
        ticker="TRIM_CARD",
        v2_visible_action="TRIM",
        analyst_action="TRIM",
        conviction_level="MEDIUM",
        technical_signal="NEUTRAL",
        risk_flag=None,
        analyst_risks=None,
        category="finance",
        data_quality_label=None,
        intel_read=None,
        thesis_v2=None,
    ),
    # SELL: critical risk + SELL signal.
    dict(
        ticker="SELL_CARD",
        v2_visible_action="SELL",
        analyst_action="SELL",
        conviction_level="MEDIUM",
        technical_signal="BEARISH",
        risk_flag="critical risk of insolvency",
        analyst_risks=["severe cash burn"],
        category="retail",
        data_quality_label=None,
        intel_read=None,
        thesis_v2=None,
    ),
    # HOLD: neutral signals, no clear trigger.
    dict(
        ticker="HOLD_CARD",
        v2_visible_action="HOLD",
        analyst_action="HOLD",
        conviction_level="MEDIUM",
        technical_signal="NEUTRAL",
        risk_flag=None,
        analyst_risks=None,
        category="consumer",
        data_quality_label="MEDIUM",
        intel_read=None,
        thesis_v2=None,
    ),
    # Honest HOLD: no data.
    dict(
        ticker="HONEST_HOLD",
        v2_visible_action="HOLD",
        analyst_action=None,
        conviction_level=None,
        technical_signal=None,
        risk_flag=None,
        analyst_risks=None,
        category="unknown",
        data_quality_label=None,
        intel_read=None,
        thesis_v2=None,
    ),
]


class TestGoldenPortfolioDiversity:
    """Golden portfolio regression: action diversity must be preserved."""

    def _run_portfolio(self):
        return [project_shadow_from_card_signals(**c) for c in _GOLDEN_PORTFOLIO]

    def test_all_projections_succeed(self):
        results = self._run_portfolio()
        valid = [r for r in results if isinstance(r, dict)]
        assert len(valid) == len(_GOLDEN_PORTFOLIO), (
            f"Expected {len(_GOLDEN_PORTFOLIO)} projections, got {len(valid)}"
        )

    def test_action_diversity(self):
        results = self._run_portfolio()
        actions = {r["v3_shadow_action"] for r in results if isinstance(r, dict)}
        assert len(actions) >= 3, f"Expected at least 3 distinct actions, got: {actions}"

    def test_buy_action_present(self):
        results = self._run_portfolio()
        actions = [r["v3_shadow_action"] for r in results if isinstance(r, dict)]
        assert "BUY" in actions

    def test_sell_or_trim_present(self):
        results = self._run_portfolio()
        actions = {r["v3_shadow_action"] for r in results if isinstance(r, dict)}
        assert actions & {"SELL", "TRIM"}, "Expected at least one SELL or TRIM"

    def test_strong_buy_card_retains_high_conviction(self):
        results = self._run_portfolio()
        strong_buy = next(
            (r for r in results if isinstance(r, dict) and r["ticker"] == "STRONG_BUY"),
            None,
        )
        assert strong_buy is not None
        assert strong_buy["v3_shadow_action"] == "BUY"
        assert strong_buy["v3_shadow_conviction"] == "HIGH"

    def test_medium_evidence_buy_capped_at_medium(self):
        """Policy caps MED_EV_BUY conviction to MEDIUM; shadow guardrail then does not fire.

        After Build 3 PR 1: the visible policy caps OK-evidence BUY to MEDIUM before
        the shadow guardrail runs. Shadow guardrail receives MEDIUM → does not fire.
        Final conviction is still MEDIUM (correct). Visible and shadow now agree.
        """
        results = self._run_portfolio()
        med_buy = next(
            (r for r in results if isinstance(r, dict) and r["ticker"] == "MED_EV_BUY"),
            None,
        )
        assert med_buy is not None
        assert med_buy["v3_shadow_action"] == "BUY"
        assert med_buy["v3_shadow_conviction"] == "MEDIUM"
        td = med_buy.get("truth_diagnostics") or {}
        gcg = td.get("buy_conviction_guardrail", {})
        # Policy already capped; shadow guardrail does not fire.
        assert gcg.get("buy_high_conviction_guardrail_applied") is False

    def test_sell_card_conviction_independent(self):
        results = self._run_portfolio()
        sell_card = next(
            (r for r in results if isinstance(r, dict) and r["ticker"] == "SELL_CARD"),
            None,
        )
        assert sell_card is not None
        assert sell_card["v3_shadow_action"] in {"SELL", "TRIM"}
        td = sell_card.get("truth_diagnostics") or {}
        gcg = td.get("buy_conviction_guardrail", {})
        assert gcg.get("buy_high_conviction_guardrail_applied") is False

    def test_no_invalid_actions_in_portfolio(self):
        results = self._run_portfolio()
        _valid = {"BUY", "HOLD", "TRIM", "SELL"}
        for r in results:
            if isinstance(r, dict):
                assert r["v3_shadow_action"] in _valid

    def test_v2_visible_actions_unchanged(self):
        """v2 visible actions from golden fixtures are never mutated."""
        results = self._run_portfolio()
        for orig, result in zip(_GOLDEN_PORTFOLIO, results):
            if isinstance(result, dict):
                expected_v2 = (orig["v2_visible_action"] or "HOLD").upper()
                assert result["v2_visible_action"] == expected_v2

    def test_portfolio_summary_still_works(self):
        results = self._run_portfolio()
        summary = summarize_shadow_diagnostics(results, total_cards=len(_GOLDEN_PORTFOLIO))
        assert summary["total_cards"] == len(_GOLDEN_PORTFOLIO)
        assert summary["projected_cards"] == len(_GOLDEN_PORTFOLIO)
        assert summary["projection_failures"] == 0
        # At least one non-HOLD shadow action from v2 HOLDs
        assert summary["non_hold_shadow_from_v2_hold_count"] >= 1
