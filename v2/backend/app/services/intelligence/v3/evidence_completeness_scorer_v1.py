"""Stage 5D — Evidence Completeness Scorer v1.

Deterministic, typed completeness scorer for research artifact evidence.
No LLM calls, no external API calls, no IO, no semantic inference.

Architecture contracts (non-negotiable):
  - Pure function: no IO, no LLM, no external calls.
  - Replayable: same inputs always produce the same output.
  - No fake numeric 0–100 scores. Bands only.
  - No final Buy/Hold/Trim/Sell, price target, conviction, allocation, or
    broker action emitted.
  - No contradiction resolution — only uses contradiction metadata as a
    completeness penalty.
  - No inferred completeness from prose quality. Structured fields only.
  - safe_for_decision is never touched.
  - Contradiction resolution deferred to Stage 5E truth adapter.

Completeness bands:
  COMPLETE:      sources + facts + structured fields + time context +
                 no contradictions + sufficient quote grounding.
  PARTIAL:       Useful but missing one or more non-critical requirements.
  THIN:          Source/fact exists but weak, unknown, non-comparable, or
                 missing major requirements.
  NOT_EVALUABLE: No sources AND no facts, or no structured evidence.

Requirements evaluated (present / missing / not_applicable):
  has_at_least_one_source
  has_known_or_contextual_source_credibility
  has_at_least_one_fact
  has_structured_claim_key_or_metric_name
  has_time_context_period_or_as_of
  has_quote_grounded_fact
  has_no_detected_contradictions
  has_comparable_fact_when_claim_is_metric_like

Hard rules:
  - Contradicted artifacts cannot be COMPLETE.
  - Unknown-only source artifacts cannot be COMPLETE (→ THIN).
  - Editorial-only source artifacts cannot be COMPLETE (→ THIN).
  - Non-comparable facts (no structured claim_key/value) cannot reach COMPLETE.
  - THIN is preferred over PARTIAL when evidence is structurally weak.
  - If a requirement is not applicable, mark it not_applicable (never missing).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.services.intelligence.v3.contradiction_detector_v1 import (
    ContradictionAssessment,
)
from app.services.intelligence.v3.source_credibility_registry_v1 import (
    AuthorityLevel,
    SourceCredibilityAssessment,
)

EVIDENCE_COMPLETENESS_SCORER_VERSION = "evidence_completeness_scorer.v1"

# ── Bands ─────────────────────────────────────────────────────────────────────

BAND_COMPLETE = "COMPLETE"
BAND_PARTIAL = "PARTIAL"
BAND_THIN = "THIN"
BAND_NOT_EVALUABLE = "NOT_EVALUABLE"

# ── Requirement status labels ─────────────────────────────────────────────────

REQ_PRESENT = "present"
REQ_MISSING = "missing"
REQ_NOT_APPLICABLE = "not_applicable"

# Canonical requirement names (order matters for deterministic output).
_REQUIREMENT_NAMES: List[str] = [
    "has_at_least_one_source",
    "has_known_or_contextual_source_credibility",
    "has_at_least_one_fact",
    "has_structured_claim_key_or_metric_name",
    "has_time_context_period_or_as_of",
    "has_quote_grounded_fact",
    "has_no_detected_contradictions",
    "has_comparable_fact_when_claim_is_metric_like",
]

# Authority levels that prevent COMPLETE when they are the strongest present.
_THIN_CAP_AUTHORITY_LEVELS = frozenset({
    AuthorityLevel.EDITORIAL_CONTEXT.value,
    AuthorityLevel.UNKNOWN.value,
})


# ── Assessment dataclass ──────────────────────────────────────────────────────


@dataclass
class EvidenceCompletenessAssessment:
    """Deterministic completeness assessment for one artifact's evidence.

    Replayable: same sources/facts/assessments always produce the same output.
    Never contains Buy/Hold/Trim/Sell, price target, conviction, or allocation.
    """
    scorer_version: str
    is_evaluable: bool
    completeness_band: str           # COMPLETE | PARTIAL | THIN | NOT_EVALUABLE
    source_count: int
    fact_count: int
    quote_grounded_fact_count: int
    comparable_fact_count: int       # from contradiction assessment
    contradiction_count: Optional[int]  # None when contradiction not evaluable
    missing_requirements: List[str]
    present_requirements: List[str]
    not_applicable_requirements: List[str]
    per_fact_assessments: List[Dict[str, Any]]
    limitations: List[str]
    no_guessing: bool = True         # Always True — deterministic-safety indicator

    def to_dict(self) -> Dict[str, Any]:
        """Plain dict for JSON serialization into artifact payload.

        Key 'evidence_completeness_assessment' is NOT in WORKER_FORBIDDEN_PAYLOAD_KEYS.
        """
        return {
            "scorer_version": self.scorer_version,
            "is_evaluable": self.is_evaluable,
            "completeness_band": self.completeness_band,
            "source_count": self.source_count,
            "fact_count": self.fact_count,
            "quote_grounded_fact_count": self.quote_grounded_fact_count,
            "comparable_fact_count": self.comparable_fact_count,
            "contradiction_count": self.contradiction_count,
            "missing_requirements": self.missing_requirements,
            "present_requirements": self.present_requirements,
            "not_applicable_requirements": self.not_applicable_requirements,
            "per_fact_assessments": self.per_fact_assessments,
            "limitations": self.limitations,
            "no_guessing": self.no_guessing,
        }


# ── Internal helpers ──────────────────────────────────────────────────────────


def _evaluate_requirements(
    *,
    source_count: int,
    fact_count: int,
    strongest_authority_level: str,
    is_insufficient: bool,
    has_structured_claim: bool,
    comparable_fact_count: int,
    has_time_context: bool,
    quote_grounded_fact_count: int,
    contradiction_is_evaluable: bool,
    has_contradictions: bool,
) -> Dict[str, str]:
    """Return ordered dict of requirement → status (present/missing/not_applicable)."""
    req: Dict[str, str] = {}

    # 1. has_at_least_one_source
    req["has_at_least_one_source"] = (
        REQ_PRESENT if source_count >= 1 else REQ_MISSING
    )

    # 2. has_known_or_contextual_source_credibility
    if source_count == 0:
        req["has_known_or_contextual_source_credibility"] = REQ_NOT_APPLICABLE
    elif strongest_authority_level != AuthorityLevel.UNKNOWN.value:
        req["has_known_or_contextual_source_credibility"] = REQ_PRESENT
    else:
        req["has_known_or_contextual_source_credibility"] = REQ_MISSING

    # 3. has_at_least_one_fact
    req["has_at_least_one_fact"] = (
        REQ_PRESENT if fact_count >= 1 else REQ_MISSING
    )

    # 4. has_structured_claim_key_or_metric_name
    if fact_count == 0:
        req["has_structured_claim_key_or_metric_name"] = REQ_NOT_APPLICABLE
    elif has_structured_claim:
        req["has_structured_claim_key_or_metric_name"] = REQ_PRESENT
    else:
        req["has_structured_claim_key_or_metric_name"] = REQ_MISSING

    # 5. has_time_context_period_or_as_of
    if fact_count == 0:
        req["has_time_context_period_or_as_of"] = REQ_NOT_APPLICABLE
    elif has_time_context:
        req["has_time_context_period_or_as_of"] = REQ_PRESENT
    else:
        req["has_time_context_period_or_as_of"] = REQ_MISSING

    # 6. has_quote_grounded_fact
    if fact_count == 0:
        req["has_quote_grounded_fact"] = REQ_NOT_APPLICABLE
    elif quote_grounded_fact_count >= 1:
        req["has_quote_grounded_fact"] = REQ_PRESENT
    else:
        req["has_quote_grounded_fact"] = REQ_MISSING

    # 7. has_no_detected_contradictions
    if not contradiction_is_evaluable:
        req["has_no_detected_contradictions"] = REQ_NOT_APPLICABLE
    elif not has_contradictions:
        req["has_no_detected_contradictions"] = REQ_PRESENT
    else:
        req["has_no_detected_contradictions"] = REQ_MISSING

    # 8. has_comparable_fact_when_claim_is_metric_like
    if fact_count == 0:
        req["has_comparable_fact_when_claim_is_metric_like"] = REQ_NOT_APPLICABLE
    elif comparable_fact_count >= 1:
        req["has_comparable_fact_when_claim_is_metric_like"] = REQ_PRESENT
    else:
        req["has_comparable_fact_when_claim_is_metric_like"] = REQ_MISSING

    return req


def _compute_band(
    *,
    source_count: int,
    fact_count: int,
    is_insufficient: bool,
    strongest_authority_level: str,
    comparable_fact_count: int,
    has_contradictions: bool,
    has_time_context: bool,
    quote_grounded_fact_count: int,
) -> str:
    """Return the completeness band using deterministic, conservative rules."""
    # NOT_EVALUABLE: nothing to evaluate.
    if source_count == 0 and fact_count == 0:
        return BAND_NOT_EVALUABLE

    # THIN: structurally weak or missing major requirements.
    if source_count == 0:
        return BAND_THIN
    if fact_count == 0:
        return BAND_THIN
    if is_insufficient:
        # All sources are UNKNOWN — cannot reason about credibility.
        return BAND_THIN
    if strongest_authority_level in _THIN_CAP_AUTHORITY_LEVELS:
        # Editorial/news-only or unknown-only sources are too weak for financial facts.
        return BAND_THIN
    if comparable_fact_count == 0:
        # No structured, comparable facts — completeness cannot be assessed.
        return BAND_THIN

    # PARTIAL: has structure but missing one or more significant requirements.
    if has_contradictions:
        # Contradictions prevent COMPLETE — resolution deferred to Stage 5E.
        return BAND_PARTIAL
    if not has_time_context:
        # No period or as_of on any fact — time anchor missing.
        return BAND_PARTIAL
    if quote_grounded_fact_count == 0:
        # No quote-grounded facts — grounding missing.
        return BAND_PARTIAL

    return BAND_COMPLETE


def _build_limitations(band: str, missing: List[str]) -> List[str]:
    """Return a deterministic limitations list based on band and missing requirements."""
    base = [
        "Completeness scoring is limited to explicitly structured evidence fields. "
        "Prose quality, narrative coherence, and source reputation beyond "
        "source_kind classification are not evaluated.",
        "Quote grounding is assessed by the is_quote_grounded flag on FactRecord. "
        "The scorer does not read or verify quote text.",
        "Time context (period/as_of) is assessed from explicitly structured fields only. "
        "Dates mentioned in prose or fact text values are not parsed.",
        "Completeness band is conservative. THIN is preferred over PARTIAL when "
        "evidence is structurally weak or source credibility is insufficient.",
        "Contradiction resolution is deferred to Stage 5E truth adapter. "
        "This scorer treats detected contradictions as a completeness penalty only.",
    ]
    if band == BAND_NOT_EVALUABLE:
        base.insert(0, "No sources and no facts were provided. Completeness is NOT_EVALUABLE.")
    elif band == BAND_THIN:
        base.insert(0, "Evidence is structurally weak (THIN). Major requirements are missing.")
    if missing:
        base.append(f"Missing requirements: {', '.join(missing)}.")
    return base


# ── Public API ────────────────────────────────────────────────────────────────


def score_evidence_completeness(
    sources: List[Any],
    facts: List[Any],
    credibility_assessment: SourceCredibilityAssessment,
    contradiction_assessment: ContradictionAssessment,
) -> EvidenceCompletenessAssessment:
    """Deterministically score evidence completeness for a research artifact.

    Args:
        sources:   List of SourceRecord-compatible objects.
        facts:     List of FactRecord-compatible objects.
        credibility_assessment: Output from assess_artifact_sources() (Stage 5B).
        contradiction_assessment: Output from detect_contradictions() (Stage 5C).

    Returns:
        EvidenceCompletenessAssessment — always non-None, fully replayable.
        Same inputs always produce the same output.

    Hard invariants:
        - Contradicted artifacts cannot be COMPLETE.
        - Unknown-only source artifacts cannot be COMPLETE (→ THIN).
        - Editorial-only source artifacts cannot be COMPLETE (→ THIN).
        - Non-comparable facts cannot reach COMPLETE.
        - NOT_EVALUABLE when no sources AND no facts.
    """
    source_count = len(sources)
    fact_count = len(facts)

    # Pull credibility metadata.
    strongest_authority_level = credibility_assessment.strongest_authority_level
    is_insufficient = credibility_assessment.is_insufficient

    # Pull contradiction metadata.
    contradiction_is_evaluable = contradiction_assessment.is_evaluable
    comparable_fact_count = contradiction_assessment.comparable_fact_count
    has_contradictions = contradiction_assessment.has_contradictions
    contradiction_count: Optional[int] = (
        contradiction_assessment.contradiction_count
        if contradiction_is_evaluable
        else None
    )

    # Per-fact structural analysis (deterministic, no fact values read).
    quote_grounded_fact_count = 0
    has_time_context = False
    has_structured_claim = False
    per_fact_assessments: List[Dict[str, Any]] = []

    for i, fact in enumerate(facts):
        sp: Dict[str, Any] = getattr(fact, "structured_payload", {}) or {}
        period = getattr(fact, "period", None) or sp.get("period")
        as_of = getattr(fact, "as_of", None) or sp.get("as_of")
        is_qg: bool = bool(getattr(fact, "is_quote_grounded", False))

        ck = sp.get("claim_key") or sp.get("metric_name")
        fact_has_claim = bool(ck and str(ck).strip())
        fact_has_value = any(
            k in sp for k in ("value", "value_normalized", "boolean_value", "text_value")
        )
        fact_has_time = bool(period or as_of)

        if is_qg:
            quote_grounded_fact_count += 1
        if fact_has_time:
            has_time_context = True
        if fact_has_claim:
            has_structured_claim = True

        per_fact_assessments.append({
            "fact_index": i,
            "fact_kind": getattr(fact, "fact_kind", "") or "",
            "has_claim_key": fact_has_claim,
            "has_value_field": fact_has_value,
            "is_comparable": fact_has_claim and fact_has_value,
            "has_time_context": fact_has_time,
            "is_quote_grounded": is_qg,
        })

    # Evaluate all requirements.
    req_statuses = _evaluate_requirements(
        source_count=source_count,
        fact_count=fact_count,
        strongest_authority_level=strongest_authority_level,
        is_insufficient=is_insufficient,
        has_structured_claim=has_structured_claim,
        comparable_fact_count=comparable_fact_count,
        has_time_context=has_time_context,
        quote_grounded_fact_count=quote_grounded_fact_count,
        contradiction_is_evaluable=contradiction_is_evaluable,
        has_contradictions=has_contradictions,
    )

    # Split into present / missing / not_applicable (stable insertion order).
    present = [r for r in _REQUIREMENT_NAMES if req_statuses.get(r) == REQ_PRESENT]
    missing = [r for r in _REQUIREMENT_NAMES if req_statuses.get(r) == REQ_MISSING]
    not_applicable = [r for r in _REQUIREMENT_NAMES if req_statuses.get(r) == REQ_NOT_APPLICABLE]

    band = _compute_band(
        source_count=source_count,
        fact_count=fact_count,
        is_insufficient=is_insufficient,
        strongest_authority_level=strongest_authority_level,
        comparable_fact_count=comparable_fact_count,
        has_contradictions=has_contradictions,
        has_time_context=has_time_context,
        quote_grounded_fact_count=quote_grounded_fact_count,
    )

    return EvidenceCompletenessAssessment(
        scorer_version=EVIDENCE_COMPLETENESS_SCORER_VERSION,
        is_evaluable=(band != BAND_NOT_EVALUABLE),
        completeness_band=band,
        source_count=source_count,
        fact_count=fact_count,
        quote_grounded_fact_count=quote_grounded_fact_count,
        comparable_fact_count=comparable_fact_count,
        contradiction_count=contradiction_count,
        missing_requirements=missing,
        present_requirements=present,
        not_applicable_requirements=not_applicable,
        per_fact_assessments=per_fact_assessments,
        limitations=_build_limitations(band, missing),
        no_guessing=True,
    )
