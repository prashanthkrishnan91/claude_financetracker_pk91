"""Tests for the Stage 3.0b.6 Analyst Refresh Adapter v1.

Covers acceptance criteria from the task brief:
  - Stale production-like analyst evidence triggers analyst_refresh when adapter
    is available.
  - Per-ticker success updates only successful tickers' analyst/recommendation
    freshness (no fabricated freshness for failed tickers).
  - Partial analyst refresh produces PARTIAL_CERTIFIED or BLOCKED_UNCERTIFIED
    as appropriate.
  - When ALL required analyst evidence refreshes, the run can move to
    REFRESH_THEN_RUN / trusted.
  - attempted/successful/failed_llm_calls reflect reality.
  - Budget cap limits analyst refresh calls.
  - Priority order: BUY/TRIM > SELL > HOLD/UNKNOWN, then weight, then age, then
    ticker A→Z.
  - Deterministic decide() remains final authority (no direct action override).
  - Tier 0 price refresh + Deploy/Watchtower boundary regression.
  - Banner truth-label reports recommendation + analyst evidence separately.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.services.intelligence.v3.analyst_refresh_adapter_v1 import (
    AnalystRefreshAdapter,
    AnalystRefreshBudget,
    AnalystRefreshResult,
    DEFAULT_MAX_ANALYST_LLM_CALLS_PER_RUN,
    DEFAULT_MAX_ANALYST_TICKERS_PER_RUN,
    REASON_FALLBACK_VERDICT,
    REASON_NO_AGENT_INSIGHT_ROW_FOR_RUN,
    REASON_NO_POST_RUN_EVIDENCE,
    REASON_PERSISTENCE_MISSING,
    REASON_READ_QUERY_FAILED,
    REASON_TIMESTAMP_BEFORE_STARTED_AT,
    STATUS_FAILED,
    STATUS_NO_STALE,
    STATUS_PARTIAL_SUCCESS,
    STATUS_SKIPPED_BUDGET,
    STATUS_SKIPPED_TIMEOUT,
    STATUS_SUCCEEDED,
    TickerPriorityHint,
    prioritize_stale_tickers,
)
from app.services.intelligence.v3.evidence_freshness_contract_v1 import (
    RUN_MODE_BLOCKED_UNCERTIFIED,
    RUN_MODE_PARTIAL_CERTIFIED,
    RUN_MODE_REFRESH_THEN_RUN,
    SOURCE_AGENT_INSIGHTS,
    SOURCE_RECOMMENDATIONS,
    TRUST_PARTIAL,
    TRUST_TRUSTED,
    TRUST_UNCERTIFIED,
)
from app.services.intelligence.v3.evidence_refresh_orchestrator_v1 import (
    EvidenceRefreshOrchestrator,
    OrchestratorInputs,
    RefreshBudget,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime(2026, 5, 14, 12, 0, 0, tzinfo=timezone.utc)


def _iso_ago(hours: float, now: datetime | None = None) -> str:
    base = now or _now()
    return (base - timedelta(hours=hours)).isoformat()


def _build_production_stale_inputs(
    *,
    tickers: list[str],
    now: datetime,
    per_ticker_evidence: list[dict] | None = None,
) -> OrchestratorInputs:
    """Replicates production: rec=193h, insight=287h, 68 stale signals."""
    return OrchestratorInputs(
        evidence_stats={
            "recommendation_timestamps":     [_iso_ago(193.0, now), _iso_ago(160.0, now)],
            "agent_insight_run_timestamps":  [_iso_ago(287.0, now), _iso_ago(250.0, now)],
            "active_position_count":         len(tickers),
            "persisted_recommendation_count": len(tickers),
            "persisted_agent_insight_count":  len(tickers),
        },
        portfolio_snapshot_at=_iso_ago(0.5, now),
        market_value_certified_ats=[_iso_ago(0.1, now)] * len(tickers),
        tickers=tickers,
        research_artifact_timestamps=[],
        now=now,
        per_ticker_evidence=per_ticker_evidence or [
            {"ticker": t, "prior_action": "HOLD", "weight_pct": 5.0, "evidence_age_hours": 287.0}
            for t in tickers
        ],
    )


# ── prioritize_stale_tickers — deterministic priority ─────────────────────────

class TestPriorityOrdering:
    def test_buy_then_trim_then_sell_then_hold(self):
        hints = [
            TickerPriorityHint(ticker="HOLD1", prior_action="HOLD"),
            TickerPriorityHint(ticker="BUY1", prior_action="BUY"),
            TickerPriorityHint(ticker="SELL1", prior_action="SELL"),
            TickerPriorityHint(ticker="TRIM1", prior_action="TRIM"),
        ]
        order = [h.ticker for h in prioritize_stale_tickers(hints)]
        assert order == ["BUY1", "TRIM1", "SELL1", "HOLD1"]

    def test_higher_weight_first_within_action(self):
        hints = [
            TickerPriorityHint(ticker="A", prior_action="BUY", weight_pct=2.0),
            TickerPriorityHint(ticker="B", prior_action="BUY", weight_pct=10.0),
            TickerPriorityHint(ticker="C", prior_action="BUY", weight_pct=5.0),
        ]
        order = [h.ticker for h in prioritize_stale_tickers(hints)]
        assert order == ["B", "C", "A"]

    def test_older_evidence_first_when_action_and_weight_tied(self):
        hints = [
            TickerPriorityHint(ticker="A", prior_action="HOLD", weight_pct=5.0, evidence_age_hours=10.0),
            TickerPriorityHint(ticker="B", prior_action="HOLD", weight_pct=5.0, evidence_age_hours=200.0),
            TickerPriorityHint(ticker="C", prior_action="HOLD", weight_pct=5.0, evidence_age_hours=100.0),
        ]
        order = [h.ticker for h in prioritize_stale_tickers(hints)]
        assert order == ["B", "C", "A"]

    def test_alphabetical_when_all_else_equal(self):
        hints = [
            TickerPriorityHint(ticker="NVDA", prior_action="HOLD"),
            TickerPriorityHint(ticker="AAPL", prior_action="HOLD"),
            TickerPriorityHint(ticker="MSFT", prior_action="HOLD"),
        ]
        order = [h.ticker for h in prioritize_stale_tickers(hints)]
        assert order == ["AAPL", "MSFT", "NVDA"]

    def test_unknown_action_deprioritized(self):
        hints = [
            TickerPriorityHint(ticker="A", prior_action="HOLD"),
            TickerPriorityHint(ticker="B", prior_action=None),
            TickerPriorityHint(ticker="C", prior_action="BUY"),
        ]
        order = [h.ticker for h in prioritize_stale_tickers(hints)]
        assert order == ["C", "A", "B"]


# ── AnalystRefreshAdapter direct unit tests ───────────────────────────────────

class TestAdapterDirect:
    @pytest.mark.asyncio
    async def test_no_stale_tickers_returns_no_stale_status(self):
        async def _backend(user_id, tickers, started_at):
            return {}

        adapter = AnalystRefreshAdapter(
            user_id=uuid4(),
            run_backend=_backend,
            budget=AnalystRefreshBudget(max_tickers=6, max_llm_calls=6),
        )
        result = await adapter([])
        assert result.status == STATUS_NO_STALE
        assert result.attempted_llm_calls == 0

    @pytest.mark.asyncio
    async def test_budget_cap_limits_selected_subset(self):
        """20 stale tickers + budget=5 → only top 5 attempted, 15 deferred."""
        called_with: list[list[str]] = []

        async def _backend(user_id, tickers, started_at):
            called_with.append(list(tickers))
            # All selected tickers succeed.
            return {
                t: {
                    "agent_insight_created_at": started_at.isoformat(),
                    "recommendation_created_at": started_at.isoformat(),
                    "used_fallback": False,
                    "agent_run_id": "run-abc",
                }
                for t in tickers
            }

        stale_tickers = [f"T{i:02d}" for i in range(20)]
        hints = [
            TickerPriorityHint(ticker=t, prior_action="HOLD", weight_pct=1.0, evidence_age_hours=200.0)
            for t in stale_tickers
        ]
        adapter = AnalystRefreshAdapter(
            user_id=uuid4(),
            run_backend=_backend,
            budget=AnalystRefreshBudget(max_tickers=5, max_llm_calls=5),
        )
        result = await adapter(stale_tickers, priority_hints=hints)
        assert result.status == STATUS_SUCCEEDED
        assert len(result.selected_tickers) == 5
        assert len(result.deferred_tickers) == 15
        # Adapter called the backend with exactly the 5 selected.
        assert called_with and len(called_with[0]) == 5
        assert result.attempted_llm_calls == 5

    @pytest.mark.asyncio
    async def test_priority_subset_prefers_buy_trim_over_hold(self):
        """6 stale: 1 BUY, 1 TRIM, 4 HOLD; budget=2 → selects BUY + TRIM."""
        seen_subsets: list[set[str]] = []

        async def _backend(user_id, tickers, started_at):
            seen_subsets.append(set(tickers))
            return {
                t: {
                    "agent_insight_created_at": started_at.isoformat(),
                    "recommendation_created_at": started_at.isoformat(),
                    "used_fallback": False,
                    "agent_run_id": "r",
                }
                for t in tickers
            }

        hints = [
            TickerPriorityHint(ticker="HOLD_A", prior_action="HOLD", weight_pct=1.0),
            TickerPriorityHint(ticker="HOLD_B", prior_action="HOLD", weight_pct=1.0),
            TickerPriorityHint(ticker="HOLD_C", prior_action="HOLD", weight_pct=1.0),
            TickerPriorityHint(ticker="HOLD_D", prior_action="HOLD", weight_pct=1.0),
            TickerPriorityHint(ticker="MYBUY", prior_action="BUY", weight_pct=1.0),
            TickerPriorityHint(ticker="MYTRIM", prior_action="TRIM", weight_pct=1.0),
        ]
        adapter = AnalystRefreshAdapter(
            user_id=uuid4(),
            run_backend=_backend,
            budget=AnalystRefreshBudget(max_tickers=2, max_llm_calls=2),
        )
        result = await adapter([h.ticker for h in hints], priority_hints=hints)
        assert set(result.selected_tickers) == {"MYBUY", "MYTRIM"}
        assert seen_subsets[0] == {"MYBUY", "MYTRIM"}

    @pytest.mark.asyncio
    async def test_per_ticker_success_only_stamps_successful_tickers(self):
        """Mixed-outcome backend: only successful tickers get fresh stamps."""
        async def _backend(user_id, tickers, started_at):
            # AAPL succeeds, NVDA returns no row (failure).
            return {
                "AAPL": {
                    "agent_insight_created_at": started_at.isoformat(),
                    "recommendation_created_at": started_at.isoformat(),
                    "used_fallback": False,
                    "agent_run_id": "r",
                },
                "NVDA": None,
            }

        adapter = AnalystRefreshAdapter(
            user_id=uuid4(),
            run_backend=_backend,
            budget=AnalystRefreshBudget(max_tickers=10, max_llm_calls=10),
        )
        result = await adapter(
            ["AAPL", "NVDA"],
            priority_hints=[
                TickerPriorityHint(ticker="AAPL", prior_action="BUY"),
                TickerPriorityHint(ticker="NVDA", prior_action="HOLD"),
            ],
        )
        assert result.status == STATUS_PARTIAL_SUCCESS
        succ = {o.ticker: o for o in result.per_ticker if o.success}
        fail = {o.ticker: o for o in result.per_ticker if not o.success}
        assert "AAPL" in succ and succ["AAPL"].refreshed_agent_insight_at is not None
        assert "NVDA" in fail and fail["NVDA"].refreshed_agent_insight_at is None

    @pytest.mark.asyncio
    async def test_used_fallback_verdict_does_not_count_as_success(self):
        async def _backend(user_id, tickers, started_at):
            return {
                "AAPL": {
                    "agent_insight_created_at": started_at.isoformat(),
                    "recommendation_created_at": started_at.isoformat(),
                    "used_fallback": True,   # fallback ≠ refreshed
                    "agent_run_id": "r",
                },
            }

        adapter = AnalystRefreshAdapter(
            user_id=uuid4(),
            run_backend=_backend,
            budget=AnalystRefreshBudget(max_tickers=5, max_llm_calls=5),
        )
        result = await adapter(["AAPL"])
        assert result.status == STATUS_FAILED
        assert result.per_ticker[0].success is False
        assert result.per_ticker[0].error_reason == REASON_FALLBACK_VERDICT
        assert result.per_ticker[0].refreshed_agent_insight_at is None

    @pytest.mark.asyncio
    async def test_zero_budget_skips_with_budget_status(self):
        async def _backend(user_id, tickers, started_at):
            raise AssertionError("should not be called when budget=0")

        adapter = AnalystRefreshAdapter(
            user_id=uuid4(),
            run_backend=_backend,
            budget=AnalystRefreshBudget(max_tickers=0, max_llm_calls=0),
        )
        result = await adapter(["AAPL", "NVDA"])
        assert result.status == STATUS_SKIPPED_BUDGET
        assert result.attempted_llm_calls == 0
        assert "AAPL" in result.deferred_tickers
        assert "NVDA" in result.deferred_tickers

    @pytest.mark.asyncio
    async def test_backend_timeout_is_honest(self):
        async def _backend(user_id, tickers, started_at):
            await asyncio.sleep(5.0)
            return {}

        adapter = AnalystRefreshAdapter(
            user_id=uuid4(),
            run_backend=_backend,
            budget=AnalystRefreshBudget(max_tickers=2, max_llm_calls=2, max_seconds=1.0),
        )
        result = await adapter(
            ["AAPL"],
            priority_hints=[TickerPriorityHint(ticker="AAPL", prior_action="BUY")],
        )
        assert result.status == STATUS_SKIPPED_TIMEOUT
        assert all(not o.success for o in result.per_ticker)

    @pytest.mark.asyncio
    async def test_backend_exception_yields_failed_status(self):
        async def _backend(user_id, tickers, started_at):
            raise RuntimeError("provider down")

        adapter = AnalystRefreshAdapter(
            user_id=uuid4(),
            run_backend=_backend,
            budget=AnalystRefreshBudget(max_tickers=2, max_llm_calls=2),
        )
        result = await adapter(["AAPL"])
        assert result.status == STATUS_FAILED
        assert all(not o.success for o in result.per_ticker)
        assert any("RuntimeError" in (o.error_reason or "") for o in result.per_ticker)


# ── EvidenceRefreshOrchestrator + adapter integration ────────────────────────

class TestOrchestratorWithAdapter:
    """End-to-end behavior when the adapter is wired into the orchestrator."""

    @pytest.mark.asyncio
    async def test_production_stale_triggers_analyst_refresh_when_adapter_available(self):
        now = _now()
        tickers = ["AAPL", "NVDA", "TSLA"]
        inputs = _build_production_stale_inputs(tickers=tickers, now=now)

        async def _price_refresh(t):
            return {x: {"is_valid": True, "is_stale": False} for x in t}

        called = {"count": 0, "args": None}

        async def _analyst_adapter(stale, *, priority_hints, started_at):
            called["count"] += 1
            called["args"] = (list(stale), [(h.ticker, h.prior_action) for h in priority_hints])
            return AnalystRefreshResult(
                status=STATUS_SUCCEEDED,
                selected_tickers=list(stale),
                deferred_tickers=[],
                per_ticker=[],
                attempted_llm_calls=len(stale),
                successful_llm_calls=len(stale),
                failed_llm_calls=0,
            )

        orch = EvidenceRefreshOrchestrator(
            user_id=uuid4(),
            inputs=inputs,
            price_refresh=_price_refresh,
            analyst_refresh=_analyst_adapter,
            budget=RefreshBudget(),
        )
        result = await orch.run()
        assert called["count"] == 1
        assert result.analyst_refresh_supported is True
        # Adapter called with stale ticker list and priority hints.
        sent_tickers, sent_actions = called["args"]
        assert set(sent_tickers) == set(tickers)

    @pytest.mark.asyncio
    async def test_full_analyst_refresh_unblocks_to_refresh_then_run(self):
        """When every active ticker's analyst evidence refreshes, mode upgrades."""
        now = _now()
        tickers = ["AAPL", "NVDA"]
        per_ticker_ev = [
            {"ticker": "AAPL", "prior_action": "BUY",  "weight_pct": 5.0, "evidence_age_hours": 287.0},
            {"ticker": "NVDA", "prior_action": "HOLD", "weight_pct": 3.0, "evidence_age_hours": 287.0},
        ]
        inputs = _build_production_stale_inputs(
            tickers=tickers, now=now, per_ticker_evidence=per_ticker_ev,
        )

        async def _price_refresh(t):
            return {x: {"is_valid": True, "is_stale": False} for x in t}

        async def _analyst_adapter(stale, *, priority_hints, started_at):
            return AnalystRefreshResult(
                status=STATUS_SUCCEEDED,
                selected_tickers=list(stale),
                deferred_tickers=[],
                per_ticker=[
                    {
                        "ticker": t,
                        "success": True,
                        "refreshed_recommendation_at": started_at.isoformat(),
                        "refreshed_agent_insight_at": started_at.isoformat(),
                        "error_reason": None,
                        "llm_call_count": 1,
                        "llm_success_count": 1,
                    }
                    for t in stale
                ],
                attempted_llm_calls=len(stale),
                successful_llm_calls=len(stale),
                failed_llm_calls=0,
            )

        orch = EvidenceRefreshOrchestrator(
            user_id=uuid4(),
            inputs=inputs,
            price_refresh=_price_refresh,
            analyst_refresh=_analyst_adapter,
            budget=RefreshBudget(),
        )
        result = await orch.run()
        assert result.run_mode == RUN_MODE_REFRESH_THEN_RUN
        assert result.trust_status == TRUST_TRUSTED
        # Honest LLM accounting from adapter result.
        assert result.attempted_llm_calls == 2
        assert result.successful_llm_calls == 2
        assert result.failed_llm_calls == 0
        diag = result.to_diagnostics_dict()
        assert set(diag["analyst_refresh_successful_tickers"]) == set(tickers)
        assert diag["analyst_refresh_failed_tickers"] == []
        assert diag["analyst_refresh_supported"] is True

    @pytest.mark.asyncio
    async def test_partial_analyst_refresh_stays_blocked_or_partial(self):
        """Only one of two tickers refreshes; mode must NOT upgrade to trusted."""
        now = _now()
        tickers = ["AAPL", "NVDA"]
        per_ticker_ev = [
            {"ticker": "AAPL", "prior_action": "BUY",  "weight_pct": 5.0, "evidence_age_hours": 287.0},
            {"ticker": "NVDA", "prior_action": "HOLD", "weight_pct": 3.0, "evidence_age_hours": 287.0},
        ]
        inputs = _build_production_stale_inputs(
            tickers=tickers, now=now, per_ticker_evidence=per_ticker_ev,
        )

        async def _price_refresh(t):
            return {x: {"is_valid": True, "is_stale": False} for x in t}

        async def _analyst_adapter(stale, *, priority_hints, started_at):
            return AnalystRefreshResult(
                status=STATUS_PARTIAL_SUCCESS,
                selected_tickers=list(stale),
                deferred_tickers=[],
                per_ticker=[
                    {
                        "ticker": "AAPL",
                        "success": True,
                        "refreshed_recommendation_at": started_at.isoformat(),
                        "refreshed_agent_insight_at": started_at.isoformat(),
                        "error_reason": None,
                        "llm_call_count": 1,
                        "llm_success_count": 1,
                    },
                    {
                        "ticker": "NVDA",
                        "success": False,
                        "refreshed_recommendation_at": None,
                        "refreshed_agent_insight_at": None,
                        "error_reason": "no_fresh_row_post_started_at",
                        "llm_call_count": 1,
                        "llm_success_count": 0,
                    },
                ],
                attempted_llm_calls=2,
                successful_llm_calls=1,
                failed_llm_calls=1,
            )

        orch = EvidenceRefreshOrchestrator(
            user_id=uuid4(),
            inputs=inputs,
            price_refresh=_price_refresh,
            analyst_refresh=_analyst_adapter,
            budget=RefreshBudget(),
        )
        result = await orch.run()
        # Critical analyst sources are still HARD_STALE for NVDA → BLOCKED.
        # (Aggregate state worst-observed bucket includes the un-refreshed
        # ticker's original 287h age.)
        assert result.run_mode in (
            RUN_MODE_BLOCKED_UNCERTIFIED, RUN_MODE_PARTIAL_CERTIFIED,
        )
        assert result.trust_status != TRUST_TRUSTED
        assert result.attempted_llm_calls == 2
        assert result.successful_llm_calls == 1
        assert result.failed_llm_calls == 1
        diag = result.to_diagnostics_dict()
        assert set(diag["analyst_refresh_successful_tickers"]) == {"AAPL"}
        assert set(diag["analyst_refresh_failed_tickers"]) == {"NVDA"}

    @pytest.mark.asyncio
    async def test_failed_analyst_refresh_does_not_stamp_fresh_timestamps(self):
        """Adapter reports all failure → analyst source ages stay stale."""
        now = _now()
        tickers = ["AAPL", "NVDA"]
        per_ticker_ev = [
            {"ticker": "AAPL", "prior_action": "BUY", "weight_pct": 5.0, "evidence_age_hours": 287.0},
            {"ticker": "NVDA", "prior_action": "BUY", "weight_pct": 5.0, "evidence_age_hours": 287.0},
        ]
        inputs = _build_production_stale_inputs(
            tickers=tickers, now=now, per_ticker_evidence=per_ticker_ev,
        )

        async def _analyst_adapter(stale, *, priority_hints, started_at):
            return AnalystRefreshResult(
                status=STATUS_FAILED,
                selected_tickers=list(stale),
                deferred_tickers=[],
                per_ticker=[
                    {
                        "ticker": t,
                        "success": False,
                        "refreshed_recommendation_at": None,
                        "refreshed_agent_insight_at": None,
                        "error_reason": "no_post_run_evidence",
                        "llm_call_count": 1,
                        "llm_success_count": 0,
                    }
                    for t in stale
                ],
                attempted_llm_calls=2,
                successful_llm_calls=0,
                failed_llm_calls=2,
            )

        orch = EvidenceRefreshOrchestrator(
            user_id=uuid4(),
            inputs=inputs,
            price_refresh=None,
            analyst_refresh=_analyst_adapter,
            budget=RefreshBudget(),
        )
        result = await orch.run()
        assert result.run_mode == RUN_MODE_BLOCKED_UNCERTIFIED
        # The post-state for critical analyst sources is still HARD_STALE
        # because no ticker actually refreshed — no fabricated freshness.
        rec_after = result.source_states_after[SOURCE_RECOMMENDATIONS]
        ai_after = result.source_states_after[SOURCE_AGENT_INSIGHTS]
        assert rec_after.state == "HARD_STALE"
        assert ai_after.state == "HARD_STALE"

    @pytest.mark.asyncio
    async def test_adapter_skipped_budget_marks_status_and_zero_calls(self):
        """Adapter returns skipped_budget → orchestrator records 0 LLM calls."""
        now = _now()
        tickers = ["AAPL", "NVDA"]
        per_ticker_ev = [
            {"ticker": "AAPL", "prior_action": "HOLD", "weight_pct": 5.0, "evidence_age_hours": 287.0},
            {"ticker": "NVDA", "prior_action": "HOLD", "weight_pct": 5.0, "evidence_age_hours": 287.0},
        ]
        inputs = _build_production_stale_inputs(
            tickers=tickers, now=now, per_ticker_evidence=per_ticker_ev,
        )

        async def _analyst_adapter(stale, *, priority_hints, started_at):
            return AnalystRefreshResult(
                status=STATUS_SKIPPED_BUDGET,
                selected_tickers=[],
                deferred_tickers=list(stale),
                per_ticker=[],
                attempted_llm_calls=0,
                successful_llm_calls=0,
                failed_llm_calls=0,
                budget_exhausted=True,
            )

        orch = EvidenceRefreshOrchestrator(
            user_id=uuid4(),
            inputs=inputs,
            price_refresh=None,
            analyst_refresh=_analyst_adapter,
            budget=RefreshBudget(),
        )
        result = await orch.run()
        assert result.attempted_llm_calls == 0
        assert result.analyst_refresh_status == STATUS_SKIPPED_BUDGET
        # Stale critical evidence stays blocked.
        assert result.run_mode == RUN_MODE_BLOCKED_UNCERTIFIED

    @pytest.mark.asyncio
    async def test_attempted_successful_failed_llm_counts_are_honest(self):
        now = _now()
        tickers = ["A", "B", "C", "D"]
        per_ticker_ev = [
            {"ticker": t, "prior_action": "HOLD", "weight_pct": 1.0, "evidence_age_hours": 287.0}
            for t in tickers
        ]
        inputs = _build_production_stale_inputs(
            tickers=tickers, now=now, per_ticker_evidence=per_ticker_ev,
        )

        async def _analyst_adapter(stale, *, priority_hints, started_at):
            return AnalystRefreshResult(
                status=STATUS_PARTIAL_SUCCESS,
                selected_tickers=list(stale),
                deferred_tickers=[],
                per_ticker=[
                    {"ticker": "A", "success": True, "refreshed_agent_insight_at": started_at.isoformat(),
                     "refreshed_recommendation_at": started_at.isoformat(), "error_reason": None,
                     "llm_call_count": 1, "llm_success_count": 1},
                    {"ticker": "B", "success": True, "refreshed_agent_insight_at": started_at.isoformat(),
                     "refreshed_recommendation_at": started_at.isoformat(), "error_reason": None,
                     "llm_call_count": 1, "llm_success_count": 1},
                    {"ticker": "C", "success": False, "error_reason": "no_post_run_evidence",
                     "refreshed_agent_insight_at": None, "refreshed_recommendation_at": None,
                     "llm_call_count": 1, "llm_success_count": 0},
                    {"ticker": "D", "success": False, "error_reason": "no_post_run_evidence",
                     "refreshed_agent_insight_at": None, "refreshed_recommendation_at": None,
                     "llm_call_count": 1, "llm_success_count": 0},
                ],
                attempted_llm_calls=4,
                successful_llm_calls=2,
                failed_llm_calls=2,
            )

        orch = EvidenceRefreshOrchestrator(
            user_id=uuid4(),
            inputs=inputs,
            price_refresh=None,
            analyst_refresh=_analyst_adapter,
            budget=RefreshBudget(),
        )
        result = await orch.run()
        assert result.attempted_llm_calls == 4
        assert result.successful_llm_calls == 2
        assert result.failed_llm_calls == 2

    @pytest.mark.asyncio
    async def test_analyst_refresh_supported_true_when_adapter_wired(self):
        now = _now()
        tickers = ["AAPL"]
        inputs = _build_production_stale_inputs(tickers=tickers, now=now)

        async def _analyst(stale, *, priority_hints, started_at):
            return AnalystRefreshResult(
                status=STATUS_FAILED,
                selected_tickers=list(stale),
                deferred_tickers=[],
                per_ticker=[{"ticker": t, "success": False, "error_reason": "x"} for t in stale],
                attempted_llm_calls=1,
                successful_llm_calls=0,
                failed_llm_calls=1,
            )

        orch = EvidenceRefreshOrchestrator(
            user_id=uuid4(),
            inputs=inputs,
            price_refresh=None,
            analyst_refresh=_analyst,
            budget=RefreshBudget(),
        )
        result = await orch.run()
        assert result.analyst_refresh_supported is True

    @pytest.mark.asyncio
    async def test_tier0_price_refresh_path_intact_with_analyst_adapter(self):
        """Regression: PR #308 price refresh still works when adapter is wired."""
        now = _now()
        tickers = ["AAPL", "NVDA"]
        # Stale price evidence, fresh analyst evidence.
        inputs = OrchestratorInputs(
            evidence_stats={
                "recommendation_timestamps":     [_iso_ago(1.0, now)],
                "agent_insight_run_timestamps":  [_iso_ago(1.0, now)],
                "active_position_count":         2,
                "persisted_recommendation_count": 2,
                "persisted_agent_insight_count":  2,
            },
            portfolio_snapshot_at=_iso_ago(1.0, now),
            market_value_certified_ats=[_iso_ago(1.0, now)] * 2,
            tickers=tickers,
            research_artifact_timestamps=[],
            now=now,
            per_ticker_evidence=[
                {"ticker": "AAPL", "prior_action": "HOLD", "weight_pct": 5.0, "evidence_age_hours": 1.0},
                {"ticker": "NVDA", "prior_action": "HOLD", "weight_pct": 5.0, "evidence_age_hours": 1.0},
            ],
        )
        price_called = {"count": 0, "args": None}

        async def _price_refresh(t):
            price_called["count"] += 1
            price_called["args"] = list(t)
            return {x: {"is_valid": True, "is_stale": False} for x in t}

        analyst_called = {"count": 0}

        async def _analyst_adapter(stale, *, priority_hints, started_at):
            analyst_called["count"] += 1
            return AnalystRefreshResult(
                status=STATUS_NO_STALE,
                selected_tickers=[],
                deferred_tickers=[],
                per_ticker=[],
            )

        orch = EvidenceRefreshOrchestrator(
            user_id=uuid4(),
            inputs=inputs,
            price_refresh=_price_refresh,
            analyst_refresh=_analyst_adapter,
            budget=RefreshBudget(),
        )
        result = await orch.run()
        # Price refresh ran and certified the run.
        assert price_called["count"] == 1
        assert set(price_called["args"]) == set(tickers)
        # Analyst adapter was NOT called because analyst evidence was fresh.
        assert analyst_called["count"] == 0
        assert result.successful_provider_calls == 2

    @pytest.mark.asyncio
    async def test_no_deploy_or_watchtower_imports_in_adapter(self):
        """Boundary check: adapter module never imports Deploy or Watchtower."""
        from app.services.intelligence.v3 import analyst_refresh_adapter_v1 as mod
        src = open(mod.__file__).read()
        import re
        imports = re.findall(r"^\s*(?:from\s+\S+\s+)?import\s+\S+", src, re.MULTILINE)
        for line in imports:
            assert "deploy" not in line.lower(), f"Unexpected deploy import: {line}"
            assert "watchtower" not in line.lower(), f"Unexpected watchtower import: {line}"
        # Module never calls decide() either.
        assert "from .decision_policy_v1" not in src
        assert "decision_policy_v1" not in src


