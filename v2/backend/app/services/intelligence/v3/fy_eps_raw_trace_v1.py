"""Phase 14C.4 — FY EPS raw trace classifier (pure, diagnostics-only).

For each explicitly requested ticker, determines exactly where in the data
pipeline annual FY EPS is lost between raw SEC EDGAR companyfacts and the
Phase 14C earnings-yield extractor.

Loss stages (stable enum — callers must treat as opaque strings):
    no_research_artifact_facts        — no artifact or artifact has zero facts
    no_eps_payload_present            — artifact has facts but zero EPS tags
    source_accession_missing_10k      — EPS stored but only quarterly; no 10-K in source set
    raw_companyfacts_unavailable      — SEC fetch failed (CIK not found, timeout, etc.)
    raw_eps_tag_absent                — raw SEC data has no EPS us-gaap tags at all
    raw_eps_wrong_unit                — EPS tag present but no USD/shares unit in raw data
    raw_fy_eps_absent                 — EPS tag + correct unit, but no FY-period entry in raw
    raw_fy_eps_present_but_not_source_linked — raw FY EPS present but its accession not in
                                              the stored source_accession set
    parser_selection_gap              — source-linked FY EPS exists in raw but parser selected 0
    artifact_writer_gap               — parser selected FY EPS but artifact facts lack it
    extractor_gap                     — FY EPS stored but Phase 14C extractor cannot use it
    artifact_generation_selection_gap — latest artifact lacks FY EPS; older artifact has it
    mixed_old_artifacts_or_latest_artifact_selection_gap — Phase 14C reads stale artifact
    unknown_manual_review             — data present but loss stage cannot be determined

Architecture invariants (non-negotiable):
    - No IO, no DB, no provider, no LLM.
    - No decision authority.
    - No PriceBand, no TTM, no quarterly annualization.
    - No DecisionInputV3 mutation.
    - Never fabricates EPS values.
    - Read-only and deterministic.
    - Exactly one loss_stage per ticker.
    - Safe for cert-gated operator use only — never exposed to frontend.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

FY_EPS_RAW_TRACE_V1_CONTRACT_VERSION: str = "phase14c4_fy_eps_raw_trace_v1"

# ── Loss stage enum constants (stable — callers must not construct raw strings) ──
LOSS_NO_RESEARCH_ARTIFACT_FACTS: str = "no_research_artifact_facts"
LOSS_NO_EPS_PAYLOAD_PRESENT: str = "no_eps_payload_present"
LOSS_SOURCE_ACCESSION_MISSING_10K: str = "source_accession_missing_10k"
LOSS_RAW_COMPANYFACTS_UNAVAILABLE: str = "raw_companyfacts_unavailable"
LOSS_RAW_EPS_TAG_ABSENT: str = "raw_eps_tag_absent"
LOSS_RAW_EPS_WRONG_UNIT: str = "raw_eps_wrong_unit"
LOSS_RAW_FY_EPS_ABSENT: str = "raw_fy_eps_absent"
LOSS_RAW_FY_EPS_NOT_SOURCE_LINKED: str = "raw_fy_eps_present_but_not_source_linked"
LOSS_PARSER_SELECTION_GAP: str = "parser_selection_gap"
LOSS_ARTIFACT_WRITER_GAP: str = "artifact_writer_gap"
LOSS_EXTRACTOR_GAP: str = "extractor_gap"
LOSS_ARTIFACT_GENERATION_SELECTION_GAP: str = "artifact_generation_selection_gap"
LOSS_MIXED_OLD_ARTIFACTS: str = "mixed_old_artifacts_or_latest_artifact_selection_gap"
LOSS_UNKNOWN_MANUAL_REVIEW: str = "unknown_manual_review"

_ALL_LOSS_STAGES: tuple[str, ...] = (
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
    LOSS_ARTIFACT_GENERATION_SELECTION_GAP,
    LOSS_MIXED_OLD_ARTIFACTS,
    LOSS_UNKNOWN_MANUAL_REVIEW,
)

# Recommended next action per loss stage (stable labels — informational only).
_RECOMMENDED_ACTION: dict[str, str] = {
    LOSS_NO_RESEARCH_ARTIFACT_FACTS: "run_sec_earnings_reviewer_to_populate_artifact_facts",
    LOSS_NO_EPS_PAYLOAD_PRESENT: "investigate_sec_companyfacts_for_eps_tags_or_check_cik",
    LOSS_SOURCE_ACCESSION_MISSING_10K: "fix_source_accession_selection_to_include_latest_10k",
    LOSS_RAW_COMPANYFACTS_UNAVAILABLE: "check_sec_edgar_cik_mapping_and_provider_connectivity",
    LOSS_RAW_EPS_TAG_ABSENT: "check_sec_companyfacts_for_eps_tags_may_use_nonstandard_taxonomy",
    LOSS_RAW_EPS_WRONG_UNIT: "expand_eps_unit_allowlist_or_investigate_nonstandard_unit",
    LOSS_RAW_FY_EPS_ABSENT: "check_fiscal_period_field_fp_in_sec_raw_data_may_need_fp_mapping",
    LOSS_RAW_FY_EPS_NOT_SOURCE_LINKED: "expand_max_filings_or_add_historic_10k_to_source_set",
    LOSS_PARSER_SELECTION_GAP: "review_parser_source_accession_linkage_and_max_periods_per_tag",
    LOSS_ARTIFACT_WRITER_GAP: "investigate_artifact_store_writer_for_metric_observation_drop",
    LOSS_EXTRACTOR_GAP: "review_eps_payload_extractor_skip_reasons_for_stored_fy_eps_rows",
    LOSS_ARTIFACT_GENERATION_SELECTION_GAP: "rerun_backfill_to_regenerate_latest_artifact_with_fy_eps",
    LOSS_MIXED_OLD_ARTIFACTS: "verify_phase14c_reads_latest_artifact_only_not_all_artifact_rows",
    LOSS_UNKNOWN_MANUAL_REVIEW: "manual_investigation_required_data_present_but_stage_indeterminate",
}


@dataclass(frozen=True)
class FyEpsRawTraceInput:
    """Pre-aggregated per-ticker inputs for the pure loss-stage classifier.

    The router assembles all values from DB queries and optional SEC fetches.
    The pure classifier never touches IO.

    Raw SEC fields are populated only when the SEC fetch was attempted and
    succeeded. When raw_companyfacts_fetch_attempted is False, all raw_*
    and filter-simulation fields default to zero/empty/None.
    """

    ticker: str

    # ── Stored artifact presence (from research_artifacts table) ─────────────
    has_research_artifact: bool = False
    artifact_count: int = 0
    latest_artifact_id: Optional[str] = None

    # ── Stored fact counts (from research_artifact_facts) ────────────────────
    artifact_fact_count: int = 0
    stored_eps_fact_count: int = 0           # any EPS tag, any period
    stored_fy_eps_fact_count: int = 0        # FY annual EPS
    stored_quarterly_eps_fact_count: int = 0  # non-FY EPS

    # ── Source filing analysis (from sourced_claim facts) ────────────────────
    # form_type is stored in structured_payload of fact_kind="sourced_claim"
    source_record_count: int = 0
    source_10k_accession_count: int = 0
    source_10q_accession_count: int = 0
    source_accessions_include_10k: bool = False

    # ── Multi-artifact analysis (latest vs. all artifacts for ticker) ─────────
    latest_artifact_has_fy_eps: bool = False
    any_artifact_has_fy_eps: bool = False

    # ── Extractor usability (Phase 14C extractor result on stored FY EPS) ────
    fy_eps_extractor_usable_count: int = 0

    # ── Raw SEC companyfacts (optional — only when fetch attempted) ───────────
    raw_companyfacts_fetch_attempted: bool = False
    # "success" | "skipped" | "failed" | "no_cik" | "no_user_agent"
    raw_companyfacts_fetch_status: str = "skipped"
    raw_eps_tag_present_count: int = 0       # EPS tags in raw us-gaap
    raw_eps_unit_keys: list[str] = field(default_factory=list)
    raw_eps_observation_count: int = 0       # all EPS entries in raw (unfiltered)
    raw_fy_eps_observation_count: int = 0    # FY-period EPS entries in raw (unfiltered)
    raw_latest_fy_eps_filed: Optional[str] = None
    raw_latest_fy_eps_form: Optional[str] = None
    raw_latest_fy_eps_fp: Optional[str] = None
    raw_latest_fy_eps_has_accn: bool = False

    # ── Parser-simulation filter counts (from raw SEC with stored accessions) ─
    fy_eps_filtered_by_unit_count: int = 0
    fy_eps_filtered_by_source_accession_count: int = 0
    fy_eps_selected_by_parser_count: int = 0
    # Alias of stored_fy_eps_fact_count for output clarity:
    fy_eps_stored_as_fact_count: int = 0


@dataclass(frozen=True)
class FyEpsRawTraceDiagnostic:
    """Per-ticker raw trace diagnostic (cert-gated, operator-only).

    Exactly one loss_stage is assigned for each missing ticker.
    loss_stage is None only when fy_eps_extractor_usable_count > 0 (usable case).
    """

    ticker: str

    # ── Artifact / fact summary ───────────────────────────────────────────────
    has_research_artifact: bool
    artifact_count: int
    latest_artifact_id: Optional[str]
    artifact_fact_count: int
    stored_eps_fact_count: int
    stored_fy_eps_fact_count: int
    stored_quarterly_eps_fact_count: int

    # ── Source record summary ─────────────────────────────────────────────────
    source_record_count: int
    source_10k_accession_count: int
    source_10q_accession_count: int
    source_accessions_include_10k: bool

    # ── Raw SEC summary ───────────────────────────────────────────────────────
    raw_companyfacts_fetch_attempted: bool
    raw_companyfacts_fetch_status: str
    raw_eps_tag_present_count: int
    raw_eps_unit_keys: list[str]
    raw_eps_observation_count: int
    raw_fy_eps_observation_count: int
    raw_latest_fy_eps_filed: Optional[str]
    raw_latest_fy_eps_form: Optional[str]
    raw_latest_fy_eps_fp: Optional[str]
    raw_latest_fy_eps_has_accn: bool

    # ── Filter / pipeline simulation ─────────────────────────────────────────
    fy_eps_filtered_by_unit_count: int
    fy_eps_filtered_by_source_accession_count: int
    fy_eps_selected_by_parser_count: int
    fy_eps_stored_as_fact_count: int
    fy_eps_extractor_usable_count: int

    # ── Classification ───────────────────────────────────────────────────────
    loss_stage: Optional[str]          # None if fy_eps_extractor_usable_count > 0
    recommended_next_action: str


@dataclass(frozen=True)
class FyEpsRawTraceResult:
    """Aggregate result for the per-ticker raw trace diagnostic."""

    trace_version: str
    trace_diagnostics: list[FyEpsRawTraceDiagnostic]
    trace_count: int
    usable_fy_eps_count: int
    missing_fy_eps_count: int
    loss_stage_counts: dict[str, int]
    raw_fetch_attempted_count: int
    raw_fetch_succeeded_count: int
    errors: list[str] = field(default_factory=list)


# ── Pure classifier ───────────────────────────────────────────────────────────

def classify_fy_eps_raw_trace(inp: FyEpsRawTraceInput) -> FyEpsRawTraceDiagnostic:
    """Classify one ticker's raw FY EPS loss stage.

    Returns FyEpsRawTraceDiagnostic — never raises.
    """
    is_usable = inp.fy_eps_extractor_usable_count > 0
    if is_usable:
        loss_stage = None
    else:
        loss_stage = _determine_loss_stage(inp)

    return FyEpsRawTraceDiagnostic(
        ticker=inp.ticker,
        has_research_artifact=inp.has_research_artifact,
        artifact_count=inp.artifact_count,
        latest_artifact_id=inp.latest_artifact_id,
        artifact_fact_count=inp.artifact_fact_count,
        stored_eps_fact_count=inp.stored_eps_fact_count,
        stored_fy_eps_fact_count=inp.stored_fy_eps_fact_count,
        stored_quarterly_eps_fact_count=inp.stored_quarterly_eps_fact_count,
        source_record_count=inp.source_record_count,
        source_10k_accession_count=inp.source_10k_accession_count,
        source_10q_accession_count=inp.source_10q_accession_count,
        source_accessions_include_10k=inp.source_accessions_include_10k,
        raw_companyfacts_fetch_attempted=inp.raw_companyfacts_fetch_attempted,
        raw_companyfacts_fetch_status=inp.raw_companyfacts_fetch_status,
        raw_eps_tag_present_count=inp.raw_eps_tag_present_count,
        raw_eps_unit_keys=list(inp.raw_eps_unit_keys),
        raw_eps_observation_count=inp.raw_eps_observation_count,
        raw_fy_eps_observation_count=inp.raw_fy_eps_observation_count,
        raw_latest_fy_eps_filed=inp.raw_latest_fy_eps_filed,
        raw_latest_fy_eps_form=inp.raw_latest_fy_eps_form,
        raw_latest_fy_eps_fp=inp.raw_latest_fy_eps_fp,
        raw_latest_fy_eps_has_accn=inp.raw_latest_fy_eps_has_accn,
        fy_eps_filtered_by_unit_count=inp.fy_eps_filtered_by_unit_count,
        fy_eps_filtered_by_source_accession_count=inp.fy_eps_filtered_by_source_accession_count,
        fy_eps_selected_by_parser_count=inp.fy_eps_selected_by_parser_count,
        fy_eps_stored_as_fact_count=inp.fy_eps_stored_as_fact_count,
        fy_eps_extractor_usable_count=inp.fy_eps_extractor_usable_count,
        loss_stage=loss_stage,
        recommended_next_action=_RECOMMENDED_ACTION.get(
            loss_stage or "", "no_action_required_fy_eps_usable"
        ),
    )


def build_fy_eps_raw_trace(
    *,
    inputs: list[FyEpsRawTraceInput],
    extra_errors: list[str] | None = None,
) -> FyEpsRawTraceResult:
    """Build aggregate raw trace result for all requested tickers.

    Args:
        inputs:        One FyEpsRawTraceInput per ticker.
        extra_errors:  Non-fatal fetch errors from the router.

    Returns:
        FyEpsRawTraceResult — never raises.
    """
    try:
        return _build(inputs=inputs, extra_errors=list(extra_errors or []))
    except Exception as exc:  # noqa: BLE001
        return FyEpsRawTraceResult(
            trace_version=FY_EPS_RAW_TRACE_V1_CONTRACT_VERSION,
            trace_diagnostics=[],
            trace_count=0,
            usable_fy_eps_count=0,
            missing_fy_eps_count=0,
            loss_stage_counts={s: 0 for s in _ALL_LOSS_STAGES},
            raw_fetch_attempted_count=0,
            raw_fetch_succeeded_count=0,
            errors=[f"build_error: {type(exc).__name__}: {exc}"],
        )


def _build(
    *,
    inputs: list[FyEpsRawTraceInput],
    extra_errors: list[str],
) -> FyEpsRawTraceResult:
    diagnostics: list[FyEpsRawTraceDiagnostic] = []
    loss_stage_counts: dict[str, int] = {s: 0 for s in _ALL_LOSS_STAGES}
    usable = 0
    missing = 0
    raw_attempted = 0
    raw_succeeded = 0

    for inp in inputs:
        diag = classify_fy_eps_raw_trace(inp)
        diagnostics.append(diag)
        if diag.loss_stage is None:
            usable += 1
        else:
            missing += 1
            if diag.loss_stage in loss_stage_counts:
                loss_stage_counts[diag.loss_stage] += 1
        if inp.raw_companyfacts_fetch_attempted:
            raw_attempted += 1
        if inp.raw_companyfacts_fetch_status == "success":
            raw_succeeded += 1

    return FyEpsRawTraceResult(
        trace_version=FY_EPS_RAW_TRACE_V1_CONTRACT_VERSION,
        trace_diagnostics=diagnostics,
        trace_count=len(diagnostics),
        usable_fy_eps_count=usable,
        missing_fy_eps_count=missing,
        loss_stage_counts=loss_stage_counts,
        raw_fetch_attempted_count=raw_attempted,
        raw_fetch_succeeded_count=raw_succeeded,
        errors=extra_errors,
    )


# ── Loss stage decision tree ──────────────────────────────────────────────────

def _determine_loss_stage(inp: FyEpsRawTraceInput) -> str:
    """Deterministic decision tree — exactly one loss_stage per missing ticker.

    Ordered from earliest (most upstream) to latest (most downstream) in the
    data pipeline, so we surface the root cause rather than a symptom.
    """
    # Stage 1 — artifact / fact absence
    if not inp.has_research_artifact or inp.artifact_fact_count == 0:
        return LOSS_NO_RESEARCH_ARTIFACT_FACTS

    # Stage 2 — no EPS payload in stored facts.
    # When raw SEC fetch succeeded, use it to give a more specific root cause
    # instead of the generic no_eps_payload_present stage.
    if inp.stored_eps_fact_count == 0:
        if (
            inp.raw_companyfacts_fetch_attempted
            and inp.raw_companyfacts_fetch_status == "success"
        ):
            if inp.raw_eps_tag_present_count == 0:
                return LOSS_RAW_EPS_TAG_ABSENT
            if "USD/shares" not in inp.raw_eps_unit_keys and inp.raw_eps_observation_count == 0:
                return LOSS_RAW_EPS_WRONG_UNIT
        return LOSS_NO_EPS_PAYLOAD_PRESENT

    # Stage 3 — FY EPS stored but Phase 14C extractor cannot use it
    if inp.stored_fy_eps_fact_count > 0 and inp.fy_eps_extractor_usable_count == 0:
        # Check multi-artifact: latest artifact missing FY EPS but older one has it
        if inp.any_artifact_has_fy_eps and not inp.latest_artifact_has_fy_eps:
            return LOSS_MIXED_OLD_ARTIFACTS
        return LOSS_EXTRACTOR_GAP

    # Stage 4 — FY EPS stored in older artifact but not latest
    if inp.any_artifact_has_fy_eps and not inp.latest_artifact_has_fy_eps:
        return LOSS_ARTIFACT_GENERATION_SELECTION_GAP

    # Stages 5–N — FY EPS absent from stored facts; EPS present but only quarterly.
    # Diagnose from stored source records first (no raw SEC fetch needed).
    if not inp.source_accessions_include_10k:
        # No 10-K accession in stored source records — cannot have FY EPS from SEC.
        return LOSS_SOURCE_ACCESSION_MISSING_10K

    # Source records include a 10-K but still no FY EPS stored.
    # Need raw SEC data for deeper diagnosis.
    if not inp.raw_companyfacts_fetch_attempted:
        return LOSS_UNKNOWN_MANUAL_REVIEW

    if inp.raw_companyfacts_fetch_status in ("failed", "no_cik", "no_user_agent"):
        return LOSS_RAW_COMPANYFACTS_UNAVAILABLE

    # Raw fetch succeeded — now trace through the parser pipeline.
    if inp.raw_eps_tag_present_count == 0:
        return LOSS_RAW_EPS_TAG_ABSENT

    if "USD/shares" not in inp.raw_eps_unit_keys and inp.raw_eps_observation_count == 0:
        return LOSS_RAW_EPS_WRONG_UNIT

    if inp.raw_fy_eps_observation_count == 0:
        return LOSS_RAW_FY_EPS_ABSENT

    # Raw FY EPS present — check if source_accession filter drops all of them.
    if (
        inp.raw_fy_eps_observation_count > 0
        and inp.fy_eps_filtered_by_source_accession_count >= inp.raw_fy_eps_observation_count
    ):
        return LOSS_RAW_FY_EPS_NOT_SOURCE_LINKED

    # Parser had source-linked FY EPS but selected zero.
    if inp.fy_eps_selected_by_parser_count == 0:
        return LOSS_PARSER_SELECTION_GAP

    # Parser selected FY EPS but artifact writer did not store it.
    if inp.fy_eps_stored_as_fact_count == 0:
        return LOSS_ARTIFACT_WRITER_GAP

    return LOSS_UNKNOWN_MANUAL_REVIEW
