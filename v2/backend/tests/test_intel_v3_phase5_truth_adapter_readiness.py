"""Phase 5 Truth Adapter Readiness Contract — test suite (hardened).

Acceptance criteria covered:
  1.  Phase 4-style artifact (UNKNOWN confidence, UNKNOWN freshness, zero sources,
      facts present) => ineligible.
  2.  Missing sources / sources without provenance handle => ineligible.
  3.  Missing facts => ineligible.
  4.  UNKNOWN confidence/trust => ineligible.
  5.  UNKNOWN freshness => ineligible.
  6.  Invalidated artifact (invalidated_at not None) => ineligible.
  7.  Expired artifact (expires_at in the past) => ineligible.
  8.  Forbidden payload key anywhere nested => ineligible.
  9.  Unsupported artifact_type => ineligible.
  10. Unsupported skill_pack => ineligible.
  11. Malformed/null fields fail closed.
  12. Well-formed source-linked artifact: eligible_for_truth_adapter=True,
      eligible_for_decision_consumption=False (Phase 5 invariant).
  13. Module does not import decide(), IntelV3Service, or frontend code.
  14. No visible snapshot/action behavior changes (structural source guard).
  15. Existing Phase 3/3.5/3.6/3.7/4 tests still pass (verified by joint test run).

Phase 5 hardening additions:
  H1. Every valid fact must have a non-empty source_id (fact_missing_source_link).
  H2. Fact source_id must match a valid source's id (fact_source_not_found).
  H3. Source without any provenance handle fails (no_valid_sources).
  H4. Source provenance: source_url OR source_id OR source_hash OR section_reference.
  H5. safe_for_decision=True fails with unexpected_safe_for_decision_true.

All tests use in-memory fixtures only — no Supabase, no external calls.
"""
from __future__ import annotations

import importlib
import inspect
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import pytest

from app.services.intelligence.research_workers.artifact_truth_readiness import (
    SUPPORTED_ARTIFACT_TYPES,
    SUPPORTED_SKILL_PACKS,
    VALID_CONFIDENCE_LEVELS,
    VALID_FRESHNESS_STATUSES,
    ArtifactReadinessResult,
    evaluate_artifact_truth_readiness,
)


# ── Fixture helpers ───────────────────────────────────────────────────────────

def _future_iso(days: int = 30) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def _past_iso(days: int = 1) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _ideal_artifact() -> dict:
    """Artifact that satisfies all 12 readiness conditions."""
    return {
        "id": "art-ideal-001",
        "ticker": "AAPL",
        "artifact_type": "catalyst_window",
        "skill_pack": "earnings_reviewer",
        "is_active": True,
        "invalidated_at": None,
        "expires_at": _future_iso(30),
        "confidence_or_trust_level": "MEDIUM",
        "freshness_status": "FRESH",
        "payload": {
            "evidence_summary": "Q1 2026 earnings reviewed from SEC EDGAR filing.",
            "found_fields": ["revenue", "eps"],
            "reviewed_fields": ["revenue", "eps", "guidance"],
        },
    }


def _ideal_sources() -> list[dict]:
    # Must include at least one provenance handle (source_id here).
    return [
        {
            "id": "src-001",
            "artifact_id": "art-ideal-001",
            "source_kind": "earnings_report",
            "provider_name": "sec_edgar",
            "source_id": "aapl-q1-2026-10q",  # provenance handle
        }
    ]


def _ideal_facts() -> list[dict]:
    # source_id links to _ideal_sources()[0]["id"] (DB row id = "src-001").
    return [
        {
            "id": "fact-001",
            "artifact_id": "art-ideal-001",
            "fact_kind": "earnings_event",
            "structured_payload": {"period": "Q1-2026", "status": "reported"},
            "source_id": "src-001",
        }
    ]


def _phase4_artifact() -> dict:
    """Mirrors a real Phase 3/4 dark-run artifact — UNKNOWN everywhere, zero sources."""
    return {
        "id": "art-phase4-001",
        "ticker": "AAPL",
        "artifact_type": "catalyst_window",
        "skill_pack": "earnings_reviewer",
        "is_active": True,
        "invalidated_at": None,
        "expires_at": None,
        "confidence_or_trust_level": "UNKNOWN",
        "freshness_status": "UNKNOWN",
        "payload": {
            "evidence_summary": "Scaffold artifact — no external source.",
            "found_fields": [],
            "limitations_or_missing_evidence": [
                "no_external_provider",
                "no_earnings_data",
                "no_price_target",
            ],
        },
    }


# ── Acceptance criterion 1: Phase 4-style artifact ────────────────────────────

class TestPhase4StyleArtifactIneligible:
    """AC-1: Current production Phase 4 artifacts must be ineligible."""

    def test_phase4_artifact_is_ineligible(self):
        result = evaluate_artifact_truth_readiness(
            artifact=_phase4_artifact(),
            sources=[],   # zero sources — as in Phase 4 production
            facts=[
                {
                    "id": "fact-p4-001",
                    "fact_kind": "missing_evidence_scaffold",
                    "structured_payload": {"note": "scaffold only"},
                    "source_id": None,
                }
            ],
        )
        assert result.eligible_for_truth_adapter is False
        assert result.eligible_for_decision_consumption is False
        assert "unknown_or_invalid_confidence" in result.reason_codes
        assert "unknown_or_invalid_freshness" in result.reason_codes
        assert "no_valid_sources" in result.reason_codes

    def test_phase4_fail_closed_always_true(self):
        result = evaluate_artifact_truth_readiness(artifact=_phase4_artifact())
        assert result.fail_closed is True

    def test_phase4_db_promotion_blocked(self):
        result = evaluate_artifact_truth_readiness(artifact=_phase4_artifact())
        assert result.safe_for_decision_db_promotion_blocked is True

    def test_phase4_decision_consumption_always_false(self):
        result = evaluate_artifact_truth_readiness(artifact=_phase4_artifact())
        assert result.eligible_for_decision_consumption is False


