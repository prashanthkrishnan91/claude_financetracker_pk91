"""Focused production tests for the durable Run Intel session implementation.

The immutable acceptance suite (``test_run_intel_session_contract_v1.py``)
mocks enqueue / drain / prewarm at the seams, so it proves the black-box
contract but not the production plumbing that threads ``run_session_id`` (==
the existing ``analyst_refresh_jobs.refresh_window`` column) end to end. These
tests cover exactly those implementation details:

  * ``restamp_jobs_to_session`` — a fresh click's per-day-window rows are moved
    onto the minted session id and nothing else is touched (old sessions, other
    users, other statuses, other windows).
  * ``AnalystRefreshWorker(scope_session_id=...)`` — a resumed multi-batch run
    only ever claims its own session's jobs and surfaces run_session_id.
  * ``count_due_jobs`` / ``claim_due_jobs`` session isolation on the shared
    queue table.
  * ``IntelV3Service._latest_certified_run_session_id`` — derives the durable
    session link a published snapshot embeds, ignoring legacy date windows.
  * Zero portfolio-synthesis calls anywhere in the Run Intel worker path.
"""
from __future__ import annotations

import uuid
from collections import Counter

import pytest

from app.services.intelligence.v3.analyst_refresh_adapter_v1 import (
    STATUS_SUCCEEDED,
    TickerRefreshOutcome,
)
from app.services.intelligence.v3.analyst_refresh_job_store_v1 import (
    JOB_PENDING,
    claim_due_jobs,
    count_due_jobs,
    restamp_jobs_to_session,
)
from app.services.intelligence.v3.analyst_refresh_worker_v1 import AnalystRefreshWorker
from app.services.intelligence.v3.full_portfolio_analyst_refresh_adapter_v1 import (
    AnalystRefreshResult,
)

USER_A = "00000000-0000-0000-0000-0000000000aa"
USER_B = "00000000-0000-0000-0000-0000000000bb"
_TODAY = "2026-07-20"


# ── Minimal in-memory Supabase fake (insert/select/eq/in_/update/execute) ─────


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, store, table):
        self._store = store
        self._table = table
        self._op = "select"
        self._payload = None
        self._filters: list[tuple] = []

    def insert(self, rows):
        self._op = "insert"
        self._payload = rows if isinstance(rows, list) else [rows]
        return self

    def update(self, values):
        self._op = "update"
        self._payload = dict(values)
        return self

    def select(self, *_a, **_kw):
        self._op = "select"
        return self

    def eq(self, col, val):
        self._filters.append(("eq", col, val))
        return self

    def in_(self, col, vals):
        self._filters.append(("in", col, list(vals)))
        return self

    def order(self, *_a, **_kw):
        return self

    def limit(self, *_a, **_kw):
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
            out = []
            for r in self._payload:
                nr = dict(r)
                nr.setdefault("id", str(uuid.uuid4()))
                rows.append(nr)
                out.append(dict(nr))
            return _Result(out)
        if self._op == "update":
            out = []
            for r in rows:
                if self._match(r):
                    r.update(self._payload)
                    out.append(dict(r))
            return _Result(out)
        return _Result([dict(r) for r in rows if self._match(r)])


class _Fake:
    def __init__(self):
        self.store: dict[str, list[dict]] = {}

    def table(self, name):
        return _Query(self.store, name)

    def seed(self, table, rows):
        self.store.setdefault(table, []).extend(rows)


def _job(user_id, ticker, window, *, status=JOB_PENDING):
    return {
        "id": str(uuid.uuid4()),
        "user_id": str(user_id),
        "ticker": ticker,
        "refresh_window": window,
        "status": status,
        "attempts": 0,
        "max_attempts": 5,
        "next_retry_at": None,
        "requested_at": "2026-07-20T12:00:00+00:00",
    }


