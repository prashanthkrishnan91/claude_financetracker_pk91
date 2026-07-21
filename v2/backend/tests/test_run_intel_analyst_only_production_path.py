"""Run Intel production path — genuine analyst-only execution, no synthesis.

Mission suites 2 + 3:

  * The REAL production backend seam
    (``default_full_portfolio_agent_orchestrator_backend``) is executed with
    the REAL ``AgentOrchestrator`` class instrumented so that:
      - ``run()`` (the full pipeline) raises immediately if called;
      - ``_run_portfolio_synthesis()`` raises immediately if called.
    The test proves the seam calls the new analyst-only production method,
    that selected-ticker evidence is durably persisted and verified, and —
    via an explicit revert simulation — that this test FAILS if the adapter
    ever goes back to the full orchestrator path.

  * The REAL ``run_analyst_refresh_only`` method is executed end-to-end with
    ``_run_portfolio_synthesis`` patched to raise: the selected-ticker path
    still completes and persists.

  * Production-timeout regression: with a synthesis stage that would exceed
    the remaining deadline, the analyst-only path returns and credits the
    ticker work; the OLD full-pipeline shape demonstrably times out and
    blanket-fails the batch under the same budget.

No unused local assertion lists: every "must not run" stage is patched to
RAISE, so reaching it fails the test outright.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock

import pytest

import app.services.agents.orchestrator as orch_mod
import app.services.intelligence.v3.analyst_evidence_writer_v1 as writer_mod
import app.services.intelligence.v3.full_portfolio_analyst_refresh_adapter_v1 as adapter_mod
from app.services.agents.orchestrator import AgentOrchestrator, AgentPipelineResult
from app.services.agents.state import TickerInsight
from app.services.intelligence.v3.analyst_refresh_adapter_v1 import (
    STATUS_SKIPPED_TIMEOUT,
    STATUS_SUCCEEDED,
)
from app.services.intelligence.v3.full_portfolio_analyst_refresh_adapter_v1 import (
    FullPortfolioAnalystRefreshAdapter,
    FullPortfolioAnalystRefreshBudget,
    default_full_portfolio_agent_orchestrator_backend,
)

from tests.run_intel_session_test_utils import FakeSupabase

USER_ID = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
TICKERS = ["AAPL", "MSFT", "NVDA"]


def _forbid(label: str):
    def _sync(self, *a, **k):
        raise AssertionError(f"{label} must NEVER be called on the Run Intel path")
    return _sync


def _forbid_async(label: str):
    async def _async(self, *a, **k):
        raise AssertionError(f"{label} must NEVER be called on the Run Intel path")
    return _async


def _insight(ticker: str) -> TickerInsight:
    ins = TickerInsight(ticker=ticker, name=ticker, category="stock")
    ins.suggested_action = "HOLD"
    ins.investment_thesis = (
        f"{ticker}: earnings trajectory supports holding at the current weight."
    )
    return ins


def _verdict_dict(ticker: str) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "action": "HOLD",
        "conviction": 0.5,
        "confidence": 0.6,
        "primary_driver": f"{ticker} revenue growth is steady quarter over quarter",
        "action_reason": "valuation is fair relative to growth",
        "risk_flag": "sector concentration",
        "conviction_level": "MEDIUM",
    }


# ═══ Suite 2a — the production adapter seam calls the analyst-only method ═════


class TestProductionAdapterSeam:
    @pytest.mark.asyncio
    async def test_default_backend_calls_analyst_only_and_persists_evidence(
        self, monkeypatch,
    ):
        fake_db = FakeSupabase()
        monkeypatch.setattr(orch_mod, "get_supabase_client", lambda: fake_db)
        monkeypatch.setattr(writer_mod, "get_supabase_client", lambda: fake_db)
        import app.database as app_db
        monkeypatch.setattr(app_db, "get_supabase_client", lambda: fake_db)

        # Real class, instrumented: full pipeline + synthesis raise on touch.
        monkeypatch.setattr(
            AgentOrchestrator, "run", _forbid_async("AgentOrchestrator.run()"),
        )
        monkeypatch.setattr(
            AgentOrchestrator,
            "_run_portfolio_synthesis",
            _forbid_async("_run_portfolio_synthesis()"),
        )

        run_id = str(uuid.uuid4())
        explicit_run_id = run_id
        created_run_ids: list = []
        analyst_only_calls: list[tuple[str, list[str]]] = []

        async def _create_run(self, tickers=None, run_id=None):
            created_run_ids.append(run_id)
            return run_id or explicit_run_id

        async def _analyst_only(self, rid, tickers=None):
            # Stands in for the LLM-bearing stages ONLY: records the call and
            # exposes real insights/verdicts. Durable persistence + post-run
            # verification below run through the REAL production code
            # (write_analyst_evidence + _read_post_run_evidence).
            analyst_only_calls.append((rid, list(tickers or [])))
            self._verdicts = {t: _verdict_dict(t) for t in TICKERS}
            return AgentPipelineResult(
                run_id=rid,
                status="completed",
                summary="analyst-only",
                insights=[_insight(t) for t in TICKERS],
            )

        monkeypatch.setattr(AgentOrchestrator, "create_run", _create_run)
        monkeypatch.setattr(
            AgentOrchestrator, "run_analyst_refresh_only", _analyst_only,
        )

        started = datetime.now(timezone.utc)
        rows = await default_full_portfolio_agent_orchestrator_backend(
            USER_ID, list(TICKERS), started,
        )

        # The seam called the analyst-only production method exactly once,
        # with the selected batch.
        assert analyst_only_calls == [(run_id, TICKERS)]

        # Selected-ticker evidence was durably persisted (real writer) and
        # verified from real rows (real readback) — run-id matched.
        insights = fake_db.rows("agent_insights")
        recs = fake_db.rows("recommendations")
        assert {r["ticker"] for r in insights} >= set(TICKERS)
        assert {r["ticker"] for r in recs} >= set(TICKERS)
        assert all(r["run_id"] == run_id for r in insights)
        for t in TICKERS:
            row = rows[t]
            assert row is not None
            assert row["agent_run_id"] == run_id
            assert row["insight_run_match"] is True
            assert row["used_fallback"] is False

    @pytest.mark.asyncio
    async def test_seam_would_fail_if_reverted_to_full_orchestrator_path(
        self, monkeypatch,
    ):
        """Revert simulation: if the adapter went back to run(), the
        instrumented full pipeline raises, no evidence persists, and every
        assertion of the seam test above breaks — proving the guard bites."""
        fake_db = FakeSupabase()
        monkeypatch.setattr(orch_mod, "get_supabase_client", lambda: fake_db)
        monkeypatch.setattr(writer_mod, "get_supabase_client", lambda: fake_db)
        import app.database as app_db
        monkeypatch.setattr(app_db, "get_supabase_client", lambda: fake_db)

        monkeypatch.setattr(
            AgentOrchestrator, "run", _forbid_async("AgentOrchestrator.run()"),
        )

        async def _create_run(self, tickers=None, run_id=None):
            return run_id or str(uuid.uuid4())

        async def _reverted(self, rid, tickers=None):
            # A revert is exactly this: delegating back to the full pipeline.
            return await self.run(rid)

        monkeypatch.setattr(AgentOrchestrator, "create_run", _create_run)
        monkeypatch.setattr(
            AgentOrchestrator, "run_analyst_refresh_only", _reverted,
        )

        rows = await default_full_portfolio_agent_orchestrator_backend(
            USER_ID, list(TICKERS), datetime.now(timezone.utc),
        )

        # The full pipeline raised → zero persisted evidence, zero successes.
        assert fake_db.rows("agent_insights") == []
        for t in TICKERS:
            row = rows[t]
            assert row["insight_row_present"] is False
            assert str(row["failure_reason"]).startswith("agent_run_raised")

    @pytest.mark.asyncio
    async def test_backend_threads_explicit_worker_run_id_into_agent_run(
        self, monkeypatch,
    ):
        """Blocker 1: when the session worker supplies its batch
        worker_run_id, the backend creates the agent_runs row with that EXACT
        id, so every evidence row carries the job rows' durable run id."""
        fake_db = FakeSupabase()
        monkeypatch.setattr(orch_mod, "get_supabase_client", lambda: fake_db)
        monkeypatch.setattr(writer_mod, "get_supabase_client", lambda: fake_db)
        import app.database as app_db
        monkeypatch.setattr(app_db, "get_supabase_client", lambda: fake_db)
        monkeypatch.setattr(
            AgentOrchestrator, "run", _forbid_async("AgentOrchestrator.run()"),
        )

        worker_run_id = str(uuid.uuid4())
        created_run_ids: list = []

        async def _create_run(self, tickers=None, run_id=None):
            created_run_ids.append(run_id)
            return run_id or str(uuid.uuid4())

        async def _analyst_only(self, rid, tickers=None):
            self._verdicts = {t: _verdict_dict(t) for t in TICKERS}
            return AgentPipelineResult(
                run_id=rid, status="completed", summary="analyst-only",
                insights=[_insight(t) for t in TICKERS],
            )

        monkeypatch.setattr(AgentOrchestrator, "create_run", _create_run)
        monkeypatch.setattr(
            AgentOrchestrator, "run_analyst_refresh_only", _analyst_only,
        )

        rows = await default_full_portfolio_agent_orchestrator_backend(
            USER_ID, list(TICKERS), datetime.now(timezone.utc),
            run_id=worker_run_id,
        )

        assert created_run_ids == [worker_run_id]
        insights = fake_db.rows("agent_insights")
        assert insights and all(r["run_id"] == worker_run_id for r in insights)
        recs = fake_db.rows("recommendations")
        assert recs and all(r["agent_run_id"] == worker_run_id for r in recs)
        for t in TICKERS:
            assert rows[t]["agent_run_id"] == worker_run_id
            assert rows[t]["insight_run_match"] is True

    @pytest.mark.asyncio
    async def test_real_create_run_uses_explicit_id_when_supplied(
        self, monkeypatch,
    ):
        """The REAL create_run inserts agent_runs with the explicit id and
        keeps DB-generated behavior when the id is omitted."""
        fake_db = FakeSupabase()
        monkeypatch.setattr(orch_mod, "get_supabase_client", lambda: fake_db)
        orch = AgentOrchestrator(user_id=USER_ID, anthropic_api_key="")
        explicit = str(uuid.uuid4())
        rid = await orch.create_run(tickers=["AAPL"], run_id=explicit)
        assert rid == explicit
        assert any(r["id"] == explicit for r in fake_db.rows("agent_runs"))
        # Legacy behavior: omitted id → generated by the store.
        rid2 = await orch.create_run(tickers=["AAPL"])
        assert rid2 and rid2 != explicit

    def test_adapter_source_contract_calls_analyst_only_never_full_run(self):
        """Source-level guard: the production backend must invoke
        run_analyst_refresh_only and must not invoke orch.run(."""
        import inspect
        src = inspect.getsource(default_full_portfolio_agent_orchestrator_backend)
        assert "run_analyst_refresh_only(" in src
        assert "orch.run(" not in src
        assert "_run_portfolio_synthesis" not in src


