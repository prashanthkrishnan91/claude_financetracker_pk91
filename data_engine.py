"""
data_engine.py — Portfolio War Room v11.0
All business logic — zero UI code.

Changes vs v10.2:
  - fetch_prices() replaced with PriceService (Finnhub → Polygon → CoinGecko → cache)
  - Plaid holdings integration via sync_live_portfolio()
  - sync_portfolio_total() bridges Plaid qty × real-time price → PortfolioSnapshot
  - yfinance fully removed from price path (kept as optional last-resort fallback only)
  - All other logic (tx_store, recs, deposit engine, targets, DRIP) unchanged from v10.2
"""

from __future__ import annotations

import csv
import datetime
import hashlib
import io
import json
import logging
import os
import re
import streamlit as st
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from pathlib import Path
from typing import Optional

# ─── V11 real-time modules ────────────────────────────────────────────────────
try:
    from price_service import PriceService, PriceResult
    from portfolio_aggregator import PortfolioAggregator
    _V11_AVAILABLE = True
except ImportError:
    _V11_AVAILABLE = False

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# FILE PATHS
# ═══════════════════════════════════════════════════════════════════════════════
TX_STORE_PATH      = Path("tx_store.json")
CRYPTO_OVR_PATH    = Path("crypto_overrides.json")
REC_HISTORY_PATH   = Path("rec_history.json")
DEPOSIT_LOG_PATH   = Path("deposit_log.json")
TARGETS_PATH       = Path("targets.json")
PRICE_CACHE_PATH   = Path("price_cache.json")
RECON_LOG_PATH     = Path("recon_log.json")
PLAID_SNAPSHOT_PATH = Path("plaid_snapshot.json")   # NEW v11: exported by sync engine

# ═══════════════════════════════════════════════════════════════════════════════
# CLASSIFICATION LISTS  (drives recommendation engine)
# ═══════════════════════════════════════════════════════════════════════════════
FOREVER_HOLD  = {"VYM", "SCHD", "VTI"}
DCA_ALWAYS    = {"VOO", "QQQ"}
SELL_LIST     = {"VTV", "VEA", "VWO", "BND"}
SELL_PENDING  = {"SPY", "VUG"}
IPO_HOLDS     = {"BLSH", "KLAR", "STUB"}
CRYPTO_TICKERS = {"BTC", "ETH", "XRP", "SOL", "DOGE", "ADA", "AVAX", "MATIC", "DOT", "LTC"}

# Analyst price targets (used for upside calculation)
TARGETS: dict[str, float] = {
    "NVDA":  180.0,  "META":  700.0,  "GOOGL": 210.0,  "AAPL":  235.0,
    "MSFT":  480.0,  "NFLX":  1000.0, "COST":  1050.0, "TSM":   220.0,
    "CRM":   370.0,  "QCOM":  200.0,  "WMT":   115.0,  "BRK-B": 550.0,
    "VOO":   600.0,  "QQQ":   520.0,  "VYM":   140.0,  "SCHD":  95.0,
    "VTI":   310.0,  "GLD":   340.0,  "VGT":   600.0,  "XLE":   100.0,
    "VHT":   280.0,  "VIS":   250.0,  "VXUS":  70.0,   "RDDT":  200.0,
    "BTC":   150000, "XRP":   5.0,    "SPY":   650.0,  "VUG":   450.0,
    "ALK":   70.0,   "AMD":   160.0,  "SNOW":  180.0,
}

# Biweekly deposit rotation
DEPOSIT_ROTATION = ["META", "GOOGL", "AAPL", "MSFT", "COST", "TSM", "CRM", "NFLX"]
DEPOSIT_PLAN: list[tuple[str, float]] = [
    ("NVDA", 0.28), ("VOO", 0.22), ("VYM", 0.17), ("QQQ", 0.17), ("ROTATING", 0.16),
]

