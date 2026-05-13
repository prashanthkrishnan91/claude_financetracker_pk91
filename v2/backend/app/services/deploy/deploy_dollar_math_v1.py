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
    cash_to_deploy: Optional[float] = None,
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

    cash_to_deploy: when provided and > 0, enables new-cash mode for BUY items.
      BUY target dollars are sized relative to (portfolio_value + cash_to_deploy)
      rather than portfolio_value alone. TRIM/SELL always use current-gap math.
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

    # Current-gap target dollars (used for TRIM/SELL and non-amount-aware BUY).
    current_gap_target_dollars = ta.target_weight * portfolio_value  # type: ignore[operator]

    # Retrieve current position value. Position must be explicitly present and certified.
    # An absent position is treated as missing certified data — suppress rather than
    # assume zero. BUY-from-scratch requires an explicit certified zero position.
    pos = bundle.positions.get(ticker)
    if pos is None:
        return item
    current_dollars = pos.current_market_value_usd if pos.current_market_value_usd is not None else 0.0

    # Compute action-specific delta.
    new_cash_mode_active = intel_action == _BUY_ACTION and cash_to_deploy and cash_to_deploy > 0
    if new_cash_mode_active:
        # New-cash mode: size toward target fraction of (portfolio + new cash).
        # This produces positive BUY deltas even when current weights already match
        # targets, because the post-cash portfolio is larger.
        effective_portfolio = portfolio_value + cash_to_deploy  # type: ignore[operator]
        delta = ta.target_weight * effective_portfolio - current_dollars  # type: ignore[operator]
    elif intel_action == _BUY_ACTION:
        delta = current_gap_target_dollars - current_dollars
    else:
        # TRIM or SELL — always current-gap math.
        delta = current_dollars - current_gap_target_dollars

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


def cap_buy_amounts_to_cash(
    items: list[DeployPlanItem],
    cash_to_deploy: float,
    minimum_trade_usd: Optional[float] = None,
    rounding_policy: str = "WHOLE_DOLLAR",
) -> list[DeployPlanItem]:
    """Pro-rate BUY ACTIONABLE_CANDIDATE dollar amounts to fit within cash_to_deploy.

    Only BUY items with a positive recommended_dollar_amount are considered.
    If total BUY <= cash_to_deploy, returns items unchanged.
    When total exceeds budget: pro-rates each item, re-applies rounding, and suppresses
    items that fall below minimum_trade_usd (sets recommended_dollar_amount to None).
    Share quantities are cleared after pro-rating (price unavailable at this stage).
    Returns a new list. No item is mutated.
    """
    if cash_to_deploy <= 0:
        return items

    buy_indices = [
        i for i, it in enumerate(items)
        if it.intel_action.upper() == _BUY_ACTION
        and it.actionability_status == DeployActionabilityStatus.ACTIONABLE_CANDIDATE
        and it.recommended_dollar_amount is not None
        and it.recommended_dollar_amount > 0
    ]

    if not buy_indices:
        return items

    total_buy = sum(items[i].recommended_dollar_amount for i in buy_indices)  # type: ignore[misc]
    if total_buy <= cash_to_deploy:
        return items

    scale = cash_to_deploy / total_buy
    new_items = list(items)
    for i in buy_indices:
        it = items[i]
        raw_scaled = it.recommended_dollar_amount * scale  # type: ignore[operator]
        rounded = _apply_rounding(raw_scaled, rounding_policy)
        if rounded <= 0 or (minimum_trade_usd is not None and rounded < minimum_trade_usd):
            new_items[i] = replace(it, recommended_dollar_amount=None, estimated_share_quantity=None)
        else:
            new_items[i] = replace(it, recommended_dollar_amount=rounded, estimated_share_quantity=None)

    return new_items


def apply_dollar_math_to_plan_items(
    bundle: DeploySizingInputBundle,
    items: list[DeployPlanItem],
    price_per_share_map: Optional[dict[str, float]] = None,
    cash_to_deploy: Optional[float] = None,
) -> list[DeployPlanItem]:
    """Apply exact-dollar math to a list of DeployPlanItems.

    Returns a new list of DeployPlanItems. Items that are ineligible (HOLD,
    suppressed, non-positive delta, below minimum trade) are returned unchanged.

    price_per_share_map: optional mapping of ticker → certified price per share.
      Tickers absent from the map will have share quantity suppressed (null).

    cash_to_deploy: when provided and > 0, enables new-cash mode BUY sizing.
      Passed through to compute_dollar_amount_for_item. Cap must be applied
      separately via cap_buy_amounts_to_cash after this call.
    """
    if price_per_share_map is None:
        price_per_share_map = {}

    return [
        compute_dollar_amount_for_item(
            bundle=bundle,
            item=item,
            price_per_share_usd=price_per_share_map.get(item.ticker),
            cash_to_deploy=cash_to_deploy,
        )
        for item in items
    ]
