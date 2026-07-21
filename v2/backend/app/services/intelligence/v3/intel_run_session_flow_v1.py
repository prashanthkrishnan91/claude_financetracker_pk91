"""Durable Run Intel session flow — one click, one SQL-backed session.

This module is the production request-side orchestration for
``POST /intel/v3/run``. Each explicit Run Intel click owns exactly one
``intel_run_sessions`` row (id minted by the browser per manual click);
every bounded automatic continuation of that click carries the SAME id.

Responsibilities per request:

  1. Load the session by its exact id; verify ownership. Never create a
     replacement session for an existing id, never infer a session from queue
     rows, windows, timestamps, or the latest snapshot.
  2. First use of an id: capture the immutable holdings scope + stale subset,
     capture the pre-session snapshot row id, create the session row, and
     enqueue exactly one session job per stale ticker.
  3. Continuations: reconcile interrupted work (credit tickers whose durable
     evidence already exists — never regenerate them), then drain a bounded
     batch of THIS session's jobs only.
  4. When every required session job has succeeded: run deterministic
     certification over the session's immutable scope and publish ONE
     snapshot explicitly linked to the session (scalar column + payload).
  5. Publication failures are retryable WITHOUT repeating any ticker analyst
     work: the session moves to ``publication_retryable_failed`` and the next
     continuation retries certification/publication only.
  6. Completion is reported ONLY from the session's own completed,
     session-linked snapshot — never from the globally-latest snapshot.

Hard boundaries:
  * Zero portfolio-synthesis calls anywhere on this path (the drain reaches
    the analyst-only orchestrator method via the production adapter).
  * No sentinel rows, no fake tickers, no daily-window identity.
  * This module never decides Buy/Hold/Trim/Sell.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID

from ....config import get_settings
from .analyst_refresh_job_store_v1 import (
    AnalystRefreshJob,
    JOB_CLAIMED,
    JOB_PENDING,
    STALE_CLAIM_TIMEOUT_SECONDS,
    enqueue_session_jobs,
    make_session_failed_jobs_due,
    mark_job_succeeded,
)
from .analyst_refresh_on_demand_drain_v1 import run_on_demand_drain
from .intel_run_session_store_v1 import (
    STATUS_COMPLETED,
    STATUS_CREATED,
    STATUS_FAILED,
    STATUS_PUBLICATION_RETRY,
    STATUS_PUBLISHING,
    STATUS_TICKER_REFRESH,
    count_session_job_states,
    create_session,
    get_session,
    update_session,
)

logger = logging.getLogger(__name__)

# ── Publication status vocabulary (response contract) ────────────────────────
PUBLICATION_NOT_STARTED = "not_started"
PUBLICATION_PENDING = "pending"
PUBLICATION_RETRYABLE_FAILED = "retryable_failed"
PUBLICATION_COMPLETED = "completed"

# ── next_required_action values ──────────────────────────────────────────────
# Continuation values reuse the existing "reclick_" prefix so the frontend's
# bounded auto-continuation classifies them as "partial" without new logic.
ACTION_CONTINUE = "reclick_run_intel_or_run_worker_entrypoint_to_continue_draining"
ACTION_COMPLETE = "none_certified_snapshot_current"
ACTION_ADD_POSITIONS = "add_positions_before_running_intel"
ACTION_RETRY_NEW_CLICK = "analyst_jobs_retry_budget_exhausted"
ACTION_SESSION_CREATE_FAILED = "run_session_create_failed_retry"
ACTION_QUEUE_ONLY = (
    "queue_only_enable_intel_v3_on_demand_refresh_enabled_or_run_"
    "analyst_refresh_worker_entrypoint_separately"
)
ACTION_WRITES_DISABLED = (
    "on_demand_drain_completed_but_intel_v3_snapshot_writes_enabled_is_false"
)

_OWNERSHIP_MISMATCH = "session_ownership_mismatch"


def _json_list(value: Any) -> list[str]:
    """Session JSONB columns come back as lists; be defensive about shape."""
    if isinstance(value, list):
        return [str(v) for v in value if v]
    return []


def _service() -> Any:
    from .intel_v3_service import IntelV3Service
    return IntelV3Service


class SessionOwnershipError(Exception):
    """The session id exists but belongs to another user."""


# ── Response builder ─────────────────────────────────────────────────────────

def _build_response(
    *,
    session_id: str,
    session_status: str,
    status: str,
    expected_ticker_count: int = 0,
    total_holding_count: int = 0,
    succeeded_total: int = 0,
    remaining: int = 0,
    attempted: int = 0,
    succeeded_now: int = 0,
    failed_now: int = 0,
    publication_status: str = PUBLICATION_NOT_STARTED,
    completed_snapshot_id: Optional[str] = None,
    retryable: bool = True,
    next_required_action: str = ACTION_CONTINUE,
    snapshot_available_after_run: bool = False,
    message: str = "",
    on_demand_enabled: Optional[bool] = None,
) -> dict[str, Any]:
    """Explicit session state the frontend can consume without inference.

    Legacy field names (``status`` / ``queued_ticker_count`` /
    ``on_demand_jobs_*`` / ``snapshot_available_after_run`` /
    ``next_required_action``) are kept for backward compatibility.
    """
    if on_demand_enabled is None:
        on_demand_enabled = bool(get_settings().intel_v3_on_demand_refresh_enabled)
    return {
        # ── Explicit durable-session state (authoritative) ──
        "run_session_id": session_id,
        "session_status": session_status,
        "expected_ticker_count": expected_ticker_count,
        "session_succeeded_ticker_count": succeeded_total,
        "session_remaining_ticker_count": remaining,
        "publication_status": publication_status,
        "completed_snapshot_id": completed_snapshot_id,
        "retryable": retryable,
        # ── Legacy-compatible fields ──
        "status": status,
        "queued_ticker_count": expected_ticker_count,
        "total_holding_count": total_holding_count,
        "on_demand_processing_enabled": on_demand_enabled,
        "on_demand_jobs_attempted": attempted,
        "on_demand_jobs_succeeded": succeeded_now,
        "on_demand_jobs_failed": failed_now,
        "snapshot_available_after_run": snapshot_available_after_run,
        "next_required_action": next_required_action,
        "message": message,
    }


# ── Entry point ──────────────────────────────────────────────────────────────

async def run_intel_session_request(
    *,
    user_id: "UUID | str",
    run_session_id: str,
    service: Any = None,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Handle one bounded HTTP request of a durable Run Intel session.

    Raises :class:`SessionOwnershipError` when the id belongs to another user
    (the router maps this to 403). All other failures return an explicit,
    retryable response dict — never a silent fallback to legacy behavior.
    """
    if service is None:
        service = _service()(user_id=UUID(str(user_id)))
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    client = service.client
    session_id = str(run_session_id)

    session = await asyncio.to_thread(get_session, client, session_id)
    if session is not None and str(session.get("user_id")) != str(user_id):
        logger.warning(
            "intel_run_session.ownership_mismatch session_id=%s requester=%s owner=%s",
            session_id, user_id, session.get("user_id"),
        )
        raise SessionOwnershipError(_OWNERSHIP_MISMATCH)

    if session is None:
        created = await _create_session(
            service=service, user_id=user_id, session_id=session_id, now=now,
        )
        if isinstance(created, dict) and created.get("__response__"):
            return created["response"]
        session = created

    return await _continue_session(
        service=service, session=session, session_id=session_id, now=now,
    )


