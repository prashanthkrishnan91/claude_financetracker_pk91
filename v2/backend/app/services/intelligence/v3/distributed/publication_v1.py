"""Distributed Run Intel — portfolio join, certification and publication.

Runs once per session when every ticker is terminal
(decided / no_call / failed). Reuses the existing zero-LLM deterministic
certification + publication path (``IntelV3Service.run_prewarm_snapshot`` →
``check_certified_intel_run_contract`` → ``_persist_snapshot``), publishing
ONE snapshot explicitly linked to the session (unique index enforced).

Failure isolation: this task retries publication ONLY — it never re-runs
collectors or specialists (it reads persisted evidence exclusively). Exhausting
its retry budget is one of the few honest terminal session failures.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from . import run_task_store_v1 as store
from .task_contracts_v1 import (
    SESSION_COMPLETED,
    SESSION_COMPLETED_WITH_GAPS,
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


async def _find_existing_session_snapshot(
    client: Any, session_id: str
) -> Optional[str]:
    import asyncio

    def _read() -> Optional[str]:
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

    return await asyncio.to_thread(_read)


async def execute_publication_task(
    client: Any,
    *,
    task: dict[str, Any],
    service: Any = None,
    now: Optional[datetime] = None,
) -> PublicationOutcome:
    """Certify + publish the session snapshot; mark the session terminal.

    ``service`` injection point exists for tests; production builds the real
    ``IntelV3Service`` (zero-LLM prewarm path).
    """
    import asyncio

    now = now or _now()
    outcome = PublicationOutcome()
    session_id = str(task.get("run_session_id") or "")
    user_id = str(task.get("user_id") or "")

    ticker_rows = await asyncio.to_thread(
        lambda: store.list_ticker_rows(client, run_session_id=session_id)
    )
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
    degraded_lanes = {
        str(r.get("ticker")): list(r.get("degraded_lanes") or [])
        for r in ticker_rows if r.get("degraded_lanes")
    }
    outcome.gaps = {
        "decided_count": len(decided),
        "no_call_tickers": sorted(no_call),
        "failed_tickers": sorted(failed),
        "degraded_lane_tickers": sorted(degraded_lanes.keys()),
    }

    if not decided:
        # Deterministic policy could produce no decision at all — honest
        # terminal failure (one of the reserved failure conditions).
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

    has_gaps = bool(no_call or failed or degraded_lanes)
    target_status = (
        SESSION_COMPLETED_WITH_GAPS if has_gaps else SESSION_COMPLETED
    )

    # Idempotency / crash recovery: adopt an existing session-linked snapshot
    # (an earlier attempt inserted it but died before the session update).
    existing = await _find_existing_session_snapshot(client, session_id)
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

    if service is None:
        from ..intel_v3_service import IntelV3Service
        from uuid import UUID as _UUID

        service = IntelV3Service(user_id=_UUID(user_id))

    try:
        payload = await service.run_prewarm_snapshot(
            prewarm_run_id=str(uuid.uuid4()),
            skip_persist_on_fail=True,
            run_session_id=session_id,
            scope_tickers=decided,
        )
    except Exception as exc:
        outcome.error = f"publication_failed:{type(exc).__name__}:{exc}"[:400]
        outcome.final_state = TASK_FAILED_RETRYABLE
        return outcome

    if payload.get("snapshot_source") != "worker_certified":
        summary = payload.get("certification_summary") or {}
        outcome.error = (
            "certification_failed:failed_holdings="
            f"{summary.get('failed_holding_count', '?')}"
        )
        outcome.final_state = TASK_FAILED_RETRYABLE
        return outcome

    snapshot_row_id = payload.get("snapshot_row_id")
    if not snapshot_row_id:
        snapshot_row_id = await _find_existing_session_snapshot(client, session_id)
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
        # collector/specialist work.
        outcome.error = "session_update_failed"
        outcome.final_state = TASK_FAILED_RETRYABLE
        return outcome

    outcome.final_state = "succeeded"
    outcome.session_status = target_status
    outcome.snapshot_row_id = str(snapshot_row_id)
    logger.info(
        "distributed_publication.completed session=%s status=%s snapshot=%s "
        "decided=%d no_call=%d failed=%d",
        session_id, target_status, snapshot_row_id,
        len(decided), len(no_call), len(failed),
    )
    return outcome


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
            res = (
                client.table("intel_run_sessions")
                .update(patch)
                .eq("id", session_id)
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
