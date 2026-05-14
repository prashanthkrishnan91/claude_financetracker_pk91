"""Durable analyst refresh job store (Intel v3 Stage 3.2).

The Stage 3.1 refresh-request seam logged that stale owned-position analyst
evidence needed a refresh but did not persist anything — a future Intelligence
Plane PR owned consumption. This module is that durable mechanism's storage
layer: a thin, SQL-backed access layer over the ``analyst_refresh_jobs`` table
(migration ``v2/database/018_analyst_refresh_jobs.sql``).

Responsibilities:
  * ``enqueue_refresh_jobs`` — idempotently upsert one durable job per
    (user, ticker, window). Repeated Run Intel v3 clicks inside the same window
    touch the existing row instead of creating a duplicate.
  * ``claim_due_jobs`` — atomically claim pending/failed jobs whose retry time
    has arrived, so the worker can refresh them outside the HTTP request.
  * ``mark_job_succeeded`` / ``mark_job_failed`` — record per-ticker outcome.
    Failed tickers stay claimable (with exponential backoff) and never get a
    fabricated success — "failed refreshes must not fabricate freshness".

What this module is NOT:
  * It does not call the LLM / AgentOrchestrator (the worker does).
  * It does not import or own deterministic decision authority.
  * It never raises into its callers — DB failure degrades to an explicit
    error field / empty result so the synchronous request and the worker both
    stay resilient.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID

logger = logging.getLogger(__name__)

TABLE = "analyst_refresh_jobs"

# ── Job statuses ──────────────────────────────────────────────────────────────
JOB_PENDING = "pending"
JOB_CLAIMED = "claimed"
JOB_SUCCEEDED = "succeeded"
JOB_FAILED = "failed"

# Statuses the worker is allowed to claim. ``claimed`` is excluded so two
# concurrent workers cannot grab the same row; ``succeeded`` is terminal.
CLAIMABLE_STATUSES = (JOB_PENDING, JOB_FAILED)

DEFAULT_MAX_ATTEMPTS = 5

# Exponential backoff base for failed-job retry scheduling: 15m, 30m, 60m,
# 120m, ... capped at 24h. A pending job's next_retry_at is set to "now" at
# enqueue time so it is due immediately.
_RETRY_BASE_MINUTES = 15
_RETRY_MAX_MINUTES = 24 * 60


# ── Window + retry helpers ────────────────────────────────────────────────────

def default_refresh_window(now: Optional[datetime] = None) -> str:
    """Idempotency window key — one bucket per UTC day.

    Repeated Run Intel v3 clicks for the same user/ticker on the same UTC day
    collapse onto a single durable job row.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc).strftime("%Y-%m-%d")


def compute_next_retry_at(attempts: int, now: Optional[datetime] = None) -> str:
    """Exponential backoff for failed jobs, capped at 24h."""
    now = now or datetime.now(timezone.utc)
    minutes = min(_RETRY_BASE_MINUTES * (2 ** max(0, attempts - 1)), _RETRY_MAX_MINUTES)
    return (now + timedelta(minutes=minutes)).isoformat()


def _rows(res: Any) -> list[dict[str, Any]]:
    """Return ``res.data`` only when it is a real list — defensive against
    mocked clients in tests and unexpected Supabase shapes in production."""
    data = getattr(res, "data", None)
    return data if isinstance(data, list) else []


