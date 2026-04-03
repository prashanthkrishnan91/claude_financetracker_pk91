"""
Portfolio War Room — Data Engine v10.1
Fixes: $1 price bug, PDF crypto parsing, AI target engine, SPL/LIQ/SXCH/REC handling.
"""

import csv
import hashlib
import io
import json
import os
import re
import time
from datetime import date, datetime, timedelta
from typing import Any

import requests

try:
    import yfinance as yf
    _YF_AVAILABLE = True
except ImportError:
    _YF_AVAILABLE = False

try:
    import pdfplumber
    _PDF_AVAILABLE = True
except ImportError:
    _PDF_AVAILABLE = False

# ─── Paths ──────────────────────────────────────────────────────────────────────
DATA_DIR      = os.path.dirname(os.path.abspath(__file__))
TX_STORE_PATH = os.path.join(DATA_DIR, "tx_store.json")
CRYPTO_PATH   = os.path.join(DATA_DIR, "crypto_overrides.json")
REC_HIST_PATH = os.path.join(DATA_DIR, "rec_history.json")
DEPOSIT_LOG   = os.path.join(DATA_DIR, "deposit_log.json")
TARGETS_PATH  = os.path.join(DATA_DIR, "targets.json")
PRICE_CACHE_P = os.path.join(DATA_DIR, "price_cache.json")

# ─── Constants ───────────────────────────────────────────────────────────────────
ROBINHOOD_CASH_DEFAULT = 1042.17
DEPOSIT_AMOUNT         = 900.0
FIRST_DEPOSIT_DATE     = date(2026, 4, 3)

DEPOSIT_FIXED    = {"NVDA": 0.28, "VOO": 0.22, "VYM": 0.17, "QQQ": 0.17}
DEPOSIT_ROTATING = ["META", "GOOGL", "AAPL", "MSFT", "COST", "TSM", "CRM", "NFLX"]
DEPOSIT_ROTATING_PCT = 0.16

FOREVER_HOLD = {"VYM", "SCHD", "VTI"}
DCA_ALWAYS   = {"VOO", "QQQ"}
SELL_LIST    = {"VTV", "VEA", "VWO", "BND"}
SELL_PENDING = {"SPY", "VUG"}
IPO_HOLDS    = {"RDDT", "BLSH", "KLAR", "STUB"}
CRYPTO_BASE  = {"BTC", "ETH", "XRP", "SOL", "DOGE"}

# yfinance needs these remapped
YF_TICKER_MAP: dict[str, str] = {
    "BTC": "BTC-USD", "ETH": "ETH-USD", "XRP": "XRP-USD",
    "SOL": "SOL-USD", "DOGE": "DOGE-USD",
    "BRK.B": "BRK-B",
}

COINGECKO_IDS = {
    "BTC": "bitcoin", "ETH": "ethereum", "XRP": "ripple",
    "SOL": "solana",  "DOGE": "dogecoin",
}

PRICE_TARGETS: dict[str, float] = {
    "NVDA": 175.0, "AAPL": 270.0, "GOOGL": 220.0, "META": 700.0,
    "MSFT": 500.0, "AMZN": 260.0, "NFLX": 1100.0, "COST": 1050.0,
    "VOO": 650.0,  "QQQ": 650.0,  "VYM": 165.0,   "SCHD": 35.0,
    "GLD": 450.0,  "VTI": 310.0,  "TSM": 230.0,   "CRM": 350.0,
    "QCOM": 200.0, "WMT": 115.0,  "VGT": 750.0,   "XLE": 95.0,
    "VHT": 310.0,  "VXUS": 90.0,  "BRK.B": 580.0,
    "BTC": 120000.0, "XRP": 4.00, "ETH": 8000.0,
}

LT_DATES: dict[str, date] = {
    "SPY":  date(2026, 5, 20), "VUG":  date(2026, 7, 15),
    "BLSH": date(2026, 8, 14), "KLAR": date(2026, 9, 11),
    "STUB": date(2026, 9, 18),
}

BUY_CODES   = {"Buy"}
SELL_CODES  = {"Sell", "LIQ"}
SPLIT_CODES = {"SPL", "REC"}
SKIP_CODES  = {"CDIV", "RTP", "ACH", "DTAX", "MISC", "DFEE", "SXCH", None, ""}

# ─── Persistence ────────────────────────────────────────────────────────────────
def _load(path: str, default: Any) -> Any:
    try:
        if os.path.exists(path) and os.path.getsize(path) > 2:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return default

def _save(path: str, obj: Any) -> None:
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2, default=str)
    except Exception:
        pass