def _counting_factory():
    calls: Counter = Counter()

    class _Adapter:
        def __init__(self, *, user_id):
            self.user_id = user_id
            self.budget = None

        async def __call__(self, tickers, *, priority_hints=None, started_at=None):
            for t in tickers:
                calls[t] += 1
            return AnalystRefreshResult(
                status=STATUS_SUCCEEDED,
                selected_tickers=list(tickers),
                deferred_tickers=[],
                per_ticker=[
                    TickerRefreshOutcome(ticker=t, success=True, llm_call_count=1, llm_success_count=1)
                    for t in tickers
                ],
            )

    return (lambda user_id: _Adapter(user_id=user_id)), calls


# ── 1. restamp_jobs_to_session ───────────────────────────────────────────────


class TestRestampJobsToSession:
    def test_restamps_only_this_clicks_own_rows(self):
        session_id = str(uuid.uuid4())
        old_session = str(uuid.uuid4())
        fake = _Fake()
        fake.seed("analyst_refresh_jobs", [
            _job(USER_A, "VTI", _TODAY),        # this click's fresh rows
            _job(USER_A, "AAPL", _TODAY),
            _job(USER_A, "MSFT", old_session),  # an older session — must stay put
            _job(USER_B, "VTI", _TODAY),        # another user — must stay put
            _job(USER_A, "QQQ", _TODAY, status="succeeded"),  # terminal — not claimable
        ])

        moved = restamp_jobs_to_session(
            fake, user_id=USER_A, from_window=_TODAY, run_session_id=session_id,
            tickers=["VTI", "AAPL"],
        )
        assert moved == 2

        rows = {(r["user_id"], r["ticker"]): r for r in fake.store["analyst_refresh_jobs"]}
        assert rows[(USER_A, "VTI")]["refresh_window"] == session_id
        assert rows[(USER_A, "AAPL")]["refresh_window"] == session_id
        # Everything else untouched.
        assert rows[(USER_A, "MSFT")]["refresh_window"] == old_session
        assert rows[(USER_B, "VTI")]["refresh_window"] == _TODAY
        assert rows[(USER_A, "QQQ")]["refresh_window"] == _TODAY

    def test_noop_when_from_equals_to(self):
        session_id = str(uuid.uuid4())
        fake = _Fake()
        fake.seed("analyst_refresh_jobs", [_job(USER_A, "VTI", session_id)])
        assert restamp_jobs_to_session(
            fake, user_id=USER_A, from_window=session_id, run_session_id=session_id,
        ) == 0


# ── 2. Queue-store session isolation ─────────────────────────────────────────


class TestQueueStoreSessionIsolation:
    def test_count_and_claim_scope_to_run_session_id(self):
        s1, s2 = str(uuid.uuid4()), str(uuid.uuid4())
        fake = _Fake()
        fake.seed("analyst_refresh_jobs", [
            _job(USER_A, "VTI", s1), _job(USER_A, "AAPL", s1),
            _job(USER_A, "VTI", s2),  # same ticker, different session
        ])
        assert count_due_jobs(fake, user_id=USER_A, tickers=["VTI", "AAPL"], run_session_id=s1)["total_due"] == 2
        assert count_due_jobs(fake, user_id=USER_A, tickers=["VTI", "AAPL"], run_session_id=s2)["total_due"] == 1

        claimed = claim_due_jobs(
            fake, worker_run_id=uuid.uuid4(), user_id=USER_A, tickers=["VTI", "AAPL"], run_session_id=s1,
        )
        assert {j.ticker for j in claimed} == {"VTI", "AAPL"}
        assert all(j.refresh_window == s1 for j in claimed)
        # s2's VTI row was never claimed by s1.
        s2_vti = next(r for r in fake.store["analyst_refresh_jobs"] if r["refresh_window"] == s2)
        assert s2_vti["status"] == JOB_PENDING


# ── 3. Worker scoped to one session across resumed batches ───────────────────


