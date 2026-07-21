"""Distributed Run Intel — SQL access layer for tickers + tasks.

Thin store over ``intel_run_tickers`` / ``intel_run_tasks`` /
``intel_run_specialist_outputs`` (migration 027). Follows the repo store
conventions (analyst_refresh_job_store_v1): reads never raise into callers,
writes raise only where the caller must surface an explicit retryable error.

Claiming: production preference is the migration-027 RPC
``claim_intel_run_tasks`` (FOR UPDATE SKIP LOCKED). When the RPC is
unavailable (pre-migration environment, in-memory test fakes) the store falls
back to the repository-consistent guarded-UPDATE compare-and-swap — a SELECT
of due candidates followed by a per-row conditional UPDATE keyed on the
previous state, where an empty update result means another worker won the
race. Both paths yield the same lease semantics.
"""
from __future__ import annotations

import logging
import socket
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from .task_contracts_v1 import (
    TASK_BLOCKED,
    TASK_CLAIMED,
    TASK_PENDING,
    TASK_TERMINAL_STATES,
    TICKER_PENDING,
)

logger = logging.getLogger(__name__)

TICKERS_TABLE = "intel_run_tickers"
TASKS_TABLE = "intel_run_tasks"
SPECIALIST_TABLE = "intel_run_specialist_outputs"

DEFAULT_LEASE_SECONDS = 300
DEFAULT_MAX_ATTEMPTS = 3
# Retry backoff for failed-but-retryable tasks: 30s * 2^(attempts-1).
_RETRY_BASE_SECONDS = 30


def _rows(res: Any) -> list[dict[str, Any]]:
    data = getattr(res, "data", None)
    return data if isinstance(data, list) else []