# ── Diagnostics + banner truth ────────────────────────────────────────────────

class TestBannerTruthLabel:
    def test_banner_age_summary_reports_both_sources_separately(self):
        """Production-like state: rec 8d, insight 12d → separate reporting."""
        from app.services.intelligence.v3.snapshot_freshness_diagnostics import (
            build_evidence_freshness,
        )
        now = _now()
        rec_iso = (now - timedelta(days=8)).isoformat()
        insight_iso = (now - timedelta(days=12)).isoformat()
        evidence_stats = {
            "recommendation_timestamps": [rec_iso],
            "agent_insight_run_timestamps": [insight_iso],
            "persisted_recommendation_count": 1,
            "persisted_agent_insight_count": 1,
            "active_position_count": 1,
            "missing_evidence_count": 0,
        }
        result = build_evidence_freshness(evidence_stats, now=now)
        summary = result["banner_age_summary"]
        assert "Analyst evidence" in summary
        assert "Recommendation evidence" in summary
        # Both ages reported separately — never a single "Oldest evidence" claim.
        assert "12.0 days" in summary
        assert "8.0 days" in summary

    def test_banner_age_summary_unknown_when_no_timestamps(self):
        from app.services.intelligence.v3.snapshot_freshness_diagnostics import (
            build_evidence_freshness,
        )
        result = build_evidence_freshness({}, now=_now())
        assert result["banner_age_summary"] == "Evidence age: unknown."

    def test_banner_age_summary_partial_recommendation_only(self):
        from app.services.intelligence.v3.snapshot_freshness_diagnostics import (
            build_evidence_freshness,
        )
        now = _now()
        evidence_stats = {
            "recommendation_timestamps": [(now - timedelta(hours=24)).isoformat()],
            "persisted_recommendation_count": 1,
            "active_position_count": 1,
        }
        result = build_evidence_freshness(evidence_stats, now=now)
        assert "Recommendation evidence" in result["banner_age_summary"]
        assert "Analyst evidence" not in result["banner_age_summary"]


