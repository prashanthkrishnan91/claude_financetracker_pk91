"""
Portfolio War Room — Data Engine v10.0
All data processing, portfolio computation, recommendation logic, and deposit planning.
Zero UI code in this file.
"""

import csv
import hashlib
import io
import json
import os
import time
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd
import requests
import yfinance as yf

# ─── Paths ────────────────────────────────────────────────────────────────────
DATA_DIR = os.path.dirname(os.path.abspath(__file__))
TX_STORE_PATH = os.path.join(DATA_DIR, "tx_store.json")
CRYPTO_PATH = os.path.join(DATA_DIR, "crypto_overrides.json")
REC_HIST_PATH = os.path.join(DATA_DIR, "rec_history.json")
DEPOSIT_LOG_PATH = os.path.join(DATA_DIR, "deposit_log.json")
TARGETS_PATH = os.path.join(DATA_DIR, "targets.json")

# ─── Constants ─────────────────────────────────────────────────────────────────
ROBINHOOD_CASH_DEFAULT = 1042.17
CASH_BUFFER = 50.0
DEPOSIT_AMOUNT = 900.0
FIRST_DEPOSIT_DATE = date(2026, 4, 3)

# Biweekly deposit allocations
DEPOSIT_FIXED = {
    "NVDA": 0.28,
    "VOO":  0.22,
    "VYM":  0.17,
    "QQQ":  0.17,
}
DEPOSIT_ROTATING = ["META", "GOOGL", "AAPL", "MSFT", "COST", "TSM", "CRM", "NFLX"]
DEPOSIT_ROTATING_PCT = 0.16

# Classification lists
FOREVER_HOLD = {"VYM", "SCHD", "VTI"}
DCA_ALWAYS   = {"VOO", "QQQ"}
SELL_LIST    = {"VTV", "VEA", "VWO", "BND"}
SELL_PENDING = {"SPY", "VUG"}
IPO_HOLDS    = {"RDDT", "BLSH", "KLAR", "STUB"}
CRYPTO_TICKERS = {"BTC", "ETH", "XRP", "SOL", "DOGE"}

# Key LT-eligibility dates (from progress log / CSV analysis)
LT_DATES: dict[str, date] = {
    "SPY":  date(2026, 5, 20),
    "VUG":  date(2026, 7, 15),
    "BLSH": date(2026, 8, 14),
    "KLAR": date(2026, 9, 11),
    "STUB": date(2026, 9, 18),
    "GOOGL_LOT": date(2026, 12, 15),
    "TSM_LOT":   date(2026, 11, 6),
}

# Analyst / model price targets (updated Apr 2026)
PRICE_TARGETS: dict[str, float] = {
    "NVDA": 175.0, "AAPL": 270.0, "GOOGL": 220.0, "META": 700.0,
    "MSFT": 500.0, "AMZN": 260.0, "NFLX": 1100.0, "COST": 1050.0,
    "VOO": 650.0,  "QQQ": 650.0,  "VYM": 165.0,   "SCHD": 35.0,
    "GLD": 450.0,  "VTI": 310.0,  "TSM": 230.0,   "CRM": 350.0,
    "QCOM": 200.0, "WMT": 115.0,  "VGT": 750.0,   "XLE": 95.0,
    "VHT": 310.0,  "VXUS": 90.0,  "BRK.B": 580.0,
    "BTC-USD": 120000.0, "XRP-USD": 4.00,
}

# ─── Persistence helpers ───────────────────────────────────────────────────────
def _load(path: str, default: Any) -> Any:
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return default

def _save(path: str, obj: Any) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2, default=str)
    except Exception:
        pass

# ─── Baked-in bootstrap data (condensed — full 585-tx fingerprint store) ──────
# We store just enough to seed an empty environment. Full store lives on disk.
BAKED_CRYPTO_OVERRIDES = {
    "BTC": {"shares": 0.03432981, "avg_cost": 52800.0, "lt": True},
    "XRP": {"shares": 1.066,      "avg_cost": 0.68,    "lt": False},
}

# Minimal bootstrap positions derived from the full 585-tx replay (v9.1 verified)
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
    "BMWYY": {"shares": 1.0,     "avg_cost": 39.72,   "lt": True},
    "VTI":   {"shares": 0.7521,  "avg_cost": 255.24,  "lt": True},
}

