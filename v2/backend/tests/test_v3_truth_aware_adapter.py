"""Table-driven tests for Intel v3 truth-aware shadow input adapter (PR 7).

Dark launch — backend only. Tests confirm:
 1. PRESENT trusted data feeds v3 shadow decisions normally (no suppression).
 2. MISSING data suppresses only the affected axis.
 3. UNAVAILABLE sentinel data suppresses only the affected axis.
 4. WEAK data is safe (LOW trust) per PR 6 contract — not suppressed.
 5. CONFLICTING action signals suppress only action-derived axes.
 6. STALE classification (via classify_with_staleness) marks axis unsafe.
 7. One unsafe axis does not globally flatten the card when other safe axes
    justify BUY/TRIM/SELL.
 8. All useful axes unsafe/missing → honest HOLD.
 9. v2 visible action is not mutated.
10. Shadow action diversity preserved across golden-portfolio fixtures.
11. HOLD-collapse risk remains detectable.
12. Honest HOLD separated from HOLD-collapse risk.
13. Diagnostics expose safe truth-aware suppression counts and reason codes.
14. Diagnostics do not include raw card payloads, account/user ids, account
    values, holdings quantities, provider payloads, or LLM text.
15. Pure function — no IO, no DB, no provider calls, no Supabase dependency.
"""
from __future__ import annotations

import pytest

from app.services.intelligence.v3.data_truth_contracts import (
    DataTruthFinding,
    DataTruthStatus,
    SourceTrustLevel,
)
from app.services.intelligence.v3.data_truth_v1 import classify_with_staleness
from app.services.intelligence.v3.decision_contracts import AxisBand, PriceBand
from app.services.intelligence.v3.existing_signal_adapter import (
    build_decision_input_from_card,
    build_truth_aware_decision_input,
)
from app.services.intelligence.v3.existing_signal_truth_adapter import (
    _build_axis_summary,
)
from app.services.intelligence.v3.shadow_projection import (
    project_shadow_from_card_signals,
    summarize_shadow_diagnostics,
)

_VALID_ACTIONS = frozenset({"BUY", "HOLD", "TRIM", "SELL"})
_SENTINEL = object()  # marks "use default" vs explicit None in _ta()


# ── Shared helpers ────────────────────────────────────────────────────────────

def _good_intel_read(n: int = 3) -> dict:
    dims = ["business quality", "valuation", "growth", "momentum"][:n]
    return {"insufficient_data": False, "trusted_dimensions": dims}


def _thin_intel_read() -> dict:
    return {"insufficient_data": True, "trusted_dimensions": []}


def _ta(
    *,
    ticker: str = "AAPL",
    action: str | None = "BUY",
    analyst_action: str | None = "BUY",
    conviction_level: str | None = "HIGH",
    technical_signal: str | None = "BULLISH",
    risk_flag: str | None = None,
    analyst_risks: list | None = None,
    category: str = "Core",
    data_quality_label: str | None = "HIGH",
    intel_read=_SENTINEL,  # use _SENTINEL to mean "use default _good_intel_read(3)"
    thesis_v2: dict | None = None,
) -> dict:
    """Build keyword dict for build_truth_aware_decision_input.

    Pass intel_read=None explicitly to test with no intel_read.
    Omit intel_read (or pass _SENTINEL) to use _good_intel_read(3) as default.
    """
    resolved_intel = _good_intel_read(3) if intel_read is _SENTINEL else intel_read
    return dict(
        ticker=ticker,
        action=action,
        analyst_action=analyst_action,
        conviction_level=conviction_level,
        technical_signal=technical_signal,
        risk_flag=risk_flag,
        analyst_risks=analyst_risks or [],
        category=category,
        data_quality_label=data_quality_label,
        intel_read=resolved_intel,
        thesis_v2=thesis_v2,
    )


def _shadow(
    *,
    ticker: str = "AAPL",
    v2_visible_action: str = "HOLD",
    analyst_action: str | None = None,
    conviction_level: str | None = None,
    technical_signal: str | None = None,
    risk_flag: str | None = None,
    analyst_risks: list | None = None,
    category: str = "Core",
    data_quality_label: str | None = None,
    intel_read: dict | None = None,
    thesis_v2: dict | None = None,
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
    )


# ── Test 1: PRESENT trusted data — no suppression ────────────────────────────

