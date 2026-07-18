"""PR 11: production-shaped synthetic fixture tests for v3 truth diagnostics wiring.

Validates that project_shadow_from_card_signals() and summarize_guardrail_impact_observability()
produce non-empty truth/evidence-quality counts when real production card fields are present.

Production observation: after enabling INFO logs, production summary showed
safe_axis_count=0, unsafe_axis_count=0, evidence_quality_status_counts={},
guardrail_evaluated_count=0 despite cards having analyst/data-quality/evidence fields.

Root cause: recommendation_engine._v3_shadow_projection() called the stale non-truth-aware
_v3_shadow_decide() path and returned dicts without truth_diagnostics. Fixed in PR 11 by
delegating to project_shadow_from_card_signals() which wires truth-aware diagnostics.

These tests use only synthetic production-shaped fixtures — no real user/account data.
"""
from __future__ import annotations

import pytest

from app.services.intelligence.v3.shadow_projection import (
    project_shadow_from_card_signals,
    summarize_guardrail_impact_observability,
    summarize_truth_aware_suppression,
)

# ── Production-shaped synthetic fixture helpers ───────────────────────────────

def _good_intel_read(n_trusted: int = 3) -> dict:
    """Synthetic intel_read with n trusted signals (mirrors production field shape)."""
    dims = ["business quality", "valuation", "growth", "momentum"][:n_trusted]
    return {"insufficient_data": False, "trusted_signals": dims, "suppressed_dimensions": []}


def _thin_intel_read() -> dict:
    return {"insufficient_data": True, "trusted_signals": [], "suppressed_dimensions": ["valuation"]}


