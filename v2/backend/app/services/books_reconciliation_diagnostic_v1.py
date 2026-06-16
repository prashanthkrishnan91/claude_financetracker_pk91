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
# Cost-basis match threshold used in forensics (same as reconciliation tolerance).
COST_BASIS_MATCH_THRESHOLD_PCT: float = COST_BASIS_PCT_TOLERANCE_PCT
# Cost-basis drift above this % is treated as material disagreement in the gate.
COST_BASIS_MATERIAL_DISAGREEMENT_PCT: float = 5.0

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


def _compute_ticker_forensics(
    all_tx_rows: list[dict[str, Any]],
    qty_drift_pct: float | None,
    cb_drift_pct: float | None,
) -> dict[str, Any]:
    """Read-only forensic analysis of all transaction rows for a blocked/degraded ticker.

    Surfaces raw transaction-type distribution and conservative adjustment hints
    without fabricating semantics. Never writes. Never calls external providers.
    """
    tx_type_counts: dict[str, int] = {}
    tx_qty_by_type: dict[str, float] = {}
    tx_cb_by_type: dict[str, float] = {}
    dates: list[str] = []

    for row in all_tx_rows:
        tx_type = (
            str(row.get("tx_type") or row.get("type") or "").strip() or "unknown"
        )
        try:
            qty = abs(float(row.get("quantity") or 0))
        except (TypeError, ValueError):
            qty = 0.0
        try:
            price = abs(float(row.get("price") or 0))
        except (TypeError, ValueError):
            price = 0.0

        tx_type_counts[tx_type] = tx_type_counts.get(tx_type, 0) + 1
        tx_qty_by_type[tx_type] = round(
            tx_qty_by_type.get(tx_type, 0.0) + qty, 8
        )
        tx_cb_by_type[tx_type] = round(
            tx_cb_by_type.get(tx_type, 0.0) + qty * price, 6
        )

        tx_date = str(row.get("tx_date") or row.get("date") or "").strip()
        if tx_date:
            dates.append(tx_date)

    first_transaction_date = min(dates) if dates else None
    last_transaction_date = max(dates) if dates else None

    buy_sell_types = {"Buy", "Sell"}
    ignored_tx_type_counts: dict[str, int] = {
        t: c for t, c in tx_type_counts.items() if t not in buy_sell_types
    }
    ignored_tx_qty_by_type: dict[str, float] = {
        t: tx_qty_by_type[t] for t in ignored_tx_type_counts
    }
    ignored_tx_cb_by_type: dict[str, float] = {
        t: tx_cb_by_type[t] for t in ignored_tx_type_counts
    }

    # Detect: large qty drift with matching cost basis — strong signal of unmodeled shares
    cost_basis_matches_but_quantity_drift_detected = bool(
        cb_drift_pct is not None
        and cb_drift_pct <= COST_BASIS_MATCH_THRESHOLD_PCT
        and qty_drift_pct is not None
        and qty_drift_pct > QUANTITY_BLOCKED_THRESHOLD_PCT
    )

    possible_unmodeled_adjustment_detected = False
    possible_unmodeled_adjustment_reason: str | None = None

    if cost_basis_matches_but_quantity_drift_detected:
        possible_unmodeled_adjustment_detected = True
        if ignored_tx_type_counts:
            types_str = ", ".join(sorted(ignored_tx_type_counts.keys()))
            possible_unmodeled_adjustment_reason = (
                f"quantity_drift_pct={qty_drift_pct:.2f}% "
                f"cost_basis_drift_pct={cb_drift_pct:.4f}% — "
                "large quantity gap with matching cost basis; "
                f"unmodeled_transaction_type_present types=[{types_str}]; "
                "shares may have arrived via transfer, reinvestment, split, or "
                "broker lot import not recorded as a Buy transaction"
            )
        else:
            possible_unmodeled_adjustment_reason = (
                f"quantity_drift_pct={qty_drift_pct:.2f}% "
                f"cost_basis_drift_pct={cb_drift_pct:.4f}% — "
                "large quantity gap with matching cost basis; "
                "no non-Buy/Sell transaction types found to explain gap; "
                "possible external transfer or broker lot import with no recorded transaction"
            )
    elif ignored_tx_type_counts:
        types_str = ", ".join(sorted(ignored_tx_type_counts.keys()))
        possible_unmodeled_adjustment_reason = (
            f"unmodeled_transaction_type_present types=[{types_str}]; "
            "raw transaction fields do not clearly indicate corporate actions — "
            "flagging as unmodeled without assuming semantics"
        )

    return {
        "transaction_type_counts": tx_type_counts,
        "transaction_quantity_by_type": tx_qty_by_type,
        "transaction_cost_basis_by_type": tx_cb_by_type,
        "ignored_transaction_type_counts": ignored_tx_type_counts,
        "ignored_transaction_quantity_by_type": ignored_tx_qty_by_type,
        "ignored_transaction_cost_basis_by_type": ignored_tx_cb_by_type,
        "first_transaction_date": first_transaction_date,
        "last_transaction_date": last_transaction_date,
        "possible_unmodeled_adjustment_detected": possible_unmodeled_adjustment_detected,
        "possible_unmodeled_adjustment_reason": possible_unmodeled_adjustment_reason,
        "cost_basis_matches_but_quantity_drift_detected": cost_basis_matches_but_quantity_drift_detected,
        "cost_basis_match_threshold_used": COST_BASIS_MATCH_THRESHOLD_PCT,
    }


