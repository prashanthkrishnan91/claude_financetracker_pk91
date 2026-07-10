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
  * ``max_runtime_seconds`` overall wall-clock cap.
Stops as soon as a batch claims zero jobs or the worker reports nothing left
to resume — never loops waiting for more work to appear.

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

# Small enough to fit inside one HTTP request's timeout, large enough to
# clear a typical portfolio (34 holdings / 10 per batch ≈ 4 batches) across
# 1-2 clicks. AnalystRefreshWorker's own per-batch runtime cap still applies
# on top of this.
MAX_BATCHES_PER_RUN = 3
MAX_JOBS_PER_BATCH = 10
MAX_RUNTIME_SECONDS = 90.0

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
    worker: Optional[AnalystRefreshWorker] = None,
    max_batches: int = MAX_BATCHES_PER_RUN,
    max_jobs_per_batch: int = MAX_JOBS_PER_BATCH,
    max_runtime_seconds: float = MAX_RUNTIME_SECONDS,
) -> OnDemandDrainResult:
    """Drain the durable job queue for a bounded number of batches.

    ``worker`` is injectable for tests; production callers omit it and get a
    real ``AnalystRefreshWorker`` scoped to the given batch/runtime caps.

    The queue is global (not per-user — same as the standalone worker), so a
    drain triggered by one user's click may also process other users' due
    jobs. That matches existing ``AnalystRefreshWorker`` behavior and is not
    a new risk introduced here.
    """
    active_worker = worker or AnalystRefreshWorker(
        client=client,
        max_jobs_per_run=max_jobs_per_batch,
        max_runtime_seconds=max_runtime_seconds,
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