# ── Acceptance criterion 2: Missing sources ───────────────────────────────────

class TestMissingSourcesIneligible:
    """AC-2: Artifact with zero valid sources must be ineligible."""

    def test_no_sources_arg_ineligible(self):
        art = _ideal_artifact()
        result = evaluate_artifact_truth_readiness(artifact=art, sources=None, facts=_ideal_facts())
        assert result.eligible_for_truth_adapter is False
        assert "no_valid_sources" in result.reason_codes

    def test_empty_sources_list_ineligible(self):
        result = evaluate_artifact_truth_readiness(
            artifact=_ideal_artifact(), sources=[], facts=_ideal_facts()
        )
        assert result.eligible_for_truth_adapter is False
        assert "no_valid_sources" in result.reason_codes

    def test_source_missing_source_kind_ineligible(self):
        bad_source = {"id": "src-bad", "provider_name": "sec_edgar"}  # no source_kind
        result = evaluate_artifact_truth_readiness(
            artifact=_ideal_artifact(), sources=[bad_source], facts=_ideal_facts()
        )
        assert result.eligible_for_truth_adapter is False
        assert "no_valid_sources" in result.reason_codes

    def test_source_missing_provider_name_ineligible(self):
        bad_source = {"id": "src-bad", "source_kind": "earnings_report"}  # no provider_name
        result = evaluate_artifact_truth_readiness(
            artifact=_ideal_artifact(), sources=[bad_source], facts=_ideal_facts()
        )
        assert result.eligible_for_truth_adapter is False
        assert "no_valid_sources" in result.reason_codes

    def test_source_empty_string_fields_ineligible(self):
        bad_source = {"id": "src-bad", "source_kind": "  ", "provider_name": ""}
        result = evaluate_artifact_truth_readiness(
            artifact=_ideal_artifact(), sources=[bad_source], facts=_ideal_facts()
        )
        assert result.eligible_for_truth_adapter is False
        assert "no_valid_sources" in result.reason_codes

    def test_source_count_zero_when_all_invalid(self):
        bad_source = {"id": "src-bad", "source_kind": "  ", "provider_name": ""}
        result = evaluate_artifact_truth_readiness(
            artifact=_ideal_artifact(), sources=[bad_source], facts=_ideal_facts()
        )
        assert result.source_count == 0


# ── Acceptance criterion 3: Missing facts ─────────────────────────────────────

class TestMissingFactsIneligible:
    """AC-3: Artifact with zero valid facts must be ineligible."""

    def test_no_facts_arg_ineligible(self):
        result = evaluate_artifact_truth_readiness(
            artifact=_ideal_artifact(), sources=_ideal_sources(), facts=None
        )
        assert result.eligible_for_truth_adapter is False
        assert "no_valid_facts" in result.reason_codes

    def test_empty_facts_list_ineligible(self):
        result = evaluate_artifact_truth_readiness(
            artifact=_ideal_artifact(), sources=_ideal_sources(), facts=[]
        )
        assert result.eligible_for_truth_adapter is False
        assert "no_valid_facts" in result.reason_codes

    def test_fact_missing_fact_kind_ineligible(self):
        bad_fact = {"id": "fact-bad", "structured_payload": {"x": 1}}  # no fact_kind
        result = evaluate_artifact_truth_readiness(
            artifact=_ideal_artifact(), sources=_ideal_sources(), facts=[bad_fact]
        )
        assert result.eligible_for_truth_adapter is False
        assert "no_valid_facts" in result.reason_codes

    def test_fact_empty_structured_payload_ineligible(self):
        bad_fact = {"id": "fact-bad", "fact_kind": "earnings_event", "structured_payload": {}}
        result = evaluate_artifact_truth_readiness(
            artifact=_ideal_artifact(), sources=_ideal_sources(), facts=[bad_fact]
        )
        assert result.eligible_for_truth_adapter is False
        assert "no_valid_facts" in result.reason_codes

    def test_fact_none_structured_payload_ineligible(self):
        bad_fact = {"id": "fact-bad", "fact_kind": "earnings_event", "structured_payload": None}
        result = evaluate_artifact_truth_readiness(
            artifact=_ideal_artifact(), sources=_ideal_sources(), facts=[bad_fact]
        )
        assert result.eligible_for_truth_adapter is False
        assert "no_valid_facts" in result.reason_codes

    def test_fact_count_zero_when_all_invalid(self):
        bad_fact = {"fact_kind": "earnings_event", "structured_payload": {}}
        result = evaluate_artifact_truth_readiness(
            artifact=_ideal_artifact(), sources=_ideal_sources(), facts=[bad_fact]
        )
        assert result.fact_count == 0


# ── Acceptance criterion 4: UNKNOWN confidence ───────────────────────────────

