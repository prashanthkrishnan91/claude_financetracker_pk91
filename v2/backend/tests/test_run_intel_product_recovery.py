"""Run Intel product recovery — Part A of the Advisor product-recovery PR.

Proven production failure being fixed (see SETUP_AUDIT.md Cluster 3 / PR body):
  * analyst jobs were processed successfully; pending/retryable durable jobs
    remained; snapshot publication was deferred while those jobs remained;
  * the request lasted ~148s and appeared hung;
  * later Run Intel clicks queued zero new jobs because analyst evidence was
    considered current, so the on-demand drain — gated on
    ``queued_ticker_count > 0`` — never picked the leftover jobs back up;
  * no new certified snapshot was ever published.

Contract under test:
  A1. ``queued_ticker_count == 0`` plus existing current-user pending jobs
      still invokes bounded processing, scoped to the current user.
  A2. The on-demand quantum is small and the adapter deadline is threaded
      through so one request cannot materially exceed a production-safe
      wall-clock bound.
  A4. Zero newly queued jobs can still produce successful publication after
      existing work drains; a publication failure is a terminal retry, never
      a false "nothing to do."
"""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from unittest.mock import AsyncMock

import pytest

from app.routers import intel_v3 as router_mod
from app.services.intelligence.v3.analyst_refresh_job_store_v1 import (
    JOB_FAILED,
    JOB_PENDING,
    claim_due_jobs,
    count_due_jobs,
)
from app.services.intelligence.v3.analyst_refresh_on_demand_drain_v1 import (
    STOPPED_DRAINED,
    OnDemandDrainResult,
    run_on_demand_drain,
)
from app.services.intelligence.v3.analyst_refresh_worker_v1 import AnalystRefreshWorker

USER_A = "00000000-0000-0000-0000-0000000000aa"
USER_B = "00000000-0000-0000-0000-0000000000bb"


def _now():
    from datetime import datetime, timezone

    return datetime(2026, 7, 19, 12, 0, 0, tzinfo=timezone.utc)


# ── In-memory Supabase fake (trimmed copy of the Stage 3.2 test fake) ────────


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

    def order(self, col, desc=False):
        self._order_col = col
        return self

    def limit(self, _n):
        return self

    def _match(self, row) -> bool:
        for kind, col, val in self._filters:
            rv = row.get(col)
            if kind == "eq" and rv != val:
                return False
            if kind == "in" and rv not in val:
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
        return _FakeResult(out)


class _FakeSupabase:
    def __init__(self):
        self.store: dict[str, list[dict]] = {}

    def table(self, name):
        return _FakeQuery(self.store, name)

    def seed(self, table, rows):
        self.store.setdefault(table, []).extend(rows)


def _job_row(user_id: str, ticker: str, *, status=JOB_PENDING, attempts=0) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "ticker": ticker,
        "refresh_window": "2026-07-19",
        "status": status,
        "attempts": attempts,
        "max_attempts": 5,
        "next_retry_at": _now().isoformat(),
        "requested_at": _now().isoformat(),
    }


# ── A1a. Job store: user/ticker scoping ──────────────────────────────────────


