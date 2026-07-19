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

from ..config import get_settings
from ..middleware.auth import AuthenticatedUser, get_current_user
from ..services.intelligence.v3.analyst_refresh_on_demand_drain_v1 import (
    run_on_demand_drain,
)
from ..services.intelligence.v3.intel_v3_service import IntelV3Service, is_intel_v3_enabled
from ..services.intelligence.v3.watchtower_intel_republisher_v1 import (
    PUBLISH_CERTIFIED_CURRENT,
)

router = APIRouter(prefix="/intel/v3", tags=["intel_v3"])
logger = logging.getLogger(__name__)

# Zero-queued statuses from IntelV3Service.enqueue_run_v3() that genuinely mean
# the request succeeded without any analyst work — either evidence was already
# current, or a zero-LLM deterministic recertification (prewarm) rebuilt the
# snapshot from already-persisted evidence. Only these may let an existing
# worker_certified + certified_current snapshot count as this request's own
# completed outcome when nothing was queued.
_ZERO_QUEUED_SUCCESS_STATUSES = frozenset({
    "analyst_evidence_current",
    "mapping_version_recertified",
    "stage7_contract_recertified",
    "stage8e_contract_recertified",
    "stage8f_contract_recertified",
})

# Zero-queued statuses that mean the request itself failed — a historical
# certified-current snapshot must never be allowed to paper over these, and
# they must never fall through to "no stale evidence to refresh" (that value
# implies nothing needed doing, which is false — recertification was
# attempted and failed).
_ZERO_QUEUED_FAILURE_STATUSES = frozenset({
    "enqueue_failed",
    "failed",
    "mapping_version_recertification_failed",
    "stage7_contract_recertification_failed",
    "stage8e_contract_recertification_failed",
    "stage8f_contract_recertification_failed",
})


def _next_required_action(
    *,
    status_value: str,
    on_demand_processing_enabled: bool,
    queued_ticker_count: int,
    drain_ran: bool,
    drain_remaining: bool,
    snapshot_available_after_run: bool,
    snapshot_writes_enabled: bool,
) -> str:
    """Derive an honest, operator-facing next step from the run outcome.

    Never implies a snapshot is being built when it is not — the whole point
    of Stage 13B is to stop the queue-only 202 from silently reading as
    "in progress" when nothing will ever drain it.

    Priority order (each branch outranks everything below it):
      1. no active holdings
      2. a zero-queued request-level failure (enqueue or deterministic
         recertification failed) -> retry, never "no stale evidence"
      3. queued jobs + on-demand processing disabled -> queue-only
      4. drain ran + snapshot writes disabled -> write-guard (outranks
         "continue" — reclicking can never publish while writes are off)
      5. drain ran + remaining resumable work -> continue draining
      6. a newly/currently certified snapshot is available -> complete
      7. nothing was queued -> no stale evidence to refresh
      8. otherwise -> retry
    """
    if status_value == "no_active_holdings":
        return "add_positions_before_running_intel"
    if status_value in _ZERO_QUEUED_FAILURE_STATUSES:
        return "reclick_run_intel_to_retry"
    if queued_ticker_count > 0 and not on_demand_processing_enabled:
        return (
            "queue_only_enable_intel_v3_on_demand_refresh_enabled_or_run_"
            "analyst_refresh_worker_entrypoint_separately"
        )
    if drain_ran and not snapshot_writes_enabled:
        return "on_demand_drain_completed_but_intel_v3_snapshot_writes_enabled_is_false"
    if drain_ran and drain_remaining:
        return "reclick_run_intel_or_run_worker_entrypoint_to_continue_draining"
    if snapshot_available_after_run:
        return "none_certified_snapshot_current"
    if queued_ticker_count == 0:
        return "none_no_stale_evidence_to_refresh"
    return "reclick_run_intel_to_retry"


