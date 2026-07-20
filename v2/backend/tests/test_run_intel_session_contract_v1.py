"""Run Intel session contract — immutable black-box acceptance suite (v1).

Production failure being specified (see CLAUDE.md prompt for the full
contract): Run Intel has no durable identity spanning one explicit user
click and its automatic continuation requests. Today's stack infers "is this
session done?" from three unscoped, shared signals:

  * ``analyst_refresh_jobs`` rows scoped only by ``(user_id, ticker)`` —
    never by which Run Intel click created them (see
    ``analyst_refresh_job_store_v1.count_due_jobs`` / ``claim_due_jobs``,
    which accept ``user_id`` / ``tickers`` but no session concept at all);
  * whichever ``analyst_refresh_jobs`` rows happen to remain claimable;
  * whichever ``intel_v3_snapshots`` row is currently ``is_active`` for the
    user (see ``IntelV3Service.get_latest_snapshot`` /
    ``app.routers.intel_v3._augment_with_on_demand_status``).

None of ``IntelV3Service.enqueue_run_v3()``, the ``POST /intel/v3/run``
response, ``AnalystRefreshWorker.run_once()``'s ``WorkerRunResult``, or the
``intel_v3_snapshots`` row shape carries a ``run_session_id`` (or any
equivalent durable session identity) anywhere in current production code —
confirmed by direct inspection of ``intel_v3_service.py``,
``analyst_refresh_job_store_v1.py``, ``analyst_refresh_worker_v1.py``, and
``app/routers/intel_v3.py``. This file is immutable acceptance evidence for
the later implementation agent that closes that gap.

Test strategy — real entry points, deterministic edges:
  * ``analyst_refresh_job_store_v1.count_due_jobs`` / ``claim_due_jobs`` and
    ``analyst_refresh_worker_v1.AnalystRefreshWorker.run_once`` are called
    directly against an in-memory Supabase fake (adapted from
    ``test_run_intel_product_recovery.py``'s proven fake) — these are the
    real queue-store / worker boundaries.
  * ``app.routers.intel_v3.run_intel_v3`` (the real POST /run coroutine) and
    ``app.routers.intel_v3._augment_with_on_demand_status`` (the real
    session-completion gate) are called directly, with
    ``router_mod.IntelV3Service`` monkeypatched to a minimal test double
    (``_FakeIntelService``) standing in for the heavy real service — a
    clearly marked adapter seam, not an imagined new API. ``_FakeIntelService
    .get_latest_snapshot`` re-implements only ``IntelV3Service``'s documented
    "latest ``is_active`` row for this user" read against the same fake
    Supabase table, so the snapshot-completion boundary is still genuinely
    exercised.
  * ``trigger_snapshot_prewarm`` (the real certification/publication trigger
    called from inside ``AnalystRefreshWorker.run_once``) is monkeypatched
    per-test to a deterministic recorder/failer — swapping only the
    LLM/provider-adjacent edge, never the worker's own control flow.
  * There is no "portfolio synthesis" stage in this codebase today; where the
    product contract requires one to never block completion, this file
    tracks a local spy that current production code never calls (documented
    inline) rather than inventing a fake production API.

Every test below either fails today on an assertion that directly proves a
listed gap, or is a passing sanity check documenting that a *different* part
of the stack already behaves correctly (so the failing assertions are sharp,
not incidental).
"""
from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import AsyncMock

import pytest

from app.routers import intel_v3 as router_mod
from app.services.intelligence.v3 import analyst_refresh_worker_v1 as worker_mod
from app.services.intelligence.v3.analyst_refresh_adapter_v1 import (
    STATUS_SUCCEEDED,
    TickerRefreshOutcome,
)
from app.services.intelligence.v3.analyst_refresh_job_store_v1 import (
    JOB_PENDING,
    claim_due_jobs,
    count_due_jobs,
)
from app.services.intelligence.v3.analyst_refresh_on_demand_drain_v1 import (
    STOPPED_DRAINED,
    OnDemandDrainResult,
)
from app.services.intelligence.v3.analyst_refresh_worker_v1 import AnalystRefreshWorker
from app.services.intelligence.v3.full_portfolio_analyst_refresh_adapter_v1 import (
    AnalystRefreshResult,
)

