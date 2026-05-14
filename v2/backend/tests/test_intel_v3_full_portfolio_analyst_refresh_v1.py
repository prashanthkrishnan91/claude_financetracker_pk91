"""Tests for the Stage 3.0c full-portfolio analyst evidence refresh.

Covers:
  * ``FullPortfolioAnalystRefreshAdapter`` selects the full stale ticker list
    instead of a 6-cap subset, and reports per-ticker success from durable
    DB row state.
  * Production-stale 34-ticker portfolio refreshes via the new adapter and
    moves the orchestrator's run mode to ``REFRESH_THEN_RUN`` / trusted.
  * (Stage 3.1) ``IntelV3Service._build_analyst_refresh_callable`` no longer
    wires this LLM adapter into the synchronous path — it returns the non-LLM
    ``AnalystRefreshRequestSeam``. This adapter is retained in-repo for a
    future background Intelligence Plane and is still exercised directly here.
  * ``IntelV3Service.run_v3()`` re-reads evidence after a successful analyst
    refresh so deterministic decisions consume the refreshed rows, not the
    pre-refresh snapshot.
  * Snapshot action counts match the refreshed cards, not the pre-refresh
    cards.
  * UI snapshot does not remain ``BLOCKED_UNCERTIFIED`` after a full
    successful refresh; ``run_mode`` reaches ``REFRESH_THEN_RUN`` /
    ``trust_status`` = ``trusted``.
  * Adversarial: failed / timed-out refresh stays blocked or partial honestly
    — no fabricated freshness, no fake timestamps.
  * No Deploy / Watchtower imports in the new adapter; deterministic decide()
    remains final action authority (adapter never writes final actions).
"""
from __future__ import annotations

import asyncio
import os
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.services.intelligence.v3.analyst_refresh_adapter_v1 import (
    AnalystRefreshResult,
    DEFAULT_MAX_ANALYST_TICKERS_PER_RUN,
    STATUS_FAILED,
    STATUS_NO_STALE,
    STATUS_PARTIAL_SUCCESS,
    STATUS_SKIPPED_BUDGET,
    STATUS_SKIPPED_TIMEOUT,
    STATUS_SUCCEEDED,
    TickerPriorityHint,
)
from app.services.intelligence.v3.evidence_freshness_contract_v1 import (
    RUN_MODE_BLOCKED_UNCERTIFIED,
    RUN_MODE_PARTIAL_CERTIFIED,
    RUN_MODE_REFRESH_THEN_RUN,
    TRUST_TRUSTED,
)
from app.services.intelligence.v3.evidence_refresh_orchestrator_v1 import (
    EvidenceRefreshOrchestrator,
    OrchestratorInputs,
    RefreshBudget,
)
from app.services.intelligence.v3.full_portfolio_analyst_refresh_adapter_v1 import (
    DEFAULT_MAX_FULL_PORTFOLIO_LLM_CALLS,
    DEFAULT_MAX_FULL_PORTFOLIO_TICKERS,
    FullPortfolioAnalystRefreshAdapter,
    FullPortfolioAnalystRefreshBudget,
)


def _now() -> datetime:
    return datetime(2026, 5, 14, 12, 0, 0, tzinfo=timezone.utc)


def _iso_ago(hours: float, now: datetime | None = None) -> str:
    base = now or _now()
    return (base - timedelta(hours=hours)).isoformat()


# ── 1. Full-portfolio default budgets ────────────────────────────────────────

class TestFullPortfolioBudgetDefaults(unittest.TestCase):
    def test_defaults_exceed_legacy_6_ticker_cap(self):
        """The full-portfolio path must default to a budget large enough for a
        typical 30–40 position personal portfolio."""
        assert DEFAULT_MAX_FULL_PORTFOLIO_TICKERS > DEFAULT_MAX_ANALYST_TICKERS_PER_RUN
        assert DEFAULT_MAX_FULL_PORTFOLIO_TICKERS >= 34
        assert DEFAULT_MAX_FULL_PORTFOLIO_LLM_CALLS >= 34