class TestJobStoreScoping:
    def test_count_due_jobs_scoped_to_user_ignores_other_users(self):
        fake = _FakeSupabase()
        fake.seed("analyst_refresh_jobs", [
            _job_row(USER_A, "VTI"),
            _job_row(USER_B, "AAPL"),
        ])
        counts = count_due_jobs(fake, now=_now(), user_id=USER_A)
        assert counts["total_due"] == 1

        counts_unscoped = count_due_jobs(fake, now=_now())
        assert counts_unscoped["total_due"] == 2

    def test_count_due_jobs_scoped_to_tickers(self):
        fake = _FakeSupabase()
        fake.seed("analyst_refresh_jobs", [
            _job_row(USER_A, "VTI"),
            _job_row(USER_A, "QQQ"),
        ])
        counts = count_due_jobs(fake, now=_now(), user_id=USER_A, tickers=["VTI"])
        assert counts["total_due"] == 1

    def test_claim_due_jobs_scoped_to_user_never_claims_another_users_job(self):
        fake = _FakeSupabase()
        fake.seed("analyst_refresh_jobs", [
            _job_row(USER_A, "VTI"),
            _job_row(USER_B, "AAPL"),
        ])
        claimed = claim_due_jobs(
            fake, worker_run_id=uuid.uuid4(), now=_now(), user_id=USER_A,
        )
        assert [j.ticker for j in claimed] == ["VTI"]
        assert all(j.user_id == USER_A for j in claimed)

    def test_count_due_jobs_reports_earliest_retry_at_for_backoff_rows(self):
        """Small job-store extension (product-recovery Blocker 3): the
        earliest next_retry_at among failed_not_yet_due rows is surfaced so a
        caller reporting a backoff state can tell the user roughly when to
        expect the next retry."""
        fake = _FakeSupabase()
        later = "2099-06-01T00:00:00+00:00"
        earlier = "2099-01-01T00:00:00+00:00"
        row_later = _job_row(USER_A, "VTI", status=JOB_FAILED, attempts=1)
        row_later["next_retry_at"] = later
        row_earlier = _job_row(USER_A, "AAPL", status=JOB_FAILED, attempts=1)
        row_earlier["next_retry_at"] = earlier
        fake.seed("analyst_refresh_jobs", [row_later, row_earlier])

        counts = count_due_jobs(fake, now=_now(), user_id=USER_A)
        assert counts["failed_not_yet_due"] == 2
        assert counts["earliest_retry_at"] == earlier

    def test_count_due_jobs_earliest_retry_at_is_none_with_no_backoff_rows(self):
        fake = _FakeSupabase()
        fake.seed("analyst_refresh_jobs", [_job_row(USER_A, "VTI")])
        counts = count_due_jobs(fake, now=_now(), user_id=USER_A)
        assert counts["earliest_retry_at"] is None

    def test_claim_due_jobs_unscoped_keeps_existing_global_behavior(self):
        """The standalone always-on worker omits user_id/tickers and keeps
        claiming globally — unchanged from before this fix."""
        fake = _FakeSupabase()
        fake.seed("analyst_refresh_jobs", [
            _job_row(USER_A, "VTI"),
            _job_row(USER_B, "AAPL"),
        ])
        claimed = claim_due_jobs(fake, worker_run_id=uuid.uuid4(), now=_now())
        assert {j.ticker for j in claimed} == {"VTI", "AAPL"}


# ── A1b. AnalystRefreshWorker: scoping + adapter deadline clamp ──────────────


class _StubAdapter:
    """Records the budget it was constructed/clamped with; never calls an LLM."""

    def __init__(self, *, user_id, run_backend=None, budget=None):
        self.user_id = user_id
        self.budget = budget

    async def __call__(self, tickers, *, priority_hints=None, started_at=None):
        from app.services.intelligence.v3.analyst_refresh_adapter_v1 import (
            STATUS_SUCCEEDED,
            TickerRefreshOutcome,
        )
        from app.services.intelligence.v3.full_portfolio_analyst_refresh_adapter_v1 import (
            AnalystRefreshResult,
        )
        return AnalystRefreshResult(
            status=STATUS_SUCCEEDED,
            selected_tickers=list(tickers),
            deferred_tickers=[],
            per_ticker=[
                TickerRefreshOutcome(ticker=t, success=True, llm_call_count=1, llm_success_count=1)
                for t in tickers
            ],
        )