USER_A = "00000000-0000-0000-0000-0000000000aa"
USER_B = "00000000-0000-0000-0000-0000000000bb"

_TODAY = "2026-07-20"
_YESTERDAY = "2026-07-19"


def _now() -> datetime:
    return datetime(2026, 7, 20, 12, 0, 0, tzinfo=timezone.utc)


# ── In-memory Supabase fake (adapted from test_run_intel_product_recovery.py) ─


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

    def order(self, _col, desc=False):
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


def _job_row(
    user_id: str,
    ticker: str,
    *,
    status: str = JOB_PENDING,
    attempts: int = 0,
    refresh_window: Optional[str] = None,
    next_retry_at: Optional[str] = None,
    max_attempts: int = 5,
    run_session_id: Optional[str] = None,
) -> dict:
    row = {
        "id": str(uuid.uuid4()),
        "user_id": str(user_id),
        "ticker": ticker,
        "refresh_window": refresh_window or _TODAY,
        "status": status,
        "attempts": attempts,
        "max_attempts": max_attempts,
        "next_retry_at": next_retry_at if next_retry_at is not None else _now().isoformat(),
        "requested_at": _now().isoformat(),
    }
    if run_session_id is not None:
        # Forward-compat seam: current production schema has no
        # run_session_id column yet — seeding it here only lets fixtures
        # tag which session a row conceptually belongs to.
        row["run_session_id"] = run_session_id
    return row


def _snapshot_row(
    user_id: str,
    snapshot_id: str,
    *,
    is_active: bool = True,
    source: str = "worker_certified",
    freshness: str = "certified_current",
    created_at: Optional[str] = None,
) -> dict:
    created_at = created_at or _now().isoformat()
    return {
        "id": str(uuid.uuid4()),
        "user_id": str(user_id),
        "is_active": is_active,
        "created_at": created_at,
        "snapshot_source": source,
        "payload": {
            "snapshot_id": snapshot_id,
            "snapshot_source": source,
            "evidence_freshness_state": freshness,
            "generated_at": created_at,
        },
    }


# ── Adapter-seam test double for IntelV3Service ──────────────────────────────
#
# Stands in for the real IntelV3Service at the router boundary so the router
# endpoint and _augment_with_on_demand_status (both real production code) can
# be exercised without standing up ReadOnlyEvidenceAdapter / AgentOrchestrator
# / real Supabase. get_latest_snapshot re-implements only the documented
# "latest is_active row for this user" read against the same fake table that
# analyst_refresh_job_store_v1 also operates on, so the snapshot-completion
# boundary is genuinely exercised rather than stubbed to a constant.


class _FakeIntelService:
    def __init__(self, *, user_id: str, client: _FakeSupabase, active_tickers: list[str]):
        self.user_id = user_id
        self.client = client
        self._active_tickers = list(active_tickers)
        self.enqueue_run_v3: Any = None  # set per-test via AsyncMock

    async def _get_active_tickers(self) -> list[str]:
        return list(self._active_tickers)

    async def get_latest_snapshot(self) -> Optional[dict]:
        rows = [
            r
            for r in self.client.store.get("intel_v3_snapshots", [])
            if r.get("user_id") == str(self.user_id) and r.get("is_active")
        ]
        if not rows:
            return None
        rows.sort(key=lambda r: r.get("created_at") or "", reverse=True)
        return dict(rows[0].get("payload") or {})


@dataclass
class _FakeSettings:
    intel_v3_on_demand_refresh_enabled: bool
    intel_v3_snapshot_writes_enabled: bool = True


def _make_user(user_id: str):
    return SimpleNamespace(id=user_id)