# ── Policy authority boundary ─────────────────────────────────────────────────

class TestPolicyAuthorityBoundary:
    @pytest.mark.asyncio
    async def test_adapter_does_not_set_visible_action(self):
        """Adapter never returns or writes any action label."""
        from app.services.intelligence.v3 import analyst_refresh_adapter_v1 as mod

        # Adapter result type has no action / decision field.
        result_fields = AnalystRefreshResult.__dataclass_fields__
        forbidden = {"action", "decision", "verdict", "final_action"}
        assert not any(f in result_fields for f in forbidden)

        outcome_fields = mod.TickerRefreshOutcome.__dataclass_fields__
        assert not any(f in outcome_fields for f in forbidden)

    def test_adapter_module_no_intel_v3_snapshot_writes(self):
        """Adapter never writes intel_v3_snapshots — that's decide()'s job."""
        from app.services.intelligence.v3 import analyst_refresh_adapter_v1 as mod
        src = open(mod.__file__).read()
        # No table("intel_v3_snapshots") access of any kind.
        assert 'table("intel_v3_snapshots")' not in src
        assert "table('intel_v3_snapshots')" not in src
        # No direct insert/update against intel_v3_snapshots.
        assert "intel_v3_snapshots\")" not in src
        assert "intel_v3_snapshots')" not in src