# ═══════════════════════════════════════════════════════════════════════════════
# BAKED BOOTSTRAP  (verified via full Decimal CSV replay — v10.2 audit)
# ═══════════════════════════════════════════════════════════════════════════════
BAKED_BOOTSTRAP: dict[str, dict] = {
    "VOO":   {"shares": "7.624667",   "avg_cost": "389.1600", "first_buy_date": "2024-03-18", "category": "ETF"},
    "VYM":   {"shares": "21.914842",  "avg_cost": "119.8200", "first_buy_date": "2024-03-18", "category": "ETF"},
    "NVDA":  {"shares": "35.504150",  "avg_cost": "82.5000",  "first_buy_date": "2024-04-15", "category": "Stocks"},
    "NFLX":  {"shares": "21.332452",  "avg_cost": "580.0000", "first_buy_date": "2024-05-10", "category": "Stocks"},
    "GLD":   {"shares": "6.640750",   "avg_cost": "196.8000", "first_buy_date": "2024-03-18", "category": "ETF"},
    "QQQ":   {"shares": "1.827600",   "avg_cost": "428.5000", "first_buy_date": "2024-06-01", "category": "ETF"},
    "VTI":   {"shares": "4.456200",   "avg_cost": "228.4000", "first_buy_date": "2024-03-18", "category": "ETF"},
    "SCHD":  {"shares": "8.240100",   "avg_cost": "77.9000",  "first_buy_date": "2024-03-18", "category": "ETF"},
    "META":  {"shares": "2.302400",   "avg_cost": "490.0000", "first_buy_date": "2025-03-01", "category": "Stocks"},
    "GOOGL": {"shares": "4.003300",   "avg_cost": "165.0000", "first_buy_date": "2024-12-15", "category": "Stocks"},
    "AAPL":  {"shares": "2.597700",   "avg_cost": "172.5000", "first_buy_date": "2024-03-01", "category": "Stocks"},
    "MSFT":  {"shares": "0.012400",   "avg_cost": "398.0000", "first_buy_date": "2024-03-01", "category": "Stocks"},
    "COST":  {"shares": "2.342300",   "avg_cost": "880.0000", "first_buy_date": "2024-08-01", "category": "Stocks"},
    "TSM":   {"shares": "3.500000",   "avg_cost": "155.0000", "first_buy_date": "2024-11-01", "category": "Stocks"},
    "CRM":   {"shares": "2.740427",   "avg_cost": "285.0000", "first_buy_date": "2024-09-01", "category": "Stocks"},
    "QCOM":  {"shares": "2.372400",   "avg_cost": "158.0000", "first_buy_date": "2024-03-01", "category": "Stocks"},
    "WMT":   {"shares": "4.102000",   "avg_cost": "65.0000",  "first_buy_date": "2024-03-18", "category": "Stocks"},
    "BRK-B": {"shares": "0.526200",   "avg_cost": "400.0000", "first_buy_date": "2024-03-18", "category": "Stocks"},
    "VGT":   {"shares": "0.852000",   "avg_cost": "540.0000", "first_buy_date": "2024-07-01", "category": "ETF"},
    "XLE":   {"shares": "5.820000",   "avg_cost": "89.5000",  "first_buy_date": "2024-03-18", "category": "ETF"},
    "VHT":   {"shares": "1.240000",   "avg_cost": "248.0000", "first_buy_date": "2024-07-01", "category": "ETF"},
    "VIS":   {"shares": "0.960000",   "avg_cost": "230.0000", "first_buy_date": "2024-07-01", "category": "ETF"},
    "VXUS":  {"shares": "3.880000",   "avg_cost": "58.5000",  "first_buy_date": "2024-03-18", "category": "ETF"},
    "RDDT":  {"shares": "1.250000",   "avg_cost": "110.0000", "first_buy_date": "2024-09-01", "category": "Stocks"},
    "ALK":   {"shares": "0.608716",   "avg_cost": "48.0000",  "first_buy_date": "2024-06-01", "category": "Stocks"},
    "AMD":   {"shares": "1.559692",   "avg_cost": "140.0000", "first_buy_date": "2024-05-01", "category": "Stocks"},
    "SNOW":  {"shares": "3.735346",   "avg_cost": "155.0000", "first_buy_date": "2024-11-01", "category": "Stocks"},
    "SPY":   {"shares": "0.508410",   "avg_cost": "490.0000", "first_buy_date": "2024-11-20", "category": "ETF"},
    "VUG":   {"shares": "0.820000",   "avg_cost": "380.0000", "first_buy_date": "2024-07-15", "category": "ETF"},
    "BLSH":  {"shares": "10.000000",  "avg_cost": "37.0000",  "first_buy_date": "2025-08-14", "category": "Stocks"},
    "KLAR":  {"shares": "11.000000",  "avg_cost": "28.0000",  "first_buy_date": "2025-09-11", "category": "Stocks"},
    "STUB":  {"shares": "23.356143",  "avg_cost": "25.0000",  "first_buy_date": "2025-09-18", "category": "Stocks"},
    "BTC":   {"shares": "0.034330",   "avg_cost": "52800.00", "first_buy_date": "2024-09-01", "category": "Crypto"},
    "XRP":   {"shares": "1.066000",   "avg_cost": "0.6800",   "first_buy_date": "2024-11-01", "category": "Crypto"},
}

ROBINHOOD_CASH_DEFAULT = Decimal("1042.17")

# ═══════════════════════════════════════════════════════════════════════════════
# PERSISTENCE HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _load(path: Path, default):
    if path.exists():
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            pass
    return default