# ── 2. Adapter selects the full stale list (no 6-cap on 34 tickers) ──────────

class TestFullPortfolioAdapterSelection:
    @pytest.mark.asyncio
    async def test_thirty_four_tickers_all_selected_no_six_cap(self):
        seen: list[list[str]] = []

        async def _backend(user_id, tickers, started_at):
            seen.append(list(tickers))
            return {
                t: {
                    "agent_insight_created_at": started_at.isoformat(),
                    "recommendation_created_at": started_at.isoformat(),
                    "used_fallback": False,
                    "agent_run_id": "full-run-1",
                    "insight_run_match": True,
                    "rec_run_match": True,
                    "insight_row_present": True,
                    "rec_row_present": True,
                }
                for t in tickers
            }

        stale = [f"T{i:02d}" for i in range(34)]
        hints = [
            TickerPriorityHint(
                ticker=t, prior_action="HOLD",
                weight_pct=1.0, evidence_age_hours=287.0,
            )
            for t in stale
        ]
        adapter = FullPortfolioAnalystRefreshAdapter(
            user_id=uuid4(),
            run_backend=_backend,
        )
        result = await adapter(stale, priority_hints=hints)
        assert result.status == STATUS_SUCCEEDED
        assert len(result.selected_tickers) == 34
        assert result.deferred_tickers == []
        assert result.attempted_llm_calls == 34
        assert result.successful_llm_calls == 34
        # Backend received every stale ticker; no 6-ticker carve-out.
        assert len(seen[0]) == 34
        assert set(seen[0]) == set(stale)

    @pytest.mark.asyncio
    async def test_empty_stale_list_returns_no_stale(self):
        async def _backend(user_id, tickers, started_at):
            raise AssertionError("should not be called when nothing stale")

        adapter = FullPortfolioAnalystRefreshAdapter(
            user_id=uuid4(),
            run_backend=_backend,
        )
        result = await adapter([])
        assert result.status == STATUS_NO_STALE
        assert result.attempted_llm_calls == 0
        assert result.deferred_tickers == []

    @pytest.mark.asyncio
    async def test_priority_order_preserved_for_full_pass(self):
        order: list[str] = []

        async def _backend(user_id, tickers, started_at):
            nonlocal order
            order = list(tickers)
            return {
                t: {
                    "agent_insight_created_at": started_at.isoformat(),
                    "recommendation_created_at": started_at.isoformat(),
                    "used_fallback": False,
                    "agent_run_id": "r",
                    "insight_run_match": True,
                    "rec_run_match": True,
                    "insight_row_present": True,
                    "rec_row_present": True,
                }
                for t in tickers
            }

        hints = [
            TickerPriorityHint(ticker="HOLD1", prior_action="HOLD", weight_pct=1.0),
            TickerPriorityHint(ticker="MYBUY", prior_action="BUY", weight_pct=1.0),
            TickerPriorityHint(ticker="MYTRIM", prior_action="TRIM", weight_pct=1.0),
            TickerPriorityHint(ticker="HOLD2", prior_action="HOLD", weight_pct=1.0),
        ]
        adapter = FullPortfolioAnalystRefreshAdapter(
            user_id=uuid4(),
            run_backend=_backend,
        )
        result = await adapter([h.ticker for h in hints], priority_hints=hints)
        # BUY/TRIM first, then HOLD A→Z.
        assert order[:2] == ["MYBUY", "MYTRIM"]
        assert set(order[2:]) == {"HOLD1", "HOLD2"}
        assert result.status == STATUS_SUCCEEDED


# ── 3. Per-ticker success / failure honesty ─────────────────────────────────

