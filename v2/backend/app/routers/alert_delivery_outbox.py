"""Alert Delivery Outbox router — read-only, authenticated, user-scoped.

Returns provider-neutral outbox entries created by the delivery policy.
No external delivery occurs here. No Intel v3, Deploy, Watchtower, or
alert candidate mutations.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..middleware.auth import AuthenticatedUser, get_current_user
from ..models.alert_delivery_outbox import AlertDeliveryOutboxResponse
from ..services.alert.alert_delivery_outbox_service import AlertDeliveryOutboxService

router = APIRouter(prefix="/alert-delivery-outbox", tags=["alert-delivery-outbox"])

_VALID_CHANNELS = frozenset({"email", "push", "in_app"})
_VALID_STATUSES = frozenset({"pending", "suppressed", "sent", "failed", "cancelled"})


@router.get("", response_model=list[AlertDeliveryOutboxResponse])
async def list_alert_delivery_outbox(
    limit: int = Query(default=50, ge=1, le=200),
    channel: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Return delivery outbox entries for the authenticated user, newest first.

    Outbox entries are created deterministically from alert candidates by the
    delivery policy (policy v1). This endpoint is read-only; it does not create
    or modify records and does not trigger any external delivery.

    Filter by channel (email | push | in_app) and/or status
    (pending | suppressed | sent | failed | cancelled).
    """
    if channel is not None and channel not in _VALID_CHANNELS:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid channel '{channel}'. Valid values: {sorted(_VALID_CHANNELS)}",
        )
    if status is not None and status not in _VALID_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid status '{status}'. Valid values: {sorted(_VALID_STATUSES)}",
        )
    svc = AlertDeliveryOutboxService()
    return svc.list_outbox_entries(
        user_id=str(user.id),
        limit=limit,
        channel=channel,
        status=status,
    )
