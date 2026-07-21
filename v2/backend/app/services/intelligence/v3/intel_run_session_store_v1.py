"""Durable Run Intel session store (SQL access layer).

One explicit Run Intel click == one row in ``public.intel_run_sessions``
(migration ``v2/database/026_intel_run_sessions.sql``). The session row is the
authoritative durable state of that click:

  * the immutable active-holdings scope captured when the click began;
  * the stale subset that required analyst refresh;
  * the expected session job count (exactly one ``analyst_refresh_jobs`` row
    per stale ticker, FK'd by ``run_session_id``);
  * the pre-session snapshot row id (completion requires a DIFFERENT,
    session-linked snapshot);
  * the completed snapshot row id once this session's own snapshot published;
  * a retryable-error field for publication failures.

State machine (`STATUS_*` below):

    created → ticker_refresh_in_progress → publishing → completed
                                        ↘ publication_retryable_failed ↗
              (terminal) failed

No sentinel rows anywhere: session state lives ONLY in this table. This
module never writes fake tickers into ``analyst_refresh_jobs`` or any other
table, and never infers a session from queue rows, snapshots, or timestamps.

Like the job store, this module never raises into its callers for reads —
DB failures degrade to ``None`` / explicit error strings. Writes raise so the
caller can surface an explicit retryable failure to the user.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

logger = logging.getLogger(__name__)

TABLE = "intel_run_sessions"

# ── Session statuses (must match the migration CHECK constraint) ──────────────
STATUS_CREATED = "created"
STATUS_TICKER_REFRESH = "ticker_refresh_in_progress"
STATUS_PUBLISHING = "publishing"
STATUS_PUBLICATION_RETRY = "publication_retryable_failed"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"

ALL_STATUSES = (
    STATUS_CREATED,
    STATUS_TICKER_REFRESH,
    STATUS_PUBLISHING,
    STATUS_PUBLICATION_RETRY,
    STATUS_COMPLETED,
    STATUS_FAILED,
)

# Statuses from which a continuation request can still make progress.
ACTIVE_STATUSES = (
    STATUS_CREATED,
    STATUS_TICKER_REFRESH,
    STATUS_PUBLISHING,
    STATUS_PUBLICATION_RETRY,
)


def _rows(res: Any) -> list[dict[str, Any]]:
    data = getattr(res, "data", None)
    return data if isinstance(data, list) else []


def _now_iso(now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.isoformat()


# ── Reads ─────────────────────────────────────────────────────────────────────

def get_session(client: Any, session_id: "UUID | str") -> Optional[dict[str, Any]]:
    """Load one session row by id. Returns None when missing or on DB failure."""
    try:
        res = (
            client.table(TABLE)
            .select("*")
            .eq("id", str(session_id))
            .limit(1)
            .execute()
        )
        rows = _rows(res)
        return rows[0] if rows else None
    except Exception as exc:
        logger.warning(
            "intel_run_session.get_failed session_id=%s err=%s", session_id, exc,
        )
        return None


# ── Writes ────────────────────────────────────────────────────────────────────

def create_session(
    client: Any,
    *,
    session_id: "UUID | str",
    user_id: "UUID | str",
    holdings_scope: list[str],
    stale_tickers: list[str],
    pre_session_snapshot_id: Optional[str],
    status: str = STATUS_CREATED,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Insert the durable session row for one explicit Run Intel click.

    Raises on DB failure — session creation failing must surface as an
    explicit retryable error, never as a silent legacy fallback.

    Race-safe for duplicate network retries of the FIRST request: when the
    insert hits the primary-key conflict (the same click's retry already
    created the row), the existing row is read back and returned as long as
    it belongs to the same user.
    """
    now_iso = _now_iso(now)
    row = {
        "id": str(session_id),
        "user_id": str(user_id),
        "status": status,
        # Stored AS GIVEN (positions-table casing) — the certification
        # contract matches recommendation/insight rows against these strings
        # the same way it matches live positions rows. Queue-layer helpers
        # upper-case internally where needed.
        "holdings_scope": [str(t) for t in holdings_scope],
        "stale_tickers": [str(t) for t in stale_tickers],
        "expected_ticker_job_count": len(stale_tickers),
        "pre_session_snapshot_id": pre_session_snapshot_id,
        "created_at": now_iso,
        "updated_at": now_iso,
    }
    try:
        res = client.table(TABLE).insert(row).execute()
        inserted = _rows(res)
        created = inserted[0] if inserted else row
        logger.info(
            "intel_run_session.created session_id=%s user_id=%s status=%s "
            "holdings_scope_count=%d stale_ticker_count=%d pre_session_snapshot_id=%s",
            session_id, user_id, status,
            len(holdings_scope), len(stale_tickers), pre_session_snapshot_id,
        )
        return created
    except Exception as exc:
        # Duplicate-id race (same click retried concurrently): adopt the
        # existing row instead of failing the click.
        existing = get_session(client, session_id)
        if existing is not None and str(existing.get("user_id")) == str(user_id):
            logger.info(
                "intel_run_session.create_raced_adopting_existing session_id=%s "
                "user_id=%s",
                session_id, user_id,
            )
            return existing
        logger.error(
            "intel_run_session.create_failed session_id=%s user_id=%s err=%s",
            session_id, user_id, exc,
        )
        raise