# ═══ Suite 2b — the REAL analyst-only orchestrator method ═════════════════════


def _build_real_orchestrator(monkeypatch, fake_db: FakeSupabase) -> AgentOrchestrator:
    monkeypatch.setattr(orch_mod, "get_supabase_client", lambda: fake_db)
    orch = AgentOrchestrator(
        user_id=USER_ID,
        anthropic_api_key="",
        force_recompute=True,
        analyst_refresh_tickers=set(TICKERS),
    )
    # Substitute the IO-heavy deterministic stages with minimal shapes; the
    # method under test, its ordering, its persistence, and its terminal
    # lifecycle updates all run for real.
    monkeypatch.setattr(
        orch, "_fetch_market_bundle_for_user",
        AsyncMock(return_value={"live_prices": {t: 100.0 for t in TICKERS}}),
    )
    monkeypatch.setattr(
        orch_mod, "build_portfolio_context",
        lambda **kw: {
            "portfolio": [
                {
                    "ticker": t, "name": t, "category": "stock",
                    "shares": 1.0, "avg_cost": 50.0, "current_price": 100.0,
                }
                for t in TICKERS
            ],
            "insights": [],
            "data_quality": {"completeness_score": 1.0},
        },
    )
    monkeypatch.setattr(orch, "_attach_sec_filing_intelligence", AsyncMock())
    monkeypatch.setattr(orch, "_build_and_persist_snapshots", AsyncMock(return_value={}))
    monkeypatch.setattr(orch, "_build_and_persist_features", AsyncMock(return_value={}))
    monkeypatch.setattr(orch, "_compute_thesis_scorecards", lambda bundle: {})
    return orch


