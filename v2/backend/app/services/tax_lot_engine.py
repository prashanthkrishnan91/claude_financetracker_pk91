"""Tax-lot engine — FIFO lots with event classification and reconciliation gating.

Pure computation over plain dicts (no DB, no providers, no LLM). The consolidation
contract's tax-lot rules are enforced here:

- Every transaction type present in the production ingestion contract
  (``Buy``, ``Sell``, ``CDIV``, ``DRIP``, ``SPL``, ``ACH``, ``RTP``, ``Other``)
  is explicitly classified; an unknown or share-affecting-but-unmodelable event
  is NEVER silently ignored — it blocks authoritative display for its ticker and
  is surfaced in diagnostics.
- Open-lot quantity reconciles against certified position shares, and lot cost
  basis against certified position basis, within documented tolerances; a ticker
  whose ledger does not reconcile shows a blocked state, not numbers.
- Long-term status uses the calendar-date anniversary (shares become long-term
  the day AFTER the one-year anniversary of acquisition), not a 365-day
  shortcut. Feb-29 acquisitions use Mar-1 as the anniversary in non-leap years.
- Jurisdiction assumption is explicit: US federal holding-period convention.
  All outputs are ESTIMATES for planning only, never tax advice, and no dollar
  tax liability is ever computed (that requires sale quantity, lot selection,
  and the filer's actual rates).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Optional

ENGINE_VERSION = "tax_lot_engine_v1"

# Reconciliation tolerances (documented contract):
# - quantity: |lot shares − position shares| ≤ max(0.0001 shares, 0.1% of position shares)
# - basis:    |lot basis − position basis| ≤ 2.0% of position basis (matches the
#   books-reconciliation diagnostic's COST_BASIS_MATCH_THRESHOLD_PCT)
QUANTITY_ABS_TOLERANCE = 1e-4
QUANTITY_REL_TOLERANCE_PCT = 0.1
BASIS_TOLERANCE_PCT = 2.0

_NEAR_ZERO = 1e-9

JURISDICTION_NOTE = (
    "Holding periods use the US federal convention (long-term begins the day "
    "after the one-year calendar anniversary). Estimates only — not tax advice."
)
NOT_RECONCILED_MESSAGE = (
    "Tax-lot details need reconciliation before they can be relied on."
)

# Event classification vocabulary
SHARE_INCREASING = "share_increasing"
SHARE_DECREASING = "share_decreasing"
BASIS_ADJUSTING = "basis_adjusting"
NON_SHARE_AFFECTING = "non_share_affecting"
UNSUPPORTED_UNKNOWN = "unsupported_unknown"

# Reconciliation statuses
STATUS_RECONCILED = "reconciled"
STATUS_QUANTITY_MISMATCH = "quantity_mismatch"
STATUS_BASIS_MISMATCH = "basis_mismatch"
STATUS_BLOCKED_UNSUPPORTED = "blocked_unsupported_events"
STATUS_OVERSOLD = "blocked_share_ledger_oversold"
STATUS_NO_TRANSACTIONS = "no_transaction_history"


def _parse_date(value: Any) -> Optional[date]:
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


def classify_transaction(row: dict[str, Any]) -> str:
    """Classify one transaction row against the production tx_type vocabulary.

    Fail-closed: anything that moves shares in a way this engine cannot model
    (splits without ratios, unknown codes carrying quantity) is
    ``unsupported_unknown`` — never dropped.
    """
    tx_type = str(row.get("tx_type") or "").strip()
    qty = float(row.get("quantity") or 0.0)
    price = row.get("price")
    has_qty = abs(qty) > _NEAR_ZERO

    if tx_type == "Buy":
        if not has_qty:
            return NON_SHARE_AFFECTING
        # A share-adding Buy without a positive price cannot establish basis —
        # fail closed (same rule as DRIP) instead of minting a zero-basis lot
        # that would fabricate a 100% gain.
        if price is None or float(price) <= 0:
            return UNSUPPORTED_UNKNOWN
        return SHARE_INCREASING
    if tx_type == "Sell":
        return SHARE_DECREASING if has_qty else NON_SHARE_AFFECTING
    if tx_type == "DRIP":
        # Dividend reinvestment: share-increasing only when it carries both
        # quantity and price (basis). A DRIP with shares but no price cannot
        # establish basis → unsupported, blocks the ticker.
        if has_qty and price is not None and float(price) > 0:
            return SHARE_INCREASING
        if has_qty:
            return UNSUPPORTED_UNKNOWN
        return NON_SHARE_AFFECTING
    if tx_type == "SPL":
        # Stock split / reverse split. The ingestion contract does not carry a
        # split ratio, so a SPL row with share movement cannot be modeled.
        return UNSUPPORTED_UNKNOWN if has_qty else NON_SHARE_AFFECTING
    if tx_type in ("CDIV", "ACH", "RTP"):
        # Cash dividend / cash transfers — but if one ever carries share
        # quantity it is NOT safe to ignore.
        return UNSUPPORTED_UNKNOWN if has_qty else NON_SHARE_AFFECTING
    # "Other" and anything outside the vocabulary (inbound/outbound share
    # transfers, mergers, spin-offs, corporate actions land here today).
    return UNSUPPORTED_UNKNOWN if has_qty else NON_SHARE_AFFECTING


def long_term_anniversary(acquired: date) -> date:
    """Calendar one-year anniversary (Feb 29 → Mar 1 in non-leap years)."""
    try:
        return acquired.replace(year=acquired.year + 1)
    except ValueError:  # Feb 29 in a non-leap target year
        return date(acquired.year + 1, 3, 1)


def long_term_start_date(acquired: date) -> date:
    """The first date the holding is estimated long-term (day after anniversary)."""
    return long_term_anniversary(acquired) + timedelta(days=1)


def build_ticker_ledger(
    transactions: list[dict[str, Any]],
    *,
    as_of: Optional[date] = None,
) -> dict[str, dict[str, Any]]:
    """Build a per-ticker FIFO lot ledger with full event accounting.

    Returns {ticker: {open_lots, event_counts, unsupported_events, oversold,
    missing_date_events, lot_shares, lot_cost_basis}}.
    """
    today = as_of or date.today()
    by_ticker: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in transactions:
        ticker = str(row.get("ticker") or "").strip().upper()
        if not ticker:
            continue
        by_ticker[ticker].append(row)

    ledgers: dict[str, dict[str, Any]] = {}
    for ticker, rows in by_ticker.items():
        event_counts: dict[str, int] = defaultdict(int)
        unsupported: list[dict[str, Any]] = []
        missing_date = 0
        oversold = False

        dated: list[tuple[date, dict[str, Any], str]] = []
        for row in rows:
            cls = classify_transaction(row)
            event_counts[cls] += 1
            if cls == UNSUPPORTED_UNKNOWN:
                unsupported.append({
                    "tx_type": str(row.get("tx_type") or "unknown"),
                    "tx_date": str(row.get("tx_date") or ""),
                    "quantity": row.get("quantity"),
                    "reason": "share_affecting_event_not_modelable",
                })
                continue
            if cls == NON_SHARE_AFFECTING:
                continue
            tx_date = _parse_date(row.get("tx_date"))
            if tx_date is None:
                # A share-affecting event without a date cannot anchor a
                # holding period — treat as unsupported, never guess.
                missing_date += 1
                unsupported.append({
                    "tx_type": str(row.get("tx_type") or "unknown"),
                    "tx_date": None,
                    "quantity": row.get("quantity"),
                    "reason": "share_affecting_event_missing_date",
                })
                continue
            dated.append((tx_date, row, cls))

        dated.sort(key=lambda item: item[0])
        open_lots: list[dict[str, Any]] = []
        for tx_date, row, cls in dated:
            qty = abs(float(row.get("quantity") or 0.0))
            if cls == SHARE_INCREASING:
                open_lots.append({
                    "acquired": tx_date,
                    "quantity": qty,
                    "cost_per_share": float(row.get("price") or 0.0),
                    "source_tx_type": str(row.get("tx_type")),
                })
            elif cls == SHARE_DECREASING:
                remaining = qty
                while remaining > _NEAR_ZERO and open_lots:
                    lot = open_lots[0]
                    take = min(lot["quantity"], remaining)
                    lot["quantity"] -= take
                    remaining -= take
                    if lot["quantity"] < _NEAR_ZERO:
                        open_lots.pop(0)
                if remaining > QUANTITY_ABS_TOLERANCE:
                    oversold = True

        lot_shares = sum(l["quantity"] for l in open_lots)
        lot_basis = sum(l["quantity"] * l["cost_per_share"] for l in open_lots)
        ledgers[ticker] = {
            "open_lots": open_lots,
            "event_counts": dict(event_counts),
            "unsupported_events": unsupported,
            "oversold": oversold,
            "lot_shares": round(lot_shares, 8),
            "lot_cost_basis": round(lot_basis, 6),
            "as_of": today,
        }
    return ledgers


def reconcile_ledger(
    ledger: dict[str, Any],
    position_shares: float,
    position_cost_basis: Optional[float],
) -> dict[str, Any]:
    """Reconcile a ticker's lot ledger against its certified position row."""
    lot_shares = ledger["lot_shares"]
    share_diff = lot_shares - position_shares
    qty_tolerance = max(QUANTITY_ABS_TOLERANCE, abs(position_shares) * QUANTITY_REL_TOLERANCE_PCT / 100.0)

    result: dict[str, Any] = {
        "position_shares": round(position_shares, 8),
        "lot_shares": lot_shares,
        "share_difference": round(share_diff, 8),
        "quantity_tolerance": qty_tolerance,
        "position_cost_basis": round(position_cost_basis, 2) if position_cost_basis is not None else None,
        "lot_cost_basis": round(ledger["lot_cost_basis"], 2),
        "basis_difference_pct": None,
        "basis_tolerance_pct": BASIS_TOLERANCE_PCT,
    }

    if ledger["oversold"]:
        result["status"] = STATUS_OVERSOLD
        return result
    if ledger["unsupported_events"]:
        result["status"] = STATUS_BLOCKED_UNSUPPORTED
        return result
    if abs(share_diff) > qty_tolerance:
        result["status"] = STATUS_QUANTITY_MISMATCH
        return result
    if position_cost_basis is not None and position_cost_basis > _NEAR_ZERO:
        basis_diff_pct = abs(ledger["lot_cost_basis"] - position_cost_basis) / position_cost_basis * 100.0
        result["basis_difference_pct"] = round(basis_diff_pct, 4)
        if basis_diff_pct > BASIS_TOLERANCE_PCT:
            result["status"] = STATUS_BASIS_MISMATCH
            return result
    result["status"] = STATUS_RECONCILED
    return result