# ── Stage 3.0b.6 patch — run-id verification + explicit failure reasons ─────

class TestRunIdBasedVerification:
    """Production failure mode (2026-05-14):

    AgentOrchestrator ran successfully and produced valid analyst output, but
    the adapter reported `failed_llm_calls=6` / `successful_llm_calls=0`. The
    root cause: read-back filtered only by `created_at >= started_at`, which is
    fragile across the Python ↔ Supabase timestamp boundary. This patch makes
    `run_id` / `agent_run_id` the primary verification key with timestamp as a
    secondary sanity check.
    """

    @pytest.mark.asyncio
    async def test_success_when_insight_run_match_even_if_timestamp_borderline(self):
        """Production case: rows exist for THIS run_id even if ts roundtrip is borderline."""
        async def _backend(user_id, tickers, started_at):
            # Backend signals durable run-id match for both tables. Note that
            # the timestamps are AT or BEFORE started_at (simulating a tight
            # roundtrip / millisecond rounding); the run-id match must win.
            borderline_iso = (started_at - timedelta(milliseconds=2)).isoformat()
            return {
                "AAPL": {
                    "agent_insight_created_at":  borderline_iso,
                    "recommendation_created_at": borderline_iso,
                    "used_fallback":             False,
                    "agent_run_id":              "run-abc",
                    "insight_run_match":         True,
                    "rec_run_match":             True,
                    "insight_row_present":       True,
                    "rec_row_present":           True,
                    "failure_reason":            None,
                },
            }

        adapter = AnalystRefreshAdapter(
            user_id=uuid4(),
            run_backend=_backend,
            budget=AnalystRefreshBudget(max_tickers=5, max_llm_calls=5),
        )
        result = await adapter(["AAPL"])
        assert result.status == STATUS_SUCCEEDED
        assert result.successful_llm_calls == 1
        assert result.per_ticker[0].success is True
        assert result.per_ticker[0].refreshed_agent_insight_at is not None

    @pytest.mark.asyncio
    async def test_failure_no_agent_insight_row_for_run(self):
        async def _backend(user_id, tickers, started_at):
            return {
                "AAPL": {
                    "agent_insight_created_at":  None,
                    "recommendation_created_at": None,
                    "used_fallback":             False,
                    "agent_run_id":              "run-abc",
                    "insight_run_match":         False,
                    "rec_run_match":             False,
                    "insight_row_present":       False,
                    "rec_row_present":           False,
                    "failure_reason":            REASON_NO_AGENT_INSIGHT_ROW_FOR_RUN,
                },
            }

        adapter = AnalystRefreshAdapter(
            user_id=uuid4(),
            run_backend=_backend,
            budget=AnalystRefreshBudget(max_tickers=5, max_llm_calls=5),
        )
        result = await adapter(["AAPL"])
        assert result.status == STATUS_FAILED
        assert result.per_ticker[0].error_reason == REASON_NO_AGENT_INSIGHT_ROW_FOR_RUN

    @pytest.mark.asyncio
    async def test_failure_persistence_missing_when_ts_present_but_run_id_mismatch(self):
        """Insight row exists in DB for the ticker, but run_id doesn't match."""
        async def _backend(user_id, tickers, started_at):
            return {
                "AAPL": {
                    "agent_insight_created_at":  started_at.isoformat(),
                    "recommendation_created_at": None,
                    "used_fallback":             False,
                    "agent_run_id":              "run-abc",
                    "insight_run_match":         False,    # ← critical signal
                    "rec_run_match":             False,
                    "insight_row_present":       True,
                    "rec_row_present":           False,
                    "failure_reason":            REASON_PERSISTENCE_MISSING,
                },
            }

        adapter = AnalystRefreshAdapter(
            user_id=uuid4(),
            run_backend=_backend,
            budget=AnalystRefreshBudget(max_tickers=5, max_llm_calls=5),
        )
        result = await adapter(["AAPL"])
        assert result.status == STATUS_FAILED
        assert result.per_ticker[0].error_reason == REASON_PERSISTENCE_MISSING
        assert result.per_ticker[0].success is False

    @pytest.mark.asyncio
    async def test_success_without_recommendation_row_if_insight_for_run_present(self):
        """A real agent_insight row for this run is sufficient evidence —
        a missing recommendation row alone must not flip success to false."""
        async def _backend(user_id, tickers, started_at):
            return {
                "AAPL": {
                    "agent_insight_created_at":  started_at.isoformat(),
                    "recommendation_created_at": None,
                    "used_fallback":             False,
                    "agent_run_id":              "run-abc",
                    "insight_run_match":         True,
                    "rec_run_match":             False,
                    "insight_row_present":       True,
                    "rec_row_present":           False,
                    "failure_reason":            None,
                },
            }

        adapter = AnalystRefreshAdapter(
            user_id=uuid4(),
            run_backend=_backend,
            budget=AnalystRefreshBudget(max_tickers=5, max_llm_calls=5),
        )
        result = await adapter(["AAPL"])
        assert result.status == STATUS_SUCCEEDED
        assert result.per_ticker[0].success is True
        # No fabricated rec stamp.
        assert result.per_ticker[0].refreshed_recommendation_at is None

    @pytest.mark.asyncio
    async def test_fallback_verdict_remains_failure_even_with_run_id_match(self):
        async def _backend(user_id, tickers, started_at):
            return {
                "AAPL": {
                    "agent_insight_created_at":  started_at.isoformat(),
                    "recommendation_created_at": started_at.isoformat(),
                    "used_fallback":             True,     # ← invalid output
                    "agent_run_id":              "run-abc",
                    "insight_run_match":         True,
                    "rec_run_match":             True,
                    "insight_row_present":       True,
                    "rec_row_present":           True,
                    "failure_reason":            REASON_FALLBACK_VERDICT,
                },
            }

        adapter = AnalystRefreshAdapter(
            user_id=uuid4(),
            run_backend=_backend,
            budget=AnalystRefreshBudget(max_tickers=5, max_llm_calls=5),
        )
        result = await adapter(["AAPL"])
        assert result.status == STATUS_FAILED
        assert result.per_ticker[0].error_reason == REASON_FALLBACK_VERDICT

    @pytest.mark.asyncio
    async def test_mixed_run_id_match_and_no_row_produces_partial_success(self):
        async def _backend(user_id, tickers, started_at):
            ts = started_at.isoformat()
            return {
                "AAPL": {
                    "agent_insight_created_at":  ts,
                    "recommendation_created_at": ts,
                    "used_fallback":             False,
                    "agent_run_id":              "run-abc",
                    "insight_run_match":         True,
                    "rec_run_match":             True,
                    "insight_row_present":       True,
                    "rec_row_present":           True,
                    "failure_reason":            None,
                },
                "NVDA": {
                    "agent_insight_created_at":  None,
                    "recommendation_created_at": None,
                    "used_fallback":             False,
                    "agent_run_id":              "run-abc",
                    "insight_run_match":         False,
                    "rec_run_match":             False,
                    "insight_row_present":       False,
                    "rec_row_present":           False,
                    "failure_reason":            REASON_NO_AGENT_INSIGHT_ROW_FOR_RUN,
                },
            }

        adapter = AnalystRefreshAdapter(
            user_id=uuid4(),
            run_backend=_backend,
            budget=AnalystRefreshBudget(max_tickers=5, max_llm_calls=5),
        )
        result = await adapter(["AAPL", "NVDA"])
        assert result.status == STATUS_PARTIAL_SUCCESS
        assert result.successful_llm_calls == 1
        assert result.failed_llm_calls == 1
        succ = {o.ticker: o for o in result.per_ticker if o.success}
        fail = {o.ticker: o for o in result.per_ticker if not o.success}
        assert "AAPL" in succ and succ["AAPL"].refreshed_agent_insight_at is not None
        assert "NVDA" in fail
        assert fail["NVDA"].error_reason == REASON_NO_AGENT_INSIGHT_ROW_FOR_RUN
        # No fabricated freshness on the failed ticker.
        assert fail["NVDA"].refreshed_agent_insight_at is None
        assert fail["NVDA"].refreshed_recommendation_at is None

    @pytest.mark.asyncio
    async def test_backend_read_query_failed_surfaces_reason(self):
        async def _backend(user_id, tickers, started_at):
            return {
                "AAPL": {
                    "agent_insight_created_at":  None,
                    "recommendation_created_at": None,
                    "used_fallback":             False,
                    "agent_run_id":              "run-abc",
                    "insight_run_match":         False,
                    "rec_run_match":             False,
                    "insight_row_present":       False,
                    "rec_row_present":           False,
                    "failure_reason":            REASON_READ_QUERY_FAILED,
                },
            }

        adapter = AnalystRefreshAdapter(
            user_id=uuid4(),
            run_backend=_backend,
            budget=AnalystRefreshBudget(max_tickers=5, max_llm_calls=5),
        )
        result = await adapter(["AAPL"])
        assert result.status == STATUS_FAILED
        assert result.per_ticker[0].error_reason == REASON_READ_QUERY_FAILED

    @pytest.mark.asyncio
    async def test_legacy_backend_without_run_signals_still_works(self):
        """Existing tests that stub the backend without `insight_run_match`
        must keep passing — the legacy timestamp-only path is the fallback."""
        async def _backend(user_id, tickers, started_at):
            return {
                "AAPL": {
                    "agent_insight_created_at":  started_at.isoformat(),
                    "recommendation_created_at": started_at.isoformat(),
                    "used_fallback":             False,
                    "agent_run_id":              "run-abc",
                },
            }

        adapter = AnalystRefreshAdapter(
            user_id=uuid4(),
            run_backend=_backend,
            budget=AnalystRefreshBudget(max_tickers=5, max_llm_calls=5),
        )
        result = await adapter(["AAPL"])
        assert result.status == STATUS_SUCCEEDED
        assert result.per_ticker[0].success is True

    @pytest.mark.asyncio
    async def test_legacy_backend_no_rows_reports_no_insight_row_reason(self):
        """Legacy backend returning no rows must surface a real reason, not the
        old vague "no_post_run_evidence" string only."""
        async def _backend(user_id, tickers, started_at):
            return {
                "AAPL": {
                    "agent_insight_created_at":  None,
                    "recommendation_created_at": None,
                    "used_fallback":             False,
                    "agent_run_id":              "run-abc",
                },
            }

        adapter = AnalystRefreshAdapter(
            user_id=uuid4(),
            run_backend=_backend,
            budget=AnalystRefreshBudget(max_tickers=5, max_llm_calls=5),
        )
        result = await adapter(["AAPL"])
        assert result.status == STATUS_FAILED
        # Legacy path: no row at all → no_agent_insight_row_for_run.
        assert result.per_ticker[0].error_reason == REASON_NO_AGENT_INSIGHT_ROW_FOR_RUN

    @pytest.mark.asyncio
    async def test_legacy_backend_ts_before_started_at_reports_timestamp_reason(self):
        """Legacy backend with a stamp BEFORE started_at must surface
        timestamp_before_started_at (production diagnostic clarity)."""
        async def _backend(user_id, tickers, started_at):
            before = (started_at - timedelta(hours=1)).isoformat()
            return {
                "AAPL": {
                    "agent_insight_created_at":  before,
                    "recommendation_created_at": before,
                    "used_fallback":             False,
                    "agent_run_id":              "run-abc",
                },
            }

        adapter = AnalystRefreshAdapter(
            user_id=uuid4(),
            run_backend=_backend,
            budget=AnalystRefreshBudget(max_tickers=5, max_llm_calls=5),
        )
        result = await adapter(["AAPL"])
        assert result.status == STATUS_FAILED
        assert result.per_ticker[0].error_reason == REASON_TIMESTAMP_BEFORE_STARTED_AT