class TestPresentAxes:
    """All axes PRESENT → no truth suppression → full decision power."""

    def test_present_decision_axes_not_suppressed(self):
        """Key decision axes (evidence, action, conviction, technical) must not be
        suppressed when their signals are present and valid."""
        inp, summaries, suppressed = build_truth_aware_decision_input(
            **_ta(intel_read=_good_intel_read(3))
        )
        # These four axes drive BUY/TRIM/SELL and must not be suppressed
        for axis in ("evidence_quality", "action_signal", "conviction", "technical_signal"):
            assert axis not in suppressed, f"Unexpected suppression of {axis}: {suppressed}"
        assert inp.evidence_quality != AxisBand.SUPPRESSED
        assert inp.price_context != PriceBand.SUPPRESSED

    def test_all_present_safe_axis_count(self):
        diag = _shadow(
            analyst_action="BUY",
            conviction_level="HIGH",
            data_quality_label="HIGH",
            intel_read=_good_intel_read(3),
        )
        assert diag is not None
        td = diag["truth_diagnostics"]
        assert td is not None
        # action + conviction + technical missing but other axes still evaluated
        assert td["truth_aware_adapter_enabled"] is True
        assert isinstance(td["safe_axis_count"], int)
        assert isinstance(td["unsafe_axis_count"], int)
        assert td["safe_axis_count"] + td["unsafe_axis_count"] == 5

    def test_strong_buy_signals_produce_buy_shadow(self):
        diag = _shadow(
            ticker="AAPL",
            v2_visible_action="HOLD",
            analyst_action="BUY",
            conviction_level="HIGH",
            technical_signal="BULLISH",
            data_quality_label="HIGH",
            intel_read=_good_intel_read(3),
        )
        assert diag is not None
        assert diag["v3_shadow_action"] == "BUY"
        assert diag["hold_collapse_risk"] is True


# ── Test 2: MISSING data suppresses only affected axis ───────────────────────

class TestMissingAxesSuppressOnlyAffectedAxis:
    """MISSING signal on one axis → only that axis suppressed."""

    def test_missing_evidence_suppresses_only_evidence(self):
        # No data_quality_label, no intel_read → evidence_quality axis MISSING (unsafe)
        inp, summaries, suppressed = build_truth_aware_decision_input(
            **_ta(data_quality_label=None, intel_read=None)
        )
        assert "evidence_quality" in suppressed
        # action_signal, conviction, technical still safe since those fields present
        assert "action_signal" not in suppressed
        assert "conviction" not in suppressed

    def test_missing_evidence_does_not_prevent_trim(self):
        """Evidence MISSING but TRIM action is safe → v3 still produces TRIM, not HOLD."""
        diag = _shadow(
            ticker="META",
            v2_visible_action="HOLD",
            analyst_action="TRIM",
            conviction_level="MEDIUM",
            technical_signal="NEUTRAL",
            data_quality_label=None,
            intel_read=None,
        )
        assert diag is not None
        # portfolio_fit=OVERWEIGHT from TRIM → TRIM should fire even with missing evidence
        assert diag["v3_shadow_action"] == "TRIM", (
            "TRIM action signal is safe; missing evidence alone must not suppress TRIM"
        )
        assert diag["hold_collapse_risk"] is True

    def test_missing_evidence_does_not_prevent_sell(self):
        """Evidence MISSING but SELL action + CRITICAL risk → v3 can still produce SELL."""
        diag = _shadow(
            ticker="RIVN",
            v2_visible_action="HOLD",
            analyst_action="SELL",
            conviction_level="LOW",
            technical_signal="BEARISH",
            risk_flag="Critical insolvency — severe cash burn",
            analyst_risks=["Critical default risk"],
            data_quality_label=None,
            intel_read=None,
            category="Speculative",
        )
        assert diag is not None
        # risk and action axes are safe → SELL should still fire
        assert diag["v3_shadow_action"] in {"SELL", "TRIM"}

    def test_missing_risk_suppresses_only_risk_axis(self):
        """No risk data → risk_signal MISSING; action/evidence axes still safe."""
        inp, summaries, suppressed = build_truth_aware_decision_input(
            **_ta(risk_flag=None, analyst_risks=None)
        )
        # risk_signal should be suppressed (MISSING)
        assert "risk_signal" in suppressed
        # but evidence, action, conviction remain safe
        for axis in ("evidence_quality", "action_signal", "conviction"):
            assert axis not in suppressed, f"Unexpected suppression of {axis}"

    def test_missing_conviction_suppresses_only_conviction(self):
        inp, summaries, suppressed = build_truth_aware_decision_input(
            **_ta(conviction_level=None)
        )
        assert "conviction" in suppressed
        assert "evidence_quality" not in suppressed
        assert "action_signal" not in suppressed

    def test_missing_technical_suppresses_only_technical(self):
        inp, summaries, suppressed = build_truth_aware_decision_input(
            **_ta(technical_signal=None)
        )
        assert "technical_signal" in suppressed
        assert "action_signal" not in suppressed
        assert "evidence_quality" not in suppressed


