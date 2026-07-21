"""Intel v3 router — visible snapshot endpoints.

GET  /intel/v3/snapshot        — read latest v3 snapshot (zero LLM calls)
POST /intel/v3/run             — one bounded request of a durable Run Intel
                                 session (see intel_run_session_flow_v1)
GET  /intel/v3/runs/{run_id}   — placeholder for run status polling

Feature flag: INTEL_V3_VISIBLE_SNAPSHOT_ENABLED
  When disabled, GET /snapshot returns 404 with flag-off message.
"""
from __future__ import annotations

import logging
import uuid as _uuid
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, status
from pydantic import BaseModel

from ..middleware.auth import AuthenticatedUser, get_current_user
from ..services.intelligence.v3.intel_run_session_flow_v1 import (
    SessionOwnershipError,
    run_intel_session_request,
)
from ..services.intelligence.v3.intel_v3_service import IntelV3Service, is_intel_v3_enabled

router = APIRouter(prefix="/intel/v3", tags=["intel_v3"])
logger = logging.getLogger(__name__)


class RunIntelV3Request(BaseModel):
    """Body for POST /intel/v3/run.

    ``run_session_id`` is minted by the browser (crypto.randomUUID()) once per
    manual click; every bounded automatic continuation of that click sends the
    SAME id. Legacy callers may omit the body entirely — the backend then
    mints a session id and returns it, but the frontend always supplies one.
    """

    run_session_id: Optional[str] = None

def _check_flag() -> None:
    """Raise 404 with clear message when the v3 feature flag is disabled."""
    if not is_intel_v3_enabled():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "Intel v3 snapshot path is not enabled. "
                "Set INTEL_V3_VISIBLE_SNAPSHOT_ENABLED=true to enable."
            ),
        )


@router.get("/snapshot")
async def get_latest_v3_snapshot(
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Get the latest Intel v3 snapshot for this user.

    Zero LLM calls. Zero provider calls.
    Returns 404 if no snapshot exists yet (user must POST /run first).
    Returns 404 if feature flag is disabled.
    """
    _check_flag()

    service = IntelV3Service(user_id=user.id)
    snapshot = await service.get_latest_snapshot()

    if snapshot is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code":    "no_snapshot",
                "message": "No Intel v3 snapshot exists yet. Run Intel v3 first.",
            },
        )

    return snapshot


@router.post("/run", status_code=status.HTTP_202_ACCEPTED)
async def run_intel_v3(
    body: Optional[RunIntelV3Request] = Body(default=None),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Execute one bounded request of a durable Run Intel session.

    One explicit manual click == one ``intel_run_sessions`` row, identified by
    the browser-minted ``run_session_id`` sent in the body. The SAME id on
    every bounded automatic continuation resumes that exact session — the
    backend never creates a replacement session for an existing id, never
    claims jobs outside the session, and never reports completion from the
    globally-latest snapshot.

    Per-request behavior (see ``intel_run_session_flow_v1``):
      * First use of an id: capture immutable holdings scope + stale subset +
        pre-session snapshot, create the session, enqueue one durable job per
        stale ticker (FK ``run_session_id``).
      * Continuations: credit interrupted-but-persisted tickers, drain one
        bounded batch of THIS session's jobs via the analyst-only
        orchestrator path (zero portfolio synthesis), and, once every job has
        succeeded, deterministically certify + publish ONE snapshot linked to
        the session. Publication failures retry without re-running analysis.
      * Completion is reported only when this session's own snapshot row
        (scalar column AND payload carrying the session id) is published,
        certified, and different from the pre-session snapshot.

    Legacy callers that omit the body get a backend-minted session id in the
    response (``run_session_id``) and identical semantics.
    """
    _check_flag()

    raw_session_id = (body.run_session_id if body else None) or str(_uuid.uuid4())
    try:
        session_id = str(_uuid.UUID(str(raw_session_id)))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"run_session_id must be a UUID, got: {raw_session_id!r}",
        )

    try:
        return await run_intel_session_request(
            user_id=user.id, run_session_id=session_id,
        )
    except SessionOwnershipError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "run_session_forbidden",
                "message": "This run session belongs to another user.",
            },
        )
    except Exception as exc:
        logger.error(
            "intel_v3.run_session_request_failed user_id=%s run_session_id=%s error=%s",
            user.id, session_id, exc,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Intel v3 run failed: {exc}",
        )


@router.get("/runs/{run_id}")
async def get_v3_run_status(
    run_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Get the status of a v3 run by run_id.

    Currently reads from the snapshot table to confirm the run completed.
    """
    _check_flag()

    service = IntelV3Service(user_id=user.id)
    try:
        result = await service._get_run_by_id(run_id)
        if result is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "run_not_found", "message": f"Run {run_id} not found."},
            )
        return result
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("intel_v3.run_status_failed user_id=%s run_id=%s error=%s", user.id, run_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Run status lookup failed: {exc}",
        )