class TestRealAnalystOnlyMethod:
    @pytest.mark.asyncio
    async def test_completes_and_persists_with_synthesis_patched_to_raise(
        self, monkeypatch,
    ):
        fake_db = FakeSupabase()
        orch = _build_real_orchestrator(monkeypatch, fake_db)
        monkeypatch.setattr(
            AgentOrchestrator,
            "_run_portfolio_synthesis",
            _forbid_async("_run_portfolio_synthesis()"),
        )
        run_updates: list[dict] = []

        async def _record_update(self, run_id, **kw):
            run_updates.append({"run_id": run_id, **kw})

        monkeypatch.setattr(AgentOrchestrator, "_update_run", _record_update)

        result = await orch.run_analyst_refresh_only("run-real-1", tickers=TICKERS)

        assert result.status == "completed"
        assert len(result.insights) == len(TICKERS)
        # Durable evidence persisted through the REAL _persist path.
        assert {r["ticker"] for r in fake_db.rows("agent_insights")} == set(TICKERS)
        assert {r["ticker"] for r in fake_db.rows("recommendations")} == set(TICKERS)
        assert all(
            r["agent_run_id"] == "run-real-1"
            for r in fake_db.rows("recommendations")
        )
        # Agent-run lifecycle reached terminal completed.
        terminal = [u for u in run_updates if u.get("status") == "completed"]
        assert terminal, f"no terminal completed update: {run_updates}"

    @pytest.mark.asyncio
    async def test_marks_run_failed_on_internal_error(self, monkeypatch):
        fake_db = FakeSupabase()
        orch = _build_real_orchestrator(monkeypatch, fake_db)
        monkeypatch.setattr(
            orch, "_run_per_ticker_analyst",
            AsyncMock(side_effect=RuntimeError("boom")),
        )
        run_updates: list[dict] = []

        async def _record_update(self, run_id, **kw):
            run_updates.append({"run_id": run_id, **kw})

        monkeypatch.setattr(AgentOrchestrator, "_update_run", _record_update)

        result = await orch.run_analyst_refresh_only("run-real-2")
        assert result.status == "failed"
        assert any(u.get("status") == "failed" for u in run_updates)

    def test_analyst_only_source_never_references_synthesis(self):
        import inspect
        src = inspect.getsource(AgentOrchestrator.run_analyst_refresh_only)
        assert "_run_portfolio_synthesis" not in src.replace(
            "``_run_portfolio_synthesis``", ""
        )
        assert "synthesize_portfolio" not in src


