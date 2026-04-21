"""Decision log service — persists and retrieves deploy-plan decisions."""

from __future__ import annotations

from ..database import get_supabase_client


class DecisionLogService:
    def __init__(self) -> None:
        self.client = get_supabase_client()

    def log(self, data: dict):
        return self.client.table("decision_logs").insert(data).execute()

    def list(self, limit: int = 50):
        return (
            self.client.table("decision_logs")
            .select("*")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