async def _call_router(
    monkeypatch,
    service: _FakeIntelService,
    *,
    enqueue_result: dict,
    on_demand_enabled: bool = True,
    snapshot_writes_enabled: bool = True,
    drain_result: Optional[OnDemandDrainResult] = None,
) -> dict:
    """Invoke the REAL `POST /intel/v3/run` coroutine with IntelV3Service
    swapped for the fake above. run_on_demand_drain is monkeypatched only
    when a caller wants a deterministic drain outcome without exercising a
    real AnalystRefreshWorker/orchestrator pass through the router."""
    settings = _FakeSettings(
        intel_v3_on_demand_refresh_enabled=on_demand_enabled,
        intel_v3_snapshot_writes_enabled=snapshot_writes_enabled,
    )
    monkeypatch.setattr(router_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(router_mod, "is_intel_v3_enabled", lambda: True)
    monkeypatch.setattr(router_mod, "IntelV3Service", lambda user_id: service)
    service.enqueue_run_v3 = AsyncMock(return_value=dict(enqueue_result))
    if drain_result is not None:
        monkeypatch.setattr(
            router_mod, "run_on_demand_drain", AsyncMock(return_value=drain_result)
        )
    return await router_mod.run_intel_v3(user=_make_user(service.user_id))


def _make_counting_factory():
    """A worker adapter factory that records exactly which tickers it was
    asked to refresh, and always succeeds them — used to prove per-ticker
    call-accounting (each ticker analysed at most/exactly once)."""
    calls: Counter = Counter()

    class _CountingAdapter:
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
                    TickerRefreshOutcome(
                        ticker=t, success=True, llm_call_count=1, llm_success_count=1,
                    )
                    for t in tickers
                ],
            )

    def factory(user_id):
        return _CountingAdapter(user_id=user_id)

    return factory, calls


# ── 1. Historical certified snapshot cannot complete a new session ──────────


class TestHistoricalSnapshotCannotCompleteNewSession:
    @pytest.mark.asyncio
    async def test_old_certified_snapshot_falsely_satisfies_new_session(self, monkeypatch):
        old_snapshot_id = "old-certified-snap-1"
        fake = _FakeSupabase()
        fake.seed("intel_v3_snapshots", [_snapshot_row(USER_A, old_snapshot_id)])
        # Old analyst evidence backing the old snapshot.
        fake.seed(
            "agent_insights",
            [{"user_id": USER_A, "ticker": "VTI", "created_at": _YESTERDAY + "T00:00:00+00:00"}],
        )
        service = _FakeIntelService(user_id=USER_A, client=fake, active_tickers=["VTI", "AAPL"])

        # A new Run Intel click on stale active holdings whose freshness gate
        # nonetheless classifies analyst evidence as already current (the
        # exact class of gap this contract closes: the gate's age-based
        # heuristic has no notion of "this session", so it can't distinguish
        # stale-for-a-new-click from fresh-from-an-old-click).
        result = await _call_router(
            monkeypatch,
            service,
            enqueue_result={
                "status": "analyst_evidence_current",
                "queued_ticker_count": 0,
                "existing_certified_snapshot_id": old_snapshot_id,
            },
        )

        latest = await service.get_latest_snapshot()
        # Sanity: no new snapshot was ever published for this click — the
        # latest snapshot is still the pre-existing historical one.
        assert latest["snapshot_id"] == old_snapshot_id

        # Contract requirement: a new Run Intel session must receive its own
        # durable run_session_id, and completion must never be reported from
        # a snapshot with no durable link to it.
        assert result.get("run_session_id") is not None, (
            "no durable run_session_id was created for this explicit Run "
            "Intel action"
        )
        assert result.get("next_required_action") != "none_certified_snapshot_current", (
            "the pre-existing historical snapshot satisfied a brand-new "
            "session's completion with no session-scoped link between them"
        )


# ── 2. Old queue rows cannot interfere with a new session ───────────────────