def _save(path: Path, obj) -> None:
    try:
        with open(path, "w") as f:
            json.dump(obj, f, indent=2)
    except Exception as e:
        logger.error("_save(%s): %s", path, e)

# ═══════════════════════════════════════════════════════════════════════════════
# BOOTSTRAP
# ═══════════════════════════════════════════════════════════════════════════════

def _bootstrap() -> None:
    """Write bootstrap positions to tx_store.json if store is empty."""
    if TX_STORE_PATH.exists():
        try:
            store = json.loads(TX_STORE_PATH.read_text())
            if store:
                return
        except Exception:
            pass
    # Write BAKED_BOOTSTRAP as synthetic Buy rows
    synthetic: dict[str, dict] = {}
    for ticker, pos in BAKED_BOOTSTRAP.items():
        key = hashlib.sha256(f"BOOTSTRAP|{ticker}".encode()).hexdigest()
        synthetic[key] = {
            "date": pos["first_buy_date"],
            "code": "Buy",
            "ticker": ticker,
            "qty": pos["shares"],
            "price": pos["avg_cost"],
            "amount": str(Decimal(pos["shares"]) * Decimal(pos["avg_cost"])),
            "description": "Bootstrap",
            "category": pos["category"],
        }
    _save(TX_STORE_PATH, synthetic)
    logger.info("Bootstrap: wrote %d positions to tx_store.json", len(synthetic))

# ═══════════════════════════════════════════════════════════════════════════════
# PORTFOLIO COMPUTATION  (Decimal precision, from tx_store)
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class IngestStats:
    total_rows_in_file:     int = 0
    new_rows_added:         int = 0
    duplicate_rows_skipped: int = 0
    skipped_no_code:        int = 0
    errors:                 list = field(default_factory=list)


def recompute_portfolio(tx_store: dict, crypto_overrides: dict) -> dict:
    """
    Replay all rows in tx_store (oldest→newest) to build current holdings.
    Returns dict of ticker → {shares, avg_cost, first_buy_date, category}
    All arithmetic in Decimal(prec=28).
    """
    portfolio: dict[str, dict] = {}

    # Sort by date
    rows = sorted(tx_store.values(), key=lambda r: r.get("date", ""))

    for row in rows:
        code    = row.get("code", "")
        ticker  = row.get("ticker", "").strip().upper()
        qty_s   = row.get("qty", "0") or "0"
        price_s = row.get("price", "0") or "0"
        amount_s = row.get("amount", "0") or "0"
        category = row.get("category", "Stocks")
        date_s  = row.get("date", "")

        try:
            qty   = Decimal(str(qty_s).replace(",", ""))
            price = Decimal(str(price_s).replace(",", ""))
            amount = abs(Decimal(str(amount_s).replace(",", "")))
        except InvalidOperation:
            continue

        if not ticker or code in ("ACH", "RTP", "DTAX", "MISC", "DFEE"):
            continue

        if code in ("Buy", "CDIV", "RTP"):
            if ticker not in portfolio:
                portfolio[ticker] = {
                    "shares": Decimal("0"),
                    "total_cost": Decimal("0"),
                    "first_buy_date": date_s,
                    "category": category,
                }
            p = portfolio[ticker]
            cost_basis = amount if amount > 0 else qty * price
            p["shares"]     += qty
            p["total_cost"] += cost_basis

        elif code in ("Sell", "LIQ", "SXCH"):
            if ticker in portfolio:
                p = portfolio[ticker]
                sold = qty if qty > 0 else p["shares"]
                p["shares"] -= sold
                if p["shares"] <= Decimal("0.0001"):
                    del portfolio[ticker]
                else:
                    # proportionally reduce cost
                    if (p["shares"] + sold) > 0:
                        p["total_cost"] = p["total_cost"] * p["shares"] / (p["shares"] + sold)

        elif code == "SPL":
            # Stock split — add shares, cost basis unchanged
            if ticker in portfolio and qty > 0:
                portfolio[ticker]["shares"] += qty

    # Apply crypto overrides (from PDF import)
    for ticker, ov in crypto_overrides.items():
        t = ticker.upper()
        if t in portfolio:
            portfolio[t]["shares"]     = Decimal(str(ov.get("shares", portfolio[t]["shares"])))
            portfolio[t]["total_cost"] = Decimal(str(ov.get("avg_cost", "0"))) * portfolio[t]["shares"]
        else:
            portfolio[t] = {
                "shares":         Decimal(str(ov.get("shares", "0"))),
                "total_cost":     Decimal(str(ov.get("avg_cost", "0"))) * Decimal(str(ov.get("shares", "0"))),
                "first_buy_date": ov.get("first_buy_date", ""),
                "category":       "Crypto",
            }

    # Compute avg_cost per share
    result: dict[str, dict] = {}
    for ticker, pos in portfolio.items():
        shares = pos["shares"]
        if shares <= Decimal("0.0001"):
            continue
        avg_cost = (pos["total_cost"] / shares).quantize(Decimal("0.0001"), ROUND_HALF_UP)
        result[ticker] = {
            "shares":         float(shares),
            "avg_cost":       float(avg_cost),
            "first_buy_date": pos.get("first_buy_date", ""),
            "category":       pos.get("category", "Stocks"),
        }

    return result


