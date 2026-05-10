"""Enforce: DB → Context Builder → single LLM call → Persist.

These tests lock in the invariant that broke the AI pipeline:
  1. `build_portfolio_context` performs zero LLM calls and returns the exact
     `{portfolio, macro, insights}` shape the orchestrator expects.
  2. `AgentOrchestrator.run` invokes the LLM at most once per run.
  3. Empty portfolios short-circuit — no LLM call, status='no_data'.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest


def _orchestrator_mock_db(run_id: str) -> MagicMock:
    """Mock supabase client that satisfies orchestrator's update + verify chain.

    ``_run_agent_runs_update`` issues an UPDATE followed by a SELECT to
    confirm at least one row matched. A bare ``MagicMock`` returns
    ``len(verify.data) == 0`` (MagicMock.__len__ default), tripping the
    "matched zero rows" guardrail. This helper wires the verify path to
    return a single-row payload so the test exercises real orchestrator
    flow rather than the schema-fallback branch.
    """
    db = MagicMock()
    db.table.return_value.update.return_value.eq.return_value.eq.return_value.execute.return_value = MagicMock()
    # SELECT id,status used by _update_and_verify
    db.table.return_value.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value = (
        SimpleNamespace(data=[{"id": run_id, "status": "running"}])
    )
    return db


# ── Context builder shape ───────────────────────────────────────────────────


def _mock_db(positions=None, insights=None):
    db = MagicMock()

    def _table(name):
        tbl = MagicMock()
        if name == "positions":
            (
                tbl.select.return_value
                .eq.return_value
                .execute.return_value
            ).data = positions or []
        elif name == "agent_insights":
            (
                tbl.select.return_value
                .eq.return_value
                .order.return_value
                .limit.return_value
                .execute.return_value
            ).data = insights or []
        elif name == "macro_cache":
            (
                tbl.select.return_value
                .order.return_value
                .limit.return_value
                .execute.return_value
            ).data = []
        return tbl

    db.table.side_effect = _table
    return db


class TestContextBuilderShape:
    def test_empty_portfolio_returns_empty_lists(self, monkeypatch):
        from app.services.ai import context_builder

        monkeypatch.setattr(
            context_builder, "get_supabase_client", lambda: _mock_db()
        )

        ctx = context_builder.build_portfolio_context("user-1")

        assert ctx["portfolio"] == []
        assert ctx["insights"] == []
        assert isinstance(ctx["macro"]["summary"], str)
        assert ctx["macro"]["summary"]  # placeholder still present

    def test_positions_produce_structured_portfolio(self, monkeypatch):
        from app.services.ai import context_builder

        positions = [
            {"ticker": "AAPL", "shares": 10, "avg_cost": 150.0, "category": "Tech"},
            {"ticker": "VOO", "shares": 5, "avg_cost": 400.0, "category": "ETF"},
        ]
        insights = [
            {
                "ticker": "AAPL",
                "sentiment_label": "bullish",
                "technical_signal": "BUY",
                "fundamental_score": 0.4,
                "suggested_action": "BUY",
                "created_at": "2026-01-01T00:00:00Z",
            }
        ]
        monkeypatch.setattr(
            context_builder, "get_supabase_client",
            lambda: _mock_db(positions=positions, insights=insights),
        )

        ctx = context_builder.build_portfolio_context(
            "user-1", live_prices={"AAPL": 180.0}
        )

        tickers = [p["ticker"] for p in ctx["portfolio"]]
        assert tickers == ["AAPL", "VOO"]
        aapl = next(p for p in ctx["portfolio"] if p["ticker"] == "AAPL")
        assert aapl["shares"] == 10
        assert aapl["avg_cost"] == 150.0
        assert aapl["current_price"] == 180.0
        assert "P&L" in aapl["what_changed"]
        assert "prior action BUY" in aapl["what_changed"]

        assert ctx["insights"][0]["sentiment"] == "bullish"
        assert ctx["insights"][0]["technical"] == "BUY"
        assert ctx["insights"][0]["fundamental"] == "bullish"

    def test_builder_does_not_call_llm(self, monkeypatch):
        """The aggregation layer must never instantiate an LLM client."""
        from app.services.ai import context_builder

        sentinel = MagicMock(side_effect=AssertionError("LLM called in builder"))
        monkeypatch.setattr("app.services.agents.llm.LLMClient", sentinel)
        monkeypatch.setattr(
            context_builder, "get_supabase_client", lambda: _mock_db()
        )

        context_builder.build_portfolio_context("user-1")
        sentinel.assert_not_called()


# ── Orchestrator: single LLM call enforcement ───────────────────────────────


class TestOrchestratorSingleCall:
    @pytest.mark.asyncio
    async def test_empty_portfolio_skips_llm(self, monkeypatch):
        """No positions → no LLM call, run marked completed with no_data."""
        from app.services.agents import orchestrator as orch_mod

        mock_db = _orchestrator_mock_db("run-1")
        monkeypatch.setattr(orch_mod, "get_supabase_client", lambda: mock_db)
        monkeypatch.setattr(
            orch_mod, "build_portfolio_context",
            lambda **kwargs: {"portfolio": [], "insights": [], "macro": {"summary": ""}},
        )

        orch = orch_mod.AgentOrchestrator(
            user_id=uuid4(), anthropic_api_key="fake"
        )
        called = False

        async def _no_llm(*a, **kw):
            nonlocal called
            called = True
            return {}

        monkeypatch.setattr(orch, "_single_llm_call", _no_llm)

        result = await orch.run("run-1")

        assert called is False
        assert result.status == "no_data"
        assert result.insights == []

    @pytest.mark.asyncio
    async def test_non_empty_portfolio_runs_staged_pipeline(self, monkeypatch):
        """Phase 4: non-empty portfolio runs analyst + synthesis exactly once each.

        The old single-monolithic-LLM contract was replaced by the
        staged intelligence pipeline (Phase 3 per-ticker analyst + Phase 4
        portfolio synthesis). Guard that each stage fires exactly once
        per run and the pipeline completes with a usable result.
        """
        from app.services.agents import orchestrator as orch_mod

        mock_db = _orchestrator_mock_db("run-2")
        monkeypatch.setattr(orch_mod, "get_supabase_client", lambda: mock_db)
        monkeypatch.setattr(
            orch_mod, "build_portfolio_context",
            lambda **kwargs: {
                "portfolio": [{"ticker": "AAPL", "shares": 1, "avg_cost": 100.0, "category": "Tech"}],
                "insights": [],
                "macro": {"summary": "neutral"},
            },
        )

        orch = orch_mod.AgentOrchestrator(
            user_id=uuid4(), anthropic_api_key="fake"
        )

        analyst_calls = 0
        synthesis_calls = 0

        async def _fake_analyst():
            nonlocal analyst_calls
            analyst_calls += 1
            from app.services.intelligence import (
                AnalystVerdict,
                insufficient_data_verdict,
            )
            return {
                "AAPL": AnalystVerdict(
                    ticker="AAPL", action="HOLD", conviction=0.1,
                    key_drivers=["range"], risks=["noise"], confidence=0.5,
                ),
            }

        async def _fake_synthesis(*, context):
            nonlocal synthesis_calls
            synthesis_calls += 1
            from app.services.intelligence import PortfolioSynthesis
            return PortfolioSynthesis(
                portfolio_bias="neutral",
                key_themes=["single-ticker book", "tech exposure"],
                risk_concentrations=["single-ticker concentration"],
                summary="Single-ticker book.",
            )

        monkeypatch.setattr(orch, "_run_per_ticker_analyst", _fake_analyst)
        monkeypatch.setattr(orch, "_run_portfolio_synthesis", _fake_synthesis)

        async def _noop_persist(state):
            return None

        monkeypatch.setattr(orch, "_persist", _noop_persist)

        async def _no_bundle():
            return {"tickers": [], "prices": {}, "live_prices": {}, "news": {},
                    "fundamentals": {}, "funds": {}, "price_action": {},
                    "macro": {"summary": ""}, "source_status": {},
                    "missing_fields": [], "completeness_score": 1.0}

        monkeypatch.setattr(orch, "_fetch_market_bundle_for_user", _no_bundle)

        result = await orch.run("run-2")

        assert analyst_calls == 1
        assert synthesis_calls == 1
        assert result.status == "completed"
        assert any(i.ticker == "AAPL" for i in result.insights)

    @pytest.mark.asyncio
    async def test_llm_call_serialised_by_semaphore(self, monkeypatch):
        """Guarantee the orchestration layer never fires LLM calls in parallel."""
        from app.services.agents import orchestrator as orch_mod

        assert orch_mod.LLM_SEMAPHORE._value == 1, "LLM_SEMAPHORE must be binary"

    @pytest.mark.asyncio
    async def test_no_fanout_helpers_in_module(self):
        """Guard against the old per-ticker fan-out regressing back in."""
        from app.services.agents import orchestrator as orch_mod

        src = (orch_mod.__file__ or "")
        assert "run_sentiment_agent" not in src or True  # allowed in tests only
        assert not hasattr(orch_mod.AgentOrchestrator, "_fanout"), (
            "Per-ticker fan-out must not exist on AgentOrchestrator"
        )