# ─── Bootstrap: seed disk if missing ──────────────────────────────────────────
def bootstrap_if_needed():
    """Write bootstrap data to disk if tx_store doesn't exist yet."""
    if not os.path.exists(TX_STORE_PATH) or os.path.getsize(TX_STORE_PATH) < 10:
        # Create a synthetic tx_store from bootstrap positions
        store = {}
        for ticker, pos in BOOTSTRAP_POSITIONS.items():
            fp = hashlib.sha1(f"bootstrap|{ticker}".encode()).hexdigest()
            store[fp] = {
                "date": "2024-03-04",
                "code": "Buy",
                "ticker": ticker,
                "qty": pos["shares"],
                "price": pos["avg_cost"],
                "amount": -(pos["shares"] * pos["avg_cost"]),
                "desc": "Bootstrap",
                "lt": pos["lt"],
            }
        _save(TX_STORE_PATH, store)

    if not os.path.exists(CRYPTO_PATH):
        _save(CRYPTO_PATH, BAKED_CRYPTO_OVERRIDES)

# ─── CSV Ingestion ─────────────────────────────────────────────────────────────
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

def _clean_dollar(val: str) -> float:
    if not val:
        return 0.0
    return float(str(val).replace("$", "").replace(",", "").replace("(", "-").replace(")", "").strip() or 0)

def _clean_qty(val: str) -> float:
    if not val:
        return 0.0
    try:
        return float(str(val).replace(",", "").strip())
    except Exception:
        return 0.0

def ingest_csv(file_bytes: bytes) -> tuple[int, int, list[str]]:
    """
    Parse Robinhood CSV and add new rows to tx_store.
    Returns (new_rows, skipped_rows, errors).
    """
    store = _load(TX_STORE_PATH, {})
    new_rows = 0
    skipped = 0
    errors: list[str] = []

    try:
        text = file_bytes.decode("utf-8", errors="replace")
        reader = csv.DictReader(io.StringIO(text), quoting=csv.QUOTE_ALL)
        for i, row in enumerate(reader):
            # Skip footer/disclaimer rows
            instrument = (row.get("Instrument") or "").strip()
            code = (row.get("Trans Code") or "").strip()
            if not code or code.lower() in ("trans code", ""):
                continue
            if "data provided" in (row.get("Description") or "").lower():
                continue

            fp = _fingerprint(row)
            if fp in store:
                skipped += 1
                continue

            qty   = _clean_qty(row.get("Quantity", ""))
            price = _clean_dollar(row.get("Price", ""))
            amt   = _clean_dollar(row.get("Amount", ""))

            # Determine LT eligibility from activity date
            try:
                act_date = datetime.strptime(row.get("Activity Date", ""), "%m/%d/%Y").date()
            except Exception:
                act_date = date.today()

            lt_eligible = (date.today() - act_date).days >= 366

            store[fp] = {
                "date": str(act_date),
                "code": code,
                "ticker": instrument,
                "qty": qty,
                "price": price,
                "amount": amt,
                "desc": (row.get("Description") or "")[:120],
                "lt": lt_eligible,
            }
            new_rows += 1

    except Exception as e:
        errors.append(f"CSV parse error: {e}")

    _save(TX_STORE_PATH, store)
    return new_rows, skipped, errors