class TestWorkerScopingAndDeadline:
    @pytest.mark.asyncio
    async def test_worker_scoped_to_user_only_claims_that_users_jobs(self):
        fake = _FakeSupabase()
        fake.seed("analyst_refresh_jobs", [
            _job_row(USER_A, "VTI"),
            _job_row(USER_B, "AAPL"),
        ])

        def factory(user_id):
            from app.services.intelligence.v3.full_portfolio_analyst_refresh_adapter_v1 import (
                FullPortfolioAnalystRefreshBudget,
            )
            return _StubAdapter(user_id=user_id, budget=FullPortfolioAnalystRefreshBudget())

        worker = AnalystRefreshWorker(
            client=fake, adapter_factory=factory, scope_user_id=USER_A,
        )
        result = await worker.run_once(now=_now())
        assert result.claimed_job_count == 1
        assert result.succeeded_tickers == ["VTI"]

    @pytest.mark.asyncio
    async def test_adapter_budget_clamped_to_worker_deadline(self):
        """Part A2 root-cause fix: the adapter's own wait_for() budget must
        never exceed the caller's intended per-request bound, regardless of
        the adapter's own larger default (180s) — this is what turned a
        nominal 90s on-demand cap into a ~148s hung request."""
        fake = _FakeSupabase()
        fake.seed("analyst_refresh_jobs", [_job_row(USER_A, "VTI")])

        captured = {}

        def factory(user_id):
            from app.services.intelligence.v3.full_portfolio_analyst_refresh_adapter_v1 import (
                FullPortfolioAnalystRefreshBudget,
            )
            adapter = _StubAdapter(
                user_id=user_id,
                budget=FullPortfolioAnalystRefreshBudget(max_seconds=180.0),
            )
            captured["adapter"] = adapter
            return adapter

        worker = AnalystRefreshWorker(
            client=fake, adapter_factory=factory, scope_user_id=USER_A,
            max_adapter_seconds=20.0,
        )
        await worker.run_once(now=_now())
        assert captured["adapter"].budget.max_seconds == 20.0

    @pytest.mark.asyncio
    async def test_adapter_budget_never_widened_by_a_larger_deadline(self):
        fake = _FakeSupabase()
        fake.seed("analyst_refresh_jobs", [_job_row(USER_A, "VTI")])
        captured = {}

        def factory(user_id):
            from app.services.intelligence.v3.full_portfolio_analyst_refresh_adapter_v1 import (
                FullPortfolioAnalystRefreshBudget,
            )
            adapter = _StubAdapter(
                user_id=user_id,
                budget=FullPortfolioAnalystRefreshBudget(max_seconds=10.0),
            )
            captured["adapter"] = adapter
            return adapter

        worker = AnalystRefreshWorker(
            client=fake, adapter_factory=factory, scope_user_id=USER_A,
            max_adapter_seconds=90.0,
        )
        await worker.run_once(now=_now())
        # A smaller adapter-supplied budget is never widened.
        assert captured["adapter"].budget.max_seconds == 10.0


# ── A2. A slow fake analyst call proves no work starts past the deadline ────


class TestOnDemandDrainRespectsWallClockDeadline:
    @pytest.mark.asyncio
    async def test_slow_analyst_call_is_bounded_by_the_on_demand_deadline(self):
        """A backend whose single call would take far longer than the
        on-demand cap must be cut off close to that cap, not run to its own
        much larger default — proving the fix does not merely lower the
        *intended* cap on paper while a slow call still blows through it."""
        from app.services.intelligence.v3.full_portfolio_analyst_refresh_adapter_v1 import (
            FullPortfolioAnalystRefreshAdapter,
            FullPortfolioAnalystRefreshBudget,
        )

        fake = _FakeSupabase()
        fake.seed("analyst_refresh_jobs", [_job_row(USER_A, "VTI")])

        async def slow_backend(user_id, tickers, started_at):
            await asyncio.sleep(5.0)
            return {}

        def factory(user_id):
            return FullPortfolioAnalystRefreshAdapter(
                user_id=user_id,
                run_backend=slow_backend,
                budget=FullPortfolioAnalystRefreshBudget(max_seconds=180.0),
            )

        worker = AnalystRefreshWorker(
            client=fake, adapter_factory=factory,
            scope_user_id=USER_A, max_adapter_seconds=1.0,
        )
        started = time.monotonic()
        result = await worker.run_once(now=_now())
        elapsed = time.monotonic() - started
        # Cut off near the 1s clamp, nowhere close to the 5s backend sleep or
        # the adapter's 180s default.
        assert elapsed < 3.0
        assert result.claimed_job_count == 1
        assert result.succeeded_tickers == []


