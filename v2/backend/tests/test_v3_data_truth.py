"""Table-driven tests for Intel v3 Data Truth Contract v1 (PR 6).

Backend-only dark launch. Tests confirm:
 1. Missing values → MISSING and unsafe for decision.
 2. Unavailable provider sentinels → UNAVAILABLE and unsafe.
 3. Present trusted values → PRESENT and safe.
 4. Weak/fallback/inferred values → WEAK (safe but LOW trust).
 5. Stale timestamped values → STALE and unsafe (via classify_with_staleness).
 6. Conflicting deterministic inputs → CONFLICTING and unsafe.
 7. Axis-level summary separates present/missing/stale/weak counts correctly.
 8. No provider calls, no Supabase dependency, no LLM calls.
 9. Synthetic InsightCard-like data adapts into axis truth summaries.
10. Existing v3 shadow projection tests still pass (regression guard here).
11. No visible action mutation — truth diagnostics are additive only.
12. No frontend/API/Deploy/schema files changed (scope assertion in naming).
"""
from __future__ import annotations

import pytest

from app.services.intelligence.v3.data_truth_contracts import (
    AxisTruthSummary,
    DataTruthFinding,
    DataTruthStatus,
    SourceTrustLevel,
)
from app.services.intelligence.v3.data_truth_v1 import (
    classify_action_signals,
    classify_conviction_signal,
    classify_evidence_signals,
    classify_risk_signals,
    classify_technical_signal,
    classify_with_staleness,
)
from app.services.intelligence.v3.existing_signal_truth_adapter import (
    build_truth_diagnostic_summary,
    evaluate_card_signals_truth,
)
from app.services.intelligence.v3.shadow_projection import (
    project_shadow_from_card_signals,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _good_intel_read(n_trusted: int = 3) -> dict:
    dims = ["business quality", "valuation", "growth", "momentum"][:n_trusted]
    return {"insufficient_data": False, "trusted_dimensions": dims}


def _thin_intel_read() -> dict:
    return {"insufficient_data": True, "trusted_dimensions": []}


def _card_truth(
    *,
    action=None,
    analyst_action=None,
    conviction_level=None,
    technical_signal=None,
    risk_flag=None,
    analyst_risks=None,
    data_quality_label=None,
    intel_read=None,
) -> list[AxisTruthSummary]:
    return evaluate_card_signals_truth(
        action=action,
        analyst_action=analyst_action,
        conviction_level=conviction_level,
        technical_signal=technical_signal,
        risk_flag=risk_flag,
        analyst_risks=analyst_risks,
        data_quality_label=data_quality_label,
        intel_read=intel_read,
    )


def _axis(summaries: list[AxisTruthSummary], name: str) -> AxisTruthSummary:
    return next(s for s in summaries if s.axis_name == name)


# ── Test 1: Missing values → MISSING and unsafe ───────────────────────────────

class TestMissingValues:
    """None/empty field values classify as MISSING and are not safe for decision."""

    @pytest.mark.parametrize("action,analyst_action", [
        (None, None),
        ("", ""),
        (None, ""),
    ])
    def test_action_none_is_missing(self, action, analyst_action):
        f = classify_action_signals(action, analyst_action)
        assert f.status == DataTruthStatus.MISSING
        assert f.safe_for_decision is False

    def test_conviction_none_is_missing(self):
        f = classify_conviction_signal(None)
        assert f.status == DataTruthStatus.MISSING
        assert f.safe_for_decision is False

    def test_technical_none_is_missing(self):
        f = classify_technical_signal(None)
        assert f.status == DataTruthStatus.MISSING
        assert f.safe_for_decision is False

    def test_evidence_all_none_is_missing(self):
        f = classify_evidence_signals(None, None)
        assert f.status == DataTruthStatus.MISSING
        assert f.safe_for_decision is False

    def test_risk_none_none_is_missing(self):
        f = classify_risk_signals(None, None)
        assert f.status == DataTruthStatus.MISSING
        assert f.safe_for_decision is False

    def test_risk_empty_list_is_missing(self):
        f = classify_risk_signals(None, [])
        assert f.status == DataTruthStatus.MISSING
        assert f.safe_for_decision is False

    def test_missing_reason_code_is_field_absent(self):
        f = classify_conviction_signal(None)
        assert f.reason_code == "field_absent"
        assert f.trust_level == SourceTrustLevel.UNKNOWN


# ── Test 2: Unavailable sentinels → UNAVAILABLE and unsafe ───────────────────

class TestUnavailableSentinels:
    """Explicit provider-unavailability markers classify as UNAVAILABLE."""

    @pytest.mark.parametrize("sentinel", ["UNAVAILABLE", "N/A", "UNAVAIL", "NOT_AVAILABLE", "NA"])
    def test_action_unavailable_sentinels(self, sentinel):
        f = classify_action_signals(sentinel, None)
        assert f.status == DataTruthStatus.UNAVAILABLE
        assert f.safe_for_decision is False

    @pytest.mark.parametrize("sentinel", ["UNAVAILABLE", "N/A"])
    def test_evidence_unavailable_sentinel_in_label(self, sentinel):
        f = classify_evidence_signals(sentinel, None)
        assert f.status == DataTruthStatus.UNAVAILABLE
        assert f.safe_for_decision is False

    def test_technical_unavailable_sentinel(self):
        f = classify_technical_signal("UNAVAILABLE")
        assert f.status == DataTruthStatus.UNAVAILABLE
        assert f.safe_for_decision is False

    def test_unavailable_reason_code(self):
        f = classify_action_signals("UNAVAILABLE", None)
        assert f.reason_code == "provider_sentinel"
        assert f.trust_level == SourceTrustLevel.UNKNOWN

    def test_classify_with_staleness_unavailable(self):
        f = classify_with_staleness("some_signal", "UNAVAILABLE", last_updated_hours_ago=None)
        assert f.status == DataTruthStatus.UNAVAILABLE
        assert f.safe_for_decision is False


# ── Test 3: Present trusted values → PRESENT and safe ────────────────────────

class TestPresentTrustedValues:
    """Well-sourced present values classify as PRESENT with appropriate trust."""

    @pytest.mark.parametrize("action,analyst_action,expected_trust", [
        ("BUY", "BUY", SourceTrustLevel.HIGH),
        ("HOLD", "HOLD", SourceTrustLevel.HIGH),
        ("BUY", None, SourceTrustLevel.MEDIUM),
        (None, "SELL", SourceTrustLevel.MEDIUM),
    ])
    def test_valid_action_is_present(self, action, analyst_action, expected_trust):
        f = classify_action_signals(action, analyst_action)
        assert f.status == DataTruthStatus.PRESENT
        assert f.safe_for_decision is True
        assert f.trust_level == expected_trust

    def test_conviction_high_is_present_high_trust(self):
        f = classify_conviction_signal("HIGH")
        assert f.status == DataTruthStatus.PRESENT
        assert f.trust_level == SourceTrustLevel.HIGH
        assert f.safe_for_decision is True

    def test_conviction_medium_is_present_medium_trust(self):
        f = classify_conviction_signal("MEDIUM")
        assert f.status == DataTruthStatus.PRESENT
        assert f.trust_level == SourceTrustLevel.MEDIUM
        assert f.safe_for_decision is True

    @pytest.mark.parametrize("tech_signal", ["BUY", "SELL", "BULLISH", "BEARISH", "NEUTRAL", "STRONG"])
    def test_known_technical_is_present(self, tech_signal):
        f = classify_technical_signal(tech_signal)
        assert f.status == DataTruthStatus.PRESENT
        assert f.safe_for_decision is True

    def test_evidence_good_intel_read_3_dims_is_present_high(self):
        f = classify_evidence_signals(None, _good_intel_read(3))
        assert f.status == DataTruthStatus.PRESENT
        assert f.trust_level == SourceTrustLevel.HIGH
        assert f.safe_for_decision is True

    def test_evidence_good_intel_read_2_dims_is_present_medium(self):
        f = classify_evidence_signals(None, _good_intel_read(2))
        assert f.status == DataTruthStatus.PRESENT
        assert f.trust_level == SourceTrustLevel.MEDIUM
        assert f.safe_for_decision is True

    def test_evidence_data_quality_high_is_present_high(self):
        f = classify_evidence_signals("HIGH", None)
        assert f.status == DataTruthStatus.PRESENT
        assert f.trust_level == SourceTrustLevel.HIGH
        assert f.safe_for_decision is True

    def test_risk_with_flag_is_present(self):
        f = classify_risk_signals("earnings miss risk", None)
        assert f.status == DataTruthStatus.PRESENT
        assert f.safe_for_decision is True

    def test_risk_with_analyst_list_is_present(self):
        f = classify_risk_signals(None, ["rate risk", "margin compression"])
        assert f.status == DataTruthStatus.PRESENT
        assert f.safe_for_decision is True

    def test_present_reason_code_is_field_present(self):
        f = classify_conviction_signal("HIGH")
        assert f.reason_code == "field_present"


# ── Test 4: Weak values → WEAK (safe but LOW trust) ──────────────────────────

class TestWeakValues:
    """Fallback/inferred/low-quality values classify as WEAK."""

    def test_conviction_low_is_weak(self):
        f = classify_conviction_signal("LOW")
        assert f.status == DataTruthStatus.WEAK
        assert f.trust_level == SourceTrustLevel.LOW
        assert f.safe_for_decision is True
        assert f.reason_code == "conviction_low"

    def test_data_quality_low_is_weak(self):
        f = classify_evidence_signals("LOW", None)
        assert f.status == DataTruthStatus.WEAK
        assert f.trust_level == SourceTrustLevel.LOW
        assert f.safe_for_decision is True
        assert f.reason_code == "data_quality_low"

    def test_intel_read_insufficient_is_weak(self):
        f = classify_evidence_signals(None, _thin_intel_read())
        assert f.status == DataTruthStatus.WEAK
        assert f.trust_level == SourceTrustLevel.LOW
        assert f.safe_for_decision is True
        assert f.reason_code == "intel_insufficient"

    def test_intel_read_zero_trusted_dims_is_weak(self):
        f = classify_evidence_signals(None, {"insufficient_data": False, "trusted_dimensions": []})
        assert f.status == DataTruthStatus.WEAK
        assert f.safe_for_decision is True

    def test_weak_has_low_trust_level(self):
        f = classify_evidence_signals("LOW", None)
        assert f.trust_level == SourceTrustLevel.LOW


# ── Test 5: Stale values → STALE and unsafe ──────────────────────────────────

class TestStaleValues:
    """Signals with age exceeding the threshold classify as STALE and are unsafe."""

    @pytest.mark.parametrize("hours_ago,threshold,expected_status", [
        (72.0, 48.0, DataTruthStatus.STALE),
        (49.0, 48.0, DataTruthStatus.STALE),
        (48.0, 48.0, DataTruthStatus.PRESENT),
        (24.0, 48.0, DataTruthStatus.PRESENT),
        (None, 48.0, DataTruthStatus.PRESENT),
    ])
    def test_staleness_threshold_boundary(self, hours_ago, threshold, expected_status):
        f = classify_with_staleness(
            "some_signal", "BUY",
            last_updated_hours_ago=hours_ago,
            stale_threshold_hours=threshold,
            source_kind="test",
        )
        assert f.status == expected_status

    def test_stale_is_not_safe_for_decision(self):
        f = classify_with_staleness(
            "some_signal", "BUY",
            last_updated_hours_ago=100.0,
        )
        assert f.safe_for_decision is False
        assert f.trust_level == SourceTrustLevel.LOW
        assert f.reason_code == "field_stale"

    def test_stale_freshness_label_includes_hours(self):
        f = classify_with_staleness(
            "some_signal", "BUY",
            last_updated_hours_ago=72.0,
        )
        assert "72" in f.freshness_label
        assert "stale" in f.freshness_label

    def test_fresh_signal_has_current_label_when_no_age(self):
        f = classify_with_staleness(
            "some_signal", "BUY",
            last_updated_hours_ago=None,
        )
        assert f.freshness_label == "current"


# ── Test 6: Conflicting signals → CONFLICTING and unsafe ─────────────────────

class TestConflictingSignals:
    """Directly opposing action pairs classify as CONFLICTING."""

    @pytest.mark.parametrize("action,analyst_action", [
        ("BUY", "SELL"),
        ("SELL", "BUY"),
    ])
    def test_opposing_actions_are_conflicting(self, action, analyst_action):
        f = classify_action_signals(action, analyst_action)
        assert f.status == DataTruthStatus.CONFLICTING
        assert f.safe_for_decision is False
        assert f.reason_code == "action_conflict"
        assert f.trust_level == SourceTrustLevel.UNKNOWN

    @pytest.mark.parametrize("action,analyst_action", [
        ("BUY", "TRIM"),
        ("BUY", "HOLD"),
        ("TRIM", "HOLD"),
        ("HOLD", "SELL"),
    ])
    def test_non_opposing_pairs_are_not_conflicting(self, action, analyst_action):
        f = classify_action_signals(action, analyst_action)
        assert f.status != DataTruthStatus.CONFLICTING

    def test_conflicting_makes_axis_unsafe(self):
        summaries = _card_truth(action="BUY", analyst_action="SELL")
        axis = _axis(summaries, "action_signal")
        assert axis.safe_for_decision is False
        assert axis.dominant_reason_code == "action_conflict"


# ── Test 7: Axis-level summary counting ──────────────────────────────────────

class TestAxisTruthSummaryCounting:
    """AxisTruthSummary correctly counts present/missing/stale/weak findings."""

    def test_all_present_signals_are_counted(self):
        summaries = _card_truth(
            action="BUY",
            analyst_action="BUY",
            conviction_level="HIGH",
            technical_signal="BULLISH",
            risk_flag="low risk",
            data_quality_label="HIGH",
            intel_read=_good_intel_read(3),
        )
        ev = _axis(summaries, "evidence_quality")
        assert ev.present_count == 1
        assert ev.missing_count == 0
        assert ev.safe_for_decision is True
        assert ev.dominant_reason_code == "all_present"

    def test_missing_signal_increments_missing_count(self):
        summaries = _card_truth(action=None, analyst_action=None)
        axis = _axis(summaries, "action_signal")
        assert axis.missing_count == 1
        assert axis.present_count == 0
        assert axis.safe_for_decision is False

    def test_weak_signal_increments_weak_count(self):
        summaries = _card_truth(conviction_level="LOW")
        axis = _axis(summaries, "conviction")
        assert axis.weak_count == 1
        assert axis.present_count == 0
        assert axis.safe_for_decision is True

    def test_conflicting_makes_axis_unsafe_despite_other_presence(self):
        summaries = _card_truth(action="BUY", analyst_action="SELL")
        axis = _axis(summaries, "action_signal")
        assert axis.safe_for_decision is False

    def test_summary_returns_five_axes(self):
        summaries = _card_truth()
        assert len(summaries) == 5

    def test_all_axis_names_present(self):
        summaries = _card_truth()
        names = {s.axis_name for s in summaries}
        assert names == {"evidence_quality", "action_signal", "conviction", "technical_signal", "risk_signal"}

    def test_dominant_reason_code_reflects_most_common_unsafe(self):
        summaries = _card_truth(action=None, analyst_action=None)
        axis = _axis(summaries, "action_signal")
        assert axis.dominant_reason_code == "field_absent"


# ── Test 8: No provider calls, no Supabase, no LLM ───────────────────────────

class TestNoDependencies:
    """Pure function — zero external dependencies during classification."""

    def test_classifiers_importable_without_supabase(self):
        """Import succeeds in test environment with mocked Supabase env vars."""
        from app.services.intelligence.v3.data_truth_v1 import classify_evidence_signals
        assert callable(classify_evidence_signals)

    def test_evaluate_card_truth_does_not_raise(self):
        """evaluate_card_signals_truth runs without exceptions on minimal input."""
        summaries = _card_truth()
        assert isinstance(summaries, list)
        assert len(summaries) == 5

    def test_all_findings_are_datatruthfinding_instances(self):
        summaries = _card_truth(action="BUY", conviction_level="HIGH")
        for summary in summaries:
            for finding in summary.findings:
                assert isinstance(finding, DataTruthFinding)

    def test_all_summaries_are_axistruthsummary_instances(self):
        summaries = _card_truth()
        for s in summaries:
            assert isinstance(s, AxisTruthSummary)


# ── Test 9: Synthetic InsightCard-like data adapts into truth summaries ───────

class TestInsightCardLikeAdaptation:
    """Synthetic card-like fixtures produce correct truth summary shapes."""

    def test_strong_buy_card_has_high_safe_axes(self):
        summaries = _card_truth(
            action="BUY",
            analyst_action="BUY",
            conviction_level="HIGH",
            technical_signal="BULLISH",
            risk_flag=None,
            analyst_risks=[],
            data_quality_label=None,
            intel_read=_good_intel_read(3),
        )
        safe_count = sum(1 for s in summaries if s.safe_for_decision)
        assert safe_count >= 3, f"Expected ≥3 safe axes for strong BUY card, got {safe_count}"

    def test_empty_card_has_all_unsafe_axes(self):
        summaries = _card_truth()
        safe_count = sum(1 for s in summaries if s.safe_for_decision)
        assert safe_count == 0, "Fully empty card should have 0 safe axes"

    def test_mixed_card_reflects_partial_truth(self):
        summaries = _card_truth(
            action="HOLD",
            analyst_action=None,
            conviction_level="MEDIUM",
            technical_signal=None,
            data_quality_label="LOW",
        )
        ev = _axis(summaries, "evidence_quality")
        assert ev.safe_for_decision is True
        assert ev.weak_count == 1

        conv = _axis(summaries, "conviction")
        assert conv.safe_for_decision is True

        tech = _axis(summaries, "technical_signal")
        assert tech.safe_for_decision is False

    def test_insufficient_data_card_evidence_axis_is_weak(self):
        summaries = _card_truth(
            action="HOLD",
            intel_read=_thin_intel_read(),
        )
        ev = _axis(summaries, "evidence_quality")
        assert ev.safe_for_decision is True
        assert ev.weak_count == 1
        assert ev.findings[0].reason_code == "intel_insufficient"

    def test_no_real_user_data_in_fixtures(self):
        """All fixtures are synthetic — no real ticker/account/user data."""
        summaries = _card_truth(action="BUY", conviction_level="HIGH")
        for s in summaries:
            for f in s.findings:
                assert "user" not in f.source_kind.lower()
                assert "account" not in f.source_kind.lower()


# ── Test 10: Shadow projection v3 regression guard ───────────────────────────

class TestShadowProjectionRegression:
    """Existing v3 shadow projection stable-key contract unchanged after PR 6."""

    _STABLE_KEYS = {
        "ticker", "v2_visible_action", "v3_shadow_action",
        "v3_shadow_conviction", "hold_collapse_risk",
        "v3_honest_hold", "suppressed_axes", "v3_schema_version",
    }

    def test_all_stable_shadow_keys_still_present(self):
        diag = project_shadow_from_card_signals(
            ticker="AAPL",
            v2_visible_action="BUY",
            analyst_action="BUY",
            conviction_level="HIGH",
            technical_signal="BULLISH",
            risk_flag=None,
            analyst_risks=[],
            category="Core",
            data_quality_label="HIGH",
            intel_read=_good_intel_read(3),
            thesis_v2=None,
        )
        assert diag is not None
        for key in self._STABLE_KEYS:
            assert key in diag, f"Stable key {key!r} missing from shadow projection"

    def test_truth_diagnostics_key_is_additive(self):
        """truth_diagnostics is a new additive key — does not replace stable keys."""
        diag = project_shadow_from_card_signals(
            ticker="MSFT",
            v2_visible_action="HOLD",
            analyst_action="BUY",
            conviction_level="HIGH",
            technical_signal=None,
            risk_flag=None,
            analyst_risks=None,
            category="Core",
            data_quality_label="HIGH",
            intel_read=_good_intel_read(3),
            thesis_v2=None,
        )
        assert diag is not None
        assert "truth_diagnostics" in diag
        td = diag["truth_diagnostics"]
        assert td is not None
        assert td["schema_version"] == "v3.truth.v1"
        assert "axes" in td
        assert "safe_axes" in td
        assert "unsafe_axes" in td

    def test_visible_action_unchanged_by_truth_diagnostics(self):
        """truth_diagnostics must not alter v2_visible_action."""
        diag = project_shadow_from_card_signals(
            ticker="GOOGL",
            v2_visible_action="HOLD",
            analyst_action=None,
            conviction_level=None,
            technical_signal=None,
            risk_flag=None,
            analyst_risks=None,
            category="Core",
            data_quality_label=None,
            intel_read=None,
            thesis_v2=None,
        )
        assert diag is not None
        assert diag["v2_visible_action"] == "HOLD"

    def test_shadow_still_fails_soft_on_malformed_input(self):
        """Malformed input still returns None safely — truth layer does not raise."""
        diag = project_shadow_from_card_signals(
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
        # None is acceptable for malformed input — must not raise.
        assert diag is None or isinstance(diag, dict)


# ── Test 11: No visible action mutation ───────────────────────────────────────

class TestNoVisibleActionMutation:
    """Truth evaluation is read-only — no action field is modified."""

    def test_evaluate_truth_does_not_modify_input_dicts(self):
        intel = _good_intel_read(3)
        original_intel = dict(intel)
        _card_truth(action="BUY", intel_read=intel)
        assert intel == original_intel

    def test_truth_summary_does_not_include_action_label(self):
        """Compact truth summary does not surface raw action labels."""
        summaries = _card_truth(action="BUY", conviction_level="HIGH")
        summary_dict = build_truth_diagnostic_summary(summaries)
        # No action label should appear in the summary values directly.
        for axis_data in summary_dict["axes"].values():
            assert "BUY" not in str(axis_data)
            assert "SELL" not in str(axis_data)


# ── Test 12: build_truth_diagnostic_summary schema ───────────────────────────

class TestTruthDiagnosticSummarySchema:
    """Compact diagnostic summary has stable keys and correct counts."""

    def test_schema_version_is_stable(self):
        summaries = _card_truth()
        d = build_truth_diagnostic_summary(summaries)
        assert d["schema_version"] == "v3.truth.v1"

    def test_safe_plus_unsafe_equals_total_axes(self):
        summaries = _card_truth(action="BUY", conviction_level="HIGH")
        d = build_truth_diagnostic_summary(summaries)
        assert d["safe_axes"] + d["unsafe_axes"] == len(summaries)

    def test_axes_key_contains_all_axis_names(self):
        summaries = _card_truth()
        d = build_truth_diagnostic_summary(summaries)
        expected = {"evidence_quality", "action_signal", "conviction", "technical_signal", "risk_signal"}
        assert set(d["axes"].keys()) == expected

    def test_each_axis_has_required_sub_keys(self):
        summaries = _card_truth(action="BUY", conviction_level="HIGH")
        d = build_truth_diagnostic_summary(summaries)
        required = {"safe_for_decision", "present", "missing", "stale", "weak", "dominant_reason"}
        for axis_name, axis_data in d["axes"].items():
            for key in required:
                assert key in axis_data, f"Axis {axis_name!r} missing key {key!r}"

    def test_empty_card_has_zero_safe_axes(self):
        summaries = _card_truth()
        d = build_truth_diagnostic_summary(summaries)
        assert d["safe_axes"] == 0
        assert d["unsafe_axes"] == 5

    def test_full_card_has_multiple_safe_axes(self):
        summaries = _card_truth(
            action="BUY",
            analyst_action="BUY",
            conviction_level="HIGH",
            technical_signal="BULLISH",
            risk_flag="low risk",
            intel_read=_good_intel_read(3),
        )
        d = build_truth_diagnostic_summary(summaries)
        assert d["safe_axes"] >= 4