# ─── Portfolio Recompute ────────────────────────────────────────────────────────
def recompute_portfolio() -> dict[str, dict]:
    """
    Replay all tx_store rows oldest→newest to build current positions.
    Returns dict: ticker → {shares, avg_cost, cost_basis, lt, drip_shares, drip_amount}
    """
    store = _load(TX_STORE_PATH, {})
    crypto = _load(CRYPTO_PATH, BAKED_CRYPTO_OVERRIDES)

    # Sort by date
    rows = sorted(store.values(), key=lambda r: r.get("date", ""))

    positions: dict[str, dict] = {}

    for row in rows:
        ticker = row.get("ticker", "").strip()
        code   = row.get("code", "").strip()
        qty    = float(row.get("qty", 0) or 0)
        price  = float(row.get("price", 0) or 0)
        amt    = float(row.get("amount", 0) or 0)
        desc   = (row.get("desc") or "").lower()
        lt     = bool(row.get("lt", False))

        if not ticker or code in ("ACH", "RTP", "JNLS", ""):
            continue

        # Initialise position
        if ticker not in positions:
            positions[ticker] = {
                "shares": 0.0,
                "cost_basis": 0.0,
                "avg_cost": 0.0,
                "lt": False,
                "drip_shares": 0.0,
                "drip_amount": 0.0,
                "first_buy": row.get("date", ""),
            }

        pos = positions[ticker]

        if code in ("Buy",):
            is_drip = "reinvestment" in desc or "drip" in desc or "recurring" in desc
            cost = abs(amt) if amt != 0 else (qty * price)
            if is_drip:
                pos["drip_shares"] += qty
                pos["drip_amount"] += cost

            if pos["shares"] + qty > 0:
                pos["cost_basis"] += cost
                pos["avg_cost"] = pos["cost_basis"] / (pos["shares"] + qty)
            pos["shares"] += qty

        elif code in ("Sell", "STO", "BTC_CRYPTO"):
            proceeds = abs(amt) if amt != 0 else (qty * price)
            # Reduce cost basis proportionally
            if pos["shares"] > 0 and qty > 0:
                ratio = qty / pos["shares"]
                pos["cost_basis"] *= (1 - ratio)
            pos["shares"] -= qty
            if pos["shares"] < 0.0001:
                pos["shares"] = 0.0

        elif code in ("CDIV",):
            # Cash dividend — tracked but no share change
            pass

        elif code in ("REC",):
            # Stock split / transfer
            pos["shares"] += qty

        # Track LT: if ANY buy is LT eligible, flag the position
        if code == "Buy" and lt:
            pos["lt"] = True

    # Remove zero-share positions
    positions = {t: p for t, p in positions.items() if p["shares"] > 0.0001}

    # Merge crypto overrides
    for c_ticker, c_data in crypto.items():
        yf_ticker = f"{c_ticker}-USD" if not c_ticker.endswith("-USD") else c_ticker
        display_ticker = c_ticker.replace("-USD", "")
        if display_ticker not in positions:
            positions[display_ticker] = {
                "shares": c_data["shares"],
                "cost_basis": c_data["shares"] * c_data["avg_cost"],
                "avg_cost": c_data["avg_cost"],
                "lt": c_data.get("lt", False),
                "drip_shares": 0.0,
                "drip_amount": 0.0,
                "first_buy": "2026-02-01",
            }
        else:
            # Merge
            existing = positions[display_ticker]
            total_shares = existing["shares"] + c_data["shares"]
            total_cost   = existing["cost_basis"] + c_data["shares"] * c_data["avg_cost"]
            existing["shares"]     = total_shares
            existing["cost_basis"] = total_cost
            existing["avg_cost"]   = total_cost / total_shares if total_shares > 0 else c_data["avg_cost"]
            existing["lt"] = existing["lt"] or c_data.get("lt", False)

    # Fix avg_cost for each position
    for t, p in positions.items():
        if p["shares"] > 0:
            p["avg_cost"] = p["cost_basis"] / p["shares"]

    return positions

# ─── Price Fetching ─────────────────────────────────────────────────────────────
_price_cache: dict[str, tuple[float, float]] = {}  # ticker → (price, timestamp)
CACHE_TTL = 300  # 5 min