class TestOldQueueRowsCannotInterfereWithNewSession:
    def test_new_session_claims_only_its_own_session_scoped_rows(self):
        old_session_id = str(uuid.uuid4())
        new_session_id = str(uuid.uuid4())
        fake = _FakeSupabase()
        fake.seed(
            "analyst_refresh_jobs",
            [
                # Old session's own rows — VTI (still held today) and OLDX
                # (a since-sold ticker sitting in the old backlog).
                _job_row(USER_A, "VTI", refresh_window=_YESTERDAY, run_session_id=old_session_id),
                _job_row(USER_A, "OLDX", refresh_window=_YESTERDAY, run_session_id=old_session_id),
                # The new session's own rows for its current active tickers.
                _job_row(USER_A, "VTI", refresh_window=_TODAY, run_session_id=new_session_id),
                _job_row(USER_A, "AAPL", refresh_window=_TODAY, run_session_id=new_session_id),
            ],
        )
        current_active_tickers = ["VTI", "AAPL"]  # OLDX was sold, no longer held

        # Contract requirement: queue counting/claiming must be scoped to the
        # CURRENT run_session_id, not merely (user_id, tickers) — VTI exists
        # in both an old-session row and a new-session row, and only the
        # new-session row may ever count as this session's progress.
        # count_due_jobs / claim_due_jobs accept no run_session_id parameter
        # at all today, so this is expected to fail on current main with a
        # TypeError — a legitimate missing-contract failure, not a solved
        # behavior.
        counts = count_due_jobs(
            fake, now=_now(), user_id=USER_A, tickers=current_active_tickers,
            run_session_id=new_session_id,
        )
        claimed = claim_due_jobs(
            fake, worker_run_id=uuid.uuid4(), now=_now(),
            user_id=USER_A, tickers=current_active_tickers,
            run_session_id=new_session_id,
        )

        # Once session-scoped queue operations exist: exactly the
        # current-session VTI and AAPL rows are counted and claimed.
        assert counts["total_due"] == 2
        assert {j.ticker for j in claimed} == {"VTI", "AAPL"}
        assert all(j.run_session_id == new_session_id for j in claimed)

        # The old session's VTI row (same ticker, different session) is
        # never claimed as this session's own progress.
        assert not any(
            j.ticker == "VTI" and j.run_session_id == old_session_id for j in claimed
        )
        # The obsolete sold ticker in the old backlog is never processed.
        assert not any(j.ticker == "OLDX" for j in claimed)

        # Both old-session rows remain unchanged.
        old_rows = [
            r for r in fake.store["analyst_refresh_jobs"]
            if r.get("run_session_id") == old_session_id
        ]
        assert len(old_rows) == 2
        assert all(r["status"] == JOB_PENDING for r in old_rows)


# ── 3. Continuations preserve one session identity ───────────────────────────


class TestContinuationsPreserveOneSessionIdentity:
    @pytest.mark.asyncio
    async def test_repeated_reclicks_have_no_shared_durable_session_id(self, monkeypatch):
        fake = _FakeSupabase()
        service = _FakeIntelService(user_id=USER_A, client=fake, active_tickers=["VTI"])
        enqueue_result = {
            "status": "refresh_requested",
            "queued_ticker_count": 1,
            "existing_certified_snapshot_id": None,
        }
        drain_result = OnDemandDrainResult(
            batches_run=1, jobs_attempted=1, jobs_succeeded=0, jobs_failed=0,
            duration_ms=5, run_resumable=True, stopped_reason="runtime_cap_reached",
        )

        # One explicit click, then several automatic continuation requests —
        # today these are literally indistinguishable repeats of the same
        # call, since nothing threads a session id between them.
        responses = []
        for _ in range(4):
            responses.append(
                await _call_router(
                    monkeypatch, service,
                    enqueue_result=enqueue_result, drain_result=drain_result,
                )
            )

        session_ids = {r.get("run_session_id") for r in responses}
        assert all(sid is not None for sid in session_ids), (
            "no request — explicit click or continuation — ever received a "
            "durable run_session_id"
        )
        assert len(session_ids) == 1, (
            "every continuation must reuse the same run_session_id as the "
            "explicit click that started it"
        )


# ── 4. Completed ticker work survives interruption ──────────────────────────