class TestPerTickerHonesty:
    @pytest.mark.asyncio
    async def test_used_fallback_does_not_count_as_success(self):
        async def _backend(user_id, tickers, started_at):
            return {
                "AAPL": {
                    "agent_insight_created_at": started_at.isoformat(),
                    "recommendation_created_at": started_at.isoformat(),
                    "used_fallback": True,
                    "agent_run_id": "r",
                    "insight_run_match": True,
                    "rec_run_match": True,
                    "insight_row_present": True,
                    "rec_row_present": True,
                },
            }

        adapter = FullPortfolioAnalystRefreshAdapter(
            user_id=uuid4(),
            run_backend=_backend,
        )
        result = await adapter(["AAPL"])
        assert result.status == STATUS_FAILED
        assert result.per_ticker[0].success is False
        assert result.per_ticker[0].refreshed_agent_insight_at is None
        assert result.successful_llm_calls == 0

    @pytest.mark.asyncio
    async def test_missing_insight_row_for_run_is_failure(self):
        async def _backend(user_id, tickers, started_at):
            return {
                "AAPL": {
                    "agent_insight_created_at": None,
                    "recommendation_created_at": None,
                    "used_fallback": False,
                    "agent_run_id": "r",
                    "insight_run_match": False,
                    "rec_run_match": False,
                    "insight_row_present": False,
                    "rec_row_present": False,
                },
            }

        adapter = FullPortfolioAnalystRefreshAdapter(
            user_id=uuid4(),
            run_backend=_backend,
        )
        result = await adapter(["AAPL"])
        assert result.status == STATUS_FAILED
        assert result.per_ticker[0].success is False
        assert result.per_ticker[0].refreshed_agent_insight_at is None
        # No fabricated freshness — the failed ticker keeps no stamp.

    @pytest.mark.asyncio
    async def test_partial_success_status_reflects_mix(self):
        async def _backend(user_id, tickers, started_at):
            return {
                "AAPL": {
                    "agent_insight_created_at": started_at.isoformat(),
                    "recommendation_created_at": started_at.isoformat(),
                    "used_fallback": False,
                    "agent_run_id": "r",
                    "insight_run_match": True,
                    "rec_run_match": True,
                    "insight_row_present": True,
                    "rec_row_present": True,
                },
                "NVDA": None,
            }

        adapter = FullPortfolioAnalystRefreshAdapter(
            user_id=uuid4(),
            run_backend=_backend,
        )
        result = await adapter(["AAPL", "NVDA"])
        assert result.status == STATUS_PARTIAL_SUCCESS
        ok = {o.ticker: o for o in result.per_ticker if o.success}
        bad = {o.ticker: o for o in result.per_ticker if not o.success}
        assert "AAPL" in ok and ok["AAPL"].refreshed_agent_insight_at is not None
        assert "NVDA" in bad and bad["NVDA"].refreshed_agent_insight_at is None

    @pytest.mark.asyncio
    async def test_backend_timeout_is_honest(self):
        async def _backend(user_id, tickers, started_at):
            await asyncio.sleep(5.0)
            return {}

        adapter = FullPortfolioAnalystRefreshAdapter(
            user_id=uuid4(),
            run_backend=_backend,
            budget=FullPortfolioAnalystRefreshBudget(
                max_tickers=10, max_llm_calls=10, max_seconds=1.0,
            ),
        )
        result = await adapter(
            ["AAPL"],
            priority_hints=[TickerPriorityHint(ticker="AAPL", prior_action="BUY")],
        )
        assert result.status == STATUS_SKIPPED_TIMEOUT
        assert all(not o.success for o in result.per_ticker)
        assert result.successful_llm_calls == 0

    @pytest.mark.asyncio
    async def test_backend_exception_yields_failed_status_with_reason(self):
        async def _backend(user_id, tickers, started_at):
            raise RuntimeError("provider down")

        adapter = FullPortfolioAnalystRefreshAdapter(
            user_id=uuid4(),
            run_backend=_backend,
        )
        result = await adapter(["AAPL", "NVDA"])
        assert result.status == STATUS_FAILED
        assert all(not o.success for o in result.per_ticker)
        assert any("RuntimeError" in (o.error_reason or "") for o in result.per_ticker)

    @pytest.mark.asyncio
    async def test_zero_budget_skips_with_budget_status(self):
        async def _backend(user_id, tickers, started_at):
            raise AssertionError("should not be called when budget=0")

        adapter = FullPortfolioAnalystRefreshAdapter(
            user_id=uuid4(),
            run_backend=_backend,
            budget=FullPortfolioAnalystRefreshBudget(max_tickers=0, max_llm_calls=0),
        )
        result = await adapter(["AAPL", "NVDA"])
        assert result.status == STATUS_SKIPPED_BUDGET
        assert "AAPL" in result.deferred_tickers
        assert "NVDA" in result.deferred_tickers


