"""Deploy Stage 2.3B — cash constraint guardrail v1.

Pure functions only. No IO, no LLM, no DB, no broker.

Evaluates cash constraints for Deploy plan items AFTER exact-dollar math has
produced candidate dollar amounts.  The guardrail never changes Intel action,
actionability_status, or recommended_dollar_amount.  It only sets
cash_constraint_status on each item.

Evaluation model:
  BUY (ACTIONABLE_CANDIDATE, has recommended_dollar_amount):
    - cash not certified / missing / invalid          → BLOCKED_UNCERTIFIED_CASH
    - recommended_dollar_amount > available_cash_usd  → BLOCKED_INSUFFICIENT_CASH
    - recommended_dollar_amount <= available_cash_usd → PASSED

  BUY (ACTIONABLE_CANDIDATE, no recommended_dollar_amount — dollar math suppressed):
    - cash not certified                              → BLOCKED_UNCERTIFIED_CASH
    - cash certified                                  → NOT_EVALUATED_NO_DOLLAR_AMOUNT

  TRIM / SELL:
    - Never blocked by cash.                          → NOT_APPLICABLE_TRIM_SELL

  HOLD (NOT_ACTIONABLE_HOLD):
    - Never blocked.                                  → NOT_APPLICABLE_HOLD

  Suppressed / other non-actionable items:
    - No cash evaluation.                             → NOT_APPLICABLE_SUPPRESSED

Intel v3 remains the only Buy/Hold/Trim/Sell authority.
This guardrail does not create, override, reinterpret, or downgrade Intel actions.
Tax / wash-sale guardrails are out of scope for this slice.
"""
from __future__ import annotations

from dataclasses import replace
from typing import List

from .deploy_contracts import DeployActionabilityStatus, DeployPlanItem
from .deploy_sizing_contracts import DeploySizingInputBundle, DeploySizingTrustStatus

# Canonical cash_constraint_status string literals.
CASH_PASSED = "passed"
CASH_BLOCKED_INSUFFICIENT = "blocked_insufficient_cash"
CASH_BLOCKED_UNCERTIFIED = "blocked_uncertified_cash"
CASH_NOT_APPLICABLE_TRIM_SELL = "not_applicable_trim_sell"
CASH_NOT_APPLICABLE_HOLD = "not_applicable_hold"
CASH_NOT_APPLICABLE_SUPPRESSED = "not_applicable_suppressed"
CASH_NOT_EVALUATED_NO_DOLLAR_AMOUNT = "not_evaluated_no_dollar_amount"

_TRIM_SELL_ACTIONS = frozenset({"TRIM", "SELL"})

# Trust statuses that mean cash is not certified for constraint evaluation.
_NON_CERTIFIED_STATUSES = frozenset({
    DeploySizingTrustStatus.MISSING,
    DeploySizingTrustStatus.STALE,
    DeploySizingTrustStatus.WEAK,
    DeploySizingTrustStatus.CONFLICTING,
    DeploySizingTrustStatus.NOT_EVALUATED,
    DeploySizingTrustStatus.UNSUPPORTED,
})


def _cash_is_certified_and_valid(bundle: DeploySizingInputBundle) -> bool:
    """True when the bundle has certified, non-None, non-negative available cash."""
    if bundle.cash is None:
        return False
    if bundle.cash.trust_status in _NON_CERTIFIED_STATUSES:
        return False
    cash = bundle.cash.available_cash_usd
    if cash is None or cash < 0:
        return False
    return True


def evaluate_cash_constraint_for_item(
    bundle: DeploySizingInputBundle,
    item: DeployPlanItem,
) -> DeployPlanItem:
    """Return a new DeployPlanItem with cash_constraint_status evaluated.

    Returns a copy of item — the original is never mutated.
    Intel action and recommended_dollar_amount are never changed.
    """
    action_upper = item.intel_action.upper()
    status = item.actionability_status

    # HOLD — not actionable, cash not relevant.
    if status == DeployActionabilityStatus.NOT_ACTIONABLE_HOLD:
        return replace(item, cash_constraint_status=CASH_NOT_APPLICABLE_HOLD)

    # Suppressed / non-actionable (not BUY/TRIM/SELL candidates).
    if status != DeployActionabilityStatus.ACTIONABLE_CANDIDATE:
        return replace(item, cash_constraint_status=CASH_NOT_APPLICABLE_SUPPRESSED)

    # TRIM / SELL — never blocked by cash.
    if action_upper in _TRIM_SELL_ACTIONS:
        return replace(item, cash_constraint_status=CASH_NOT_APPLICABLE_TRIM_SELL)

    # BUY path.
    if action_upper == "BUY":
        cash_certified = _cash_is_certified_and_valid(bundle)

        # If dollar math produced no amount (suppressed upstream), check certification only.
        if item.recommended_dollar_amount is None:
            if not cash_certified:
                return replace(item, cash_constraint_status=CASH_BLOCKED_UNCERTIFIED)
            return replace(item, cash_constraint_status=CASH_NOT_EVALUATED_NO_DOLLAR_AMOUNT)

        # Dollar amount present — enforce cash constraint.
        if not cash_certified:
            return replace(item, cash_constraint_status=CASH_BLOCKED_UNCERTIFIED)

        available = bundle.cash.available_cash_usd  # type: ignore[union-attr]
        if item.recommended_dollar_amount > available:
            return replace(item, cash_constraint_status=CASH_BLOCKED_INSUFFICIENT)

        return replace(item, cash_constraint_status=CASH_PASSED)

    # Unknown action — treat as suppressed (no fabrication).
    return replace(item, cash_constraint_status=CASH_NOT_APPLICABLE_SUPPRESSED)


def apply_cash_guardrail_to_plan_items(
    bundle: DeploySizingInputBundle,
    items: List[DeployPlanItem],
) -> List[DeployPlanItem]:
    """Apply cash constraint guardrail to a list of DeployPlanItems.

    Returns a new list. Each item's cash_constraint_status is set according to
    the evaluation model. recommended_dollar_amount and intel_action are never changed.
    """
    return [evaluate_cash_constraint_for_item(bundle, item) for item in items]