class TestCompletedTickerWorkSurvivesInterruption:
    @pytest.mark.asyncio
    async def test_resumed_batches_never_reprocess_succeeded_tickers(self, monkeypatch):
        tickers = [f"TCK{i:02d}" for i in range(16)]
        fake = _FakeSupabase()
        fake.seed("analyst_refresh_jobs", [_job_row(USER_A, t) for t in tickers])
        factory, calls = _make_counting_factory()
        # Prewarm/publication is not under test here — keep it a no-op.
        monkeypatch.setattr(worker_mod, "trigger_snapshot_prewarm", AsyncMock(return_value=None))

        worker = AnalystRefreshWorker(
            client=fake, adapter_factory=factory, scope_user_id=USER_A, max_jobs_per_run=4,
        )

        batch1 = await worker.run_once(now=_now())
        batch2 = await worker.run_once(now=_now())
        succeeded_so_far = set(batch1.succeeded_tickers) | set(batch2.succeeded_tickers)
        assert len(succeeded_so_far) == 8  # two bounded batches of 4

        # "Interrupted" — the process simply stops calling run_once here.
        # "Resume using the same session id" — today's only resumption
        # scoping is (user_id, scope_tickers), reused via the same worker
        # instance; there is no run_session_id to resume against.
        batch3 = await worker.run_once(now=_now())
        batch4 = await worker.run_once(now=_now())
        succeeded_total = succeeded_so_far | set(batch3.succeeded_tickers) | set(batch4.succeeded_tickers)

        assert succeeded_total == set(tickers)
        # Each ticker analysed at most once across the whole resumed run.
        assert all(calls[t] == 1 for t in tickers), dict(calls)
        # Already-succeeded tickers are never re-selected in a later batch.
        assert not (set(batch1.succeeded_tickers) & set(batch3.selected_tickers))
        assert not (set(batch2.succeeded_tickers) & set(batch4.selected_tickers))

        # Contract requirement: resumption must be provably scoped to the
        # session that started the run, not merely to (user_id, tickers) —
        # otherwise an unrelated interrupted run sharing the same user and
        # tickers on the same day would be silently folded into "this" run's
        # resumption.
        assert hasattr(batch4, "run_session_id"), (
            "WorkerRunResult carries no durable run_session_id — resumption "
            "cannot be proven to belong to one specific Run Intel session"
        )


# ── 5. Publication retry performs zero analyst calls ─────────────────────────


class TestPublicationRetryPerformsZeroAnalystCalls:
    @pytest.mark.asyncio
    async def test_failed_publication_has_no_retry_path_once_all_tickers_succeeded(
        self, monkeypatch
    ):
        tickers = ["VTI", "AAPL", "QQQ"]
        fake = _FakeSupabase()
        fake.seed("analyst_refresh_jobs", [_job_row(USER_A, t) for t in tickers])
        factory, calls = _make_counting_factory()

        publish_calls: list[str] = []

        # trigger_snapshot_prewarm's own contract is "never raises" (it
        # swallows internally) — model that faithfully: publication is
        # attempted and (per this test's premise) fails internally, but the
        # call itself does not propagate an exception into run_once().
        async def _prewarm_records_attempt(*, user_id, worker_run_id):
            publish_calls.append(worker_run_id)

        monkeypatch.setattr(worker_mod, "trigger_snapshot_prewarm", _prewarm_records_attempt)

        worker = AnalystRefreshWorker(
            client=fake, adapter_factory=factory, scope_user_id=USER_A, max_jobs_per_run=10,
        )

        result1 = await worker.run_once(now=_now())
        assert set(result1.succeeded_tickers) == set(tickers)
        assert len(publish_calls) == 1  # one publication attempt was made (and — per this test's premise — failed)
        assert all(calls[t] == 1 for t in tickers)

        # Reclick Run Intel with the same session. Every ticker job is
        # already `succeeded` (terminal, non-claimable), so a real reclick's
        # freshness gate would enqueue nothing — the worker has nothing new
        # to claim, and run_once() only re-triggers publication when a fresh
        # batch of successes just completed. There is no production entry
        # point that retries JUST certification/publication scoped to a
        # session once its ticker work is already done.
        result2 = await worker.run_once(now=_now())
        assert result2.claimed_job_count == 0

        # Contract requirement: a publication failure must enter a
        # publication-retry state that a same-session retry resolves without
        # rerunning any ticker's analyst call.
        assert len(publish_calls) >= 2, (
            "no route exists to retry publication alone once all ticker jobs "
            "already succeeded — run_once() only retriggers "
            "trigger_snapshot_prewarm after a batch produces NEW successes, "
            "so a failed publication has no retry path today"
        )
        # Zero additional per-ticker analyst calls on the (attempted) retry.
        assert all(calls[t] == 1 for t in tickers)


