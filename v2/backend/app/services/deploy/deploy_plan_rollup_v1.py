"""Deploy Stage 2.3E — plan-level readiness rollup v1.

Pure functions only. No IO, no LLM, no DB, no broker, no UI, no API.

Aggregates a list of finalized DeployPlanItems (after exact-dollar math, cash
guardrail, finalization, and pending-reason logic) into a deterministic
plan-level summary suitable for a future plain-English UI / API to render
without re-implementing the inference logic.

Invariants:
  - Items are never mutated by rollup.
  - Item-level intel_action, actionability_status, recommended_dollar_amount,
    cash_constraint_status, final_actionability_status, and
    pending_guardrails_reason are never read for derivation in any way that
    would change item-level state.
  - Unknown / missing / unexpected item fields fall into unknown / not_ready
    rollup buckets — never fabricated as readiness.
  - PriceBand, tax/wash-sale guardrail status, and Intel authority are not
    re-evaluated here.

Plan-level readiness ladder (deterministic, evaluated in order):
  1. NO_ITEMS            — total == 0.
  2. ALL_INFORMATIONAL   — every item is informational_hold (HOLD-only plan).
  3. ALL_SUPPRESSED      — every item is suppressed.
  4. READY_PENDING_GUARDRAILS — at least one pending-tax item and zero
                                blocked / not_ready / unknown items
                                (informational_hold and suppressed allowed).
  5. PARTIALLY_READY     — at least one pending-tax item, but other items
                           include blocked / not_ready / unknown.
  6. BLOCKED             — zero pending-tax items, at least one blocked_cash.
  7. NOT_READY           — default fail-safe (e.g., scaffold-only, all
                           not_ready, unknown final statuses, etc.).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from .deploy_contracts import DeployPlanItem
from .deploy_finalization_v1 import (
    FINAL_ACTIONABLE_PENDING_TAX,
    FINAL_BLOCKED_CASH,
    FINAL_INFORMATIONAL_HOLD,
    FINAL_NOT_READY,
    FINAL_SUPPRESSED,
    PENDING_REASON_NONE,
    PENDING_REASON_TAX_WASH_NOT_EVALUATED,
)

# Canonical plan-level readiness status string literals.
ROLLUP_NO_ITEMS = "no_items"
ROLLUP_ALL_INFORMATIONAL = "all_informational"
ROLLUP_ALL_SUPPRESSED = "all_suppressed"
ROLLUP_READY_PENDING_GUARDRAILS = "ready_pending_guardrails"
ROLLUP_PARTIALLY_READY = "partially_ready"
ROLLUP_BLOCKED = "blocked"
ROLLUP_NOT_READY = "not_ready"

# Bucket key used when a final_actionability_status or pending_guardrails_reason
# value is missing or not one of the canonical literals (fail-safe).
ROLLUP_UNKNOWN_BUCKET = "unknown"

_KNOWN_FINAL_STATUSES = (
    FINAL_ACTIONABLE_PENDING_TAX,
    FINAL_BLOCKED_CASH,
    FINAL_INFORMATIONAL_HOLD,
    FINAL_SUPPRESSED,
    FINAL_NOT_READY,
)

_KNOWN_PENDING_REASONS = (
    PENDING_REASON_TAX_WASH_NOT_EVALUATED,
    PENDING_REASON_NONE,
)


@dataclass
class DeployPlanRollup:
    """Deterministic plan-level readiness summary for one DeployPlan.

    Backend-only contract. Intended to feed a future plain-English UI / API
    without duplicating per-item inference logic.
    """
    total_items: int

    # Counts keyed by canonical final_actionability_status string + "unknown".
    counts_by_final_actionability_status: Dict[str, int] = field(default_factory=dict)

    # Counts keyed by canonical pending_guardrails_reason string + "unknown".
    counts_by_pending_guardrails_reason: Dict[str, int] = field(default_factory=dict)

    # Convenience totals (also derivable from counts_by_final_actionability_status).
    # actionable_count is reserved for a future fully-actionable final status; it
    # is always zero today because tax/wash-sale guardrails are not yet wired.
    actionable_count: int = 0
    pending_count: int = 0
    blocked_count: int = 0
    informational_count: int = 0
    suppressed_count: int = 0
    not_ready_count: int = 0
    unknown_count: int = 0

    # Plan-level readiness label suitable for plain-English UI.
    plan_readiness_status: str = ROLLUP_NO_ITEMS

    schema_version: str = "deploy_v1_scaffold"


def _empty_status_counts() -> Dict[str, int]:
    counts = {status: 0 for status in _KNOWN_FINAL_STATUSES}
    counts[ROLLUP_UNKNOWN_BUCKET] = 0
    return counts


def _empty_reason_counts() -> Dict[str, int]:
    counts = {reason: 0 for reason in _KNOWN_PENDING_REASONS}
    counts[ROLLUP_UNKNOWN_BUCKET] = 0
    return counts


def _classify_status_bucket(value: object) -> str:
    if isinstance(value, str) and value in _KNOWN_FINAL_STATUSES:
        return value
    return ROLLUP_UNKNOWN_BUCKET


def _classify_reason_bucket(value: object) -> str:
    if isinstance(value, str) and value in _KNOWN_PENDING_REASONS:
        return value
    return ROLLUP_UNKNOWN_BUCKET


def _derive_plan_readiness_status(
    total: int,
    pending: int,
    blocked: int,
    informational: int,
    suppressed: int,
    not_ready: int,
    unknown: int,
) -> str:
    """Apply the deterministic readiness ladder.

    Today there is no fully-actionable final status, so "actionable" count is
    always zero. The ladder treats pending-tax items as the highest readiness
    achievable, which matches the honest contract (tax/wash not yet evaluated).
    """
    if total == 0:
        return ROLLUP_NO_ITEMS

    # ALL_INFORMATIONAL — every item is informational_hold.
    if informational == total:
        return ROLLUP_ALL_INFORMATIONAL

    # ALL_SUPPRESSED — every item is suppressed.
    if suppressed == total:
        return ROLLUP_ALL_SUPPRESSED

    if pending > 0:
        # READY_PENDING_GUARDRAILS — at least one pending; no blocked, not_ready,
        # or unknown items; only informational_hold and suppressed allowed
        # alongside pending items.
        if blocked == 0 and not_ready == 0 and unknown == 0:
            return ROLLUP_READY_PENDING_GUARDRAILS
        return ROLLUP_PARTIALLY_READY

    # No pending items — fall back to blocked vs not-ready signals.
    if blocked > 0:
        return ROLLUP_BLOCKED

    return ROLLUP_NOT_READY


def build_plan_rollup(items: List[DeployPlanItem]) -> DeployPlanRollup:
    """Aggregate finalized DeployPlanItems into a deterministic plan rollup.

    Reads only item-level final_actionability_status and
    pending_guardrails_reason. Does not mutate items. Unknown / missing values
    fall into the "unknown" bucket and contribute to fail-safe readiness.
    """
    status_counts = _empty_status_counts()
    reason_counts = _empty_reason_counts()

    for item in items:
        status_bucket = _classify_status_bucket(getattr(item, "final_actionability_status", None))
        status_counts[status_bucket] += 1

        reason_bucket = _classify_reason_bucket(getattr(item, "pending_guardrails_reason", None))
        reason_counts[reason_bucket] += 1

    pending = status_counts[FINAL_ACTIONABLE_PENDING_TAX]
    blocked = status_counts[FINAL_BLOCKED_CASH]
    informational = status_counts[FINAL_INFORMATIONAL_HOLD]
    suppressed = status_counts[FINAL_SUPPRESSED]
    not_ready = status_counts[FINAL_NOT_READY]
    unknown = status_counts[ROLLUP_UNKNOWN_BUCKET]
    total = len(items)

    plan_readiness_status = _derive_plan_readiness_status(
        total=total,
        pending=pending,
        blocked=blocked,
        informational=informational,
        suppressed=suppressed,
        not_ready=not_ready,
        unknown=unknown,
    )

    return DeployPlanRollup(
        total_items=total,
        counts_by_final_actionability_status=status_counts,
        counts_by_pending_guardrails_reason=reason_counts,
        actionable_count=0,  # Reserved; no fully-actionable final status today.
        pending_count=pending,
        blocked_count=blocked,
        informational_count=informational,
        suppressed_count=suppressed,
        not_ready_count=not_ready,
        unknown_count=unknown,
        plan_readiness_status=plan_readiness_status,
        schema_version="deploy_v1_scaffold",
    )