def ingest_csv(file_bytes: bytes, existing_ids: set) -> tuple[IngestStats, set]:
    """Parse a Robinhood CSV export and return (stats, new_ids_added)."""
    stats = IngestStats()
    new_ids: set = set()
    tx_store = _load(TX_STORE_PATH, {})

    text = file_bytes.decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(text), quoting=csv.QUOTE_ALL)

    for row in reader:
        stats.total_rows_in_file += 1
        code = (row.get("Trans Code") or row.get("Activity Type") or "").strip()
        if not code:
            stats.skipped_no_code += 1
            continue

        ticker   = (row.get("Instrument") or row.get("Symbol") or "").strip().upper()
        qty_raw  = row.get("Quantity", "") or ""
        price_raw = row.get("Price", "") or ""
        amount_raw = row.get("Amount", "") or ""
        date_raw = row.get("Process Date") or row.get("Date") or ""
        settle   = row.get("Settle Date", "") or ""
        desc     = row.get("Description", "") or ""

        # SHA-256 fingerprint — ACH/RTP cash rows include Amount to distinguish same-day deposits
        if not ticker and not qty_raw:
            fp_src = f"{date_raw}|{code}|{amount_raw}|{settle}"
        else:
            fp_src = f"{date_raw}|{code}|{ticker}|{qty_raw}|{price_raw}|{settle}"
        fp = hashlib.sha256(fp_src.encode()).hexdigest()

        if fp in existing_ids or fp in tx_store:
            stats.duplicate_rows_skipped += 1
            continue

        # Clean quantity
        qty_clean = re.sub(r"[^\d.\-]", "", qty_raw)
        if qty_clean in ("", "-", "."):
            qty_clean = "0"

        # Infer category
        category = "Crypto" if ticker in CRYPTO_TICKERS else "ETF" if ticker in {
            "VOO","QQQ","VYM","VTI","SCHD","GLD","VGT","XLE","VHT","VIS","VXUS",
            "SPY","VUG","VTV","VEA","VWO","BND","IVV","IEFA","AGG"
        } else "Stocks"

        tx_store[fp] = {
            "date":        date_raw,
            "code":        code,
            "ticker":      ticker,
            "qty":         qty_clean,
            "price":       re.sub(r"[^\d.\-]", "", price_raw) or "0",
            "amount":      re.sub(r"[^\d.\-]", "", amount_raw) or "0",
            "description": desc,
            "category":    category,
        }
        new_ids.add(fp)
        stats.new_rows_added += 1

    _save(TX_STORE_PATH, tx_store)

    # Append to recon log
    recon = _load(RECON_LOG_PATH, [])
    recon.append({
        "timestamp":   datetime.datetime.now().isoformat(),
        "total_rows":  stats.total_rows_in_file,
        "new":         stats.new_rows_added,
        "dupes":       stats.duplicate_rows_skipped,
        "errors":      stats.errors,
    })
    _save(RECON_LOG_PATH, recon[-100:])

    return stats, new_ids

# ═══════════════════════════════════════════════════════════════════════════════
# V11 PRICE FETCHING  — Finnhub/Polygon/CoinGecko via PriceService
# ═══════════════════════════════════════════════════════════════════════════════

def _load_env_from_secrets() -> None:
    """Load Plaid/Finnhub/Polygon keys from Streamlit secrets into os.environ."""
    try:
        for key in [
            "PLAID_CLIENT_ID", "PLAID_SECRET", "PLAID_ENV", "PLAID_ACCESS_TOKEN",
            "FINNHUB_API_KEY", "POLYGON_API_KEY",
        ]:
            if key in st.secrets and not os.environ.get(key):
                os.environ[key] = st.secrets[key]
    except Exception:
        pass  # st.secrets not available in test context


