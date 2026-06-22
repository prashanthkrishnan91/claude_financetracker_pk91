"""Stage 10C — VTI DCA benchmark diagnostic.

Read-only. No writes. No live provider calls. No market-data provider calls.

Compares the user's actual portfolio against a hypothetical VTI DCA strategy:
"If the user had put the same deposits into VTI on the same dates, what would
the benchmark value and return look like?"

Data sources:
- deposit_plans table (executed=True) as primary contribution source
- transactions table (Buy type) as fallback if no deposit_plans exist
- price_history table for historical VTI close prices
- portfolio_snapshots table for actual portfolio market value (total_equity)
- Books reconciliation gate passed in as parameter (not re-run inline)
"""

from __future__ import annotations

from datetime import date as _date
from datetime import datetime, timedelta, timezone
from typing import Any

DIAGNOSTIC_VERSION = "vti_dca_benchmark_v1"
VTI_TICKER = "VTI"
_NEAR_ZERO = 1e-9
# Maximum calendar days to search forward/backward for a VTI price on non-trading days.
_PRICE_SEARCH_DAYS = 7
# Explicit row limit for the bounded VTI price query — well above any realistic date window.
# Prevents Supabase's default 1000-row cap from silently truncating newer price rows.
_VTI_PRICE_FETCH_LIMIT = 10_000


def _find_vti_price(
    target_date: str,
    price_map: dict[str, float],
) -> tuple[float | None, str | None, str]:
    """Map a deposit date to a VTI close price.

    Returns (price, price_date_used, mapping_reason).
    Searches forward up to _PRICE_SEARCH_DAYS for the next trading day,
    then backward for the previous trading day. Never silently substitutes —
    every substitution is recorded in mapping_reason.
    """
    if target_date in price_map:
        return price_map[target_date], target_date, "exact_match"

    try:
        dt = _date.fromisoformat(target_date)
    except ValueError:
        return None, None, "invalid_date_format"

    for delta in range(1, _PRICE_SEARCH_DAYS + 1):
        candidate = (dt + timedelta(days=delta)).isoformat()
        if candidate in price_map:
            return price_map[candidate], candidate, "next_available_trading_day"

    for delta in range(1, _PRICE_SEARCH_DAYS + 1):
        candidate = (dt - timedelta(days=delta)).isoformat()
        if candidate in price_map:
            return price_map[candidate], candidate, "previous_available_trading_day"

    return None, None, "missing_price_data"


def _extract_books_gate(
    books_gate_result: dict[str, Any] | None,
) -> tuple[str, list[str], list[str]]:
    """Extract books gate status from a prior books reconciliation result.

    Returns (gate_status, blockers_to_add, warnings_to_add).
    gate_status is one of: "pass", "pass_with_exclusions", "blocked", "unavailable".
    """
    if books_gate_result is None:
        return "unavailable", [], ["books_gate_runtime_not_available"]

    gate = str(books_gate_result.get("benchmark_books_gate") or "")
    if gate == "pass":
        return "pass", [], []
    if gate == "pass_with_exclusions":
        per_ticker = books_gate_result.get("per_ticker") or []
        flagged = sorted(
            r["ticker"]
            for r in per_ticker
            if r.get("reconciliation_status") in ("blocked", "degraded")
        )
        warnings: list[str] = [
            "books_pass_with_exclusions_manual_review_recommended"
        ]
        if flagged:
            warnings.append(
                f"books_flagged_tickers_set_aside: {','.join(flagged)}"
            )
        return "pass_with_exclusions", [], warnings
    if gate == "blocked":
        reason = str(books_gate_result.get("benchmark_books_gate_reason") or "books_gate_blocked")
        return "blocked", [f"books_gate_blocked: {reason}"], []

    return "unavailable", [], ["books_gate_unknown_or_insufficient_evidence"]


