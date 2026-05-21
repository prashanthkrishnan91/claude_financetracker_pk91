"""Stage 8B — Sentiment Evidence Quality Threshold v1.

Deterministic, pure quality threshold evaluator for news/sentiment evidence.
Makes the implicit editorial-context suppression explicit and testable, and
defines the criteria required for sentiment evidence to graduate to LIMITED
or READY.

Quality levels:
  READY:      All criteria pass; completeness is COMPLETE.
  LIMITED:    All criteria pass; completeness is PARTIAL (sufficiently complete
              with minor gaps).
  NOT_USABLE: One or more criteria fail — evidence remains suppressed or
              incomplete by design.

Design rationale:
  Current yfinance news produces catalyst_item facts with EDITORIAL_CONTEXT
  authority. EDITORIAL_CONTEXT is structurally too weak to be decision-useful
  regardless of item count or freshness — it is contextual evidence only.
  That suppression is correct and must be preserved.

  For a future data source that provides vendor-scored sentiment (e.g.,
  VENDOR_DERIVED authority + structured sentiment_score facts with
  claim_key="sentiment_polarity"), the pipeline already supports LIMITED/READY
  propagation through Stage 5J → 5K → snapshot. This module provides the
  explicit, auditable gate that verifies those conditions before allowing
  sentiment to influence evidence readiness.

Architecture contracts (non-negotiable):
  - Pure function: no IO, no DB reads, no LLM calls, no provider calls.
  - No Buy/Hold/Trim/Sell emitted.
  - safe_for_decision: never touched — this module is a quality evaluator only.
  - No fabrication: NOT_USABLE is conservative when inputs are ambiguous.
  - EDITORIAL_CONTEXT or UNKNOWN authority always yields NOT_USABLE.
  - THIN or NOT_EVALUABLE completeness always yields NOT_USABLE.
"""
from __future__ import annotations

from dataclasses import dataclass

SENTIMENT_QUALITY_THRESHOLD_VERSION = "sentiment_quality_threshold.v1"

# Sentinel missing_reason value used by Stage 5J for news_sentiment artifacts that are
# present but suppressed due to editorial-context authority only.
# Distinct from "usability_suppressed" (generic) and "suppressed_data_quality_issue"
# (e.g. contradictions), this value means: "we have news items but they are
# editorial-context only — correct suppression by design, not a data quality error."
SENTINEL_EDITORIAL_CONTEXT_REASON = "editorial_context_present_not_decision_useful"

# ── Authority level constants ──────────────────────────────────────────────────
# These are the string values stored in artifact payloads (AuthorityLevel enum).

# Authority levels too weak for sentiment to be decision-useful.
# Even multiple items from these sources remain editorial/contextual only.
WEAK_AUTHORITY_LEVELS: frozenset[str] = frozenset({
    "EDITORIAL_CONTEXT",
    "UNKNOWN",
})

# Authority levels sufficient for sentiment to be decision-useful.
# Requires at least VENDOR_DERIVED (a recognised financial data vendor).
SUFFICIENT_AUTHORITY_LEVELS: frozenset[str] = frozenset({
    "VENDOR_DERIVED",
    "COMPANY_AUTHORED",
    "PRIMARY_AUTHORITY",
})

# ── Completeness band constants ────────────────────────────────────────────────

# Completeness bands that disqualify sentiment from being decision-useful.
DISQUALIFYING_COMPLETENESS_BANDS: frozenset[str] = frozenset({
    "THIN",
    "NOT_EVALUABLE",
})

# Completeness bands that allow sentiment to be decision-useful (with level).
_READY_BAND = "COMPLETE"
_LIMITED_BAND = "PARTIAL"


# ── Result dataclass ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SentimentQualityResult:
    """Result of evaluating sentiment evidence against the quality threshold.

    Fields safe for diagnostics: no raw payloads, no source URLs, no API keys.
    """
    version: str
    quality_tier: str              # NOT_USABLE | LIMITED | READY
    is_decision_useful: bool
    failure_reasons: tuple[str, ...]  # empty when is_decision_useful is True
    notes: tuple[str, ...]            # human-readable explanation per failure


# ── Public API ────────────────────────────────────────────────────────────────