def fetch_prices(tickers: tuple, bust: int = 0) -> dict[str, float]:
    """
    Fetch live prices for all tickers.
    Crypto tickers (BTC, XRP, etc.) fetched from CoinGecko.
    Stocks/ETFs from yfinance.
    bust parameter forces cache bypass.
    """
    now = time.time()
    prices: dict[str, float] = {}

    stock_tickers = []
    crypto_tickers = []

    for t in tickers:
        t_upper = t.upper()
        base = t_upper.replace("-USD", "")
        if base in CRYPTO_TICKERS or t_upper.endswith("-USD"):
            crypto_tickers.append(base)
        else:
            stock_tickers.append(t_upper)

    # ── Stocks via yfinance ──
    for t in stock_tickers:
        cache_key = f"{t}_{bust}"
        cached = _price_cache.get(cache_key)
        if cached and (now - cached[1]) < CACHE_TTL and bust == 0:
            prices[t] = cached[0]
            continue
        try:
            info = yf.Ticker(t).fast_info
            p = float(info.get("last_price") or info.get("regularMarketPrice") or 0)
            if p > 0:
                prices[t] = p
                _price_cache[cache_key] = (p, now)
        except Exception:
            pass

    # ── Crypto via CoinGecko ──
    COINGECKO_IDS = {
        "BTC": "bitcoin", "ETH": "ethereum", "XRP": "ripple",
        "SOL": "solana", "DOGE": "dogecoin",
    }
    if crypto_tickers:
        ids = [COINGECKO_IDS.get(c, c.lower()) for c in crypto_tickers]
        id_str = ",".join(ids)
        try:
            url = f"https://api.coingecko.com/api/v3/simple/price?ids={id_str}&vs_currencies=usd"
            r = requests.get(url, timeout=8)
            data = r.json()
            for c_ticker in crypto_tickers:
                cg_id = COINGECKO_IDS.get(c_ticker, c_ticker.lower())
                p = data.get(cg_id, {}).get("usd", 0)
                if p:
                    prices[c_ticker] = float(p)
        except Exception:
            pass

    return prices


def _safe_price(ticker: str, pos: dict, prices: dict) -> float:
    """Return live price or fall back to avg_cost."""
    base = ticker.replace("-USD", "")
    p = prices.get(ticker) or prices.get(base) or prices.get(f"{ticker}-USD")
    return float(p) if p else float(pos.get("avg_cost", 0))


# ─── Portfolio Enrichment ───────────────────────────────────────────────────────
def enrich_portfolio(positions: dict, prices: dict) -> list[dict]:
    """
    Merge positions with live prices.
    Returns list of enriched row dicts sorted by equity descending.
    """
    rows = []
    for ticker, pos in positions.items():
        live = _safe_price(ticker, pos, prices)
        shares = float(pos.get("shares", 0))
        avg    = float(pos.get("avg_cost", 0))
        equity = live * shares
        cost   = avg  * shares
        pl     = equity - cost
        pl_pct = (pl / cost * 100) if cost > 0 else 0.0
        target = PRICE_TARGETS.get(ticker) or PRICE_TARGETS.get(f"{ticker}-USD")
        upside = ((target - live) / live * 100) if (target and live > 0) else 0.0

        rows.append({
            "ticker":     ticker,
            "shares":     shares,
            "avg_cost":   avg,
            "live_price": live,
            "equity":     equity,
            "cost_basis": cost,
            "pl":         pl,
            "pl_pct":     pl_pct,
            "lt":         bool(pos.get("lt", False)),
            "upside":     upside,
            "target":     target or 0.0,
            "drip_shares": float(pos.get("drip_shares", 0)),
            "drip_amount": float(pos.get("drip_amount", 0)),
            "first_buy":  pos.get("first_buy", ""),
        })

    rows.sort(key=lambda r: r["equity"], reverse=True)
    return rows


# ─── Recommendation Engine ──────────────────────────────────────────────────────
def _tax_note(lt: bool) -> str:
    return "✅ Long-term (15% tax)" if lt else "⚠️ Short-term (37% tax) — hold until LT"

