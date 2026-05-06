"""Intel v3 router — visible snapshot endpoints.

GET  /intel/v3/snapshot        — read latest v3 snapshot (zero LLM calls)
POST /intel/v3/run             — trigger a v3 decision run
GET  /intel/v3/runs/{run_id}   — placeholder for run status polling

Feature flag: INTEL_V3_VISIBLE_SNAPSHOT_ENABLED
  When disabled, GET /snapshot returns 404 with flag-off message.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status

from ..middleware.auth import AuthenticatedUser, get_current_user
from ..services.intelligence.v3.intel_v3_service import IntelV3Service, is_intel_v3_enabled

router = APIRouter(prefix="/intel/v3", tags=["intel_v3"])
logger = logging.getLogger(__name__)


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
    background_tasks: BackgroundTasks,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Trigger an Intel v3 decision run.

    Runs the v3 decision kernel on existing signals (no new LLM calls).
    Creates one immutable snapshot and makes it the active snapshot.
    Returns immediately; the snapshot is available at GET /snapshot after completion.

    Note: Currently synchronous for portfolios ≤ ~50 tickers.
    For async behavior, the run will be backgrounded and status polled.
    """
    _check_flag()

    service = IntelV3Service(user_id=user.id)
    try:
        snapshot = await service.run_v3()
        return {
            "status":      "completed",
            "snapshot_id": snapshot.get("snapshot_id"),
            "run_id":      snapshot.get("run_id"),
            "total_cards": len(snapshot.get("current_holdings", [])),
            "action_counts": snapshot.get("action_counts", {}),
        }
    except Exception as exc:
        logger.error("intel_v3.run_failed user_id=%s error=%s", user.id, exc)
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