def evaluate_sentiment_quality(
    *,
    freshness_status: str | None,
    source_authority: str | None,
    completeness_band: str | None,
    is_contradicted: bool,
    source_count: int,
    fact_count: int,
) -> SentimentQualityResult:
    """Evaluate whether sentiment evidence meets decision-useful quality criteria.

    All criteria must pass for sentiment to be decision-useful:

      1. Freshness: must be FRESH (not STALE, UNKNOWN, or absent).
      2. Source quality: authority must be above weak editorial-only context
         (VENDOR_DERIVED, COMPANY_AUTHORED, or PRIMARY_AUTHORITY).
      3. Completeness: must be PARTIAL or COMPLETE (not THIN or NOT_EVALUABLE).
      4. No contradictions: contradicted evidence is not usable.
      5. Minimum coverage: at least one source and one fact.

    Returns NOT_USABLE with failure_reasons when any criterion fails.
    Returns LIMITED (PARTIAL completeness) or READY (COMPLETE completeness)
    when all criteria pass.

    Note: EDITORIAL_CONTEXT authority always fails criterion 2. Yfinance
    news headlines (catalyst_item facts) are EDITORIAL_CONTEXT by design
    and are correctly suppressed even when fresh and numerous.
    """
    failure_reasons: list[str] = []
    notes: list[str] = []

    # Criterion 1: Freshness.
    fresh_upper = (freshness_status or "").upper()
    if fresh_upper != "FRESH":
        failure_reasons.append(
            f"freshness_not_acceptable:{freshness_status or 'unknown'}"
        )
        notes.append(
            f"Freshness must be FRESH for sentiment to be decision-useful. "
            f"Got: {freshness_status or 'unknown'}."
        )

    # Criterion 2: Source authority above editorial/unknown context.
    if source_authority is None:
        failure_reasons.append("source_authority_unknown")
        notes.append("Source authority is not available — cannot evaluate quality.")
    elif source_authority in WEAK_AUTHORITY_LEVELS:
        failure_reasons.append(
            f"source_quality_too_weak:{source_authority}"
        )
        notes.append(
            f"Source authority '{source_authority}' is editorial or unknown context — "
            "sentiment from these sources is contextual only and never decision-useful. "
            "VENDOR_DERIVED or higher is required."
        )
    elif source_authority not in SUFFICIENT_AUTHORITY_LEVELS:
        # Unrecognised authority string — conservative: treat as weak.
        failure_reasons.append(
            f"source_authority_unrecognised:{source_authority}"
        )
        notes.append(
            f"Source authority '{source_authority}' is not in the known-sufficient set."
        )

    # Criterion 3: Completeness band.
    if completeness_band is None:
        failure_reasons.append("completeness_band_missing")
        notes.append("Completeness band is not available — cannot evaluate quality.")
    elif completeness_band in DISQUALIFYING_COMPLETENESS_BANDS:
        failure_reasons.append(
            f"completeness_too_weak:{completeness_band}"
        )
        notes.append(
            f"Completeness band '{completeness_band}' disqualifies sentiment from being "
            "decision-useful. PARTIAL or COMPLETE is required."
        )
    elif completeness_band not in (_READY_BAND, _LIMITED_BAND):
        failure_reasons.append(
            f"completeness_band_unrecognised:{completeness_band}"
        )
        notes.append(
            f"Completeness band '{completeness_band}' is not in the known-sufficient set."
        )

    # Criterion 4: No contradictions.
    if is_contradicted:
        failure_reasons.append("evidence_contradicted")
        notes.append(
            "Sentiment evidence contains contradictions — cannot be used for a decision."
        )

    # Criterion 5: Minimum coverage.
    if source_count < 1:
        failure_reasons.append("no_sources")
        notes.append("At least one source is required.")
    if fact_count < 1:
        failure_reasons.append("no_facts")
        notes.append("At least one fact is required.")

    if failure_reasons:
        return SentimentQualityResult(
            version=SENTIMENT_QUALITY_THRESHOLD_VERSION,
            quality_tier="NOT_USABLE",
            is_decision_useful=False,
            failure_reasons=tuple(failure_reasons),
            notes=tuple(notes),
        )

    # All criteria pass — quality tier from completeness band.
    quality_tier = "READY" if completeness_band == _READY_BAND else "LIMITED"
    return SentimentQualityResult(
        version=SENTIMENT_QUALITY_THRESHOLD_VERSION,
        quality_tier=quality_tier,
        is_decision_useful=True,
        failure_reasons=(),
        notes=(),
    )
