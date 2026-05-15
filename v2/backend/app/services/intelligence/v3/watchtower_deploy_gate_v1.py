"""Watchtower Deploy Gate v1 (Build 1D).

Backend contract for Deploy safety groundwork.

Even before full Deploy is implemented, this module enforces the contract:
  - Dollar deployment plans require FRESH or AGING evidence for all
    deploy-critical types (price, position, portfolio_weight).
  - LLM analyst text can explain but cannot override freshness blocks.
  - No deploy-actionable output when critical evidence is stale/missing.
  - Returns a structured DeployGateResult so callers can log + surface honestly.

Pure module — no IO. Input: list[EvidenceRecord]. Output: DeployGateResult.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .watchtower_freshness_ledger_v1 import (
    DEPLOY_CRITICAL_TYPES,
    EVIDENCE_TYPE_ANALYST_LLM,
    FRESHNESS_FRESH,
    FRESHNESS_AGING,
    EvidenceRecord,
)


# ── Deploy gate statuses ──────────────────────────────────────────────────────

DEPLOY_GATE_ELIGIBLE = "deploy_eligible"
DEPLOY_GATE_BLOCKED = "deploy_blocked"


@dataclass
class DeployGateResult:
    """Result of the Watchtower deploy gate check."""
    status: str                         # deploy_eligible | deploy_blocked
    deploy_eligible: bool
    blockers: list[str]                 # evidence_types blocking deploy
    blocker_details: list[dict[str, Any]]  # per-record details
    critical_evidence_fresh: bool       # all deploy-critical types are fresh/aging
    analyst_llm_stale: bool             # analyst LLM is stale (informational only)
    summary: str                        # human-readable summary

    def to_dict(self) -> dict[str, Any]:
        return {
            "status":                   self.status,
            "deploy_eligible":          self.deploy_eligible,
            "blockers":                 self.blockers,
            "blocker_details":          self.blocker_details,
            "critical_evidence_fresh":  self.critical_evidence_fresh,
            "analyst_llm_stale":        self.analyst_llm_stale,
            "summary":                  self.summary,
        }


def check_deploy_gate(evidence_records: list[EvidenceRecord]) -> DeployGateResult:
    """Evaluate deploy eligibility from evidence records.

    Rules enforced:
      1. Each deploy-critical type (price, position, portfolio_weight) must be
         FRESH or AGING. STALE, MISSING, or FAILED → deploy_blocked.
      2. Analyst LLM staleness does NOT block deploy — it is informational.
         LLM can explain but cannot grant or revoke deploy authority.
      3. Non-critical types (technical, SEC, news, fundamental) that are MISSING
         because they are not yet collected by this app do not block deploy.
         This matches the per-type deploy_eligible flag on each record.
      4. The gate is strict: any single deploy-critical blocker → deploy_blocked.

    This is the deterministic backend gate. No LLM output overrides it.
    """
    blockers: list[str] = []
    blocker_details: list[dict[str, Any]] = []
    analyst_llm_stale = False

    # Check each record against its deploy_eligible flag
    for rec in evidence_records:
        if rec.evidence_type == EVIDENCE_TYPE_ANALYST_LLM:
            if rec.freshness_status not in (FRESHNESS_FRESH, FRESHNESS_AGING):
                analyst_llm_stale = True
            # Analyst LLM does NOT block deploy — skip to next record
            continue

        # For deploy-critical types, check eligibility
        if not rec.deploy_eligible and rec.evidence_type in DEPLOY_CRITICAL_TYPES:
            if rec.evidence_type not in blockers:
                blockers.append(rec.evidence_type)
            blocker_details.append({
                "evidence_type":   rec.evidence_type,
                "ticker":          rec.ticker,
                "scope":           rec.scope,
                "freshness_status": rec.freshness_status,
                "reason":          rec.reason or f"{rec.evidence_type} is {rec.freshness_status}",
            })

    deploy_eligible = len(blockers) == 0
    critical_fresh = deploy_eligible

    if deploy_eligible:
        summary = "All critical evidence is fresh — deploy conditions met."
        if analyst_llm_stale:
            summary += " Analyst explanation is stale (informational — does not block deploy)."
        status = DEPLOY_GATE_ELIGIBLE
    else:
        types_str = ", ".join(sorted(set(blockers)))
        summary = f"Deploy blocked: {types_str} evidence is stale or missing."
        status = DEPLOY_GATE_BLOCKED

    return DeployGateResult(
        status=status,
        deploy_eligible=deploy_eligible,
        blockers=list(set(blockers)),
        blocker_details=blocker_details,
        critical_evidence_fresh=critical_fresh,
        analyst_llm_stale=analyst_llm_stale,
        summary=summary,
    )
