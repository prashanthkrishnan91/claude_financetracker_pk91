"""Stage 10B — books-of-record integrity & reconciliation diagnostic.

Read-only. No writes. No live Plaid calls. No market-data calls.
Compares persisted position data against transaction-derived quantities
using the existing AVCO portfolio engine.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .portfolio_engine import build_positions, normalize_transactions

DIAGNOSTIC_VERSION = "books_reconciliation_v1"

# Drift thresholds — conservative defaults for financial data integrity.
# Allow up to 0.001 shares absolute drift (fractional-share rounding artifacts).
QUANTITY_ABS_TOLERANCE: float = 0.001
# Allow up to 1% relative quantity drift (normal AVCO rounding, DRIP rounding).
QUANTITY_PCT_TOLERANCE_PCT: float = 1.0
# Allow up to 2% cost-basis drift (AVCO rounding over many lots).
COST_BASIS_PCT_TOLERANCE_PCT: float = 2.0
# Quantity drift above this % is blocked rather than degraded.
QUANTITY_BLOCKED_THRESHOLD_PCT: float = 10.0

_NEAR_ZERO = 1e-9


def _pct(drift: float, base: float | None) -> float | None:
    if base is None or abs(base) < _NEAR_ZERO:
        return None
    return round(abs(drift) / abs(base) * 100.0, 4)


def _asset_type_from_category(category: str) -> str:
    c = (category or "").strip().lower()
    if c == "crypto":
        return "crypto"
    if c == "etf":
        return "etf"
    return "stock"


def _reconcile_ticker(
    ticker: str,
    pos_row: dict[str, Any] | None,
    tx_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Produce a per-ticker reconciliation record. Pure function — no I/O."""
    blockers: list[str] = []
    warnings: list[str] = []

    if pos_row is None:
        return {
            "ticker": ticker,
            "asset_type": None,
            "position_found": False,
            "current_quantity": None,
            "position_cost_basis": None,
            "avg_cost": None,
            "source": None,
            "transaction_history_found": bool(tx_rows),
            "transaction_quantity": None,
            "transaction_cost_basis": None,
            "quantity_drift": None,
            "quantity_drift_abs": None,
            "quantity_drift_pct": None,
            "cost_basis_drift": None,
            "cost_basis_drift_abs": None,
            "cost_basis_drift_pct": None,
            "plaid_source_present": False,
            "manual_position_detected": False,
            "crypto_or_pdf_position_detected": False,
            "reconciliation_status": "blocked",
            "blockers": ["position_not_found"],
            "warnings": [],
            "rationale": "No persisted position row found for this ticker.",
        }

    # Parse position fields defensively
    try:
        current_quantity = float(pos_row.get("shares") or 0)
    except (TypeError, ValueError):
        current_quantity = None

    try:
        avg_cost = float(pos_row.get("avg_cost") or 0)
    except (TypeError, ValueError):
        avg_cost = None

    category = str(pos_row.get("category") or "")
    source = str(pos_row.get("source") or "")
    asset_type = _asset_type_from_category(category)
    is_crypto = asset_type == "crypto"
    is_plaid = source == "plaid"
    is_manual = source == "manual"

    position_cost_basis: float | None = None
    if current_quantity is not None and avg_cost is not None:
        position_cost_basis = round(current_quantity * avg_cost, 6)

    # ── Validate quantity ────────────────────────────────────────────────────
    if current_quantity is None:
        blockers.append("position_quantity_missing")
    elif current_quantity < 0:
        blockers.append("position_quantity_negative")
    elif current_quantity < _NEAR_ZERO:
        blockers.append("position_quantity_zero")

    # ── Validate cost basis (non-crypto only) ────────────────────────────────
    if not is_crypto and current_quantity and current_quantity > _NEAR_ZERO:
        if avg_cost is None:
            blockers.append("avg_cost_missing")
        elif avg_cost < 0:
            blockers.append("avg_cost_negative")
        elif avg_cost < _NEAR_ZERO:
            # Zero avg_cost on a live non-crypto position is suspicious.
            # Degrade rather than block — bootstrap/manual rows may legitimately
            # carry zero cost if the user did not record purchase price.
            warnings.append("avg_cost_zero_cost_basis_unverifiable")

    # ── Crypto: not evaluable for AVCO reconciliation ────────────────────────
    if is_crypto and not blockers:
        return {
            "ticker": ticker,
            "asset_type": asset_type,
            "position_found": True,
            "current_quantity": current_quantity,
            "position_cost_basis": position_cost_basis,
            "avg_cost": avg_cost,
            "source": source,
            "transaction_history_found": bool(tx_rows),
            "transaction_quantity": None,
            "transaction_cost_basis": None,
            "quantity_drift": None,
            "quantity_drift_abs": None,
            "quantity_drift_pct": None,
            "cost_basis_drift": None,
            "cost_basis_drift_abs": None,
            "cost_basis_drift_pct": None,
            "plaid_source_present": is_plaid,
            "manual_position_detected": is_manual,
            "crypto_or_pdf_position_detected": True,
            "reconciliation_status": "not_evaluable",
            "blockers": [],
            "warnings": warnings + ["crypto_avco_reconciliation_not_applicable"],
            "rationale": (
                "Crypto position is visible and has data. "
                "AVCO Buy/Sell transaction reconciliation is not applicable for on-chain "
                "assets — position may originate from manual entry or external import."
            ),
        }

    # ── Early-return if already blocked ─────────────────────────────────────
    if blockers:
        return {
            "ticker": ticker,
            "asset_type": asset_type,
            "position_found": True,
            "current_quantity": current_quantity,
            "position_cost_basis": position_cost_basis,
            "avg_cost": avg_cost,
            "source": source,
            "transaction_history_found": bool(tx_rows),
            "transaction_quantity": None,
            "transaction_cost_basis": None,
            "quantity_drift": None,
            "quantity_drift_abs": None,
            "quantity_drift_pct": None,
            "cost_basis_drift": None,
            "cost_basis_drift_abs": None,
            "cost_basis_drift_pct": None,
            "plaid_source_present": is_plaid,
            "manual_position_detected": is_manual,
            "crypto_or_pdf_position_detected": is_crypto,
            "reconciliation_status": "blocked",
            "blockers": blockers,
            "warnings": warnings,
            "rationale": f"Position blocked by missing/invalid fields: {', '.join(blockers)}.",
        }

    # ── No transaction history ───────────────────────────────────────────────
    if not tx_rows:
        warnings.append("no_transaction_history_position_may_be_authoritative")
        if is_plaid:
            warnings.append("plaid_source_authoritative_no_tx_verification_possible")
        elif is_manual:
            warnings.append("manual_source_no_tx_verification_possible")
        return {
            "ticker": ticker,
            "asset_type": asset_type,
            "position_found": True,
            "current_quantity": current_quantity,
            "position_cost_basis": position_cost_basis,
            "avg_cost": avg_cost,
            "source": source,
            "transaction_history_found": False,
            "transaction_quantity": None,
            "transaction_cost_basis": None,
            "quantity_drift": None,
            "quantity_drift_abs": None,
            "quantity_drift_pct": None,
            "cost_basis_drift": None,
            "cost_basis_drift_abs": None,
            "cost_basis_drift_pct": None,
            "plaid_source_present": is_plaid,
            "manual_position_detected": is_manual,
            "crypto_or_pdf_position_detected": is_crypto,
            "reconciliation_status": "degraded",
            "blockers": [],
            "warnings": warnings,
            "rationale": (
                f"No Buy/Sell transactions found for {ticker} (source={source!r}). "
                "Plaid/manual positions may be authoritative without transaction history."
            ),
        }

    # ── Rebuild position from transactions via AVCO engine ───────────────────
    normalized = normalize_transactions(tx_rows)
    ticker_normalized = [t for t in normalized if t["symbol"] == ticker.upper()]

    if not ticker_normalized:
        # All rows were non-Buy/Sell (e.g. CDIV only)
        warnings.append("no_buy_sell_transactions_for_ticker_avco_rebuild_not_possible")
        return {
            "ticker": ticker,
            "asset_type": asset_type,
            "position_found": True,
            "current_quantity": current_quantity,
            "position_cost_basis": position_cost_basis,
            "avg_cost": avg_cost,
            "source": source,
            "transaction_history_found": False,
            "transaction_quantity": None,
            "transaction_cost_basis": None,
            "quantity_drift": None,
            "quantity_drift_abs": None,
            "quantity_drift_pct": None,
            "cost_basis_drift": None,
            "cost_basis_drift_abs": None,
            "cost_basis_drift_pct": None,
            "plaid_source_present": is_plaid,
            "manual_position_detected": is_manual,
            "crypto_or_pdf_position_detected": is_crypto,
            "reconciliation_status": "degraded",
            "blockers": [],
            "warnings": warnings,
            "rationale": (
                f"Transactions exist for {ticker} but none are Buy/Sell type; "
                "AVCO rebuild not possible."
            ),
        }

    built = build_positions(ticker_normalized)
    tx_pos = next((p for p in built if p["symbol"] == ticker.upper()), None)

    if tx_pos is None:
        # Transactions netted to zero shares — position is ahead of transaction history
        blockers.append("transaction_derived_quantity_zero_position_nonzero")
        return {
            "ticker": ticker,
            "asset_type": asset_type,
            "position_found": True,
            "current_quantity": current_quantity,
            "position_cost_basis": position_cost_basis,
            "avg_cost": avg_cost,
            "source": source,
            "transaction_history_found": True,
            "transaction_quantity": 0.0,
            "transaction_cost_basis": 0.0,
            "quantity_drift": round(0.0 - current_quantity, 8),
            "quantity_drift_abs": current_quantity,
            "quantity_drift_pct": 100.0,
            "cost_basis_drift": None,
            "cost_basis_drift_abs": None,
            "cost_basis_drift_pct": 100.0,
            "plaid_source_present": is_plaid,
            "manual_position_detected": is_manual,
            "crypto_or_pdf_position_detected": is_crypto,
            "reconciliation_status": "blocked",
            "blockers": blockers,
            "warnings": warnings,
            "rationale": (
                f"Transaction history nets to zero shares but position shows "
                f"quantity={current_quantity}. "
                "Transaction history may be incomplete or position includes Plaid/manual lots."
            ),
        }

    tx_quantity: float = tx_pos["total_quantity"]
    tx_avg_cost: float = tx_pos["avg_cost_basis"]
    tx_cost_basis = round(tx_quantity * tx_avg_cost, 6)

    qty_drift = round(tx_quantity - current_quantity, 8)
    qty_drift_abs = abs(qty_drift)
    qty_drift_pct = _pct(qty_drift, current_quantity)

    cb_drift: float | None = None
    cb_drift_abs: float | None = None
    cb_drift_pct: float | None = None
    if position_cost_basis is not None:
        cb_drift = round(tx_cost_basis - position_cost_basis, 6)
        cb_drift_abs = abs(cb_drift)
        cb_drift_pct = _pct(cb_drift, position_cost_basis)

    # Evaluate quantity drift
    qty_within_tol = qty_drift_abs <= QUANTITY_ABS_TOLERANCE or (
        qty_drift_pct is not None and qty_drift_pct <= QUANTITY_PCT_TOLERANCE_PCT
    )

    if not qty_within_tol:
        if qty_drift_pct is not None and qty_drift_pct > QUANTITY_BLOCKED_THRESHOLD_PCT:
            blockers.append(
                f"quantity_drift_blocked: drift={qty_drift:+.6f}shrs "
                f"({qty_drift_pct:.2f}% > {QUANTITY_BLOCKED_THRESHOLD_PCT}% threshold)"
            )
        else:
            warnings.append(
                f"quantity_drift_degraded: drift={qty_drift:+.6f}shrs "
                f"({qty_drift_pct:.4f}% > {QUANTITY_PCT_TOLERANCE_PCT}% tolerance)"
            )

    # Evaluate cost basis drift
    cb_within_tol = (cb_drift_pct is not None and cb_drift_pct <= COST_BASIS_PCT_TOLERANCE_PCT) or (
        cb_drift_abs is not None and cb_drift_abs < 0.01
    )
    if position_cost_basis is not None and cb_drift_pct is not None and not cb_within_tol:
        warnings.append(
            f"cost_basis_drift_degraded: drift={cb_drift:+.6f} "
            f"({cb_drift_pct:.4f}% > {COST_BASIS_PCT_TOLERANCE_PCT}% tolerance)"
        )

    # Final status
    if blockers:
        status = "blocked"
        rationale = (
            f"Ticker {ticker} blocked — position qty={current_quantity}, "
            f"tx-derived qty={tx_quantity}. Blockers: {'; '.join(blockers)}."
        )
    elif warnings:
        status = "degraded"
        rationale = (
            f"Ticker {ticker} degraded — position qty={current_quantity}, "
            f"tx-derived qty={tx_quantity}. Warnings: {'; '.join(warnings)}."
        )
    else:
        status = "facts_ready"
        rationale = (
            f"Ticker {ticker} matches: position qty={current_quantity}, "
            f"tx-derived qty={tx_quantity} "
            f"(drift={qty_drift:+.6f}shrs, {qty_drift_pct:.4f}%). "
            f"Cost basis within {COST_BASIS_PCT_TOLERANCE_PCT}% tolerance."
        )

    return {
        "ticker": ticker,
        "asset_type": asset_type,
        "position_found": True,
        "current_quantity": current_quantity,
        "position_cost_basis": position_cost_basis,
        "avg_cost": avg_cost,
        "source": source,
        "transaction_history_found": True,
        "transaction_quantity": tx_quantity,
        "transaction_cost_basis": tx_cost_basis,
        "quantity_drift": qty_drift,
        "quantity_drift_abs": qty_drift_abs,
        "quantity_drift_pct": qty_drift_pct,
        "cost_basis_drift": cb_drift,
        "cost_basis_drift_abs": cb_drift_abs,
        "cost_basis_drift_pct": cb_drift_pct,
        "plaid_source_present": is_plaid,
        "manual_position_detected": is_manual,
        "crypto_or_pdf_position_detected": is_crypto,
        "reconciliation_status": status,
        "blockers": blockers,
        "warnings": warnings,
        "rationale": rationale,
    }