# ─── Bootstrap ──────────────────────────────────────────────────────────────────
BOOTSTRAP_POSITIONS = {
    "VOO":   {"shares": 7.8613,  "avg_cost": 570.71,  "lt": True},
    "NVDA":  {"shares": 35.5042, "avg_cost": 116.02,  "lt": True},
    "AAPL":  {"shares": 16.1298, "avg_cost": 213.03,  "lt": True},
    "VYM":   {"shares": 23.3882, "avg_cost": 136.98,  "lt": True},
    "GLD":   {"shares": 8.7980,  "avg_cost": 361.41,  "lt": False},
    "BRK.B": {"shares": 4.5154,  "avg_cost": 489.88,  "lt": True},
    "COST":  {"shares": 2.3453,  "avg_cost": 942.22,  "lt": True},
    "NFLX":  {"shares": 21.3325, "avg_cost": 101.32,  "lt": True},
    "QQQ":   {"shares": 2.7566,  "avg_cost": 606.29,  "lt": True},
    "VXUS":  {"shares": 23.8945, "avg_cost": 76.78,   "lt": False},
    "META":  {"shares": 2.3070,  "avg_cost": 610.10,  "lt": True},
    "GOOGL": {"shares": 4.0087,  "avg_cost": 299.84,  "lt": False},
    "WMT":   {"shares": 13.5867, "avg_cost": 86.21,   "lt": True},
    "SCHD":  {"shares": 19.4457, "avg_cost": 30.71,   "lt": True},
    "MSFT":  {"shares": 0.0124,  "avg_cost": 402.39,  "lt": True},
    "QCOM":  {"shares": 2.3886,  "avg_cost": 164.73,  "lt": True},
    "TSM":   {"shares": 2.0,     "avg_cost": 185.00,  "lt": False},
    "XLE":   {"shares": 7.1998,  "avg_cost": 56.32,   "lt": False},
    "VGT":   {"shares": 1.4665,  "avg_cost": 527.18,  "lt": True},
    "VHT":   {"shares": 0.5566,  "avg_cost": 268.44,  "lt": True},
    "VIS":   {"shares": 0.9725,  "avg_cost": 308.44,  "lt": True},
    "VUG":   {"shares": 0.0005,  "avg_cost": 440.56,  "lt": False},
    "RDDT":  {"shares": 1.0,     "avg_cost": 34.00,   "lt": True},
    "VTI":   {"shares": 0.7521,  "avg_cost": 255.24,  "lt": True},
}
BAKED_CRYPTO = {
    "BTC": {"shares": 0.03432981, "avg_cost": 52800.0, "lt": True},
    "XRP": {"shares": 1.066,      "avg_cost": 0.68,    "lt": False},
}

def bootstrap_if_needed() -> None:
    if not os.path.exists(TX_STORE_PATH) or os.path.getsize(TX_STORE_PATH) < 10:
        store: dict[str, Any] = {}
        for ticker, pos in BOOTSTRAP_POSITIONS.items():
            fp = hashlib.sha1(f"bootstrap|{ticker}".encode()).hexdigest()
            store[fp] = {
                "date": "2024-03-04", "code": "Buy",
                "ticker": ticker, "qty": pos["shares"],
                "price": pos["avg_cost"],
                "amount": -(pos["shares"] * pos["avg_cost"]),
                "desc": "Bootstrap", "lt": pos["lt"],
            }
        _save(TX_STORE_PATH, store)
    if not os.path.exists(CRYPTO_PATH):
        _save(CRYPTO_PATH, BAKED_CRYPTO)

# ─── Dollar / qty helpers ────────────────────────────────────────────────────────
def _clean_dollar(val: str) -> float:
    """'($1,234.56)' -> -1234.56  |  '$1,234.56' -> 1234.56  |  '' -> 0.0"""
    if not val:
        return 0.0
    s = str(val).strip()
    negative = s.startswith("(") and s.endswith(")")
    s = re.sub(r"[($,)]", "", s).strip()
    try:
        f = float(s)
        return -f if negative else f
    except ValueError:
        return 0.0

def _clean_qty(val: str) -> float:
    """'18' -> 18.0  |  '1S' -> 0.0  |  '' -> 0.0"""
    if not val:
        return 0.0
    s = re.sub(r"[^\d.]", "", str(val).strip())
    try:
        return float(s) if s else 0.0
    except ValueError:
        return 0.0

def _fingerprint(row: dict) -> str:
    key = "|".join([
        str(row.get("Activity Date", "")),
        str(row.get("Trans Code", "")),
        str(row.get("Instrument", "")),
        str(row.get("Quantity", "")),
        str(row.get("Amount", "")),
        str(row.get("Price", "")),
    ])
    return hashlib.sha1(key.encode()).hexdigest()