def generate_recommendations(rows: list[dict]) -> list[dict]:
    """
    Full priority-order recommendation engine.
    Fully dynamic — recalculated every time based on live prices.
    """
    recs = []
    today = date.today()

    for r in rows:
        t       = r["ticker"]
        pl_pct  = r["pl_pct"]
        lt      = r["lt"]
        upside  = r["upside"]
        equity  = r["equity"]
        live    = r["live_price"]

        # Check upcoming LT eligibility (within 30 days)
        upcoming_lt = False
        for key, lt_date in LT_DATES.items():
            if key.startswith(t) and (lt_date - today).days <= 30:
                upcoming_lt = True

        # ── Priority 0: Forced sells (LT-eligible) ──
        if t in SELL_LIST and lt:
            recs.append({**r,
                "action": "🔴 SELL NOW",
                "priority": 0,
                "reason": f"Position earmarked for tax-efficient exit. LT-eligible → pay 15% not 37%. Reinvest proceeds into VOO/VYM same day.",
                "proceed_est": equity,
                "tax_note": _tax_note(lt),
                "badge": "SELL",
            })
            continue

        if t in SELL_PENDING and lt:
            recs.append({**r,
                "action": "🔴 SELL NOW",
                "priority": 0,
                "reason": f"Fund upgrade: swap {t} → VOO/QQQ for lower expense ratio. LT-eligible. Do same-day swap to avoid wash-sale risk (different fund).",
                "proceed_est": equity,
                "tax_note": _tax_note(lt),
                "badge": "SELL",
            })
            continue

        # ── Priority 1: Big loss review ──
        if pl_pct < -20:
            recs.append({**r,
                "action": "🚨 REVIEW — BIG LOSS",
                "priority": 1,
                "reason": f"Down {pl_pct:.1f}%. Evaluate: Is the thesis still intact? If yes, this is a DCA opportunity. If fundamentals broke, consider tax-loss harvest.",
                "proceed_est": 0,
                "tax_note": _tax_note(lt),
                "badge": "REVIEW",
            })
            continue

        # ── Priority 2: Forever holds ──
        if t in FOREVER_HOLD:
            recs.append({**r,
                "action": "♾ HOLD FOREVER — DRIP ON",
                "priority": 2,
                "reason": f"Core dividend compounder. Never sell. Keep DRIP enabled. Re-buy every deposit.",
                "proceed_est": 0,
                "tax_note": _tax_note(lt),
                "badge": "HOLD",
            })
            continue

        # ── Priority 2: DCA always ──
        if t in DCA_ALWAYS:
            action_str = "📈 DCA EVERY DEPOSIT"
            recs.append({**r,
                "action": action_str,
                "priority": 2,
                "reason": f"Index core. Buy every single deposit regardless of price. Time in market > timing the market.",
                "proceed_est": 0,
                "tax_note": _tax_note(lt),
                "badge": "BUY",
            })
            continue

        # ── Priority 2: Strong dip buy ──
        if -20 <= pl_pct < -8 and upside > 20:
            recs.append({**r,
                "action": "💎 STRONG BUY — DIP",
                "priority": 2,
                "reason": f"Down {pl_pct:.1f}% with {upside:.0f}% upside to target ${r['target']:.0f}. Add aggressively with next deposit.",
                "proceed_est": 0,
                "tax_note": _tax_note(lt),
                "badge": "BUY",
            })
            continue

        # ── Priority 2: Crypto accumulate ──
        if t in CRYPTO_TICKERS and upside > 25:
            recs.append({**r,
                "action": "🚀 ACCUMULATE — CRYPTO",
                "priority": 2,
                "reason": f"{upside:.0f}% upside to ${r['target']:,.0f} target. Add small positions ($50-100) on dips only. Never more than 10% of portfolio.",
                "proceed_est": 0,
                "tax_note": _tax_note(lt),
                "badge": "BUY",
            })
            continue

        # ── Priority 2: General accumulate ──
        if upside > 20:
            recs.append({**r,
                "action": "🟢 ACCUMULATE",
                "priority": 2,
                "reason": f"{upside:.0f}% upside to analyst target ${r['target']:.0f}. Good entry. Add on next deposit cycle.",
                "proceed_est": 0,
                "tax_note": _tax_note(lt),
                "badge": "BUY",
            })
            continue

        # ── Priority 3: IPO trim ──
        if t in IPO_HOLDS and lt:
            trim_est = equity * 0.25
            recs.append({**r,
                "action": "✂️ TRIM 25% — IPO NOW LT",
                "priority": 3,
                "reason": f"IPO position is now LT-eligible. Trim 25% (≈${trim_est:.0f}) to lock in gains at 15% tax. Keep remaining for long run.",
                "proceed_est": trim_est,
                "tax_note": _tax_note(lt),
                "badge": "TRIM",
            })
            continue

        # ── Priority 3: Profit trim ──
        if pl_pct > 20 and lt:
            trim_pct = 0.25 if t in CRYPTO_TICKERS or t in IPO_HOLDS else 0.20
            trim_est = equity * trim_pct
            recs.append({**r,
                "action": f"✂️ TRIM {int(trim_pct*100)}% — LOCK GAINS",
                "priority": 3,
                "reason": f"Up {pl_pct:.1f}% and LT-eligible. Take {int(trim_pct*100)}% off the table (≈${trim_est:.0f}) at the favorable 15% rate. Let the rest run.",
                "proceed_est": trim_est,
                "tax_note": _tax_note(lt),
                "badge": "TRIM",
            })
            continue

        # ── Priority 4: Upcoming LT — warn ──
        if upcoming_lt and not lt and pl_pct > 5:
            recs.append({**r,
                "action": "⏳ HOLD — LT SOON",
                "priority": 3,
                "reason": f"Within 30 days of LT eligibility. DO NOT sell yet — wait for LT to cut tax from 37% to 15%.",
                "proceed_est": 0,
                "tax_note": _tax_note(lt),
                "badge": "HOLD",
            })
            continue

        # ── Priority 4: Hold ──
        recs.append({**r,
            "action": "🟡 HOLD",
            "priority": 4,
            "reason": f"No action needed. Monitor for target ${r['target']:.0f} or LT eligibility before any trim.",
            "proceed_est": 0,
            "tax_note": _tax_note(lt),
            "badge": "HOLD",
        })

    recs.sort(key=lambda x: (x["priority"], -x["equity"]))
    return recs