# ── 6. Completion requires the current session's snapshot ───────────────────


class TestCompletionRequiresCurrentSessionSnapshot:
    @pytest.mark.asyncio
    async def test_completion_reported_without_all_required_conditions_met(self, monkeypatch):
        old_snapshot_id = "pre-session-snap-1"
        fake = _FakeSupabase()
        fake.seed("intel_v3_snapshots", [_snapshot_row(USER_A, old_snapshot_id)])
        service = _FakeIntelService(user_id=USER_A, client=fake, active_tickers=["VTI"])

        result = await _call_router(
            monkeypatch, service,
            enqueue_result={
                "status": "analyst_evidence_current",
                "queued_ticker_count": 0,
                "existing_certified_snapshot_id": old_snapshot_id,
            },
        )
        latest = await service.get_latest_snapshot()

        conditions_met = {
            "no_pending_or_retryable_or_terminal_jobs": (
                count_due_jobs(fake, now=_now(), user_id=USER_A, tickers=["VTI"])["total_due"] == 0
            ),
            "new_snapshot_published": latest.get("snapshot_id") != old_snapshot_id,
            "snapshot_linked_to_current_session": (
                result.get("run_session_id") is not None
                and latest.get("run_session_id") == result.get("run_session_id")
            ),
            "snapshot_id_differs_from_pre_session_snapshot": latest.get("snapshot_id") != old_snapshot_id,
            "snapshot_source_worker_certified": latest.get("snapshot_source") == "worker_certified",
            "evidence_freshness_state_certified_current": (
                latest.get("evidence_freshness_state") == "certified_current"
            ),
        }
        # Sanity: several required conditions are genuinely unmet here.
        unmet = [k for k, v in conditions_met.items() if not v]
        assert unmet, "expected at least one unmet completion condition in this fixture"
        assert "new_snapshot_published" in unmet
        assert "snapshot_linked_to_current_session" in unmet

        # Contract requirement: the router must not report completion while
        # any required condition is unmet.
        assert result.get("next_required_action") != "none_certified_snapshot_current", (
            f"router reported completion even though these conditions were "
            f"unmet: {unmet}"
        )


# ── 7. Optional narrative stages cannot block completion ────────────────────


class TestOptionalNarrativeStagesCannotBlockCompletion:
    @pytest.mark.asyncio
    async def test_deterministic_certification_completes_without_narrative(self, monkeypatch):
        # Adapter seam: this codebase has no distinct portfolio
        # narrative/synthesis stage today. Modeled as a spy the
        # certification path must never call and never retry — current
        # production code genuinely never calls it (0 == 0 below), which is
        # the passing half of this contract; the session-identity half still
        # fails (see final assertion).
        synthesis_calls: list[str] = []

        publish_calls: list[str] = []

        async def _record_publish(*, user_id, worker_run_id):
            publish_calls.append(worker_run_id)

        monkeypatch.setattr(worker_mod, "trigger_snapshot_prewarm", _record_publish)

        tickers = ["VTI", "AAPL"]
        fake = _FakeSupabase()
        fake.seed("analyst_refresh_jobs", [_job_row(USER_A, t) for t in tickers])
        factory, calls = _make_counting_factory()
        worker = AnalystRefreshWorker(client=fake, adapter_factory=factory, scope_user_id=USER_A)

        result = await worker.run_once(now=_now())

        assert set(result.succeeded_tickers) == set(tickers)
        assert len(publish_calls) == 1  # deterministic certification/publication ran
        assert synthesis_calls == []  # narrative/synthesis never invoked

        # No successful ticker job is reopened by a later pass.
        result2 = await worker.run_once(now=_now())
        assert result2.claimed_job_count == 0
        assert set(result2.succeeded_tickers) == set()

        # Contract requirement: completion must be provably tied to the
        # session whose tickers were analysed.
        assert hasattr(result, "run_session_id"), (
            "WorkerRunResult carries no durable run_session_id — "
            "certification cannot be attributed to one specific Run Intel "
            "session even though it never depended on portfolio synthesis"
        )


