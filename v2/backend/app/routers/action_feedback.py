"""Action Feedback router — user feedback on Intel/Deploy/Watchtower actions."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status

from ..middleware.auth import AuthenticatedUser, get_current_user
from ..models.action_feedback import ActionFeedbackCreateRequest, ActionFeedbackResponse
from ..services.action_feedback_service import ActionFeedbackService

router = APIRouter(prefix="/action-feedback", tags=["action-feedback"])


@router.post("", response_model=ActionFeedbackResponse, status_code=status.HTTP_201_CREATED)
async def create_action_feedback(
    body: ActionFeedbackCreateRequest,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Persist a feedback event on an Intel/Deploy/Watchtower action.

    Idempotent: repeated submits with the same ``idempotency_key`` return
    the existing row (HTTP 201) without creating duplicates.
    Feedback does not alter Intel v3 decisions, Deploy sizing, or Watchtower behavior.
    """
    svc = ActionFeedbackService()
    row, _ = svc.create(user_id=str(user.id), data=body.model_dump())
    return row


@router.get("", response_model=list[ActionFeedbackResponse])
async def list_action_feedback(
    limit: int = Query(default=50, ge=1, le=200),
    ticker: str | None = Query(default=None),
    source_area: str | None = Query(default=None),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Return recent feedback events for the authenticated user, newest first.

    Optionally filter by ``ticker`` and/or ``source_area``
    (intel | deploy | watchtower | alert).
    """
    svc = ActionFeedbackService()
    return svc.list(
        user_id=str(user.id),
        limit=limit,
        ticker=ticker,
        source_area=source_area,
    )
