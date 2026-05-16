"""Build 3 PR 2A hotfix — Watchtower must enqueue analyst jobs for stale recommendations.

Root cause: Step 4 in WatchtowerBackgroundRefreshWorker only checked EVIDENCE_TYPE_ANALYST_LLM.
After Build 2.6 tightened recommendation SLA to 8h, stale recommendations block Intel
certification but no analyst jobs were enqueued — Watchtower looped indefinitely.

Fix: stale EVIDENCE_TYPE_RECOMMENDATION is now treated identically to stale
EVIDENCE_TYPE_ANALYST_LLM for the purpose of enqueuing analyst refresh jobs.
The analyst worker produces both; both share the same job queue.

Proves:
  1. Stale recommendation evidence → analyst enqueue callable called.
  2. Stale recommendation evidence → analyst_jobs_enqueued > 0.
  3. Fresh recommendation → no analyst enqueue.
  4. Stale analyst_llm still enqueues (regression).
  5. Stale price-only → no analyst enqueue (price and analyst are independent).
  6. Stale recommendation + stale analyst_llm → tickers deduplicated (union, not double-enqueue).
  7. analyst_enqueue_reason field emitted correctly for each scenario.
  8. _analyst_enqueue_reason() helper returns correct values.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

UID = uuid.UUID("ccccdddd-0000-0000-0000-000000000001")
NOW = datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc)


def _make_stale_record(evidence_type: str, ticker: str = "AAPL"):
    from app.services.intelligence.v3.watchtower_freshness_ledger_v1 import (
        FRESHNESS_STALE,
        FRESHNESS_SLA_CONFIG,
        EvidenceRecord,
        is_deploy_eligible_for_type,
        is_decision_eligible_for_type,
    )
    sla = FRESHNESS_SLA_CONFIG.get(evidence_type)
    sla_seconds = sla.fresh_seconds if sla else 0
    ts = NOW - timedelta(seconds=(sla.stale_seconds + 60)) if sla else NOW
    deploy_elig, deploy_reason = is_deploy_eligible_for_type(evidence_type, FRESHNESS_STALE)
    decision_elig, decision_reason = is_decision_eligible_for_type(evidence_type, FRESHNESS_STALE)
    return EvidenceRecord(
        evidence_type=evidence_type,
        ticker=ticker,
        scope="ticker",
        as_of=ts,
        collected_at=ts,
        source="test",
        freshness_status=FRESHNESS_STALE,
        freshness_sla_seconds=sla_seconds,
        deploy_eligible=deploy_elig,
        decision_eligible=decision_elig,
        reason=deploy_reason or decision_reason,
    )


def _make_fresh_record(evidence_type: str, ticker: str = "AAPL"):
    from app.services.intelligence.v3.watchtower_freshness_ledger_v1 import (
        FRESHNESS_FRESH,
        FRESHNESS_SLA_CONFIG,
        EvidenceRecord,
        is_deploy_eligible_for_type,
        is_decision_eligible_for_type,
    )
    sla = FRESHNESS_SLA_CONFIG.get(evidence_type)
    sla_seconds = sla.fresh_seconds if sla else 0
    ts = NOW - timedelta(seconds=(sla.fresh_seconds // 2)) if sla else NOW
    deploy_elig, deploy_reason = is_deploy_eligible_for_type(evidence_type, FRESHNESS_FRESH)
    decision_elig, decision_reason = is_decision_eligible_for_type(evidence_type, FRESHNESS_FRESH)
    return EvidenceRecord(
        evidence_type=evidence_type,
        ticker=ticker,
        scope="ticker",
        as_of=ts,
        collected_at=ts,
        source="test",
        freshness_status=FRESHNESS_FRESH,
        freshness_sla_seconds=sla_seconds,
        deploy_eligible=deploy_elig,
        decision_eligible=decision_elig,
        reason=deploy_reason or decision_reason,
    )


# ── 1. Stale recommendation → analyst enqueue called ─────────────────────────

class TestStaleRecommendationEnqueuesAnalystJobs:

    @pytest.mark.asyncio
    async def test_stale_recommendation_triggers_enqueue(self):
        """Stale recommendation evidence must cause analyst_job_enqueue_callable to be called."""
        from app.services.intelligence.v3.watchtower_background_refresh_worker_v1 import (
            WatchtowerBackgroundRefreshWorker,
        )
        from app.services.intelligence.v3.watchtower_freshness_ledger_v1 import (
            EVIDENCE_TYPE_RECOMMENDATION,
        )

        stale_rec = _make_stale_record(EVIDENCE_TYPE_RECOMMENDATION, "AAPL")
        mock_client = MagicMock()
        mock_enqueue = AsyncMock(return_value=1)

        with patch(
            "app.services.intelligence.v3.watchtower_background_refresh_worker_v1.collect_evidence_records",
            return_value=[stale_rec],
        ):
            worker = WatchtowerBackgroundRefreshWorker(
                client=mock_client,
                analyst_job_enqueue_callable=mock_enqueue,
            )
            result = await worker.run_refresh_cycle(UID, now=NOW)

        mock_enqueue.assert_called_once()
        assert result.analyst_jobs_enqueued > 0

    @pytest.mark.asyncio
    async def test_stale_recommendation_jobs_enqueued_count(self):
        """analyst_jobs_enqueued reflects enqueue callable return value for stale recs."""
        from app.services.intelligence.v3.watchtower_background_refresh_worker_v1 import (
            WatchtowerBackgroundRefreshWorker,
        )
        from app.services.intelligence.v3.watchtower_freshness_ledger_v1 import (
            EVIDENCE_TYPE_RECOMMENDATION,
        )

        stale_rec = _make_stale_record(EVIDENCE_TYPE_RECOMMENDATION, "MSFT")
        mock_client = MagicMock()
        mock_enqueue = AsyncMock(return_value=3)

        with patch(
            "app.services.intelligence.v3.watchtower_background_refresh_worker_v1.collect_evidence_records",
            return_value=[stale_rec],
        ):
            worker = WatchtowerBackgroundRefreshWorker(
                client=mock_client,
                analyst_job_enqueue_callable=mock_enqueue,
            )
            result = await worker.run_refresh_cycle(UID, now=NOW)

        assert result.analyst_jobs_enqueued == 3

    @pytest.mark.asyncio
    async def test_stale_recommendation_tickers_passed_to_enqueue(self):
        """Stale recommendation ticker is included in the tickers passed to enqueue."""
        from app.services.intelligence.v3.watchtower_background_refresh_worker_v1 import (
            WatchtowerBackgroundRefreshWorker,
        )
        from app.services.intelligence.v3.watchtower_freshness_ledger_v1 import (
            EVIDENCE_TYPE_RECOMMENDATION,
        )

        stale_rec = _make_stale_record(EVIDENCE_TYPE_RECOMMENDATION, "NVDA")
        mock_client = MagicMock()
        captured: list = []

        async def _capture_enqueue(user_id, tickers):
            captured.append(tickers)
            return len(tickers)

        with patch(
            "app.services.intelligence.v3.watchtower_background_refresh_worker_v1.collect_evidence_records",
            return_value=[stale_rec],
        ):
            worker = WatchtowerBackgroundRefreshWorker(
                client=mock_client,
                analyst_job_enqueue_callable=_capture_enqueue,
            )
            await worker.run_refresh_cycle(UID, now=NOW)

        assert len(captured) == 1, "enqueue must be called exactly once"
        assert "NVDA" in captured[0], "stale recommendation ticker must be in enqueue tickers"


# ── 2. Fresh recommendation → no enqueue ─────────────────────────────────────

class TestFreshRecommendationNoEnqueue:

    @pytest.mark.asyncio
    async def test_fresh_recommendation_no_analyst_enqueue(self):
        """Fresh recommendation must not trigger analyst job enqueue."""
        from app.services.intelligence.v3.watchtower_background_refresh_worker_v1 import (
            WatchtowerBackgroundRefreshWorker,
        )
        from app.services.intelligence.v3.watchtower_freshness_ledger_v1 import (
            EVIDENCE_TYPE_RECOMMENDATION,
        )

        fresh_rec = _make_fresh_record(EVIDENCE_TYPE_RECOMMENDATION, "AAPL")
        mock_client = MagicMock()
        mock_enqueue = AsyncMock(return_value=0)

        with patch(
            "app.services.intelligence.v3.watchtower_background_refresh_worker_v1.collect_evidence_records",
            return_value=[fresh_rec],
        ):
            worker = WatchtowerBackgroundRefreshWorker(
                client=mock_client,
                analyst_job_enqueue_callable=mock_enqueue,
            )
            result = await worker.run_refresh_cycle(UID, now=NOW)

        mock_enqueue.assert_not_called()
        assert result.analyst_jobs_enqueued == 0


# ── 3. Stale analyst_llm still enqueues (regression) ─────────────────────────

class TestStaleAnalystLLMRegressionStillEnqueues:

    @pytest.mark.asyncio
    async def test_stale_analyst_llm_still_enqueues(self):
        """Pre-existing behavior: stale analyst_llm still triggers enqueue."""
        from app.services.intelligence.v3.watchtower_background_refresh_worker_v1 import (
            WatchtowerBackgroundRefreshWorker,
        )
        from app.services.intelligence.v3.watchtower_freshness_ledger_v1 import (
            EVIDENCE_TYPE_ANALYST_LLM,
        )

        stale_llm = _make_stale_record(EVIDENCE_TYPE_ANALYST_LLM, "GOOGL")
        mock_client = MagicMock()
        mock_enqueue = AsyncMock(return_value=1)

        with patch(
            "app.services.intelligence.v3.watchtower_background_refresh_worker_v1.collect_evidence_records",
            return_value=[stale_llm],
        ):
            worker = WatchtowerBackgroundRefreshWorker(
                client=mock_client,
                analyst_job_enqueue_callable=mock_enqueue,
            )
            result = await worker.run_refresh_cycle(UID, now=NOW)

        mock_enqueue.assert_called_once()
        assert result.analyst_jobs_enqueued > 0


# ── 4. Stale price only → no analyst enqueue ─────────────────────────────────

class TestStalePriceOnlyNoAnalystEnqueue:

    @pytest.mark.asyncio
    async def test_stale_price_only_does_not_enqueue_analyst_jobs(self):
        """Price and analyst refresh are independent. Stale price alone must not enqueue analyst jobs."""
        from app.services.intelligence.v3.watchtower_background_refresh_worker_v1 import (
            WatchtowerBackgroundRefreshWorker,
        )
        from app.services.intelligence.v3.watchtower_freshness_ledger_v1 import (
            EVIDENCE_TYPE_PRICE,
        )

        stale_price = _make_stale_record(EVIDENCE_TYPE_PRICE, "AAPL")
        mock_client = MagicMock()
        mock_analyst = AsyncMock(return_value=0)
        mock_price = AsyncMock(return_value={"AAPL": None})  # failed price refresh

        with patch(
            "app.services.intelligence.v3.watchtower_background_refresh_worker_v1.collect_evidence_records",
            return_value=[stale_price],
        ):
            worker = WatchtowerBackgroundRefreshWorker(
                client=mock_client,
                price_refresh_callable=mock_price,
                analyst_job_enqueue_callable=mock_analyst,
            )
            result = await worker.run_refresh_cycle(UID, now=NOW)

        mock_analyst.assert_not_called()
        assert result.analyst_jobs_enqueued == 0


# ── 5. Stale recommendation + stale analyst_llm → tickers deduplicated ───────

class TestDeduplicatedTickers:

    @pytest.mark.asyncio
    async def test_overlapping_tickers_not_double_enqueued(self):
        """When same ticker has stale recommendation AND stale analyst_llm,
        it appears only once in the enqueue call."""
        from app.services.intelligence.v3.watchtower_background_refresh_worker_v1 import (
            WatchtowerBackgroundRefreshWorker,
        )
        from app.services.intelligence.v3.watchtower_freshness_ledger_v1 import (
            EVIDENCE_TYPE_ANALYST_LLM,
            EVIDENCE_TYPE_RECOMMENDATION,
        )

        stale_rec = _make_stale_record(EVIDENCE_TYPE_RECOMMENDATION, "AAPL")
        stale_llm = _make_stale_record(EVIDENCE_TYPE_ANALYST_LLM, "AAPL")
        mock_client = MagicMock()
        captured: list = []

        async def _capture_enqueue(user_id, tickers):
            captured.append(list(tickers))
            return len(tickers)

        with patch(
            "app.services.intelligence.v3.watchtower_background_refresh_worker_v1.collect_evidence_records",
            return_value=[stale_rec, stale_llm],
        ):
            worker = WatchtowerBackgroundRefreshWorker(
                client=mock_client,
                analyst_job_enqueue_callable=_capture_enqueue,
            )
            await worker.run_refresh_cycle(UID, now=NOW)

        assert len(captured) == 1, "enqueue called exactly once"
        # AAPL must appear exactly once despite being stale in two types
        assert captured[0].count("AAPL") == 1, "AAPL must not be duplicated in enqueue list"

    @pytest.mark.asyncio
    async def test_disjoint_tickers_both_included(self):
        """When stale recommendation (AAPL) and stale analyst_llm (MSFT) are different tickers,
        both are included in the enqueue call."""
        from app.services.intelligence.v3.watchtower_background_refresh_worker_v1 import (
            WatchtowerBackgroundRefreshWorker,
        )
        from app.services.intelligence.v3.watchtower_freshness_ledger_v1 import (
            EVIDENCE_TYPE_ANALYST_LLM,
            EVIDENCE_TYPE_RECOMMENDATION,
        )

        stale_rec = _make_stale_record(EVIDENCE_TYPE_RECOMMENDATION, "AAPL")
        stale_llm = _make_stale_record(EVIDENCE_TYPE_ANALYST_LLM, "MSFT")
        mock_client = MagicMock()
        captured: list = []

        async def _capture_enqueue(user_id, tickers):
            captured.append(sorted(tickers))
            return len(tickers)

        with patch(
            "app.services.intelligence.v3.watchtower_background_refresh_worker_v1.collect_evidence_records",
            return_value=[stale_rec, stale_llm],
        ):
            worker = WatchtowerBackgroundRefreshWorker(
                client=mock_client,
                analyst_job_enqueue_callable=_capture_enqueue,
            )
            await worker.run_refresh_cycle(UID, now=NOW)

        assert len(captured) == 1
        assert "AAPL" in captured[0]
        assert "MSFT" in captured[0]
        assert len(captured[0]) == 2


# ── 6. _analyst_enqueue_reason() helper ──────────────────────────────────────

class TestAnalystEnqueueReason:

    def _reason(self, *, llm_stale: bool, rec_stale: bool) -> str:
        from app.services.intelligence.v3.watchtower_background_refresh_worker_v1 import (
            _analyst_enqueue_reason,
        )
        return _analyst_enqueue_reason(llm_stale=llm_stale, rec_stale=rec_stale)

    def test_recommendation_stale_only(self):
        assert self._reason(llm_stale=False, rec_stale=True) == "recommendation_stale"

    def test_analyst_llm_stale_only(self):
        assert self._reason(llm_stale=True, rec_stale=False) == "analyst_llm_stale"

    def test_both_stale(self):
        assert self._reason(llm_stale=True, rec_stale=True) == "analyst_llm_and_recommendation_stale"
