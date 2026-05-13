"""Deploy Stage 2.6D — New-cash sleeve sizing v1.

Pure function. No IO, no LLM, no DB, no broker.

Answers the user's question "I have $X of new cash — which 3-5 Intel v3 BUY
opportunities should I buy today?" Replaces current-gap math
(target_weight * portfolio - current_value) for BUY items when
cash_to_deploy > 0. Current-gap math leaves cash idle when current weights
already match target weights, producing tiny BUY deltas that are then
suppressed by the minimum trade threshold.

Policy:
  - Eligible universe: ACTIONABLE_CANDIDATE BUY items only. HOLD/TRIM/SELL
    never receive new-cash BUY dollars.
  - Selection: deterministic input-order ranking, capped at
    MAX_RECOMMENDATIONS. When fewer than MAX_RECOMMENDATIONS eligible BUYs
    exist, return only those — no fabrication.
  - Allocation: distribute cash_to_deploy across selected items using saved
    target_weight as a relative-weight guardrail. Fall back to equal weight
    when target_weight is missing or sums to zero. Saved target allocation is
    a guardrail/context, not the sole source of new-cash dollars.
  - Budget safety: floor-round so total deployed never exceeds cash_to_deploy.
  - Threshold safety: drop selections below minimum_trade_usd. Surface
    residual_cash and plain-English residual_reason so the UI/API can explain
    any idle planning capital.

Determinism: same inputs produce the same plan. No randomization, no LLM.
"""
from __future__ import annotations

import math
from dataclasses import replace
from typing import Dict, List, Optional, Tuple

from .deploy_contracts import DeployActionabilityStatus, DeployPlanItem
from .deploy_sizing_contracts import DeploySizingInputBundle

# Maximum new-cash BUY recommendations surfaced in one plan. Mirrors the
# frontend Step 2 cap (MAX_AMOUNT_AWARE_BUY_ITEMS) so the backend never
# produces more than the UI can display.
MAX_RECOMMENDATIONS = 5

_BUY_ACTION = "BUY"