# ─── CSV Ingestion ───────────────────────────────────────────────────────────────
def ingest_csv(file_bytes: bytes) -> tuple[int, int, list[str]]:
    """
    Parse Robinhood CSV. Handles all 14 tx codes.
    Returns (new_rows, skipped_duplicates, errors).

    Anomalies handled:
    - SPL rows: qty=shares-added, price/amount empty
    - SXCH rows: qty may be '1S' (string) — cleaned to 0
    - LIQ rows: qty empty, amount = proceeds
    - Footer disclaimer in None key of last row — skipped
    - Parentheses amount convention: (xx) = negative
    """
    store = _load(TX_STORE_PATH, {})
    new_rows = skipped = 0
    errors: list[str] = []

    try:
        text = file_bytes.decode("utf-8", errors="replace")
        for row in csv.DictReader(io.StringIO(text)):
            code = (row.get("Trans Code") or "").strip()
            if not code:
                continue
            desc = (row.get("Description") or "").lower()
            if "data provided is for informational" in desc:
                continue

            fp = _fingerprint(row)
            if fp in store:
                skipped += 1
                continue

            instrument = (row.get("Instrument") or "").strip()
            qty   = _clean_qty(row.get("Quantity", ""))
            price = _clean_dollar(row.get("Price", ""))
            amt   = _clean_dollar(row.get("Amount", ""))

            try:
                act_date = datetime.strptime(
                    (row.get("Activity Date") or "").strip(), "%m/%d/%Y"
                ).date()
            except ValueError:
                act_date = date.today()

            lt = (date.today() - act_date).days >= 366

            store[fp] = {
                "date": str(act_date), "code": code,
                "ticker": instrument, "qty": qty,
                "price": price, "amount": amt,
                "desc": (row.get("Description") or "").replace("\n", " ")[:120],
                "lt": lt,
            }
            new_rows += 1
    except Exception as e:
        errors.append(f"CSV parse error: {e}")

    _save(TX_STORE_PATH, store)
    return new_rows, skipped, errors

# ─── PDF Crypto Parsing ──────────────────────────────────────────────────────────
_CRYPTO_NAME_MAP = {
    "bitcoin": "BTC", "ethereum": "ETH", "ripple": "XRP",
    "xrp": "XRP", "solana": "SOL", "dogecoin": "DOGE",
}

def parse_crypto_pdf(file_bytes: bytes) -> dict[str, Any]:
    """
    Parse Robinhood Crypto monthly PDF statement.
    Extracts: ticker, shares, market_value, period_end, closing_balance.

    Strategy:
    1. pdfplumber table extraction on page 2
    2. Regex fallback on raw page 2 text
    """
    result: dict[str, Any] = {}
    errors: list[str] = []

    if not _PDF_AVAILABLE:
        return {"_errors": ["pdfplumber not installed — pip install pdfplumber"]}

    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            if len(pdf.pages) < 2:
                return {"_errors": ["PDF has <2 pages — unexpected format"]}

            page2 = pdf.pages[1]
            page2_text = page2.extract_text() or ""

            # ── Strategy 1: pdfplumber table ──
            for table in (page2.extract_tables() or []):
                for row in table:
                    if not row:
                        continue
                    cells = [str(c or "").strip() for c in row]
                    if len(cells) < 3:
                        continue
                    ticker = _CRYPTO_NAME_MAP.get(cells[0].lower(), "")
                    if not ticker:
                        sym = re.sub(r"[^A-Z]", "", cells[2].upper())
                        ticker = sym if sym in CRYPTO_BASE else ""
                    if not ticker:
                        continue
                    qty  = _clean_qty(cells[1]) if len(cells) > 1 else 0
                    mval = _clean_dollar(cells[3]) if len(cells) > 3 else 0
                    try:
                        pct = float(re.sub(r"[^\d.]", "", cells[4])) if len(cells) > 4 else 0.0
                    except ValueError:
                        pct = 0.0
                    if qty > 0:
                        result[ticker] = {"shares": qty, "market_value": mval,
                                          "pct": pct, "source": "pdf_table"}

            # ── Strategy 2: regex fallback ──
            if not result:
                for m in re.finditer(
                    r"(Bitcoin|Ethereum|XRP|Solana|Dogecoin)\s+([\d.]+)\s+([A-Z]+)"
                    r"\s+\$([\d,]+\.?\d*)\s+([\d.]+)%",
                    page2_text, re.IGNORECASE
                ):
                    name   = m.group(1)
                    qty    = _clean_qty(m.group(2))
                    symbol = m.group(3).upper()
                    mval   = _clean_dollar("$" + m.group(4))
                    pct    = float(m.group(5))
                    ticker = _CRYPTO_NAME_MAP.get(name.lower(), symbol)
                    if qty > 0:
                        result[ticker] = {"shares": qty, "market_value": mval,
                                          "pct": pct, "source": "pdf_regex"}

            # Extract metadata
            if m := re.search(r"PERIOD END\s+([\d-]+)", page2_text):
                for v in result.values():
                    if isinstance(v, dict):
                        v["period_end"] = m.group(1)
            if m := re.search(r"CLOSING BALANCE\s+\$([\d.,]+)", page2_text):
                for v in result.values():
                    if isinstance(v, dict):
                        v["closing_balance"] = _clean_dollar("$" + m.group(1))

    except Exception as e:
        errors.append(f"PDF parse error: {e}")

    if errors:
        result["_errors"] = errors
    return result

