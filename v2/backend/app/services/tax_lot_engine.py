"""Pure tax-lot computation layer — no DB calls, fully testable.

Builds per-ticker tax lots from raw transaction rows (FIFO depletion on
sells), and derives per-lot tax status:
  - short-term vs long-term (holding period >= long_term_days)
  - days_until_long_term countdown for short-term lots
  - unrealized gain/loss and estimated tax impact at configured rates

Mirrors the conventions of portfolio_engine.py: stateless functions over
plain dicts/lists. Sells deplete the oldest lots first (FIFO); non-trade
rows (CDIV, DRIP without shares, ACH, ...) are ignored, matching
portfolio_engine.normalize_transactions().
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Optional

_NEAR_ZERO = 1e-6

LONG_TERM_HOLDING_DAYS = 365


def _parse_date(value: Any) -> Optional[date]:
    """Best-effort parse of a tx_date value (date, datetime, or ISO string)."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
        except ValueError:
            return None
    return None


def build_tax_lots(
    transactions: list[dict[str, Any]],
    *,
    as_of: Optional[date] = None,
    long_term_days: int = LONG_TERM_HOLDING_DAYS,
) -> dict[str, list[dict[str, Any]]]:
    """Build open tax lots per ticker from raw transaction rows.

    Each Buy creates a lot; each Sell depletes the oldest open lots first
    (FIFO). Rows without a ticker, without share movement, or with an
    unparseable date are skipped (same fail-closed posture as the AVCO
    position builder — a lot with no acquisition date cannot have a
    holding period).

    Returns {ticker: [lot, ...]} where each lot has:
      acquired_date (ISO str), quantity, cost_per_share, cost_basis,
      holding_days, is_long_term, days_until_long_term (0 when long-term),
      long_term_date (ISO str — the date the lot turns long-term).
    """
    today = as_of or date.today()

    # Group trade rows per ticker, sorted chronologically.
    trades: dict[str, list[tuple[date, str, float, float]]] = defaultdict(list)
    for row in transactions:
        ticker = (row.get("ticker") or row.get("symbol") or "").strip().upper()
        tx_type = (row.get("tx_type") or "").strip()
        if not ticker or tx_type not in ("Buy", "Sell"):
            continue
        qty = float(row.get("quantity") or 0)
        if qty < _NEAR_ZERO:
            continue
        tx_date = _parse_date(row.get("tx_date") or row.get("created_at"))
        if tx_date is None:
            continue
        price = float(row.get("price") or 0)
        trades[ticker].append((tx_date, tx_type, qty, price))

    lots_by_ticker: dict[str, list[dict[str, Any]]] = {}
    for ticker, rows in trades.items():
        rows.sort(key=lambda r: r[0])
        open_lots: list[dict[str, Any]] = []
        for tx_date, tx_type, qty, price in rows:
            if tx_type == "Buy":
                open_lots.append({
                    "acquired": tx_date,
                    "quantity": qty,
                    "cost_per_share": price,
                })
            else:  # Sell — FIFO depletion
                remaining = qty
                while remaining > _NEAR_ZERO and open_lots:
                    lot = open_lots[0]
                    take = min(lot["quantity"], remaining)
                    lot["quantity"] -= take
                    remaining -= take
                    if lot["quantity"] < _NEAR_ZERO:
                        open_lots.pop(0)

        enriched: list[dict[str, Any]] = []
        for lot in open_lots:
            acquired: date = lot["acquired"]
            holding_days = (today - acquired).days
            lt_date = acquired + timedelta(days=long_term_days)
            is_long_term = holding_days >= long_term_days
            enriched.append({
                "acquired_date": acquired.isoformat(),
                "quantity": round(lot["quantity"], 8),
                "cost_per_share": round(lot["cost_per_share"], 6),
                "cost_basis": round(lot["quantity"] * lot["cost_per_share"], 2),
                "holding_days": holding_days,
                "is_long_term": is_long_term,
                "days_until_long_term": 0 if is_long_term else max(0, (lt_date - today).days),
                "long_term_date": lt_date.isoformat(),
            })
        if enriched:
            lots_by_ticker[ticker] = enriched

    return lots_by_ticker


def enrich_lots_with_market(
    lots_by_ticker: dict[str, list[dict[str, Any]]],
    prices: dict[str, float],
    *,
    short_term_rate: float,
    long_term_rate: float,
) -> dict[str, list[dict[str, Any]]]:
    """Add unrealized gain/loss and estimated tax impact to each lot.

    A lot with no current price gets null market fields — never a fabricated
    value (Data Truth: missing data is reported missing, not invented).
    Estimated tax is only meaningful for gains; losses report a negative
    number (potential offset) at the same rate.
    """
    out: dict[str, list[dict[str, Any]]] = {}
    for ticker, lots in lots_by_ticker.items():
        price = prices.get(ticker)
        enriched_lots = []
        for lot in lots:
            lot = dict(lot)
            if price is not None:
                market_value = lot["quantity"] * price
                gain = market_value - lot["cost_basis"]
                rate = long_term_rate if lot["is_long_term"] else short_term_rate
                lot.update({
                    "current_price": round(price, 4),
                    "market_value": round(market_value, 2),
                    "unrealized_gain": round(gain, 2),
                    "unrealized_gain_pct": round(
                        (gain / lot["cost_basis"] * 100) if lot["cost_basis"] > 0 else 0.0, 4
                    ),
                    "tax_rate_applied": rate,
                    "estimated_tax_if_sold": round(gain * rate, 2),
                })
            else:
                lot.update({
                    "current_price": None,
                    "market_value": None,
                    "unrealized_gain": None,
                    "unrealized_gain_pct": None,
                    "tax_rate_applied": None,
                    "estimated_tax_if_sold": None,
                })
            enriched_lots.append(lot)
        out[ticker] = enriched_lots
    return out


def summarize_ticker_lots(lots: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate a ticker's lots: totals plus the nearest long-term countdown."""
    total_qty = sum(l["quantity"] for l in lots)
    total_cost = sum(l["cost_basis"] for l in lots)
    st_lots = [l for l in lots if not l["is_long_term"]]
    gains = [l["unrealized_gain"] for l in lots if l.get("unrealized_gain") is not None]
    return {
        "lot_count": len(lots),
        "total_quantity": round(total_qty, 8),
        "total_cost_basis": round(total_cost, 2),
        "short_term_lot_count": len(st_lots),
        "long_term_lot_count": len(lots) - len(st_lots),
        "next_long_term_countdown_days": (
            min(l["days_until_long_term"] for l in st_lots) if st_lots else 0
        ),
        "unrealized_gain_total": round(sum(gains), 2) if gains else None,
    }
