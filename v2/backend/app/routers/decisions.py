"""Decisions router — user overrides and feedback on generated decision plans."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from ..middleware.auth import AuthenticatedUser, get_current_user
from ..models.decision import DecisionFeedbackRequest, DecisionResponse
from ..services.decision_history_service import get_decision, submit_user_feedback

router = APIRouter(prefix="/decision", tags=["decisions"])


@router.get("/{decision_id}", response_model=DecisionResponse)
async def fetch_decision(
    decision_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Fetch a single decision record."""
    record = get_decision(decision_id)
    if record["user_id"] != str(user.id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your decision")
    return record


@router.post("/{decision_id}/feedback", response_model=DecisionResponse)
async def submit_feedback(
    decision_id: str,
    body: DecisionFeedbackRequest,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Submit user feedback (accept / modify / reject) for a decision plan.

    - accept  → final_actions copied from generated_actions
    - modify  → final_actions overwritten with modified_actions
    - reject  → final_actions set to []
    """
    record = get_decision(decision_id)
    if record["user_id"] != str(user.id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your decision")

    updated = submit_user_feedback(decision_id, body.model_dump(exclude_none=True))
    return updated