def merge_pdf_into_crypto_overrides(pdf_data: dict) -> list[str]:
    """Merge PDF-parsed holdings into crypto_overrides.json. Returns status messages."""
    msgs: list[str] = []
    if not pdf_data or "_errors" in pdf_data:
        return [f"PDF merge skipped: {pdf_data.get('_errors', ['unknown'])}"]

    overrides = _load(CRYPTO_PATH, BAKED_CRYPTO)
    for ticker, data in pdf_data.items():
        if ticker.startswith("_") or not isinstance(data, dict):
            continue
        shares = data.get("shares", 0)
        mval   = data.get("market_value", 0)
        if shares <= 0:
            continue
        existing_cost = (overrides.get(ticker) or {}).get("avg_cost", 0)
        new_cost = (mval / shares) if shares > 0 else 0
        overrides[ticker] = {
            "shares":   shares,
            "avg_cost": existing_cost if existing_cost > 0 else new_cost,
            "lt":       (overrides.get(ticker) or {}).get("lt", False),
            "pdf_mval": mval,
        }
        msgs.append(f"✓ {ticker}: {shares:.6f} shares merged from PDF (mval ${mval:,.2f})")

    _save(CRYPTO_PATH, overrides)
    return msgs

# ─── Portfolio Recompute ─────────────────────────────────────────────────────────
def recompute_portfolio() -> dict[str, dict]:
    """
    Full tx-store replay. Handles: Buy, Sell, LIQ, SPL, REC (SXCH/CDIV/fees skipped).
    SPL = stock split: adds shares, avg_cost decreases, cost_basis unchanged.
    LIQ = liquidation: qty may be 0 -> treat as full sell.
    """
    store  = _load(TX_STORE_PATH, {})
    crypto = _load(CRYPTO_PATH, BAKED_CRYPTO)
    rows   = sorted(store.values(), key=lambda r: r.get("date", ""))
    positions: dict[str, dict] = {}

    for row in rows:
        ticker = (row.get("ticker") or "").strip()
        code   = (row.get("code") or "").strip()
        qty    = float(row.get("qty", 0) or 0)
        price  = float(row.get("price", 0) or 0)
        amt    = float(row.get("amount", 0) or 0)
        desc   = (row.get("desc") or "").lower()
        lt     = bool(row.get("lt", False))

        if not ticker or code in SKIP_CODES:
            continue
        if ticker not in positions:
            positions[ticker] = {
                "shares": 0.0, "cost_basis": 0.0, "avg_cost": 0.0,
                "lt": False,   "drip_shares": 0.0, "drip_amount": 0.0,
                "first_buy": row.get("date", ""),
            }

        pos = positions[ticker]

        if code in BUY_CODES:
            is_drip = any(k in desc for k in ("reinvestment", "drip", "recurring"))
            cost = abs(amt) if amt != 0 else (qty * price)
            if qty > 0:
                new_total_shares = pos["shares"] + qty
                pos["cost_basis"] += cost
                pos["avg_cost"]    = pos["cost_basis"] / new_total_shares if new_total_shares > 0 else pos["avg_cost"]
                pos["shares"]      = new_total_shares
            if is_drip:
                pos["drip_shares"] += qty
                pos["drip_amount"] += cost
            if lt:
                pos["lt"] = True

        elif code in SELL_CODES:
            sell_qty = qty if qty > 0 else pos["shares"]  # LIQ with qty=0 -> sell all
            if pos["shares"] > 0 and sell_qty > 0:
                ratio = min(sell_qty / pos["shares"], 1.0)
                pos["cost_basis"] *= (1 - ratio)
            pos["shares"] = max(0.0, pos["shares"] - sell_qty)
            if pos["shares"] < 0.0001:
                pos["shares"] = 0.0

        elif code in SPLIT_CODES:
            # Split: more shares, same total cost basis -> lower avg_cost
            if qty > 0:
                new_shares = pos["shares"] + qty
                pos["avg_cost"] = pos["cost_basis"] / new_shares if new_shares > 0 else pos["avg_cost"]
                pos["shares"]   = new_shares

    # Remove zeroed positions
    positions = {t: p for t, p in positions.items() if p["shares"] > 0.0001}

    # Merge crypto
    for c_ticker, c_data in crypto.items():
        if c_ticker.startswith("_") or not isinstance(c_data, dict):
            continue
        sh = float(c_data.get("shares", 0) or 0)
        ac = float(c_data.get("avg_cost", 0) or 0)
        if sh <= 0:
            continue
        if c_ticker not in positions:
            positions[c_ticker] = {
                "shares": sh, "cost_basis": sh * ac, "avg_cost": ac,
                "lt": bool(c_data.get("lt", False)),
                "drip_shares": 0.0, "drip_amount": 0.0, "first_buy": "2026-02-01",
            }
        else:
            pos = positions[c_ticker]
            ns  = pos["shares"] + sh
            nc  = pos["cost_basis"] + sh * ac
            pos.update({"shares": ns, "cost_basis": nc,
                         "avg_cost": nc / ns if ns > 0 else ac,
                         "lt": pos["lt"] or bool(c_data.get("lt", False))})

    for p in positions.values():
        if p["shares"] > 0 and p["cost_basis"] > 0:
            p["avg_cost"] = p["cost_basis"] / p["shares"]

    return positions

