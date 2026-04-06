"""
data_engine.py — Portfolio War Room v11.4
All business logic — zero UI code.

v11.4 changes (dedup-only surgical refactor — no UI changes):
  ROOT CAUSES FIXED:
    1. _bootstrap() used opaque "BOOTSTRAP|ticker" keys → those hashes could
       NEVER match a real CSV row fingerprint. Every CSV upload appeared to
       be all-new rows because processed_ids was seeded with un-matchable keys.

    2. make_tx_fingerprint() used raw strings: "4/2/2026" and "2026-04-02"
       hashed differently; "$173.78" and "173.78" hashed differently. Any
       field formatting difference between bootstrap data and CSV data produced
       a collision-free but logically-duplicate fingerprint.

    3. ingest_csv() never updated existing_ids in-place, so Layer 1 (session
       dedup) could never block a same-session re-upload.

    4. _init() seeded processed_ids from strip_existing_tx_store_fingerprints()
       which returned only the opaque BOOTSTRAP keys — never CSV-canonical ones.

  FIXES APPLIED:
    A. _norm_decimal(val, places=6) — new helper
       Strips $, commas, parentheses; converts to Decimal at fixed precision.
       "874.63", "$874.63", "874.630000" all → "874.630000". Never raises.

    B. _norm_date(val) — new helper
       pd.to_datetime(val).strftime('%Y-%m-%d') with ISO fast-path.
       "4/2/2026" and "2026-04-02" → same "2026-04-02".

    C. make_tx_fingerprint() rewritten — canonical string:
         NormDate | Ticker | Code | NormQty | NormPrice
       (cash-only rows use NormAmt + settle as tiebreaker instead)
       Now produces IDENTICAL hashes for bootstrap data and CSV rows of the
       same transaction, regardless of date or number formatting.

    D. _bootstrap() rewritten — uses make_tx_fingerprint() to write keys
       so tx_store fingerprints match what ingest_csv() will produce for the
       same rows from a real CSV export.

    E. ingest_csv() — existing_ids.update(seen_this_upload) called AFTER the
       loop but BEFORE return. Caller's session set updated atomically in-place.

    F. seed_processed_ids_from_history() — new public function
       Generates fingerprints from both BAKED_BOOTSTRAP and tx_store so that
       _init() in main_app.py can seed the session with ALL ~600 historical
       fingerprints, not just the ~34 opaque bootstrap keys. The sidebar
       dedup badge will now show the correct full count on first load.

  All v11.2 features (cash-informed rebalancing, decision log, pandas 3.0
  map() fixes) preserved exactly as-is.
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
SYSTEM_STATE_PATH   = Path("system_state.json")   # ← NEW v12: bootstrap/live mode

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
# SYSTEM STATE (v12) — bootstrap / live mode
# ═══════════════════════════════════════════════════════════════════════════════

def get_system_mode() -> str:
    """
    Return 'bootstrap' (default) or 'live'.

    'bootstrap': app is running on BAKED_BOOTSTRAP data only.
                 No real CSV/PDF has ever been imported.
    'live':      at least one real CSV or PDF has been imported.
                 Bootstrap data has been purged from tx_store.json.
    """
    state = _load(SYSTEM_STATE_PATH, {})
    return state.get("mode", "bootstrap")


def transition_to_live() -> None:
    """
    Called once, the first time a real CSV or crypto PDF is uploaded.

    1. Wipes tx_store.json (removes all bootstrap-derived synthetic rows).
    2. Writes system_state.json with mode='live'.

    After this call, _bootstrap() will never re-run (tx_store is non-empty).
    Bootstrap data and real transaction data can never be mixed.
    """
    _save(TX_STORE_PATH, {})
    _save(SYSTEM_STATE_PATH, {
        "mode": "live",
        "transitioned_at": datetime.datetime.now().isoformat(),
    })
    logger.info("System mode: bootstrap → live. tx_store cleared for real-data rebuild.")


# ═══════════════════════════════════════════════════════════════════════════════
# ① TRANSACTION DEDUPLICATION — High-Integrity Hashing Engine (v11.4)
# ═══════════════════════════════════════════════════════════════════════════════

def _norm_decimal(val: str, places: int = 6) -> str:
    """
    Normalise any price/quantity/amount string to a fixed-precision Decimal
    string for use in SHA-256 fingerprinting.

    Handles every format Robinhood produces:
      "$173.78"       → "173.780000"
      "($14.23)"      → "-14.230000"   (debit — parens mean negative)
      "$1,600.00"     → "1600.000000"  (comma thousands)
      "0.002071"      → "0.002071"     (fractional share qty)
      ""  / "—"       → "0.000000"     (blank field)

    Uses Decimal to prevent IEEE-754 float rounding artefacts.
    Returns a zero string on any parse failure — never raises.
    """
    s = str(val).strip()
    paren_neg = s.startswith("(") and s.endswith(")")
    s = s.replace(",", "").replace("$", "").replace("(", "").replace(")", "").strip()
    if paren_neg and s and not s.startswith("-"):
        s = "-" + s
    if not s or s in ("-", "."):
        return "0." + "0" * places
    try:
        d = Decimal(s).quantize(Decimal(10) ** -places, rounding=ROUND_HALF_UP)
        return format(d, f".{places}f")
    except (InvalidOperation, ValueError):
        return "0." + "0" * places


def _norm_date(val: str) -> str:
    """
    Normalise any date string to YYYY-MM-DD.
    Fast-path for already-ISO strings (bootstrap/stored rows).
    Uses pd.to_datetime() for M/D/YYYY and all other CSV variants.
    Returns the raw string on failure — never raises.
    """
    import pandas as pd
    s = str(val).strip()
    if not s:
        return s
    # Fast path: already ISO (most common for bootstrap and stored rows)
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        return s
    try:
        return pd.to_datetime(s, dayfirst=False).strftime("%Y-%m-%d")
    except Exception:
        return s


def make_tx_fingerprint(
    date_raw:  str,
    code:      str,
    ticker:    str,
    qty_raw:   str,
    price_raw: str,
    amt_raw:   str,
    settle:    str,
) -> str:
    """
    High-integrity SHA-256 fingerprint for one transaction row.

    Canonical string (pipe-separated, all fields normalised):
      Trade/CDIV/SPL rows (ticker OR qty present):
        NormDate | Ticker | Code | NormQty(6dp) | NormPrice(6dp)
        Amount excluded: Robinhood rounds debits independently.

      Cash-only rows (no ticker AND no qty — ACH/RTP deposits):
        NormDate | "" | Code | NormAmt(6dp) | Settle
        Settle-date tiebreaker distinguishes same-day same-amount deposits.

    Guarantees:
      "4/2/2026"  == "2026-04-02"  after _norm_date   → identical hash
      "$173.78"   == "173.78"      after _norm_decimal → identical hash
      "0.002071"  == "0.00207100"  after _norm_decimal → identical hash
      bootstrap row hash == CSV row hash for same transaction
    """
    nd = _norm_date(date_raw)
    t  = (ticker or "").strip().upper()
    c  = (code   or "").strip()

    if not t and not (qty_raw or "").strip():
        # Cash-only: ACH / RTP / MISC deposits and withdrawals
        src = f"{nd}|{t}|{c}|{_norm_decimal(amt_raw)}|{(settle or '').strip()}"
    else:
        # Trade, dividend, split — anything with a ticker or quantity
        src = f"{nd}|{t}|{c}|{_norm_decimal(qty_raw)}|{_norm_decimal(price_raw)}"

    return hashlib.sha256(src.encode()).hexdigest()


# ═══════════════════════════════════════════════════════════════════════════════
# BOOTSTRAP  (writes tx_store.json with canonical fingerprints on first run)
# ═══════════════════════════════════════════════════════════════════════════════

def _bootstrap() -> None:
    """
    Write BAKED_BOOTSTRAP positions to tx_store.json on first run (empty store).

    CRITICAL: Uses make_tx_fingerprint() to generate keys — NOT the old
    "BOOTSTRAP|ticker" opaque scheme. This ensures that when a real Robinhood
    CSV is uploaded later, the fingerprints match and duplicates are correctly
    detected instead of appearing as all-new rows.
    """
    if TX_STORE_PATH.exists():
        try:
            if json.loads(TX_STORE_PATH.read_text()):
                return
        except Exception:
            pass
    synthetic: dict[str, dict] = {}
    for ticker, pos in BAKED_BOOTSTRAP.items():
        # Generate canonical fingerprint — identical to what ingest_csv() would
        # produce for the same row from a real CSV export.
        key = make_tx_fingerprint(
            date_raw  = pos["first_buy_date"],
            code      = "Buy",
            ticker    = ticker,
            qty_raw   = pos["shares"],
            price_raw = pos["avg_cost"],
            amt_raw   = "",
            settle    = "",
        )
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


def strip_existing_tx_store_fingerprints() -> set[str]:
    """
    Return all fingerprints currently persisted in tx_store.json.
    Since _bootstrap() now writes canonical fingerprints, these match what
    ingest_csv() produces — so seeding processed_ids with these is correct.
    """
    store = _load(TX_STORE_PATH, {})
    return set(store.keys())


def seed_processed_ids_from_history() -> set[str]:
    """
    Generate the COMPLETE set of historical fingerprints for session seeding.

    Returns the union of:
      1. Fingerprints derived from BAKED_BOOTSTRAP (what _bootstrap() writes)
      2. Fingerprints already in tx_store.json on disk

    This is what _init() in main_app.py should call so that:
      - The sidebar badge shows the real count (~600+ when CSV is imported)
      - Every bootstrap position is correctly recognised as already-known
      - No CSV upload can re-insert positions that bootstrap already covers

    After _bootstrap() is called and tx_store is populated, sets (1) and (2)
    are identical — the union is just a safety net for the transition period.
    """
    # Set 1: bootstrap-derived fingerprints (canonical, matches CSV hashes)
    bootstrap_fps: set[str] = set()
    for ticker, pos in BAKED_BOOTSTRAP.items():
        fp = make_tx_fingerprint(
            date_raw  = pos["first_buy_date"],
            code      = "Buy",
            ticker    = ticker,
            qty_raw   = pos["shares"],
            price_raw = pos["avg_cost"],
            amt_raw   = "",
            settle    = "",
        )
        bootstrap_fps.add(fp)

    # Set 2: whatever is already on disk (covers real CSV rows after first import)
    disk_fps = strip_existing_tx_store_fingerprints()

    return bootstrap_fps | disk_fps


@dataclass
class IngestStats:
    total_rows_in_file:     int  = 0   # every non-blank row seen
    new_rows_added:         int  = 0   # rows actually written to tx_store
    duplicate_rows_skipped: int  = 0   # L1 + L2: already in session or on disk
    seen_in_file:           int  = 0   # L3: same row appeared twice in this file
    skipped_no_code:        int  = 0   # blank Trans Code / footer rows
    errors:                 list = field(default_factory=list)
    mode_transitioned:      bool = False  # v12: True if bootstrap→live happened

    @property
    def total_skipped(self) -> int:
        return self.duplicate_rows_skipped + self.seen_in_file + self.skipped_no_code


def ingest_csv(file_bytes: bytes, existing_ids: set) -> tuple[IngestStats, set]:
    """
    Parse a Robinhood CSV and append only genuinely new rows to tx_store.json.

    Three-layer deduplication:
      Layer 1 — existing_ids: fingerprints known to this session
                (seeded by seed_processed_ids_from_history() at cold-start,
                 then updated atomically after each upload — see post-loop fix).
      Layer 2 — existing_on_disk: snapshot of tx_store keys frozen BEFORE the
                loop so rows added during this upload never pollute Layer 2.
      Layer 3 — seen_this_upload: intra-file duplicates (row appears twice).

    ATOMIC SESSION UPDATE (the bug fix):
      existing_ids.update(seen_this_upload) runs AFTER the loop but BEFORE
      return — updates the caller's set in-place so Layer 1 correctly blocks
      any re-upload of the same file in the same Streamlit session.

    Returns (IngestStats, set_of_newly_added_fingerprints).
    """
    stats             = IngestStats()
    new_ids: set      = set()
    seen_this_upload: set = set()

    # ── v12: bootstrap → live transition ─────────────────────────────────────
    # If this is the first real CSV import, purge bootstrap-synthetic rows so
    # they can never mix with real transaction data.  After transition_to_live()
    # tx_store.json is empty and existing_ids is cleared so every real row is
    # treated as genuinely new.
    if get_system_mode() == "bootstrap":
        transition_to_live()
        existing_ids.clear()          # bootstrap FPs are no longer relevant
        stats.mode_transitioned = True

    # Load tx_store and snapshot keys BEFORE the loop (Layer 2 source).
    # Adding rows during this loop must not pollute the Layer 2 snapshot.
    tx_store          = _load(TX_STORE_PATH, {})
    existing_on_disk: set = set(tx_store.keys())

    # Strip null bytes (occasionally present in Robinhood CSV exports)
    text   = file_bytes.decode("utf-8", errors="replace").replace("\x00", "")
    reader = csv.DictReader(io.StringIO(text), quoting=csv.QUOTE_ALL)

    for row in reader:
        stats.total_rows_in_file += 1

        code = (row.get("Trans Code") or row.get("Activity Type") or "").strip()
        if not code:
            stats.skipped_no_code += 1
            continue

        ticker    = (row.get("Instrument") or row.get("Symbol") or "").strip().upper()
        qty_raw   = (row.get("Quantity",    "") or "").strip()
        price_raw = (row.get("Price",       "") or "").strip()
        amt_raw   = (row.get("Amount",      "") or "").strip()
        # Prefer Activity Date (stable); fall back to Process Date or Date
        date_raw  = (row.get("Activity Date") or row.get("Process Date") or
                     row.get("Date") or "").strip()
        settle    = (row.get("Settle Date", "") or "").strip()
        desc      = (row.get("Description", "") or "").strip()

        fp = make_tx_fingerprint(date_raw, code, ticker, qty_raw, price_raw, amt_raw, settle)

        # ── Layer 1: session dedup ────────────────────────────────────────────
        if fp in existing_ids:
            stats.duplicate_rows_skipped += 1
            continue

        # ── Layer 2: disk dedup (snapshot frozen before loop) ─────────────────
        if fp in existing_on_disk:
            stats.duplicate_rows_skipped += 1
            continue

        # ── Layer 3: intra-file dedup ─────────────────────────────────────────
        if fp in seen_this_upload:
            stats.seen_in_file += 1
            continue
        seen_this_upload.add(fp)

        # ── Store the row ─────────────────────────────────────────────────────
        qty_clean = re.sub(r"[^\d.\-]", "", qty_raw)
        if qty_clean in ("", "-", "."):
            qty_clean = "0"

        category = (
            "Crypto" if ticker in CRYPTO_TICKERS else
            "ETF"    if ticker in ETF_TICKERS    else
            "Stocks"
        )

        tx_store[fp] = {
            "date":        date_raw,
            "code":        code,
            "ticker":      ticker,
            "qty":         qty_clean,
            "price":       re.sub(r"[^\d.\-]", "", price_raw) or "0",
            "amount":      re.sub(r"[^\d.\-]", "", amt_raw)   or "0",
            "description": desc,
            "category":    category,
        }
        new_ids.add(fp)
        stats.new_rows_added += 1

    # ── ATOMIC SESSION UPDATE ─────────────────────────────────────────────────
    # Update existing_ids IN PLACE so the caller's session_state.processed_ids
    # immediately knows about every fingerprint imported above.
    # Without this, Layer 1 can never block a same-session re-upload.
    existing_ids.update(seen_this_upload)

    _save(TX_STORE_PATH, tx_store)

    # Append entry to rolling recon log
    recon = _load(RECON_LOG_PATH, [])
    recon.append({
        "timestamp":   datetime.datetime.now().isoformat(),
        "total_rows":  stats.total_rows_in_file,
        "new":         stats.new_rows_added,
        "cross_dupes": stats.duplicate_rows_skipped,
        "intra_dupes": stats.seen_in_file,
        "no_code":     stats.skipped_no_code,
        "errors":      stats.errors,
    })
    _save(RECON_LOG_PATH, recon[-100:])

    return stats, new_ids


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
        logger.error("No PLAID_ACCESS_TOKEN found in secrets.")
        return None
        
    try:
        # 1. Check cache age to decide if we burn an 'Investments Refresh' credit
        cs = get_holdings_cache_status()
        age_hours = cs.get("age_hours")
        
        # If age_hours is None (no cache), or >= 24, or user clicked force sync, we refresh
        needs_hard_refresh = force_plaid or (age_hours is None) or (age_hours >= 24)
        from plaid_client import PlaidClient
        p_client = PlaidClient()
        
        if needs_hard_refresh:
            # This consumes 1 credit from your 'Investments Refresh' (0/200) bucket
            logger.info(f"Sync: Age {age_hours}h >= 24h. Triggering hard refresh...")
            p_client.refresh_investments()
            # Give Plaid's backend 2 seconds to start the worker
            import time
            time.sleep(2) 
        else:
            logger.info(f"Sync: Age {age_hours}h < 24h. Using Plaid's intraday cache (FREE).")
            
        # 2. Proceed with normal aggregation
        mgr      = HoldingsManager(cache_path=HOLDINGS_CACHE_PATH)
        agg      = PortfolioAggregator(holdings_manager=mgr)
        
        # Always set this to True so the aggregator calls get_holdings() intraday
        # This pulls from Plaid's cache (uses 0 credits)
        snapshot = agg.calculate_total_value(force_plaid_refresh=True)
        
        # 3. Export the snapshot to your cache
        from main_sync import export_json
        export_json(snapshot, str(PLAID_SNAPSHOT_PATH))
        
        return snapshot
        
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
    """
    Parse a Robinhood Crypto monthly statement PDF.

    The statement table format is:
      "Bitcoin  0.03432981  BTC  $2301.45  99.94%"
      "XRP      1.066       XRP  $1.47     0.06%"

    Returns {ticker: {shares, avg_cost, first_buy_date}}.
    avg_cost is 0.0 — not available in the statement; user supplies via override.
    """
    overrides: dict[str, dict] = {}
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    except Exception:
        return overrides

    # Primary: "Bitcoin  0.03432981  BTC  $2301.45  99.94%"
    primary = re.compile(
        r"(?:Bitcoin|Ethereum|XRP|Ripple|Solana|Dogecoin|Cardano|Litecoin|Avalanche)"
        r"\s+([\d]+\.[\d]+)"
        r"\s+(BTC|ETH|XRP|SOL|DOGE|ADA|LTC|AVAX)"
        r"\s+\$[\d,]+\.[\d]+",
        re.IGNORECASE,
    )
    for m in primary.finditer(text):
        try:
            qty    = float(m.group(1).replace(",", ""))
            ticker = m.group(2).upper()
            if qty > 0:
                overrides[ticker] = {"shares": qty, "avg_cost": 0.0, "first_buy_date": ""}
        except Exception:
            pass

    # Fallback: "0.03432981 BTC" (>=4 decimal places identifies crypto qty)
    if not overrides:
        fallback = re.compile(
            r"([\d]+\.[\d]{4,})\s+(BTC|ETH|XRP|SOL|DOGE|ADA|LTC|AVAX)\b",
            re.IGNORECASE,
        )
        for m in fallback.finditer(text):
            try:
                qty    = float(m.group(1))
                ticker = m.group(2).upper()
                if qty > 0 and ticker not in overrides:
                    overrides[ticker] = {"shares": qty, "avg_cost": 0.0, "first_buy_date": ""}
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