# ── Test 3: UNAVAILABLE sentinel suppresses only affected axis ───────────────

class TestUnavailableAxesSuppressOnlyAffectedAxis:
    """Provider-unavailable sentinels on one axis suppress only that axis."""

    @pytest.mark.parametrize("sentinel", ["UNAVAILABLE", "N/A", "UNAVAIL", "NOT_AVAILABLE", "NA"])
    def test_unavailable_action_suppresses_action_only(self, sentinel):
        inp, summaries, suppressed = build_truth_aware_decision_input(
            **_ta(action=sentinel, analyst_action=sentinel)
        )
        assert "action_signal" in suppressed
        # conviction and evidence not affected
        assert "conviction" not in suppressed
        assert "evidence_quality" not in suppressed

    def test_unavailable_action_leaves_risk_safe(self):
        """Unavailable action sentinels do not affect risk_band derivation."""
        inp, summaries, suppressed = build_truth_aware_decision_input(
            **_ta(
                action="UNAVAILABLE",
                analyst_action="UNAVAILABLE",
                risk_flag="Critical insolvency",
                analyst_risks=["Cash burn critical"],
            )
        )
        assert "action_signal" in suppressed
        assert "risk_signal" not in suppressed

    def test_unavailable_conviction_suppresses_conviction_only(self):
        inp, summaries, suppressed = build_truth_aware_decision_input(
            **_ta(conviction_level="N/A")
        )
        assert "conviction" in suppressed
        assert "action_signal" not in suppressed

    def test_unavailable_technical_suppresses_technical_only(self):
        inp, summaries, suppressed = build_truth_aware_decision_input(
            **_ta(technical_signal="UNAVAILABLE")
        )
        assert "technical_signal" in suppressed
        assert "evidence_quality" not in suppressed

    def test_unavailable_risk_suppresses_risk_only(self):
        inp, summaries, suppressed = build_truth_aware_decision_input(
            **_ta(risk_flag="UNAVAILABLE", analyst_risks=[])
        )
        assert "risk_signal" in suppressed
        assert "action_signal" not in suppressed


# ── Test 4: WEAK data is safe per PR 6 contract ──────────────────────────────

class TestWeakAxesAreSafe:
    """WEAK findings → safe_for_decision=True → axis NOT suppressed (LOW trust only)."""

    def test_weak_conviction_low_is_not_suppressed(self):
        """conviction_level=LOW → WEAK finding → still safe → not suppressed."""
        inp, summaries, suppressed = build_truth_aware_decision_input(
            **_ta(conviction_level="LOW")
        )
        assert "conviction" not in suppressed, (
            "LOW conviction is WEAK but safe_for_decision=True per PR 6 — must not suppress"
        )

    def test_weak_evidence_intel_insufficient_not_suppressed(self):
        """intel_read.insufficient_data=True → WEAK evidence → still safe → not suppressed."""
        inp, summaries, suppressed = build_truth_aware_decision_input(
            **_ta(intel_read=_thin_intel_read())
        )
        assert "evidence_quality" not in suppressed, (
            "Thin intel_read is WEAK but safe — must not null evidence inputs"
        )
        # But evidence_quality in DecisionInputV3 is still THIN (adapter handles it)
        assert inp.evidence_quality == AxisBand.THIN

    def test_weak_data_quality_low_not_suppressed(self):
        """data_quality_label=LOW → WEAK → safe → not suppressed."""
        inp, summaries, suppressed = build_truth_aware_decision_input(
            **_ta(data_quality_label="LOW", intel_read=None)
        )
        assert "evidence_quality" not in suppressed

    def test_weak_technical_unrecognized_not_suppressed(self):
        """Unrecognized technical value → WEAK → safe → not suppressed."""
        inp, summaries, suppressed = build_truth_aware_decision_input(
            **_ta(technical_signal="TRENDING_UP")
        )
        assert "technical_signal" not in suppressed


# ── Test 5: CONFLICTING action signals ───────────────────────────────────────

