"""Decision history service — records and updates user decision records."""

from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from fastapi import HTTPException, status

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


def get_decision(decision_id: str) -> dict[str, Any]:
    """Fetch a single decision record by id. Raises 404 if not found."""
    client = get_supabase_client()
    result = (
        client.table("decision_history")
        .select("*")
        .eq("id", decision_id)
        .limit(1)
        .execute()
    )
    if not result.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Decision {decision_id} not found",
        )
    return result.data[0]


def submit_user_feedback(
    decision_id: str,
    feedback: dict[str, Any],
) -> dict[str, Any]:
    """Apply user feedback to a decision record and return the updated row.

    Behavior by feedback type:
    - "accept"  → final_actions = generated_actions, status = "accept"
    - "modify"  → final_actions = feedback["modified_actions"], status = "modify"
    - "reject"  → final_actions = [], status = "reject"
    """
    record = get_decision(decision_id)

    feedback_type = feedback["type"]

    if feedback_type == "accept":
        final_actions = record.get("generated_actions") or []
    elif feedback_type == "modify":
        final_actions = feedback.get("modified_actions") or []
    else:  # reject
        final_actions = []

    client = get_supabase_client()
    payload: dict[str, Any] = {
        "status": feedback_type,
        "final_actions": final_actions,
        "user_feedback": feedback,
    }
    result = (
        client.table("decision_history")
        .update(payload)
        .eq("id", decision_id)
        .execute()
    )
    return result.data[0]
