"""Phase 5 — Backend-only Truth Adapter Readiness Contract.

Purpose:
    Define and evaluate when a research artifact would be eligible for future
    deterministic consumption by the Intel v3 policy (truth adapter).

    This module answers: "Does this artifact satisfy all structural
    preconditions for the truth adapter?" It does NOT consume artifacts into
    any decision path now.

Phase 5 hard constraints (non-negotiable):
    - Does NOT import or call decide() from decision_policy_v1.
    - Does NOT modify research_artifacts, intel_v3_snapshots, or any table.
    - Does NOT set safe_for_decision=True anywhere.
    - Does NOT run on page load — pure function, explicit invocation only.
    - eligible_for_decision_consumption is always False in Phase 5.
    - fail_closed is always True — any ambiguity makes the artifact ineligible.
    - safe_for_decision_db_promotion_blocked is always True in Phase 5.

Current production status (Phase 4 artifacts):
    All current artifacts have confidence_or_trust_level=UNKNOWN,
    freshness_status=UNKNOWN, and zero sources. They fail conditions 4, 5,
    and 6 respectively and remain excluded by this contract.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from .contracts import _has_forbidden_key

# ── Supported registries ──────────────────────────────────────────────────────
# Fail-closed for any type/pack not registered here.
# Add entries only when a worker has been implemented and certified.
SUPPORTED_ARTIFACT_TYPES: frozenset[str] = frozenset({"catalyst_window"})
SUPPORTED_SKILL_PACKS: frozenset[str] = frozenset({"earnings_reviewer"})

# Explicit known classifications — UNKNOWN/empty/null are not acceptable.
VALID_CONFIDENCE_LEVELS: frozenset[str] = frozenset({"HIGH", "MEDIUM", "LOW"})
VALID_FRESHNESS_STATUSES: frozenset[str] = frozenset({"FRESH", "STALE"})


@dataclass
class ArtifactReadinessResult:
    """Compact readiness evaluation result for one research artifact.

    All fields are machine-readable and safe to log.
    No raw payloads, source URLs, quotes, or secrets are included.

    Phase 5 invariants (always enforced):
        eligible_for_decision_consumption = False
        fail_closed = True
        safe_for_decision_db_promotion_blocked = True
    """
    eligible_for_truth_adapter: bool
    eligible_for_decision_consumption: bool  # always False in Phase 5
    fail_closed: bool                        # always True
    artifact_id: Optional[str]
    ticker: Optional[str]
    artifact_type: Optional[str]
    skill_pack: Optional[str]
    reason_codes: list[str]
    source_count: int
    fact_count: int
    confidence_or_trust_level: Optional[str]
    freshness_status: Optional[str]
    forbidden_payload_violation: bool
    safe_for_decision_db_promotion_blocked: bool  # always True in Phase 5
    notes: Optional[str] = None


def evaluate_artifact_truth_readiness(
    artifact: Any,
    sources: Optional[list[dict]] = None,
    facts: Optional[list[dict]] = None,
) -> ArtifactReadinessResult:
    """Evaluate whether an artifact satisfies the Phase 5 truth adapter readiness contract.

    Pure, read-only, deterministic. No DB calls. No external calls. Never raises.
    Any exception triggers fail-closed (ineligible).

    Args:
        artifact: Dict of artifact fields (research_artifacts row or test fixture).
        sources:  Source dicts for this artifact (research_artifact_sources rows).
        facts:    Fact dicts for this artifact (research_artifact_facts rows).

    Returns:
        ArtifactReadinessResult. eligible_for_decision_consumption is always False.
    """
    try:
        return _evaluate(artifact, list(sources or []), list(facts or []))
    except Exception as exc:  # noqa: BLE001
        return ArtifactReadinessResult(
            eligible_for_truth_adapter=False,
            eligible_for_decision_consumption=False,
            fail_closed=True,
            artifact_id=None,
            ticker=None,
            artifact_type=None,
            skill_pack=None,
            reason_codes=["exception_fail_closed"],
            source_count=0,
            fact_count=0,
            confidence_or_trust_level=None,
            freshness_status=None,
            forbidden_payload_violation=False,
            safe_for_decision_db_promotion_blocked=True,
            notes=f"exception={type(exc).__name__}",
        )


# ── Internal evaluation ───────────────────────────────────────────────────────

def _evaluate(
    artifact: Any,
    sources: list[dict],
    facts: list[dict],
) -> ArtifactReadinessResult:
    """Core readiness evaluation — all 12 conditions checked."""
    reason_codes: list[str] = []

    if not isinstance(artifact, dict):
        return ArtifactReadinessResult(
            eligible_for_truth_adapter=False,
            eligible_for_decision_consumption=False,
            fail_closed=True,
            artifact_id=None,
            ticker=None,
            artifact_type=None,
            skill_pack=None,
            reason_codes=["artifact_not_a_dict"],
            source_count=0,
            fact_count=0,
            confidence_or_trust_level=None,
            freshness_status=None,
            forbidden_payload_violation=False,
            safe_for_decision_db_promotion_blocked=True,
            notes="artifact_not_a_dict",
        )

    artifact_id = artifact.get("id")
    ticker = artifact.get("ticker")
    artifact_type = artifact.get("artifact_type")
    skill_pack = artifact.get("skill_pack")
    is_active = artifact.get("is_active")
    invalidated_at = artifact.get("invalidated_at")
    expires_at_raw = artifact.get("expires_at")
    confidence_raw = artifact.get("confidence_or_trust_level")
    freshness_raw = artifact.get("freshness_status")
    payload = artifact.get("payload")

    confidence_str = str(confidence_raw).strip().upper() if confidence_raw is not None else None
    freshness_str = str(freshness_raw).strip().upper() if freshness_raw is not None else None
    artifact_type_str = str(artifact_type).strip() if artifact_type is not None else None
    skill_pack_str = str(skill_pack).strip() if skill_pack is not None else None

    # ── Condition 1: Active and not invalidated ───────────────────────────────
    if not is_active:
        reason_codes.append("not_active")
    if invalidated_at is not None:
        reason_codes.append("invalidated")

    # ── Condition 2: Not expired ──────────────────────────────────────────────
    if expires_at_raw is not None:
        try:
            exp_str = str(expires_at_raw).replace("Z", "+00:00")
            exp = datetime.fromisoformat(exp_str)
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if exp < datetime.now(timezone.utc):
                reason_codes.append("expired")
        except Exception:  # noqa: BLE001
            reason_codes.append("expires_at_malformed")

    # ── Condition 3: Known supported artifact_type and skill_pack ─────────────
    if not artifact_type_str or artifact_type_str not in SUPPORTED_ARTIFACT_TYPES:
        reason_codes.append("unsupported_artifact_type")
    if not skill_pack_str or skill_pack_str not in SUPPORTED_SKILL_PACKS:
        reason_codes.append("unsupported_skill_pack")

    # ── Condition 4: confidence_or_trust_level known and classified ───────────
    if not confidence_str or confidence_str not in VALID_CONFIDENCE_LEVELS:
        reason_codes.append("unknown_or_invalid_confidence")

    # ── Condition 5: freshness_status known and classified ────────────────────
    if not freshness_str or freshness_str not in VALID_FRESHNESS_STATUSES:
        reason_codes.append("unknown_or_invalid_freshness")

    # ── Valid sources and facts ───────────────────────────────────────────────
    valid_sources = _valid_sources(sources)
    valid_facts = _valid_facts(facts)
    valid_source_ids = {str(s.get("id")) for s in valid_sources if s.get("id") is not None}

    # ── Condition 6: At least one valid source ────────────────────────────────
    if len(valid_sources) == 0:
        reason_codes.append("no_valid_sources")

    # ── Condition 7: At least one valid fact ─────────────────────────────────
    if len(valid_facts) == 0:
        reason_codes.append("no_valid_facts")

    # ── Condition 8: Facts source-grounded when source_id is present ──────────
    for fact in valid_facts:
        src_id = fact.get("source_id")
        if src_id is not None:
            if str(src_id) not in valid_source_ids:
                reason_codes.append("fact_source_not_found")
                break

    # ── Conditions 9/10: No forbidden payload keys ────────────────────────────
    forbidden_violation = False

    if payload is not None:
        try:
            if isinstance(payload, dict):
                found_key = _has_forbidden_key(payload)
                if found_key is not None:
                    forbidden_violation = True
                    reason_codes.append(f"forbidden_payload_key={found_key}")
            else:
                reason_codes.append("payload_not_a_dict")
        except Exception:  # noqa: BLE001
            forbidden_violation = True
            reason_codes.append("payload_check_exception")

    # Also check fact structured_payloads (defense in depth)
    if not forbidden_violation:
        for fact in facts:
            try:
                sp = fact.get("structured_payload")
                if isinstance(sp, dict):
                    found_key = _has_forbidden_key(sp)
                    if found_key is not None:
                        forbidden_violation = True
                        reason_codes.append(f"forbidden_fact_payload_key={found_key}")
                        break
            except Exception:  # noqa: BLE001
                forbidden_violation = True
                reason_codes.append("fact_payload_check_exception")
                break

    # ── Condition 11: DB safe_for_decision promotion explicitly blocked ────────
    # Never require, read, or attempt to set safe_for_decision=True.
    # The DB hard-locks it False via CHECK constraint. This is recorded as an
    # explicit invariant in every result rather than a pass/fail condition.

    # ── Condition 12: Malformed/missing fields already handled above ──────────
    # Each malformed field adds a reason_code and makes the artifact ineligible.

    eligible = len(reason_codes) == 0

    notes: Optional[str] = None
    if not eligible:
        notes = f"ineligible_reason_count={len(reason_codes)}"

    return ArtifactReadinessResult(
        eligible_for_truth_adapter=eligible,
        eligible_for_decision_consumption=False,
        fail_closed=True,
        artifact_id=str(artifact_id) if artifact_id is not None else None,
        ticker=str(ticker) if ticker is not None else None,
        artifact_type=artifact_type_str,
        skill_pack=skill_pack_str,
        reason_codes=reason_codes,
        source_count=len(valid_sources),
        fact_count=len(valid_facts),
        confidence_or_trust_level=confidence_str,
        freshness_status=freshness_str,
        forbidden_payload_violation=forbidden_violation,
        safe_for_decision_db_promotion_blocked=True,
        notes=notes,
    )


def _valid_sources(sources: list[dict]) -> list[dict]:
    """Return sources with non-empty source_kind and provider_name."""
    valid = []
    for src in sources:
        try:
            if not isinstance(src, dict):
                continue
            sk = src.get("source_kind")
            pn = src.get("provider_name")
            if sk and str(sk).strip() and pn and str(pn).strip():
                valid.append(src)
        except Exception:  # noqa: BLE001
            pass
    return valid


def _valid_facts(facts: list[dict]) -> list[dict]:
    """Return facts with non-empty fact_kind and non-empty structured_payload dict."""
    valid = []
    for fact in facts:
        try:
            if not isinstance(fact, dict):
                continue
            fk = fact.get("fact_kind")
            sp = fact.get("structured_payload")
            if fk and str(fk).strip() and isinstance(sp, dict) and len(sp) > 0:
                valid.append(fact)
        except Exception:  # noqa: BLE001
            pass
    return valid
