"""Phase 14C.4 — FY EPS raw trace classifier tests.

Covers (matching the 10-loss-stage spec):
  1. Raw FY EPS present in stored facts and extractable → loss_stage None (usable).
  2. No research artifact → loss_stage no_research_artifact_facts.
  3. Artifact present but zero facts → loss_stage no_research_artifact_facts.
  4. Facts present but no EPS payload → loss_stage no_eps_payload_present.
  5. FY EPS stored and source-linked but no 10-K in stored sources →
       loss_stage source_accession_missing_10k.
  6. FY EPS stored but extractor cannot use it, latest artifact same as any →
       loss_stage extractor_gap.
  7. FY EPS in older artifact but NOT latest artifact →
       loss_stage mixed_old_artifacts_or_latest_artifact_selection_gap.
  8. Raw SEC fetch attempted; EPS tag absent → loss_stage raw_eps_tag_absent.
  9. Raw EPS tag present but no FY-period entries → loss_stage raw_fy_eps_absent.
  10. Raw FY EPS present but accession not in stored source set →
        loss_stage raw_fy_eps_present_but_not_source_linked.
  11. Raw FY EPS source-linked but parser selected 0 → loss_stage parser_selection_gap.
  12. Parser selected FY EPS but stored fact count 0 → loss_stage artifact_writer_gap.
  13. Only quarterly raw EPS exists (correct unit, no FY fp) → raw_fy_eps_absent.
  14. EPS tag present in raw but wrong unit → loss_stage raw_eps_wrong_unit.
  15. Config flag defaults to False.
  16. build_fy_eps_raw_trace aggregate counts are correct.
  17. Governance locks on aggregate result object.
  18. Multi-ticker aggregate loss_stage_counts populated correctly.
"""
from __future__ import annotations

import pytest

