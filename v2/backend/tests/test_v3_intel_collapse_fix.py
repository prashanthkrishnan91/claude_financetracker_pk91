"""PR 13: all-HOLD Intel collapse fix tests.

Root cause: intel_read.insufficient_data=True was used as a binary HOLD gate
in three places, collapsing ALL cards to HOLD even when 2-3 evidence dimensions
(quality, valuation, momentum) were published — only because growth/risk axes
were missing in the thesis scorecard.

Fix: Use n_trusted == 0 (zero trusted signals) as the HOLD trigger, not the
binary insufficient_data flag. Missing one axis suppresses ONLY that axis, not
the entire card.

Three changes:
1. recommendation_engine.py visible gate: n_trusted == 0 → collapse; n_trusted
   >= 1 → preserve action, downgrade conviction only.
2. existing_signal_adapter._derive_evidence_quality: n_trusted == 0 → THIN;
   n_trusted >= 1 → OK/STRONG (previously insufficient or n_trusted==0 → THIN).
3. data_truth_v1.classify_evidence_signals: n_trusted == 0 → WEAK; n_trusted
   >= 1 → PRESENT/MEDIUM or HIGH (previously insufficient or n_trusted==0 → WEAK).

All tests use synthetic production-shaped fixtures — no real user/account data.
"""
from __future__ import annotations

import pytest

from app.services.intelligence.v3.data_truth_contracts import (
    DataTruthStatus,
    SourceTrustLevel,
)
from app.services.intelligence.v3.data_truth_v1 import classify_evidence_signals
from app.services.intelligence.v3.existing_signal_adapter import (
    _derive_evidence_quality,
)
from app.services.intelligence.v3.decision_contracts import AxisBand
from app.services.intelligence.v3.shadow_projection import (
    project_shadow_from_card_signals,
    summarize_shadow_diagnostics,
    summarize_guardrail_impact_observability,
)


# ── Fixture helpers ──────────────────────────────────────────────────────────

def _intel_read(
    n_trusted: int,
    *,
    insufficient: bool = False,
    incomplete: list[str] | None = None,
) -> dict:
    """Production-shaped intel_read using the correct 'trusted_signals' key."""
    signals = ["business quality", "valuation", "recent market behavior", "growth", "risk"]
    return {
        "insufficient_data": insufficient,
        "trusted_signals": signals[:n_trusted],
        "incomplete_signals": incomplete or [],
    }