def _now(now: Optional[datetime] = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _parse_iso(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def default_worker_id() -> str:
    """Stable-ish identity for this process (lease diagnostics only)."""
    return f"{socket.gethostname()}:{os.getpid()}"


def compute_task_retry_at(attempts: int, now: Optional[datetime] = None) -> str:
    now = _now(now)
    delay = _RETRY_BASE_SECONDS * (2 ** max(0, attempts - 1))
    return _iso(now + timedelta(seconds=min(delay, 3600)))


# ── Ticker rows ──────────────────────────────────────────────────────────────

def insert_ticker_rows(
    client: Any,
    *,
    run_session_id: str,
    user_id: str,
    rows: list[dict[str, Any]],
    now: Optional[datetime] = None,
) -> int:
    """Insert frozen scope rows. Idempotent per (session, ticker): unique
    violations from a concurrent/retried create are absorbed row-by-row.

    Raises only when NO row could be written and none pre-exist (scope freeze
    failed entirely — the session must fail honestly).
    """
    now_iso = _iso(_now(now))
    inserted = 0
    last_error: Optional[Exception] = None
    for row in rows:
        payload = {
            "id": str(uuid.uuid4()),
            "run_session_id": run_session_id,
            "user_id": user_id,
            "state": TICKER_PENDING,
            "created_at": now_iso,
            "updated_at": now_iso,
            **row,
        }
        try:
            client.table(TICKERS_TABLE).insert(payload).execute()
            inserted += 1
        except Exception as exc:  # unique violation → already frozen
            last_error = exc
    # Honest scope check (adversarial-review defect D6): every requested
    # ticker must now have a row — a transient per-row insert failure must
    # surface as an explicit retryable error, never as a silently shrunken
    # run scope. (Unique-violation absorption stays: on a retry the row
    # already exists and is counted here.)
    if rows:
        existing = {
            str(r.get("ticker") or "")
            for r in list_ticker_rows(client, run_session_id=run_session_id)
        }
        missing = [
            str(r.get("ticker")) for r in rows
            if str(r.get("ticker")) not in existing
        ]
        if missing:
            raise RuntimeError(
                f"intel_run_tickers scope freeze incomplete — missing "
                f"{missing}: {last_error}"
            )
    return inserted


def list_ticker_rows(
    client: Any, *, run_session_id: str
) -> list[dict[str, Any]]:
    try:
        res = (
            client.table(TICKERS_TABLE)
            .select("*")
            .eq("run_session_id", run_session_id)
            .execute()
        )
        return _rows(res)
    except Exception as exc:
        logger.warning(
            "run_task_store.list_tickers_failed session=%s err=%s",
            run_session_id, exc,
        )
        return []


def update_ticker_row(
    client: Any,
    *,
    run_session_id: str,
    ticker: str,
    patch: dict[str, Any],
    expected_states: Optional[list[str]] = None,
    now: Optional[datetime] = None,
) -> bool:
    """Patch one frozen ticker row.

    ``expected_states`` is the claim fence for state transitions: when given,
    the update matches only rows currently in one of those states — a stale
    worker whose task was reclaimed (and whose rival already advanced the
    ticker) matches zero rows and cannot overwrite the newer transition.
    Returns False when nothing matched or on DB failure.
    """
    try:
        query = (
            client.table(TICKERS_TABLE)
            .update({**patch, "updated_at": _iso(_now(now))})
            .eq("run_session_id", run_session_id)
            .eq("ticker", ticker)
        )
        if expected_states:
            query = query.in_("state", list(expected_states))
        res = query.execute()
        return bool(_rows(res))
    except Exception as exc:
        logger.warning(
            "run_task_store.update_ticker_failed session=%s ticker=%s err=%s",
            run_session_id, ticker, exc,
        )
        return False


# ── Task creation (idempotent) ───────────────────────────────────────────────

def logical_task_key(
    task_type: str,
    lane: Optional[str],
    ticker: Optional[str],
    batch_key: Optional[str],
) -> tuple[str, str, str, str]:
    """The migration-027 logical identity of a task (NULLs normalized)."""
    return (
        str(task_type),
        str(lane or ""),
        str(ticker or ""),
        str(batch_key or ""),
    )


def find_task_by_logical_key(
    client: Any,
    *,
    run_session_id: str,
    task_type: str,
    lane: Optional[str] = None,
    ticker: Optional[str] = None,
    batch_key: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Read the exact task matching one logical identity (or None).

    Raises on DB read failure — callers use this to VERIFY a suspected
    duplicate, and an unverifiable duplicate must never be treated as one.
    """
    query = (
        client.table(TASKS_TABLE)
        .select("*")
        .eq("run_session_id", run_session_id)
        .eq("task_type", task_type)
    )
    rows = _rows(query.execute())
    wanted = logical_task_key(task_type, lane, ticker, batch_key)
    for row in rows:
        if logical_task_key(
            str(row.get("task_type")), row.get("lane"),
            row.get("ticker"), row.get("batch_key"),
        ) == wanted:
            return row
    return None


def get_or_create_task(
    client: Any,
    *,
    run_session_id: str,
    user_id: str,
    task_type: str,
    ticker: Optional[str] = None,
    batch_key: Optional[str] = None,
    lane: Optional[str] = None,
    asset_type: Optional[str] = None,
    state: str = TASK_PENDING,
    priority: int = 100,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    input_fingerprint: Optional[str] = None,
    now: Optional[datetime] = None,
) -> tuple[dict[str, Any], bool]:
    """Fail-closed idempotent task creation.

    Contract (adversarial-completion item 3):
      1. attempt the insert;
      2. on ANY insert exception, VERIFY the suspected duplicate by reading
         the exact logical identity back — if the row exists, return it
         (``created=False``);
      3. if no such row exists, the failure was NOT a duplicate — re-raise.
         An unknown database error is never translated into "duplicate".

    Returns (task_row, created).
    """
    now_dt = _now(now)
    payload = {
        "id": str(uuid.uuid4()),
        "run_session_id": run_session_id,
        "user_id": user_id,
        "task_type": task_type,
        "ticker": ticker,
        "batch_key": batch_key,
        "lane": lane,
        "asset_type": asset_type,
        "state": state,
        "priority": int(priority),
        "attempts": 0,
        "max_attempts": int(max_attempts),
        "next_retry_at": _iso(now_dt),
        "input_fingerprint": input_fingerprint,
        "created_at": _iso(now_dt),
        "updated_at": _iso(now_dt),
    }
    try:
        res = client.table(TASKS_TABLE).insert(payload).execute()
        rows = _rows(res)
        return (rows[0] if rows else payload), True
    except Exception as insert_exc:
        existing = find_task_by_logical_key(
            client,
            run_session_id=run_session_id,
            task_type=task_type,
            lane=lane,
            ticker=ticker,
            batch_key=batch_key,
        )
        if existing is not None:
            return existing, False
        logger.error(
            "run_task_store.create_task_failed_not_duplicate session=%s "
            "type=%s lane=%s ticker=%s err=%s",
            run_session_id, task_type, lane, ticker, insert_exc,
        )
        raise


def create_task(
    client: Any,
    *,
    run_session_id: str,
    user_id: str,
    task_type: str,
    ticker: Optional[str] = None,
    batch_key: Optional[str] = None,
    lane: Optional[str] = None,
    asset_type: Optional[str] = None,
    state: str = TASK_PENDING,
    priority: int = 100,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    input_fingerprint: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Optional[dict[str, Any]]:
    """Scheduler-facing wrapper over ``get_or_create_task``.

    Returns the row when THIS call created it, None when the logical task
    already existed. Raises on any non-duplicate database failure — a
    transient error can never silently drop a task from the graph.
    """
    row, created = get_or_create_task(
        client,
        run_session_id=run_session_id,
        user_id=user_id,
        task_type=task_type,
        ticker=ticker,
        batch_key=batch_key,
        lane=lane,
        asset_type=asset_type,
        state=state,
        priority=priority,
        max_attempts=max_attempts,
        input_fingerprint=input_fingerprint,
        now=now,
    )
    return row if created else None


def list_tasks(
    client: Any,
    *,
    run_session_id: str,
    task_type: Optional[str] = None,
    ticker: Optional[str] = None,
    states: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    try:
        query = (
            client.table(TASKS_TABLE)
            .select("*")
            .eq("run_session_id", run_session_id)
        )
        if task_type is not None:
            query = query.eq("task_type", task_type)
        if ticker is not None:
            query = query.eq("ticker", ticker)
        if states:
            query = query.in_("state", list(states))
        return _rows(query.execute())
    except Exception as exc:
        logger.warning(
            "run_task_store.list_tasks_failed session=%s err=%s",
            run_session_id, exc,
        )
        return []


def count_tasks_by_state(client: Any, *, run_session_id: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in list_tasks(client, run_session_id=run_session_id):
        state = str(row.get("state") or "")
        counts[state] = counts.get(state, 0) + 1
    return counts


def unblock_task(
    client: Any, *, task_id: str, now: Optional[datetime] = None
) -> bool:
    """blocked → pending (prerequisites became terminal)."""
    try:
        res = (
            client.table(TASKS_TABLE)
            .update({
                "state": TASK_PENDING,
                "next_retry_at": _iso(_now(now)),
                "updated_at": _iso(_now(now)),
            })
            .eq("id", task_id)
            .eq("state", TASK_BLOCKED)
            .execute()
        )
        return bool(_rows(res))
    except Exception as exc:
        logger.warning("run_task_store.unblock_failed task=%s err=%s", task_id, exc)
        return False


def sweep_exhausted_expired_claims(
    client: Any,
    *,
    run_session_id: Optional[str] = None,
    now: Optional[datetime] = None,
) -> int:
    """Terminalize tasks stuck in 'claimed' with an expired lease and NO
    attempts remaining.

    Claim paths only accept ``attempts < max_attempts``, so a worker that
    crashes while holding the FINAL attempt would otherwise leave the task
    claimed forever — never terminal, never reclaimable — wedging its
    session permanently (adversarial-review defect D1). CAS-guarded on the
    exact observed (state, lease_expires_at) so an in-flight worker that is
    merely slow can never be terminalized underneath itself before its lease
    truly expired. Returns how many tasks were failed.
    """
    now_dt = _now(now)
    try:
        query = (
            client.table(TASKS_TABLE)
            .select("*")
            .eq("state", TASK_CLAIMED)
        )
        if run_session_id is not None:
            query = query.eq("run_session_id", run_session_id)
        rows = _rows(query.execute())
    except Exception as exc:
        logger.warning("run_task_store.sweep_read_failed err=%s", exc)
        return 0
    swept = 0
    for row in rows:
        attempts = int(row.get("attempts") or 0)
        max_attempts = int(row.get("max_attempts") or DEFAULT_MAX_ATTEMPTS)
        lease = _parse_iso(row.get("lease_expires_at"))
        if attempts < max_attempts or lease is None or lease > now_dt:
            continue
        try:
            res = (
                client.table(TASKS_TABLE)
                .update({
                    "state": "failed",
                    "error_code": "lease_expired_attempts_exhausted",
                    "completed_at": _iso(now_dt),
                    "lease_expires_at": None,
                    "updated_at": _iso(now_dt),
                })
                .eq("id", str(row.get("id")))
                .eq("state", TASK_CLAIMED)
                .eq("lease_expires_at", str(row.get("lease_expires_at")))
                .execute()
            )
            if _rows(res):
                swept += 1
        except Exception as exc:
            logger.debug(
                "run_task_store.sweep_cas_lost task=%s err=%s",
                row.get("id"), exc,
            )
    if swept:
        logger.info(
            "run_task_store.swept_exhausted_expired_claims count=%d", swept,
        )
    return swept


# ── Claiming ─────────────────────────────────────────────────────────────────

def claim_tasks(
    client: Any,
    *,
    worker_id: str,
    limit: int = 1,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    run_session_id: Optional[str] = None,
    now: Optional[datetime] = None,
) -> list[dict[str, Any]]:
    """Atomically claim up to ``limit`` due tasks.

    RPC first (SKIP LOCKED, true multi-worker atomicity); guarded-UPDATE CAS
    fallback otherwise. Both increment ``attempts`` at claim time so a
    crash-looping task still exhausts its budget via lease expiry.
    """
    rpc = getattr(client, "rpc", None)
    if callable(rpc):
        try:
            res = rpc(
                "claim_intel_run_tasks",
                {
                    "p_worker_id": worker_id,
                    "p_limit": int(limit),
                    "p_lease_seconds": int(lease_seconds),
                    "p_run_session_id": run_session_id,
                },
            ).execute()
            rows = _rows(res)
            if rows or getattr(res, "data", None) == []:
                return rows
        except Exception as exc:
            logger.debug(
                "run_task_store.claim_rpc_unavailable err=%s — CAS fallback", exc,
            )
    return _claim_tasks_cas(
        client,
        worker_id=worker_id,
        limit=limit,
        lease_seconds=lease_seconds,
        run_session_id=run_session_id,
        now=now,
    )


def _claim_tasks_cas(
    client: Any,
    *,
    worker_id: str,
    limit: int,
    lease_seconds: int,
    run_session_id: Optional[str],
    now: Optional[datetime],
) -> list[dict[str, Any]]:
    now_dt = _now(now)
    try:
        query = (
            client.table(TASKS_TABLE)
            .select("*")
            .in_("state", [TASK_PENDING, TASK_CLAIMED])
        )
        if run_session_id is not None:
            query = query.eq("run_session_id", run_session_id)
        candidates = _rows(query.execute())
    except Exception as exc:
        logger.warning("run_task_store.claim_read_failed err=%s", exc)
        return []

    def _due(row: dict[str, Any]) -> bool:
        attempts = int(row.get("attempts") or 0)
        max_attempts = int(row.get("max_attempts") or DEFAULT_MAX_ATTEMPTS)
        if attempts >= max_attempts:
            return False
        state = str(row.get("state") or "")
        if state == TASK_PENDING:
            due_at = _parse_iso(row.get("next_retry_at"))
            return due_at is None or due_at <= now_dt
        if state == TASK_CLAIMED:
            lease = _parse_iso(row.get("lease_expires_at"))
            return lease is not None and lease <= now_dt
        return False

    due = [r for r in candidates if _due(r)]
    due.sort(
        key=lambda r: (
            int(r.get("priority") or 100),
            str(r.get("next_retry_at") or ""),
            str(r.get("created_at") or ""),
            str(r.get("ticker") or ""),
        )
    )

    claimed: list[dict[str, Any]] = []
    lease_iso = _iso(now_dt + timedelta(seconds=lease_seconds))
    for row in due:
        if len(claimed) >= max(0, limit):
            break
        prev_state = str(row.get("state") or "")
        patch = {
            "state": TASK_CLAIMED,
            "claim_owner": worker_id,
            # Claim-generation fence: a fresh token on EVERY claim. All
            # task-owned side effects prove they still hold this exact token,
            # so a stale worker whose task was reclaimed can never write.
            "claim_token": str(uuid.uuid4()),
            "claimed_at": _iso(now_dt),
            "started_at": row.get("started_at") or _iso(now_dt),
            "lease_expires_at": lease_iso,
            "attempts": int(row.get("attempts") or 0) + 1,
            "updated_at": _iso(now_dt),
        }
        try:
            update = (
                client.table(TASKS_TABLE)
                .update(patch)
                .eq("id", str(row.get("id")))
                .eq("state", prev_state)
            )
            if prev_state == TASK_CLAIMED:
                # Only steal the exact expired lease we observed.
                update = update.eq(
                    "lease_expires_at", str(row.get("lease_expires_at"))
                )
            res = update.execute()
            updated_rows = _rows(res)
            if updated_rows:
                claimed.append(updated_rows[0])
        except Exception as exc:
            logger.debug(
                "run_task_store.claim_cas_lost task=%s err=%s", row.get("id"), exc,
            )
    return claimed


# ── Completion ───────────────────────────────────────────────────────────────

def complete_task(
    client: Any,
    *,
    task: dict[str, Any],
    worker_id: str,
    final_state: str,
    output_ref: Optional[str] = None,
    output: Optional[dict[str, Any]] = None,
    error_code: Optional[str] = None,
    error_detail: Optional[str] = None,
    now: Optional[datetime] = None,
) -> bool:
    """Terminal-complete (succeeded/degraded/failed) or release-to-pending.

    Guarded on (id, state='claimed', claim_owner, claim_token) so a task can
    never be completed twice and a stale worker can never overwrite a
    reclaimed task's result. Retryable failures (attempts remaining) go back
    to ``pending`` with backoff instead of terminal ``failed``.

    Production path: the transactional ``complete_intel_run_task`` RPC
    (single guarded UPDATE, token-required). Fallback: the identical
    guarded-UPDATE CAS when the RPC is unavailable (pre-migration
    environments, in-memory test fakes).
    """
    now_dt = _now(now)
    task_id = str(task.get("id"))
    attempts = int(task.get("attempts") or 0)
    max_attempts = int(task.get("max_attempts") or DEFAULT_MAX_ATTEMPTS)

    state = final_state
    next_retry_at: Optional[str] = None
    if final_state == TASK_FAILED_RETRYABLE:
        if attempts >= max_attempts:
            state = "failed"
        else:
            state = TASK_PENDING
            next_retry_at = compute_task_retry_at(attempts, now_dt)

    claim_token = task.get("claim_token")
    rpc = getattr(client, "rpc", None)
    if callable(rpc) and claim_token:
        try:
            res = rpc(
                "complete_intel_run_task",
                {
                    "p_task_id": task_id,
                    "p_worker_id": worker_id,
                    "p_claim_token": str(claim_token),
                    "p_final_state": state,
                    "p_output_ref": output_ref,
                    "p_output": output,
                    "p_error_code": error_code,
                    "p_error_detail": (
                        str(error_detail)[:500] if error_detail else None
                    ),
                    "p_retry_at": next_retry_at,
                },
            ).execute()
            data = getattr(res, "data", None)
            if isinstance(data, bool):
                return data
            if isinstance(data, list) and data and isinstance(data[0], bool):
                return data[0]
        except Exception as exc:
            logger.debug(
                "run_task_store.complete_rpc_unavailable err=%s — CAS fallback",
                exc,
            )

    patch: dict[str, Any] = {
        "state": state,
        "updated_at": _iso(now_dt),
        "error_code": error_code,
        "error_detail": (str(error_detail)[:500] if error_detail else None),
        "lease_expires_at": None,
    }
    if output_ref is not None:
        patch["output_ref"] = output_ref
    if output is not None:
        patch["output"] = output
    if state in TASK_TERMINAL_STATES:
        patch["completed_at"] = _iso(now_dt)
    else:
        patch["claim_owner"] = None
        patch["next_retry_at"] = next_retry_at or _iso(now_dt)

    try:
        query = (
            client.table(TASKS_TABLE)
            .update(patch)
            .eq("id", task_id)
            .eq("state", TASK_CLAIMED)
            .eq("claim_owner", worker_id)
        )
        # Claim-generation fence: completion requires the exact claim token
        # issued at claim time — a stale worker whose task was reclaimed
        # holds an old token and matches zero rows.
        claim_token = task.get("claim_token")
        if claim_token:
            query = query.eq("claim_token", str(claim_token))
        res = query.execute()
        return bool(_rows(res))
    except Exception as exc:
        logger.warning(
            "run_task_store.complete_failed task=%s err=%s", task_id, exc,
        )
        return False


# Sentinel "final state" meaning: failed this attempt, retry if budget allows.
TASK_FAILED_RETRYABLE = "__failed_retryable__"


def owns_claim(client: Any, task: dict[str, Any]) -> bool:
    """Does the caller still hold this task's CURRENT claim?

    Verified against the durable row: state is 'claimed', and both the
    claim_owner and the claim-generation token match the claim the caller was
    issued. Executors call this immediately before every task-owned side
    effect (specialist outputs, ticker transitions, decision/evidence writes,
    publication) so a stale worker whose lease expired and whose task was
    reclaimed refuses to write. Fail closed: unreadable ⇒ not owned.
    """
    try:
        res = (
            client.table(TASKS_TABLE)
            .select("state,claim_owner,claim_token")
            .eq("id", str(task.get("id")))
            .limit(1)
            .execute()
        )
        rows = _rows(res)
        if not rows:
            return False
        row = rows[0]
        if str(row.get("state") or "") != TASK_CLAIMED:
            return False
        if str(row.get("claim_owner") or "") != str(task.get("claim_owner") or ""):
            return False
        issued = task.get("claim_token")
        current = row.get("claim_token")
        if issued and str(current or "") != str(issued):
            return False
        return True
    except Exception:
        return False


# ── Specialist outputs ───────────────────────────────────────────────────────

def upsert_specialist_output(
    client: Any,
    *,
    run_session_id: str,
    user_id: str,
    ticker: str,
    axis: str,
    output: dict[str, Any],
    now: Optional[datetime] = None,
) -> bool:
    """Insert one (session, ticker, axis) output; on unique conflict (repair
    retry) update the existing row in place."""
    now_iso = _iso(_now(now))
    payload = {
        "id": str(uuid.uuid4()),
        "run_session_id": run_session_id,
        "user_id": user_id,
        "ticker": ticker,
        "axis": axis,
        "created_at": now_iso,
        **output,
    }
    try:
        client.table(SPECIALIST_TABLE).insert(payload).execute()
        return True
    except Exception:
        try:
            (
                client.table(SPECIALIST_TABLE)
                .update({**output})
                .eq("run_session_id", run_session_id)
                .eq("ticker", ticker)
                .eq("axis", axis)
                .execute()
            )
            return True
        except Exception as exc:
            logger.warning(
                "run_task_store.specialist_upsert_failed session=%s ticker=%s "
                "axis=%s err=%s",
                run_session_id, ticker, axis, exc,
            )
            return False


def list_specialist_outputs(
    client: Any,
    *,
    run_session_id: str,
    ticker: Optional[str] = None,
) -> list[dict[str, Any]]:
    try:
        query = (
            client.table(SPECIALIST_TABLE)
            .select("*")
            .eq("run_session_id", run_session_id)
        )
        if ticker is not None:
            query = query.eq("ticker", ticker)
        return _rows(query.execute())
    except Exception as exc:
        logger.warning(
            "run_task_store.list_specialist_failed session=%s err=%s",
            run_session_id, exc,
        )
        return []


def find_reusable_specialist_output(
    client: Any,
    *,
    user_id: str,
    ticker: str,
    axis: str,
    input_fingerprint: str,
    now: Optional[datetime] = None,
) -> Optional[dict[str, Any]]:
    """A prior session's still-valid output for the same evidence fingerprint
    (skips a duplicate LLM call). Returns None when none is reusable."""
    now_dt = _now(now)
    try:
        res = (
            client.table(SPECIALIST_TABLE)
            .select("*")
            .eq("user_id", user_id)
            .eq("ticker", ticker)
            .eq("axis", axis)
            .eq("input_fingerprint", input_fingerprint)
            .order("created_at", desc=True)
            .limit(5)
            .execute()
        )
        for row in _rows(res):
            valid_until = _parse_iso(row.get("valid_until"))
            if valid_until is not None and valid_until > now_dt:
                return row
        return None
    except Exception:
        return None