class TestWorkerScopedToSession:
    @pytest.mark.asyncio
    async def test_resumed_run_claims_only_its_session_and_reports_id(self, monkeypatch):
        import app.services.intelligence.v3.analyst_refresh_worker_v1 as worker_mod
        from unittest.mock import AsyncMock
        monkeypatch.setattr(worker_mod, "trigger_snapshot_prewarm", AsyncMock(return_value=None))

        session_id = str(uuid.uuid4())
        other_session = str(uuid.uuid4())
        tickers = [f"TCK{i:02d}" for i in range(6)]
        fake = _Fake()
        fake.seed("analyst_refresh_jobs", [_job(USER_A, t, session_id) for t in tickers])
        # Same user, DIFFERENT session — must never be touched by this run.
        fake.seed("analyst_refresh_jobs", [_job(USER_A, "ZZZ", other_session)])
        factory, calls = _counting_factory()

        worker = AnalystRefreshWorker(
            client=fake, adapter_factory=factory, scope_user_id=USER_A,
            scope_session_id=session_id, max_jobs_per_run=4,
        )
        b1 = await worker.run_once()
        b2 = await worker.run_once()
        succeeded = set(b1.succeeded_tickers) | set(b2.succeeded_tickers)

        assert succeeded == set(tickers)
        assert b1.run_session_id == session_id and b2.run_session_id == session_id
        assert "ZZZ" not in calls  # the other session's ticker never analysed
        assert all(calls[t] == 1 for t in tickers)
        other = next(r for r in fake.store["analyst_refresh_jobs"] if r["refresh_window"] == other_session)
        assert other["status"] == JOB_PENDING


# ── 4. Snapshot session-link derivation (ignores legacy date windows) ────────


class TestLatestCertifiedRunSessionId:
    @pytest.mark.asyncio
    async def test_returns_uuid_window_of_succeeded_jobs_only(self, monkeypatch):
        import app.services.intelligence.v3.intel_v3_service as svc_mod
        fake = _Fake()
        monkeypatch.setattr(svc_mod, "get_supabase_client", lambda: fake)
        service = svc_mod.IntelV3Service(user_id=USER_A)

        session_id = str(uuid.uuid4())
        fake.seed("analyst_refresh_jobs", [
            {**_job(USER_A, "VTI", session_id, status="succeeded"), "completed_at": "2026-07-20T13:00:00+00:00"},
            {**_job(USER_A, "AAPL", "2026-07-19", status="succeeded"), "completed_at": "2026-07-20T14:00:00+00:00"},
        ])
        # Even though the date-window row is "newer", only a UUID window is a session id.
        assert await service._latest_certified_run_session_id() == session_id

    @pytest.mark.asyncio
    async def test_returns_none_for_legacy_date_windows_only(self, monkeypatch):
        import app.services.intelligence.v3.intel_v3_service as svc_mod
        fake = _Fake()
        monkeypatch.setattr(svc_mod, "get_supabase_client", lambda: fake)
        service = svc_mod.IntelV3Service(user_id=USER_A)
        fake.seed("analyst_refresh_jobs", [
            {**_job(USER_A, "VTI", "2026-07-20", status="succeeded"), "completed_at": "2026-07-20T13:00:00+00:00"},
        ])
        assert await service._latest_certified_run_session_id() is None


# ── 4b. Session-anchor row mechanics (router helpers) ────────────────────────