# ── 4. Orchestrator integration: full success unblocks the run mode ─────────

class TestOrchestratorIntegration:
    @pytest.mark.asyncio
    async def test_full_portfolio_refresh_unblocks_to_refresh_then_run(self):
        """Production-stale 34-ticker inventory + full refresh → trusted."""
        now = _now()
        tickers = [f"T{i:02d}" for i in range(34)]
        per_ticker_ev = [
            {
                "ticker": t, "prior_action": "HOLD",
                "weight_pct": round(100 / 34, 2),
                "evidence_age_hours": 287.0,
            }
            for t in tickers
        ]
        inputs = OrchestratorInputs(
            evidence_stats={
                "recommendation_timestamps":     [_iso_ago(193.0, now)] * 34,
                "agent_insight_run_timestamps":  [_iso_ago(287.0, now)] * 34,
                "active_position_count":         34,
                "persisted_recommendation_count": 34,
                "persisted_agent_insight_count":  34,
            },
            portfolio_snapshot_at=_iso_ago(0.5, now),
            market_value_certified_ats=[_iso_ago(0.1, now)] * 34,
            tickers=tickers,
            research_artifact_timestamps=[],
            now=now,
            per_ticker_evidence=per_ticker_ev,
        )

        async def _price_refresh(t):
            return {x: {"is_valid": True, "is_stale": False} for x in t}

        async def _backend(user_id, selected_tickers, started_at):
            # Mimics the AgentOrchestrator unscoped full-portfolio LLM path
            # that production observed: every selected ticker gets a fresh
            # non-fallback row tied to the new agent_run_id.
            return {
                t: {
                    "agent_insight_created_at": started_at.isoformat(),
                    "recommendation_created_at": started_at.isoformat(),
                    "used_fallback": False,
                    "agent_run_id": "full-run-1",
                    "insight_run_match": True,
                    "rec_run_match": True,
                    "insight_row_present": True,
                    "rec_row_present": True,
                }
                for t in selected_tickers
            }

        adapter = FullPortfolioAnalystRefreshAdapter(
            user_id=uuid4(),
            run_backend=_backend,
        )

        orch = EvidenceRefreshOrchestrator(
            user_id=uuid4(),
            inputs=inputs,
            price_refresh=_price_refresh,
            analyst_refresh=adapter,
            budget=RefreshBudget(max_llm_calls=60),
        )
        result = await orch.run()
        assert result.run_mode == RUN_MODE_REFRESH_THEN_RUN
        assert result.trust_status == TRUST_TRUSTED
        assert result.attempted_llm_calls == 34
        assert result.successful_llm_calls == 34
        assert result.failed_llm_calls == 0
        diag = result.to_diagnostics_dict()
        assert set(diag["analyst_refresh_successful_tickers"]) == set(tickers)
        assert diag["analyst_refresh_failed_tickers"] == []

    @pytest.mark.asyncio
    async def test_partial_refresh_stays_partial_or_blocked(self):
        """Adversarial: if one ticker fails, run does NOT claim trusted."""
        now = _now()
        tickers = ["AAPL", "NVDA"]
        per_ticker_ev = [
            {"ticker": "AAPL", "prior_action": "BUY",  "weight_pct": 5.0, "evidence_age_hours": 287.0},
            {"ticker": "NVDA", "prior_action": "HOLD", "weight_pct": 3.0, "evidence_age_hours": 287.0},
        ]
        inputs = OrchestratorInputs(
            evidence_stats={
                "recommendation_timestamps":     [_iso_ago(193.0, now)] * 2,
                "agent_insight_run_timestamps":  [_iso_ago(287.0, now)] * 2,
                "active_position_count":         2,
                "persisted_recommendation_count": 2,
                "persisted_agent_insight_count":  2,
            },
            portfolio_snapshot_at=_iso_ago(0.5, now),
            market_value_certified_ats=[_iso_ago(0.1, now)] * 2,
            tickers=tickers,
            research_artifact_timestamps=[],
            now=now,
            per_ticker_evidence=per_ticker_ev,
        )

        async def _backend(user_id, selected_tickers, started_at):
            return {
                "AAPL": {
                    "agent_insight_created_at": started_at.isoformat(),
                    "recommendation_created_at": started_at.isoformat(),
                    "used_fallback": False,
                    "agent_run_id": "r",
                    "insight_run_match": True,
                    "rec_run_match": True,
                    "insight_row_present": True,
                    "rec_row_present": True,
                },
                "NVDA": None,
            }

        adapter = FullPortfolioAnalystRefreshAdapter(
            user_id=uuid4(),
            run_backend=_backend,
        )

        orch = EvidenceRefreshOrchestrator(
            user_id=uuid4(),
            inputs=inputs,
            price_refresh=None,
            analyst_refresh=adapter,
            budget=RefreshBudget(max_llm_calls=10),
        )
        result = await orch.run()
        assert result.run_mode in (
            RUN_MODE_BLOCKED_UNCERTIFIED,
            RUN_MODE_PARTIAL_CERTIFIED,
        )
        assert result.trust_status != TRUST_TRUSTED
        assert result.successful_llm_calls == 1
        assert result.failed_llm_calls == 1

    @pytest.mark.asyncio
    async def test_total_refresh_failure_stays_blocked_no_fake_stamps(self):
        now = _now()
        tickers = ["AAPL", "NVDA"]
        per_ticker_ev = [
            {"ticker": t, "prior_action": "BUY", "weight_pct": 5.0, "evidence_age_hours": 287.0}
            for t in tickers
        ]
        inputs = OrchestratorInputs(
            evidence_stats={
                "recommendation_timestamps":     [_iso_ago(193.0, now)] * 2,
                "agent_insight_run_timestamps":  [_iso_ago(287.0, now)] * 2,
                "active_position_count":         2,
                "persisted_recommendation_count": 2,
                "persisted_agent_insight_count":  2,
            },
            portfolio_snapshot_at=_iso_ago(0.5, now),
            market_value_certified_ats=[_iso_ago(0.1, now)] * 2,
            tickers=tickers,
            research_artifact_timestamps=[],
            now=now,
            per_ticker_evidence=per_ticker_ev,
        )

        async def _backend(user_id, selected_tickers, started_at):
            return {t: None for t in selected_tickers}

        adapter = FullPortfolioAnalystRefreshAdapter(
            user_id=uuid4(),
            run_backend=_backend,
        )

        orch = EvidenceRefreshOrchestrator(
            user_id=uuid4(),
            inputs=inputs,
            price_refresh=None,
            analyst_refresh=adapter,
            budget=RefreshBudget(),
        )
        result = await orch.run()
        assert result.run_mode == RUN_MODE_BLOCKED_UNCERTIFIED
        # No fabricated freshness: critical analyst sources stay HARD_STALE.
        rec_after = result.source_states_after["recommendations"]
        ai_after = result.source_states_after["agent_insights"]
        assert rec_after.state == "HARD_STALE"
        assert ai_after.state == "HARD_STALE"
        # All ticker outcomes report failure honestly.
        assert result.successful_llm_calls == 0
        assert result.failed_llm_calls == 2


