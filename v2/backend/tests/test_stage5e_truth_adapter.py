"""Stage 5E — Artifact Truth Adapter v1 tests.

Proves all acceptance criteria for the deterministic truth/usability adapter
that classifies research artifacts based on already-computed enrichment metadata.

Acceptance criteria verified:
  1. All six usability labels are reachable from deterministic fixture inputs.
  2. Missing metadata (None inputs) returns NOT_EVALUABLE.
  3. Malformed metadata does not crash the adapter or the writer path.
  4. Contradiction suppression beats completeness/source positives (SUPPRESSED_CONTRADICTED
     wins when both contradiction AND unknown-source or incompleteness apply).
  5. Unknown source suppression is deterministic (SUPPRESSED_UNKNOWN_SOURCE when
     credibility is_insufficient=True and no contradictions).
  6. Incomplete evidence suppression is deterministic (SUPPRESSED_INCOMPLETE for THIN).
  7. USABLE_WITH_LIMITATIONS is distinct from USABLE (PARTIAL vs COMPLETE band).
  8. Earnings reviewer artifacts written through ResearchArtifactServiceV1 now
     include 'truth_usability_assessment' in the payload.
  9. Stage 5A–5E0 behavior remains intact: safe_for_decision=False, no
     intel_v3_snapshots writes.
 10. assess_artifact_usability never imports decide().
 11. truth_usability_assessment has expected shape (adapter_version, usability_label,
     is_usable, suppression_reason, limitations, no_guessing).

No production Supabase access — all DB interactions use FakeSupabaseClient.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, List, Optional

import pytest

from app.services.intelligence.v3.artifact_truth_adapter_v1 import (
    ARTIFACT_TRUTH_ADAPTER_VERSION,
    ArtifactUsabilityAssessment,
    ArtifactUsabilityLabel,
    assess_artifact_usability,
)
from app.services.intelligence.v3.contradiction_detector_v1 import (
    detect_contradictions,
)
from app.services.intelligence.v3.evidence_completeness_scorer_v1 import (
    score_evidence_completeness,
)
from app.services.intelligence.v3.source_credibility_registry_v1 import (
    assess_artifact_sources,
)
from app.services.intelligence.research_workers.contracts import (
    FactRecord,
    SourceRecord,
    WorkerInput,
)
from app.services.intelligence.research_workers.runner import run_earnings_reviewer_dark
from app.config import Settings


# ── Fixtures: SourceRecord / FactRecord builders ──────────────────────────────


def _sec_source() -> SourceRecord:
    return SourceRecord(
        source_kind="sec_filing",
        provider_name="sec_edgar",
        source_url="https://sec.gov/Archives/test/0000000001-24-000001.htm",
        section_reference="R1",
    )


def _unknown_source() -> SourceRecord:
    return SourceRecord(
        source_kind="other",
        provider_name="unknown_provider",
    )


def _news_source() -> SourceRecord:
    return SourceRecord(
        source_kind="news",
        provider_name="news_vendor",
        source_url="https://news.example.com/story/1",
    )


def _comparable_fact(claim_key: str, value: float, period: str = "2024-Q1") -> FactRecord:
    return FactRecord(
        fact_kind="metric",
        structured_payload={"claim_key": claim_key, "value": value},
        is_quote_grounded=True,
        period=period,
        as_of="2024-01-31",
    )


def _unquoted_comparable_fact(claim_key: str, value: float, period: str = "2024-Q1") -> FactRecord:
    return FactRecord(
        fact_kind="metric",
        structured_payload={"claim_key": claim_key, "value": value},
        is_quote_grounded=False,
        period=period,
        as_of="2024-01-31",
    )


def _noncomparable_fact() -> FactRecord:
    return FactRecord(
        fact_kind="narrative",
        structured_payload={"summary": "Revenue grew"},
        is_quote_grounded=False,
    )


# ── Helpers for building assessment objects from fixtures ─────────────────────


def _assess(sources, facts) -> tuple:
    cred = assess_artifact_sources(sources)
    contr = detect_contradictions(facts)
    comp = score_evidence_completeness(
        sources=sources, facts=facts,
        credibility_assessment=cred,
        contradiction_assessment=contr,
    )
    return cred, contr, comp


# ── Criterion 1: All six labels are reachable ─────────────────────────────────


class TestAllSixLabelsReachable:

    def test_usable_label(self) -> None:
        """USABLE: sec_filing source, comparable+quote-grounded fact, no contradictions."""
        sources = [_sec_source()]
        facts = [_comparable_fact("revenue", 1000.0)]
        cred, contr, comp = _assess(sources, facts)
        result = assess_artifact_usability(cred, contr, comp)
        assert result.usability_label == ArtifactUsabilityLabel.USABLE.value
        assert result.is_usable is True
        assert result.suppression_reason is None

    def test_usable_with_limitations_label(self) -> None:
        """USABLE_WITH_LIMITATIONS: sec_filing, unquoted comparable fact → PARTIAL band."""
        sources = [_sec_source()]
        facts = [_unquoted_comparable_fact("revenue", 1000.0)]
        cred, contr, comp = _assess(sources, facts)
        assert comp.completeness_band == "PARTIAL"
        result = assess_artifact_usability(cred, contr, comp)
        assert result.usability_label == ArtifactUsabilityLabel.USABLE_WITH_LIMITATIONS.value
        assert result.is_usable is True
        assert result.suppression_reason is None

    def test_suppressed_incomplete_label(self) -> None:
        """SUPPRESSED_INCOMPLETE: news-only source with comparable fact → THIN band."""
        sources = [_news_source()]
        facts = [_comparable_fact("revenue", 1000.0)]
        cred, contr, comp = _assess(sources, facts)
        assert comp.completeness_band == "THIN"
        result = assess_artifact_usability(cred, contr, comp)
        assert result.usability_label == ArtifactUsabilityLabel.SUPPRESSED_INCOMPLETE.value
        assert result.is_usable is False
        assert result.suppression_reason is not None

    def test_suppressed_contradicted_label(self) -> None:
        """SUPPRESSED_CONTRADICTED: two facts with same claim_key, >1% value difference."""
        sources = [_sec_source()]
        facts = [
            _comparable_fact("revenue", 1000.0),
            _comparable_fact("revenue", 2000.0),  # 100% difference → contradiction
        ]
        cred, contr, comp = _assess(sources, facts)
        assert contr.has_contradictions is True
        result = assess_artifact_usability(cred, contr, comp)
        assert result.usability_label == ArtifactUsabilityLabel.SUPPRESSED_CONTRADICTED.value
        assert result.is_usable is False
        assert "contradiction" in result.suppression_reason

    def test_suppressed_unknown_source_label(self) -> None:
        """SUPPRESSED_UNKNOWN_SOURCE: other/unknown source kind only, no contradictions."""
        sources = [_unknown_source()]
        facts = [_comparable_fact("revenue", 1000.0)]
        cred, contr, comp = _assess(sources, facts)
        assert cred.is_insufficient is True
        result = assess_artifact_usability(cred, contr, comp)
        assert result.usability_label == ArtifactUsabilityLabel.SUPPRESSED_UNKNOWN_SOURCE.value
        assert result.is_usable is False
        assert "unknown" in result.suppression_reason.lower()

    def test_not_evaluable_label_no_sources_no_facts(self) -> None:
        """NOT_EVALUABLE: no sources and no facts → completeness=NOT_EVALUABLE."""
        cred, contr, comp = _assess([], [])
        assert comp.completeness_band == "NOT_EVALUABLE"
        result = assess_artifact_usability(cred, contr, comp)
        assert result.usability_label == ArtifactUsabilityLabel.NOT_EVALUABLE.value
        assert result.is_usable is False
        assert result.suppression_reason is not None


# ── Criterion 2: Missing metadata returns NOT_EVALUABLE ──────────────────────


class TestMissingMetadataNotEvaluable:

    def test_none_credibility_returns_not_evaluable(self) -> None:
        _, contr, comp = _assess([_sec_source()], [_comparable_fact("rev", 100.0)])
        result = assess_artifact_usability(None, contr, comp)
        assert result.usability_label == ArtifactUsabilityLabel.NOT_EVALUABLE.value
        assert result.is_usable is False

    def test_none_contradiction_returns_not_evaluable(self) -> None:
        cred, _, comp = _assess([_sec_source()], [_comparable_fact("rev", 100.0)])
        result = assess_artifact_usability(cred, None, comp)
        assert result.usability_label == ArtifactUsabilityLabel.NOT_EVALUABLE.value

    def test_none_completeness_returns_not_evaluable(self) -> None:
        cred, contr, _ = _assess([_sec_source()], [_comparable_fact("rev", 100.0)])
        result = assess_artifact_usability(cred, contr, None)
        assert result.usability_label == ArtifactUsabilityLabel.NOT_EVALUABLE.value

    def test_all_none_returns_not_evaluable(self) -> None:
        result = assess_artifact_usability(None, None, None)
        assert result.usability_label == ArtifactUsabilityLabel.NOT_EVALUABLE.value
        assert result.is_usable is False
        assert "missing_enrichment_metadata" in result.suppression_reason


# ── Criterion 3: Malformed metadata does not crash the writer path ────────────


class TestMalformedMetadataDoesNotCrash:

    def test_none_inputs_do_not_raise(self) -> None:
        """assess_artifact_usability never raises on None inputs."""
        result = assess_artifact_usability(None, None, None)
        assert isinstance(result, ArtifactUsabilityAssessment)

    def test_writer_path_survives_no_sources_no_facts(self) -> None:
        """ResearchArtifactServiceV1 writer path does not crash on empty sources/facts.

        The resulting artifact must have truth_usability_assessment with NOT_EVALUABLE.
        """
        from app.services.intelligence.v3.research_artifact_service_v1 import (
            ResearchArtifactServiceV1,
        )
        from app.services.intelligence.research_workers.contracts import (
            AuditEventRecord,
            WorkerOutput,
        )

        client = _FakeSupabaseClient()
        service = ResearchArtifactServiceV1(client, user_id=str(uuid.uuid4()))
        run_id = str(uuid.uuid4())
        output = WorkerOutput(
            skill_pack="earnings_reviewer",
            artifact_type="catalyst_window",
            scope_kind="ticker",
            ticker="AAPL",
            worker_run_id=run_id,
            artifact_payload={"test_key": "test_value"},
            sources=[],
            facts=[],
            audit_events=[],
            evidence_summary_plain_english=None,
            limitations_or_missing_evidence=[],
            confidence_or_trust_level="UNKNOWN",
            freshness_status="UNKNOWN",
            input_fingerprint="test-fingerprint",
            replay_idempotency_key=f"test-key-{run_id}",
        )
        artifact_id = service.write_artifact(output)
        assert artifact_id is not None
        rows = client.artifact_inserts()
        assert rows
        payload = rows[0].get("payload", {})
        assert "truth_usability_assessment" in payload
        ta = payload["truth_usability_assessment"]
        assert ta["usability_label"] == ArtifactUsabilityLabel.NOT_EVALUABLE.value


# ── Criterion 4: Contradiction suppression beats completeness/source positives ─


class TestContradictionSuppressionPriority:

    def test_contradiction_beats_unknown_source(self) -> None:
        """SUPPRESSED_CONTRADICTED wins when both contradiction and unknown source apply.

        Scenario: unknown source + contradicting facts → SUPPRESSED_CONTRADICTED,
        not SUPPRESSED_UNKNOWN_SOURCE.
        """
        sources = [_unknown_source()]
        facts = [
            _comparable_fact("eps", 1.0),
            _comparable_fact("eps", 5.0),  # contradicts first
        ]
        cred, contr, comp = _assess(sources, facts)
        assert cred.is_insufficient is True
        assert contr.has_contradictions is True
        result = assess_artifact_usability(cred, contr, comp)
        assert result.usability_label == ArtifactUsabilityLabel.SUPPRESSED_CONTRADICTED.value

    def test_contradiction_beats_thin_completeness(self) -> None:
        """SUPPRESSED_CONTRADICTED wins when both contradiction and THIN band apply."""
        sources = [_news_source()]
        facts = [
            _comparable_fact("margin", 0.10),
            _comparable_fact("margin", 0.50),  # >1% contradiction
        ]
        cred, contr, comp = _assess(sources, facts)
        # THIN because editorial-only source, but also has contradictions
        assert contr.has_contradictions is True
        result = assess_artifact_usability(cred, contr, comp)
        assert result.usability_label == ArtifactUsabilityLabel.SUPPRESSED_CONTRADICTED.value

    def test_contradiction_beats_partial_completeness(self) -> None:
        """SUPPRESSED_CONTRADICTED wins even when completeness would be PARTIAL."""
        sources = [_sec_source()]
        facts = [
            _unquoted_comparable_fact("revenue", 100.0),
            _unquoted_comparable_fact("revenue", 200.0),  # contradiction
        ]
        cred, contr, comp = _assess(sources, facts)
        assert contr.has_contradictions is True
        result = assess_artifact_usability(cred, contr, comp)
        assert result.usability_label == ArtifactUsabilityLabel.SUPPRESSED_CONTRADICTED.value


# ── Criterion 5: Unknown source suppression is deterministic ─────────────────


class TestUnknownSourceSuppression:

    def test_unknown_source_only_no_contradictions(self) -> None:
        """SUPPRESSED_UNKNOWN_SOURCE when credibility insufficient, no contradictions."""
        sources = [_unknown_source()]
        facts = [_comparable_fact("revenue", 1000.0)]
        cred, contr, comp = _assess(sources, facts)
        assert cred.is_insufficient is True
        assert contr.has_contradictions is False
        result = assess_artifact_usability(cred, contr, comp)
        assert result.usability_label == ArtifactUsabilityLabel.SUPPRESSED_UNKNOWN_SOURCE.value
        assert result.is_usable is False

    def test_known_source_does_not_suppress_for_unknown(self) -> None:
        """SEC filing source → is_insufficient=False, no SUPPRESSED_UNKNOWN_SOURCE."""
        sources = [_sec_source()]
        facts = [_comparable_fact("revenue", 1000.0)]
        cred, contr, comp = _assess(sources, facts)
        assert cred.is_insufficient is False
        result = assess_artifact_usability(cred, contr, comp)
        assert result.usability_label != ArtifactUsabilityLabel.SUPPRESSED_UNKNOWN_SOURCE.value

    def test_suppressed_unknown_source_is_deterministic_repeated(self) -> None:
        """Same unknown-source inputs always produce SUPPRESSED_UNKNOWN_SOURCE."""
        sources = [_unknown_source()]
        facts = [_comparable_fact("revenue", 1000.0)]
        for _ in range(3):
            cred, contr, comp = _assess(sources, facts)
            result = assess_artifact_usability(cred, contr, comp)
            assert result.usability_label == ArtifactUsabilityLabel.SUPPRESSED_UNKNOWN_SOURCE.value


# ── Criterion 6: Incomplete evidence suppression is deterministic ─────────────


class TestIncompleteEvidenceSuppression:

    def test_thin_completeness_gives_suppressed_incomplete(self) -> None:
        """SUPPRESSED_INCOMPLETE when completeness=THIN and no contradictions."""
        sources = [_news_source()]
        facts = [_comparable_fact("revenue", 500.0)]
        cred, contr, comp = _assess(sources, facts)
        assert comp.completeness_band == "THIN"
        assert contr.has_contradictions is False
        assert cred.is_insufficient is False
        result = assess_artifact_usability(cred, contr, comp)
        assert result.usability_label == ArtifactUsabilityLabel.SUPPRESSED_INCOMPLETE.value
        assert result.is_usable is False

    def test_suppressed_incomplete_is_deterministic_repeated(self) -> None:
        """Same THIN inputs always produce SUPPRESSED_INCOMPLETE."""
        sources = [_news_source()]
        facts = [_comparable_fact("revenue", 500.0)]
        for _ in range(3):
            cred, contr, comp = _assess(sources, facts)
            result = assess_artifact_usability(cred, contr, comp)
            assert result.usability_label == ArtifactUsabilityLabel.SUPPRESSED_INCOMPLETE.value

    def test_no_sources_gives_suppressed_unknown_source(self) -> None:
        """No sources (but has facts) → is_insufficient=True → SUPPRESSED_UNKNOWN_SOURCE.

        Priority 3 (unknown source) fires before priority 4 (incomplete) because
        assess_artifact_sources([]) produces is_insufficient=True. The reason is
        more specific: source credibility cannot be established at all.
        """
        sources = []
        facts = [_comparable_fact("eps", 2.5)]
        cred, contr, comp = _assess(sources, facts)
        assert comp.completeness_band == "THIN"
        assert cred.is_insufficient is True
        result = assess_artifact_usability(cred, contr, comp)
        # SUPPRESSED_UNKNOWN_SOURCE (priority 3) beats SUPPRESSED_INCOMPLETE (priority 4)
        assert result.usability_label == ArtifactUsabilityLabel.SUPPRESSED_UNKNOWN_SOURCE.value


# ── Criterion 7: USABLE_WITH_LIMITATIONS is distinct from USABLE ─────────────


class TestUsableWithLimitationsDistinct:

    def test_partial_completeness_is_usable_with_limitations(self) -> None:
        """PARTIAL completeness → USABLE_WITH_LIMITATIONS, not USABLE."""
        sources = [_sec_source()]
        facts = [_unquoted_comparable_fact("revenue", 1000.0)]
        cred, contr, comp = _assess(sources, facts)
        assert comp.completeness_band == "PARTIAL"
        result = assess_artifact_usability(cred, contr, comp)
        assert result.usability_label == ArtifactUsabilityLabel.USABLE_WITH_LIMITATIONS.value
        assert result.is_usable is True

    def test_complete_completeness_is_usable(self) -> None:
        """COMPLETE completeness → USABLE, not USABLE_WITH_LIMITATIONS."""
        sources = [_sec_source()]
        facts = [_comparable_fact("revenue", 1000.0)]  # quote_grounded=True
        cred, contr, comp = _assess(sources, facts)
        assert comp.completeness_band == "COMPLETE"
        result = assess_artifact_usability(cred, contr, comp)
        assert result.usability_label == ArtifactUsabilityLabel.USABLE.value
        assert result.is_usable is True

    def test_usable_and_usable_with_limitations_both_have_is_usable_true(self) -> None:
        """Both USABLE and USABLE_WITH_LIMITATIONS set is_usable=True."""
        sources = [_sec_source()]
        # USABLE
        facts_complete = [_comparable_fact("rev", 100.0)]
        cred, contr, comp = _assess(sources, facts_complete)
        r1 = assess_artifact_usability(cred, contr, comp)
        # USABLE_WITH_LIMITATIONS
        facts_partial = [_unquoted_comparable_fact("rev", 100.0)]
        cred2, contr2, comp2 = _assess(sources, facts_partial)
        r2 = assess_artifact_usability(cred2, contr2, comp2)
        assert r1.is_usable is True
        assert r2.is_usable is True
        assert r1.usability_label != r2.usability_label


# ── Criterion 8: Earnings reviewer artifacts include truth_usability_assessment ─


class TestEarningsReviewerArtifactsIncludeTruthAdapter:

    def test_run_earnings_reviewer_dark_payload_includes_truth_usability(self) -> None:
        """run_earnings_reviewer_dark artifacts now include truth_usability_assessment."""
        client = _FakeSupabaseClient()
        run_earnings_reviewer_dark(
            user_id=str(uuid.uuid4()),
            ticker="AAPL",
            db_client=client,
            settings=_settings_all_on(),
        )
        rows = client.artifact_inserts()
        assert rows, "Expected at least one artifact INSERT"
        payload = rows[0].get("payload", {})
        assert "truth_usability_assessment" in payload, (
            "Stage 5E: truth_usability_assessment missing from artifact payload. "
            "ResearchArtifactServiceV1 must inject Stage 5E assessment."
        )

    def test_truth_usability_assessment_has_all_expected_fields(self) -> None:
        """truth_usability_assessment shape: all required fields present."""
        client = _FakeSupabaseClient()
        run_earnings_reviewer_dark(
            user_id=str(uuid.uuid4()),
            ticker="MSFT",
            db_client=client,
            settings=_settings_all_on(),
        )
        rows = client.artifact_inserts()
        assert rows
        ta = rows[0].get("payload", {}).get("truth_usability_assessment", {})
        for field_name in (
            "adapter_version",
            "usability_label",
            "is_usable",
            "suppression_reason",
            "limitations",
            "no_guessing",
        ):
            assert field_name in ta, f"truth_usability_assessment missing field: {field_name}"

    def test_truth_usability_assessment_adapter_version_correct(self) -> None:
        """truth_usability_assessment must carry the correct adapter_version."""
        client = _FakeSupabaseClient()
        run_earnings_reviewer_dark(
            user_id=str(uuid.uuid4()),
            ticker="NVDA",
            db_client=client,
            settings=_settings_all_on(),
        )
        rows = client.artifact_inserts()
        ta = rows[0].get("payload", {}).get("truth_usability_assessment", {})
        assert ta.get("adapter_version") == ARTIFACT_TRUTH_ADAPTER_VERSION

    def test_dark_run_no_sources_gives_not_evaluable_or_suppressed(self) -> None:
        """Phase 3 dark-run (no external sources) → usability is NOT_EVALUABLE or SUPPRESSED."""
        client = _FakeSupabaseClient()
        run_earnings_reviewer_dark(
            user_id=str(uuid.uuid4()),
            ticker="AAPL",
            db_client=client,
            settings=_settings_all_on(),
        )
        rows = client.artifact_inserts()
        ta = rows[0].get("payload", {}).get("truth_usability_assessment", {})
        usable_labels = {
            ArtifactUsabilityLabel.USABLE.value,
            ArtifactUsabilityLabel.USABLE_WITH_LIMITATIONS.value,
        }
        assert ta.get("usability_label") not in usable_labels, (
            "Phase 3 dark-run (no real sources) must not produce USABLE or "
            "USABLE_WITH_LIMITATIONS — evidence is not credible enough."
        )
        assert ta.get("is_usable") is False

    def test_all_four_enrichment_layers_present_in_one_artifact(self) -> None:
        """All four enrichment layers (5B, 5C, 5D, 5E) present in every artifact."""
        client = _FakeSupabaseClient()
        run_earnings_reviewer_dark(
            user_id=str(uuid.uuid4()),
            ticker="TSLA",
            db_client=client,
            settings=_settings_all_on(),
        )
        rows = client.artifact_inserts()
        payload = rows[0].get("payload", {})
        for key in (
            "source_credibility_assessment",
            "contradiction_assessment",
            "evidence_completeness_assessment",
            "truth_usability_assessment",
        ):
            assert key in payload, f"Enrichment layer key '{key}' missing from artifact payload."


# ── Criterion 9: Stage 5A–5E0 behavior intact ────────────────────────────────


class TestStage5BehaviorIntact:

    def test_safe_for_decision_still_false(self) -> None:
        """safe_for_decision must still be False in every artifact row."""
        client = _FakeSupabaseClient()
        run_earnings_reviewer_dark(
            user_id=str(uuid.uuid4()),
            ticker="AAPL",
            db_client=client,
            settings=_settings_all_on(),
        )
        rows = client.artifact_inserts()
        assert rows
        for row in rows:
            assert row.get("safe_for_decision") is False

    def test_no_intel_v3_snapshots_writes(self) -> None:
        """run_earnings_reviewer_dark must NEVER write to intel_v3_snapshots."""
        client = _FakeSupabaseClient()
        run_earnings_reviewer_dark(
            user_id=str(uuid.uuid4()),
            ticker="AAPL",
            db_client=client,
            settings=_settings_all_on(),
        )
        assert client.snapshot_writes() == []

    def test_truth_usability_assessment_does_not_set_safe_for_decision_true(self) -> None:
        """truth_usability_assessment.is_usable must NOT propagate to safe_for_decision."""
        client = _FakeSupabaseClient()
        run_earnings_reviewer_dark(
            user_id=str(uuid.uuid4()),
            ticker="AAPL",
            db_client=client,
            settings=_settings_all_on(),
        )
        rows = client.artifact_inserts()
        for row in rows:
            assert row.get("safe_for_decision") is not True, (
                "safe_for_decision must never be True regardless of usability label."
            )


# ── Criterion 10: No decide() import in truth adapter ────────────────────────


class TestNoDecideImport:

    def test_truth_adapter_does_not_import_decide(self) -> None:
        """artifact_truth_adapter_v1.py must not import decide()."""
        import ast
        import os
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(
            base, "app", "services", "intelligence", "v3",
            "artifact_truth_adapter_v1.py"
        )
        with open(path) as f:
            source = f.read()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert "decision_policy_v1" not in module, (
                    f"artifact_truth_adapter_v1.py must never import from decision_policy_v1"
                )
                for alias in node.names:
                    assert alias.name != "decide", (
                        "artifact_truth_adapter_v1.py must never import decide()"
                    )


# ── Criterion 11: Assessment shape ────────────────────────────────────────────


class TestAssessmentShape:

    def test_to_dict_has_all_required_keys(self) -> None:
        """ArtifactUsabilityAssessment.to_dict() includes all required keys."""
        cred, contr, comp = _assess([_sec_source()], [_comparable_fact("rev", 100.0)])
        result = assess_artifact_usability(cred, contr, comp)
        d = result.to_dict()
        for key in (
            "adapter_version", "usability_label", "is_usable",
            "suppression_reason", "limitations", "no_guessing",
        ):
            assert key in d, f"to_dict() missing key: {key}"

    def test_no_guessing_always_true(self) -> None:
        """no_guessing is always True for all labels."""
        test_cases = [
            ([], []),
            ([_sec_source()], [_comparable_fact("rev", 100.0)]),
            ([_news_source()], [_comparable_fact("rev", 100.0)]),
            ([_unknown_source()], [_comparable_fact("rev", 100.0)]),
            ([_sec_source()], [_comparable_fact("rev", 100.0), _comparable_fact("rev", 500.0)]),
            ([_sec_source()], [_unquoted_comparable_fact("rev", 100.0)]),
        ]
        for sources, facts in test_cases:
            cred, contr, comp = _assess(sources, facts)
            result = assess_artifact_usability(cred, contr, comp)
            assert result.no_guessing is True, (
                f"no_guessing must always be True, got False for label {result.usability_label}"
            )

    def test_suppressed_labels_have_suppression_reason(self) -> None:
        """All SUPPRESSED_* and NOT_EVALUABLE labels include a suppression_reason."""
        suppressed_cases = [
            ([], []),  # NOT_EVALUABLE
            ([_news_source()], [_comparable_fact("rev", 100.0)]),  # SUPPRESSED_INCOMPLETE
            ([_unknown_source()], [_comparable_fact("rev", 100.0)]),  # SUPPRESSED_UNKNOWN_SOURCE
            ([_sec_source()], [_comparable_fact("rev", 100.0), _comparable_fact("rev", 500.0)]),  # SUPPRESSED_CONTRADICTED
        ]
        for sources, facts in suppressed_cases:
            cred, contr, comp = _assess(sources, facts)
            result = assess_artifact_usability(cred, contr, comp)
            assert result.suppression_reason is not None, (
                f"Label {result.usability_label} must have suppression_reason"
            )

    def test_usable_labels_have_no_suppression_reason(self) -> None:
        """USABLE and USABLE_WITH_LIMITATIONS have suppression_reason=None."""
        usable_cases = [
            ([_sec_source()], [_comparable_fact("rev", 100.0)]),           # USABLE
            ([_sec_source()], [_unquoted_comparable_fact("rev", 100.0)]),  # USABLE_WITH_LIMITATIONS
        ]
        for sources, facts in usable_cases:
            cred, contr, comp = _assess(sources, facts)
            result = assess_artifact_usability(cred, contr, comp)
            assert result.suppression_reason is None, (
                f"Label {result.usability_label} must have suppression_reason=None"
            )


# ── Fake Supabase infrastructure (mirrors test_stage5e0 pattern) ─────────────


@dataclass
class _TableState:
    inserts: list = field(default_factory=list)
    upserts: list = field(default_factory=list)


class _FakeTableQuery:

    def __init__(self, state: _TableState, return_id: Optional[str] = None) -> None:
        self._state = state
        self._return_id = return_id or str(uuid.uuid4())
        self._row: Optional[dict] = None
        self._on_conflict: Optional[str] = None
        self._is_update: bool = False
        self._select_cols: Optional[str] = None
        self._filters: dict = {}

    def insert(self, row: dict) -> "_FakeTableQuery":
        self._row = row
        return self

    def update(self, row: dict) -> "_FakeTableQuery":
        self._row = row
        self._is_update = True
        return self

    def upsert(self, row: dict, *, on_conflict: str = "", ignore_duplicates: bool = False) -> "_FakeTableQuery":
        self._row = row
        self._on_conflict = on_conflict
        return self

    def select(self, cols: str = "*") -> "_FakeTableQuery":
        self._select_cols = cols
        return self

    def eq(self, col: str, val: Any) -> "_FakeTableQuery":
        self._filters[col] = val
        return self

    def neq(self, col: str, val: Any) -> "_FakeTableQuery":
        return self

    def is_(self, col: str, val: Any) -> "_FakeTableQuery":
        return self

    def order(self, *args: Any, **kwargs: Any) -> "_FakeTableQuery":
        return self

    def limit(self, n: int) -> "_FakeTableQuery":
        return self

    def execute(self) -> Any:
        if self._row is not None and self._is_update:
            class _U:
                data = []
            return _U()
        if self._row is not None:
            row_with_id = {"id": self._return_id, **self._row}
            if self._on_conflict is not None:
                self._state.upserts.append(self._row)
            else:
                self._state.inserts.append(self._row)
            class _R:
                data = [row_with_id]
            return _R()
        class _E:
            data = []
        return _E()


class _FakeSupabaseClient:

    def __init__(self) -> None:
        self.tables: dict[str, _TableState] = {
            "research_artifacts": _TableState(),
            "research_artifact_sources": _TableState(),
            "research_artifact_facts": _TableState(),
            "worker_audit_events": _TableState(),
            "intel_v3_snapshots": _TableState(),
        }

    def table(self, name: str) -> _FakeTableQuery:
        state = self.tables.setdefault(name, _TableState())
        return _FakeTableQuery(state)

    def artifact_inserts(self) -> list:
        return self.tables["research_artifacts"].inserts

    def snapshot_writes(self) -> list:
        return (
            self.tables["intel_v3_snapshots"].inserts
            + self.tables["intel_v3_snapshots"].upserts
        )


def _settings_all_on() -> Settings:
    return Settings(
        supabase_url="http://fake",
        supabase_anon_key="fake",
        supabase_service_role_key="fake",
        supabase_jwt_secret="fake",
        encryption_key="fake",
        intel_v3_research_workers_enabled=True,
        intel_v3_earnings_reviewer_enabled=True,
        intel_v3_research_worker_validation_enabled=True,
        intel_v3_research_worker_validation_info_logs_enabled=False,
    )