@st.cache_data(ttl=30, show_spinner=False)
def fetch_prices(tickers: tuple, _bust: int = 0) -> dict[str, float]:
    """
    Fetch real-time mid-prices for all tickers.
    Uses PriceService (Finnhub → Polygon → CoinGecko → cache) when available.
    Falls back to yfinance + CoinGecko if v11 modules not installed.
    Returns dict[ticker → float price].
    """
    _load_env_from_secrets()
    prices: dict[str, float] = {}

    if _V11_AVAILABLE:
        svc = PriceService()
        results = svc.fetch_prices(list(tickers))
        for ticker, result in results.items():
            if result.mid_price > 0:
                prices[ticker] = result.mid_price
            else:
                # Try to get from price cache on disk
                cache = _load(PRICE_CACHE_PATH, {})
                if ticker in cache:
                    prices[ticker] = cache[ticker]
        # Save updated prices to disk cache
        cache = _load(PRICE_CACHE_PATH, {})
        cache.update({t: p for t, p in prices.items() if p > 0})
        _save(PRICE_CACHE_PATH, cache)
        return prices

    # ── Fallback: yfinance + CoinGecko ────────────────────────────────────────
    import requests as req
    stocks  = [t for t in tickers if t not in CRYPTO_TICKERS]
    cryptos = [t for t in tickers if t in CRYPTO_TICKERS]

    # yfinance stocks
    try:
        import yfinance as yf
        for t in stocks:
            try:
                info = yf.Ticker(t).fast_info
                p = info.get("last_price") or info.get("regularMarketPrice")
                prices[t] = round(float(p), 4) if p else None
            except Exception:
                prices[t] = None
    except ImportError:
        pass

    # CoinGecko crypto
    _CG = {"BTC":"bitcoin","XRP":"ripple","ETH":"ethereum","SOL":"solana","DOGE":"dogecoin"}
    for t in cryptos:
        cid = _CG.get(t)
        if not cid:
            prices[t] = None
            continue
        try:
            r = req.get(
                f"https://api.coingecko.com/api/v3/simple/price?ids={cid}&vs_currencies=usd",
                timeout=8
            )
            prices[t] = round(float(r.json()[cid]["usd"]), 4)
        except Exception:
            prices[t] = None

    # Disk-cache fallback for anything that returned None
    cache = _load(PRICE_CACHE_PATH, {})
    for t in tickers:
        if not prices.get(t):
            if t in cache:
                prices[t] = cache[t]
    # Update cache with fresh prices
    cache.update({t: p for t, p in prices.items() if p})
    _save(PRICE_CACHE_PATH, cache)
    return prices


def sync_live_portfolio(bust: int = 0) -> Optional[dict]:
    """
    Attempt a full Plaid sync. Returns snapshot dict or None if Plaid not configured.
    The snapshot is also written to plaid_snapshot.json for the UI to reference.
    """
    if not _V11_AVAILABLE:
        return None
    _load_env_from_secrets()
    if not os.environ.get("PLAID_ACCESS_TOKEN"):
        return None
    try:
        agg = PortfolioAggregator()
        snapshot = agg.sync_portfolio_total()
        # Export to JSON for UI consumption
        from main_sync import export_json
        export_json(snapshot, str(PLAID_SNAPSHOT_PATH))
        return _load(PLAID_SNAPSHOT_PATH, None)
    except Exception as e:
        logger.warning("Plaid sync failed: %s — falling back to tx_store", e)
        return None


def _safe_price(ticker: str, pos: dict, prices: dict) -> float:
    """Return live price for ticker; fallback to avg_cost to avoid zero multiplication."""
    p = prices.get(ticker)
    if p and p > 0:
        return p
    cache = _load(PRICE_CACHE_PATH, {})
    if ticker in cache and cache[ticker] > 0:
        return cache[ticker]
    return pos.get("avg_cost", 1.0) or 1.0

# ═══════════════════════════════════════════════════════════════════════════════
# LT ELIGIBILITY
# ═══════════════════════════════════════════════════════════════════════════════

def is_lt_eligible(first_buy_date: str) -> bool:
    if not first_buy_date:
        return False
    try:
        fbd = datetime.date.fromisoformat(first_buy_date)
        return (datetime.date.today() - fbd).days >= 366
    except ValueError:
        return False

def days_to_lt(first_buy_date: str) -> int:
    if not first_buy_date:
        return 9999
    try:
        fbd = datetime.date.fromisoformat(first_buy_date)
        return max(0, 366 - (datetime.date.today() - fbd).days)
    except ValueError:
        return 9999

# ═══════════════════════════════════════════════════════════════════════════════
# RECOMMENDATION ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

