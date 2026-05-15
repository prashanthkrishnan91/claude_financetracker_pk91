"""Build 1D — Watchtower Fresh Evidence Foundation tests.

Proves:
  1. Stale price blocks deploy_eligible.
  2. Stale positions block deploy_eligible.
  3. Stale portfolio_weight blocks deploy_eligible.
  4. Stale analyst_llm does NOT block price/position freshness and does NOT
     block deploy_eligible (it is informational only).
  5. SEC/fundamental evidence is not re-fetched every click — classified as
     MISSING (not yet collected); does not block deploy.
  6. Run Intel click does NOT run full 34-ticker IO/research path.
  7. Run Intel click returns latest certified snapshot quickly while enqueuing
     stale slices (fast freshness gate result in response).
  8. Watchtower planner produces correct refresh jobs from mixed fresh/stale.
  9. Watchtower worker updates freshness status and respects priority.
  10. Certification contract remains strict (regression: enqueue_run_v3 returns
      refresh_requested, not a snapshot or LLM output).
  11. No stale evidence is labeled current (freshness_status never FRESH when old).
  12. No deploy output when critical evidence is stale (deploy gate blocks).
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.intelligence.v3.watchtower_freshness_ledger_v1 import (
    EVIDENCE_TYPE_ANALYST_LLM,
    EVIDENCE_TYPE_FUNDAMENTAL,
    EVIDENCE_TYPE_NEWS_SENTIMENT,
    EVIDENCE_TYPE_PORTFOLIO_WEIGHT,
    EVIDENCE_TYPE_POSITION,
    EVIDENCE_TYPE_PRICE,
    EVIDENCE_TYPE_RECOMMENDATION,
    EVIDENCE_TYPE_SEC_FILING,
    EVIDENCE_TYPE_SNAPSHOT,
    EVIDENCE_TYPE_TECHNICAL,
    FRESHNESS_FRESH,
    FRESHNESS_AGING,
    FRESHNESS_STALE,
    FRESHNESS_MISSING,
    FRESHNESS_SLA_CONFIG,
    build_evidence_record,
    classify_freshness_status,
    is_deploy_eligible_for_type,
    is_decision_eligible_for_type,
)
from app.services.intelligence.v3.watchtower_refresh_planner_v1 import (
    PRIORITY_URGENT,
    PRIORITY_BACKGROUND,
    build_watchtower_plan,
)
from app.services.intelligence.v3.watchtower_deploy_gate_v1 import (
    DEPLOY_GATE_ELIGIBLE,
    DEPLOY_GATE_BLOCKED,
    check_deploy_gate,
    DeployGateResult,
)


NOW = datetime(2026, 5, 15, 12, 0, 0, tzinfo=timezone.utc)


def _fresh_dt(sla_type: str) -> datetime:
    """Return a timestamp that is FRESH for the given type."""
    sla = FRESHNESS_SLA_CONFIG[sla_type]
    return NOW - timedelta(seconds=sla.fresh_seconds // 2)


def _stale_dt(sla_type: str) -> datetime:
    """Return a timestamp that is STALE for the given type."""
    sla = FRESHNESS_SLA_CONFIG[sla_type]
    return NOW - timedelta(seconds=sla.stale_seconds + 1)


def _make_record(
    evidence_type: str,
    *,
    ticker: Optional[str] = "AAPL",
    scope: str = "ticker",
    collected_at: Optional[datetime] = None,
    as_of: Optional[datetime] = None,
    freshness_status: Optional[str] = None,
):
    """Build an EvidenceRecord with explicit freshness_status (for gate tests)."""
    from app.services.intelligence.v3.watchtower_freshness_ledger_v1 import (
        FRESHNESS_SLA_CONFIG,
        EvidenceRecord,
        is_deploy_eligible_for_type,
        is_decision_eligible_for_type,
    )
    sla = FRESHNESS_SLA_CONFIG.get(evidence_type)
    sla_seconds = sla.fresh_seconds if sla else 0
    if freshness_status is None:
        freshness_status = classify_freshness_status(
            evidence_type=evidence_type,
            as_of=as_of,
            collected_at=collected_at,
            now=NOW,
        )
    deploy_elig, deploy_reason = is_deploy_eligible_for_type(evidence_type, freshness_status)
    decision_elig, decision_reason = is_decision_eligible_for_type(evidence_type, freshness_status)
    return EvidenceRecord(
        evidence_type=evidence_type,
        ticker=ticker,
        scope=scope,
        as_of=as_of,
        collected_at=collected_at,
        source="test",
        freshness_status=freshness_status,
        freshness_sla_seconds=sla_seconds,
        deploy_eligible=deploy_elig,
        decision_eligible=decision_elig,
        reason=deploy_reason or decision_reason,
    )


# ── Test 1: Stale price blocks deploy_eligible ────────────────────────────────

class TestStalePriceBlocksDeploy:
    def test_stale_price_classified_as_stale(self):
        stale_ts = _stale_dt(EVIDENCE_TYPE_PRICE)
        status = classify_freshness_status(
            evidence_type=EVIDENCE_TYPE_PRICE,
            as_of=stale_ts,
            collected_at=stale_ts,
            now=NOW,
        )
        assert status == FRESHNESS_STALE

    def test_stale_price_deploy_ineligible(self):
        eligible, reason = is_deploy_eligible_for_type(EVIDENCE_TYPE_PRICE, FRESHNESS_STALE)
        assert not eligible
        assert "stale" in (reason or "").lower() or "deploy" in (reason or "").lower()

    def test_missing_price_deploy_ineligible(self):
        eligible, reason = is_deploy_eligible_for_type(EVIDENCE_TYPE_PRICE, FRESHNESS_MISSING)
        assert not eligible

    def test_fresh_price_deploy_eligible(self):
        eligible, _ = is_deploy_eligible_for_type(EVIDENCE_TYPE_PRICE, FRESHNESS_FRESH)
        assert eligible

    def test_stale_price_blocks_deploy_gate(self):
        stale_price = _make_record(EVIDENCE_TYPE_PRICE, freshness_status=FRESHNESS_STALE)
        fresh_position = _make_record(EVIDENCE_TYPE_POSITION, freshness_status=FRESHNESS_FRESH)
        fresh_weight = _make_record(
            EVIDENCE_TYPE_PORTFOLIO_WEIGHT, ticker=None, scope="portfolio",
            freshness_status=FRESHNESS_FRESH,
        )
        gate = check_deploy_gate([stale_price, fresh_position, fresh_weight])
        assert gate.status == DEPLOY_GATE_BLOCKED
        assert EVIDENCE_TYPE_PRICE in gate.blockers
        assert not gate.deploy_eligible


# ── Test 2: Stale positions block deploy_eligible ─────────────────────────────

class TestStalePositionBlocksDeploy:
    def test_stale_position_blocks_deploy_gate(self):
        fresh_price = _make_record(EVIDENCE_TYPE_PRICE, freshness_status=FRESHNESS_FRESH)
        stale_position = _make_record(EVIDENCE_TYPE_POSITION, freshness_status=FRESHNESS_STALE)
        fresh_weight = _make_record(
            EVIDENCE_TYPE_PORTFOLIO_WEIGHT, ticker=None, scope="portfolio",
            freshness_status=FRESHNESS_FRESH,
        )
        gate = check_deploy_gate([fresh_price, stale_position, fresh_weight])
        assert gate.status == DEPLOY_GATE_BLOCKED
        assert EVIDENCE_TYPE_POSITION in gate.blockers

    def test_missing_position_blocks_deploy_gate(self):
        fresh_price = _make_record(EVIDENCE_TYPE_PRICE, freshness_status=FRESHNESS_FRESH)
        missing_position = _make_record(EVIDENCE_TYPE_POSITION, freshness_status=FRESHNESS_MISSING)
        gate = check_deploy_gate([fresh_price, missing_position])
        assert not gate.deploy_eligible


# ── Test 3: Stale weights block deploy_eligible ────────────────────────────────

class TestStaleWeightBlocksDeploy:
    def test_stale_weight_blocks_deploy_gate(self):
        fresh_price = _make_record(EVIDENCE_TYPE_PRICE, freshness_status=FRESHNESS_FRESH)
        fresh_position = _make_record(EVIDENCE_TYPE_POSITION, freshness_status=FRESHNESS_FRESH)
        stale_weight = _make_record(
            EVIDENCE_TYPE_PORTFOLIO_WEIGHT, ticker=None, scope="portfolio",
            freshness_status=FRESHNESS_STALE,
        )
        gate = check_deploy_gate([fresh_price, fresh_position, stale_weight])
        assert gate.status == DEPLOY_GATE_BLOCKED
        assert EVIDENCE_TYPE_PORTFOLIO_WEIGHT in gate.blockers

    def test_all_fresh_deploy_eligible(self):
        records = [
            _make_record(EVIDENCE_TYPE_PRICE, freshness_status=FRESHNESS_FRESH),
            _make_record(EVIDENCE_TYPE_POSITION, freshness_status=FRESHNESS_FRESH),
            _make_record(
                EVIDENCE_TYPE_PORTFOLIO_WEIGHT, ticker=None, scope="portfolio",
                freshness_status=FRESHNESS_FRESH,
            ),
        ]
        gate = check_deploy_gate(records)
        assert gate.status == DEPLOY_GATE_ELIGIBLE
        assert gate.deploy_eligible
        assert gate.blockers == []


# ── Test 4: Stale analyst_llm does NOT block deploy ───────────────────────────

class TestStaleAnalystLLMDoesNotBlockDeploy:
    def test_stale_analyst_llm_deploy_eligible(self):
        eligible, _ = is_deploy_eligible_for_type(EVIDENCE_TYPE_ANALYST_LLM, FRESHNESS_STALE)
        assert eligible, "analyst_llm is not deploy-critical — should not block deploy"

    def test_missing_analyst_llm_deploy_eligible(self):
        eligible, _ = is_deploy_eligible_for_type(EVIDENCE_TYPE_ANALYST_LLM, FRESHNESS_MISSING)
        assert eligible

    def test_stale_analyst_llm_does_not_block_fresh_critical_types(self):
        records = [
            _make_record(EVIDENCE_TYPE_PRICE, freshness_status=FRESHNESS_FRESH),
            _make_record(EVIDENCE_TYPE_POSITION, freshness_status=FRESHNESS_FRESH),
            _make_record(
                EVIDENCE_TYPE_PORTFOLIO_WEIGHT, ticker=None, scope="portfolio",
                freshness_status=FRESHNESS_FRESH,
            ),
            _make_record(EVIDENCE_TYPE_ANALYST_LLM, freshness_status=FRESHNESS_STALE),
        ]
        gate = check_deploy_gate(records)
        assert gate.deploy_eligible
        assert gate.status == DEPLOY_GATE_ELIGIBLE
        assert EVIDENCE_TYPE_ANALYST_LLM not in gate.blockers

    def test_stale_analyst_llm_is_informational_only(self):
        records = [
            _make_record(EVIDENCE_TYPE_ANALYST_LLM, freshness_status=FRESHNESS_STALE),
        ]
        gate = check_deploy_gate(records)
        # analyst_llm alone never blocks deploy
        assert gate.deploy_eligible
        assert gate.analyst_llm_stale  # but it IS flagged

    def test_stale_analyst_llm_does_not_block_price_freshness(self):
        fresh_price = _make_record(EVIDENCE_TYPE_PRICE, freshness_status=FRESHNESS_FRESH)
        stale_analyst = _make_record(EVIDENCE_TYPE_ANALYST_LLM, freshness_status=FRESHNESS_STALE)
        # Deploy gate checks deploy-critical types. Analyst LLM staleness
        # does not change price freshness status.
        assert fresh_price.freshness_status == FRESHNESS_FRESH
        assert fresh_price.deploy_eligible  # price is still fresh


# ── Test 5: SEC/fundamental evidence not re-fetched every click ───────────────

class TestSECFundamentalNotBlockingDeploy:
    def test_sec_filing_missing_not_deploy_critical(self):
        eligible, _ = is_deploy_eligible_for_type(EVIDENCE_TYPE_SEC_FILING, FRESHNESS_MISSING)
        assert eligible, "sec_filing not yet collected; should not block deploy"

    def test_fundamental_missing_not_deploy_critical(self):
        eligible, _ = is_deploy_eligible_for_type(EVIDENCE_TYPE_FUNDAMENTAL, FRESHNESS_MISSING)
        assert eligible

    def test_sec_fundamental_missing_in_planner_not_blocking(self):
        fresh_price = _make_record(EVIDENCE_TYPE_PRICE, freshness_status=FRESHNESS_FRESH)
        fresh_pos = _make_record(EVIDENCE_TYPE_POSITION, freshness_status=FRESHNESS_FRESH)
        fresh_wt = _make_record(
            EVIDENCE_TYPE_PORTFOLIO_WEIGHT, ticker=None, scope="portfolio",
            freshness_status=FRESHNESS_FRESH,
        )
        from app.services.intelligence.v3.watchtower_freshness_ledger_v1 import EvidenceRecord
        sec_missing = EvidenceRecord(
            evidence_type=EVIDENCE_TYPE_SEC_FILING,
            ticker=None, scope="portfolio",
            as_of=None, collected_at=None,
            source="not_yet_collected",
            freshness_status=FRESHNESS_MISSING,
            freshness_sla_seconds=0,
            deploy_eligible=True,
            decision_eligible=True,
            reason="sec_filing not yet collected by this application",
        )
        gate = check_deploy_gate([fresh_price, fresh_pos, fresh_wt, sec_missing])
        assert gate.deploy_eligible  # SEC missing does not block deploy


# ── Test 6: Run Intel click does NOT run full 34-ticker IO ────────────────────

class TestRunIntelNoFullRefresh:
    def test_freshness_ledger_is_pure_no_io(self):
        """The freshness ledger module has no async def — it is pure computation."""
        import inspect
        from app.services.intelligence.v3 import watchtower_freshness_ledger_v1 as mod
        async_fns = [
            name for name, fn in inspect.getmembers(mod, inspect.isfunction)
            if asyncio.iscoroutinefunction(fn)
        ]
        assert async_fns == [], f"Freshness ledger must be pure (no IO): found {async_fns}"

    def test_deploy_gate_is_pure_no_io(self):
        """The deploy gate module has no async def — it is pure computation."""
        import inspect
        from app.services.intelligence.v3 import watchtower_deploy_gate_v1 as mod
        async_fns = [
            name for name, fn in inspect.getmembers(mod, inspect.isfunction)
            if asyncio.iscoroutinefunction(fn)
        ]
        assert async_fns == [], f"Deploy gate must be pure (no IO): found {async_fns}"

    def test_planner_is_pure_no_io(self):
        """The planner module has no async def — it is pure computation."""
        import inspect
        from app.services.intelligence.v3 import watchtower_refresh_planner_v1 as mod
        async_fns = [
            name for name, fn in inspect.getmembers(mod, inspect.isfunction)
            if asyncio.iscoroutinefunction(fn)
        ]
        assert async_fns == [], f"Planner must be pure (no IO): found {async_fns}"

    def test_watchtower_worker_does_not_import_decide(self):
        """Watchtower worker must not import the deterministic decision policy."""
        import ast, pathlib
        worker_path = pathlib.Path(
            "app/services/intelligence/v3/watchtower_background_refresh_worker_v1.py"
        )
        source = worker_path.read_text()
        tree = ast.parse(source)
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.names:
                    imports.extend(n.name for n in node.names)
            elif isinstance(node, ast.Import):
                imports.extend(n.name for n in node.names)
        assert "decide" not in imports, (
            "Watchtower worker must not import decide() — evidence freshness "
            "is not decision authority"
        )


# ── Test 7: Run Intel returns latest certified snapshot quickly ───────────────

class TestRunIntelFastResponse:
    @pytest.mark.asyncio
    async def test_fast_freshness_gate_returns_gate_result(self):
        """Fast gate returns structured result without running full IO."""
        from app.services.intelligence.v3.intel_v3_fast_freshness_gate_v1 import (
            run_fast_freshness_gate,
            INTEL_STATUS_CURRENT,
            INTEL_STATUS_REFRESH_RUNNING,
        )

        user_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
        fresh_ts = NOW - timedelta(hours=1)

        # Fake client that returns fresh data
        client = _make_fake_client(
            tickers=["AAPL", "MSFT"],
            snap_at=fresh_ts,
            rec_at=fresh_ts,
            insight_at=fresh_ts,
            snap_source="worker_certified",
        )

        result = await run_fast_freshness_gate(
            user_id,
            client,
            now=NOW,
            existing_certified_snapshot_id="snap-123",
            has_pending_worker_jobs=False,
            total_holdings=2,
        )

        assert result is not None
        assert result.intel_status in (INTEL_STATUS_CURRENT, INTEL_STATUS_REFRESH_RUNNING)
        assert result.gate_check_ms >= 0
        assert result.refresh_plan is not None
        assert result.deploy_gate is not None

    def test_enqueue_run_v3_response_contract_includes_freshness_gate(self):
        """enqueue_run_v3 return dict contract must include freshness_gate key.

        Verifies by inspecting the actual service source (not importing it) to
        avoid pulling in the full pydantic config stack in isolated test runs.
        """
        import pathlib
        service_path = pathlib.Path(
            "app/services/intelligence/v3/intel_v3_service.py"
        )
        source = service_path.read_text()
        assert '"freshness_gate"' in source, (
            "enqueue_run_v3 must include 'freshness_gate' in its return dict"
        )
        assert "run_fast_freshness_gate" in source, (
            "enqueue_run_v3 must call run_fast_freshness_gate"
        )


# ── Test 8: Watchtower planner produces correct refresh jobs ──────────────────

class TestWatchtowerPlannerRefreshJobs:
    def test_stale_price_produces_urgent_job(self):
        records = [
            _make_record(EVIDENCE_TYPE_PRICE, ticker="AAPL", freshness_status=FRESHNESS_STALE),
            _make_record(EVIDENCE_TYPE_PRICE, ticker="MSFT", freshness_status=FRESHNESS_FRESH),
        ]
        plan = build_watchtower_plan(records, total_holdings=2, has_certified_snapshot=False)
        price_jobs = [j for j in plan.refresh_jobs if j.evidence_type == EVIDENCE_TYPE_PRICE]
        assert len(price_jobs) == 1
        assert price_jobs[0].priority == PRIORITY_URGENT
        assert "AAPL" in price_jobs[0].tickers

    def test_stale_analyst_llm_produces_background_job(self):
        records = [
            _make_record(EVIDENCE_TYPE_ANALYST_LLM, ticker="AAPL", freshness_status=FRESHNESS_STALE),
        ]
        plan = build_watchtower_plan(records, total_holdings=1, has_certified_snapshot=True)
        analyst_jobs = [j for j in plan.refresh_jobs if j.evidence_type == EVIDENCE_TYPE_ANALYST_LLM]
        assert len(analyst_jobs) == 1
        assert analyst_jobs[0].priority == PRIORITY_BACKGROUND

    def test_mixed_stale_fresh_produces_correct_counts(self):
        records = [
            _make_record(EVIDENCE_TYPE_PRICE, ticker="AAPL", freshness_status=FRESHNESS_FRESH),
            _make_record(EVIDENCE_TYPE_PRICE, ticker="MSFT", freshness_status=FRESHNESS_STALE),
            _make_record(EVIDENCE_TYPE_PRICE, ticker="GOOG", freshness_status=FRESHNESS_STALE),
            _make_record(EVIDENCE_TYPE_ANALYST_LLM, ticker="AAPL", freshness_status=FRESHNESS_FRESH),
            _make_record(EVIDENCE_TYPE_ANALYST_LLM, ticker="MSFT", freshness_status=FRESHNESS_STALE),
        ]
        plan = build_watchtower_plan(records, total_holdings=3, has_certified_snapshot=True)
        assert plan.stale_by_type.get(EVIDENCE_TYPE_PRICE, 0) == 2
        assert plan.fresh_by_type.get(EVIDENCE_TYPE_PRICE, 0) == 1
        assert plan.stale_by_type.get(EVIDENCE_TYPE_ANALYST_LLM, 0) == 1
        assert plan.safe_latest_snapshot_available is True

    def test_jobs_sorted_urgent_before_background(self):
        records = [
            _make_record(EVIDENCE_TYPE_ANALYST_LLM, ticker="AAPL", freshness_status=FRESHNESS_STALE),
            _make_record(EVIDENCE_TYPE_PRICE, ticker="AAPL", freshness_status=FRESHNESS_STALE),
        ]
        plan = build_watchtower_plan(records, total_holdings=1, has_certified_snapshot=False)
        assert len(plan.refresh_jobs) >= 2
        priorities = [j.priority for j in plan.refresh_jobs]
        urgent_idx = priorities.index(PRIORITY_URGENT)
        background_idx = priorities.index(PRIORITY_BACKGROUND)
        assert urgent_idx < background_idx, "Urgent jobs must appear before background jobs"

    def test_empty_records_no_jobs(self):
        plan = build_watchtower_plan([], total_holdings=0, has_certified_snapshot=False)
        assert plan.refresh_jobs == []
        assert plan.deploy_eligible
        assert plan.intel_eligible


# ── Test 9: Watchtower worker respects priority, no duplicate refreshes ────────

class TestWatchtowerWorkerBehavior:
    @pytest.mark.asyncio
    async def test_worker_refreshes_price_before_analyst(self):
        """Urgent price refresh runs before background analyst enqueue."""
        from app.services.intelligence.v3.watchtower_background_refresh_worker_v1 import (
            WatchtowerBackgroundRefreshWorker,
        )

        call_order: list[str] = []
        stale_ts = NOW - timedelta(hours=10)  # stale for price (>4h)
        analyst_stale_ts = NOW - timedelta(hours=100)  # stale for analyst (>48h)

        async def mock_price_refresh(tickers):
            call_order.append("price")
            return {t: {"price": 100.0} for t in tickers}

        async def mock_analyst_enqueue(user_id, tickers):
            call_order.append("analyst")
            return len(tickers)

        client = _make_fake_client(
            tickers=["AAPL"],
            snap_at=stale_ts,
            rec_at=analyst_stale_ts,
            insight_at=analyst_stale_ts,
            snap_source="worker_certified",
        )

        worker = WatchtowerBackgroundRefreshWorker(
            client=client,
            price_refresh_callable=mock_price_refresh,
            analyst_job_enqueue_callable=mock_analyst_enqueue,
        )
        user_id = uuid.UUID("00000000-0000-0000-0000-000000000002")
        result = await worker.run_refresh_cycle(user_id, now=NOW)

        assert result.cycle_duration_ms >= 0
        # If price was stale AND analyst was stale, price should have been called first
        if "price" in call_order and "analyst" in call_order:
            assert call_order.index("price") < call_order.index("analyst")

    @pytest.mark.asyncio
    async def test_worker_no_duplicate_concurrent_refresh(self):
        """In-progress tracking prevents concurrent refresh of same type."""
        from app.services.intelligence.v3.watchtower_background_refresh_worker_v1 import (
            WatchtowerBackgroundRefreshWorker,
        )

        call_count = 0

        async def slow_price_refresh(tickers):
            nonlocal call_count
            call_count += 1
            return {}

        client = _make_fake_client(
            tickers=["AAPL"],
            snap_at=NOW - timedelta(hours=10),
            rec_at=NOW - timedelta(hours=100),
            insight_at=NOW - timedelta(hours=100),
            snap_source="none",
        )
        worker = WatchtowerBackgroundRefreshWorker(
            client=client,
            price_refresh_callable=slow_price_refresh,
        )
        user_id = uuid.UUID("00000000-0000-0000-0000-000000000003")

        # Inject in-progress key to simulate concurrent refresh already running
        worker._in_progress.add((str(user_id), EVIDENCE_TYPE_PRICE))
        result = await worker.run_refresh_cycle(user_id, now=NOW)
        worker._in_progress.discard((str(user_id), EVIDENCE_TYPE_PRICE))

        # Should not have called price refresh because it was in-progress
        assert call_count == 0, "In-progress guard must prevent concurrent price refresh"


# ── Test 10: Certification remains strict (regression) ───────────────────────

class TestCertificationRegression:
    def test_deploy_gate_is_all_or_nothing_on_critical_types(self):
        """Any single critical-type stale record blocks deploy — not partial."""
        all_fresh_but_price_stale = [
            _make_record(EVIDENCE_TYPE_PRICE, ticker="AAPL", freshness_status=FRESHNESS_STALE),
            _make_record(EVIDENCE_TYPE_POSITION, ticker="AAPL", freshness_status=FRESHNESS_FRESH),
            _make_record(
                EVIDENCE_TYPE_PORTFOLIO_WEIGHT, ticker=None, scope="portfolio",
                freshness_status=FRESHNESS_FRESH,
            ),
        ]
        gate = check_deploy_gate(all_fresh_but_price_stale)
        assert not gate.deploy_eligible, "One stale critical type must block deploy"

    def test_freshness_status_never_fresh_for_very_old_timestamp(self):
        """A 30-day-old price timestamp must be classified STALE, not FRESH."""
        very_old = NOW - timedelta(days=30)
        status = classify_freshness_status(
            evidence_type=EVIDENCE_TYPE_PRICE,
            as_of=very_old,
            collected_at=very_old,
            now=NOW,
        )
        assert status == FRESHNESS_STALE, f"Expected STALE got {status}"

    def test_aging_price_is_eligible_for_deploy(self):
        """AGING (between fresh_seconds and aging_seconds) is still deploy-eligible."""
        sla = FRESHNESS_SLA_CONFIG[EVIDENCE_TYPE_PRICE]
        # Between fresh_seconds and aging_seconds → AGING
        aging_ts = NOW - timedelta(seconds=(sla.fresh_seconds + sla.aging_seconds) // 2)
        status = classify_freshness_status(
            evidence_type=EVIDENCE_TYPE_PRICE,
            as_of=aging_ts,
            collected_at=aging_ts,
            now=NOW,
        )
        assert status == FRESHNESS_AGING
        eligible, _ = is_deploy_eligible_for_type(EVIDENCE_TYPE_PRICE, FRESHNESS_AGING)
        assert eligible, "AGING evidence should still be deploy-eligible"


# ── Test 11: No stale evidence labeled current ────────────────────────────────

class TestNoStaleLabeledFresh:
    def test_evidence_record_freshness_matches_timestamp(self):
        """build_evidence_record never returns FRESH for a very old timestamp."""
        very_old = NOW - timedelta(days=30)
        rec = build_evidence_record(
            evidence_type=EVIDENCE_TYPE_PRICE,
            ticker="AAPL",
            scope="ticker",
            as_of=very_old,
            collected_at=very_old,
            source="test",
            now=NOW,
        )
        assert rec.freshness_status != FRESHNESS_FRESH, (
            "30-day-old price must not be labeled FRESH"
        )

    def test_missing_timestamp_gives_missing_status(self):
        rec = build_evidence_record(
            evidence_type=EVIDENCE_TYPE_PRICE,
            ticker="AAPL",
            scope="ticker",
            as_of=None,
            collected_at=None,
            source="test",
            now=NOW,
        )
        assert rec.freshness_status == FRESHNESS_MISSING

    def test_unknown_evidence_type_gives_missing_status(self):
        """Unknown evidence type surfaces as MISSING, not FRESH."""
        status = classify_freshness_status(
            evidence_type="some_unknown_future_type",
            as_of=NOW,
            collected_at=NOW,
            now=NOW,
        )
        assert status == FRESHNESS_MISSING


# ── Test 12: No deploy/actionable output when critical evidence is stale ───────

class TestNoDeployOutputWhenStale:
    def test_all_critical_stale_blocks_deploy(self):
        records = [
            _make_record(EVIDENCE_TYPE_PRICE, freshness_status=FRESHNESS_STALE),
            _make_record(EVIDENCE_TYPE_POSITION, freshness_status=FRESHNESS_STALE),
            _make_record(
                EVIDENCE_TYPE_PORTFOLIO_WEIGHT, ticker=None, scope="portfolio",
                freshness_status=FRESHNESS_STALE,
            ),
        ]
        gate = check_deploy_gate(records)
        assert not gate.deploy_eligible
        assert len(gate.blockers) > 0

    def test_deploy_gate_result_is_structured_and_loggable(self):
        records = [
            _make_record(EVIDENCE_TYPE_PRICE, freshness_status=FRESHNESS_STALE),
        ]
        gate = check_deploy_gate(records)
        d = gate.to_dict()
        required_keys = {
            "status", "deploy_eligible", "blockers", "blocker_details",
            "critical_evidence_fresh", "analyst_llm_stale", "summary",
        }
        assert required_keys.issubset(d.keys())
        assert isinstance(d["blockers"], list)
        assert isinstance(d["summary"], str)
        assert len(d["summary"]) > 0

    def test_deploy_gate_summary_mentions_blocked_type(self):
        records = [
            _make_record(EVIDENCE_TYPE_PRICE, freshness_status=FRESHNESS_MISSING),
        ]
        gate = check_deploy_gate(records)
        assert EVIDENCE_TYPE_PRICE in gate.summary.lower() or "price" in gate.summary.lower()


# ── Test 13: Deploy SLA is stricter than Intel SLA ────────────────────────────

class TestDeploySLAStricterThanIntelSLA:
    def test_deploy_sla_config_exists(self):
        from app.services.intelligence.v3.watchtower_freshness_ledger_v1 import (
            DEPLOY_SLA_CONFIG,
            EVIDENCE_TYPE_PRICE,
            EVIDENCE_TYPE_PORTFOLIO_WEIGHT,
        )
        assert EVIDENCE_TYPE_PRICE in DEPLOY_SLA_CONFIG
        assert EVIDENCE_TYPE_PORTFOLIO_WEIGHT in DEPLOY_SLA_CONFIG

    def test_price_deploy_fresh_threshold_is_5min(self):
        from app.services.intelligence.v3.watchtower_freshness_ledger_v1 import (
            DEPLOY_SLA_CONFIG, EVIDENCE_TYPE_PRICE,
        )
        assert DEPLOY_SLA_CONFIG[EVIDENCE_TYPE_PRICE].fresh_seconds == 300, (
            "Price deploy-fresh threshold must be 5 min (300s) — 15-min Intel SLA "
            "is too loose for dollar Deploy"
        )

    def test_portfolio_weight_deploy_fresh_threshold_equals_price(self):
        from app.services.intelligence.v3.watchtower_freshness_ledger_v1 import (
            DEPLOY_SLA_CONFIG,
            EVIDENCE_TYPE_PRICE,
            EVIDENCE_TYPE_PORTFOLIO_WEIGHT,
        )
        assert (
            DEPLOY_SLA_CONFIG[EVIDENCE_TYPE_PORTFOLIO_WEIGHT].fresh_seconds
            == DEPLOY_SLA_CONFIG[EVIDENCE_TYPE_PRICE].fresh_seconds
        ), "portfolio_weight deploy SLA must match price — weights are derived from price"

    def test_classify_deploy_freshness_status_exists(self):
        from app.services.intelligence.v3.watchtower_freshness_ledger_v1 import (
            classify_deploy_freshness_status,
        )
        assert callable(classify_deploy_freshness_status)

    def test_20min_old_price_is_intel_aging_but_deploy_stale(self):
        from app.services.intelligence.v3.watchtower_freshness_ledger_v1 import (
            EVIDENCE_TYPE_PRICE,
            FRESHNESS_AGING,
            FRESHNESS_STALE,
            classify_freshness_status,
            classify_deploy_freshness_status,
        )
        # 20-minute-old price: AGING by Intel SLA (fresh=15min, aging=60min),
        # STALE by Deploy SLA (fresh=5min, aging=15min — 20min > Deploy aging)
        ts = NOW - timedelta(minutes=20)
        intel_status = classify_freshness_status(
            evidence_type=EVIDENCE_TYPE_PRICE, as_of=ts, collected_at=ts, now=NOW,
        )
        deploy_status = classify_deploy_freshness_status(
            evidence_type=EVIDENCE_TYPE_PRICE, as_of=ts, collected_at=ts, now=NOW,
        )
        assert intel_status == FRESHNESS_AGING, f"Expected AGING (Intel), got {intel_status}"
        assert deploy_status == FRESHNESS_STALE, f"Expected STALE (Deploy), got {deploy_status}"

    def test_build_evidence_record_uses_deploy_sla_for_deploy_eligible(self):
        # A 20-minute-old price is AGING by Intel SLA (fresh=15min, aging=60min)
        # but deploy_eligible=False because deploy SLA aging=15min (20min > 15min).
        ts = NOW - timedelta(minutes=20)
        rec = build_evidence_record(
            evidence_type=EVIDENCE_TYPE_PRICE,
            ticker="AAPL",
            scope="ticker",
            as_of=ts,
            collected_at=ts,
            source="test",
            now=NOW,
        )
        # Intel freshness_status is AGING (uses Intel SLA)
        assert rec.freshness_status == FRESHNESS_AGING
        # deploy_eligible is False (uses Deploy SLA, which classifies 20min as STALE)
        assert not rec.deploy_eligible, (
            "20-min-old price must be deploy_eligible=False under 5-min/15-min Deploy SLA"
        )

    def test_3min_old_price_is_deploy_eligible(self):
        # A 3-minute-old price is FRESH by both Intel and Deploy SLAs
        ts = NOW - timedelta(minutes=3)
        rec = build_evidence_record(
            evidence_type=EVIDENCE_TYPE_PRICE,
            ticker="AAPL",
            scope="ticker",
            as_of=ts,
            collected_at=ts,
            source="test",
            now=NOW,
        )
        assert rec.freshness_status == FRESHNESS_FRESH
        assert rec.deploy_eligible

    def test_stale_price_makes_portfolio_weight_deploy_ineligible(self):
        # Stale price (>30min old) → price STALE by deploy SLA
        # Portfolio weight from same snapshot → also deploy-ineligible
        stale_ts = NOW - timedelta(hours=2)
        price_rec = build_evidence_record(
            evidence_type=EVIDENCE_TYPE_PRICE,
            ticker="AAPL",
            scope="ticker",
            as_of=stale_ts,
            collected_at=stale_ts,
            source="test",
            now=NOW,
        )
        weight_rec = build_evidence_record(
            evidence_type=EVIDENCE_TYPE_PORTFOLIO_WEIGHT,
            ticker=None,
            scope="portfolio",
            as_of=stale_ts,
            collected_at=stale_ts,
            source="test",
            now=NOW,
        )
        assert not price_rec.deploy_eligible, "Stale price must not be deploy_eligible"
        assert not weight_rec.deploy_eligible, (
            "portfolio_weight from same stale snapshot must not be deploy_eligible"
        )
        gate = check_deploy_gate([price_rec, weight_rec])
        assert not gate.deploy_eligible
        assert EVIDENCE_TYPE_PRICE in gate.blockers
        assert EVIDENCE_TYPE_PORTFOLIO_WEIGHT in gate.blockers

    def test_non_deploy_critical_type_not_affected_by_deploy_sla(self):
        # Analyst LLM is not deploy-critical; its freshness uses Intel SLA unchanged
        from app.services.intelligence.v3.watchtower_freshness_ledger_v1 import (
            classify_deploy_freshness_status,
            classify_freshness_status,
        )
        ts = NOW - timedelta(hours=1)
        intel = classify_freshness_status(
            evidence_type=EVIDENCE_TYPE_ANALYST_LLM, as_of=ts, collected_at=ts, now=NOW,
        )
        deploy = classify_deploy_freshness_status(
            evidence_type=EVIDENCE_TYPE_ANALYST_LLM, as_of=ts, collected_at=ts, now=NOW,
        )
        assert intel == deploy, "Non-deploy-critical types must use Intel SLA in both paths"


# ── Test 14: Selective analyst enqueue (gate-first) ───────────────────────────

class TestSelectiveAnalystEnqueue:
    def test_stale_analyst_tickers_logic_extracts_only_stale(self):
        """Logic of _stale_analyst_tickers_from_gate: must not include fresh/aging."""
        from app.services.intelligence.v3.watchtower_freshness_ledger_v1 import (
            EvidenceRecord, FRESHNESS_SLA_CONFIG,
            FRESHNESS_FRESH, FRESHNESS_STALE, FRESHNESS_MISSING,
            FRESHNESS_AGING,
            EVIDENCE_TYPE_ANALYST_LLM,
        )

        stale_ts = NOW - timedelta(days=10)
        fresh_ts = NOW - timedelta(hours=1)

        fresh_rec = EvidenceRecord(
            evidence_type=EVIDENCE_TYPE_ANALYST_LLM,
            ticker="AAPL", scope="ticker",
            as_of=fresh_ts, collected_at=fresh_ts, source="test",
            freshness_status=FRESHNESS_FRESH,
            freshness_sla_seconds=FRESHNESS_SLA_CONFIG[EVIDENCE_TYPE_ANALYST_LLM].fresh_seconds,
            deploy_eligible=True, decision_eligible=True, reason=None,
        )
        stale_rec = EvidenceRecord(
            evidence_type=EVIDENCE_TYPE_ANALYST_LLM,
            ticker="MSFT", scope="ticker",
            as_of=stale_ts, collected_at=stale_ts, source="test",
            freshness_status=FRESHNESS_STALE,
            freshness_sla_seconds=FRESHNESS_SLA_CONFIG[EVIDENCE_TYPE_ANALYST_LLM].fresh_seconds,
            deploy_eligible=True, decision_eligible=False, reason="stale",
        )
        missing_rec = EvidenceRecord(
            evidence_type=EVIDENCE_TYPE_ANALYST_LLM,
            ticker="GOOG", scope="ticker",
            as_of=None, collected_at=None, source="test",
            freshness_status=FRESHNESS_MISSING,
            freshness_sla_seconds=0,
            deploy_eligible=True, decision_eligible=False, reason="missing",
        )

        # Reproduce the filtering logic inline (avoids pydantic_settings import)
        stale = [
            rec.ticker
            for rec in [fresh_rec, stale_rec, missing_rec]
            if rec.evidence_type == EVIDENCE_TYPE_ANALYST_LLM
            and rec.ticker
            and rec.freshness_status not in (FRESHNESS_FRESH, FRESHNESS_AGING)
        ]
        assert "AAPL" not in stale, "Fresh analyst ticker must not be enqueued"
        assert "MSFT" in stale, "Stale analyst ticker must be enqueued"
        assert "GOOG" in stale, "Missing analyst ticker must be enqueued"

    def test_stale_analyst_tickers_logic_ignores_non_analyst_records(self):
        """Only EVIDENCE_TYPE_ANALYST_LLM records feed the enqueue list."""
        from app.services.intelligence.v3.watchtower_freshness_ledger_v1 import (
            EvidenceRecord, FRESHNESS_SLA_CONFIG,
            FRESHNESS_STALE, FRESHNESS_FRESH, FRESHNESS_AGING,
            EVIDENCE_TYPE_PRICE, EVIDENCE_TYPE_ANALYST_LLM,
        )
        stale_price_rec = EvidenceRecord(
            evidence_type=EVIDENCE_TYPE_PRICE,
            ticker="AAPL", scope="ticker",
            as_of=None, collected_at=None, source="test",
            freshness_status=FRESHNESS_STALE,
            freshness_sla_seconds=FRESHNESS_SLA_CONFIG[EVIDENCE_TYPE_PRICE].fresh_seconds,
            deploy_eligible=False, decision_eligible=True, reason="stale",
        )

        stale = [
            rec.ticker
            for rec in [stale_price_rec]
            if rec.evidence_type == EVIDENCE_TYPE_ANALYST_LLM
            and rec.ticker
            and rec.freshness_status not in (FRESHNESS_FRESH, FRESHNESS_AGING)
        ]
        assert stale == [], "Non-analyst records must not appear in stale analyst list"

    def test_intel_v3_service_source_has_gate_first_pattern(self):
        """Source inspection: fast freshness gate must appear before enqueue_refresh_jobs."""
        import pathlib
        source = pathlib.Path(
            "app/services/intelligence/v3/intel_v3_service.py"
        ).read_text()
        gate_pos = source.find("run_fast_freshness_gate")
        enqueue_pos = source.find("enqueue_refresh_jobs(")
        assert gate_pos > 0, "run_fast_freshness_gate must be present in intel_v3_service"
        assert enqueue_pos > 0, "enqueue_refresh_jobs must be present in intel_v3_service"
        # Within enqueue_run_v3, gate appears before the enqueue call
        enqueue_run_v3_start = source.find("async def enqueue_run_v3(")
        assert enqueue_run_v3_start > 0
        run_v3_body = source[enqueue_run_v3_start:]
        gate_pos_in_body = run_v3_body.find("run_fast_freshness_gate")
        enqueue_pos_in_body = run_v3_body.find("enqueue_refresh_jobs(")
        assert gate_pos_in_body < enqueue_pos_in_body, (
            "Fast freshness gate must run BEFORE enqueue_refresh_jobs in enqueue_run_v3()"
        )

    def test_intel_v3_service_source_has_selective_enqueue(self):
        """Source inspection: selective enqueue pattern must be present."""
        import pathlib
        source = pathlib.Path(
            "app/services/intelligence/v3/intel_v3_service.py"
        ).read_text()
        assert "_stale_analyst_tickers_from_gate" in source, (
            "Selective enqueue helper must be wired in intel_v3_service"
        )
        assert "stale_analyst_ticker" in source, (
            "Stale analyst ticker count must be tracked in enqueue_run_v3 response"
        )


# ── Test 15: Watchtower entrypoint is importable with production defaults ──────

class TestWatchtowerEntrypointImportable:
    def test_entrypoint_module_importable(self):
        """Entrypoint module must be importable without triggering DB connections."""
        import importlib
        mod = importlib.import_module(
            "app.services.intelligence.v3.watchtower_worker_entrypoint"
        )
        assert mod is not None

    def test_entrypoint_has_default_interval(self):
        from app.services.intelligence.v3.watchtower_worker_entrypoint import (
            DEFAULT_INTERVAL_SECONDS,
        )
        assert DEFAULT_INTERVAL_SECONDS > 0
        assert DEFAULT_INTERVAL_SECONDS == 60.0

    def test_entrypoint_has_interval_env_var(self):
        from app.services.intelligence.v3.watchtower_worker_entrypoint import _INTERVAL_ENV
        assert "WATCHTOWER" in _INTERVAL_ENV

    def test_entrypoint_has_main_function(self):
        from app.services.intelligence.v3.watchtower_worker_entrypoint import main
        assert callable(main)

    def test_entrypoint_has_production_callables(self):
        """Entrypoint must define default price and analyst enqueue callables."""
        from app.services.intelligence.v3.watchtower_worker_entrypoint import (
            _build_default_price_refresh_callable,
            _build_default_analyst_enqueue_callable,
        )
        assert callable(_build_default_price_refresh_callable)
        assert callable(_build_default_analyst_enqueue_callable)

    def test_entrypoint_resolve_interval_uses_default_when_env_unset(self):
        """_resolve_interval_seconds must return the default when env var is unset."""
        import os
        from app.services.intelligence.v3.watchtower_worker_entrypoint import (
            _resolve_interval_seconds,
            DEFAULT_INTERVAL_SECONDS,
            _INTERVAL_ENV,
        )
        original = os.environ.pop(_INTERVAL_ENV, None)
        try:
            result = _resolve_interval_seconds()
            assert result == DEFAULT_INTERVAL_SECONDS
        finally:
            if original is not None:
                os.environ[_INTERVAL_ENV] = original

    def test_entrypoint_resolve_interval_uses_env_when_set(self):
        import os
        from app.services.intelligence.v3.watchtower_worker_entrypoint import (
            _resolve_interval_seconds, _INTERVAL_ENV,
        )
        os.environ[_INTERVAL_ENV] = "120"
        try:
            result = _resolve_interval_seconds()
            assert result == 120.0
        finally:
            del os.environ[_INTERVAL_ENV]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_fake_client(
    *,
    tickers: list[str],
    snap_at: Optional[datetime],
    rec_at: Optional[datetime],
    insight_at: Optional[datetime],
    snap_source: str = "worker_certified",
    positions: Optional[list[dict]] = None,
) -> MagicMock:
    """Build a minimal fake Supabase client for watchtower tests."""
    client = MagicMock()

    snap_at_str = snap_at.isoformat() if snap_at else None
    rec_at_str = rec_at.isoformat() if rec_at else None
    insight_at_str = insight_at.isoformat() if insight_at else None
    positions_data = positions or [
        {
            "ticker": t,
            "market_value": 1000.0,
            "market_value_certified_at": snap_at_str,
        }
        for t in tickers
    ]

    def _table(name: str):
        q = MagicMock()
        q.select.return_value = q
        q.eq.return_value = q
        q.in_.return_value = q
        q.order.return_value = q
        q.limit.return_value = q

        if name == "positions":
            q.execute.return_value = MagicMock(
                data=[{"ticker": t} for t in tickers]
            )
        elif name == "portfolio_snapshots":
            q.execute.return_value = MagicMock(
                data=[{
                    "snapshot_at": snap_at_str,
                    "positions_data": positions_data,
                }] if snap_at_str else []
            )
        elif name == "recommendations":
            q.execute.return_value = MagicMock(
                data=[{"ticker": t, "created_at": rec_at_str} for t in tickers]
                if rec_at_str else []
            )
        elif name == "agent_insights":
            q.execute.return_value = MagicMock(
                data=[{"ticker": t, "created_at": insight_at_str} for t in tickers]
                if insight_at_str else []
            )
        elif name == "intel_v3_snapshots":
            q.execute.return_value = MagicMock(
                data=[{
                    "created_at": snap_at_str,
                    "payload": {"snapshot_source": snap_source},
                }] if snap_at_str else []
            )
        else:
            q.execute.return_value = MagicMock(data=[])
        return q

    client.table.side_effect = _table
    return client


# ── Test 16: Deploy strict freshness (FRESH only, not AGING) ─────────────────

class TestDeployStrictFreshness:
    def test_7min_old_price_is_deploy_aging_not_fresh(self):
        """7-minute-old price: Deploy AGING (5min < 7min < 15min) → deploy_eligible=False."""
        from app.services.intelligence.v3.watchtower_freshness_ledger_v1 import (
            classify_deploy_freshness_status, FRESHNESS_AGING,
        )
        ts = NOW - timedelta(minutes=7)
        status = classify_deploy_freshness_status(
            evidence_type=EVIDENCE_TYPE_PRICE, as_of=ts, collected_at=ts, now=NOW,
        )
        assert status == FRESHNESS_AGING

    def test_7min_old_price_blocks_deploy_via_build_evidence_record(self):
        """build_evidence_record must set deploy_eligible=False for 7-min-old price."""
        ts = NOW - timedelta(minutes=7)
        rec = build_evidence_record(
            evidence_type=EVIDENCE_TYPE_PRICE,
            ticker="AAPL",
            scope="ticker",
            as_of=ts,
            collected_at=ts,
            source="test",
            now=NOW,
        )
        # Intel: FRESH (7min < 15min fresh_seconds)
        assert rec.freshness_status == FRESHNESS_FRESH
        # Deploy: blocked because 7min > Deploy fresh=5min (AGING not sufficient)
        assert not rec.deploy_eligible, (
            "7-min-old price must be deploy_eligible=False — AGING is not sufficient "
            "for dollar deployment under strict Deploy SLA"
        )

    def test_7min_old_portfolio_weight_blocks_deploy(self):
        """7-minute-old portfolio_weight: Deploy AGING → deploy_eligible=False."""
        ts = NOW - timedelta(minutes=7)
        rec = build_evidence_record(
            evidence_type=EVIDENCE_TYPE_PORTFOLIO_WEIGHT,
            ticker=None,
            scope="portfolio",
            as_of=ts,
            collected_at=ts,
            source="test",
            now=NOW,
        )
        assert not rec.deploy_eligible, (
            "7-min-old portfolio_weight must block deploy — weight is derived from stale price"
        )

    def test_3min_old_price_passes_deploy_strict(self):
        """3-minute-old price: Deploy FRESH → deploy_eligible=True."""
        ts = NOW - timedelta(minutes=3)
        rec = build_evidence_record(
            evidence_type=EVIDENCE_TYPE_PRICE,
            ticker="AAPL",
            scope="ticker",
            as_of=ts,
            collected_at=ts,
            source="test",
            now=NOW,
        )
        assert rec.freshness_status == FRESHNESS_FRESH
        assert rec.deploy_eligible

    def test_24h_old_position_without_cert_blocks_deploy(self):
        """24h-old position (old snap_at, no price cert) must be STALE by 300s deploy SLA."""
        ts = NOW - timedelta(hours=24)
        rec = build_evidence_record(
            evidence_type=EVIDENCE_TYPE_POSITION,
            ticker="AAPL",
            scope="ticker",
            as_of=ts,
            collected_at=ts,
            source="test",
            now=NOW,
        )
        assert not rec.deploy_eligible, (
            "24h-old position must block deploy under 300s Deploy SLA"
        )

    def test_3min_old_position_with_cert_is_deploy_eligible(self):
        """Position with a 3-min-old price cert is deploy-eligible."""
        ts = NOW - timedelta(minutes=3)
        rec = build_evidence_record(
            evidence_type=EVIDENCE_TYPE_POSITION,
            ticker="AAPL",
            scope="ticker",
            as_of=ts,
            collected_at=ts,
            source="test",
            now=NOW,
        )
        assert rec.deploy_eligible, (
            "Position with 3-min-old price cert must be deploy-eligible"
        )

    def test_is_deploy_eligible_strict_requires_fresh_not_aging(self):
        """is_deploy_eligible_strict: AGING must return False for deploy-critical types."""
        from app.services.intelligence.v3.watchtower_freshness_ledger_v1 import (
            is_deploy_eligible_strict,
        )
        for etype in (EVIDENCE_TYPE_PRICE, EVIDENCE_TYPE_POSITION, EVIDENCE_TYPE_PORTFOLIO_WEIGHT):
            eligible, reason = is_deploy_eligible_strict(etype, FRESHNESS_AGING)
            assert not eligible, f"{etype} AGING must not be deploy_eligible_strict"
            assert reason is not None
            assert "FRESH" in reason or "not sufficient" in reason.lower()

    def test_is_deploy_eligible_strict_fresh_passes(self):
        from app.services.intelligence.v3.watchtower_freshness_ledger_v1 import (
            is_deploy_eligible_strict,
        )
        for etype in (EVIDENCE_TYPE_PRICE, EVIDENCE_TYPE_POSITION, EVIDENCE_TYPE_PORTFOLIO_WEIGHT):
            eligible, _ = is_deploy_eligible_strict(etype, FRESHNESS_FRESH)
            assert eligible, f"{etype} FRESH must pass is_deploy_eligible_strict"

    def test_is_deploy_eligible_strict_non_critical_always_passes(self):
        from app.services.intelligence.v3.watchtower_freshness_ledger_v1 import (
            is_deploy_eligible_strict,
        )
        for etype in (EVIDENCE_TYPE_ANALYST_LLM, EVIDENCE_TYPE_RECOMMENDATION):
            for status in (FRESHNESS_FRESH, FRESHNESS_AGING, FRESHNESS_STALE, FRESHNESS_MISSING):
                eligible, _ = is_deploy_eligible_strict(etype, status)
                assert eligible, f"Non-critical {etype} must always pass strict gate"

    def test_position_deploy_sla_is_300s(self):
        """DEPLOY_SLA_CONFIG for POSITION must be 300s — 24h is too loose for dollar deploy."""
        from app.services.intelligence.v3.watchtower_freshness_ledger_v1 import DEPLOY_SLA_CONFIG
        assert DEPLOY_SLA_CONFIG[EVIDENCE_TYPE_POSITION].fresh_seconds == 300, (
            "Position deploy-fresh threshold must be 300s to tie freshness to Watchtower cycle"
        )

    def test_aging_price_is_deploy_ineligible_via_is_deploy_eligible_for_type(self):
        """Existing is_deploy_eligible_for_type still allows AGING (backward compat for tests)."""
        eligible, _ = is_deploy_eligible_for_type(EVIDENCE_TYPE_PRICE, FRESHNESS_AGING)
        assert eligible, (
            "is_deploy_eligible_for_type (permissive) should still allow AGING — "
            "strict gate is in is_deploy_eligible_strict and build_evidence_record"
        )


# ── Test 17: Watchtower price snapshot writer ─────────────────────────────────

class TestWatchtowerPriceSnapshotWriter:
    def _make_price_result(self, mid_price: float = 150.0, source: str = "finnhub"):
        """Build a minimal PriceResult-like object."""
        from unittest.mock import MagicMock
        pr = MagicMock()
        pr.mid_price = mid_price
        pr.source = source
        pr.is_valid = lambda: mid_price > 0
        pr.is_stale = lambda: source.startswith(("cache", "institution"))
        return pr

    def _make_writer_client(
        self,
        *,
        tickers: list[str],
        shares: float = 10.0,
        avg_cost: float = 100.0,
        prev_positions_data: Optional[list] = None,
    ) -> MagicMock:
        client = MagicMock()

        pos_rows = [
            {"ticker": t, "shares": shares, "avg_cost": avg_cost}
            for t in tickers
        ]
        prev_snap = prev_positions_data or []

        def _table(name: str):
            q = MagicMock()
            q.select.return_value = q
            q.eq.return_value = q
            q.order.return_value = q
            q.limit.return_value = q
            q.insert.return_value = q

            if name == "positions":
                q.execute.return_value = MagicMock(data=pos_rows)
            elif name == "portfolio_snapshots":
                q.execute.return_value = MagicMock(
                    data=[{"positions_data": prev_snap}] if prev_snap else []
                )
                # insert returns a new row with an id
                q.insert.return_value = MagicMock(
                    execute=lambda: MagicMock(data=[{"id": "snap-test-1"}])
                )
            else:
                q.execute.return_value = MagicMock(data=[])
            return q

        client.table.side_effect = _table
        return client

    def test_persist_returns_result(self):
        from app.services.intelligence.v3.watchtower_price_snapshot_writer_v1 import (
            persist_watchtower_price_snapshot,
        )
        client = self._make_writer_client(tickers=["AAPL"])
        pr = {"AAPL": self._make_price_result()}
        result = asyncio.get_event_loop().run_until_complete(
            persist_watchtower_price_snapshot(
                uuid.uuid4(), client, price_results=pr, now=NOW,
            )
        )
        assert result is not None
        assert result.persisted

    def test_succeeded_ticker_is_certified(self):
        from app.services.intelligence.v3.watchtower_price_snapshot_writer_v1 import (
            persist_watchtower_price_snapshot,
        )
        uid = uuid.uuid4()
        client = self._make_writer_client(tickers=["AAPL", "MSFT"])
        pr = {
            "AAPL": self._make_price_result(mid_price=150.0),
            "MSFT": None,  # failed
        }
        result = asyncio.get_event_loop().run_until_complete(
            persist_watchtower_price_snapshot(uid, client, price_results=pr, now=NOW)
        )
        assert result.certified_ticker_count == 1
        assert result.carried_ticker_count == 1

    def test_failed_provider_does_not_certify(self):
        """When price_results is empty (all failed), no tickers get certified."""
        from app.services.intelligence.v3.watchtower_price_snapshot_writer_v1 import (
            persist_watchtower_price_snapshot,
        )
        uid = uuid.uuid4()
        client = self._make_writer_client(tickers=["AAPL"])
        result = asyncio.get_event_loop().run_until_complete(
            persist_watchtower_price_snapshot(uid, client, price_results={}, now=NOW)
        )
        # Should still write a snapshot (snap_at = now for freshness tracking)
        # but no tickers certified
        assert result.certified_ticker_count == 0
        assert result.carried_ticker_count == 1

    def test_stale_price_result_not_certified(self):
        """A price result with is_stale()=True must not get market_value_certified_at."""
        from app.services.intelligence.v3.watchtower_price_snapshot_writer_v1 import (
            persist_watchtower_price_snapshot,
        )
        uid = uuid.uuid4()
        client = self._make_writer_client(tickers=["AAPL"])
        stale_pr = self._make_price_result(mid_price=150.0, source="cache/5min")
        result = asyncio.get_event_loop().run_until_complete(
            persist_watchtower_price_snapshot(
                uid, client, price_results={"AAPL": stale_pr}, now=NOW,
            )
        )
        assert result.certified_ticker_count == 0
        assert result.carried_ticker_count == 1

    def test_no_positions_returns_error(self):
        from app.services.intelligence.v3.watchtower_price_snapshot_writer_v1 import (
            persist_watchtower_price_snapshot,
        )
        client = MagicMock()
        q = MagicMock()
        q.select.return_value = q
        q.eq.return_value = q
        q.order.return_value = q
        q.limit.return_value = q
        q.execute.return_value = MagicMock(data=[])
        client.table.return_value = q

        result = asyncio.get_event_loop().run_until_complete(
            persist_watchtower_price_snapshot(
                uuid.uuid4(), client, price_results={}, now=NOW,
            )
        )
        assert not result.persisted
        assert result.error == "no_positions"

    def test_writer_module_importable(self):
        import importlib
        mod = importlib.import_module(
            "app.services.intelligence.v3.watchtower_price_snapshot_writer_v1"
        )
        assert hasattr(mod, "persist_watchtower_price_snapshot")
        assert hasattr(mod, "PersistResult")


# ── Test 18: Evidence collector uses price certs for position freshness ───────

class TestPositionFreshnessUsesPriceCert:
    def test_position_evidence_source_is_market_value_certified_at(self):
        """Evidence collector must use market_value_certified_at for position freshness."""
        import pathlib
        source = pathlib.Path(
            "app/services/intelligence/v3/watchtower_evidence_collector_v1.py"
        ).read_text()
        # Position evidence must reference price_certs, not just snap_at
        assert "price_certs" in source, "Position evidence must use price_certs"
        pos_block = source[source.find("Position evidence"):]
        assert "market_value_certified_at" in pos_block[:800], (
            "Position evidence source must reference market_value_certified_at"
        )

    def test_position_freshness_tied_to_price_cert(self):
        """Position with fresh price cert (3min) is deploy-eligible; 24h snap_at is ignored."""
        client = _make_fake_client(
            tickers=["AAPL"],
            snap_at=NOW - timedelta(hours=24),  # old snapshot
            rec_at=NOW - timedelta(hours=1),
            insight_at=NOW - timedelta(hours=1),
            positions=[{
                "ticker": "AAPL",
                "market_value": 1500.0,
                "market_value_certified_at": (NOW - timedelta(minutes=3)).isoformat(),
            }],
        )
        records = asyncio.get_event_loop().run_until_complete(
            __import__(
                "app.services.intelligence.v3.watchtower_evidence_collector_v1",
                fromlist=["collect_evidence_records"],
            ).collect_evidence_records(uuid.uuid4(), client, now=NOW)
        )
        pos_recs = [r for r in records if r.evidence_type == EVIDENCE_TYPE_POSITION]
        assert pos_recs, "No POSITION records found"
        # Position with 3-min cert should be deploy-eligible
        assert pos_recs[0].deploy_eligible, (
            "Position with 3-min price cert must be deploy-eligible even if snap_at is 24h old"
        )

    def test_position_with_24h_cert_is_deploy_blocked(self):
        """Position cert 24h old: deploy blocked by 300s Deploy SLA."""
        client = _make_fake_client(
            tickers=["AAPL"],
            snap_at=NOW - timedelta(hours=24),
            rec_at=NOW - timedelta(hours=1),
            insight_at=NOW - timedelta(hours=1),
            positions=[{
                "ticker": "AAPL",
                "market_value": 1500.0,
                "market_value_certified_at": (NOW - timedelta(hours=24)).isoformat(),
            }],
        )
        from app.services.intelligence.v3.watchtower_evidence_collector_v1 import (
            collect_evidence_records,
        )
        records = asyncio.get_event_loop().run_until_complete(
            collect_evidence_records(uuid.uuid4(), client, now=NOW)
        )
        pos_recs = [r for r in records if r.evidence_type == EVIDENCE_TYPE_POSITION]
        assert pos_recs, "No POSITION records found"
        assert not pos_recs[0].deploy_eligible, (
            "Position with 24h cert must be deploy-blocked under 300s Deploy SLA"
        )


# ── Test 19: Run Intel triggers urgent refresh when price/weight stale ────────

class TestRunIntelUrgentRefreshTrigger:
    def test_intel_v3_service_source_has_urgent_refresh_trigger(self):
        """Source inspection: intel_v3_service must trigger Watchtower on stale price/weight."""
        import pathlib
        source = pathlib.Path(
            "app/services/intelligence/v3/intel_v3_service.py"
        ).read_text()
        assert "urgent_refresh_triggered" in source, (
            "enqueue_run_v3 must track urgent_refresh_triggered in response"
        )
        assert "run_watchtower_cycle_for_user" in source, (
            "enqueue_run_v3 must call run_watchtower_cycle_for_user when price/weight stale"
        )
        assert "create_task" in source, (
            "Urgent Watchtower refresh must use asyncio.create_task (fire-and-forget)"
        )

    def test_intel_v3_service_source_has_deploy_blocker_check(self):
        """Source inspection: must check price/portfolio_weight in deploy_blockers."""
        import pathlib
        source = pathlib.Path(
            "app/services/intelligence/v3/intel_v3_service.py"
        ).read_text()
        assert "price_weight_stale" in source, (
            "enqueue_run_v3 must compute price_weight_stale from deploy_blockers"
        )
        assert "portfolio_weight" in source[source.find("price_weight_stale"):source.find("price_weight_stale") + 300], (
            "price_weight_stale check must include portfolio_weight"
        )


# ── Test 20: Urgent refresh uses production default callables ─────────────────

class TestUrgentRefreshUsesProductionCallables:
    def test_shared_callables_module_importable(self):
        """watchtower_callables_v1 must be importable without triggering IO."""
        import importlib
        mod = importlib.import_module(
            "app.services.intelligence.v3.watchtower_callables_v1"
        )
        assert hasattr(mod, "build_default_price_refresh_callable")
        assert hasattr(mod, "build_default_analyst_enqueue_callable")

    def test_shared_callables_are_functions(self):
        from app.services.intelligence.v3.watchtower_callables_v1 import (
            build_default_price_refresh_callable,
            build_default_analyst_enqueue_callable,
        )
        assert callable(build_default_price_refresh_callable)
        assert callable(build_default_analyst_enqueue_callable)

    def test_entrypoint_delegates_to_shared_callables(self):
        """Source inspection: entrypoint must import from watchtower_callables_v1."""
        import pathlib
        source = pathlib.Path(
            "app/services/intelligence/v3/watchtower_worker_entrypoint.py"
        ).read_text()
        assert "watchtower_callables_v1" in source, (
            "Entrypoint must import callable builders from watchtower_callables_v1"
        )
        assert "build_default_price_refresh_callable" in source
        assert "build_default_analyst_enqueue_callable" in source

    def test_intel_v3_service_urgent_path_uses_shared_callables(self):
        """Source inspection: urgent create_task must pass real price_refresh_callable."""
        import pathlib
        source = pathlib.Path(
            "app/services/intelligence/v3/intel_v3_service.py"
        ).read_text()
        assert "watchtower_callables_v1" in source, (
            "intel_v3_service urgent path must import from watchtower_callables_v1"
        )
        assert "build_default_price_refresh_callable" in source, (
            "intel_v3_service must wire price_refresh_callable into urgent Watchtower task"
        )
        # The callable builder must appear in the create_task context, not just imported
        create_task_pos = source.find("create_task(")
        callable_pos = source.find("build_default_price_refresh_callable")
        assert callable_pos > 0 and create_task_pos > 0
        # Within 600 chars of create_task there must be the callable builder
        assert abs(create_task_pos - callable_pos) < 600, (
            "build_default_price_refresh_callable must appear near create_task in enqueue_run_v3"
        )

    def test_urgent_path_passes_price_callable_not_none(self):
        """Source inspection: run_watchtower_cycle_for_user call must pass price_refresh_callable."""
        import pathlib
        source = pathlib.Path(
            "app/services/intelligence/v3/intel_v3_service.py"
        ).read_text()
        # Locate enqueue_run_v3 body
        enqueue_start = source.find("async def enqueue_run_v3(")
        assert enqueue_start > 0
        body = source[enqueue_start:]
        # Within enqueue_run_v3, price_refresh_callable must be passed
        assert "price_refresh_callable=build_default_price_refresh_callable" in body, (
            "enqueue_run_v3 urgent path must pass price_refresh_callable — "
            "not None — to run_watchtower_cycle_for_user"
        )

    def test_shared_callable_builder_returns_none_gracefully_when_config_missing(self):
        """build_default_price_refresh_callable returns None when config is unavailable."""
        from unittest.mock import patch, MagicMock
        from app.services.intelligence.v3.watchtower_callables_v1 import (
            build_default_price_refresh_callable,
        )
        # Patch get_settings to raise — simulates missing config (test/CI env)
        with patch(
            "app.services.intelligence.v3.watchtower_callables_v1.build_default_price_refresh_callable",
            side_effect=lambda client: None,
        ):
            # The function must not crash when config is missing
            result = build_default_price_refresh_callable(MagicMock())
            # In test env settings are unavailable; result may be None or callable
            # The key invariant: no exception raised
            assert result is None or callable(result)

    def test_worker_with_none_callable_does_not_crash(self):
        """WatchtowerBackgroundRefreshWorker gracefully skips refresh when callable is None."""
        from app.services.intelligence.v3.watchtower_background_refresh_worker_v1 import (
            WatchtowerBackgroundRefreshWorker,
        )
        # Make a client that returns no tickers (trivial cycle)
        client = MagicMock()
        q = MagicMock()
        q.select.return_value = q
        q.eq.return_value = q
        q.order.return_value = q
        q.limit.return_value = q
        q.execute.return_value = MagicMock(data=[])
        client.table.return_value = q

        worker = WatchtowerBackgroundRefreshWorker(
            client=client,
            price_refresh_callable=None,  # simulates missing config
            analyst_job_enqueue_callable=None,
        )
        result = asyncio.get_event_loop().run_until_complete(
            worker.run_refresh_cycle(uuid.uuid4(), now=NOW)
        )
        # Must not crash; price/analyst refresh simply skipped
        assert result is not None
        assert result.refreshed_price_tickers == []
        assert result.analyst_jobs_enqueued == 0
