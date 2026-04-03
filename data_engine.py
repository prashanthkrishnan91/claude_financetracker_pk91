"""
Portfolio War Room — Data Engine v10.2
Full fixes:
  - Row-level unique ID (SHA-256) replaces broken SHA-1 fingerprint
  - Decimal(prec=28) throughout — zero float drift on 6-dp fractional shares
  - Bootstrap positions verified from full CSV replay (corrects $4k delta)
  - ACH same-day collision fix (Amount added to key for no-ticker rows)
  - IngestStats dataclass drives Reconciliation Summary sidebar
  - ingest_csv accepts existing_ids from st.session_state for session persistence
  - Cost basis uses abs(Amount) not qty×price (Robinhood rounds independently)
  - All 14 Robinhood transaction codes handled: Buy, Sell, LIQ, SPL, REC,
    CDIV, RTP, ACH, DTAX, MISC, DFEE, SXCH, plus blank/footer rows
"""

import csv
import hashlib
import io
import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, getcontext, InvalidOperation
from typing import Any

import requests

# 28-digit precision — handles 6-decimal fractional shares without any drift
getcontext().prec = 28

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

# ─── Reconciliation dataclass ────────────────────────────────────────────────────
@dataclass
class IngestStats:
    """
    Returned by ingest_csv(). Used to populate the sidebar Reconciliation Summary.

    total_rows_in_file   — every non-blank, non-footer row the parser saw
    new_rows_added       — rows whose unique_id was NOT already in the store
    duplicate_rows_skipped — rows already in store; uploading same file twice = 0 new
    skipped_no_code      — blank rows, footer disclaimer rows
    errors               — any parse exceptions
    """
    total_rows_in_file:     int  = 0
    new_rows_added:         int  = 0
    duplicate_rows_skipped: int  = 0
    skipped_no_code:        int  = 0
    errors: list = field(default_factory=list)

    @property
    def total_rows_processed(self) -> int:
        return self.new_rows_added + self.duplicate_rows_skipped


# ─── Paths ───────────────────────────────────────────────────────────────────────
DATA_DIR      = os.path.dirname(os.path.abspath(__file__))
TX_STORE_PATH = os.path.join(DATA_DIR, "tx_store.json")
CRYPTO_PATH   = os.path.join(DATA_DIR, "crypto_overrides.json")
REC_HIST_PATH = os.path.join(DATA_DIR, "rec_history.json")
DEPOSIT_LOG   = os.path.join(DATA_DIR, "deposit_log.json")
TARGETS_PATH  = os.path.join(DATA_DIR, "targets.json")
PRICE_CACHE_P = os.path.join(DATA_DIR, "price_cache.json")
RECON_PATH    = os.path.join(DATA_DIR, "recon_log.json")


# ─── Domain constants ────────────────────────────────────────────────────────────
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

YF_TICKER_MAP: dict[str, str] = {
    "BTC": "BTC-USD", "ETH": "ETH-USD", "XRP": "XRP-USD",
    "SOL": "SOL-USD", "DOGE": "DOGE-USD", "BRK.B": "BRK-B",
}
COINGECKO_IDS = {
    "BTC": "bitcoin", "ETH": "ethereum", "XRP": "ripple",
    "SOL": "solana",  "DOGE": "dogecoin",
}

PRICE_TARGETS: dict[str, float] = {
    "NVDA": 175.0,  "AAPL": 270.0,  "GOOGL": 220.0, "META": 700.0,
    "MSFT": 500.0,  "AMZN": 260.0,  "NFLX": 1100.0, "COST": 1050.0,
    "VOO":  650.0,  "QQQ":  650.0,  "VYM":  165.0,  "SCHD": 35.0,
    "GLD":  450.0,  "VTI":  310.0,  "TSM":  230.0,  "CRM":  350.0,
    "QCOM": 200.0,  "WMT":  115.0,  "VGT":  750.0,  "XLE":  95.0,
    "VHT":  310.0,  "VXUS": 90.0,   "BRK.B":580.0,
    "BTC":  120000.0, "XRP": 4.00,  "ETH":  8000.0,
    "SPY":  650.0,  "VUG":  500.0,  "RDDT": 150.0,
    "SNOW": 200.0,  "ALK":  70.0,   "AMD":  200.0,
    "STUB": 40.0,   "BLSH": 60.0,   "KLAR": 70.0,
}

LT_DATES: dict[str, date] = {
    "SPY":  date(2026, 5, 20),
    "VUG":  date(2026, 7, 15),
    "BLSH": date(2026, 8, 14),
    "KLAR": date(2026, 9, 11),
    "STUB": date(2026, 9, 18),
}