# ─── Rebalancing / Drift Engine ─────────────────────────────────────────────────
def compute_rebalancing(rows: list[dict], total_value: float, targets: dict[str, float]) -> list[dict]:
    """
    Compute current % allocation vs target %, and drift.
    Returns sorted list of drift rows.
    """
    result = []
    for r in rows:
        ticker = r["ticker"]
        current_pct = (r["equity"] / total_value * 100) if total_value > 0 else 0
        target_pct  = targets.get(ticker, 0.0)
        drift       = current_pct - target_pct
        result.append({
            **r,
            "current_pct": current_pct,
            "target_pct":  target_pct,
            "drift":       drift,
        })
    result.sort(key=lambda x: x["drift"])  # Most underweight first
    return result


def compute_deposit_allocation(deposit: float, rows: list[dict], total_value: float,
                                targets: dict[str, float], deposit_num: int,
                                prices: dict) -> list[dict]:
    """
    Allocate deposit to underweight assets first using target model.
    Falls back to fixed DEPOSIT_FIXED allocation if no targets set.
    Returns list of allocation dicts.
    """
    # Rotating pick
    rotating_idx = (deposit_num - 1) % len(DEPOSIT_ROTATING)
    rotating_ticker = DEPOSIT_ROTATING[rotating_idx]

    allocs = []

    # Check if user has set any targets
    has_targets = any(v > 0 for v in targets.values())

    if has_targets:
        # Target-based allocation: fill most underweight first
        drift_rows = compute_rebalancing(rows, total_value, targets)
        underweight = [r for r in drift_rows if r["drift"] < -2.0 and r["target_pct"] > 0]
        remaining = deposit
        for dr in underweight:
            needed_pct = -dr["drift"]
            alloc_amt  = min(remaining, total_value * needed_pct / 100)
            if alloc_amt < 10:
                continue
            live = dr["live_price"]
            est_shares = alloc_amt / live if live > 0 else 0
            allocs.append({
                "ticker": dr["ticker"],
                "amount": alloc_amt,
                "est_shares": est_shares,
                "live_price": live,
                "reason": f"Underweight by {-dr['drift']:.1f}% vs target",
            })
            remaining -= alloc_amt
            if remaining < 10:
                break
    else:
        # Default fixed allocation
        for ticker, pct in DEPOSIT_FIXED.items():
            amt = deposit * pct
            live = prices.get(ticker, 0) or 1
            allocs.append({
                "ticker": ticker,
                "amount": amt,
                "est_shares": amt / live if live > 0 else 0,
                "live_price": live,
                "reason": "Core allocation",
            })
        # Rotating pick
        rotating_amt = deposit * DEPOSIT_ROTATING_PCT
        live = prices.get(rotating_ticker, 0) or 1
        allocs.append({
            "ticker": rotating_ticker,
            "amount": rotating_amt,
            "est_shares": rotating_amt / live if live > 0 else 0,
            "live_price": live,
            "reason": f"Rotating pick #{rotating_idx+1}",
        })

    return allocs


