"""Personalization layer — adjusts decision_engine outputs using user behavior.

Never modifies decision_engine or portfolio_engine. Applies bounded adjustments
post-generation and normalizes so total_allocated stays within the original budget.
"""

from __future__ import annotations

import copy
from typing import Any

_MIN_ALLOCATION = 25.0
_MAX_INCREASE_FACTOR = 1.25  # hard ceiling: no asset can exceed +25% of original
_BIAS_SCALE = 0.2            # bias_score * 0.2 → fractional increase
_RISK_HIGH_BOOST = 0.10      # +10% for high-growth when risk_score > 0.7
_RISK_LOW_REDUCE = 0.10      # -10% for volatile when risk_score < 0.3

# Matches the high-beta set used by personalization_engine._risk_score
_HIGH_GROWTH: frozenset[str] = frozenset({
    "NVDA", "QQQ", "AMD", "RIVN", "SNOW", "RDDT", "CAVA",
    "BTC", "XRP", "KLAR", "BLSH", "STUB",
})


def adjust_decision_plan(
    plan: dict[str, Any],
    user_profile: dict[str, Any],
) -> dict[str, Any]:
    """Return an adjusted copy of *plan* shaped by *user_profile*.

    The output preserves the exact structure of the input — same keys, same
    ordering of actions — with amounts re-proportioned within the original budget.

    Constraints enforced:
    - No new symbols are introduced.
    - Each allocation is capped at original * 1.25.
    - Each allocation stays >= _MIN_ALLOCATION (or original if original < minimum).
    - total_allocated in meta (if present) stays <= original total.
    """
    actions: list[dict[str, Any]] = plan.get("actions", [])
    if not actions:
        return copy.deepcopy(plan)

    # Original per-action amounts (used for cap/floor calculations)
    originals: list[float] = [float(a.get("amount", 0.0)) for a in actions]
    original_total = sum(originals)

    if original_total <= 0:
        return copy.deepcopy(plan)

    # ── Extract profile fields ──────────────────────────────────────────────
    biases: dict[str, Any] = user_profile.get("biases", {})
    overweighted_map: dict[str, float] = {
        item["symbol"].upper(): float(item["bias_score"])
        for item in biases.get("overweighted_symbols", [])
        if item.get("symbol") and item.get("bias_score") is not None
    }
    risk_score: float = float(user_profile.get("risk_score", 0.0))

    # ── Build adjusted amounts ──────────────────────────────────────────────
    amounts: list[float] = list(originals)

    for i, action in enumerate(actions):
        symbol = (action.get("symbol") or "").upper()

        # 1. Bias adjustment — increase for overweighted symbols
        if symbol in overweighted_map:
            bias_score = overweighted_map[symbol]
            amounts[i] *= 1.0 + (bias_score * _BIAS_SCALE)

        # 2. Risk adjustment — high risk boosts high-growth; low risk trims them
        if symbol in _HIGH_GROWTH:
            if risk_score > 0.7:
                amounts[i] *= 1.0 + _RISK_HIGH_BOOST
            elif risk_score < 0.3:
                amounts[i] *= 1.0 - _RISK_LOW_REDUCE

    # ── Safety constraints ──────────────────────────────────────────────────
    for i, orig in enumerate(originals):
        # Cap: no asset may exceed original * 1.25
        amounts[i] = min(amounts[i], orig * _MAX_INCREASE_FACTOR)
        # Floor: stay above minimum threshold (or original if it was already below minimum)
        floor = min(orig, _MIN_ALLOCATION)
        amounts[i] = max(amounts[i], floor)

    # ── Normalize so total <= original_total ────────────────────────────────
    adjusted_total = sum(amounts)
    if adjusted_total > original_total and adjusted_total > 0:
        scale = original_total / adjusted_total
        amounts = [a * scale for a in amounts]
        # Re-apply floor after scaling (scale can only push amounts down)
        for i, orig in enumerate(originals):
            floor = min(orig, _MIN_ALLOCATION)
            if amounts[i] < floor:
                amounts[i] = floor

    # ── Assemble output ─────────────────────────────────────────────────────
    adjusted = copy.deepcopy(plan)
    for i, action in enumerate(adjusted["actions"]):
        action["amount"] = round(amounts[i], 2)

    final_total = round(sum(a["amount"] for a in adjusted["actions"]), 2)
    if "meta" in adjusted:
        original_unallocated = float(adjusted["meta"].get("unallocated_cash", 0.0))
        adjusted["meta"]["total_allocated"] = final_total
        adjusted["meta"]["unallocated_cash"] = round(
            original_total + original_unallocated - final_total, 2
        )

    return adjusted