class TestUnknownConfidenceIneligible:
    """AC-4: confidence_or_trust_level=UNKNOWN/empty/null => ineligible."""

    @pytest.mark.parametrize("bad_confidence", [
        "UNKNOWN", "unknown", "Unknown", "", None,
    ])
    def test_bad_confidence_ineligible(self, bad_confidence):
        art = {**_ideal_artifact(), "confidence_or_trust_level": bad_confidence}
        result = evaluate_artifact_truth_readiness(
            artifact=art, sources=_ideal_sources(), facts=_ideal_facts()
        )
        assert result.eligible_for_truth_adapter is False
        assert "unknown_or_invalid_confidence" in result.reason_codes

    @pytest.mark.parametrize("valid_confidence", ["HIGH", "MEDIUM", "LOW"])
    def test_valid_confidence_passes(self, valid_confidence):
        art = {**_ideal_artifact(), "confidence_or_trust_level": valid_confidence}
        result = evaluate_artifact_truth_readiness(
            artifact=art, sources=_ideal_sources(), facts=_ideal_facts()
        )
        assert "unknown_or_invalid_confidence" not in result.reason_codes

    def test_supported_confidence_values_match_contract(self):
        assert "HIGH" in VALID_CONFIDENCE_LEVELS
        assert "MEDIUM" in VALID_CONFIDENCE_LEVELS
        assert "LOW" in VALID_CONFIDENCE_LEVELS
        assert "UNKNOWN" not in VALID_CONFIDENCE_LEVELS


# ── Acceptance criterion 5: UNKNOWN freshness ────────────────────────────────

class TestUnknownFreshnessIneligible:
    """AC-5: freshness_status=UNKNOWN/empty/null => ineligible."""

    @pytest.mark.parametrize("bad_freshness", [
        "UNKNOWN", "unknown", "Unknown", "", None,
    ])
    def test_bad_freshness_ineligible(self, bad_freshness):
        art = {**_ideal_artifact(), "freshness_status": bad_freshness}
        result = evaluate_artifact_truth_readiness(
            artifact=art, sources=_ideal_sources(), facts=_ideal_facts()
        )
        assert result.eligible_for_truth_adapter is False
        assert "unknown_or_invalid_freshness" in result.reason_codes

    @pytest.mark.parametrize("valid_freshness", ["FRESH", "STALE"])
    def test_valid_freshness_passes(self, valid_freshness):
        art = {**_ideal_artifact(), "freshness_status": valid_freshness}
        result = evaluate_artifact_truth_readiness(
            artifact=art, sources=_ideal_sources(), facts=_ideal_facts()
        )
        assert "unknown_or_invalid_freshness" not in result.reason_codes

    def test_supported_freshness_values_match_contract(self):
        assert "FRESH" in VALID_FRESHNESS_STATUSES
        assert "STALE" in VALID_FRESHNESS_STATUSES
        assert "UNKNOWN" not in VALID_FRESHNESS_STATUSES


# ── Acceptance criterion 6: Invalidated artifact ─────────────────────────────

class TestInvalidatedArtifactIneligible:
    """AC-6: Artifact with invalidated_at not None => ineligible."""

    def test_invalidated_at_set_ineligible(self):
        art = {**_ideal_artifact(), "invalidated_at": _past_iso(1)}
        result = evaluate_artifact_truth_readiness(
            artifact=art, sources=_ideal_sources(), facts=_ideal_facts()
        )
        assert result.eligible_for_truth_adapter is False
        assert "invalidated" in result.reason_codes

    def test_invalidated_at_none_passes(self):
        art = {**_ideal_artifact(), "invalidated_at": None}
        result = evaluate_artifact_truth_readiness(
            artifact=art, sources=_ideal_sources(), facts=_ideal_facts()
        )
        assert "invalidated" not in result.reason_codes


# ── Acceptance criterion 7: Expired artifact ─────────────────────────────────

class TestExpiredArtifactIneligible:
    """AC-7: Artifact with expires_at in the past => ineligible."""

    def test_past_expires_at_ineligible(self):
        art = {**_ideal_artifact(), "expires_at": _past_iso(1)}
        result = evaluate_artifact_truth_readiness(
            artifact=art, sources=_ideal_sources(), facts=_ideal_facts()
        )
        assert result.eligible_for_truth_adapter is False
        assert "expired" in result.reason_codes

    def test_future_expires_at_passes(self):
        art = {**_ideal_artifact(), "expires_at": _future_iso(30)}
        result = evaluate_artifact_truth_readiness(
            artifact=art, sources=_ideal_sources(), facts=_ideal_facts()
        )
        assert "expired" not in result.reason_codes

    def test_null_expires_at_passes(self):
        art = {**_ideal_artifact(), "expires_at": None}
        result = evaluate_artifact_truth_readiness(
            artifact=art, sources=_ideal_sources(), facts=_ideal_facts()
        )
        assert "expired" not in result.reason_codes
        assert "expires_at_malformed" not in result.reason_codes

    def test_malformed_expires_at_fail_closed(self):
        art = {**_ideal_artifact(), "expires_at": "not-a-date"}
        result = evaluate_artifact_truth_readiness(
            artifact=art, sources=_ideal_sources(), facts=_ideal_facts()
        )
        assert result.eligible_for_truth_adapter is False
        assert "expires_at_malformed" in result.reason_codes


# ── Acceptance criterion 8: Forbidden payload key ────────────────────────────