# ── A1c. Router: recognizes existing durable work when nothing new queued ──


class _FakeIntelService:
    def __init__(self, *, user_id, client, active_tickers, latest_snapshot=None):
        self.user_id = user_id
        self.client = client
        self._active_tickers_value = active_tickers
        self.get_latest_snapshot = AsyncMock(return_value=latest_snapshot)

    async def _get_active_tickers(self):
        return self._active_tickers_value


@dataclass
class _FakeSettings:
    intel_v3_on_demand_refresh_enabled: bool
    intel_v3_snapshot_writes_enabled: bool = True


class TestRouterRecognizesExistingDurableWork:
    @pytest.mark.asyncio
    async def test_zero_queued_but_existing_user_jobs_invokes_bounded_drain(self, monkeypatch):
        """The proven failure mode: queued_ticker_count==0 (gate says
        evidence current / a recert path ran) must not skip processing when
        this user still has claimable durable work left over."""
        settings = _FakeSettings(intel_v3_on_demand_refresh_enabled=True)
        monkeypatch.setattr(router_mod, "get_settings", lambda: settings)

        fake_client = _FakeSupabase()
        fake_client.seed("analyst_refresh_jobs", [_job_row(str(USER_A), "VTI")])

        drain_result = OnDemandDrainResult(
            batches_run=1, jobs_attempted=1, jobs_succeeded=1, jobs_failed=0,
            duration_ms=50, run_resumable=False, stopped_reason=STOPPED_DRAINED,
        )
        drain_spy = AsyncMock(return_value=drain_result)
        monkeypatch.setattr(router_mod, "run_on_demand_drain", drain_spy)

        service = _FakeIntelService(
            user_id=USER_A, client=fake_client, active_tickers=["VTI"],
            latest_snapshot={
                "snapshot_id": "new-1",
                "snapshot_source": "worker_certified",
                "evidence_freshness_state": "certified_current",
            },
        )
        result = {
            "status": "analyst_evidence_current",
            "queued_ticker_count": 0,
            "existing_certified_snapshot_id": "old-0",
        }

        out = await router_mod._augment_with_on_demand_status(service, result)

        drain_spy.assert_awaited_once()
        _, kwargs = drain_spy.call_args
        assert str(kwargs["user_id"]) == str(USER_A)
        assert kwargs["tickers"] == ["VTI"]
        assert out["snapshot_available_after_run"] is True
        assert out["next_required_action"] == "none_certified_snapshot_current"

    @pytest.mark.asyncio
    async def test_zero_queued_and_no_existing_work_stays_a_fast_noop(self, monkeypatch):
        settings = _FakeSettings(intel_v3_on_demand_refresh_enabled=True)
        monkeypatch.setattr(router_mod, "get_settings", lambda: settings)
        drain_spy = AsyncMock()
        monkeypatch.setattr(router_mod, "run_on_demand_drain", drain_spy)

        service = _FakeIntelService(
            user_id=USER_A, client=_FakeSupabase(), active_tickers=["VTI"],
            latest_snapshot={
                "snapshot_id": "current-1",
                "snapshot_source": "worker_certified",
                "evidence_freshness_state": "certified_current",
            },
        )
        result = {
            "status": "analyst_evidence_current",
            "queued_ticker_count": 0,
            "existing_certified_snapshot_id": "current-1",
        }

        out = await router_mod._augment_with_on_demand_status(service, result)

        drain_spy.assert_not_awaited()
        assert out["snapshot_available_after_run"] is True

    @pytest.mark.asyncio
    async def test_existing_work_drain_left_incomplete_reports_continue(self, monkeypatch):
        settings = _FakeSettings(intel_v3_on_demand_refresh_enabled=True)
        monkeypatch.setattr(router_mod, "get_settings", lambda: settings)

        drain_result = OnDemandDrainResult(
            batches_run=1, jobs_attempted=3, jobs_succeeded=3, jobs_failed=0,
            duration_ms=50, run_resumable=True, stopped_reason="runtime_cap_reached",
        )
        drain_spy = AsyncMock(return_value=drain_result)
        monkeypatch.setattr(router_mod, "run_on_demand_drain", drain_spy)

        fake_client = _FakeSupabase()
        fake_client.seed("analyst_refresh_jobs", [_job_row(str(USER_A), "VTI")])
        service = _FakeIntelService(
            user_id=USER_A, client=fake_client, active_tickers=["VTI", "AAPL", "QQQ"],
            latest_snapshot=None,
        )
        result = {
            "status": "analyst_evidence_current",
            "queued_ticker_count": 0,
            "existing_certified_snapshot_id": None,
        }

        out = await router_mod._augment_with_on_demand_status(service, result)

        drain_spy.assert_awaited_once()
        assert out["snapshot_available_after_run"] is False
        assert (
            out["next_required_action"]
            == "reclick_run_intel_or_run_worker_entrypoint_to_continue_draining"
        )
        # Never falsely "nothing was stale" once a drain of real work ran.
        assert out["next_required_action"] != "none_no_stale_evidence_to_refresh"

    @pytest.mark.asyncio
    async def test_publication_failure_after_existing_work_drain_is_a_terminal_retry(
        self, monkeypatch
    ):
        """A drain that fully resolves existing work but never produces a
        provably new certified snapshot must be an honest retry — never an
        endless partial state and never a false "nothing needed doing.\""""
        settings = _FakeSettings(intel_v3_on_demand_refresh_enabled=True)
        monkeypatch.setattr(router_mod, "get_settings", lambda: settings)

        drain_result = OnDemandDrainResult(
            batches_run=1, jobs_attempted=1, jobs_succeeded=1, jobs_failed=0,
            duration_ms=50, run_resumable=False, stopped_reason=STOPPED_DRAINED,
        )
        drain_spy = AsyncMock(return_value=drain_result)
        monkeypatch.setattr(router_mod, "run_on_demand_drain", drain_spy)

        fake_client = _FakeSupabase()
        fake_client.seed("analyst_refresh_jobs", [_job_row(str(USER_A), "VTI")])
        service = _FakeIntelService(
            user_id=USER_A, client=fake_client, active_tickers=["VTI"],
            latest_snapshot={
                "snapshot_id": "unchanged-1",
                "snapshot_source": "worker_certified",
                "evidence_freshness_state": "certified_current",
            },
        )
        result = {
            "status": "analyst_evidence_current",
            "queued_ticker_count": 0,
            "existing_certified_snapshot_id": "unchanged-1",
        }

        out = await router_mod._augment_with_on_demand_status(service, result)

        drain_spy.assert_awaited_once()
        assert out["snapshot_available_after_run"] is False
        assert out["next_required_action"] == "reclick_run_intel_to_retry"

    @pytest.mark.asyncio
    async def test_no_active_holdings_never_triggers_existing_work_check(self, monkeypatch):
        settings = _FakeSettings(intel_v3_on_demand_refresh_enabled=True)
        monkeypatch.setattr(router_mod, "get_settings", lambda: settings)
        drain_spy = AsyncMock()
        monkeypatch.setattr(router_mod, "run_on_demand_drain", drain_spy)

        service = _FakeIntelService(
            user_id=USER_A, client=_FakeSupabase(), active_tickers=[], latest_snapshot=None,
        )
        result = {
            "status": "no_active_holdings",
            "queued_ticker_count": 0,
            "existing_certified_snapshot_id": None,
        }

        out = await router_mod._augment_with_on_demand_status(service, result)

        drain_spy.assert_not_awaited()
        assert out["next_required_action"] == "add_positions_before_running_intel"