from app.services.intelligence.v3.fy_eps_raw_trace_v1 import (
    FyEpsRawTraceInput,
    FyEpsRawTraceDiagnostic,
    FyEpsRawTraceResult,
    classify_fy_eps_raw_trace,
    build_fy_eps_raw_trace,
    FY_EPS_RAW_TRACE_V1_CONTRACT_VERSION,
    LOSS_NO_RESEARCH_ARTIFACT_FACTS,
    LOSS_NO_EPS_PAYLOAD_PRESENT,
    LOSS_SOURCE_ACCESSION_MISSING_10K,
    LOSS_RAW_COMPANYFACTS_UNAVAILABLE,
    LOSS_RAW_EPS_TAG_ABSENT,
    LOSS_RAW_EPS_WRONG_UNIT,
    LOSS_RAW_FY_EPS_ABSENT,
    LOSS_RAW_FY_EPS_NOT_SOURCE_LINKED,
    LOSS_PARSER_SELECTION_GAP,
    LOSS_ARTIFACT_WRITER_GAP,
    LOSS_EXTRACTOR_GAP,
    LOSS_MIXED_OLD_ARTIFACTS,
    LOSS_ARTIFACT_GENERATION_SELECTION_GAP,
    LOSS_UNKNOWN_MANUAL_REVIEW,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _inp(
    ticker: str = "TEST",
    *,
    has_artifact: bool = True,
    artifact_count: int = 1,
    latest_artifact_id: str | None = "art-001",
    artifact_fact_count: int = 5,
    stored_eps_fact_count: int = 2,
    stored_fy_eps_fact_count: int = 1,
    stored_quarterly_eps_fact_count: int = 1,
    source_record_count: int = 3,
    source_10k_accession_count: int = 1,
    source_10q_accession_count: int = 2,
    source_accessions_include_10k: bool = True,
    latest_artifact_has_fy_eps: bool = True,
    any_artifact_has_fy_eps: bool = True,
    fy_eps_extractor_usable_count: int = 1,
    raw_fetch_attempted: bool = False,
    raw_fetch_status: str = "skipped",
    raw_eps_tag_count: int = 0,
    raw_eps_unit_keys: list[str] | None = None,
    raw_eps_obs_count: int = 0,
    raw_fy_eps_obs_count: int = 0,
    raw_latest_filed: str | None = None,
    raw_latest_form: str | None = None,
    raw_latest_fp: str | None = None,
    raw_latest_has_accn: bool = False,
    filtered_by_unit: int = 0,
    filtered_by_accn: int = 0,
    selected_by_parser: int = 0,
    fy_eps_stored_as_fact: int | None = None,
) -> FyEpsRawTraceInput:
    return FyEpsRawTraceInput(
        ticker=ticker,
        has_research_artifact=has_artifact,
        artifact_count=artifact_count,
        latest_artifact_id=latest_artifact_id,
        artifact_fact_count=artifact_fact_count,
        stored_eps_fact_count=stored_eps_fact_count,
        stored_fy_eps_fact_count=stored_fy_eps_fact_count,
        stored_quarterly_eps_fact_count=stored_quarterly_eps_fact_count,
        source_record_count=source_record_count,
        source_10k_accession_count=source_10k_accession_count,
        source_10q_accession_count=source_10q_accession_count,
        source_accessions_include_10k=source_accessions_include_10k,
        latest_artifact_has_fy_eps=latest_artifact_has_fy_eps,
        any_artifact_has_fy_eps=any_artifact_has_fy_eps,
        fy_eps_extractor_usable_count=fy_eps_extractor_usable_count,
        raw_companyfacts_fetch_attempted=raw_fetch_attempted,
        raw_companyfacts_fetch_status=raw_fetch_status,
        raw_eps_tag_present_count=raw_eps_tag_count,
        raw_eps_unit_keys=raw_eps_unit_keys or [],
        raw_eps_observation_count=raw_eps_obs_count,
        raw_fy_eps_observation_count=raw_fy_eps_obs_count,
        raw_latest_fy_eps_filed=raw_latest_filed,
        raw_latest_fy_eps_form=raw_latest_form,
        raw_latest_fy_eps_fp=raw_latest_fp,
        raw_latest_fy_eps_has_accn=raw_latest_has_accn,
        fy_eps_filtered_by_unit_count=filtered_by_unit,
        fy_eps_filtered_by_source_accession_count=filtered_by_accn,
        fy_eps_selected_by_parser_count=selected_by_parser,
        fy_eps_stored_as_fact_count=(
            stored_fy_eps_fact_count if fy_eps_stored_as_fact is None
            else fy_eps_stored_as_fact
        ),
    )


def _classify(inp: FyEpsRawTraceInput) -> FyEpsRawTraceDiagnostic:
    return classify_fy_eps_raw_trace(inp)


# ── Test 1: Usable — FY EPS present, extractable ──────────────────────────────

class TestUsableFyEps:
    def test_loss_stage_is_none_when_usable(self):
        diag = _classify(_inp(fy_eps_extractor_usable_count=1))
        assert diag.loss_stage is None

    def test_recommended_action_is_no_action_required(self):
        diag = _classify(_inp(fy_eps_extractor_usable_count=1))
        assert diag.recommended_next_action == "no_action_required_fy_eps_usable"

    def test_stored_counts_reflected(self):
        diag = _classify(_inp(
            stored_fy_eps_fact_count=2,
            fy_eps_extractor_usable_count=2,
        ))
        assert diag.stored_fy_eps_fact_count == 2
        assert diag.fy_eps_extractor_usable_count == 2


# ── Test 2: No artifact ────────────────────────────────────────────────────────

class TestNoArtifact:
    def test_no_artifact_gives_no_research_artifact_facts(self):
        diag = _classify(_inp(
            has_artifact=False,
            artifact_count=0,
            latest_artifact_id=None,
            artifact_fact_count=0,
            stored_eps_fact_count=0,
            stored_fy_eps_fact_count=0,
            fy_eps_extractor_usable_count=0,
        ))
        assert diag.loss_stage == LOSS_NO_RESEARCH_ARTIFACT_FACTS

    def test_recommended_action_for_no_artifact(self):
        diag = _classify(_inp(
            has_artifact=False,
            artifact_count=0,
            artifact_fact_count=0,
            stored_eps_fact_count=0,
            stored_fy_eps_fact_count=0,
            fy_eps_extractor_usable_count=0,
        ))
        assert "run_sec_earnings_reviewer" in diag.recommended_next_action


# ── Test 3: Artifact but zero facts ──────────────────────────────────────────

class TestArtifactZeroFacts:
    def test_zero_facts_gives_no_research_artifact_facts(self):
        diag = _classify(_inp(
            has_artifact=True,
            artifact_count=1,
            artifact_fact_count=0,
            stored_eps_fact_count=0,
            stored_fy_eps_fact_count=0,
            fy_eps_extractor_usable_count=0,
        ))
        assert diag.loss_stage == LOSS_NO_RESEARCH_ARTIFACT_FACTS


# ── Test 4: Facts present but no EPS payload ─────────────────────────────────

class TestNoEpsPayload:
    def test_no_eps_gives_no_eps_payload_present(self):
        diag = _classify(_inp(
            artifact_fact_count=10,
            stored_eps_fact_count=0,
            stored_fy_eps_fact_count=0,
            fy_eps_extractor_usable_count=0,
            source_10k_accession_count=1,
            source_accessions_include_10k=True,
        ))
        assert diag.loss_stage == LOSS_NO_EPS_PAYLOAD_PRESENT

    def test_brk_b_like_no_eps_gets_no_eps_stage(self):
        # BRK-B: artifact with sourced_claim facts but 0 EPS metric observations
        diag = _classify(_inp(
            ticker="BRK-B",
            artifact_fact_count=5,
            stored_eps_fact_count=0,
            stored_fy_eps_fact_count=0,
            fy_eps_extractor_usable_count=0,
        ))
        assert diag.loss_stage == LOSS_NO_EPS_PAYLOAD_PRESENT


# ── Test 5: Source accession missing 10-K ────────────────────────────────────

class TestSourceAccessionMissing10K:
    def test_no_10k_source_with_quarterly_eps_stored(self):
        diag = _classify(_inp(
            artifact_fact_count=5,
            stored_eps_fact_count=2,
            stored_fy_eps_fact_count=0,
            stored_quarterly_eps_fact_count=2,
            source_10k_accession_count=0,
            source_10q_accession_count=2,
            source_accessions_include_10k=False,
            any_artifact_has_fy_eps=False,
            latest_artifact_has_fy_eps=False,
            fy_eps_extractor_usable_count=0,
        ))
        assert diag.loss_stage == LOSS_SOURCE_ACCESSION_MISSING_10K

    def test_recommended_action_for_source_accession_gap(self):
        diag = _classify(_inp(
            stored_eps_fact_count=2,
            stored_fy_eps_fact_count=0,
            source_10k_accession_count=0,
            source_accessions_include_10k=False,
            fy_eps_extractor_usable_count=0,
        ))
        assert "fix_source_accession_selection" in diag.recommended_next_action

    def test_aapl_like_quarterly_only_no_10k_source(self):
        # Mirrors AAPL production gap: EPS stored (quarterly), no 10-K in sources
        diag = _classify(_inp(
            ticker="AAPL",
            stored_eps_fact_count=4,
            stored_fy_eps_fact_count=0,
            stored_quarterly_eps_fact_count=4,
            source_10k_accession_count=0,
            source_10q_accession_count=5,
            source_accessions_include_10k=False,
            fy_eps_extractor_usable_count=0,
        ))
        assert diag.loss_stage == LOSS_SOURCE_ACCESSION_MISSING_10K


# ── Test 6: Extractor gap ─────────────────────────────────────────────────────

class TestExtractorGap:
    def test_fy_eps_stored_but_extractor_zero(self):
        diag = _classify(_inp(
            stored_fy_eps_fact_count=2,
            any_artifact_has_fy_eps=True,
            latest_artifact_has_fy_eps=True,
            fy_eps_extractor_usable_count=0,
            source_accessions_include_10k=True,
        ))
        assert diag.loss_stage == LOSS_EXTRACTOR_GAP

    def test_recommended_action_for_extractor_gap(self):
        diag = _classify(_inp(
            stored_fy_eps_fact_count=1,
            any_artifact_has_fy_eps=True,
            latest_artifact_has_fy_eps=True,
            fy_eps_extractor_usable_count=0,
        ))
        assert "eps_payload_extractor_skip_reasons" in diag.recommended_next_action


# ── Test 7: Mixed old artifacts / latest artifact selection gap ───────────────

class TestMixedOldArtifacts:
    def test_older_artifact_has_fy_eps_but_not_latest(self):
        # any_artifact_has_fy_eps=True but latest_artifact_has_fy_eps=False
        # AND stored_fy_eps_fact_count > 0 (some artifact has it) but extractor=0
        diag = _classify(_inp(
            stored_fy_eps_fact_count=1,
            any_artifact_has_fy_eps=True,
            latest_artifact_has_fy_eps=False,
            fy_eps_extractor_usable_count=0,
            source_accessions_include_10k=True,
        ))
        assert diag.loss_stage == LOSS_MIXED_OLD_ARTIFACTS

    def test_any_has_fy_but_not_latest_without_stored_fy(self):
        # any_artifact_has_fy_eps=True (older), latest doesn't
        # stored_fy_eps_fact_count = 0 (queries all artifacts but FY EPS is only in older one)
        diag = _classify(_inp(
            stored_eps_fact_count=2,
            stored_fy_eps_fact_count=0,
            stored_quarterly_eps_fact_count=2,
            source_accessions_include_10k=True,
            any_artifact_has_fy_eps=True,
            latest_artifact_has_fy_eps=False,
            fy_eps_extractor_usable_count=0,
        ))
        assert diag.loss_stage == LOSS_ARTIFACT_GENERATION_SELECTION_GAP

    def test_mixed_recommended_action(self):
        diag = _classify(_inp(
            stored_fy_eps_fact_count=1,
            any_artifact_has_fy_eps=True,
            latest_artifact_has_fy_eps=False,
            fy_eps_extractor_usable_count=0,
        ))
        assert "rerun_backfill" in diag.recommended_next_action or "artifact" in diag.recommended_next_action


# ── Test 8: Raw EPS tag absent ────────────────────────────────────────────────

class TestRawEpsTagAbsent:
    def test_raw_fetch_success_no_eps_tags(self):
        diag = _classify(_inp(
            stored_eps_fact_count=0,
            stored_fy_eps_fact_count=0,
            source_10k_accession_count=1,
            source_accessions_include_10k=True,
            fy_eps_extractor_usable_count=0,
            raw_fetch_attempted=True,
            raw_fetch_status="success",
            raw_eps_tag_count=0,
            raw_eps_obs_count=0,
        ))
        assert diag.loss_stage == LOSS_RAW_EPS_TAG_ABSENT


# ── Test 9: Raw FY EPS absent ─────────────────────────────────────────────────

class TestRawFyEpsAbsent:
    def test_eps_tag_present_but_no_fy_period_in_raw(self):
        diag = _classify(_inp(
            stored_eps_fact_count=2,
            stored_fy_eps_fact_count=0,
            stored_quarterly_eps_fact_count=2,
            source_10k_accession_count=1,
            source_accessions_include_10k=True,
            fy_eps_extractor_usable_count=0,
            raw_fetch_attempted=True,
            raw_fetch_status="success",
            raw_eps_tag_count=2,
            raw_eps_unit_keys=["USD/shares"],
            raw_eps_obs_count=4,
            raw_fy_eps_obs_count=0,
        ))
        assert diag.loss_stage == LOSS_RAW_FY_EPS_ABSENT

    def test_only_quarterly_raw_eps_gives_raw_fy_eps_absent(self):
        # All raw EPS are Q1/Q2/Q3 — no FY annual entries
        diag = _classify(_inp(
            stored_eps_fact_count=3,
            stored_fy_eps_fact_count=0,
            stored_quarterly_eps_fact_count=3,
            source_10k_accession_count=1,
            source_accessions_include_10k=True,
            fy_eps_extractor_usable_count=0,
            raw_fetch_attempted=True,
            raw_fetch_status="success",
            raw_eps_tag_count=1,
            raw_eps_unit_keys=["USD/shares"],
            raw_eps_obs_count=6,
            raw_fy_eps_obs_count=0,
        ))
        assert diag.loss_stage == LOSS_RAW_FY_EPS_ABSENT


# ── Test 10: Raw FY EPS present but not source-linked ────────────────────────

class TestRawFyEpsNotSourceLinked:
    def test_all_raw_fy_eps_filtered_by_accession(self):
        # raw has 2 FY EPS entries; both have accession not in stored source set
        diag = _classify(_inp(
            stored_eps_fact_count=2,
            stored_fy_eps_fact_count=0,
            stored_quarterly_eps_fact_count=2,
            source_10k_accession_count=1,
            source_accessions_include_10k=True,
            fy_eps_extractor_usable_count=0,
            raw_fetch_attempted=True,
            raw_fetch_status="success",
            raw_eps_tag_count=2,
            raw_eps_unit_keys=["USD/shares"],
            raw_eps_obs_count=4,
            raw_fy_eps_obs_count=2,
            raw_latest_filed="2024-10-31",
            raw_latest_form="10-K",
            raw_latest_fp="FY",
            raw_latest_has_accn=True,
            filtered_by_accn=2,  # all 2 FY EPS filtered by source accession
            selected_by_parser=0,
        ))
        assert diag.loss_stage == LOSS_RAW_FY_EPS_NOT_SOURCE_LINKED

    def test_raw_latest_fy_fields_populated(self):
        diag = _classify(_inp(
            stored_eps_fact_count=2,
            stored_fy_eps_fact_count=0,
            source_10k_accession_count=1,
            source_accessions_include_10k=True,
            fy_eps_extractor_usable_count=0,
            raw_fetch_attempted=True,
            raw_fetch_status="success",
            raw_eps_tag_count=1,
            raw_eps_unit_keys=["USD/shares"],
            raw_eps_obs_count=2,
            raw_fy_eps_obs_count=1,
            raw_latest_filed="2024-10-31",
            raw_latest_form="10-K",
            raw_latest_fp="FY",
            raw_latest_has_accn=True,
            filtered_by_accn=1,
            selected_by_parser=0,
        ))
        assert diag.raw_latest_fy_eps_filed == "2024-10-31"
        assert diag.raw_latest_fy_eps_form == "10-K"
        assert diag.raw_latest_fy_eps_fp == "FY"
        assert diag.raw_latest_fy_eps_has_accn is True


# ── Test 11: Parser selection gap ────────────────────────────────────────────

class TestParserSelectionGap:
    def test_source_linked_fy_eps_in_raw_but_parser_selected_zero(self):
        # Some FY EPS pass accession filter but parser still selects 0
        diag = _classify(_inp(
            stored_eps_fact_count=2,
            stored_fy_eps_fact_count=0,
            source_10k_accession_count=1,
            source_accessions_include_10k=True,
            fy_eps_extractor_usable_count=0,
            raw_fetch_attempted=True,
            raw_fetch_status="success",
            raw_eps_tag_count=2,
            raw_eps_unit_keys=["USD/shares"],
            raw_eps_obs_count=4,
            raw_fy_eps_obs_count=2,
            filtered_by_accn=1,    # 1 of 2 FY EPS pass the accession filter
            selected_by_parser=0,  # but parser selects none (e.g. max_periods logic)
            fy_eps_stored_as_fact=0,
        ))
        assert diag.loss_stage == LOSS_PARSER_SELECTION_GAP


# ── Test 12: Artifact writer gap ─────────────────────────────────────────────

class TestArtifactWriterGap:
    def test_parser_selected_but_stored_fact_count_zero(self):
        diag = _classify(_inp(
            stored_eps_fact_count=2,
            stored_fy_eps_fact_count=0,
            source_10k_accession_count=1,
            source_accessions_include_10k=True,
            fy_eps_extractor_usable_count=0,
            raw_fetch_attempted=True,
            raw_fetch_status="success",
            raw_eps_tag_count=1,
            raw_eps_unit_keys=["USD/shares"],
            raw_eps_obs_count=2,
            raw_fy_eps_obs_count=1,
            filtered_by_accn=0,
            selected_by_parser=1,
            fy_eps_stored_as_fact=0,
        ))
        assert diag.loss_stage == LOSS_ARTIFACT_WRITER_GAP

    def test_recommended_action_for_writer_gap(self):
        diag = _classify(_inp(
            stored_fy_eps_fact_count=0,
            source_accessions_include_10k=True,
            fy_eps_extractor_usable_count=0,
            raw_fetch_attempted=True,
            raw_fetch_status="success",
            raw_eps_tag_count=1,
            raw_eps_unit_keys=["USD/shares"],
            raw_eps_obs_count=2,
            raw_fy_eps_obs_count=1,
            filtered_by_accn=0,
            selected_by_parser=1,
            fy_eps_stored_as_fact=0,
        ))
        assert "artifact_store_writer" in diag.recommended_next_action


# ── Test 13: Only quarterly raw EPS (raw_fy_eps_absent) ──────────────────────

class TestOnlyQuarterlyRaw:
    def test_quarterly_only_raw_gives_raw_fy_eps_absent(self):
        # Tag present, USD/shares unit, observations exist, but all are Q1/Q2/Q3
        diag = _classify(_inp(
            stored_eps_fact_count=3,
            stored_fy_eps_fact_count=0,
            source_10k_accession_count=1,
            source_accessions_include_10k=True,
            fy_eps_extractor_usable_count=0,
            raw_fetch_attempted=True,
            raw_fetch_status="success",
            raw_eps_tag_count=1,
            raw_eps_unit_keys=["USD/shares"],
            raw_eps_obs_count=3,
            raw_fy_eps_obs_count=0,
        ))
        assert diag.loss_stage == LOSS_RAW_FY_EPS_ABSENT


# ── Test 14: Wrong unit in raw ────────────────────────────────────────────────

class TestRawEpsWrongUnit:
    def test_eps_tag_present_but_not_usd_shares_unit(self):
        # EPS tag found but only "USD" unit (not "USD/shares") — unit mismatch
        diag = _classify(_inp(
            stored_eps_fact_count=0,
            stored_fy_eps_fact_count=0,
            source_10k_accession_count=1,
            source_accessions_include_10k=True,
            fy_eps_extractor_usable_count=0,
            raw_fetch_attempted=True,
            raw_fetch_status="success",
            raw_eps_tag_count=1,
            raw_eps_unit_keys=["USD"],  # wrong unit — no USD/shares
            raw_eps_obs_count=0,        # 0 because our counter skips wrong-unit entries
            raw_fy_eps_obs_count=0,
        ))
        assert diag.loss_stage == LOSS_RAW_EPS_WRONG_UNIT


# ── Test 15: Config flag defaults to False ────────────────────────────────────

class TestConfigFlag:
    def test_config_flag_defaults_false(self):
        from app.config import Settings
        s = Settings()
        assert s.intel_v3_fy_eps_raw_trace_v1_diagnostics_enabled is False


# ── Test 16: Aggregate result counts ─────────────────────────────────────────

class TestAggregateResult:
    def _make_multi(self) -> FyEpsRawTraceResult:
        return build_fy_eps_raw_trace(
            inputs=[
                _inp("AAPL", stored_fy_eps_fact_count=0, stored_eps_fact_count=2,
                     source_10k_accession_count=0, source_accessions_include_10k=False,
                     fy_eps_extractor_usable_count=0),
                _inp("MSFT", stored_fy_eps_fact_count=0, stored_eps_fact_count=2,
                     source_10k_accession_count=0, source_accessions_include_10k=False,
                     fy_eps_extractor_usable_count=0),
                _inp("NVDA", fy_eps_extractor_usable_count=1),  # usable
                _inp("BLSH", has_artifact=False, artifact_count=0, artifact_fact_count=0,
                     stored_eps_fact_count=0, stored_fy_eps_fact_count=0,
                     fy_eps_extractor_usable_count=0),
            ],
        )

    def test_trace_count(self):
        result = self._make_multi()
        assert result.trace_count == 4

    def test_usable_count(self):
        result = self._make_multi()
        assert result.usable_fy_eps_count == 1

    def test_missing_count(self):
        result = self._make_multi()
        assert result.missing_fy_eps_count == 3

    def test_loss_stage_counts_accurate(self):
        result = self._make_multi()
        assert result.loss_stage_counts[LOSS_SOURCE_ACCESSION_MISSING_10K] == 2
        assert result.loss_stage_counts[LOSS_NO_RESEARCH_ARTIFACT_FACTS] == 1

    def test_trace_version(self):
        result = self._make_multi()
        assert result.trace_version == FY_EPS_RAW_TRACE_V1_CONTRACT_VERSION


# ── Test 17: Governance locks ─────────────────────────────────────────────────

class TestGovernanceLocks:
    def test_loss_stage_none_when_usable(self):
        diag = _classify(_inp(fy_eps_extractor_usable_count=1))
        assert diag.loss_stage is None

    def test_diagnostic_fields_are_counts_not_values(self):
        diag = _classify(_inp())
        assert isinstance(diag.stored_eps_fact_count, int)
        assert isinstance(diag.raw_eps_observation_count, int)
        assert isinstance(diag.raw_fy_eps_observation_count, int)

    def test_build_never_raises_on_empty_inputs(self):
        result = build_fy_eps_raw_trace(inputs=[])
        assert result.trace_count == 0
        assert result.usable_fy_eps_count == 0
        assert result.missing_fy_eps_count == 0


# ── Test 18: Multi-ticker loss_stage_counts ────────────────────────────────────

class TestMultiTickerLossStageCounts:
    def test_all_known_loss_stages_in_result_keys(self):
        result = build_fy_eps_raw_trace(inputs=[])
        from app.services.intelligence.v3.fy_eps_raw_trace_v1 import _ALL_LOSS_STAGES
        for stage in _ALL_LOSS_STAGES:
            assert stage in result.loss_stage_counts

    def test_counts_sum_to_missing_count(self):
        inputs = [
            _inp("A", has_artifact=False, artifact_count=0, artifact_fact_count=0,
                 stored_eps_fact_count=0, stored_fy_eps_fact_count=0,
                 fy_eps_extractor_usable_count=0),
            _inp("B", stored_eps_fact_count=0, stored_fy_eps_fact_count=0,
                 artifact_fact_count=5, fy_eps_extractor_usable_count=0),
            _inp("C", fy_eps_extractor_usable_count=1),  # usable — not counted
        ]
        result = build_fy_eps_raw_trace(inputs=inputs)
        total_from_counts = sum(result.loss_stage_counts.values())
        assert total_from_counts == result.missing_fy_eps_count

    def test_raw_fetch_counts_tracked(self):
        inputs = [
            _inp("A", raw_fetch_attempted=True, raw_fetch_status="success",
                 stored_eps_fact_count=0, stored_fy_eps_fact_count=0,
                 source_10k_accession_count=1, source_accessions_include_10k=True,
                 fy_eps_extractor_usable_count=0,
                 raw_eps_tag_count=0),
            _inp("B", raw_fetch_attempted=True, raw_fetch_status="failed",
                 stored_eps_fact_count=0, stored_fy_eps_fact_count=0,
                 source_10k_accession_count=1, source_accessions_include_10k=True,
                 fy_eps_extractor_usable_count=0),
            _inp("C", fy_eps_extractor_usable_count=1),  # no fetch attempted
        ]
        result = build_fy_eps_raw_trace(inputs=inputs)
        assert result.raw_fetch_attempted_count == 2
        assert result.raw_fetch_succeeded_count == 1