# ═══ Suite 3 — production-timeout regression ══════════════════════════════════


class TestTimeoutRegression:
    @pytest.mark.asyncio
    async def test_analyst_only_returns_before_synthesis_could_eat_deadline(
        self, monkeypatch,
    ):
        """Original incident shape: ticker analysis succeeds fast, synthesis
        would consume the remaining deadline. The analyst-only path must
        return (evidence persisted) well inside a deadline that a synthesis
        stage would have blown."""
        fake_db = FakeSupabase()
        orch = _build_real_orchestrator(monkeypatch, fake_db)

        async def _slow_synthesis(self, *a, **k):
            await asyncio.sleep(30)  # would blow any request deadline

        monkeypatch.setattr(
            AgentOrchestrator, "_run_portfolio_synthesis", _slow_synthesis,
        )
        monkeypatch.setattr(AgentOrchestrator, "_update_run", AsyncMock())

        result = await asyncio.wait_for(
            orch.run_analyst_refresh_only("run-fast"), timeout=5.0,
        )
        assert result.status == "completed"
        assert {r["ticker"] for r in fake_db.rows("agent_insights")} == set(TICKERS)

    @pytest.mark.asyncio
    async def test_adapter_credits_tickers_when_backend_returns_after_analysis(
        self, monkeypatch,
    ):
        """At the production adapter seam: a backend with analyst-only
        semantics (returns right after evidence) succeeds under a tight
        budget; the OLD shape (synthesis running after analysis inside the
        same call) times out and blanket-fails the very same tickers."""
        started = datetime.now(timezone.utc)

        def _verified_rows() -> dict:
            return {
                t: {
                    "agent_insight_created_at": started.isoformat(),
                    "recommendation_created_at": started.isoformat(),
                    "used_fallback": False,
                    "agent_run_id": "run-x",
                    "insight_run_match": True,
                    "rec_run_match": True,
                    "insight_row_present": True,
                    "rec_row_present": True,
                    "failure_reason": None,
                }
                for t in TICKERS
            }

        async def analyst_only_backend(user_id, tickers, started_at):
            await asyncio.sleep(0.05)  # per-ticker analysis: fast, succeeds
            return _verified_rows()

        async def old_full_pipeline_backend(user_id, tickers, started_at):
            await asyncio.sleep(0.05)  # analysis succeeded…
            await asyncio.sleep(30)    # …then unconditional synthesis
            return _verified_rows()

        budget = FullPortfolioAnalystRefreshBudget(max_seconds=1.5)

        fixed = FullPortfolioAnalystRefreshAdapter(
            user_id=USER_ID, run_backend=analyst_only_backend, budget=budget,
        )
        ok = await fixed(list(TICKERS), started_at=started)
        assert ok.status == STATUS_SUCCEEDED
        assert all(o.success for o in ok.per_ticker)

        regressed = FullPortfolioAnalystRefreshAdapter(
            user_id=USER_ID,
            run_backend=old_full_pipeline_backend,
            budget=FullPortfolioAnalystRefreshBudget(max_seconds=1.5),
        )
        broken = await regressed(list(TICKERS), started_at=started)
        # The original production failure shape: successful analysis reported
        # failed because the unused synthesis stage consumed the deadline.
        assert broken.status == STATUS_SKIPPED_TIMEOUT
        assert all(not o.success for o in broken.per_ticker)
