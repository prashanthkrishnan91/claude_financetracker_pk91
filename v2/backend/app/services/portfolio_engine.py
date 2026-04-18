"""Pure portfolio computation layer — no DB calls, fully testable.

All functions are stateless and accept plain dicts/lists.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

_NEAR_ZERO = 1e-6


def normalize_transactions(transactions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize raw DB transaction rows into a canonical representation.

    Returns a list of dicts with keys:
        symbol, quantity (signed: buy +, sell -), price, timestamp, asset_type.
    Rows with no ticker or zero quantity are dropped.
    """
    result: list[dict[str, Any]] = []
    for row in transactions:
        symbol = (row.get("ticker") or "").strip().upper()
        if not symbol:
            continue

        tx_type = (row.get("tx_type") or "").strip()
        raw_qty = float(row.get("quantity") or 0)
        price = float(row.get("price") or 0)
        timestamp = row.get("tx_date") or row.get("created_at") or ""

        if abs(raw_qty) < _NEAR_ZERO:
            continue

        if tx_type == "Buy":
            signed_qty = raw_qty
        elif tx_type == "Sell":
            signed_qty = -raw_qty
        else:
            # Non-trade rows (CDIV, DRIP, etc.) carry no share movement
            continue

        category = (row.get("category") or "").lower()
        asset_type = "crypto" if category == "crypto" else "stock"

        result.append({
            "symbol": symbol,
            "quantity": signed_qty,
            "price": price,
            "timestamp": str(timestamp),
            "asset_type": asset_type,
        })

    return result


def build_positions(transactions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate normalized transactions into per-symbol positions.

    Each returned dict has: symbol, total_quantity, avg_cost_basis, asset_type.
    Symbols whose net quantity is effectively zero are excluded.

    avg_cost_basis uses running weighted-average cost:
      - Buys raise the average: new_avg = (held*old_avg + qty*price) / (held+qty)
      - Sells leave the per-share average unchanged (AVCO method).
    """
    qty: dict[str, float] = defaultdict(float)
    avg_cost: dict[str, float] = defaultdict(float)
    asset_types: dict[str, str] = {}

    for tx in transactions:
        sym = tx["symbol"]
        tx_qty = tx["quantity"]
        tx_price = tx["price"]
        asset_types[sym] = tx.get("asset_type", "stock")

        if tx_qty > 0:
            # Buy: update running weighted average
            held = qty[sym]
            new_held = held + tx_qty
            avg_cost[sym] = (held * avg_cost[sym] + tx_qty * tx_price) / new_held
            qty[sym] = new_held
        else:
            # Sell: reduce quantity, average cost per share stays the same
            qty[sym] = max(0.0, qty[sym] + tx_qty)  # tx_qty is negative
            if qty[sym] < _NEAR_ZERO:
                qty[sym] = 0.0
                avg_cost[sym] = 0.0

    positions: list[dict[str, Any]] = []
    for sym, total_qty in qty.items():
        if total_qty < _NEAR_ZERO:
            continue

        positions.append({
            "symbol": sym,
            "total_quantity": round(total_qty, 8),
            "avg_cost_basis": round(avg_cost[sym], 6),
            "asset_type": asset_types[sym],
        })

    return positions


def build_portfolio_snapshot(
    positions: list[dict[str, Any]],
    prices: dict[str, float],
) -> dict[str, Any]:
    """Compute a portfolio snapshot from positions and a price map.

    Args:
        positions: output of build_positions()
        prices: {symbol: current_price}

    Returns a dict with:
        total_value, total_cost, total_pnl,
        positions (each enriched with weight, pnl, pnl_percent).
    """
    total_value = 0.0
    total_cost = 0.0
    enriched: list[dict[str, Any]] = []

    for pos in positions:
        sym = pos["symbol"]
        qty = pos["total_quantity"]
        avg_cost = pos["avg_cost_basis"]
        price = prices.get(sym)

        position_cost = qty * avg_cost
        if price is not None:
            position_value = qty * price
        else:
            # Fall back to cost basis when no price available
            position_value = position_cost

        total_value += position_value
        total_cost += position_cost

        enriched.append({
            **pos,
            "current_price": price,
            "market_value": round(position_value, 2),
            "cost_basis": round(position_cost, 2),
            "pnl": round(position_value - position_cost, 2),
            "pnl_percent": (
                round((position_value - position_cost) / position_cost * 100, 4)
                if position_cost > _NEAR_ZERO else 0.0
            ),
            # weight filled in after totals are known
            "weight": 0.0,
        })

    # Back-fill portfolio weights now that total_value is known
    for pos in enriched:
        pos["weight"] = (
            round(pos["market_value"] / total_value * 100, 4)
            if total_value > _NEAR_ZERO else 0.0
        )

    total_pnl = total_value - total_cost

    return {
        "total_value": round(total_value, 2),
        "total_cost": round(total_cost, 2),
        "total_pnl": round(total_pnl, 2),
        "total_pnl_percent": (
            round(total_pnl / total_cost * 100, 4) if total_cost > _NEAR_ZERO else 0.0
        ),
        "positions": enriched,
    }
