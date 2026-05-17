"""Alert Candidates router — read-only, authenticated, user-scoped.

Returns deterministic alert candidates created by the alert trigger policy.
No Intel v3 decisions, Deploy sizing, or Watchtower behavior is modified here.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query

from ..middleware.auth import AuthenticatedUser, get_current_user
from ..models.alert_candidate import AlertCandidateResponse
from ..services.alert.alert_candidate_service import AlertCandidateService

router = APIRouter(prefix="/alert-candidates", tags=["alert-candidates"])


@router.get("", response_model=list[AlertCandidateResponse])
async def list_alert_candidates(
    limit: int = Query(default=50, ge=1, le=200),
    ticker: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Return alert candidates for the authenticated user, newest first.

    Candidates are created by the deterministic alert trigger policy (policy v1).
    This endpoint is read-only; it does not create or modify any records.
    Optionally filter by ticker and/or status
    (candidate | suppressed | dismissed | snoozed | expired).
    """
    svc = AlertCandidateService()
    return svc.list_candidates(
        user_id=str(user.id),
        limit=limit,
        ticker=ticker,
        status=status,
    )
