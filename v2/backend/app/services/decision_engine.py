"""Deterministic deposit-plan decision engine — no LLM, no side effects."""

from __future__ import annotations

from typing import Any

_MIN_ALLOCATION = 25.0
_NEAR_ZERO = 1e-9


def generate_deposit_plan(
    snapshot: dict[str, Any],
    cash_to_invest: float,
) -> dict[str, Any]:
    """Allocate cash_to_invest across underweight positions using equal-weight rebalancing.

    Args:
        snapshot: output of get_portfolio_snapshot()
        cash_to_invest: dollars available to deploy

    Returns structured JSON with actions and meta.
    """
    empty_result: dict[str, Any] = {
        "actions": [],
        "meta": {
            "strategy": "rebalance",
            "total_allocated": 0.0,
            "unallocated_cash": round(cash_to_invest, 2),
        },
    }

    if cash_to_invest <= 0:
        return empty_result

    positions: list[dict[str, Any]] = snapshot.get("positions", [])

    # Step 1: skip positions with no live price
    valid = [p for p in positions if p.get("current_price") is not None]

    if not valid:
        return empty_result

    # Step 2: equal-weight target across all valid positions (as %)
    n = len(valid)
    target_weight = 100.0 / n

    # Step 3: compute delta; keep only underweight positions
    candidates = []
    for pos in valid:
        current_weight = float(pos.get("weight", 0.0))
        delta = target_weight - current_weight
        if delta > _NEAR_ZERO:
            candidates.append({
                "symbol": pos["symbol"],
                "target_weight": target_weight,
                "current_weight": current_weight,
                "delta_weight": delta,
            })

    if not candidates:
        return empty_result

    # Step 4: normalize deltas so they sum to 1, then allocate
    total_delta = sum(c["delta_weight"] for c in candidates)

    actions: list[dict[str, Any]] = []
    total_allocated = 0.0

    for c in candidates:
        normalized = c["delta_weight"] / total_delta
        amount = round(normalized * cash_to_invest, 2)
        if amount < _MIN_ALLOCATION:
            continue
        actions.append({
            "symbol": c["symbol"],
            "action": "buy",
            "amount": amount,
            "target_weight": round(c["target_weight"], 4),
            "current_weight": round(c["current_weight"], 4),
            "delta_weight": round(c["delta_weight"], 4),
        })
        total_allocated += amount

    total_allocated = round(total_allocated, 2)
    # Floating-point safety: clamp to available cash
    if total_allocated > cash_to_invest:
        total_allocated = round(cash_to_invest, 2)

    return {
        "actions": actions,
        "meta": {
            "strategy": "rebalance",
            "total_allocated": total_allocated,
            "unallocated_cash": round(cash_to_invest - total_allocated, 2),
        },
    }