async def run_books_reconciliation_diagnostic(
    db_client: Any,
    user_id: str,
    tickers: list[str] | None = None,
    include_not_evaluable: bool = True,
) -> dict[str, Any]:
    """Run books-of-record integrity diagnostic for a user.

    Reads positions and transactions from DB. Never writes. Never calls
    Plaid live. Never calls market-data providers.
    """
    started_at = datetime.now(timezone.utc)

    # ── Load positions ───────────────────────────────────────────────────────
    pos_query = db_client.table("positions").select("*").eq("user_id", str(user_id))
    if tickers:
        pos_query = pos_query.in_("ticker", [t.upper() for t in tickers])
    pos_rows: list[dict[str, Any]] = (pos_query.execute().data or [])

    pos_map: dict[str, dict[str, Any]] = {
        str(row.get("ticker") or "").upper(): row
        for row in pos_rows
        if row.get("ticker")
    }

    # ── Load Buy/Sell transactions ordered by date ───────────────────────────
    tx_query = (
        db_client.table("transactions")
        .select("ticker, tx_type, quantity, price, tx_date")
        .eq("user_id", str(user_id))
        .in_("tx_type", ["Buy", "Sell"])
        .order("tx_date", desc=False)
    )
    if tickers:
        tx_query = tx_query.in_("ticker", [t.upper() for t in tickers])
    tx_rows: list[dict[str, Any]] = (tx_query.execute().data or [])

    tx_by_ticker: dict[str, list[dict[str, Any]]] = {}
    for row in tx_rows:
        t = str(row.get("ticker") or "").upper()
        if t:
            tx_by_ticker.setdefault(t, []).append(row)

    # ── Determine evaluation scope ───────────────────────────────────────────
    eval_tickers: list[str] = sorted(pos_map.keys())
    if tickers:
        # Include explicitly requested tickers even if missing from positions
        requested_upper = [t.upper() for t in tickers]
        for t in requested_upper:
            if t not in pos_map:
                eval_tickers.append(t)
        eval_tickers = sorted(set(eval_tickers))

    # ── Reconcile ────────────────────────────────────────────────────────────
    per_ticker: list[dict[str, Any]] = [
        _reconcile_ticker(
            ticker=t,
            pos_row=pos_map.get(t),
            tx_rows=tx_by_ticker.get(t, []),
        )
        for t in eval_tickers
    ]

    if not include_not_evaluable:
        per_ticker = [r for r in per_ticker if r["reconciliation_status"] != "not_evaluable"]

    # ── Aggregate ────────────────────────────────────────────────────────────
    counts = {"facts_ready": 0, "degraded": 0, "blocked": 0, "not_evaluable": 0}
    for r in per_ticker:
        s = r["reconciliation_status"]
        if s in counts:
            counts[s] += 1

    # Global facts_ready: true only when no positions are blocked or degraded.
    # not_evaluable positions (crypto) are exempt but must have a safe rationale.
    global_facts_ready = (
        len(per_ticker) > 0
        and counts["blocked"] == 0
        and counts["degraded"] == 0
    )

    completed_at = datetime.now(timezone.utc)

    return {
        "diagnostic_version": DIAGNOSTIC_VERSION,
        "user_id": str(user_id),
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "tickers_requested": tickers or [],
        "positions_checked": len(eval_tickers),
        "positions_facts_ready_count": counts["facts_ready"],
        "positions_degraded_count": counts["degraded"],
        "positions_blocked_count": counts["blocked"],
        "positions_not_evaluable_count": counts["not_evaluable"],
        "facts_ready": global_facts_ready,
        "diagnostics_only": True,
        "writes_performed": 0,
        "policy_unchanged": True,
        "visible_snapshot_unchanged": True,
        "per_ticker": per_ticker,
    }