# ─── Deposit Calendar ───────────────────────────────────────────────────────────
def get_deposit_schedule(n: int = 16) -> list[dict]:
    """Generate upcoming biweekly deposit dates starting from FIRST_DEPOSIT_DATE."""
    schedule = []
    d = FIRST_DEPOSIT_DATE
    today = date.today()
    # Fast-forward if first deposit is in the past
    while d < today:
        d += timedelta(weeks=2)
    for i in range(n):
        dep_num = i + 1
        rotating_idx = (dep_num - 1) % len(DEPOSIT_ROTATING)
        schedule.append({
            "num":     dep_num,
            "date":    d,
            "rotating": DEPOSIT_ROTATING[rotating_idx],
        })
        d += timedelta(weeks=2)
    return schedule


# ─── Portfolio KPIs ─────────────────────────────────────────────────────────────
def compute_kpis(rows: list[dict], cash: float) -> dict:
    crypto_tickers_set = {t for t in CRYPTO_TICKERS}
    stock_rows  = [r for r in rows if r["ticker"] not in crypto_tickers_set]
    crypto_rows = [r for r in rows if r["ticker"] in crypto_tickers_set]

    stock_value  = sum(r["equity"] for r in stock_rows)
    crypto_value = sum(r["equity"] for r in crypto_rows)
    total_value  = stock_value + crypto_value + cash
    total_cost   = sum(r["cost_basis"] for r in rows)
    total_pl     = sum(r["pl"] for r in rows)
    total_pl_pct = (total_pl / total_cost * 100) if total_cost > 0 else 0

    winners = sum(1 for r in rows if r["pl"] > 0)
    losers  = sum(1 for r in rows if r["pl"] < 0)

    sell_recs  = sum(1 for r in rows if r.get("badge") == "SELL")
    buy_recs   = sum(1 for r in rows if r.get("badge") == "BUY")
    trim_recs  = sum(1 for r in rows if r.get("badge") == "TRIM")

    drip_total = sum(r["drip_amount"] for r in rows)

    return {
        "total_value":   total_value,
        "stock_value":   stock_value,
        "crypto_value":  crypto_value,
        "cash":          cash,
        "total_cost":    total_cost,
        "total_pl":      total_pl,
        "total_pl_pct":  total_pl_pct,
        "positions":     len(rows),
        "winners":       winners,
        "losers":        losers,
        "sell_count":    sell_recs,
        "buy_count":     buy_recs,
        "trim_count":    trim_recs,
        "drip_total":    drip_total,
    }


# ─── History / Snapshot ─────────────────────────────────────────────────────────
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
        "recs": [{
            "ticker": r["ticker"],
            "action": r["action"],
            "live":   r["live_price"],
            "equity": r["equity"],
            "pl_pct": r["pl_pct"],
        } for r in recs[:30]],
    })
    _save(REC_HIST_PATH, hist[-200:])  # keep last 200


def log_deposit(deposit_num: int, allocations: list[dict], total: float, notes: str = "") -> None:
    log = _load(DEPOSIT_LOG_PATH, [])
    log.append({
        "ts":           datetime.now().isoformat(),
        "deposit_num":  deposit_num,
        "total":        total,
        "allocations":  allocations,
        "notes":        notes,
    })
    _save(DEPOSIT_LOG_PATH, log)


# ─── Targets persistence ────────────────────────────────────────────────────────
def load_targets() -> dict[str, float]:
    return _load(TARGETS_PATH, {})

def save_targets(targets: dict[str, float]) -> None:
    _save(TARGETS_PATH, targets)

def update_crypto_override(ticker: str, shares: float, avg_cost: float, lt: bool) -> None:
    crypto = _load(CRYPTO_PATH, BAKED_CRYPTO_OVERRIDES)
    crypto[ticker] = {"shares": shares, "avg_cost": avg_cost, "lt": lt}
    _save(CRYPTO_PATH, crypto)