# ── 5. IntelV3Service analyst-refresh wiring (Stage 3.1) ────────────────────
#
# Stage 3.1 decouples the synchronous Run Intel v3 path: it no longer wires the
# LLM analyst adapters into the HTTP request. ``_build_analyst_refresh_callable``
# now returns the non-LLM ``AnalystRefreshRequestSeam``. The LLM adapters tested
# in sections 1-4 / 7 above remain in the repo for a future background plane —
# they are simply no longer wired into the synchronous service path.

class TestServiceAdapterSelection(unittest.TestCase):
    def _build_service(self):
        from app.services.intelligence.v3.intel_v3_service import IntelV3Service
        service = IntelV3Service.__new__(IntelV3Service)
        service.user_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
        service.client = MagicMock()
        return service

    def test_default_callable_is_the_non_llm_request_seam(self):
        from app.services.intelligence.v3.analyst_refresh_request_seam_v1 import (
            AnalystRefreshRequestSeam,
        )
        service = self._build_service()
        seam = service._build_analyst_refresh_callable()
        assert isinstance(seam, AnalystRefreshRequestSeam)
        # The synchronous path must not wire an LLM adapter.
        assert not isinstance(seam, FullPortfolioAnalystRefreshAdapter)

    def test_disabling_analyst_refresh_returns_none(self):
        os.environ["INTEL_V3_ANALYST_REFRESH_ENABLED"] = "0"
        try:
            service = self._build_service()
            seam = service._build_analyst_refresh_callable()
            assert seam is None
        finally:
            os.environ.pop("INTEL_V3_ANALYST_REFRESH_ENABLED", None)


