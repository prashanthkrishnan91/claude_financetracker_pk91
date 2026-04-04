"""
data_engine.py — Portfolio War Room v11.4
All business logic — zero UI code.

v11.4 changes (dedup refactor):
  - make_tx_fingerprint() rewritten with _norm_decimal() for lossless 6dp normalisation
  - ingest_csv() is now PURE — no disk writes; returns (new_rows, IngestStats)
  - commit_new_transactions(new_rows) is the single commit step (atomic rename)
  - 3-layer dedup race condition fixed: existing_ids.update() runs POST-loop only
  - IngestStats gains already_on_disk / seen_in_file / parse_errors counters
  - strip_existing_tx_store_fingerprints() unchanged (cold-start seed)

v11.3 changes:
  - _parse_date_robust() handles M/D/YYYY Robinhood CSV dates
  - is_lt_eligible() / days_to_lt() use _parse_date_robust()
  - All 5 applymap() → map() in main_app.py (pandas 3.0 compat — documented here)

v11.2 changes:
  - compute_rebalancing(cash_available=0.0) — cash-informed drift
  - generate_deposit_recs(cash_balance=0.0) — total_investable = deposit + cash
  - DecisionLogEntry / log_decision / load_decision_log / apply_overrides_to_recs

v11.1 changes:
  - smart_sync_portfolio() using HoldingsManager 24h cache
  - get_holdings_cache_status() for sidebar badge

v11.0 changes:
  - fetch_prices() routes to PriceService (Finnhub → Polygon → CoinGecko → cache)
  - yfinance removed from primary price path
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
from dataclasses import asdict, dataclass, field
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from pathlib import Path
from typing import Optional

# ─── v11 real-time + smart-sync modules ──────────────────────────────────────
try:
    from price_service import PriceService, PriceResult
    from holdings_manager import HoldingsManager, HoldingsCache
    from portfolio_aggregator import PortfolioAggregator
    _V11_AVAILABLE = True
except ImportError:
    _V11_AVAILABLE = False

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# FILE PATHS
# ═══════════════════════════════════════════════════════════════════════════════
TX_STORE_PATH       = Path("tx_store.json")
CRYPTO_OVR_PATH     = Path("crypto_overrides.json")
REC_HISTORY_PATH    = Path("rec_history.json")
DEPOSIT_LOG_PATH    = Path("deposit_log.json")
TARGETS_PATH        = Path("targets.json")
PRICE_CACHE_PATH    = Path("price_cache.json")
RECON_LOG_PATH      = Path("recon_log.json")
HOLDINGS_CACHE_PATH = Path("holdings_cache.json")
PLAID_SNAPSHOT_PATH = Path("plaid_snapshot.json")
DECISION_LOG_PATH   = Path("decision_log.json")

MAX_RECON_ROWS = 100
MAX_REC_HISTORY = 200
MAX_DECISION_LOG = 500

# ═══════════════════════════════════════════════════════════════════════════════
# CLASSIFICATION LISTS
# ═══════════════════════════════════════════════════════════════════════════════
FOREVER_HOLD   = {"VYM", "SCHD", "VTI"}
DCA_ALWAYS     = {"VOO", "QQQ"}
SELL_LIST      = {"VTV", "VEA", "VWO", "BND"}
SELL_PENDING   = {"SPY", "VUG"}
IPO_HOLDS      = {"BLSH", "KLAR", "STUB"}
CRYPTO_TICKERS = {"BTC", "ETH", "XRP", "SOL", "DOGE", "ADA", "AVAX", "MATIC", "DOT", "LTC"}
ETF_TICKERS    = {
    "VOO","QQQ","VYM","VTI","SCHD","GLD","VGT","XLE","VHT","VIS","VXUS",
    "SPY","VUG","VTV","VEA","VWO","BND","IVV","IEFA","AGG",
}

TARGETS: dict[str, float] = {
    "NVDA":  180.0,  "META":  700.0,  "GOOGL": 210.0,  "AAPL":  235.0,
    "MSFT":  480.0,  "NFLX": 1000.0,  "COST": 1050.0,  "TSM":   220.0,
    "CRM":   370.0,  "QCOM":  200.0,  "WMT":   115.0,  "BRK-B": 550.0,
    "VOO":   600.0,  "QQQ":   520.0,  "VYM":   140.0,  "SCHD":   95.0,
    "VTI":   310.0,  "GLD":   340.0,  "VGT":   600.0,  "XLE":   100.0,
    "VHT":   280.0,  "VIS":   250.0,  "VXUS":   70.0,  "RDDT":  200.0,
    "BTC": 150000.0, "XRP":     5.0,  "SPY":   650.0,  "VUG":   450.0,
    "ALK":    70.0,  "AMD":   160.0,  "SNOW":  180.0,
}

DEPOSIT_ROTATION = ["META", "GOOGL", "AAPL", "MSFT", "COST", "TSM", "CRM", "NFLX"]
DEPOSIT_PLAN: list[tuple[str, float]] = [
    ("NVDA", 0.28), ("VOO", 0.22), ("VYM", 0.17), ("QQQ", 0.17), ("ROTATING", 0.16),
]

DEPOSIT_START = datetime.date(2026, 4, 3)
DEPOSIT_AMOUNT = 900.0

ACTION_CALENDAR = [
    {"Date": "Apr 3",  "Type": "SELL",  "Ticker": "VTV,VEA,VWO,BND", "Note": "LT eligible — reinvest VOO/VYM same day"},
    {"Date": "Apr 3",  "Type": "BUY",   "Ticker": "Deposit #1",       "Note": "$900 → NVDA/VOO/VYM/QQQ + META"},
    {"Date": "Apr 4",  "Type": "TRIM",  "Ticker": "GLD",              "Note": "GLD turns LT — trim 25% near $450"},
    {"Date": "Apr 17", "Type": "BUY",   "Ticker": "Deposit #2",       "Note": "$900 → NVDA/VOO/VYM/QQQ + GOOGL"},
    {"Date": "May 1",  "Type": "BUY",   "Ticker": "Deposit #3",       "Note": "$900 → NVDA/VOO/VYM/QQQ + AAPL"},
    {"Date": "May 20", "Type": "SELL",  "Ticker": "SPY",              "Note": "SPY turns LT — sell, buy VOO same day"},
    {"Date": "Jul 15", "Type": "SELL",  "Ticker": "VUG",              "Note": "VUG turns LT — sell, buy QQQ same day"},
    {"Date": "Aug 14", "Type": "EVAL",  "Ticker": "BLSH",             "Note": "Hits 1yr — trim 25% if up >20%"},
    {"Date": "Sep 11", "Type": "EVAL",  "Ticker": "KLAR",             "Note": "Hits 1yr — trim 25% if up >20%"},
    {"Date": "Nov 6",  "Type": "TRIM",  "Ticker": "TSM",              "Note": "Big lot turns LT — trim 20%"},
    {"Date": "Dec 15", "Type": "TRIM",  "Ticker": "GOOGL",            "Note": "Big lot turns LT — trim 20%"},
    {"Date": "Dec 20", "Type": "TAX",   "Ticker": "Portfolio",        "Note": "Year-end harvest — net gains vs losses"},
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
    "SCHD":  {"shares": "6.895300",   "avg_cost": "78.5000",  "first_buy_date": "2024-03-18", "category": "ETF"},
    "VGT":   {"shares": "1.312400",   "avg_cost": "445.0000", "first_buy_date": "2024-03-18", "category": "ETF"},
    "VHT":   {"shares": "1.028600",   "avg_cost": "252.0000", "first_buy_date": "2024-03-18", "category": "ETF"},
    "VIS":   {"shares": "1.212500",   "avg_cost": "220.0000", "first_buy_date": "2024-03-18", "category": "ETF"},
    "XLE":   {"shares": "3.840200",   "avg_cost": "91.5000",  "first_buy_date": "2024-03-18", "category": "ETF"},
    "VXUS":  {"shares": "5.212300",   "avg_cost": "59.8000",  "first_buy_date": "2024-03-18", "category": "ETF"},
    "SPY":   {"shares": "0.511200",   "avg_cost": "524.0000", "first_buy_date": "2025-05-20", "category": "ETF"},
    "VUG":   {"shares": "1.023400",   "avg_cost": "380.0000", "first_buy_date": "2025-07-15", "category": "ETF"},
    "VTV":   {"shares": "0.872100",   "avg_cost": "154.0000", "first_buy_date": "2024-03-18", "category": "ETF"},
    "VEA":   {"shares": "3.412300",   "avg_cost": "48.5000",  "first_buy_date": "2024-03-18", "category": "ETF"},
    "VWO":   {"shares": "2.891200",   "avg_cost": "42.3000",  "first_buy_date": "2024-03-18", "category": "ETF"},
    "BND":   {"shares": "3.102400",   "avg_cost": "72.1000",  "first_buy_date": "2024-03-18", "category": "ETF"},
    "META":  {"shares": "2.302400",   "avg_cost": "490.0000", "first_buy_date": "2025-03-01", "category": "Stocks"},
    "GOOGL": {"shares": "4.003300",   "avg_cost": "165.0000", "first_buy_date": "2024-12-01", "category": "Stocks"},
    "AAPL":  {"shares": "2.597700",   "avg_cost": "172.5000", "first_buy_date": "2024-03-18", "category": "Stocks"},
    "MSFT":  {"shares": "0.012400",   "avg_cost": "398.0000", "first_buy_date": "2024-03-18", "category": "Stocks"},
    "COST":  {"shares": "2.342300",   "avg_cost": "880.0000", "first_buy_date": "2024-08-01", "category": "Stocks"},
    "TSM":   {"shares": "3.500000",   "avg_cost": "155.0000", "first_buy_date": "2024-11-01", "category": "Stocks"},
    "CRM":   {"shares": "1.200000",   "avg_cost": "285.0000", "first_buy_date": "2024-09-01", "category": "Stocks"},
    "QCOM":  {"shares": "2.372400",   "avg_cost": "158.0000", "first_buy_date": "2024-03-18", "category": "Stocks"},
    "WMT":   {"shares": "4.149000",   "avg_cost": "62.0000",  "first_buy_date": "2024-03-18", "category": "Stocks"},
    "BRK-B": {"shares": "4.515400",   "avg_cost": "360.0000", "first_buy_date": "2024-06-01", "category": "Stocks"},
    "RDDT":  {"shares": "1.000000",   "avg_cost": "34.0000",  "first_buy_date": "2025-03-01", "category": "Stocks"},
    "ALK":   {"shares": "0.608700",   "avg_cost": "41.0700",  "first_buy_date": "2025-04-01", "category": "Stocks"},
    "SNOW":  {"shares": "0.780800",   "avg_cost": "158.0000", "first_buy_date": "2025-04-01", "category": "Stocks"},
    "BMWYY": {"shares": "1.000000",   "avg_cost": "39.7200",  "first_buy_date": "2025-03-01", "category": "Stocks"},
    "BLSH":  {"shares": "10.000000",  "avg_cost": "37.0000",  "first_buy_date": "2025-08-14", "category": "Stocks"},
    "KLAR":  {"shares": "11.000000",  "avg_cost": "40.0000",  "first_buy_date": "2025-09-11", "category": "Stocks"},
    "STUB":  {"shares": "23.356100",  "avg_cost": "25.6200",  "first_buy_date": "2025-09-18", "category": "Stocks"},
    "BTC":   {"shares": "0.023644",   "avg_cost": "42000.00", "first_buy_date": "2024-09-01", "category": "Crypto"},
    "XRP":   {"shares": "1.066000",   "avg_cost": "0.6800",   "first_buy_date": "2024-11-01", "category": "Crypto"},
}

# ═══════════════════════════════════════════════════════════════════════════════
# DISK I/O HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _load(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default
    return default


def _save(path: Path, obj) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def _load_env_from_secrets():
    for key in ("FINNHUB_API_KEY", "POLYGON_API_KEY", "PLAID_ACCESS_TOKEN",
                "PLAID_CLIENT_ID", "PLAID_SECRET", "PLAID_ENV",
                "HOLDINGS_CACHE_TTL_HOURS"):
        try:
            val = st.secrets.get(key)
            if val and not os.environ.get(key):
                os.environ[key] = str(val)
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════════════
# BOOTSTRAP  (writes tx_store.json once if file doesn't exist)
# ═══════════════════════════════════════════════════════════════════════════════

def bootstrap_tx_store() -> None:
    if TX_STORE_PATH.exists():
        try:
            if json.loads(TX_STORE_PATH.read_text()):
                return
        except Exception:
            pass
    synthetic: dict[str, dict] = {}
    for ticker, pos in BAKED_BOOTSTRAP.items():
        key = hashlib.sha256(f"BOOTSTRAP|{ticker}".encode()).hexdigest()
        synthetic[key] = {
            "date":        pos["first_buy_date"],
            "trans_code":  "BUY",
            "ticker":      ticker,
            "qty":         pos["shares"],
            "price":       pos["avg_cost"],
            "amount":      str(Decimal(pos["shares"]) * Decimal(pos["avg_cost"])),
            "description": "Bootstrap",
            "category":    pos["category"],
            "is_drip":     False,
            "fingerprint": key,
        }
    _save(TX_STORE_PATH, synthetic)
    logger.info("Bootstrap: wrote %d positions to tx_store.json", len(synthetic))


# ═══════════════════════════════════════════════════════════════════════════════
# ① TRANSACTION DEDUPLICATION  (v11.4 refactor)
# ═══════════════════════════════════════════════════════════════════════════════

def _norm_decimal(raw: str, places: int = 6) -> str:
    """Normalise a money/qty string to fixed decimal places for fingerprinting."""
    try:
        d = Decimal(
            str(raw).strip()
                    .replace(",", "")
                    .replace("$", "")
                    .replace("(", "-")
                    .replace(")", "")
        )
        return f"{d:.{places}f}"
    except (InvalidOperation, ValueError):
        return "0." + "0" * places


def make_tx_fingerprint(
    date: str,
    trans_code: str,
    ticker: str,
    qty: str,
    price: str,
    amount: str,
) -> str:
    """
    SHA-256 fingerprint for one Robinhood transaction row.

    Canonical string:
        "{date}|{TRANS_CODE}|{TICKER}|{qty_6dp}|{price_6dp}|{amt_6dp}"

    All fields normalised so '875.22' == '875.220000', 'Buy' == 'BUY', etc.
    Amount is included as tiebreaker for cash-only ACH rows (no ticker/qty).
    """
    canonical = "|".join([
        str(date).strip(),
        str(trans_code).strip().upper(),
        str(ticker).strip().upper(),
        _norm_decimal(qty,    places=6),
        _norm_decimal(price,  places=6),
        _norm_decimal(amount, places=6),
    ])
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def strip_existing_tx_store_fingerprints() -> set:
    """
    Return set of all fingerprints already in tx_store.json.
    Call once at cold-start to pre-seed session_state.processed_ids.
    """
    store = _load(TX_STORE_PATH, {})
    return set(store.keys())


# ─── IngestStats dataclass ────────────────────────────────────────────────────

@dataclass
class IngestStats:
    filename:        str  = ""
    total_rows:      int  = 0
    imported:        int  = 0
    already_on_disk: int  = 0   # Layer 1+2 catches
    seen_in_file:    int  = 0   # Layer 3 intra-file dupes
    no_code:         int  = 0   # rows with no tradeable trans code
    parse_errors:    int  = 0
    new_tickers:     list = field(default_factory=list)

    @property
    def total_skipped(self) -> int:
        return self.already_on_disk + self.seen_in_file + self.no_code + self.parse_errors


# ─── Trans-code map ───────────────────────────────────────────────────────────

_TRANS_CODE_MAP = {
    "Buy":   "BUY",  "Sell":  "SELL", "CDIV":  "CDIV",
    "SLIP":  "SLIP", "SPL":   "SPL",  "ACH":   "ACH",
    "ACHAT": "ACH",  "JNLS":  "JNLS", "DFEE":  "DFEE",
    "RINT":  "RINT", "BSELL": "SELL", "BBUY":  "BUY",
    "REC":   "REC",  "LIQ":   "LIQ",  "SXCH":  "SXCH",
    "RTP":   "ACH",  "MISC":  "MISC", "ACATS": "ACATS",
}
_SKIP_CODES = {"", "GOLD", "MARGIN", "MARG", "INT", "FEE"}


# ─── Robust date parser (v11.3) ───────────────────────────────────────────────

def _parse_date_robust(date_str: str) -> Optional[datetime.date]:
    """Parse any Robinhood date format. Returns None on failure."""
    import pandas as pd  # lazy import
    if not date_str:
        return None
    try:
        return datetime.date.fromisoformat(str(date_str).strip())
    except (ValueError, TypeError):
        pass
    try:
        return pd.to_datetime(date_str, dayfirst=False).date()
    except Exception:
        return None


def _date_to_iso(raw: str) -> str:
    d = _parse_date_robust(raw)
    return d.isoformat() if d else str(raw).strip()


# ─── ingest_csv — PURE (no disk writes) ──────────────────────────────────────

def ingest_csv(
    csv_bytes: bytes,
    filename: str = "upload.csv",
    existing_ids: Optional[set] = None,
) -> tuple[dict, IngestStats]:
    """
    Parse a Robinhood CSV.  Returns (new_rows_dict, IngestStats).

    PURE — never writes to disk.  Call commit_new_transactions(new_rows) after.

    Three dedup layers:
      Layer 2 — Disk snapshot  (existing_on_disk) — frozen before loop
      Layer 1 — Session set    (existing_ids)      — previous uploads this session
      Layer 3 — Intra-file     (seen_this_upload)  — duplicates in THIS file

    existing_ids is updated POST-LOOP so Layer 3 always fires for intra-file dupes.
    """
    if existing_ids is None:
        existing_ids = set()

    stats = IngestStats(filename=filename)

    # Layer 2 snapshot: read disk ONCE before the loop, never mutated during it
    tx_store_on_disk = _load(TX_STORE_PATH, {})
    existing_on_disk = set(tx_store_on_disk.keys())
    known_tickers    = {v.get("ticker", "") for v in tx_store_on_disk.values() if v.get("ticker")}

    seen_this_upload: set = set()
    new_rows: dict = {}

    try:
        text = csv_bytes.decode("utf-8", errors="replace").replace("\x00", "")
    except Exception:
        stats.parse_errors += 1
        return new_rows, stats

    # Strip Robinhood disclaimer footer
    lines = []
    for line in text.splitlines():
        s = line.strip().strip('"')
        if s.startswith("The data provided"):
            break
        lines.append(line)

    reader = csv.DictReader(io.StringIO("\n".join(lines)), quoting=csv.QUOTE_ALL)

    for raw_row in reader:
        stats.total_rows += 1
        try:
            _process_row(raw_row, stats, existing_ids, existing_on_disk,
                         seen_this_upload, known_tickers, new_rows)
        except Exception:
            stats.parse_errors += 1

    # Post-loop: update session set so NEXT upload in same session skips these rows
    existing_ids.update(seen_this_upload)

    return new_rows, stats


def _process_row(raw_row, stats, existing_ids, existing_on_disk,
                 seen_this_upload, known_tickers, new_rows):
    date_raw   = raw_row.get("Activity Date", "").strip()
    code_raw   = raw_row.get("Trans Code", "").strip()
    ticker_raw = raw_row.get("Instrument", "").strip().upper()
    # Robinhood uses BRK.B — normalise to BRK-B for yfinance/Finnhub
    if ticker_raw == "BRK.B":
        ticker_raw = "BRK-B"
    desc_raw   = raw_row.get("Description", "").strip()
    qty_raw    = raw_row.get("Quantity", "0").strip() or "0"
    price_raw  = raw_row.get("Price", "0").strip() or "0"
    amount_raw = raw_row.get("Amount", "0").strip() or "0"

    code = _TRANS_CODE_MAP.get(code_raw, code_raw.upper())
    if code in _SKIP_CODES or not code:
        stats.no_code += 1
        return

    date_iso = _date_to_iso(date_raw) if date_raw else ""

    fp = make_tx_fingerprint(
        date=date_iso, trans_code=code, ticker=ticker_raw,
        qty=qty_raw, price=price_raw, amount=amount_raw,
    )

    # Layer 2 — disk (snapshot never changes mid-loop)
    if fp in existing_on_disk:
        stats.already_on_disk += 1
        return

    # Layer 1 — session (previous uploads, pre-seeded at cold-start)
    if fp in existing_ids:
        stats.already_on_disk += 1
        return

    # Layer 3 — intra-file
    if fp in seen_this_upload:
        stats.seen_in_file += 1
        return

    seen_this_upload.add(fp)
    # NOTE: existing_ids updated POST-LOOP in ingest_csv()

    is_drip = "reinvest" in desc_raw.lower()

    qty_dec = Decimal("0")
    try:
        qty_dec = Decimal(qty_raw.replace(",", ""))
    except InvalidOperation:
        pass

    price_dec = Decimal("0")
    try:
        price_dec = Decimal(price_raw.replace("$", "").replace(",", ""))
    except InvalidOperation:
        pass

    amt_dec = Decimal("0")
    try:
        amt_dec = abs(Decimal(
            amount_raw.replace("$", "").replace(",", "")
                       .replace("(", "-").replace(")", "")
        ))
    except InvalidOperation:
        pass

    tx = {
        "fingerprint":  fp,
        "date":         date_iso,
        "trans_code":   code,
        "ticker":       ticker_raw,
        "description":  desc_raw,
        "qty":          str(qty_dec),
        "price":        str(price_dec),
        "amount":       str(amt_dec),
        "is_drip":      is_drip,
        "raw_code":     code_raw,
    }
    new_rows[fp] = tx

    if ticker_raw and ticker_raw not in known_tickers:
        stats.new_tickers.append(ticker_raw)
        known_tickers.add(ticker_raw)

    stats.imported += 1


def commit_new_transactions(new_rows: dict) -> None:
    """
    Merge new_rows into tx_store.json (atomic write).
    Call AFTER ingest_csv() returns.  No-op if new_rows is empty.
    """
    if not new_rows:
        return
    store = _load(TX_STORE_PATH, {})
    store.update(new_rows)
    _save(TX_STORE_PATH, store)
    _append_recon_log(new_rows)


def _append_recon_log(new_rows: dict) -> None:
    log = _load(RECON_LOG_PATH, [])
    log.append({
        "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        "new_rows":  len(new_rows),
        "tickers":   sorted({v.get("ticker", "") for v in new_rows.values() if v.get("ticker")}),
    })
    _save(RECON_LOG_PATH, log[-MAX_RECON_ROWS:])


# ═══════════════════════════════════════════════════════════════════════════════
# CRYPTO PDF PARSER
# ═══════════════════════════════════════════════════════════════════════════════

def parse_crypto_pdf(file_obj) -> Optional[dict]:
    try:
        import pdfplumber
    except ImportError:
        return None
    try:
        with pdfplumber.open(file_obj) as pdf:
            text = "\n".join(p.extract_text() or "" for p in pdf.pages)
        result = {}
        pat = re.compile(
            r"([A-Za-z][A-Za-z ]{1,20}?)\s+([\d]+\.[\d]+)\s+([A-Z]{2,6})\s+\$([\d,]+\.[\d]{2})"
        )
        for m in pat.finditer(text):
            ticker = m.group(3).strip()
            qty    = float(m.group(2))
            if ticker in CRYPTO_TICKERS and qty > 0:
                result[ticker] = {"shares": qty, "market_value": float(m.group(4).replace(",", ""))}
        return result if result else None
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# PORTFOLIO RECOMPUTE
# ═══════════════════════════════════════════════════════════════════════════════

def recompute_portfolio(crypto_overrides: Optional[dict] = None) -> dict:
    """
    Replay all rows in tx_store oldest→newest. Return current holdings dict.
    """
    store = _load(TX_STORE_PATH, {})
    if not store:
        bootstrap_tx_store()
        store = _load(TX_STORE_PATH, {})

    rows = sorted(store.values(), key=lambda r: r.get("date", ""))
    portfolio: dict[str, dict] = {}

    for row in rows:
        ticker = (row.get("ticker") or row.get("Instrument") or "").strip()
        if ticker == "BRK.B":
            ticker = "BRK-B"
        code = (row.get("trans_code") or row.get("Trans Code") or "").strip().upper()
        is_drip = row.get("is_drip", False)

        try:
            qty = Decimal(str(row.get("qty") or row.get("Quantity") or "0").replace(",", ""))
        except InvalidOperation:
            qty = Decimal("0")

        try:
            price = Decimal(str(row.get("price") or row.get("Price") or "0")
                            .replace("$", "").replace(",", ""))
        except InvalidOperation:
            price = Decimal("0")

        try:
            amount = abs(Decimal(
                str(row.get("amount") or row.get("Amount") or "0")
                .replace("$", "").replace(",", "").replace("(", "-").replace(")", "")
            ))
        except InvalidOperation:
            amount = Decimal("0")

        fbd = _date_to_iso(row.get("date") or row.get("Activity Date") or "")
        category = row.get("category", "Stocks")
        if ticker in CRYPTO_TICKERS:
            category = "Crypto"
        elif ticker in ETF_TICKERS:
            category = "ETF"

        if code == "BUY" and ticker:
            if ticker not in portfolio:
                portfolio[ticker] = {
                    "shares": Decimal("0"), "cost_basis": Decimal("0"),
                    "first_buy_date": fbd, "category": category,
                    "drip_count": 0, "drip_total": Decimal("0"),
                }
            cost = amount if amount > 0 else qty * price
            portfolio[ticker]["shares"]     += qty
            portfolio[ticker]["cost_basis"] += cost
            if is_drip:
                portfolio[ticker]["drip_count"] += 1
                portfolio[ticker]["drip_total"] += cost
            if not portfolio[ticker]["first_buy_date"]:
                portfolio[ticker]["first_buy_date"] = fbd

        elif code == "SELL" and ticker and ticker in portfolio:
            held = portfolio[ticker]["shares"]
            if held > 0 and qty > 0:
                frac = min(qty / held, Decimal("1"))
                portfolio[ticker]["cost_basis"] *= (1 - frac)
            portfolio[ticker]["shares"] = max(Decimal("0"), held - qty)

        elif code in ("SPL", "REC", "SXCH") and ticker:
            if ticker in portfolio:
                portfolio[ticker]["shares"] += qty

        elif code == "LIQ" and ticker in portfolio:
            portfolio[ticker]["shares"] = Decimal("0")

    # Apply crypto overrides (from PDF import)
    crypto_ovr = crypto_overrides or _load(CRYPTO_OVR_PATH, {})
    for ticker, info in crypto_ovr.items():
        portfolio[ticker] = {
            "shares":         Decimal(str(info.get("shares", 0))),
            "cost_basis":     Decimal(str(info.get("avg_cost", 0))) * Decimal(str(info.get("shares", 0))),
            "first_buy_date": info.get("first_buy_date", ""),
            "category":       "Crypto",
            "drip_count":     0,
            "drip_total":     Decimal("0"),
        }

    # Finalise: convert to float-based dict; drop zero positions
    result = {}
    for ticker, pos in portfolio.items():
        shares = float(pos["shares"])
        if shares < 0.0001:
            continue
        cost_basis = float(pos["cost_basis"])
        avg_cost   = cost_basis / shares if shares > 0 else 0.0
        result[ticker] = {
            "shares":         shares,
            "avg_cost":       round(avg_cost, 4),
            "cost_basis":     round(cost_basis, 2),
            "first_buy_date": pos["first_buy_date"],
            "category":       pos["category"],
            "drip_count":     pos["drip_count"],
            "drip_total":     round(float(pos["drip_total"]), 2),
        }
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# DATE HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def is_lt_eligible(first_buy_date: str) -> bool:
    d = _parse_date_robust(first_buy_date)
    if d is None:
        return False
    return (datetime.date.today() - d).days >= 366


def days_to_lt(first_buy_date: str) -> int:
    d = _parse_date_robust(first_buy_date)
    if d is None:
        return 9999
    return max(0, 366 - (datetime.date.today() - d).days)


# ═══════════════════════════════════════════════════════════════════════════════
# SAFE PRICE HELPER
# ═══════════════════════════════════════════════════════════════════════════════

def _safe_price(ticker: str, pos: dict, prices: dict) -> float:
    p = prices.get(ticker)
    if p and float(p) > 0:
        return float(p)
    disk = _load(PRICE_CACHE_PATH, {})
    if ticker in disk and disk[ticker] > 0:
        return float(disk[ticker])
    return float(pos.get("avg_cost", 1.0) or 1.0)


# ═══════════════════════════════════════════════════════════════════════════════
# PRICE FETCHER  (v11: Finnhub → Polygon → CoinGecko → cache → yfinance fallback)
# ═══════════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=120, show_spinner=False)
def fetch_prices(tickers: tuple, _bust: int = 0) -> dict:
    """
    Fetch live prices for all tickers. Returns {ticker: float}.
    Routes to PriceService (v11) if available, else falls back to yfinance.
    _bust parameter is used to force cache invalidation on refresh.
    """
    _load_env_from_secrets()
    prices: dict[str, Optional[float]] = {}
    ticker_list = list(tickers)

    if _V11_AVAILABLE:
        try:
            holdings_cache = None
            if HOLDINGS_CACHE_PATH.exists():
                try:
                    from holdings_manager import HoldingsCache
                    holdings_cache = HoldingsCache.load(str(HOLDINGS_CACHE_PATH))
                except Exception:
                    pass
            svc = PriceService(
                finnhub_key=os.environ.get("FINNHUB_API_KEY", ""),
                polygon_key=os.environ.get("POLYGON_API_KEY", ""),
                holdings_cache=holdings_cache,
            )
            results = svc.fetch_prices(ticker_list)
            for t, r in results.items():
                prices[t] = r.price if r and r.price and r.price > 0 else None
        except Exception as e:
            logger.warning("PriceService failed: %s — falling back to yfinance", e)

    # yfinance fallback for any missing prices
    missing = [t for t in ticker_list if not prices.get(t)]
    if missing:
        _fetch_prices_yfinance(missing, prices)

    # CoinGecko for crypto fallback
    crypto_missing = [t for t in missing if t in CRYPTO_TICKERS and not prices.get(t)]
    if crypto_missing:
        _fetch_prices_coingecko(crypto_missing, prices)

    # Persist to disk cache; serve stale for anything still missing
    disk = _load(PRICE_CACHE_PATH, {})
    for t in ticker_list:
        if prices.get(t):
            disk[t] = prices[t]
        elif t in disk:
            prices[t] = disk[t]
    _save(PRICE_CACHE_PATH, disk)

    return prices


def _fetch_prices_yfinance(tickers: list, prices: dict) -> None:
    try:
        import yfinance as yf
        import requests as req
    except ImportError:
        return
    for t in tickers:
        if prices.get(t):
            continue
        try:
            p = yf.Ticker(t).fast_info.get("last_price")
            if p and float(p) > 0:
                prices[t] = round(float(p), 4)
        except Exception:
            prices[t] = None


def _fetch_prices_coingecko(tickers: list, prices: dict) -> None:
    import requests
    cg_map = {
        "BTC": "bitcoin", "XRP": "ripple", "ETH": "ethereum",
        "SOL": "solana",  "DOGE": "dogecoin", "ADA": "cardano",
    }
    for t in tickers:
        cid = cg_map.get(t)
        if not cid:
            continue
        try:
            r = requests.get(
                f"https://api.coingecko.com/api/v3/simple/price?ids={cid}&vs_currencies=usd",
                timeout=8,
            )
            prices[t] = round(float(r.json()[cid]["usd"]), 4)
        except Exception:
            prices[t] = None


# ═══════════════════════════════════════════════════════════════════════════════
# SMART SYNC — Plaid + HoldingsManager
# ═══════════════════════════════════════════════════════════════════════════════

def smart_sync_portfolio(force_plaid: bool = False) -> Optional[dict]:
    if not _V11_AVAILABLE:
        return None
    _load_env_from_secrets()
    if not os.environ.get("PLAID_ACCESS_TOKEN"):
        return None
    try:
        mgr      = HoldingsManager(cache_path=str(HOLDINGS_CACHE_PATH))
        agg      = PortfolioAggregator(holdings_manager=mgr)
        snapshot = agg.calculate_total_value(force_plaid_refresh=force_plaid)
        try:
            from main_sync import export_json
            export_json(snapshot, str(PLAID_SNAPSHOT_PATH))
        except Exception:
            _save(PLAID_SNAPSHOT_PATH, snapshot.__dict__ if hasattr(snapshot, "__dict__") else snapshot)
        return _load(PLAID_SNAPSHOT_PATH, None)
    except Exception as e:
        logger.warning("Smart sync failed: %s", e)
        return None


def get_holdings_cache_status() -> dict:
    if not _V11_AVAILABLE:
        return {"status": "unavailable", "label": "v11 modules not installed",
                "holdings_count": 0, "age_hours": None, "is_stale": True,
                "last_synced": None, "cash_usd": 0.0, "next_sync_in": None}
    _load_env_from_secrets()
    try:
        return HoldingsManager(cache_path=str(HOLDINGS_CACHE_PATH)).get_cache_status()
    except Exception as e:
        return {"status": "error", "label": str(e), "holdings_count": 0,
                "age_hours": None, "is_stale": True, "last_synced": None,
                "cash_usd": 0.0, "next_sync_in": None}


# ═══════════════════════════════════════════════════════════════════════════════
# RECOMMENDATION ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

def generate_recs(portfolio: dict, prices: dict) -> list[dict]:
    """Generate dynamic buy/sell/trim/hold recommendations sorted by priority."""
    recs = []
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

        d = _parse_date_robust(fbd)
        lt_date = (d + datetime.timedelta(days=366)).isoformat() if d else "?"

        tax = "✅ LT (15%)" if lt else (f"⏳ ST → {lt_date}" if d else "⏳ ST")
        proceeds = round(price * shares, 2)

        # Priority 0 — explicit sell list
        if ticker in SELL_LIST:
            if lt:
                action, cat, priority = "SELL NOW — LT ✅", "sell", 0
                plain = f"Sell all {shares:.4f} sh (est. ${proceeds:,.0f}). Reinvest into VOO/VYM same day."
                why   = "LT eligible. Pay 15% not 37%. ETF swap = no wash sale."
            else:
                action, cat, priority = f"HOLD — LT in {dtlt}d", "hold", 3
                plain = f"Wait until {lt_date} to sell and pay only 15% tax."
                why   = "Currently short-term. Patience saves ~22%."

        elif ticker in SELL_PENDING:
            if lt:
                action, cat, priority = "SELL NOW (ETF swap)", "sell", 0
                plain = f"Now LT eligible. Sell → buy VOO/QQQ same day."
                why   = "Calendar-flagged swap. No wash sale on ETF→ETF."
            else:
                action, cat, priority = f"HOLD — LT in {dtlt}d", "hold", 3
                plain = f"Wait until {lt_date}."
                why   = "Pending swap. Not yet LT."

        # Priority 1 — big loss review
        elif pnl_pct < -20:
            action, cat, priority = "REVIEW — BIG LOSS ⚠️", "review", 1
            plain = f"Down {pnl_pct:.1f}%. Decide: cut loss or hold for recovery?"
            why   = "Position is down >20%. Review thesis."

        # Priority 2 — strong signals
        elif ticker in FOREVER_HOLD:
            action, cat, priority = "HOLD FOREVER — DRIP on", "hold", 2
            plain = "Never sell. Every dividend buys more shares automatically."
            why   = "Core income ETF. Compounding machine."

        elif ticker in DCA_ALWAYS:
            action, cat, priority = "DCA EVERY DEPOSIT", "buy", 2
            plain = f"Add to this every biweekly deposit. Track the whole market."
            why   = "Core index. Never stop accumulating."

        elif pnl_pct < -8 and upside > 20:
            action, cat, priority = "STRONG BUY — ON DIP", "buy", 2
            plain = f"Down {pnl_pct:.1f}% but analyst target ${target:.0f} (+{upside:.0f}%). Add more."
            why   = "Dip within uptrend. Best time to accumulate."

        elif ticker in CRYPTO_TICKERS and upside > 25:
            action, cat, priority = "ACCUMULATE — CRYPTO", "buy", 2
            plain = f"Analyst target ${target:,.0f} (+{upside:.0f}% upside). Add small position."
            why   = "High-conviction crypto. Size appropriately (≤5% total)."

        elif upside > 20:
            action, cat, priority = "ACCUMULATE", "buy", 2
            plain = f"Target ${target:.0f} implies +{upside:.0f}% upside. Add on dips."
            why   = "Strong upside to analyst consensus."

        # Priority 3 — near LT or trim
        elif dtlt <= 30 and not lt:
            action, cat, priority = f"HOLD — LT IN {dtlt}d", "hold", 3
            plain = f"Only {dtlt} days until long-term treatment. Don't sell now."
            why   = "Just {dtlt}d away from 15% tax rate. Be patient."

        elif ticker in IPO_HOLDS:
            if lt:
                action, cat, priority = "TRIM 25% — IPO LT", "trim", 3
                plain = f"Sell {shares*0.25:.2f} sh (est. ${proceeds*0.25:,.0f}). Keep 75%."
                why   = "IPO now LT. Lock in partial gains at 15% rate."
            else:
                action, cat, priority = f"HOLD — LT in {dtlt}d", "hold", 3
                plain = f"Hold until {lt_date} before trimming IPO position."
                why   = "IPO/speculative. Wait for LT treatment."

        elif pnl_pct > 20 and lt:
            action, cat, priority = "TRIM 20% — TAKE GAINS", "trim", 3
            plain = f"Up {pnl_pct:.1f}%. Sell {shares*0.2:.2f} sh (est. ${proceeds*0.2:,.0f}) at LT rate."
            why   = "Rule of 20%: lock in gains when up >20% and LT eligible."

        # Priority 4 — hold
        else:
            action, cat, priority = "HOLD", "hold", 4
            plain = "No action needed. Monitor."
            why   = "No strong signal in either direction."

        recs.append({
            "ticker":    ticker,
            "action":    action,
            "category":  cat,
            "priority":  priority,
            "shares":    shares,
            "price":     price,
            "cost":      cost,
            "equity":    equity,
            "pnl_pct":   round(pnl_pct, 2),
            "proceeds":  proceeds,
            "tax":       tax,
            "plain":     plain,
            "why":       why,
            "lt":        lt,
            "dtlt":      dtlt,
            "upside":    round(upside, 1),
            "asset_cat": pos.get("category", "Stocks"),
        })

    recs.sort(key=lambda r: (r["priority"], -r["equity"]))
    return recs


# ═══════════════════════════════════════════════════════════════════════════════
# PORTFOLIO TOTALS
# ═══════════════════════════════════════════════════════════════════════════════

def portfolio_totals(portfolio: dict, prices: dict, cash: float = 0.0) -> dict:
    stocks_eq = crypto_eq = cost_total = 0.0
    for ticker, pos in portfolio.items():
        p      = _safe_price(ticker, pos, prices)
        equity = p * pos["shares"]
        if pos.get("category") == "Crypto" or ticker in CRYPTO_TICKERS:
            crypto_eq += equity
        else:
            stocks_eq += equity
        cost_total += pos["avg_cost"] * pos["shares"]
    total = stocks_eq + crypto_eq + cash
    pnl   = total - cash - cost_total
    return {
        "total":    round(total, 2),
        "stocks":   round(stocks_eq, 2),
        "crypto":   round(crypto_eq, 2),
        "cash":     round(cash, 2),
        "cost":     round(cost_total, 2),
        "pnl":      round(pnl, 2),
        "pnl_pct":  round(pnl / cost_total * 100 if cost_total else 0, 2),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# AI TARGETS
# ═══════════════════════════════════════════════════════════════════════════════

def load_targets() -> dict:
    return _load(TARGETS_PATH, {})


def save_targets(targets: dict) -> None:
    _save(TARGETS_PATH, targets)


# ═══════════════════════════════════════════════════════════════════════════════
# ② REBALANCING ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

def compute_rebalancing(
    portfolio: dict,
    prices: dict,
    targets: dict,
    cash_available: float = 0.0,
) -> list[dict]:
    """
    Returns drift table sorted by drift ascending (most underweight first).
    When cash_available > 0, adds cash_to_deploy field to BUY rows.
    """
    total_eq = sum(_safe_price(t, p, prices) * p["shares"] for t, p in portfolio.items())
    total_with_cash = total_eq + cash_available

    rows = []
    for ticker, pos in portfolio.items():
        if ticker not in targets:
            continue
        price   = _safe_price(ticker, pos, prices)
        equity  = price * pos["shares"]
        target  = targets[ticker]
        current = equity / total_with_cash * 100 if total_with_cash else 0
        drift   = round(current - target, 2)
        rows.append({
            "ticker":  ticker,
            "equity":  round(equity, 2),
            "current": round(current, 2),
            "target":  target,
            "drift":   drift,
            "action":  "BUY" if drift < -2 else ("TRIM" if drift > 5 else "OK"),
            "cash_to_deploy": 0.0,
        })

    rows.sort(key=lambda r: r["drift"])

    # Distribute cash proportionally to underweight positions
    if cash_available > 0:
        under = [r for r in rows if r["action"] == "BUY"]
        total_def = sum(abs(r["drift"]) for r in under)
        if total_def > 0:
            for r in under:
                r["cash_to_deploy"] = round(cash_available * abs(r["drift"]) / total_def, 2)

    return rows


# ═══════════════════════════════════════════════════════════════════════════════
# DEPOSIT PLAN GENERATOR
# ═══════════════════════════════════════════════════════════════════════════════

def generate_deposit_recs(
    portfolio: dict,
    prices: dict,
    deposit_num: int = 1,
    amount: float = DEPOSIT_AMOUNT,
    targets: Optional[dict] = None,
    cash_balance: float = 0.0,
) -> list[dict]:
    """
    Generate biweekly deposit allocation recs.
    total_investable = amount (new deposit) + cash_balance (idle Robinhood cash).
    """
    total_investable = amount + cash_balance
    rotating_pick    = DEPOSIT_ROTATION[(deposit_num - 1) % len(DEPOSIT_ROTATION)]
    total_eq         = sum(_safe_price(t, p, prices) * p["shares"] for t, p in portfolio.items())

    if targets and total_eq > 0:
        rebal = compute_rebalancing(portfolio, prices, targets, cash_available=cash_balance)
        under = [r for r in rebal if r["drift"] < -2][:5]
        if under:
            total_def = sum(abs(r["drift"]) for r in under)
            recs = []
            for r in under:
                alloc = total_investable * abs(r["drift"]) / total_def
                p     = _safe_price(r["ticker"], portfolio.get(r["ticker"], {}), prices)
                recs.append({
                    "ticker":           r["ticker"],
                    "alloc_pct":        round(abs(r["drift"]) / total_def * 100, 1),
                    "amount":           round(alloc, 2),
                    "price":            p,
                    "est_shares":       round(alloc / p, 4) if p > 0 else 0,
                    "why":              f"{r['drift']:.1f}% under target",
                    "investable_total": round(total_investable, 2),
                    "from_cash":        round(r.get("cash_to_deploy", 0), 2),
                    "overridden":       False,
                    "override_delta":   0.0,
                })
            return recs

    # Fixed plan scaled to total_investable
    recs = []
    for ticker, pct in DEPOSIT_PLAN:
        t     = rotating_pick if ticker == "ROTATING" else ticker
        alloc = total_investable * pct
        p     = _safe_price(t, portfolio.get(t, {}), prices)
        recs.append({
            "ticker":           t,
            "alloc_pct":        round(pct * 100, 1),
            "amount":           round(alloc, 2),
            "price":            p,
            "est_shares":       round(alloc / p, 4) if p > 0 else 0,
            "why":              "Rotating pick" if ticker == "ROTATING" else "Core allocation",
            "investable_total": round(total_investable, 2),
            "from_cash":        round(cash_balance * pct, 2),
            "overridden":       False,
            "override_delta":   0.0,
        })
    return recs


def next_deposit_date(from_date: Optional[datetime.date] = None) -> datetime.date:
    d = DEPOSIT_START
    today = from_date or datetime.date.today()
    while d < today:
        d += datetime.timedelta(days=14)
    return d


def deposit_schedule(n: int = 19) -> list[dict]:
    dates = []
    d = DEPOSIT_START
    for i in range(n):
        rotation = DEPOSIT_ROTATION[i % len(DEPOSIT_ROTATION)]
        dates.append({
            "Deposit #": i + 1,
            "Date":      d.isoformat(),
            "Rotating":  rotation,
            "Core":      "NVDA 28% · VOO 22% · VYM 17% · QQQ 17%",
        })
        d += datetime.timedelta(days=14)
    return dates


# ═══════════════════════════════════════════════════════════════════════════════
# ③ DECISION LOG / MANUAL OVERRIDE SYSTEM
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class DecisionLogEntry:
    date:           str
    ticker:         str
    ai_rec_amount:  float
    manual_amount:  float
    delta:          float
    reason:         str
    timestamp:      str
    deposit_num:    int
    action_type:    str = "override"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "DecisionLogEntry":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


def log_decision(entry: DecisionLogEntry) -> None:
    log = _load(DECISION_LOG_PATH, [])
    log.append(entry.to_dict())
    _save(DECISION_LOG_PATH, log[-MAX_DECISION_LOG:])


def load_decision_log() -> list[dict]:
    return _load(DECISION_LOG_PATH, [])


def apply_overrides_to_recs(
    recs:        list[dict],
    overrides:   dict,
    reasons:     dict,
    deposit_num: int,
) -> list[dict]:
    updated = []
    for r in recs:
        rc = dict(r)
        t  = rc["ticker"]
        if t in overrides and overrides[t] != rc["amount"]:
            manual = float(overrides[t])
            ai     = float(rc["amount"])
            delta  = manual - ai
            rc["amount"]         = round(manual, 2)
            rc["est_shares"]     = round(manual / rc["price"], 4) if rc.get("price", 0) > 0 else 0
            rc["overridden"]     = True
            rc["override_delta"] = round(delta, 2)
            log_decision(DecisionLogEntry(
                date=datetime.date.today().isoformat(),
                ticker=t,
                ai_rec_amount=ai,
                manual_amount=manual,
                delta=delta,
                reason=reasons.get(t, ""),
                timestamp=datetime.datetime.now().isoformat(),
                deposit_num=deposit_num,
            ))
        updated.append(rc)
    return updated


# ═══════════════════════════════════════════════════════════════════════════════
# SNAPSHOT / HISTORY
# ═══════════════════════════════════════════════════════════════════════════════

def snapshot_portfolio(portfolio: dict, prices: dict, cash: float, recs: list) -> dict:
    totals = portfolio_totals(portfolio, prices, cash)
    snap   = {
        "timestamp": datetime.datetime.now().isoformat(),
        "totals":    totals,
        "recs":      [{"ticker": r["ticker"], "action": r["action"], "pnl_pct": r["pnl_pct"]}
                      for r in recs],
    }
    history = _load(REC_HISTORY_PATH, [])
    history.append(snap)
    _save(REC_HISTORY_PATH, history[-MAX_REC_HISTORY:])
    return snap


def load_rec_history() -> list:
    return _load(REC_HISTORY_PATH, [])


def log_deposit(deposit_num: int, date_str: str, recs: list, total: float) -> None:
    log = _load(DEPOSIT_LOG_PATH, [])
    log.append({
        "num":  deposit_num,
        "date": date_str,
        "total": total,
        "buys": [{"ticker": r["ticker"], "amount": r["amount"], "shares": r["est_shares"]}
                 for r in recs],
    })
    _save(DEPOSIT_LOG_PATH, log)


def load_deposit_log() -> list:
    return _load(DEPOSIT_LOG_PATH, [])
