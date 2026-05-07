"""Phase 3 artifact store writer — persists WorkerOutput to the four artifact tables.

Architecture constraints:
  - NEVER sets safe_for_decision = True. The DB CHECK constraint also enforces this.
  - NEVER writes to intel_v3_snapshots or any visible-decision table.
  - Handles DB errors safely: logs, emits a failure audit event, returns None.
    Callers (runner.py) must not propagate DB errors into the visible Intel v3 path.
  - Idempotency: on replay_idempotency_key conflict (existing active artifact),
    skips the insert and returns the existing artifact_id. This prevents duplicates
    on repeated dark-run calls without a source change.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

from .contracts import AuditEventRecord, WorkerOutput

_SKILL_PACK = "earnings_reviewer"


class ArtifactStoreWriter:
    """Writes one WorkerOutput to the four research artifact tables.

    Accepts a Supabase client-compatible object. Tests inject a fake client;
    production callers pass the real client from get_supabase_client().
    """

    def __init__(self, supabase_client: Any, user_id: str) -> None:
        self._client = supabase_client
        self._user_id = user_id

    def write(self, output: WorkerOutput) -> Optional[str]:
        """Persist artifact + sources + facts + audit events.

        Returns artifact_id (str UUID) on success, None on failure.
        Never raises — all errors are logged and returned as None.
        """
        artifact_id: Optional[str] = None
        start_ts = datetime.now(timezone.utc)
        try:
            artifact_id = self._upsert_artifact(output)
            if artifact_id is None:
                self._write_audit_events(
                    output, artifact_id=None, status="rejected",
                    error_message="Artifact insert returned no ID (possible idempotency skip).",
                )
                return None

            source_id_map = self._insert_sources(output, artifact_id)
            self._insert_facts(output, artifact_id, source_id_map)
            self._write_audit_events(output, artifact_id=artifact_id, status="completed")

            latency_ms = int((datetime.now(timezone.utc) - start_ts).total_seconds() * 1000)
            logger.info(
                "research_artifact_write_success worker=%s ticker=%s artifact_id=%s latency_ms=%d",
                output.skill_pack,
                output.ticker,
                artifact_id,
                latency_ms,
            )
            return artifact_id

        except Exception as exc:  # noqa: BLE001
            logger.error(
                "research_artifact_write_failure worker=%s ticker=%s error=%s",
                output.skill_pack,
                output.ticker,
                exc,
            )
            try:
                self._write_audit_events(
                    output, artifact_id=artifact_id, status="failed",
                    error_message=str(exc),
                )
            except Exception as audit_exc:  # noqa: BLE001
                logger.error("research_artifact_audit_write_failure error=%s", audit_exc)
            return None

    # ── private helpers ───────────────────────────────────────────────────────

    def _upsert_artifact(self, output: WorkerOutput) -> Optional[str]:
        """Insert artifact row. On idempotency key conflict, return existing id."""
        row: dict[str, Any] = {
            "user_id": self._user_id,
            "artifact_schema_version": "artifact.v1",
            "artifact_type": output.artifact_type,
            "skill_pack": output.skill_pack,
            "scope_kind": output.scope_kind,
            "ticker": output.ticker,
            "generated_by_worker": output.skill_pack,
            "input_fingerprint": output.input_fingerprint,
            "replay_idempotency_key": output.replay_idempotency_key,
            "worker_run_id": output.worker_run_id,
            "confidence_or_trust_level": output.confidence_or_trust_level,
            "freshness_status": output.freshness_status,
            "is_active": True,
            "safe_for_decision": False,  # DB constraint also enforces this
            "deterministic_inputs_allowed": [],
            "deterministic_inputs_forbidden": [],
            "limitations_or_missing_evidence": output.limitations_or_missing_evidence,
            "payload": output.artifact_payload,
        }
        if output.evidence_summary_plain_english:
            row["evidence_summary_plain_english"] = output.evidence_summary_plain_english
        if output.source_window_start:
            row["source_window_start"] = output.source_window_start
        if output.source_window_end:
            row["source_window_end"] = output.source_window_end
        if output.expires_at:
            row["expires_at"] = output.expires_at
        if output.parent_intel_run_id:
            row["parent_intel_run_id"] = output.parent_intel_run_id
        if output.generated_by_model:
            row["generated_by_model"] = output.generated_by_model
        if output.model_version:
            row["model_version"] = output.model_version

        result = (
            self._client.table("research_artifacts")
            .upsert(row, on_conflict="replay_idempotency_key", ignore_duplicates=True)
            .execute()
        )
        data = result.data or []
        if data:
            return data[0].get("id")

        # Row already exists (idempotency skip) — fetch the existing id.
        existing = (
            self._client.table("research_artifacts")
            .select("id")
            .eq("replay_idempotency_key", output.replay_idempotency_key)
            .eq("is_active", True)
            .limit(1)
            .execute()
        )
        rows = existing.data or []
        if rows:
            logger.info(
                "research_artifact_idempotency_skip ticker=%s key=%s existing_id=%s",
                output.ticker,
                output.replay_idempotency_key,
                rows[0].get("id"),
            )
            return rows[0].get("id")
        return None

    def _insert_sources(
        self, output: WorkerOutput, artifact_id: str
    ) -> dict[int, str]:
        """Insert source rows. Returns {source_index: source_db_id}."""
        id_map: dict[int, str] = {}
        for idx, src in enumerate(output.sources):
            row: dict[str, Any] = {
                "artifact_id": artifact_id,
                "user_id": self._user_id,
                "source_kind": src.source_kind,
                "provider_name": src.provider_name,
            }
            if src.provider_version:
                row["provider_version"] = src.provider_version
            if src.source_url:
                row["source_url"] = src.source_url
            if src.source_id:
                row["source_id"] = src.source_id
            if src.source_published_at:
                row["source_published_at"] = src.source_published_at
            if src.quote_or_excerpt:
                row["quote_or_excerpt"] = src.quote_or_excerpt
            if src.section_reference:
                row["section_reference"] = src.section_reference
            if src.source_hash:
                row["source_hash"] = src.source_hash

            result = self._client.table("research_artifact_sources").insert(row).execute()
            data = result.data or []
            if data:
                id_map[idx] = data[0]["id"]
        return id_map

    def _insert_facts(
        self,
        output: WorkerOutput,
        artifact_id: str,
        source_id_map: dict[int, str],
    ) -> None:
        """Insert fact rows, resolving source_index to DB source_id."""
        for fact in output.facts:
            row: dict[str, Any] = {
                "artifact_id": artifact_id,
                "user_id": self._user_id,
                "fact_kind": fact.fact_kind,
                "structured_payload": fact.structured_payload,
                "is_quote_grounded": fact.is_quote_grounded,
            }
            if fact.axis_hint:
                row["axis_hint"] = fact.axis_hint
            if fact.severity:
                row["severity"] = fact.severity
            if fact.period:
                row["period"] = fact.period
            if fact.as_of:
                row["as_of"] = fact.as_of
            if fact.source_index is not None:
                db_source_id = source_id_map.get(fact.source_index)
                if db_source_id:
                    row["source_id"] = db_source_id

            self._client.table("research_artifact_facts").insert(row).execute()

    def _write_audit_events(
        self,
        output: WorkerOutput,
        artifact_id: Optional[str],
        status: str,
        error_message: Optional[str] = None,
    ) -> None:
        """Insert worker_audit_events rows from WorkerOutput.audit_events.

        If the output has explicit audit events, write those. Otherwise write
        a single summary event for the overall worker run.
        """
        events = output.audit_events if output.audit_events else [
            AuditEventRecord(tool_call="worker_run", status=status)
        ]
        for event in events:
            row: dict[str, Any] = {
                "user_id": self._user_id,
                "worker_run_id": output.worker_run_id,
                "skill_pack": output.skill_pack,
                "tool_call": event.tool_call,
                "status": event.status if status == "completed" else status,
            }
            if artifact_id:
                row["artifact_id"] = artifact_id
            if output.parent_intel_run_id:
                row["parent_intel_run_id"] = output.parent_intel_run_id
            if event.input_digest:
                row["input_digest"] = event.input_digest
            if event.output_digest:
                row["output_digest"] = event.output_digest
            if event.model_id:
                row["model_id"] = event.model_id
            if event.model_version:
                row["model_version"] = event.model_version
            if event.latency_ms is not None:
                row["latency_ms"] = event.latency_ms
            if event.cost_estimate_usd is not None:
                row["cost_estimate_usd"] = event.cost_estimate_usd
            if event.rejected_claims:
                row["rejected_claims"] = event.rejected_claims
            effective_error = error_message or event.error_message
            if effective_error:
                row["error_message"] = effective_error

            self._client.table("worker_audit_events").insert(row).execute()
