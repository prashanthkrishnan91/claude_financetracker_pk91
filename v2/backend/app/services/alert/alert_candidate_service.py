"""Alert Candidate Service — persist and retrieve alert policy candidates.

Candidates are stored in watchtower_alert_candidates (migration 020).
Persistence is idempotent by (user_id, dedupe_key).
This service does NOT mutate Intel v3, Deploy, Watchtower, or action_feedback_events.
"""

from __future__ import annotations

import logging
from typing import Any

from ...database import get_supabase_client
from .alert_trigger_policy_v1 import AlertCandidate

logger = logging.getLogger(__name__)

_TABLE = "watchtower_alert_candidates"


class AlertCandidateService:
    def __init__(self) -> None:
        self.client = get_supabase_client()

    def persist_candidate(self, candidate: AlertCandidate) -> tuple[dict[str, Any], bool]:
        """Persist one alert candidate. Returns (row, created).

        created=False means the row already existed (idempotent dedupe hit).
        Never raises on a unique-constraint conflict.
        """
        payload: dict[str, Any] = {
            "user_id": candidate.user_id,
            "ticker": candidate.ticker.upper(),
            "source_area": candidate.source_area,
            "candidate_type": candidate.candidate_type,
            "action_type": candidate.action_type,
            "severity": candidate.severity,
            "reason_code": candidate.reason_code,
            "plain_english_reason": candidate.plain_english_reason,
            "policy_version": candidate.policy_version,
            "status": candidate.status,
            "dedupe_key": candidate.dedupe_key,
            "source_snapshot_id": candidate.source_snapshot_id,
            "source_run_id": candidate.source_run_id,
            "expires_at": (
                candidate.expires_at.isoformat() if candidate.expires_at else None
            ),
        }

        is_unique_conflict = False
        try:
            result = self.client.table(_TABLE).insert(payload).execute()
            if result.data:
                logger.info(
                    "alert_candidate.created user_id=%s ticker=%s candidate_type=%s "
                    "action_type=%s severity=%s",
                    candidate.user_id,
                    candidate.ticker,
                    candidate.candidate_type,
                    candidate.action_type,
                    candidate.severity,
                )
                return result.data[0], True
            is_unique_conflict = False
        except Exception as exc:
            exc_str = str(exc).lower()
            is_unique_violation = any(
                marker in exc_str for marker in ("unique", "duplicate", "23505")
            )
            if not is_unique_violation:
                raise
            is_unique_conflict = True

        existing = self._fetch_by_dedupe_key(
            user_id=candidate.user_id, dedupe_key=candidate.dedupe_key
        )
        if existing:
            logger.info(
                "alert_candidate.%s user_id=%s dedupe_key=%s",
                "dedup_hit" if is_unique_conflict else "insert_no_data_recovered",
                candidate.user_id,
                candidate.dedupe_key,
            )
            return existing, False

        raise RuntimeError(
            f"alert_candidate_dedup_lookup_failed dedupe_key={candidate.dedupe_key!r}"
        )

    def _fetch_by_dedupe_key(
        self, user_id: str, dedupe_key: str
    ) -> dict[str, Any] | None:
        result = (
            self.client.table(_TABLE)
            .select("*")
            .eq("user_id", user_id)
            .eq("dedupe_key", dedupe_key)
            .limit(1)
            .execute()
        )
        rows = result.data or []
        return rows[0] if rows else None

    def list_candidates(
        self,
        user_id: str,
        *,
        limit: int = 50,
        ticker: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return alert candidates for a user, newest first."""
        query = self.client.table(_TABLE).select("*").eq("user_id", user_id)
        if ticker:
            query = query.eq("ticker", str(ticker).strip().upper())
        if status:
            query = query.eq("status", status)
        result = query.order("created_at", desc=True).limit(limit).execute()
        return result.data or []
