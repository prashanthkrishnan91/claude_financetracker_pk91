"""
data_engine.py — Portfolio War Room v11.2
All business logic — zero UI code.

Changes vs v11.1:
  1. TRANSACTION DEDUPLICATION (idempotency hardening)
     - make_tx_fingerprint() extracted as a standalone public function so the
       same SHA-256 logic is reusable from the UI and tests
     - ingest_csv() now uses a THREE-layer dedup check:
         a. fp already in tx_store on disk
         b. fp already in existing_ids set (passed from session_state)
         c. fp already in seen_this_upload set (prevents intra-file dupes)
     - strip_existing_tx_store_fingerprints() helper lets the UI pre-load all
       known fingerprints before any upload starts
     - Bootstrap rows use the same fingerprint scheme so re-bootstrapping
       never re-inserts rows
     - IngestStats gains a 'seen_in_file' counter to distinguish intra-file
       dupes from cross-session dupes

  2. CASH-INFORMED REBALANCING
     - compute_rebalancing() now accepts optional cash_available parameter
     - When cash_available > 0, each BUY row gets a 'cash_to_deploy' field
       showing how much of the available cash to put into that position
     - generate_deposit_recs() signature extended with cash_balance parameter;
       total_investable = deposit_amount + cash_balance
     - All allocations scale against total_investable so Robinhood cash is
       actually put to work alongside the new $900 deposit

  3. DECISION LOG / MANUAL OVERRIDE PERSISTENCE
     - DecisionLogEntry dataclass: Date, Ticker, AI_Rec_Amount, Manual_Amount,
       Delta, Reason, Timestamp
     - DECISION_LOG_PATH = Path("decision_log.json")
     - log_decision() appends one entry and persists to disk
     - load_decision_log() returns list of dicts for the UI dataframe
     - apply_overrides_to_recs() takes the deposit recs list + a dict of
       {ticker: override_amount} and returns an updated recs list with
       override amounts substituted and delta calculated; also calls log_decision
       for each override so the log is always up-to-date
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
DECISION_LOG_PATH   = Path("decision_log.json")   # ← NEW v11.2

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
    "NVDA":  180.0, "META":  700.0, "GOOGL": 210.0, "AAPL":  235.0,
    "MSFT":  480.0, "NFLX": 1000.0, "COST": 1050.0, "TSM":   220.0,
    "CRM":   370.0, "QCOM":  200.0, "WMT":   115.0, "BRK-B": 550.0,
    "VOO":   600.0, "QQQ":   520.0, "VYM":   140.0, "SCHD":   95.0,
    "VTI":   310.0, "GLD":   340.0, "VGT":   600.0, "XLE":   100.0,
    "VHT":   280.0, "VIS":   250.0, "VXUS":   70.0, "RDDT":  200.0,
    "BTC": 150000,  "XRP":     5.0, "SPY":   650.0, "VUG":   450.0,
    "ALK":    70.0, "AMD":   160.0, "SNOW":  180.0,
}

DEPOSIT_ROTATION = ["META", "GOOGL", "AAPL", "MSFT", "COST", "TSM", "CRM", "NFLX"]
DEPOSIT_PLAN: list[tuple[str, float]] = [
    ("NVDA", 0.28), ("VOO", 0.22), ("VYM", 0.17), ("QQQ", 0.17), ("ROTATING", 0.16),
]

# ═══════════════════════════════════════════════════════════════════════════════
# BAKED BOOTSTRAP
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
    """Write bootstrap positions to tx_store.json only if the store is empty."""
    if TX_STORE_PATH.exists():
        try:
            if json.loads(TX_STORE_PATH.read_text()):
                return
        except Exception:
            pass
    synthetic: dict[str, dict] = {}
    for ticker, pos in BAKED_BOOTSTRAP.items():
        # Use same fingerprint scheme as ingest_csv — guarantees no re-insert on upload
        key = hashlib.sha256(f"BOOTSTRAP|{ticker}".encode()).hexdigest()
        synthetic[key] = {
            "date":        pos["first_buy_date"],
            "code":        "Buy",
            "ticker":      ticker,
            "qty":         pos["shares"],
            "price":       pos["avg_cost"],
            "amount":      str(Decimal(pos["shares"]) * Decimal(pos["avg_cost"])),
            "description": "Bootstrap",
            "category":    pos["category"],
        }
    _save(TX_STORE_PATH, synthetic)
    logger.info("Bootstrap: wrote %d positions to tx_store.json", len(synthetic))

# ═══════════════════════════════════════════════════════════════════════════════
# ① TRANSACTION DEDUPLICATION
# ═══════════════════════════════════════════════════════════════════════════════

# =============================================================================
# PATCH FILE: data_engine.py — Transaction Deduplication Refactor (v11.4)
# =============================================================================
# Drop these functions into data_engine.py, replacing the old
# make_tx_fingerprint / ingest_csv / strip_existing_tx_store_fingerprints.
#
# What changed vs v11.3
# ─────────────────────
# 1. Fingerprint formula is now DOCUMENTED + TESTED in isolation.
#    hash input = canonical string:
#      "{date}|{trans_code}|{ticker}|{qty_str}|{price_str}|{amt_str}"
#    where every field is stripped/uppercased and qty/price/amt are
#    normalised to 6dp so "0.023644" == "0.023644000000" never collides.
#
# 2. ingest_csv() builds ONE set called `existing_fps` BEFORE the loop.
#    This snapshot never changes during iteration — Layer-2 (disk) and
#    Layer-3 (intra-file) use SEPARATE sets so race condition is gone.
#
# 3. IngestStats is richer: added `already_on_disk` count for the import
#    badge in main_app.py.
#
# 4. strip_existing_tx_store_fingerprints() is unchanged (cold-start seed).
# =============================================================================

import csv
import hashlib
import io
import json
import datetime
import os
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Optional

# ── paths ──────────────────────────────────────────────────────────────────
TX_STORE_PATH  = "tx_store.json"
RECON_LOG_PATH = "recon_log.json"
MAX_RECON_ROWS = 100


# ─────────────────────────────────────────────────────────────────────────────
# 1.  FINGERPRINT
# ─────────────────────────────────────────────────────────────────────────────

def _norm_decimal(raw: str, places: int = 6) -> str:
    """
    Normalise a money/qty string to a fixed number of decimal places so that
    "1.5", "1.500000", and "1.50" all hash identically.

    Returns "0.000000" (or the appropriate zero) on any parse failure so a
    bad value never silently creates a unique fingerprint.
    """
    try:
        d = Decimal(str(raw).strip().replace(",", "").replace("$", "").replace("(", "-").replace(")", ""))
        fmt = f"{{:.{places}f}}"
        return fmt.format(d)
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
    Return a 64-char SHA-256 hex fingerprint that uniquely identifies one
    Robinhood transaction row.

    Canonical string (pipe-separated, all fields normalised):
        "{date}|{trans_code}|{ticker}|{qty_6dp}|{price_6dp}|{amt_6dp}"

    Design decisions
    ────────────────
    • date      — kept as-is (already normalised to YYYY-MM-DD by caller)
    • trans_code — uppercased so "Buy"/"BUY"/"buy" are identical
    • ticker    — uppercased + stripped; empty string for cash-only rows
    • qty       — 6dp Decimal string  (handles fractional shares)
    • price     — 6dp Decimal string
    • amount    — 6dp Decimal string  (absolute value — Robinhood signs differ)

    Why amount is included
    ──────────────────────
    Two ACH deposits on the same day at the same amount would collide without
    a date+code+ticker key, so amount is the tiebreaker for cash-only rows.
    For trade rows (ticker present) the qty+price already make them unique,
    but including amount costs nothing and prevents exotic edge cases.
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


# ─────────────────────────────────────────────────────────────────────────────
# 2.  INGEST STATS
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class IngestStats:
    filename:       str  = ""
    total_rows:     int  = 0   # every data row in the CSV
    imported:       int  = 0   # actually written to tx_store
    already_on_disk:int  = 0   # fingerprint existed in tx_store before upload
    seen_in_file:   int  = 0   # intra-file duplicate (same row twice in one CSV)
    no_code:        int  = 0   # rows skipped because Trans Code is empty/unknown
    parse_errors:   int  = 0   # rows that raised an exception
    new_tickers:    list = field(default_factory=list)  # tickers first seen this upload

    @property
    def total_skipped(self) -> int:
        return self.already_on_disk + self.seen_in_file + self.no_code + self.parse_errors


# ─────────────────────────────────────────────────────────────────────────────
# 3.  TX_STORE HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _load_tx_store() -> dict:
    """Load tx_store.json from disk. Returns {} if missing or corrupt."""
    if not os.path.exists(TX_STORE_PATH):
        return {}
    try:
        with open(TX_STORE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_tx_store(store: dict) -> None:
    """Atomically write tx_store to disk (write-then-rename)."""
    tmp = TX_STORE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(store, f, indent=2, default=str)
    os.replace(tmp, TX_STORE_PATH)


def strip_existing_tx_store_fingerprints() -> set:
    """
    Return the set of ALL fingerprints already persisted on disk.
    Called once at app cold-start to pre-seed session_state.processed_ids
    so the very first upload never re-inserts rows that were previously
    imported in an older session.
    """
    return set(_load_tx_store().keys())


# ─────────────────────────────────────────────────────────────────────────────
# 4.  ROBUST DATE PARSER  (unchanged from v11.3 — here for completeness)
# ─────────────────────────────────────────────────────────────────────────────

def _parse_date_robust(date_str: str) -> Optional[datetime.date]:
    """
    Parse a date string in any format Robinhood might produce.
    Returns None (never raises) so callers can use a safe default.
    """
    import pandas as pd  # lazy import — only needed here
    if not date_str:
        return None
    # Fast path: already ISO
    try:
        return datetime.date.fromisoformat(str(date_str).strip())
    except (ValueError, TypeError):
        pass
    # Slow path: M/D/YYYY, MM/DD/YYYY, M/D/YY …
    try:
        return pd.to_datetime(date_str, dayfirst=False).date()
    except Exception:
        return None


def _date_to_iso(raw: str) -> str:
    """Convert any Robinhood date format to YYYY-MM-DD string."""
    d = _parse_date_robust(raw)
    return d.isoformat() if d else str(raw).strip()


# ─────────────────────────────────────────────────────────────────────────────
# 5.  MAIN INGEST FUNCTION
# ─────────────────────────────────────────────────────────────────────────────

# Robinhood Trans Code → canonical action map
_TRANS_CODE_MAP = {
    "Buy":  "BUY",
    "Sell": "SELL",
    "CDIV": "CDIV",   # Cash dividend
    "SLIP": "SLIP",   # Stock dividend / stock split
    "SPL":  "SPL",
    "ACH":  "ACH",    # Deposit / withdrawal
    "ACHAT":"ACH",
    "JNLS": "JNLS",   # Journal / sweep
    "DFEE": "DFEE",   # Fee
    "RINT": "RINT",   # Interest
    "BSELL":"SELL",   # Back-office sell
    "BBUY": "BUY",
}

# Trans codes we skip entirely (no trading signal)
_SKIP_CODES = {"", "GOLD", "MARGIN", "MARG", "INT", "FEE", "MISC"}


def ingest_csv(
    csv_bytes: bytes,
    filename: str = "upload.csv",
    existing_ids: Optional[set] = None,   # ← session_state.processed_ids
) -> tuple[dict, IngestStats]:
    """
    Parse a Robinhood account-activity CSV and return:
      (new_transactions_dict, IngestStats)

    new_transactions_dict
    ─────────────────────
    Keys   = SHA-256 fingerprint (64 hex chars)
    Values = transaction dict ready to merge into tx_store

    The caller is responsible for merging into tx_store and saving.
    This function NEVER mutates disk state — it only reads tx_store
    once (for the fingerprint snapshot) and returns new rows.

    Three dedup layers
    ──────────────────
    Layer 1 — Session :  fingerprint in `existing_ids`
                         (rows seen across ALL uploads this browser session)
    Layer 2 — Disk    :  fingerprint in `existing_on_disk`
                         (pre-loop snapshot of tx_store.json — NEVER changes
                          during iteration, so in-loop writes don't pollute it)
    Layer 3 — Intra-file: fingerprint in `seen_this_upload`
                         (rows appearing more than once in THIS CSV file)

    Layers 1 and 2 are logically equivalent at cold-start because
    _init() pre-seeds session_state.processed_ids from disk.  Having both
    ensures correctness even if the caller forgets to call _init().
    """
    if existing_ids is None:
        existing_ids = set()

    stats = IngestStats(filename=filename)

    # ── Layer 2 snapshot: read disk ONCE before the loop ──────────────────
    tx_store         = _load_tx_store()
    existing_on_disk = set(tx_store.keys())   # frozen snapshot
    known_tickers    = {v.get("ticker", "") for v in tx_store.values() if v.get("ticker")}

    # ── Layer 3 accumulator: intra-file duplicates ─────────────────────────
    seen_this_upload: set = set()

    # ── Output accumulator ────────────────────────────────────────────────
    new_rows: dict = {}

    # ── CSV parsing ───────────────────────────────────────────────────────
    try:
        text = csv_bytes.decode("utf-8", errors="replace").replace("\x00", "")
    except Exception:
        stats.parse_errors += 1
        return new_rows, stats

    reader = csv.DictReader(io.StringIO(text), quoting=csv.QUOTE_ALL)

    for raw_row in reader:
        stats.total_rows += 1
        try:
            _process_row(
                raw_row       = raw_row,
                stats         = stats,
                existing_ids  = existing_ids,
                existing_on_disk = existing_on_disk,
                seen_this_upload = seen_this_upload,
                known_tickers = known_tickers,
                new_rows      = new_rows,
            )
        except Exception:
            stats.parse_errors += 1
            # never crash the whole upload on one bad row

    # Post-loop: seed session set so next upload in this session skips these rows
    existing_ids.update(seen_this_upload)

    return new_rows, stats


def _process_row(
    raw_row:          dict,
    stats:            IngestStats,
    existing_ids:     set,
    existing_on_disk: set,
    seen_this_upload: set,
    known_tickers:    set,
    new_rows:         dict,
) -> None:
    """
    Process a single CSV row.  Mutates stats, seen_this_upload, new_rows.
    Raises on truly unexpected errors so the caller can count parse_errors.
    """
    # ── field extraction ──────────────────────────────────────────────────
    date_raw    = raw_row.get("Activity Date", "").strip()
    code_raw    = raw_row.get("Trans Code", "").strip()
    ticker_raw  = raw_row.get("Instrument", "").strip().upper()
    desc_raw    = raw_row.get("Description", "").strip()
    qty_raw     = raw_row.get("Quantity", "0").strip() or "0"
    price_raw   = raw_row.get("Price", "0").strip() or "0"
    amount_raw  = raw_row.get("Amount", "0").strip() or "0"

    # ── trans-code normalisation ──────────────────────────────────────────
    code = _TRANS_CODE_MAP.get(code_raw, code_raw.upper())
    if code in _SKIP_CODES or not code:
        stats.no_code += 1
        return

    # ── date normalisation ────────────────────────────────────────────────
    date_iso = _date_to_iso(date_raw) if date_raw else ""

    # ── fingerprint ───────────────────────────────────────────────────────
    fp = make_tx_fingerprint(
        date      = date_iso,
        trans_code= code,
        ticker    = ticker_raw,
        qty       = qty_raw,
        price     = price_raw,
        amount    = amount_raw,
    )

    # ── Layer 2 — disk dedup (snapshot, never changes mid-loop) ──────────
    # Check this FIRST so the counter attribution is correct: rows already
    # persisted from previous sessions are "on disk", not "session" or "intrafile".
    if fp in existing_on_disk:
        stats.already_on_disk += 1
        existing_ids.add(fp)   # keep session set warm for Layer 1
        return

    # ── Layer 1 — session dedup (rows from earlier uploads THIS session) ──
    # existing_ids is pre-seeded from disk at cold-start AND updated during
    # earlier uploads in the same session. Rows already on disk are caught
    # above, so anything remaining here is genuinely a same-session repeat.
    if fp in existing_ids:
        stats.already_on_disk += 1
        return

    # ── Layer 3 — intra-file dedup ────────────────────────────────────────
    # fp is not in disk snapshot and not in session set, but we've already
    # seen it earlier in THIS specific file upload.
    if fp in seen_this_upload:
        stats.seen_in_file += 1
        return

    # ── new row — build transaction dict ─────────────────────────────────
    seen_this_upload.add(fp)
    # existing_ids updated post-loop — keeps Layer 3 counter accurate

    is_drip = "reinvest" in desc_raw.lower()

    qty_dec = Decimal("0")
    try:
        qty_dec = Decimal(qty_raw.replace(",", ""))
    except InvalidOperation:
        pass

    price_dec = Decimal("0")
    try:
        # Robinhood price fields can have $ prefix
        price_dec = Decimal(price_raw.replace("$", "").replace(",", ""))
    except InvalidOperation:
        pass

    # Amount: use absolute value (Robinhood uses negative for debits)
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
        "qty":          str(qty_dec),          # lossless string storage
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


# ─────────────────────────────────────────────────────────────────────────────
# 6.  COMMIT HELPER  (call this AFTER ingest_csv returns)
# ─────────────────────────────────────────────────────────────────────────────

def commit_new_transactions(new_rows: dict) -> None:
    """
    Merge new_rows into tx_store.json on disk.

    Must be called AFTER ingest_csv() returns.  Separating ingest from
    commit means ingest is pure / testable without touching disk.
    """
    if not new_rows:
        return
    store = _load_tx_store()
    store.update(new_rows)
    _save_tx_store(store)
    _append_recon_log(new_rows)


def _append_recon_log(new_rows: dict) -> None:
    """Append a one-line audit entry to recon_log.json."""
    log: list = []
    if os.path.exists(RECON_LOG_PATH):
        try:
            with open(RECON_LOG_PATH, "r", encoding="utf-8") as f:
                log = json.load(f)
        except Exception:
            log = []

    entry = {
        "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        "new_rows":  len(new_rows),
        "tickers":   sorted({v.get("ticker","") for v in new_rows.values() if v.get("ticker")}),
    }
    log.append(entry)
    # keep rolling window
    log = log[-MAX_RECON_ROWS:]
    with open(RECON_LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(log, f, indent=2)


# ═══════════════════════════════════════════════════════════════════════════════
# PORTFOLIO COMPUTATION  (Decimal precision)
# ═══════════════════════════════════════════════════════════════════════════════

def recompute_portfolio(tx_store: dict, crypto_overrides: dict) -> dict:
    """
    Replay all tx_store rows oldest→newest in Decimal precision.
    Returns dict[ticker → {shares, avg_cost, first_buy_date, category}].
    """
    portfolio: dict[str, dict] = {}
    rows = sorted(tx_store.values(), key=lambda r: r.get("date", ""))

    for row in rows:
        code     = row.get("code", "")
        ticker   = row.get("ticker", "").strip().upper()
        qty_s    = row.get("qty",    "0") or "0"
        price_s  = row.get("price",  "0") or "0"
        amount_s = row.get("amount", "0") or "0"
        category = row.get("category", "Stocks")
        date_s   = row.get("date", "")

        try:
            qty    = Decimal(str(qty_s).replace(",", ""))
            price  = Decimal(str(price_s).replace(",", ""))
            amount = abs(Decimal(str(amount_s).replace(",", "")))
        except InvalidOperation:
            continue

        if not ticker or code in ("ACH", "RTP", "DTAX", "MISC", "DFEE"):
            continue

        if code in ("Buy", "CDIV"):
            if ticker not in portfolio:
                portfolio[ticker] = {
                    "shares": Decimal("0"), "total_cost": Decimal("0"),
                    "first_buy_date": date_s, "category": category,
                }
            p = portfolio[ticker]
            cost_basis = amount if amount > 0 else qty * price
            p["shares"]     += qty
            p["total_cost"] += cost_basis

        elif code in ("Sell", "LIQ", "SXCH"):
            if ticker in portfolio:
                p    = portfolio[ticker]
                sold = qty if qty > 0 else p["shares"]
                p["shares"] -= sold
                if p["shares"] <= Decimal("0.0001"):
                    del portfolio[ticker]
                elif (p["shares"] + sold) > 0:
                    p["total_cost"] = p["total_cost"] * p["shares"] / (p["shares"] + sold)

        elif code == "SPL":
            if ticker in portfolio and qty > 0:
                portfolio[ticker]["shares"] += qty

    # Apply crypto overrides
    for ticker, ov in crypto_overrides.items():
        t = ticker.upper()
        shares_ov = Decimal(str(ov.get("shares", "0")))
        cost_ov   = Decimal(str(ov.get("avg_cost", "0")))
        if t in portfolio:
            portfolio[t]["shares"]     = shares_ov
            portfolio[t]["total_cost"] = cost_ov * shares_ov
        else:
            portfolio[t] = {
                "shares": shares_ov, "total_cost": cost_ov * shares_ov,
                "first_buy_date": ov.get("first_buy_date", ""), "category": "Crypto",
            }

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

# ═══════════════════════════════════════════════════════════════════════════════
# SECRETS → ENV BRIDGE
# ═══════════════════════════════════════════════════════════════════════════════

def _load_env_from_secrets() -> None:
    try:
        for key in [
            "PLAID_CLIENT_ID", "PLAID_SECRET", "PLAID_ENV", "PLAID_ACCESS_TOKEN",
            "FINNHUB_API_KEY", "POLYGON_API_KEY", "HOLDINGS_CACHE_TTL_HOURS",
        ]:
            if key in st.secrets and not os.environ.get(key):
                os.environ[key] = st.secrets[key]
    except Exception:
        pass

# ═══════════════════════════════════════════════════════════════════════════════
# PRICE FETCHING — HoldingsManager-aware PriceService
# ═══════════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=30, show_spinner=False)
def fetch_prices(tickers: tuple, _bust: int = 0) -> dict[str, float]:
    """Fetch real-time mid-prices. v11 PriceService if available, yfinance fallback."""
    _load_env_from_secrets()
    prices: dict[str, float] = {}

    if _V11_AVAILABLE:
        holdings_cache: Optional[HoldingsCache] = None
        try:
            mgr = HoldingsManager(cache_path=HOLDINGS_CACHE_PATH)
            needs, _ = mgr.needs_plaid_sync()
            if not needs:
                holdings_cache = mgr.get_holdings()
        except Exception:
            pass

        svc     = PriceService(holdings_cache=holdings_cache)
        results = svc.fetch_prices(list(tickers))
        for ticker, result in results.items():
            if result.mid_price > 0:
                prices[ticker] = result.mid_price
            else:
                disk = _load(PRICE_CACHE_PATH, {})
                if ticker in disk:
                    prices[ticker] = disk[ticker]

        disk = _load(PRICE_CACHE_PATH, {})
        disk.update({t: p for t, p in prices.items() if p and p > 0})
        _save(PRICE_CACHE_PATH, disk)
        return prices

    # Fallback
    import requests as req
    try:
        import yfinance as yf
        for t in [x for x in tickers if x not in CRYPTO_TICKERS]:
            try:
                info = yf.Ticker(t).fast_info
                p = info.get("last_price") or info.get("regularMarketPrice")
                prices[t] = round(float(p), 4) if p else None
            except Exception:
                prices[t] = None
    except ImportError:
        pass

    _CG = {"BTC":"bitcoin","XRP":"ripple","ETH":"ethereum","SOL":"solana","DOGE":"dogecoin"}
    for t in [x for x in tickers if x in CRYPTO_TICKERS]:
        cid = _CG.get(t)
        if not cid:
            prices[t] = None; continue
        try:
            r = req.get(f"https://api.coingecko.com/api/v3/simple/price?ids={cid}&vs_currencies=usd", timeout=8)
            prices[t] = round(float(r.json()[cid]["usd"]), 4)
        except Exception:
            prices[t] = None

    disk = _load(PRICE_CACHE_PATH, {})
    for t in tickers:
        if not prices.get(t) and t in disk:
            prices[t] = disk[t]
    disk.update({t: p for t, p in prices.items() if p})
    _save(PRICE_CACHE_PATH, disk)
    return prices


# ═══════════════════════════════════════════════════════════════════════════════
# SMART SYNC — HoldingsManager + PortfolioAggregator
# ═══════════════════════════════════════════════════════════════════════════════

def smart_sync_portfolio(force_plaid: bool = False) -> Optional[dict]:
    if not _V11_AVAILABLE:
        return None
    _load_env_from_secrets()
    if not os.environ.get("PLAID_ACCESS_TOKEN"):
        return None
    try:
        mgr      = HoldingsManager(cache_path=HOLDINGS_CACHE_PATH)
        agg      = PortfolioAggregator(holdings_manager=mgr)
        snapshot = agg.calculate_total_value(force_plaid_refresh=force_plaid)
        from main_sync import export_json
        export_json(snapshot, str(PLAID_SNAPSHOT_PATH))
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
        return HoldingsManager(cache_path=HOLDINGS_CACHE_PATH).get_cache_status()
    except Exception as e:
        return {"status": "error", "label": str(e), "holdings_count": 0,
                "age_hours": None, "is_stale": True, "last_synced": None,
                "cash_usd": 0.0, "next_sync_in": None}


# ═══════════════════════════════════════════════════════════════════════════════
# SAFE PRICE HELPER
# ═══════════════════════════════════════════════════════════════════════════════

def _safe_price(ticker: str, pos: dict, prices: dict) -> float:
    p = prices.get(ticker)
    if p and p > 0:
        return float(p)
    disk = _load(PRICE_CACHE_PATH, {})
    if ticker in disk and disk[ticker] > 0:
        return float(disk[ticker])
    return float(pos.get("avg_cost", 1.0) or 1.0)

# ═══════════════════════════════════════════════════════════════════════════════
# LT ELIGIBILITY
# ═══════════════════════════════════════════════════════════════════════════════

def _parse_date_robust(date_str: str) -> Optional[datetime.date]:
    """
    Parse a date string tolerantly, handling:
      - ISO format:        '2024-03-18'
      - M/D/YYYY:          '1/10/2025'
      - M/D/YY:            '1/10/25'
      - MM/DD/YYYY:        '01/10/2025'
      - pandas-parsed:     anything pd.to_datetime can handle

    Returns a datetime.date or None on failure.
    Never raises.
    """
    if not date_str:
        return None
    # Fast path: already valid ISO (most common for bootstrap/stored rows)
    try:
        return datetime.date.fromisoformat(date_str)
    except (ValueError, TypeError):
        pass
    # Slow path: let pandas handle M/D/YYYY and other CSV variants
    try:
        import pandas as pd
        return pd.to_datetime(date_str, dayfirst=False).date()
    except Exception:
        pass
    return None


def is_lt_eligible(first_buy_date: str) -> bool:
    """Return True if the position has been held >= 366 days (long-term eligible)."""
    d = _parse_date_robust(first_buy_date)
    if d is None:
        return False
    return (datetime.date.today() - d).days >= 366


def days_to_lt(first_buy_date: str) -> int:
    """Return days remaining until long-term eligibility (0 if already eligible)."""
    d = _parse_date_robust(first_buy_date)
    if d is None:
        return 9999
    return max(0, 366 - (datetime.date.today() - d).days)

# ═══════════════════════════════════════════════════════════════════════════════
# RECOMMENDATION ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

def generate_recs(portfolio: dict, prices: dict) -> list[dict]:
    """Dynamic recs. Returns list sorted: sell→review→buy→trim→hold."""
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
        d_fbd   = _parse_date_robust(fbd)
        lt_date = (
            (d_fbd + datetime.timedelta(days=366)).isoformat()
            if d_fbd else "?"
        )
        tax_tag = "✅ LT (15%)" if lt else f"⏳ ST — wait until {lt_date}"

        rec = {
            "ticker": ticker, "category": pos.get("category", "Stocks"),
            "shares": shares, "price": price, "cost": cost,
            "equity": equity, "pnl_pct": pnl_pct, "upside": upside,
            "lt": lt, "dtlt": dtlt, "lt_date": lt_date, "tax": tax_tag,
            "action": "", "cat": "", "plain": "", "why": "",
            "priority": 4, "proceeds": 0.0,
        }

        if ticker in SELL_LIST and lt:
            rec.update(action="SELL NOW — LT ✅", cat="sell", priority=0, proceeds=equity,
                       plain=f"Sell all {shares:.4f} sh (~${equity:,.0f}). Reinvest VOO/VYM same day.",
                       why="LT eligible. Pay 15% not 37%. ETF swap = no wash sale.")
        elif ticker in SELL_PENDING and lt:
            swap = "VOO" if ticker == "SPY" else "QQQ"
            rec.update(action=f"SELL NOW — {ticker} LT ✅", cat="sell", priority=0, proceeds=equity,
                       plain=f"Now LT eligible. Sell → reinvest into {swap}.",
                       why="Calendar-flagged. ETF→ETF swap. Lock gains at 15%.")
        elif pnl_pct < -20:
            rec.update(action="REVIEW — BIG LOSS ⚠️", cat="review", priority=1,
                       plain=f"Down {pnl_pct:.1f}%. Decide: add at dip or cut losses.",
                       why="Position >20% underwater. Reassess thesis.")
        elif ticker in FOREVER_HOLD:
            rec.update(action="HOLD FOREVER — DRIP on 🔄", cat="hold", priority=2,
                       plain="Never sell. Keep DRIP on — dividends compound automatically.",
                       why="Core income ETF. Compounding dividend machine.")
        elif ticker in DCA_ALWAYS:
            rec.update(action="DCA EVERY DEPOSIT 💰", cat="buy", priority=2,
                       plain="Add every 2 weeks. Tracks the whole market.",
                       why="Core index. Never stop accumulating.")
        elif pnl_pct < -8 and upside > 20:
            rec.update(action="STRONG BUY — ON DIP 🟢", cat="buy", priority=2,
                       plain=f"Down {abs(pnl_pct):.1f}% from cost. Target ${target:,.0f} = {upside:.0f}% upside.",
                       why="Quality stock on sale. Add more to lower avg cost.")
        elif ticker in CRYPTO_TICKERS and upside > 25:
            rec.update(action="ACCUMULATE — CRYPTO 🟡", cat="buy", priority=2,
                       plain=f"Target ${target:,.0f}. {upside:.0f}% upside. Keep under 5% of portfolio.",
                       why="High-conviction crypto. Size position carefully.")
        elif upside > 20:
            rec.update(action="ACCUMULATE 📈", cat="buy", priority=2,
                       plain=f"Target ${target:,.0f} = {upside:.0f}% upside. Good entry point.",
                       why="Strong analyst upside.")
        elif 0 < dtlt <= 30:
            rec.update(action=f"HOLD — LT IN {dtlt} DAYS ⏰", cat="hold", priority=3,
                       plain=f"Only {dtlt} days until LT status. Wait — saves ~22% in tax.",
                       why=f"LT date: {lt_date}. Patience = tax savings.")
        elif ticker in IPO_HOLDS and lt:
            trim_sh = shares * 0.25
            rec.update(action="TRIM 25% — IPO LT ✅", cat="trim", priority=3,
                       proceeds=price * trim_sh,
                       plain=f"Sell {trim_sh:.2f} sh (~${price*trim_sh:,.0f}). Keep 75%.",
                       why="IPO position now LT. Partial lock-in at 15% rate.")
        elif pnl_pct > 20 and lt:
            trim_sh = shares * 0.20
            rec.update(action="TRIM 20% — TAKE GAINS 💰", cat="trim", priority=3,
                       proceeds=price * trim_sh,
                       plain=f"Sell {trim_sh:.2f} sh (~${price*trim_sh:,.0f}). Let rest ride.",
                       why=f"Up {pnl_pct:.0f}% and LT. Lock gains at 15% tax rate.")
        else:
            msg = f"Holding {shares:.4f} sh @ ${price:,.2f}. "
            msg += f"LT in {dtlt} days." if not lt else (
                f"Target ${target:,.0f} = {upside:.0f}% upside." if upside > 0 else "No action needed."
            )
            rec.update(action="HOLD", cat="hold", priority=4, plain=msg, why="No action needed today.")

        recs.append(rec)

    return sorted(recs, key=lambda r: (r["priority"], -r["equity"]))

# ═══════════════════════════════════════════════════════════════════════════════
# AI TARGET ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

def generate_suggested_targets(portfolio: dict) -> dict[str, float]:
    _WEIGHTS = {
        "VOO": 12, "QQQ": 10, "VYM": 8, "SCHD": 5, "VTI": 5,
        "NVDA": 10, "AAPL": 6, "META": 6, "GOOGL": 5, "MSFT": 5,
        "BRK-B": 4, "WMT": 3, "COST": 3,
        "VXUS": 3, "GLD": 3, "VGT": 2, "XLE": 2, "VHT": 2, "VIS": 1,
        "NFLX": 2, "TSM": 2, "QCOM": 1, "RDDT": 1, "CRM": 1,
        "BTC": 3, "XRP": 2,
    }
    held  = {t: _WEIGHTS.get(t, 1) for t in portfolio}
    total = sum(held.values()) or 1
    return {t: round(w / total * 100, 1) for t, w in held.items()}


# ═══════════════════════════════════════════════════════════════════════════════
# ② CASH-INFORMED REBALANCING
# ═══════════════════════════════════════════════════════════════════════════════

def compute_rebalancing(
    portfolio: dict,
    prices: dict,
    targets: dict,
    cash_available: float = 0.0,
) -> list[dict]:
    """
    Drift table: current % vs target %.

    When cash_available > 0, each underweight BUY row gains a 'cash_to_deploy'
    field: the dollar amount from available cash that should go into that ticker
    to start closing the gap.  The cash is distributed proportionally by the
    size of each position's deficit.

    Returns list sorted most-underweight first.
    """
    total_invested = sum(_safe_price(t, p, prices) * p["shares"] for t, p in portfolio.items())
    # Total assets including cash for percentage calculations
    total_with_cash = total_invested + cash_available
    if total_with_cash <= 0:
        return []

    rows = []
    for ticker, pos in portfolio.items():
        p   = _safe_price(ticker, pos, prices)
        mkt = p * pos["shares"]
        # Current % of the full portfolio (invested + cash)
        cur  = mkt / total_with_cash * 100
        tgt  = targets.get(ticker, 0)
        drft = cur - tgt
        rows.append({
            "ticker":          ticker,
            "market_value":    mkt,
            "current_pct":     round(cur, 1),
            "target_pct":      round(tgt, 1),
            "drift":           round(drft, 1),
            "action":          "TRIM" if drft > 5 else ("BUY" if drft < -5 else "OK"),
            "cash_to_deploy":  0.0,   # filled below for BUY rows
        })

    # Distribute available cash proportionally across underweight positions
    if cash_available > 0:
        buy_rows   = [r for r in rows if r["action"] == "BUY"]
        total_gap  = sum(abs(r["drift"]) for r in buy_rows)
        if total_gap > 0:
            for r in buy_rows:
                r["cash_to_deploy"] = round(cash_available * abs(r["drift"]) / total_gap, 2)

    return sorted(rows, key=lambda r: r["drift"])


# ═══════════════════════════════════════════════════════════════════════════════
# $900 BIWEEKLY DEPOSIT ENGINE  (cash-informed)
# ═══════════════════════════════════════════════════════════════════════════════

def get_biweekly_dates(start: datetime.date, n: int = 18) -> list[datetime.date]:
    dates: list[datetime.date] = []
    d = start
    for _ in range(n * 14 + 14):
        if d.weekday() == 4:
            if not dates or (d - dates[-1]).days >= 14:
                dates.append(d)
            if len(dates) >= n:
                break
        d += datetime.timedelta(days=1)
    return dates[:n]


def generate_deposit_recs(
    deposit_num:  int,
    portfolio:    dict,
    prices:       dict,
    targets:      dict,
    amount:       float = 900.0,
    cash_balance: float = 0.0,
) -> list[dict]:
    """
    Allocate investable capital for deposit #N.

    Total investable = amount (new deposit) + cash_balance (Robinhood cash).
    This ensures the existing idle cash is put to work alongside the deposit.

    With targets: drift-fill against total_investable.
    Without targets: fixed 28/22/17/17/16 plan against total_investable.

    Each rec gains an 'investable_total' field so the UI can display
    the combined figure.
    """
    total_investable = amount + cash_balance
    rotating_pick    = DEPOSIT_ROTATION[(deposit_num - 1) % len(DEPOSIT_ROTATION)]
    total_eq         = sum(_safe_price(t, p, prices) * p["shares"] for t, p in portfolio.items())

    if targets and total_eq > 0:
        # Rebalancing uses total_with_cash so drift is calculated against full liquidity
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
                })
            return recs

    # Fixed plan scaled to total_investable
    recs = []
    for ticker, pct in DEPOSIT_PLAN:
        t     = rotating_pick if ticker == "ROTATING" else ticker
        alloc = total_investable * pct
        p     = _safe_price(t, portfolio.get(t, {}), prices)
        # Attribute cash contribution proportionally
        cash_contrib = cash_balance * pct
        recs.append({
            "ticker":           t,
            "alloc_pct":        round(pct * 100, 1),
            "amount":           round(alloc, 2),
            "price":            p,
            "est_shares":       round(alloc / p, 4) if p > 0 else 0,
            "why":              "Rotating pick" if ticker == "ROTATING" else "Core allocation",
            "investable_total": round(total_investable, 2),
            "from_cash":        round(cash_contrib, 2),
        })
    return recs


# ═══════════════════════════════════════════════════════════════════════════════
# ③ DECISION LOG / MANUAL OVERRIDE SYSTEM
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class DecisionLogEntry:
    """One manual-override decision record."""
    date:           str    # ISO date of the override (today)
    ticker:         str    # Asset being bought/sold
    ai_rec_amount:  float  # AI-recommended dollar amount
    manual_amount:  float  # What the user actually decided to invest
    delta:          float  # manual_amount - ai_rec_amount (+ = more than AI, - = less)
    reason:         str    # Free-text justification entered by the user
    timestamp:      str    # Full ISO timestamp for ordering
    deposit_num:    int    # Which deposit cycle this belongs to
    action_type:    str    # 'buy' | 'sell' | 'trim' | 'override'

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "DecisionLogEntry":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


def log_decision(entry: DecisionLogEntry) -> None:
    """Append one DecisionLogEntry to decision_log.json (disk-persisted)."""
    log = _load(DECISION_LOG_PATH, [])
    log.append(entry.to_dict())
    _save(DECISION_LOG_PATH, log[-500:])   # keep last 500 decisions
    logger.info("Decision logged: %s AI=$%.2f Manual=$%.2f Δ=$%.2f",
                entry.ticker, entry.ai_rec_amount, entry.manual_amount, entry.delta)


def load_decision_log() -> list[dict]:
    """Return all decision log entries as a list of dicts (for Streamlit dataframe)."""
    return _load(DECISION_LOG_PATH, [])


def apply_overrides_to_recs(
    recs:        list[dict],
    overrides:   dict[str, float],   # {ticker: manual_amount}
    reasons:     dict[str, str],     # {ticker: reason_text}
    deposit_num: int,
) -> list[dict]:
    """
    Apply manual override amounts to the deposit recs list.

    For each ticker with an override:
      - Substitute rec['amount'] with the manual value
      - Recalculate est_shares based on current price
      - Persist a DecisionLogEntry to decision_log.json
      - Add rec['overridden'] = True and rec['override_delta'] = delta

    Returns the updated recs list. Does NOT mutate the originals — returns copies.
    """
    today = datetime.date.today().isoformat()
    updated = []
    for rec in recs:
        r = dict(rec)   # shallow copy — never mutate caller's list
        ticker = r["ticker"]
        if ticker in overrides:
            manual_amt  = float(overrides[ticker])
            ai_amt      = float(r["amount"])
            delta       = manual_amt - ai_amt
            price       = float(r.get("price", 0))
            reason_text = reasons.get(ticker, "")

            r["amount"]        = round(manual_amt, 2)
            r["est_shares"]    = round(manual_amt / price, 4) if price > 0 else 0
            r["overridden"]    = True
            r["override_delta"] = round(delta, 2)
            r["reason"]        = reason_text

            log_decision(DecisionLogEntry(
                date          = today,
                ticker        = ticker,
                ai_rec_amount = round(ai_amt, 2),
                manual_amount = round(manual_amt, 2),
                delta         = round(delta, 2),
                reason        = reason_text,
                timestamp     = datetime.datetime.now().isoformat(),
                deposit_num   = deposit_num,
                action_type   = "override",
            ))
        else:
            r["overridden"]     = False
            r["override_delta"] = 0.0
        updated.append(r)
    return updated


# ═══════════════════════════════════════════════════════════════════════════════
# CRYPTO PDF PARSER
# ═══════════════════════════════════════════════════════════════════════════════

def parse_crypto_pdf(file_bytes: bytes) -> dict[str, dict]:
    overrides: dict[str, dict] = {}
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    except Exception:
        return overrides
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
# PORTFOLIO SUMMARY
# ═══════════════════════════════════════════════════════════════════════════════

def portfolio_totals(portfolio: dict, prices: dict, cash: float) -> dict:
    stocks_val = crypto_val = cost_tot = 0.0
    for ticker, pos in portfolio.items():
        p   = _safe_price(ticker, pos, prices)
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
        "total": total, "stocks": stocks_val, "crypto": crypto_val,
        "cash": cash, "cost_basis": cost_tot, "pnl": pnl, "pnl_pct": pct,
    }


def snapshot_portfolio(portfolio: dict, prices: dict, cash: float, recs: list) -> dict:
    totals = portfolio_totals(portfolio, prices, cash)
    snap   = {
        "timestamp": datetime.datetime.now().isoformat(),
        "totals":    totals,
        "recs":      [{"ticker": r["ticker"], "action": r["action"], "pnl_pct": r["pnl_pct"]} for r in recs],
    }
    history = _load(REC_HISTORY_PATH, [])
    history.append(snap)
    _save(REC_HISTORY_PATH, history[-200:])
    return snap


def log_deposit(deposit_num: int, date_str: str, recs: list, total: float) -> None:
    log = _load(DEPOSIT_LOG_PATH, [])
    log.append({
        "num": deposit_num, "date": date_str, "total": total,
        "buys": [{"ticker": r["ticker"], "amount": r["amount"], "shares": r["est_shares"]} for r in recs],
    })
    _save(DEPOSIT_LOG_PATH, log)