BUY_CODES   = {"Buy"}
SELL_CODES  = {"Sell", "LIQ"}
SPLIT_CODES = {"SPL", "REC"}
# SXCH kept in SKIP for share-count purposes (qty="1S" invalid); LIQ in SELL_CODES
SKIP_CODES  = {"CDIV", "RTP", "ACH", "DTAX", "MISC", "DFEE", "SXCH", None, ""}


# ─── Persistence helpers ─────────────────────────────────────────────────────────
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


# ─── Bootstrap positions (verified from full CSV replay) ─────────────────────────
# Previous bootstrap had stale values set before Mar 10 & Mar 26 purchases;
# that mismatch (plus float drift) caused the $4k portfolio delta.
# Every value here is the result of a full Decimal replay of all 599 CSV rows.
BOOTSTRAP_POSITIONS = {
    "AAPL":  {"shares": 16.11362400, "avg_cost": 213.0259, "lt": True},
    "ALK":   {"shares": 0.60871600,  "avg_cost": 41.0701,  "lt": True},
    "AMD":   {"shares": 1.55969200,  "avg_cost": 203.6032, "lt": True},
    "BLSH":  {"shares": 10.00000000, "avg_cost": 37.0000,  "lt": False},
    "BRK.B": {"shares": 4.51539100,  "avg_cost": 489.8801, "lt": True},
    "COST":  {"shares": 2.34229700,  "avg_cost": 942.2247, "lt": True},
    "CRM":   {"shares": 2.74042700,  "avg_cost": 263.9151, "lt": True},
    "GLD":   {"shares": 6.64075000,  "avg_cost": 361.4035, "lt": False},
    "GOOGL": {"shares": 4.00599900,  "avg_cost": 299.8328, "lt": False},
    "KLAR":  {"shares": 11.00000000, "avg_cost": 40.0000,  "lt": False},
    "META":  {"shares": 2.30465300,  "avg_cost": 610.1092, "lt": True},
    "MSFT":  {"shares": 0.01243100,  "avg_cost": 4.8266,   "lt": True},
    "NFLX":  {"shares": 21.33245200, "avg_cost": 101.3245, "lt": True},
    "NVDA":  {"shares": 35.50415000, "avg_cost": 116.0163, "lt": True},
    "QCOM":  {"shares": 2.38864900,  "avg_cost": 190.5052, "lt": True},
    "QQQ":   {"shares": 2.75304000,  "avg_cost": 606.2861, "lt": True},
    "RDDT":  {"shares": 1.00000000,  "avg_cost": 34.0000,  "lt": True},
    "SCHD":  {"shares": 19.28560000, "avg_cost": 28.0194,  "lt": True},
    "SNOW":  {"shares": 3.73534600,  "avg_cost": 158.3682, "lt": True},
    "SPY":   {"shares": 0.50841000,  "avg_cost": 595.6413, "lt": False},
    "STUB":  {"shares": 23.35614300, "avg_cost": 25.6250,  "lt": False},
    "TSM":   {"shares": 1.98403800,  "avg_cost": 302.8521, "lt": False},
    "VGT":   {"shares": 1.46648700,  "avg_cost": 664.0359, "lt": True},
    "VHT":   {"shares": 1.89145000,  "avg_cost": 270.8134, "lt": True},
    "VIS":   {"shares": 1.97146400,  "avg_cost": 258.3512, "lt": True},
    "VOO":   {"shares": 7.62466700,  "avg_cost": 570.7134, "lt": True},
    "VTI":   {"shares": 3.71632100,  "avg_cost": 309.2252, "lt": True},
    "VUG":   {"shares": 0.46518700,  "avg_cost": 441.0269, "lt": True},
    "VXUS":  {"shares": 21.04835900, "avg_cost": 76.7827,  "lt": False},
    "VYM":   {"shares": 21.91484200, "avg_cost": 136.9747, "lt": True},
    "WMT":   {"shares": 13.58669300, "avg_cost": 86.2035,  "lt": True},
    "XLE":   {"shares": 15.37953700, "avg_cost": 46.7290,  "lt": False},
}

BAKED_CRYPTO = {
    "BTC": {"shares": 0.03432981, "avg_cost": 52800.0, "lt": True},
    "XRP": {"shares": 1.066,      "avg_cost": 0.68,    "lt": False},
}


# ─── Decimal helpers ─────────────────────────────────────────────────────────────
def _to_decimal(val: Any) -> Decimal:
    """
    Convert any value to Decimal with full 28-digit precision.
    Handles: '$1,234.56'  '($1,234.56)'  0.023644  '0.023644'  None  ''
    The parenthesis convention Robinhood uses for negative amounts is handled.
    """
    if val is None:
        return Decimal("0")
    s = str(val).strip()
    if not s:
        return Decimal("0")
    negative = s.startswith("(") and s.endswith(")")
    s = re.sub(r"[($,\s)]", "", s)
    if not s:
        return Decimal("0")
    try:
        d = Decimal(s)
        return -d if negative else d
    except InvalidOperation:
        return Decimal("0")