def _iso_lte(iso_value: Any, now: datetime) -> bool:
    """True when ``iso_value`` is a parseable timestamp at or before ``now``."""
    if not isinstance(iso_value, str) or not iso_value:
        return False
    try:
        dt = datetime.fromisoformat(iso_value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt <= now


# ── Job dataclass ─────────────────────────────────────────────────────────────

@dataclass
class AnalystRefreshJob:
    """A claimed durable refresh job, as the worker consumes it."""
    id: str
    user_id: str
    ticker: str
    refresh_window: str
    status: str
    attempts: int = 0
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    prior_action: Optional[str] = None
    weight_pct: Optional[float] = None
    evidence_age_hours_at_request: Optional[float] = None
    next_retry_at: Optional[str] = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "AnalystRefreshJob":
        return cls(
            id=str(row.get("id")),
            user_id=str(row.get("user_id")),
            ticker=str(row.get("ticker") or "").upper(),
            refresh_window=str(row.get("refresh_window") or ""),
            status=str(row.get("status") or ""),
            attempts=int(row.get("attempts") or 0),
            max_attempts=int(row.get("max_attempts") or DEFAULT_MAX_ATTEMPTS),
            prior_action=row.get("prior_action"),
            weight_pct=row.get("weight_pct"),
            evidence_age_hours_at_request=row.get("evidence_age_hours_at_request"),
            next_retry_at=row.get("next_retry_at"),
        )


@dataclass
class EnqueueResult:
    """Outcome of one ``enqueue_refresh_jobs`` call."""
    requested_tickers: list[str] = field(default_factory=list)
    created_count: int = 0
    touched_count: int = 0
    error: Optional[str] = None

    @property
    def durable_job_count(self) -> int:
        """Tickers that now have exactly one durable job row (created + touched)."""
        return self.created_count + self.touched_count


# ── Enqueue (idempotent) ──────────────────────────────────────────────────────

def enqueue_refresh_jobs(
    client: Any,
    *,
    user_id: "UUID | str",
    tickers: list[str],
    hints_by_ticker: Optional[dict[str, dict[str, Any]]] = None,
    window: Optional[str] = None,
    now: Optional[datetime] = None,
) -> EnqueueResult:
    """Idempotently upsert one durable refresh job per (user, ticker, window).

    Idempotency is enforced by a read-then-write: existing rows for the window
    are *touched* (``requested_at`` bumped) rather than re-inserted, so repeated
    clicks never create duplicates and never reset the retry/attempt counters.
    The unique index ``(user_id, ticker, refresh_window)`` is the durable
    backstop if two enqueues race.

    Never raises — a DB failure degrades to an ``EnqueueResult`` with ``error``
    set so the synchronous Run Intel v3 request stays fast and resilient.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    window = window or default_refresh_window(now)
    hints_by_ticker = hints_by_ticker or {}
    now_iso = now.isoformat()

    seen: set[str] = set()
    requested: list[str] = []
    for raw in tickers or []:
        t = str(raw or "").strip().upper()
        if t and t not in seen:
            seen.add(t)
            requested.append(t)
    if not requested:
        return EnqueueResult(requested_tickers=[])

    # Read existing rows for this window so we touch instead of duplicate.
    try:
        existing_res = (
            client.table(TABLE)
            .select("id,ticker,status,attempts")
            .eq("user_id", str(user_id))
            .eq("refresh_window", window)
            .in_("ticker", requested)
            .execute()
        )
        existing_rows = _rows(existing_res)
    except Exception as exc:
        logger.warning(
            "intel_v3.analyst_refresh_job_enqueue_read_failed user_id=%s window=%s err=%s",
            user_id, window, exc,
        )
        return EnqueueResult(requested_tickers=requested, error=str(exc))

    existing_by_ticker = {
        str(r.get("ticker") or "").upper(): r for r in existing_rows
    }

    to_insert: list[dict[str, Any]] = []
    touched_ids: list[str] = []
    for t in requested:
        if t in existing_by_ticker:
            touched_ids.append(str(existing_by_ticker[t].get("id")))
            continue
        hint = hints_by_ticker.get(t) or {}
        to_insert.append({
            "user_id": str(user_id),
            "ticker": t,
            "refresh_window": window,
            "status": JOB_PENDING,
            "attempts": 0,
            "prior_action": hint.get("prior_action"),
            "weight_pct": hint.get("weight_pct"),
            "evidence_age_hours_at_request": hint.get("evidence_age_hours"),
            "requested_at": now_iso,
            # Pending jobs are due for the worker immediately.
            "next_retry_at": now_iso,
            "updated_at": now_iso,
        })

    error: Optional[str] = None
    created = 0
    if to_insert:
        try:
            res = client.table(TABLE).insert(to_insert).execute()
            inserted = _rows(res)
            created = len(inserted) if inserted else len(to_insert)
        except Exception as exc:
            error = str(exc)
            logger.warning(
                "intel_v3.analyst_refresh_job_enqueue_insert_failed user_id=%s "
                "window=%s err=%s",
                user_id, window, exc,
            )

    # Touch existing rows — bump requested_at only. Never reset status/attempts:
    # a re-click must not wipe a failed job's backoff or an in-flight claim.
    for job_id in touched_ids:
        try:
            (
                client.table(TABLE)
                .update({"requested_at": now_iso, "updated_at": now_iso})
                .eq("id", job_id)
                .execute()
            )
        except Exception as exc:
            logger.debug(
                "intel_v3.analyst_refresh_job_touch_failed job_id=%s err=%s",
                job_id, exc,
            )

    logger.info(
        "intel_v3.analyst_refresh_job_enqueued user_id=%s window=%s "
        "requested=%d created=%d touched=%d tickers=%s",
        user_id, window, len(requested), created, len(touched_ids),
        ",".join(requested),
    )
    return EnqueueResult(
        requested_tickers=requested,
        created_count=created,
        touched_count=len(touched_ids),
        error=error,
    )


# ── Claim ─────────────────────────────────────────────────────────────────────

def claim_due_jobs(
    client: Any,
    *,
    worker_run_id: "UUID | str",
    now: Optional[datetime] = None,
    limit: int = 50,
) -> list[AnalystRefreshJob]:
    """Claim up to ``limit`` due jobs for this worker run.

    A job is *due* when:
      * status is pending or failed,
      * attempts < max_attempts (exhausted jobs are never re-claimed), and
      * next_retry_at is null or at/before ``now``.

    Each claim is a guarded single-row UPDATE (``status`` must still equal the
    pre-claim status) so two concurrent workers cannot both grab the same row.
    Never raises — a read failure returns an empty list.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now_iso = now.isoformat()

    try:
        res = (
            client.table(TABLE)
            .select("*")
            .in_("status", list(CLAIMABLE_STATUSES))
            .order("requested_at")
            .execute()
        )
        candidates = _rows(res)
    except Exception as exc:
        logger.warning(
            "intel_v3.analyst_refresh_job_claim_read_failed worker_run_id=%s err=%s",
            worker_run_id, exc,
        )
        return []

    claimed: list[AnalystRefreshJob] = []
    for row in candidates:
        if len(claimed) >= limit:
            break
        attempts = int(row.get("attempts") or 0)
        max_attempts = int(row.get("max_attempts") or DEFAULT_MAX_ATTEMPTS)
        if attempts >= max_attempts:
            continue
        next_retry = row.get("next_retry_at")
        if next_retry is not None and not _iso_lte(next_retry, now):
            continue

        prev_status = row.get("status")
        job_id = str(row.get("id"))
        try:
            upd = (
                client.table(TABLE)
                .update({
                    "status": JOB_CLAIMED,
                    "attempts": attempts + 1,
                    "claimed_at": now_iso,
                    "worker_run_id": str(worker_run_id),
                    "updated_at": now_iso,
                })
                .eq("id", job_id)
                .eq("status", prev_status)
                .execute()
            )
        except Exception as exc:
            logger.warning(
                "intel_v3.analyst_refresh_job_claim_failed job_id=%s err=%s",
                job_id, exc,
            )
            continue

        if not _rows(upd):
            # Lost the race — another worker claimed it first.
            continue

        merged = dict(row)
        merged.update({"status": JOB_CLAIMED, "attempts": attempts + 1})
        job = AnalystRefreshJob.from_row(merged)
        claimed.append(job)
        logger.info(
            "intel_v3.analyst_refresh_job_claimed worker_run_id=%s job_id=%s "
            "user_id=%s ticker=%s attempt=%d/%d",
            worker_run_id, job_id, job.user_id, job.ticker,
            job.attempts, job.max_attempts,
        )

    return claimed


# ── Per-job terminal updates ──────────────────────────────────────────────────

def mark_job_succeeded(
    client: Any,
    job: AnalystRefreshJob,
    *,
    now: Optional[datetime] = None,
) -> bool:
    """Mark a job succeeded after the worker persisted refreshed evidence."""
    now_iso = (now or datetime.now(timezone.utc)).isoformat()
    try:
        (
            client.table(TABLE)
            .update({
                "status": JOB_SUCCEEDED,
                "completed_at": now_iso,
                "next_retry_at": None,
                "last_error": None,
                "updated_at": now_iso,
            })
            .eq("id", job.id)
            .execute()
        )
        return True
    except Exception as exc:
        logger.warning(
            "intel_v3.analyst_refresh_job_mark_succeeded_failed job_id=%s err=%s",
            job.id, exc,
        )
        return False


def mark_job_failed(
    client: Any,
    job: AnalystRefreshJob,
    *,
    error: str,
    now: Optional[datetime] = None,
) -> Optional[str]:
    """Mark a job failed. Never fabricates freshness.

    The job stays in ``failed`` status. If attempts remain, ``next_retry_at`` is
    scheduled with exponential backoff so the worker retries it later; if
    attempts are exhausted, ``next_retry_at`` is null and ``completed_at`` is
    stamped so the worker never re-claims it. Returns the scheduled
    ``next_retry_at`` ISO string, or None when exhausted / on DB failure.
    """
    now = now or datetime.now(timezone.utc)
    now_iso = now.isoformat()
    exhausted = job.attempts >= job.max_attempts
    next_retry = None if exhausted else compute_next_retry_at(job.attempts, now)
    try:
        (
            client.table(TABLE)
            .update({
                "status": JOB_FAILED,
                "last_error": str(error)[:500],
                "next_retry_at": next_retry,
                "completed_at": now_iso if exhausted else None,
                "updated_at": now_iso,
            })
            .eq("id", job.id)
            .execute()
        )
    except Exception as exc:
        logger.warning(
            "intel_v3.analyst_refresh_job_mark_failed_failed job_id=%s err=%s",
            job.id, exc,
        )
        return None
    return next_retry
