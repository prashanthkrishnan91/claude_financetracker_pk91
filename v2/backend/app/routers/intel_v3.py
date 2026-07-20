"""Intel v3 router — visible snapshot endpoints.

GET  /intel/v3/snapshot        — read latest v3 snapshot (zero LLM calls)
POST /intel/v3/run             — trigger a v3 decision run
GET  /intel/v3/runs/{run_id}   — placeholder for run status polling

Feature flag: INTEL_V3_VISIBLE_SNAPSHOT_ENABLED
  When disabled, GET /snapshot returns 404 with flag-off message.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel

from ..config import get_settings
from ..middleware.auth import AuthenticatedUser, get_current_user
from ..services.intelligence.v3.analyst_refresh_job_store_v1 import count_due_jobs
from ..services.intelligence.v3.analyst_refresh_on_demand_drain_v1 import (
    run_on_demand_drain,
)
from ..services.intelligence.v3.intel_v3_service import IntelV3Service, is_intel_v3_enabled
from ..services.intelligence.v3.watchtower_intel_republisher_v1 import (
    PUBLISH_CERTIFIED_CURRENT,
)

router = APIRouter(prefix="/intel/v3", tags=["intel_v3"])
logger = logging.getLogger(__name__)


class RunIntelRunRequest(BaseModel):
    """Optional POST /intel/v3/run body.

    The initial explicit Run Intel click omits it (or sends an empty body).
    Each automatic frontend continuation supplies the ``run_session_id`` the
    initial response returned, so the whole click + continuation sequence
    shares one durable session identity. Backward compatible: an absent body
    is treated as a brand-new manual action.
    """

    run_session_id: Optional[str] = None


# ── Durable Run Intel session anchor ─────────────────────────────────────────
#
# Migration-free session tracking: a session's durable identity is the
# ``analyst_refresh_jobs.refresh_window`` its jobs carry (refresh_window ==
# run_session_id). To also remember "which session is currently in-flight for
# this user" across a click's automatic continuations — without a new table or
# column — the router writes a single sentinel "anchor" row per session into
# the same analyst_refresh_jobs table.
#
# The anchor uses ``status = 'succeeded'`` — a value the table's existing
# CHECK constraint already allows (migration 018:
# ``CHECK (status IN ('pending','claimed','succeeded','failed'))``) AND that
# ``count_due_jobs`` / ``claim_due_jobs`` never even fetch (they query only
# ``status IN ('pending','failed')``), so the anchor is invisible to the queue.
# Active vs concluded is tracked by the sentinel TICKER (never a real holding),
# flipped active→done on completion — no out-of-CHECK status is ever written.
# The anchor's refresh_window IS the run_session_id. Old date-window rows and
# other users' rows are untouched.
_SESSION_ANCHOR_TICKER_ACTIVE = "__run_session_active__"
_SESSION_ANCHOR_TICKER_DONE = "__run_session_done__"
_SESSION_ANCHOR_STATUS = "succeeded"  # CHECK-valid; never claimable/counted
# All sentinel tickers, for readers that must exclude anchor rows.
_SESSION_ANCHOR_TICKERS = (_SESSION_ANCHOR_TICKER_ACTIVE, _SESSION_ANCHOR_TICKER_DONE)


def _rows(res: Any) -> list[dict]:
    data = getattr(res, "data", None)
    return data if isinstance(data, list) else []


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_active_session_anchor(client: Any, user_id: Any) -> Optional[dict]:
    """The most recent still-active session anchor for this user, or None."""
    try:
        rows = _rows(
            client.table("analyst_refresh_jobs")
            .select("*")
            .eq("user_id", str(user_id))
            .eq("ticker", _SESSION_ANCHOR_TICKER_ACTIVE)
            .eq("status", _SESSION_ANCHOR_STATUS)
            .order("requested_at", desc=True)
            .execute()
        )
    except Exception as exc:
        logger.debug("intel_v3.session_anchor_read_failed user_id=%s err=%s", user_id, exc)
        return None
    if not rows:
        return None
    rows.sort(key=lambda r: r.get("requested_at") or "", reverse=True)
    return rows[0]


def _write_session_anchor(client: Any, user_id: Any, run_session_id: str, now_iso: str) -> None:
    try:
        client.table("analyst_refresh_jobs").insert({
            "user_id": str(user_id),
            "ticker": _SESSION_ANCHOR_TICKER_ACTIVE,
            "refresh_window": str(run_session_id),
            # CHECK-valid status the queue never fetches (count/claim query only
            # pending/failed) — the anchor is invisible to queue processing.
            "status": _SESSION_ANCHOR_STATUS,
            "attempts": 0,
            "requested_at": now_iso,
            "updated_at": now_iso,
        }).execute()
    except Exception as exc:
        logger.debug("intel_v3.session_anchor_write_failed user_id=%s err=%s", user_id, exc)


def _complete_session_anchor(client: Any, anchor_id: Any, now_iso: str) -> None:
    """Conclude an anchor by flipping its sentinel ticker active→done (its
    status stays the CHECK-valid ``succeeded``, so no invalid status is ever
    written)."""
    if anchor_id is None:
        return
    try:
        (
            client.table("analyst_refresh_jobs")
            .update({"ticker": _SESSION_ANCHOR_TICKER_DONE, "updated_at": now_iso})
            .eq("id", anchor_id)
            .execute()
        )
    except Exception as exc:
        logger.debug("intel_v3.session_anchor_complete_failed err=%s", exc)


async def _snapshot_run_session_id(service: IntelV3Service) -> Optional[str]:
    """run_session_id embedded in this user's latest certified snapshot, if any."""
    try:
        snap = await service.get_latest_snapshot()
    except Exception:
        return None
    if isinstance(snap, dict) and snap.get("run_session_id"):
        return str(snap.get("run_session_id"))
    return None