def update_session(
    client: Any,
    session_id: "UUID | str",
    *,
    status: Optional[str] = None,
    completed_snapshot_id: Optional[str] = None,
    last_error: Optional[str] = "__unset__",
    increment_publication_attempts: bool = False,
    completed: bool = False,
    now: Optional[datetime] = None,
) -> bool:
    """Patch the session row. Returns False (and logs) on DB failure.

    ``last_error`` uses the "__unset__" sentinel default so callers can
    explicitly clear it with ``last_error=None``.
    """
    now_iso = _now_iso(now)
    patch: dict[str, Any] = {"updated_at": now_iso}
    if status is not None:
        patch["status"] = status
    if completed_snapshot_id is not None:
        patch["completed_snapshot_id"] = completed_snapshot_id
    if last_error != "__unset__":
        patch["last_error"] = (
            str(last_error)[:500] if last_error is not None else None
        )
    if completed:
        patch["completed_at"] = now_iso
    try:
        if increment_publication_attempts:
            current = get_session(client, session_id) or {}
            patch["publication_attempts"] = int(
                current.get("publication_attempts") or 0
            ) + 1
        (
            client.table(TABLE)
            .update(patch)
            .eq("id", str(session_id))
            .execute()
        )
        logger.info(
            "intel_run_session.updated session_id=%s patch_status=%s "
            "completed_snapshot_id=%s completed=%s",
            session_id, status, completed_snapshot_id, completed,
        )
        return True
    except Exception as exc:
        logger.warning(
            "intel_run_session.update_failed session_id=%s err=%s",
            session_id, exc,
        )
        return False


# ── Session-scoped job accounting ─────────────────────────────────────────────

def count_session_job_states(
    client: Any,
    *,
    run_session_id: "UUID | str",
) -> dict[str, Any]:
    """Full status breakdown of THIS session's jobs (and only this session's).

    Returns::

        {
          "total": int,
          "pending": int,
          "claimed": int,
          "succeeded": int,
          "failed_retryable": int,   # failed, attempts remaining
          "failed_terminal": int,    # failed, attempt budget exhausted
          "succeeded_tickers": [..],
          "error": str | None,
        }

    Old jobs (NULL or different ``run_session_id``) and other users' jobs are
    invisible here by construction — the query filters on the exact session id.
    """
    out: dict[str, Any] = {
        "total": 0,
        "pending": 0,
        "claimed": 0,
        "succeeded": 0,
        "failed_retryable": 0,
        "failed_terminal": 0,
        "succeeded_tickers": [],
        "error": None,
    }
    try:
        res = (
            client.table("analyst_refresh_jobs")
            .select("ticker,status,attempts,max_attempts")
            .eq("run_session_id", str(run_session_id))
            .execute()
        )
        rows = _rows(res)
    except Exception as exc:
        logger.warning(
            "intel_run_session.count_jobs_failed run_session_id=%s err=%s",
            run_session_id, exc,
        )
        out["error"] = str(exc)
        return out

    for row in rows:
        out["total"] += 1
        status = str(row.get("status") or "")
        attempts = int(row.get("attempts") or 0)
        max_attempts = int(row.get("max_attempts") or 5)
        if status == "succeeded":
            out["succeeded"] += 1
            ticker = str(row.get("ticker") or "").upper()
            if ticker:
                out["succeeded_tickers"].append(ticker)
        elif status == "pending":
            out["pending"] += 1
        elif status == "claimed":
            out["claimed"] += 1
        elif status == "failed":
            if attempts >= max_attempts:
                out["failed_terminal"] += 1
            else:
                out["failed_retryable"] += 1
    return out