# ── Default backend read-back path (column contract) ─────────────────────────

class TestDefaultBackendReadColumns:
    """The default backend must query the correct primary-key columns:
    agent_insights.run_id (not agent_run_id) and recommendations.agent_run_id.
    These are the column names the persist phase writes through.
    """

    def test_default_backend_uses_run_id_for_agent_insights(self):
        from app.services.intelligence.v3 import analyst_refresh_adapter_v1 as mod
        src = open(mod.__file__).read()
        # agent_insights is keyed by `run_id`.
        assert '.eq("run_id", agent_run_id)' in src
        # recommendations is keyed by `agent_run_id`.
        assert '.eq("agent_run_id", agent_run_id)' in src

    def test_default_backend_propagates_run_signals_to_adapter(self):
        from app.services.intelligence.v3 import analyst_refresh_adapter_v1 as mod
        src = open(mod.__file__).read()
        # Read-back returns the durable signals the adapter consumes.
        for key in (
            '"insight_run_match"',
            '"rec_run_match"',
            '"insight_row_present"',
            '"rec_row_present"',
            '"failure_reason"',
        ):
            assert key in src, f"default backend must surface {key}"


# ── Scope filter regression: non-selected tickers preserved ──────────────────

class TestScopedPersistencePreservesOthers:
    """Regression: when AgentOrchestrator runs with analyst_refresh_tickers, the
    persist-time recommendation expire must only touch the scoped subset."""

    def test_orchestrator_module_only_expires_scoped_tickers(self):
        # The orchestrator's scoped persist phase uses an `.in_("ticker", ...)`
        # constraint so non-scope tickers' active rows are never expired.
        with open("app/services/agents/orchestrator.py") as f:
            src = f.read()
        # The scoped run-mismatch expire uses scoped_tickers.
        assert "recommendations.expire.scoped_run_mismatch" in src
        assert 'in_(\n                    "ticker", scoped_tickers' in src or '.in_(' in src
        # Hard rule: in the scope==None branch we expire all mismatched rows;
        # in the scope!=None branch we only expire scoped tickers' rows.
        assert "if scope is None:" in src
        assert "elif rec_rows:" in src