def present_lots(
    ledger: dict[str, Any],
    current_price: Optional[float],
    *,
    as_of: Optional[date] = None,
) -> list[dict[str, Any]]:
    """Presentation rows for reconciled lots. Missing price → null market fields
    (missing data is reported missing, never invented)."""
    today = as_of or ledger.get("as_of") or date.today()
    rows: list[dict[str, Any]] = []
    for lot in ledger["open_lots"]:
        acquired: date = lot["acquired"]
        lt_start = long_term_start_date(acquired)
        is_long_term = today >= lt_start
        cost_basis = lot["quantity"] * lot["cost_per_share"]
        row: dict[str, Any] = {
            "acquired_date": acquired.isoformat(),
            "source_tx_type": lot.get("source_tx_type"),
            "remaining_shares": round(lot["quantity"], 8),
            "cost_per_share": round(lot["cost_per_share"], 6),
            "cost_basis": round(cost_basis, 2),
            "estimated_holding_classification": "long_term" if is_long_term else "short_term",
            "estimated_long_term_start_date": lt_start.isoformat(),
            "days_until_long_term": 0 if is_long_term else (lt_start - today).days,
        }
        if current_price is not None and current_price > 0:
            market_value = lot["quantity"] * current_price
            gain = market_value - cost_basis
            row.update({
                "current_value": round(market_value, 2),
                "unrealized_gain": round(gain, 2),
                "unrealized_gain_pct": round(gain / cost_basis * 100.0, 4) if cost_basis > _NEAR_ZERO else None,
            })
        else:
            row.update({
                "current_value": None,
                "unrealized_gain": None,
                "unrealized_gain_pct": None,
            })
        rows.append(row)
    return rows
