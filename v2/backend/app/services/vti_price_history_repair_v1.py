"""
Stage 10C.1 — VTI price history repair service.

Fetches VTI OHLCV from yfinance and upserts into price_history.
Idempotent: ON CONFLICT(ticker, price_date) DO UPDATE.
Writes ONLY VTI rows — ticker is hardcoded; no other ticker can be written.
dry_run=True by default — no DB writes without explicit dry_run=False.
Never fabricates prices: missing or zero-close points are dropped.

Supported backfill_period values:
  "1Y", "3Y", "5Y" — approximate calendar range via yfinance
  "max"            — full available history; use when oldest contribution date exceeds 5Y
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from .history_service import HistoryService

logger = logging.getLogger(__name__)

REPAIR_VERSION = "vti_price_history_repair_v1"
VTI_TICKER = "VTI"
_DEFAULT_BACKFILL_PERIOD = "5Y"
_PRICE_SEARCH_DAYS = 7   # ±days used by _find_vti_price in benchmark
_SAMPLE_MISSING_CAP = 10
_SAMPLE_CONTRIBUTION_CAP = 5
_NEAR_ZERO = 1e-9
# Approximate calendar days for each period (for coverage warning only)
_PERIOD_APPROX_DAYS = {"1Y": 365, "3Y": 1095, "5Y": 1826, "max": 99999}


# ── helpers ───────────────────────────────────────────────────────────────────

def _count_vti_rows(db_client) -> int:
    try:
        result = (
            db_client.table("price_history")
            .select("price_date", count="exact")
            .eq("ticker", VTI_TICKER)
            .execute()
        )
        return result.count or 0
    except Exception as e:
        logger.warning("vti_repair count_before error: %s", e)
        return -1


def _load_contribution_forensics(db_client, user_id: str) -> dict:
    """Load contribution dates from both sources and return full forensics.

    Always queries both deposit_plans and buy_transactions so the caller has
    complete visibility into why a source was or was not selected.

    Returns a dict with:
      selected_dates, selected_mode, selected_reason,
      executed_deposit_plans_count, buy_transactions_fallback_count
    """
    dp_dates: list[str] = []
    dp_error: str | None = None
    try:
        dp_result = (
            db_client.table("deposit_plans")
            .select("execution_date")
            .eq("user_id", user_id)
            .eq("executed", True)
            .execute()
        )
        dp_dates = sorted({
            row["execution_date"]
            for row in (dp_result.data or [])
            if row.get("execution_date")
        })
    except Exception as e:
        dp_error = f"{type(e).__name__}: {e}"
        logger.warning("vti_repair deposit_plans read error: %s", dp_error)

    tx_dates: list[str] = []
    tx_error: str | None = None
    try:
        tx_result = (
            db_client.table("transactions")
            .select("tx_date")
            .eq("user_id", user_id)
            .eq("tx_type", "Buy")
            .execute()
        )
        tx_dates = sorted({
            row["tx_date"]
            for row in (tx_result.data or [])
            if row.get("tx_date")
        })
    except Exception as e:
        tx_error = f"{type(e).__name__}: {e}"
        logger.warning("vti_repair transactions read error: %s", tx_error)

    # Selection logic: deposit_plans primary if non-empty, else buy_transactions fallback
    if dp_dates:
        selected_dates = dp_dates
        selected_mode = "deposit_plans_primary"
        selected_reason = "deposit_plans_executed_true_found"
    elif tx_dates:
        selected_dates = tx_dates
        selected_mode = "buy_transactions_fallback"
        selected_reason = "deposit_plans_empty_used_buy_transactions"
    else:
        selected_dates = []
        selected_mode = "buy_transactions_fallback"
        if dp_error:
            selected_reason = f"deposit_plans_read_error_{dp_error[:60]}"
        elif tx_error:
            selected_reason = f"transactions_read_error_{tx_error[:60]}"
        else:
            selected_reason = "no_contribution_dates_found"

    return {
        "selected_dates": selected_dates,
        "selected_mode": selected_mode,
        "selected_reason": selected_reason,
        "executed_deposit_plans_count": len(dp_dates),
        "buy_transactions_fallback_count": len(tx_dates),
    }


def _coverage_check(
    contribution_dates: list[str],
    price_index: set[str],
    search_days: int = _PRICE_SEARCH_DAYS,
) -> tuple[int, int, list[str]]:
    """Return (covered_count, missing_count, sample_missing_dates[:cap])."""
    covered = 0
    missing_dates: list[str] = []

    for d_str in contribution_dates:
        try:
            d = date.fromisoformat(d_str)
        except ValueError:
            continue
        found = False
        for delta in range(search_days + 1):
            if (d + timedelta(days=delta)).isoformat() in price_index:
                found = True
                break
            if delta > 0 and (d - timedelta(days=delta)).isoformat() in price_index:
                found = True
                break
        if found:
            covered += 1
        else:
            missing_dates.append(d_str)

    return covered, len(missing_dates), missing_dates[:_SAMPLE_MISSING_CAP]


def _period_coverage_warning(
    required_price_start_date: str | None,
    backfill_period: str,
) -> str | None:
    """Return a warning string if earliest contribution date may predate the period window."""
    if not required_price_start_date:
        return None
    approx_days = _PERIOD_APPROX_DAYS.get(backfill_period)
    if approx_days is None or approx_days >= 99999:
        return None  # "max" covers everything
    try:
        earliest = date.fromisoformat(required_price_start_date)
    except ValueError:
        return None
    period_start = date.today() - timedelta(days=approx_days)
    if earliest < period_start:
        return (
            f"earliest_contribution_date_{required_price_start_date}_predates_"
            f"{backfill_period}_window_{period_start.isoformat()}_"
            f"consider_backfill_period_max"
        )
    return None


# ── main entry point ──────────────────────────────────────────────────────────

async def run_vti_price_history_repair(
    db_client,
    user_id: str,
    dry_run: bool = True,
    backfill_period: str = _DEFAULT_BACKFILL_PERIOD,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict:
    """Fetch VTI price history from yfinance and optionally upsert to price_history.

    Returns a forensics dict describing what was found, what would be written
    (dry_run=True), or what was written (dry_run=False).

    backfill_period: "1Y", "3Y", "5Y", or "max" (full history).
    Use "max" when earliest contribution date predates 5Y window.
    """
    forensics = _load_contribution_forensics(db_client, user_id)
    contribution_dates = forensics["selected_dates"]

    # Filter contribution dates to requested range
    if start_date or end_date:
        contribution_dates = [
            d for d in contribution_dates
            if (start_date is None or d >= start_date)
            and (end_date is None or d <= end_date)
        ]

    required_price_start_date = contribution_dates[0] if contribution_dates else None
    required_price_end_date = contribution_dates[-1] if contribution_dates else None
    period_warning = _period_coverage_warning(required_price_start_date, backfill_period)

    vti_rows_before = _count_vti_rows(db_client)

    # Fetch from provider — never raises; empty list on failure
    history_service = HistoryService()
    try:
        fetched_points = await history_service.fetch_prices_from_provider(
            VTI_TICKER, backfill_period
        )
    finally:
        await history_service.close()

    # Filter by date range if requested; drop zero-close (fabrication guard)
    if start_date or end_date:
        fetched_points = [
            p for p in fetched_points
            if (start_date is None or p.date >= start_date)
            and (end_date is None or p.date <= end_date)
        ]
    valid_points = [p for p in fetched_points if p.close > _NEAR_ZERO]

    rows_written = 0
    write_error: str | None = None

    if not dry_run and valid_points:
        rows_to_write = [
            {
                "ticker": VTI_TICKER,  # hardcoded — prevents non-VTI writes
                "price_date": p.date,
                "open_price": p.open,
                "high_price": p.high,
                "low_price": p.low,
                "close_price": p.close,
                "volume": p.volume,
                "source": "yfinance",
            }
            for p in valid_points
        ]
        try:
            batch_size = 100
            for i in range(0, len(rows_to_write), batch_size):
                db_client.table("price_history").upsert(
                    rows_to_write[i:i + batch_size],
                    on_conflict="ticker,price_date",
                ).execute()
            rows_written = len(rows_to_write)
            logger.info(
                "vti_price_history_repair wrote %d VTI rows user=%s",
                rows_written, user_id,
            )
        except Exception as e:
            write_error = f"{type(e).__name__}: {e}"
            logger.error("vti_price_history_repair write error: %s", write_error)

    vti_rows_after = _count_vti_rows(db_client) if not dry_run else vti_rows_before

    price_index = {p.date for p in valid_points}
    covered, missing_count, sample_missing = _coverage_check(contribution_dates, price_index)

    return {
        "repair_version": REPAIR_VERSION,
        "dry_run": dry_run,
        "backfill_period_requested": backfill_period,
        "date_range_filter": {
            "start_date": start_date,
            "end_date": end_date,
        },
        "contribution_source": {
            "selected_mode": forensics["selected_mode"],
            "selected_reason": forensics["selected_reason"],
            "executed_deposit_plans_count": forensics["executed_deposit_plans_count"],
            "buy_transactions_fallback_count": forensics["buy_transactions_fallback_count"],
            "contribution_dates_count": len(contribution_dates),
            "required_price_start_date": required_price_start_date,
            "required_price_end_date": required_price_end_date,
            "sample_contribution_dates": contribution_dates[:_SAMPLE_CONTRIBUTION_CAP],
            "period_coverage_warning": period_warning,
        },
        "provider_fetch": {
            "fetched_points_total": len(fetched_points),
            "valid_points_after_zero_close_filter": len(valid_points),
            "provider_failure": len(fetched_points) == 0,
        },
        "write_result": {
            "rows_written": rows_written,
            "write_skipped_dry_run": dry_run,
            "write_error": write_error,
        },
        "price_history_row_counts": {
            "vti_rows_before": vti_rows_before,
            "vti_rows_after": vti_rows_after,
            "net_change": (
                (vti_rows_after - vti_rows_before)
                if vti_rows_before >= 0 and vti_rows_after >= 0
                else None
            ),
        },
        "coverage": {
            "contribution_dates_checked": len(contribution_dates),
            "covered_within_search_window": covered,
            "missing_count": missing_count,
            "search_window_days": _PRICE_SEARCH_DAYS,
            "sample_missing_dates": sample_missing,
        },
    }
