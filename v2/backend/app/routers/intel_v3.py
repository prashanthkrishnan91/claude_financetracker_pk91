"""Intel v3 router — visible snapshot + distributed Run Intel endpoints.

GET  /intel/v3/snapshot                     — latest v3 snapshot (zero LLM)
POST /intel/v3/run                          — create ONE durable distributed
                                              run session (fast; no provider,
                                              no LLM, no policy, no snapshot)
GET  /intel/v3/sessions/active              — the user's active session, if any
GET  /intel/v3/sessions/{session_id}/status — lightweight read-only status
GET  /intel/v3/runs/{run_id}                — legacy run status read

Execution architecture (docs/ai/RUN_INTEL_DISTRIBUTED_WORKFLOW.md): the run
endpoint freezes the portfolio scope, creates the durable task graph and
activates the in-process worker supervisor. The browser then only POLLS the
status endpoints — polling observes work, it never performs or advances work.
This router must never import the retired bounded-drain / orchestrator
execution path (enforced by tests/test_distributed_architecture_boundary.py).

Feature flag: INTEL_V3_VISIBLE_SNAPSHOT_ENABLED
  When disabled, endpoints return 404 with flag-off message.
"""
from __future__ import annotations

import logging
import uuid as _uuid
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, status
from pydantic import BaseModel

from ..middleware.auth import AuthenticatedUser, get_current_user
from ..services.intelligence.v3.distributed.session_control_v1 import (
    SessionOwnershipError,
    SessionScopeError,
    create_distributed_session,
    find_active_session,
    get_session_status,
)
from ..services.intelligence.v3.distributed.worker_supervisor_v1 import (
    ensure_supervisor_running,
)
from ..services.intelligence.v3.intel_v3_service import (
    IntelV3Service,
    is_intel_v3_enabled,
)

router = APIRouter(prefix="/intel/v3", tags=["intel_v3"])
logger = logging.getLogger(__name__)


class RunIntelV3Request(BaseModel):
    """Body for POST /intel/v3/run.

    ``run_session_id`` is minted by the browser (crypto.randomUUID()) once per
    manual click. Retrying the same id is idempotent: it returns the existing
    session's status instead of creating a duplicate. Legacy callers may omit
    the body — the backend then mints the id.
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


def _parse_session_id(raw: Optional[str]) -> str:
    candidate = raw or str(_uuid.uuid4())
    try:
        return str(_uuid.UUID(str(candidate)))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"run_session_id must be a UUID, got: {candidate!r}",
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
    """Create ONE durable distributed Run Intel session and return fast.

    This endpoint only: verifies identity, adopts-or-creates the session row,
    freezes the immutable per-holding scope (DB reads only), creates the seed
    task graph, and activates the worker supervisor. It performs ZERO provider
    fetches, ZERO LLM calls, ZERO decision-policy runs, ZERO snapshot writes —
    the durable task graph executes in the backend worker regardless of what
    the browser does afterwards. Clients poll GET /sessions/{id}/status.
    """
    _check_flag()
    session_id = _parse_session_id(body.run_session_id if body else None)

    service = IntelV3Service(user_id=user.id)
    try:
        result = await create_distributed_session(
            client=service.client,
            user_id=str(user.id),
            session_id=session_id,
        )
    except SessionOwnershipError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "run_session_forbidden",
                "message": "This run session belongs to another user.",
            },
        )
    except SessionScopeError as exc:
        logger.error(
            "intel_v3.distributed_create_failed user_id=%s session=%s err=%s",
            user.id, session_id, exc,
        )
        return {
            "run_session_id": session_id,
            "session_status": "not_created",
            "reason": "run_session_create_failed",
            "plain_status": (
                "Could not start the run. If this persists, verify migration "
                "027_intel_run_distributed_tasks.sql has been applied, then retry."
            ),
            "retryable": True,
        }
    except Exception as exc:
        logger.error(
            "intel_v3.run_create_failed user_id=%s session=%s error=%s",
            user.id, session_id, exc,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Intel v3 run failed: {exc}",
        )

    if result.get("session_status") in ("created", "running"):
        await ensure_supervisor_running(client=service.client)
    return result


@router.get("/sessions/active")
async def get_active_session(
    user: AuthenticatedUser = Depends(get_current_user),
):
    """The user's latest non-terminal distributed session (or none).

    Read-only. Lets a returning page rediscover the run it started earlier.
    """
    _check_flag()
    service = IntelV3Service(user_id=user.id)
    session = await find_active_session(
        client=service.client, user_id=str(user.id)
    )
    if session is None:
        return {"active": False}
    session_status = await get_session_status(
        client=service.client,
        user_id=str(user.id),
        session_id=str(session.get("id")),
    )
    # A live session found on page return should have a live supervisor
    # (crash recovery when the process restarted mid-run). This starts the
    # observer-side recovery only — it never executes work in-request.
    await ensure_supervisor_running(client=service.client)
    return {"active": True, **session_status}


@router.get("/sessions/{session_id}/status")
async def get_run_session_status(
    session_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Lightweight read-only status for one session. Polling-safe: zero
    provider calls, zero LLM calls, zero task advancement."""
    _check_flag()
    parsed = _parse_session_id(session_id)
    service = IntelV3Service(user_id=user.id)
    try:
        return await get_session_status(
            client=service.client, user_id=str(user.id), session_id=parsed,
        )
    except SessionOwnershipError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "run_session_forbidden",
                "message": "This run session belongs to another user.",
            },
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