async def run_vti_dca_benchmark_diagnostic(
    db_client: Any,
    user_id: str,
    start_date: str | None = None,
    end_date: str | None = None,
    include_position_breakdown: bool = True,
    books_gate_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run VTI DCA benchmark diagnostic for a user.

    Reads from: deposit_plans, transactions, price_history, portfolio_snapshots.
    Never writes. Never calls Plaid live. Never calls market-data providers.

    books_gate_result: pass the result of run_books_reconciliation_diagnostic()
    if available. If None, computation proceeds but benchmark_status will be
    degraded with warning "books_gate_runtime_not_available".
    """
    started_at = datetime.now(timezone.utc)
    benchmark_blockers: list[str] = []
    benchmark_warnings: list[str] = []

    # ── 1. Books gate ─────────────────────────────────────────────────────────
    books_gate, gate_blockers, gate_warnings = _extract_books_gate(books_gate_result)
    benchmark_blockers.extend(gate_blockers)
    benchmark_warnings.extend(gate_warnings)

    def _early_return(
        deposits_detected: int = 0,
        contribution_source: str | None = None,
        vti_rows_loaded: int = 0,
        vti_qstart: str | None = None,
        vti_qend: str | None = None,
        vti_truncated: bool = False,
    ) -> dict[str, Any]:
        return {
            "diagnostic_version": DIAGNOSTIC_VERSION,
            "user_id": str(user_id),
            "started_at": started_at.isoformat(),
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "start_date": start_date,
            "end_date": end_date,
            "deposits_detected_count": deposits_detected,
            "benchmark_contribution_count": 0,
            "actual_portfolio_value": None,
            "actual_cost_basis": None,
            "actual_return_abs": None,
            "actual_return_pct": None,
            "vti_dca_units": None,
            "vti_dca_cost_basis": None,
            "vti_dca_current_value": None,
            "vti_dca_return_abs": None,
            "vti_dca_return_pct": None,
            "relative_vs_vti_abs": None,
            "relative_vs_vti_pct": None,
            "benchmark_status": "blocked",
            "benchmark_blockers": benchmark_blockers,
            "benchmark_warnings": benchmark_warnings,
            "required_price_points_count": 0,
            "available_price_points_count": 0,
            "missing_price_points": [],
            "contribution_source_mode": contribution_source,
            "contribution_records": [],
            "vti_price_rows_loaded_count": vti_rows_loaded,
            "vti_price_query_start_date": vti_qstart,
            "vti_price_query_end_date": vti_qend,
            "vti_price_query_truncated": vti_truncated,
            "diagnostics_only": True,
            "writes_performed": 0,
            "policy_unchanged": True,
            "visible_snapshot_unchanged": True,
        }

    if books_gate == "blocked":
        return _early_return()

    # ── 2. Load contributions ─────────────────────────────────────────────────
    deposit_query = (
        db_client.table("deposit_plans")
        .select("id, deposit_date, amount, executed, executed_at")
        .eq("user_id", str(user_id))
        .eq("executed", True)
        .order("deposit_date", desc=False)
    )
    if start_date:
        deposit_query = deposit_query.gte("deposit_date", start_date)
    if end_date:
        deposit_query = deposit_query.lte("deposit_date", end_date)

    raw_deposit_rows: list[dict[str, Any]] = deposit_query.execute().data or []
    # Defensive Python-level filter: DB query uses eq("executed", True) but
    # mock clients in tests don't implement filters, so enforce here as well.
    deposit_rows = [r for r in raw_deposit_rows if r.get("executed") is True]

    contribution_amounts: list[tuple[str, float]] = []
    contribution_source_mode: str | None = None

    if deposit_rows:
        contribution_source_mode = "deposit_plans"
        for row in deposit_rows:
            deposit_date = str(row.get("deposit_date") or "").strip()
            try:
                amount = float(row.get("amount") or 0)
            except (TypeError, ValueError):
                amount = 0.0
            if deposit_date and amount > _NEAR_ZERO:
                contribution_amounts.append((deposit_date, amount))
    else:
        # Fallback: aggregate Buy transaction amounts per date
        tx_query = (
            db_client.table("transactions")
            .select("tx_type, quantity, price, tx_date, amount")
            .eq("user_id", str(user_id))
            .in_("tx_type", ["Buy"])
            .order("tx_date", desc=False)
        )
        if start_date:
            tx_query = tx_query.gte("tx_date", start_date)
        if end_date:
            tx_query = tx_query.lte("tx_date", end_date)

        tx_rows: list[dict[str, Any]] = tx_query.execute().data or []

        if tx_rows:
            date_amounts: dict[str, float] = {}
            for row in tx_rows:
                tx_date = str(row.get("tx_date") or "").strip()
                try:
                    amt = abs(float(row.get("amount") or 0))
                    if amt < _NEAR_ZERO:
                        qty = abs(float(row.get("quantity") or 0))
                        price = abs(float(row.get("price") or 0))
                        amt = qty * price
                except (TypeError, ValueError):
                    amt = 0.0
                if tx_date and amt > _NEAR_ZERO:
                    date_amounts[tx_date] = round(date_amounts.get(tx_date, 0.0) + amt, 2)

            contribution_amounts = sorted(date_amounts.items())
            contribution_source_mode = "buy_transactions_fallback"
            benchmark_warnings.append(
                "contribution_source_is_buy_transactions_not_deposit_plans: "
                "amounts may overstate contributions if reinvestment or DRIP transactions included"
            )
        else:
            benchmark_blockers.append("no_executed_deposits_or_buy_transactions_found")
            contribution_source_mode = "none"

    deposits_detected_count = len(contribution_amounts) if contribution_amounts else len(deposit_rows)

    if not contribution_amounts:
        return _early_return(
            deposits_detected=deposits_detected_count,
            contribution_source=contribution_source_mode,
        )

    # ── 3. Load VTI historical prices ─────────────────────────────────────────
    # Derive a bounded date window from contribution dates. Without this,
    # Supabase's default 1000-row cap silently drops newer price rows and
    # produces false "missing price" results for recent contribution dates.
    _contrib_date_strs = [d for d, _ in contribution_amounts]
    vti_query_start_date: str | None = None
    vti_query_end_date: str | None = None
    try:
        _min_contrib = _date.fromisoformat(min(_contrib_date_strs))
        _max_contrib = _date.fromisoformat(max(_contrib_date_strs))
        _window_start = _min_contrib - timedelta(days=_PRICE_SEARCH_DAYS)
        # Extend end to today so the most-recent VTI price is captured for current value.
        _window_end = max(
            _max_contrib + timedelta(days=_PRICE_SEARCH_DAYS),
            datetime.now(timezone.utc).date(),
        )
        vti_query_start_date = _window_start.isoformat()
        vti_query_end_date = _window_end.isoformat()
    except (ValueError, TypeError):
        pass  # fall through; proceed without date bounds as a safe fallback

    _price_query = (
        db_client.table("price_history")
        .select("price_date, close_price")
        .eq("ticker", VTI_TICKER)
    )
    if vti_query_start_date:
        _price_query = _price_query.gte("price_date", vti_query_start_date)
    if vti_query_end_date:
        _price_query = _price_query.lte("price_date", vti_query_end_date)
    vti_price_rows: list[dict[str, Any]] = (
        _price_query
        .order("price_date", desc=False)
        .limit(_VTI_PRICE_FETCH_LIMIT)
        .execute()
        .data or []
    )
    vti_price_rows_loaded = len(vti_price_rows)
    vti_price_query_truncated = vti_price_rows_loaded >= _VTI_PRICE_FETCH_LIMIT

    vti_price_map: dict[str, float] = {}
    for row in vti_price_rows:
        pd_str = str(row.get("price_date") or "").strip()
        try:
            cp = float(row.get("close_price") or 0)
        except (TypeError, ValueError):
            cp = 0.0
        if pd_str and cp > _NEAR_ZERO:
            vti_price_map[pd_str] = cp

    vti_current_price: float | None = None
    vti_current_price_date: str | None = None
    if vti_price_map:
        vti_current_price_date = max(vti_price_map.keys())
        vti_current_price = vti_price_map[vti_current_price_date]

    if not vti_price_map:
        benchmark_blockers.append("vti_price_history_unavailable_no_rows_in_price_history")
        return _early_return(
            deposits_detected=deposits_detected_count,
            contribution_source=contribution_source_mode,
            vti_rows_loaded=vti_price_rows_loaded,
            vti_qstart=vti_query_start_date,
            vti_qend=vti_query_end_date,
            vti_truncated=vti_price_query_truncated,
        )

    if vti_current_price is None:
        benchmark_blockers.append("vti_current_price_unavailable")
        return _early_return(
            deposits_detected=deposits_detected_count,
            contribution_source=contribution_source_mode,
            vti_rows_loaded=vti_price_rows_loaded,
            vti_qstart=vti_query_start_date,
            vti_qend=vti_query_end_date,
            vti_truncated=vti_price_query_truncated,
        )

    # ── 4. Map contributions to VTI prices ────────────────────────────────────
    contribution_records: list[dict[str, Any]] = []
    missing_price_points: list[str] = []
    required_price_points_count = len(contribution_amounts)
    available_price_points_count = 0

    vti_dca_units_total = 0.0
    vti_dca_cost_basis_total = 0.0

    for contrib_date, contrib_amount in contribution_amounts:
        price, price_date_used, mapping_reason = _find_vti_price(contrib_date, vti_price_map)

        if price is not None and price > _NEAR_ZERO:
            units_purchased = round(contrib_amount / price, 8)
            vti_dca_units_total += units_purchased
            vti_dca_cost_basis_total += contrib_amount
            available_price_points_count += 1
            contribution_records.append({
                "requested_date": contrib_date,
                "price_date_used": price_date_used,
                "price": round(price, 4),
                "mapping_reason": mapping_reason,
                "contribution_amount": round(contrib_amount, 2),
                "units_purchased": units_purchased,
            })
        else:
            missing_price_points.append(contrib_date)
            contribution_records.append({
                "requested_date": contrib_date,
                "price_date_used": None,
                "price": None,
                "mapping_reason": "missing_price_data",
                "contribution_amount": round(contrib_amount, 2),
                "units_purchased": None,
            })

    if missing_price_points:
        benchmark_warnings.append(
            f"missing_vti_prices_for_{len(missing_price_points)}_date(s): "
            "benchmark cost basis excludes those contribution dates"
        )

    if available_price_points_count == 0:
        benchmark_blockers.append("no_vti_prices_available_for_any_contribution_date")
        return _early_return(
            deposits_detected=deposits_detected_count,
            contribution_source=contribution_source_mode,
            vti_rows_loaded=vti_price_rows_loaded,
            vti_qstart=vti_query_start_date,
            vti_qend=vti_query_end_date,
            vti_truncated=vti_price_query_truncated,
        )

    benchmark_contribution_count = available_price_points_count

    # ── 5. Compute VTI DCA metrics ────────────────────────────────────────────
    vti_dca_units = round(vti_dca_units_total, 8)
    vti_dca_cost_basis = round(vti_dca_cost_basis_total, 2)
    vti_dca_current_value = round(vti_dca_units * vti_current_price, 2)
    vti_dca_return_abs = round(vti_dca_current_value - vti_dca_cost_basis, 2)
    vti_dca_return_pct: float | None = None
    if vti_dca_cost_basis > _NEAR_ZERO:
        vti_dca_return_pct = round(vti_dca_return_abs / vti_dca_cost_basis * 100.0, 4)

    # ── 6. Get actual portfolio value ─────────────────────────────────────────
    actual_portfolio_value: float | None = None
    actual_cost_basis: float | None = None
    actual_return_abs: float | None = None
    actual_return_pct: float | None = None

    snapshot_rows: list[dict[str, Any]] = (
        db_client.table("portfolio_snapshots")
        .select("total_equity, total_cost, snapshot_at")
        .eq("user_id", str(user_id))
        .order("snapshot_at", desc=True)
        .limit(1)
        .execute()
        .data or []
    )

    if snapshot_rows:
        snap = snapshot_rows[0]
        try:
            raw_equity = float(snap.get("total_equity") or 0)
            actual_portfolio_value = raw_equity if raw_equity > _NEAR_ZERO else None
        except (TypeError, ValueError):
            actual_portfolio_value = None
        try:
            raw_cost = float(snap.get("total_cost") or 0)
            actual_cost_basis = raw_cost if raw_cost > _NEAR_ZERO else None
        except (TypeError, ValueError):
            actual_cost_basis = None

    if actual_portfolio_value is None:
        benchmark_warnings.append(
            "actual_portfolio_value_unavailable: no portfolio_snapshots row found; "
            "relative_vs_vti metrics cannot be computed"
        )
    else:
        if actual_cost_basis is not None and actual_cost_basis > _NEAR_ZERO:
            actual_return_abs = round(actual_portfolio_value - actual_cost_basis, 2)
            actual_return_pct = round(actual_return_abs / actual_cost_basis * 100.0, 4)

    # ── 7. Relative comparison ────────────────────────────────────────────────
    relative_vs_vti_abs: float | None = None
    relative_vs_vti_pct: float | None = None

    if actual_portfolio_value is not None and vti_dca_current_value is not None:
        relative_vs_vti_abs = round(actual_portfolio_value - vti_dca_current_value, 2)
        if vti_dca_current_value > _NEAR_ZERO:
            relative_vs_vti_pct = round(
                relative_vs_vti_abs / vti_dca_current_value * 100.0, 4
            )

    # ── 8. Final benchmark_status ─────────────────────────────────────────────
    benchmark_status: str
    if benchmark_blockers:
        benchmark_status = "blocked"
    elif (
        books_gate == "pass"
        and not missing_price_points
        and actual_portfolio_value is not None
        and vti_dca_current_value is not None
    ):
        benchmark_status = "computed"
    else:
        benchmark_status = "degraded"

    completed_at = datetime.now(timezone.utc)
    return {
        "diagnostic_version": DIAGNOSTIC_VERSION,
        "user_id": str(user_id),
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "start_date": start_date,
        "end_date": end_date,
        "deposits_detected_count": deposits_detected_count,
        "benchmark_contribution_count": benchmark_contribution_count,
        "actual_portfolio_value": actual_portfolio_value,
        "actual_cost_basis": actual_cost_basis,
        "actual_return_abs": actual_return_abs,
        "actual_return_pct": actual_return_pct,
        "vti_dca_units": vti_dca_units,
        "vti_dca_cost_basis": vti_dca_cost_basis,
        "vti_dca_current_value": vti_dca_current_value,
        "vti_dca_return_abs": vti_dca_return_abs,
        "vti_dca_return_pct": vti_dca_return_pct,
        "relative_vs_vti_abs": relative_vs_vti_abs,
        "relative_vs_vti_pct": relative_vs_vti_pct,
        "benchmark_status": benchmark_status,
        "benchmark_blockers": benchmark_blockers,
        "benchmark_warnings": benchmark_warnings,
        "required_price_points_count": required_price_points_count,
        "available_price_points_count": available_price_points_count,
        "missing_price_points": missing_price_points,
        "contribution_source_mode": contribution_source_mode,
        "contribution_records": contribution_records if include_position_breakdown else [],
        "vti_price_rows_loaded_count": vti_price_rows_loaded,
        "vti_price_query_start_date": vti_query_start_date,
        "vti_price_query_end_date": vti_query_end_date,
        "vti_price_query_truncated": vti_price_query_truncated,
        "diagnostics_only": True,
        "writes_performed": 0,
        "policy_unchanged": True,
        "visible_snapshot_unchanged": True,
    }
