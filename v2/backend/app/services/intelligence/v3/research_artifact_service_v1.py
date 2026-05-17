"""Stage 5A — Research Artifact Service v1.

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
    same (user_id, ticker, artifact_type, skill_pack), all previously active
    artifacts for that combination are deactivated (is_active=False,
    invalidated_at=now, invalidation_reason='superseded_by_new_write') before
    the new artifact row is inserted. This ensures at most one active artifact
    per (user_id, ticker, artifact_type, skill_pack) combination at any time.

  No-source writes:
    Workers MAY write artifacts with no sources or facts (for scaffold/dark-run
    testing). The DB does not enforce a minimum source count. The truth adapter
    (Stage 5B+) enforces source requirements at consumption time.

Read helpers (safe, read-only):
  query_active_artifacts() — returns a compact summary list of active artifact
    fields for validation harness and future worker use. Never returns raw
    payloads, source URLs, or fact contents.
"""
from __future__ import annotations

import logging
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
          3. Deactivate previous active artifacts for (user_id, ticker, artifact_type,
             skill_pack) that have a different idempotency key (clean replacement).
          4. Delegate insert to ArtifactStoreWriter.
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
                    "research_artifact_service_idempotency_skip ticker=%s type=%s key=%s existing_id=%s",
                    output.ticker,
                    output.artifact_type,
                    output.replay_idempotency_key,
                    existing_id,
                )
                return existing_id

            # Step 3: Clean replacement — deactivate superseded active artifacts.
            if output.ticker:
                self._deactivate_superseded(
                    ticker=output.ticker,
                    artifact_type=output.artifact_type,
                    skill_pack=output.skill_pack,
                    new_idempotency_key=output.replay_idempotency_key,
                )

            # Step 4: Insert the new artifact.
            artifact_id = self._writer.write(output)
            if artifact_id:
                logger.info(
                    "research_artifact_service_write_ok ticker=%s type=%s artifact_id=%s",
                    output.ticker,
                    output.artifact_type,
                    artifact_id,
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
        ticker: str,
        artifact_type: str,
        skill_pack: str,
        new_idempotency_key: str,
    ) -> None:
        """Deactivate all active artifacts for (user_id, ticker, artifact_type, skill_pack)
        that do NOT have the new idempotency key.

        Fail-soft: errors are logged but do not abort the write.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            result = (
                self._client.table("research_artifacts")
                .update({
                    "is_active": False,
                    "invalidated_at": now_iso,
                    "invalidation_reason": "superseded_by_new_write",
                })
                .eq("user_id", self._user_id)
                .eq("ticker", ticker)
                .eq("artifact_type", artifact_type)
                .eq("skill_pack", skill_pack)
                .eq("is_active", True)
                .neq("replay_idempotency_key", new_idempotency_key)
                .execute()
            )
            deactivated = len(result.data or [])
            if deactivated > 0:
                logger.info(
                    "research_artifact_service_clean_replacement ticker=%s type=%s "
                    "deactivated_count=%d new_key=%s",
                    ticker,
                    artifact_type,
                    deactivated,
                    new_idempotency_key,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "research_artifact_service_deactivate_fail ticker=%s type=%s error=%s",
                ticker,
                artifact_type,
                exc,
            )