def generate_recs(portfolio: dict, prices: dict) -> list[dict]:
    """
    Dynamic recommendation engine — recalculates on every call with live prices.
    Returns list of rec dicts sorted: sell → buy → trim → hold.
    """
    recs = []
    today = datetime.date.today()

    for ticker, pos in portfolio.items():
        price   = _safe_price(ticker, pos, prices)
        shares  = pos.get("shares", 0)
        cost    = pos.get("avg_cost", price)
        fbd     = pos.get("first_buy_date", "")
        lt      = is_lt_eligible(fbd)
        dtlt    = days_to_lt(fbd)
        equity  = price * shares
        pnl_pct = ((price - cost) / cost * 100) if cost > 0 else 0.0
        target  = TARGETS.get(ticker, cost * 1.25)
        upside  = ((target - price) / price * 100) if price > 0 else 0.0
        lt_date = (datetime.date.fromisoformat(fbd) + datetime.timedelta(days=366)).isoformat() if fbd else "?"
        tax_tag = "✅ LT (15%)" if lt else f"⏳ ST — wait until {lt_date}"

        rec = {
            "ticker":    ticker,
            "category":  pos.get("category", "Stocks"),
            "shares":    shares,
            "price":     price,
            "cost":      cost,
            "equity":    equity,
            "pnl_pct":   pnl_pct,
            "upside":    upside,
            "lt":        lt,
            "dtlt":      dtlt,
            "lt_date":   lt_date,
            "tax":       tax_tag,
            "action":    "",
            "cat":       "",
            "plain":     "",
            "why":       "",
            "priority":  4,
            "proceeds":  0.0,
        }

        # ── Priority 0: Explicit sells ────────────────────────────────────────
        if ticker in SELL_LIST and lt:
            rec.update(action="SELL NOW — LT ✅", cat="sell", priority=0,
                proceeds=equity,
                plain=f"Sell all {shares:.4f} shares (~${equity:,.0f}). Reinvest into VOO or VYM same day.",
                why="LT eligible. Pay 15% not 37%. VOO/VYM swap = no wash sale.")
        elif ticker in SELL_PENDING and lt:
            rec.update(action=f"SELL NOW — {ticker} LT ✅", cat="sell", priority=0,
                proceeds=equity,
                plain=f"Now LT eligible. Sell → reinvest into {('VOO' if ticker=='SPY' else 'QQQ')}.",
                why="Calendar-flagged. ETF→ETF swap. Lock gains at 15%.")

        # ── Priority 1: Big loss ──────────────────────────────────────────────
        elif pnl_pct < -20:
            rec.update(action="REVIEW — BIG LOSS ⚠️", cat="review", priority=1,
                plain=f"Down {pnl_pct:.1f}%. Decide: add more at this dip or cut losses.",
                why=f"Position is >20% underwater. Reassess thesis.")

        # ── Priority 2: Buys ──────────────────────────────────────────────────
        elif ticker in FOREVER_HOLD:
            rec.update(action="HOLD FOREVER — DRIP on 🔄", cat="hold", priority=2,
                plain="Never sell. Keep DRIP on — every dividend buys more shares.",
                why="Core income ETF. Compounding dividend machine.")
        elif ticker in DCA_ALWAYS:
            rec.update(action="DCA EVERY DEPOSIT 💰", cat="buy", priority=2,
                plain="Add to this every 2 weeks. It tracks the whole market.",
                why="Core index. Never stop accumulating.")
        elif pnl_pct < -8 and upside > 20:
            rec.update(action="STRONG BUY — ON DIP 🟢", cat="buy", priority=2,
                plain=f"Down {abs(pnl_pct):.1f}% from your cost. Analyst target ${target:,.0f} = {upside:.0f}% upside.",
                why="Quality stock on sale. Add more to lower avg cost.")
        elif ticker in CRYPTO_TICKERS and upside > 25:
            rec.update(action="ACCUMULATE — CRYPTO 🟡", cat="buy", priority=2,
                plain=f"Target ${target:,.0f}. {upside:.0f}% upside from here. Add small position.",
                why="High-conviction crypto. Keep under 5% of portfolio.")
        elif upside > 20:
            rec.update(action="ACCUMULATE 📈", cat="buy", priority=2,
                plain=f"Analyst target ${target:,.0f} = {upside:.0f}% upside. Add at current levels.",
                why="Strong upside. Good entry point.")

        # ── Priority 3: Trims ─────────────────────────────────────────────────
        elif dtlt <= 30 and dtlt > 0:
            rec.update(action=f"HOLD — LT IN {dtlt} DAYS ⏰", cat="hold", priority=3,
                plain=f"Only {dtlt} days until LT status. Don't sell early — saves ~22% in tax.",
                why=f"LT date: {lt_date}. Patience = tax savings.")
        elif ticker in IPO_HOLDS and lt:
            trim_shares = shares * 0.25
            rec.update(action="TRIM 25% — IPO LT ✅", cat="trim", priority=3,
                proceeds=price * trim_shares,
                plain=f"Sell {trim_shares:.2f} shares (~${price*trim_shares:,.0f}). Keep 75%.",
                why="IPO position now LT. Lock in partial gains at 15% rate.")
        elif pnl_pct > 20 and lt:
            trim_shares = shares * 0.20
            rec.update(action="TRIM 20% — TAKE GAINS 💰", cat="trim", priority=3,
                proceeds=price * trim_shares,
                plain=f"Sell {trim_shares:.2f} shares (~${price*trim_shares:,.0f}). Let rest ride.",
                why=f"Up {pnl_pct:.0f}% and LT. Lock in gains at 15% tax rate.")

        # ── Priority 4: Hold ──────────────────────────────────────────────────
        else:
            hold_msg = f"Holding {shares:.4f} sh @ ${price:,.2f}. "
            if not lt:
                hold_msg += f"LT in {dtlt} days."
            elif upside > 0:
                hold_msg += f"Target ${target:,.0f} = {upside:.0f}% upside."
            rec.update(action="HOLD", cat="hold", priority=4,
                plain=hold_msg,
                why="No action needed today.")

        recs.append(rec)

    # Sort: sell(0) → review(1) → buy(2) → trim(3) → hold(4)
    return sorted(recs, key=lambda r: (r["priority"], -r["equity"]))


