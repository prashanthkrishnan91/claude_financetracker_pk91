"""Stage 8D — SEC/company catalyst evidence display adapter tests.

Verifies that:
- _build_catalyst_display_fields() produces safe boolean display fields
- snapshot_builder embeds sec_catalyst_evidence in evidence_explanation
- No raw backend codes appear in the display dict
- ETF/crypto tickers get sec_lane_applicable=False
- No decision authority is assigned (display-only, no policy mutation)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pytest

from app.services.intelligence.v3.catalyst_display_adapter_v1 import build_catalyst_display_fields as _build_catalyst_display_fields


# ── Minimal stub for TickerDecisionReadiness ──────────────────────────────────

@dataclass
class _StubAxisReadiness:
    readiness: str = "MISSING"
    is_usable: bool = False
    contributing_lanes: list = field(default_factory=list)
    degraded_lanes: list = field(default_factory=list)
    missing_lanes: list = field(default_factory=list)
    not_applicable_lanes: list = field(default_factory=list)
    lane_contributions: list = field(default_factory=list)


@dataclass
class _StubTickerReadiness:
    ticker: str = "MSFT"
    sec_lane_applicable: bool = True
    axes: dict = field(default_factory=dict)
    any_axis_usable: bool = False
    usable_axis_count: int = 0


def _make_readiness(
    *,
    sec_catalyst_contributing: bool = False,
    news_sentiment_degraded: bool = False,
    sec_lane_applicable: bool = True,
) -> _StubTickerReadiness:
    """Build a minimal TickerDecisionReadiness stub for testing."""
    sent_axis = _StubAxisReadiness(
        readiness="LIMITED" if sec_catalyst_contributing else "MISSING",
        is_usable=sec_catalyst_contributing,
        contributing_lanes=["sec_catalyst_sentiment"] if sec_catalyst_contributing else [],
        degraded_lanes=["news_sentiment"] if news_sentiment_degraded else [],
    )
    return _StubTickerReadiness(
        sec_lane_applicable=sec_lane_applicable,
        axes={"sentiment": sent_axis},
    )


# ── _build_catalyst_display_fields tests ─────────────────────────────────────

class TestBuildCatalystDisplayFields:

    def test_none_input_returns_safe_defaults(self):
        result = _build_catalyst_display_fields(None)
        assert result == {
            "sec_catalyst_found": False,
            "editorial_suppressed": False,
            "sec_lane_applicable": True,
        }

    def test_sec_catalyst_contributing_sets_found_true(self):
        readiness = _make_readiness(sec_catalyst_contributing=True)
        result = _build_catalyst_display_fields(readiness)
        assert result["sec_catalyst_found"] is True

    def test_no_sec_catalyst_sets_found_false(self):
        readiness = _make_readiness(sec_catalyst_contributing=False)
        result = _build_catalyst_display_fields(readiness)
        assert result["sec_catalyst_found"] is False

    def test_news_sentiment_degraded_sets_editorial_suppressed(self):
        readiness = _make_readiness(news_sentiment_degraded=True)
        result = _build_catalyst_display_fields(readiness)
        assert result["editorial_suppressed"] is True

    def test_news_sentiment_not_degraded_sets_editorial_suppressed_false(self):
        readiness = _make_readiness(news_sentiment_degraded=False)
        result = _build_catalyst_display_fields(readiness)
        assert result["editorial_suppressed"] is False

    def test_etf_sets_sec_lane_applicable_false(self):
        readiness = _make_readiness(sec_lane_applicable=False)
        result = _build_catalyst_display_fields(readiness)
        assert result["sec_lane_applicable"] is False

    def test_equity_sets_sec_lane_applicable_true(self):
        readiness = _make_readiness(sec_lane_applicable=True)
        result = _build_catalyst_display_fields(readiness)
        assert result["sec_lane_applicable"] is True

    def test_both_flags_set_independently(self):
        readiness = _make_readiness(
            sec_catalyst_contributing=True,
            news_sentiment_degraded=True,
        )
        result = _build_catalyst_display_fields(readiness)
        assert result["sec_catalyst_found"] is True
        assert result["editorial_suppressed"] is True

    def test_no_raw_backend_codes_in_values(self):
        """Ensure no raw status codes are in the display dict values."""
        forbidden = {
            "sec_catalyst_sentiment", "news_sentiment", "LIMITED", "SUPPRESSED",
            "READY", "MISSING", "THIN", "PARTIAL", "USABLE_WITH_LIMITATIONS",
        }
        readiness = _make_readiness(sec_catalyst_contributing=True, news_sentiment_degraded=True)
        result = _build_catalyst_display_fields(readiness)
        for val in result.values():
            assert str(val) not in forbidden, f"Raw code leaked: {val}"

    def test_result_contains_exactly_three_fields(self):
        result = _build_catalyst_display_fields(None)
        assert set(result.keys()) == {"sec_catalyst_found", "editorial_suppressed", "sec_lane_applicable"}

    def test_all_values_are_booleans(self):
        readiness = _make_readiness(sec_catalyst_contributing=True, news_sentiment_degraded=True)
        result = _build_catalyst_display_fields(readiness)
        for key, val in result.items():
            assert isinstance(val, bool), f"Field {key} is not bool: {val}"

    def test_no_axes_attribute_returns_safe_defaults(self):
        """Object without axes attribute should not raise."""

        class _Stub:
            sec_lane_applicable = True

        result = _build_catalyst_display_fields(_Stub())
        assert result["sec_catalyst_found"] is False
        assert result["editorial_suppressed"] is False


# ── Snapshot builder integration ──────────────────────────────────────────────

class TestSnapshotBuilderCatalystEvidence:
    """Test that snapshot_builder._build_held_card injects sec_catalyst_evidence."""

    def _make_card_meta(
        self,
        *,
        sec_catalyst_found: bool = False,
        editorial_suppressed: bool = False,
        sec_lane_applicable: bool = True,
        include_catalyst_display: bool = True,
    ) -> dict:
        meta = {
            "ticker": "MSFT",
            "name": "Microsoft",
            "category": "stock",
            "thesis_state": "intact",
            "governance_result": None,
            "research_axis_readiness": None,
        }
        if include_catalyst_display:
            meta["research_axis_readiness"] = {
                "technical_signals": "MISSING",
                "sentiment": "LIMITED" if sec_catalyst_found else "MISSING",
                "sec_catalyst_display": {
                    "sec_catalyst_found": sec_catalyst_found,
                    "editorial_suppressed": editorial_suppressed,
                    "sec_lane_applicable": sec_lane_applicable,
                },
            }
        return meta

    def _run_builder(self, card_meta: dict) -> dict:
        from app.services.intelligence.v3.snapshot_builder import _build_held_card
        from app.services.intelligence.v3.decision_contracts import (
            DecisionOutputV3,
            ActionV3,
            ConvictionV3,
            AxisBand,
            FitBand,
            PriceBand,
            RiskBand,
        )

        decision = DecisionOutputV3(
            ticker=card_meta.get("ticker", "TEST"),
            action=ActionV3.HOLD,
            conviction=ConvictionV3.LOW,
            evidence_quality=AxisBand.THIN,
            portfolio_fit=FitBand.UNKNOWN,
            risk_band=RiskBand.LOW,
            attractiveness=AxisBand.THIN,
            price_context=PriceBand.SUPPRESSED,
            rationale_plain_english="Holding conservatively.",
            why_now="",
            why_not_now="",
            suppression_reasons={},
            blockers=[],
            source_signal_summary={},
            schema_version="test_v1",
        )

        return _build_held_card(
            decision=decision,
            card_meta=card_meta,
            snapshot_id="snap-001",
            run_id="run-001",
        )

    def test_sec_catalyst_evidence_injected_when_display_present(self):
        meta = self._make_card_meta(sec_catalyst_found=True)
        card = self._run_builder(meta)
        ex = card["detail_drawer_payload"]["evidence_explanation"]
        assert "sec_catalyst_evidence" in ex
        assert ex["sec_catalyst_evidence"]["sec_catalyst_found"] is True

    def test_editorial_suppressed_propagated(self):
        meta = self._make_card_meta(editorial_suppressed=True)
        card = self._run_builder(meta)
        ex = card["detail_drawer_payload"]["evidence_explanation"]
        assert ex["sec_catalyst_evidence"]["editorial_suppressed"] is True

    def test_etf_sec_lane_applicable_false(self):
        meta = self._make_card_meta(sec_lane_applicable=False)
        card = self._run_builder(meta)
        ex = card["detail_drawer_payload"]["evidence_explanation"]
        assert ex["sec_catalyst_evidence"]["sec_lane_applicable"] is False

    def test_sec_catalyst_evidence_absent_when_no_display_fields(self):
        meta = self._make_card_meta(include_catalyst_display=False)
        card = self._run_builder(meta)
        ex = card["detail_drawer_payload"]["evidence_explanation"]
        assert "sec_catalyst_evidence" not in ex

    def test_no_decision_authority_in_display_fields(self):
        """sec_catalyst_evidence must not contain action/conviction fields."""
        meta = self._make_card_meta(sec_catalyst_found=True)
        card = self._run_builder(meta)
        ex = card["detail_drawer_payload"]["evidence_explanation"]
        cata = ex["sec_catalyst_evidence"]
        forbidden_keys = {"action", "conviction", "buy", "sell", "hold", "trim", "decision"}
        for key in cata:
            assert key.lower() not in forbidden_keys

    def test_no_raw_status_code_values_in_evidence_display(self):
        forbidden = {
            "sec_catalyst_sentiment", "news_sentiment", "LIMITED", "READY",
            "SUPPRESSED", "MISSING", "THIN", "PARTIAL",
        }
        meta = self._make_card_meta(sec_catalyst_found=True, editorial_suppressed=True)
        card = self._run_builder(meta)
        ex = card["detail_drawer_payload"]["evidence_explanation"]
        cata = ex["sec_catalyst_evidence"]
        for val in cata.values():
            assert str(val) not in forbidden, f"Raw code in display: {val}"

    def _make_card_meta_with_gov(self, *, sec_catalyst_found: bool = False) -> dict:
        """Build card_meta with governance_result set (simulates Stage 6 active path)."""
        # Minimal governance dict matching what _build_evidence_explanation() reads.
        gov = {
            "primary_evidence_readiness": "LIMITED",
            "auxiliary_evidence_readiness": {
                "technical_signals": "MISSING",
                "sentiment": "LIMITED" if sec_catalyst_found else "MISSING",
            },
            "conviction_cap_applied": True,
            "conviction_cap_reason": "ok_cap_medium",
            "safe_for_visible_decision": True,
            "safe_for_visible_decision_reason": "",
            "governance_priority_applied": "p4b_limited_no_corroboration",
            "corroboration_gap": True,
            "action_blocks_applied": [],
        }
        return {
            "ticker": "MSFT",
            "name": "Microsoft",
            "category": "stock",
            "thesis_state": "intact",
            "governance_result": gov,
            "research_axis_readiness": {
                "sec_catalyst_display": {
                    "sec_catalyst_found": sec_catalyst_found,
                    "editorial_suppressed": False,
                    "sec_lane_applicable": True,
                },
            },
        }

    def test_sec_catalyst_evidence_injected_when_stage6_active(self):
        """When governance_result is present (Stage 6 active path), sec_catalyst_evidence
        must still be injected from research_axis_readiness. This test fails on the original
        PR where _research_axis_readiness was None for s6_active=True."""
        meta = self._make_card_meta_with_gov(sec_catalyst_found=True)
        card = self._run_builder(meta)
        ex = card["detail_drawer_payload"]["evidence_explanation"]
        assert "sec_catalyst_evidence" in ex, (
            "sec_catalyst_evidence missing — Stage 6 active path not wired"
        )
        assert ex["sec_catalyst_evidence"]["sec_catalyst_found"] is True

    def test_governance_result_not_mutated_by_catalyst_display(self):
        """Injecting sec_catalyst_evidence must not change governance-derived fields."""
        meta = self._make_card_meta_with_gov(sec_catalyst_found=True)
        card = self._run_builder(meta)
        ex = card["detail_drawer_payload"]["evidence_explanation"]
        # governance-derived fields must still reflect the governance dict
        assert ex["primary_evidence_status"] == "LIMITED"
        assert ex["governance_priority"] == "p4b_limited_no_corroboration"
        assert ex["conviction_cap_applied"] is True
