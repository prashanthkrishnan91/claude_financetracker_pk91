"""Stage 11B — Current price truth repair.

Repairs price_history for open-position tickers that are missing or stale.
Uses per-ticker DB queries to avoid Supabase row-cap truncation (the same
issue that caused Stage 10C.2's VTI truncation bug: a bulk query over all
tickers silently capped at 1000 rows).

Fetches current/latest prices via:
  - yfinance (Yahoo Finance v8 chart API) for equities and ETFs
  - CoinGecko (free, no key) for crypto tickers with a known coin ID

Writes only to price_history. No LLM calls, no recommendation changes,
no snapshot mutations, no Buy/Hold/Trim/Sell policy changes.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DIAGNOSTIC_VERSION = "current_price_truth_repair_v1"
PRICE_STALE_BUSINESS_DAYS = 3   # same threshold as Stage 11A

# Per-ticker query limit — we only need the most recent row; 10 is a safe
# ceiling that can never be mistaken for a 1000-row Supabase default-cap hit.
_PER_TICKER_LIMIT = 10

# Suspicious total: if a bulk approach ever loaded exactly this many rows it
# would signal Supabase default-cap truncation. Used only in the diagnostic
# field — this service uses per-ticker queries and cannot hit this cap.
_SUPABASE_DEFAULT_ROW_CAP = 1000

_COINGECKO_BASE = "https://api.coingecko.com/api/v3"
_YF_CHART_BASE = "https://query1.finance.yahoo.com/v8/finance/chart"
_HTTP_TIMEOUT = 10.0

# Crypto tickers supported via CoinGecko (free, no key required)
_CRYPTO_IDS: dict[str, str] = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "XRP": "ripple",
    "SOL": "solana",
    "DOGE": "dogecoin",
    "ADA": "cardano",
    "AVAX": "avalanche-2",
    "MATIC": "matic-network",
    "DOT": "polkadot",
    "LTC": "litecoin",
}

# yfinance symbol overrides for crypto tickers
_CRYPTO_YF: dict[str, str] = {
    "BTC": "BTC-USD",
    "ETH": "ETH-USD",
    "XRP": "XRP-USD",
    "SOL": "SOL-USD",
    "DOGE": "DOGE-USD",
    "ADA": "ADA-USD",
}


# ── Utility helpers ───────────────────────────────────────────────────────────

def _safe_float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_date(v: Any) -> date | None:
    if v is None:
        return None
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except (ValueError, TypeError):
        return None


def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


def _business_days_since(price_date: date, today: date) -> int:
    """Count Mon-Fri days strictly after price_date up to and including today."""
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


# ── Position loading ──────────────────────────────────────────────────────────

def _load_open_positions(db_client: Any, user_id: str) -> list[dict]:
    """Load open positions using Stage 11A active-position semantics.

    Excludes: rows with no ticker, category==SELL, and shares <= 0.
    """
    try:
        res = (
            db_client.table("positions")
            .select("ticker,shares,avg_cost,category,source")
            .eq("user_id", user_id)
            .execute()
        )
        rows: list[dict] = res.data or []
    except Exception as exc:
        logger.warning("current_price_truth_repair: positions query failed: %s", exc)
        return []

    return [
        r for r in rows
        if r.get("ticker")
        and (r.get("category") or "").upper() != "SELL"
        and (_safe_float(r.get("shares")) or 0.0) > 0
    ]


# ── Price history loading ─────────────────────────────────────────────────────

def _load_latest_prices_per_ticker(
    db_client: Any,
    tickers: list[str],
) -> tuple[dict[str, dict], int, bool]:
    """Load latest price for each ticker using per-ticker queries.

    Per-ticker queries avoid the Supabase default-row-limit truncation that
    would hide latest prices for active tickers when total price_history rows
    exceed the Supabase default cap of 1000. This was the root cause of the
    Stage 10C.2 VTI truncation bug.

    Returns:
        latest_by_ticker: mapping of ticker → most recent price row
        total_rows_loaded: total rows returned across all queries
        price_query_truncated: True if any per-ticker query hit _PER_TICKER_LIMIT
            (should never happen for recent data; would indicate an unexpected
            anomaly in per-ticker data volume)
    """
    latest_by_ticker: dict[str, dict] = {}
    total_loaded = 0
    truncated = False

    for ticker in tickers:
        try:
            res = (
                db_client.table("price_history")
                .select("ticker,price_date,close_price")
                .eq("ticker", ticker)
                .order("price_date", desc=True)
                .limit(_PER_TICKER_LIMIT)
                .execute()
            )
            rows: list[dict] = res.data or []
        except Exception as exc:
            logger.warning(
                "current_price_truth_repair: price_history query failed for %s: %s",
                ticker, exc,
            )
            rows = []

        total_loaded += len(rows)
        if len(rows) >= _PER_TICKER_LIMIT:
            # Per-ticker hit the limit — suspicious for recent-data queries
            truncated = True
            logger.warning(
                "current_price_truth_repair: per-ticker price query hit limit=%d for %s",
                _PER_TICKER_LIMIT, ticker,
            )
        if rows:
            latest_by_ticker[ticker] = rows[0]

    return latest_by_ticker, total_loaded, truncated


# ── Price status classification ───────────────────────────────────────────────

def _classify_price_status(
    ticker: str,
    latest_by_ticker: dict[str, dict],
    today: date,
) -> dict[str, Any]:
    """Classify current price status for a single ticker."""
    if ticker not in latest_by_ticker:
        return {
            "ticker": ticker,
            "current_price_status": "missing",
            "latest_price_date": None,
            "latest_price_value": None,
            "business_days_old": None,
        }

    row = latest_by_ticker[ticker]
    price_date = _parse_date(row.get("price_date"))
    close_price = _safe_float(row.get("close_price"))

    if price_date is None:
        return {
            "ticker": ticker,
            "current_price_status": "stale",
            "latest_price_date": None,
            "latest_price_value": close_price,
            "business_days_old": None,
        }

    bdays = _business_days_since(price_date, today)

    if _is_price_stale(price_date, today):
        return {
            "ticker": ticker,
            "current_price_status": "stale",
            "latest_price_date": str(price_date),
            "latest_price_value": close_price,
            "business_days_old": bdays,
        }

    return {
        "ticker": ticker,
        "current_price_status": "recent",
        "latest_price_date": str(price_date),
        "latest_price_value": close_price,
        "business_days_old": bdays,
    }


# ── Provider fetches ──────────────────────────────────────────────────────────

async def _fetch_yfinance_current(ticker: str) -> dict[str, Any]:
    """Fetch latest close price from Yahoo Finance v8 chart API (5-day window).

    Returns a dict with keys: price, price_date, open_price, high_price,
    low_price, volume, provider, error. price is None on failure.
    """
    yf_symbol = _CRYPTO_YF.get(ticker, ticker)
    url = f"{_YF_CHART_BASE}/{yf_symbol}"
    params = {"interval": "1d", "range": "5d"}

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(_HTTP_TIMEOUT),
            headers={"User-Agent": "PortfolioIntelligence/2.0"},
        ) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning(
            "current_price_truth_repair: yfinance fetch failed for %s: %s",
            ticker, exc,
        )
        return {"price": None, "price_date": None, "open_price": None,
                "high_price": None, "low_price": None, "volume": None,
                "provider": "yfinance", "error": str(exc)}

    chart_results = data.get("chart", {}).get("result", [])
    if not chart_results:
        return {"price": None, "price_date": None, "open_price": None,
                "high_price": None, "low_price": None, "volume": None,
                "provider": "yfinance", "error": "no_chart_results"}

    result = chart_results[0]
    timestamps = result.get("timestamp", [])
    quote = result.get("indicators", {}).get("quote", [{}])[0]
    closes = quote.get("close", [])
    opens = quote.get("open", [])
    highs = quote.get("high", [])
    lows = quote.get("low", [])
    volumes = quote.get("volume", [])

    # Walk backwards to find the most recent valid close
    for i in range(len(timestamps) - 1, -1, -1):
        close_val = closes[i] if i < len(closes) else None
        if close_val is not None and float(close_val) > 0:
            ts = timestamps[i]
            price_date = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
            return {
                "price": float(close_val),
                "price_date": price_date,
                "open_price": float(opens[i] or 0) if i < len(opens) else 0.0,
                "high_price": float(highs[i] or 0) if i < len(highs) else 0.0,
                "low_price": float(lows[i] or 0) if i < len(lows) else 0.0,
                "volume": int(volumes[i] or 0) if i < len(volumes) else 0,
                "provider": "yfinance",
                "error": None,
            }

    return {"price": None, "price_date": None, "open_price": None,
            "high_price": None, "low_price": None, "volume": None,
            "provider": "yfinance", "error": "no_valid_close_in_window"}


async def _fetch_coingecko_current(ticker: str) -> dict[str, Any]:
    """Fetch latest price from CoinGecko free API (no key required).

    Returns the same shape as _fetch_yfinance_current.
    CoinGecko only provides a spot price, so OHLCV fields are set to the
    spot price / zero respectively.
    """
    coin_id = _CRYPTO_IDS.get(ticker)
    if not coin_id:
        return {"price": None, "price_date": None, "open_price": None,
                "high_price": None, "low_price": None, "volume": None,
                "provider": "coingecko", "error": f"no_coin_id_for_{ticker}"}

    url = f"{_COINGECKO_BASE}/simple/price"
    params = {"ids": coin_id, "vs_currencies": "usd"}

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(_HTTP_TIMEOUT),
            headers={"User-Agent": "PortfolioIntelligence/2.0"},
        ) as client:
            resp = await client.get(url, params=params)
            if resp.status_code == 429:
                return {"price": None, "price_date": None, "open_price": None,
                        "high_price": None, "low_price": None, "volume": None,
                        "provider": "coingecko", "error": "rate_limited_429"}
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning(
            "current_price_truth_repair: CoinGecko fetch failed for %s: %s",
            ticker, exc,
        )
        return {"price": None, "price_date": None, "open_price": None,
                "high_price": None, "low_price": None, "volume": None,
                "provider": "coingecko", "error": str(exc)}

    price = _safe_float(data.get(coin_id, {}).get("usd"))
    if price is None or price <= 0:
        return {"price": None, "price_date": None, "open_price": None,
                "high_price": None, "low_price": None, "volume": None,
                "provider": "coingecko", "error": "zero_or_missing_price"}

    today_str = _now_utc().strftime("%Y-%m-%d")
    return {
        "price": price,
        "price_date": today_str,
        "open_price": price,   # CoinGecko spot: OHLC all set to close
        "high_price": price,
        "low_price": price,
        "volume": 0,
        "provider": "coingecko",
        "error": None,
    }


async def _fetch_price_for_ticker(
    ticker: str,
    category: str,
) -> dict[str, Any]:
    """Route to the correct provider based on position category.

    Equities/ETFs (Core/ETF/Other/IPO) → yfinance
    Crypto → CoinGecko if in _CRYPTO_IDS, else yfinance fallback
    Returns the same shape as _fetch_yfinance_current / _fetch_coingecko_current
    plus a 'unsupported' key indicating the ticker type is not supported.
    """
    is_crypto = category.upper() == "CRYPTO"

    if is_crypto:
        if ticker in _CRYPTO_IDS:
            result = await _fetch_coingecko_current(ticker)
            result["unsupported"] = False
            return result
        # Crypto ticker not in known mapping — no safe provider path
        logger.info(
            "current_price_truth_repair: crypto ticker %s not in _CRYPTO_IDS — unsupported",
            ticker,
        )
        return {"price": None, "price_date": None, "open_price": None,
                "high_price": None, "low_price": None, "volume": None,
                "provider": None, "error": f"unsupported_crypto_{ticker}",
                "unsupported": True}

    # Equities and ETFs: yfinance
    result = await _fetch_yfinance_current(ticker)
    result["unsupported"] = False
    return result


# ── Price history write ───────────────────────────────────────────────────────

def _write_price_row(
    db_client: Any,
    ticker: str,
    price_date: str,
    close_price: float,
    open_price: float,
    high_price: float,
    low_price: float,
    volume: int,
    source: str,
) -> bool:
    """Upsert a single price row into price_history. Returns True on success."""
    row = {
        "ticker": ticker,
        "price_date": price_date,
        "close_price": close_price,
        "open_price": open_price,
        "high_price": high_price,
        "low_price": low_price,
        "volume": volume,
        "source": source,
    }
    try:
        db_client.table("price_history").upsert(
            row, on_conflict="ticker,price_date"
        ).execute()
        return True
    except Exception as exc:
        logger.error(
            "current_price_truth_repair: price_history write failed for %s: %s",
            ticker, exc,
        )
        return False


# ── Main entry point ──────────────────────────────────────────────────────────

async def run_current_price_truth_repair(
    db_client: Any,
    user_id: str,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Stage 11B — repair current price truth for open-position tickers.

    Invariants:
    - dry_run=True (default): performs no writes — safe to call repeatedly
    - dry_run=False: writes only to price_history; no other table touched
    - No LLM calls
    - No recommendation, snapshot, position, or agent table writes
    - No Buy/Hold/Trim/Sell policy changes
    - Per-ticker price queries avoid Supabase 1000-row default-cap truncation
    """
    today = _now_utc().date()

    # ── 1. Load open positions ────────────────────────────────────────────────
    open_positions = _load_open_positions(db_client, user_id)
    open_tickers: list[str] = [p["ticker"] for p in open_positions]
    category_by_ticker: dict[str, str] = {
        p["ticker"]: (p.get("category") or "")
        for p in open_positions
    }

    if not open_tickers:
        return {
            "diagnostic_version": DIAGNOSTIC_VERSION,
            "dry_run": dry_run,
            "open_tickers_count": 0,
            "price_rows_loaded_count": 0,
            "price_query_truncated": False,
            "price_load_strategy": "per_ticker",
            "missing_before_count": 0,
            "stale_before_count": 0,
            "attempted_fetch_count": 0,
            "successful_fetch_count": 0,
            "unsupported_count": 0,
            "provider_error_count": 0,
            "rows_written": 0,
            "safe_to_rerun": True,
            "next_step": "no_open_positions_found",
            "per_ticker": [],
            "writes_performed": 0,
            "policy_unchanged": True,
            "snapshot_unchanged": True,
        }

    # ── 2. Load latest prices per ticker (per-ticker to avoid truncation) ────
    latest_by_ticker, rows_loaded, truncated = _load_latest_prices_per_ticker(
        db_client, open_tickers
    )

    # ── 3. Classify each ticker ───────────────────────────────────────────────
    missing_before: list[str] = []
    stale_before: list[str] = []
    recent_tickers: list[str] = []

    ticker_statuses: dict[str, dict] = {}
    for ticker in open_tickers:
        status = _classify_price_status(ticker, latest_by_ticker, today)
        ticker_statuses[ticker] = status
        ps = status["current_price_status"]
        if ps == "missing":
            missing_before.append(ticker)
        elif ps == "stale":
            stale_before.append(ticker)
        else:
            recent_tickers.append(ticker)

    # ── 4. Fetch and optionally write prices for missing/stale tickers ────────
    needs_fetch = missing_before + stale_before
    per_ticker_results: list[dict] = []
    attempted_fetch = 0
    successful_fetch = 0
    unsupported_count = 0
    provider_error_count = 0
    rows_written = 0

    # Add recent tickers to results as-is (no fetch needed)
    for ticker in recent_tickers:
        st = ticker_statuses[ticker]
        per_ticker_results.append({
            "ticker": ticker,
            "current_price_status": st["current_price_status"],
            "latest_price_date": st["latest_price_date"],
            "latest_price_value": st["latest_price_value"],
            "business_days_old": st["business_days_old"],
            "provider_used": None,
            "write_status": "unchanged",
            "fetched_price": None,
            "fetched_price_date": None,
            "fetch_error": None,
        })

    for ticker in needs_fetch:
        st = ticker_statuses[ticker]
        category = category_by_ticker.get(ticker, "")
        attempted_fetch += 1

        fetch_result = await _fetch_price_for_ticker(ticker, category)

        if fetch_result.get("unsupported"):
            unsupported_count += 1
            per_ticker_results.append({
                "ticker": ticker,
                "current_price_status": "unsupported",
                "latest_price_date": st["latest_price_date"],
                "latest_price_value": st["latest_price_value"],
                "business_days_old": st["business_days_old"],
                "provider_used": None,
                "write_status": "unsupported",
                "fetched_price": None,
                "fetched_price_date": None,
                "fetch_error": fetch_result.get("error"),
            })
            continue

        fetched_price = fetch_result.get("price")
        fetched_date = fetch_result.get("price_date")
        provider = fetch_result.get("provider")
        fetch_error = fetch_result.get("error")

        if fetched_price is None or fetched_price <= 0:
            provider_error_count += 1
            per_ticker_results.append({
                "ticker": ticker,
                "current_price_status": "provider_error",
                "latest_price_date": st["latest_price_date"],
                "latest_price_value": st["latest_price_value"],
                "business_days_old": st["business_days_old"],
                "provider_used": provider,
                "write_status": "skipped_fetch_failed",
                "fetched_price": None,
                "fetched_price_date": None,
                "fetch_error": fetch_error,
            })
            continue

        successful_fetch += 1

        if dry_run:
            write_status = "skipped_dry_run"
        else:
            ok = _write_price_row(
                db_client=db_client,
                ticker=ticker,
                price_date=fetched_date,
                close_price=fetched_price,
                open_price=fetch_result.get("open_price") or fetched_price,
                high_price=fetch_result.get("high_price") or fetched_price,
                low_price=fetch_result.get("low_price") or fetched_price,
                volume=fetch_result.get("volume") or 0,
                source=provider or "unknown",
            )
            if ok:
                rows_written += 1
                write_status = "written"
            else:
                write_status = "failed"

        per_ticker_results.append({
            "ticker": ticker,
            "current_price_status": st["current_price_status"],
            "latest_price_date": st["latest_price_date"],
            "latest_price_value": st["latest_price_value"],
            "business_days_old": st["business_days_old"],
            "provider_used": provider,
            "write_status": write_status,
            "fetched_price": fetched_price,
            "fetched_price_date": fetched_date,
            "fetch_error": None,
        })

    logger.info(
        "current_price_truth_repair user=%s dry_run=%s open=%d missing=%d "
        "stale=%d fetched=%d written=%d unsupported=%d errors=%d",
        user_id, dry_run, len(open_tickers),
        len(missing_before), len(stale_before),
        successful_fetch, rows_written, unsupported_count, provider_error_count,
    )

    return {
        "diagnostic_version": DIAGNOSTIC_VERSION,
        "dry_run": dry_run,
        "open_tickers_count": len(open_tickers),
        "price_rows_loaded_count": rows_loaded,
        "price_query_truncated": truncated,
        "price_load_strategy": "per_ticker",
        "missing_before_count": len(missing_before),
        "stale_before_count": len(stale_before),
        "attempted_fetch_count": attempted_fetch,
        "successful_fetch_count": successful_fetch,
        "unsupported_count": unsupported_count,
        "provider_error_count": provider_error_count,
        "rows_written": rows_written,
        "dry_run_note": (
            "No writes performed — set dry_run=false to write price_history rows"
            if dry_run else None
        ),
        "safe_to_rerun": True,
        "next_step": "rerun_financial_truth_baseline",
        "per_ticker": per_ticker_results,
        "writes_performed": rows_written,
        "policy_unchanged": True,
        "snapshot_unchanged": True,
    }