def _prod_card(
    *,
    ticker: str = "MSFT",
    v2_visible_action: str = "HOLD",
    action: str = "HOLD",
    analyst_action: str | None = "HOLD",
    conviction_level: str | None = "LOW",
    technical_signal: str | None = None,
    risk_flag: str | None = None,
    analyst_risks: list | None = None,
    category: str = "Core",
    data_quality_label: str | None = "MEDIUM",
    intel_read: dict | None = None,
    thesis_v2: dict | None = None,
) -> dict:
    """Build a production-shaped card dict matching fields logged in Railway."""
    return dict(
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


# ── Test 1: truth_diagnostics present in output when card fields exist ────────

class TestTruthDiagnosticsHydrated:
    """project_shadow_from_card_signals must return truth_diagnostics for real card shapes."""

    def test_truth_diagnostics_key_present_for_production_hold_card(self):
        card = _prod_card(
            ticker="MSFT",
            v2_visible_action="HOLD",
            analyst_action="HOLD",
            conviction_level="LOW",
            data_quality_label="MEDIUM",
            intel_read=_good_intel_read(2),
        )
        diag = project_shadow_from_card_signals(**card)
        assert diag is not None, "projection must not fail on production-shaped card"
        assert "truth_diagnostics" in diag, "truth_diagnostics must be hydrated"
        assert isinstance(diag["truth_diagnostics"], dict)

    def test_safe_axis_count_nonzero_when_evidence_present(self):
        card = _prod_card(
            ticker="AAPL",
            v2_visible_action="HOLD",
            analyst_action="HOLD",
            conviction_level="MEDIUM",
            data_quality_label="HIGH",
            intel_read=_good_intel_read(3),
        )
        diag = project_shadow_from_card_signals(**card)
        assert diag is not None
        td = diag["truth_diagnostics"]
        assert td["safe_axis_count"] > 0, "safe_axis_count must be nonzero when fields exist"

    def test_unsafe_axis_count_plus_safe_axis_count_equals_five_axes(self):
        """Always exactly 5 axes evaluated by evaluate_card_signals_truth."""
        card = _prod_card(
            ticker="GOOGL",
            v2_visible_action="HOLD",
            data_quality_label="MEDIUM",
            intel_read=_good_intel_read(2),
        )
        diag = project_shadow_from_card_signals(**card)
        assert diag is not None
        td = diag["truth_diagnostics"]
        total = td["safe_axis_count"] + td["unsafe_axis_count"]
        assert total == 5, f"expected 5 total axes, got {total}"

    @pytest.mark.parametrize("data_quality_label,intel_read,expect_safe", [
        ("HIGH", _good_intel_read(3), True),
        ("MEDIUM", _good_intel_read(2), True),
        (None, None, False),
    ])
    def test_evidence_axis_safety_by_data_quality_label(self, data_quality_label, intel_read, expect_safe):
        card = _prod_card(
            ticker="AMZN",
            data_quality_label=data_quality_label,
            intel_read=intel_read,
        )
        diag = project_shadow_from_card_signals(**card)
        assert diag is not None
        td = diag["truth_diagnostics"]
        axes = td.get("axes", {})
        ev = axes.get("evidence_quality", {})
        assert ev["safe_for_decision"] is expect_safe


# ── Test 2: evidence_quality_status_counts nonempty when guardrail evaluated ──

class TestGuardrailEvaluatedForBuyCards:
    """guardrail_evaluated_count must be nonzero when shadow produces BUY."""

    def test_guardrail_evaluated_count_nonzero_for_buy_card(self):
        """A card with strong BUY signals triggers the guardrail evaluation path."""
        card = _prod_card(
            ticker="NVDA",
            v2_visible_action="BUY",
            analyst_action="BUY",
            conviction_level="HIGH",
            data_quality_label="HIGH",
            intel_read=_good_intel_read(3),
        )
        diag = project_shadow_from_card_signals(**card)
        assert diag is not None
        assert diag["v3_shadow_action"] == "BUY"
        td = diag["truth_diagnostics"]
        guardrail = td.get("buy_conviction_guardrail")
        assert isinstance(guardrail, dict), "guardrail diagnostics must be present"

        summary = summarize_guardrail_impact_observability([diag])
        assert summary["guardrail_evaluated_count"] == 1
        assert summary["evidence_quality_status_counts"] != {}, \
            "evidence_quality_status_counts must not be empty when guardrail evaluated"

    def test_evidence_quality_status_counts_not_empty_for_buy_batch(self):
        """Batch of BUY cards must produce non-empty evidence_quality_status_counts."""
        cards = [
            _prod_card(ticker=f"T{i}", v2_visible_action="BUY", analyst_action="BUY",
                       conviction_level="HIGH", data_quality_label="HIGH",
                       intel_read=_good_intel_read(3))
            for i in range(3)
        ]
        diagnostics = [project_shadow_from_card_signals(**c) for c in cards]
        assert all(d is not None for d in diagnostics)

        summary = summarize_guardrail_impact_observability(diagnostics)
        assert summary["guardrail_evaluated_count"] == 3
        assert summary["evidence_quality_status_counts"] != {}
        assert summary["evidence_quality_trust_counts"] != {}

    def test_guardrail_caps_high_conviction_when_evidence_not_high_trust(self):
        """Card with MEDIUM evidence trust + BUY/HIGH should be capped to MEDIUM conviction."""
        card = _prod_card(
            ticker="META",
            v2_visible_action="BUY",
            analyst_action="BUY",
            conviction_level="HIGH",
            data_quality_label="MEDIUM",
            intel_read=_good_intel_read(2),
        )
        diag = project_shadow_from_card_signals(**card)
        assert diag is not None
        assert diag["v3_shadow_action"] == "BUY"

        guardrail = diag["truth_diagnostics"]["buy_conviction_guardrail"]
        # The evidence-quality guardrail was promoted into the visible policy
        # kernel (Cap 5 in _compute_conviction), so conviction arrives here
        # already capped at MEDIUM and the shadow guardrail reports
        # applied=False (see test_v3_evidence_quality_guardrail.py).
        assert guardrail["buy_high_conviction_guardrail_applied"] is False
        assert guardrail["evidence_quality_trust_level"] == "MEDIUM"
        assert diag["v3_shadow_conviction"] == "MEDIUM"

        summary = summarize_guardrail_impact_observability([diag])
        assert summary["buy_conviction_capped_count"] == 0
        assert summary["guardrail_evaluated_count"] == 1


# ── Test 3: HOLD cards — truth diagnostics populated, guardrail not triggered ─

class TestHoldCardsProduceTruthDiagnosticsNotGuardrail:
    """HOLD cards must hydrate truth_diagnostics but not activate the guardrail."""

    @pytest.mark.parametrize("ticker,data_quality_label,intel_read", [
        ("MSFT", "MEDIUM", _good_intel_read(2)),
        ("AAPL", "HIGH", _good_intel_read(3)),
        ("VYM", "LOW", _thin_intel_read()),
        ("GOOGL", None, None),
    ])
    def test_hold_card_truth_diagnostics_hydrated(self, ticker, data_quality_label, intel_read):
        card = _prod_card(
            ticker=ticker,
            v2_visible_action="HOLD",
            analyst_action="HOLD",
            conviction_level="LOW",
            data_quality_label=data_quality_label,
            intel_read=intel_read,
        )
        diag = project_shadow_from_card_signals(**card)
        assert diag is not None
        assert "truth_diagnostics" in diag
        td = diag["truth_diagnostics"]
        total = td["safe_axis_count"] + td["unsafe_axis_count"]
        assert total == 5

    def test_hold_batch_truth_suppression_summary_nonzero(self):
        """Batch of HOLD cards with mixed data quality produces nonzero truth aggregates."""
        cards = [
            _prod_card(ticker="MSFT", v2_visible_action="HOLD", data_quality_label="MEDIUM", intel_read=_good_intel_read(2)),
            _prod_card(ticker="AAPL", v2_visible_action="HOLD", data_quality_label="HIGH", intel_read=_good_intel_read(3)),
            _prod_card(ticker="GOOGL", v2_visible_action="HOLD", data_quality_label=None, intel_read=None),
        ]
        diagnostics = [project_shadow_from_card_signals(**c) for c in cards]
        assert all(d is not None for d in diagnostics)

        truth_summary = summarize_truth_aware_suppression(diagnostics)
        total = truth_summary["safe_axis_count"] + truth_summary["unsafe_axis_count"]
        assert total > 0, "truth aggregates must be nonzero when cards have evidence fields"

    def test_hold_card_guardrail_not_triggered(self):
        """HOLD shadow action must not trigger guardrail capping."""
        card = _prod_card(ticker="VOO", v2_visible_action="HOLD", analyst_action="HOLD",
                          data_quality_label="HIGH", intel_read=_good_intel_read(3))
        diag = project_shadow_from_card_signals(**card)
        assert diag is not None
        assert diag["v3_shadow_action"] == "HOLD"
        guardrail = diag["truth_diagnostics"]["buy_conviction_guardrail"]
        assert guardrail["buy_high_conviction_guardrail_applied"] is False


# ── Test 4: summary aggregation sees truth_diagnostics from per-card shape ────

class TestSummaryAggregationReadsNestedTruthDiagnostics:
    """summarize_truth_aware_suppression and summarize_guardrail_impact_observability
    must correctly aggregate from the nested truth_diagnostics key produced by
    project_shadow_from_card_signals — this is the key PR 11 regression test."""

    def test_truth_suppression_aggregates_from_real_projection_output(self):
        cards = [
            _prod_card(ticker="MSFT", v2_visible_action="HOLD", data_quality_label="HIGH", intel_read=_good_intel_read(3)),
            _prod_card(ticker="AAPL", v2_visible_action="HOLD", data_quality_label="MEDIUM", intel_read=_good_intel_read(2)),
            _prod_card(ticker="GOOGL", v2_visible_action="HOLD", data_quality_label=None, intel_read=None),
        ]
        diagnostics = [project_shadow_from_card_signals(**c) for c in cards]
        assert all(d is not None for d in diagnostics)

        summary = summarize_truth_aware_suppression(diagnostics)
        # 3 cards × 5 axes each = 15 total
        assert summary["safe_axis_count"] + summary["unsafe_axis_count"] == 15

    def test_guardrail_summary_aggregates_from_real_projection_output(self):
        cards = [
            _prod_card(ticker="NVDA", v2_visible_action="BUY", analyst_action="BUY",
                       conviction_level="HIGH", data_quality_label="HIGH", intel_read=_good_intel_read(3)),
            _prod_card(ticker="META", v2_visible_action="BUY", analyst_action="BUY",
                       conviction_level="HIGH", data_quality_label="MEDIUM", intel_read=_good_intel_read(2)),
            _prod_card(ticker="MSFT", v2_visible_action="HOLD", analyst_action="HOLD",
                       conviction_level="LOW", data_quality_label="MEDIUM", intel_read=_good_intel_read(2)),
        ]
        diagnostics = [project_shadow_from_card_signals(**c) for c in cards]
        assert all(d is not None for d in diagnostics)

        summary = summarize_guardrail_impact_observability(diagnostics)
        # BUY cards always have guardrail evaluated; HOLD card also evaluated (always runs)
        assert summary["guardrail_evaluated_count"] == 3
        assert summary["evidence_quality_status_counts"] != {}

    def test_honest_hold_batch_truth_aggregates_not_empty(self):
        """Regression: 34-card all-HOLD batch still produces nonzero truth aggregate counts."""
        cards = [
            _prod_card(ticker=f"HOLD{i}", v2_visible_action="HOLD", analyst_action="HOLD",
                       conviction_level="LOW", data_quality_label="MEDIUM",
                       intel_read=_good_intel_read(2))
            for i in range(34)
        ]
        diagnostics = [project_shadow_from_card_signals(**c) for c in cards]
        assert all(d is not None for d in diagnostics)

        truth_summary = summarize_truth_aware_suppression(diagnostics)
        guardrail_summary = summarize_guardrail_impact_observability(diagnostics)

        # Truth aggregates must not be zero for 34 real cards
        assert truth_summary["safe_axis_count"] > 0, \
            "safe_axis_count must be nonzero for 34-card all-HOLD production batch"
        assert truth_summary["unsafe_axis_count"] >= 0

        # Guardrail is always evaluated (even for HOLD cards)
        assert guardrail_summary["guardrail_evaluated_count"] == 34
        assert guardrail_summary["evidence_quality_status_counts"] != {}, \
            "evidence_quality_status_counts must not be empty for 34-card batch"


# ── Test 5: visible action unchanged ─────────────────────────────────────────

class TestVisibleActionUnchanged:
    """v2_visible_action in the diagnostic must always match the input action."""

    @pytest.mark.parametrize("action", ["BUY", "HOLD", "TRIM", "SELL"])
    def test_v2_visible_action_preserved(self, action):
        card = _prod_card(
            ticker="AAPL",
            v2_visible_action=action,
            analyst_action=action,
            conviction_level="MEDIUM",
            data_quality_label="MEDIUM",
            intel_read=_good_intel_read(2),
        )
        diag = project_shadow_from_card_signals(**card)
        assert diag is not None
        assert diag["v2_visible_action"] == action


# ── Test 6: malformed/partial cards fail soft ─────────────────────────────────

class TestFailSoftBehavior:
    """Malformed or missing fields must produce None or partial diagnostics, never raise."""

    def test_all_none_fields_returns_none_or_partial(self):
        # ticker is required; all optional fields None
        card = _prod_card(ticker="ANON", v2_visible_action="HOLD",
                          analyst_action=None, conviction_level=None,
                          technical_signal=None, risk_flag=None,
                          analyst_risks=None, data_quality_label=None,
                          intel_read=None)
        diag = project_shadow_from_card_signals(**card)
        # Fail-soft: either returns None (acceptable) or a valid dict
        assert diag is None or isinstance(diag, dict)

    def test_none_batch_counted_as_projection_failures_not_exceptions(self):
        diagnostics = [None, None, None]
        summary = summarize_truth_aware_suppression(diagnostics)
        guardrail_summary = summarize_guardrail_impact_observability(diagnostics)
        assert summary["safe_axis_count"] == 0
        assert guardrail_summary["guardrail_evaluated_count"] == 0

    def test_partial_truth_diagnostics_does_not_raise(self):
        # Card with partial field shape (some fields missing from production log)
        diag = project_shadow_from_card_signals(
            ticker="PARTIAL",
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
        # Must not raise; result is None or a valid dict with truth_diagnostics
        assert diag is None or "truth_diagnostics" in diag
