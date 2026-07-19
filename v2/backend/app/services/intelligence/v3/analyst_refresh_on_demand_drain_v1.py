"""Stage 13B — bounded on-demand analyst-refresh drain.

Reuses ``AnalystRefreshWorker.run_once()`` (Stage 3.2) to process a bounded
number of ``analyst_refresh_jobs`` synchronously inside the ``POST
/intel/v3/run`` HTTP request, so an explicit manual click can produce a
certified snapshot without requiring the separate always-on
``analyst_refresh_worker_v1`` Railway worker service.

Hard bounds (no infinite loop, no always-on polling):
  * ``max_batches`` worker batches per call (each batch itself claims at most
    ``max_jobs_per_batch`` jobs — same claim/backoff/retry semantics as the
    standalone worker).
  * ``max_runtime_seconds`` overall wall-clock cap, also threaded into the
    worker as its per-call adapter deadline (see ``max_adapter_seconds`` on
    ``AnalystRefreshWorker``) so a single in-flight analyst-refresh call
    cannot silently run to the adapter's own, much larger, default budget.
    Production evidence: prior to this bound being threaded through, one
    batch could block for the adapter's full 180s default regardless of the
    on-demand cap the caller intended, turning a nominal 90s cap into a
    ~148s+ hung HTTP request.
Stops as soon as a batch claims zero jobs or the worker reports nothing left
to resume — never loops waiting for more work to appear.

Scoping: every call always scopes claiming to the requesting ``user_id``
(and, when supplied, their current active ``tickers``) — an on-demand drain
triggered by one user's explicit click must never claim or process another
user's durable jobs, even though the underlying queue table is shared with
the standalone always-on worker (which keeps its own unscoped, global
claiming behavior — unchanged).

This module must NOT import the deterministic Intel v3 decision policy —
same boundary as ``analyst_refresh_worker_v1``. It only drives the existing
evidence-refresh worker; it never decides Buy/Hold/Trim/Sell.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Optional
from uuid import UUID

from .analyst_refresh_worker_v1 import AnalystRefreshWorker

logger = logging.getLogger(__name__)

# Small enough that ONE HTTP request can never materially exceed a
# production-safe wall-clock bound, even in the worst case where every
# selected ticker's LLM call runs to the full deadline. A 34-holding
# portfolio needs many bounded clicks/continuations (see Part A3's automatic
# bounded continuation) rather than draining in 1-2 requests — trading a
# single ~148s hang for many fast, resumable, ~20s-bounded requests.
#
# max_runtime_seconds is also threaded into AnalystRefreshWorker as its
# per-call adapter deadline (max_adapter_seconds), so the underlying
# analyst-refresh call's own wait_for() is bounded to this same cap rather
# than the adapter's much larger 180s default — see module docstring.
MAX_BATCHES_PER_RUN = 1
MAX_JOBS_PER_BATCH = 3
MAX_RUNTIME_SECONDS = 20.0

STOPPED_DRAINED = "drained"
STOPPED_NO_MORE_CLAIMABLE_JOBS = "no_more_claimable_jobs"
STOPPED_RUNTIME_CAP_REACHED = "runtime_cap_reached"
STOPPED_MAX_BATCHES_REACHED = "max_batches_reached"
STOPPED_NOT_RUN = "not_run"


@dataclass
class OnDemandDrainResult:
    """Observable outcome of one bounded on-demand drain call."""

    batches_run: int = 0
    jobs_attempted: int = 0
    jobs_succeeded: int = 0
    jobs_failed: int = 0
    duration_ms: int = 0
    run_resumable: bool = False
    stopped_reason: str = STOPPED_NOT_RUN

    def to_dict(self) -> dict[str, Any]:
        return {
            "batches_run": self.batches_run,
            "jobs_attempted": self.jobs_attempted,
            "jobs_succeeded": self.jobs_succeeded,
            "jobs_failed": self.jobs_failed,
            "duration_ms": self.duration_ms,
            "run_resumable": self.run_resumable,
            "stopped_reason": self.stopped_reason,
        }


async def run_on_demand_drain(
    *,
    user_id: "UUID | str",
    client: Any,
    tickers: Optional[list[str]] = None,
    worker: Optional[AnalystRefreshWorker] = None,
    max_batches: int = MAX_BATCHES_PER_RUN,
    max_jobs_per_batch: int = MAX_JOBS_PER_BATCH,
    max_runtime_seconds: float = MAX_RUNTIME_SECONDS,
) -> OnDemandDrainResult:
    """Drain the durable job queue for a bounded number of batches.

    ``worker`` is injectable for tests; production callers omit it and get a
    real ``AnalystRefreshWorker`` scoped to ``user_id`` (and ``tickers`` when
    supplied) with the given batch/runtime caps. An explicitly injected
    ``worker`` is used as-is (its own scoping, if any, is the test's
    responsibility) — this keeps existing unit tests that inject a stub
    worker unaffected.

    Scoped to the requesting user: unlike the standalone always-on worker
    (which claims globally across all users), an on-demand drain triggered by
    one user's explicit Run Intel click only ever claims and processes that
    user's own durable jobs.
    """
    active_worker = worker or AnalystRefreshWorker(
        client=client,
        max_jobs_per_run=max_jobs_per_batch,
        max_runtime_seconds=max_runtime_seconds,
        scope_user_id=user_id,
        scope_tickers=tickers,
        max_adapter_seconds=max_runtime_seconds,
    )
    result = OnDemandDrainResult()
    started = time.monotonic()

    for _ in range(max_batches):
        batch = await active_worker.run_once()
        result.batches_run += 1
        result.jobs_attempted += batch.claimed_job_count
        result.jobs_succeeded += len(batch.succeeded_tickers)
        result.jobs_failed += len(batch.failed_tickers)
        result.run_resumable = batch.run_resumable

        if batch.claimed_job_count == 0:
            result.stopped_reason = STOPPED_NO_MORE_CLAIMABLE_JOBS
            break
        if not batch.run_resumable:
            result.stopped_reason = STOPPED_DRAINED
            break
        if (time.monotonic() - started) >= max_runtime_seconds:
            result.stopped_reason = STOPPED_RUNTIME_CAP_REACHED
            break
    else:
        result.stopped_reason = STOPPED_MAX_BATCHES_REACHED

    result.duration_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "intel_v3.on_demand_drain_summary user_id=%s batches_run=%d "
        "jobs_attempted=%d jobs_succeeded=%d jobs_failed=%d "
        "run_resumable=%s stopped_reason=%s duration_ms=%d",
        user_id,
        result.batches_run,
        result.jobs_attempted,
        result.jobs_succeeded,
        result.jobs_failed,
        result.run_resumable,
        result.stopped_reason,
        result.duration_ms,
    )
    return result