async def _augment_with_on_demand_status(
    service: IntelV3Service,
    result: dict,
) -> dict:
    """Add Stage 13B operational-truth fields to the enqueue_run_v3() result.

    Reuses the existing bounded ``run_on_demand_drain`` (Stage 13B) so a
    manual Run Intel click can produce a certified snapshot without an
    always-on worker service — see analyst_refresh_on_demand_drain_v1.py.
    Read-only augmentation: never changes ``result``'s existing keys.
    """
    settings = get_settings()
    on_demand_enabled = settings.intel_v3_on_demand_refresh_enabled
    snapshot_writes_enabled = settings.intel_v3_snapshot_writes_enabled
    queued_ticker_count = int(result.get("queued_ticker_count") or 0)
    existing_certified_snapshot_id = result.get("existing_certified_snapshot_id")
    status_value = str(result.get("status") or "")

    drain_ran = False
    drain_remaining = False
    jobs_attempted = jobs_succeeded = jobs_failed = 0

    if on_demand_enabled and queued_ticker_count > 0:
        drain_ran = True
        drain_result = await run_on_demand_drain(
            user_id=service.user_id, client=service.client,
        )
        jobs_attempted = drain_result.jobs_attempted
        jobs_succeeded = drain_result.jobs_succeeded
        jobs_failed = drain_result.jobs_failed
        drain_remaining = drain_result.run_resumable

    latest_snapshot = await service.get_latest_snapshot()
    latest_snapshot_id = (
        latest_snapshot.get("snapshot_id") if isinstance(latest_snapshot, dict) else None
    )
    latest_is_certified_current = (
        isinstance(latest_snapshot, dict)
        and latest_snapshot.get("snapshot_source") == "worker_certified"
        and latest_snapshot.get("evidence_freshness_state") == PUBLISH_CERTIFIED_CURRENT
    )

    if queued_ticker_count > 0:
        # This request queued work, so completion requires proof THIS
        # request actually published a new certified snapshot — not merely
        # that an older worker_certified + certified_current snapshot still
        # happens to be sitting there untouched. Requires the full chain
        # (on-demand enabled, drain ran, nothing left resumable, writes
        # enabled) AND a concrete latest snapshot id that differs from
        # whatever certified snapshot (if any) existed before this request.
        snapshot_available_after_run = (
            on_demand_enabled
            and drain_ran
            and not drain_remaining
            and snapshot_writes_enabled
            and latest_is_certified_current
            and latest_snapshot_id is not None
            and latest_snapshot_id != existing_certified_snapshot_id
        )
    elif status_value in _ZERO_QUEUED_SUCCESS_STATUSES:
        # Nothing was queued this request, but the status confirms it's a
        # genuine no-op (evidence already current) or a successful zero-LLM
        # deterministic recertification — an already-current certified
        # snapshot legitimately means "nothing to do."
        snapshot_available_after_run = latest_is_certified_current
    else:
        # Zero queued for any other reason (no_active_holdings, enqueue
        # failure, a recertification failure, or an unrecognized status) must
        # never borrow completeness from a historical snapshot — that
        # snapshot did not come from this request succeeding.
        snapshot_available_after_run = False

    result["on_demand_processing_enabled"] = on_demand_enabled
    result["on_demand_jobs_attempted"] = jobs_attempted
    result["on_demand_jobs_succeeded"] = jobs_succeeded
    result["on_demand_jobs_failed"] = jobs_failed
    result["snapshot_available_after_run"] = snapshot_available_after_run
    result["next_required_action"] = _next_required_action(
        status_value=status_value,
        on_demand_processing_enabled=on_demand_enabled,
        queued_ticker_count=queued_ticker_count,
        drain_ran=drain_ran,
        drain_remaining=drain_remaining,
        snapshot_available_after_run=snapshot_available_after_run,
        snapshot_writes_enabled=snapshot_writes_enabled,
    )
    return result


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
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Enqueue a full Intel v3 analyst refresh for all active holdings.

    Stage 3.3 — all-or-nothing certified intelligence run contract.
    Stage 13B — bounded on-demand evidence drain (operational-truth fields).

    The Run Intel v3 button means "start a new certified intelligence run."
    This endpoint:

      1. Enqueues a durable ``analyst_refresh_jobs`` row for EVERY stale
         active holding (idempotent — repeated clicks do not create
         duplicate jobs).
      2. Returns ``status=refresh_requested`` or ``refresh_in_progress``.
      3. Does NOT build a snapshot, does NOT run any LLM analysis in-request
         beyond the bounded drain in step 4.
      4. If ``INTEL_V3_ON_DEMAND_REFRESH_ENABLED=true``, drains a bounded
         number of the jobs it just enqueued in-request (reusing
         ``AnalystRefreshWorker`` — capped batches/runtime, see
         ``analyst_refresh_on_demand_drain_v1.py``) so a manual click can
         reach a certified snapshot without the separate always-on
         ``analyst_refresh_worker_v1`` Railway service. When the flag is
         false (default), the response says so explicitly instead of
         implying a snapshot is being built.

    Response carries operational-truth fields so the UI never overclaims:
    ``on_demand_processing_enabled``, ``on_demand_jobs_attempted/succeeded/
    failed``, ``snapshot_available_after_run``, ``next_required_action``.

    The separately-deployed ``analyst_refresh_worker_v1`` Railway service
    (polling, ``--loop``) remains available and optional — it will pick up
    any jobs this request did not finish draining, regardless of whether
    on-demand draining is enabled.
    """
    _check_flag()

    service = IntelV3Service(user_id=user.id)
    try:
        result = await service.enqueue_run_v3()
    except Exception as exc:
        logger.error("intel_v3.enqueue_run_failed user_id=%s error=%s", user.id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Intel v3 enqueue failed: {exc}",
        )

    try:
        result = await _augment_with_on_demand_status(service, result)
    except Exception as exc:
        # On-demand augmentation is best-effort operational truth-telling on
        # top of an already-successful enqueue — never fail the whole click
        # over it, but log so it's visible in Railway.
        logger.warning(
            "intel_v3.on_demand_status_augment_failed user_id=%s error=%s",
            user.id, exc,
        )
        result.setdefault("on_demand_processing_enabled", get_settings().intel_v3_on_demand_refresh_enabled)
        result.setdefault("on_demand_jobs_attempted", 0)
        result.setdefault("on_demand_jobs_succeeded", 0)
        result.setdefault("on_demand_jobs_failed", 0)
        result.setdefault("snapshot_available_after_run", False)
        result.setdefault("next_required_action", "reclick_run_intel_to_retry")
    return result


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