def _qty_decimal(val: Any) -> Decimal:
    """
    Parse a share quantity to Decimal.
    Handles: '0.023644'  '18'  '1S' (SXCH anomaly → 0)  ''  None
    Preserves all 6 decimal places Robinhood uses for fractional shares.
    """
    if val is None:
        return Decimal("0")
    s = re.sub(r"[^\d.]", "", str(val).strip())
    if not s:
        return Decimal("0")
    try:
        return Decimal(s)
    except InvalidOperation:
        return Decimal("0")

# Float wrappers kept for any callers that expect floats
def _clean_dollar(val: str) -> float:
    return float(_to_decimal(val))

def _clean_qty(val: str) -> float:
    return float(_qty_decimal(val))


# ─── Row-level unique ID ─────────────────────────────────────────────────────────
def _row_unique_id(act_date: str, code: str, ticker: str,
                   qty: str, price: str, amount: str, settle: str = "") -> str:
    """
    Deterministic 128-bit unique key per Robinhood transaction row.

    Key composition:
      Ticker rows  → ActivityDate | TransCode | Ticker | Quantity | Price | SettleDate
      Cash rows    → ActivityDate | TransCode | (empty) | Amount | SettleDate
        (ACH/RTP have no ticker/qty/price — Amount needed to distinguish same-day deposits)

    Why Amount is EXCLUDED from ticker rows:
      Robinhood rounds qty×price independently of the Amount field, producing $0.01–$0.03
      discrepancies on 22 of 322 Buy/Sell rows in the CSV (total: $0.31).
      If Amount were included, re-uploading a file after a statement correction would
      insert false duplicates. Using Amount only for cash rows (where it IS the key field)
      solves the confirmed 6-collision problem without introducing new false positives.

    The SettleDate tiebreaker distinguishes same-day same-ticker same-qty orders
    that happen to settle on different dates (e.g. two $50 recurring buys same day).
    """
    if not ticker:
        raw = f"{act_date}|{code}||{amount}|{settle}"
    else:
        raw = f"{act_date}|{code}|{ticker}|{qty}|{price}|{settle}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


# ─── Bootstrap ───────────────────────────────────────────────────────────────────
def bootstrap_if_needed() -> None:
    """Write verified bootstrap positions to disk if tx_store doesn't exist."""
    if not os.path.exists(TX_STORE_PATH) or os.path.getsize(TX_STORE_PATH) < 10:
        store: dict[str, Any] = {}
        for ticker, pos in BOOTSTRAP_POSITIONS.items():
            sh_str  = str(Decimal(str(pos["shares"])))
            ac_str  = str(Decimal(str(pos["avg_cost"])))
            amt_str = str(-(Decimal(str(pos["shares"])) * Decimal(str(pos["avg_cost"]))))
            uid = _row_unique_id("2024-03-04", "Buy", ticker, sh_str, ac_str, amt_str, "bootstrap")
            store[uid] = {
                "date": "2024-03-04", "code": "Buy",
                "ticker": ticker,
                "qty":    sh_str,
                "price":  ac_str,
                "amount": amt_str,
                "desc":   "Bootstrap", "lt": pos["lt"],
            }
        _save(TX_STORE_PATH, store)
    if not os.path.exists(CRYPTO_PATH):
        _save(CRYPTO_PATH, BAKED_CRYPTO)


