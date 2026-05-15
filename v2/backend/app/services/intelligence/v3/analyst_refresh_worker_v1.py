"""Continuous Intelligence Plane v1 — durable analyst refresh worker (Stage 3.2).

Stage 3.1 made the synchronous Run Intel v3 HTTP request a fast certification
path: stale owned-position analyst evidence is *requested* for refresh, never
refreshed inside the click. Stage 3.2 adds the real background plane that
consumes those durable requests.

Build 1 (durable resumable execution) adds:
  * Per-ticker retryable vs terminal failure distinction so one wall-clock
    timeout never blanket-terminal-fails all 34 tickers.
  * Post-timeout residual evidence check: after a timeout the worker queries
    the DB for any evidence committed before the cancellation propagated and
    marks those tickers succeeded rather than re-processing them needlessly.
  * Expanded structured log (jobs_due / failed_retryable / failed_terminal /
    timed_out_before_completion / remaining_pending_or_retryable / run_resumable)
    so production validation can answer exactly whether the worker is
    progressing, retrying, blocked, or certified.

This worker:
  1. Claims due ``analyst_refresh_jobs`` rows (owned-position analyst refresh
     jobs the Stage 3.1 seam enqueued) under budget + runtime caps.
  2. Groups claimed jobs by user and prioritises them (owned BUY/TRIM first,
     then portfolio weight, then evidence age, then ticker A→Z) — every job is
     already an owned position, so owned tickers are inherently first.
  3. Drives the existing ``FullPortfolioAnalystRefreshAdapter`` OUTSIDE the HTTP
     request. That adapter wraps the existing ``AgentOrchestrator`` path, which
     persists ``recommendations`` / ``agent_insights`` through the existing
     durable write path.
  4. Records per-ticker outcome: successful tickers' jobs are marked succeeded;
     failed tickers' jobs stay ``failed`` with an exponential-backoff retry —
     never a fabricated success, so failed refreshes do not fabricate freshness.
  5. After a timeout, performs a supplemental DB check for any evidence committed
     before the cancellation, so tickers that actually succeeded are not retried.

Hard boundary: this module must NOT import the deterministic Intel v3 decision
policy. The worker refreshes analyst *evidence* only. Visible Buy/Hold/Trim/Sell
authority stays with the synchronous deterministic policy — Stage 3.2 enforces
this with a backend test that greps this module.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional
from uuid import UUID

from .analyst_refresh_adapter_v1 import (
    STATUS_SKIPPED_TIMEOUT,
    TickerPriorityHint,
    prioritize_stale_tickers,
)
from .analyst_refresh_job_store_v1 import (
    AnalystRefreshJob,
    claim_due_jobs,
    count_due_jobs,
    mark_job_failed,
    mark_job_succeeded,
)
from .full_portfolio_analyst_refresh_adapter_v1 import (
    FullPortfolioAnalystRefreshAdapter,
    FullPortfolioAnalystRefreshBudget,
    default_full_portfolio_agent_orchestrator_backend,
)

logger = logging.getLogger(__name__)


# ── Worker budgets ────────────────────────────────────────────────────────────

# Upper safety limits for one worker pass. The adapter enforces its own
# per-call LLM/ticker budget; these cap how much one run claims + how long it
# may take before releasing remaining work back for a later pass.
DEFAULT_MAX_JOBS_PER_RUN = 60
DEFAULT_MAX_RUNTIME_SECONDS = 240.0


# An analyst-refresh callable matches the adapter contract:
#   async __call__(tickers, *, priority_hints, started_at) -> result-with-to_dict
AnalystAdapterFactory = Callable[[UUID], Any]


def _default_adapter_factory(user_id: UUID) -> FullPortfolioAnalystRefreshAdapter:
    """Build the production analyst-refresh adapter for one user.

    Uses the existing full-portfolio adapter + its default AgentOrchestrator
    backend, which persists ``recommendations`` / ``agent_insights`` through the
    existing durable write path. Per-ticker success is verified from real DB
    rows by the adapter — no fabricated freshness.
    """
    return FullPortfolioAnalystRefreshAdapter(
        user_id=user_id,
        run_backend=default_full_portfolio_agent_orchestrator_backend,
        budget=FullPortfolioAnalystRefreshBudget(),
    )


# ── Result accounting ─────────────────────────────────────────────────────────

@dataclass
class WorkerRunResult:
    """Observable outcome of one ``run_once`` pass.

    Fields added in Build 1 (durable resumable execution):
      jobs_due                 — claimable jobs counted before claiming, so
                                 production logs show portfolio-level backlog.
      failed_retryable_tickers — failed tickers still within their attempt
                                 budget; will be retried in a later poll.
      failed_terminal_tickers  — failed tickers whose attempt budget is
                                 exhausted; permanently block certification.
      timed_out_before_completion — True when the worker's own runtime budget
                                 ran out before all users were processed.
      remaining_pending_or_retryable — retryable failures from this run that
                                 will be picked up in a later iteration.
      run_resumable            — True when retryable failures remain and more
                                 worker iterations can make progress.
    """
    worker_run_id: str
    claimed_job_count: int = 0
    jobs_due: int = 0
    selected_tickers: list[str] = field(default_factory=list)
    succeeded_tickers: list[str] = field(default_factory=list)
    failed_tickers: list[str] = field(default_factory=list)
    failed_retryable_tickers: list[str] = field(default_factory=list)
    failed_terminal_tickers: list[str] = field(default_factory=list)
    attempted_llm_calls: int = 0
    successful_llm_calls: int = 0
    failed_llm_calls: int = 0
    persisted_ticker_success_count: int = 0
    timed_out_before_completion: bool = False
    remaining_pending_or_retryable: int = 0
    run_resumable: bool = True
    duration_ms: int = 0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "worker_run_id": self.worker_run_id,
            "jobs_due": self.jobs_due,
            "claimed_job_count": self.claimed_job_count,
            "selected_tickers": list(self.selected_tickers),
            "succeeded_tickers": list(self.succeeded_tickers),
            "failed_tickers": list(self.failed_tickers),
            "failed_retryable_tickers": list(self.failed_retryable_tickers),
            "failed_terminal_tickers": list(self.failed_terminal_tickers),
            "attempted_llm_calls": self.attempted_llm_calls,
            "successful_llm_calls": self.successful_llm_calls,
            "failed_llm_calls": self.failed_llm_calls,
            "persisted_ticker_success_count": self.persisted_ticker_success_count,
            "timed_out_before_completion": self.timed_out_before_completion,
            "remaining_pending_or_retryable": self.remaining_pending_or_retryable,
            "run_resumable": self.run_resumable,
            "duration_ms": self.duration_ms,
            "notes": list(self.notes),
        }


def _failure_reason_for(per_ticker: list[dict[str, Any]], ticker: str) -> Optional[str]:
    for outcome in per_ticker or []:
        if str(outcome.get("ticker") or "").upper() == ticker:
            return outcome.get("error_reason")
    return None


def _rows_from_result(res: Any) -> list[dict[str, Any]]:
    """Extract row list from a Supabase result — safe against mocked clients."""
    data = getattr(res, "data", None)
    return data if isinstance(data, list) else []


# ── Worker ────────────────────────────────────────────────────────────────────

class AnalystRefreshWorker:
    """Consumes durable analyst refresh jobs outside the HTTP request.

    Usage (single pass — manual validation / cron tick):
        worker = AnalystRefreshWorker(client=get_supabase_client())
        result = await worker.run_once()

    The ``adapter_factory`` is injectable so tests can drive the worker without
    standing up a real AgentOrchestrator / LLM provider.
    """

    def __init__(
        self,
        *,
        client: Any,
        adapter_factory: Optional[AnalystAdapterFactory] = None,
        max_jobs_per_run: int = DEFAULT_MAX_JOBS_PER_RUN,
        max_runtime_seconds: float = DEFAULT_MAX_RUNTIME_SECONDS,
    ):
        self.client = client
        self._adapter_factory = adapter_factory or _default_adapter_factory
        self.max_jobs_per_run = max_jobs_per_run
        self.max_runtime_seconds = max_runtime_seconds

    async def run_once(self, *, now: Optional[datetime] = None) -> WorkerRunResult:
        """Claim and process one batch of due analyst refresh jobs."""
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        worker_run_id = str(uuid.uuid4())
        started = time.monotonic()
        result = WorkerRunResult(worker_run_id=worker_run_id)

        # Count claimable jobs before claiming so the log reports full backlog.
        due_counts = count_due_jobs(self.client, now=now)
        result.jobs_due = due_counts.get("total_due", 0)

        claimed = claim_due_jobs(
            self.client,
            worker_run_id=worker_run_id,
            now=now,
            limit=self.max_jobs_per_run,
        )
        result.claimed_job_count = len(claimed)

        if not claimed:
            result.run_resumable = due_counts.get("failed_not_yet_due", 0) > 0
            result.duration_ms = int((time.monotonic() - started) * 1000)
            logger.info(
                "intel_v3.analyst_refresh_worker_run_summary worker_run_id=%s "
                "jobs_due=%d claimed=0 selected=0 succeeded=0 "
                "failed_retryable=0 failed_terminal=0 "
                "attempted_llm_calls=0 successful_llm_calls=0 failed_llm_calls=0 "
                "persisted_ticker_success_count=0 "
                "timed_out_before_completion=False "
                "remaining_pending_or_retryable=0 run_resumable=%s "
                "duration_ms=%d notes=none",
                worker_run_id,
                result.jobs_due,
                result.run_resumable,
                result.duration_ms,
            )
            return result

        # Every claimed job is already an owned position; grouping by user keeps
        # one full-portfolio adapter pass per user.
        jobs_by_user: dict[str, list[AnalystRefreshJob]] = {}
        for job in claimed:
            jobs_by_user.setdefault(job.user_id, []).append(job)

        for user_id, jobs in jobs_by_user.items():
            elapsed = time.monotonic() - started
            if elapsed >= self.max_runtime_seconds:
                # Out of runtime budget — release this user's claimed jobs back
                # to a retryable failed state. No fabricated freshness: these
                # tickers stay stale until a later worker pass.
                result.timed_out_before_completion = True
                for job in jobs:
                    next_retry = mark_job_failed(
                        self.client, job,
                        error="worker_runtime_budget_exhausted", now=now,
                    )
                    result.failed_tickers.append(job.ticker)
                    exhausted = job.attempts >= job.max_attempts
                    if exhausted:
                        result.failed_terminal_tickers.append(job.ticker)
                    else:
                        result.failed_retryable_tickers.append(job.ticker)
                    logger.info(
                        "intel_v3.analyst_refresh_worker_ticker_failed "
                        "worker_run_id=%s user_id=%s ticker=%s "
                        "reason=worker_runtime_budget_exhausted next_retry_at=%s "
                        "attempts=%d terminal=%s",
                        worker_run_id, user_id, job.ticker, next_retry, job.attempts,
                        exhausted,
                    )
                result.notes.append("worker_runtime_budget_exhausted")
                continue
            await self._refresh_user_jobs(
                user_id, jobs, now=now, result=result, worker_run_id=worker_run_id,
            )

        # Remaining = retryable failures from this run that future polls can pick up.
        result.remaining_pending_or_retryable = len(result.failed_retryable_tickers)
        result.run_resumable = result.remaining_pending_or_retryable > 0

        result.duration_ms = int((time.monotonic() - started) * 1000)
        logger.info(
            "intel_v3.analyst_refresh_worker_run_summary worker_run_id=%s "
            "jobs_due=%d claimed=%d selected=%d succeeded=%d "
            "failed_retryable=%d failed_terminal=%d "
            "attempted_llm_calls=%d successful_llm_calls=%d failed_llm_calls=%d "
            "persisted_ticker_success_count=%d "
            "timed_out_before_completion=%s "
            "remaining_pending_or_retryable=%d run_resumable=%s "
            "duration_ms=%d notes=%s",
            worker_run_id,
            result.jobs_due,
            result.claimed_job_count,
            len(result.selected_tickers),
            len(result.succeeded_tickers),
            len(result.failed_retryable_tickers),
            len(result.failed_terminal_tickers),
            result.attempted_llm_calls,
            result.successful_llm_calls,
            result.failed_llm_calls,
            result.persisted_ticker_success_count,
            result.timed_out_before_completion,
            result.remaining_pending_or_retryable,
            result.run_resumable,
            result.duration_ms,
            ",".join(result.notes) if result.notes else "none",
        )
        return result

    async def _refresh_user_jobs(
        self,
        user_id: str,
        jobs: list[AnalystRefreshJob],
        *,
        now: datetime,
        result: WorkerRunResult,
        worker_run_id: str,
    ) -> None:
        """Run one full-portfolio analyst refresh pass for a single user."""
        job_by_ticker: dict[str, AnalystRefreshJob] = {j.ticker: j for j in jobs}
        hints = [
            TickerPriorityHint(
                ticker=j.ticker,
                prior_action=j.prior_action,
                weight_pct=j.weight_pct,
                evidence_age_hours=j.evidence_age_hours_at_request,
            )
            for j in jobs
        ]
        # Owned BUY/TRIM first, then weight, then evidence age, then ticker A→Z.
        ranked = prioritize_stale_tickers(hints)
        selected = [h.ticker for h in ranked]
        result.selected_tickers.extend(selected)
        logger.info(
            "intel_v3.analyst_refresh_worker_tickers_selected worker_run_id=%s "
            "user_id=%s ticker_count=%d tickers=%s",
            worker_run_id, user_id, len(selected), ",".join(selected),
        )

        try:
            adapter = self._adapter_factory(UUID(str(user_id)))
        except Exception as exc:
            logger.warning(
                "intel_v3.analyst_refresh_worker_adapter_build_failed "
                "worker_run_id=%s user_id=%s err=%s",
                worker_run_id, user_id, exc,
            )
            self._fail_all(
                jobs, error=f"adapter_build_error:{type(exc).__name__}",
                now=now, result=result, worker_run_id=worker_run_id, user_id=user_id,
            )
            result.notes.append(f"adapter_build_error:{type(exc).__name__}")
            return

        try:
            refresh = await adapter(selected, priority_hints=hints, started_at=now)
        except Exception as exc:
            # The adapter is contracted not to raise, but the worker must never
            # crash on a misbehaving adapter — fail every ticker honestly.
            logger.warning(
                "intel_v3.analyst_refresh_worker_adapter_failed worker_run_id=%s "
                "user_id=%s err=%s",
                worker_run_id, user_id, exc,
            )
            self._fail_all(
                jobs, error=f"adapter_error:{type(exc).__name__}",
                now=now, result=result, worker_run_id=worker_run_id, user_id=user_id,
            )
            result.notes.append(f"adapter_error:{type(exc).__name__}")
            return

        refresh_dict = (
            refresh.to_dict() if hasattr(refresh, "to_dict")
            else dict(refresh) if isinstance(refresh, dict)
            else {}
        )
        result.attempted_llm_calls += int(refresh_dict.get("attempted_llm_calls") or 0)
        result.successful_llm_calls += int(refresh_dict.get("successful_llm_calls") or 0)
        result.failed_llm_calls += int(refresh_dict.get("failed_llm_calls") or 0)

        per_ticker = list(refresh_dict.get("per_ticker") or [])
        success_set = {
            str(o.get("ticker") or "").upper()
            for o in per_ticker
            if o.get("success") is True
        }
        refresh_status = refresh_dict.get("status") or "unknown"

        # Post-timeout residual evidence check.
        #
        # When asyncio.wait_for() cancels the backend coroutine on timeout, any
        # asyncio.to_thread() DB writes inside it may still complete in their
        # OS threads. Evidence committed before cancellation propagated would
        # otherwise be re-processed on the next retry. Check the DB for any
        # agent_insights rows written since the run started, and mark those
        # tickers as succeeded so they are not retried unnecessarily. This is
        # the core "resumable" fix: partial evidence is not discarded on timeout.
        is_timeout = refresh_status == STATUS_SKIPPED_TIMEOUT
        if is_timeout and not success_set:
            residual = await self._check_residual_evidence(
                user_id=user_id,
                tickers=list(job_by_ticker.keys()),
                started_at=now,
            )
            if residual:
                success_set = residual
                result.notes.append(
                    f"post_timeout_residual_evidence_found_{len(residual)}"
                )

        # Partial success persists per ticker. A ticker only counts as a real
        # refresh when the adapter verified it from durable DB rows.
        for ticker, job in job_by_ticker.items():
            if ticker in success_set:
                mark_job_succeeded(self.client, job, now=now)
                result.succeeded_tickers.append(ticker)
                result.persisted_ticker_success_count += 1
                logger.info(
                    "intel_v3.analyst_refresh_worker_ticker_succeeded "
                    "worker_run_id=%s user_id=%s ticker=%s",
                    worker_run_id, user_id, ticker,
                )
            else:
                reason = (
                    _failure_reason_for(per_ticker, ticker)
                    or f"no_refresh_evidence:{refresh_status}"
                )
                next_retry = mark_job_failed(
                    self.client, job, error=str(reason), now=now,
                )
                result.failed_tickers.append(ticker)
                exhausted = job.attempts >= job.max_attempts
                if exhausted:
                    result.failed_terminal_tickers.append(ticker)
                else:
                    result.failed_retryable_tickers.append(ticker)
                logger.info(
                    "intel_v3.analyst_refresh_worker_ticker_failed worker_run_id=%s "
                    "user_id=%s ticker=%s reason=%s next_retry_at=%s "
                    "attempts=%d terminal=%s",
                    worker_run_id, user_id, ticker, reason, next_retry,
                    job.attempts, exhausted,
                )

    def _fail_all(
        self,
        jobs: list[AnalystRefreshJob],
        *,
        error: str,
        now: datetime,
        result: WorkerRunResult,
        worker_run_id: str,
        user_id: str,
    ) -> None:
        """Mark every job in the batch failed with a retry — no fabricated freshness."""
        for job in jobs:
            next_retry = mark_job_failed(self.client, job, error=error, now=now)
            result.failed_tickers.append(job.ticker)
            exhausted = job.attempts >= job.max_attempts
            if exhausted:
                result.failed_terminal_tickers.append(job.ticker)
            else:
                result.failed_retryable_tickers.append(job.ticker)
            logger.info(
                "intel_v3.analyst_refresh_worker_ticker_failed worker_run_id=%s "
                "user_id=%s ticker=%s reason=%s next_retry_at=%s "
                "attempts=%d terminal=%s",
                worker_run_id, user_id, job.ticker, error, next_retry,
                job.attempts, exhausted,
            )

    async def _check_residual_evidence(
        self,
        *,
        user_id: str,
        tickers: list[str],
        started_at: datetime,
    ) -> set[str]:
        """After a timeout, check which tickers have fresh evidence in the DB.

        When asyncio.wait_for() cancels the backend coroutine, DB writes that
        ran inside asyncio.to_thread() may still complete in their OS threads.
        This query surfaces tickers whose evidence was committed before or
        shortly after the cancellation so they can be marked succeeded instead
        of retried unnecessarily.

        Returns the subset of ``tickers`` (upper-cased) that have a fresh
        ``agent_insights`` row with ``created_at >= started_at``. Returns an
        empty set on DB failure — the caller falls back to marking all as failed.
        """
        started_iso = started_at.isoformat()
        upper_tickers = {t.upper() for t in tickers}
        try:
            res = await asyncio.to_thread(
                lambda: self.client.table("agent_insights")
                .select("ticker")
                .eq("user_id", user_id)
                .gte("created_at", started_iso)
                .execute()
            )
            rows = _rows_from_result(res)
            found = {
                str(r.get("ticker") or "").upper()
                for r in rows
                if str(r.get("ticker") or "").upper() in upper_tickers
            }
            if found:
                logger.info(
                    "intel_v3.analyst_refresh_worker_residual_check user_id=%s "
                    "residual_tickers=%s",
                    user_id, ",".join(sorted(found)),
                )
            return found
        except Exception as exc:
            logger.warning(
                "intel_v3.analyst_refresh_worker_residual_check_failed "
                "user_id=%s err=%s",
                user_id, exc,
            )
            return set()