# ─── Price Fetching — Fixed ───────────────────────────────────────────────────────
_MEM_CACHE: dict[str, tuple[float, float]] = {}
_CACHE_TTL = 300  # 5 min

def _load_price_cache() -> dict[str, float]:
    return _load(PRICE_CACHE_P, {})

def _save_price_cache(prices: dict[str, float]) -> None:
    existing = _load_price_cache()
    existing.update({k: v for k, v in prices.items() if v > 0})
    _save(PRICE_CACHE_P, existing)

def _yf_single(ticker: str) -> float:
    """Fetch one price from yfinance. Returns 0 on any failure."""
    if not _YF_AVAILABLE:
        return 0.0
    yf_sym = YF_TICKER_MAP.get(ticker, ticker)
    try:
        fi = yf.Ticker(yf_sym).fast_info
        for key in ("last_price", "regularMarketPrice", "previousClose"):
            v = fi.get(key)
            if v is not None and float(v) > 0:
                return float(v)
    except Exception:
        pass
    return 0.0

def _coingecko_batch(tickers: list[str]) -> dict[str, float]:
    if not tickers:
        return {}
    ids = [COINGECKO_IDS.get(t, t.lower()) for t in tickers]
    id_str = ",".join(dict.fromkeys(ids))
    try:
        r = requests.get(
            f"https://api.coingecko.com/api/v3/simple/price?ids={id_str}&vs_currencies=usd",
            timeout=8, headers={"Accept": "application/json"}
        )
        r.raise_for_status()
        data = r.json()
        return {t: float(data.get(COINGECKO_IDS.get(t, t.lower()), {}).get("usd", 0) or 0)
                for t in tickers}
    except Exception:
        return {}

def get_clean_prices(tickers: tuple, bust: int = 0) -> tuple[dict[str, float], dict[str, str]]:
    """
    THE key fix for the $1 bug.

    For each ticker the fallback chain is:
      1. CoinGecko (crypto) or yfinance (stocks)  → status: 'live'
      2. In-process memory cache (TTL: 5 min)     → status: 'cached'
      3. Disk price cache (last known good price)  → status: 'fallback'
      4. Never defaults to $1

    Returns (prices_dict, status_dict).
    """
    now        = time.time()
    prices:  dict[str, float] = {}
    status:  dict[str, str]   = {}
    disk     = _load_price_cache()

    crypto_t = [t for t in tickers if t in CRYPTO_BASE]
    stock_t  = [t for t in tickers if t not in CRYPTO_BASE]

    # ── Crypto: CoinGecko batch ──
    cg = _coingecko_batch(crypto_t)
    for t in crypto_t:
        ck = f"{t}|{bust}"
        mem = _MEM_CACHE.get(ck)
        if mem and (now - mem[1]) < _CACHE_TTL and bust == 0:
            prices[t] = mem[0]; status[t] = "cached"
        elif cg.get(t, 0) > 0:
            prices[t] = cg[t];  status[t] = "live"
            _MEM_CACHE[ck] = (cg[t], now)
        elif disk.get(t, 0) > 0:
            prices[t] = disk[t]; status[t] = "fallback"
        # If all fail, left out of prices → _safe_price uses avg_cost

    # ── Stocks: yfinance per-ticker ──
    for t in stock_t:
        ck = f"{t}|{bust}"
        mem = _MEM_CACHE.get(ck)
        if mem and (now - mem[1]) < _CACHE_TTL and bust == 0:
            prices[t] = mem[0]; status[t] = "cached"
            continue
        p = _yf_single(t)
        if p > 0:
            prices[t] = p; status[t] = "live"
            _MEM_CACHE[ck] = (p, now)
        elif disk.get(t, 0) > 0:
            prices[t] = disk[t]; status[t] = "fallback"

    # Persist live prices
    live = {t: p for t, p in prices.items() if status.get(t) == "live"}
    if live:
        _save_price_cache(live)

    return prices, status

def _safe_price(ticker: str, pos: dict, prices: dict) -> float:
    """Returns live price or avg_cost. Never 0. Never 1 unless position cost is truly zero."""
    p = prices.get(ticker)
    if p and float(p) > 0:
        return float(p)
    avg = float(pos.get("avg_cost") or 0)
    return avg if avg > 0 else 0.01

def data_health_summary(status: dict[str, str]) -> dict:
    live = sum(1 for s in status.values() if s == "live")
    cached = sum(1 for s in status.values() if s == "cached")
    fallback = sum(1 for s in status.values() if s == "fallback")
    total = len(status)
    if total == 0:
        return {"label": "No Data", "color": "gray", "live": 0, "cached": 0, "fallback": 0, "total": 0}
    if fallback == 0:
        return {"label": "🟢 Live", "color": "green", "live": live, "cached": cached, "fallback": 0, "total": total}
    elif live > 0 or cached > 0:
        return {"label": "🟡 Partial", "color": "yellow", "live": live, "cached": cached, "fallback": fallback, "total": total}
    else:
        return {"label": "🔴 Stale", "color": "red", "live": 0, "cached": cached, "fallback": fallback, "total": total}

