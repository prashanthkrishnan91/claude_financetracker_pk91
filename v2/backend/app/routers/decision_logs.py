"""Decision Logs router — Deploy recommendation snapshots vs actual actions."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..middleware.auth import AuthenticatedUser, get_current_user
from ..models.decision import DecisionLogCreateRequest, DecisionLogResponse, DecisionLogUpdateRequest
from ..services.decision_log_service import DecisionLogService

router = APIRouter(prefix="/decision-logs", tags=["decision-logs"])


@router.post("", response_model=DecisionLogResponse, status_code=status.HTTP_201_CREATED)
async def create_decision_log(
    body: DecisionLogCreateRequest,
    user: AuthenticatedUser = Depends(get_current_user),
):
    svc = DecisionLogService()
    return svc.create(user_id=str(user.id), data=body.model_dump())


@router.get("", response_model=list[DecisionLogResponse])
async def list_decision_logs(
    limit: int = Query(default=25, ge=1, le=200),
    user: AuthenticatedUser = Depends(get_current_user),
):
    svc = DecisionLogService()
    return svc.list(user_id=str(user.id), limit=limit)


@router.get("/{decision_log_id}", response_model=DecisionLogResponse)
async def get_decision_log(
    decision_log_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
):
    svc = DecisionLogService()
    row = svc.get(user_id=str(user.id), decision_log_id=decision_log_id)
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Decision log not found")
    return row


@router.patch("/{decision_log_id}", response_model=DecisionLogResponse)
async def update_decision_log(
    decision_log_id: str,
    body: DecisionLogUpdateRequest,
    user: AuthenticatedUser = Depends(get_current_user),
):
    patch = body.model_dump(exclude_unset=True)
    svc = DecisionLogService()
    row = svc.update(user_id=str(user.id), decision_log_id=decision_log_id, patch=patch)
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Decision log not found")
    return row


@router.delete("/{decision_log_id}")
async def delete_decision_log(
    decision_log_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
):
    svc = DecisionLogService()
    deleted = svc.delete(user_id=str(user.id), decision_log_id=decision_log_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Decision log not found")
    return {"deleted": True}
