"""Stage 5A — Research Artifact Service v1 (Stage 5B: source credibility;
Stage 5C: contradiction detection; Stage 5D: evidence completeness scoring;
Stage 5E: truth/usability assessment).

Public, narrow, typed API for writing evidence-only research artifacts into
the Research Artifact Store (migration 017 + 023).

Architecture contracts (non-negotiable):
  - NEVER imports or calls decide() from decision_policy_v1.
  - NEVER writes to intel_v3_snapshots or any visible-decision table.
  - NEVER sets safe_for_decision = True (DB CHECK constraint also enforces this).
  - NEVER produces or returns final Buy/Hold/Trim/Sell authority.
  - All DB failures are contained; callers receive None / empty list — never an
    exception that could propagate into the Intel v3 visible path.

Write policies (explicit):
  Idempotency:
    A write with the same replay_idempotency_key as an existing *active* artifact
    is skipped. The existing artifact_id is returned. No duplicate rows.

  Clean replacement:
    When a new artifact is written with a *different* idempotency key for the
    same evidence lane (user_id, artifact_type, skill_pack, scope_kind,
    COALESCE(ticker, '')), all previously active artifacts for that lane are
    deactivated (is_active=False, invalidated_at=now,
    invalidation_reason='superseded_by_new_write') before the new artifact row
    is inserted. This ensures at most one active artifact per lane at any time.
    Covers ticker-scope (ticker IS NOT NULL) and portfolio-scope (ticker IS NULL).

  No-source / no-fact writes:
    Workers MAY write artifacts with no sources or facts (for scaffold/dark-run
    testing). No-source artifacts receive an UNKNOWN/INSUFFICIENT source
    credibility assessment automatically (Stage 5B). No-fact artifacts receive
    a not_evaluable contradiction assessment (Stage 5C). No-source-and-no-fact
    artifacts receive a NOT_EVALUABLE completeness assessment (Stage 5D).

  Source credibility (Stage 5B):
    Every newly-written artifact receives a deterministic source credibility
    assessment injected into its payload under key
    'source_credibility_assessment'. The assessment is produced by
    source_credibility_registry_v1 and is always replayable.

  Contradiction detection (Stage 5C):
    Every newly-written artifact receives a deterministic contradiction
    assessment injected into its payload under key 'contradiction_assessment'.
    The assessment is produced by contradiction_detector_v1 and is always
    replayable. No-fact or non-comparable-fact artifacts are marked not evaluable
    — not "no contradictions." Contradiction resolution is deferred to Stage 5E.

  Evidence completeness scoring (Stage 5D):
    Every newly-written artifact receives a deterministic evidence completeness
    assessment injected into its payload under key
    'evidence_completeness_assessment'. The assessment is produced by
    evidence_completeness_scorer_v1 consuming the source credibility and
    contradiction assessments from the same write. Bands: COMPLETE / PARTIAL /
    THIN / NOT_EVALUABLE. No fake numeric scores. No LLM calls. Replayable.

  Truth/usability assessment (Stage 5E):
    Every newly-written artifact receives a deterministic usability assessment
    injected into its payload under key 'truth_usability_assessment'. The
    assessment is produced by artifact_truth_adapter_v1 consuming the credibility,
    contradiction, and completeness assessments from the same write.
    Labels: USABLE / USABLE_WITH_LIMITATIONS / SUPPRESSED_INCOMPLETE /
    SUPPRESSED_CONTRADICTED / SUPPRESSED_UNKNOWN_SOURCE / NOT_EVALUABLE.
    No LLM calls. No fake confidence. Replayable. Does not affect
    safe_for_decision (remains False).

Read helpers (safe, read-only):
  query_active_artifacts() — returns a compact summary list of active artifact
    fields for validation harness and future worker use. Never returns raw
    payloads, source URLs, or fact contents.
"""
from __future__ import annotations

import logging
from dataclasses import replace as _dc_replace
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

from app.services.intelligence.research_workers.artifact_store_writer import (
    ArtifactStoreWriter,
)
from app.services.intelligence.research_workers.contracts import (
    AuditEventRecord,
    FactRecord,
    SourceRecord,
    WorkerOutput,
    validate_payload,
)
from app.services.intelligence.v3.contradiction_detector_v1 import (
    detect_contradictions,
)
from app.services.intelligence.v3.artifact_truth_adapter_v1 import (
    assess_artifact_usability,
)
from app.services.intelligence.v3.evidence_completeness_scorer_v1 import (
    score_evidence_completeness,
)
from app.services.intelligence.v3.source_credibility_registry_v1 import (
    assess_artifact_sources,
)

# Fields returned by query_active_artifacts (safe subset — no payloads, URLs, excerpts).
_SAFE_QUERY_COLUMNS = (
    "id,artifact_type,skill_pack,scope_kind,ticker,artifact_schema_version,"
    "confidence_or_trust_level,freshness_status,generated_at,expires_at,"
    "is_active,invalidated_at,replay_idempotency_key,worker_run_id,"
    "parent_intel_run_id,input_fingerprint,safe_for_decision"
)