async def _session_needs_publication_retry(
    service: IntelV3Service, run_session_id: Optional[str],
) -> bool:
    """True when this session's ticker work already succeeded but no
    session-linked certified snapshot has been published yet — i.e. a prior
    certification/publication failed and a publication-only retry is warranted.

    Read-only and defensive: returns False on any error, and False whenever
    there is no real (non-anchor) succeeded ticker job for the session, so a
    session with no completed work never triggers a spurious drain.
    """
    if not run_session_id:
        return False
    try:
        succeeded = _rows(
            service.client.table("analyst_refresh_jobs")
            .select("ticker")
            .eq("user_id", str(service.user_id))
            .eq("status", "succeeded")
            .eq("refresh_window", str(run_session_id))
            .execute()
        )
    except Exception:
        return False
    real = [
        r for r in succeeded
        if not str(r.get("ticker") or "").startswith("__run_session")
    ]
    if not real:
        return False
    # Already certified for this exact session? then nothing to retry.
    return (await _snapshot_run_session_id(service)) != str(run_session_id)


async def _resolve_run_session_id(
    service: IntelV3Service,
    enqueue_result: dict,
    request_session_id: Optional[str],
    now_iso: str,
) -> tuple[str, bool]:
    """Resolve the durable run_session_id for this POST /run, returning
    ``(run_session_id, is_new_session)``.

    * An explicit continuation supplies ``request_session_id`` — reuse it.
    * A no-id request that queued real work (or is still refreshing) reuses the
      user's in-flight session anchor when one exists and its session has not
      already published — this is the automatic-continuation path (the initial
      click created the anchor; each continuation reuses it).
    * A no-id request that queued nothing (evidence already current) is a fresh
      standalone manual action: it supersedes any in-flight anchor and mints a
      new session — so a same-day second Run Intel action always gets a
      distinct id and can never be completed by the first session's snapshot.
    """
    if request_session_id:
        return str(request_session_id), False

    status_value = str(enqueue_result.get("status") or "")
    queued = int(enqueue_result.get("queued_ticker_count") or 0)
    is_work = queued > 0 or status_value in ("refresh_requested", "refresh_in_progress")

    client = service.client
    anchor = _read_active_session_anchor(client, service.user_id)

    if is_work and anchor is not None:
        anchor_session = str(anchor.get("refresh_window") or "")
        published_session = await _snapshot_run_session_id(service)
        if anchor_session and anchor_session != published_session:
            # In-flight session whose work is not yet certified — continue it.
            return anchor_session, False
        # The anchored session already published — conclude it and start fresh.
        _complete_session_anchor(client, anchor.get("id"), now_iso)
    elif anchor is not None:
        # A zero-queued standalone action supersedes any in-flight session.
        _complete_session_anchor(client, anchor.get("id"), now_iso)

    new_session_id = str(uuid.uuid4())
    if is_work:
        _write_session_anchor(client, service.user_id, new_session_id, now_iso)
    return new_session_id, True

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