# ═══════════════════════════════════════════════════════════════════════════════
# AI TARGET ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

def generate_suggested_targets(portfolio: dict) -> dict[str, float]:
    """
    Suggest target weights (Moderate-Aggressive profile).
    Returns dict[ticker → float 0-100] normalised to exactly 100%.
    Only includes tickers currently held.
    """
    _WEIGHTS = {
        "VOO": 12, "QQQ": 10, "VYM": 8, "SCHD": 5, "VTI": 5,
        "NVDA": 10, "AAPL": 6, "META": 6, "GOOGL": 5, "MSFT": 5,
        "BRK-B": 4, "WMT": 3, "COST": 3,
        "VXUS": 3, "GLD": 3, "VGT": 2, "XLE": 2, "VHT": 2, "VIS": 1,
        "NFLX": 2, "TSM": 2, "QCOM": 1, "RDDT": 1, "CRM": 1,
        "BTC": 3, "XRP": 2,
    }
    held = {t: _WEIGHTS.get(t, 1) for t in portfolio}
    total = sum(held.values()) or 1
    return {t: round(w / total * 100, 1) for t, w in held.items()}


def compute_rebalancing(portfolio: dict, prices: dict, targets: dict) -> list[dict]:
    """Compute drift vs targets. Returns list sorted by most under-weight first."""
    total_equity = sum(_safe_price(t, p, prices) * p["shares"] for t, p in portfolio.items())
    if total_equity <= 0:
        return []
    rows = []
    for ticker, pos in portfolio.items():
        price      = _safe_price(ticker, pos, prices)
        mkt_val    = price * pos["shares"]
        current_pct = mkt_val / total_equity * 100
        target_pct  = targets.get(ticker, 0)
        drift       = current_pct - target_pct
        rows.append({
            "ticker":      ticker,
            "market_value": mkt_val,
            "current_pct": round(current_pct, 1),
            "target_pct":  round(target_pct, 1),
            "drift":       round(drift, 1),
            "action":      "TRIM" if drift > 5 else ("BUY" if drift < -5 else "OK"),
        })
    return sorted(rows, key=lambda r: r["drift"])


# ═══════════════════════════════════════════════════════════════════════════════
# $900 DEPOSIT ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

def get_biweekly_dates(start: datetime.date, n: int = 18) -> list[datetime.date]:
    dates = []
    d = start
    while len(dates) < n:
        if d.weekday() == 4:  # Friday
            dates.append(d)
        d += datetime.timedelta(days=1)
        if len(dates) == 0 and d > start + datetime.timedelta(days=14):
            break
    d = start
    for _ in range(n * 14):
        if d.weekday() == 4:
            if not dates or (d - dates[-1]).days >= 14:
                dates.append(d)
            if len(dates) >= n:
                break
        d += datetime.timedelta(days=1)
    return dates[:n]


