"""Stage 11A — Financial Truth Baseline Diagnostic.

Read-only. No writes. No provider calls. No LLM calls.
Inspects existing Supabase tables to determine whether the app's displayed
portfolio value, cost basis, and recommendation inputs can be trusted.
"""

from __future__ import annotations

import math
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Any

DIAGNOSTIC_VERSION = "financial_truth_baseline_v1"

# Staleness thresholds
SNAPSHOT_STALE_HOURS: int = 24
PRICE_STALE_BUSINESS_DAYS: int = 3   # equity markets closed weekends; count Mon-Fri only
INTEL_STALE_HOURS: int = 48

# Price history fetch limit — prevents silent Supabase 1000-row truncation (Stage 10C.2 VTI bug)
_PRICE_HISTORY_FETCH_LIMIT: int = 10_000

# Reconciliation tolerance bands
RECONCILIATION_CERTIFIED_PCT: float = 1.0   # within 1%  → certified
RECONCILIATION_DEGRADED_PCT: float = 5.0    # within 5%  → degraded; above → blocked

# Per-ticker list caps (avoid bloat in output)
_MAX_LIST_CAP: int = 20

_NEAR_ZERO: float = 1e-9

# Transaction type groups
_BUY_TYPES = {"Buy"}
_SELL_TYPES = {"Sell"}
_DIVIDEND_TYPES = {"CDIV", "DRIP"}
_DEPOSIT_TYPES = {"ACH", "RTP"}


# ── Utility helpers ───────────────────────────────────────────────────────────

def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


def _business_days_since(price_date: date, today: date) -> int:
    """Count Mon-Fri days strictly between price_date and today (inclusive)."""
    if today <= price_date:
        return 0
    count = 0
    d = price_date + timedelta(days=1)
    while d <= today:
        if d.weekday() < 5:  # Mon=0 … Fri=4
            count += 1
        d += timedelta(days=1)
    return count


def _is_price_stale(price_date: date, today: date) -> bool:
    return _business_days_since(price_date, today) > PRICE_STALE_BUSINESS_DAYS


