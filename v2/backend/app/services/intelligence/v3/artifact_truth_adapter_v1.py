"""Stage 5E — Artifact Truth Adapter v1.

Deterministic adapter that consumes source credibility, contradiction, and
evidence completeness metadata to determine whether a research artifact is
usable for downstream intelligence.

Architecture contracts (non-negotiable):
  - Pure function: no IO, no LLM, no external calls, no DB.
  - Replayable: same inputs always produce the same output.
  - Not a truth oracle. Not a recommendation engine.
  - Never sets safe_for_decision=True.
  - Never imports or calls decide() from decision_policy_v1.
  - Does NOT write to intel_v3_snapshots or any visible-decision table.
  - Determines usability from already-computed enrichment metadata only.
  - Designed for write-path integration inside ResearchArtifactServiceV1.

Usability labels (exhaustive):
  USABLE:                  Credible, non-contradicted, sufficiently complete evidence.
  USABLE_WITH_LIMITATIONS: Partial but usable evidence (PARTIAL completeness band).
  SUPPRESSED_INCOMPLETE:   Insufficient evidence completeness (THIN band).
  SUPPRESSED_CONTRADICTED: Material contradiction detected in facts.
  SUPPRESSED_UNKNOWN_SOURCE: Unknown/untrusted source credibility only.
  NOT_EVALUABLE:           Missing or malformed enrichment metadata.

Label priority (first match wins — higher beats lower):
  1. NOT_EVALUABLE — any input None, or completeness_band == NOT_EVALUABLE.
  2. SUPPRESSED_CONTRADICTED — explicit contradiction beats source/completeness issues.
  3. SUPPRESSED_UNKNOWN_SOURCE — known unknown-only source, more specific than incomplete.
  4. SUPPRESSED_INCOMPLETE — THIN completeness band (structurally weak evidence).
  5. USABLE_WITH_LIMITATIONS — PARTIAL completeness band (useful, some gaps).
  6. USABLE — COMPLETE band, credible sources, no contradictions.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

from app.services.intelligence.v3.contradiction_detector_v1 import (
    ContradictionAssessment,
)
from app.services.intelligence.v3.evidence_completeness_scorer_v1 import (
    BAND_COMPLETE,
    BAND_NOT_EVALUABLE,
    BAND_PARTIAL,
    BAND_THIN,
    EvidenceCompletenessAssessment,
)
from app.services.intelligence.v3.source_credibility_registry_v1 import (
    SourceCredibilityAssessment,
)

ARTIFACT_TRUTH_ADAPTER_VERSION = "artifact_truth_adapter.v1"


# ── Usability labels ──────────────────────────────────────────────────────────


class ArtifactUsabilityLabel(str, Enum):
    """Exhaustive set of deterministic usability labels for a research artifact."""
    USABLE = "USABLE"
    USABLE_WITH_LIMITATIONS = "USABLE_WITH_LIMITATIONS"
    SUPPRESSED_INCOMPLETE = "SUPPRESSED_INCOMPLETE"
    SUPPRESSED_CONTRADICTED = "SUPPRESSED_CONTRADICTED"
    SUPPRESSED_UNKNOWN_SOURCE = "SUPPRESSED_UNKNOWN_SOURCE"
    NOT_EVALUABLE = "NOT_EVALUABLE"


# Labels that permit downstream consumption (with appropriate caveats).
_USABLE_LABELS = frozenset({
    ArtifactUsabilityLabel.USABLE,
    ArtifactUsabilityLabel.USABLE_WITH_LIMITATIONS,
})


# ── Assessment dataclass ──────────────────────────────────────────────────────


@dataclass
class ArtifactUsabilityAssessment:
    """Deterministic usability assessment for one research artifact.

    Replayable: same enrichment inputs always produce the same assessment.
    Never contains Buy/Hold/Trim/Sell, price target, conviction, or allocation.

    Invariants:
      - is_usable is True only for USABLE and USABLE_WITH_LIMITATIONS.
      - suppression_reason is None for USABLE and USABLE_WITH_LIMITATIONS.
      - suppression_reason is set for all suppressed/not-evaluable labels.
      - no_guessing is always True (deterministic-safety indicator).
    """
    adapter_version: str
    usability_label: str          # ArtifactUsabilityLabel value string
    is_usable: bool               # True for USABLE and USABLE_WITH_LIMITATIONS only
    suppression_reason: Optional[str]
    limitations: List[str]
    no_guessing: bool = True      # Always True

    def to_dict(self) -> Dict[str, Any]:
        """Plain dict for JSON serialization into artifact payload.

        Key 'truth_usability_assessment' is NOT in WORKER_FORBIDDEN_PAYLOAD_KEYS.
        """
        return {
            "adapter_version": self.adapter_version,
            "usability_label": self.usability_label,
            "is_usable": self.is_usable,
            "suppression_reason": self.suppression_reason,
            "limitations": self.limitations,
            "no_guessing": self.no_guessing,
        }


# ── Limitation text constants ─────────────────────────────────────────────────

_LIMITATION_NOT_EVALUABLE = (
    "Usability is NOT_EVALUABLE: enrichment metadata is missing, malformed, "
    "or no sources and no facts were provided."
)
_LIMITATION_CONTRADICTED = (
    "Artifact is SUPPRESSED_CONTRADICTED: explicit structured contradiction detected "
    "in artifact facts. Contradiction resolution is not performed by this adapter. "
    "The artifact must be re-evaluated with resolved or additional evidence."
)
_LIMITATION_UNKNOWN_SOURCE = (
    "Artifact is SUPPRESSED_UNKNOWN_SOURCE: all sources have UNKNOWN authority level. "
    "No recognized source kind can establish credibility for the artifact's claims."
)
_LIMITATION_INCOMPLETE = (
    "Artifact is SUPPRESSED_INCOMPLETE: evidence completeness is THIN. "
    "One or more major structural requirements (sources, facts, comparable claims, "
    "non-unknown source authority) are missing."
)
_LIMITATION_USABLE_WITH_LIMITATIONS = (
    "Artifact is USABLE_WITH_LIMITATIONS: evidence completeness is PARTIAL. "
    "Evidence is useful but missing one or more non-critical requirements "
    "(e.g. time context, quote grounding, or contradictions present)."
)
_LIMITATION_USABLE = (
    "Artifact is USABLE: evidence is credible, non-contradicted, and sufficiently complete."
)
_LIMITATION_COMMON = (
    "Usability is determined solely from structured enrichment metadata. "
    "Prose quality, narrative coherence, and real-world outcome are not evaluated. "
    "Usability does not imply a Buy/Hold/Trim/Sell recommendation or price target."
)


# ── Public API ────────────────────────────────────────────────────────────────


def assess_artifact_usability(
    credibility: Optional[SourceCredibilityAssessment],
    contradiction: Optional[ContradictionAssessment],
    completeness: Optional[EvidenceCompletenessAssessment],
) -> ArtifactUsabilityAssessment:
    """Deterministically assess the usability of a research artifact.

    Args:
        credibility:  Output from assess_artifact_sources() (Stage 5B).
                      None → NOT_EVALUABLE.
        contradiction: Output from detect_contradictions() (Stage 5C).
                      None → NOT_EVALUABLE.
        completeness: Output from score_evidence_completeness() (Stage 5D).
                      None → NOT_EVALUABLE.

    Returns:
        ArtifactUsabilityAssessment — always non-None, fully replayable.
        Same inputs always produce the same output.

    Priority (first match wins):
        1. NOT_EVALUABLE   — any input None, or completeness=NOT_EVALUABLE.
        2. SUPPRESSED_CONTRADICTED — explicit contradiction in facts.
        3. SUPPRESSED_UNKNOWN_SOURCE — all sources are UNKNOWN authority.
        4. SUPPRESSED_INCOMPLETE — completeness is THIN.
        5. USABLE_WITH_LIMITATIONS — completeness is PARTIAL.
        6. USABLE — completeness is COMPLETE, credible, no contradictions.
    """
    # ── Priority 1: NOT_EVALUABLE ─────────────────────────────────────────────
    if credibility is None or contradiction is None or completeness is None:
        return _make_assessment(
            ArtifactUsabilityLabel.NOT_EVALUABLE,
            suppression_reason="missing_enrichment_metadata",
            primary_limitation=_LIMITATION_NOT_EVALUABLE,
        )

    completeness_band = completeness.completeness_band
    if completeness_band == BAND_NOT_EVALUABLE:
        return _make_assessment(
            ArtifactUsabilityLabel.NOT_EVALUABLE,
            suppression_reason="completeness_not_evaluable:no_sources_and_no_facts",
            primary_limitation=_LIMITATION_NOT_EVALUABLE,
        )

    if completeness_band not in (BAND_COMPLETE, BAND_PARTIAL, BAND_THIN, BAND_NOT_EVALUABLE):
        return _make_assessment(
            ArtifactUsabilityLabel.NOT_EVALUABLE,
            suppression_reason=f"unknown_completeness_band:{completeness_band}",
            primary_limitation=_LIMITATION_NOT_EVALUABLE,
        )

    # ── Priority 2: SUPPRESSED_CONTRADICTED ───────────────────────────────────
    if contradiction.is_evaluable and contradiction.has_contradictions:
        return _make_assessment(
            ArtifactUsabilityLabel.SUPPRESSED_CONTRADICTED,
            suppression_reason=(
                f"material_contradiction_detected:contradiction_count="
                f"{contradiction.contradiction_count}"
            ),
            primary_limitation=_LIMITATION_CONTRADICTED,
        )

    # ── Priority 3: SUPPRESSED_UNKNOWN_SOURCE ─────────────────────────────────
    if credibility.is_insufficient:
        return _make_assessment(
            ArtifactUsabilityLabel.SUPPRESSED_UNKNOWN_SOURCE,
            suppression_reason=(
                f"all_sources_unknown_authority:source_count="
                f"{credibility.source_count}"
            ),
            primary_limitation=_LIMITATION_UNKNOWN_SOURCE,
        )

    # ── Priority 4: SUPPRESSED_INCOMPLETE ────────────────────────────────────
    if completeness_band == BAND_THIN:
        return _make_assessment(
            ArtifactUsabilityLabel.SUPPRESSED_INCOMPLETE,
            suppression_reason=(
                f"evidence_completeness_thin:missing_requirements="
                f"{','.join(completeness.missing_requirements) or 'none_listed'}"
            ),
            primary_limitation=_LIMITATION_INCOMPLETE,
        )

    # ── Priority 5: USABLE_WITH_LIMITATIONS ──────────────────────────────────
    if completeness_band == BAND_PARTIAL:
        return _make_assessment(
            ArtifactUsabilityLabel.USABLE_WITH_LIMITATIONS,
            suppression_reason=None,
            primary_limitation=_LIMITATION_USABLE_WITH_LIMITATIONS,
        )

    # ── Priority 6: USABLE ───────────────────────────────────────────────────
    # completeness_band == BAND_COMPLETE, credibility not insufficient,
    # no contradictions — all positive conditions confirmed above.
    return _make_assessment(
        ArtifactUsabilityLabel.USABLE,
        suppression_reason=None,
        primary_limitation=_LIMITATION_USABLE,
    )


# ── Internal helpers ──────────────────────────────────────────────────────────


def _make_assessment(
    label: ArtifactUsabilityLabel,
    *,
    suppression_reason: Optional[str],
    primary_limitation: str,
) -> ArtifactUsabilityAssessment:
    return ArtifactUsabilityAssessment(
        adapter_version=ARTIFACT_TRUTH_ADAPTER_VERSION,
        usability_label=label.value,
        is_usable=label in _USABLE_LABELS,
        suppression_reason=suppression_reason,
        limitations=[primary_limitation, _LIMITATION_COMMON],
        no_guessing=True,
    )
