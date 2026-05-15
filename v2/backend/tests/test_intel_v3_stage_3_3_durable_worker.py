"""Build 1 regression tests — Intel v3 durable resumable analyst refresh worker.

These tests prove the acceptance criteria from the Build 1 task:

  1. 34 jobs can be processed across multiple BOUNDED worker iterations where
     each pass claims at most DEFAULT_MAX_JOBS_PER_RUN tickers. No single
     adapter call ever receives all 34 tickers.
  2. timeout after partial work does not terminal-fail all remaining jobs.
  3. retryable provider overload does not publish a certified snapshot.
  4. completed per-ticker results persist and are not redone unnecessarily.
  5. failed terminal ticker blocks certification instead of green/current UI.
  6. existing certified snapshot remains available while a new refresh runs.
  7. structured log contains all required fields.
  8. run_resumable=True when retryable failures or unclaimed pending backlog remain.
  9. residual evidence requires BOTH agent_insights AND recommendations rows —
     agent_insights-only is NOT sufficient to mark a job succeeded.
  10. batching is end-to-end — the adapter AND evidence writer receive only the
      selected batch, never the full 34-ticker portfolio.

The tests use the same in-memory Supabase fake as test_intel_v3_stage_3_2_* so
no real DB or LLM calls are made.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from unittest.mock import patch

from app.services.intelligence.v3.analyst_evidence_writer_v1 import (
    write_analyst_evidence,
)
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
    DEFAULT_MAX_JOBS_PER_RUN,
    AnalystRefreshWorker,
    WorkerRunResult,
)
from app.services.intelligence.v3.full_portfolio_analyst_refresh_adapter_v1 import (
    FullPortfolioAnalystRefreshAdapter,
    FullPortfolioAnalystRefreshBudget,
    _read_post_run_evidence,
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


# ── Test 1: Bounded batch execution — 34 jobs require multiple worker passes ───


class TestBoundedBatchExecution:
    """Worker batch size is bounded; 34 jobs require multiple run_once() calls."""

    @pytest.mark.asyncio
    async def test_each_adapter_call_receives_at_most_batch_size_tickers(self):
        """Each adapter call receives ≤ DEFAULT_MAX_JOBS_PER_RUN tickers, never all 34."""
        fake = _FakeSupabase()
        tickers = [f"T{i:02d}" for i in range(34)]
        adapter = _FakeAnalystAdapter()  # all succeed by default
        _enqueue_tickers(fake, tickers)

        worker = _make_worker(fake, adapter)  # uses DEFAULT_MAX_JOBS_PER_RUN=10

        # Drive until all jobs are done (safety cap at 10 iterations)
        for _ in range(10):
            result = await worker.run_once(now=_now())
            if result.claimed_job_count == 0:
                break

        # Every adapter call received at most DEFAULT_MAX_JOBS_PER_RUN tickers
        for call in adapter.calls:
            assert len(call) <= DEFAULT_MAX_JOBS_PER_RUN, (
                f"Adapter received {len(call)} tickers; expected ≤ {DEFAULT_MAX_JOBS_PER_RUN}. "
                "Worker batch must be bounded."
            )

        # Adapter was called multiple times (never a single 34-ticker call)
        assert len(adapter.calls) > 1, (
            "Expected multiple adapter calls for 34 jobs with bounded batch size, "
            f"but adapter was called only {len(adapter.calls)} time(s)."
        )

        # All 34 tickers eventually succeeded
        final_succeeded = {r["ticker"] for r in fake.rows() if r.get("status") == JOB_SUCCEEDED}
        assert len(final_succeeded) == 34

    @pytest.mark.asyncio
    async def test_run_resumable_true_while_unclaimed_pending_backlog_remains(self):
        """run_resumable=True after first batch even when all claimed jobs succeeded, because unclaimed pending jobs remain."""
        fake = _FakeSupabase()
        tickers = [f"T{i:02d}" for i in range(34)]
        adapter = _FakeAnalystAdapter()  # all succeed
        _enqueue_tickers(fake, tickers)

        worker = _make_worker(fake, adapter, max_jobs_per_run=10)
        result = await worker.run_once(now=_now())

        assert result.claimed_job_count == 10
        assert len(result.succeeded_tickers) == 10
        assert len(result.failed_retryable_tickers) == 0

        # 24 unclaimed pending jobs remain → run must be resumable
        assert result.run_resumable is True, (
            "run_resumable must be True when unclaimed pending backlog remains, "
            "even if all claimed jobs in this pass succeeded."
        )
        assert result.remaining_pending_or_retryable >= 24

    @pytest.mark.asyncio
    async def test_run_resumable_false_only_when_no_backlog_remains(self):
        """run_resumable=False only on the final pass when all jobs are done."""
        fake = _FakeSupabase()
        tickers = [f"T{i:02d}" for i in range(4)]  # small enough for one pass
        adapter = _FakeAnalystAdapter()
        _enqueue_tickers(fake, tickers)

        worker = _make_worker(fake, adapter, max_jobs_per_run=10)
        result = await worker.run_once(now=_now())

        assert result.claimed_job_count == 4
        assert len(result.succeeded_tickers) == 4
        assert result.run_resumable is False
        assert result.remaining_pending_or_retryable == 0

    @pytest.mark.asyncio
    async def test_succeeded_jobs_never_reclaimed(self):
        """Tickers marked succeeded in iteration 1 are not claimed in iteration 2."""
        fake = _FakeSupabase()
        tickers = ["AAPL", "NVDA", "MSFT"]
        adapter = _FakeAnalystAdapter(call_outcomes=[{"__all__"}])
        _enqueue_tickers(fake, tickers)

        worker = _make_worker(fake, adapter)
        await worker.run_once(now=_now())

        # All succeeded. A second pass should claim nothing.
        result2 = await worker.run_once(now=_now())
        assert result2.claimed_job_count == 0
        assert len(adapter.calls) == 1  # adapter called only once


# ── Test 2: Multi-iteration via retry mechanism (explicit large batch) ─────────


class TestMultipleIterationCompletion:
    """34 jobs can be completed across two bounded worker iterations via retry."""

    @pytest.mark.asyncio
    async def test_34_jobs_complete_via_retry_across_two_iterations(self):
        """Iteration 1 succeeds for first 17 tickers; iteration 2 completes the rest via retry."""
        fake = _FakeSupabase()
        tickers = [f"T{i:02d}" for i in range(34)]
        first_batch = {t.upper() for t in tickers[:17]}

        # Adapter: call 1 → first 17 succeed; subsequent calls → all succeed
        adapter = _FakeAnalystAdapter(call_outcomes=[first_batch, {"__all__"}])
        _enqueue_tickers(fake, tickers)

        # Use explicit large batch so all 34 are claimed at once (tests retry, not bounding)
        worker = _make_worker(fake, adapter, max_jobs_per_run=34)

        now1 = _now()
        result1 = await worker.run_once(now=now1)

        assert len(result1.succeeded_tickers) == 17
        assert len(result1.failed_retryable_tickers) == 17
        assert len(result1.failed_terminal_tickers) == 0
        assert result1.run_resumable is True

        succeeded_after_iter1 = {
            r["ticker"] for r in fake.rows() if r.get("status") == JOB_SUCCEEDED
        }
        assert len(succeeded_after_iter1) == 17

        # Advance past 15-minute backoff for the failed batch
        now2 = _now() + timedelta(minutes=20)
        result2 = await worker.run_once(now=now2)

        assert len(result2.succeeded_tickers) == 17
        assert len(result2.failed_retryable_tickers) == 0

        final_succeeded = {
            r["ticker"] for r in fake.rows() if r.get("status") == JOB_SUCCEEDED
        }
        assert len(final_succeeded) == 34

    @pytest.mark.asyncio
    async def test_failed_retryable_reclaimed_but_succeeded_not(self):
        """Only failed-retryable tickers are re-claimed in the next pass."""
        fake = _FakeSupabase()
        tickers = ["AAPL", "NVDA", "MSFT"]
        # AAPL succeeds; NVDA, MSFT fail retryably
        adapter = _FakeAnalystAdapter(call_outcomes=[{"AAPL"}, {"__all__"}])
        _enqueue_tickers(fake, tickers)

        worker = _make_worker(fake, adapter, max_jobs_per_run=10)
        result1 = await worker.run_once(now=_now())

        assert "AAPL" in result1.succeeded_tickers
        assert len(result1.failed_retryable_tickers) == 2

        # Second pass (after backoff window)
        result2 = await worker.run_once(now=_now() + timedelta(minutes=20))
        # Only NVDA and MSFT were re-claimed (AAPL stays succeeded)
        assert result2.claimed_job_count == 2
        assert len(result2.succeeded_tickers) == 2


# ── Test 3: Timeout retryability ──────────────────────────────────────────────


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
        result1 = await worker.run_once(now=_now())
        assert result1.claimed_job_count == len(tickers)
        assert len(result1.failed_retryable_tickers) == len(tickers)

        result2 = await worker.run_once(now=_now() + timedelta(minutes=20))
        assert len(result2.succeeded_tickers) == len(tickers)
        assert len(result2.failed_retryable_tickers) == 0

    @pytest.mark.asyncio
    async def test_timeout_with_residual_evidence_marks_those_succeeded(self):
        """After timeout, tickers with BOTH fresh agent_insights and recommendations are marked succeeded."""
        fake = _FakeSupabase()
        now = _now()
        tickers = ["AAPL", "NVDA", "MSFT"]

        # Pre-populate BOTH agent_insights AND recommendations for AAPL.
        # Both rows are required — insight-only is insufficient (see next test).
        fake.store.setdefault("agent_insights", []).append({
            "ticker": "AAPL",
            "user_id": USER_A,
            "created_at": now.isoformat(),
            "run_id": "residual-run",
        })
        fake.store.setdefault("recommendations", []).append({
            "ticker": "AAPL",
            "user_id": USER_A,
            "created_at": now.isoformat(),
            "agent_run_id": "residual-run",
        })

        adapter = _FakeAnalystAdapter(timeout=True)
        _enqueue_tickers(fake, tickers, now=now)

        worker = _make_worker(fake, adapter)
        result = await worker.run_once(now=now)

        # AAPL has both rows → succeeded
        assert "AAPL" in result.succeeded_tickers
        # NVDA and MSFT have no residual evidence → retryable
        assert "NVDA" in result.failed_retryable_tickers or "NVDA" in result.failed_tickers
        assert "MSFT" in result.failed_retryable_tickers or "MSFT" in result.failed_tickers
        # AAPL job is marked succeeded in DB
        aapl_row = next(r for r in fake.rows() if r["ticker"] == "AAPL")
        assert aapl_row["status"] == JOB_SUCCEEDED

    @pytest.mark.asyncio
    async def test_residual_evidence_requires_both_insight_and_recommendation(self):
        """agent_insights-only (without matching recommendation) is not sufficient to mark a job succeeded."""
        fake = _FakeSupabase()
        now = _now()
        tickers = ["AAPL"]

        # Only agent_insights present — deliberately NO recommendations row.
        # This simulates a partial write where the insight committed but the
        # recommendation write was cancelled before completing.
        fake.store.setdefault("agent_insights", []).append({
            "ticker": "AAPL",
            "user_id": USER_A,
            "created_at": now.isoformat(),
            "run_id": "partial-run",
        })
        # No recommendations row — worker should NOT mark AAPL succeeded.

        adapter = _FakeAnalystAdapter(timeout=True)
        _enqueue_tickers(fake, tickers, now=now)

        worker = _make_worker(fake, adapter)
        result = await worker.run_once(now=now)

        # agent_insights-only is insufficient — AAPL must NOT be succeeded
        assert "AAPL" not in result.succeeded_tickers, (
            "AAPL should not be marked succeeded from agent_insights alone. "
            "Downstream certification requires both insight and recommendation rows."
        )
        assert "AAPL" in result.failed_retryable_tickers or "AAPL" in result.failed_tickers
        aapl_row = next(r for r in fake.rows() if r["ticker"] == "AAPL")
        assert aapl_row["status"] == JOB_FAILED

    @pytest.mark.asyncio
    async def test_max_attempts_exhaustion_marks_terminal(self):
        """After max_attempts failures, a ticker is terminal-failed."""
        fake = _FakeSupabase()
        tickers = ["AAPL"]
        _enqueue_tickers(fake, tickers)

        now = _now()
        jobs = claim_due_jobs(fake, worker_run_id="wid-1", now=now, limit=10)
        assert len(jobs) == 1
        job = jobs[0]

        for i in range(DEFAULT_MAX_ATTEMPTS - 1):
            mark_job_failed(fake, job, error="overload", now=now + timedelta(hours=i))
            fake.rows()[0]["status"] = JOB_PENDING
            fake.rows()[0]["next_retry_at"] = now.isoformat()
            fake.rows()[0]["attempts"] = i + 2
            jobs = claim_due_jobs(fake, worker_run_id=f"wid-{i+2}",
                                  now=now + timedelta(hours=i), limit=10)
            if jobs:
                job = jobs[0]

        mark_job_failed(fake, job, error="overload", now=now)
        final_row = fake.rows()[0]
        assert final_row["status"] == JOB_FAILED
        assert final_row["next_retry_at"] is None  # exhausted = no retry scheduled


# ── Test 4: Provider overload (retryable) does not publish certified snapshot ──


class TestProviderOverloadNoCertification:
    """Retryable provider overload errors do not publish a certified snapshot."""

    @pytest.mark.asyncio
    async def test_overload_failed_jobs_block_certification_not_green(self):
        """Worker with all-failed jobs emits failed outcome, not certified snapshot."""
        fake = _FakeSupabase()
        tickers = ["AAPL", "NVDA"]

        adapter = _FakeAnalystAdapter(call_outcomes=[set()])
        _enqueue_tickers(fake, tickers)

        worker = _make_worker(fake, adapter)
        result = await worker.run_once(now=_now())

        assert len(result.succeeded_tickers) == 0
        assert len(result.failed_retryable_tickers) == len(tickers)
        assert "intel_v3_snapshots" not in fake.store or not fake.store["intel_v3_snapshots"]

    @pytest.mark.asyncio
    async def test_partial_success_does_not_trigger_certification(self):
        """Partial success (only some tickers refreshed) does not yield certified green."""
        fake = _FakeSupabase()
        tickers = ["AAPL", "NVDA", "MSFT"]
        adapter = _FakeAnalystAdapter(call_outcomes=[{"AAPL"}])
        _enqueue_tickers(fake, tickers)

        worker = _make_worker(fake, adapter)
        result = await worker.run_once(now=_now())

        assert len(result.succeeded_tickers) == 1
        assert len(result.failed_retryable_tickers) == 2
        assert not fake.store.get("intel_v3_snapshots")


# ── Test 5: Completed per-ticker results not redone unnecessarily ─────────────


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

        assert all(r["status"] == JOB_SUCCEEDED for r in fake.rows())

        result2 = await worker.run_once(now=_now())
        assert result2.claimed_job_count == 0
        assert len(adapter.calls) == 1  # adapter was not called again


# ── Test 6: Terminal failed ticker blocks certification ───────────────────────


class TestTerminalFailureBlocksCertification:
    """A terminal-failed ticker is logged as terminal and blocks certification."""

    @pytest.mark.asyncio
    async def test_exhausted_job_is_classified_terminal_in_result(self):
        """Worker reports failed_terminal_tickers when a job exhausts its attempts."""
        fake = _FakeSupabase()
        tickers = ["AAPL"]
        now = _now()

        _enqueue_tickers(fake, tickers, now=now)
        # Set attempts to max_attempts - 1; claim increments to max
        fake.rows()[0]["attempts"] = DEFAULT_MAX_ATTEMPTS - 1
        fake.rows()[0]["max_attempts"] = DEFAULT_MAX_ATTEMPTS

        adapter = _FakeAnalystAdapter(call_outcomes=[set()])
        worker = _make_worker(fake, adapter)
        result = await worker.run_once(now=now)

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

        fake.rows()[0]["attempts"] = DEFAULT_MAX_ATTEMPTS
        fake.rows()[0]["status"] = JOB_FAILED
        fake.rows()[0]["next_retry_at"] = None

        worker = _make_worker(fake, _FakeAnalystAdapter())
        result = await worker.run_once(now=now)

        assert result.claimed_job_count == 0


# ── Test 7: Existing certified snapshot remains available during refresh ───────


class TestCertifiedSnapshotAvailableDuringRefresh:
    """A prior certified snapshot is not replaced until a new one is certified."""

    def test_enqueue_does_not_remove_existing_certified_snapshot(self):
        """Enqueueing new jobs does not touch intel_v3_snapshots table."""
        fake = _FakeSupabase()
        fake.store["intel_v3_snapshots"] = [
            {"id": "snap-1", "is_active": True, "snapshot_source": "worker_certified"}
        ]

        enqueue_refresh_jobs(
            fake, user_id=USER_A, tickers=["AAPL", "NVDA"], now=_now(),
        )

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
        adapter = _FakeAnalystAdapter(call_outcomes=[set()])
        _enqueue_tickers(fake, tickers)

        worker = _make_worker(fake, adapter)
        result = await worker.run_once(now=_now())

        assert len(result.succeeded_tickers) == 0
        snaps = fake.store.get("intel_v3_snapshots", [])
        assert len(snaps) == 1
        assert snaps[0]["snapshot_source"] == "worker_certified"


# ── Test 8: Structured log contains all required fields ──────────────────────


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


# ── Test 9: run_resumable correctly reflects all resumability conditions ───────


class TestRunResumable:
    """run_resumable reflects whether future iterations can make progress."""

    @pytest.mark.asyncio
    async def test_run_resumable_true_when_retryable_failures(self):
        """run_resumable=True when current pass had retryable failures."""
        fake = _FakeSupabase()
        tickers = ["AAPL", "NVDA"]
        adapter = _FakeAnalystAdapter(call_outcomes=[set()])
        _enqueue_tickers(fake, tickers)

        worker = _make_worker(fake, adapter)
        result = await worker.run_once(now=_now())

        assert len(result.failed_retryable_tickers) > 0
        assert result.run_resumable is True

    @pytest.mark.asyncio
    async def test_run_resumable_true_when_unclaimed_backlog_even_if_no_failures(self):
        """run_resumable=True when unclaimed pending backlog exists, even with zero failures in this pass."""
        fake = _FakeSupabase()
        tickers = [f"T{i:02d}" for i in range(20)]
        adapter = _FakeAnalystAdapter()  # all succeed
        _enqueue_tickers(fake, tickers)

        # Batch size 5 → claims 5, 15 remain pending
        worker = _make_worker(fake, adapter, max_jobs_per_run=5)
        result = await worker.run_once(now=_now())

        assert result.claimed_job_count == 5
        assert len(result.succeeded_tickers) == 5
        assert len(result.failed_retryable_tickers) == 0
        # 15 unclaimed pending → must be resumable
        assert result.run_resumable is True
        assert result.remaining_pending_or_retryable >= 15

    @pytest.mark.asyncio
    async def test_run_resumable_false_when_no_retryable_remain(self):
        """run_resumable=False when all claimed jobs succeeded and no backlog remains."""
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
        """run_resumable=False when there are no jobs at all."""
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


# ── Helpers for Build 1 end-to-end batching tests ─────────────────────────────

class _SimpleInsight:
    """Minimal TickerInsight-alike for write_analyst_evidence tests."""
    def __init__(self, ticker: str, action: str = "HOLD"):
        self.ticker = ticker
        self.suggested_action = action
        self.conviction_score = 0.5
        self.investment_thesis = f"{action} signal."
        self.sentiment_score = 0.1
        self.sentiment_label = "neutral"
        self.technical_signal = "HOLD"
        self.technical_summary = "stable"
        self.fundamental_score = 0.2
        self.fundamental_summary = "solid"
        self.suggested_allocation = 0.0


def _patch_writer_client(fake):
    return patch(
        "app.services.intelligence.v3.analyst_evidence_writer_v1.get_supabase_client",
        return_value=fake,
    )


# ── Test 10: End-to-end batching — adapter and evidence writer bounded ─────────


class TestAnalystBatchingEndToEnd:
    """Build 1 core fix: batching is end-to-end, not job-store-only.

    These tests prove the fix for the production bug where the worker claimed
    10 jobs but the orchestrator still analyzed all 34 holdings.
    """

    @pytest.mark.asyncio
    async def test_adapter_receives_only_selected_batch_not_full_portfolio(self):
        """When the worker claims 10 jobs, the adapter is called with exactly those 10 tickers."""
        fake = _FakeSupabase()
        all_tickers = [f"T{i:02d}" for i in range(34)]
        adapter = _FakeAnalystAdapter()  # records call args
        _enqueue_tickers(fake, all_tickers)

        worker = _make_worker(fake, adapter, max_jobs_per_run=10)
        result = await worker.run_once(now=_now())

        assert result.claimed_job_count == 10
        assert len(adapter.calls) == 1
        # Adapter must have received exactly 10 tickers — the bounded batch.
        assert len(adapter.calls[0]) == 10, (
            f"Adapter received {len(adapter.calls[0])} tickers; expected 10. "
            "Batching must be end-to-end: adapter must not receive all 34."
        )

    @pytest.mark.asyncio
    async def test_four_bounded_passes_complete_all_34_holdings(self):
        """34 holdings complete across 4 bounded passes of 10 (3×10 + 1×4)."""
        fake = _FakeSupabase()
        all_tickers = [f"T{i:02d}" for i in range(34)]
        adapter = _FakeAnalystAdapter()  # all succeed by default
        _enqueue_tickers(fake, all_tickers)

        worker = _make_worker(fake, adapter, max_jobs_per_run=10)

        pass_results = []
        for _ in range(6):  # safety cap; should finish in 4
            r = await worker.run_once(now=_now())
            pass_results.append(r)
            if r.claimed_job_count == 0:
                break

        # No single adapter call received all 34
        for call in adapter.calls:
            assert len(call) <= 10

        # 4 passes required (3×10 + 1×4)
        non_empty = [r for r in pass_results if r.claimed_job_count > 0]
        assert len(non_empty) == 4, (
            f"Expected 4 passes for 34 jobs at batch_size=10, got {len(non_empty)}"
        )

        # All 34 tickers succeeded
        succeeded = {r["ticker"] for r in fake.rows() if r.get("status") == JOB_SUCCEEDED}
        assert len(succeeded) == 34

    @pytest.mark.asyncio
    async def test_evidence_writer_scoped_tickers_limits_writes_to_selected_batch(self):
        """write_analyst_evidence with scoped_tickers writes only the selected batch."""
        fake = _FakeSupabase()
        run_id = str(uuid.uuid4())
        now = _now()

        # 34 insights but only 10 are selected for this worker batch
        all_insights = [_SimpleInsight(f"T{i:02d}") for i in range(34)]
        batch_tickers = [f"T{i:02d}" for i in range(10)]
        batch_insights = all_insights[:10]

        with _patch_writer_client(fake):
            result = await write_analyst_evidence(
                user_id=uuid.UUID(USER_A),
                agent_run_id=run_id,
                insights=batch_insights,
                started_at=now,
                scoped_tickers=batch_tickers,
            )

        assert result.insights_written == 10
        assert result.recommendations_written == 10
        written_tickers = {r["ticker"] for r in fake.store.get("agent_insights", [])}
        assert len(written_tickers) == 10
        assert not (written_tickers - {f"T{i:02d}" for i in range(10)}), (
            "Evidence writer must not write tickers outside the selected batch."
        )

    @pytest.mark.asyncio
    async def test_scoped_expiry_preserves_other_tickers_recommendations(self):
        """Scoped rec expiry only expires the batch tickers; others survive."""
        fake = _FakeSupabase()
        run_id = str(uuid.uuid4())
        old_run_id = str(uuid.uuid4())
        now = _now()

        # Pre-seed active recommendations for 34 tickers from a prior run
        all_tickers = [f"T{i:02d}" for i in range(34)]
        fake.store["recommendations"] = [
            {
                "ticker": t,
                "user_id": USER_A,
                "is_active": True,
                "agent_run_id": old_run_id,
                "action": "HOLD",
                "created_at": _iso_ago(50, now),
            }
            for t in all_tickers
        ]

        # Write a new batch for only the first 10 tickers
        batch_tickers = all_tickers[:10]
        batch_insights = [_SimpleInsight(t) for t in batch_tickers]

        with _patch_writer_client(fake):
            result = await write_analyst_evidence(
                user_id=uuid.UUID(USER_A),
                agent_run_id=run_id,
                insights=batch_insights,
                started_at=now,
                scoped_tickers=batch_tickers,
            )

        assert result.recommendations_written == 10

        recs = fake.store.get("recommendations", [])
        # Batch tickers' OLD recs are expired; NEW recs are active
        expired_batch = [
            r for r in recs
            if r["ticker"] in batch_tickers
            and r.get("is_active") is False
            and r["agent_run_id"] == old_run_id
        ]
        active_batch = [
            r for r in recs
            if r["ticker"] in batch_tickers
            and r.get("is_active") is True
            and r["agent_run_id"] == run_id
        ]
        assert len(expired_batch) == 10, "Old batch recs must be expired"
        assert len(active_batch) == 10, "New batch recs must be active"

        # Non-batch tickers' recs must be UNTOUCHED (still active from old run)
        non_batch = [t for t in all_tickers if t not in batch_tickers]
        untouched = [
            r for r in recs
            if r["ticker"] in non_batch and r.get("is_active") is True
        ]
        assert len(untouched) == 24, (
            f"Non-batch ticker recs must survive scoped pass. "
            f"Expected 24 untouched, got {len(untouched)}"
        )

    @pytest.mark.asyncio
    async def test_regression_no_scoping_would_write_34_proving_old_bug(self):
        """Regression: without scoped_tickers, full-portfolio expiry wipes non-batch recs.

        This test proves the old behavior (no scope) would incorrectly expire ALL
        other tickers' active recommendations — confirming the fix is necessary.
        """
        fake = _FakeSupabase()
        run_id = str(uuid.uuid4())
        old_run_id = str(uuid.uuid4())
        now = _now()

        all_tickers = [f"T{i:02d}" for i in range(34)]
        # Pre-seed active recs for all 34 tickers from an older run
        fake.store["recommendations"] = [
            {
                "ticker": t,
                "user_id": USER_A,
                "is_active": True,
                "agent_run_id": old_run_id,
                "action": "HOLD",
                "created_at": _iso_ago(50, now),
            }
            for t in all_tickers
        ]

        # Write only 10 insights WITHOUT scoped_tickers (simulates old bug)
        batch_insights = [_SimpleInsight(all_tickers[i]) for i in range(10)]

        with _patch_writer_client(fake):
            result = await write_analyst_evidence(
                user_id=uuid.UUID(USER_A),
                agent_run_id=run_id,
                insights=batch_insights,
                started_at=now,
                scoped_tickers=None,  # no scoping → old full-expiry behavior
            )

        assert result.recommendations_written == 10

        recs = fake.store.get("recommendations", [])
        # WITHOUT scoping, all 34 old recs are expired (the old bug)
        still_active_old = [
            r for r in recs
            if r.get("is_active") is True and r["agent_run_id"] == old_run_id
        ]
        assert len(still_active_old) == 0, (
            "Without scoped_tickers, ALL old recs are expired — "
            "proving scoped_tickers is necessary to protect non-batch evidence."
        )

    @pytest.mark.asyncio
    async def test_certification_not_published_until_all_34_pass(self):
        """Worker does not write a certified snapshot until all 34 holdings complete."""
        fake = _FakeSupabase()
        all_tickers = [f"T{i:02d}" for i in range(34)]
        adapter = _FakeAnalystAdapter()  # all succeed
        _enqueue_tickers(fake, all_tickers)

        worker = _make_worker(fake, adapter, max_jobs_per_run=10)

        # First 3 passes — jobs remain for later passes
        for _ in range(3):
            r = await worker.run_once(now=_now())
            if r.claimed_job_count > 0:
                # Certification snapshot must not be written mid-way
                # (the worker in tests uses _FakeAnalystAdapter which never
                # triggers prewarm, but the job store must not be drained)
                pending_or_retryable = r.remaining_pending_or_retryable
                if pending_or_retryable > 0:
                    assert "intel_v3_snapshots" not in fake.store or not fake.store.get("intel_v3_snapshots"), (
                        "Snapshot must not be written while jobs remain pending."
                    )

        # Final pass — drain remaining jobs
        r4 = await worker.run_once(now=_now())
        all_succeeded = {r["ticker"] for r in fake.rows() if r.get("status") == JOB_SUCCEEDED}
        assert len(all_succeeded) == 34
        # run_resumable=False after all 34 complete
        assert r4.run_resumable is False or r4.claimed_job_count == 0

    @pytest.mark.asyncio
    async def test_backend_scoped_to_selected_batch_via_custom_backend(self):
        """Verify that default_full_portfolio_agent_orchestrator_backend receives
        selected_tickers as the analysis scope — not the full portfolio.

        Uses a custom backend that records which tickers were requested so we can
        assert the scope was bounded to the worker batch (not all 34 positions).
        """
        fake_db = _FakeSupabase()
        run_id = str(uuid.uuid4())
        now = _now()

        # Seed a 10-ticker job batch from a 34-ticker portfolio
        all_tickers = [f"T{i:02d}" for i in range(34)]
        batch_tickers = all_tickers[:10]
        for t in all_tickers:
            fake_db.store.setdefault(TABLE, []).append({
                "id": str(uuid.uuid4()),
                "user_id": USER_A,
                "ticker": t,
                "status": JOB_PENDING,
                "refresh_window": "2026-05-15",
                "attempts": 0,
                "next_retry_at": now.isoformat(),
                "requested_at": _iso_ago(2, now),
                "prior_action": "HOLD",
                "weight_pct": 2.9,
                "evidence_age_hours_at_request": 200.0,
                "max_attempts": 5,
            })

        requested_tickers_log: list[list[str]] = []

        async def _recording_backend(uid, selected, started):
            """Records which tickers were passed as the analysis scope."""
            requested_tickers_log.append(list(selected))
            # Write minimal evidence rows so readback succeeds
            with _patch_writer_client(fake_db):
                await write_analyst_evidence(
                    user_id=uid,
                    agent_run_id=run_id,
                    insights=[_SimpleInsight(t) for t in selected],
                    started_at=started,
                    scoped_tickers=list(selected),
                )
            with patch("app.database.get_supabase_client", return_value=fake_db):
                return await _read_post_run_evidence(
                    uid, selected, run_id, started,
                    agent_run_status="completed",
                    agent_run_insight_count=len(selected),
                )

        def _factory(uid):
            return FullPortfolioAnalystRefreshAdapter(
                user_id=uid,
                run_backend=_recording_backend,
                budget=FullPortfolioAnalystRefreshBudget(),
            )

        worker = AnalystRefreshWorker(
            client=fake_db,
            adapter_factory=_factory,
            max_jobs_per_run=10,
        )
        result = await worker.run_once(now=now)

        assert result.claimed_job_count == 10
        assert len(requested_tickers_log) == 1
        assert len(requested_tickers_log[0]) == 10, (
            f"Backend received {len(requested_tickers_log[0])} tickers; "
            "expected exactly 10 (the worker batch). "
            "The orchestrator must be scoped to the selected batch — "
            "not the full 34-ticker portfolio."
        )
        # All 10 batch tickers appeared in the backend request
        received = {t.upper() for t in requested_tickers_log[0]}
        expected = {t.upper() for t in batch_tickers}
        assert received == expected, (
            f"Backend received {received}; expected {expected}"
        )