# Durable-job-state classification for the current user's current active
# tickers (product-recovery Blocker 3). Derived from the existing
# analyst_refresh_job_store_v1.count_due_jobs breakdown — no new queue, no
# new table.
#   due      — total_due > 0: immediately claimable, bounded drain runs.
#   backoff  — only failed-but-retryable jobs remain, all in their backoff
#              window (next_retry_at in the future) — nothing to claim yet.
#   terminal — at least one job has exhausted its retry budget.
#   none     — no due/backoff/terminal work for this user's active tickers.
_JOB_STATE_NONE = "none"
_JOB_STATE_DUE = "due"
_JOB_STATE_BACKOFF = "backoff"
_JOB_STATE_TERMINAL = "terminal"

# Action strings for the two new durable-job states, plus the active-ticker
# lookup failure. Deliberately NOT prefixed "reclick_" — the frontend's
# existing "reclick_" -> partial/auto-continue classification must never
# apply to these (backoff/terminal/lookup-failure must present as a stopped,
# explicit Retry state, never an automatic continuation).
_ACTION_ANALYST_JOBS_BACKOFF = "analyst_jobs_in_backoff_retry_after_window"
_ACTION_ANALYST_JOBS_TERMINAL = "analyst_jobs_retry_budget_exhausted"
_ACTION_ACTIVE_TICKERS_LOOKUP_FAILED = "active_tickers_lookup_failed_retry"