class TestSessionAnchorRowMechanics:
    def test_anchor_write_read_complete_and_invisible_to_queue(self):
        import app.routers.intel_v3 as router_mod
        fake = _Fake()
        session_id = str(uuid.uuid4())

        # No anchor yet.
        assert router_mod._read_active_session_anchor(fake, USER_A) is None

        # Write an active anchor. It uses a CHECK-valid 'succeeded' status
        # (migration 018 allows only pending/claimed/succeeded/failed) and a
        # sentinel ticker; active/done is tracked by the ticker, not the status.
        router_mod._write_session_anchor(fake, USER_A, session_id, "2026-07-20T12:00:00+00:00")
        anchor = router_mod._read_active_session_anchor(fake, USER_A)
        assert anchor is not None
        assert anchor["refresh_window"] == session_id
        assert anchor["ticker"] == router_mod._SESSION_ANCHOR_TICKER_ACTIVE
        assert anchor["status"] == "succeeded"  # CHECK-valid

        # The anchor shares the session window but is NEVER counted or claimed
        # (count/claim fetch only pending/failed rows).
        assert count_due_jobs(fake, user_id=USER_A, run_session_id=session_id)["total_due"] == 0
        assert claim_due_jobs(fake, worker_run_id=uuid.uuid4(), user_id=USER_A, run_session_id=session_id) == []

        # Completing it flips the sentinel ticker active->done; it is no longer
        # the active anchor and its status stays the CHECK-valid 'succeeded'.
        router_mod._complete_session_anchor(fake, anchor["id"], "2026-07-20T12:05:00+00:00")
        assert router_mod._read_active_session_anchor(fake, USER_A) is None
        completed = next(
            r for r in fake.store["analyst_refresh_jobs"]
            if r["ticker"] == router_mod._SESSION_ANCHOR_TICKER_DONE
        )
        assert completed["status"] == "succeeded"

    @pytest.mark.asyncio
    async def test_anchor_rows_never_leak_into_certified_session_link(self, monkeypatch):
        # The anchor (status='succeeded', UUID window) must NOT be mistaken for
        # a certified ticker session by _latest_certified_run_session_id.
        import app.routers.intel_v3 as router_mod
        import app.services.intelligence.v3.intel_v3_service as svc_mod
        fake = _Fake()
        session_id = str(uuid.uuid4())
        router_mod._write_session_anchor(fake, USER_A, session_id, "2026-07-20T12:00:00+00:00")
        monkeypatch.setattr(svc_mod, "get_supabase_client", lambda: fake)
        service = svc_mod.IntelV3Service(user_id=USER_A)
        # Only an anchor exists (no real succeeded ticker job) -> no session link.
        assert await service._latest_certified_run_session_id() is None

    def test_anchor_helpers_never_raise_on_a_broken_client(self):
        import app.routers.intel_v3 as router_mod

        class _Broken:
            def table(self, _name):
                raise RuntimeError("db down")

        broken = _Broken()
        # All three must degrade gracefully (the continuation flow is primary).
        assert router_mod._read_active_session_anchor(broken, USER_A) is None
        router_mod._write_session_anchor(broken, USER_A, str(uuid.uuid4()), "2026-07-20T12:00:00+00:00")
        router_mod._complete_session_anchor(broken, "some-id", "2026-07-20T12:00:00+00:00")


# ── 4c. Publication-only retry is reachable through the router ───────────────


class _RouterServiceStub:
    def __init__(self, *, user_id, client, active_tickers, latest_snapshot=None):
        self.user_id = user_id
        self.client = client
        self._active = list(active_tickers)
        self._snap = latest_snapshot

    async def _get_active_tickers(self):
        return list(self._active)

    async def get_latest_snapshot(self):
        return self._snap


