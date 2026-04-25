"""Decision log service — persists and retrieves deploy-plan decisions."""

from __future__ import annotations

import logging
from typing import Any

from ..database import get_supabase_client
from .decision_delta import analyzeDecisionDelta

logger = logging.getLogger(__name__)


INCOME_ETF_HINTS = {"SCHD", "VYM", "DGRO", "HDV", "JEPI", "DIVO", "BND", "SCHY"}
ETF_HINTS = {
    "VOO", "VTI", "IVV", "SPY", "QQQ", "VYM", "SCHD", "DGRO", "BND", "VXUS", "VEA",
    "SCHY", "HDV", "JEPI", "DIVO",
}


def _to_float(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return 0.0
    return 0.0


def _is_etf_like(ticker: str) -> bool:
    return ticker.upper() in ETF_HINTS


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

    def getUserBehaviorProfile(self, user_id: str, limit: int = 10) -> dict[str, Any]:
        """Aggregate recent decision log behavior for soft adaptive deploy nudges."""
        rows = self.list(user_id=user_id, limit=limit)
        if not rows:
            return {
                "avg_deploy_ratio": 1.0,
                "skip_rate": 0.0,
                "replace_rate": 0.0,
                "prefers_etf": False,
                "prefers_income": False,
                "growth_to_income_count": 0,
                "single_to_etf_count": 0,
                "avg_execution_gap_percent": 0.0,
                "sample_size": 0,
                "under_deployer": False,
            }

        deploy_ratios: list[float] = []
        execution_gaps: list[float] = []
        skip_count = 0
        replace_count = 0
        decision_count = 0
        replacement_events = 0
        etf_replacements = 0
        income_replacements = 0
        growth_to_income_count = 0
        single_to_etf_count = 0

        for row in rows:
            delta = row.get("decision_delta") if isinstance(row.get("decision_delta"), dict) else {}
            rec_total = _to_float(delta.get("total_recommended"))
            actual_total = _to_float(delta.get("total_actual"))
            if rec_total > 0:
                deploy_ratios.append(max(0.0, actual_total / rec_total))
            execution_gaps.append(_to_float(row.get("execution_gap_percent")))

            actuals = row.get("actual_decisions")
            decisions = actuals if isinstance(actuals, list) else []
            for decision in decisions:
                if not isinstance(decision, dict):
                    continue
                decision_count += 1
                action = str(decision.get("actual_action") or "").strip().upper()
                replacement_ticker = str(decision.get("replacement_ticker") or "").strip().upper()
                reason_blob = str(decision.get("reason") or "").lower()
                if action == "SKIPPED":
                    skip_count += 1
                if action == "REPLACED" or replacement_ticker:
                    replace_count += 1
                    replacement_events += 1
                    if replacement_ticker and _is_etf_like(replacement_ticker):
                        etf_replacements += 1
                    if replacement_ticker in INCOME_ETF_HINTS or any(
                        phrase in reason_blob for phrase in ("income", "dividend", "yield")
                    ):
                        income_replacements += 1

            category_shift = delta.get("category_shift") if isinstance(delta.get("category_shift"), dict) else {}
            if bool(category_shift.get("growth_to_income")) or str(row.get("style_shift") or "") == "growth_to_income":
                growth_to_income_count += 1
            if bool(category_shift.get("single_to_etf")):
                single_to_etf_count += 1

        total_decisions = max(1, decision_count)
        avg_deploy_ratio = sum(deploy_ratios) / len(deploy_ratios) if deploy_ratios else 1.0
        skip_rate = skip_count / total_decisions
        replace_rate = replace_count / total_decisions
        prefers_etf = replacement_events > 0 and (etf_replacements / replacement_events) > 0.50
        prefers_income = income_replacements >= 2 or growth_to_income_count >= 2
        avg_execution_gap = sum(execution_gaps) / len(execution_gaps) if execution_gaps else 0.0

        return {
            "avg_deploy_ratio": round(avg_deploy_ratio, 4),
            "skip_rate": round(skip_rate, 4),
            "replace_rate": round(replace_rate, 4),
            "prefers_etf": prefers_etf,
            "prefers_income": prefers_income,
            "growth_to_income_count": growth_to_income_count,
            "single_to_etf_count": single_to_etf_count,
            "avg_execution_gap_percent": round(avg_execution_gap, 2),
            "sample_size": len(rows),
            "under_deployer": avg_deploy_ratio < 0.85,
        }