# ── 8. Session isolation between users ───────────────────────────────────────


class TestSessionIsolationBetweenUsers:
    @pytest.mark.asyncio
    async def test_concurrent_users_get_no_distinct_session_ids(self, monkeypatch):
        fake = _FakeSupabase()
        fake.seed(
            "analyst_refresh_jobs",
            [_job_row(USER_A, "VTI"), _job_row(USER_B, "VTI")],
        )
        fake.seed("intel_v3_snapshots", [_snapshot_row(USER_A, "snap-a-1")])

        counts_a = count_due_jobs(fake, now=_now(), user_id=USER_A, tickers=["VTI"])
        counts_b = count_due_jobs(fake, now=_now(), user_id=USER_B, tickers=["VTI"])
        claimed_a = claim_due_jobs(fake, worker_run_id=uuid.uuid4(), now=_now(), user_id=USER_A, tickers=["VTI"])
        claimed_b = claim_due_jobs(fake, worker_run_id=uuid.uuid4(), now=_now(), user_id=USER_B, tickers=["VTI"])

        # Sanity: today's per-user queue scoping already isolates job claims.
        assert counts_a["total_due"] == 1 and counts_b["total_due"] == 1
        assert [j.user_id for j in claimed_a] == [USER_A]
        assert [j.user_id for j in claimed_b] == [USER_B]

        service_b = _FakeIntelService(user_id=USER_B, client=fake, active_tickers=["VTI"])
        snap_b = await service_b.get_latest_snapshot()
        assert snap_b is None  # sanity: user A's snapshot never leaks to user B's read

        # Contract requirement: each user's concurrent Run Intel session must
        # carry its own distinct run_session_id — a stronger guarantee than
        # user_id scoping alone, since the response has no session concept
        # to isolate at all today.
        service_a2 = _FakeIntelService(user_id=USER_A, client=fake, active_tickers=["VTI"])
        service_b2 = _FakeIntelService(user_id=USER_B, client=fake, active_tickers=["VTI"])
        enqueue_result = {
            "status": "refresh_requested",
            "queued_ticker_count": 1,
            "existing_certified_snapshot_id": None,
        }
        drain_result = OnDemandDrainResult(
            batches_run=1, jobs_attempted=1, jobs_succeeded=1, jobs_failed=0,
            duration_ms=5, run_resumable=False, stopped_reason=STOPPED_DRAINED,
        )
        result_a = await _call_router(
            monkeypatch, service_a2, enqueue_result=enqueue_result, drain_result=drain_result,
        )
        result_b = await _call_router(
            monkeypatch, service_b2, enqueue_result=enqueue_result, drain_result=drain_result,
        )
        assert result_a.get("run_session_id") is not None, "user A's session has no durable id"
        assert result_b.get("run_session_id") is not None, "user B's session has no durable id"
        assert result_a.get("run_session_id") != result_b.get("run_session_id"), (
            "two concurrent users' sessions must never share a run_session_id"
        )


# ── 9. Same-day second Run Intel action creates a new session ───────────────


