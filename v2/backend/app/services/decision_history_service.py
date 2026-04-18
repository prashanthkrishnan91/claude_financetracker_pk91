"""Decision history service — records and updates user decision records."""

from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from ..database import get_supabase_client


def create_decision_record(
    user_id: UUID,
    decision_type: str,
    input_snapshot: dict[str, Any],
    input_params: dict[str, Any],
    generated_actions: dict[str, Any],
) -> str:
    """Insert a new decision record with status='generated'. Returns the new record id."""
    client = get_supabase_client()
    row = {
        "user_id": str(user_id),
        "decision_type": decision_type,
        "input_snapshot": input_snapshot,
        "input_params": input_params,
        "generated_actions": generated_actions,
        "final_actions": None,
        "status": "generated",
    }
    result = client.table("decision_history").insert(row).execute()
    return result.data[0]["id"]


def update_decision_status(
    decision_id: str,
    status: str,
    final_actions: Optional[dict[str, Any]] = None,
) -> None:
    """Update status (and optionally final_actions) for a decision record."""
    client = get_supabase_client()
    payload: dict[str, Any] = {"status": status}
    if final_actions is not None:
        payload["final_actions"] = final_actions
    client.table("decision_history").update(payload).eq("id", decision_id).execute()


def get_decision_history(user_id: UUID, limit: int = 20) -> list[dict[str, Any]]:
    """Return recent decisions for a user ordered by created_at desc."""
    client = get_supabase_client()
    result = (
        client.table("decision_history")
        .select("*")
        .eq("user_id", str(user_id))
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return result.data