def _shadow(
    *,
    ticker: str = "AAPL",
    v2_action: str = "HOLD",
    analyst_action: str | None = "BUY",
    conviction_level: str | None = "HIGH",
    technical_signal: str | None = "BULLISH",
    risk_flag: str | None = None,
    analyst_risks: list | None = None,
    category: str = "Core",
    data_quality_label: str | None = None,
    intel_read: dict | None = None,
    thesis_v2: dict | None = None,
    analyst_used_fallback: bool | None = None,
) -> dict | None:
    return project_shadow_from_card_signals(
        ticker=ticker,
        v2_visible_action=v2_action,
        analyst_action=analyst_action,
        conviction_level=conviction_level,
        technical_signal=technical_signal,
        risk_flag=risk_flag,
        analyst_risks=analyst_risks,
        category=category,
        data_quality_label=data_quality_label,
        intel_read=intel_read,
        thesis_v2=thesis_v2,
        analyst_used_fallback=analyst_used_fallback,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Section 1: classify_evidence_signals — partial coverage is PRESENT not WEAK
# ══════════════════════════════════════════════════════════════════════════════

class TestClassifyEvidenceSignalsPartialCoverage:
    """Verifies that insufficient_data=True with trusted signals → PRESENT not WEAK.

    Production root cause: scorecard.status=INSUFFICIENT_DATA (growth/risk missing)
    sets intel_read.insufficient_data=True. Before this fix, that alone caused
    classify_evidence_signals to return WEAK regardless of trusted_signals count,
    making evidence_quality_status_counts: {'WEAK': 34} even after PR 12.
    """

    def test_insufficient_true_three_trusted_yields_present_high(self):
        """3 trusted signals + insufficient_data=True → PRESENT/HIGH (not WEAK)."""
        ir = _intel_read(3, insufficient=True)
        f = classify_evidence_signals(None, ir)
        assert f.status == DataTruthStatus.PRESENT
        assert f.trust_level == SourceTrustLevel.HIGH

    def test_insufficient_true_two_trusted_yields_present_medium(self):
        """2 trusted signals + insufficient_data=True → PRESENT/MEDIUM (not WEAK)."""
        ir = _intel_read(2, insufficient=True)
        f = classify_evidence_signals(None, ir)
        assert f.status == DataTruthStatus.PRESENT
        assert f.trust_level == SourceTrustLevel.MEDIUM

    def test_insufficient_true_one_trusted_yields_present_medium(self):
        """1 trusted signal + insufficient_data=True → PRESENT/MEDIUM (not WEAK)."""
        ir = _intel_read(1, insufficient=True)
        f = classify_evidence_signals(None, ir)
        assert f.status == DataTruthStatus.PRESENT
        assert f.trust_level == SourceTrustLevel.MEDIUM

    def test_insufficient_true_zero_trusted_still_weak(self):
        """0 trusted signals + insufficient_data=True → still WEAK (truly no evidence)."""
        ir = _intel_read(0, insufficient=True)
        f = classify_evidence_signals(None, ir)
        assert f.status == DataTruthStatus.WEAK
        assert f.trust_level == SourceTrustLevel.LOW

    def test_insufficient_false_zero_trusted_still_weak(self):
        """0 trusted signals + insufficient_data=False → WEAK (no signal coverage)."""
        ir = _intel_read(0, insufficient=False)
        f = classify_evidence_signals(None, ir)
        assert f.status == DataTruthStatus.WEAK

    def test_insufficient_false_three_trusted_still_present_high(self):
        """Pre-existing case unchanged: sufficient=False, 3 trusted → PRESENT/HIGH."""
        ir = _intel_read(3, insufficient=False)
        f = classify_evidence_signals(None, ir)
        assert f.status == DataTruthStatus.PRESENT
        assert f.trust_level == SourceTrustLevel.HIGH

    def test_production_shaped_quality_valuation_momentum_incomplete_growth_risk(self):
        """Mirrors production: quality/valuation/momentum published, growth/risk missing.

        Before PR 13: insufficient_data=True → WEAK (all 34 cards collapsed).
        After  PR 13: 3 trusted signals → PRESENT/HIGH.
        """
        ir = {
            "insufficient_data": True,
            "trusted_signals": ["business quality", "valuation", "recent market behavior"],
            "incomplete_signals": ["growth", "risk"],
        }
        f = classify_evidence_signals(None, ir)
        assert f.status == DataTruthStatus.PRESENT
        assert f.trust_level == SourceTrustLevel.HIGH

    def test_production_shaped_two_published_two_missing(self):
        """quality + valuation published, growth/risk/momentum missing."""
        ir = {
            "insufficient_data": True,
            "trusted_signals": ["business quality", "valuation"],
            "incomplete_signals": ["growth", "risk", "recent market behavior"],
        }
        f = classify_evidence_signals(None, ir)
        assert f.status == DataTruthStatus.PRESENT
        assert f.trust_level == SourceTrustLevel.MEDIUM


# ══════════════════════════════════════════════════════════════════════════════
# Section 2: _derive_evidence_quality — partial coverage is OK/STRONG not THIN
# ══════════════════════════════════════════════════════════════════════════════

class TestDeriveEvidenceQualityPartialCoverage:
    """Verifies that insufficient_data=True + trusted signals → OK/STRONG not THIN."""

    def test_insufficient_true_three_trusted_yields_strong(self):
        reasons: dict = {}
        result = _derive_evidence_quality(
            data_quality_label=None,
            intel_read=_intel_read(3, insufficient=True),
            suppression_reasons=reasons,
        )
        assert result == AxisBand.STRONG
        assert "evidence_quality" not in reasons

    def test_insufficient_true_two_trusted_yields_ok(self):
        reasons: dict = {}
        result = _derive_evidence_quality(
            data_quality_label=None,
            intel_read=_intel_read(2, insufficient=True),
            suppression_reasons=reasons,
        )
        assert result == AxisBand.OK
        assert "evidence_quality" not in reasons

    def test_insufficient_true_one_trusted_yields_ok(self):
        reasons: dict = {}
        result = _derive_evidence_quality(
            data_quality_label=None,
            intel_read=_intel_read(1, insufficient=True),
            suppression_reasons=reasons,
        )
        assert result == AxisBand.OK

    def test_insufficient_true_zero_trusted_still_thin(self):
        reasons: dict = {}
        result = _derive_evidence_quality(
            data_quality_label=None,
            intel_read=_intel_read(0, insufficient=True),
            suppression_reasons=reasons,
        )
        assert result == AxisBand.THIN
        assert "evidence_quality" in reasons

    def test_insufficient_false_zero_trusted_still_thin(self):
        reasons: dict = {}
        result = _derive_evidence_quality(
            data_quality_label=None,
            intel_read=_intel_read(0, insufficient=False),
            suppression_reasons=reasons,
        )
        assert result == AxisBand.THIN

    def test_production_shaped_three_published_axes(self):
        """quality/valuation/momentum published, growth/risk missing → STRONG."""
        reasons: dict = {}
        result = _derive_evidence_quality(
            data_quality_label=None,
            intel_read={
                "insufficient_data": True,
                "trusted_signals": ["business quality", "valuation", "recent market behavior"],
                "incomplete_signals": ["growth", "risk"],
            },
            suppression_reasons=reasons,
        )
        assert result == AxisBand.STRONG


# ══════════════════════════════════════════════════════════════════════════════
# Section 3: v3 shadow — BUY can emerge for partial-evidence cards
# ══════════════════════════════════════════════════════════════════════════════

class TestShadowProjectionPartialEvidence:
    """Verifies v3 shadow produces non-HOLD when trusted signals are present."""

    def test_insufficient_three_trusted_analyst_buy_shadow_buys(self):
        """Strong analyst BUY with 3 published dimensions → v3 shadow = BUY."""
        ir = _intel_read(3, insufficient=True)
        result = _shadow(
            v2_action="BUY",
            analyst_action="BUY",
            conviction_level="HIGH",
            intel_read=ir,
        )
        assert result is not None
        assert result["v3_shadow_action"] == "BUY"

    def test_insufficient_two_trusted_analyst_buy_shadow_buys(self):
        """2 published dimensions + analyst BUY → v3 shadow can produce BUY."""
        ir = _intel_read(2, insufficient=True)
        result = _shadow(
            v2_action="BUY",
            analyst_action="BUY",
            conviction_level="MEDIUM",
            intel_read=ir,
        )
        assert result is not None
        assert result["v3_shadow_action"] == "BUY"

    def test_insufficient_zero_trusted_shadow_holds(self):
        """0 published dimensions → v3 shadow still HOLD (truly no evidence)."""
        ir = _intel_read(0, insufficient=True)
        result = _shadow(
            v2_action="HOLD",
            analyst_action="BUY",
            conviction_level="HIGH",
            intel_read=ir,
        )
        assert result is not None
        assert result["v3_shadow_action"] == "HOLD"

    def test_trim_signal_not_affected(self):
        """TRIM action is unaffected by insufficient_data — risk protection preserved."""
        ir = _intel_read(0, insufficient=True)
        result = _shadow(
            v2_action="TRIM",
            analyst_action="TRIM",
            conviction_level="MEDIUM",
            intel_read=ir,
        )
        assert result is not None
        assert result["v3_shadow_action"] == "TRIM"

    def test_sell_signal_not_affected(self):
        """SELL action is unaffected by insufficient_data — risk protection preserved."""
        ir = _intel_read(0, insufficient=True)
        result = _shadow(
            v2_action="SELL",
            analyst_action="SELL",
            conviction_level="MEDIUM",
            technical_signal="BEARISH",
            risk_flag="Critical risk: elevated default concern",
            intel_read=ir,
        )
        assert result is not None
        # SELL with critical risk text → should not be downgraded to HOLD
        assert result["v3_shadow_action"] in {"SELL", "TRIM"}

    def test_hold_collapse_risk_flag_set_when_v3_disagrees(self):
        """hold_collapse_risk=True when v2=HOLD but v3 would say BUY."""
        ir = _intel_read(3, insufficient=True)
        result = _shadow(
            v2_action="HOLD",  # v2 forced HOLD
            analyst_action="BUY",
            conviction_level="HIGH",
            intel_read=ir,
        )
        assert result is not None
        # v3 produces BUY; v2 is HOLD → divergence visible
        assert result["v3_shadow_action"] == "BUY"
        assert result["hold_collapse_risk"] is True


# ══════════════════════════════════════════════════════════════════════════════
# Section 4: evidence_quality_status_counts no longer all WEAK after fix
# ══════════════════════════════════════════════════════════════════════════════

class TestPortfolioShadowEvidenceCounts:
    """Verifies that a production-shaped 34-card portfolio no longer returns all WEAK.

    Before PR 13: evidence_quality_status_counts: {'WEAK': 34}
    After  PR 13: PRESENT entries appear for cards with published dimensions.
    """

    def _make_prod_card(
        self,
        ticker: str,
        *,
        n_trusted: int,
        insufficient: bool,
        analyst_action: str = "BUY",
        conviction_level: str = "HIGH",
    ) -> dict | None:
        ir = _intel_read(n_trusted, insufficient=insufficient)
        return _shadow(
            ticker=ticker,
            v2_action="HOLD",
            analyst_action=analyst_action,
            conviction_level=conviction_level,
            intel_read=ir,
        )

    def test_34_card_portfolio_no_longer_all_weak(self):
        """Production-shaped 34-card batch: at least some PRESENT evidence after fix."""
        # 28 cards with 3 published dims + growth/risk missing (mirrors production)
        # 6 cards with 0 published dims (truly insufficient)
        diagnostics = []
        for i in range(28):
            d = self._make_prod_card(f"TICK{i:02d}", n_trusted=3, insufficient=True)
            diagnostics.append(d)
        for i in range(6):
            d = self._make_prod_card(f"BARE{i:02d}", n_trusted=0, insufficient=True)
            diagnostics.append(d)

        assert len(diagnostics) == 34
        valid = [d for d in diagnostics if d is not None]
        assert len(valid) == 34

        obs = summarize_guardrail_impact_observability(valid)
        ev_counts = obs.get("evidence_quality_status_counts", {})

        # Before fix: {"WEAK": 34}; After fix: PRESENT should appear for 28 cards
        assert ev_counts.get("PRESENT", 0) == 28, (
            f"Expected 28 PRESENT after fix, got {ev_counts}"
        )
        assert ev_counts.get("WEAK", 0) == 6, (
            f"Expected 6 WEAK (truly empty), got {ev_counts}"
        )

    def test_34_card_all_empty_still_all_weak(self):
        """All 34 cards with 0 trusted signals → WEAK 34 (unchanged behavior)."""
        diagnostics = [
            self._make_prod_card(f"EMPTY{i:02d}", n_trusted=0, insufficient=True)
            for i in range(34)
        ]
        valid = [d for d in diagnostics if d is not None]
        obs = summarize_guardrail_impact_observability(valid)
        ev_counts = obs.get("evidence_quality_status_counts", {})
        assert ev_counts.get("WEAK", 0) == 34
        assert ev_counts.get("PRESENT", 0) == 0


# ══════════════════════════════════════════════════════════════════════════════
# Section 5: mixed synthetic portfolio — action diversity
# ══════════════════════════════════════════════════════════════════════════════

class TestMixedPortfolioActionDiversity:
    """Verifies the v3 shadow produces mixed actions for a realistic portfolio.

    The production failure was BUY 0 / HOLD 34. After the fix, cards with
    published evidence dimensions should yield BUY in shadow.
    """

    def _buy_card(self, ticker: str) -> dict | None:
        """Strong BUY candidate: 3 published dims + analyst BUY + HIGH conviction."""
        return _shadow(
            ticker=ticker,
            v2_action="HOLD",  # v2 still HOLD (current DB state)
            analyst_action="BUY",
            conviction_level="HIGH",
            intel_read=_intel_read(3, insufficient=True),
        )

    def _hold_card(self, ticker: str) -> dict | None:
        """True HOLD: 0 trusted signals, analyst HOLD."""
        return _shadow(
            ticker=ticker,
            v2_action="HOLD",
            analyst_action="HOLD",
            conviction_level="LOW",
            intel_read=_intel_read(0, insufficient=True),
        )

    def _trim_card(self, ticker: str) -> dict | None:
        """TRIM candidate: analyst TRIM."""
        return _shadow(
            ticker=ticker,
            v2_action="TRIM",
            analyst_action="TRIM",
            conviction_level="MEDIUM",
            intel_read=_intel_read(2, insufficient=True),
        )

    def test_mixed_portfolio_has_buy_shadow_actions(self):
        diagnostics = (
            [self._buy_card(f"BUY{i}") for i in range(4)]
            + [self._hold_card(f"HLD{i}") for i in range(4)]
            + [self._trim_card(f"TRM{i}") for i in range(2)]
        )
        valid = [d for d in diagnostics if d is not None]
        assert len(valid) == 10

        summary = summarize_shadow_diagnostics(valid, total_cards=10)
        v3_counts = summary.get("v3_shadow_action_counts", {})

        assert v3_counts.get("BUY", 0) >= 2, (
            f"Expected ≥2 BUY in shadow, got {v3_counts}"
        )
        assert v3_counts.get("HOLD", 0) >= 2, (
            f"Expected ≥2 HOLD in shadow (true HOLDs), got {v3_counts}"
        )

    def test_no_all_hold_collapse_in_shadow(self):
        """The all-HOLD collapse regression test: mixed signals must not all produce HOLD."""
        diagnostics = [self._buy_card(f"CARD{i}") for i in range(10)]
        valid = [d for d in diagnostics if d is not None]
        summary = summarize_shadow_diagnostics(valid, total_cards=10)
        v3_counts = summary.get("v3_shadow_action_counts", {})
        # After the fix, BUY cards with 3 trusted signals must not all be HOLD.
        assert v3_counts.get("BUY", 0) > 0, (
            f"All-HOLD collapse still occurring: {v3_counts}"
        )
        assert v3_counts.get("HOLD", 0) < 10, (
            f"All-HOLD collapse still occurring: {v3_counts}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# Section 6: TRIM / SELL risk-protection never affected
# ══════════════════════════════════════════════════════════════════════════════

class TestRiskProtectionPreserved:
    """TRIM and SELL actions are never downgraded by evidence-quality checks."""

    def test_trim_zero_trusted_still_trim(self):
        result = _shadow(
            v2_action="TRIM",
            analyst_action="TRIM",
            conviction_level="MEDIUM",
            intel_read=_intel_read(0, insufficient=True),
        )
        assert result is not None
        assert result["v3_shadow_action"] == "TRIM"

    def test_trim_three_trusted_still_trim(self):
        result = _shadow(
            v2_action="TRIM",
            analyst_action="TRIM",
            conviction_level="MEDIUM",
            intel_read=_intel_read(3, insufficient=True),
        )
        assert result is not None
        assert result["v3_shadow_action"] == "TRIM"

    def test_sell_zero_trusted_still_sell_or_trim(self):
        result = _shadow(
            v2_action="SELL",
            analyst_action="SELL",
            conviction_level="HIGH",
            technical_signal="BEARISH",
            risk_flag="Severe liquidity risk and potential insolvency",
            intel_read=_intel_read(0, insufficient=True),
        )
        assert result is not None
        assert result["v3_shadow_action"] in {"SELL", "TRIM"}


# ══════════════════════════════════════════════════════════════════════════════
# Section 7: conviction ladder preserved for partial-evidence cards
# ══════════════════════════════════════════════════════════════════════════════

class TestConvictionLadder:
    """Evidence-quality axis band maps correctly to conviction in v3 shadow."""

    def test_three_trusted_strong_evidence_high_conviction_possible(self):
        """3 trusted → AxisBand.STRONG → conviction can be HIGH in v3."""
        result = _shadow(
            v2_action="BUY",
            analyst_action="BUY",
            conviction_level="HIGH",
            intel_read=_intel_read(3, insufficient=True),
        )
        assert result is not None
        # With STRONG evidence the guardrail does not fire for HIGH conviction
        assert result["v3_shadow_conviction"] in {"HIGH", "MEDIUM"}

    def test_one_trusted_ok_evidence_medium_conviction(self):
        """1 trusted → AxisBand.OK → conviction capped (not THIN so BUY still possible)."""
        result = _shadow(
            v2_action="BUY",
            analyst_action="BUY",
            conviction_level="HIGH",
            intel_read=_intel_read(1, insufficient=True),
        )
        assert result is not None
        assert result["v3_shadow_action"] == "BUY"
        # Conviction should not be HIGH with only 1 trusted signal
        assert result["v3_shadow_conviction"] in {"MEDIUM", "LOW"}

    def test_zero_trusted_thin_evidence_hold_low_conviction(self):
        """0 trusted → THIN → HOLD with LOW conviction."""
        result = _shadow(
            v2_action="HOLD",
            analyst_action="BUY",
            conviction_level="HIGH",
            intel_read=_intel_read(0, insufficient=True),
        )
        assert result is not None
        assert result["v3_shadow_action"] == "HOLD"
        assert result["v3_shadow_conviction"] == "LOW"
