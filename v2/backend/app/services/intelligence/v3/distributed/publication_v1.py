"""Distributed Run Intel — portfolio join and SESSION-NATIVE publication.

Runs once per session when every frozen ticker is terminal
(decided / no_call / failed). The snapshot is built exclusively from this
session's durable rows (``session_publication_v1``):

  * decided cards come verbatim from the deterministic decisions persisted on
    ``intel_run_tickers`` — publication NEVER runs ``decide()`` and NEVER
    reads global active recommendations for actions;
  * NO CALL / failed tickers are explicit coverage gaps — an older session's
    action can never surface for them;
  * the distributed certification contract proves the full frozen scope is
    accounted for before anything persists;
  * exactly ONE session-linked snapshot row exists (unique index + adopt).

Failure isolation: this task retries publication ONLY — zero collector
re-runs, zero specialist LLM calls, zero policy calls. Exhausting its retry
budget is one of the few honest terminal session failures.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from .....config import get_settings
from . import run_task_store_v1 as store
from .session_publication_v1 import (
    SessionPublicationError,
    build_session_snapshot_payload,
    certify_session_snapshot,
    persist_session_snapshot,
)
from .task_contracts_v1 import (
    SESSION_COMPLETED,
    SESSION_COMPLETED_WITH_GAPS,
    SESSION_RUNNING,
    STAGE_DONE,
    TICKER_DECIDED,
    TICKER_FAILED,
    TICKER_NO_CALL,
)
from .run_task_store_v1 import TASK_FAILED_RETRYABLE

logger = logging.getLogger(__name__)


class PublicationOutcome:
    def __init__(self):
        self.final_state: str = TASK_FAILED_RETRYABLE
        self.session_status: Optional[str] = None
        self.snapshot_row_id: Optional[str] = None
        self.error: Optional[str] = None
        self.gaps: dict[str, Any] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def execute_publication_task(
    client: Any,
    *,
    task: dict[str, Any],
    settings: Any = None,
    build_payload: Optional[Callable[..., dict[str, Any]]] = None,
    persist: Optional[Callable[..., Optional[str]]] = None,
    now: Optional[datetime] = None,
) -> PublicationOutcome:
    """Certify + publish the session-native snapshot; mark the session
    terminal.

    ``build_payload`` / ``persist`` are narrow error-injection seams for
    tests; the real session-native builder and persistence run by default —
    the primary semantic acceptance tests exercise the real path.
    """
    import asyncio

    now = now or _now()
    settings = settings or get_settings()
    build_payload = build_payload or build_session_snapshot_payload
    persist = persist or persist_session_snapshot
    outcome = PublicationOutcome()
    session_id = str(task.get("run_session_id") or "")
    user_id = str(task.get("user_id") or "")

    def _read_state():
        from ..intel_run_session_store_v1 import get_session

        session = get_session(client, session_id)
        rows = store.list_ticker_rows(client, run_session_id=session_id)
        outputs = store.list_specialist_outputs(
            client, run_session_id=session_id
        )
        return session, rows, outputs

    session, ticker_rows, specialist_outputs = await asyncio.to_thread(
        _read_state
    )
    if session is None:
        outcome.error = "session_row_missing"
        return outcome

    decided = [
        str(r.get("ticker")) for r in ticker_rows
        if str(r.get("state")) == TICKER_DECIDED
    ]
    no_call = [
        str(r.get("ticker")) for r in ticker_rows
        if str(r.get("state")) == TICKER_NO_CALL
    ]
    failed = [
        str(r.get("ticker")) for r in ticker_rows
        if str(r.get("state")) == TICKER_FAILED
    ]
    outcome.gaps = {
        "decided_count": len(decided),
        "no_call_tickers": sorted(no_call),
        "failed_tickers": sorted(failed),
    }

    if not decided:
        # Deterministic join produced no decision at all — honest terminal
        # failure (one of the reserved failure conditions).
        outcome.final_state = "failed"
        outcome.error = "no_decided_tickers"
        await _mark_session(
            client, session_id,
            status="failed",
            last_error="no_decided_tickers_publication_impossible",
            metrics_patch={"publication": outcome.gaps},
            now=now,
        )
        outcome.session_status = "failed"
        return outcome

    # Idempotency / crash recovery: adopt an existing session-linked snapshot
    # (an earlier attempt inserted it but died before the session update).
    existing = await asyncio.to_thread(
        lambda: _find_existing_session_snapshot(client, session_id)
    )
    has_gaps = bool(no_call or failed)
    target_status = (
        SESSION_COMPLETED_WITH_GAPS if has_gaps else SESSION_COMPLETED
    )
    if existing is not None:
        await _mark_session(
            client, session_id,
            status=target_status,
            completed_snapshot_id=existing,
            metrics_patch={"publication": {**outcome.gaps, "adopted": True}},
            now=now,
        )
        outcome.final_state = "succeeded"
        outcome.session_status = target_status
        outcome.snapshot_row_id = existing
        return outcome

    # ── Session-native build + certification (zero policy, zero global reads).
    try:
        payload = await asyncio.to_thread(
            lambda: build_payload(
                session=session,
                ticker_rows=ticker_rows,
                specialist_outputs=specialist_outputs,
                now=now,
            )
        )
    except SessionPublicationError as exc:
        outcome.error = f"session_build_failed:{exc}"[:400]
        outcome.final_state = TASK_FAILED_RETRYABLE
        return outcome
    except Exception as exc:
        outcome.error = f"publication_failed:{type(exc).__name__}:{exc}"[:400]
        outcome.final_state = TASK_FAILED_RETRYABLE
        return outcome

    certification = certify_session_snapshot(
        payload=payload, session=session, ticker_rows=ticker_rows,
    )
    if not certification.certified:
        outcome.error = (
            "session_certification_failed:"
            + ";".join(certification.errors[:8])
        )[:400]
        outcome.final_state = TASK_FAILED_RETRYABLE
        return outcome

    # Claim fence: only the current claim may persist and terminalize.
    owns = await asyncio.to_thread(lambda: store.owns_claim(client, task))
    if not owns:
        outcome.error = "claim_lost"
        outcome.final_state = TASK_FAILED_RETRYABLE
        return outcome

    if not getattr(settings, "intel_v3_snapshot_writes_enabled", False):
        outcome.error = "snapshot_writes_disabled"
        outcome.final_state = TASK_FAILED_RETRYABLE
        return outcome

    try:
        snapshot_row_id = await asyncio.to_thread(
            lambda: persist(
                client,
                settings=settings,
                user_id=user_id,
                session_id=session_id,
                payload=payload,
            )
        )
    except Exception as exc:
        outcome.error = f"persist_failed:{type(exc).__name__}:{exc}"[:400]
        outcome.final_state = TASK_FAILED_RETRYABLE
        return outcome
    if not snapshot_row_id:
        outcome.error = "publication_no_snapshot_row_id"
        outcome.final_state = TASK_FAILED_RETRYABLE
        return outcome

    marked = await _mark_session(
        client, session_id,
        status=target_status,
        completed_snapshot_id=str(snapshot_row_id),
        metrics_patch={"publication": outcome.gaps},
        now=now,
    )
    if not marked:
        # Snapshot row exists; next retry adopts it idempotently with zero
        # collector/specialist/policy work.
        outcome.error = "session_update_failed"
        outcome.final_state = TASK_FAILED_RETRYABLE
        return outcome

    outcome.final_state = "succeeded"
    outcome.session_status = target_status
    outcome.snapshot_row_id = str(snapshot_row_id)
    logger.info(
        "distributed_publication.completed session=%s status=%s snapshot=%s "
        "decided=%d no_call=%d failed=%d source=%s",
        session_id, target_status, snapshot_row_id,
        len(decided), len(no_call), len(failed),
        payload.get("snapshot_source"),
    )
    return outcome


def _find_existing_session_snapshot(
    client: Any, session_id: str
) -> Optional[str]:
    try:
        res = (
            client.table("intel_v3_snapshots")
            .select("id")
            .eq("run_session_id", session_id)
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        if rows and isinstance(rows[0], dict) and rows[0].get("id"):
            return str(rows[0]["id"])
        return None
    except Exception:
        return None


async def _mark_session(
    client: Any,
    session_id: str,
    *,
    status: str,
    completed_snapshot_id: Optional[str] = None,
    last_error: Optional[str] = None,
    metrics_patch: Optional[dict[str, Any]] = None,
    now: Optional[datetime] = None,
) -> bool:
    import asyncio

    now = now or _now()

    def _update() -> bool:
        try:
            patch: dict[str, Any] = {
                "status": status,
                "current_stage": STAGE_DONE,
                "updated_at": now.isoformat(),
            }
            if completed_snapshot_id is not None:
                patch["completed_snapshot_id"] = completed_snapshot_id
                patch["completed_at"] = now.isoformat()
            if last_error is not None:
                patch["last_error"] = last_error[:500]
            if metrics_patch:
                try:
                    res = (
                        client.table("intel_run_sessions")
                        .select("metrics")
                        .eq("id", session_id)
                        .limit(1)
                        .execute()
                    )
                    rows = getattr(res, "data", None) or []
                    metrics = (
                        rows[0].get("metrics") if rows and isinstance(rows[0], dict)
                        else {}
                    ) or {}
                except Exception:
                    metrics = {}
                patch["metrics"] = {**metrics, **metrics_patch}
            # Terminalization fence: only an ACTIVE session can be completed —
            # a stale worker can never re-terminalize or flip a terminal state.
            res = (
                client.table("intel_run_sessions")
                .update(patch)
                .eq("id", session_id)
                .in_("status", [SESSION_RUNNING, "created"])
                .execute()
            )
            return bool(getattr(res, "data", None))
        except Exception as exc:
            logger.warning(
                "distributed_publication.session_update_failed session=%s err=%s",
                session_id, exc,
            )
            return False

    return await asyncio.to_thread(_update)
