"""
Portfolio War Room — v8.0
Fix: tx_store persisted to disk (tx_store.json) so it survives Streamlit Cloud restarts.
No more re-upload required on every new session.
"""

import streamlit as st
import pandas as pd
import yfinance as yf
import requests
import hashlib
import csv
import io
import json
import os
import datetime
from pathlib import Path
import plotly.express as px
import plotly.graph_objects as go

# ─── PERSISTENCE ────────────────────────────────────────────────────────────────
TX_STORE_PATH = Path("tx_store.json")
REC_HISTORY_PATH = Path("rec_history.json")
DEPOSIT_LOG_PATH = Path("deposit_log.json")

def load_tx_store() -> dict:
    """Load tx_store from disk. Returns empty dict if file doesn't exist."""
    if TX_STORE_PATH.exists():
        try:
            with open(TX_STORE_PATH, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_tx_store(tx_store: dict):
    """Persist tx_store to disk."""
    with open(TX_STORE_PATH, "w") as f:
        json.dump(tx_store, f)

def load_rec_history() -> list:
    if REC_HISTORY_PATH.exists():
        try:
            with open(REC_HISTORY_PATH, "r") as f:
                return json.load(f)
        except Exception:
            return []
    return []

def save_rec_history(history: list):
    with open(REC_HISTORY_PATH, "w") as f:
        json.dump(history, f)

def load_deposit_log() -> list:
    if DEPOSIT_LOG_PATH.exists():
        try:
            with open(DEPOSIT_LOG_PATH, "r") as f:
                return json.load(f)
        except Exception:
            return []
    return []

def save_deposit_log(log: list):
    with open(DEPOSIT_LOG_PATH, "w") as f:
        json.dump(log, f)

# ─── PAGE CONFIG ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Portfolio War Room",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700&family=Inter:wght@400;600;700&display=swap');
html, body, [class*="css"] {
    background-color: #0a0e1a;
    color: #e2e8f0;
    font-family: 'Inter', sans-serif;
}
.stApp { background-color: #0a0e1a; }
h1,h2,h3 { color: #f8fafc; }
.metric-card {
    background: linear-gradient(135deg, #1e2538, #151929);
    border: 1px solid #2d3748;
    border-radius: 12px;
    padding: 18px 22px;
    margin: 6px 0;
    cursor: pointer;
    transition: border-color 0.2s;
}
.metric-card:hover { border-color: #4f7cff; }
.kpi-val { font-family: 'JetBrains Mono', monospace; font-size: 1.6rem; font-weight: 700; }
.kpi-label { font-size: 0.75rem; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.08em; }
.tag-buy { background:#14532d; color:#86efac; padding:2px 8px; border-radius:4px; font-size:0.72rem; }
.tag-sell { background:#7f1d1d; color:#fca5a5; padding:2px 8px; border-radius:4px; font-size:0.72rem; }
.tag-hold { background:#1e3a5f; color:#93c5fd; padding:2px 8px; border-radius:4px; font-size:0.72rem; }
.tag-trim { background:#3d2b00; color:#fbbf24; padding:2px 8px; border-radius:4px; font-size:0.72rem; }
.persist-badge {
    background:#14532d; color:#86efac; padding:4px 10px;
    border-radius:6px; font-size:0.72rem; font-family:'JetBrains Mono',monospace;
}
div[data-testid="stDataFrame"] { border-radius:8px; overflow:hidden; }
</style>
""", unsafe_allow_html=True)

# ─── CONSTANTS ──────────────────────────────────────────────────────────────────
CRYPTO_TICKERS = {"BTC", "XRP", "ETH", "SOL", "DOGE"}
FOREVER_HOLD = {"VYM", "SCHD", "VTI"}
DCA_ALWAYS   = {"VOO", "QQQ", "VTI"}
SELL_LIST    = {"VTV", "VEA", "VWO", "BND"}  # LT eligible — sell into VOO/VYM

TARGETS = {
    "NVDA": 180, "AAPL": 240, "NFLX": 900, "WMT": 110,
    "GLD": 450, "XLE": 75, "VXUS": 90, "QQQ": 620,
    "VOO": 620, "VYM": 165, "SCHD": 38, "BTC": 120000,
    "XRP": 5.0, "META": 700, "GOOGL": 220, "MSFT": 460,
    "VGT": 750, "VHT": 290, "VIS": 340, "QCOM": 170,
    "COST": 1100, "TSM": 220,
}

BIWEEKLY_PLAN = [
    {"ticker": "NVDA",  "pct": 0.28, "rationale": "AI supercycle — core conviction"},
    {"ticker": "VOO",   "pct": 0.22, "rationale": "S&P 500 — DCA forever"},
    {"ticker": "VYM",   "pct": 0.17, "rationale": "Dividend engine — compound income"},
    {"ticker": "QQQ",   "pct": 0.17, "rationale": "Nasdaq-100 — never stop"},
    {"ticker": "META",  "pct": 0.16, "rationale": "Rotating pick (META/GOOGL/AAPL cycling)"},
]

DEPOSIT_AMOUNT = 900
DEPOSIT_START  = datetime.date(2026, 4, 3)

ACTION_CALENDAR = [
    {"date": "Apr 3",  "action": "🔴 SELL VTV, VEA, VWO, BND — LT eligible → reinvest VOO/VYM"},
    {"date": "Apr 3",  "action": "💰 Deposit #1 — NVDA/VOO/VYM/QQQ + META"},
    {"date": "Apr 4",  "action": "🟡 GLD → LT eligible — trim 25% at $450 target"},
    {"date": "Apr 17", "action": "💰 Deposit #2 — NVDA/VOO/VYM/QQQ + GOOGL"},
    {"date": "May 20", "action": "🔴 SPY turns LT → sell all, buy VOO same day"},
    {"date": "Jul 15", "action": "🔴 VUG turns LT → sell all, buy QQQ same day"},
    {"date": "Nov 6",  "action": "🔵 TSM big lot → LT — trim 20%"},
    {"date": "Dec 15", "action": "🔵 GOOGL big lot → LT — trim 20%"},
    {"date": "Dec 20", "action": "🧾 Year-end: net gains vs losses before Dec 31"},
]

# ─── TX FINGERPRINT & PARSER ─────────────────────────────────────────────────
def _tx_fingerprint(row: dict) -> str:
    key = "|".join([
        str(row.get("Activity Date", "")),
        str(row.get("Trans Code", "")),
        str(row.get("Instrument", "")),
        str(row.get("Quantity", "")),
        str(row.get("Amount", "")),
        str(row.get("Price", "")),
    ])
    return hashlib.sha1(key.encode()).hexdigest()

def _parse_dollar(s) -> float:
    if not s:
        return 0.0
    s = str(s).strip().replace("$", "").replace(",", "").replace("(", "-").replace(")", "")
    try:
        return float(s)
    except Exception:
        return 0.0

def _parse_qty(s) -> float:
    if not s:
        return 0.0
    try:
        return float(str(s).strip().replace(",", ""))
    except Exception:
        return 0.0

def _parse_date(s) -> datetime.date | None:
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(s.strip(), fmt).date()
        except Exception:
            continue
    return None

def ingest_csv(source, existing_tx_store: dict) -> tuple[dict, dict]:
    """
    Parse a Robinhood CSV. Add only rows whose fingerprint is not in existing_tx_store.
    Returns (updated_tx_store, stats).
    source: file-like object or str path.
    """
    if hasattr(source, "read"):
        raw = source.read()
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8-sig", errors="replace")
        lines = raw.splitlines()
    else:
        with open(source, encoding="utf-8-sig") as f:
            lines = f.read().splitlines()

    # Strip trailing disclaimer / empty rows
    clean = []
    for line in lines:
        stripped = line.strip().strip('"')
        if stripped in ("", "The data provided is for informational purposes only."):
            continue
        if stripped.startswith("The data provided"):
            break
        clean.append(line)

    reader = csv.DictReader(io.StringIO("\n".join(clean)), quoting=csv.QUOTE_ALL)

    new_store = dict(existing_tx_store)
    added = 0
    skipped = 0

    for row in reader:
        fp = _tx_fingerprint(row)
        if fp in new_store:
            skipped += 1
            continue
        code = str(row.get("Trans Code", "")).strip()
        if code not in ("Buy", "Sell", "CDIV", "SPL", "REC", "LIQ", "ACH", "RTP", "JNLS", "MISC", "ACATS"):
            skipped += 1
            continue
        new_store[fp] = {k: str(v) for k, v in row.items()}
        added += 1

    return new_store, {"added": added, "skipped": skipped, "total": len(new_store)}

# ─── PORTFOLIO RECOMPUTE ────────────────────────────────────────────────────────
def recompute_portfolio(tx_store: dict, crypto_overrides: dict | None = None) -> dict:
    """
    Replay all rows oldest→newest. Compute running shares and blended avg cost.
    Returns portfolio dict: {ticker: {shares, avg_cost, first_buy_date, drip_count, drip_total, is_drip}}
    """
    rows = list(tx_store.values())

    def _sort_key(r):
        d = _parse_date(r.get("Activity Date", ""))
        return d or datetime.date(2000, 1, 1)

    rows.sort(key=_sort_key)

    portfolio = {}
    cash_flow = 0.0  # net deposits minus withdrawals

    for row in rows:
        code    = str(row.get("Trans Code", "")).strip()
        ticker  = str(row.get("Instrument", "")).strip().upper()
        qty     = _parse_qty(row.get("Quantity", ""))
        price   = _parse_dollar(row.get("Price", ""))
        amount  = abs(_parse_dollar(row.get("Amount", "")))
        date_s  = row.get("Activity Date", "")
        desc    = str(row.get("Description", "")).lower()
        dt      = _parse_date(date_s)

        if code in ("ACH", "RTP"):
            cash_flow += abs(_parse_dollar(row.get("Amount", "")))
            continue

        if code == "CDIV":
            # Cash dividend — no shares, just income
            continue

        if code in ("Buy", "REC", "SPL"):
            if not ticker:
                continue
            is_drip = "reinvestment" in desc or "drip" in desc
            if ticker not in portfolio:
                portfolio[ticker] = {
                    "shares": 0.0, "avg_cost": 0.0,
                    "first_buy_date": dt, "drip_count": 0,
                    "drip_total": 0.0, "is_drip": False,
                }
            pos = portfolio[ticker]
            cost = price * qty if price and qty else amount
            old_shares = pos["shares"]
            old_cost   = pos["avg_cost"]
            new_shares = old_shares + qty
            if new_shares > 0:
                pos["avg_cost"] = (old_shares * old_cost + cost) / new_shares
            pos["shares"] = new_shares
            if is_drip:
                pos["drip_count"] += 1
                pos["drip_total"]  = round(pos["drip_total"] + cost, 4)
            if pos["first_buy_date"] is None or (dt and dt < pos["first_buy_date"]):
                pos["first_buy_date"] = dt

        elif code == "Sell":
            if not ticker or ticker not in portfolio:
                continue
            pos = portfolio[ticker]
            pos["shares"] = max(0.0, pos["shares"] - qty)
            if pos["shares"] < 0.0001:
                pos["shares"] = 0.0

        elif code == "LIQ":
            if ticker in portfolio:
                portfolio[ticker]["shares"] = 0.0

    # Apply crypto overrides
    if crypto_overrides:
        for ticker, info in crypto_overrides.items():
            portfolio[ticker] = {
                "shares": info.get("shares", 0),
                "avg_cost": info.get("avg_cost", 0),
                "first_buy_date": None,
                "drip_count": 0, "drip_total": 0.0, "is_drip": False,
            }

    # Remove zero-share positions (keep for display if user wants)
    portfolio = {k: v for k, v in portfolio.items() if v["shares"] > 0.0001}
    return portfolio

# ─── PRICE FETCHER ────────────────────────────────────────────────────────────
@st.cache_data(ttl=120)
def fetch_all_prices(tickers: list) -> dict:
    prices = {}
    stock_tickers = [t for t in tickers if t not in CRYPTO_TICKERS]
    crypto_tickers = [t for t in tickers if t in CRYPTO_TICKERS]

    if stock_tickers:
        try:
            data = yf.download(stock_tickers, period="1d", progress=False, auto_adjust=True)
            if "Close" in data.columns:
                close = data["Close"].iloc[-1]
                for t in stock_tickers:
                    if t in close.index:
                        prices[t] = round(float(close[t]), 2)
            elif hasattr(data["Close"], "iloc"):
                for t in stock_tickers:
                    try:
                        p = yf.Ticker(t).fast_info["last_price"]
                        prices[t] = round(float(p), 2)
                    except Exception:
                        prices[t] = None
        except Exception:
            for t in stock_tickers:
                try:
                    p = yf.Ticker(t).fast_info["last_price"]
                    prices[t] = round(float(p), 2)
                except Exception:
                    prices[t] = None

    # CoinGecko for crypto
    cg_map = {"BTC": "bitcoin", "XRP": "ripple", "ETH": "ethereum", "SOL": "solana", "DOGE": "dogecoin"}
    for t in crypto_tickers:
        coin = cg_map.get(t)
        if not coin:
            continue
        try:
            url = f"https://api.coingecko.com/api/v3/simple/price?ids={coin}&vs_currencies=usd"
            r = requests.get(url, timeout=8)
            prices[t] = round(r.json()[coin]["usd"], 4)
        except Exception:
            prices[t] = None

    return prices

# ─── RECOMMENDATION ENGINE ────────────────────────────────────────────────────
def lt_eligible(first_buy_date) -> bool:
    if first_buy_date is None:
        return False
    return (datetime.date.today() - first_buy_date).days >= 366

def generate_recs(portfolio: dict, prices: dict) -> list:
    recs = []
    today = datetime.date.today()

    for ticker, pos in portfolio.items():
        price   = prices.get(ticker)
        shares  = pos["shares"]
        cost    = pos["avg_cost"]
        fbd     = pos["first_buy_date"]
        is_lt   = lt_eligible(fbd)
        tax_note = "✅ LT cap gains (15%)" if is_lt else f"⏳ ST (ordinary income) — LT eligible {fbd + datetime.timedelta(days=366) if fbd else 'unknown'}"

        if price is None:
            recs.append({
                "ticker": ticker, "shares": shares, "avg_cost": cost,
                "price": None, "pnl_pct": None, "equity": None,
                "action": "❓ PRICE UNAVAILABLE", "rationale": "Could not fetch price",
                "tax_note": tax_note, "proceeds": None,
            })
            continue

        equity  = price * shares
        pnl     = (price - cost) / cost * 100 if cost > 0 else 0
        target  = TARGETS.get(ticker, cost * 1.25)
        upside  = (target - price) / price * 100 if price > 0 else 0

        # ── Rule engine ────────────────────────────────────────────────────
        if ticker in FOREVER_HOLD:
            action    = "♾ HOLD FOREVER — DRIP on"
            rationale = "Income ETF — reinvest every dividend"

        elif ticker in DCA_ALWAYS:
            action    = "📈 DCA ALWAYS — add every deposit"
            rationale = "Core index — never stop accumulating"

        elif ticker in SELL_LIST:
            if is_lt:
                action    = "🔴 SELL NOW — LT eligible"
                rationale = "Exit position → reinvest into VOO/VYM same day (not wash sale)"
            else:
                lt_date = fbd + datetime.timedelta(days=366) if fbd else None
                action    = f"⏳ WAIT → SELL {lt_date}" if lt_date else "⏳ WAIT → SELL (check date)"
                rationale = "Hold for LT treatment — sell immediately after 1-year mark"

        elif ticker in CRYPTO_TICKERS:
            if upside > 25:
                action    = "🚀 ACCUMULATE — strong crypto upside"
                rationale = f"Target ${target:,.0f} — {upside:.0f}% upside"
            elif pnl > 20 and is_lt:
                action    = "✂️ TRIM 25% — LT crypto gains"
                rationale = "Lock in gains tax-efficiently; keep 75%"
            else:
                action    = "🟡 HOLD — crypto core"
                rationale = "Within normal range — hold and monitor"

        elif pnl < -20:
            action    = "🚨 STOP-LOSS REVIEW — down >20%"
            rationale = f"Cost ${cost:.2f} → now ${price:.2f} ({pnl:.1f}%) — consider exit or double-down"

        elif pnl < -8 and upside > 20:
            action    = "💎 STRONG BUY — dip + high upside"
            rationale = f"Down {pnl:.1f}% with {upside:.0f}% to target — great entry"

        elif upside > 20:
            action    = "🟢 ACCUMULATE — good upside"
            rationale = f"{upside:.0f}% to target ${target}"

        elif pnl > 20 and is_lt:
            action    = "✂️ TRIM 20% — LT gains"
            rationale = f"Up {pnl:.1f}% — harvest partial gains at LT rates"

        elif pnl > 20 and not is_lt:
            action    = "🟡 HOLD — wait for LT"
            rationale = f"Up {pnl:.1f}% but ST — wait for LT eligibility"

        else:
            action    = "🟡 HOLD — on track"
            rationale = f"{upside:.0f}% to target; P&L {pnl:+.1f}%"

        proceeds = round(shares * price * 0.20, 2) if "TRIM" in action else (
                   round(shares * price, 2) if "SELL" in action else None)

        recs.append({
            "ticker": ticker, "shares": round(shares, 4), "avg_cost": round(cost, 2),
            "price": price, "pnl_pct": round(pnl, 2), "equity": round(equity, 2),
            "action": action, "rationale": rationale, "tax_note": tax_note,
            "proceeds": proceeds,
        })

    recs.sort(key=lambda r: (
        0 if "SELL" in r["action"] else
        1 if "STOP-LOSS" in r["action"] else
        2 if "STRONG BUY" in r["action"] else
        3 if "TRIM" in r["action"] else
        4 if "ACCUMULATE" in r["action"] else
        5 if "DCA" in r["action"] else 6
    ))
    return recs

# ─── BIWEEKLY SCHEDULE ────────────────────────────────────────────────────────
def get_biweekly_dates(start: datetime.date, n: int = 18) -> list[datetime.date]:
    dates = []
    d = start
    for _ in range(n):
        dates.append(d)
        d += datetime.timedelta(days=14)
    return dates

def build_deploy_schedule(prices: dict, deposit: float = 900) -> pd.DataFrame:
    dates = get_biweekly_dates(DEPOSIT_START)
    rotating = ["META", "GOOGL", "AAPL", "MSFT", "COST", "TSM", "CRM", "NFLX"]
    rows = []
    for i, d in enumerate(dates):
        rot = rotating[i % len(rotating)]
        plan = BIWEEKLY_PLAN[:-1] + [{"ticker": rot, "pct": 0.16, "rationale": "Rotating pick"}]
        for slot in plan:
            t = slot["ticker"]
            amt = round(deposit * slot["pct"], 2)
            p = prices.get(t)
            sh = round(amt / p, 4) if p else None
            rows.append({
                "Date": d.strftime("%b %d"),
                "Ticker": t,
                "Amount": f"${amt:,.2f}",
                "Est Shares": sh,
                "Rationale": slot["rationale"],
            })
    return pd.DataFrame(rows)

# ─── SESSION STATE INIT ───────────────────────────────────────────────────────
if "tx_store" not in st.session_state:
    # ✅ KEY FIX: load from disk on startup — no re-upload needed
    st.session_state.tx_store = load_tx_store()

if "crypto_overrides" not in st.session_state:
    st.session_state.crypto_overrides = {
        "BTC": {"shares": 0.03433, "avg_cost": 52800},
        "XRP": {"shares": 1.066,   "avg_cost": 0.68},
    }

if "prices" not in st.session_state:
    st.session_state.prices = {}

if "portfolio" not in st.session_state:
    st.session_state.portfolio = {}

if "recs" not in st.session_state:
    st.session_state.recs = []

if "rec_history" not in st.session_state:
    st.session_state.rec_history = load_rec_history()

if "deposit_log" not in st.session_state:
    st.session_state.deposit_log = load_deposit_log()

# ─── HEADER ──────────────────────────────────────────────────────────────────
st.markdown("## 📈 Portfolio War Room")
col_h1, col_h2 = st.columns([4, 1])
with col_h1:
    tx_count = len(st.session_state.tx_store)
    if tx_count > 0:
        st.markdown(f'<span class="persist-badge">✅ {tx_count} transactions loaded from disk — no re-upload needed</span>', unsafe_allow_html=True)
    else:
        st.info("No transactions loaded yet. Go to **Import** tab to upload your Robinhood CSV.")

with col_h2:
    if st.button("🔄 Refresh Prices", type="primary", use_container_width=True):
        st.cache_data.clear()
        portfolio = recompute_portfolio(st.session_state.tx_store, st.session_state.crypto_overrides)
        st.session_state.portfolio = portfolio
        tickers = list(portfolio.keys())
        st.session_state.prices = fetch_all_prices(tickers)
        st.session_state.recs = generate_recs(portfolio, st.session_state.prices)
        st.success("Prices refreshed!")
        st.rerun()

# Auto-compute on first load if tx_store has data
if st.session_state.tx_store and not st.session_state.portfolio:
    portfolio = recompute_portfolio(st.session_state.tx_store, st.session_state.crypto_overrides)
    st.session_state.portfolio = portfolio
    tickers = list(portfolio.keys())
    st.session_state.prices = fetch_all_prices(tickers)
    st.session_state.recs = generate_recs(portfolio, st.session_state.prices)

# ─── TABS ────────────────────────────────────────────────────────────────────
tabs = st.tabs(["📊 Overview", "📋 Holdings", "💰 Deploy", "📥 Import", "🌱 DRIP", "🕐 History", "⚙️ Settings"])

# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — OVERVIEW
# ══════════════════════════════════════════════════════════════════════════════
with tabs[0]:
    portfolio = st.session_state.portfolio
    prices    = st.session_state.prices
    recs      = st.session_state.recs

    if not portfolio:
        st.info("Load transactions and click **Refresh Prices** to see your portfolio overview.")
    else:
        total_equity = sum(prices.get(t, pos["avg_cost"]) * pos["shares"] for t, pos in portfolio.items())
        total_cost   = sum(pos["avg_cost"] * pos["shares"] for pos in portfolio.values())
        total_pnl    = total_equity - total_cost
        pnl_pct      = total_pnl / total_cost * 100 if total_cost else 0

        sells_today = [r for r in recs if "SELL NOW" in r["action"]]
        buys_today  = [r for r in recs if "STRONG BUY" in r["action"] or "ACCUMULATE" in r["action"]]
        alerts      = [r for r in recs if "STOP-LOSS" in r["action"]]

        c1, c2, c3, c4, c5 = st.columns(5)
        kpi_data = [
            (c1, "💼 Portfolio Value", f"${total_equity:,.0f}", "#60a5fa"),
            (c2, "📈 Total P&L", f"{'+'if total_pnl>0 else ''}{total_pnl:,.0f} ({pnl_pct:+.1f}%)", "#34d399" if total_pnl > 0 else "#f87171"),
            (c3, "🔴 Sell Alerts", str(len(sells_today)), "#f87171"),
            (c4, "🟢 Buy Signals", str(len(buys_today)), "#34d399"),
            (c5, "🚨 Stop-Loss", str(len(alerts)), "#fbbf24"),
        ]
        for col, label, val, color in kpi_data:
            with col:
                st.markdown(f"""
                <div class="metric-card">
                  <div class="kpi-label">{label}</div>
                  <div class="kpi-val" style="color:{color}">{val}</div>
                </div>""", unsafe_allow_html=True)

        st.markdown("---")

        # Recommendations table
        st.markdown("### 🎯 Live Recommendations")
        if recs:
            rec_df = pd.DataFrame([{
                "Ticker": r["ticker"],
                "Shares": r["shares"],
                "Avg Cost": f"${r['avg_cost']:,.2f}",
                "Price": f"${r['price']:,.2f}" if r["price"] else "—",
                "P&L %": f"{r['pnl_pct']:+.1f}%" if r["pnl_pct"] is not None else "—",
                "Equity": f"${r['equity']:,.0f}" if r["equity"] else "—",
                "Action": r["action"],
                "Tax": r["tax_note"],
            } for r in recs])
            st.dataframe(rec_df, use_container_width=True, hide_index=True)
        else:
            st.info("No recommendations yet — click Refresh Prices.")

        # Action calendar
        st.markdown("---")
        st.markdown("### 📅 Action Calendar 2026")
        cal_df = pd.DataFrame(ACTION_CALENDAR)
        st.dataframe(cal_df, use_container_width=True, hide_index=True)

        # Tax playbook
        with st.expander("🧾 Tax Playbook"):
            st.markdown("""
**Never sell ST** — 37% ordinary income vs 15-20% LT cap gains.  
**SELL order:** VTV, VEA, VWO, BND (LT now) → SPY (May 20) → VUG (Jul 15).  
**After each SELL:** Reinvest same day into target ETF (ETF swaps ≠ wash sales).  
**DRIP lots:** Each reinvestment = new tax lot. Track individually.  
**Year-end:** Net realized gains vs losses before Dec 31.  
**Crypto:** Hold BTC/XRP >1 year for LT treatment. Never sell ST crypto.
""")

# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — HOLDINGS
# ══════════════════════════════════════════════════════════════════════════════
with tabs[1]:
    portfolio = st.session_state.portfolio
    prices    = st.session_state.prices

    if not portfolio:
        st.info("Load transactions and refresh prices.")
    else:
        rows = []
        for ticker, pos in portfolio.items():
            price  = prices.get(ticker, pos["avg_cost"])
            equity = price * pos["shares"] if price else pos["avg_cost"] * pos["shares"]
            pnl    = (price - pos["avg_cost"]) / pos["avg_cost"] * 100 if price and pos["avg_cost"] > 0 else 0
            rows.append({
                "Ticker": ticker,
                "Shares": round(pos["shares"], 4),
                "Avg Cost": pos["avg_cost"],
                "Price": price,
                "Equity": round(equity, 2),
                "P&L %": round(pnl, 2),
                "First Buy": str(pos["first_buy_date"]) if pos["first_buy_date"] else "—",
                "LT?": "✅" if lt_eligible(pos["first_buy_date"]) else "❌",
            })

        df = pd.DataFrame(rows).sort_values("Equity", ascending=False)

        # Color negative rows
        def color_row(row):
            if row["P&L %"] < 0:
                return ["background-color: #3d0000; color:#fca5a5"] * len(row)
            return [""] * len(row)

        st.markdown("### 📋 Current Holdings")
        styled = df.style.apply(color_row, axis=1).format({
            "Avg Cost": "${:,.2f}", "Price": "${:,.2f}", "Equity": "${:,.0f}", "P&L %": "{:+.2f}%"
        })
        st.dataframe(styled, use_container_width=True, hide_index=True)

        # Charts
        col_pie, col_bar = st.columns(2)
        with col_pie:
            fig_pie = px.pie(df, values="Equity", names="Ticker",
                             title="Portfolio Allocation",
                             color_discrete_sequence=px.colors.qualitative.Set3)
            fig_pie.update_layout(paper_bgcolor="#0a0e1a", font_color="#e2e8f0")
            st.plotly_chart(fig_pie, use_container_width=True)

        with col_bar:
            df_bar = df.copy()
            df_bar["Color"] = df_bar["P&L %"].apply(lambda x: "#34d399" if x >= 0 else "#f87171")
            fig_bar = go.Figure(go.Bar(
                x=df_bar["Ticker"], y=df_bar["P&L %"],
                marker_color=df_bar["Color"],
                text=[f"{v:+.1f}%" for v in df_bar["P&L %"]],
                textposition="outside",
            ))
            fig_bar.update_layout(
                title="P&L by Position (%)",
                paper_bgcolor="#0a0e1a", plot_bgcolor="#0a0e1a",
                font_color="#e2e8f0", yaxis_title="P&L %",
            )
            st.plotly_chart(fig_bar, use_container_width=True)

# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — DEPLOY (Cash & Biweekly $900)
# ══════════════════════════════════════════════════════════════════════════════
with tabs[2]:
    prices = st.session_state.prices
    st.markdown("### 💰 Biweekly $900 Deploy Schedule")
    st.caption(f"Starting {DEPOSIT_START.strftime('%B %d, %Y')} — every other Friday")

    if prices:
        sched_df = build_deploy_schedule(prices)
        st.dataframe(sched_df, use_container_width=True, hide_index=True)
    else:
        # Show schedule without estimated shares
        sched_df = build_deploy_schedule({})
        sched_df["Est Shares"] = "—"
        st.dataframe(sched_df, use_container_width=True, hide_index=True)
        st.info("Refresh prices to see estimated share counts.")

    st.markdown("---")
    st.markdown("### 📝 Log a Deposit / Purchase")
    with st.form("deposit_form"):
        dep_date   = st.date_input("Date", value=datetime.date.today())
        dep_ticker = st.selectbox("Ticker", [p["ticker"] for p in BIWEEKLY_PLAN] + ["OTHER"])
        dep_amount = st.number_input("Amount ($)", value=900.0, step=50.0)
        dep_note   = st.text_input("Note (optional)")
        submitted  = st.form_submit_button("Log Deposit")
        if submitted:
            entry = {
                "date": str(dep_date), "ticker": dep_ticker,
                "amount": dep_amount, "note": dep_note,
                "logged_at": datetime.datetime.now().isoformat(),
            }
            st.session_state.deposit_log.append(entry)
            save_deposit_log(st.session_state.deposit_log)
            st.success(f"Logged ${dep_amount:,.2f} → {dep_ticker} on {dep_date}")

    if st.session_state.deposit_log:
        st.markdown("#### Deposit History")
        log_df = pd.DataFrame(st.session_state.deposit_log)
        st.dataframe(log_df, use_container_width=True, hide_index=True)

# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 — IMPORT
# ══════════════════════════════════════════════════════════════════════════════
with tabs[3]:
    st.markdown("### 📥 Import Robinhood Activity CSV")

    tx_count = len(st.session_state.tx_store)
    if tx_count > 0:
        st.markdown(f'<span class="persist-badge">✅ {tx_count} transactions on disk — upload only needed for NEW activity</span>', unsafe_allow_html=True)
        st.markdown("")

    uploaded = st.file_uploader("Upload Robinhood activity CSV", type=["csv"])
    if uploaded:
        new_store, stats = ingest_csv(uploaded, st.session_state.tx_store)
        st.markdown(f"""
**Preview:**
- New rows to add: **{stats['added']}**
- Already known (skipped): **{stats['skipped']}**
- Total after merge: **{stats['total']}**
        """)

        if stats["added"] > 0:
            if st.button("✅ Apply Import", type="primary"):
                st.session_state.tx_store = new_store
                # ✅ KEY FIX: persist to disk immediately
                save_tx_store(new_store)
                portfolio = recompute_portfolio(new_store, st.session_state.crypto_overrides)
                st.session_state.portfolio = portfolio
                tickers = list(portfolio.keys())
                st.session_state.prices = fetch_all_prices(tickers)
                st.session_state.recs = generate_recs(portfolio, st.session_state.prices)
                st.success(f"✅ Imported {stats['added']} new rows. Portfolio recomputed. Data saved to disk — no re-upload needed on restart.")
                st.rerun()
        else:
            st.info("All rows already imported — nothing new to add.")

    st.markdown("---")
    st.markdown("### 🔑 Why You Won't Need to Re-Upload")
    st.markdown("""
Every time you upload a CSV, it is **saved to disk** (`tx_store.json`).  
When the app restarts (Streamlit Cloud idle timeout, redeploy, etc.), it loads  
that file automatically. You only need to upload a **new CSV export** when  
you have new activity to add — and duplicates are always filtered out by content hash.
    """)

# ══════════════════════════════════════════════════════════════════════════════
# TAB 5 — DRIP
# ══════════════════════════════════════════════════════════════════════════════
with tabs[4]:
    portfolio = st.session_state.portfolio
    st.markdown("### 🌱 Dividend Reinvestment (DRIP) Summary")

    drip_rows = [
        {"Ticker": t, "DRIP Events": pos["drip_count"], "Total Reinvested": f"${pos['drip_total']:,.2f}"}
        for t, pos in portfolio.items() if pos["drip_count"] > 0
    ]
    if drip_rows:
        drip_df = pd.DataFrame(drip_rows).sort_values("Total Reinvested", ascending=False)
        total_drip = sum(pos["drip_total"] for pos in portfolio.values())
        total_events = sum(pos["drip_count"] for pos in portfolio.values())
        st.metric("Total DRIP Events", total_events)
        st.metric("Total Reinvested", f"${total_drip:,.2f}")
        st.dataframe(drip_df, use_container_width=True, hide_index=True)
    else:
        st.info("No DRIP activity found. Import your Robinhood CSV to see dividend reinvestments.")

# ══════════════════════════════════════════════════════════════════════════════
# TAB 6 — HISTORY
# ══════════════════════════════════════════════════════════════════════════════
with tabs[5]:
    st.markdown("### 🕐 Recommendation History")

    if st.button("📸 Snapshot Current Recommendations"):
        recs = st.session_state.recs
        if recs:
            snapshot = {
                "timestamp": datetime.datetime.now().isoformat(),
                "recs": recs,
            }
            st.session_state.rec_history.append(snapshot)
            save_rec_history(st.session_state.rec_history)
            st.success("Snapshot saved.")

    history = st.session_state.rec_history
    if not history:
        st.info("No snapshots yet. Click the button above after refreshing prices.")
    else:
        for i, snap in enumerate(reversed(history)):
            ts = snap["timestamp"][:16].replace("T", " ")
            with st.expander(f"📸 Snapshot {len(history)-i} — {ts}"):
                snap_df = pd.DataFrame([{
                    "Ticker": r["ticker"],
                    "Price": f"${r['price']:,.2f}" if r["price"] else "—",
                    "P&L %": f"{r['pnl_pct']:+.1f}%" if r["pnl_pct"] is not None else "—",
                    "Action": r["action"],
                } for r in snap["recs"]])
                st.dataframe(snap_df, use_container_width=True, hide_index=True)

# ══════════════════════════════════════════════════════════════════════════════
# TAB 7 — SETTINGS
# ══════════════════════════════════════════════════════════════════════════════
with tabs[6]:
    st.markdown("### ⚙️ Settings & Manual Overrides")

    # Crypto overrides
    st.markdown("#### Crypto Manual Overrides")
    c1, c2 = st.columns(2)
    with c1:
        btc_shares = st.number_input("BTC Shares", value=float(st.session_state.crypto_overrides.get("BTC", {}).get("shares", 0.03433)), format="%.5f")
        btc_cost   = st.number_input("BTC Avg Cost ($)", value=float(st.session_state.crypto_overrides.get("BTC", {}).get("avg_cost", 52800)), step=100.0)
    with c2:
        xrp_shares = st.number_input("XRP Shares", value=float(st.session_state.crypto_overrides.get("XRP", {}).get("shares", 1.066)), format="%.3f")
        xrp_cost   = st.number_input("XRP Avg Cost ($)", value=float(st.session_state.crypto_overrides.get("XRP", {}).get("avg_cost", 0.68)), format="%.4f")

    if st.button("Save Crypto Overrides"):
        st.session_state.crypto_overrides = {
            "BTC": {"shares": btc_shares, "avg_cost": btc_cost},
            "XRP": {"shares": xrp_shares, "avg_cost": xrp_cost},
        }
        st.success("Crypto overrides saved. Refresh prices to recalculate.")

    st.markdown("---")
    st.markdown("#### Export")
    if st.session_state.portfolio:
        export_rows = []
        for ticker, pos in st.session_state.portfolio.items():
            price = st.session_state.prices.get(ticker, pos["avg_cost"])
            export_rows.append({
                "Ticker": ticker,
                "Shares": pos["shares"],
                "Avg Cost": pos["avg_cost"],
                "Price": price,
                "Equity": price * pos["shares"],
                "First Buy": str(pos["first_buy_date"]),
            })
        export_df = pd.DataFrame(export_rows)
        csv_bytes = export_df.to_csv(index=False).encode()
        st.download_button("⬇️ Download Portfolio CSV", csv_bytes, "portfolio_export.csv", "text/csv")

    st.markdown("---")
    st.markdown("#### Danger Zone")
    if st.button("🗑️ Clear ALL Transactions (irreversible)", type="secondary"):
        st.session_state.tx_store = {}
        save_tx_store({})
        st.session_state.portfolio = {}
        st.session_state.prices = {}
        st.session_state.recs = []
        st.warning("All transactions cleared from disk and session.")
        st.rerun()

    # Show disk status
    st.markdown("---")
    st.markdown("#### Disk Status")
    st.markdown(f"- `tx_store.json` exists: **{'✅' if TX_STORE_PATH.exists() else '❌'}**")
    st.markdown(f"- Transactions on disk: **{len(st.session_state.tx_store)}**")
    st.markdown(f"- Rec snapshots saved: **{len(st.session_state.rec_history)}**")
    st.markdown(f"- Deposit log entries: **{len(st.session_state.deposit_log)}**")