class TestForbiddenPayloadKeyIneligible:
    """AC-8: Forbidden payload key anywhere nested => ineligible."""

    @pytest.mark.parametrize("forbidden_key", [
        "final_action", "buy", "sell", "trim", "hold",
        "final_conviction", "final_allocation",
        "action", "recommendation", "target_price", "allocation",
    ])
    def test_top_level_forbidden_key_ineligible(self, forbidden_key):
        art = {**_ideal_artifact(), "payload": {forbidden_key: "some_value"}}
        result = evaluate_artifact_truth_readiness(
            artifact=art, sources=_ideal_sources(), facts=_ideal_facts()
        )
        assert result.eligible_for_truth_adapter is False
        assert result.forbidden_payload_violation is True
        assert any("forbidden_payload_key" in code for code in result.reason_codes)

    def test_nested_forbidden_key_ineligible(self):
        art = {
            **_ideal_artifact(),
            "payload": {
                "summary": "safe text",
                "nested": {"deep": {"FINAL_ACTION": "BUY"}},
            },
        }
        result = evaluate_artifact_truth_readiness(
            artifact=art, sources=_ideal_sources(), facts=_ideal_facts()
        )
        assert result.eligible_for_truth_adapter is False
        assert result.forbidden_payload_violation is True

    def test_case_insensitive_forbidden_key_ineligible(self):
        art = {**_ideal_artifact(), "payload": {"FINAL_ACTION": "BUY"}}
        result = evaluate_artifact_truth_readiness(
            artifact=art, sources=_ideal_sources(), facts=_ideal_facts()
        )
        assert result.eligible_for_truth_adapter is False
        assert result.forbidden_payload_violation is True

    def test_forbidden_key_in_fact_payload_ineligible(self):
        # Fact has a forbidden key AND a missing source link — both are contract violations.
        bad_fact = {
            "id": "fact-bad",
            "fact_kind": "earnings_event",
            "structured_payload": {"buy": "strong signal"},  # forbidden key
            "source_id": "src-001",  # linked, but payload is forbidden
        }
        result = evaluate_artifact_truth_readiness(
            artifact=_ideal_artifact(),
            sources=_ideal_sources(),
            facts=[bad_fact],
        )
        assert result.eligible_for_truth_adapter is False
        assert result.forbidden_payload_violation is True
        assert any("forbidden_fact_payload_key" in code for code in result.reason_codes)

    def test_clean_payload_no_violation(self):
        result = evaluate_artifact_truth_readiness(
            artifact=_ideal_artifact(),
            sources=_ideal_sources(),
            facts=_ideal_facts(),
        )
        assert result.forbidden_payload_violation is False


# ── Acceptance criterion 9: Unsupported artifact_type ────────────────────────

class TestUnsupportedArtifactTypeIneligible:
    """AC-9: Unsupported artifact_type => ineligible."""

    @pytest.mark.parametrize("bad_type", [
        "unknown_type", "buy_signal", "allocation_advisor", "", None,
        "CATALYST_WINDOW",  # wrong case is unsupported (exact match required)
    ])
    def test_unsupported_type_ineligible(self, bad_type):
        art = {**_ideal_artifact(), "artifact_type": bad_type}
        result = evaluate_artifact_truth_readiness(
            artifact=art, sources=_ideal_sources(), facts=_ideal_facts()
        )
        assert result.eligible_for_truth_adapter is False
        assert "unsupported_artifact_type" in result.reason_codes

    def test_supported_type_passes(self):
        for atype in SUPPORTED_ARTIFACT_TYPES:
            art = {**_ideal_artifact(), "artifact_type": atype}
            result = evaluate_artifact_truth_readiness(
                artifact=art, sources=_ideal_sources(), facts=_ideal_facts()
            )
            assert "unsupported_artifact_type" not in result.reason_codes, (
                f"Expected {atype} to be supported but got unsupported_artifact_type"
            )


# ── Acceptance criterion 10: Unsupported skill_pack ──────────────────────────

class TestUnsupportedSkillPackIneligible:
    """AC-10: Unsupported skill_pack => ineligible."""

    @pytest.mark.parametrize("bad_pack", [
        "unknown_pack", "llm_advisor", "gpt_trader", "", None,
        "EARNINGS_REVIEWER",  # wrong case is unsupported
    ])
    def test_unsupported_skill_pack_ineligible(self, bad_pack):
        art = {**_ideal_artifact(), "skill_pack": bad_pack}
        result = evaluate_artifact_truth_readiness(
            artifact=art, sources=_ideal_sources(), facts=_ideal_facts()
        )
        assert result.eligible_for_truth_adapter is False
        assert "unsupported_skill_pack" in result.reason_codes

    def test_supported_skill_pack_passes(self):
        for sp in SUPPORTED_SKILL_PACKS:
            art = {**_ideal_artifact(), "skill_pack": sp}
            result = evaluate_artifact_truth_readiness(
                artifact=art, sources=_ideal_sources(), facts=_ideal_facts()
            )
            assert "unsupported_skill_pack" not in result.reason_codes, (
                f"Expected {sp} to be supported but got unsupported_skill_pack"
            )


# ── Acceptance criterion 11: Malformed/null fields fail closed ────────────────

