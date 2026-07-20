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

from .analyst_finalization_v1 import (
    FINALIZATION_COMPLETED,
    FinalizationResult,
    run_finalization_if_ready,
)
from .analyst_refresh_worker_v1 import AnalystRefreshWorker

logger = logging.getLogger(__name__)

# Batch sizing (Run Intel v3 ticker/finalization split).
#
# Now that portfolio synthesis is NO LONGER run inside a ticker batch (it moved
# to the one-shot finalization pass), the whole per-request 20s budget belongs
# to bounded per-ticker analyst work. Production proved that 3 ticker analyses
# already completed comfortably before the 20s deadline — only the trailing
# synthesis overran — so removing synthesis frees the window to safely double
# the batch to 8 tickers while keeping each request well under 20s.
#
# Continuation-budget proof for the full 32-holding portfolio:
#   * ceil(32 / 8) = 4 ticker batches; finalization runs in the same call as the
#     last ticker batch, so 4 requests drain + certify (a finalization-only retry
#     adds at most 1 more).
#   * Frontend caps (advisor-readiness.ts): RUN_INTEL_MAX_CONTINUATIONS=20,
#     RUN_INTEL_MAX_ELAPSED_MS=120_000.
#   * Each request is code-bounded to ~20s: ticker work by max_adapter_seconds
#     (the worker's wait_for), and the finalization synthesis by its own
#     independent budget (analyst_finalization_v1.DEFAULT_MAX_FINALIZATION_SYNTHESIS_SECONDS,
#     also 20s). So even in the pathological case where every request runs to its
#     full bound, 5 * 20s = 100s <= 120s elapsed cap — an ENFORCED bound, not a
#     model. Certification (prewarm) is deterministic + fast (DB reads + decide()),
#     so it does not materially extend the finalization request.
#
# max_runtime_seconds is also threaded into AnalystRefreshWorker as its
# per-call adapter deadline (max_adapter_seconds), so the underlying
# analyst-refresh call's own wait_for() is bounded to this same cap rather
# than the adapter's much larger 180s default — see module docstring.
MAX_BATCHES_PER_RUN = 1
MAX_JOBS_PER_BATCH = 8
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
    # Finalization (Phase 2) — populated only when no ticker work remains and
    # finalization was attempted this call.
    finalization_ran: bool = False
    finalization_status: Optional[str] = None
    synthesis_llm_calls: int = 0
    certified_snapshot_id: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "batches_run": self.batches_run,
            "jobs_attempted": self.jobs_attempted,
            "jobs_succeeded": self.jobs_succeeded,
            "jobs_failed": self.jobs_failed,
            "duration_ms": self.duration_ms,
            "run_resumable": self.run_resumable,
            "stopped_reason": self.stopped_reason,
            "finalization_ran": self.finalization_ran,
            "finalization_status": self.finalization_status,
            "synthesis_llm_calls": self.synthesis_llm_calls,
            "certified_snapshot_id": self.certified_snapshot_id,
        }


# Finalization seam — async (user_id, client, tickers) -> FinalizationResult.
FinalizerFn = Any


async def run_on_demand_drain(
    *,
    user_id: "UUID | str",
    client: Any,
    tickers: Optional[list[str]] = None,
    worker: Optional[AnalystRefreshWorker] = None,
    max_batches: int = MAX_BATCHES_PER_RUN,
    max_jobs_per_batch: int = MAX_JOBS_PER_BATCH,
    max_runtime_seconds: float = MAX_RUNTIME_SECONDS,
    adapter_factory: Optional[Any] = None,
    finalizer: Optional[FinalizerFn] = None,
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
        adapter_factory=adapter_factory,
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

    # ── Phase 2: one finalization pass ────────────────────────────────────────
    # Only when no ticker work remains resumable this call — either the final
    # ticker batch just drained, or this was a finalization-only continuation
    # (zero ticker jobs claimed, all already succeeded). ``run_finalization_if_ready``
    # is itself gated: it no-ops unless every active-ticker job has succeeded and
    # no certified-current snapshot exists yet, and it runs synthesis exactly once
    # with its own budget. Skipped when a ``worker`` was injected (unit tests that
    # script batch results drive finalization directly instead).
    if worker is None and not result.run_resumable:
        active_finalizer = finalizer or run_finalization_if_ready
        try:
            fin: FinalizationResult = await active_finalizer(
                user_id=user_id, client=client, tickers=tickers,
            )
            result.finalization_ran = True
            result.finalization_status = fin.status
            result.synthesis_llm_calls = fin.synthesis_llm_calls
            if fin.certified:
                result.certified_snapshot_id = fin.published_snapshot_id
            # A pending/failed finalization keeps the run resumable so the
            # frontend fires another (finalization-only) continuation.
            if fin.status != FINALIZATION_COMPLETED and fin.status != "already_certified":
                result.run_resumable = True
        except Exception as exc:  # never fail the drain on finalization
            logger.warning(
                "intel_v3.on_demand_drain_finalization_failed user_id=%s err=%s",
                user_id, exc,
            )
            result.finalization_ran = True
            result.finalization_status = "failed_retryable"
            result.run_resumable = True

    result.duration_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "intel_v3.on_demand_drain_summary user_id=%s batches_run=%d "
        "jobs_attempted=%d jobs_succeeded=%d jobs_failed=%d "
        "run_resumable=%s stopped_reason=%s finalization_ran=%s "
        "finalization_status=%s synthesis_llm_calls=%d duration_ms=%d",
        user_id,
        result.batches_run,
        result.jobs_attempted,
        result.jobs_succeeded,
        result.jobs_failed,
        result.run_resumable,
        result.stopped_reason,
        result.finalization_ran,
        result.finalization_status,
        result.synthesis_llm_calls,
        result.duration_ms,
    )
    return result
