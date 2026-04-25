"""Decision log service — persists and retrieves deploy-plan decisions."""

from __future__ import annotations

import logging
from typing import Any

from ..database import get_supabase_client
from .decision_delta import analyzeDecisionDelta

logger = logging.getLogger(__name__)


class DecisionLogService:
    def __init__(self) -> None:
        self.client = get_supabase_client()

    def create(self, user_id: str, data: dict[str, Any]) -> dict[str, Any]:
        analysis = analyzeDecisionDelta(
            recommendation_snapshot=data.get("recommendation_snapshot"),
            actual_decisions=data.get("actual_decisions"),
        )
        payload = {
            **data,
            "user_id": user_id,
            "status": analysis["status"],
            "decision_delta": analysis["decision_delta"],
            "risk_behavior": analysis["risk_behavior"],
            "style_shift": analysis["style_shift"],
            "execution_gap_percent": analysis["execution_gap_percent"],
        }
        logger.info("decision_log.create user_id=%s status=%s", user_id, payload.get("status"))
        result = self.client.table("decision_logs").insert(payload).execute()
        return result.data[0] if result.data else {}

    def list(self, user_id: str, limit: int = 50) -> list[dict[str, Any]]:
        logger.info("decision_log.list user_id=%s limit=%s", user_id, limit)
        result = (
            self.client.table("decision_logs")
            .select("*")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data or []

    def get(self, user_id: str, decision_log_id: str) -> dict[str, Any] | None:
        logger.info("decision_log.get user_id=%s id=%s", user_id, decision_log_id)
        result = (
            self.client.table("decision_logs")
            .select("*")
            .eq("id", decision_log_id)
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        rows = result.data or []
        return rows[0] if rows else None

    def update(self, user_id: str, decision_log_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
        logger.info("decision_log.update user_id=%s id=%s", user_id, decision_log_id)
        payload = dict(patch)
        should_recompute = "actual_decisions" in payload or "recommendation_snapshot" in payload
        if should_recompute:
            current = self.get(user_id=user_id, decision_log_id=decision_log_id)
            if not current:
                return None
            recommendation_snapshot = payload.get("recommendation_snapshot", current.get("recommendation_snapshot"))
            actual_decisions = payload.get("actual_decisions", current.get("actual_decisions"))
            analysis = analyzeDecisionDelta(
                recommendation_snapshot=recommendation_snapshot,
                actual_decisions=actual_decisions,
            )
            payload.update(
                {
                    "status": analysis["status"],
                    "decision_delta": analysis["decision_delta"],
                    "risk_behavior": analysis["risk_behavior"],
                    "style_shift": analysis["style_shift"],
                    "execution_gap_percent": analysis["execution_gap_percent"],
                }
            )
        if not should_recompute:
            payload.pop("status", None)
        result = (
            self.client.table("decision_logs")
            .update(payload)
            .eq("id", decision_log_id)
            .eq("user_id", user_id)
            .execute()
        )
        rows = result.data or []
        return rows[0] if rows else None

    def delete(self, user_id: str, decision_log_id: str) -> bool:
        logger.info("decision_log.delete user_id=%s id=%s", user_id, decision_log_id)
        result = (
            self.client.table("decision_logs")
            .delete()
            .eq("id", decision_log_id)
            .eq("user_id", user_id)
            .execute()
        )
        return bool(result.data)