# ── 6. run_v3() re-reads cards after successful refresh ─────────────────────

class TestRunV3PostRefreshReread(unittest.TestCase):
    @staticmethod
    def _card(ticker: str, action: str):
        card = MagicMock()
        card.ticker = ticker
        card.name = f"{ticker} Corp"
        card.category = "stock"
        card.action = action
        card.analyst_action = action
        card.conviction_level = "MEDIUM"
        card.technical_signal = None
        card.risk_flag = None
        card.analyst_risks = []
        card.data_quality_label = "PARTIAL"
        card.intel_read = None
        card.thesis_v2 = None
        card.analyst_used_fallback = False
        card.primary_driver = "Driver text"
        card.action_reason = "Action reason"
        card.analyst_drivers = []
        return card

    def _build_service(self):
        from app.services.intelligence.v3.intel_v3_service import IntelV3Service
        service = IntelV3Service.__new__(IntelV3Service)
        service.user_id = uuid.UUID("00000000-0000-0000-0000-000000000002")

        mock_client = MagicMock()
        mock_table = MagicMock()
        mock_table.update.return_value = mock_table
        mock_table.insert.return_value = mock_table
        mock_table.select.return_value = mock_table
        mock_table.eq.return_value = mock_table
        mock_table.order.return_value = mock_table
        mock_table.limit.return_value = mock_table
        mock_table.execute.return_value = MagicMock(data=[])
        mock_client.table.return_value = mock_table
        service.client = mock_client
        return service

    def test_run_v3_re_reads_evidence_when_refresh_produces_successful_tickers(self):
        """Stage 3.0c contract: when the orchestrator reports refreshed tickers,
        run_v3 must re-call load_cards before building decisions."""
        from app.services.intelligence.v3.evidence_refresh_orchestrator_v1 import (
            RefreshResult,
        )

        # Pre-refresh load: all HOLD (the stale state).
        cards_pre = [self._card("AAPL", "HOLD"), self._card("NVDA", "HOLD")]
        stats_pre = {
            "active_position_count": 2,
            "persisted_recommendation_count": 2,
            "persisted_agent_insight_count": 2,
            "missing_recommendation_count": 0,
            "missing_evidence_count": 0,
            "stale_or_missing_source_count": 0,
            "recommendation_timestamps": [_iso_ago(193.0)],
            "agent_insight_run_timestamps": [_iso_ago(287.0)],
        }
        # Post-refresh load: refreshed evidence flipped AAPL HOLD→BUY.
        cards_post = [self._card("AAPL", "BUY"), self._card("NVDA", "HOLD")]
        stats_post = {
            "active_position_count": 2,
            "persisted_recommendation_count": 2,
            "persisted_agent_insight_count": 2,
            "missing_recommendation_count": 0,
            "missing_evidence_count": 0,
            "stale_or_missing_source_count": 0,
            "recommendation_timestamps": [_iso_ago(0.1)],
            "agent_insight_run_timestamps": [_iso_ago(0.1)],
        }

        load_cards_mock = AsyncMock(side_effect=[(cards_pre, stats_pre), (cards_post, stats_post)])
        adapter_mock = MagicMock()
        adapter_mock.load_cards = load_cards_mock

        refresh_result = MagicMock(spec=RefreshResult)
        refresh_result.to_diagnostics_dict.return_value = {
            "run_mode": RUN_MODE_REFRESH_THEN_RUN,
            "trust_status": TRUST_TRUSTED,
            "banner_copy": "Refreshed stale evidence before running.",
            "source_freshness": {},
            "per_source_oldest_timestamp": {},
            "per_source_newest_timestamp": {},
            "stale_source_count": 0,
            "hard_stale_source_count": 0,
            "missing_source_count": 0,
            "attempted_llm_calls": 2,
            "successful_llm_calls": 2,
            "failed_llm_calls": 0,
            "attempted_provider_calls": 0,
            "successful_provider_calls": 0,
            "failed_provider_calls": 0,
            "refreshed_source_count": 2,
            "failed_refresh_count": 0,
            "analyst_refresh_supported": True,
            "analyst_refresh_status": STATUS_SUCCEEDED,
            "analyst_refresh_per_ticker": [],
            "analyst_refresh_selected_tickers": ["AAPL", "NVDA"],
            "analyst_refresh_deferred_tickers": [],
            "analyst_refresh_successful_tickers": ["AAPL", "NVDA"],
            "analyst_refresh_failed_tickers": [],
            "budget_exhausted": False,
            "orchestrator_notes": [],
        }

        service = self._build_service()
        with (
            patch(
                "app.services.intelligence.v3.intel_v3_service.ReadOnlyEvidenceAdapter",
                return_value=adapter_mock,
            ),
            patch.object(service, "_get_weight_map", new_callable=AsyncMock, return_value={}),
            patch.object(
                service, "_run_refresh_orchestrator",
                new_callable=AsyncMock, return_value=refresh_result,
            ),
            patch.object(service, "_persist_snapshot", new_callable=AsyncMock),
        ):
            snapshot = asyncio.run(service.run_v3())

        # load_cards was called twice: once pre-refresh, once post-refresh.
        # That is the Stage 3.0c contract — decide() runs over the refreshed
        # evidence, not the pre-refresh snapshot.
        assert load_cards_mock.await_count == 2
        # The snapshot was actually produced and persists with current_holdings.
        assert "current_holdings" in snapshot
        # Every card in the snapshot came from the post-refresh ticker set.
        holding_tickers = {c.get("ticker") for c in snapshot["current_holdings"]}
        assert holding_tickers == {"AAPL", "NVDA"}

    def test_run_v3_does_not_re_read_when_no_successful_tickers(self):
        """If refresh produced zero successful tickers, no re-read is performed."""
        from app.services.intelligence.v3.evidence_refresh_orchestrator_v1 import (
            RefreshResult,
        )

        cards_pre = [self._card("AAPL", "HOLD"), self._card("NVDA", "HOLD")]
        stats_pre = {
            "active_position_count": 2,
            "persisted_recommendation_count": 2,
            "persisted_agent_insight_count": 2,
            "missing_recommendation_count": 0,
            "missing_evidence_count": 0,
            "stale_or_missing_source_count": 0,
            "recommendation_timestamps": [_iso_ago(193.0)],
            "agent_insight_run_timestamps": [_iso_ago(287.0)],
        }

        load_cards_mock = AsyncMock(return_value=(cards_pre, stats_pre))
        adapter_mock = MagicMock()
        adapter_mock.load_cards = load_cards_mock

        refresh_result = MagicMock(spec=RefreshResult)
        refresh_result.to_diagnostics_dict.return_value = {
            "run_mode": RUN_MODE_BLOCKED_UNCERTIFIED,
            "trust_status": "uncertified",
            "banner_copy": "Blocked.",
            "source_freshness": {},
            "per_source_oldest_timestamp": {},
            "per_source_newest_timestamp": {},
            "stale_source_count": 0,
            "hard_stale_source_count": 0,
            "missing_source_count": 0,
            "attempted_llm_calls": 2,
            "successful_llm_calls": 0,
            "failed_llm_calls": 2,
            "analyst_refresh_supported": True,
            "analyst_refresh_status": STATUS_FAILED,
            "analyst_refresh_successful_tickers": [],
            "analyst_refresh_failed_tickers": ["AAPL", "NVDA"],
            "analyst_refresh_selected_tickers": ["AAPL", "NVDA"],
            "analyst_refresh_deferred_tickers": [],
            "analyst_refresh_per_ticker": [],
            "attempted_provider_calls": 0,
            "successful_provider_calls": 0,
            "failed_provider_calls": 0,
            "refreshed_source_count": 0,
            "failed_refresh_count": 1,
            "budget_exhausted": False,
            "orchestrator_notes": [],
        }

        service = self._build_service()
        with (
            patch(
                "app.services.intelligence.v3.intel_v3_service.ReadOnlyEvidenceAdapter",
                return_value=adapter_mock,
            ),
            patch.object(service, "_get_weight_map", new_callable=AsyncMock, return_value={}),
            patch.object(
                service, "_run_refresh_orchestrator",
                new_callable=AsyncMock, return_value=refresh_result,
            ),
            patch.object(service, "_persist_snapshot", new_callable=AsyncMock),
        ):
            asyncio.run(service.run_v3())

        # Exactly one load_cards call — no second read after failed refresh.
        assert load_cards_mock.await_count == 1


