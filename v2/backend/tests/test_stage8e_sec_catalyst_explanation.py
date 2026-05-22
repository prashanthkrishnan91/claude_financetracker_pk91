"""Stage 8E — SEC catalyst explanation display adapter tests.

Verifies that:
- build_sec_catalyst_explanation() returns correct plain-English fields
  from artifact payload when catalyst_count > 0
- Falls back to empty dict when payload is None, missing, or catalyst_count=0
- No raw backend codes appear in any returned string
- No decision authority keys or phrases appear
- ETF/non-equity hidden state unchanged (handled by Stage 8D layer)
- Existing Stage 8D snapshot_builder tests still pass (no regression)
"""
from __future__ import annotations

import pytest

from app.services.intelligence.v3.sec_catalyst_explanation_adapter_v1 import (
    build_sec_catalyst_explanation,
)

# ── Raw-code leak guard ────────────────────────────────────────────────────────

_FORBIDDEN_CODES = {
    "sec_catalyst_sentiment",
    "SEC_CATALYST_MODEL_VERSION",
    "skill_pack",
    "fact_kind",
    "model_version",
    "READY",
    "PARTIAL",
    "LIMITED",
    "USABLE_WITH_LIMITATIONS",
    "SUPPRESSED_INCOMPLETE",
    "stage8c_sec_catalyst_sentiment",
    "sec_catalyst_sentiment_evidence_v1",
    "Stage 5K",
    "Stage 5J",
}

_DECISION_AUTHORITY_PHRASES = {
    "buy", "sell", "trim", "hold", "recommendation", "action",
}


def _assert_no_raw_codes(text: str) -> None:
    for code in _FORBIDDEN_CODES:
        assert code not in text, f"Raw code '{code}' leaked in: {text!r}"


def _assert_no_decision_authority(text: str) -> None:
    lower = text.lower()
    for phrase in _DECISION_AUTHORITY_PHRASES:
        # Allow "decide" / "does not decide" in context; block standalone action words
        # unless they appear in the negation "does not decide ... Buy, Hold, Trim, or Sell"
        if phrase in ("buy", "sell", "trim", "hold"):
            # These words are OK only as part of an explicit negation
            if phrase in lower and "does not decide" not in lower and "did not determine" not in lower:
                # Check it's not just part of the standard disclaimer
                if "buy, hold, trim, or sell" not in lower:
                    pytest.fail(
                        f"Possible decision authority phrase '{phrase}' in: {text!r}"
                    )


# ── Minimal payload builders ───────────────────────────────────────────────────

def _make_payload(
    *,
    catalyst_count: int = 1,
    usable_count: int = 1,
    filing_count: int = 5,
) -> dict:
    return {
        "lane": "sec_catalyst_sentiment_evidence_v1",
        "reviewed_ticker": "MSFT",
        "worker_phase": "stage8c_sec_catalyst_sentiment",
        "provider": "sec_edgar",
        "filing_count": filing_count,
        "catalyst_count": catalyst_count,
        "usable_count": usable_count,
        "skipped_stale_count": 0,
        "skipped_routine_count": 0,
        "cik": "0000789019",
        "best_decision_usefulness_tier": "LIMITED",
    }


# ── Unit tests ─────────────────────────────────────────────────────────────────

