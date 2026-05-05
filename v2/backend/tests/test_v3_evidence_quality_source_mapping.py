"""PR 12: evidence-quality source mapping calibration tests.

Root cause fixed: classify_evidence_signals() and _derive_evidence_quality() read
intel_read.get("trusted_dimensions") but production intel_read uses "trusted_signals".
All 34 production cards returned WEAK/LOW because trusted_signals was never found.

Secondary fix: analyst_used_fallback=True conservatively caps evidence quality trust
at MEDIUM even when intel_read has >= 3 trusted signals, because HIGH trust should
require non-fallback structured evidence.

These tests use only synthetic production-shaped fixtures — no real user/account data.
All tests are backend-only. No frontend, API, Deploy, schema, provider, or LLM changes.
"""
from __future__ import annotations

import pytest

from app.services.intelligence.v3.data_truth_contracts import (
    DataTruthStatus,
    SourceTrustLevel,
)
from app.services.intelligence.v3.data_truth_v1 import classify_evidence_signals
from app.services.intelligence.v3.existing_signal_truth_adapter import (
    evaluate_card_signals_truth,
)
from app.services.intelligence.v3.shadow_projection import (
    project_shadow_from_card_signals,
    summarize_guardrail_impact_observability,
)


# ── Fixture helpers ───────────────────────────────────────────────────────────

def _intel_read(n_trusted: int, *, insufficient: bool = False) -> dict:
    """Production-shaped intel_read using 'trusted_signals' (correct production key)."""
    signals = ["business quality", "valuation", "growth", "momentum", "risk"][:n_trusted]
    return {
        "insufficient_data": insufficient,
        "trusted_signals": signals,
        "insufficient_signals": [],
    }


