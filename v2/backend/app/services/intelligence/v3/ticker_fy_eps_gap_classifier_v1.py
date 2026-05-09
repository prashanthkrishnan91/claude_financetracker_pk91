"""Phase 14C.3 — Ticker-level FY EPS gap classifier (pure, diagnostics-only).

For each company ticker, classifies exactly why it does or does not have a
usable FY EPS for the Phase 14C earnings yield computation.

Architecture invariants (non-negotiable):
    - No IO, no DB, no provider, no LLM.
    - No decision authority.
    - No PriceBand, no TTM, no quarterly annualization.
    - No DecisionInputV3 mutation.
    - Never fabricates EPS values.
    - Read-only and deterministic.
    - Exactly one gap_reason per missing ticker — stable enum.
    - Per-ticker selected_eps_value is surfaced in this diagnostic only
      because it is cert-gated and never exposed to the frontend.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

TICKER_FY_EPS_GAP_CLASSIFIER_V1_CONTRACT_VERSION: str = (
    "phase14c3_ticker_fy_eps_gap_classifier_v1"
)

# ── Company classification constants ─────────────────────────────────────────
# All tickers reaching this module come from Phase 9 SEC readiness; they are
# classified sec_company by default. Foreign/non-US classification is only
# assigned when supporting metadata explicitly indicates it.
COMPANY_CLASS_SEC_COMPANY: str = "sec_company"
COMPANY_CLASS_NON_SEC_COMPANY: str = "non_sec_company"
COMPANY_CLASS_FOREIGN_OR_UNSUPPORTED: str = "foreign_or_unsupported"
COMPANY_CLASS_UNKNOWN: str = "unknown"

# ── Gap reason enum constants (stable — used by callers for metrics) ──────────
GAP_NO_SEC_COMPANYFACTS_ARTIFACT: str = "no_sec_companyfacts_artifact"
GAP_NO_RESEARCH_ARTIFACT_FACTS: str = "no_research_artifact_facts"
GAP_NO_EPS_PAYLOAD_PRESENT: str = "no_eps_payload_present"
GAP_EPS_PAYLOAD_NO_FY_PERIOD: str = "eps_payload_present_but_no_fy_period"
GAP_FY_EPS_NOT_SOURCE_LINKED: str = "fy_eps_present_but_not_source_linked"
GAP_FY_EPS_MISSING_FISCAL_YEAR: str = "fy_eps_present_but_missing_fiscal_year"
GAP_FY_EPS_MISSING_NUMERIC_VALUE: str = "fy_eps_present_but_missing_numeric_value"
GAP_FY_EPS_INVALID_NUMERIC_VALUE: str = "fy_eps_present_but_invalid_numeric_value"
GAP_WRONG_UNIT_OR_FILTERED: str = "eps_tag_present_wrong_unit_or_filtered_before_storage"
GAP_UNSUPPORTED_FOREIGN: str = "unsupported_foreign_or_non_us_reporting"
GAP_UNSUPPORTED_NON_OPERATING: str = "unsupported_non_operating_company"
GAP_SOURCE_LINKAGE_GAP: str = "source_linkage_gap"
GAP_UNKNOWN_MANUAL_REVIEW: str = "unknown_gap_requires_manual_review"

_ALL_GAP_REASONS: tuple[str, ...] = (
    GAP_NO_SEC_COMPANYFACTS_ARTIFACT,
    GAP_NO_RESEARCH_ARTIFACT_FACTS,
    GAP_NO_EPS_PAYLOAD_PRESENT,
    GAP_EPS_PAYLOAD_NO_FY_PERIOD,
    GAP_FY_EPS_NOT_SOURCE_LINKED,
    GAP_FY_EPS_MISSING_FISCAL_YEAR,
    GAP_FY_EPS_MISSING_NUMERIC_VALUE,
    GAP_FY_EPS_INVALID_NUMERIC_VALUE,
    GAP_WRONG_UNIT_OR_FILTERED,
    GAP_UNSUPPORTED_FOREIGN,
    GAP_UNSUPPORTED_NON_OPERATING,
    GAP_SOURCE_LINKAGE_GAP,
    GAP_UNKNOWN_MANUAL_REVIEW,
)


@dataclass(frozen=True)
class TickerFyEpsGapInput:
    """Per-ticker inputs assembled by the router for gap classification.

    All values are pre-fetched from DB by the router. The pure classifier
    never touches IO.
    """
    ticker: str

    # Company classification — sourced from Phase 9 SEC readiness output.
    # All tickers reaching this classifier are classified sec_company unless
    # the router explicitly signals otherwise (reserved for future extension).
    company_classification: str = COMPANY_CLASS_SEC_COMPANY

    # Price / sector availability (from market_snapshots).
    has_price: bool = False
    has_sector: bool = False

    # Artifact presence (from research_artifacts table).
    has_any_sec_metric_artifact: bool = False

    # Fact presence (from research_artifact_facts, any fact_kind).
    has_any_fact: bool = False

    # EPS payload counts (from research_artifact_facts, EPS tags only).
    eps_payload_count: int = 0          # any period
    fy_eps_payload_count: int = 0       # FY period (fiscal_period=="FY" or form=="10-K")
    source_linked_fy_eps_count: int = 0  # FY + has source_id

    # Extraction skip reason counts for FY EPS rows that had source_ids but
    # still failed extraction (e.g. missing fiscal_year, missing value).
    fy_eps_skip_missing_year_count: int = 0
    fy_eps_skip_missing_value_count: int = 0

    # Computable FY EPS presence (final, post-extraction).
    has_computable_diluted_fy_eps: bool = False
    has_computable_basic_fy_eps: bool = False

    # Selected observation metadata (present when usable_fy_eps_for_yield).
    # Surfaced only in the cert-gated diagnostic endpoint — never in frontend.
    selected_eps_tag: str | None = None
    selected_eps_value: float | None = None
    selected_eps_fiscal_year: int | None = None
    selected_eps_form: str | None = None
    selected_eps_source_id_present: bool = False


@dataclass(frozen=True)
class TickerFyEpsGapDiagnostic:
    """Per-ticker gap diagnostic result (cert-gated, diagnostics-only).

    Exactly one primary gap_reason is assigned for missing tickers.
    gap_reason is None when usable_fy_eps_for_yield is True.
    """
    ticker: str
    company_classification: str
    has_price: bool
    has_sector: bool
    has_any_sec_metric_artifact: bool
    has_any_eps_payload: bool
    eps_payload_count: int
    has_fy_eps_payload: bool
    fy_eps_payload_count: int
    has_source_linked_fy_eps: bool
    usable_fy_eps_for_yield: bool
    selected_eps_tag: str | None
    selected_eps_value: float | None
    selected_eps_fiscal_year: int | None
    selected_eps_form: str | None
    selected_eps_source_id_present: bool
    gap_reason: str | None  # None when usable_fy_eps_for_yield is True


@dataclass(frozen=True)
class TickerFyEpsGapResult:
    """Aggregate result for ticker-level FY EPS gap diagnostics.

    ticker_gap_diagnostics is a list of per-ticker diagnostic objects.
    All aggregate counters are derived deterministically from the list.
    """
    classifier_version: str

    # Per-ticker diagnostics (cert-gated — never exposed to frontend).
    ticker_gap_diagnostics: list[Any]  # list[TickerFyEpsGapDiagnostic]

    # Aggregate counters.
    ticker_gap_diagnostics_count: int
    usable_fy_eps_ticker_count: int
    missing_fy_eps_ticker_count: int
    unsupported_or_excludable_ticker_count: int
    potentially_fixable_ticker_count: int

    # Stable gap_reason distribution (dict from _ALL_GAP_REASONS keys).
    gap_reason_counts: dict[str, int]

    errors: list[str] = field(default_factory=list)


def classify_ticker_fy_eps_gap(inp: TickerFyEpsGapInput) -> TickerFyEpsGapDiagnostic:
    """Classify one ticker's FY EPS gap using a deterministic decision tree.

    Returns a TickerFyEpsGapDiagnostic — never raises.
    """
    usable = inp.has_computable_diluted_fy_eps or inp.has_computable_basic_fy_eps
    gap_reason = None if usable else _classify_gap_reason(inp)

    return TickerFyEpsGapDiagnostic(
        ticker=inp.ticker,
        company_classification=inp.company_classification,
        has_price=inp.has_price,
        has_sector=inp.has_sector,
        has_any_sec_metric_artifact=inp.has_any_sec_metric_artifact,
        has_any_eps_payload=inp.eps_payload_count > 0,
        eps_payload_count=inp.eps_payload_count,
        has_fy_eps_payload=inp.fy_eps_payload_count > 0,
        fy_eps_payload_count=inp.fy_eps_payload_count,
        has_source_linked_fy_eps=inp.source_linked_fy_eps_count > 0,
        usable_fy_eps_for_yield=usable,
        selected_eps_tag=inp.selected_eps_tag if usable else None,
        selected_eps_value=inp.selected_eps_value if usable else None,
        selected_eps_fiscal_year=inp.selected_eps_fiscal_year if usable else None,
        selected_eps_form=inp.selected_eps_form if usable else None,
        selected_eps_source_id_present=inp.selected_eps_source_id_present if usable else False,
        gap_reason=gap_reason,
    )


def _classify_gap_reason(inp: TickerFyEpsGapInput) -> str:
    """Deterministic decision tree — exactly one gap_reason per missing ticker."""
    if not inp.has_any_sec_metric_artifact:
        return GAP_NO_SEC_COMPANYFACTS_ARTIFACT
    if not inp.has_any_fact:
        return GAP_NO_RESEARCH_ARTIFACT_FACTS
    if inp.eps_payload_count == 0:
        return GAP_NO_EPS_PAYLOAD_PRESENT
    if inp.fy_eps_payload_count == 0:
        return GAP_EPS_PAYLOAD_NO_FY_PERIOD
    if inp.source_linked_fy_eps_count == 0:
        return GAP_FY_EPS_NOT_SOURCE_LINKED
    # Has source-linked FY EPS but extraction still failed.
    # Distinguish by the most specific known skip reason.
    if inp.fy_eps_skip_missing_year_count > 0:
        return GAP_FY_EPS_MISSING_FISCAL_YEAR
    if inp.fy_eps_skip_missing_value_count > 0:
        return GAP_FY_EPS_MISSING_NUMERIC_VALUE
    # Source-linked FY EPS present but no computable result and no known skip.
    return GAP_SOURCE_LINKAGE_GAP


def build_ticker_fy_eps_gap_diagnostics(
    *,
    inputs: list[TickerFyEpsGapInput],
    extra_errors: list[str] | None = None,
) -> TickerFyEpsGapResult:
    """Build aggregate ticker-level FY EPS gap diagnostics.

    Args:
        inputs:        One TickerFyEpsGapInput per company ticker.
        extra_errors:  Non-fatal fetch errors from the router.

    Returns:
        TickerFyEpsGapResult — never raises.
    """
    try:
        return _build(inputs=inputs, extra_errors=list(extra_errors or []))
    except Exception as exc:  # noqa: BLE001
        return TickerFyEpsGapResult(
            classifier_version=TICKER_FY_EPS_GAP_CLASSIFIER_V1_CONTRACT_VERSION,
            ticker_gap_diagnostics=[],
            ticker_gap_diagnostics_count=0,
            usable_fy_eps_ticker_count=0,
            missing_fy_eps_ticker_count=0,
            unsupported_or_excludable_ticker_count=0,
            potentially_fixable_ticker_count=0,
            gap_reason_counts={r: 0 for r in _ALL_GAP_REASONS},
            errors=[f"build_error: {type(exc).__name__}: {exc}"],
        )


def _build(
    *,
    inputs: list[TickerFyEpsGapInput],
    extra_errors: list[str],
) -> TickerFyEpsGapResult:
    diagnostics: list[TickerFyEpsGapDiagnostic] = []
    gap_reason_counts: dict[str, int] = {r: 0 for r in _ALL_GAP_REASONS}

    usable_count = 0
    missing_count = 0

    for inp in inputs:
        diag = classify_ticker_fy_eps_gap(inp)
        diagnostics.append(diag)
        if diag.usable_fy_eps_for_yield:
            usable_count += 1
        else:
            missing_count += 1
            if diag.gap_reason and diag.gap_reason in gap_reason_counts:
                gap_reason_counts[diag.gap_reason] += 1

    # Unsupported/excludable: tickers with no artifact or classified foreign.
    # Potentially fixable: has some EPS data but a correctable gap.
    unsupported = sum(
        1 for d in diagnostics
        if not d.usable_fy_eps_for_yield
        and d.gap_reason in (
            GAP_NO_SEC_COMPANYFACTS_ARTIFACT,
            GAP_UNSUPPORTED_FOREIGN,
            GAP_UNSUPPORTED_NON_OPERATING,
        )
    )
    fixable = sum(
        1 for d in diagnostics
        if not d.usable_fy_eps_for_yield
        and d.gap_reason in (
            GAP_NO_RESEARCH_ARTIFACT_FACTS,
            GAP_NO_EPS_PAYLOAD_PRESENT,
            GAP_EPS_PAYLOAD_NO_FY_PERIOD,
            GAP_FY_EPS_NOT_SOURCE_LINKED,
            GAP_FY_EPS_MISSING_FISCAL_YEAR,
            GAP_FY_EPS_MISSING_NUMERIC_VALUE,
            GAP_FY_EPS_INVALID_NUMERIC_VALUE,
            GAP_WRONG_UNIT_OR_FILTERED,
            GAP_SOURCE_LINKAGE_GAP,
            GAP_UNKNOWN_MANUAL_REVIEW,
        )
    )

    return TickerFyEpsGapResult(
        classifier_version=TICKER_FY_EPS_GAP_CLASSIFIER_V1_CONTRACT_VERSION,
        ticker_gap_diagnostics=diagnostics,
        ticker_gap_diagnostics_count=len(diagnostics),
        usable_fy_eps_ticker_count=usable_count,
        missing_fy_eps_ticker_count=missing_count,
        unsupported_or_excludable_ticker_count=unsupported,
        potentially_fixable_ticker_count=fixable,
        gap_reason_counts=gap_reason_counts,
        errors=extra_errors,
    )