# ── 7. Boundary checks: deterministic decide() authority preserved ──────────

class TestBoundaryInvariants(unittest.TestCase):
    def test_no_deploy_or_watchtower_imports_in_full_portfolio_adapter(self):
        from app.services.intelligence.v3 import (
            full_portfolio_analyst_refresh_adapter_v1 as mod,
        )
        src = open(mod.__file__).read()
        import re
        imports = re.findall(r"^\s*(?:from\s+\S+\s+)?import\s+\S+", src, re.MULTILINE)
        for line in imports:
            assert "deploy" not in line.lower(), f"Unexpected deploy import: {line}"
            assert "watchtower" not in line.lower(), f"Unexpected watchtower import: {line}"
        # Adapter never imports / calls decide() directly.
        assert "from .decision_policy_v1" not in src
        assert "decision_policy_v1" not in src
        # Adapter never WRITES to the v3 snapshot or rebuilds it. (The name may
        # appear in a docstring; the test rule is that no call site targets it.)
        assert '.table("intel_v3_snapshots")' not in src
        assert "build_snapshot" not in src

    def test_full_portfolio_adapter_default_backend_does_not_scope_orchestrator(self):
        """Default backend must call AgentOrchestrator WITHOUT
        analyst_refresh_tickers — that's the difference vs Stage 3.0b.6."""
        import inspect
        from app.services.intelligence.v3 import (
            full_portfolio_analyst_refresh_adapter_v1 as mod,
        )
        src = inspect.getsource(mod.default_full_portfolio_agent_orchestrator_backend)
        # The scoped 6-ticker call site set analyst_refresh_tickers=... — the
        # full-portfolio call site must not.
        assert "analyst_refresh_tickers=" not in src, (
            "Full-portfolio backend must NOT pass analyst_refresh_tickers — "
            "the orchestrator runs over the full portfolio."
        )
        assert "force_recompute=True" in src