class TestMalformedFieldsFailClosed:
    """AC-11: Malformed, null, or non-dict artifacts must fail closed."""

    def test_none_artifact_fail_closed(self):
        result = evaluate_artifact_truth_readiness(artifact=None)
        assert result.eligible_for_truth_adapter is False
        assert result.fail_closed is True
        assert result.eligible_for_decision_consumption is False

    def test_non_dict_artifact_fail_closed(self):
        result = evaluate_artifact_truth_readiness(artifact="not-a-dict")
        assert result.eligible_for_truth_adapter is False
        assert result.fail_closed is True

    def test_empty_dict_artifact_fail_closed(self):
        result = evaluate_artifact_truth_readiness(artifact={})
        assert result.eligible_for_truth_adapter is False
        assert result.fail_closed is True
        assert len(result.reason_codes) > 0

    def test_non_dict_source_excluded_gracefully(self):
        result = evaluate_artifact_truth_readiness(
            artifact=_ideal_artifact(),
            sources=["not-a-dict", 42, None],  # all malformed
            facts=_ideal_facts(),
        )
        assert result.eligible_for_truth_adapter is False
        assert "no_valid_sources" in result.reason_codes
        assert result.source_count == 0

    def test_non_dict_fact_excluded_gracefully(self):
        result = evaluate_artifact_truth_readiness(
            artifact=_ideal_artifact(),
            sources=_ideal_sources(),
            facts=["not-a-dict", 42, None],  # all malformed
        )
        assert result.eligible_for_truth_adapter is False
        assert "no_valid_facts" in result.reason_codes
        assert result.fact_count == 0

    def test_non_active_artifact_fail_closed(self):
        art = {**_ideal_artifact(), "is_active": False}
        result = evaluate_artifact_truth_readiness(
            artifact=art, sources=_ideal_sources(), facts=_ideal_facts()
        )
        assert result.eligible_for_truth_adapter is False
        assert "not_active" in result.reason_codes

    def test_is_active_none_treated_as_not_active(self):
        art = {**_ideal_artifact(), "is_active": None}
        result = evaluate_artifact_truth_readiness(
            artifact=art, sources=_ideal_sources(), facts=_ideal_facts()
        )
        assert "not_active" in result.reason_codes

    def test_multiple_failures_all_reported(self):
        # UNKNOWN confidence + UNKNOWN freshness + no sources = 3+ reason_codes
        result = evaluate_artifact_truth_readiness(artifact=_phase4_artifact(), sources=[], facts=[])
        assert len(result.reason_codes) >= 3

    def test_fact_with_source_id_but_no_matching_source(self):
        fact_with_source = {
            "id": "fact-001",
            "fact_kind": "earnings_event",
            "structured_payload": {"period": "Q1-2026"},
            "source_id": "src-does-not-exist",
        }
        result = evaluate_artifact_truth_readiness(
            artifact=_ideal_artifact(),
            sources=_ideal_sources(),
            facts=[fact_with_source],
        )
        assert result.eligible_for_truth_adapter is False
        assert "fact_source_not_found" in result.reason_codes

    def test_fact_with_valid_source_id_passes(self):
        # _ideal_sources() includes a provenance handle, so "src-001" is in valid_source_ids.
        fact_linked = {
            "id": "fact-002",
            "fact_kind": "earnings_event",
            "structured_payload": {"period": "Q1-2026"},
            "source_id": "src-001",  # matches _ideal_sources()[0]["id"]
        }
        result = evaluate_artifact_truth_readiness(
            artifact=_ideal_artifact(),
            sources=_ideal_sources(),
            facts=[fact_linked],
        )
        assert "fact_source_not_found" not in result.reason_codes
        assert "fact_missing_source_link" not in result.reason_codes


# ── Acceptance criterion 12: Theoretically well-formed artifact ───────────────

class TestIdealArtifactEligibleForTruthAdapter:
    """AC-12: Well-formed sourced artifact: eligible_for_truth_adapter=True,
    eligible_for_decision_consumption=False (Phase 5 invariant).
    """

    def test_ideal_artifact_eligible_for_truth_adapter(self):
        result = evaluate_artifact_truth_readiness(
            artifact=_ideal_artifact(),
            sources=_ideal_sources(),
            facts=_ideal_facts(),
        )
        assert result.eligible_for_truth_adapter is True

    def test_eligible_for_decision_consumption_always_false(self):
        result = evaluate_artifact_truth_readiness(
            artifact=_ideal_artifact(),
            sources=_ideal_sources(),
            facts=_ideal_facts(),
        )
        assert result.eligible_for_decision_consumption is False

    def test_fail_closed_always_true_even_when_eligible(self):
        result = evaluate_artifact_truth_readiness(
            artifact=_ideal_artifact(),
            sources=_ideal_sources(),
            facts=_ideal_facts(),
        )
        assert result.fail_closed is True

    def test_db_promotion_blocked_always_true(self):
        result = evaluate_artifact_truth_readiness(
            artifact=_ideal_artifact(),
            sources=_ideal_sources(),
            facts=_ideal_facts(),
        )
        assert result.safe_for_decision_db_promotion_blocked is True

    def test_no_reason_codes_when_eligible(self):
        result = evaluate_artifact_truth_readiness(
            artifact=_ideal_artifact(),
            sources=_ideal_sources(),
            facts=_ideal_facts(),
        )
        assert result.reason_codes == []

    def test_source_count_reflects_valid_sources(self):
        result = evaluate_artifact_truth_readiness(
            artifact=_ideal_artifact(),
            sources=_ideal_sources(),
            facts=_ideal_facts(),
        )
        assert result.source_count == 1

    def test_fact_count_reflects_valid_facts(self):
        result = evaluate_artifact_truth_readiness(
            artifact=_ideal_artifact(),
            sources=_ideal_sources(),
            facts=_ideal_facts(),
        )
        assert result.fact_count == 1

    def test_metadata_populated(self):
        result = evaluate_artifact_truth_readiness(
            artifact=_ideal_artifact(),
            sources=_ideal_sources(),
            facts=_ideal_facts(),
        )
        assert result.artifact_id == "art-ideal-001"
        assert result.ticker == "AAPL"
        assert result.artifact_type == "catalyst_window"
        assert result.skill_pack == "earnings_reviewer"
        assert result.confidence_or_trust_level == "MEDIUM"
        assert result.freshness_status == "FRESH"
        assert result.forbidden_payload_violation is False

    def test_stale_freshness_still_eligible(self):
        art = {**_ideal_artifact(), "freshness_status": "STALE"}
        result = evaluate_artifact_truth_readiness(
            artifact=art, sources=_ideal_sources(), facts=_ideal_facts()
        )
        assert result.eligible_for_truth_adapter is True
        assert result.eligible_for_decision_consumption is False

    def test_high_confidence_eligible(self):
        art = {**_ideal_artifact(), "confidence_or_trust_level": "HIGH"}
        result = evaluate_artifact_truth_readiness(
            artifact=art, sources=_ideal_sources(), facts=_ideal_facts()
        )
        assert result.eligible_for_truth_adapter is True

    def test_multiple_sources_and_facts_eligible(self):
        # Both sources have a provenance handle; both facts are source-linked.
        sources = [
            {"id": "src-001", "source_kind": "earnings_report", "provider_name": "sec_edgar",
             "source_id": "aapl-q1-2026-10q"},
            {"id": "src-002", "source_kind": "analyst_report", "provider_name": "bloomberg",
             "source_url": "https://example.com/report"},
        ]
        facts = [
            {"id": "f1", "fact_kind": "earnings_event", "structured_payload": {"period": "Q1"},
             "source_id": "src-001"},
            {"id": "f2", "fact_kind": "guidance_update", "structured_payload": {"note": "raised"},
             "source_id": "src-002"},
        ]
        result = evaluate_artifact_truth_readiness(
            artifact=_ideal_artifact(), sources=sources, facts=facts
        )
        assert result.eligible_for_truth_adapter is True
        assert result.eligible_for_decision_consumption is False
        assert result.source_count == 2
        assert result.fact_count == 2