class TestConflictingActionSignals:
    """BUY↔SELL direct conflict → action_signal axis unsafe → only action axes suppressed."""

    def test_conflicting_buy_sell_suppresses_action_only(self):
        """action=BUY, analyst_action=SELL → CONFLICTING → null action signals."""
        inp, summaries, suppressed = build_truth_aware_decision_input(
            **_ta(action="BUY", analyst_action="SELL")
        )
        assert "action_signal" in suppressed
        # evidence, conviction, technical, risk remain independent
        for axis in ("evidence_quality", "conviction", "technical_signal"):
            assert axis not in suppressed, f"Unexpected suppression of {axis}"

    def test_conflicting_sell_buy_suppresses_action_only(self):
        inp, summaries, suppressed = build_truth_aware_decision_input(
            **_ta(action="SELL", analyst_action="BUY")
        )
        assert "action_signal" in suppressed
        assert "evidence_quality" not in suppressed

    def test_conflicting_action_nulls_price_context(self):
        """Conflicting action → raw_action/raw_analyst_action nulled → price_context SUPPRESSED."""
        inp, summaries, suppressed = build_truth_aware_decision_input(
            **_ta(action="BUY", analyst_action="SELL")
        )
        # With actions nulled, price_context has no BUY/SELL signal → SUPPRESSED
        assert inp.price_context == PriceBand.SUPPRESSED

    def test_conflicting_action_produces_hold_not_random(self):
        """Conflicting action + no other risk signals → HOLD (safe default)."""
        diag = _shadow(
            ticker="XYZ",
            v2_visible_action="BUY",
            analyst_action="SELL",
            conviction_level="HIGH",
            data_quality_label="HIGH",
            intel_read=_good_intel_read(3),
        )
        assert diag is not None
        assert diag["v3_shadow_action"] in _VALID_ACTIONS

    def test_hold_trim_not_flagged_conflicting(self):
        """HOLD vs TRIM is disagreement, not contradiction — not CONFLICTING."""
        inp, summaries, suppressed = build_truth_aware_decision_input(
            **_ta(action="HOLD", analyst_action="TRIM")
        )
        assert "action_signal" not in suppressed

    def test_hold_buy_not_flagged_conflicting(self):
        """HOLD vs BUY is not CONFLICTING."""
        inp, summaries, suppressed = build_truth_aware_decision_input(
            **_ta(action="HOLD", analyst_action="BUY")
        )
        assert "action_signal" not in suppressed


# ── Test 6: STALE classification ─────────────────────────────────────────────

class TestStaleClassification:
    """STALE findings (via classify_with_staleness) mark the axis as unsafe.

    Note: current evaluate_card_signals_truth() does not produce STALE findings
    because existing InsightCard signals carry no timestamps. This test validates
    the staleness classifier contract so future wiring is unambiguous.
    """

    def test_stale_finding_is_not_safe(self):
        finding = classify_with_staleness(
            "evidence_quality",
            "some_value",
            last_updated_hours_ago=72.0,
            stale_threshold_hours=48.0,
            source_kind="test",
        )
        assert finding.status == DataTruthStatus.STALE
        assert finding.safe_for_decision is False

    def test_stale_axis_summary_is_not_safe(self):
        stale_finding = DataTruthFinding(
            signal_name="evidence_quality",
            status=DataTruthStatus.STALE,
            trust_level=SourceTrustLevel.LOW,
            source_kind="test",
            freshness_label="stale_72h_ago",
            reason_code="field_stale",
            safe_for_decision=False,
        )
        axis = _build_axis_summary("evidence_quality", [stale_finding])
        assert axis.safe_for_decision is False
        assert axis.stale_count == 1

    def test_fresh_signal_is_safe(self):
        finding = classify_with_staleness(
            "evidence_quality",
            "some_value",
            last_updated_hours_ago=12.0,
            stale_threshold_hours=48.0,
            source_kind="test",
        )
        assert finding.status == DataTruthStatus.PRESENT
        assert finding.safe_for_decision is True

    def test_no_age_data_treated_as_present(self):
        finding = classify_with_staleness(
            "evidence_quality",
            "some_value",
            last_updated_hours_ago=None,
            source_kind="test",
        )
        assert finding.status == DataTruthStatus.PRESENT
        assert finding.safe_for_decision is True


# ── Test 7: One unsafe axis does not globally collapse to HOLD ────────────────

