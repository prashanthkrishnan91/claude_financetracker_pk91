"""Decisions router — user overrides and feedback on generated decision plans."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..middleware.auth import AuthenticatedUser, get_current_user
from ..models.decision import DecisionFeedbackRequest, DecisionResponse, ManualDecisionLogCreate
from ..services.decision_history_service import get_decision, submit_user_feedback
from ..services.decision_log_service import DecisionLogService

router = APIRouter(prefix="/decision", tags=["decisions"])


@router.get("/logs")
async def get_decision_logs(
    limit: int = Query(default=50, le=500),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """List deploy-plan decision log entries, newest first."""
    svc = DecisionLogService()
    return svc.list(limit).data


@router.post("/logs", status_code=201)
async def create_decision_log(
    body: ManualDecisionLogCreate,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Manually log a deploy decision (with optional ticker/amount edits)."""
    svc = DecisionLogService()
    result = svc.log({**body.model_dump(), "user_id": str(user.id)})
    return result.data[0] if result.data else {}


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
