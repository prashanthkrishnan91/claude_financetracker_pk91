"""Strategy engine — applies a strategy mode's multipliers to a decision plan.

Never modifies portfolio_engine or decision_engine core logic.
Operates as a post-personalization layer: receives the personalized plan and
returns a strategy-adjusted copy, normalized to the original budget.
"""

from __future__ import annotations

import copy
from typing import Any

from .strategy_modes import DEFAULT_STRATEGY, DIVIDEND_ASSETS, GROWTH_ASSETS, STRATEGY_MODES


def apply_strategy(
    plan: dict[str, Any],
    strategy_mode: str,
) -> dict[str, Any]:
    """Return a strategy-adjusted copy of *plan*.

    Steps:
    1. Classify each action as growth, dividend, or neutral.
    2. Apply the appropriate bias multiplier (growth_bias / dividend_bias).
    3. Apply risk_multiplier to every action.
    4. Normalize adjusted amounts back to the original total budget.
    """
    config = STRATEGY_MODES.get(strategy_mode, STRATEGY_MODES[DEFAULT_STRATEGY])
    risk_multiplier: float = config["risk_multiplier"]
    growth_bias: float = config["growth_bias"]
    dividend_bias: float = config["dividend_bias"]

    actions: list[dict[str, Any]] = plan.get("actions", [])
    if not actions:
        return copy.deepcopy(plan)

    originals: list[float] = [float(a.get("amount", 0.0)) for a in actions]
    original_total = sum(originals)

    if original_total <= 0:
        return copy.deepcopy(plan)

    amounts: list[float] = list(originals)

    for i, action in enumerate(actions):
        symbol = (action.get("symbol") or "").upper()

        if symbol in GROWTH_ASSETS:
            amounts[i] *= growth_bias
        elif symbol in DIVIDEND_ASSETS:
            amounts[i] *= dividend_bias

        amounts[i] *= risk_multiplier

    # Normalize back to original total
    adjusted_total = sum(amounts)
    if adjusted_total > 0:
        scale = original_total / adjusted_total
        amounts = [a * scale for a in amounts]

    result = copy.deepcopy(plan)
    for i, action in enumerate(result["actions"]):
        action["amount"] = round(amounts[i], 2)

    return result
