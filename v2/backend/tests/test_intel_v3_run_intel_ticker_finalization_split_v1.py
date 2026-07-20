"""Run Intel v3 production recovery — ticker/finalization split (post PR #476).

Production failure reproduced + fixed here
------------------------------------------
One Run Intel request claimed three analyst-refresh jobs; all three per-ticker
Claude analyses completed; the SAME adapter then ran full portfolio synthesis;
the per-request deadline expired DURING synthesis; the orchestrator run was
cancelled; the three completed ticker analyses were NOT durably credited; all
three jobs were marked ``full_portfolio_analyst_refresh_timeout``; the queue
stayed at 36 pending/retryable; the next click repeated the same work and
failure; 0 of 32 holdings certified.

Root cause: the bounded ticker batch invoked ``AgentOrchestrator.run()``, which
ran the per-ticker analyst stage AND portfolio synthesis AND persistence inside
ONE request budget — so a synthesis-stage timeout discarded already-completed
ticker work (persistence runs *after* synthesis).

Fix (this suite proves it):
  * Phase 1 — the ticker batch runs ``run(run_synthesis=False)``: per-ticker
    analyst + persist only, no synthesis. Completed ticker evidence is durably
    persisted and its job marked succeeded before synthesis is ever attempted;
    a later synthesis failure can never reopen it.
  * Phase 2 — synthesis runs exactly once at finalization, over the durable
    evidence, with its own request budget, followed by the existing
    certification + snapshot-publication path.

The reproduction (``TestReproSynthesisTimeoutLosesTickerEvidence``) exercises
the REAL ``AgentOrchestrator.run()`` control flow with provider/LLM/DB edges
stubbed — not a bare sleep — and demonstrates BOTH the broken behaviour (the
synthesis-inclusive path loses ticker evidence on a deadline) and the fix (the
synthesis-free path persists it).
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import pytest

from app.services.agents import orchestrator as orch_mod
from app.services.agents.orchestrator import AgentOrchestrator
from app.services.intelligence import RunMode, ModeDecision
from app.services.intelligence.per_ticker_analyst import AnalystVerdict

USER_ID = uuid.UUID("00000000-0000-0000-0000-0000000000aa")


def _now() -> datetime:
    return datetime(2026, 7, 20, 12, 0, 0, tzinfo=timezone.utc)


# ── In-memory Supabase fake (covers the orchestrator + job-store calls) ───────

class _FakeResult:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, store: dict, table: str):
        self._store = store
        self._table = table
        self._op = "select"
        self._payload = None
        self._filters: list[tuple] = []
        self._order_col = None
        self._order_desc = False
        self._limit = None

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

    def neq(self, col, val):
        self._filters.append(("neq", col, val))
        return self

    def in_(self, col, vals):
        self._filters.append(("in", col, list(vals)))
        return self

    def gte(self, col, val):
        self._filters.append(("gte", col, val))
        return self

    def filter(self, col, op, val):
        self._filters.append((op, col, val))
        return self

    def order(self, col, desc=False):
        self._order_col = col
        self._order_desc = desc
        return self

    def limit(self, n):
        self._limit = n
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
            if kind == "is" and val == "null" and rv is not None:
                return False
        return True

    def execute(self):
        rows = self._store.setdefault(self._table, [])
        if self._op == "insert":
            inserted = []
            for r in self._payload:
                nr = dict(r)
                nr.setdefault("id", str(uuid.uuid4()))
                nr.setdefault("created_at", _now().isoformat())
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
            out.sort(
                key=lambda x: (x.get(self._order_col) is None, x.get(self._order_col)),
                reverse=self._order_desc,
            )
        if self._limit is not None:
            out = out[: self._limit]
        return _FakeResult(out)


class _FakeSupabase:
    def __init__(self):
        self.store: dict[str, list[dict]] = {}

    def table(self, name):
        return _FakeQuery(self.store, name)

    def rows(self, name):
        return self.store.get(name, [])


# ── Orchestrator harness: real run() control flow, stubbed edges ──────────────

def _buy_verdict(ticker: str) -> AnalystVerdict:
    return AnalystVerdict(
        ticker=ticker,
        action="BUY",
        conviction=0.6,
        key_drivers=["earnings momentum"],
        risks=["macro"],
        confidence=0.8,
        summary=f"{ticker} constructive.",
        thesis=f"{ticker} shows durable demand and improving cash generation.",
        reasoning=f"{ticker} fundamentals support accumulation.",
        conviction_level="MEDIUM",
        primary_driver="improving cash generation",
        used_fallback=False,
        llm_attempted=True,
        analysis_source="llm",
    )


class _HarnessOrchestrator(AgentOrchestrator):
    """Real ``run()``/``run_finalization()`` control flow with the heavy phase
    methods (market IO, snapshots, features, per-ticker LLM, synthesis) replaced
    by deterministic stubs. The persistence path (``_persist`` / ``_persist_sync``)
    stays REAL so we exercise the exact ordering that made the production bug:
    persistence runs AFTER synthesis inside ``run()``."""

    def __init__(self, *args, tickers: list[str], synthesis_delay: float = 0.0, **kw):
        super().__init__(*args, **kw)
        self._h_tickers = tickers
        self._synthesis_delay = synthesis_delay
        self.synthesis_calls = 0
        self.per_ticker_calls = 0

    async def _fetch_market_bundle_for_user(self) -> dict[str, Any]:
        return {"live_prices": {t: 100.0 for t in self._h_tickers}}

    async def _attach_sec_filing_intelligence(self, context):
        return None

    async def _build_and_persist_snapshots(self, *, run_id, context, bundle):
        return {t: {"ticker": t, "price": 100.0} for t in self._h_tickers}

    async def _build_and_persist_features(self, *, run_id, bundle):
        return {t: {"ticker": t, "trend_regime": "up"} for t in self._h_tickers}

    def _compute_thesis_scorecards(self, bundle):
        return {}

    async def _run_per_ticker_analyst(self):
        self.per_ticker_calls += len(self._h_tickers)
        return {t: _buy_verdict(t) for t in self._h_tickers}

    async def _run_portfolio_synthesis(self, *, context):
        self.synthesis_calls += 1
        if self._synthesis_delay:
            await asyncio.sleep(self._synthesis_delay)
        from app.services.intelligence import PortfolioSynthesis
        return PortfolioSynthesis(
            summary="Portfolio synthesis narrative.",
            portfolio_bias="neutral",
            key_themes=["theme a", "theme b"],
            risk_concentrations=["tech"],
            overexposure_flags=[],
            rebalancing_suggestions=[],
            used_fallback=False,
        )


def _make_orch(fake_db, tickers, *, synthesis_delay=0.0, monkeypatch):
    # build_portfolio_context / classify_run_mode are module-level imports in
    # run(); stub them to a deterministic portfolio + FULL mode.
    def _fake_context(*, user_id, live_prices, market_data):
        return {
            "portfolio": [{"ticker": t, "shares": 1, "avg_cost": 90.0,
                           "current_price": 100.0, "category": "stock"}
                          for t in tickers],
            "insights": [],
            "macro": {"summary": "steady"},
            "data_quality": {"completeness_score": 1.0},
        }

    def _fake_mode(_snaps):
        return ModeDecision(mode=RunMode.FULL, avg_quality=0.9,
                            insufficient_count=0, total_tickers=len(tickers),
                            reason="ok", explanation="")

    monkeypatch.setattr(orch_mod, "build_portfolio_context", _fake_context)
    monkeypatch.setattr(orch_mod, "classify_run_mode", _fake_mode)

    orch = _HarnessOrchestrator(
        USER_ID, tickers=tickers, synthesis_delay=synthesis_delay,
        force_recompute=True,
        analyst_refresh_tickers=set(tickers),
    )
    orch.db = fake_db
    return orch


def _insight_rows(fake_db, run_id):
    return [r for r in fake_db.rows("agent_insights") if r.get("run_id") == run_id]


# ── Reproduction: synthesis-timeout loses ticker evidence on main ─────────────

class TestReproSynthesisTimeoutLosesTickerEvidence:
    """Iteration 0 — the exact production failure, reproduced at the real
    ``AgentOrchestrator.run()`` boundary, and the fix that closes it."""

    @pytest.mark.asyncio
    async def test_synthesis_inclusive_run_loses_ticker_evidence_on_deadline(self, monkeypatch):
        """BROKEN path (current main): run() with synthesis, wrapped in the
        adapter's per-request deadline. Synthesis outlives the deadline; the run
        is cancelled; because persistence runs AFTER synthesis, the completed
        per-ticker analysis is DISCARDED — no durable agent_insights rows."""
        fake = _FakeSupabase()
        tickers = ["AAPL", "NVDA", "MSFT"]
        orch = _make_orch(fake, tickers, synthesis_delay=5.0, monkeypatch=monkeypatch)
        run_id = await orch.create_run(tickers=tickers)

        # The adapter bounds the whole run() (analyst + synthesis + persist) with
        # one wait_for — exactly the production wiring. Synthesis (5s) outruns
        # the 0.2s deadline.
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(orch.run(run_id, run_synthesis=True), timeout=0.2)

        # Ticker analysis completed (per-ticker calls happened) ...
        assert orch.per_ticker_calls == 3
        # ... yet NO durable ticker evidence was credited: persistence never ran
        # because the cancellation fired during synthesis.
        assert _insight_rows(fake, run_id) == []

    @pytest.mark.asyncio
    async def test_synthesis_free_ticker_batch_persists_evidence_and_completes(self, monkeypatch):
        """FIXED path: run(run_synthesis=False) runs the per-ticker analyst +
        persist ONLY. No synthesis is attempted, evidence is durably persisted,
        and the run reaches an intentional ``completed`` terminal state — well
        inside the same deadline that broke the synthesis-inclusive path."""
        fake = _FakeSupabase()
        tickers = ["AAPL", "NVDA", "MSFT"]
        orch = _make_orch(fake, tickers, synthesis_delay=5.0, monkeypatch=monkeypatch)
        run_id = await orch.create_run(tickers=tickers)

        result = await asyncio.wait_for(
            orch.run(run_id, run_synthesis=False), timeout=2.0
        )

        # Synthesis was never called on the ticker batch path.
        assert orch.synthesis_calls == 0
        assert orch.per_ticker_calls == 3
        # Durable ticker evidence persisted for all three tickers.
        rows = _insight_rows(fake, run_id)
        assert {r["ticker"] for r in rows} == {"AAPL", "NVDA", "MSFT"}
        # Intentional terminal state — never a forced-failed LIFECYCLE VIOLATION.
        assert result.status == "completed"
        run_row = [r for r in fake.rows("agent_runs") if r["id"] == run_id][0]
        assert run_row["status"] == "completed"

    @pytest.mark.asyncio
    async def test_finalization_runs_synthesis_exactly_once_over_durable_evidence(self, monkeypatch):
        """Phase 2: run_finalization() runs the ONE synthesis pass over durable
        evidence with ZERO per-ticker analyst calls, and reaches a terminal
        completed state without a LIFECYCLE VIOLATION."""
        fake = _FakeSupabase()
        tickers = ["AAPL", "NVDA", "MSFT"]
        # First, persist ticker evidence via the synthesis-free batch.
        batch = _make_orch(fake, tickers, monkeypatch=monkeypatch)
        batch_run = await batch.create_run(tickers=tickers)
        await batch.run(batch_run, run_synthesis=False)
        assert batch.synthesis_calls == 0

        # Now finalization: fresh orchestrator, load durable verdicts, synthesise once.
        fin = _make_orch(fake, tickers, monkeypatch=monkeypatch)
        fin_run = await fin.create_run(tickers=[])
        result = await fin.run_finalization(fin_run)

        assert fin.synthesis_calls == 1          # exactly one synthesis call
        assert fin.per_ticker_calls == 0         # zero per-ticker analyst calls
        assert result.status == "completed"
        run_row = [r for r in fake.rows("agent_runs") if r["id"] == fin_run][0]
        assert run_row["status"] == "completed"
        assert run_row.get("portfolio_synthesis") is not None


# ── E2E: worker + finalization boundary (real worker/job-store/finalization) ──

from app.services.intelligence.v3.analyst_refresh_adapter_v1 import (  # noqa: E402
    AnalystRefreshResult,
    STATUS_SUCCEEDED,
    TickerRefreshOutcome,
)
from app.services.intelligence.v3.analyst_refresh_job_store_v1 import (  # noqa: E402
    JOB_SUCCEEDED,
    count_due_jobs,
    enqueue_refresh_jobs,
)
from app.services.intelligence.v3 import analyst_finalization_v1 as fin_mod  # noqa: E402
from app.services.intelligence.v3.analyst_finalization_v1 import (  # noqa: E402
    FINALIZATION_COMPLETED,
    run_finalization_if_ready,
)
from app.services.intelligence.v3.analyst_refresh_on_demand_drain_v1 import (  # noqa: E402
    run_on_demand_drain,
)


def _portfolio_tickers(n: int) -> list[str]:
    return [f"T{i:02d}" for i in range(n)]


def _past() -> datetime:
    """A timestamp safely in the past relative to the worker's real wall clock,
    so enqueued jobs (whose next_retry_at defaults to the enqueue time) are
    immediately due when the worker claims with ``datetime.now()``."""
    return datetime.now(timezone.utc) - timedelta(hours=2)


class _SynthesisFreeTickerAdapter:
    """Models ``run(run_synthesis=False)`` + durable evidence writer for one
    user: writes durable agent_insights + recommendations rows for the selected
    tickers and returns per-ticker success. It NEVER runs portfolio synthesis —
    that is the whole point of the split. Records per-ticker call counts so the
    LLM-accounting assertions can prove each ticker is analysed at most once."""

    def __init__(self, fake_db: _FakeSupabase, per_ticker_calls: dict[str, int]):
        self.db = fake_db
        self.per_ticker_calls = per_ticker_calls
        self.synthesis_calls = 0  # must stay 0

    async def __call__(self, tickers, *, priority_hints=None, started_at=None):
        started_iso = (started_at or _now()).isoformat()
        run_id = str(uuid.uuid4())
        per_ticker: list[TickerRefreshOutcome] = []
        for t in tickers:
            up = t.upper()
            self.per_ticker_calls[up] = self.per_ticker_calls.get(up, 0) + 1
            # Durable ticker evidence (both tables — the certification contract
            # requires an agent_insights AND a recommendations row per holding).
            self.db.table("agent_insights").insert({
                "run_id": run_id, "user_id": str(USER_ID), "ticker": up,
                "analyst_verdict": {"action": "BUY", "used_fallback": False},
                "created_at": started_iso,
            }).execute()
            self.db.table("recommendations").insert({
                "agent_run_id": run_id, "user_id": str(USER_ID), "ticker": up,
                "action": "BUY", "is_active": True, "created_at": started_iso,
            }).execute()
            per_ticker.append(TickerRefreshOutcome(
                ticker=up, success=True,
                refreshed_agent_insight_at=started_iso,
                llm_call_count=1, llm_success_count=1,
            ))
        return AnalystRefreshResult(
            status=STATUS_SUCCEEDED,
            selected_tickers=list(tickers),
            deferred_tickers=[],
            per_ticker=per_ticker,
            attempted_llm_calls=len(tickers),
            successful_llm_calls=len(tickers),
            failed_llm_calls=0,
        )


class _FinalizationHarness:
    """Stubs the finalization provider edges (synthesis LLM + certification
    publish) while driving the REAL ``run_finalization_if_ready`` gate/sequence.
    A certified snapshot is published only when every active holding has durable
    evidence — modelling the real certification contract."""

    def __init__(self, fake_db: _FakeSupabase, active_tickers: list[str],
                 *, synthesis_ok: bool = True, certify_ok: bool = True):
        self.db = fake_db
        self.active = [t.upper() for t in active_tickers]
        self.synthesis_ok = synthesis_ok
        self.certify_ok = certify_ok
        self.synthesis_calls = 0
        self.published_snapshot_ids: list[str] = []

    async def synthesis_backend(self, user_id):
        self.synthesis_calls += 1
        if not self.synthesis_ok:
            return {"status": "failed", "synthesis_llm_calls": 1}
        return {"status": "completed", "synthesis_llm_calls": 1}

    async def prewarm(self, *, user_id, worker_run_id):
        # Certify only when every active holding has both durable rows.
        insight_tickers = {r["ticker"] for r in self.db.rows("agent_insights")}
        rec_tickers = {r["ticker"] for r in self.db.rows("recommendations")
                       if r.get("is_active")}
        complete = self.certify_ok and all(
            t in insight_tickers and t in rec_tickers for t in self.active
        )
        snap_id = str(uuid.uuid4())
        for r in self.db.rows("intel_v3_snapshots"):
            r["is_active"] = False
        self.db.table("intel_v3_snapshots").insert({
            "user_id": str(user_id), "is_active": True,
            "payload": {
                "snapshot_id": snap_id,
                "snapshot_source": "worker_certified" if complete else "certification_failed",
            },
        }).execute()
        if complete:
            self.published_snapshot_ids.append(snap_id)

    async def snapshot_reader(self, user_id, client):
        rows = [r for r in self.db.rows("intel_v3_snapshots") if r.get("is_active")]
        if not rows:
            return None
        payload = rows[-1].get("payload") or {}
        source = payload.get("snapshot_source")
        return {
            "snapshot_id": payload.get("snapshot_id"),
            "snapshot_source": source,
            "evidence_freshness_state": (
                fin_mod.PUBLISH_CERTIFIED_CURRENT if source == "worker_certified" else "stale"
            ),
        }

    def finalizer(self):
        async def _f(*, user_id, client, tickers=None):
            return await run_finalization_if_ready(
                user_id=user_id, client=client, tickers=tickers,
                synthesis_backend=self.synthesis_backend,
                prewarm=self.prewarm,
                snapshot_state_reader=self.snapshot_reader,
            )
        return _f


async def _drive_run_intel(fake_db, active_tickers, harness, *, max_requests=20):
    """Simulate the frontend's bounded automatic continuation: fire
    run_on_demand_drain repeatedly until it is no longer resumable, capped like
    RUN_INTEL_MAX_CONTINUATIONS. Returns (requests_made, last_result)."""
    per_ticker_calls: dict[str, int] = {}
    adapter = _SynthesisFreeTickerAdapter(fake_db, per_ticker_calls)
    requests = 0
    last = None
    for _ in range(max_requests):
        requests += 1
        last = await run_on_demand_drain(
            user_id=USER_ID, client=fake_db, tickers=active_tickers,
            adapter_factory=lambda _uid: adapter,
            finalizer=harness.finalizer(),
        )
        if not last.run_resumable:
            break
    return requests, last, per_ticker_calls, adapter


class TestEndToEnd32HoldingRun:
    @pytest.mark.asyncio
    async def test_full_run_certifies_with_exact_llm_accounting(self):
        fake = _FakeSupabase()
        tickers = _portfolio_tickers(32)
        enqueue_refresh_jobs(fake, user_id=str(USER_ID), tickers=tickers, now=_past())
        harness = _FinalizationHarness(fake, tickers)

        requests, last, per_ticker_calls, adapter = await _drive_run_intel(
            fake, tickers, harness,
        )

        # 4) Every active ticker analysed at most once during the successful run.
        assert set(per_ticker_calls.keys()) == {t.upper() for t in tickers}
        assert all(c == 1 for c in per_ticker_calls.values())
        # LLM accounting: 32 per-ticker calls, exactly one synthesis call.
        assert sum(per_ticker_calls.values()) == 32
        assert adapter.synthesis_calls == 0        # 6) batches never synthesise
        assert harness.synthesis_calls == 1        # 7) synthesis exactly once
        # 5) Each completed ticker durably persisted before its job succeeded.
        succeeded = [r for r in fake.rows("analyst_refresh_jobs")
                     if r["status"] == JOB_SUCCEEDED]
        assert len(succeeded) == 32
        # 13) No queue rows remain pending/retryable/terminal for active tickers.
        due = count_due_jobs(fake, now=_now(), user_id=str(USER_ID), tickers=tickers)
        assert due["total_due"] == 0
        assert due["failed_not_yet_due"] == 0
        assert due["failed_terminal"] == 0
        # 10/11) A NEW worker_certified + certified_current snapshot is published.
        assert last.finalization_status == FINALIZATION_COMPLETED
        assert last.certified_snapshot_id is not None
        assert last.certified_snapshot_id in harness.published_snapshot_ids
        snap = await harness.snapshot_reader(USER_ID, fake)
        assert snap["snapshot_source"] == "worker_certified"
        assert snap["evidence_freshness_state"] == fin_mod.PUBLISH_CERTIFIED_CURRENT
        # 14/15) Continuation stops after completion, within the frontend caps.
        assert last.run_resumable is False
        assert requests <= 20                       # RUN_INTEL_MAX_CONTINUATIONS
        # 32 holdings / batch 8 = 4 ticker requests; finalization lands on the
        # 4th (same call as the last ticker batch) → 4 requests total.
        assert requests == 4


class TestInterruptionResume:
    @pytest.mark.asyncio
    async def test_resume_does_not_reanalyse_succeeded_tickers(self):
        fake = _FakeSupabase()
        tickers = _portfolio_tickers(32)
        enqueue_refresh_jobs(fake, user_id=str(USER_ID), tickers=tickers, now=_past())
        harness = _FinalizationHarness(fake, tickers)
        per_ticker_calls: dict[str, int] = {}
        adapter = _SynthesisFreeTickerAdapter(fake, per_ticker_calls)

        # Two ticker batches, then "interrupt" (stop firing continuations).
        for _ in range(2):
            await run_on_demand_drain(
                user_id=USER_ID, client=fake, tickers=tickers,
                adapter_factory=lambda _uid: adapter, finalizer=harness.finalizer(),
            )
        first_pass_calls = dict(per_ticker_calls)
        assert 0 < len(first_pass_calls) < 32          # partial progress
        assert harness.synthesis_calls == 0            # not finalized yet

        # Resume: keep firing until drained.
        for _ in range(20):
            res = await run_on_demand_drain(
                user_id=USER_ID, client=fake, tickers=tickers,
                adapter_factory=lambda _uid: adapter, finalizer=harness.finalizer(),
            )
            if not res.run_resumable:
                break

        # Already-succeeded tickers were NOT regenerated; every ticker analysed once.
        assert all(c == 1 for c in per_ticker_calls.values())
        assert sum(per_ticker_calls.values()) == 32
        assert harness.synthesis_calls == 1            # finalization once


class TestFinalizationFailureRetry:
    @pytest.mark.asyncio
    async def test_synthesis_failure_preserves_jobs_and_retry_adds_zero_ticker_calls(self):
        fake = _FakeSupabase()
        tickers = _portfolio_tickers(8)  # single ticker batch → finalize same call
        enqueue_refresh_jobs(fake, user_id=str(USER_ID), tickers=tickers, now=_past())

        # First finalization: certification FAILS (a holding failed the evidence
        # contract). Ticker jobs must stay succeeded and NOT be reopened.
        failing = _FinalizationHarness(fake, tickers, certify_ok=False)
        per_ticker_calls: dict[str, int] = {}
        adapter = _SynthesisFreeTickerAdapter(fake, per_ticker_calls)
        res1 = await run_on_demand_drain(
            user_id=USER_ID, client=fake, tickers=tickers,
            adapter_factory=lambda _uid: adapter, finalizer=failing.finalizer(),
        )
        # 8) A finalization failure does not reset or reopen succeeded ticker jobs.
        succeeded = [r for r in fake.rows("analyst_refresh_jobs")
                     if r["status"] == JOB_SUCCEEDED]
        assert len(succeeded) == 8
        assert sum(per_ticker_calls.values()) == 8
        assert failing.synthesis_calls == 1
        assert res1.finalization_status == "failed_retryable"
        assert res1.run_resumable is True          # frontend fires a retry

        calls_after_first = sum(per_ticker_calls.values())

        # 9) Retry finalization only (no ticker jobs remain). ZERO per-ticker calls.
        ok = _FinalizationHarness(fake, tickers, certify_ok=True)
        res2 = await run_finalization_if_ready(
            user_id=USER_ID, client=fake, tickers=tickers,
            synthesis_backend=ok.synthesis_backend, prewarm=ok.prewarm,
            snapshot_state_reader=ok.snapshot_reader,
        )
        assert sum(per_ticker_calls.values()) == calls_after_first  # +0 ticker calls
        assert res2.status == FINALIZATION_COMPLETED               # 10) retry certifies
        assert res2.certified is True


class TestFinalizationSynthesisBounded:
    @pytest.mark.asyncio
    async def test_slow_synthesis_is_bounded_and_preserves_succeeded_jobs(self):
        """The finalization synthesis has its own server-side budget: a slow /
        hung synthesis cannot make the finalization request exceed the
        per-request envelope, and it never reopens the already-succeeded ticker
        jobs. This is what makes the whole-run wall-clock bound enforced rather
        than merely modelled."""
        import time as _time

        from app.services.intelligence.v3.analyst_refresh_worker_v1 import AnalystRefreshWorker

        fake = _FakeSupabase()
        tickers = _portfolio_tickers(8)
        enqueue_refresh_jobs(fake, user_id=str(USER_ID), tickers=tickers, now=_past())
        harness = _FinalizationHarness(fake, tickers)
        per_ticker_calls: dict[str, int] = {}
        adapter = _SynthesisFreeTickerAdapter(fake, per_ticker_calls)
        # Drive the worker directly so evidence is durable + jobs succeeded,
        # WITHOUT triggering finalization (scoped worker defers it to the drain).
        worker = AnalystRefreshWorker(
            client=fake, adapter_factory=lambda _uid: adapter,
            max_jobs_per_run=8, scope_user_id=USER_ID, scope_tickers=tickers,
        )
        await worker.run_once()

        async def _slow_synthesis(user_id):
            harness.synthesis_calls += 1
            await asyncio.sleep(5.0)      # far longer than the 0.3s budget
            return {"status": "completed", "synthesis_llm_calls": 1}

        started = _time.monotonic()
        res = await run_finalization_if_ready(
            user_id=USER_ID, client=fake, tickers=tickers,
            synthesis_backend=_slow_synthesis, prewarm=harness.prewarm,
            snapshot_state_reader=harness.snapshot_reader,
            max_synthesis_seconds=0.3,
        )
        elapsed = _time.monotonic() - started

        # Bounded: returned well before the 5s synthesis would have finished.
        assert elapsed < 2.0
        # Ticker jobs remain succeeded — a synthesis timeout loses zero work.
        succeeded = [r for r in fake.rows("analyst_refresh_jobs")
                     if r["status"] == JOB_SUCCEEDED]
        assert len(succeeded) == 8
        assert sum(per_ticker_calls.values()) == 8
        # Certification still ran despite the synthesis timeout (evidence complete).
        assert res.certified is True


class TestFinalizationGate:
    @pytest.mark.asyncio
    async def test_finalization_skipped_while_ticker_jobs_remain(self):
        fake = _FakeSupabase()
        tickers = _portfolio_tickers(32)
        enqueue_refresh_jobs(fake, user_id=str(USER_ID), tickers=tickers, now=_past())
        harness = _FinalizationHarness(fake, tickers)
        # Jobs are all still pending (nothing drained) → finalization not ready.
        res = await run_finalization_if_ready(
            user_id=USER_ID, client=fake, tickers=tickers,
            synthesis_backend=harness.synthesis_backend, prewarm=harness.prewarm,
            snapshot_state_reader=harness.snapshot_reader,
        )
        assert res.status == "skipped_not_ready"
        assert harness.synthesis_calls == 0
        assert res.certified is False