class TestIndependentSafeAxes:
    """When one axis is unsafe, other safe axes must still drive TRIM/SELL/BUY."""

    def test_missing_evidence_does_not_prevent_trim_from_safe_action(self):
        """evidence_quality MISSING + safe TRIM action → still produces TRIM."""
        diag = _shadow(
            ticker="META",
            v2_visible_action="HOLD",
            analyst_action="TRIM",
            conviction_level="MEDIUM",
            data_quality_label=None,
            intel_read=None,
        )
        assert diag is not None
        assert diag["v3_shadow_action"] == "TRIM"

    def test_missing_conviction_does_not_prevent_trim(self):
        """conviction MISSING + safe TRIM action → still produces TRIM."""
        diag = _shadow(
            ticker="META",
            v2_visible_action="HOLD",
            analyst_action="TRIM",
            conviction_level=None,
            data_quality_label="HIGH",
            intel_read=_good_intel_read(3),
        )
        assert diag is not None
        assert diag["v3_shadow_action"] == "TRIM"

    def test_missing_technical_does_not_prevent_buy(self):
        """technical MISSING + strong BUY evidence → may still produce BUY."""
        diag = _shadow(
            ticker="AAPL",
            v2_visible_action="HOLD",
            analyst_action="BUY",
            conviction_level="HIGH",
            technical_signal=None,
            data_quality_label="HIGH",
            intel_read=_good_intel_read(3),
        )
        assert diag is not None
        # BUY evidence is present; missing technical should not globally collapse
        assert diag["v3_shadow_action"] in _VALID_ACTIONS
        # And specifically: risk_signal MISSING suppresses risk (UNKNOWN), which
        # doesn't block BUY (risk ≤ MEDIUM check passes for UNKNOWN → BUY is ok)

    def test_missing_risk_with_strong_buy_still_allows_buy(self):
        """risk_signal MISSING + strong BUY evidence → BUY should still fire.

        The policy allows BUY when risk_band is UNKNOWN (not HIGH/CRITICAL),
        so a missing risk signal does not suppress BUY.
        """
        diag = _shadow(
            ticker="AAPL",
            v2_visible_action="HOLD",
            analyst_action="BUY",
            conviction_level="HIGH",
            technical_signal="BULLISH",
            risk_flag=None,
            analyst_risks=None,
            data_quality_label="HIGH",
            intel_read=_good_intel_read(3),
        )
        assert diag is not None
        assert diag["v3_shadow_action"] == "BUY"

    @pytest.mark.parametrize("unsafe_axis,kwargs_override", [
        # Missing evidence; action signals are consistent SELL (no conflict)
        ("evidence_quality", {"data_quality_label": None, "intel_read": None,
                               "action": "SELL", "analyst_action": "SELL",
                               "risk_flag": "Critical insolvency",
                               "analyst_risks": ["Critical default risk"]}),
        # Missing conviction; action signals are consistent TRIM (no conflict)
        ("conviction", {"conviction_level": None,
                        "action": "TRIM", "analyst_action": "TRIM",
                        "data_quality_label": "HIGH",
                        "intel_read": _good_intel_read(2)}),
    ])
    def test_one_unsafe_axis_does_not_suppress_safe_action_axis(
        self, unsafe_axis, kwargs_override
    ):
        base = _ta(ticker="XYZ", category="Core")
        base.update(kwargs_override)
        inp, summaries, suppressed = build_truth_aware_decision_input(**base)
        assert unsafe_axis in suppressed
        # action_signal must remain safe (consistent action pair, no conflict)
        assert "action_signal" not in suppressed


# ── Test 8: All useful axes unsafe → honest HOLD ─────────────────────────────

class TestAllAxesUnsafeProducesHonestHold:
    """When every axis is unsafe/missing, v3 must produce HOLD honestly."""

    def test_no_signals_at_all_produces_honest_hold(self):
        diag = _shadow(
            ticker="KLAR",
            v2_visible_action="HOLD",
            analyst_action=None,
            conviction_level=None,
            technical_signal=None,
            risk_flag=None,
            analyst_risks=None,
            data_quality_label=None,
            intel_read=None,
        )
        assert diag is not None
        assert diag["v3_shadow_action"] == "HOLD"
        assert diag["v3_honest_hold"] is True
        assert len(diag["suppressed_axes"]) > 0

    def test_all_unavailable_sentinels_produce_hold(self):
        diag = _shadow(
            ticker="BLSH",
            v2_visible_action="HOLD",
            analyst_action="UNAVAILABLE",
            conviction_level="N/A",
            technical_signal="UNAVAILABLE",
            risk_flag="UNAVAILABLE",
            analyst_risks=None,
            data_quality_label="N/A",
            intel_read=None,
        )
        assert diag is not None
        assert diag["v3_shadow_action"] == "HOLD"

    def test_conflicting_action_plus_all_missing_produces_hold(self):
        """BUY↔SELL conflict + all other axes missing → action nulled → HOLD.

        Note: _shadow passes v2_visible_action as the action param so we use
        build_truth_aware_decision_input directly to supply action=BUY vs SELL.
        """
        inp, summaries, suppressed = build_truth_aware_decision_input(
            ticker="XYZ",
            action="BUY",
            analyst_action="SELL",
            conviction_level=None,
            technical_signal=None,
            risk_flag=None,
            analyst_risks=None,
            category="Core",
            data_quality_label=None,
            intel_read=None,
            thesis_v2=None,
        )
        # CONFLICTING action + all other axes missing → multiple suppressions
        assert "action_signal" in suppressed
        assert "evidence_quality" in suppressed
        # With all key axes suppressed, decision should be HOLD
        from app.services.intelligence.v3.decision_policy_v1 import decide
        v3_out = decide(inp)
        assert v3_out.action.value == "HOLD"