def generate_deposit_recs(deposit_num: int, portfolio: dict, prices: dict, targets: dict, amount: float = 900.0) -> list[dict]:
    """
    Generate deposit allocation for deposit #N.
    If targets set: allocate to most-underweight assets (drift-fill).
    Else: fixed 28/22/17/17/16 plan with rotating pick.
    """
    rotating_pick = DEPOSIT_ROTATION[(deposit_num - 1) % len(DEPOSIT_ROTATION)]
    total_equity = sum(_safe_price(t, p, prices) * p["shares"] for t, p in portfolio.items())

    if targets and total_equity > 0:
        # Drift-fill: put money toward most underweight
        rebal = compute_rebalancing(portfolio, prices, targets)
        underweight = [r for r in rebal if r["drift"] < -2][:5]
        if underweight:
            total_deficit = sum(abs(r["drift"]) for r in underweight)
            recs = []
            for r in underweight:
                alloc = amount * abs(r["drift"]) / total_deficit
                p = _safe_price(r["ticker"], portfolio.get(r["ticker"], {}), prices)
                recs.append({
                    "ticker":    r["ticker"],
                    "alloc_pct": round(abs(r["drift"]) / total_deficit * 100, 1),
                    "amount":    round(alloc, 2),
                    "price":     p,
                    "est_shares": round(alloc / p, 4) if p > 0 else 0,
                    "why":       f"{r['drift']:.1f}% under target",
                })
            return recs

    # Fixed plan
    recs = []
    for ticker, pct in DEPOSIT_PLAN:
        actual_ticker = rotating_pick if ticker == "ROTATING" else ticker
        alloc = amount * pct
        p = _safe_price(actual_ticker, portfolio.get(actual_ticker, {}), prices)
        recs.append({
            "ticker":    actual_ticker,
            "alloc_pct": pct * 100,
            "amount":    round(alloc, 2),
            "price":     p,
            "est_shares": round(alloc / p, 4) if p > 0 else 0,
            "why":       "Rotating pick" if ticker == "ROTATING" else "Core allocation",
        })
    return recs


# ═══════════════════════════════════════════════════════════════════════════════
# CRYPTO PDF PARSER
# ═══════════════════════════════════════════════════════════════════════════════

def parse_crypto_pdf(file_bytes: bytes) -> dict[str, dict]:
    """
    Parse a Robinhood Crypto statement PDF.
    Returns dict[ticker → {shares, avg_cost}].
    """
    overrides: dict[str, dict] = {}
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    except Exception:
        return overrides

    # Pattern: "BTC   0.03432981   $52,800.00"
    patterns = [
        r"\b(BTC|ETH|XRP|SOL|DOGE|ADA)\b.*?([\d,]+\.[\d]+)\s+(?:shares|coins)?\s*[@$]?\s*([\d,]+\.[\d]+)",
        r"(BTC|ETH|XRP|SOL|DOGE|ADA)\s+([\d.]+)\s+[\$]?([\d,.]+)",
    ]
    for pat in patterns:
        for m in re.finditer(pat, text, re.IGNORECASE):
            ticker = m.group(1).upper()
            try:
                shares   = float(m.group(2).replace(",", ""))
                avg_cost = float(m.group(3).replace(",", ""))
                if shares > 0:
                    overrides[ticker] = {"shares": shares, "avg_cost": avg_cost, "first_buy_date": ""}
            except Exception:
                pass

    return overrides


# ═══════════════════════════════════════════════════════════════════════════════
# PORTFOLIO SUMMARY HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def portfolio_totals(portfolio: dict, prices: dict, cash: float) -> dict:
    """Compute total equity, cost basis, P&L broken down by bucket."""
    stocks_val = crypto_val = cost_tot = 0.0
    for ticker, pos in portfolio.items():
        p = _safe_price(ticker, pos, prices)
        mkt = p * pos["shares"]
        if pos.get("category") == "Crypto":
            crypto_val += mkt
        else:
            stocks_val += mkt
        cost_tot += pos.get("avg_cost", 0) * pos["shares"]

    total = stocks_val + crypto_val + cash
    pnl   = (stocks_val + crypto_val) - cost_tot
    pct   = (pnl / cost_tot * 100) if cost_tot > 0 else 0.0
    return {
        "total":       total,
        "stocks":      stocks_val,
        "crypto":      crypto_val,
        "cash":        cash,
        "cost_basis":  cost_tot,
        "pnl":         pnl,
        "pnl_pct":     pct,
    }


def snapshot_portfolio(portfolio: dict, prices: dict, cash: float, recs: list) -> dict:
    """Save a timestamped snapshot to rec_history.json."""
    totals = portfolio_totals(portfolio, prices, cash)
    snap = {
        "timestamp": datetime.datetime.now().isoformat(),
        "totals":    totals,
        "recs":      [{"ticker": r["ticker"], "action": r["action"], "pnl_pct": r["pnl_pct"]} for r in recs],
    }
    history = _load(REC_HISTORY_PATH, [])
    history.append(snap)
    _save(REC_HISTORY_PATH, history[-200:])
    return snap


def log_deposit(deposit_num: int, date_str: str, recs: list, total: float) -> None:
    """Append a completed deposit to deposit_log.json."""
    log = _load(DEPOSIT_LOG_PATH, [])
    log.append({
        "num":   deposit_num,
        "date":  date_str,
        "total": total,
        "buys":  [{"ticker": r["ticker"], "amount": r["amount"], "shares": r["est_shares"]} for r in recs],
    })
    _save(DEPOSIT_LOG_PATH, log)
