"""Intel v3 click-time Fast Freshness Gate v1 (Build 1D).

This is the gate that the Run Intel button calls at click time.

Design principle:
  - Click time performs fast freshness validation only.
  - Click time does NOT run a full 34-ticker IO/research bundle.
  - Click time enqueues/marks Watchtower refresh work for stale slices.
  - Click time returns the latest certified snapshot immediately if available.

The gate:
  1. Reads existing DB tables (portfolio_snapshots, positions, recommendations,
     agent_insights, intel_v3_snapshots) — all fast indexed queries.
  2. Builds EvidenceRecords from those reads.
  3. Runs the Watchtower planner to classify fresh/stale/missing.
  4. Runs the deploy gate.
  5. Returns FastFreshnessGateResult — structured, loggable.

Target: sub-200ms. No provider calls, no LLM calls.

intel_status values:
  current_enough_for_intel         — certified snapshot exists and is fresh
  refresh_running                  — worker is processing, snapshot pending
  blocked_missing_critical_evidence — no certified snapshot and critical evidence missing
  stale_evidence_enqueued          — stale slices enqueued, latest snapshot still shown

Emits structured log: intel_v3_fast_freshness_gate_summary
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from .watchtower_evidence_collector_v1 import collect_evidence_records
from .watchtower_freshness_ledger_v1 import (
    EVIDENCE_TYPE_SNAPSHOT,
    FRESHNESS_FRESH,
    FRESHNESS_AGING,
    EvidenceRecord,
)
from .watchtower_refresh_planner_v1 import WatchtowerRefreshPlan, build_watchtower_plan
from .watchtower_deploy_gate_v1 import DeployGateResult, check_deploy_gate

logger = logging.getLogger(__name__)


# ── Intel status values ───────────────────────────────────────────────────────

INTEL_STATUS_CURRENT = "current_enough_for_intel"
INTEL_STATUS_REFRESH_RUNNING = "refresh_running"
INTEL_STATUS_BLOCKED_MISSING = "blocked_missing_critical_evidence"
INTEL_STATUS_STALE_ENQUEUED = "stale_evidence_enqueued"


@dataclass
class FastFreshnessGateResult:
    """Structured result of the click-time fast freshness gate."""
    intel_status: str
    deploy_status: str                      # deploy_eligible | deploy_blocked
    deploy_blockers: list[str]
    latest_certified_snapshot_available: bool
    latest_certified_snapshot_id: Optional[str]
    evidence_records: list[EvidenceRecord]
    refresh_plan: WatchtowerRefreshPlan
    deploy_gate: DeployGateResult
    gate_check_ms: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "intel_status":                     self.intel_status,
            "deploy_status":                    self.deploy_status,
            "deploy_blockers":                  self.deploy_blockers,
            "latest_certified_snapshot_available": self.latest_certified_snapshot_available,
            "latest_certified_snapshot_id":     self.latest_certified_snapshot_id,
            "refresh_plan":                     self.refresh_plan.to_dict(),
            "deploy_gate":                      self.deploy_gate.to_dict(),
            "gate_check_ms":                    self.gate_check_ms,
        }


async def run_fast_freshness_gate(
    user_id: UUID,
    client: Any,
    *,
    now: Optional[datetime] = None,
    existing_certified_snapshot_id: Optional[str] = None,
    has_pending_worker_jobs: bool = False,
    total_holdings: int = 0,
) -> FastFreshnessGateResult:
    """Run the click-time fast freshness gate.

    Reads existing DB tables, classifies evidence freshness, evaluates
    deploy gate, and determines intel_status.

    Does NOT:
      - Call any providers.
      - Run any LLM work.
      - Run a full 34-ticker refresh.
      - Mark stale evidence as current.

    Args:
        user_id: the user's UUID.
        client: Supabase client.
        now: current UTC time (injectable for tests).
        existing_certified_snapshot_id: snapshot ID if a certified snapshot exists.
        has_pending_worker_jobs: True if analyst refresh jobs are pending in worker.
        total_holdings: total number of active holdings.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    t0 = time.monotonic()

    evidence_records = await collect_evidence_records(user_id, client, now=now)

    has_certified = existing_certified_snapshot_id is not None

    refresh_plan = build_watchtower_plan(
        evidence_records,
        total_holdings=total_holdings,
        has_certified_snapshot=has_certified,
    )

    deploy_gate = check_deploy_gate(evidence_records)

    # Determine intel_status
    # Check if snapshot evidence is fresh enough to show as current
    snapshot_records = [r for r in evidence_records if r.evidence_type == EVIDENCE_TYPE_SNAPSHOT]
    snapshot_is_fresh = any(
        r.freshness_status in (FRESHNESS_FRESH, FRESHNESS_AGING)
        for r in snapshot_records
    )

    if has_certified and snapshot_is_fresh and not has_pending_worker_jobs:
        intel_status = INTEL_STATUS_CURRENT
    elif has_pending_worker_jobs:
        intel_status = INTEL_STATUS_REFRESH_RUNNING
    elif has_certified and refresh_plan.urgent_refresh_count > 0:
        intel_status = INTEL_STATUS_STALE_ENQUEUED
    elif has_certified:
        intel_status = INTEL_STATUS_CURRENT
    else:
        # No certified snapshot and either blocked or stale
        if refresh_plan.intel_blockers:
            intel_status = INTEL_STATUS_BLOCKED_MISSING
        else:
            intel_status = INTEL_STATUS_REFRESH_RUNNING

    gate_check_ms = int((time.monotonic() - t0) * 1000)

    result = FastFreshnessGateResult(
        intel_status=intel_status,
        deploy_status=deploy_gate.status,
        deploy_blockers=deploy_gate.blockers,
        latest_certified_snapshot_available=has_certified,
        latest_certified_snapshot_id=existing_certified_snapshot_id,
        evidence_records=evidence_records,
        refresh_plan=refresh_plan,
        deploy_gate=deploy_gate,
        gate_check_ms=gate_check_ms,
    )

    _emit_gate_log(user_id, result, refresh_plan, deploy_gate)
    return result


def _emit_gate_log(
    user_id: UUID,
    result: FastFreshnessGateResult,
    plan: WatchtowerRefreshPlan,
    deploy_gate: DeployGateResult,
) -> None:
    logger.info(
        "intel_v3_fast_freshness_gate_summary user_id=%s "
        "intel_status=%s deploy_status=%s "
        "deploy_eligible=%s deploy_blockers=%s "
        "latest_certified_snapshot_available=%s "
        "total_holdings=%d "
        "fresh_types=%s stale_types=%s missing_types=%s "
        "urgent_refresh_count=%d background_refresh_count=%d "
        "estimated_refresh_class=%s "
        "analyst_llm_stale=%s "
        "gate_check_ms=%d",
        user_id,
        result.intel_status,
        result.deploy_status,
        deploy_gate.deploy_eligible,
        ",".join(deploy_gate.blockers) or "none",
        result.latest_certified_snapshot_available,
        plan.total_holdings,
        ",".join(plan.fresh_by_type.keys()) or "none",
        ",".join(plan.stale_by_type.keys()) or "none",
        ",".join(plan.missing_by_type.keys()) or "none",
        plan.urgent_refresh_count,
        plan.background_refresh_count,
        plan.estimated_refresh_class,
        deploy_gate.analyst_llm_stale,
        result.gate_check_ms,
    )