# ─── Portfolio Enrichment ────────────────────────────────────────────────────────
def enrich_portfolio(positions: dict, prices: dict) -> list[dict]:
    rows = []
    for ticker, pos in positions.items():
        live   = _safe_price(ticker, pos, prices)
        shares = float(pos.get("shares", 0))
        avg    = float(pos.get("avg_cost", 0))
        equity = live * shares
        cost   = avg  * shares
        pl     = equity - cost
        pl_pct = (pl / cost * 100) if cost > 0 else 0.0
        target = PRICE_TARGETS.get(ticker, 0.0)
        upside = ((target - live) / live * 100) if (target and live > 0) else 0.0
        rows.append({
            "ticker": ticker, "shares": shares, "avg_cost": avg,
            "live_price": live, "equity": equity, "cost_basis": cost,
            "pl": pl, "pl_pct": pl_pct, "lt": bool(pos.get("lt", False)),
            "upside": upside, "target": target,
            "drip_shares": float(pos.get("drip_shares", 0)),
            "drip_amount": float(pos.get("drip_amount", 0)),
            "first_buy":   pos.get("first_buy", ""),
        })
    rows.sort(key=lambda r: r["equity"], reverse=True)
    return rows

# ─── AI Target Engine ────────────────────────────────────────────────────────────
def generate_suggested_targets(rows: list[dict], total_value: float) -> dict[str, float]:
    """
    Moderate-Aggressive target allocations.

    Allocation philosophy:
    - Core broad ETFs (VOO/QQQ/VYM/SCHD/VTI): 35-40% total
    - Mega-cap tech growth (NVDA/AAPL/META/GOOGL/MSFT): 30-35%
    - Dividend/value (BRK.B/WMT/COST): 8-10%
    - Sector/intl ETFs (VXUS/XLE/VGT/VHT/VIS): 7-9%
    - Commodities (GLD): 4-5%
    - Crypto (BTC+XRP): max 5% combined
    - Individual speculative stocks: 1-3% each

    Returns only tickers that exist in the current portfolio.
    Values normalised to sum to 100%.
    """
    tickers_in = {r["ticker"] for r in rows}
    base: dict[str, float] = {
        "VOO": 20.0, "QQQ": 10.0, "VYM": 10.0, "SCHD": 5.0, "VTI": 4.0,
        "NVDA": 12.0, "AAPL": 6.0, "META": 4.0, "GOOGL": 4.0, "MSFT": 3.0,
        "BRK.B": 4.0, "WMT": 2.0, "COST": 2.0,
        "VXUS": 3.0, "GLD": 4.0, "VGT": 2.0, "XLE": 2.0, "VHT": 1.5, "VIS": 1.0,
        "NFLX": 2.0, "TSM": 2.0, "QCOM": 1.5, "RDDT": 0.5,
        "BTC": 3.5, "XRP": 0.5,
    }
    filtered = {t: w for t, w in base.items() if t in tickers_in}
    total_w = sum(filtered.values())
    if total_w > 0:
        filtered = {t: round(w / total_w * 100, 1) for t, w in filtered.items()}
    return filtered

# ─── Rebalancing ─────────────────────────────────────────────────────────────────
def compute_rebalancing(rows: list[dict], total_value: float, targets: dict[str, float]) -> list[dict]:
    result = []
    for r in rows:
        current_pct = (r["equity"] / total_value * 100) if total_value > 0 else 0
        target_pct  = targets.get(r["ticker"], 0.0)
        result.append({**r, "current_pct": current_pct,
                        "target_pct": target_pct, "drift": current_pct - target_pct})
    return sorted(result, key=lambda x: x["drift"])