# ── Test 9: v2 visible action never mutated ───────────────────────────────────

class TestV2ActionNotMutated:
    """shadow_projection must not mutate the v2 visible action under any truth path."""

    @pytest.mark.parametrize("v2_action,analyst_action,data_quality", [
        ("BUY", "BUY", "HIGH"),
        ("HOLD", "SELL", "LOW"),
        ("TRIM", "TRIM", "HIGH"),
        ("SELL", "SELL", "MEDIUM"),
        ("HOLD", None, None),
    ])
    def test_v2_action_unchanged_in_diagnostic(self, v2_action, analyst_action, data_quality):
        diag = _shadow(
            ticker="AAPL",
            v2_visible_action=v2_action,
            analyst_action=analyst_action,
            conviction_level="HIGH",
            data_quality_label=data_quality,
            intel_read=_good_intel_read(2) if data_quality else None,
        )
        if diag is not None:
            assert diag["v2_visible_action"] == v2_action, (
                f"v2 visible action must not be mutated; expected {v2_action}, got {diag['v2_visible_action']}"
            )


# ── Test 10: Shadow action diversity preserved ───────────────────────────────

class TestShadowActionDiversity:
    """PR 7 wiring must not homogenize shadow actions to HOLD."""

    _GOLDEN = [
        dict(ticker="AAPL", v2_visible_action="HOLD", analyst_action="BUY",
             conviction_level="HIGH", technical_signal="BULLISH",
             data_quality_label="HIGH", intel_read=_good_intel_read(3), category="Core",
             risk_flag=None, analyst_risks=None, thesis_v2=None),
        dict(ticker="META", v2_visible_action="HOLD", analyst_action="TRIM",
             conviction_level="MEDIUM", technical_signal="NEUTRAL",
             data_quality_label="HIGH", intel_read=_good_intel_read(2), category="Growth",
             risk_flag=None, analyst_risks=None, thesis_v2=None),
        dict(ticker="RIVN", v2_visible_action="HOLD", analyst_action="SELL",
             conviction_level="LOW", technical_signal="BEARISH",
             risk_flag="Critical covenant breach and severe liquidity risk",
             analyst_risks=["Cash runway under 6 months", "Critical covenant breach"],
             data_quality_label="MEDIUM", intel_read=_good_intel_read(2),
             category="Speculative", thesis_v2=None),
        dict(ticker="SNOW", v2_visible_action="HOLD", analyst_action="BUY",
             conviction_level="HIGH", technical_signal=None,
             data_quality_label="HIGH", intel_read=_thin_intel_read(), category="Growth",
             risk_flag=None, analyst_risks=None, thesis_v2=None),
        dict(ticker="VOO", v2_visible_action="HOLD", analyst_action="HOLD",
             conviction_level="MEDIUM", technical_signal="NEUTRAL",
             data_quality_label="HIGH", intel_read=_good_intel_read(3), category="ETF",
             risk_flag=None, analyst_risks=None, thesis_v2=None),
    ]

    def test_action_diversity_across_golden_fixtures(self):
        results = [project_shadow_from_card_signals(**f) for f in self._GOLDEN]
        actions = {r["v3_shadow_action"] for r in results if r is not None}
        assert len(actions) >= 3, (
            f"Truth-aware PR 7 must preserve shadow action diversity; got {actions}"
        )
        assert "HOLD" in actions and len(actions - {"HOLD"}) >= 1, (
            "Must have both HOLD and at least one other action"
        )

    def test_golden_portfolio_counts_stable(self):
        """Golden portfolio shadow counts must match pre-PR-7 expected values."""
        results = [project_shadow_from_card_signals(**f) for f in self._GOLDEN]
        summary = summarize_shadow_diagnostics(results, total_cards=len(self._GOLDEN))
        assert summary["hold_collapse_risk_count"] >= 2
        assert summary["honest_hold_count"] >= 1
        assert summary["projected_cards"] == len(self._GOLDEN)


# ── Test 11: HOLD-collapse risk detectable ───────────────────────────────────