def _enrich_ticker_with_forensics(
    ticker_result: dict[str, Any],
    all_tx_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Attach forensic fields to a blocked/degraded per-ticker result. No-op for others."""
    status = ticker_result.get("reconciliation_status")
    if status not in ("blocked", "degraded"):
        ticker_result["ticker_forensics"] = None
        return ticker_result

    ticker_result["ticker_forensics"] = _compute_ticker_forensics(
        all_tx_rows=all_tx_rows,
        qty_drift_pct=ticker_result.get("quantity_drift_pct"),
        cb_drift_pct=ticker_result.get("cost_basis_drift_pct"),
    )
    return ticker_result


def _compute_benchmark_books_gate(
    per_ticker: list[dict[str, Any]],
) -> dict[str, Any]:
    """Conservative gate: can the books support a VTI DCA benchmark run?

    Returns benchmark_books_gate, a reason string, and next_recommended_stage.
    Does not alter any position data or reconciliation status.
    """
    non_crypto = [
        r for r in per_ticker
        if not r.get("crypto_or_pdf_position_detected")
    ]

    if not non_crypto:
        return {
            "benchmark_books_gate": "unknown",
            "benchmark_books_gate_reason": "no_non_crypto_positions_evaluated",
            "next_recommended_stage": "stage10b_forensics_needed",
        }

    # Hard blockers: missing/invalid position fields
    hard_blocker_keys = {
        "position_not_found",
        "position_quantity_missing",
        "position_quantity_negative",
        "position_quantity_zero",
        "avg_cost_missing",
        "avg_cost_negative",
    }
    has_hard_blocked = any(
        r["reconciliation_status"] == "blocked"
        and any(
            any(hk in b for hk in hard_blocker_keys)
            for b in r.get("blockers", [])
        )
        for r in non_crypto
    )

    # Material cost-basis disagreement: cb drift > threshold AND NOT the
    # cost-basis-match+qty-drift pattern (which is explainable differently)
    has_cb_material_disagreement = any(
        r["reconciliation_status"] == "blocked"
        and r.get("cost_basis_drift_pct") is not None
        and r["cost_basis_drift_pct"] > COST_BASIS_MATERIAL_DISAGREEMENT_PCT
        and not (r.get("ticker_forensics") or {}).get(
            "cost_basis_matches_but_quantity_drift_detected", False
        )
        for r in non_crypto
    )

    if has_hard_blocked or has_cb_material_disagreement:
        reason = (
            "hard_blocked_positions_missing_or_invalid_data"
            if has_hard_blocked
            else "cost_basis_material_disagreement_exceeds_threshold"
        )
        return {
            "benchmark_books_gate": "blocked",
            "benchmark_books_gate_reason": reason,
            "next_recommended_stage": "stage10b_books_repair",
        }

    all_safe = all(
        r["reconciliation_status"] in ("facts_ready", "not_evaluable")
        for r in non_crypto
    )
    if all_safe:
        return {
            "benchmark_books_gate": "pass",
            "benchmark_books_gate_reason": "all_non_crypto_positions_facts_ready_or_not_evaluable",
            "next_recommended_stage": "stage10c_vti_benchmark",
        }

    blocked_degraded = [
        r for r in non_crypto
        if r["reconciliation_status"] in ("blocked", "degraded")
    ]

    # pass_with_exclusions: all blocked/degraded tickers must be explainable by
    # unmodeled transactions AND must have sane current position/cost-basis data.
    def _is_explainable(r: dict[str, Any]) -> bool:
        forensics = r.get("ticker_forensics") or {}
        position_sane = (
            r.get("current_quantity") is not None
            and (r.get("current_quantity") or 0.0) > _NEAR_ZERO
            and r.get("position_cost_basis") is not None
            and (r.get("position_cost_basis") or 0.0) > _NEAR_ZERO
        )
        explainable = forensics.get(
            "cost_basis_matches_but_quantity_drift_detected", False
        ) or forensics.get("possible_unmodeled_adjustment_detected", False)
        return position_sane and explainable

    if blocked_degraded and all(_is_explainable(r) for r in blocked_degraded):
        return {
            "benchmark_books_gate": "pass_with_exclusions",
            "benchmark_books_gate_reason": (
                "blocked_degraded_tickers_appear_explainable_by_unmodeled_transactions; "
                "current_position_and_cost_basis_data_present; "
                "manual_review_recommended_before_proceeding"
            ),
            "next_recommended_stage": "stage10b_manual_review",
        }

    return {
        "benchmark_books_gate": "unknown",
        "benchmark_books_gate_reason": (
            "blocked_degraded_tickers_not_clearly_explainable_by_forensic_evidence"
        ),
        "next_recommended_stage": "stage10b_forensics_needed",
    }


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

    # ── Load Buy/Sell transactions ordered by date (AVCO reconciliation) ───────
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

    # ── Load ALL transaction types for forensic analysis (read-only) ─────────
    all_tx_query = (
        db_client.table("transactions")
        .select("ticker, tx_type, quantity, price, tx_date, amount, fees")
        .eq("user_id", str(user_id))
        .order("tx_date", desc=False)
    )
    if tickers:
        all_tx_query = all_tx_query.in_("ticker", [t.upper() for t in tickers])
    all_tx_rows_raw: list[dict[str, Any]] = (all_tx_query.execute().data or [])

    all_tx_by_ticker: dict[str, list[dict[str, Any]]] = {}
    for row in all_tx_rows_raw:
        t = str(row.get("ticker") or "").upper()
        if t:
            all_tx_by_ticker.setdefault(t, []).append(row)

    # ── Determine evaluation scope ───────────────────────────────────────────
    eval_tickers: list[str] = sorted(pos_map.keys())
    if tickers:
        # Include explicitly requested tickers even if missing from positions
        requested_upper = [t.upper() for t in tickers]
        for t in requested_upper:
            if t not in pos_map:
                eval_tickers.append(t)
        eval_tickers = sorted(set(eval_tickers))

    # ── Reconcile + forensic enrichment ─────────────────────────────────────
    per_ticker: list[dict[str, Any]] = [
        _enrich_ticker_with_forensics(
            _reconcile_ticker(
                ticker=t,
                pos_row=pos_map.get(t),
                tx_rows=tx_by_ticker.get(t, []),
            ),
            all_tx_rows=all_tx_by_ticker.get(t, []),
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

    gate = _compute_benchmark_books_gate(per_ticker)

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
        "benchmark_books_gate": gate["benchmark_books_gate"],
        "benchmark_books_gate_reason": gate["benchmark_books_gate_reason"],
        "next_recommended_stage": gate["next_recommended_stage"],
        "diagnostics_only": True,
        "writes_performed": 0,
        "policy_unchanged": True,
        "visible_snapshot_unchanged": True,
        "per_ticker": per_ticker,
    }