def compute_deposit_allocation(
    deposit: float, rows: list[dict], total_value: float,
    targets: dict[str, float], deposit_num: int, prices: dict
) -> list[dict]:
    """
    Returns allocation table with columns:
    ticker | current_value | target_pct | action (BUY/TRIM) | amount | est_shares | live_price | reason
    """
    rotating_ticker = DEPOSIT_ROTATING[(deposit_num - 1) % len(DEPOSIT_ROTATING)]
    has_targets = any(v > 0 for v in targets.values())
    allocs: list[dict] = []

    if has_targets:
        drift_rows = compute_rebalancing(rows, total_value, targets)
        remaining = deposit
        for dr in sorted(drift_rows, key=lambda x: x["drift"]):   # most underweight first
            if dr["drift"] >= -1.5 or dr["target_pct"] <= 0:
                continue
            needed = abs(dr["drift"]) / 100 * total_value
            alloc  = min(remaining, needed)
            if alloc < 5:
                continue
            live = dr["live_price"]
            allocs.append({
                "ticker":        dr["ticker"],
                "current_value": round(dr["equity"], 2),
                "target_pct":    dr["target_pct"],
                "action":        "BUY",
                "amount":        round(alloc, 2),
                "est_shares":    round(alloc / live, 6) if live > 0 else 0,
                "live_price":    live,
                "reason":        f"Underweight {abs(dr['drift']):.1f}% vs {dr['target_pct']:.1f}% target",
            })
            remaining -= alloc
            if remaining < 5:
                break
        # Flag overweight trims
        for dr in drift_rows:
            if dr["drift"] > 5.0 and dr["target_pct"] > 0:
                trim = dr["drift"] / 100 * total_value * 0.5
                live = dr["live_price"]
                allocs.append({
                    "ticker":        dr["ticker"],
                    "current_value": round(dr["equity"], 2),
                    "target_pct":    dr["target_pct"],
                    "action":        "TRIM",
                    "amount":        round(trim, 2),
                    "est_shares":    round(trim / live, 6) if live > 0 else 0,
                    "live_price":    live,
                    "reason":        f"Overweight {dr['drift']:.1f}% above target",
                })
    else:
        row_map = {r["ticker"]: r for r in rows}
        for ticker, pct in DEPOSIT_FIXED.items():
            amt  = deposit * pct
            row  = row_map.get(ticker, {})
            live = row.get("live_price") or prices.get(ticker) or 100
            allocs.append({
                "ticker": ticker, "current_value": round(row.get("equity", 0), 2),
                "target_pct": 0.0, "action": "BUY", "amount": round(amt, 2),
                "est_shares": round(amt / live, 6) if live > 0 else 0,
                "live_price": live, "reason": "Core fixed allocation",
            })
        r_row  = row_map.get(rotating_ticker, {})
        r_live = r_row.get("live_price") or prices.get(rotating_ticker) or 100
        r_amt  = deposit * DEPOSIT_ROTATING_PCT
        allocs.append({
            "ticker": rotating_ticker, "current_value": round(r_row.get("equity", 0), 2),
            "target_pct": 0.0, "action": "BUY", "amount": round(r_amt, 2),
            "est_shares": round(r_amt / r_live, 6) if r_live > 0 else 0,
            "live_price": r_live, "reason": f"Rotating pick #{(deposit_num-1)%len(DEPOSIT_ROTATING)+1}",
        })

    return allocs

# ─── Recommendations ──────────────────────────────────────────────────────────────
def _tax_note(lt: bool) -> str:
    return "✅ Long-term — 15% cap gains" if lt else "⚠️ Short-term — 37% rate. Hold until 1-yr mark."

def generate_recommendations(rows: list[dict]) -> list[dict]:
    recs = []
    today = date.today()
    for r in rows:
        t      = r["ticker"]
        pl_pct = r["pl_pct"]
        lt     = r["lt"]
        upside = r["upside"]
        equity = r["equity"]
        upcoming_lt = any(
            k.startswith(t) and 0 <= (d - today).days <= 30
            for k, d in LT_DATES.items()
        )
        base = {"proceed_est": 0, "tax_note": _tax_note(lt)}

        if t in SELL_LIST and lt:
            recs.append({**r, **base, "badge": "SELL", "priority": 0,
                "action": "🔴 SELL NOW",
                "reason": f"Earmarked for exit. LT-eligible → 15% tax. Reinvest into VOO/VYM same day.",
                "proceed_est": equity}); continue
        if t in SELL_PENDING and lt:
            recs.append({**r, **base, "badge": "SELL", "priority": 0,
                "action": "🔴 SELL NOW",
                "reason": f"Fund upgrade: swap {t} → lower-cost ETF. LT now. Same-day swap ≠ wash sale.",
                "proceed_est": equity}); continue
        if pl_pct < -20:
            recs.append({**r, **base, "badge": "REVIEW", "priority": 1,
                "action": "🚨 REVIEW — BIG LOSS",
                "reason": f"Down {pl_pct:.1f}%. Re-evaluate thesis. DCA if intact; harvest loss if broken."}); continue
        if t in FOREVER_HOLD:
            recs.append({**r, **base, "badge": "HOLD", "priority": 2,
                "action": "♾ HOLD FOREVER — DRIP ON",
                "reason": "Core dividend compounder. Never sell. DRIP enabled."}); continue
        if t in DCA_ALWAYS:
            recs.append({**r, **base, "badge": "BUY", "priority": 2,
                "action": "📈 DCA EVERY DEPOSIT",
                "reason": "Index core. Buy every deposit regardless of price."}); continue
        if -20 <= pl_pct < -8 and upside > 20:
            recs.append({**r, **base, "badge": "BUY", "priority": 2,
                "action": "💎 STRONG BUY — DIP",
                "reason": f"Down {pl_pct:.1f}%, {upside:.0f}% upside to ${r['target']:.0f}. Add aggressively."}); continue
        if t in CRYPTO_BASE and upside > 25:
            recs.append({**r, **base, "badge": "BUY", "priority": 2,
                "action": "🚀 ACCUMULATE — CRYPTO",
                "reason": f"{upside:.0f}% upside to ${r['target']:,.0f}. Keep crypto ≤5% of portfolio."}); continue
        if upside > 20:
            recs.append({**r, **base, "badge": "BUY", "priority": 2,
                "action": "🟢 ACCUMULATE",
                "reason": f"{upside:.0f}% upside to ${r['target']:.0f}. Good entry point."}); continue
        if t in IPO_HOLDS and lt:
            trim = equity * 0.25
            recs.append({**r, **base, "badge": "TRIM", "priority": 3,
                "action": "✂️ TRIM 25% — IPO NOW LT",
                "reason": f"IPO now LT-eligible. Trim 25% ≈${trim:.0f} at 15% tax.",
                "proceed_est": trim}); continue
        if pl_pct > 20 and lt:
            trim_pct = 0.25 if t in CRYPTO_BASE or t in IPO_HOLDS else 0.20
            trim = equity * trim_pct
            recs.append({**r, **base, "badge": "TRIM", "priority": 3,
                "action": f"✂️ TRIM {int(trim_pct*100)}% — LOCK GAINS",
                "reason": f"Up {pl_pct:.1f}%, LT-eligible. Take {int(trim_pct*100)}% off table ≈${trim:.0f} at 15%.",
                "proceed_est": trim}); continue
        if upcoming_lt and not lt and pl_pct > 5:
            recs.append({**r, **base, "badge": "HOLD", "priority": 3,
                "action": "⏳ HOLD — LT IN <30 DAYS",
                "reason": "Within 30 days of LT. Do NOT sell — wait for 15% rate."}); continue
        recs.append({**r, **base, "badge": "HOLD", "priority": 4,
            "action": "🟡 HOLD",
            "reason": f"No action. Monitor for ${r['target']:.0f} target or LT eligibility."})

    recs.sort(key=lambda x: (x["priority"], -x["equity"]))
    return recs