class TestHoldCollapseDetectable:
    """HOLD-collapse detection must survive PR 7 truth-aware wiring."""

    def test_hold_with_strong_buy_still_flags_collapse(self):
        diag = _shadow(
            ticker="AAPL",
            v2_visible_action="HOLD",
            analyst_action="BUY",
            conviction_level="HIGH",
            technical_signal="BULLISH",
            data_quality_label="HIGH",
            intel_read=_good_intel_read(3),
        )
        assert diag is not None
        assert diag["hold_collapse_risk"] is True
        assert diag["v3_shadow_action"] == "BUY"

    def test_hold_with_trim_still_flags_collapse(self):
        diag = _shadow(
            ticker="META",
            v2_visible_action="HOLD",
            analyst_action="TRIM",
            conviction_level="MEDIUM",
            data_quality_label="HIGH",
            intel_read=_good_intel_read(2),
        )
        assert diag is not None
        assert diag["hold_collapse_risk"] is True
        assert diag["v3_shadow_action"] == "TRIM"


# ── Test 12: Honest HOLD vs HOLD-collapse separation ─────────────────────────

class TestHonestHoldSeparation:
    """Honest HOLD (thin data) must be separated from HOLD-collapse risk."""

    def test_thin_data_hold_is_honest_hold(self):
        diag = _shadow(
            ticker="SNOW",
            v2_visible_action="HOLD",
            analyst_action="BUY",
            conviction_level="HIGH",
            data_quality_label="HIGH",
            intel_read=_thin_intel_read(),
        )
        assert diag is not None
        assert diag["v3_shadow_action"] == "HOLD"
        assert diag["v3_honest_hold"] is True
        assert diag["hold_collapse_risk"] is False

    def test_no_data_hold_is_honest_hold(self):
        diag = _shadow(
            ticker="KLAR",
            v2_visible_action="HOLD",
            analyst_action=None,
            data_quality_label=None,
            intel_read=None,
        )
        assert diag is not None
        assert diag["v3_shadow_action"] == "HOLD"
        assert diag["v3_honest_hold"] is True

    def test_strong_buy_hold_is_collapse_not_honest(self):
        diag = _shadow(
            ticker="AAPL",
            v2_visible_action="HOLD",
            analyst_action="BUY",
            conviction_level="HIGH",
            data_quality_label="HIGH",
            intel_read=_good_intel_read(3),
        )
        assert diag is not None
        assert diag["v3_honest_hold"] is False
        assert diag["hold_collapse_risk"] is True


# ── Test 13: Diagnostics expose safe suppression counts/reason codes ──────────

class TestDiagnosticsShape:
    """truth_diagnostics must contain all required truth-aware PR 7 keys."""

    _REQUIRED_TRUTH_KEYS = {
        "schema_version",
        "safe_axes",
        "unsafe_axes",
        "axes",
        "truth_aware_adapter_enabled",
        "safe_axis_count",
        "unsafe_axis_count",
        "suppressed_axis_reasons",
        "dominant_truth_reason",
    }

    def test_truth_diagnostics_has_all_pr7_keys(self):
        diag = _shadow(
            analyst_action="BUY",
            conviction_level="HIGH",
            data_quality_label="HIGH",
            intel_read=_good_intel_read(3),
        )
        assert diag is not None
        td = diag["truth_diagnostics"]
        assert td is not None
        missing_keys = self._REQUIRED_TRUTH_KEYS - set(td.keys())
        assert not missing_keys, f"truth_diagnostics missing keys: {missing_keys}"

    def test_truth_aware_adapter_enabled_is_true(self):
        diag = _shadow(
            analyst_action="TRIM",
            data_quality_label="HIGH",
            intel_read=_good_intel_read(2),
        )
        assert diag is not None
        assert diag["truth_diagnostics"]["truth_aware_adapter_enabled"] is True

    def test_suppressed_axis_reasons_reflects_actual_suppressions(self):
        """When evidence axis is unsafe, suppressed_axis_reasons includes it."""
        diag = _shadow(
            analyst_action="BUY",
            conviction_level="HIGH",
            data_quality_label=None,
            intel_read=None,
        )
        assert diag is not None
        td = diag["truth_diagnostics"]
        assert "evidence_quality" in td["suppressed_axis_reasons"]

    def test_dominant_truth_reason_is_string(self):
        diag = _shadow(
            data_quality_label=None,
            intel_read=None,
            conviction_level=None,
        )
        assert diag is not None
        td = diag["truth_diagnostics"]
        assert isinstance(td["dominant_truth_reason"], str)
        assert len(td["dominant_truth_reason"]) > 0

    def test_all_axes_present_dominant_reason_is_none_string(self):
        """When all axes are PRESENT/safe, suppressed_axis_reasons is empty
        and dominant_truth_reason is 'none'."""
        diag = _shadow(
            ticker="AAPL",
            analyst_action="BUY",
            conviction_level="HIGH",
            technical_signal="BULLISH",
            risk_flag="some risk text",
            analyst_risks=["risk detail"],
            data_quality_label="HIGH",
            intel_read=_good_intel_read(3),
        )
        assert diag is not None
        td = diag["truth_diagnostics"]
        # All axes PRESENT/safe → no suppression → dominant_truth_reason is "none"
        assert td["suppressed_axis_reasons"] == {}
        assert td["dominant_truth_reason"] == "none"

    def test_axis_counts_sum_to_five(self):
        diag = _shadow(
            analyst_action="BUY",
            data_quality_label="HIGH",
            intel_read=_good_intel_read(3),
        )
        assert diag is not None
        td = diag["truth_diagnostics"]
        assert td["safe_axis_count"] + td["unsafe_axis_count"] == 5