# ── Session creation (first use of a click's id) ─────────────────────────────

async def _create_session(
    *,
    service: Any,
    user_id: "UUID | str",
    session_id: str,
    now: datetime,
) -> Any:
    client = service.client

    holdings = await service._get_active_tickers()
    if not holdings:
        return {
            "__response__": True,
            "response": _build_response(
                session_id=session_id,
                session_status="not_created",
                status="no_active_holdings",
                retryable=False,
                next_required_action=ACTION_ADD_POSITIONS,
                message=(
                    "No active holdings found. Add positions before running "
                    "Intel v3."
                ),
            ),
        }

    # Stale subset via the fast freshness gate; safe fallback = all holdings.
    stale: list[str] = list(holdings)
    try:
        from .intel_v3_fast_freshness_gate_v1 import run_fast_freshness_gate
        from .intel_v3_service import _stale_analyst_tickers_from_gate
        gate_result = await run_fast_freshness_gate(
            service.user_id,
            client,
            now=now,
            existing_certified_snapshot_id=None,
            has_pending_worker_jobs=False,
            total_holdings=len(holdings),
        )
        gate_stale = {
            str(t).upper() for t in _stale_analyst_tickers_from_gate(gate_result)
        }
        stale = [t for t in holdings if str(t).upper() in gate_stale]
    except Exception as gate_exc:
        logger.warning(
            "intel_run_session.freshness_gate_failed session_id=%s err=%s — "
            "falling back to full-holdings refresh",
            session_id, gate_exc,
        )
        stale = list(holdings)

    pre_session_snapshot_id = await _get_latest_snapshot_row_id(service)

    try:
        session = await asyncio.to_thread(
            lambda: create_session(
                client,
                session_id=session_id,
                user_id=user_id,
                holdings_scope=holdings,
                stale_tickers=stale,
                pre_session_snapshot_id=pre_session_snapshot_id,
                status=STATUS_CREATED,
                now=now,
            )
        )
    except Exception as exc:
        return {
            "__response__": True,
            "response": _build_response(
                session_id=session_id,
                session_status="not_created",
                status="enqueue_failed",
                total_holding_count=len(holdings),
                retryable=True,
                next_required_action=ACTION_SESSION_CREATE_FAILED,
                message=(
                    "Could not create the durable run session "
                    f"(apply migration 026_intel_run_sessions.sql?): {exc}"
                ),
            ),
        }

    # Duplicate first-request retry may have adopted an existing session row
    # (possibly already progressed) — continue from its real state.
    if str(session.get("status") or STATUS_CREATED) != STATUS_CREATED:
        return session

    # Fire-and-forget evidence lanes for the full portfolio — same explicit-run
    # behavior as before sessions existed. Never blocks or fails the click.
    _dispatch_evidence_lanes_safe(service, holdings)

    return session


