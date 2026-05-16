"""Watchtower Background Refresh Worker v1 (Build 1D).

Durable, backend-only Watchtower refresh path that can be run continuously
or on a schedule to keep evidence fresh without blocking click-time requests.

Responsibilities:
  1. Use the WatchtowerRefreshPlan to identify what is stale.
  2. Refresh stale evidence by priority (price/weights first — fast and cheap,
     analyst LLM deferred to existing analyst_refresh_worker_v1 — slow).
  3. Avoid full 34-ticker all-source refresh when only a slice is stale.
  4. Update freshness ledger / log freshness status after each refresh.
  5. Avoid duplicate concurrent refreshes via in-progress tracking.
  6. Respect provider backoff limits.
  7. Log clearly — watchtower_refresh_worker_summary, watchtower_evidence_updated.

Architecture note:
  - Price/weight refresh: this worker handles directly via price_refresh_callable.
  - Analyst LLM refresh: deferred to analyst_refresh_worker_v1 (already has
    durable job queue + backoff). This worker enqueues jobs; it does NOT run
    LLM calls inline.
  - Position data: user-imported; this worker does not manufacture positions.
    It reports staleness honestly and waits for user to update.

Hard boundary: this module must NOT import the deterministic Intel v3 decision
policy (decide()). Evidence freshness is NOT the same as final action authority.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional
from uuid import UUID

from .watchtower_evidence_collector_v1 import collect_evidence_records
from .watchtower_freshness_ledger_v1 import (
    EVIDENCE_TYPE_ANALYST_LLM,
    EVIDENCE_TYPE_PORTFOLIO_WEIGHT,
    EVIDENCE_TYPE_POSITION,
    EVIDENCE_TYPE_PRICE,
    EVIDENCE_TYPE_RECOMMENDATION,
    FRESHNESS_FRESH,
    FRESHNESS_AGING,
    EvidenceRecord,
)
from .watchtower_refresh_planner_v1 import (
    PRIORITY_URGENT,
    PRIORITY_NORMAL,
    PRIORITY_BACKGROUND,
    WatchtowerRefreshPlan,
    build_watchtower_plan,
)

logger = logging.getLogger(__name__)

# Max seconds this worker will spend refreshing evidence in one cycle.
# Keeps individual cycles bounded even if many slices are stale.
DEFAULT_MAX_CYCLE_SECONDS = 30.0
DEFAULT_MAX_PRICE_TICKERS_PER_CYCLE = 50


@dataclass
class WatchtowerRefreshCycleResult:
    """Result of one Watchtower refresh cycle."""
    refreshed_price_tickers: list[str] = field(default_factory=list)
    failed_price_tickers: list[str] = field(default_factory=list)
    analyst_jobs_enqueued: int = 0
    position_staleness_logged: bool = False
    cycle_duration_ms: int = 0
    deploy_eligible_after: bool = False
    intel_eligible_after: bool = False
    evidence_summary: dict[str, Any] = field(default_factory=dict)
    intel_republish_result: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "refreshed_price_tickers":   self.refreshed_price_tickers,
            "failed_price_tickers":      self.failed_price_tickers,
            "analyst_jobs_enqueued":     self.analyst_jobs_enqueued,
            "position_staleness_logged": self.position_staleness_logged,
            "cycle_duration_ms":         self.cycle_duration_ms,
            "deploy_eligible_after":     self.deploy_eligible_after,
            "intel_eligible_after":      self.intel_eligible_after,
            "evidence_summary":          self.evidence_summary,
            "intel_republish_result":    self.intel_republish_result,
        }


class WatchtowerBackgroundRefreshWorker:
    """Background worker that keeps evidence fresh continuously.

    Prioritizes refreshes:
      1. Price/weights (fast, cheap) — refreshed inline via price_refresh_callable
      2. Analyst LLM (slow) — enqueued to existing analyst_refresh_worker_v1
      3. Position data (user input) — logged as stale; cannot auto-refresh
      4. Technical/SEC/news — future support; currently reported as missing

    Duplicate protection: in-progress set prevents concurrent refresh of same
    (user_id, evidence_type). Backoff: price refresh backed off if provider
    fails; analyst refresh backed off by existing job queue backoff.
    """

    def __init__(
        self,
        client: Any,
        *,
        price_refresh_callable: Optional[Callable] = None,
        analyst_job_enqueue_callable: Optional[Callable] = None,
        intel_republish_callable: Optional[Callable] = None,
        max_cycle_seconds: float = DEFAULT_MAX_CYCLE_SECONDS,
        max_price_tickers_per_cycle: int = DEFAULT_MAX_PRICE_TICKERS_PER_CYCLE,
    ):
        self.client = client
        self._price_refresh = price_refresh_callable
        self._analyst_enqueue = analyst_job_enqueue_callable
        self._intel_republish = intel_republish_callable
        self._max_cycle_seconds = max_cycle_seconds
        self._max_price_tickers_per_cycle = max_price_tickers_per_cycle
        self._in_progress: set[tuple[str, str]] = set()  # (user_id_str, evidence_type)

    async def run_refresh_cycle(
        self,
        user_id: UUID,
        *,
        now: Optional[datetime] = None,
    ) -> WatchtowerRefreshCycleResult:
        """Run one Watchtower refresh cycle for a user.

        Steps:
          1. Collect current evidence records.
          2. Build refresh plan (sorted by priority).
          3. Execute urgent refresh (price) if callable is wired.
          4. Enqueue background refresh (analyst) if jobs are due.
          5. Log stale positions (cannot auto-refresh).
          6. Re-assess eligibility.
          7. Emit summary log.
        """
        if now is None:
            now = datetime.now(timezone.utc)

        cycle_start = time.monotonic()
        result = WatchtowerRefreshCycleResult()
        user_key = str(user_id)

        evidence_records = await collect_evidence_records(user_id, self.client, now=now)

        # Count tickers from any record with a ticker
        tickers = list({r.ticker for r in evidence_records if r.ticker})
        total_holdings = len(tickers)

        # Check if any certified snapshot exists
        has_certified = any(
            r.evidence_type == "snapshot" and r.source_quality == "worker_certified"
            for r in evidence_records
        )

        plan = build_watchtower_plan(
            evidence_records,
            total_holdings=total_holdings,
            has_certified_snapshot=has_certified,
        )

        elapsed = time.monotonic() - cycle_start

        # ── Step 3: Urgent refresh — price/weights ─────────────────────────
        price_key = (user_key, EVIDENCE_TYPE_PRICE)
        if (
            EVIDENCE_TYPE_PRICE in plan.stale_by_type
            and price_key not in self._in_progress
            and self._price_refresh is not None
            and elapsed < self._max_cycle_seconds
        ):
            self._in_progress.add(price_key)
            try:
                stale_price_tickers = _stale_tickers_for_type(plan, EVIDENCE_TYPE_PRICE)
                capped = stale_price_tickers[:self._max_price_tickers_per_cycle]
                if capped:
                    logger.info(
                        "watchtower_evidence_updated user_id=%s evidence_type=%s "
                        "action=refresh_started ticker_count=%d tickers=%s",
                        user_id, EVIDENCE_TYPE_PRICE, len(capped), capped[:5],
                    )
                    try:
                        price_result = await self._price_refresh(capped)
                        succeeded = [t for t, v in (price_result or {}).items() if v is not None]
                        failed = [t for t in capped if t not in succeeded]
                        result.refreshed_price_tickers = succeeded
                        result.failed_price_tickers = failed
                        logger.info(
                            "watchtower_evidence_updated user_id=%s evidence_type=%s "
                            "action=refresh_complete succeeded=%d failed=%d",
                            user_id, EVIDENCE_TYPE_PRICE, len(succeeded), len(failed),
                        )
                        # Persist refreshed prices so evidence collector reads fresh evidence.
                        # Only write when at least one ticker succeeded — no point writing
                        # an all-failed snapshot (it just carries forward old values).
                        if price_result and succeeded:
                            try:
                                from .watchtower_price_snapshot_writer_v1 import (
                                    persist_watchtower_price_snapshot,
                                )
                                persist_res = await persist_watchtower_price_snapshot(
                                    user_id,
                                    self.client,
                                    price_results=price_result,
                                    now=now,
                                )
                                if persist_res.persisted:
                                    logger.info(
                                        "watchtower_evidence_updated user_id=%s evidence_type=%s "
                                        "action=snapshot_persisted certified=%d carried=%d",
                                        user_id, EVIDENCE_TYPE_PRICE,
                                        persist_res.certified_ticker_count,
                                        persist_res.carried_ticker_count,
                                    )
                                    # Build 2: evidence-grade certification + publish contract.
                                    # After a durable Watchtower price snapshot is written,
                                    # compare evidence timestamps against the current Intel
                                    # snapshot and trigger a deterministic rebuild if fresh
                                    # evidence postdates the certified snapshot.
                                    # analyst_jobs_queued stays 0 — no LLM calls for price-only refresh.
                                    try:
                                        from .watchtower_intel_republisher_v1 import (
                                            compare_and_republish,
                                        )
                                        republish_res = await compare_and_republish(
                                            user_id,
                                            self.client,
                                            intel_republish_callable=self._intel_republish,
                                        )
                                        result.intel_republish_result = republish_res.to_dict()
                                    except Exception as republish_exc:
                                        logger.warning(
                                            "watchtower_evidence_updated user_id=%s "
                                            "action=intel_republish_error error=%s",
                                            user_id, republish_exc,
                                        )
                                else:
                                    logger.warning(
                                        "watchtower_evidence_updated user_id=%s evidence_type=%s "
                                        "action=snapshot_persist_failed error=%s",
                                        user_id, EVIDENCE_TYPE_PRICE, persist_res.error,
                                    )
                            except Exception as persist_exc:
                                logger.warning(
                                    "watchtower_evidence_updated user_id=%s evidence_type=%s "
                                    "action=snapshot_persist_error error=%s",
                                    user_id, EVIDENCE_TYPE_PRICE, persist_exc,
                                )
                    except Exception as exc:
                        result.failed_price_tickers = capped
                        logger.warning(
                            "watchtower_evidence_updated user_id=%s evidence_type=%s "
                            "action=refresh_failed error=%s",
                            user_id, EVIDENCE_TYPE_PRICE, exc,
                        )
            finally:
                self._in_progress.discard(price_key)

        # ── Step 4: Background refresh — analyst LLM and stale recommendations ──
        # Recommendations expire at the same 8h SLA as the certification contract.
        # Stale recommendations require analyst re-runs just like stale analyst_llm
        # evidence — the analyst worker produces both.  Without this, Watchtower
        # would loop indefinitely with stale research and never re-certify Intel.
        analyst_key = (user_key, EVIDENCE_TYPE_ANALYST_LLM)
        rec_stale = (
            EVIDENCE_TYPE_RECOMMENDATION in plan.stale_by_type
            or EVIDENCE_TYPE_RECOMMENDATION in plan.missing_by_type
        )
        llm_stale = (
            EVIDENCE_TYPE_ANALYST_LLM in plan.stale_by_type
            or EVIDENCE_TYPE_ANALYST_LLM in plan.missing_by_type
        )
        if (
            (llm_stale or rec_stale)
            and analyst_key not in self._in_progress
            and self._analyst_enqueue is not None
            and elapsed < self._max_cycle_seconds
        ):
            # Union tickers from both analyst_llm and recommendation stale records.
            analyst_llm_tickers = set(_stale_tickers_for_type(plan, EVIDENCE_TYPE_ANALYST_LLM))
            rec_tickers = set(_stale_tickers_for_type(plan, EVIDENCE_TYPE_RECOMMENDATION))
            stale_analyst_tickers = list(analyst_llm_tickers | rec_tickers)
            enqueue_reason = _analyst_enqueue_reason(llm_stale=llm_stale, rec_stale=rec_stale)
            if stale_analyst_tickers:
                self._in_progress.add(analyst_key)
                try:
                    logger.info(
                        "watchtower_evidence_updated user_id=%s evidence_type=%s "
                        "action=enqueue_started ticker_count=%d "
                        "analyst_enqueue_reason=%s",
                        user_id, EVIDENCE_TYPE_ANALYST_LLM,
                        len(stale_analyst_tickers), enqueue_reason,
                    )
                    count = await self._analyst_enqueue(user_id, stale_analyst_tickers)
                    result.analyst_jobs_enqueued = count or 0
                    logger.info(
                        "watchtower_evidence_updated user_id=%s evidence_type=%s "
                        "action=enqueue_complete jobs_enqueued=%d "
                        "analyst_enqueue_reason=%s",
                        user_id, EVIDENCE_TYPE_ANALYST_LLM,
                        result.analyst_jobs_enqueued, enqueue_reason,
                    )
                except Exception as exc:
                    logger.warning(
                        "watchtower_evidence_updated user_id=%s evidence_type=%s "
                        "action=enqueue_failed error=%s",
                        user_id, EVIDENCE_TYPE_ANALYST_LLM, exc,
                    )
                finally:
                    self._in_progress.discard(analyst_key)

        # ── Step 5: Log stale positions (informational — cannot auto-refresh) ─
        if EVIDENCE_TYPE_POSITION in plan.stale_by_type:
            stale_pos_count = plan.stale_by_type[EVIDENCE_TYPE_POSITION]
            logger.warning(
                "watchtower_evidence_updated user_id=%s evidence_type=%s "
                "action=staleness_detected count=%d "
                "note=position_data_requires_user_update",
                user_id, EVIDENCE_TYPE_POSITION, stale_pos_count,
            )
            result.position_staleness_logged = True

        # ── Step 6: Re-assess eligibility after refresh ────────────────────
        # Simple check from plan — a follow-up collect would be more accurate
        # but is not worth the extra DB round trip for this summary.
        # (The next fast gate call will re-assess from fresh DB state.)
        result.deploy_eligible_after = plan.deploy_eligible
        result.intel_eligible_after = plan.intel_eligible

        cycle_ms = int((time.monotonic() - cycle_start) * 1000)
        result.cycle_duration_ms = cycle_ms
        result.evidence_summary = {
            "fresh_types": list(plan.fresh_by_type.keys()),
            "stale_types": list(plan.stale_by_type.keys()),
            "missing_types": list(plan.missing_by_type.keys()),
        }

        logger.info(
            "watchtower_refresh_worker_summary user_id=%s "
            "refreshed_price_count=%d failed_price_count=%d "
            "analyst_jobs_enqueued=%d position_staleness_logged=%s "
            "cycle_duration_ms=%d deploy_eligible=%s intel_eligible=%s "
            "stale_types=%s missing_types=%s",
            user_id,
            len(result.refreshed_price_tickers),
            len(result.failed_price_tickers),
            result.analyst_jobs_enqueued,
            result.position_staleness_logged,
            cycle_ms,
            result.deploy_eligible_after,
            result.intel_eligible_after,
            ",".join(sorted(plan.stale_by_type.keys())) or "none",
            ",".join(sorted(plan.missing_by_type.keys())) or "none",
        )

        return result


def _stale_tickers_for_type(
    plan: WatchtowerRefreshPlan,
    evidence_type: str,
) -> list[str]:
    """Extract stale/missing tickers for a specific evidence type from the plan."""
    tickers = []
    for job in plan.refresh_jobs:
        if job.evidence_type == evidence_type:
            tickers.extend(job.tickers)
    return list(set(tickers))


def _analyst_enqueue_reason(*, llm_stale: bool, rec_stale: bool) -> str:
    """Return a structured log value describing why analyst jobs are being enqueued."""
    if llm_stale and rec_stale:
        return "analyst_llm_and_recommendation_stale"
    if rec_stale:
        return "recommendation_stale"
    return "analyst_llm_stale"


# ── Runnable entry point (consistent with analyst_refresh_worker_entrypoint) ──

async def run_watchtower_cycle_for_user(
    user_id: UUID,
    client: Any,
    *,
    price_refresh_callable: Optional[Callable] = None,
    analyst_job_enqueue_callable: Optional[Callable] = None,
    intel_republish_callable: Optional[Callable] = None,
    now: Optional[datetime] = None,
) -> WatchtowerRefreshCycleResult:
    """Convenience entry point: run one Watchtower cycle for a single user."""
    worker = WatchtowerBackgroundRefreshWorker(
        client=client,
        price_refresh_callable=price_refresh_callable,
        analyst_job_enqueue_callable=analyst_job_enqueue_callable,
        intel_republish_callable=intel_republish_callable,
    )
    return await worker.run_refresh_cycle(user_id, now=now)


def emit_watchtower_freshness_summary(
    user_id: UUID,
    plan: WatchtowerRefreshPlan,
) -> None:
    """Emit the canonical watchtower_freshness_summary log. Called by gate/planner."""
    logger.info(
        "watchtower_freshness_summary user_id=%s "
        "total_holdings=%d "
        "fresh_by_type=%s stale_by_type=%s missing_by_type=%s "
        "deploy_eligible=%s deploy_blockers=%s "
        "intel_eligible=%s intel_blockers=%s "
        "urgent_refresh_count=%d background_refresh_count=%d "
        "estimated_refresh_class=%s "
        "safe_latest_snapshot_available=%s",
        user_id,
        plan.total_holdings,
        {k: v for k, v in plan.fresh_by_type.items()},
        {k: v for k, v in plan.stale_by_type.items()},
        {k: v for k, v in plan.missing_by_type.items()},
        plan.deploy_eligible,
        ",".join(plan.deploy_blockers) or "none",
        plan.intel_eligible,
        ",".join(plan.intel_blockers) or "none",
        plan.urgent_refresh_count,
        plan.background_refresh_count,
        plan.estimated_refresh_class,
        plan.safe_latest_snapshot_available,
    )


def emit_watchtower_deploy_gate_summary(
    user_id: UUID,
    gate_result: Any,  # DeployGateResult
) -> None:
    """Emit the canonical watchtower_deploy_gate_summary log."""
    logger.info(
        "watchtower_deploy_gate_summary user_id=%s "
        "status=%s deploy_eligible=%s blockers=%s "
        "critical_evidence_fresh=%s analyst_llm_stale=%s "
        "summary=%r",
        user_id,
        gate_result.status,
        gate_result.deploy_eligible,
        ",".join(gate_result.blockers) or "none",
        gate_result.critical_evidence_fresh,
        gate_result.analyst_llm_stale,
        gate_result.summary,
    )
