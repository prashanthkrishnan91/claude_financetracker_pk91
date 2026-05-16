"""Action Feedback Service — persist and retrieve user feedback on Intel/Deploy/Watchtower actions.

Feedback is append-only, user-scoped, and idempotent by idempotency_key.
It is stored evidence/context only — it does NOT mutate Intel v3 decisions,
Deploy sizing, Watchtower refresh behavior, or any broker/execution behavior.
"""

from __future__ import annotations

import logging
from typing import Any

from ..database import get_supabase_client

logger = logging.getLogger(__name__)

_TABLE = "action_feedback_events"


class ActionFeedbackService:
    def __init__(self) -> None:
        self.client = get_supabase_client()

    def create(self, user_id: str, data: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        """Persist a feedback event.

        Returns ``(row, created)`` where ``created=False`` means an existing row
        was returned due to idempotency (duplicate submit with same key).
        Never raises on duplicate — callers always get a valid row back.
        """
        ticker = data.get("ticker")
        if ticker:
            ticker = str(ticker).strip().upper() or None

        agent_run_id = data.get("agent_run_id")
        snapshot_id = data.get("snapshot_id")

        payload: dict[str, Any] = {
            "user_id": user_id,
            "feedback_type": data["feedback_type"],
            "source_area": data["source_area"],
            "idempotency_key": data["idempotency_key"],
            "ticker": ticker,
            "action_type": data.get("action_type"),
            "agent_run_id": str(agent_run_id) if agent_run_id else None,
            "snapshot_id": str(snapshot_id) if snapshot_id else None,
            "note": data.get("note"),
        }

        try:
            result = self.client.table(_TABLE).insert(payload).execute()
            if result.data:
                logger.info(
                    "action_feedback.created user_id=%s type=%s source=%s ticker=%s",
                    user_id,
                    payload["feedback_type"],
                    payload["source_area"],
                    ticker,
                )
                return result.data[0], True
        except Exception as exc:
            exc_str = str(exc).lower()
            is_unique_violation = any(
                marker in exc_str for marker in ("unique", "duplicate", "23505")
            )
            if not is_unique_violation:
                raise

        # Dedup: unique constraint hit — return the existing row
        existing = self._fetch_by_idempotency_key(
            user_id=user_id, idempotency_key=payload["idempotency_key"]
        )
        if existing:
            logger.info(
                "action_feedback.dedup_hit user_id=%s idempotency_key=%s",
                user_id,
                payload["idempotency_key"],
            )
            return existing, False
        # Fallback: shouldn't reach here, but return payload as-is rather than raising
        return payload, False

    def _fetch_by_idempotency_key(
        self, user_id: str, idempotency_key: str
    ) -> dict[str, Any] | None:
        result = (
            self.client.table(_TABLE)
            .select("*")
            .eq("user_id", user_id)
            .eq("idempotency_key", idempotency_key)
            .limit(1)
            .execute()
        )
        rows = result.data or []
        return rows[0] if rows else None

    def list(
        self,
        user_id: str,
        *,
        limit: int = 50,
        ticker: str | None = None,
        source_area: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return recent feedback events for a user, newest first.

        Optionally filtered by ``ticker`` and/or ``source_area``.
        """
        query = self.client.table(_TABLE).select("*").eq("user_id", user_id)
        if ticker:
            query = query.eq("ticker", str(ticker).strip().upper())
        if source_area:
            query = query.eq("source_area", source_area)
        result = query.order("created_at", desc=True).limit(limit).execute()
        return result.data or []