# ─── KPIs ────────────────────────────────────────────────────────────────────────
def compute_kpis(rows: list[dict], cash: float) -> dict:
    sr = [r for r in rows if r["ticker"] not in CRYPTO_BASE]
    cr = [r for r in rows if r["ticker"] in CRYPTO_BASE]
    sv = sum(r["equity"] for r in sr)
    cv = sum(r["equity"] for r in cr)
    tv = sv + cv + cash
    tc = sum(r["cost_basis"] for r in rows)
    tp = sum(r["pl"] for r in rows)
    return {
        "total_value": tv, "stock_value": sv, "crypto_value": cv, "cash": cash,
        "total_cost": tc, "total_pl": tp,
        "total_pl_pct": (tp / tc * 100) if tc > 0 else 0,
        "positions": len(rows),
        "winners": sum(1 for r in rows if r["pl"] > 0),
        "losers":  sum(1 for r in rows if r["pl"] < 0),
        "drip_total": sum(r["drip_amount"] for r in rows),
    }

# ─── Deposit schedule ────────────────────────────────────────────────────────────
def get_deposit_schedule(n: int = 16) -> list[dict]:
    schedule = []
    d = FIRST_DEPOSIT_DATE
    today = date.today()
    while d < today:
        d += timedelta(weeks=2)
    for i in range(n):
        num = i + 1
        schedule.append({"num": num, "date": d,
                          "rotating": DEPOSIT_ROTATING[(num - 1) % len(DEPOSIT_ROTATING)]})
        d += timedelta(weeks=2)
    return schedule

# ─── History ─────────────────────────────────────────────────────────────────────
def save_snapshot(kpis: dict, recs: list[dict]) -> None:
    hist = _load(REC_HIST_PATH, [])
    hist.append({
        "ts": datetime.now().isoformat(),
        "total_value": kpis["total_value"], "stock_value": kpis["stock_value"],
        "crypto_value": kpis["crypto_value"], "cash": kpis["cash"],
        "total_pl": kpis["total_pl"], "total_pl_pct": kpis["total_pl_pct"],
        "recs": [{"ticker": r["ticker"], "action": r["action"],
                  "live": r["live_price"], "pl_pct": r["pl_pct"]} for r in recs[:30]],
    })
    _save(REC_HIST_PATH, hist[-200:])

def log_deposit(num: int, allocs: list[dict], total: float, notes: str = "") -> None:
    log = _load(DEPOSIT_LOG, [])
    log.append({"ts": datetime.now().isoformat(), "deposit_num": num,
                 "total": total, "allocations": allocs, "notes": notes})
    _save(DEPOSIT_LOG, log)

def load_targets() -> dict[str, float]:
    return _load(TARGETS_PATH, {})

def save_targets(targets: dict[str, float]) -> None:
    _save(TARGETS_PATH, targets)

def update_crypto_override(ticker: str, shares: float, avg_cost: float, lt: bool) -> None:
    c = _load(CRYPTO_PATH, BAKED_CRYPTO)
    c[ticker] = {"shares": shares, "avg_cost": avg_cost, "lt": lt}
    _save(CRYPTO_PATH, c)