async def _get_latest_snapshot_row_id(service: Any) -> Optional[str]:
    """SQL row id of the user's latest active snapshot (pre-session marker)."""
    try:
        res = await asyncio.to_thread(
            lambda: service.client.table("intel_v3_snapshots")
            .select("id")
            .eq("user_id", str(service.user_id))
            .eq("is_active", True)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        if isinstance(rows, list) and rows and isinstance(rows[0], dict):
            rid = rows[0].get("id")
            return str(rid) if rid else None
        return None
    except Exception as exc:
        logger.warning(
            "intel_run_session.pre_session_snapshot_lookup_failed user_id=%s err=%s",
            service.user_id, exc,
        )
        return None


def _dispatch_evidence_lanes_safe(service: Any, tickers: list[str]) -> None:
    """Schedule the existing explicit-run evidence-lane dispatch (best-effort)."""
    try:
        from .intel_v3_evidence_lane_orchestrator_v1 import (
            run_enabled_evidence_lanes_for_portfolio,
        )

        _user_id = str(service.user_id)
        _tickers = list(tickers)
        _client = service.client
        _settings = get_settings()
        _lane_run_id = str(uuid.uuid4())

        async def _run() -> None:
            try:
                holding_ctx = await service._get_active_holding_context_by_ticker()
                await asyncio.to_thread(
                    run_enabled_evidence_lanes_for_portfolio,
                    _user_id,
                    _tickers,
                    _client,
                    _lane_run_id,
                    _settings,
                    holding_ctx,
                )
            except Exception as exc:
                logger.warning(
                    "intel_run_session.evidence_lanes_dispatch_failed user_id=%s err=%s",
                    _user_id, exc,
                )

        asyncio.create_task(_run())
        logger.info(
            "intel_run_session.evidence_lanes_dispatch_scheduled user_id=%s "
            "total_tickers=%d lane_run_id=%s",
            _user_id, len(_tickers), _lane_run_id,
        )
    except Exception as exc:
        logger.warning(
            "intel_run_session.evidence_lanes_schedule_failed user_id=%s err=%s",
            service.user_id, exc,
        )


# ── Continuation ─────────────────────────────────────────────────────────────

async def _continue_session(
    *,
    service: Any,
    session: dict[str, Any],
    session_id: str,
    now: datetime,
) -> dict[str, Any]:
    client = service.client
    settings = get_settings()
    session_status = str(session.get("status") or "")
    holdings = _json_list(session.get("holdings_scope"))
    stale = _json_list(session.get("stale_tickers"))
    expected = int(session.get("expected_ticker_job_count") or 0)

    # Terminal states first.
    if session_status == STATUS_COMPLETED:
        return await _completed_response(
            service=service, session=session, session_id=session_id,
        )
    if session_status == STATUS_FAILED:
        return _build_response(
            session_id=session_id,
            session_status=STATUS_FAILED,
            status="failed",
            expected_ticker_count=expected,
            total_holding_count=len(holdings),
            retryable=False,
            next_required_action=ACTION_RETRY_NEW_CLICK,
            message=(
                str(session.get("last_error") or "")
                or "This run session failed. Click Run Intel to start a new one."
            ),
        )

    # 'created': (re-)enqueue idempotently, then transition.
    if session_status == STATUS_CREATED:
        if stale:
            enqueue = await asyncio.to_thread(
                lambda: enqueue_session_jobs(
                    client,
                    run_session_id=session_id,
                    user_id=str(session.get("user_id")),
                    tickers=stale,
                    now=now,
                )
            )
            if enqueue.error:
                return _build_response(
                    session_id=session_id,
                    session_status=STATUS_CREATED,
                    status="enqueue_failed",
                    expected_ticker_count=expected,
                    total_holding_count=len(holdings),
                    remaining=expected,
                    retryable=True,
                    next_required_action=ACTION_CONTINUE,
                    message=f"Failed to enqueue session jobs: {enqueue.error}",
                )
            session_status = STATUS_TICKER_REFRESH
        else:
            # No stale tickers: a new deterministic session-linked snapshot is
            # STILL required — the old snapshot is never this action's outcome.
            session_status = STATUS_PUBLISHING
        await asyncio.to_thread(
            lambda: update_session(
                client, session_id, status=session_status, now=now,
            )
        )
        session = dict(session, status=session_status)

    if session_status == STATUS_TICKER_REFRESH:
        return await _refresh_tickers_step(
            service=service, session=session, session_id=session_id, now=now,
            settings=settings,
        )

    # publishing / publication_retryable_failed → publication only.
    return await _publish_step(
        service=service, session=session, session_id=session_id, now=now,
        attempted=0, succeeded_now=0, failed_now=0,
    )


async def _refresh_tickers_step(
    *,
    service: Any,
    session: dict[str, Any],
    session_id: str,
    now: datetime,
    settings: Any,
) -> dict[str, Any]:
    client = service.client
    holdings = _json_list(session.get("holdings_scope"))
    stale = _json_list(session.get("stale_tickers"))
    expected = int(session.get("expected_ticker_job_count") or 0)
    user_id = str(session.get("user_id"))

    # 1. Resume reconciliation: credit interrupted-but-persisted tickers,
    #    recover abandoned claims, and lift worker backoff for this explicit
    #    action's own jobs.
    await _reconcile_claimed_session_jobs(
        client=client, session=session, session_id=session_id, now=now,
    )
    await asyncio.to_thread(
        lambda: make_session_failed_jobs_due(
            client, run_session_id=session_id, now=now,
        )
    )

    counts = await asyncio.to_thread(
        lambda: count_session_job_states(client, run_session_id=session_id)
    )

    # Top-up: a partially-failed earlier enqueue left missing rows.
    if counts["total"] < expected and stale:
        await asyncio.to_thread(
            lambda: enqueue_session_jobs(
                client,
                run_session_id=session_id,
                user_id=user_id,
                tickers=stale,
                now=now,
            )
        )
        counts = await asyncio.to_thread(
            lambda: count_session_job_states(client, run_session_id=session_id)
        )

    # 2. Terminal job failure → terminal session failure (honest, retryable
    #    only via a NEW click / new session).
    if counts["failed_terminal"] > 0:
        error = (
            f"{counts['failed_terminal']} session ticker job(s) exhausted "
            "their retry budget"
        )
        await asyncio.to_thread(
            lambda: update_session(
                client, session_id, status=STATUS_FAILED, last_error=error, now=now,
            )
        )
        return _build_response(
            session_id=session_id,
            session_status=STATUS_FAILED,
            status="failed",
            expected_ticker_count=expected,
            total_holding_count=len(holdings),
            succeeded_total=counts["succeeded"],
            remaining=max(0, expected - counts["succeeded"]),
            retryable=False,
            next_required_action=ACTION_RETRY_NEW_CLICK,
            message=error + ". Click Run Intel to start a new run.",
        )

    attempted = succeeded_now = failed_now = 0
    unfinished = counts["pending"] + counts["claimed"] + counts["failed_retryable"]

    if unfinished > 0:
        if not settings.intel_v3_on_demand_refresh_enabled:
            return _build_response(
                session_id=session_id,
                session_status=STATUS_TICKER_REFRESH,
                status="refresh_in_progress",
                expected_ticker_count=expected,
                total_holding_count=len(holdings),
                succeeded_total=counts["succeeded"],
                remaining=max(0, expected - counts["succeeded"]),
                retryable=True,
                next_required_action=ACTION_QUEUE_ONLY,
                message=(
                    "Session jobs are queued but on-demand processing is "
                    "disabled on the server."
                ),
            )

        # 3. Bounded drain of THIS session's jobs only.
        drain = await run_on_demand_drain(
            user_id=user_id,
            client=client,
            tickers=stale or holdings,
            run_session_id=session_id,
            trigger_prewarm=False,
        )
        attempted = drain.jobs_attempted
        succeeded_now = drain.jobs_succeeded
        failed_now = drain.jobs_failed

        counts = await asyncio.to_thread(
            lambda: count_session_job_states(client, run_session_id=session_id)
        )
        if counts["failed_terminal"] > 0:
            error = (
                f"{counts['failed_terminal']} session ticker job(s) exhausted "
                "their retry budget"
            )
            await asyncio.to_thread(
                lambda: update_session(
                    client, session_id, status=STATUS_FAILED, last_error=error,
                    now=now,
                )
            )
            return _build_response(
                session_id=session_id,
                session_status=STATUS_FAILED,
                status="failed",
                expected_ticker_count=expected,
                total_holding_count=len(holdings),
                succeeded_total=counts["succeeded"],
                remaining=max(0, expected - counts["succeeded"]),
                attempted=attempted,
                succeeded_now=succeeded_now,
                failed_now=failed_now,
                retryable=False,
                next_required_action=ACTION_RETRY_NEW_CLICK,
                message=error + ". Click Run Intel to start a new run.",
            )

    still_unfinished = (
        counts["pending"] + counts["claimed"] + counts["failed_retryable"]
    )
    all_done = (
        counts["succeeded"] >= expected
        and counts["total"] >= expected
        and still_unfinished == 0
    )

    if not all_done:
        return _build_response(
            session_id=session_id,
            session_status=STATUS_TICKER_REFRESH,
            status="refresh_in_progress",
            expected_ticker_count=expected,
            total_holding_count=len(holdings),
            succeeded_total=counts["succeeded"],
            remaining=max(0, expected - counts["succeeded"]),
            attempted=attempted,
            succeeded_now=succeeded_now,
            failed_now=failed_now,
            retryable=True,
            next_required_action=ACTION_CONTINUE,
            message=(
                f"Refreshed {counts['succeeded']} of {expected} holdings so "
                "far — continuing."
            ),
        )

    # 4. Every required ticker job succeeded → publication.
    await asyncio.to_thread(
        lambda: update_session(
            client, session_id, status=STATUS_PUBLISHING, now=now,
        )
    )
    session = dict(session, status=STATUS_PUBLISHING)
    return await _publish_step(
        service=service, session=session, session_id=session_id, now=now,
        attempted=attempted, succeeded_now=succeeded_now, failed_now=failed_now,
    )


async def _reconcile_claimed_session_jobs(
    *,
    client: Any,
    session: dict[str, Any],
    session_id: str,
    now: datetime,
) -> None:
    """Credit interrupted-but-persisted tickers; recover abandoned claims.

    An HTTP request can die AFTER a ticker's durable evidence was written but
    BEFORE its session job was marked succeeded. On resume:

      * a claimed session job whose ticker has BOTH a fresh ``agent_insights``
        row AND a matching fresh ``recommendations`` row (written since the
        session began, same agent run) is marked succeeded — verified evidence
        is never regenerated;
      * a claimed session job with NO such evidence whose claim is older than
        the stale-claim timeout is reset to pending (the claiming request is
        dead);
      * an in-flight claim (younger than the timeout, no evidence yet) is
        left alone — never stolen.

    Best-effort: any DB failure leaves the jobs as they are (the stale-claim
    timeout still recovers them later).
    """
    try:
        res = await asyncio.to_thread(
            lambda: client.table("analyst_refresh_jobs")
            .select("*")
            .eq("run_session_id", session_id)
            .eq("status", JOB_CLAIMED)
            .execute()
        )
        claimed_rows = getattr(res, "data", None) or []
        if not isinstance(claimed_rows, list) or not claimed_rows:
            return
    except Exception as exc:
        logger.warning(
            "intel_run_session.reconcile_read_failed session_id=%s err=%s",
            session_id, exc,
        )
        return

    session_started = str(session.get("created_at") or "")
    user_id = str(session.get("user_id"))
    tickers = [
        str(r.get("ticker") or "").upper()
        for r in claimed_rows
        if r.get("ticker")
    ]

    evidence_tickers: set[str] = set()
    try:
        insights_res = await asyncio.to_thread(
            lambda: client.table("agent_insights")
            .select("ticker,run_id,created_at")
            .eq("user_id", user_id)
            .gte("created_at", session_started)
            .execute()
        )
        insight_by_ticker: dict[str, str] = {}
        for r in getattr(insights_res, "data", None) or []:
            tk = str(r.get("ticker") or "").upper()
            if tk in tickers:
                insight_by_ticker[tk] = str(r.get("run_id") or "")
        if insight_by_ticker:
            recs_res = await asyncio.to_thread(
                lambda: client.table("recommendations")
                .select("ticker,agent_run_id,created_at")
                .eq("user_id", user_id)
                .gte("created_at", session_started)
                .execute()
            )
            rec_by_ticker: dict[str, str] = {}
            for r in getattr(recs_res, "data", None) or []:
                tk = str(r.get("ticker") or "").upper()
                if tk in tickers:
                    rec_by_ticker[tk] = str(r.get("agent_run_id") or "")
            for tk, insight_run in insight_by_ticker.items():
                rec_run = rec_by_ticker.get(tk)
                if rec_run is None:
                    continue
                if insight_run and rec_run and insight_run != rec_run:
                    continue
                evidence_tickers.add(tk)
    except Exception as exc:
        logger.warning(
            "intel_run_session.reconcile_evidence_read_failed session_id=%s err=%s",
            session_id, exc,
        )
        evidence_tickers = set()

    stale_claim_cutoff = now - timedelta(seconds=STALE_CLAIM_TIMEOUT_SECONDS)
    credited = 0
    recovered = 0
    for row in claimed_rows:
        ticker = str(row.get("ticker") or "").upper()
        job = AnalystRefreshJob.from_row(row)
        if ticker in evidence_tickers:
            ok = await asyncio.to_thread(
                lambda j=job: mark_job_succeeded(client, j, now=now)
            )
            if ok:
                credited += 1
            continue
        claimed_at = row.get("claimed_at")
        if _iso_before(claimed_at, stale_claim_cutoff):
            try:
                await asyncio.to_thread(
                    lambda jid=str(row.get("id")): client.table(
                        "analyst_refresh_jobs"
                    )
                    .update({
                        "status": JOB_PENDING,
                        "next_retry_at": now.isoformat(),
                        "updated_at": now.isoformat(),
                    })
                    .eq("id", jid)
                    .eq("status", JOB_CLAIMED)
                    .execute()
                )
                recovered += 1
            except Exception as exc:
                logger.debug(
                    "intel_run_session.reconcile_recover_failed job_id=%s err=%s",
                    row.get("id"), exc,
                )
    if credited or recovered:
        logger.info(
            "intel_run_session.reconciled session_id=%s credited_from_evidence=%d "
            "recovered_stale_claims=%d",
            session_id, credited, recovered,
        )


def _iso_before(iso_value: Any, cutoff: datetime) -> bool:
    if not isinstance(iso_value, str) or not iso_value:
        return False
    try:
        dt = datetime.fromisoformat(iso_value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt <= cutoff


# ── Publication ──────────────────────────────────────────────────────────────

async def _publish_step(
    *,
    service: Any,
    session: dict[str, Any],
    session_id: str,
    now: datetime,
    attempted: int,
    succeeded_now: int,
    failed_now: int,
) -> dict[str, Any]:
    """Deterministically certify + publish THIS session's snapshot.

    Zero ticker analyst calls, zero synthesis calls — reads persisted evidence
    only. Idempotent under the session id: if a session-linked snapshot row
    already exists (an earlier attempt inserted it but the session update
    failed), it is adopted and the session completes without inserting a
    duplicate.
    """
    client = service.client
    settings = get_settings()
    holdings = _json_list(session.get("holdings_scope"))
    expected = int(session.get("expected_ticker_job_count") or 0)
    counts = await asyncio.to_thread(
        lambda: count_session_job_states(client, run_session_id=session_id)
    )

    def _pub_response(
        *,
        session_status: str,
        status: str,
        publication_status: str,
        completed_snapshot_id: Optional[str] = None,
        retryable: bool = True,
        action: str = ACTION_CONTINUE,
        available: bool = False,
        message: str = "",
    ) -> dict[str, Any]:
        return _build_response(
            session_id=session_id,
            session_status=session_status,
            status=status,
            expected_ticker_count=expected,
            total_holding_count=len(holdings),
            succeeded_total=counts["succeeded"],
            remaining=max(0, expected - counts["succeeded"]),
            attempted=attempted,
            succeeded_now=succeeded_now,
            failed_now=failed_now,
            publication_status=publication_status,
            completed_snapshot_id=completed_snapshot_id,
            retryable=retryable,
            next_required_action=action,
            snapshot_available_after_run=available,
            message=message,
        )

    # Crash-recovery idempotency: adopt an existing session-linked snapshot.
    existing_row_id = await _find_session_snapshot_row_id(client, session_id)
    if existing_row_id is not None:
        await asyncio.to_thread(
            lambda: update_session(
                client, session_id,
                status=STATUS_COMPLETED,
                completed_snapshot_id=existing_row_id,
                last_error=None,
                completed=True,
                now=now,
            )
        )
        session = dict(
            session,
            status=STATUS_COMPLETED,
            completed_snapshot_id=existing_row_id,
        )
        return await _completed_response(
            service=service, session=session, session_id=session_id,
            attempted=attempted, succeeded_now=succeeded_now,
            failed_now=failed_now,
        )

    if not settings.intel_v3_snapshot_writes_enabled:
        error = "snapshot_writes_disabled"
        await asyncio.to_thread(
            lambda: update_session(
                client, session_id,
                status=STATUS_PUBLICATION_RETRY,
                last_error=error,
                now=now,
            )
        )
        return _pub_response(
            session_status=STATUS_PUBLICATION_RETRY,
            status="refresh_in_progress",
            publication_status=PUBLICATION_RETRYABLE_FAILED,
            retryable=True,
            action=ACTION_WRITES_DISABLED,
            message=(
                "Ticker analysis is complete, but snapshot writes are "
                "disabled (INTEL_V3_SNAPSHOT_WRITES_ENABLED=false)."
            ),
        )

    await asyncio.to_thread(
        lambda: update_session(
            client, session_id,
            status=STATUS_PUBLISHING,
            increment_publication_attempts=True,
            now=now,
        )
    )

    try:
        payload = await service.run_prewarm_snapshot(
            prewarm_run_id=str(uuid.uuid4()),
            skip_persist_on_fail=True,
            run_session_id=session_id,
            scope_tickers=holdings,
        )
    except Exception as exc:
        error = f"publication_failed:{type(exc).__name__}:{exc}"[:400]
        await asyncio.to_thread(
            lambda: update_session(
                client, session_id,
                status=STATUS_PUBLICATION_RETRY,
                last_error=error,
                now=now,
            )
        )
        return _pub_response(
            session_status=STATUS_PUBLICATION_RETRY,
            status="refresh_in_progress",
            publication_status=PUBLICATION_RETRYABLE_FAILED,
            retryable=True,
            action=ACTION_CONTINUE,
            message=(
                "Ticker analysis is complete but snapshot publication failed; "
                "it will be retried without re-running any analysis."
            ),
        )

    if payload.get("snapshot_source") != "worker_certified":
        cert_summary = payload.get("certification_summary") or {}
        failed_tickers = cert_summary.get("failed_holding_count", "?")
        error = f"certification_failed:failed_holdings={failed_tickers}"
        await asyncio.to_thread(
            lambda: update_session(
                client, session_id,
                status=STATUS_PUBLICATION_RETRY,
                last_error=error,
                now=now,
            )
        )
        return _pub_response(
            session_status=STATUS_PUBLICATION_RETRY,
            status="refresh_in_progress",
            publication_status=PUBLICATION_RETRYABLE_FAILED,
            retryable=True,
            action=ACTION_CONTINUE,
            message=(
                "Deterministic certification did not pass for every holding "
                "in this session's scope; publication will be retried."
            ),
        )

    snapshot_row_id = payload.get("snapshot_row_id")
    if not snapshot_row_id:
        # Persist reported success but returned no row id (or writes were
        # skipped mid-flight) — re-check for the session-linked row before
        # declaring failure.
        snapshot_row_id = await _find_session_snapshot_row_id(client, session_id)
    if not snapshot_row_id:
        error = "publication_no_snapshot_row_id"
        await asyncio.to_thread(
            lambda: update_session(
                client, session_id,
                status=STATUS_PUBLICATION_RETRY,
                last_error=error,
                now=now,
            )
        )
        return _pub_response(
            session_status=STATUS_PUBLICATION_RETRY,
            status="refresh_in_progress",
            publication_status=PUBLICATION_RETRYABLE_FAILED,
            retryable=True,
            action=ACTION_CONTINUE,
            message="Snapshot publication did not persist a row; retrying.",
        )

    await asyncio.to_thread(
        lambda: update_session(
            client, session_id,
            status=STATUS_COMPLETED,
            completed_snapshot_id=str(snapshot_row_id),
            last_error=None,
            completed=True,
            now=now,
        )
    )
    session = dict(
        session,
        status=STATUS_COMPLETED,
        completed_snapshot_id=str(snapshot_row_id),
    )
    return await _completed_response(
        service=service, session=session, session_id=session_id,
        attempted=attempted, succeeded_now=succeeded_now, failed_now=failed_now,
    )


async def _find_session_snapshot_row_id(
    client: Any, session_id: str,
) -> Optional[str]:
    try:
        res = await asyncio.to_thread(
            lambda: client.table("intel_v3_snapshots")
            .select("id")
            .eq("run_session_id", session_id)
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        if isinstance(rows, list) and rows and isinstance(rows[0], dict):
            rid = rows[0].get("id")
            return str(rid) if rid else None
        return None
    except Exception as exc:
        logger.warning(
            "intel_run_session.find_session_snapshot_failed session_id=%s err=%s",
            session_id, exc,
        )
        return None


# ── Completion truth ─────────────────────────────────────────────────────────

async def _completed_response(
    *,
    service: Any,
    session: dict[str, Any],
    session_id: str,
    attempted: int = 0,
    succeeded_now: int = 0,
    failed_now: int = 0,
) -> dict[str, Any]:
    """Report completion ONLY after re-verifying the full completion truth.

    Verified from the durable rows, never from the globally-latest snapshot:
      * session status is completed and a completed_snapshot_id is set;
      * that snapshot row's ``run_session_id`` column == this session;
      * its payload's ``run_session_id`` == this session;
      * its row id differs from the pre-session snapshot;
      * ``snapshot_source == "worker_certified"``;
      * evidence freshness is ``certified_current``;
      * no required session job is pending/claimed/failed.
    """
    client = service.client
    holdings = _json_list(session.get("holdings_scope"))
    expected = int(session.get("expected_ticker_job_count") or 0)
    completed_snapshot_id = session.get("completed_snapshot_id")
    counts = await asyncio.to_thread(
        lambda: count_session_job_states(client, run_session_id=session_id)
    )

    problems: list[str] = []
    if not completed_snapshot_id:
        problems.append("no_completed_snapshot_id")
    unfinished = (
        counts["pending"] + counts["claimed"] + counts["failed_retryable"]
        + counts["failed_terminal"]
    )
    if unfinished > 0:
        problems.append(f"unfinished_session_jobs:{unfinished}")

    snapshot_row: Optional[dict[str, Any]] = None
    if completed_snapshot_id:
        try:
            res = await asyncio.to_thread(
                lambda: client.table("intel_v3_snapshots")
                .select("id,run_session_id,payload")
                .eq("id", str(completed_snapshot_id))
                .limit(1)
                .execute()
            )
            rows = getattr(res, "data", None) or []
            snapshot_row = rows[0] if isinstance(rows, list) and rows else None
        except Exception as exc:
            problems.append(f"snapshot_read_failed:{type(exc).__name__}")

    payload: dict[str, Any] = {}
    if completed_snapshot_id and snapshot_row is None and not any(
        p.startswith("snapshot_read_failed") for p in problems
    ):
        problems.append("completed_snapshot_row_missing")
    if snapshot_row is not None:
        payload = snapshot_row.get("payload") or {}
        if str(snapshot_row.get("run_session_id") or "") != session_id:
            problems.append("snapshot_column_session_mismatch")
        if str(payload.get("run_session_id") or "") != session_id:
            problems.append("snapshot_payload_session_mismatch")
        pre_id = session.get("pre_session_snapshot_id")
        if pre_id and str(snapshot_row.get("id")) == str(pre_id):
            problems.append("snapshot_is_pre_session_snapshot")
        if payload.get("snapshot_source") != "worker_certified":
            problems.append(
                f"snapshot_source={payload.get('snapshot_source')}"
            )
        try:
            from .watchtower_intel_republisher_v1 import (
                PUBLISH_CERTIFIED_CURRENT,
                get_evidence_freshness_state,
            )
            freshness = await get_evidence_freshness_state(
                service.user_id,
                client,
                intel_snapshot_generated_at=payload.get("generated_at"),
            )
            if freshness != PUBLISH_CERTIFIED_CURRENT:
                problems.append(f"evidence_freshness_state={freshness}")
        except Exception as exc:
            logger.warning(
                "intel_run_session.freshness_check_failed session_id=%s err=%s",
                session_id, exc,
            )

    if problems:
        logger.warning(
            "intel_run_session.completion_verification_failed session_id=%s "
            "problems=%s",
            session_id, ",".join(problems),
        )
        return _build_response(
            session_id=session_id,
            session_status=str(session.get("status") or STATUS_COMPLETED),
            status="refresh_in_progress",
            expected_ticker_count=expected,
            total_holding_count=len(holdings),
            succeeded_total=counts["succeeded"],
            remaining=max(0, expected - counts["succeeded"]),
            attempted=attempted,
            succeeded_now=succeeded_now,
            failed_now=failed_now,
            publication_status=PUBLICATION_PENDING,
            completed_snapshot_id=(
                str(completed_snapshot_id) if completed_snapshot_id else None
            ),
            retryable=True,
            next_required_action=ACTION_CONTINUE,
            snapshot_available_after_run=False,
            message=(
                "Completion could not be verified against the session's own "
                f"snapshot ({'; '.join(problems)})."
            ),
        )

    logger.info(
        "intel_run_session.completed session_id=%s completed_snapshot_id=%s "
        "expected_ticker_count=%d succeeded=%d",
        session_id, completed_snapshot_id, expected, counts["succeeded"],
    )
    return _build_response(
        session_id=session_id,
        session_status=STATUS_COMPLETED,
        status="completed",
        expected_ticker_count=expected,
        total_holding_count=len(holdings),
        succeeded_total=counts["succeeded"],
        remaining=0,
        attempted=attempted,
        succeeded_now=succeeded_now,
        failed_now=failed_now,
        publication_status=PUBLICATION_COMPLETED,
        completed_snapshot_id=str(completed_snapshot_id),
        retryable=False,
        next_required_action=ACTION_COMPLETE,
        snapshot_available_after_run=True,
        message="This run's certified snapshot is published and current.",
    )