def _parse_dt(val: Any) -> datetime | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
    try:
        s = str(val).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _parse_date(val: Any) -> date | None:
    if val is None:
        return None
    if isinstance(val, date) and not isinstance(val, datetime):
        return val
    if isinstance(val, datetime):
        return val.date()
    try:
        return datetime.strptime(str(val)[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def _hours_since(dt: datetime | None) -> float | None:
    if dt is None:
        return None
    now = _now_utc()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return round((now - dt).total_seconds() / 3600.0, 2)


def _days_since_date(d: date | None) -> int | None:
    if d is None:
        return None
    return (date.today(), d)[0].__class__.__sub__(_now_utc().date(), d) if False else (_now_utc().date() - d).days


def _safe_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _finite_float(val: Any) -> float | None:
    """Same as ``_safe_float`` but also rejects NaN/±infinity — a value that
    parses but isn't finite is arithmetic garbage, never usable truth."""
    v = _safe_float(val)
    return v if v is not None and math.isfinite(v) else None


def _pct_diff(a: float, b: float) -> float | None:
    if abs(b) < _NEAR_ZERO:
        return None
    return round(abs(a - b) / abs(b) * 100.0, 4)


# ── Section 1: Snapshot truth ─────────────────────────────────────────────────

def _snapshot_truth(rows: list[dict]) -> dict[str, Any]:
    if not rows:
        return {
            "status": "unavailable",
            "reason": "no_snapshots_found",
            "latest_snapshot_at": None,
            "latest_portfolio_value": None,
            "latest_cost_basis": None,
            "latest_cash_balance": None,
            "snapshot_invested_value": None,
            "snapshot_age_hours": None,
            "snapshot_is_stale": None,
            "snapshot_count": 0,
        }

    # rows already ordered desc by snapshot_at — take first
    row = rows[0]
    snapshot_at = _parse_dt(row.get("snapshot_at"))
    age_hours = _hours_since(snapshot_at)
    is_stale = (age_hours is not None and age_hours > SNAPSHOT_STALE_HOURS)
    total_equity = _finite_float(row.get("total_equity"))
    total_cost = _safe_float(row.get("total_cost"))
    # cash_balance is arithmetic data, not a truthy flag — 0.0 and negative
    # finite values are legitimate and must never be coerced away.
    cash_balance = _finite_float(row.get("cash_balance"))
    invested_value = (
        round(total_equity - cash_balance, 2)
        if total_equity is not None and cash_balance is not None
        else None
    )

    warnings: list[str] = []
    if is_stale:
        warnings.append(f"snapshot_stale: last snapshot {age_hours:.1f}h ago (threshold {SNAPSHOT_STALE_HOURS}h)")
    if total_equity is None:
        warnings.append("portfolio_value_null: total_equity is null or non-finite in latest snapshot")
    if total_cost is None:
        warnings.append("cost_basis_null: total_cost is null in latest snapshot")
    if cash_balance is None:
        warnings.append("cash_balance_null: cash_balance is missing or non-finite in latest snapshot")

    return {
        "status": "ok",
        "latest_snapshot_at": row.get("snapshot_at"),
        "latest_portfolio_value": total_equity,
        "latest_cost_basis": total_cost,
        "latest_cash_balance": cash_balance,
        "snapshot_invested_value": invested_value,
        "snapshot_age_hours": age_hours,
        "snapshot_is_stale": is_stale,
        "snapshot_count": len(rows),
        "warnings": warnings,
    }


# ── Section 2: Position-derived truth ────────────────────────────────────────

def _position_truth(pos_rows: list[dict], price_rows: list[dict]) -> dict[str, Any]:
    if not pos_rows:
        return {
            "status": "unavailable",
            "reason": "no_positions_found",
            "open_position_count": 0,
            "total_position_count": 0,
            "cost_basis_sum": None,
            "cost_basis_feasible": False,
            "market_value_sum": None,
            "market_value_feasible": False,
            "missing_price_or_cost_basis_tickers": [],
            "duplicate_active_tickers": [],
            "open_tickers": [],
            "warnings": [],
        }

    open_positions = [
        r for r in pos_rows
        if r.get("ticker")
        and (r.get("category") or "").upper() != "SELL"
        and (_safe_float(r.get("shares")) or 0.0) > 0
    ]

    # Duplicate ticker check
    ticker_counts: Counter = Counter(r.get("ticker") for r in open_positions)
    duplicate_tickers = sorted(t for t, c in ticker_counts.items() if c > 1)[:_MAX_LIST_CAP]

    # Build latest price index
    latest_price_by_ticker: dict[str, dict] = {}
    for r in price_rows:
        t = r.get("ticker")
        if t and t not in latest_price_by_ticker:
            latest_price_by_ticker[t] = r

    cost_total = 0.0
    cost_feasible = True
    mv_total = 0.0
    mv_feasible = True
    missing_cost: list[str] = []
    missing_price: list[str] = []

    for pos in open_positions:
        ticker = pos.get("ticker") or "UNKNOWN"
        shares = _safe_float(pos.get("shares"))
        avg_cost = _safe_float(pos.get("avg_cost"))

        if shares is None or avg_cost is None:
            missing_cost.append(ticker)
            cost_feasible = False
        else:
            cost_total += shares * avg_cost

        if ticker not in latest_price_by_ticker:
            missing_price.append(ticker)
            mv_feasible = False
        else:
            close_price = _safe_float(latest_price_by_ticker[ticker].get("close_price"))
            if close_price is None or shares is None:
                missing_price.append(ticker)
                mv_feasible = False
            else:
                mv_total += shares * close_price

    warnings: list[str] = []
    if duplicate_tickers:
        warnings.append(f"duplicate_active_positions: {duplicate_tickers}")
    if missing_cost:
        warnings.append(f"missing_cost_basis_fields for {len(missing_cost)} ticker(s)")
    if missing_price:
        warnings.append(f"missing_current_price for {len(missing_price)} ticker(s) — market value unavailable")

    return {
        "status": "ok",
        "open_position_count": len(open_positions),
        "total_position_count": len(pos_rows),
        "cost_basis_sum": round(cost_total, 2) if cost_feasible else None,
        "cost_basis_feasible": cost_feasible,
        "market_value_sum": round(mv_total, 2) if mv_feasible else None,
        "market_value_feasible": mv_feasible,
        "missing_price_or_cost_basis_tickers": list(dict.fromkeys(missing_cost + missing_price))[:_MAX_LIST_CAP],
        "duplicate_active_tickers": duplicate_tickers,
        "open_tickers": [r.get("ticker") for r in open_positions],
        "warnings": warnings,
    }


# ── Section 3: Transaction-derived truth ──────────────────────────────────────

def _transaction_truth(tx_rows: list[dict]) -> dict[str, Any]:
    if not tx_rows:
        return {
            "status": "unavailable",
            "reason": "no_transactions_found",
            "transaction_count": 0,
            "latest_transaction_at": None,
            "buy_count": 0,
            "sell_count": 0,
            "dividend_count": 0,
            "deposit_count": 0,
            "cost_basis_from_transactions_feasible": False,
            "deposit_proxy_warning": None,
            "warnings": ["no_transactions: cannot derive cost basis from transactions"],
        }

    tx_count = len(tx_rows)
    buy_count = sell_count = div_count = deposit_count = 0
    latest_dt: datetime | None = None

    for row in tx_rows:
        tx_type = (row.get("tx_type") or "").strip()
        if tx_type in _BUY_TYPES:
            buy_count += 1
        elif tx_type in _SELL_TYPES:
            sell_count += 1
        elif tx_type in _DIVIDEND_TYPES:
            div_count += 1
        elif tx_type in _DEPOSIT_TYPES:
            deposit_count += 1

        tx_date_val = row.get("tx_date") or row.get("created_at")
        tx_dt = _parse_dt(tx_date_val)
        if tx_dt and (latest_dt is None or tx_dt > latest_dt):
            latest_dt = tx_dt

    cost_basis_feasible = buy_count > 0

    deposit_proxy_warning: str | None = None
    if deposit_count == 0 and buy_count > 0:
        deposit_proxy_warning = (
            "no_deposit_transactions: deposits (ACH/RTP) are absent — buy transactions "
            "may be used as contribution proxy but this overstates cost basis by fees/timing"
        )

    warnings: list[str] = []
    if deposit_proxy_warning:
        warnings.append(deposit_proxy_warning)
    if not cost_basis_feasible:
        warnings.append("no_buy_transactions: transaction-derived cost basis not feasible")

    return {
        "status": "ok",
        "transaction_count": tx_count,
        "latest_transaction_at": latest_dt.isoformat() if latest_dt else None,
        "buy_count": buy_count,
        "sell_count": sell_count,
        "dividend_count": div_count,
        "deposit_count": deposit_count,
        "cost_basis_from_transactions_feasible": cost_basis_feasible,
        "deposit_proxy_warning": deposit_proxy_warning,
        "warnings": warnings,
    }


# ── Section 4: Price truth ────────────────────────────────────────────────────

def _price_truth(
    price_rows: list[dict],
    open_tickers: list[str],
    _today: date | None = None,
    price_rows_loaded_count: int | None = None,
) -> dict[str, Any]:
    if not open_tickers:
        return {
            "status": "unavailable",
            "reason": "no_open_positions_to_check",
            "tickers_checked": 0,
            "tickers_with_recent_price": 0,
            "stale_price_tickers": [],
            "missing_price_tickers": [],
            "latest_price_dates": {},
            "price_rows_loaded": price_rows_loaded_count,
            "price_query_truncated": None,
        }

    # Latest price per ticker
    latest_by_ticker: dict[str, dict] = {}
    for r in price_rows:
        t = r.get("ticker")
        if t and t not in latest_by_ticker:
            latest_by_ticker[t] = r

    today = _today if _today is not None else _now_utc().date()
    stale: list[dict] = []
    missing: list[str] = []
    latest_dates: dict[str, str | None] = {}

    for ticker in open_tickers:
        if ticker not in latest_by_ticker:
            missing.append(ticker)
            latest_dates[ticker] = None
            continue

        row = latest_by_ticker[ticker]
        price_date = _parse_date(row.get("price_date"))
        latest_dates[ticker] = str(price_date) if price_date else None

        if price_date is None:
            stale.append({"ticker": ticker, "latest_price_date": None, "business_days_old": None})
        elif _is_price_stale(price_date, today):
            bdays = _business_days_since(price_date, today)
            stale.append({"ticker": ticker, "latest_price_date": str(price_date), "business_days_old": bdays})

    recent_count = len(open_tickers) - len(missing) - len(stale)

    truncated = (
        price_rows_loaded_count >= _PRICE_HISTORY_FETCH_LIMIT
        if price_rows_loaded_count is not None
        else None
    )

    warnings: list[str] = []
    if missing:
        warnings.append(f"missing_price_data for {len(missing)} ticker(s)")
    if stale:
        warnings.append(f"stale_price_data for {len(stale)} ticker(s) (>{PRICE_STALE_BUSINESS_DAYS} business days old)")
    if truncated:
        warnings.append(f"price_query_truncated: loaded {price_rows_loaded_count} rows (limit {_PRICE_HISTORY_FETCH_LIMIT})")

    return {
        "status": "ok",
        "tickers_checked": len(open_tickers),
        "tickers_with_recent_price": max(recent_count, 0),
        "stale_price_tickers": stale[:_MAX_LIST_CAP],
        "missing_price_tickers": missing[:_MAX_LIST_CAP],
        "latest_price_dates": {t: latest_dates.get(t) for t in list(latest_dates)[:_MAX_LIST_CAP]},
        "price_rows_loaded": price_rows_loaded_count,
        "price_query_truncated": truncated,
        "warnings": warnings,
    }


# ── Section 5: Intelligence-layer contamination ───────────────────────────────

def _intel_truth(
    rec_rows: list[dict],
    agent_rows: list[dict],
    intel_snap_rows: list[dict] | None,
    intel_snap_available: bool,
) -> dict[str, Any]:
    latest_rec_dt = _parse_dt(rec_rows[0].get("created_at")) if rec_rows else None
    latest_agent_dt = _parse_dt(agent_rows[0].get("started_at") or agent_rows[0].get("finished_at")) if agent_rows else None
    latest_intel_dt = _parse_dt(intel_snap_rows[0].get("created_at")) if intel_snap_rows else None

    rec_age_h = _hours_since(latest_rec_dt)
    agent_age_h = _hours_since(latest_agent_dt)
    intel_age_h = _hours_since(latest_intel_dt)

    rec_stale = (rec_age_h is not None and rec_age_h > INTEL_STALE_HOURS)
    agent_stale = (agent_age_h is not None and agent_age_h > INTEL_STALE_HOURS)
    intel_stale = (intel_age_h is not None and intel_age_h > INTEL_STALE_HOURS) if intel_snap_available else None

    warnings: list[str] = []
    if not rec_rows:
        warnings.append("no_recommendations_found")
    elif rec_stale:
        warnings.append(f"recommendations_stale: last recommendation {rec_age_h:.1f}h ago")
    if not agent_rows:
        warnings.append("no_agent_runs_found")
    elif agent_stale:
        warnings.append(f"agent_runs_stale: last run {agent_age_h:.1f}h ago")
    if not intel_snap_available:
        warnings.append("intel_snapshot_table_unavailable: table not found or not accessible")

    warnings.append(
        "recommendations_not_product_trusted: advisor/recommendation output remains blocked "
        "until financial truth baseline is certified or explicitly accepted as degraded"
    )

    return {
        "latest_recommendation_at": latest_rec_dt.isoformat() if latest_rec_dt else None,
        "recommendation_count": len(rec_rows),
        "latest_agent_run_at": latest_agent_dt.isoformat() if latest_agent_dt else None,
        "latest_intel_snapshot_at": latest_intel_dt.isoformat() if latest_intel_dt else None,
        "intel_snapshot_table_exists": intel_snap_available,
        "recommendations_stale": rec_stale,
        "agent_runs_stale": agent_stale,
        "intel_snapshot_stale": intel_stale,
        "recommendations_unsafe_if_truth_degraded": True,
        "warnings": warnings,
    }


# ── Section 6: Reconciliation ─────────────────────────────────────────────────

def _reconciliation(
    snapshot_portfolio_value: float | None,
    snapshot_cash_balance: float | None,
    snapshot_invested_value: float | None,
    position_mv: float | None,
) -> dict[str, Any]:
    """Compares the snapshot's INVESTED value (``total_equity -
    cash_balance``) against position-derived market value — the snapshot's
    raw ``total_equity`` includes cash and must never be compared directly
    against a positions-only sum."""
    blockers: list[str] = []
    warnings: list[str] = []

    if snapshot_invested_value is None:
        blockers.append(
            "snapshot_invested_value_unavailable: total_equity or cash_balance missing/non-finite"
        )
    if position_mv is None:
        blockers.append("position_market_value_unavailable: requires current prices for all open positions")

    if blockers:
        return {
            "snapshot_portfolio_value": snapshot_portfolio_value,
            "snapshot_cash_balance": snapshot_cash_balance,
            "snapshot_invested_value": snapshot_invested_value,
            "position_derived_market_value": position_mv,
            "absolute_difference": None,
            "percentage_difference": None,
            "reconciliation_status": "unavailable",
            "blockers": blockers,
            "warnings": warnings,
        }

    abs_diff = round(abs(snapshot_invested_value - position_mv), 2)
    pct = _pct_diff(snapshot_invested_value, position_mv)

    if pct is None:
        rec_status = "unavailable"
        blockers.append("division_by_zero: position_market_value is zero")
    elif pct <= RECONCILIATION_CERTIFIED_PCT:
        rec_status = "pass"
    elif pct <= RECONCILIATION_DEGRADED_PCT:
        rec_status = "degraded"
        warnings.append(
            f"values_differ: snapshot_invested={snapshot_invested_value:.2f} vs position_mv={position_mv:.2f} "
            f"({pct:.2f}% — exceeds certified threshold of {RECONCILIATION_CERTIFIED_PCT}%)"
        )
    else:
        rec_status = "blocked"
        warnings.append(
            f"values_diverge_critically: snapshot_invested={snapshot_invested_value:.2f} vs position_mv={position_mv:.2f} "
            f"({pct:.2f}% — exceeds degraded threshold of {RECONCILIATION_DEGRADED_PCT}%)"
        )

    return {
        "snapshot_portfolio_value": snapshot_portfolio_value,
        "snapshot_cash_balance": snapshot_cash_balance,
        "snapshot_invested_value": snapshot_invested_value,
        "position_derived_market_value": position_mv,
        "absolute_difference": abs_diff,
        "percentage_difference": pct,
        "reconciliation_status": rec_status,
        "blockers": blockers,
        "warnings": warnings,
    }


# ── Section 7: Verdict ────────────────────────────────────────────────────────

def _verdict(
    snap: dict,
    pos: dict,
    tx: dict,
    prices: dict,
    intel: dict,
    recon: dict,
) -> dict[str, Any]:
    blockers: list[str] = []

    snap_ok = snap.get("status") == "ok" and snap.get("latest_portfolio_value") is not None
    snap_stale = snap.get("snapshot_is_stale", False)
    pos_ok = pos.get("status") == "ok"
    pos_mv_ok = pos.get("market_value_feasible", False)
    tx_ok = tx.get("status") == "ok"
    prices_missing = len(prices.get("missing_price_tickers") or []) > 0
    prices_stale = len(prices.get("stale_price_tickers") or []) > 0
    duplicates = bool(pos.get("duplicate_active_tickers"))
    rec_status = recon.get("reconciliation_status")

    if not snap_ok and not pos_mv_ok:
        blockers.append("no_usable_portfolio_value_source")

    if rec_status == "blocked":
        blockers.append("reconciliation_blocked: portfolio values diverge beyond tolerance")

    if snap_stale and not pos_mv_ok:
        blockers.append("snapshot_stale_and_no_position_market_value_fallback")

    # Determine truth status
    if blockers:
        truth_status = "blocked"
    elif (
        snap_stale
        or prices_missing
        or prices_stale
        or duplicates
        or rec_status == "degraded"
        or not tx_ok
    ):
        truth_status = "degraded"
    else:
        truth_status = "certified"

    # Canonical sources
    if snap_ok and not snap_stale:
        canonical_pv_source = "portfolio_snapshots"
    elif pos_mv_ok:
        canonical_pv_source = "position_derived (positions × price_history)"
    else:
        canonical_pv_source = "unavailable"

    if pos.get("cost_basis_feasible"):
        canonical_cb_source = "positions (shares × avg_cost)"
    elif tx.get("cost_basis_from_transactions_feasible"):
        canonical_cb_source = "transactions (AVCO from buy history)"
    else:
        canonical_cb_source = "unavailable"

    # Unsafe sources
    unsafe: list[str] = []
    if snap_stale:
        unsafe.append("portfolio_snapshots (stale)")
    if prices_stale:
        unsafe.append("position_market_value (stale prices)")
    if duplicates:
        unsafe.append("positions (duplicate active tickers inflate sums)")
    if rec_status in ("degraded", "blocked", "unavailable"):
        unsafe.append("recommendations (financial truth not certified)")

    # Next required fix
    if blockers:
        if "no_usable_portfolio_value_source" in blockers:
            next_fix = "Populate price_history for all open tickers so position-derived market value can be computed"
        elif "reconciliation_blocked" in " ".join(blockers):
            pct = recon.get("percentage_difference")
            next_fix = (
                f"Investigate {pct:.1f}% divergence between snapshot value and position-derived value; "
                f"reconcile position shares or refresh portfolio_snapshots"
            )
        else:
            next_fix = "Resolve all blockers listed above before trusting portfolio value"
    elif truth_status == "degraded":
        if snap_stale:
            next_fix = "Trigger a portfolio snapshot refresh to reduce snapshot age below 24h"
        elif prices_missing or prices_stale:
            next_fix = "Backfill price_history for missing/stale tickers"
        elif duplicates:
            next_fix = "Remove or close duplicate active positions"
        else:
            next_fix = "Resolve warnings above to achieve certified truth status"
    else:
        next_fix = "No immediate fix required — financial truth is certified"

    return {
        "truth_status": truth_status,
        "canonical_portfolio_value_source": canonical_pv_source,
        "canonical_cost_basis_source": canonical_cb_source,
        "unsafe_sources_to_ignore": unsafe,
        "recommendations_trusted": False,
        "next_required_fix": next_fix,
        "blockers": blockers,
    }


# ── Main entry point ──────────────────────────────────────────────────────────

class FinancialTruthReadError(Exception):
    """A CORE truth query (snapshot/positions/price-history) failed — never
    silently reinterpreted as an empty/healthy portfolio. Distinguishable
    from a legitimate empty result (no rows is a valid outcome; a query
    exception is not)."""

    def __init__(self, failed_tables: list[str]):
        self.failed_tables = failed_tables
        super().__init__(f"financial_truth_core_read_failed: {','.join(failed_tables)}")


async def _gather_truth_sections(db_client: Any, user_id: str) -> dict[str, Any]:
    """The ONE query+derivation pass backing both the public diagnostic and
    the strict Run Intel preflight — no parallel truth formula. Additive
    underscore-prefixed keys carry the exact raw rows and core-query-failure
    state; ``run_financial_truth_baseline`` strips them for backward
    compatibility."""
    generated_at = _now_utc().isoformat()
    failed_tables: list[str] = []

    # ── 1. Portfolio snapshots ────────────────────────────────────────────────
    try:
        snap_res = (
            db_client.table("portfolio_snapshots")
            .select("id,snapshot_at,total_equity,total_cost,total_pnl,total_pnl_pct,cash_balance,created_at")
            .eq("user_id", user_id)
            .order("snapshot_at", desc=True)
            .execute()
        )
        snap_rows: list[dict] = snap_res.data or []
        snap_section = _snapshot_truth(snap_rows)
    except Exception as exc:
        failed_tables.append("portfolio_snapshots")
        snap_section = {
            "status": "unavailable",
            "reason": f"query_failed: {type(exc).__name__}: {exc}",
            "latest_snapshot_at": None,
            "latest_portfolio_value": None,
            "latest_cost_basis": None,
            "latest_cash_balance": None,
            "snapshot_invested_value": None,
            "snapshot_age_hours": None,
            "snapshot_is_stale": None,
            "snapshot_count": 0,
            "warnings": [],
        }

    # ── 2. Positions (full row — Run Intel scope freeze needs the same exact
    #      rows that produced this verdict: shares/avg_cost/category plus
    #      drip/tax fields, never a second independently-derived query) ──────
    try:
        pos_res = (
            db_client.table("positions")
            .select("ticker,shares,avg_cost,category,source,drip_shares,drip_cost,lt_eligible,lt_date")
            .eq("user_id", user_id)
            .execute()
        )
        pos_rows: list[dict] = pos_res.data or []
    except Exception:
        failed_tables.append("positions")
        pos_rows = []

    open_positions = [
        r for r in pos_rows
        if r.get("ticker")
        and (r.get("category") or "").upper() != "SELL"
        and (_safe_float(r.get("shares")) or 0.0) > 0
    ]
    open_tickers: list[str] = [r.get("ticker") for r in open_positions]

    # ── 3. Price history ──────────────────────────────────────────────────────
    try:
        if open_tickers:
            ph_res = (
                db_client.table("price_history")
                .select("ticker,price_date,close_price")
                .in_("ticker", open_tickers)
                .order("price_date", desc=True)
                .limit(_PRICE_HISTORY_FETCH_LIMIT)
                .execute()
            )
            price_rows: list[dict] = ph_res.data or []
        else:
            price_rows = []
    except Exception:
        failed_tables.append("price_history")
        price_rows = []

    pos_section = _position_truth(pos_rows, price_rows)
    price_section = _price_truth(price_rows, open_tickers, price_rows_loaded_count=len(price_rows))

    # ── 4. Transactions ───────────────────────────────────────────────────────
    try:
        tx_res = (
            db_client.table("transactions")
            .select("ticker,tx_type,quantity,price,amount,tx_date,created_at")
            .eq("user_id", user_id)
            .order("tx_date", desc=True)
            .execute()
        )
        tx_rows: list[dict] = tx_res.data or []
    except Exception:
        tx_rows = []

    tx_section = _transaction_truth(tx_rows)

    # ── 5. Intelligence layer ─────────────────────────────────────────────────
    try:
        rec_res = (
            db_client.table("recommendations")
            .select("id,created_at,action,is_active")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .execute()
        )
        rec_rows: list[dict] = rec_res.data or []
    except Exception:
        rec_rows = []

    try:
        agent_res = (
            db_client.table("agent_runs")
            .select("id,started_at,finished_at,status")
            .eq("user_id", user_id)
            .order("started_at", desc=True)
            .execute()
        )
        agent_rows: list[dict] = agent_res.data or []
    except Exception:
        agent_rows = []

    intel_snap_rows: list[dict] | None = None
    intel_snap_available = False
    try:
        is_res = (
            db_client.table("intel_snapshots")
            .select("id,created_at")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .execute()
        )
        intel_snap_rows = is_res.data or []
        intel_snap_available = True
    except Exception:
        intel_snap_rows = None
        intel_snap_available = False

    intel_section = _intel_truth(rec_rows, agent_rows, intel_snap_rows, intel_snap_available)

    # ── 6. Reconciliation ─────────────────────────────────────────────────────
    recon_section = _reconciliation(
        snap_section.get("latest_portfolio_value"),
        snap_section.get("latest_cash_balance"),
        snap_section.get("snapshot_invested_value"),
        pos_section.get("market_value_sum"),
    )

    # ── 7. Verdict ────────────────────────────────────────────────────────────
    verdict_section = _verdict(snap_section, pos_section, tx_section, price_section, intel_section, recon_section)

    return {
        "diagnostic_version": DIAGNOSTIC_VERSION,
        "generated_at": generated_at,
        "user_id": user_id,
        "snapshot_truth": snap_section,
        "position_derived_truth": pos_section,
        "transaction_derived_truth": tx_section,
        "price_truth": price_section,
        "intelligence_layer": intel_section,
        "reconciliation": recon_section,
        "verdict": verdict_section,
        "_core_read_failed": bool(failed_tables),
        "_failed_tables": failed_tables,
        "_open_positions": open_positions,
        "_price_rows": price_rows,
    }


async def run_financial_truth_baseline(
    db_client: Any,
    user_id: str,
) -> dict[str, Any]:
    """Stage 11A — Financial Truth Baseline Diagnostic.

    Read-only. No writes. No provider calls. No LLM calls.
    Returns a structured audit of the app's financial data integrity.
    """
    full = await _gather_truth_sections(db_client, user_id)
    return {k: v for k, v in full.items() if not k.startswith("_")}


async def run_financial_truth_baseline_strict(
    db_client: Any,
    user_id: str,
) -> dict[str, Any]:
    """Same computation as ``run_financial_truth_baseline`` (one truth
    formula, never duplicated), but for the Run Intel preflight ONLY: raises
    ``FinancialTruthReadError`` when a CORE table query (snapshot/positions/
    price-history) failed — never silently reports an empty/healthy
    portfolio — and additionally returns the exact raw open-position rows
    (``_open_positions``) and price rows (``_price_rows``) that produced
    this verdict, so the caller can freeze its scope from these SAME rows
    with no second query and no time-of-check/time-of-use gap.
    """
    full = await _gather_truth_sections(db_client, user_id)
    if full["_core_read_failed"]:
        raise FinancialTruthReadError(full["_failed_tables"])
    return full
