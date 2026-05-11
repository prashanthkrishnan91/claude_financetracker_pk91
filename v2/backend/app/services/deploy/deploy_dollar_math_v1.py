"""Deploy Stage 2.3A — exact-dollar planning math v1.

Pure functions only. No IO, no LLM, no DB, no broker.

Computes recommended_dollar_amount and estimated_share_quantity for
actionable BUY / TRIM / SELL candidates using certified sizing inputs.

Guardrails enforced here:
  - exact_dollar_ready must be True on the bundle; otherwise all outputs are null.
  - HOLD is never actionable; dollar fields remain null.
  - Suppressed candidates (non-ACTIONABLE_CANDIDATE) never receive dollar amounts.
  - Intel action is read verbatim and never changed.
  - recommended_dollar_amount below minimum_trade_usd is suppressed (set null).
  - estimated_share_quantity is populated only when a certified price_per_share_usd
    is provided; otherwise it remains null.
  - Rounding: WHOLE_DOLLAR / NEAREST_DOLLAR → round to nearest integer dollar;
    NO_ROUNDING → pass through unmodified.
  - PriceBand is not referenced and is not an authority here.

Dollar math model:
  BUY:       delta = (target_weight * portfolio_value) - current_position_value
             (buying to close gap to target allocation)
  TRIM/SELL: delta = current_position_value - (target_weight * portfolio_value)
             (trimming/selling to reduce to target allocation)

A non-positive computed delta suppresses output (no negative or zero trade amounts).
"""
from __future__ import annotations

import math
from dataclasses import replace
from typing import Optional

from .deploy_contracts import (
    DeployActionabilityStatus,
    DeployPlanItem,
)
from .deploy_sizing_contracts import DeploySizingInputBundle

# Intel actions that may receive dollar amounts.
_BUY_ACTION = "BUY"
_TRIM_SELL_ACTIONS = frozenset({"TRIM", "SELL"})


def _apply_rounding(amount: float, rounding_policy: str) -> float:
    """Apply the certified rounding policy to a computed dollar amount."""
    normalized = rounding_policy.strip().upper()
    if normalized in ("WHOLE_DOLLAR", "NEAREST_DOLLAR"):
        return float(round(amount))
    # NO_ROUNDING — pass through.
    return amount


def compute_dollar_amount_for_item(
    bundle: DeploySizingInputBundle,
    item: DeployPlanItem,
    price_per_share_usd: Optional[float] = None,
) -> DeployPlanItem:
    """Return a new DeployPlanItem with exact-dollar fields populated if eligible.

    Returns a copy of item — the original is never mutated.

    Eligibility requirements (all must hold):
      1. bundle.exact_dollar_ready is True.
      2. item.actionability_status is ACTIONABLE_CANDIDATE.
      3. item.intel_action is BUY, TRIM, or SELL.
      4. The computed delta is positive.
      5. The computed (rounded) dollar amount meets the minimum_trade_usd threshold.

    If any requirement fails, recommended_dollar_amount and estimated_share_quantity
    remain null and the item is returned unchanged (except for updated status fields).

    price_per_share_usd: certified price per share for this ticker. If None or
      non-positive, estimated_share_quantity is suppressed (left null).
    """
    # Gate 1: exact_dollar_ready.
    if not bundle.exact_dollar_ready:
        return item

    # Gate 2: only ACTIONABLE_CANDIDATE items receive dollar amounts.
    if item.actionability_status != DeployActionabilityStatus.ACTIONABLE_CANDIDATE:
        return item

    # Gate 3: only BUY / TRIM / SELL receive dollar amounts.
    intel_action = item.intel_action.upper()
    if intel_action not in (_BUY_ACTION, *_TRIM_SELL_ACTIONS):
        return item

    # All bundle values are certified at this point (exact_dollar_ready guarantees it).
    ticker = item.ticker
    portfolio_value = bundle.portfolio.total_portfolio_value_usd  # type: ignore[union-attr]

    # Retrieve per-ticker target allocation.
    ta = bundle.target_allocation_for(ticker)
    if ta is None or not ta.is_ready_for_math:
        return item

    target_dollars = ta.target_weight * portfolio_value  # type: ignore[operator]

    # Retrieve current position value (may be zero if ticker not in positions).
    pos = bundle.positions.get(ticker)
    if pos is None:
        current_dollars = 0.0
    else:
        current_dollars = pos.current_market_value_usd or 0.0  # type: ignore[assignment]

    # Compute action-specific delta.
    if intel_action == _BUY_ACTION:
        delta = target_dollars - current_dollars
    else:
        # TRIM or SELL
        delta = current_dollars - target_dollars

    # Gate 4: suppress non-positive deltas (nothing to trade).
    if delta <= 0:
        return item

    # Apply rounding policy.
    policy = bundle.policy  # guaranteed non-None and CERTIFIED
    rounding_policy = policy.rounding_policy  # type: ignore[union-attr]
    rounded_amount = _apply_rounding(delta, rounding_policy)

    # Guard against rounding producing zero.
    if rounded_amount <= 0:
        return item

    # Gate 5: minimum trade threshold.
    minimum_trade = policy.minimum_trade_usd  # type: ignore[union-attr]
    if minimum_trade is not None and rounded_amount < minimum_trade:
        return item

    # Compute share quantity only when a certified positive price/share is provided.
    share_quantity: Optional[float] = None
    if price_per_share_usd is not None and price_per_share_usd > 0:
        share_quantity = rounded_amount / price_per_share_usd

    return replace(
        item,
        recommended_dollar_amount=rounded_amount,
        estimated_share_quantity=share_quantity,
        rounding_policy=rounding_policy,
        target_allocation_status="evaluated",
        cash_constraint_status="not_evaluated_yet",
    )


def apply_dollar_math_to_plan_items(
    bundle: DeploySizingInputBundle,
    items: list[DeployPlanItem],
    price_per_share_map: Optional[dict[str, float]] = None,
) -> list[DeployPlanItem]:
    """Apply exact-dollar math to a list of DeployPlanItems.

    Returns a new list of DeployPlanItems. Items that are ineligible (HOLD,
    suppressed, non-positive delta, below minimum trade) are returned unchanged.

    price_per_share_map: optional mapping of ticker → certified price per share.
      Tickers absent from the map will have share quantity suppressed (null).
    """
    if price_per_share_map is None:
        price_per_share_map = {}

    return [
        compute_dollar_amount_for_item(
            bundle=bundle,
            item=item,
            price_per_share_usd=price_per_share_map.get(item.ticker),
        )
        for item in items
    ]
