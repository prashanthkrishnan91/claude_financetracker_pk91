"""Deterministic portfolio growth simulation engine — no LLM, no external APIs.

Projects portfolio value forward using fixed category return rates and a
chosen allocation strategy for monthly cash deployment.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from ..database import get_supabase_client
from .decision_engine import generate_deposit_plan
from .personalized_decision_engine import adjust_decision_plan

# ── Return rate constants ─────────────────────────────────────────────────────

_ANNUAL_RETURNS: dict[str, float] = {
    "stocks": 0.07,
    "etfs":   0.06,
    "crypto": 0.10,
}

# Position category → return bucket
_CATEGORY_BUCKET: dict[str, str] = {
    "Core":   "stocks",
    "Other":  "stocks",
    "IPO":    "stocks",
    "SELL":   "stocks",
    "ETF":    "etfs",
    "Crypto": "crypto",
}

_NEAR_ZERO = 1e-9


# ── Core simulation ───────────────────────────────────────────────────────────

def simulate_growth(
    initial_snapshot: dict[str, Any],
    monthly_investment: float,
    months: int,
    strategy: str = "system",
) -> dict[str, Any]:
    """Project portfolio growth deterministically over *months* months.

    Args:
        initial_snapshot: dict with keys:
            - total_value (float): current portfolio market value
            - positions (list[dict]): each with symbol, category, weight (%), current_price
        monthly_investment: dollars added each month
        months: number of months to simulate
        strategy: "system" (equal-weight rebalance) or "user_behavior" (personalized)
            NOTE: for user_behavior, supply user_profile in initial_snapshot["user_profile"]

    Returns:
        {timeline, final_value, total_invested, total_gain}
    """
    total_value = float(initial_snapshot.get("total_value", 0.0))
    positions: list[dict[str, Any]] = initial_snapshot.get("positions", [])
    user_profile: dict[str, Any] = initial_snapshot.get("user_profile", {})

    # Build mutable category buckets from initial positions
    bucket_values = _build_bucket_values(positions, total_value)

    timeline: list[dict[str, Any]] = []
    current_value = total_value
    total_invested = total_value

    for month in range(1, months + 1):
        # 1. Determine how monthly_investment is split across categories
        if monthly_investment > _NEAR_ZERO and positions:
            allocation_split = _allocation_split(
                positions, current_value, monthly_investment, strategy, user_profile
            )
        else:
            allocation_split = {}

        # 2. Add monthly investment to bucket values
        unallocated = monthly_investment
        for bucket, amount in allocation_split.items():
            bucket_values[bucket] = bucket_values.get(bucket, 0.0) + amount
            unallocated -= amount

        # Distribute any unallocated cash proportionally to existing buckets
        if unallocated > _NEAR_ZERO:
            total_bv = sum(bucket_values.values()) or 1.0
            for bucket in bucket_values:
                bucket_values[bucket] += unallocated * (bucket_values[bucket] / total_bv)

        total_invested += monthly_investment

        # 3. Apply monthly return per bucket
        new_value = 0.0
        for bucket, val in bucket_values.items():
            monthly_rate = _ANNUAL_RETURNS[bucket] / 12.0
            bucket_values[bucket] = val * (1.0 + monthly_rate)
            new_value += bucket_values[bucket]

        current_value = new_value

        timeline.append({
            "month": month,
            "portfolio_value": round(current_value, 2),
            "invested_total": round(total_invested, 2),
            "gain": round(current_value - total_invested, 2),
        })

    return {
        "timeline": timeline,
        "final_value": round(current_value, 2),
        "total_invested": round(total_invested, 2),
        "total_gain": round(current_value - total_invested, 2),
    }


# ── Adapter ───────────────────────────────────────────────────────────────────

async def get_simulation(
    user_id: str | UUID,
    monthly_investment: float,
    months: int,
    strategy: str = "system",
) -> dict[str, Any]:
    """Fetch user portfolio from DB and run simulate_growth.

    Uses avg_cost as a stand-in price so the decision engine can compute
    allocation weights without hitting external APIs.
    """
    uid = str(user_id)
    client = get_supabase_client()

    rows = (
        client.table("positions")
        .select("ticker, category, shares, avg_cost")
        .eq("user_id", uid)
        .neq("category", "SELL")
        .execute()
    ).data or []

    # Build positions with placeholder current_price = avg_cost
    positions: list[dict[str, Any]] = []
    total_value = 0.0

    for row in rows:
        shares = float(row.get("shares") or 0.0)
        avg_cost = float(row.get("avg_cost") or 0.0)
        if shares <= _NEAR_ZERO or avg_cost <= _NEAR_ZERO:
            continue
        market_value = shares * avg_cost
        total_value += market_value
        positions.append({
            "symbol":        row["ticker"],
            "category":      row.get("category", "Other"),
            "shares":        shares,
            "current_price": avg_cost,
            "market_value":  market_value,
            "weight":        0.0,  # filled in below
        })

    # Assign weights
    if total_value > _NEAR_ZERO:
        for p in positions:
            p["weight"] = p["market_value"] / total_value * 100.0

    snapshot: dict[str, Any] = {
        "total_value": total_value,
        "positions":   positions,
    }

    # Attach user profile for personalized strategy
    if strategy == "user_behavior":
        from .personalization_engine import get_user_profile
        snapshot["user_profile"] = await get_user_profile(uid)

    return simulate_growth(snapshot, monthly_investment, months, strategy)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_bucket_values(
    positions: list[dict[str, Any]],
    total_value: float,
) -> dict[str, float]:
    """Return {bucket: dollar_value} from position weights."""
    buckets: dict[str, float] = {"stocks": 0.0, "etfs": 0.0, "crypto": 0.0}
    for p in positions:
        bucket = _CATEGORY_BUCKET.get(p.get("category", "Other"), "stocks")
        weight = float(p.get("weight", 0.0))
        buckets[bucket] += total_value * (weight / 100.0)
    return buckets


def _allocation_split(
    positions: list[dict[str, Any]],
    current_value: float,
    monthly_investment: float,
    strategy: str,
    user_profile: dict[str, Any],
) -> dict[str, float]:
    """Return {bucket: dollars} split for the monthly investment using the strategy."""
    # Recompute weights against current_value so the engine sees up-to-date %
    snapshot_positions = []
    total_mv = sum(float(p.get("market_value", 0.0)) for p in positions)
    scale = current_value / total_mv if total_mv > _NEAR_ZERO else 1.0

    for p in positions:
        mv = float(p.get("market_value", 0.0)) * scale
        snapshot_positions.append({
            "symbol":        p["symbol"],
            "category":      p.get("category", "Other"),
            "weight":        mv / current_value * 100.0 if current_value > _NEAR_ZERO else 0.0,
            "current_price": p.get("current_price", 1.0),
            "market_value":  mv,
        })

    snapshot = {"positions": snapshot_positions}
    plan = generate_deposit_plan(snapshot, monthly_investment)

    if strategy == "user_behavior" and user_profile:
        plan = adjust_decision_plan(plan, user_profile)

    # Map symbol allocations to category buckets
    symbol_to_category = {p["symbol"]: p.get("category", "Other") for p in positions}
    split: dict[str, float] = {}
    for action in plan.get("actions", []):
        bucket = _CATEGORY_BUCKET.get(
            symbol_to_category.get(action["symbol"], "Other"), "stocks"
        )
        split[bucket] = split.get(bucket, 0.0) + float(action.get("amount", 0.0))

    return split
