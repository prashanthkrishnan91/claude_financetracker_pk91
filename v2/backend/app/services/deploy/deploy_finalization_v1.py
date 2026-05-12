"""Deploy Stage 2.3C — per-item final actionability finalization v1.

Pure functions only. No IO, no LLM, no DB, no broker.

Derives a final per-item actionability status from existing DeployPlanItem
fields AFTER exact-dollar math and cash guardrail have run.  Intended to
give the future Deploy UI a single unambiguous field rather than forcing it
to infer from raw intermediate fields.

Finalization model:
  HOLD (NOT_ACTIONABLE_HOLD):
    → INFORMATIONAL_HOLD

  Suppressed (any SUPPRESSED_* status):
    → SUPPRESSED

  ACTIONABLE_CANDIDATE without recommended_dollar_amount:
    → cash blocked? BLOCKED_CASH (primary reason)
    → otherwise    NOT_READY (dollar math not run / suppressed)

  ACTIONABLE_CANDIDATE BUY with recommended_dollar_amount:
    → cash_constraint_status == "passed"          → ACTIONABLE_PENDING_TAX
    → cash blocked or uncertified or not-evaluated → BLOCKED_CASH

  ACTIONABLE_CANDIDATE TRIM/SELL with recommended_dollar_amount:
    → cash_constraint_status == "not_applicable_trim_sell" → ACTIONABLE_PENDING_TAX
    → unexpected cash status                               → NOT_READY

  All other ACTIONABLE_CANDIDATE cases:
    → NOT_READY

Tax / wash-sale guardrail statuses are "not_evaluated_yet" placeholders.
This is represented honestly: items with dollars + cash clear land in
ACTIONABLE_PENDING_TAX, not ACTIONABLE.  ACTIONABLE is reserved for a future
slice when tax / wash-sale guardrails are implemented.

Invariants:
  - intel_action is never changed.
  - actionability_status is never changed.
  - recommended_dollar_amount is never changed.
  - cash_constraint_status is never changed.
  - tax_guardrail_status and wash_sale_guardrail_status are never changed.
"""
from __future__ import annotations

from dataclasses import replace
from typing import List

from .deploy_contracts import DeployActionabilityStatus, DeployPlanItem

# Canonical final_actionability_status string literals.
FINAL_ACTIONABLE_PENDING_TAX = "actionable_pending_tax"
FINAL_BLOCKED_CASH = "blocked_cash"
FINAL_INFORMATIONAL_HOLD = "informational_hold"
FINAL_SUPPRESSED = "suppressed"
FINAL_NOT_READY = "not_ready"
FINAL_NOT_FINALIZED = "not_finalized"   # placeholder before finalization runs

# Canonical pending_guardrails_reason values.
PENDING_REASON_TAX_WASH_NOT_EVALUATED = "tax_and_wash_sale_not_evaluated"
PENDING_REASON_NONE = "none"

# Cash status values that indicate cash is blocked.
_CASH_BLOCKING_STATUSES = frozenset({
    "blocked_insufficient_cash",
    "blocked_uncertified_cash",
})

_CASH_PASSED = "passed"
_CASH_NOT_APPLICABLE_TRIM_SELL = "not_applicable_trim_sell"

_TRIM_SELL_ACTIONS = frozenset({"TRIM", "SELL"})


def finalize_item_actionability(item: DeployPlanItem) -> DeployPlanItem:
    """Return a new DeployPlanItem with final_actionability_status set.

    Returns a copy of item — the original is never mutated.
    No prior fields are changed.
    """
    status = item.actionability_status
    action = item.intel_action.upper()

    # HOLD — informational only.
    if status == DeployActionabilityStatus.NOT_ACTIONABLE_HOLD:
        return replace(item, final_actionability_status=FINAL_INFORMATIONAL_HOLD)

    # Suppressed (any SUPPRESSED_* variant).
    if status != DeployActionabilityStatus.ACTIONABLE_CANDIDATE:
        return replace(item, final_actionability_status=FINAL_SUPPRESSED)

    # ACTIONABLE_CANDIDATE — no positive dollar amount (None, zero, or negative).
    if item.recommended_dollar_amount is None or item.recommended_dollar_amount <= 0:
        if item.cash_constraint_status in _CASH_BLOCKING_STATUSES:
            return replace(item, final_actionability_status=FINAL_BLOCKED_CASH)
        return replace(item, final_actionability_status=FINAL_NOT_READY)

    # ACTIONABLE_CANDIDATE with positive dollar amount — BUY path.
    if action == "BUY":
        if item.cash_constraint_status == _CASH_PASSED:
            return replace(
                item,
                final_actionability_status=FINAL_ACTIONABLE_PENDING_TAX,
                pending_guardrails_reason=PENDING_REASON_TAX_WASH_NOT_EVALUATED,
            )
        return replace(item, final_actionability_status=FINAL_BLOCKED_CASH)

    # ACTIONABLE_CANDIDATE with positive dollar amount — TRIM/SELL path.
    if action in _TRIM_SELL_ACTIONS:
        if item.cash_constraint_status == _CASH_NOT_APPLICABLE_TRIM_SELL:
            return replace(
                item,
                final_actionability_status=FINAL_ACTIONABLE_PENDING_TAX,
                pending_guardrails_reason=PENDING_REASON_TAX_WASH_NOT_EVALUATED,
            )
        return replace(item, final_actionability_status=FINAL_NOT_READY)

    # Unknown action — not ready.
    return replace(item, final_actionability_status=FINAL_NOT_READY)


def apply_finalization_to_plan_items(items: List[DeployPlanItem]) -> List[DeployPlanItem]:
    """Apply finalization to a list of DeployPlanItems.

    Returns a new list. No item is mutated.
    """
    return [finalize_item_actionability(item) for item in items]
