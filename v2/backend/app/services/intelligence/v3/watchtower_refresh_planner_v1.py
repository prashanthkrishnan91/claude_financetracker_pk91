"""Watchtower Refresh Planner v1 (Build 1D).

Answers:
  - What evidence is stale/missing now?
  - Which refresh jobs should run next?
  - Which evidence can be refreshed cheaply (price/weights) vs slowly (analyst LLM)?
  - What blocks Deploy?
  - What blocks Intel certification?
  - Is it safe to show the latest certified snapshot?

Pure module — no IO, no DB, no LLM calls.
Input: list[EvidenceRecord]
Output: WatchtowerRefreshPlan (structured, loggable)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .watchtower_freshness_ledger_v1 import (
    DEPLOY_CRITICAL_TYPES,
    DECISION_CRITICAL_TYPES,
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
    FRESHNESS_FAILED,
    EvidenceRecord,
)


# ── Refresh priority classes ──────────────────────────────────────────────────

PRIORITY_URGENT = "urgent"      # Price, weights — fast, cheap, run immediately
PRIORITY_NORMAL = "normal"      # Technicals, recommendations
PRIORITY_BACKGROUND = "background"  # Analyst LLM, SEC/filing, news/sentiment

# Which types map to which priority when stale/missing
_REFRESH_PRIORITY_MAP: dict[str, str] = {
    EVIDENCE_TYPE_PRICE:            PRIORITY_URGENT,
    EVIDENCE_TYPE_PORTFOLIO_WEIGHT: PRIORITY_URGENT,
    EVIDENCE_TYPE_POSITION:         PRIORITY_URGENT,
    EVIDENCE_TYPE_TECHNICAL:        PRIORITY_NORMAL,
    EVIDENCE_TYPE_RECOMMENDATION:   PRIORITY_NORMAL,
    EVIDENCE_TYPE_ANALYST_LLM:      PRIORITY_BACKGROUND,
    EVIDENCE_TYPE_FUNDAMENTAL:      PRIORITY_BACKGROUND,
    EVIDENCE_TYPE_SEC_FILING:       PRIORITY_BACKGROUND,
    EVIDENCE_TYPE_NEWS_SENTIMENT:   PRIORITY_BACKGROUND,
    EVIDENCE_TYPE_SNAPSHOT:         PRIORITY_NORMAL,
}

# Estimated duration class per refresh type
_DURATION_CLASS_MAP: dict[str, str] = {
    EVIDENCE_TYPE_PRICE:            "fast",
    EVIDENCE_TYPE_PORTFOLIO_WEIGHT: "fast",
    EVIDENCE_TYPE_POSITION:         "fast",
    EVIDENCE_TYPE_TECHNICAL:        "fast",
    EVIDENCE_TYPE_RECOMMENDATION:   "normal",
    EVIDENCE_TYPE_ANALYST_LLM:      "slow",
    EVIDENCE_TYPE_FUNDAMENTAL:      "slow",
    EVIDENCE_TYPE_SEC_FILING:       "slow",
    EVIDENCE_TYPE_NEWS_SENTIMENT:   "normal",
    EVIDENCE_TYPE_SNAPSHOT:         "fast",
}

_STALE_OR_MISSING = frozenset({FRESHNESS_STALE, FRESHNESS_MISSING, FRESHNESS_FAILED})


# ── Output types ──────────────────────────────────────────────────────────────

@dataclass
class RefreshJobSpec:
    """A single refresh task identified by the planner."""
    evidence_type: str
    tickers: list[str]             # empty = portfolio-level
    priority: str                  # urgent | normal | background
    reason: str                    # human-readable reason
    estimated_duration_class: str  # fast | normal | slow

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_type":          self.evidence_type,
            "tickers":                self.tickers,
            "priority":               self.priority,
            "reason":                 self.reason,
            "estimated_duration_class": self.estimated_duration_class,
        }


@dataclass
class WatchtowerRefreshPlan:
    """Structured output of the Watchtower refresh planner.

    Covers: per-type freshness summary, deploy/intel blockers, refresh job list,
    overall eligibility, and whether a safe certified snapshot is already available.
    """
    total_holdings: int
    fresh_by_type: dict[str, int]       # evidence_type → count fresh (FRESH or AGING)
    stale_by_type: dict[str, int]       # evidence_type → count stale
    missing_by_type: dict[str, int]     # evidence_type → count missing/failed
    deploy_blockers: list[str]          # evidence_types blocking deploy
    intel_blockers: list[str]           # evidence_types blocking Intel cert
    refresh_jobs: list[RefreshJobSpec]  # sorted by priority
    urgent_refresh_count: int
    background_refresh_count: int
    estimated_refresh_class: str        # fast | normal | slow
    safe_latest_snapshot_available: bool
    deploy_eligible: bool
    intel_eligible: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_holdings":             self.total_holdings,
            "fresh_by_type":              self.fresh_by_type,
            "stale_by_type":              self.stale_by_type,
            "missing_by_type":            self.missing_by_type,
            "deploy_blockers":            self.deploy_blockers,
            "intel_blockers":             self.intel_blockers,
            "refresh_jobs":               [j.to_dict() for j in self.refresh_jobs],
            "urgent_refresh_count":       self.urgent_refresh_count,
            "background_refresh_count":   self.background_refresh_count,
            "estimated_refresh_class":    self.estimated_refresh_class,
            "safe_latest_snapshot_available": self.safe_latest_snapshot_available,
            "deploy_eligible":            self.deploy_eligible,
            "intel_eligible":             self.intel_eligible,
        }


# ── Planner ───────────────────────────────────────────────────────────────────

def build_watchtower_plan(
    evidence_records: list[EvidenceRecord],
    *,
    total_holdings: int,
    has_certified_snapshot: bool,
) -> WatchtowerRefreshPlan:
    """Build a structured refresh plan from a list of EvidenceRecords.

    Groups records by evidence_type. For each type, counts fresh/stale/missing
    records and determines whether refresh jobs should be enqueued.

    Planner logic:
      - Any deploy-critical type that is stale/missing → deploy_blocked.
      - Any decision-critical type that is stale/missing → intel_blocked.
      - Each stale/missing type gets a RefreshJobSpec at the appropriate priority.
      - urgent = price/position/weight; background = LLM/SEC/news; normal = others.
      - estimated_refresh_class: if any background job → slow; elif any normal → normal; else fast.
    """
    fresh_by_type: dict[str, int] = {}
    stale_by_type: dict[str, int] = {}
    missing_by_type: dict[str, int] = {}
    deploy_blockers: list[str] = []
    intel_blockers: list[str] = []
    stale_tickers_by_type: dict[str, list[str]] = {}

    # Group by evidence_type
    by_type: dict[str, list[EvidenceRecord]] = {}
    for rec in evidence_records:
        by_type.setdefault(rec.evidence_type, []).append(rec)

    for etype, recs in by_type.items():
        fresh_count = sum(1 for r in recs if r.freshness_status in (FRESHNESS_FRESH, FRESHNESS_AGING))
        stale_count = sum(1 for r in recs if r.freshness_status == FRESHNESS_STALE)
        missing_count = sum(1 for r in recs if r.freshness_status in (FRESHNESS_MISSING, FRESHNESS_FAILED))

        if fresh_count:
            fresh_by_type[etype] = fresh_count
        if stale_count:
            stale_by_type[etype] = stale_count
        if missing_count:
            missing_by_type[etype] = missing_count

        # Collect stale/missing tickers for job spec
        stale_tickers = [
            r.ticker for r in recs
            if r.freshness_status in _STALE_OR_MISSING and r.ticker
        ]
        if stale_tickers:
            stale_tickers_by_type[etype] = stale_tickers

        # Check blocker status
        has_stale_or_missing = (stale_count + missing_count) > 0
        if has_stale_or_missing and etype in DEPLOY_CRITICAL_TYPES:
            deploy_blockers.append(etype)
        if has_stale_or_missing and etype in DECISION_CRITICAL_TYPES:
            intel_blockers.append(etype)

    # Build refresh jobs for each stale/missing type
    refresh_jobs: list[RefreshJobSpec] = []
    for etype, tickers in stale_tickers_by_type.items():
        count = len(tickers)
        priority = _REFRESH_PRIORITY_MAP.get(etype, PRIORITY_BACKGROUND)
        duration_class = _DURATION_CLASS_MAP.get(etype, "slow")
        status = stale_by_type.get(etype, 0) and "stale" or "missing"
        reason = f"{count} {etype} record(s) are {status}"
        refresh_jobs.append(RefreshJobSpec(
            evidence_type=etype,
            tickers=tickers,
            priority=priority,
            reason=reason,
            estimated_duration_class=duration_class,
        ))

    # Also add portfolio-level jobs for types with only portfolio scope (no ticker)
    for etype, recs in by_type.items():
        portfolio_stale = [r for r in recs if r.scope == "portfolio" and r.freshness_status in _STALE_OR_MISSING]
        if portfolio_stale and etype not in stale_tickers_by_type:
            priority = _REFRESH_PRIORITY_MAP.get(etype, PRIORITY_BACKGROUND)
            duration_class = _DURATION_CLASS_MAP.get(etype, "slow")
            refresh_jobs.append(RefreshJobSpec(
                evidence_type=etype,
                tickers=[],
                priority=priority,
                reason=f"portfolio-level {etype} is stale/missing",
                estimated_duration_class=duration_class,
            ))

    # Sort: urgent first, then normal, then background
    _priority_order = {PRIORITY_URGENT: 0, PRIORITY_NORMAL: 1, PRIORITY_BACKGROUND: 2}
    refresh_jobs.sort(key=lambda j: _priority_order.get(j.priority, 3))

    urgent_count = sum(1 for j in refresh_jobs if j.priority == PRIORITY_URGENT)
    background_count = sum(1 for j in refresh_jobs if j.priority == PRIORITY_BACKGROUND)

    # Estimated class: worst-case across all jobs
    if any(j.estimated_duration_class == "slow" for j in refresh_jobs):
        est_class = "slow"
    elif any(j.estimated_duration_class == "normal" for j in refresh_jobs):
        est_class = "normal"
    elif refresh_jobs:
        est_class = "fast"
    else:
        est_class = "fast"  # nothing to refresh

    return WatchtowerRefreshPlan(
        total_holdings=total_holdings,
        fresh_by_type=fresh_by_type,
        stale_by_type=stale_by_type,
        missing_by_type=missing_by_type,
        deploy_blockers=deploy_blockers,
        intel_blockers=intel_blockers,
        refresh_jobs=refresh_jobs,
        urgent_refresh_count=urgent_count,
        background_refresh_count=background_count,
        estimated_refresh_class=est_class,
        safe_latest_snapshot_available=has_certified_snapshot,
        deploy_eligible=len(deploy_blockers) == 0,
        intel_eligible=len(intel_blockers) == 0,
    )