# ── Acceptance criterion 13: No forbidden imports ─────────────────────────────

class TestNoForbiddenImports:
    """AC-13: Module must not import decide(), IntelV3Service, or frontend code."""

    @staticmethod
    def _import_lines() -> list[str]:
        import app.services.intelligence.research_workers.artifact_truth_readiness as mod
        return [
            line.strip() for line in inspect.getsource(mod).split("\n")
            if line.strip().startswith("import ") or line.strip().startswith("from ")
        ]

    def test_does_not_import_decision_policy(self):
        import_lines = self._import_lines()
        assert not any("decision_policy_v1" in line for line in import_lines), (
            "artifact_truth_readiness.py must not import decision_policy_v1"
        )

    def test_does_not_import_intel_v3_service(self):
        import_lines = self._import_lines()
        assert not any("intel_v3_service" in line.lower() for line in import_lines), (
            "artifact_truth_readiness.py must not import IntelV3Service"
        )

    def test_does_not_import_frontend(self):
        import_lines = self._import_lines()
        assert not any("frontend" in line for line in import_lines), (
            "artifact_truth_readiness.py must not import frontend code"
        )

    def test_does_not_import_decide_function(self):
        import_lines = self._import_lines()
        assert not any("decide" in line for line in import_lines), (
            "artifact_truth_readiness.py must not import or call decide()"
        )

    def test_does_not_import_recommendation_engine(self):
        import_lines = self._import_lines()
        assert not any("recommendation_engine" in line for line in import_lines), (
            "artifact_truth_readiness.py must not import recommendation_engine"
        )


# ── Acceptance criterion 14: No snapshot/action changes ──────────────────────

class TestNoSnapshotOrActionChanges:
    """AC-14: Structural guard — module never imports or touches intel_v3_snapshots."""

    def test_module_does_not_reference_intel_v3_snapshots(self):
        import app.services.intelligence.research_workers.artifact_truth_readiness as mod
        # Only check import lines and table() calls — docstrings may mention it.
        lines = inspect.getsource(mod).split("\n")
        active_lines = [
            line.strip() for line in lines
            if (
                line.strip().startswith("import ")
                or line.strip().startswith("from ")
                or '.table("' in line
            )
        ]
        assert not any("intel_v3_snapshots" in line for line in active_lines)

    def test_module_does_not_import_supabase_client(self):
        import app.services.intelligence.research_workers.artifact_truth_readiness as mod
        src = inspect.getsource(mod)
        # Pure function — no DB dependency
        assert "supabase" not in src.lower()

    def test_evaluate_returns_dataclass_not_db_rows(self):
        result = evaluate_artifact_truth_readiness(
            artifact=_ideal_artifact(),
            sources=_ideal_sources(),
            facts=_ideal_facts(),
        )
        assert isinstance(result, ArtifactReadinessResult)

    def test_evaluate_does_not_write_to_artifact(self):
        art = _ideal_artifact()
        original_id = art["id"]
        evaluate_artifact_truth_readiness(artifact=art, sources=_ideal_sources(), facts=_ideal_facts())
        # Artifact dict must be unchanged after evaluation
        assert art["id"] == original_id
        assert art["confidence_or_trust_level"] == "MEDIUM"


# ── Result invariants ─────────────────────────────────────────────────────────

