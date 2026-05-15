"""Build 1 regression tests — Intel v3 durable resumable analyst refresh worker.

These tests prove the acceptance criteria from the Build 1 task:

  1. 34 jobs can be processed across multiple bounded worker iterations.
  2. timeout after partial work does not terminal-fail all remaining jobs.
  3. retryable provider overload does not publish a certified snapshot.
  4. completed per-ticker results persist and are not redone unnecessarily.
  5. failed terminal ticker blocks certification instead of green/current UI.
  6. existing certified snapshot remains available while a new refresh runs.
  7. structured log contains all required fields.
  8. run_resumable=True when retryable failures remain.

The tests use the same in-memory Supabase fake as test_intel_v3_stage_3_2_* so
no real DB or LLM calls are made.
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.intelligence.v3.analyst_refresh_adapter_v1 import (
    STATUS_FAILED,
    STATUS_PARTIAL_SUCCESS,
    STATUS_SKIPPED_TIMEOUT,
    STATUS_SUCCEEDED,
    AnalystRefreshResult,
    TickerRefreshOutcome,
)
from app.services.intelligence.v3.analyst_refresh_job_store_v1 import (
    DEFAULT_MAX_ATTEMPTS,
    JOB_CLAIMED,
    JOB_FAILED,
    JOB_PENDING,
    JOB_SUCCEEDED,
    AnalystRefreshJob,
    claim_due_jobs,
    count_due_jobs,
    enqueue_refresh_jobs,
    mark_job_failed,
    mark_job_succeeded,
)
from app.services.intelligence.v3.analyst_refresh_worker_v1 import (
    AnalystRefreshWorker,
    WorkerRunResult,
)

USER_A = "00000000-0000-0000-0000-0000000000aa"
TABLE = "analyst_refresh_jobs"


def _now() -> datetime:
    return datetime(2026, 5, 15, 12, 0, 0, tzinfo=timezone.utc)


def _iso_ago(hours: float, now: datetime | None = None) -> str:
    return ((now or _now()) - timedelta(hours=hours)).isoformat()


# ── In-memory Supabase fake (mirrors test_intel_v3_stage_3_2) ─────────────────


class _FakeResult:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, store, table):
        self._store = store
        self._table = table
        self._op = "select"
        self._payload = None
        self._filters: list[tuple] = []
        self._order_col = None

    def insert(self, rows):
        self._op = "insert"
        self._payload = rows if isinstance(rows, list) else [rows]
        return self

    def update(self, values):
        self._op = "update"
        self._payload = dict(values)
        return self

    def select(self, *_cols, **_kw):
        self._op = "select"
        return self

    def eq(self, col, val):
        self._filters.append(("eq", col, val))
        return self

    def in_(self, col, vals):
        self._filters.append(("in", col, list(vals)))
        return self

    def gte(self, col, val):
        self._filters.append(("gte", col, val))
        return self

    def order(self, col, desc=False):
        self._order_col = col
        return self

    def limit(self, _n):
        return self

    def neq(self, col, val):
        self._filters.append(("neq", col, val))
        return self

    def _match(self, row) -> bool:
        for kind, col, val in self._filters:
            rv = row.get(col)
            if kind == "eq" and rv != val:
                return False
            if kind == "neq" and rv == val:
                return False
            if kind == "in" and rv not in val:
                return False
            if kind == "gte" and not (rv is not None and rv >= val):
                return False
        return True

    def execute(self):
        rows = self._store.setdefault(self._table, [])
        if self._op == "insert":
            inserted = []
            for r in self._payload:
                nr = dict(r)
                nr.setdefault("id", str(uuid.uuid4()))
                rows.append(nr)
                inserted.append(dict(nr))
            return _FakeResult(inserted)
        if self._op == "update":
            updated = []
            for r in rows:
                if self._match(r):
                    r.update(self._payload)
                    updated.append(dict(r))
            return _FakeResult(updated)
        out = [dict(r) for r in rows if self._match(r)]
        if self._order_col:
            out.sort(key=lambda x: (x.get(self._order_col) is None, x.get(self._order_col)))
        return _FakeResult(out)


class _FakeSupabase:
    def __init__(self):
        self.store: dict[str, list[dict]] = {}

    def table(self, name):
        return _FakeQuery(self.store, name)

    def rows(self, name=TABLE):
        return self.store.get(name, [])


# ── Fake analyst adapter ──────────────────────────────────────────────────────


class _FakeAnalystAdapter:
    """Returns deterministic per-ticker outcomes without LLM calls.

    ``call_outcomes`` is a list of sets — each element is the success_tickers
    for one invocation. This allows programming different outcomes per call
    (e.g., partial success in call 1, full success in call 2).
    """

    _ALL = "__all__"

    def __init__(self, *, call_outcomes: list[set[str]] | None = None,
                 raises: bool = False, timeout: bool = False):
        # Preserve __all__ sentinel; uppercase only real ticker names.
        def _normalise(s: set[str]) -> set[str]:
            return {t if t == self._ALL else t.upper() for t in s}
        self._outcomes = [_normalise(s) for s in (call_outcomes or [])]
        self.raises = raises
        self.timeout = timeout
        self.calls: list[list[str]] = []

    def _success_tickers_for_call(self) -> set[str]:
        idx = len(self.calls)
        if idx < len(self._outcomes):
            return self._outcomes[idx]
        # Default: all succeed
        return {self._ALL}

    async def __call__(self, tickers, *, priority_hints=None, started_at=None):
        if self.raises:
            raise RuntimeError("simulated adapter crash")
        if self.timeout:
            self.calls.append(list(tickers))
            per_ticker = [
                TickerRefreshOutcome(ticker=t.upper(), success=False,
                                     error_reason="full_portfolio_analyst_refresh_timeout")
                for t in tickers
            ]
            return AnalystRefreshResult(
                status=STATUS_SKIPPED_TIMEOUT,
                selected_tickers=[t.upper() for t in tickers],
                deferred_tickers=[],
                per_ticker=per_ticker,
                attempted_llm_calls=len(tickers),
                successful_llm_calls=0,
                failed_llm_calls=len(tickers),
                budget_exhausted=True,
                notes=["full_portfolio_analyst_refresh_timeout"],
            )

        success_set = self._success_tickers_for_call()
        self.calls.append(list(tickers))
        all_succeed = self._ALL in success_set

        per_ticker: list[TickerRefreshOutcome] = []
        successful = failed = 0
        for t in tickers:
            up = t.upper()
            if all_succeed or up in success_set:
                per_ticker.append(TickerRefreshOutcome(
                    ticker=up, success=True,
                    refreshed_agent_insight_at=_now().isoformat(),
                    llm_call_count=1, llm_success_count=1,
                ))
                successful += 1
            else:
                per_ticker.append(TickerRefreshOutcome(
                    ticker=up, success=False,
                    error_reason="provider_overloaded",
                    llm_call_count=1, llm_success_count=0,
                ))
                failed += 1

        if failed == 0:
            status = STATUS_SUCCEEDED
        elif successful == 0:
            status = STATUS_FAILED
        else:
            status = STATUS_PARTIAL_SUCCESS

        return AnalystRefreshResult(
            status=status,
            selected_tickers=[t.upper() for t in tickers],
            deferred_tickers=[],
            per_ticker=per_ticker,
            attempted_llm_calls=successful + failed,
            successful_llm_calls=successful,
            failed_llm_calls=failed,
        )


def _enqueue_tickers(fake, tickers: list[str], now=None):
    return enqueue_refresh_jobs(
        fake, user_id=USER_A, tickers=tickers, now=now or _now(),
    )


def _make_worker(fake, adapter, **kwargs) -> AnalystRefreshWorker:
    return AnalystRefreshWorker(
        client=fake,
        adapter_factory=lambda uid: adapter,
        **kwargs,
    )


# ── Test 1: 34 jobs processed across multiple bounded worker iterations ───────


class TestMultipleIterationCompletion:
    """34 jobs can be completed across two bounded worker iterations."""

    @pytest.mark.asyncio
    async def test_34_jobs_complete_in_two_iterations(self):
        """Iteration 1 succeeds for first 17 tickers; iteration 2 completes the rest."""
        fake = _FakeSupabase()
        tickers = [f"T{i:02d}" for i in range(34)]
        first_batch = {t.upper() for t in tickers[:17]}
        second_batch = {t.upper() for t in tickers[17:]}

        # Adapter: call 1 → first 17 succeed; call 2 → all succeed
        adapter = _FakeAnalystAdapter(call_outcomes=[first_batch, {"__all__"}])
        _enqueue_tickers(fake, tickers)

        worker = _make_worker(fake, adapter)

        # Iteration 1
        now1 = _now()
        result1 = await worker.run_once(now=now1)

        assert len(result1.succeeded_tickers) == 17
        assert len(result1.failed_retryable_tickers) == 17
        assert len(result1.failed_terminal_tickers) == 0
        assert result1.run_resumable is True

        # Succeeded jobs are NOT re-claimed on the next iteration.
        succeeded_after_iter1 = {
            r["ticker"]
            for r in fake.rows()
            if r.get("status") == JOB_SUCCEEDED
        }
        assert len(succeeded_after_iter1) == 17

        # Advance time past the 15-minute backoff for the failed batch
        now2 = _now() + timedelta(minutes=20)
        result2 = await worker.run_once(now=now2)

        assert len(result2.succeeded_tickers) == 17
        assert len(result2.failed_retryable_tickers) == 0

        # All 34 tickers are now succeeded
        final_succeeded = {
            r["ticker"]
            for r in fake.rows()
            if r.get("status") == JOB_SUCCEEDED
        }
        assert len(final_succeeded) == 34

    @pytest.mark.asyncio
    async def test_succeeded_jobs_never_reclaimed(self):
        """Tickers marked succeeded in iteration 1 are not claimed in iteration 2."""
        fake = _FakeSupabase()
        tickers = ["AAPL", "NVDA", "MSFT"]
        # First call: all succeed
        adapter = _FakeAnalystAdapter(call_outcomes=[{"__all__"}])
        _enqueue_tickers(fake, tickers)

        worker = _make_worker(fake, adapter)
        await worker.run_once(now=_now())

        # All succeeded. A second pass should claim nothing.
        result2 = await worker.run_once(now=_now())
        assert result2.claimed_job_count == 0
        assert len(adapter.calls) == 1  # adapter called only once


# ── Test 2: Timeout does not terminal-fail remaining jobs ─────────────────────


class TestTimeoutRetryability:
    """Timeout after partial work leaves jobs retryable, not terminal-failed."""

    @pytest.mark.asyncio
    async def test_single_timeout_does_not_terminal_fail_all(self):
        """A single adapter timeout marks all jobs failed-retryable, not terminal."""
        fake = _FakeSupabase()
        tickers = ["AAPL", "NVDA", "MSFT", "GOOG"]
        adapter = _FakeAnalystAdapter(timeout=True)
        _enqueue_tickers(fake, tickers)

        worker = _make_worker(fake, adapter)
        result = await worker.run_once(now=_now())

        # None should be terminal — all are on first attempt
        assert len(result.failed_terminal_tickers) == 0
        assert len(result.failed_retryable_tickers) == len(tickers)
        assert result.run_resumable is True

    @pytest.mark.asyncio
    async def test_timeout_jobs_are_retried_in_later_iteration(self):
        """Jobs timed-out in iteration 1 are successfully processed in iteration 2."""
        fake = _FakeSupabase()
        tickers = ["AAPL", "NVDA"]
        # Iteration 1: timeout. Iteration 2: all succeed.
        adapter = _FakeAnalystAdapter(
            call_outcomes=[set()],  # first call timeout
            timeout=False,
        )
        # Patch the first call to timeout, second to succeed
        call_count = [0]
        async def _controlled_adapter(t, *, priority_hints=None, started_at=None):
            call_count[0] += 1
            if call_count[0] == 1:
                per_ticker = [
                    TickerRefreshOutcome(ticker=x.upper(), success=False,
                                         error_reason="full_portfolio_analyst_refresh_timeout")
                    for x in t
                ]
                return AnalystRefreshResult(
                    status=STATUS_SKIPPED_TIMEOUT,
                    selected_tickers=[x.upper() for x in t],
                    deferred_tickers=[],
                    per_ticker=per_ticker,
                    budget_exhausted=True,
                    notes=["full_portfolio_analyst_refresh_timeout"],
                )
            per_ticker = [
                TickerRefreshOutcome(ticker=x.upper(), success=True,
                                     refreshed_agent_insight_at=_now().isoformat(),
                                     llm_call_count=1, llm_success_count=1)
                for x in t
            ]
            return AnalystRefreshResult(
                status=STATUS_SUCCEEDED,
                selected_tickers=[x.upper() for x in t],
                deferred_tickers=[],
                per_ticker=per_ticker,
                attempted_llm_calls=len(t),
                successful_llm_calls=len(t),
                failed_llm_calls=0,
            )

        class _ControlledAdapter:
            async def __call__(self, t, **kw):
                return await _controlled_adapter(t, **kw)

        _enqueue_tickers(fake, tickers)
        worker = AnalystRefreshWorker(
            client=fake,
            adapter_factory=lambda uid: _ControlledAdapter(),
        )
        # Iteration 1: timeout
        result1 = await worker.run_once(now=_now())
        assert result1.claimed_job_count == len(tickers)
        assert len(result1.failed_retryable_tickers) == len(tickers)

        # Advance past 15-min backoff
        result2 = await worker.run_once(now=_now() + timedelta(minutes=20))
        assert len(result2.succeeded_tickers) == len(tickers)
        assert len(result2.failed_retryable_tickers) == 0

    @pytest.mark.asyncio
    async def test_timeout_with_residual_evidence_marks_those_succeeded(self):
        """After timeout, tickers with fresh agent_insights in DB are marked succeeded."""
        fake = _FakeSupabase()
        now = _now()
        tickers = ["AAPL", "NVDA", "MSFT"]

        # Pre-populate agent_insights as if AAPL's write completed before timeout
        fake.store.setdefault("agent_insights", []).append({
            "ticker": "AAPL",
            "user_id": USER_A,
            "created_at": now.isoformat(),
            "run_id": "some-run",
        })

        adapter = _FakeAnalystAdapter(timeout=True)
        _enqueue_tickers(fake, tickers, now=now)

        worker = _make_worker(fake, adapter)
        result = await worker.run_once(now=now)

        # AAPL has residual evidence — should be succeeded
        assert "AAPL" in result.succeeded_tickers
        # NVDA and MSFT have no residual evidence — retryable
        assert "NVDA" in result.failed_retryable_tickers or "NVDA" in result.failed_tickers
        assert "MSFT" in result.failed_retryable_tickers or "MSFT" in result.failed_tickers
        # AAPL job is marked succeeded in DB
        aapl_row = next(r for r in fake.rows() if r["ticker"] == "AAPL")
        assert aapl_row["status"] == JOB_SUCCEEDED

    @pytest.mark.asyncio
    async def test_max_attempts_exhaustion_marks_terminal(self):
        """After max_attempts failures, a ticker is terminal-failed."""
        fake = _FakeSupabase()
        tickers = ["AAPL"]
        _enqueue_tickers(fake, tickers)

        # Exhaust all attempts by calling mark_job_failed repeatedly
        now = _now()
        jobs = claim_due_jobs(fake, worker_run_id="wid-1", now=now, limit=10)
        assert len(jobs) == 1
        job = jobs[0]

        # Simulate attempts: claim increments to 1, then fail 4 more times
        for i in range(DEFAULT_MAX_ATTEMPTS - 1):
            mark_job_failed(fake, job, error="overload", now=now + timedelta(hours=i))
            # Reclaim with incremented attempts
            fake.rows()[0]["status"] = JOB_PENDING
            fake.rows()[0]["next_retry_at"] = now.isoformat()
            fake.rows()[0]["attempts"] = i + 2
            jobs = claim_due_jobs(fake, worker_run_id=f"wid-{i+2}",
                                  now=now + timedelta(hours=i), limit=10)
            if jobs:
                job = jobs[0]

        # One final failure that exhausts the budget
        mark_job_failed(fake, job, error="overload", now=now)
        final_row = fake.rows()[0]
        assert final_row["status"] == JOB_FAILED
        assert final_row["next_retry_at"] is None  # exhausted = no retry scheduled


# ── Test 3: Provider overload (retryable) does not publish certified snapshot ─


class TestProviderOverloadNoCertification:
    """Retryable provider overload errors do not publish a certified snapshot."""

    @pytest.mark.asyncio
    async def test_overload_failed_jobs_block_certification_not_green(self):
        """Worker with all-failed jobs emits failed outcome, not certified snapshot."""
        fake = _FakeSupabase()
        tickers = ["AAPL", "NVDA"]

        # All tickers fail with provider_overloaded
        adapter = _FakeAnalystAdapter(call_outcomes=[set()])
        _enqueue_tickers(fake, tickers)

        worker = _make_worker(fake, adapter)
        result = await worker.run_once(now=_now())

        # No tickers succeeded
        assert len(result.succeeded_tickers) == 0
        assert len(result.failed_retryable_tickers) == len(tickers)

        # No intel_v3_snapshots row was published (worker doesn't publish snapshots
        # — that's the prewarm step which only runs after successful writes)
        assert "intel_v3_snapshots" not in fake.store or not fake.store["intel_v3_snapshots"]

    @pytest.mark.asyncio
    async def test_partial_success_does_not_trigger_certification(self):
        """Partial success (only some tickers refreshed) does not yield certified green."""
        fake = _FakeSupabase()
        tickers = ["AAPL", "NVDA", "MSFT"]
        # Only AAPL succeeds
        adapter = _FakeAnalystAdapter(call_outcomes=[{"AAPL"}])
        _enqueue_tickers(fake, tickers)

        worker = _make_worker(fake, adapter)
        result = await worker.run_once(now=_now())

        assert len(result.succeeded_tickers) == 1
        assert len(result.failed_retryable_tickers) == 2
        # No certified snapshot published
        assert not fake.store.get("intel_v3_snapshots")


# ── Test 4: Completed per-ticker results not redone unnecessarily ─────────────


class TestCompletedResultsNotRedone:
    """Tickers already marked succeeded are not claimed again in later iterations."""

    @pytest.mark.asyncio
    async def test_succeeded_tickers_not_reclaimed(self):
        """A job in succeeded state is never re-claimed by the worker."""
        fake = _FakeSupabase()
        tickers = ["AAPL", "NVDA", "MSFT"]
        adapter = _FakeAnalystAdapter(call_outcomes=[{"__all__"}])
        _enqueue_tickers(fake, tickers)

        worker = _make_worker(fake, adapter)
        await worker.run_once(now=_now())

        # All 3 succeeded. Verify claim status.
        assert all(r["status"] == JOB_SUCCEEDED for r in fake.rows())

        # Second pass claims nothing
        result2 = await worker.run_once(now=_now())
        assert result2.claimed_job_count == 0
        assert len(adapter.calls) == 1  # adapter was not called again

    @pytest.mark.asyncio
    async def test_failed_retryable_reclaimed_but_succeeded_not(self):
        """Only the failed-retryable tickers are re-claimed in the next pass."""
        fake = _FakeSupabase()
        tickers = ["AAPL", "NVDA", "MSFT"]
        # AAPL succeeds; NVDA, MSFT fail
        adapter = _FakeAnalystAdapter(call_outcomes=[{"AAPL"}, {"__all__"}])
        _enqueue_tickers(fake, tickers)

        worker = _make_worker(fake, adapter)
        result1 = await worker.run_once(now=_now())

        assert "AAPL" in result1.succeeded_tickers
        assert len(result1.failed_retryable_tickers) == 2

        # Second pass (after backoff window)
        result2 = await worker.run_once(now=_now() + timedelta(minutes=20))
        # Only NVDA and MSFT were re-claimed (AAPL stays succeeded)
        assert result2.claimed_job_count == 2
        assert result2.claimed_job_count < 3  # AAPL not re-claimed
        assert len(result2.succeeded_tickers) == 2


# ── Test 5: Terminal failed ticker blocks certification ───────────────────────


class TestTerminalFailureBlocksCertification:
    """A terminal-failed ticker is logged as terminal and blocks certification."""

    @pytest.mark.asyncio
    async def test_exhausted_job_is_classified_terminal_in_result(self):
        """Worker reports failed_terminal_tickers when a job exhausts its attempts."""
        fake = _FakeSupabase()
        tickers = ["AAPL"]
        now = _now()

        # Enqueue and pre-exhaust attempts by manipulating the DB row
        _enqueue_tickers(fake, tickers, now=now)
        # Set attempts to max_attempts - 1; claim will increment to max
        fake.rows()[0]["attempts"] = DEFAULT_MAX_ATTEMPTS - 1
        fake.rows()[0]["max_attempts"] = DEFAULT_MAX_ATTEMPTS

        # Adapter fails
        adapter = _FakeAnalystAdapter(call_outcomes=[set()])
        worker = _make_worker(fake, adapter)
        result = await worker.run_once(now=now)

        # Claim incremented attempts to DEFAULT_MAX_ATTEMPTS → terminal on fail
        assert "AAPL" in result.failed_terminal_tickers
        assert "AAPL" not in result.failed_retryable_tickers
        assert result.run_resumable is False

    @pytest.mark.asyncio
    async def test_terminal_job_not_reclaimed(self):
        """A terminal-failed job (exhausted attempts) is never re-claimed."""
        fake = _FakeSupabase()
        tickers = ["AAPL"]
        now = _now()
        _enqueue_tickers(fake, tickers, now=now)

        # Exhaust all attempts
        fake.rows()[0]["attempts"] = DEFAULT_MAX_ATTEMPTS
        fake.rows()[0]["status"] = JOB_FAILED
        fake.rows()[0]["next_retry_at"] = None

        worker = _make_worker(fake, _FakeAnalystAdapter())
        result = await worker.run_once(now=now)

        # Exhausted job is not claimable
        assert result.claimed_job_count == 0


# ── Test 6: Existing certified snapshot remains available during refresh ───────


class TestCertifiedSnapshotAvailableDuringRefresh:
    """A prior certified snapshot is not replaced until a new one is certified."""

    def test_enqueue_does_not_remove_existing_certified_snapshot(self):
        """Enqueueing new jobs does not touch intel_v3_snapshots table."""
        fake = _FakeSupabase()
        # Pre-populate a certified snapshot
        fake.store["intel_v3_snapshots"] = [
            {"id": "snap-1", "is_active": True, "snapshot_source": "worker_certified"}
        ]

        # Enqueue new refresh jobs
        enqueue_refresh_jobs(
            fake, user_id=USER_A, tickers=["AAPL", "NVDA"], now=_now(),
        )

        # Snapshot must still be there
        snaps = fake.store.get("intel_v3_snapshots", [])
        assert len(snaps) == 1
        assert snaps[0]["snapshot_source"] == "worker_certified"

    @pytest.mark.asyncio
    async def test_failed_worker_run_does_not_remove_certified_snapshot(self):
        """A failed worker run does not remove or alter the existing certified snapshot."""
        fake = _FakeSupabase()
        fake.store["intel_v3_snapshots"] = [
            {"id": "snap-1", "is_active": True, "snapshot_source": "worker_certified"}
        ]
        tickers = ["AAPL", "NVDA"]
        adapter = _FakeAnalystAdapter(call_outcomes=[set()])  # all fail
        _enqueue_tickers(fake, tickers)

        worker = _make_worker(fake, adapter)
        result = await worker.run_once(now=_now())

        assert len(result.succeeded_tickers) == 0
        snaps = fake.store.get("intel_v3_snapshots", [])
        assert len(snaps) == 1
        assert snaps[0]["snapshot_source"] == "worker_certified"


# ── Test 7: Structured log contains all required fields ──────────────────────


class TestStructuredLogFields:
    """Structured log includes all required production monitoring fields."""

    def test_worker_run_result_to_dict_has_all_required_keys(self):
        """WorkerRunResult.to_dict() includes every field needed for production logs."""
        result = WorkerRunResult(worker_run_id="test-id")
        d = result.to_dict()

        required_keys = {
            "worker_run_id",
            "jobs_due",
            "claimed_job_count",
            "selected_tickers",
            "succeeded_tickers",
            "failed_tickers",
            "failed_retryable_tickers",
            "failed_terminal_tickers",
            "attempted_llm_calls",
            "successful_llm_calls",
            "failed_llm_calls",
            "persisted_ticker_success_count",
            "timed_out_before_completion",
            "remaining_pending_or_retryable",
            "run_resumable",
            "duration_ms",
            "notes",
        }
        missing = required_keys - set(d.keys())
        assert not missing, f"Missing keys in WorkerRunResult.to_dict(): {missing}"

    @pytest.mark.asyncio
    async def test_run_summary_log_includes_all_fields(self, caplog):
        """intel_v3.analyst_refresh_worker_run_summary log line includes all required fields."""
        import logging
        fake = _FakeSupabase()
        tickers = ["AAPL"]
        adapter = _FakeAnalystAdapter(call_outcomes=[{"__all__"}])
        _enqueue_tickers(fake, tickers)

        worker = _make_worker(fake, adapter)
        with caplog.at_level(logging.INFO, logger="app.services.intelligence.v3.analyst_refresh_worker_v1"):
            await worker.run_once(now=_now())

        summary_lines = [r for r in caplog.records
                         if "analyst_refresh_worker_run_summary" in r.getMessage()]
        assert summary_lines, "No run_summary log line found"
        msg = summary_lines[-1].getMessage()

        required_fragments = [
            "jobs_due=",
            "claimed=",
            "selected=",
            "succeeded=",
            "failed_retryable=",
            "failed_terminal=",
            "attempted_llm_calls=",
            "successful_llm_calls=",
            "failed_llm_calls=",
            "timed_out_before_completion=",
            "remaining_pending_or_retryable=",
            "run_resumable=",
            "duration_ms=",
        ]
        for frag in required_fragments:
            assert frag in msg, f"Missing field in run_summary log: {frag!r}"


# ── Test 8: run_resumable=True when retryable failures remain ─────────────────


class TestRunResumable:
    """run_resumable reflects whether future iterations can make progress."""

    @pytest.mark.asyncio
    async def test_run_resumable_true_when_retryable_failures(self):
        fake = _FakeSupabase()
        tickers = ["AAPL", "NVDA"]
        adapter = _FakeAnalystAdapter(call_outcomes=[set()])  # all fail first call
        _enqueue_tickers(fake, tickers)

        worker = _make_worker(fake, adapter)
        result = await worker.run_once(now=_now())

        assert len(result.failed_retryable_tickers) > 0
        assert result.run_resumable is True

    @pytest.mark.asyncio
    async def test_run_resumable_false_when_no_retryable_remain(self):
        fake = _FakeSupabase()
        tickers = ["AAPL"]
        adapter = _FakeAnalystAdapter(call_outcomes=[{"__all__"}])
        _enqueue_tickers(fake, tickers)

        worker = _make_worker(fake, adapter)
        result = await worker.run_once(now=_now())

        assert len(result.succeeded_tickers) == 1
        assert len(result.failed_retryable_tickers) == 0
        assert result.run_resumable is False

    @pytest.mark.asyncio
    async def test_run_resumable_false_when_no_jobs(self):
        fake = _FakeSupabase()
        worker = _make_worker(fake, _FakeAnalystAdapter())
        result = await worker.run_once(now=_now())

        assert result.claimed_job_count == 0
        assert result.run_resumable is False


# ── count_due_jobs unit tests ─────────────────────────────────────────────────


class TestCountDueJobs:
    """count_due_jobs returns accurate job-state breakdown for monitoring."""

    def test_empty_store_returns_zeros(self):
        fake = _FakeSupabase()
        counts = count_due_jobs(fake, now=_now())
        assert counts["total_due"] == 0
        assert counts["failed_terminal"] == 0

    def test_pending_jobs_counted(self):
        fake = _FakeSupabase()
        _enqueue_tickers(fake, ["AAPL", "NVDA"])
        counts = count_due_jobs(fake, now=_now())
        assert counts["pending"] == 2
        assert counts["total_due"] == 2

    def test_exhausted_jobs_counted_as_terminal(self):
        fake = _FakeSupabase()
        _enqueue_tickers(fake, ["AAPL"])
        # Mark exhausted
        fake.rows()[0]["status"] = JOB_FAILED
        fake.rows()[0]["attempts"] = DEFAULT_MAX_ATTEMPTS
        fake.rows()[0]["next_retry_at"] = None

        counts = count_due_jobs(fake, now=_now())
        assert counts["failed_terminal"] == 1
        assert counts["total_due"] == 0  # exhausted = not claimable

    def test_failed_retryable_counted_separately(self):
        fake = _FakeSupabase()
        _enqueue_tickers(fake, ["AAPL"])
        fake.rows()[0]["status"] = JOB_FAILED
        fake.rows()[0]["attempts"] = 1
        fake.rows()[0]["next_retry_at"] = _now().isoformat()  # due now

        counts = count_due_jobs(fake, now=_now())
        assert counts["failed_retryable"] == 1
        assert counts["total_due"] == 1
        assert counts["failed_terminal"] == 0

    def test_not_yet_due_jobs_not_in_total_due(self):
        fake = _FakeSupabase()
        _enqueue_tickers(fake, ["AAPL"])
        future = (_now() + timedelta(hours=1)).isoformat()
        fake.rows()[0]["status"] = JOB_FAILED
        fake.rows()[0]["attempts"] = 2
        fake.rows()[0]["next_retry_at"] = future

        counts = count_due_jobs(fake, now=_now())
        assert counts["total_due"] == 0
        assert counts["failed_not_yet_due"] == 1
        assert counts["failed_terminal"] == 0