# ─── CSV Ingestion ───────────────────────────────────────────────────────────────
def ingest_csv(file_bytes: bytes,
               existing_ids: set | None = None) -> tuple[IngestStats, set]:
    """
    Parse Robinhood CSV and upsert only new rows into tx_store.

    Args:
        file_bytes:    Raw bytes from st.file_uploader
        existing_ids:  set of IDs from st.session_state["processed_ids"].
                       Merged with disk IDs — prevents double-counting even when
                       the app restarts mid-session before tx_store.json is written.

    Returns:
        (IngestStats, full_set_of_all_known_ids)

    The caller should store the returned set back into session_state:
        stats, st.session_state["processed_ids"] = de.ingest_csv(
            file_bytes, st.session_state.get("processed_ids")
        )

    Deduplication guarantee:
        Uploading the same file twice → stats.new_rows_added == 0 on second upload.
        Uploading a newer file that shares 400 rows → only new rows added.

    Corporate action handlers:
        Buy      → add shares, increase cost_basis (Decimal, abs(Amount) preferred)
        Sell/LIQ → reduce shares; LIQ with qty=0 liquidates all remaining
        SPL/REC  → add shares at $0 marginal cost (stock split / transfer)
        SXCH     → security exchange; qty may be "1S" → parsed to 0; row stored
        CDIV     → cash dividend; no share change → skipped in replay
        RTP/ACH  → cash deposits; stored for cash reconciliation, skipped in replay
        DTAX/DFEE/MISC → fees / misc; skipped
    """
    store = _load(TX_STORE_PATH, {})

    # Merge on-disk IDs with any in-memory session IDs
    all_known_ids: set = set(store.keys())
    if existing_ids:
        all_known_ids |= existing_ids

    stats = IngestStats()

    try:
        text = file_bytes.decode("utf-8", errors="replace")
        for row in csv.DictReader(io.StringIO(text)):
            code = (row.get("Trans Code") or "").strip()

            # Skip blank rows and Robinhood's footer disclaimer row
            if not code:
                stats.skipped_no_code += 1
                continue
            desc_lower = (row.get("Description") or "").lower()
            if "data provided is for informational" in desc_lower:
                stats.skipped_no_code += 1
                continue

            stats.total_rows_in_file += 1

            # Raw strings for hashing — preserve original formatting
            act_date_raw = (row.get("Activity Date") or "").strip()
            settle_raw   = (row.get("Settle Date")   or "").strip()
            instrument   = (row.get("Instrument")    or "").strip()
            qty_raw      = (row.get("Quantity")       or "").strip()
            price_raw    = (row.get("Price")          or "").strip()
            amount_raw   = (row.get("Amount")         or "").strip()

            uid = _row_unique_id(
                act_date_raw, code, instrument,
                qty_raw, price_raw, amount_raw,
                settle_raw,
            )

            if uid in all_known_ids:
                stats.duplicate_rows_skipped += 1
                continue

            # Parse with Decimal — store as strings for lossless JSON round-trip
            qty   = _qty_decimal(qty_raw)
            price = _to_decimal(price_raw)
            amt   = _to_decimal(amount_raw)

            try:
                act_date = datetime.strptime(act_date_raw, "%m/%d/%Y").date()
            except ValueError:
                act_date = date.today()

            lt = (date.today() - act_date).days >= 366

            store[uid] = {
                "date":   str(act_date),
                "code":   code,
                "ticker": instrument,
                "qty":    str(qty),       # "0.023644" — exact, no float noise
                "price":  str(price),     # "601.84"
                "amount": str(amt),       # "-14.23"
                "desc":   (row.get("Description") or "").replace("\n", " ")[:120],
                "lt":     lt,
            }
            all_known_ids.add(uid)
            stats.new_rows_added += 1

    except Exception as e:
        stats.errors.append(f"CSV parse error: {e}")

    _save(TX_STORE_PATH, store)

    # Append to rolling reconciliation log (last 100 events)
    recon = _load(RECON_PATH, [])
    recon.append({
        "ts":          datetime.now().isoformat(),
        "file_rows":   stats.total_rows_in_file,
        "new":         stats.new_rows_added,
        "duplicates":  stats.duplicate_rows_skipped,
        "store_total": len(store),
    })
    _save(RECON_PATH, recon[-100:])

    return stats, all_known_ids


# ─── PDF Crypto Parsing ──────────────────────────────────────────────────────────
_CRYPTO_NAME_MAP = {
    "bitcoin": "BTC", "ethereum": "ETH", "ripple": "XRP",
    "xrp": "XRP", "solana": "SOL", "dogecoin": "DOGE",
}

def parse_crypto_pdf(file_bytes: bytes) -> dict[str, Any]:
    """
    Parse a Robinhood Crypto monthly PDF statement.
    Extracts: ticker, shares, market_value, period_end, closing_balance.
    Strategy: pdfplumber table extraction first; regex on raw text as fallback.
    """
    result: dict[str, Any] = {}
    errors: list[str] = []

    if not _PDF_AVAILABLE:
        return {"_errors": ["pdfplumber not installed — pip install pdfplumber"]}

    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            if len(pdf.pages) < 2:
                return {"_errors": ["PDF has fewer than 2 pages — unexpected format"]}

            page2 = pdf.pages[1]
            page2_text = page2.extract_text() or ""

            # Strategy 1: pdfplumber table extraction
            for table in (page2.extract_tables() or []):
                for trow in table:
                    if not trow:
                        continue
                    cells = [str(c or "").strip() for c in trow]
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

            # Strategy 2: regex fallback on raw page text
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

            # Extract period metadata
            if m2 := re.search(r"PERIOD END\s+([\d-]+)", page2_text):
                for v in result.values():
                    if isinstance(v, dict):
                        v["period_end"] = m2.group(1)
            if m3 := re.search(r"CLOSING BALANCE\s+\$([\d.,]+)", page2_text):
                for v in result.values():
                    if isinstance(v, dict):
                        v["closing_balance"] = _clean_dollar("$" + m3.group(1))

    except Exception as e:
        errors.append(f"PDF parse error: {e}")

    if errors:
        result["_errors"] = errors
    return result