class ResearchArtifactServiceV1:
    """Narrow typed API for Stage 5A evidence-only artifact writes.

    Accepts a Supabase client-compatible object. Tests inject a fake client;
    production callers pass the real client from get_supabase_client().

    All public methods are fail-closed: DB errors are logged and contained.
    """

    def __init__(self, supabase_client: Any, user_id: str) -> None:
        self._client = supabase_client
        self._user_id = user_id
        self._writer = ArtifactStoreWriter(supabase_client, user_id)

    # ── Public write API ──────────────────────────────────────────────────────

    def write_artifact(self, output: WorkerOutput) -> Optional[str]:
        """Persist one evidence artifact with idempotency and clean replacement.

        Returns artifact_id (str UUID) on success or idempotency skip.
        Returns None on failure — never raises.

        Steps:
          1. Validate payload has no forbidden decision-authority keys.
          2. Check idempotency: if active artifact with same replay_idempotency_key
             already exists, return its id (skip).
          3. Deactivate previous active artifacts for the same evidence lane
             (user_id, artifact_type, skill_pack, scope_kind, COALESCE(ticker, ''))
             that have a different idempotency key (clean replacement).
          4. Inject deterministic source credibility assessment into payload
             (Stage 5B — source_credibility_registry_v1).
          5. Inject deterministic contradiction assessment into payload
             (Stage 5C — contradiction_detector_v1).
          6. Inject deterministic evidence completeness assessment into payload
             (Stage 5D — evidence_completeness_scorer_v1). Consumes credibility
             and contradiction assessments from Steps 4–5.
          7. Inject deterministic truth/usability assessment into payload
             (Stage 5E — artifact_truth_adapter_v1). Consumes credibility,
             contradiction, and completeness assessments from Steps 4–6.
          8. Delegate insert to ArtifactStoreWriter.
        """
        try:
            # Step 1: App-level forbidden-key guard (DB trigger also enforces this).
            validate_payload(output.artifact_payload, label="write_artifact.payload")
        except ValueError as exc:
            logger.error(
                "research_artifact_service_forbidden_key worker=%s ticker=%s error=%s",
                output.skill_pack,
                output.ticker,
                exc,
            )
            return None

        try:
            # Step 2: Idempotency check.
            existing_id = self._find_active_by_idempotency_key(output.replay_idempotency_key)
            if existing_id is not None:
                logger.info(
                    "research_artifact_service_idempotency_skip ticker=%s type=%s "
                    "skill_pack=%s model_version=%s key=%s existing_id=%s",
                    output.ticker,
                    output.artifact_type,
                    output.skill_pack,
                    output.model_version or "none",
                    output.replay_idempotency_key,
                    existing_id,
                )
                return existing_id

            # Step 3: Fail-closed clean replacement — always deactivate superseded
            # artifacts for the full evidence lane before insert. Portfolio-scope
            # artifacts (ticker=None) are correctly handled via IS NULL filter.
            try:
                self._deactivate_superseded(
                    artifact_type=output.artifact_type,
                    skill_pack=output.skill_pack,
                    scope_kind=output.scope_kind,
                    ticker=output.ticker if output.ticker else None,
                    new_idempotency_key=output.replay_idempotency_key,
                )
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "research_artifact_service_deactivation_failed_abort_write "
                    "ticker=%s type=%s error=%s",
                    output.ticker,
                    output.artifact_type,
                    exc,
                )
                return None

            # Step 4: Inject source credibility assessment (Stage 5B).
            # Assessment is deterministic and replayable; no-source artifacts
            # receive UNKNOWN/INSUFFICIENT. Never adds forbidden keys.
            credibility = assess_artifact_sources(output.sources)
            enriched_payload = {
                **output.artifact_payload,
                "source_credibility_assessment": credibility.to_dict(),
            }

            # Step 5: Inject contradiction assessment (Stage 5C).
            # Assessment is deterministic and replayable; no-fact or
            # non-comparable-fact artifacts are marked not_evaluable.
            # Never says "no contradictions" when evidence is not comparable.
            # Contradiction resolution is deferred to Stage 5E truth adapter.
            contradiction = detect_contradictions(output.facts)
            enriched_payload["contradiction_assessment"] = contradiction.to_dict()

            # Step 6: Inject evidence completeness assessment (Stage 5D).
            # Consumes credibility (5B) and contradiction (5C) assessments.
            # No fake numeric scores. Bands: COMPLETE/PARTIAL/THIN/NOT_EVALUABLE.
            # No-source-and-no-fact → NOT_EVALUABLE with explicit missing requirements.
            completeness = score_evidence_completeness(
                sources=output.sources,
                facts=output.facts,
                credibility_assessment=credibility,
                contradiction_assessment=contradiction,
            )
            enriched_payload["evidence_completeness_assessment"] = completeness.to_dict()

            # Step 7: Inject truth/usability assessment (Stage 5E).
            # Consumes credibility (5B), contradiction (5C), completeness (5D).
            # Labels: USABLE / USABLE_WITH_LIMITATIONS / SUPPRESSED_INCOMPLETE /
            # SUPPRESSED_CONTRADICTED / SUPPRESSED_UNKNOWN_SOURCE / NOT_EVALUABLE.
            # Never sets safe_for_decision=True. No LLM calls. Replayable.
            usability = assess_artifact_usability(credibility, contradiction, completeness)
            enriched_payload["truth_usability_assessment"] = usability.to_dict()

            output = _dc_replace(output, artifact_payload=enriched_payload)

            # Step 8: Insert the new artifact.
            artifact_id = self._writer.write(output)
            if artifact_id:
                logger.info(
                    "research_artifact_service_write_ok ticker=%s type=%s skill_pack=%s "
                    "model_version=%s artifact_id=%s "
                    "credibility_strongest=%s is_insufficient=%s "
                    "contradiction_evaluable=%s has_contradictions=%s contradiction_count=%d "
                    "completeness_band=%s completeness_evaluable=%s "
                    "usability_label=%s is_usable=%s",
                    output.ticker,
                    output.artifact_type,
                    output.skill_pack,
                    output.model_version or "none",
                    artifact_id,
                    credibility.strongest_authority_level,
                    credibility.is_insufficient,
                    contradiction.is_evaluable,
                    contradiction.has_contradictions,
                    contradiction.contradiction_count,
                    completeness.completeness_band,
                    completeness.is_evaluable,
                    usability.usability_label,
                    usability.is_usable,
                )
            return artifact_id

        except Exception as exc:  # noqa: BLE001
            logger.error(
                "research_artifact_service_write_failure worker=%s ticker=%s error=%s",
                output.skill_pack,
                output.ticker,
                exc,
            )
            return None

    # ── Public read helpers ───────────────────────────────────────────────────

    def query_active_artifacts(
        self,
        ticker: Optional[str] = None,
        artifact_type: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return a safe subset of active artifact fields for validation / workers.

        Never returns raw payloads, source URLs, quotes, or fact contents.
        Returns [] on any DB error.

        Args:
            ticker: Filter by ticker (None → all tickers for this user).
            artifact_type: Filter by type (None → all types).
            limit: Max rows to return (capped at 100).
        """
        limit = min(limit, 100)
        try:
            q = (
                self._client.table("research_artifacts")
                .select(_SAFE_QUERY_COLUMNS)
                .eq("user_id", self._user_id)
                .eq("is_active", True)
            )
            if ticker is not None:
                q = q.eq("ticker", ticker)
            if artifact_type is not None:
                q = q.eq("artifact_type", artifact_type)
            q = q.order("generated_at", desc=True).limit(limit)
            result = q.execute()
            return result.data or []
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "research_artifact_service_query_failure user_id=%s ticker=%s type=%s error=%s",
                self._user_id,
                ticker,
                artifact_type,
                exc,
            )
            return []

    # ── Private helpers ───────────────────────────────────────────────────────

    def _find_active_by_idempotency_key(self, key: str) -> Optional[str]:
        """Return the artifact_id for an existing active row with this key, or None."""
        result = (
            self._client.table("research_artifacts")
            .select("id")
            .eq("user_id", self._user_id)
            .eq("replay_idempotency_key", key)
            .eq("is_active", True)
            .limit(1)
            .execute()
        )
        rows = result.data or []
        return rows[0].get("id") if rows else None

    def _deactivate_superseded(
        self,
        artifact_type: str,
        skill_pack: str,
        scope_kind: str,
        new_idempotency_key: str,
        ticker: Optional[str] = None,
    ) -> None:
        """Deactivate all active artifacts in the same evidence lane that do NOT have
        the new idempotency key.

        Evidence lane: (user_id, artifact_type, skill_pack, scope_kind, COALESCE(ticker, ''))
        Matches the DB partial unique index uq_research_artifacts_active_lane.
        Handles ticker-scope (ticker IS NOT NULL) and portfolio-scope (ticker IS NULL).

        Fail-closed: exceptions propagate to write_artifact, which catches them and
        returns None without proceeding to insert.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        q = (
            self._client.table("research_artifacts")
            .update({
                "is_active": False,
                "invalidated_at": now_iso,
                "invalidation_reason": "superseded_by_new_write",
            })
            .eq("user_id", self._user_id)
            .eq("artifact_type", artifact_type)
            .eq("skill_pack", skill_pack)
            .eq("scope_kind", scope_kind)
            .eq("is_active", True)
            .neq("replay_idempotency_key", new_idempotency_key)
        )
        if ticker is not None:
            q = q.eq("ticker", ticker)
        else:
            q = q.is_("ticker", "null")
        result = q.execute()
        deactivated = len(result.data or [])
        if deactivated > 0:
            logger.info(
                "research_artifact_service_clean_replacement ticker=%s type=%s "
                "skill_pack=%s scope_kind=%s deactivated_count=%d new_key=%s",
                ticker,
                artifact_type,
                skill_pack,
                scope_kind,
                deactivated,
                new_idempotency_key,
            )