class TestSameDaySecondRunIntelActionCreatesNewSession:
    @pytest.mark.asyncio
    async def test_second_same_day_action_completes_using_first_sessions_snapshot(
        self, monkeypatch
    ):
        fake = _FakeSupabase()
        service = _FakeIntelService(user_id=USER_A, client=fake, active_tickers=["VTI"])

        # Session 1: queues + drains VTI successfully, then a real
        # certification publishes snap-1 (simulated directly, mirroring what
        # trigger_snapshot_prewarm would have written).
        drain_result_1 = OnDemandDrainResult(
            batches_run=1, jobs_attempted=1, jobs_succeeded=1, jobs_failed=0,
            duration_ms=5, run_resumable=False, stopped_reason=STOPPED_DRAINED,
        )
        result1 = await _call_router(
            monkeypatch, service,
            enqueue_result={
                "status": "refresh_requested",
                "queued_ticker_count": 1,
                "existing_certified_snapshot_id": None,
            },
            drain_result=drain_result_1,
        )
        fake.seed("intel_v3_snapshots", [_snapshot_row(USER_A, "snap-1")])

        # Session 2, same UTC day: the ticker's evidence is now fresh (from
        # session 1), so the real freshness gate would enqueue zero tickers
        # — exactly the production bug this contract closes: session 2 has
        # no work of its own queued and nothing to distinguish it from
        # session 1's already-certified state.
        result2 = await _call_router(
            monkeypatch, service,
            enqueue_result={
                "status": "analyst_evidence_current",
                "queued_ticker_count": 0,
                "existing_certified_snapshot_id": "snap-1",
            },
        )
        latest = await service.get_latest_snapshot()

        # Sanity: the second action never published its own snapshot — the
        # latest snapshot is still the first session's.
        assert latest["snapshot_id"] == "snap-1"  # session 2 never published its own snapshot

        # Contract requirements (all unmet today):
        assert result1.get("run_session_id") is not None, "session 1 has no durable id"
        assert result2.get("run_session_id") is not None, "session 2 has no durable id"
        assert result2.get("run_session_id") != result1.get("run_session_id"), (
            "a same-day second Run Intel action must receive a distinct "
            "run_session_id from the first"
        )
        assert result2.get("next_required_action") != "none_certified_snapshot_current", (
            "the second same-day action completed using the FIRST session's "
            "snapshot instead of publishing its own"
        )


# ── 10. Exact call-accounting test ───────────────────────────────────────────


class TestExactCallAccounting:
    @pytest.mark.asyncio
    async def test_32_ticker_run_exact_accounting_and_missing_session_id(self, monkeypatch):
        tickers = [f"TCK{i:02d}" for i in range(32)]
        fake = _FakeSupabase()
        fake.seed("analyst_refresh_jobs", [_job_row(USER_A, t) for t in tickers])
        factory, calls = _make_counting_factory()

        publish_calls: list[str] = []

        async def _record_publish(*, user_id, worker_run_id):
            publish_calls.append(worker_run_id)

        monkeypatch.setattr(worker_mod, "trigger_snapshot_prewarm", _record_publish)

        worker = AnalystRefreshWorker(
            client=fake, adapter_factory=factory, scope_user_id=USER_A, max_jobs_per_run=8,
        )

        # Existing frontend continuation cap (see app/routers/intel_v3.py's
        # "queue_only_..." / "reclick_run_intel_or_run_worker_entrypoint_to_
        # continue_draining" contract) is generous; this bound only proves
        # we stayed well within any reasonable cap, not a new production
        # constant.
        MAX_CONTINUATIONS = 10
        batches = []
        continuation_count = 0
        batch = await worker.run_once(now=_now())  # the explicit click's own first batch
        batches.append(batch)
        continuation_count += 1
        while batch.run_resumable and continuation_count < MAX_CONTINUATIONS:
            batch = await worker.run_once(now=_now())
            batches.append(batch)
            continuation_count += 1

        total_succeeded: set[str] = set()
        for b in batches:
            total_succeeded |= set(b.succeeded_tickers)

        assert total_succeeded == set(tickers)
        assert sum(calls.values()) == 32
        assert all(v == 1 for v in calls.values()), dict(calls)
        assert continuation_count <= MAX_CONTINUATIONS
        assert len(publish_calls) == 1  # one certification/publication attempt on the healthy path
        assert not batches[-1].run_resumable

        remaining = count_due_jobs(fake, now=_now(), user_id=USER_A, tickers=tickers)
        assert remaining["total_due"] == 0  # no active-session queue rows remain unfinished

        # Contract requirement: this entire 32-ticker run must be provably
        # ONE Run Intel session's work — today no WorkerRunResult batch
        # carries a run_session_id at all.
        assert all(hasattr(b, "run_session_id") for b in batches), (
            "WorkerRunResult never carries a run_session_id, so this bounded "
            "multi-batch run cannot be proven to belong to exactly one "
            "explicit Run Intel session"
        )