class TestResultInvariants:
    """Invariants that must hold for every result, regardless of eligibility."""

    def test_fail_closed_invariant_ineligible(self):
        for art in [_phase4_artifact(), {}, None]:
            result = evaluate_artifact_truth_readiness(artifact=art)
            assert result.fail_closed is True, f"fail_closed must always be True, got False for {art!r}"

    def test_fail_closed_invariant_eligible(self):
        result = evaluate_artifact_truth_readiness(
            artifact=_ideal_artifact(), sources=_ideal_sources(), facts=_ideal_facts()
        )
        assert result.fail_closed is True

    def test_decision_consumption_invariant(self):
        for art in [_phase4_artifact(), _ideal_artifact(), {}, None]:
            sources = _ideal_sources() if isinstance(art, dict) and art.get("id") == "art-ideal-001" else []
            facts = _ideal_facts() if isinstance(art, dict) and art.get("id") == "art-ideal-001" else []
            result = evaluate_artifact_truth_readiness(artifact=art, sources=sources, facts=facts)
            assert result.eligible_for_decision_consumption is False, (
                "eligible_for_decision_consumption must always be False in Phase 5"
            )

    def test_db_promotion_blocked_invariant(self):
        for art in [_phase4_artifact(), _ideal_artifact(), {}, None]:
            result = evaluate_artifact_truth_readiness(artifact=art)
            assert result.safe_for_decision_db_promotion_blocked is True

    def test_reason_codes_empty_when_eligible(self):
        result = evaluate_artifact_truth_readiness(
            artifact=_ideal_artifact(), sources=_ideal_sources(), facts=_ideal_facts()
        )
        assert result.eligible_for_truth_adapter is True
        assert result.reason_codes == []

    def test_reason_codes_nonempty_when_ineligible(self):
        result = evaluate_artifact_truth_readiness(artifact=_phase4_artifact())
        assert result.eligible_for_truth_adapter is False
        assert len(result.reason_codes) > 0


# ── Hardening H1/H2: Explicit fact source linkage required ────────────────────

class TestFactSourceLinkageRequired:
    """H1/H2: Every valid fact must have a non-empty source_id linked to a valid source."""

    def test_fact_source_id_none_fails(self):
        fact = {
            "id": "fact-x",
            "fact_kind": "earnings_event",
            "structured_payload": {"period": "Q1-2026"},
            "source_id": None,
        }
        result = evaluate_artifact_truth_readiness(
            artifact=_ideal_artifact(), sources=_ideal_sources(), facts=[fact]
        )
        assert result.eligible_for_truth_adapter is False
        assert "fact_missing_source_link" in result.reason_codes

    def test_fact_source_id_empty_string_fails(self):
        fact = {
            "id": "fact-x",
            "fact_kind": "earnings_event",
            "structured_payload": {"period": "Q1-2026"},
            "source_id": "",
        }
        result = evaluate_artifact_truth_readiness(
            artifact=_ideal_artifact(), sources=_ideal_sources(), facts=[fact]
        )
        assert result.eligible_for_truth_adapter is False
        assert "fact_missing_source_link" in result.reason_codes

    def test_fact_source_id_whitespace_only_fails(self):
        fact = {
            "id": "fact-x",
            "fact_kind": "earnings_event",
            "structured_payload": {"period": "Q1-2026"},
            "source_id": "   ",
        }
        result = evaluate_artifact_truth_readiness(
            artifact=_ideal_artifact(), sources=_ideal_sources(), facts=[fact]
        )
        assert result.eligible_for_truth_adapter is False
        assert "fact_missing_source_link" in result.reason_codes

    def test_fact_source_id_absent_fails(self):
        fact = {
            "id": "fact-x",
            "fact_kind": "earnings_event",
            "structured_payload": {"period": "Q1-2026"},
            # source_id key entirely absent
        }
        result = evaluate_artifact_truth_readiness(
            artifact=_ideal_artifact(), sources=_ideal_sources(), facts=[fact]
        )
        assert result.eligible_for_truth_adapter is False
        assert "fact_missing_source_link" in result.reason_codes

    def test_fact_source_id_not_in_valid_sources_fails(self):
        fact = {
            "id": "fact-x",
            "fact_kind": "earnings_event",
            "structured_payload": {"period": "Q1-2026"},
            "source_id": "src-unknown-xyz",
        }
        result = evaluate_artifact_truth_readiness(
            artifact=_ideal_artifact(), sources=_ideal_sources(), facts=[fact]
        )
        assert result.eligible_for_truth_adapter is False
        assert "fact_source_not_found" in result.reason_codes

    def test_fact_source_id_linked_to_valid_source_passes(self):
        fact = {
            "id": "fact-x",
            "fact_kind": "earnings_event",
            "structured_payload": {"period": "Q1-2026"},
            "source_id": "src-001",  # matches _ideal_sources()[0]["id"]
        }
        result = evaluate_artifact_truth_readiness(
            artifact=_ideal_artifact(), sources=_ideal_sources(), facts=[fact]
        )
        assert "fact_missing_source_link" not in result.reason_codes
        assert "fact_source_not_found" not in result.reason_codes
        assert result.eligible_for_truth_adapter is True
        assert result.eligible_for_decision_consumption is False

    def test_first_fact_without_link_blocks_eligibility(self):
        # If even one valid fact has no source link, the whole artifact is ineligible.
        facts = [
            {"id": "f1", "fact_kind": "earnings_event", "structured_payload": {"period": "Q1"},
             "source_id": "src-001"},
            {"id": "f2", "fact_kind": "guidance_update", "structured_payload": {"note": "raised"},
             "source_id": None},  # missing link
        ]
        result = evaluate_artifact_truth_readiness(
            artifact=_ideal_artifact(), sources=_ideal_sources(), facts=facts
        )
        assert result.eligible_for_truth_adapter is False
        assert "fact_missing_source_link" in result.reason_codes

    def test_fact_linked_to_invalid_source_fails(self):
        # Source lacks provenance handle → not a valid source → fact_source_not_found.
        source_no_provenance = {
            "id": "src-noprov",
            "source_kind": "earnings_report",
            "provider_name": "sec_edgar",
            # no source_url / source_id / source_hash / section_reference
        }
        fact = {
            "id": "fact-x",
            "fact_kind": "earnings_event",
            "structured_payload": {"period": "Q1"},
            "source_id": "src-noprov",
        }
        result = evaluate_artifact_truth_readiness(
            artifact=_ideal_artifact(), sources=[source_no_provenance], facts=[fact]
        )
        assert result.eligible_for_truth_adapter is False
        # source is invalid → valid_source_ids is empty → fact_source_not_found
        assert "fact_source_not_found" in result.reason_codes