def _next_required_action(
    *,
    status_value: str,
    on_demand_processing_enabled: bool,
    queued_ticker_count: int,
    drain_ran: bool,
    drain_remaining: bool,
    snapshot_available_after_run: bool,
    snapshot_writes_enabled: bool,
    job_state: str = _JOB_STATE_NONE,
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
      4. durable jobs in backoff (none due yet) -> explicit backoff retry
      5. durable jobs with an exhausted retry budget -> terminal failure
      6. drain ran + snapshot writes disabled -> write-guard (outranks
         "continue" — reclicking can never publish while writes are off)
      7. drain ran + remaining resumable work -> continue draining
      8. a newly/currently certified snapshot is available -> complete
      9. nothing was queued AND no drain ran -> no stale evidence to refresh
      10. otherwise -> retry

    Branches 4/5 outrank 6-9 unconditionally: a historical certified snapshot
    (branch 8) or a stale "nothing to do" read (branch 9) must never mask a
    backlog of durable work still waiting on backoff or permanently blocked
    by an exhausted retry budget for this user's active holdings.
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
    if job_state == _JOB_STATE_BACKOFF:
        return _ACTION_ANALYST_JOBS_BACKOFF
    if job_state == _JOB_STATE_TERMINAL:
        return _ACTION_ANALYST_JOBS_TERMINAL
    if drain_ran and not snapshot_writes_enabled:
        return "on_demand_drain_completed_but_intel_v3_snapshot_writes_enabled_is_false"
    if drain_ran and drain_remaining:
        return "reclick_run_intel_or_run_worker_entrypoint_to_continue_draining"
    if snapshot_available_after_run:
        return "none_certified_snapshot_current"
    # Only a genuine no-op — nothing queued this click AND no drain of
    # existing durable work was ever attempted — may report "nothing was
    # stale." When a drain ran (because existing pending/retryable work was
    # recognized even though this click queued zero new jobs) but did not
    # produce a provably new certified snapshot, that is a retry, never a
    # false "nothing needed doing."
    if queued_ticker_count == 0 and not drain_ran:
        return "none_no_stale_evidence_to_refresh"
    return "reclick_run_intel_to_retry"


async def _augment_with_on_demand_status(
    service: IntelV3Service,
    result: dict,
    run_session_id: Optional[str] = None,
) -> dict:
    """Add Stage 13B operational-truth fields to the enqueue_run_v3() result.

    Reuses the existing bounded ``run_on_demand_drain`` (Stage 13B) so a
    manual Run Intel click can produce a certified snapshot without an
    always-on worker service — see analyst_refresh_on_demand_drain_v1.py.
    Read-only augmentation: never changes ``result``'s existing keys.

    Product-recovery Blockers 2/3: ``queued_ticker_count`` alone never
    decides whether/how to process work. Whenever on-demand processing is
    enabled and this isn't a no-active-holdings or zero-queued-failure
    status, this ALWAYS resolves the current user's current active tickers
    first — including when this click queued new work — and scopes every
    subsequent claim/count/drain to (user_id, active_tickers). If that
    lookup itself fails, this never falls back to an unscoped drain; it
    returns an explicit retryable failure instead. It then classifies the
    scoped durable-job state (due / backoff / terminal / none) from the
    existing ``count_due_jobs`` breakdown and only drains when jobs are
    immediately due — a backoff or terminal state is reported honestly and
    never silently drained, looped, or masked by a historical snapshot.
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
    job_state = _JOB_STATE_NONE
    earliest_retry_at: str | None = None

    should_resolve_job_state = (
        on_demand_enabled
        and status_value != "no_active_holdings"
        and status_value not in _ZERO_QUEUED_FAILURE_STATUSES
    )

    if should_resolve_job_state:
        try:
            active_tickers = await service._get_active_tickers()
        except Exception as exc:
            logger.warning(
                "intel_v3.active_tickers_lookup_failed user_id=%s error=%s",
                service.user_id, exc,
            )
            # Never fall back to an unscoped drain on a lookup failure —
            # return an explicit retryable failure so the single Run Intel
            # control stays usable for a later retry.
            result["on_demand_processing_enabled"] = on_demand_enabled
            result["on_demand_jobs_attempted"] = 0
            result["on_demand_jobs_succeeded"] = 0
            result["on_demand_jobs_failed"] = 0
            result["snapshot_available_after_run"] = False
            result["next_required_action"] = _ACTION_ACTIVE_TICKERS_LOOKUP_FAILED
            return result

        due_counts = count_due_jobs(
            service.client,
            user_id=str(service.user_id),
            tickers=active_tickers or None,
            run_session_id=run_session_id,
        )
        total_due = due_counts.get("total_due", 0)
        failed_not_yet_due = due_counts.get("failed_not_yet_due", 0)
        failed_terminal = due_counts.get("failed_terminal", 0)

        if total_due > 0:
            job_state = _JOB_STATE_DUE
            drain_ran = True
            drain_result = await run_on_demand_drain(
                user_id=service.user_id, client=service.client, tickers=active_tickers,
                run_session_id=run_session_id,
            )
            jobs_attempted = drain_result.jobs_attempted
            jobs_succeeded = drain_result.jobs_succeeded
            jobs_failed = drain_result.jobs_failed
            drain_remaining = drain_result.run_resumable
        elif failed_not_yet_due > 0:
            job_state = _JOB_STATE_BACKOFF
            earliest_retry_at = due_counts.get("earliest_retry_at")
        elif failed_terminal > 0:
            job_state = _JOB_STATE_TERMINAL
        elif await _session_needs_publication_retry(service, run_session_id):
            # No ticker work left to claim, but this session's tickers already
            # succeeded and no session-linked snapshot was published yet (a
            # prior certification/publication failed). Run the bounded drain
            # whose ONLY effect here is the worker's publication-only retry —
            # zero per-ticker analyst calls, zero portfolio synthesis — so a
            # failed publish has a same-session recovery path reachable from a
            # normal continuation, not just from a race.
            job_state = _JOB_STATE_NONE
            drain_ran = True
            drain_result = await run_on_demand_drain(
                user_id=service.user_id, client=service.client, tickers=active_tickers,
                run_session_id=run_session_id,
            )
            jobs_attempted = drain_result.jobs_attempted
            jobs_succeeded = drain_result.jobs_succeeded
            jobs_failed = drain_result.jobs_failed
            drain_remaining = drain_result.run_resumable
        else:
            job_state = _JOB_STATE_NONE

    latest_snapshot = await service.get_latest_snapshot()
    latest_snapshot_id = (
        latest_snapshot.get("snapshot_id") if isinstance(latest_snapshot, dict) else None
    )
    latest_is_certified_current = (
        isinstance(latest_snapshot, dict)
        and latest_snapshot.get("snapshot_source") == "worker_certified"
        and latest_snapshot.get("evidence_freshness_state") == PUBLISH_CERTIFIED_CURRENT
    )
    # Session-linkage gate: when this request carries a durable run_session_id,
    # a snapshot may only complete it if the snapshot itself is linked to THIS
    # session (its payload embeds the same run_session_id). A pre-existing
    # historical snapshot — even a worker_certified + certified_current one —
    # has no link to a brand-new session and must never satisfy its completion.
    # When no session id is threaded (legacy direct callers), this is a no-op.
    snapshot_is_session_linked = run_session_id is None or (
        isinstance(latest_snapshot, dict)
        and str(latest_snapshot.get("run_session_id") or "") == str(run_session_id)
    )

    if job_state in (_JOB_STATE_BACKOFF, _JOB_STATE_TERMINAL):
        # A historical certified-current snapshot must never mask a backlog
        # of durable work still waiting on backoff or permanently blocked by
        # an exhausted retry budget for this user's active holdings.
        snapshot_available_after_run = False
    elif drain_ran:
        # A drain happened this request — either because this click queued
        # new work, or because it recognized already-existing durable work
        # left over from an earlier interrupted click. Either way, completion
        # requires proof THIS request actually published a new certified
        # snapshot — not merely that an older worker_certified +
        # certified_current snapshot still happens to be sitting there
        # untouched. Requires the full chain (on-demand enabled, nothing left
        # resumable, writes enabled) AND a concrete latest snapshot id that
        # differs from whatever certified snapshot (if any) existed before
        # this request.
        snapshot_available_after_run = (
            on_demand_enabled
            and not drain_remaining
            and snapshot_writes_enabled
            and latest_is_certified_current
            and latest_snapshot_id is not None
            and latest_snapshot_id != existing_certified_snapshot_id
            and snapshot_is_session_linked
        )
    elif status_value in _ZERO_QUEUED_SUCCESS_STATUSES:
        # Nothing was queued this request, but the status confirms it's a
        # genuine no-op (evidence already current) or a successful zero-LLM
        # deterministic recertification — an already-current certified
        # snapshot legitimately means "nothing to do" ONLY when that snapshot
        # is linked to this session (never a historical one for a new session).
        snapshot_available_after_run = latest_is_certified_current and snapshot_is_session_linked
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
    if job_state == _JOB_STATE_BACKOFF:
        # Additive — only present in the backoff state, representing that
        # durable-job state's own retry timing (small job-store extension;
        # see analyst_refresh_job_store_v1.count_due_jobs's earliest_retry_at).
        result["earliest_retry_at"] = earliest_retry_at
    result["next_required_action"] = _next_required_action(
        status_value=status_value,
        on_demand_processing_enabled=on_demand_enabled,
        queued_ticker_count=queued_ticker_count,
        drain_ran=drain_ran,
        drain_remaining=drain_remaining,
        snapshot_available_after_run=snapshot_available_after_run,
        snapshot_writes_enabled=snapshot_writes_enabled,
        job_state=job_state,
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
    body: Optional[RunIntelRunRequest] = None,
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

    request_session_id = body.run_session_id if body is not None else None
    service = IntelV3Service(user_id=user.id)
    try:
        result = await service.enqueue_run_v3(run_session_id=request_session_id)
    except Exception as exc:
        logger.error("intel_v3.enqueue_run_failed user_id=%s error=%s", user.id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Intel v3 enqueue failed: {exc}",
        )

    # Durable session identity spanning this explicit click and its automatic
    # continuations. Resolved from the request (continuation), the user's
    # in-flight session anchor (reuse), or minted fresh (new manual action).
    now_iso = _now_iso()
    try:
        run_session_id, is_new_session = await _resolve_run_session_id(
            service, result, request_session_id, now_iso,
        )
    except Exception as exc:
        logger.warning(
            "intel_v3.run_session_resolve_failed user_id=%s error=%s", user.id, exc,
        )
        run_session_id = str(request_session_id) if request_session_id else str(uuid.uuid4())
        is_new_session = request_session_id is None
    result["run_session_id"] = run_session_id
    result["run_session_is_new"] = is_new_session

    # A fresh manual click enqueued its jobs under the per-day window before the
    # session id existed; re-stamp exactly those rows onto this session so every
    # bounded batch and the completion/count logic scope to it. Continuations
    # (request supplied the id) already enqueued session-windowed → no-op.
    if is_new_session and request_session_id is None:
        enqueue_window = result.get("enqueue_refresh_window")
        if enqueue_window and str(enqueue_window) != str(run_session_id):
            try:
                from ..services.intelligence.v3.analyst_refresh_job_store_v1 import (
                    restamp_jobs_to_session,
                )
                restamp_jobs_to_session(
                    service.client,
                    user_id=service.user_id,
                    from_window=str(enqueue_window),
                    run_session_id=run_session_id,
                    tickers=result.get("enqueued_tickers") or None,
                )
            except Exception as exc:
                logger.warning(
                    "intel_v3.run_session_restamp_failed user_id=%s error=%s",
                    user.id, exc,
                )

    try:
        result = await _augment_with_on_demand_status(
            service, result, run_session_id=run_session_id,
        )
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

    # Once this session has produced its own certified snapshot, conclude its
    # in-flight anchor so a later same-day manual action starts a fresh session
    # instead of continuing this one.
    if result.get("snapshot_available_after_run"):
        anchor = _read_active_session_anchor(service.client, service.user_id)
        if anchor is not None and str(anchor.get("refresh_window") or "") == str(run_session_id):
            _complete_session_anchor(service.client, anchor.get("id"), now_iso)
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
