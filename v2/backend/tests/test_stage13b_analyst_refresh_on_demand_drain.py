"""Stage 13B — bounded on-demand analyst-refresh drain.

Contract under test (analyst_refresh_on_demand_drain_v1.run_on_demand_drain):
  * Reuses AnalystRefreshWorker.run_once() per batch — no new job-processing
    logic, no fabricated freshness.
  * Bounded by max_batches, max_runtime_seconds, and max_jobs_per_batch —
    never loops forever waiting for more work.
  * Stops as soon as a batch claims zero jobs (nothing left to drain right
    now) or the worker reports run_resumable=False (fully drained).
  * Aggregates jobs_attempted/succeeded/failed across all batches that ran.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from app.services.intelligence.v3.analyst_refresh_on_demand_drain_v1 import (
    MAX_BATCHES_PER_RUN,
    STOPPED_DRAINED,
    STOPPED_MAX_BATCHES_REACHED,
    STOPPED_NO_MORE_CLAIMABLE_JOBS,
    STOPPED_RUNTIME_CAP_REACHED,
    OnDemandDrainResult,
    run_on_demand_drain,
)
from app.services.intelligence.v3.analyst_refresh_worker_v1 import WorkerRunResult

USER_ID = uuid.UUID("00000000-0000-0000-0000-0000000000aa")


def _batch(
    *,
    claimed: int,
    succeeded: list[str] | None = None,
    failed: list[str] | None = None,
    resumable: bool,
) -> WorkerRunResult:
    return WorkerRunResult(
        worker_run_id=str(uuid.uuid4()),
        claimed_job_count=claimed,
        succeeded_tickers=list(succeeded or []),
        failed_tickers=list(failed or []),
        run_resumable=resumable,
    )


class _FakeWorker:
    """Stands in for AnalystRefreshWorker — returns a scripted batch sequence."""

    def __init__(self, batches: list[WorkerRunResult]):
        self._batches = batches
        self.run_once = AsyncMock(side_effect=self._next)
        self.call_count = 0

    async def _next(self, *_a, **_kw) -> WorkerRunResult:
        self.call_count += 1
        return self._batches[self.call_count - 1]


class TestBoundedDrainStopsHonestly:
    @pytest.mark.asyncio
    async def test_stops_when_a_batch_claims_zero_jobs(self):
        worker = _FakeWorker([_batch(claimed=0, resumable=True)])
        result = await run_on_demand_drain(user_id=USER_ID, client=object(), worker=worker)
        assert result.batches_run == 1
        assert result.jobs_attempted == 0
        assert result.stopped_reason == STOPPED_NO_MORE_CLAIMABLE_JOBS
        assert worker.call_count == 1

    @pytest.mark.asyncio
    async def test_stops_when_worker_reports_fully_drained(self):
        worker = _FakeWorker(
            [_batch(claimed=10, succeeded=["AAPL"] * 10, resumable=False)]
        )
        result = await run_on_demand_drain(user_id=USER_ID, client=object(), worker=worker)
        assert result.batches_run == 1
        assert result.jobs_succeeded == 10
        assert result.stopped_reason == STOPPED_DRAINED
        assert result.run_resumable is False

    @pytest.mark.asyncio
    async def test_aggregates_counts_across_multiple_batches(self):
        """Aggregation logic across batches, exercised with an explicit
        max_batches override — the production default is intentionally a
        single small batch per request (Part A2); a caller that wants more
        than one batch in a single call still gets correct aggregation."""
        worker = _FakeWorker(
            [
                _batch(claimed=10, succeeded=["AAPL"] * 8, failed=["TSLA", "NVDA"], resumable=True),
                _batch(claimed=10, succeeded=["MSFT"] * 9, failed=["GOOG"], resumable=False),
            ]
        )
        result = await run_on_demand_drain(
            user_id=USER_ID, client=object(), worker=worker, max_batches=2,
        )
        assert result.batches_run == 2
        assert result.jobs_attempted == 20
        assert result.jobs_succeeded == 17
        assert result.jobs_failed == 3
        assert result.stopped_reason == STOPPED_DRAINED


class TestBoundedDrainNeverLoopsForever:
    @pytest.mark.asyncio
    async def test_caps_at_max_batches_even_when_always_resumable(self):
        """Regression: a worker that always reports work remaining must never
        make the drain loop indefinitely — max_batches is a hard ceiling."""
        batches = [_batch(claimed=10, resumable=True) for _ in range(50)]
        worker = _FakeWorker(batches)
        result = await run_on_demand_drain(
            user_id=USER_ID, client=object(), worker=worker, max_batches=3,
        )
        assert result.batches_run == 3
        assert worker.call_count == 3
        assert result.stopped_reason == STOPPED_MAX_BATCHES_REACHED
        assert result.run_resumable is True  # honest: more work remains

    @pytest.mark.asyncio
    async def test_default_max_batches_is_a_small_bounded_constant(self):
        assert MAX_BATCHES_PER_RUN <= 5

    @pytest.mark.asyncio
    async def test_respects_runtime_cap_between_batches(self):
        """Simulate a batch that takes longer than the runtime cap by having
        the fake worker sleep — the drain must not start a further batch."""
        import asyncio

        class _SlowWorker(_FakeWorker):
            async def _next(self, *a, **kw):
                await asyncio.sleep(0.05)
                return await super()._next(*a, **kw)

        batches = [_batch(claimed=10, resumable=True) for _ in range(10)]
        worker = _SlowWorker(batches)
        result = await run_on_demand_drain(
            user_id=USER_ID,
            client=object(),
            worker=worker,
            max_batches=10,
            max_runtime_seconds=0.06,
        )
        # At least one batch ran, but the 0.05s-per-batch pace plus the
        # runtime cap of 0.06s must stop the loop well short of 10 batches.
        assert 1 <= result.batches_run < 10
        assert result.stopped_reason == STOPPED_RUNTIME_CAP_REACHED


class TestOnDemandDrainResultShape:
    def test_to_dict_contains_all_fields(self):
        result = OnDemandDrainResult(
            batches_run=2, jobs_attempted=20, jobs_succeeded=17, jobs_failed=3,
            duration_ms=123, run_resumable=False, stopped_reason=STOPPED_DRAINED,
        )
        d = result.to_dict()
        assert d == {
            "batches_run": 2,
            "jobs_attempted": 20,
            "jobs_succeeded": 17,
            "jobs_failed": 3,
            "duration_ms": 123,
            "run_resumable": False,
            "stopped_reason": STOPPED_DRAINED,
        }

    @pytest.mark.asyncio
    async def test_produces_a_real_worker_when_none_injected(self, monkeypatch):
        """Production path: no worker injected -> builds a real bounded
        AnalystRefreshWorker scoped to the drain's own caps, not the
        standalone worker's larger 240s/10-job defaults reused blindly. Also
        scoped to the requesting user and the on-demand deadline, so this
        drain can never claim another user's jobs or let a single analyst
        call outrun the caller's intended bound (Part A1/A2)."""
        from app.services.intelligence.v3 import analyst_refresh_on_demand_drain_v1 as mod

        built = {}

        class _CapturingWorker:
            def __init__(
                self, *, client, max_jobs_per_run, max_runtime_seconds,
                scope_user_id=None, scope_tickers=None, max_adapter_seconds=None,
                scope_run_session_id=None, trigger_prewarm=True,
            ):
                built["client"] = client
                built["max_jobs_per_run"] = max_jobs_per_run
                built["max_runtime_seconds"] = max_runtime_seconds
                built["scope_user_id"] = scope_user_id
                built["scope_tickers"] = scope_tickers
                built["max_adapter_seconds"] = max_adapter_seconds
                built["scope_run_session_id"] = scope_run_session_id
                built["trigger_prewarm"] = trigger_prewarm

            async def run_once(self, *_a, **_kw):
                return _batch(claimed=0, resumable=True)

        monkeypatch.setattr(mod, "AnalystRefreshWorker", _CapturingWorker)
        fake_client = object()
        result = await run_on_demand_drain(
            user_id=USER_ID, client=fake_client, tickers=["VTI", "AAPL"],
        )
        assert built["client"] is fake_client
        assert built["max_jobs_per_run"] == mod.MAX_JOBS_PER_BATCH
        assert built["max_runtime_seconds"] == mod.MAX_RUNTIME_SECONDS
        assert built["scope_user_id"] == USER_ID
        assert built["scope_tickers"] == ["VTI", "AAPL"]
        assert built["max_adapter_seconds"] == mod.MAX_RUNTIME_SECONDS
        assert result.stopped_reason == STOPPED_NO_MORE_CLAIMABLE_JOBS

    @pytest.mark.asyncio
    async def test_default_quantum_is_production_safe_and_small(self):
        """Part A2: the on-demand quantum must be small enough that ONE
        request cannot materially exceed a production-safe wall-clock bound,
        even in the worst case — regression guard against the ~148s hang
        (MAX_BATCHES_PER_RUN=3 x MAX_JOBS_PER_BATCH=10 x a 180s adapter
        default that ignored the caller's intended cap)."""
        from app.services.intelligence.v3 import analyst_refresh_on_demand_drain_v1 as mod

        assert mod.MAX_BATCHES_PER_RUN == 1
        assert mod.MAX_JOBS_PER_BATCH <= 5
        assert mod.MAX_RUNTIME_SECONDS <= 30.0