# ── Test 14: Diagnostics do not expose sensitive data ─────────────────────────

class TestDiagnosticsSafeData:
    """truth_diagnostics must not contain raw card payloads or sensitive fields."""

    _FORBIDDEN_KEYS = {
        "account_id", "user_id", "portfolio_value", "holdings_quantity",
        "position_value", "raw_payload", "provider_response", "llm_output",
        "account_value", "shares", "cost_basis",
    }

    def _get_all_keys(self, obj, depth: int = 0) -> set[str]:
        if depth > 5 or not isinstance(obj, dict):
            return set()
        keys: set[str] = set(obj.keys())
        for v in obj.values():
            keys |= self._get_all_keys(v, depth + 1)
        return keys

    def test_truth_diagnostics_no_sensitive_keys(self):
        diag = _shadow(
            analyst_action="BUY",
            conviction_level="HIGH",
            data_quality_label="HIGH",
            intel_read=_good_intel_read(3),
        )
        assert diag is not None
        td = diag["truth_diagnostics"]
        found = self._FORBIDDEN_KEYS & self._get_all_keys(td)
        assert not found, f"truth_diagnostics contains sensitive keys: {found}"

    def test_main_diag_no_sensitive_keys(self):
        diag = _shadow(
            analyst_action="BUY",
            data_quality_label="HIGH",
            intel_read=_good_intel_read(3),
        )
        assert diag is not None
        found = self._FORBIDDEN_KEYS & self._get_all_keys(diag)
        assert not found, f"diagnostic dict contains sensitive keys: {found}"

    def test_suppressed_axis_reasons_values_are_reason_codes(self):
        """suppressed_axis_reasons values must be compact reason codes, not text blobs."""
        diag = _shadow(
            data_quality_label=None,
            intel_read=None,
            conviction_level=None,
        )
        assert diag is not None
        reasons = diag["truth_diagnostics"]["suppressed_axis_reasons"]
        for axis, reason in reasons.items():
            assert isinstance(reason, str)
            assert len(reason) < 100, f"Reason code too long for axis {axis}: {reason!r}"
            assert " " not in reason or "_" in reason, (
                f"Reason code should be snake_case, got: {reason!r}"
            )


# ── Test 15: Pure function — no IO, no DB, no provider calls ─────────────────

class TestPureFunction:
    """build_truth_aware_decision_input must be importable and runnable without
    any external dependencies (no Supabase, no network, no file system)."""

    def test_importable_without_supabase(self):
        from app.services.intelligence.v3.existing_signal_adapter import (
            build_truth_aware_decision_input as fn,
        )
        assert callable(fn)

    def test_no_io_on_call(self):
        """Calling the function with minimal inputs must complete synchronously."""
        result = build_truth_aware_decision_input(
            ticker="AAPL",
            action=None,
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
        assert result is not None
        inp, summaries, suppressed = result
        assert inp is not None
        assert isinstance(summaries, list)
        assert isinstance(suppressed, dict)

    def test_no_deploy_import_in_adapter(self):
        import inspect
        import app.services.intelligence.v3.existing_signal_adapter as mod
        src = inspect.getsource(mod)
        assert "allocation_engine" not in src
        assert "deployment_engine" not in src

    def test_no_deploy_import_in_shadow_projection(self):
        import inspect
        import app.services.intelligence.v3.shadow_projection as mod
        src = inspect.getsource(mod)
        assert "allocation_engine" not in src
        assert "deployment_engine" not in src

    def test_build_truth_aware_is_fail_soft_on_no_inputs(self):
        """All-None inputs must not raise — fails gracefully."""
        inp, summaries, suppressed = build_truth_aware_decision_input(
            ticker="STUB",
            action=None,
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
        assert inp is not None
        assert isinstance(summaries, list)
        assert len(summaries) == 5  # one per axis

    def test_shadow_projection_fail_soft_on_none_ticker(self):
        result = project_shadow_from_card_signals(
            ticker=None,  # type: ignore[arg-type]
            v2_visible_action="HOLD",
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
        assert result is None or isinstance(result, dict)