# ── Hardening H3/H4: Source provenance handle required ───────────────────────

class TestSourceProvenanceRequired:
    """H3/H4: Valid source must have source_kind + provider_name + ≥1 provenance handle."""

    def test_source_without_any_provenance_handle_ineligible(self):
        source = {
            "id": "src-bad",
            "source_kind": "earnings_report",
            "provider_name": "sec_edgar",
            # no source_url / source_id / source_hash / section_reference
        }
        result = evaluate_artifact_truth_readiness(
            artifact=_ideal_artifact(), sources=[source], facts=_ideal_facts()
        )
        assert result.eligible_for_truth_adapter is False
        assert "no_valid_sources" in result.reason_codes

    @pytest.mark.parametrize("provenance_key,provenance_val", [
        ("source_url", "https://www.sec.gov/10q"),
        ("source_id", "aapl-q1-2026-10q"),
        ("source_hash", "sha256:abc123def456"),
        ("section_reference", "Part I Item 1"),
    ])
    def test_each_provenance_handle_independently_valid(self, provenance_key, provenance_val):
        source = {
            "id": "src-001",
            "source_kind": "earnings_report",
            "provider_name": "sec_edgar",
            provenance_key: provenance_val,
        }
        fact = {
            "id": "fact-001",
            "fact_kind": "earnings_event",
            "structured_payload": {"period": "Q1-2026"},
            "source_id": "src-001",
        }
        result = evaluate_artifact_truth_readiness(
            artifact=_ideal_artifact(), sources=[source], facts=[fact]
        )
        assert "no_valid_sources" not in result.reason_codes
        assert result.eligible_for_truth_adapter is True
        assert result.eligible_for_decision_consumption is False

    def test_source_with_empty_provenance_handles_ineligible(self):
        source = {
            "id": "src-bad",
            "source_kind": "earnings_report",
            "provider_name": "sec_edgar",
            "source_url": "",
            "source_id": "  ",
            "source_hash": None,
            "section_reference": "",
        }
        result = evaluate_artifact_truth_readiness(
            artifact=_ideal_artifact(), sources=[source], facts=_ideal_facts()
        )
        assert result.eligible_for_truth_adapter is False
        assert "no_valid_sources" in result.reason_codes

    def test_source_count_zero_when_no_provenance(self):
        source = {
            "id": "src-bad",
            "source_kind": "earnings_report",
            "provider_name": "sec_edgar",
        }
        result = evaluate_artifact_truth_readiness(
            artifact=_ideal_artifact(), sources=[source], facts=_ideal_facts()
        )
        assert result.source_count == 0


# ── Hardening H5: safe_for_decision=True fails ────────────────────────────────

class TestUnexpectedSafeForDecisionTrue:
    """H5: artifact.safe_for_decision=True must be rejected as unexpected/unsafe input."""

    def test_safe_for_decision_true_ineligible(self):
        art = {**_ideal_artifact(), "safe_for_decision": True}
        result = evaluate_artifact_truth_readiness(
            artifact=art, sources=_ideal_sources(), facts=_ideal_facts()
        )
        assert result.eligible_for_truth_adapter is False
        assert "unexpected_safe_for_decision_true" in result.reason_codes

    def test_safe_for_decision_false_passes(self):
        art = {**_ideal_artifact(), "safe_for_decision": False}
        result = evaluate_artifact_truth_readiness(
            artifact=art, sources=_ideal_sources(), facts=_ideal_facts()
        )
        assert "unexpected_safe_for_decision_true" not in result.reason_codes
        assert result.eligible_for_truth_adapter is True

    def test_safe_for_decision_absent_passes(self):
        # Field absent entirely — should not add reason code.
        art = {k: v for k, v in _ideal_artifact().items() if k != "safe_for_decision"}
        result = evaluate_artifact_truth_readiness(
            artifact=art, sources=_ideal_sources(), facts=_ideal_facts()
        )
        assert "unexpected_safe_for_decision_true" not in result.reason_codes
        assert result.eligible_for_truth_adapter is True

    def test_safe_for_decision_none_passes(self):
        art = {**_ideal_artifact(), "safe_for_decision": None}
        result = evaluate_artifact_truth_readiness(
            artifact=art, sources=_ideal_sources(), facts=_ideal_facts()
        )
        assert "unexpected_safe_for_decision_true" not in result.reason_codes
        assert result.eligible_for_truth_adapter is True

    def test_eligible_for_decision_consumption_still_false_even_if_safe_for_decision_true(self):
        # Even if safe_for_decision=True somehow appeared, consumption is still False.
        art = {**_ideal_artifact(), "safe_for_decision": True}
        result = evaluate_artifact_truth_readiness(
            artifact=art, sources=_ideal_sources(), facts=_ideal_facts()
        )
        assert result.eligible_for_decision_consumption is False

    def test_db_promotion_blocked_always_true_regardless_of_safe_for_decision(self):
        for sfd in [True, False, None]:
            art = {**_ideal_artifact(), "safe_for_decision": sfd}
            result = evaluate_artifact_truth_readiness(
                artifact=art, sources=_ideal_sources(), facts=_ideal_facts()
            )
            assert result.safe_for_decision_db_promotion_blocked is True