def apply_new_cash_sleeve_sizing(
    bundle: DeploySizingInputBundle,
    items: List[DeployPlanItem],
    cash_to_deploy: float,
    price_per_share_map: Optional[Dict[str, float]] = None,
    max_recommendations: int = MAX_RECOMMENDATIONS,
) -> Tuple[List[DeployPlanItem], float, Optional[str]]:
    """Allocate cash_to_deploy across eligible BUY ACTIONABLE_CANDIDATE items.

    Returns (new_items, residual_cash, residual_reason).

    BUY ACTIONABLE_CANDIDATE items receive new-cash dollars. Non-BUY items and
    non-ACTIONABLE items are returned unchanged. Total deployed dollars never
    exceed cash_to_deploy after floor rounding.

    All BUY ACTIONABLE_CANDIDATE items are reset before allocation, so any
    upstream current-gap BUY amounts are cleared. Selected items get the
    sleeve amount; non-selected eligible BUYs end with no dollar amount.
    """
    if price_per_share_map is None:
        price_per_share_map = {}

    new_items = list(items)
    if cash_to_deploy <= 0:
        return new_items, 0.0, None

    policy = bundle.policy
    minimum_trade_usd = (
        policy.minimum_trade_usd
        if policy is not None and policy.minimum_trade_usd is not None
        else 0.0
    )
    rounding_policy = (
        policy.rounding_policy if policy is not None else "WHOLE_DOLLAR"
    )

    eligible_indices = [
        i for i, it in enumerate(items)
        if it.intel_action.upper() == _BUY_ACTION
        and it.actionability_status == DeployActionabilityStatus.ACTIONABLE_CANDIDATE
    ]
    eligible_count = len(eligible_indices)

    if eligible_count == 0:
        return new_items, cash_to_deploy, "No eligible BUY candidates from Intel v3."

    # Reset every eligible BUY so any upstream current-gap amounts are cleared.
    # Non-selected eligible BUYs end with no recommended dollar amount.
    for idx in eligible_indices:
        new_items[idx] = replace(
            items[idx],
            recommended_dollar_amount=None,
            estimated_share_quantity=None,
            target_allocation_status="not_evaluated_yet",
            cash_constraint_status="not_evaluated_yet",
        )

    selected_indices = eligible_indices[:max_recommendations]
    selected_count = len(selected_indices)

    # Relative weights using saved target allocation as a guardrail. Fall back
    # to equal weight when any selected ticker lacks a usable target weight.
    raw_weights: List[Optional[float]] = []
    for idx in selected_indices:
        ta = bundle.target_allocation_for(items[idx].ticker)
        if (
            ta is not None
            and ta.is_ready_for_math
            and ta.target_weight is not None
            and ta.target_weight > 0
        ):
            raw_weights.append(float(ta.target_weight))
        else:
            raw_weights.append(None)

    total_known = sum(w for w in raw_weights if w is not None)
    if all(w is not None for w in raw_weights) and total_known > 0:
        weights = [w / total_known for w in raw_weights]  # type: ignore[misc]
    else:
        weights = [1.0 / selected_count] * selected_count

    # Floor-round each allocation to guarantee total <= cash_to_deploy.
    allocations = [float(math.floor(cash_to_deploy * w)) for w in weights]

    # Drop allocations below the minimum trade threshold.
    suppressed_below_min = 0
    for k in range(selected_count):
        amount = allocations[k]
        if amount <= 0 or (minimum_trade_usd > 0 and amount < minimum_trade_usd):
            allocations[k] = 0.0
            suppressed_below_min += 1

    # Distribute leftover whole dollars from floor rounding to selected BUY rows
    # in deterministic top-ranked-first order. Skips suppressed/zero slots so a
    # below-min-trade row never gets resurrected by residual distribution.
    # Never adds beyond floor(cash_to_deploy) so total <= cash_to_deploy holds.
    nonzero_slots = [k for k in range(selected_count) if allocations[k] > 0]
    if nonzero_slots:
        current_total = sum(allocations)
        leftover = int(math.floor(max(cash_to_deploy - current_total, 0.0)))
        i = 0
        while leftover > 0:
            allocations[nonzero_slots[i % len(nonzero_slots)]] += 1.0
            leftover -= 1
            i += 1

    total_allocated = 0.0
    for k, idx in enumerate(selected_indices):
        amount = allocations[k]
        if amount <= 0:
            continue
        it = new_items[idx]
        price = price_per_share_map.get(it.ticker)
        shares = (amount / price) if (price is not None and price > 0) else None
        new_items[idx] = replace(
            it,
            recommended_dollar_amount=amount,
            estimated_share_quantity=shares,
            rounding_policy=rounding_policy,
            target_allocation_status="evaluated",
            cash_constraint_status="not_evaluated_yet",
        )
        total_allocated += amount

    residual = max(cash_to_deploy - total_allocated, 0.0)
    residual_reason = _residual_reason(
        residual=residual,
        eligible_count=eligible_count,
        selected_count=selected_count,
        suppressed_below_min=suppressed_below_min,
        total_allocated=total_allocated,
        minimum_trade_usd=minimum_trade_usd,
    )
    return new_items, residual, residual_reason


def _residual_reason(
    residual: float,
    eligible_count: int,
    selected_count: int,
    suppressed_below_min: int,
    total_allocated: float,
    minimum_trade_usd: float,
) -> Optional[str]:
    """Plain-English explanation for any idle planning capital."""
    if residual <= 0:
        return None
    if total_allocated == 0:
        if suppressed_below_min > 0:
            return (
                f"All sleeve allocations fell below the ${minimum_trade_usd:.0f} "
                "minimum trade threshold; no BUYs sized."
            )
        return "No BUY recommendations sized; remaining planning capital held back."
    if eligible_count < 3:
        return (
            f"Only {eligible_count} eligible BUY candidate(s) from Intel v3; "
            "remaining planning capital held back."
        )
    if suppressed_below_min > 0:
        return (
            f"{suppressed_below_min} sleeve allocation(s) fell below the "
            f"${minimum_trade_usd:.0f} minimum trade threshold and were dropped; "
            "remaining planning capital held back."
        )
    return "Rounding holdback to keep total within planning capital."