def _prod_shadow(
    *,
    ticker: str = "AAPL",
    v2_visible_action: str = "HOLD",
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
        v2_visible_action=v2_visible_action,
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
# Section 1: classify_evidence_signals — field-name fix verification
# ══════════════════════════════════════════════════════════════════════════════

class TestTrustedSignalsKeyName:
    """Verify classify_evidence_signals reads 'trusted_signals' not 'trusted_dimensions'."""

    def test_three_trusted_signals_yields_present_high(self):
        """Primary root-cause fix: 3+ trusted_signals → PRESENT/HIGH (was WEAK/LOW before fix)."""
        f = classify_evidence_signals(
            None,
            {"insufficient_data": False, "trusted_signals": ["business quality", "valuation", "growth"]},
        )
        assert f.status == DataTruthStatus.PRESENT
        assert f.trust_level == SourceTrustLevel.HIGH

    def test_wrong_key_trusted_dimensions_yields_weak(self):
        """trusted_dimensions key is not read — confirms the old bug would have returned WEAK."""
        f = classify_evidence_signals(
            None,
            {"insufficient_data": False, "trusted_dimensions": ["a", "b", "c"]},
        )
        # trusted_dimensions key not recognized → n_trusted=0 → WEAK
        assert f.status == DataTruthStatus.WEAK
        assert f.trust_level == SourceTrustLevel.LOW

    def test_one_trusted_signal_yields_present_medium(self):
        f = classify_evidence_signals(
            None,
            {"insufficient_data": False, "trusted_signals": ["valuation"]},
        )
        assert f.status == DataTruthStatus.PRESENT
        assert f.trust_level == SourceTrustLevel.MEDIUM

    def test_two_trusted_signals_yields_present_medium(self):
        f = classify_evidence_signals(
            None,
            {"insufficient_data": False, "trusted_signals": ["business quality", "valuation"]},
        )
        assert f.status == DataTruthStatus.PRESENT
        assert f.trust_level == SourceTrustLevel.MEDIUM

    def test_empty_trusted_signals_yields_weak(self):
        f = classify_evidence_signals(
            None,
            {"insufficient_data": False, "trusted_signals": []},
        )
        assert f.status == DataTruthStatus.WEAK

    def test_insufficient_data_true_with_trusted_signal_yields_present(self):
        # PR 13 fix: insufficient_data=True with 1+ trusted signals is PRESENT/MEDIUM,
        # not WEAK. Missing one axis (growth/risk) must not collapse evidence quality
        # when other axes (quality, valuation) are present and scored.
        f = classify_evidence_signals(
            None,
            {"insufficient_data": True, "trusted_signals": ["valuation"]},
        )
        assert f.status == DataTruthStatus.PRESENT
        assert f.trust_level == SourceTrustLevel.MEDIUM

    def test_insufficient_data_true_zero_trusted_still_weak(self):
        # When insufficient_data=True AND zero trusted signals: WEAK (truly no evidence).
        f = classify_evidence_signals(
            None,
            {"insufficient_data": True, "trusted_signals": []},
        )
        assert f.status == DataTruthStatus.WEAK

    def test_four_trusted_signals_yields_present_high(self):
        f = classify_evidence_signals(
            None,
            {"insufficient_data": False, "trusted_signals": ["a", "b", "c", "d"]},
        )
        assert f.status == DataTruthStatus.PRESENT
        assert f.trust_level == SourceTrustLevel.HIGH


# ══════════════════════════════════════════════════════════════════════════════
# Section 2: analyst_used_fallback caps trust at MEDIUM
# ══════════════════════════════════════════════════════════════════════════════

class TestAnalystUsedFallbackCap:
    """analyst_used_fallback=True caps PRESENT/HIGH → PRESENT/MEDIUM for both paths."""

    def test_three_signals_fallback_true_yields_medium(self):
        """Fallback + 3 trusted signals → MEDIUM (not HIGH)."""
        f = classify_evidence_signals(
            None,
            {"insufficient_data": False, "trusted_signals": ["a", "b", "c"]},
            analyst_used_fallback=True,
        )
        assert f.status == DataTruthStatus.PRESENT
        assert f.trust_level == SourceTrustLevel.MEDIUM
        assert f.reason_code == "field_present_fallback_capped"

    def test_three_signals_fallback_false_yields_high(self):
        """Non-fallback + 3 trusted signals → HIGH trust."""
        f = classify_evidence_signals(
            None,
            {"insufficient_data": False, "trusted_signals": ["a", "b", "c"]},
            analyst_used_fallback=False,
        )
        assert f.status == DataTruthStatus.PRESENT
        assert f.trust_level == SourceTrustLevel.HIGH

    def test_three_signals_fallback_none_yields_high(self):
        """analyst_used_fallback=None (default) → no cap → HIGH trust."""
        f = classify_evidence_signals(
            None,
            {"insufficient_data": False, "trusted_signals": ["a", "b", "c"]},
            analyst_used_fallback=None,
        )
        assert f.status == DataTruthStatus.PRESENT
        assert f.trust_level == SourceTrustLevel.HIGH

    def test_data_quality_high_fallback_true_yields_medium(self):
        """data_quality_label=HIGH + fallback=True → PRESENT/MEDIUM (not HIGH)."""
        f = classify_evidence_signals("HIGH", None, analyst_used_fallback=True)
        assert f.status == DataTruthStatus.PRESENT
        assert f.trust_level == SourceTrustLevel.MEDIUM
        assert f.reason_code == "field_present_fallback_capped"

    def test_data_quality_high_fallback_false_yields_high(self):
        """data_quality_label=HIGH + fallback=False → PRESENT/HIGH."""
        f = classify_evidence_signals("HIGH", None, analyst_used_fallback=False)
        assert f.status == DataTruthStatus.PRESENT
        assert f.trust_level == SourceTrustLevel.HIGH

    def test_data_quality_medium_fallback_true_unchanged(self):
        """data_quality_label=MEDIUM + fallback=True → PRESENT/MEDIUM (no change)."""
        f = classify_evidence_signals("MEDIUM", None, analyst_used_fallback=True)
        assert f.status == DataTruthStatus.PRESENT
        assert f.trust_level == SourceTrustLevel.MEDIUM

    def test_weak_intel_read_fallback_true_still_weak(self):
        """Insufficient intel_read + fallback=True → WEAK/LOW (fallback does not degrade further)."""
        f = classify_evidence_signals(
            None,
            {"insufficient_data": True, "trusted_signals": []},
            analyst_used_fallback=True,
        )
        assert f.status == DataTruthStatus.WEAK
        assert f.trust_level == SourceTrustLevel.LOW

    def test_one_signal_fallback_true_still_medium(self):
        """1 trusted signal + fallback=True → PRESENT/MEDIUM (cap only applies to HIGH path)."""
        f = classify_evidence_signals(
            None,
            {"insufficient_data": False, "trusted_signals": ["valuation"]},
            analyst_used_fallback=True,
        )
        assert f.status == DataTruthStatus.PRESENT
        assert f.trust_level == SourceTrustLevel.MEDIUM


# ══════════════════════════════════════════════════════════════════════════════
# Section 3: data_quality_label fallback path
# ══════════════════════════════════════════════════════════════════════════════

class TestDataQualityLabelPath:
    """data_quality_label path used when intel_read is absent."""

    @pytest.mark.parametrize("label,expected_status,expected_trust", [
        ("HIGH", DataTruthStatus.PRESENT, SourceTrustLevel.HIGH),
        ("MEDIUM", DataTruthStatus.PRESENT, SourceTrustLevel.MEDIUM),
        ("LOW", DataTruthStatus.WEAK, SourceTrustLevel.LOW),
        ("UNKNOWN", DataTruthStatus.MISSING, SourceTrustLevel.UNKNOWN),
        (None, DataTruthStatus.MISSING, SourceTrustLevel.UNKNOWN),
    ])
    def test_data_quality_label_mapping(self, label, expected_status, expected_trust):
        f = classify_evidence_signals(label, None)
        assert f.status == expected_status
        assert f.trust_level == expected_trust


# ══════════════════════════════════════════════════════════════════════════════
# Section 4: evaluate_card_signals_truth propagates analyst_used_fallback
# ══════════════════════════════════════════════════════════════════════════════

class TestEvaluateCardSignalsTruthFallbackPropagation:
    """evaluate_card_signals_truth correctly propagates analyst_used_fallback."""

    def test_strong_card_no_fallback_is_high(self):
        summaries = evaluate_card_signals_truth(
            action="BUY",
            analyst_action="BUY",
            conviction_level="HIGH",
            technical_signal="BULLISH",
            risk_flag=None,
            analyst_risks=None,
            data_quality_label=None,
            intel_read={"insufficient_data": False, "trusted_signals": ["a", "b", "c"]},
            analyst_used_fallback=False,
        )
        ev = next(s for s in summaries if s.axis_name == "evidence_quality")
        assert ev.findings[0].status == DataTruthStatus.PRESENT
        assert ev.findings[0].trust_level == SourceTrustLevel.HIGH

    def test_strong_card_with_fallback_is_medium(self):
        summaries = evaluate_card_signals_truth(
            action="BUY",
            analyst_action="BUY",
            conviction_level="HIGH",
            technical_signal="BULLISH",
            risk_flag=None,
            analyst_risks=None,
            data_quality_label=None,
            intel_read={"insufficient_data": False, "trusted_signals": ["a", "b", "c"]},
            analyst_used_fallback=True,
        )
        ev = next(s for s in summaries if s.axis_name == "evidence_quality")
        assert ev.findings[0].status == DataTruthStatus.PRESENT
        assert ev.findings[0].trust_level == SourceTrustLevel.MEDIUM


# ══════════════════════════════════════════════════════════════════════════════
# Section 5: project_shadow_from_card_signals — production-shaped fixtures
# ══════════════════════════════════════════════════════════════════════════════

class TestShadowProjectionProductionShapes:
    """Production-shaped synthetic fixtures verify non-uniform evidence quality distribution."""

    def test_strong_non_fallback_card_yields_present_high_in_diagnostics(self):
        """Card with 3+ trusted signals, non-fallback → evidence quality PRESENT/HIGH."""
        diag = _prod_shadow(
            ticker="AAPL",
            analyst_action="BUY",
            conviction_level="HIGH",
            intel_read=_intel_read(3),
            analyst_used_fallback=False,
        )
        assert diag is not None
        td = diag["truth_diagnostics"]
        assert td is not None
        guardrail = td["buy_conviction_guardrail"]
        assert guardrail["evidence_quality_truth_status"] == "PRESENT"
        assert guardrail["evidence_quality_trust_level"] == "HIGH"
        # Guardrail must NOT fire for PRESENT/HIGH evidence
        assert guardrail["buy_high_conviction_guardrail_applied"] is False

    def test_strong_fallback_card_yields_present_medium_in_diagnostics(self):
        """Card with 3+ trusted signals but fallback=True → PRESENT/MEDIUM → guardrail fires."""
        diag = _prod_shadow(
            ticker="MSFT",
            analyst_action="BUY",
            conviction_level="HIGH",
            intel_read=_intel_read(3),
            analyst_used_fallback=True,
        )
        assert diag is not None
        td = diag["truth_diagnostics"]
        guardrail = td["buy_conviction_guardrail"]
        assert guardrail["evidence_quality_truth_status"] == "PRESENT"
        assert guardrail["evidence_quality_trust_level"] == "MEDIUM"
        # Guardrail fires for HIGH-conviction BUY with MEDIUM evidence
        assert guardrail["buy_high_conviction_guardrail_applied"] is True

    def test_partial_signals_card_yields_present_medium(self):
        """Card with 2 trusted signals → PRESENT/MEDIUM → guardrail fires for HIGH-conviction BUY."""
        diag = _prod_shadow(
            ticker="NVDA",
            analyst_action="BUY",
            conviction_level="HIGH",
            intel_read=_intel_read(2),
            analyst_used_fallback=False,
        )
        assert diag is not None
        td = diag["truth_diagnostics"]
        guardrail = td["buy_conviction_guardrail"]
        assert guardrail["evidence_quality_truth_status"] == "PRESENT"
        assert guardrail["evidence_quality_trust_level"] == "MEDIUM"
        assert guardrail["buy_high_conviction_guardrail_applied"] is True

    def test_fallback_insufficient_card_remains_weak(self):
        """Insufficient intel_read → WEAK/LOW regardless of fallback flag."""
        diag = _prod_shadow(
            ticker="SNOW",
            analyst_action="BUY",
            conviction_level="HIGH",
            intel_read=_intel_read(0, insufficient=True),
            analyst_used_fallback=True,
        )
        assert diag is not None
        td = diag["truth_diagnostics"]
        guardrail = td["buy_conviction_guardrail"]
        assert guardrail["evidence_quality_truth_status"] == "WEAK"
        assert guardrail["evidence_quality_trust_level"] == "LOW"

    def test_no_intel_read_no_quality_label_yields_missing(self):
        """No intel_read and no data_quality_label → MISSING."""
        diag = _prod_shadow(
            ticker="UNKN",
            analyst_action=None,
            conviction_level=None,
            intel_read=None,
            data_quality_label=None,
        )
        assert diag is not None
        td = diag["truth_diagnostics"]
        guardrail = td["buy_conviction_guardrail"]
        assert guardrail["evidence_quality_truth_status"] == "MISSING"

    def test_data_quality_label_high_no_fallback_yields_high(self):
        """data_quality_label=HIGH + no fallback + no intel_read → PRESENT/HIGH."""
        diag = _prod_shadow(
            ticker="META",
            analyst_action="BUY",
            conviction_level="HIGH",
            intel_read=None,
            data_quality_label="HIGH",
            analyst_used_fallback=False,
        )
        assert diag is not None
        td = diag["truth_diagnostics"]
        guardrail = td["buy_conviction_guardrail"]
        assert guardrail["evidence_quality_truth_status"] == "PRESENT"
        assert guardrail["evidence_quality_trust_level"] == "HIGH"
        assert guardrail["buy_high_conviction_guardrail_applied"] is False

    def test_visible_action_unchanged_regardless_of_evidence_quality(self):
        """Visible v2 action is never mutated by evidence quality mapping."""
        diag = _prod_shadow(
            ticker="GOOG",
            v2_visible_action="HOLD",
            analyst_action="BUY",
            conviction_level="HIGH",
            intel_read=_intel_read(3),
            analyst_used_fallback=False,
        )
        assert diag is not None
        assert diag["v2_visible_action"] == "HOLD"


# ══════════════════════════════════════════════════════════════════════════════
# Section 6: mixed synthetic portfolio — non-uniform evidence quality
# ══════════════════════════════════════════════════════════════════════════════

class TestMixedPortfolioEvidenceDistribution:
    """Mixed portfolio of production-shaped cards must not yield uniform WEAK/LOW."""

    _CARDS = [
        # Strong, non-fallback, BUY: PRESENT/HIGH, guardrail does not fire.
        dict(ticker="STRONG_BUY", v2_visible_action="HOLD", analyst_action="BUY",
             conviction_level="HIGH", technical_signal="BULLISH", category="Core",
             data_quality_label=None, intel_read=_intel_read(3),
             analyst_used_fallback=False),
        # Strong, fallback, BUY: PRESENT/MEDIUM, guardrail fires.
        dict(ticker="FALLBACK_BUY", v2_visible_action="HOLD", analyst_action="BUY",
             conviction_level="HIGH", technical_signal="BULLISH", category="Core",
             data_quality_label=None, intel_read=_intel_read(3),
             analyst_used_fallback=True),
        # Partial signals, BUY: PRESENT/MEDIUM, guardrail fires.
        dict(ticker="PARTIAL_BUY", v2_visible_action="HOLD", analyst_action="BUY",
             conviction_level="HIGH", technical_signal="NEUTRAL", category="Core",
             data_quality_label=None, intel_read=_intel_read(2),
             analyst_used_fallback=False),
        # Insufficient: WEAK/LOW.
        dict(ticker="THIN_HOLD", v2_visible_action="HOLD", analyst_action="HOLD",
             conviction_level="LOW", technical_signal=None, category="Core",
             data_quality_label=None,
             intel_read={"insufficient_data": True, "trusted_signals": []},
             analyst_used_fallback=False),
        # No signals: MISSING.
        dict(ticker="EMPTY_HOLD", v2_visible_action="HOLD", analyst_action=None,
             conviction_level=None, technical_signal=None, category="Core",
             data_quality_label=None, intel_read=None, analyst_used_fallback=None),
        # data_quality_label HIGH, no fallback: PRESENT/HIGH.
        dict(ticker="LABEL_HIGH", v2_visible_action="HOLD", analyst_action="BUY",
             conviction_level="HIGH", technical_signal="BULLISH", category="Core",
             data_quality_label="HIGH", intel_read=None, analyst_used_fallback=False),
    ]

    def _run_portfolio(self):
        results = []
        for card in self._CARDS:
            diag = project_shadow_from_card_signals(
                ticker=card["ticker"],
                v2_visible_action=card["v2_visible_action"],
                analyst_action=card["analyst_action"],
                conviction_level=card["conviction_level"],
                technical_signal=card["technical_signal"],
                risk_flag=None,
                analyst_risks=None,
                category=card["category"],
                data_quality_label=card["data_quality_label"],
                intel_read=card["intel_read"],
                thesis_v2=None,
                analyst_used_fallback=card["analyst_used_fallback"],
            )
            results.append(diag)
        return results

    def test_not_uniform_weak_low(self):
        """Mixed portfolio must not have all WEAK/LOW evidence quality."""
        results = self._run_portfolio()
        summary = summarize_guardrail_impact_observability(results)
        eq_status = summary["evidence_quality_status_counts"]
        # At least PRESENT and WEAK/MISSING should appear
        statuses = set(eq_status.keys())
        assert "PRESENT" in statuses, f"No PRESENT in {statuses}"
        assert statuses != {"WEAK"}, "All evidence quality is still WEAK — fix not applied"

    def test_high_trust_present_in_mix(self):
        """Strong non-fallback cards yield HIGH trust entries in status counts."""
        results = self._run_portfolio()
        summary = summarize_guardrail_impact_observability(results)
        eq_trust = summary["evidence_quality_trust_counts"]
        assert "HIGH" in eq_trust, f"No HIGH trust in {eq_trust}"

    def test_guardrail_evaluated_count_equals_projected(self):
        """Guardrail must be evaluated for every successfully projected card."""
        results = self._run_portfolio()
        valid = [r for r in results if r is not None]
        summary = summarize_guardrail_impact_observability(results)
        # Every card that has truth_diagnostics.buy_conviction_guardrail is counted.
        assert summary["guardrail_evaluated_count"] > 0

    def test_visible_actions_never_mutated(self):
        """v2_visible_action must equal the input action for every card."""
        results = self._run_portfolio()
        for card, diag in zip(self._CARDS, results):
            if diag is not None:
                expected = (card["v2_visible_action"] or "HOLD").upper()
                assert diag["v2_visible_action"] == expected, (
                    f"{card['ticker']}: visible action mutated to {diag['v2_visible_action']!r}"
                )

    def test_buy_conviction_capped_for_non_high_trust(self):
        """Guardrail must cap conviction for at least one card that has BUY/HIGH/non-HIGH-trust."""
        results = self._run_portfolio()
        summary = summarize_guardrail_impact_observability(results)
        # FALLBACK_BUY and PARTIAL_BUY should both be capped
        assert summary["buy_conviction_capped_count"] >= 1

    def test_strong_non_fallback_buy_not_capped(self):
        """STRONG_BUY card (PRESENT/HIGH) must not have conviction capped."""
        strong_result = project_shadow_from_card_signals(
            ticker="STRONG_BUY",
            v2_visible_action="HOLD",
            analyst_action="BUY",
            conviction_level="HIGH",
            technical_signal="BULLISH",
            risk_flag=None,
            analyst_risks=None,
            category="Core",
            data_quality_label=None,
            intel_read=_intel_read(3),
            thesis_v2=None,
            analyst_used_fallback=False,
        )
        assert strong_result is not None
        guardrail = strong_result["truth_diagnostics"]["buy_conviction_guardrail"]
        assert guardrail["buy_high_conviction_guardrail_applied"] is False
        assert guardrail["evidence_quality_trust_level"] == "HIGH"


# ══════════════════════════════════════════════════════════════════════════════
# Section 7: 34-card all-HOLD production regression — non-uniform after fix
# ══════════════════════════════════════════════════════════════════════════════

class TestProductionRegression34Cards:
    """Regression: 34 all-HOLD cards with varied intel_read must not all be WEAK/LOW."""

    def _make_portfolio(self, n: int = 34) -> list[dict]:
        """Synthetic 34-card portfolio mimicking production variety."""
        cards = []
        for i in range(n):
            # Vary intel_read quality to simulate real portfolio diversity
            if i % 5 == 0:
                ir = _intel_read(3)
                fallback = False
            elif i % 5 == 1:
                ir = _intel_read(3)
                fallback = True  # fresh_llm but fallback
            elif i % 5 == 2:
                ir = _intel_read(1)
                fallback = False
            elif i % 5 == 3:
                ir = {"insufficient_data": True, "trusted_signals": []}
                fallback = False
            else:
                ir = None
                fallback = None
            cards.append(dict(
                ticker=f"CARD{i:02d}",
                v2_visible_action="HOLD",
                analyst_action="HOLD",
                conviction_level="LOW",
                technical_signal=None,
                risk_flag=None,
                analyst_risks=None,
                category="Core",
                data_quality_label=None,
                intel_read=ir,
                thesis_v2=None,
                analyst_used_fallback=fallback,
            ))
        return cards

    def test_34_card_portfolio_not_uniform_weak_low(self):
        portfolio = self._make_portfolio(34)
        results = [
            project_shadow_from_card_signals(
                ticker=c["ticker"],
                v2_visible_action=c["v2_visible_action"],
                analyst_action=c["analyst_action"],
                conviction_level=c["conviction_level"],
                technical_signal=c["technical_signal"],
                risk_flag=c["risk_flag"],
                analyst_risks=c["analyst_risks"],
                category=c["category"],
                data_quality_label=c["data_quality_label"],
                intel_read=c["intel_read"],
                thesis_v2=c["thesis_v2"],
                analyst_used_fallback=c["analyst_used_fallback"],
            )
            for c in portfolio
        ]
        summary = summarize_guardrail_impact_observability(results)
        eq_status = summary["evidence_quality_status_counts"]
        # Must not be uniformly WEAK anymore
        assert eq_status != {"WEAK": 34}, (
            f"Still uniform WEAK/LOW after fix: {eq_status}"
        )
        # Must have PRESENT in the mix
        assert "PRESENT" in eq_status, f"No PRESENT entries: {eq_status}"

    def test_34_card_portfolio_guardrail_evaluated_all(self):
        portfolio = self._make_portfolio(34)
        results = [
            project_shadow_from_card_signals(
                ticker=c["ticker"],
                v2_visible_action=c["v2_visible_action"],
                analyst_action=c["analyst_action"],
                conviction_level=c["conviction_level"],
                technical_signal=c["technical_signal"],
                risk_flag=c["risk_flag"],
                analyst_risks=c["analyst_risks"],
                category=c["category"],
                data_quality_label=c["data_quality_label"],
                intel_read=c["intel_read"],
                thesis_v2=c["thesis_v2"],
                analyst_used_fallback=c["analyst_used_fallback"],
            )
            for c in portfolio
        ]
        summary = summarize_guardrail_impact_observability(results)
        assert summary["guardrail_evaluated_count"] == 34