class TestBuildSecCatalystExplanation:

    def test_returns_dict_with_explanation_fields_for_valid_payload(self):
        result = build_sec_catalyst_explanation(_make_payload())
        assert isinstance(result, dict)
        assert "event_summary" in result
        assert "freshness_label" in result
        assert "material_filing_label" in result
        assert "limitation_note" in result
        assert "decision_authority_note" in result

    def test_returns_empty_dict_for_none_payload(self):
        result = build_sec_catalyst_explanation(None)
        assert result == {}

    def test_returns_empty_dict_for_empty_dict_payload(self):
        result = build_sec_catalyst_explanation({})
        assert result == {}

    def test_returns_empty_dict_when_catalyst_count_zero(self):
        result = build_sec_catalyst_explanation(_make_payload(catalyst_count=0))
        assert result == {}

    def test_returns_empty_dict_when_catalyst_count_missing(self):
        payload = _make_payload()
        del payload["catalyst_count"]
        result = build_sec_catalyst_explanation(payload)
        assert result == {}

    def test_returns_empty_dict_when_catalyst_count_non_int(self):
        payload = _make_payload()
        payload["catalyst_count"] = "one"
        result = build_sec_catalyst_explanation(payload)
        assert result == {}

    def test_returns_empty_dict_when_payload_not_dict(self):
        result = build_sec_catalyst_explanation("not_a_dict")  # type: ignore
        assert result == {}

    def test_single_filing_label(self):
        result = build_sec_catalyst_explanation(_make_payload(catalyst_count=1))
        assert "One recent official filing" in result["material_filing_label"]

    def test_multiple_filings_label(self):
        result = build_sec_catalyst_explanation(_make_payload(catalyst_count=3))
        assert "3 recent official filings" in result["material_filing_label"]

    def test_usable_count_enriches_event_summary(self):
        result = build_sec_catalyst_explanation(_make_payload(usable_count=1))
        assert "material enough" in result["event_summary"].lower()

    def test_zero_usable_count_gives_generic_event_summary(self):
        result = build_sec_catalyst_explanation(_make_payload(usable_count=0))
        assert "Recent official filing activity was found." == result["event_summary"]
        assert "material enough" not in result["event_summary"]

    def test_freshness_label_present_and_safe(self):
        result = build_sec_catalyst_explanation(_make_payload())
        assert result["freshness_label"]
        _assert_no_raw_codes(result["freshness_label"])

    def test_limitation_note_covers_official_events_only(self):
        result = build_sec_catalyst_explanation(_make_payload())
        assert "official" in result["limitation_note"].lower()
        assert "SEC" in result["limitation_note"] or "official" in result["limitation_note"].lower()


class TestRawCodeLeakGuard:

    def test_no_raw_codes_in_any_field(self):
        result = build_sec_catalyst_explanation(_make_payload(catalyst_count=2, usable_count=1))
        for val in result.values():
            _assert_no_raw_codes(val)

    def test_no_raw_codes_for_single_filing(self):
        result = build_sec_catalyst_explanation(_make_payload(catalyst_count=1))
        for val in result.values():
            _assert_no_raw_codes(val)

    def test_event_summary_no_raw_codes(self):
        result = build_sec_catalyst_explanation(_make_payload())
        _assert_no_raw_codes(result["event_summary"])


class TestDecisionAuthorityGuard:

    def test_decision_authority_note_contains_negation(self):
        result = build_sec_catalyst_explanation(_make_payload())
        note = result["decision_authority_note"].lower()
        assert "does not decide" in note or "did not determine" in note or "not decide" in note

    def test_decision_authority_note_no_authority_claim(self):
        result = build_sec_catalyst_explanation(_make_payload())
        note = result["decision_authority_note"].lower()
        # Acceptable: "does not decide Buy, Hold, Trim, or Sell"
        # The note should contain the disclaimer, not the authority
        assert "does not decide buy" in note or "does not decide" in note

    def test_event_summary_no_standalone_buy_sell(self):
        result = build_sec_catalyst_explanation(_make_payload())
        summary_lower = result["event_summary"].lower()
        # No decision authority — should not say "buy", "sell", "hold", "trim" as actions
        for word in ("buy", "sell", "trim"):
            assert word not in summary_lower, f"'{word}' in event_summary"


class TestFallbackBehavior:

    def test_minimal_payload_with_only_catalyst_count(self):
        """Minimal payload: only catalyst_count present — should still work."""
        result = build_sec_catalyst_explanation({"catalyst_count": 1})
        assert result != {}
        assert "event_summary" in result
        assert "material_filing_label" in result

    def test_payload_missing_usable_count_gives_generic_event_summary(self):
        """When usable_count absent, event_summary is the generic version."""
        payload = {"catalyst_count": 1}
        result = build_sec_catalyst_explanation(payload)
        assert "Recent official filing activity was found." == result["event_summary"]

    def test_extra_payload_fields_do_not_cause_errors(self):
        """Extra / unknown payload fields must not raise."""
        payload = _make_payload()
        payload["unknown_internal_field"] = "SOME_CODE"
        payload["best_decision_usefulness_tier"] = "LIMITED"
        result = build_sec_catalyst_explanation(payload)
        assert "event_summary" in result