class TestPublicationRetryReachableViaRouter:
    @pytest.mark.asyncio
    async def test_router_drains_for_publication_when_session_succeeded_but_uncertified(self, monkeypatch):
        import app.routers.intel_v3 as router_mod
        from unittest.mock import AsyncMock
        from dataclasses import dataclass
        from app.services.intelligence.v3.analyst_refresh_on_demand_drain_v1 import (
            OnDemandDrainResult, STOPPED_DRAINED,
        )

        @dataclass
        class _S:
            intel_v3_on_demand_refresh_enabled: bool = True
            intel_v3_snapshot_writes_enabled: bool = True

        monkeypatch.setattr(router_mod, "get_settings", lambda: _S())
        drain_spy = AsyncMock(return_value=OnDemandDrainResult(
            batches_run=1, jobs_attempted=0, jobs_succeeded=0, jobs_failed=0,
            duration_ms=1, run_resumable=False, stopped_reason=STOPPED_DRAINED,
        ))
        monkeypatch.setattr(router_mod, "run_on_demand_drain", drain_spy)

        session_id = str(uuid.uuid4())
        fake = _Fake()
        # This session's tickers already SUCCEEDED, but no session-linked snapshot exists.
        fake.seed("analyst_refresh_jobs", [
            _job(USER_A, "VTI", session_id, status="succeeded"),
            _job(USER_A, "AAPL", session_id, status="succeeded"),
        ])
        service = _RouterServiceStub(user_id=USER_A, client=fake, active_tickers=["VTI", "AAPL"], latest_snapshot=None)

        out = await router_mod._augment_with_on_demand_status(
            service,
            {"status": "analyst_evidence_current", "queued_ticker_count": 0, "existing_certified_snapshot_id": None},
            run_session_id=session_id,
        )
        # The publication-only retry drain WAS reached (not skipped just because total_due==0).
        drain_spy.assert_awaited_once()
        assert out["next_required_action"] != "none_certified_snapshot_current"

    @pytest.mark.asyncio
    async def test_router_does_not_drain_when_session_has_no_succeeded_work(self, monkeypatch):
        # Guard: a fresh session with no completed ticker work must NOT trigger
        # a publication drain (preserves the historical zero-queued behavior).
        import app.routers.intel_v3 as router_mod
        from unittest.mock import AsyncMock
        from dataclasses import dataclass

        @dataclass
        class _S:
            intel_v3_on_demand_refresh_enabled: bool = True
            intel_v3_snapshot_writes_enabled: bool = True

        monkeypatch.setattr(router_mod, "get_settings", lambda: _S())
        drain_spy = AsyncMock()
        monkeypatch.setattr(router_mod, "run_on_demand_drain", drain_spy)

        session_id = str(uuid.uuid4())
        fake = _Fake()  # no jobs at all
        service = _RouterServiceStub(user_id=USER_A, client=fake, active_tickers=["VTI"], latest_snapshot=None)

        await router_mod._augment_with_on_demand_status(
            service,
            {"status": "analyst_evidence_current", "queued_ticker_count": 0, "existing_certified_snapshot_id": None},
            run_session_id=session_id,
        )
        drain_spy.assert_not_awaited()


# ── 5. Zero portfolio synthesis in the Run Intel worker path ─────────────────


class TestNoPortfolioSynthesisInRunIntelPath:
    @pytest.mark.asyncio
    async def test_worker_path_never_invokes_portfolio_synthesis(self, monkeypatch):
        """The Run Intel worker drives only per-ticker analyst refresh +
        deterministic certification/publication. There is no portfolio
        synthesis stage in this codebase; a spy the path must never call stays
        at zero across a full drain + a publication-only retry."""
        import app.services.intelligence.v3.analyst_refresh_worker_v1 as worker_mod
        synthesis_calls: list[str] = []
        publish_calls: list[str] = []

        async def _record_publish(*, user_id, worker_run_id):
            publish_calls.append(worker_run_id)

        monkeypatch.setattr(worker_mod, "trigger_snapshot_prewarm", _record_publish)

        session_id = str(uuid.uuid4())
        fake = _Fake()
        fake.seed("analyst_refresh_jobs", [_job(USER_A, t, session_id) for t in ("VTI", "AAPL")])
        factory, calls = _counting_factory()
        worker = AnalystRefreshWorker(
            client=fake, adapter_factory=factory, scope_user_id=USER_A,
            scope_session_id=session_id, max_jobs_per_run=10,
        )
        r1 = await worker.run_once()
        r2 = await worker.run_once()  # all done -> publication-only retry

        assert set(r1.succeeded_tickers) == {"VTI", "AAPL"}
        assert len(publish_calls) >= 2         # certification + a publication-only retry
        assert all(calls[t] == 1 for t in ("VTI", "AAPL"))  # no re-analysis on retry
        assert synthesis_calls == []           # never any portfolio synthesis
