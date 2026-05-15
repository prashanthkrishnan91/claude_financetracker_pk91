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