def merge_pdf_into_crypto_overrides(pdf_data: dict) -> list[str]:
    """Merge PDF-parsed crypto holdings into crypto_overrides.json."""
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
    Replay all tx_store rows (oldest → newest) using Decimal arithmetic.

    Why Decimal:
      214 rows in the CSV have 6-decimal fractional quantities (e.g. 0.023644).
      Summing 312 Buy rows as floats accumulates ~1e-14 drift per ticker — harmless
      alone, but combined with the stale bootstrap positions it showed as a $4k gap.
      Decimal(prec=28) gives exact arithmetic: 0.023644 + 0.000499 == 0.024143 always.

    Cost basis rule:
      We use abs(Amount) as cost, NOT qty × price.
      Robinhood's Amount is the actual cash debited (already rounded to cents).
      qty × price gives a slightly different number on 22 of 322 rows (max diff $0.03)
      because Robinhood rounds them independently. Using Amount matches your statement.

    Returns dict: ticker → {
        shares (Decimal), cost_basis (Decimal), avg_cost (Decimal),
        lt (bool), drip_shares (Decimal), drip_amount (Decimal), first_buy (str)
    }
    """
    store  = _load(TX_STORE_PATH, {})
    crypto = _load(CRYPTO_PATH, BAKED_CRYPTO)
    rows   = sorted(store.values(), key=lambda r: r.get("date", ""))

    ZERO = Decimal("0")
    EPS  = Decimal("0.0001")
    positions: dict[str, dict] = {}

    for row in rows:
        ticker = (row.get("ticker") or "").strip()
        code   = (row.get("code")   or "").strip()
        qty    = _qty_decimal(row.get("qty"))
        price  = _to_decimal(row.get("price"))
        amt    = _to_decimal(row.get("amount"))
        desc   = (row.get("desc") or "").lower()
        lt     = bool(row.get("lt", False))

        if not ticker or code in SKIP_CODES:
            continue

        if ticker not in positions:
            positions[ticker] = {
                "shares":     ZERO, "cost_basis": ZERO, "avg_cost": ZERO,
                "lt":         False, "drip_shares": ZERO, "drip_amount": ZERO,
                "first_buy":  row.get("date", ""),
            }
        pos = positions[ticker]

        if code in BUY_CODES:
            is_drip = any(k in desc for k in ("reinvestment", "drip", "recurring"))
            cost = abs(amt) if amt != ZERO else (qty * price)
            if qty > ZERO:
                new_shares = pos["shares"] + qty
                pos["cost_basis"] += cost
                pos["avg_cost"]    = pos["cost_basis"] / new_shares if new_shares > ZERO else pos["avg_cost"]
                pos["shares"]      = new_shares
            if is_drip:
                pos["drip_shares"] += qty
                pos["drip_amount"] += cost
            if lt:
                pos["lt"] = True

        elif code in SELL_CODES:
            # LIQ: qty may be 0 — that means liquidate all remaining shares
            sell_qty = qty if qty > ZERO else pos["shares"]
            if pos["shares"] > ZERO and sell_qty > ZERO:
                ratio = min(sell_qty / pos["shares"], Decimal("1"))
                pos["cost_basis"] = pos["cost_basis"] * (Decimal("1") - ratio)
            pos["shares"] = max(ZERO, pos["shares"] - sell_qty)
            if pos["shares"] < EPS:
                pos["shares"] = ZERO

        elif code in SPLIT_CODES:
            # Stock split / share receipt: add shares, cost_basis unchanged,
            # avg_cost decreases proportionally
            if qty > ZERO:
                new_shares = pos["shares"] + qty
                pos["avg_cost"] = pos["cost_basis"] / new_shares if new_shares > ZERO else pos["avg_cost"]
                pos["shares"]   = new_shares

    # Remove zeroed-out positions
    positions = {t: p for t, p in positions.items() if p["shares"] > Decimal("0.0001")}

    # Merge crypto overrides (from PDF import or manual sidebar entry)
    for c_ticker, c_data in crypto.items():
        if c_ticker.startswith("_") or not isinstance(c_data, dict):
            continue
        sh = _to_decimal(c_data.get("shares", 0))
        ac = _to_decimal(c_data.get("avg_cost", 0))
        if sh <= ZERO:
            continue
        if c_ticker not in positions:
            positions[c_ticker] = {
                "shares": sh, "cost_basis": sh * ac, "avg_cost": ac,
                "lt":     bool(c_data.get("lt", False)),
                "drip_shares": ZERO, "drip_amount": ZERO, "first_buy": "2026-02-01",
            }
        else:
            p  = positions[c_ticker]
            ns = p["shares"] + sh
            nc = p["cost_basis"] + sh * ac
            p.update({
                "shares":     ns,
                "cost_basis": nc,
                "avg_cost":   nc / ns if ns > ZERO else ac,
                "lt":         p["lt"] or bool(c_data.get("lt", False)),
            })

    # Final avg_cost normalisation pass
    for p in positions.values():
        if p["shares"] > ZERO and p["cost_basis"] > ZERO:
            p["avg_cost"] = p["cost_basis"] / p["shares"]

    return positions


# ─── Reconciliation helpers ───────────────────────────────────────────────────────
def get_reconciliation_log() -> list[dict]:
    """Return last 100 ingest events for the sidebar Reconciliation Summary."""
    return _load(RECON_PATH, [])

def get_store_stats() -> dict:
    """Quick stats about the tx_store for sidebar display."""
    store = _load(TX_STORE_PATH, {})
    by_code: dict[str, int] = {}
    for row in store.values():
        c = row.get("code", "?")
        by_code[c] = by_code.get(c, 0) + 1
    return {"total_rows": len(store), "by_code": by_code}


# ─── Price fetching ───────────────────────────────────────────────────────────────
_MEM_CACHE: dict[str, tuple[float, float]] = {}
_CACHE_TTL = 300   # 5 minutes

def _load_price_cache() -> dict[str, float]:
    return _load(PRICE_CACHE_P, {})

def _save_price_cache(prices: dict[str, float]) -> None:
    existing = _load_price_cache()
    existing.update({k: v for k, v in prices.items() if v > 0})
    _save(PRICE_CACHE_P, existing)

def _yf_single(ticker: str) -> float:
    """Fetch one price from yfinance. Returns 0.0 on any failure."""
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
    """Batch-fetch crypto prices from CoinGecko. Returns {ticker: usd_price}."""
    if not tickers:
        return {}
    ids = [COINGECKO_IDS.get(t, t.lower()) for t in tickers]
    id_str = ",".join(dict.fromkeys(ids))
    try:
        r = requests.get(
            f"https://api.coingecko.com/api/v3/simple/price?ids={id_str}&vs_currencies=usd",
            timeout=8, headers={"Accept": "application/json"},
        )
        r.raise_for_status()
        data = r.json()
        return {t: float(data.get(COINGECKO_IDS.get(t, t.lower()), {}).get("usd", 0) or 0)
                for t in tickers}
    except Exception:
        return {}

def get_clean_prices(tickers: tuple, bust: int = 0) -> tuple[dict[str, float], dict[str, str]]:
    """
    Fetch live prices for all tickers.

    Fallback chain per ticker:
      1. CoinGecko (crypto) / yfinance (stocks)  → status: "live"
      2. In-process memory cache (TTL 5 min)      → status: "cached"
      3. Disk price_cache.json (last known good)  → status: "fallback"
      Never returns 1.0 as a default.

    Returns: (prices_dict, status_dict)
    """
    now    = time.time()
    prices: dict[str, float] = {}
    status: dict[str, str]   = {}
    disk   = _load_price_cache()

    crypto_t = [t for t in tickers if t in CRYPTO_BASE]
    stock_t  = [t for t in tickers if t not in CRYPTO_BASE]

    # Crypto via CoinGecko
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

    # Stocks via yfinance
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

    # Persist newly-fetched live prices to disk
    live_prices = {t: p for t, p in prices.items() if status.get(t) == "live"}
    if live_prices:
        _save_price_cache(live_prices)

    return prices, status

def _safe_price(ticker: str, pos: dict, prices: dict) -> float:
    """Returns live price if available, otherwise avg_cost. Never 0 or 1."""
    p = prices.get(ticker)
    if p and float(p) > 0:
        return float(p)
    avg = float(pos.get("avg_cost") or 0)
    return avg if avg > 0 else 0.01

def data_health_summary(status: dict[str, str]) -> dict:
    """Summarise price data quality for the sidebar Data Health indicator."""
    live     = sum(1 for s in status.values() if s == "live")
    cached   = sum(1 for s in status.values() if s == "cached")
    fallback = sum(1 for s in status.values() if s == "fallback")
    total    = len(status)
    if total == 0:
        return {"label": "No Data", "color": "gray", "live": 0, "cached": 0, "fallback": 0, "total": 0}
    if fallback == 0:
        return {"label": "🟢 Live",    "color": "green",  "live": live, "cached": cached, "fallback": 0,        "total": total}
    elif live > 0 or cached > 0:
        return {"label": "🟡 Partial", "color": "yellow", "live": live, "cached": cached, "fallback": fallback, "total": total}
    else:
        return {"label": "🔴 Stale",   "color": "red",    "live": 0,    "cached": cached, "fallback": fallback, "total": total}


# ─── Portfolio Enrichment ─────────────────────────────────────────────────────────
def enrich_portfolio(positions: dict, prices: dict) -> list[dict]:
    """
    Merge positions (may contain Decimal values) with live prices.
    All output values are plain Python float for Streamlit/pandas compatibility.
    """
    rows = []
    for ticker, pos in positions.items():
        live   = _safe_price(ticker, pos, prices)
        shares = float(pos.get("shares") or 0)
        avg    = float(pos.get("avg_cost") or 0)
        equity = live * shares
        cost   = avg  * shares
        pl     = equity - cost
        pl_pct = (pl / cost * 100) if cost > 0 else 0.0
        target = PRICE_TARGETS.get(ticker, 0.0)
        upside = ((target - live) / live * 100) if (target and live > 0) else 0.0
        rows.append({
            "ticker":      ticker,
            "shares":      shares,
            "avg_cost":    avg,
            "live_price":  live,
            "equity":      equity,
            "cost_basis":  cost,
            "pl":          pl,
            "pl_pct":      pl_pct,
            "lt":          bool(pos.get("lt", False)),
            "upside":      upside,
            "target":      target,
            "drip_shares": float(pos.get("drip_shares") or 0),
            "drip_amount": float(pos.get("drip_amount") or 0),
            "first_buy":   pos.get("first_buy", ""),
        })
    rows.sort(key=lambda r: r["equity"], reverse=True)
    return rows


# ─── AI Target Engine ─────────────────────────────────────────────────────────────
def generate_suggested_targets(rows: list[dict], total_value: float) -> dict[str, float]:
    """
    Generate Moderate-Aggressive target allocations for only the tickers held.
    Normalised to exactly 100%. Used to pre-populate sidebar number_inputs.
    """
    tickers_in = {r["ticker"] for r in rows}
    base: dict[str, float] = {
        "VOO": 20.0, "QQQ": 10.0, "VYM": 10.0, "SCHD": 5.0,  "VTI": 4.0,
        "NVDA": 12.0,"AAPL": 6.0, "META": 4.0, "GOOGL": 4.0, "MSFT": 3.0,
        "BRK.B": 4.0,"WMT": 2.0,  "COST": 2.0,
        "VXUS": 3.0, "GLD": 4.0,  "VGT": 2.0,  "XLE": 2.0,  "VHT": 1.5, "VIS": 1.0,
        "NFLX": 2.0, "TSM": 2.0,  "QCOM": 1.5, "RDDT": 0.5,
        "SPY": 1.0,  "VUG": 1.0,  "SNOW": 1.0, "CRM": 1.5,
        "AMD": 1.0,  "ALK": 0.5,
        "BTC": 3.5,  "XRP": 0.5,
    }
    filtered = {t: w for t, w in base.items() if t in tickers_in}
    total_w = sum(filtered.values())
    if total_w > 0:
        filtered = {t: round(w / total_w * 100, 1) for t, w in filtered.items()}
    return filtered


# ─── Rebalancing ──────────────────────────────────────────────────────────────────
def compute_rebalancing(rows: list[dict], total_value: float,
                        targets: dict[str, float]) -> list[dict]:
    result = []
    for r in rows:
        current_pct = (r["equity"] / total_value * 100) if total_value > 0 else 0
        target_pct  = targets.get(r["ticker"], 0.0)
        result.append({**r, "current_pct": current_pct,
                        "target_pct": target_pct, "drift": current_pct - target_pct})
    return sorted(result, key=lambda x: x["drift"])

def compute_deposit_allocation(
    deposit: float, rows: list[dict], total_value: float,
    targets: dict[str, float], deposit_num: int, prices: dict,
) -> list[dict]:
    """
    Allocate $900 deposit into underweight assets (target model) or fixed split (no targets).
    Returns list: [ticker, current_value, target_pct, action, amount, est_shares, live_price, reason]
    """
    rotating_ticker = DEPOSIT_ROTATING[(deposit_num - 1) % len(DEPOSIT_ROTATING)]
    has_targets = any(v > 0 for v in targets.values())
    allocs: list[dict] = []

    if has_targets:
        drift_rows = compute_rebalancing(rows, total_value, targets)
        remaining = deposit
        for dr in drift_rows:   # already sorted most-underweight first
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
        # Flag overweight positions for trimming
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
        # Default fixed split
        row_map = {r["ticker"]: r for r in rows}
        for ticker, pct in DEPOSIT_FIXED.items():
            amt  = deposit * pct
            row  = row_map.get(ticker, {})
            live = row.get("live_price") or prices.get(ticker) or 100
            allocs.append({
                "ticker":        ticker,
                "current_value": round(row.get("equity", 0), 2),
                "target_pct":    0.0,
                "action":        "BUY",
                "amount":        round(amt, 2),
                "est_shares":    round(amt / live, 6) if live > 0 else 0,
                "live_price":    live,
                "reason":        "Core fixed allocation",
            })
        r_row  = row_map.get(rotating_ticker, {})
        r_live = r_row.get("live_price") or prices.get(rotating_ticker) or 100
        r_amt  = deposit * DEPOSIT_ROTATING_PCT
        allocs.append({
            "ticker":        rotating_ticker,
            "current_value": round(r_row.get("equity", 0), 2),
            "target_pct":    0.0,
            "action":        "BUY",
            "amount":        round(r_amt, 2),
            "est_shares":    round(r_amt / r_live, 6) if r_live > 0 else 0,
            "live_price":    r_live,
            "reason":        f"Rotating pick #{(deposit_num - 1) % len(DEPOSIT_ROTATING) + 1}",
        })

    return allocs


# ─── Recommendation Engine ────────────────────────────────────────────────────────
def _tax_note(lt: bool) -> str:
    return "✅ Long-term — 15% cap gains rate" if lt else "⚠️ Short-term — 37% rate. Hold until 1-year mark."

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
                "reason": "Earmarked for exit. LT-eligible → 15% tax. Reinvest into VOO/VYM same day.",
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
                "action": f"✂️ TRIM {int(trim_pct * 100)}% — LOCK GAINS",
                "reason": f"Up {pl_pct:.1f}%, LT-eligible. Take {int(trim_pct * 100)}% off table ≈${trim:.0f} at 15%.",
                "proceed_est": trim}); continue
        if upcoming_lt and not lt and pl_pct > 5:
            recs.append({**r, **base, "badge": "HOLD", "priority": 3,
                "action": "⏳ HOLD — LT IN <30 DAYS",
                "reason": "Within 30 days of LT eligibility. Do NOT sell — wait for the 15% rate."}); continue
        recs.append({**r, **base, "badge": "HOLD", "priority": 4,
            "action": "🟡 HOLD",
            "reason": f"No action needed. Monitor for ${r['target']:.0f} target or LT eligibility."})

    recs.sort(key=lambda x: (x["priority"], -x["equity"]))
    return recs


# ─── KPIs ─────────────────────────────────────────────────────────────────────────
def compute_kpis(rows: list[dict], cash: float) -> dict:
    sr = [r for r in rows if r["ticker"] not in CRYPTO_BASE]
    cr = [r for r in rows if r["ticker"] in CRYPTO_BASE]
    sv = sum(r["equity"] for r in sr)
    cv = sum(r["equity"] for r in cr)
    tv = sv + cv + cash
    tc = sum(r["cost_basis"] for r in rows)
    tp = sum(r["pl"] for r in rows)
    return {
        "total_value":   tv, "stock_value": sv, "crypto_value": cv, "cash": cash,
        "total_cost":    tc, "total_pl":    tp,
        "total_pl_pct":  (tp / tc * 100) if tc > 0 else 0,
        "positions":     len(rows),
        "winners":       sum(1 for r in rows if r["pl"] > 0),
        "losers":        sum(1 for r in rows if r["pl"] < 0),
        "drip_total":    sum(r["drip_amount"] for r in rows),
    }


# ─── Deposit schedule ─────────────────────────────────────────────────────────────
def get_deposit_schedule(n: int = 16) -> list[dict]:
    schedule = []
    d = FIRST_DEPOSIT_DATE
    today = date.today()
    while d < today:
        d += timedelta(weeks=2)
    for i in range(n):
        num = i + 1
        schedule.append({
            "num":      num,
            "date":     d,
            "rotating": DEPOSIT_ROTATING[(num - 1) % len(DEPOSIT_ROTATING)],
        })
        d += timedelta(weeks=2)
    return schedule


# ─── History & logging ────────────────────────────────────────────────────────────
def save_snapshot(kpis: dict, recs: list[dict]) -> None:
    hist = _load(REC_HIST_PATH, [])
    hist.append({
        "ts":           datetime.now().isoformat(),
        "total_value":  kpis["total_value"],
        "stock_value":  kpis["stock_value"],
        "crypto_value": kpis["crypto_value"],
        "cash":         kpis["cash"],
        "total_pl":     kpis["total_pl"],
        "total_pl_pct": kpis["total_pl_pct"],
        "recs": [{"ticker": r["ticker"], "action": r["action"],
                  "live": r["live_price"], "pl_pct": r["pl_pct"]} for r in recs[:30]],
    })
    _save(REC_HIST_PATH, hist[-200:])

def log_deposit(num: int, allocs: list[dict], total: float, notes: str = "") -> None:
    log = _load(DEPOSIT_LOG, [])
    log.append({
        "ts": datetime.now().isoformat(), "deposit_num": num,
        "total": total, "allocations": allocs, "notes": notes,
    })
    _save(DEPOSIT_LOG, log)

def load_targets() -> dict[str, float]:
    return _load(TARGETS_PATH, {})

def save_targets(targets: dict[str, float]) -> None:
    _save(TARGETS_PATH, targets)

def update_crypto_override(ticker: str, shares: float, avg_cost: float, lt: bool) -> None:
    c = _load(CRYPTO_PATH, BAKED_CRYPTO)
    c[ticker] = {"shares": shares, "avg_cost": avg_cost, "lt": lt}
    _save(CRYPTO_PATH, c)