class TestSuppressedEditorialAlongsideOfficialCatalyst:
    """Stage 8E does not change suppression logic — that is Stage 8D.
    This test confirms the adapter only concerns itself with artifact payload fields.
    """

    def test_explanation_adapter_agnostic_to_editorial_flag(self):
        """The explanation adapter does not know about editorial_suppressed.
        It purely converts artifact payload to display fields."""
        result = build_sec_catalyst_explanation(_make_payload(catalyst_count=1))
        # editorial_suppressed flag is not in the output — Stage 8D handles it
        assert "editorial_suppressed" not in result
        assert "sec_catalyst_found" not in result
        assert "sec_lane_applicable" not in result


class TestSnapshotBuilderCompatibility:
    """Stage 8E must not break Stage 8D snapshot_builder tests."""

    def test_snapshot_builder_still_injects_sec_catalyst_evidence(self):
        """Confirm Stage 8D snapshot_builder contract unchanged after Stage 8E adapter added."""
        from app.services.intelligence.v3.snapshot_builder import _build_held_card
        from app.services.intelligence.v3.decision_contracts import (
            DecisionOutputV3, ActionV3, ConvictionV3, AxisBand, FitBand, PriceBand, RiskBand,
        )

        # Simulate Stage 8E-enriched sec_catalyst_display in research_axis_readiness
        explanation = build_sec_catalyst_explanation(_make_payload())
        sec_cat_display = {
            "sec_catalyst_found": True,
            "editorial_suppressed": False,
            "sec_lane_applicable": True,
            **explanation,
        }
        card_meta = {
            "ticker": "MSFT",
            "name": "Microsoft",
            "category": "stock",
            "thesis_state": "intact",
            "governance_result": None,
            "research_axis_readiness": {
                "technical_signals": "MISSING",
                "sentiment": "LIMITED",
                "sec_catalyst_display": sec_cat_display,
            },
        }
        decision = DecisionOutputV3(
            ticker="MSFT",
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
        card = _build_held_card(
            decision=decision,
            card_meta=card_meta,
            snapshot_id="snap-001",
            run_id="run-001",
        )
        ex = card["detail_drawer_payload"]["evidence_explanation"]
        assert "sec_catalyst_evidence" in ex
        cata = ex["sec_catalyst_evidence"]
        assert cata["sec_catalyst_found"] is True
        # Stage 8E fields should be present in the snapshot
        assert "event_summary" in cata
        assert "material_filing_label" in cata

    def test_snapshot_builder_snapshot_no_raw_codes_in_8e_fields(self):
        """No raw backend codes in Stage 8E explanation fields in snapshot."""
        from app.services.intelligence.v3.snapshot_builder import _build_held_card
        from app.services.intelligence.v3.decision_contracts import (
            DecisionOutputV3, ActionV3, ConvictionV3, AxisBand, FitBand, PriceBand, RiskBand,
        )

        explanation = build_sec_catalyst_explanation(_make_payload(catalyst_count=2, usable_count=1))
        sec_cat_display = {
            "sec_catalyst_found": True,
            "editorial_suppressed": True,
            "sec_lane_applicable": True,
            **explanation,
        }
        card_meta = {
            "ticker": "MSFT",
            "name": "Microsoft",
            "category": "stock",
            "thesis_state": "intact",
            "governance_result": None,
            "research_axis_readiness": {
                "technical_signals": "MISSING",
                "sentiment": "LIMITED",
                "sec_catalyst_display": sec_cat_display,
            },
        }
        decision = DecisionOutputV3(
            ticker="MSFT",
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
        card = _build_held_card(
            decision=decision,
            card_meta=card_meta,
            snapshot_id="snap-001",
            run_id="run-001",
        )
        ex = card["detail_drawer_payload"]["evidence_explanation"]
        cata = ex["sec_catalyst_evidence"]
        for val in cata.values():
            _assert_no_raw_codes(str(val))
