"""
Portfolio War Room — App.py v7.0
Single-file Streamlit app for Streamlit Cloud deployment.

ARCHITECTURE (v7 — fixes the doubling bug permanently):
  The root cause of doubling was that the old design kept a hardcoded
  BASELINE_PORTFOLIO (shares already baked in) and then ADDED transaction
  deltas on top of it every upload.  This doubled every position.

  v7 replaces that with a single source of truth:
    session_state["tx_store"]  — dict of {fingerprint: row_dict}
                                  every unique Robinhood transaction row ever seen
    portfolio                  — ALWAYS recomputed from tx_store from scratch
                                  by replaying all transactions in date order

  Uploading any CSV (new, old, partial, full re-export):
    1. Parse every row → compute fingerprint
    2. Skip rows already in tx_store (exact dedup by content hash)
    3. Add only genuinely new rows to tx_store
    4. Recompute portfolio from entire tx_store
    5. Show diff: what changed between old portfolio and new portfolio

  This means:
    - Re-uploading the same CSV → tx_store unchanged → portfolio unchanged
    - Uploading a new CSV with 5 new trades → only those 5 added → portfolio updated correctly
    - Session restart → tx_store starts empty → first upload builds it fresh from scratch

  Crypto (BTC/XRP) positions are manually managed via Settings since
  Robinhood Crypto CSV is a separate export.

Deploy: streamlit run App.py
"""

# ── stdlib ───────────────────────────────────────────────────────────────────
import io
import re
import csv
import json
import copy
import hashlib
from datetime import date, datetime
from collections import defaultdict

# ── third-party ──────────────────────────────────────────────────────────────
import streamlit as st
import pandas as pd

# ── page config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="⚡ Portfolio War Room",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ════════════════════════════════════════════════════════════════════════════════
# GLOBAL CSS  — Premium dark financial dashboard aesthetic
# Inspired by CashPilot + InvestX dark finance UIs
# ════════════════════════════════════════════════════════════════════════════════
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Syne:wght@400;600;700;800&family=IBM+Plex+Mono:wght@400;500;600&family=Inter:wght@300;400;500;600&display=swap');

/* ── reset / base ── */
html, body, [class*="css"] { font-family: 'Inter', sans-serif; background:#080c14; color:#dde3f0; }
.stApp { background: #080c14; }
#MainMenu, footer, header { visibility: hidden; }
.block-container { padding: 1rem 1.4rem 3rem; max-width: 1480px; margin: 0 auto; }

/* ── typography ── */
h1,h2,h3 { font-family: 'Syne', sans-serif; }
.mono     { font-family: 'IBM Plex Mono', monospace; }

/* ══ TOP HEADER BAR ══ */
.war-header {
    display: flex; align-items: center; justify-content: space-between;
    padding: 16px 0 20px;
    border-bottom: 1px solid rgba(255,255,255,0.05);
    margin-bottom: 24px;
}
.war-title { font-family:'Syne',sans-serif; font-size:24px; font-weight:800; letter-spacing:-0.03em; }
.war-title span { color:#4ade80; }
.war-ts { font-family:'IBM Plex Mono',monospace; font-size:10px; color:#3d4f6e; margin-top:3px; }

/* ══ METRIC CARDS ══ */
.kpi-grid { display:grid; grid-template-columns:repeat(5,1fr); gap:12px; margin-bottom:24px; }
.kpi-card {
    background: linear-gradient(145deg,#0d1525,#111c30);
    border: 1px solid rgba(255,255,255,0.06);
    border-radius:16px; padding:18px 20px 16px;
    position:relative; overflow:hidden; cursor:pointer;
    transition: transform .15s, border-color .15s, box-shadow .15s;
}
.kpi-card:hover { transform:translateY(-3px); border-color:rgba(74,222,128,.25); box-shadow:0 12px 40px rgba(0,0,0,.4); }
.kpi-card::after {
    content:''; position:absolute; top:0;left:0;right:0;height:2px;
    border-radius:16px 16px 0 0;
}
.kpi-green::after  { background:linear-gradient(90deg,#4ade80,#22d3ee); }
.kpi-red::after    { background:linear-gradient(90deg,#f87171,#fb923c); }
.kpi-yellow::after { background:linear-gradient(90deg,#fbbf24,#f59e0b); }
.kpi-blue::after   { background:linear-gradient(90deg,#60a5fa,#818cf8); }
.kpi-purple::after { background:linear-gradient(90deg,#a78bfa,#ec4899); }
.kpi-label { font-size:10px; font-weight:600; letter-spacing:.12em; text-transform:uppercase; color:#3d5478; margin-bottom:8px; }
.kpi-value { font-family:'IBM Plex Mono',monospace; font-size:24px; font-weight:600; line-height:1; color:#eef2ff; }
.kpi-sub   { font-size:11px; color:#4a6080; margin-top:6px; }
.kpi-click { font-size:9px; color:#243047; margin-top:8px; letter-spacing:.05em; }

/* ══ DRILL PANEL ══ */
.drill {
    background:#0b1220; border:1px solid rgba(96,165,250,.15);
    border-radius:12px; padding:18px 20px; margin-bottom:18px;
    animation: fadeSlide .2s ease;
}
@keyframes fadeSlide { from{opacity:0;transform:translateY(-6px)} to{opacity:1;transform:translateY(0)} }
.drill-title { font-family:'Syne',sans-serif; font-size:13px; font-weight:700; color:#60a5fa; margin-bottom:14px; letter-spacing:.02em; }
.drill-row {
    display:flex; justify-content:space-between; align-items:center;
    padding:7px 0; border-bottom:1px solid rgba(255,255,255,.04); font-size:12px;
}
.drill-row:last-child { border-bottom:none; }
.dk { color:#4a6080; }
.dv { font-family:'IBM Plex Mono',monospace; font-size:11px; color:#c7d2e7; }
.dg { color:#4ade80; } .dr { color:#f87171; } .dy { color:#fbbf24; }

/* ══ SECTION HEADER ══ */
.sec-head {
    font-family:'Syne',sans-serif; font-size:17px; font-weight:700;
    color:#c7d2e7; margin: 28px 0 14px; padding-bottom:10px;
    border-bottom:1px solid rgba(255,255,255,.05);
    display:flex; align-items:center; gap:8px;
}

/* ══ HOLDINGS TABLE ══ */
.htable { width:100%; border-collapse:collapse; font-size:12.5px; }
.htable thead th {
    background:#0b1220; color:#2e4060; font-size:9.5px; letter-spacing:.12em;
    text-transform:uppercase; padding:9px 10px; text-align:right;
    border-bottom:1px solid rgba(255,255,255,.05); position:sticky; top:0;
}
.htable thead th:first-child { text-align:left; border-radius:8px 0 0 0; }
.htable thead th:last-child  { border-radius:0 8px 0 0; }
.htable tbody td { padding:9px 10px; border-bottom:1px solid rgba(255,255,255,.03); text-align:right; }
.htable tbody td:first-child { text-align:left; font-weight:600; font-size:13px; }
.htable tbody tr:hover td { background:rgba(255,255,255,.025); }
.row-loss td { color:#f87171 !important; }
.row-loss td:first-child { color:#f87171; }
.row-gain-pnl { color:#4ade80; }
.row-loss-pnl { color:#f87171; }

/* ══ BADGES ══ */
.b { border-radius:5px; padding:2px 7px; font-size:10px; font-weight:700; letter-spacing:.04em; }
.b-sell   { background:#3d0d0d; color:#f87171; border:1px solid #7f1d1d; }
.b-buy    { background:#052e1a; color:#4ade80; border:1px solid #14532d; }
.b-trim   { background:#2d1a00; color:#fbbf24; border:1px solid #78350f; }
.b-hold   { background:#0f1f3d; color:#60a5fa; border:1px solid #1e3a5f; }
.b-dca    { background:#052e1a; color:#34d399; border:1px solid #065f46; }
.b-lock   { background:#1a1228; color:#a78bfa; border:1px solid #4c1d95; }

/* ══ ALERT STRIP ══ */
.alert-strip {
    display:flex; gap:8px; flex-wrap:wrap; margin-bottom:18px;
}
.alert-pill {
    background:#0b1220; border-radius:8px; padding:8px 14px;
    font-size:12px; border:1px solid rgba(255,255,255,.07);
    display:flex; align-items:center; gap:6px;
}
.alert-pill b { font-family:'IBM Plex Mono',monospace; }

/* ══ CASH CARDS ══ */
.cash-card {
    background:linear-gradient(145deg,#0d1525,#0a1322);
    border:1px solid rgba(96,165,250,.18); border-radius:14px;
    padding:16px 18px; margin-bottom:12px;
}
.cash-head { font-family:'Syne',sans-serif; font-size:13px; font-weight:700; color:#60a5fa; margin-bottom:10px; }
.cash-row  { display:flex; justify-content:space-between; padding:5px 0; font-size:12px; color:#8aa0c0; border-bottom:1px solid rgba(255,255,255,.04); }
.cash-row:last-child { border-bottom:none; }
.cash-amt  { font-family:'IBM Plex Mono',monospace; color:#4ade80; }

/* ══ DIFF TABLE ══ */
.diff-add  td { background:rgba(74,222,128,.06); }
.diff-mod  td { background:rgba(251,191,36,.06); }
.diff-rm   td { background:rgba(248,113,113,.06); }

/* ══ CALENDAR ITEMS ══ */
.cal-item {
    display:flex; gap:14px; padding:11px 16px;
    background:#0b1220; border-left:3px solid #60a5fa;
    border-radius:0 10px 10px 0; margin-bottom:8px; font-size:12.5px;
}
.cal-date { font-family:'IBM Plex Mono',monospace; color:#60a5fa; min-width:72px; font-size:11px; }

/* ══ TABS ══ */
.stTabs [data-baseweb="tab-list"] {
    background:#0b1220; border-radius:12px; padding:4px; gap:2px;
    border:1px solid rgba(255,255,255,.05);
}
.stTabs [data-baseweb="tab"] { border-radius:9px; color:#3d5478; font-size:12.5px; font-weight:500; padding:8px 18px; }
.stTabs [aria-selected="true"] { background:#111c30 !important; color:#60a5fa !important; font-weight:600 !important; }

/* ══ BUTTONS ══ */
.stButton > button {
    background:linear-gradient(135deg,#1d4ed8,#2563eb);
    color:#fff; border:none; border-radius:10px; font-weight:600;
    font-size:12.5px; padding:9px 20px; transition:all .15s;
    letter-spacing:.02em;
}
.stButton > button:hover { background:linear-gradient(135deg,#2563eb,#3b82f6); transform:translateY(-1px); box-shadow:0 6px 20px rgba(37,99,235,.4); }

/* ══ FILE UPLOADER ══ */
[data-testid="stFileUploaderDropzone"] { background:#0b1220 !important; border:1px dashed rgba(255,255,255,.1) !important; border-radius:12px !important; }
[data-testid="stFileUploaderDropzone"] p { color:#3d5478 !important; font-size:13px !important; }

/* ══ INPUTS ══ */
.stTextInput input, .stNumberInput input, .stSelectbox select {
    background:#0b1220 !important; border:1px solid rgba(255,255,255,.08) !important;
    color:#dde3f0 !important; border-radius:8px !important;
}

/* ══ MOBILE ══ */
@media (max-width:768px) {
    .kpi-grid { grid-template-columns:repeat(2,1fr); }
    .kpi-value { font-size:18px; }
    .block-container { padding:.6rem .6rem 2rem; }
    .htable { font-size:11px; }
    .htable td, .htable th { padding:6px 6px; }
    .war-title { font-size:18px; }
}
@media (max-width:480px) {
    .kpi-grid { grid-template-columns:1fr 1fr; }
}

/* ══ MISC ══ */
.stDataFrame { border:1px solid rgba(255,255,255,.06); border-radius:10px; overflow:hidden; }
.positive { color:#4ade80; } .negative { color:#f87171; }
hr { border-color:rgba(255,255,255,.05); }
</style>
""", unsafe_allow_html=True)


# ════════════════════════════════════════════════════════════════════════════════
# CONSTANTS & BASELINE DATA
# ════════════════════════════════════════════════════════════════════════════════

ANALYST_TARGETS = {
    "NVDA":650,"META":720,"GOOGL":230,"AAPL":240,"MSFT":480,
    "NFLX":1100,"COST":1050,"TSM":220,"CRM":310,"QCOM":175,
    "WMT":95,"BRK-B":480,"RDDT":160,"ALK":70,"SNOW":200,
    "BMWYY":55,"VOO":620,"QQQ":570,"VTI":290,"VGT":750,
    "VHT":290,"VIS":340,"VYM":155,"SCHD":38,"VXUS":80,
    "GLD":450,"XLE":100,"BTC":120000,"XRP":4.5,
    "BLSH":60,"KLAR":70,"STUB":35,
    "SPY":540,"VUG":480,"VTV":200,"VEA":66,"VWO":54,"BND":75,
}

FOREVER_HOLD = {"VYM","SCHD"}
ALWAYS_DCA   = {"VOO","QQQ","VTI"}
SELL_FLAGS   = {"SPY","VUG","VTV","VEA","VWO","BND"}
SELL_LT      = {"VTV":"2025-03-01","VEA":"2025-03-01","VWO":"2025-03-01",
                 "BND":"2025-03-01","SPY":"2026-05-20","VUG":"2026-07-15"}
CRYPTO       = {"BTC","XRP"}
IPO_SET      = {"BLSH","KLAR","STUB"}
COINGECKO    = {"BTC":"bitcoin","XRP":"ripple"}

BIWEEKLY_SCHEDULE = [
    {"date":"2026-04-03","amount":900,"picks":"NVDA · VOO · VYM · QQQ · META"},
    {"date":"2026-04-17","amount":900,"picks":"NVDA · VOO · VYM · QQQ · GOOGL"},
    {"date":"2026-05-01","amount":900,"picks":"NVDA · VOO · VYM · QQQ · AAPL"},
    {"date":"2026-05-15","amount":900,"picks":"NVDA · VOO · VYM · QQQ · MSFT"},
    {"date":"2026-05-29","amount":900,"picks":"NVDA · VOO · VYM · QQQ · COST"},
    {"date":"2026-06-12","amount":900,"picks":"NVDA · VOO · VYM · QQQ · TSM"},
    {"date":"2026-06-26","amount":900,"picks":"NVDA · VOO · VYM · QQQ · CRM"},
    {"date":"2026-07-10","amount":900,"picks":"NVDA · VOO · VYM · QQQ · NFLX"},
    {"date":"2026-07-24","amount":900,"picks":"NVDA · VOO · VYM · QQQ · META"},
    {"date":"2026-08-07","amount":900,"picks":"NVDA · VOO · VYM · QQQ · GOOGL"},
    {"date":"2026-08-21","amount":900,"picks":"NVDA · VOO · VYM · QQQ · AAPL"},
    {"date":"2026-09-04","amount":900,"picks":"NVDA · VOO · VYM · QQQ · MSFT"},
    {"date":"2026-09-18","amount":900,"picks":"NVDA · VOO · VYM · QQQ · COST"},
    {"date":"2026-10-02","amount":900,"picks":"NVDA · VOO · VYM · QQQ · TSM"},
    {"date":"2026-10-16","amount":900,"picks":"NVDA · VOO · VYM · QQQ · CRM"},
    {"date":"2026-10-30","amount":900,"picks":"NVDA · VOO · VYM · QQQ · NFLX"},
    {"date":"2026-11-13","amount":900,"picks":"NVDA · VOO · VYM · QQQ · META"},
    {"date":"2026-11-27","amount":900,"picks":"NVDA · VOO · VYM · QQQ · GOOGL"},
    {"date":"2026-12-11","amount":900,"picks":"NVDA · VOO · VYM · QQQ · AAPL"},
]

ACTION_CALENDAR = [
    {"date":"Apr 3",  "type":"sell",   "action":"SELL VTV, VEA, VWO, BND — all LT eligible now → redeploy into VOO/VYM"},
    {"date":"Apr 3",  "type":"deposit","action":"💰 $900 deposit #1 — NVDA/VOO/VYM/QQQ + META"},
    {"date":"Apr 4",  "type":"trim",   "action":"GLD → LT eligible today — trim 25% at $450 target"},
    {"date":"Apr 17", "type":"deposit","action":"💰 $900 deposit #2 — NVDA/VOO/VYM/QQQ + GOOGL"},
    {"date":"May 1",  "type":"deposit","action":"💰 $900 deposit #3 — NVDA/VOO/VYM/QQQ + AAPL"},
    {"date":"May 20", "type":"sell",   "action":"SPY → LT eligible — sell all, reinvest into VOO same day"},
    {"date":"Jul 15", "type":"sell",   "action":"VUG → LT eligible — sell all, reinvest into QQQ"},
    {"date":"Aug 14", "type":"review", "action":"BLSH hits 1 year — evaluate / trim 25%"},
    {"date":"Sep 11", "type":"review", "action":"KLAR hits 1 year — evaluate / trim 25%"},
    {"date":"Sep 18", "type":"review", "action":"STUB hits 1 year — evaluate / trim"},
    {"date":"Nov 6",  "type":"trim",   "action":"TSM big lot → LT — trim 20%"},
    {"date":"Dec 15", "type":"trim",   "action":"GOOGL big lot → LT — trim 20%"},
    {"date":"Dec 20", "type":"tax",    "action":"🧾 Year-end: harvest losses, net gains before Dec 31"},
]

# ── metadata that can't come from CSV (category, LT dates, sell flags) ────────
# These stay as reference — they are NOT used to set share counts.
TICKER_META = {
    "BTC":  {"category":"Crypto", "lt_date":"2024-09-01"},
    "XRP":  {"category":"Crypto", "lt_date":"2024-11-01"},
    "NVDA": {"category":"Stocks", "lt_date":"2024-06-01"},
    "META": {"category":"Stocks", "lt_date":"2025-03-01"},
    "GOOGL":{"category":"Stocks", "lt_date":"2024-12-01"},
    "AAPL": {"category":"Stocks", "lt_date":"2024-03-01"},
    "MSFT": {"category":"Stocks", "lt_date":"2024-03-01"},
    "NFLX": {"category":"Stocks", "lt_date":"2024-06-01"},
    "COST": {"category":"Stocks", "lt_date":"2024-08-01"},
    "TSM":  {"category":"Stocks", "lt_date":"2024-11-01"},
    "CRM":  {"category":"Stocks", "lt_date":"2024-09-01"},
    "QCOM": {"category":"Stocks", "lt_date":"2024-03-01"},
    "WMT":  {"category":"Stocks", "lt_date":"2024-03-01"},
    "BRK-B":{"category":"Stocks", "lt_date":"2024-06-01"},
    "BRK.B":{"category":"Stocks", "lt_date":"2024-06-01"},
    "RDDT": {"category":"Stocks", "lt_date":"2025-03-01"},
    "ALK":  {"category":"Stocks", "lt_date":"2025-04-01"},
    "SNOW": {"category":"Stocks", "lt_date":"2025-04-01"},
    "BMWYY":{"category":"Stocks", "lt_date":"2025-03-01"},
    "BLSH": {"category":"Stocks", "lt_date":"2026-08-14"},
    "KLAR": {"category":"Stocks", "lt_date":"2026-09-11"},
    "STUB": {"category":"Stocks", "lt_date":"2026-09-18"},
    "VOO":  {"category":"ETFs",   "lt_date":"2024-03-01"},
    "QQQ":  {"category":"ETFs",   "lt_date":"2024-03-01"},
    "VTI":  {"category":"ETFs",   "lt_date":"2024-03-01"},
    "VGT":  {"category":"ETFs",   "lt_date":"2024-03-01"},
    "VHT":  {"category":"ETFs",   "lt_date":"2024-03-01"},
    "VIS":  {"category":"ETFs",   "lt_date":"2024-03-01"},
    "VYM":  {"category":"ETFs",   "lt_date":"2024-03-01"},
    "SCHD": {"category":"ETFs",   "lt_date":"2024-03-01"},
    "VXUS": {"category":"ETFs",   "lt_date":"2024-03-01"},
    "GLD":  {"category":"ETFs",   "lt_date":"2024-04-01"},
    "XLE":  {"category":"ETFs",   "lt_date":"2024-03-01"},
    "SPY":  {"category":"ETFs",   "lt_date":"2025-05-20", "sell_flag":True},
    "VUG":  {"category":"ETFs",   "lt_date":"2025-07-15", "sell_flag":True},
    "VTV":  {"category":"ETFs",   "lt_date":"2024-03-01", "sell_flag":True},
    "VEA":  {"category":"ETFs",   "lt_date":"2024-03-01", "sell_flag":True},
    "VWO":  {"category":"ETFs",   "lt_date":"2024-03-01", "sell_flag":True},
    "BND":  {"category":"ETFs",   "lt_date":"2024-03-01", "sell_flag":True},
    "XOP":  {"category":"ETFs",   "lt_date":"2024-03-01"},
    "CAVA": {"category":"Stocks", "lt_date":"2025-01-01"},
    "RIVN": {"category":"Stocks", "lt_date":"2025-01-01"},
    "AMD":  {"category":"Stocks", "lt_date":"2024-03-01"},
}

ETF_SET = {
    "VOO","QQQ","VTI","VGT","VHT","VIS","VYM","SCHD","VXUS","GLD",
    "XLE","BND","VUG","VTV","VEA","VWO","SPY","XOP",
}

def _infer_category(ticker):
    if ticker in CRYPTO:        return "Crypto"
    if ticker in ETF_SET:       return "ETFs"
    m = TICKER_META.get(ticker) or TICKER_META.get(ticker.replace("-","."))
    if m:                       return m.get("category","Stocks")
    return "Stocks"

def _get_meta(ticker, key, default=""):
    m = TICKER_META.get(ticker) or TICKER_META.get(ticker.replace("-","."), {})
    return m.get(key, default)


# ════════════════════════════════════════════════════════════════════════════════
# CORE: recompute portfolio from transaction store
# This is the ONLY correct way to build the portfolio.
# It replays ALL transactions in chronological order from scratch.
# ════════════════════════════════════════════════════════════════════════════════

def _parse_date(s):
    """Parse M/D/YYYY → datetime for sorting."""
    try:    return datetime.strptime(str(s).strip(), "%m/%d/%Y")
    except:
        try:    return datetime.strptime(str(s).strip(), "%Y-%m-%d")
        except: return datetime.min


def recompute_portfolio(tx_store: dict, manual_overrides: dict = None) -> dict:
    """
    Replay ALL rows in tx_store (sorted oldest→newest) and compute
    current share counts and avg costs from scratch.

    tx_store: {fingerprint: row_dict}  — every unique Robinhood CSV row ever seen
    manual_overrides: {ticker: {"shares":x,"avg_cost":y}} for crypto / manual edits

    Returns portfolio dict: {ticker: {shares, avg_cost, category, lt_date, sell_flag}}
    """
    rows = sorted(tx_store.values(), key=lambda r: _parse_date(r.get("Activity Date","")))

    shares     = defaultdict(float)
    cost_total = defaultdict(float)

    for row in rows:
        ticker = (row.get("Instrument") or "").strip()
        # normalise BRK.B / BRK-B
        if ticker == "BRK.B": ticker = "BRK-B"
        code   = (row.get("Trans Code") or "").strip()
        qty    = _parse_qty(row.get("Quantity",""))
        price  = _parse_dollar(row.get("Price",""))
        amount = _parse_dollar(row.get("Amount",""))

        if code == "Buy":
            if not ticker: continue
            cost = qty * price if price else abs(amount)
            shares[ticker]     += qty
            cost_total[ticker] += cost

        elif code == "Sell":
            if not ticker: continue
            held = shares[ticker]
            if held > 0 and qty > 0:
                # reduce cost basis proportionally
                frac = min(qty / held, 1.0)
                cost_total[ticker] *= (1.0 - frac)
            shares[ticker] = max(0.0, held - qty)

        elif code == "SPL":
            if ticker: shares[ticker] += qty

        elif code in ("REC","SXCH"):
            if ticker: shares[ticker] += qty

        elif code == "LIQ":
            if not ticker: continue
            held = shares[ticker]
            if held > 0 and qty > 0:
                frac = min(qty / held, 1.0)
                cost_total[ticker] *= (1.0 - frac)
            shares[ticker] = max(0.0, held - qty)

    # build portfolio dict — only positions with shares remaining
    portfolio = {}
    for ticker, sh in shares.items():
        if sh < 0.00001: continue
        avg = cost_total[ticker] / sh if sh else 0.0
        portfolio[ticker] = {
            "shares":    round(sh, 6),
            "avg_cost":  round(avg, 4),
            "category":  _infer_category(ticker),
            "lt_date":   _get_meta(ticker, "lt_date", ""),
            "sell_flag": _get_meta(ticker, "sell_flag", ticker in SELL_FLAGS),
        }

    # apply manual overrides (crypto, manual edits)
    if manual_overrides:
        for ticker, ov in manual_overrides.items():
            if ticker in portfolio:
                portfolio[ticker].update(ov)
            else:
                portfolio[ticker] = {
                    "shares":    ov.get("shares", 0),
                    "avg_cost":  ov.get("avg_cost", 0),
                    "category":  _infer_category(ticker),
                    "lt_date":   _get_meta(ticker, "lt_date", ""),
                    "sell_flag": False,
                }

    return portfolio


# ── EMPTY baseline — no hardcoded positions, CSV is the only truth ────────────
# (kept for backwards compat with any code that references BASELINE_PORTFOLIO)
BASELINE_PORTFOLIO = {}



# ════════════════════════════════════════════════════════════════════════════════
# CSV PARSER — returns raw rows keyed by fingerprint for the tx_store
# ════════════════════════════════════════════════════════════════════════════════

TX_CODES = {"Buy","Sell","CDIV","SPL","ACH","RTP","LIQ","REC","SXCH","DFEE","DTAX","MISC"}

def _parse_dollar(s):
    s = str(s or "").strip().replace(",","").replace("$","").replace("(", "-").replace(")","")
    try:    return float(s)
    except: return 0.0

def _parse_qty(s):
    try:    return float(str(s or "").strip())
    except: return 0.0

def _tx_fingerprint(row: dict) -> str:
    """
    Deterministic SHA-1 fingerprint for one Robinhood CSV row.
    Uses: Activity Date | Trans Code | Instrument | Quantity | Amount | Price
    Same row always produces the same hash regardless of which CSV file it's from.
    """
    key = "|".join([
        (row.get("Activity Date") or "").strip(),
        (row.get("Trans Code")    or "").strip(),
        (row.get("Instrument")    or "").strip(),
        (row.get("Quantity")      or "").strip(),
        (row.get("Amount")        or "").strip(),
        (row.get("Price")         or "").strip(),
    ])
    return hashlib.sha1(key.encode()).hexdigest()


def ingest_csv(source, existing_tx_store: dict) -> dict:
    """
    Parse a Robinhood activity CSV and return an updated tx_store.

    source           : file-like object (st.file_uploader) OR str content
    existing_tx_store: {fingerprint: row_dict} — transactions already known

    Returns:
      {
        "tx_store":   dict  — updated store (existing + new rows merged in)
        "new_count":  int   — genuinely new rows added
        "skip_count": int   — rows already in store (skipped)
        "total_rows": int   — total valid-code rows in this file
        "new_rows":   list  — the new row dicts for display
        "cash_in":    float — cash deposit total in this file
        "buys":       int
        "sells":      int
        "drip":       int
      }
    """
    # ── normalise source to string ───────────────────────────────────────────
    if hasattr(source, "read"):
        raw = source.read()
        if isinstance(raw, bytes):
            for enc in ("utf-8", "utf-8-sig", "latin-1", "cp1252"):
                try:    content = raw.decode(enc); break
                except: continue
            else:
                content = raw.decode("latin-1", errors="replace")
    else:
        content = str(source)

    # ── strip Robinhood disclaimer footer ────────────────────────────────────
    lines = content.splitlines()
    clean = []
    for line in lines:
        if "data provided is for informational" in line.lower(): break
        clean.append(line)
    content = "\n".join(clean)

    reader = csv.DictReader(
        io.StringIO(content),
        quoting=csv.QUOTE_ALL,
        skipinitialspace=True,
    )

    new_store   = dict(existing_tx_store)  # copy so we can compare
    new_rows    = []
    total_rows  = new_count = skip_count = 0
    buys = sells = drip_ct = 0
    cash_in = 0.0

    for row in reader:
        code = (row.get("Trans Code") or "").strip()
        if not code or code not in TX_CODES:
            continue
        total_rows += 1

        fp = _tx_fingerprint(row)

        if fp in existing_tx_store:
            skip_count += 1
            continue   # already known — skip

        # genuinely new row
        new_count += 1
        new_store[fp] = dict(row)
        new_rows.append(dict(row))

        # stats for summary display
        desc = (row.get("Description") or "").replace("\n"," ")
        if code == "Buy":
            buys += 1
            if "reinvestment" in desc.lower(): drip_ct += 1
        elif code in ("Sell","LIQ"):
            sells += 1
        elif code in ("ACH","RTP"):
            cash_in += abs(_parse_dollar(row.get("Amount","")))

    return {
        "tx_store":   new_store,
        "new_count":  new_count,
        "skip_count": skip_count,
        "total_rows": total_rows,
        "new_rows":   new_rows,
        "cash_in":    cash_in,
        "buys":       buys,
        "sells":      sells,
        "drip":       drip_ct,
    }



# ════════════════════════════════════════════════════════════════════════════════
# PRICE FETCHER
# ════════════════════════════════════════════════════════════════════════════════

def fetch_all_prices(tickers: list) -> dict:
    prices = {}
    stock_tickers  = [t for t in tickers if t not in CRYPTO and t != "_fetched_at"]
    crypto_tickers = [t for t in tickers if t in CRYPTO]

    # ── stocks via yfinance ──
    if stock_tickers:
        try:
            import yfinance as yf
            joined = " ".join(stock_tickers)
            data = yf.download(joined, period="2d", auto_adjust=True, progress=False, threads=True)
            closes = data["Close"] if "Close" in data.columns else data
            for t in stock_tickers:
                try:
                    if hasattr(closes, "columns") and t in closes.columns:
                        series = closes[t].dropna()
                    elif len(stock_tickers) == 1:
                        series = closes.squeeze().dropna()
                    else:
                        continue
                    if len(series) >= 2:
                        p, prev = float(series.iloc[-1]), float(series.iloc[-2])
                        prices[t] = {"price":p, "chg_pct":(p-prev)/prev*100, "src":"yf"}
                    elif len(series) == 1:
                        prices[t] = {"price":float(series.iloc[-1]), "chg_pct":0.0, "src":"yf"}
                except Exception:
                    continue
        except Exception:
            pass

    # ── crypto via CoinGecko ──
    if crypto_tickers:
        try:
            import requests
            ids = ",".join(COINGECKO[t] for t in crypto_tickers if t in COINGECKO)
            url = f"https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd&include_24hr_change=true"
            r = requests.get(url, timeout=8)
            r.raise_for_status()
            d = r.json()
            for t in crypto_tickers:
                cg = COINGECKO.get(t)
                if cg and cg in d:
                    prices[t] = {"price":d[cg]["usd"],"chg_pct":d[cg].get("usd_24h_change",0),"src":"cg"}
        except Exception:
            pass

    prices["_ts"] = datetime.now().strftime("%b %d %Y %H:%M:%S")
    return prices


# ════════════════════════════════════════════════════════════════════════════════
# RECOMMENDATION ENGINE  — fully dynamic, recalculates from live price
# ════════════════════════════════════════════════════════════════════════════════

def is_lt(lt_date_str):
    if not lt_date_str: return False
    try:    return date.today() >= date.fromisoformat(lt_date_str)
    except: return False

def generate_recs(portfolio: dict, prices: dict) -> list:
    recs = []
    for ticker, pos in portfolio.items():
        shares   = pos.get("shares", 0)
        cost     = pos.get("avg_cost", 0)
        lt_date  = pos.get("lt_date", "")
        lt_elig  = is_lt(lt_date)
        sell_flg = pos.get("sell_flag", ticker in SELL_FLAGS)
        lp       = prices.get(ticker, {}).get("price") if isinstance(prices.get(ticker), dict) else None
        target   = ANALYST_TARGETS.get(ticker)

        tax_note = f"✅ LT ({lt_date})" if lt_elig else f"⚠️ ST → LT {lt_date}"
        action = rationale = ""
        trim_pct = None

        # priority 1 — income ETFs
        if ticker in FOREVER_HOLD:
            action    = "♾ HOLD FOREVER"
            rationale = "Dividend compounding — never sell, DRIP on."

        # priority 2 — core index
        elif ticker in ALWAYS_DCA:
            action    = "📈 DCA ALWAYS"
            rationale = "Core index — DCA every deposit, never stop."

        # priority 3 — sell list
        elif sell_flg or ticker in SELL_FLAGS:
            sell_lt = SELL_LT.get(ticker, lt_date)
            if is_lt(sell_lt):
                action    = "🔴 SELL NOW"
                tax_note  = "✅ LT — 15-20% rate, reinvest proceeds"
                rationale = f"On SELL list. LT eligible. Exit & redeploy to VOO/VYM."
            else:
                action    = f"⏳ WAIT → SELL {sell_lt}"
                tax_note  = f"⚠️ ST now (37%) → wait for {sell_lt}"
                rationale = "Hold until LT to avoid 37% ordinary income rate."

        # priority 4 — stop-loss
        elif ticker not in CRYPTO and lp and cost:
            bear = cost * 0.80
            if lp <= bear * 1.10:
                action    = "🚨 STOP-LOSS REVIEW"
                rationale = f"Price {_fd(lp)} within 10% of bear case {_fd(bear)}. Review thesis."

        # priority 5 — crypto
        elif ticker in CRYPTO:
            if lp and target:
                up = (target - lp) / lp * 100
                if up > 25:
                    action    = "🚀 ACCUMULATE"
                    rationale = f"Target {_fd(target)}, {up:.0f}% upside from {_fd(lp)}."
                elif up < -20:
                    action    = "✂️ TRIM 25%"
                    trim_pct  = 25
                    rationale = f"Above target {_fd(target)}. Lock 25%."
                else:
                    action    = "⏸ HOLD"
                    rationale = f"In range. Target {_fd(target)}."
            else:
                action = "⏸ HOLD"; rationale = "Accumulate on dips."

        # priority 6 — IPO
        elif ticker in IPO_SET:
            action    = f"🔒 HOLD — IPO"
            rationale = f"Hold until LT eligible ({lt_date}), then evaluate."

        # priority 7 — normal
        elif not lp or not target:
            action = "⏸ HOLD"; rationale = "No live price — monitoring."
        else:
            up  = (target - lp) / lp * 100
            dip = (lp - cost) / cost * 100 if cost else 0
            if up > 20:
                action    = "🟢 STRONG BUY" if dip < -8 else "🟢 ACCUMULATE"
                rationale = f"Target {_fd(target)} = {up:.0f}% upside. {'On dip — prime entry.' if dip<-8 else ''}"
            elif up < -15 and lt_elig:
                action    = "✂️ TRIM 20%"
                trim_pct  = 20
                tax_note  = "✅ LT — lock gains at 15-20%"
                rationale = f"Above target. LT eligible — harvest 20% of position."
            elif up < -15:
                action    = "⏸ HOLD (near target, ST)"
                rationale = f"Near target but ST — wait until {lt_date}."
            elif dip < -5:
                action    = "🟡 DIP BUY"
                rationale = f"Minor dip ({dip:.1f}%). Add small at {_fd(lp)}."
            else:
                action    = "⏸ HOLD"
                rationale = f"Fair value. Target {_fd(target)}, upside {up:.1f}%."

        mv       = lp * shares if lp else cost * shares
        gl       = (lp - cost) * shares if lp else None
        pct_gain = (lp - cost) / cost * 100 if lp and cost else None

        recs.append({
            "ticker":    ticker,
            "action":    action,
            "tax_note":  tax_note,
            "rationale": rationale,
            "trim_pct":  trim_pct,
            "live_price":lp,
            "market_val":mv,
            "gain_loss": gl,
            "pct_gain":  pct_gain,
            "shares":    shares,
            "avg_cost":  cost,
            "category":  pos.get("category","Stocks"),
            "lt_date":   lt_date,
            "sell_flag": sell_flg,
        })
    return recs

def _fd(v):
    if v is None: return "—"
    return f"${v:,.0f}" if v > 999 else f"${v:,.2f}"

def _fp(v):
    if v is None: return "—"
    return f"{'+' if v>=0 else ''}{v:.1f}%"


# ════════════════════════════════════════════════════════════════════════════════
# SESSION STATE INIT
# ════════════════════════════════════════════════════════════════════════════════

def _ss_init():
    defaults = {
        # tx_store is the ONLY source of truth for positions.
        # {fingerprint: row_dict} — every unique Robinhood CSV row ever ingested.
        # Portfolio is always recomputed from this store.
        "tx_store":        {},
        # manual overrides for crypto / positions not in Robinhood CSV
        "manual_overrides":{"BTC":{"shares":0.03433,"avg_cost":52800.00},"XRP":{"shares":1.066,"avg_cost":0.68}},
        "prices":          {},
        "last_refresh":    None,
        "cash":            1042.17,
        "active_card":     None,
        "deposit_log":     [],
        "rec_history":     [],
        "import_log":      [],
        "drip_log":        {},
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

_ss_init()


# ════════════════════════════════════════════════════════════════════════════════
# COMPUTED VARS — derived fresh every render from tx_store
# ════════════════════════════════════════════════════════════════════════════════

# Build portfolio by replaying all transactions
portfolio = recompute_portfolio(
    st.session_state.tx_store,
    st.session_state.manual_overrides,
)
prices     = st.session_state.prices
cash       = st.session_state.cash
recs       = generate_recs(portfolio, prices)
rec_map    = {r["ticker"]: r for r in recs}

sell_recs  = [r for r in recs if "SELL" in r["action"] or "STOP" in r["action"]]
trim_recs  = [r for r in recs if "TRIM" in r["action"]]
buy_recs   = [r for r in recs if any(k in r["action"] for k in ("BUY","DCA","ACCUM","FOREVER"))]

total_mv   = sum(r["market_val"] or 0 for r in recs)
total_gl   = sum(r["gain_loss"] or 0 for r in recs if r["gain_loss"] is not None)
total_cost = sum((r["avg_cost"] or 0) * r["shares"] for r in recs)
total_pct  = total_gl / total_cost * 100 if total_cost else 0
equity     = total_mv + cash
sell_proceeds_est = sum(r["market_val"] or 0 for r in sell_recs)


# ════════════════════════════════════════════════════════════════════════════════
# HEADER
# ════════════════════════════════════════════════════════════════════════════════

hc1, hc2 = st.columns([4, 1])
with hc1:
    ts = st.session_state.last_refresh or "— prices not loaded —"
    st.markdown(f"""
    <div class='war-header'>
      <div>
        <div class='war-title'>⚡ Portfolio <span>War Room</span></div>
        <div class='war-ts'>prashanthkrishnan91 · {len(portfolio)} positions · refreshed {ts}</div>
      </div>
    </div>""", unsafe_allow_html=True)
with hc2:
    if st.button("🔄 Refresh Prices", use_container_width=True):
        tickers = list(portfolio.keys())
        with st.spinner("Fetching live prices…"):
            st.session_state.prices = fetch_all_prices(tickers)
        st.session_state.last_refresh = st.session_state.prices.get("_ts","—")
        st.rerun()


# ════════════════════════════════════════════════════════════════════════════════
# KPI CARDS  (clickable)
# ════════════════════════════════════════════════════════════════════════════════

def toggle_card(key):
    st.session_state.active_card = None if st.session_state.active_card == key else key

gl_color   = "kpi-green" if total_gl >= 0 else "kpi-red"
gl_sign    = "▲" if total_gl >= 0 else "▼"
gl_style   = "color:#4ade80" if total_gl >= 0 else "color:#f87171"

st.markdown(f"""
<div class='kpi-grid'>
  <div class='kpi-card {gl_color}'>
    <div class='kpi-label'>Total Equity</div>
    <div class='kpi-value'>{_fd(equity)}</div>
    <div class='kpi-sub'>{gl_sign} {_fd(abs(total_gl))} ({_fp(total_pct)})</div>
    <div class='kpi-click'>▼ tap for breakdown</div>
  </div>
  <div class='kpi-card kpi-red'>
    <div class='kpi-label'>Sell Alerts</div>
    <div class='kpi-value' style='color:#f87171'>{len(sell_recs)}</div>
    <div class='kpi-sub'>Positions to exit</div>
    <div class='kpi-click'>▼ tap for list</div>
  </div>
  <div class='kpi-card kpi-yellow'>
    <div class='kpi-label'>Trim Alerts</div>
    <div class='kpi-value' style='color:#fbbf24'>{len(trim_recs)}</div>
    <div class='kpi-sub'>Take partial profits</div>
    <div class='kpi-click'>▼ tap for list</div>
  </div>
  <div class='kpi-card kpi-green'>
    <div class='kpi-label'>Buy Signals</div>
    <div class='kpi-value' style='color:#4ade80'>{len(buy_recs)}</div>
    <div class='kpi-sub'>Accumulate / DCA</div>
    <div class='kpi-click'>▼ tap for list</div>
  </div>
  <div class='kpi-card kpi-blue'>
    <div class='kpi-label'>Cash Available</div>
    <div class='kpi-value' style='color:#60a5fa'>{_fd(cash)}</div>
    <div class='kpi-sub'>+{_fd(sell_proceeds_est)} if sells done</div>
    <div class='kpi-click'>▼ tap to deploy</div>
  </div>
</div>
""", unsafe_allow_html=True)

# card click buttons (hidden labels)
cc1,cc2,cc3,cc4,cc5 = st.columns(5)
with cc1:
    if st.button("Equity ▼", key="c_eq",  use_container_width=True): toggle_card("eq");  st.rerun()
with cc2:
    if st.button("Sell ▼",   key="c_sell", use_container_width=True): toggle_card("sell"); st.rerun()
with cc3:
    if st.button("Trim ▼",   key="c_trim", use_container_width=True): toggle_card("trim"); st.rerun()
with cc4:
    if st.button("Buy ▼",    key="c_buy",  use_container_width=True): toggle_card("buy");  st.rerun()
with cc5:
    if st.button("Cash ▼",   key="c_cash", use_container_width=True): toggle_card("cash"); st.rerun()

# ── drill panels ──────────────────────────────────────────────────────────────
active = st.session_state.active_card

if active == "eq":
    cats = {"Stocks":0,"ETFs":0,"Crypto":0}
    for r in recs:
        cats[r["category"]] = cats.get(r["category"],0) + (r["market_val"] or 0)
    rows = "".join(f"<div class='drill-row'><span class='dk'>{k}</span><span class='dv'>{_fd(v)}</span></div>" for k,v in cats.items())
    rows += f"""
    <div class='drill-row'><span class='dk'>Cash</span><span class='dv'>{_fd(cash)}</span></div>
    <div class='drill-row'><span class='dk'>Total Cost Basis</span><span class='dv'>{_fd(total_cost)}</span></div>
    <div class='drill-row'><span class='dk'>Unrealized Gain</span><span class='dv {"dg" if total_gl>=0 else "dr"}'>{_fd(total_gl)}</span></div>
    <div class='drill-row'><span class='dk'>Overall Return</span><span class='dv {"dg" if total_pct>=0 else "dr"}'>{_fp(total_pct)}</span></div>"""
    st.markdown(f"<div class='drill'><div class='drill-title'>📊 Portfolio Breakdown</div>{rows}</div>", unsafe_allow_html=True)

elif active == "sell":
    rows = ""
    for r in sell_recs:
        gl  = r.get("gain_loss",0) or 0
        rows += f"""
        <div class='drill-row'>
          <span class='dk'><b style='color:#f87171'>{r['ticker']}</b> — {r['action']}</span>
          <span class='dv'>{_fd(r.get('market_val'))} · <span class='{"dg" if gl>=0 else "dr"}'>{_fd(gl)}</span> · <span class='dy'>{r['tax_note']}</span></span>
        </div>
        <div style='font-size:11px;color:#2e4060;padding:2px 0 6px 12px'>{r['rationale']}</div>"""
    if not rows: rows = "<div style='color:#2e4060'>No sell alerts right now.</div>"
    st.markdown(f"<div class='drill'><div class='drill-title'>🔴 Sell Alerts — Act Now</div>{rows}</div>", unsafe_allow_html=True)

elif active == "trim":
    rows = ""
    for r in trim_recs:
        gl  = r.get("gain_loss",0) or 0
        pct = r.get("trim_pct",20)
        est = (r.get("market_val") or 0) * pct/100
        rows += f"""
        <div class='drill-row'>
          <span class='dk'><b style='color:#fbbf24'>{r['ticker']}</b> — Trim {pct}%</span>
          <span class='dv'>Est proceeds {_fd(est)} · <span class='dg'>{_fd(gl)}</span> gain · <span class='dy'>{r['tax_note']}</span></span>
        </div>
        <div style='font-size:11px;color:#2e4060;padding:2px 0 6px 12px'>{r['rationale']}</div>"""
    if not rows: rows = "<div style='color:#2e4060'>No trim alerts right now.</div>"
    st.markdown(f"<div class='drill'><div class='drill-title'>✂️ Trim Alerts</div>{rows}</div>", unsafe_allow_html=True)

elif active == "buy":
    rows = ""
    for r in buy_recs:
        rows += f"""
        <div class='drill-row'>
          <span class='dk'><b style='color:#4ade80'>{r['ticker']}</b> — {r['action']}</span>
          <span class='dv'>{_fd(r.get('live_price'))} live · <span class='dy'>{r['tax_note']}</span></span>
        </div>
        <div style='font-size:11px;color:#2e4060;padding:2px 0 6px 12px'>{r['rationale']}</div>"""
    if not rows: rows = "<div style='color:#2e4060'>No buy signals.</div>"
    st.markdown(f"<div class='drill'><div class='drill-title'>🟢 Buy / Accumulate</div>{rows}</div>", unsafe_allow_html=True)

elif active == "cash":
    total_dep = cash + sell_proceeds_est + 900
    plan = [("NVDA",0.28,"AI supercycle"),("VOO",0.22,"S&P 500 DCA"),("VYM",0.17,"Dividend engine"),("QQQ",0.17,"Nasdaq-100"),("META",0.16,"Rotating pick")]
    rows = f"""
    <div class='drill-row'><span class='dk'>Current cash</span><span class='dv dg'>{_fd(cash)}</span></div>
    <div class='drill-row'><span class='dk'>Sell proceeds (est)</span><span class='dv dy'>{_fd(sell_proceeds_est)}</span></div>
    <div class='drill-row'><span class='dk'>Next $900 deposit</span><span class='dv'>$900.00</span></div>
    <div class='drill-row'><span class='dk'><b>Total deployable</b></span><span class='dv dg'><b>{_fd(total_dep)}</b></span></div>"""
    rows += "<br><div style='font-size:10px;color:#3d5478;letter-spacing:.1em;margin-bottom:6px'>ALLOCATION PLAN</div>"
    for t,pct,note in plan:
        amt = total_dep * pct
        rows += f"<div class='drill-row'><span class='dk'>{t} ({int(pct*100)}%) — {note}</span><span class='dv dg'>{_fd(amt)}</span></div>"
    st.markdown(f"<div class='drill'><div class='drill-title'>💵 Cash Deploy Plan</div>{rows}</div>", unsafe_allow_html=True)


# ════════════════════════════════════════════════════════════════════════════════
# MAIN TABS
# ════════════════════════════════════════════════════════════════════════════════

tabs = st.tabs(["📋 Holdings","💰 Cash & Deploy","📥 Import","📈 DRIP","🗓 History","⚙️ Settings"])


# ══════════════════════════════════════════════════
# TAB 1 — HOLDINGS
# ══════════════════════════════════════════════════
with tabs[0]:
    st.markdown("<div class='sec-head'>📋 All Holdings</div>", unsafe_allow_html=True)

    fc1,fc2,fc3 = st.columns([2,2,2])
    with fc1: f_cat = st.selectbox("Category", ["All","Stocks","ETFs","Crypto","🔴 Sell List"], key="f_cat")
    with fc2: f_sig = st.selectbox("Signal",   ["All","SELL","TRIM","BUY","HOLD"],              key="f_sig")
    with fc3: f_srt = st.selectbox("Sort by",  ["Ticker","Value ↓","P&L $ ↓","P&L % ↓"],       key="f_srt")

    sell_set = {r["ticker"] for r in sell_recs}

    def passes(r):
        if f_cat == "🔴 Sell List" and r["ticker"] not in sell_set: return False
        if f_cat not in ("All","🔴 Sell List") and r["category"] != f_cat: return False
        a = r["action"].upper()
        if f_sig == "SELL" and "SELL" not in a and "STOP" not in a: return False
        if f_sig == "TRIM" and "TRIM" not in a:                      return False
        if f_sig == "BUY"  and not any(k in a for k in ("BUY","DCA","ACCUM","FOREVER")): return False
        if f_sig == "HOLD" and "HOLD" not in a and "LOCK" not in a: return False
        return True

    filtered = [r for r in recs if passes(r)]
    if f_srt == "Value ↓":   filtered.sort(key=lambda r: r["market_val"] or 0, reverse=True)
    elif f_srt == "P&L $ ↓": filtered.sort(key=lambda r: r["gain_loss"] or -9e9, reverse=True)
    elif f_srt == "P&L % ↓": filtered.sort(key=lambda r: r["pct_gain"] or -9e9, reverse=True)
    else:                     filtered.sort(key=lambda r: r["ticker"])

    if not filtered:
        st.info("No positions match the selected filters.")
    else:
        def badge(action):
            a = action.upper()
            if "SELL" in a or "STOP" in a: return "<span class='b b-sell'>SELL</span>"
            if "TRIM" in a:                return "<span class='b b-trim'>TRIM</span>"
            if "FOREVER" in a or "DCA" in a: return "<span class='b b-dca'>DCA</span>"
            if "BUY" in a or "ACCUM" in a: return "<span class='b b-buy'>BUY</span>"
            if "LOCK" in a:                return "<span class='b b-lock'>LOCK</span>"
            return "<span class='b b-hold'>HOLD</span>"

        rows_html = ""
        for r in filtered:
            loss = r["gain_loss"] is not None and r["gain_loss"] < 0
            rc   = "row-loss" if loss else ""
            gl_c = "row-loss-pnl" if loss else "row-gain-pnl"
            sell_dot = " 🔴" if r["ticker"] in sell_set else ""
            rows_html += f"""
            <tr class='{rc}'>
              <td>{r['ticker']}{sell_dot}</td>
              <td>{r['shares']:.4f}</td>
              <td>{_fd(r['avg_cost'])}</td>
              <td>{_fd(r['live_price'])}</td>
              <td>{_fd(r['market_val'])}</td>
              <td class='{gl_c}'>{_fd(r['gain_loss'])}</td>
              <td class='{gl_c}'>{_fp(r['pct_gain'])}</td>
              <td style='text-align:center'>{badge(r['action'])}</td>
              <td style='font-size:11px;color:#3d5478;max-width:220px;white-space:normal'>{r['tax_note']}</td>
            </tr>"""

        st.markdown(f"""
        <div style='overflow-x:auto'>
        <table class='htable'>
          <thead>
            <tr><th>Ticker</th><th>Shares</th><th>Avg Cost</th><th>Live</th>
                <th>Mkt Value</th><th>P&L $</th><th>P&L %</th><th>Signal</th><th>Tax</th></tr>
          </thead>
          <tbody>{rows_html}</tbody>
        </table>
        </div>""", unsafe_allow_html=True)

    # ── Sell list detail ──
    st.markdown("<div class='sec-head'>🔴 Sell List — Full Detail</div>", unsafe_allow_html=True)
    if sell_recs:
        for r in sell_recs:
            gl = r.get("gain_loss",0) or 0
            st.markdown(f"""
            <div class='cash-card' style='border-color:rgba(248,113,113,.25)'>
              <div class='cash-head' style='color:#f87171'>🔴 {r['ticker']} — {r['action']}</div>
              <div class='cash-row'><span>Live Price</span><span class='cash-amt'>{_fd(r['live_price'])}</span></div>
              <div class='cash-row'><span>Market Value</span><span class='cash-amt'>{_fd(r['market_val'])}</span></div>
              <div class='cash-row'><span>P&L</span><span style='color:{"#4ade80" if gl>=0 else "#f87171"}'>{_fd(gl)}</span></div>
              <div class='cash-row'><span>Tax Note</span><span style='color:#fbbf24'>{r['tax_note']}</span></div>
              <div style='font-size:11px;color:#4a6080;margin-top:8px'>{r['rationale']}</div>
            </div>""", unsafe_allow_html=True)
    else:
        st.info("✅ No active sell alerts right now.")

    # ── Charts (if plotly available) ──
    try:
        import plotly.graph_objects as go
        import plotly.express as px

        st.markdown("<div class='sec-head'>📊 Allocation & P&L Charts</div>", unsafe_allow_html=True)
        ch1, ch2 = st.columns(2)

        with ch1:
            cat_vals = {"Stocks":0,"ETFs":0,"Crypto":0}
            for r in recs:
                cat_vals[r["category"]] = cat_vals.get(r["category"],0) + (r["market_val"] or 0)
            fig_pie = go.Figure(go.Pie(
                labels=list(cat_vals.keys()),
                values=list(cat_vals.values()),
                hole=0.6,
                marker_colors=["#4ade80","#60a5fa","#f59e0b"],
                textinfo="label+percent",
                textfont=dict(color="#dde3f0",size=11),
            ))
            fig_pie.update_layout(
                paper_bgcolor="#0b1220",plot_bgcolor="#0b1220",
                showlegend=False,margin=dict(t=20,b=10,l=10,r=10),
                height=240,
                annotations=[dict(text="Allocation",x=0.5,y=0.5,font_color="#60a5fa",font_size=13,showarrow=False)]
            )
            st.plotly_chart(fig_pie, use_container_width=True, config={"displayModeBar":False})

        with ch2:
            top = sorted(recs, key=lambda r: abs(r["gain_loss"] or 0), reverse=True)[:12]
            bar_x = [r["ticker"] for r in top]
            bar_y = [r["gain_loss"] or 0 for r in top]
            bar_c = ["#4ade80" if v>=0 else "#f87171" for v in bar_y]
            fig_bar = go.Figure(go.Bar(x=bar_x, y=bar_y, marker_color=bar_c, text=[_fd(v) for v in bar_y], textposition="outside"))
            fig_bar.update_layout(
                paper_bgcolor="#0b1220",plot_bgcolor="#0b1220",
                font_color="#dde3f0",
                xaxis=dict(tickfont=dict(size=9)),
                yaxis=dict(showgrid=True,gridcolor="#111c30"),
                margin=dict(t=20,b=10,l=10,r=10),height=240,showlegend=False
            )
            st.plotly_chart(fig_bar, use_container_width=True, config={"displayModeBar":False})
    except ImportError:
        pass


# ══════════════════════════════════════════════════
# TAB 2 — CASH & DEPLOY
# ══════════════════════════════════════════════════
with tabs[1]:
    st.markdown("<div class='sec-head'>💰 Cash & Deploy Plan</div>", unsafe_allow_html=True)

    col_ca, col_cb = st.columns(2)
    with col_ca:
        total_deployable = cash + sell_proceeds_est
        st.markdown(f"""
        <div class='cash-card'>
          <div class='cash-head'>💵 Cash Summary</div>
          <div class='cash-row'><span>Current cash</span><span class='cash-amt'>{_fd(cash)}</span></div>
          <div class='cash-row'><span>Est. sell proceeds ({len(sell_recs)} positions)</span><span class='cash-amt'>{_fd(sell_proceeds_est)}</span></div>
          <div class='cash-row'><span>Next $900 deposit (Apr 3)</span><span class='cash-amt'>$900.00</span></div>
          <div class='cash-row'><span><b>Total deployable</b></span><span class='cash-amt' style='font-size:16px'>{_fd(total_deployable+900)}</span></div>
        </div>""", unsafe_allow_html=True)

    with col_cb:
        new_cash = st.number_input("Update Cash Balance ($)", value=float(cash), step=0.01, format="%.2f")
        if st.button("💾 Save Cash Balance"):
            st.session_state.cash = new_cash
            st.success(f"Cash updated → {_fd(new_cash)}")
            st.rerun()

    st.markdown("<div class='sec-head'>🎯 Deploy $1,042 Current Cash</div>", unsafe_allow_html=True)
    cash_plan = [
        ("NVDA",0.28,"AI supercycle — core conviction at dip"),
        ("VOO", 0.22,"S&P 500 DCA — never stop buying"),
        ("VYM", 0.17,"Dividend engine — compound income"),
        ("QQQ", 0.17,"Nasdaq-100 — tech backbone"),
        ("META",0.16,"Rotating pick — strong momentum"),
    ]
    cp1,cp2 = st.columns(2)
    for i,(ticker,pct,note) in enumerate(cash_plan):
        with (cp1 if i%2==0 else cp2):
            lp = (prices.get(ticker) or {}).get("price") if isinstance(prices.get(ticker),dict) else None
            amt = cash * pct
            sh_est = amt/lp if lp else None
            st.markdown(f"""
            <div class='cash-card'>
              <div class='cash-head'>{ticker} — {int(pct*100)}%</div>
              <div class='cash-row'><span>Amount</span><span class='cash-amt'>{_fd(amt)}</span></div>
              <div class='cash-row'><span>Live price</span><span class='cash-amt'>{_fd(lp)}</span></div>
              <div class='cash-row'><span>Est. shares</span><span class='cash-amt'>{f"{sh_est:.4f}" if sh_est else "—"}</span></div>
              <div style='font-size:11px;color:#3d5478;margin-top:6px'>{note}</div>
            </div>""", unsafe_allow_html=True)

    if sell_proceeds_est > 10:
        st.markdown("<div class='sec-head'>♻️ Redeploy Sell Proceeds</div>", unsafe_allow_html=True)
        st.markdown(f"<div style='color:#4a6080;font-size:13px;margin-bottom:12px'>After {len(sell_recs)} sells, ~{_fd(sell_proceeds_est)} freed. Redeploy:</div>", unsafe_allow_html=True)
        for t,pct in [("VOO",0.40),("VYM",0.30),("QQQ",0.30)]:
            amt = sell_proceeds_est*pct
            lp  = (prices.get(t) or {}).get("price") if isinstance(prices.get(t),dict) else None
            st.markdown(f"""
            <div class='cash-card'>
              <div class='cash-head'>→ {t} ({int(pct*100)}%)</div>
              <div class='cash-row'><span>Amount</span><span class='cash-amt'>{_fd(amt)}</span></div>
              <div class='cash-row'><span>Est shares</span><span class='cash-amt'>{f"{amt/lp:.4f}" if lp else "—"}</span></div>
            </div>""", unsafe_allow_html=True)

    st.markdown("<div class='sec-head'>📅 Biweekly $900 Schedule — 2026</div>", unsafe_allow_html=True)
    today_str = date.today().strftime("%Y-%m-%d")
    sc1,sc2,sc3 = st.columns(3)
    for i,dep in enumerate(BIWEEKLY_SCHEDULE):
        past = dep["date"] <= today_str
        with [sc1,sc2,sc3][i%3]:
            bg  = "#0b1220" if past else "#0d1830"
            bdr = "#1f2d45" if past else "#1d4ed8"
            ck  = "✅" if past else "🔜"
            st.markdown(f"""
            <div style='background:{bg};border:1px solid {bdr};border-radius:10px;padding:10px 13px;margin-bottom:8px'>
              <div style='font-family:IBM Plex Mono,monospace;font-size:10px;color:#60a5fa'>{ck} {dep["date"]}</div>
              <div style='font-size:13px;font-weight:600;margin-top:3px'>${dep["amount"]:,}</div>
              <div style='font-size:10px;color:#3d5478;margin-top:2px'>{dep["picks"]}</div>
            </div>""", unsafe_allow_html=True)

    st.markdown("<div class='sec-head'>➕ Log a Deposit</div>", unsafe_allow_html=True)
    dl1,dl2,dl3 = st.columns(3)
    with dl1: dep_dt  = st.date_input("Date", value=date.today())
    with dl2: dep_amt = st.number_input("Amount ($)", value=900.0, step=50.0)
    with dl3: dep_note= st.text_input("Picks", "NVDA/VOO/VYM/QQQ")
    if st.button("➕ Log Deposit"):
        st.session_state.deposit_log.append({"date":str(dep_dt),"amount":dep_amt,"picks":dep_note})
        st.session_state.cash += dep_amt
        st.success(f"Logged {_fd(dep_amt)} on {dep_dt}")
        st.rerun()
    if st.session_state.deposit_log:
        st.dataframe(pd.DataFrame(st.session_state.deposit_log), use_container_width=True)

    # Action calendar
    st.markdown("<div class='sec-head'>📅 Action Calendar 2026</div>", unsafe_allow_html=True)
    for item in ACTION_CALENDAR:
        color = {"sell":"#f87171","trim":"#fbbf24","deposit":"#4ade80","review":"#60a5fa","tax":"#a78bfa"}.get(item["type"],"#60a5fa")
        st.markdown(f"""
        <div class='cal-item' style='border-left-color:{color}'>
          <div class='cal-date'>{item['date']}</div>
          <div>{item['action']}</div>
        </div>""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════
# TAB 3 — IMPORT
# ══════════════════════════════════════════════════
with tabs[2]:
    st.markdown("<div class='sec-head'>📥 Import Activity</div>", unsafe_allow_html=True)

    store_size = len(st.session_state.tx_store)
    st.markdown(f"""
    <div style='background:#0b1220;border:1px solid rgba(255,255,255,.06);border-radius:10px;padding:14px 16px;margin-bottom:16px;font-size:13px;color:#4a6080'>
      Upload <b>any Robinhood activity CSV</b> — new, old, partial, or a full re-export.
      Every row is fingerprinted. Rows already in the transaction store are <b>silently skipped</b>.
      Only genuinely new rows are added, then the portfolio is <b>recomputed from scratch</b>.
      <br><br>
      <span style='color:#60a5fa;font-family:IBM Plex Mono,monospace'>
        {store_size:,} transactions in store · safe to re-upload any file
      </span>
    </div>
    """, unsafe_allow_html=True)

    imp1, imp2 = st.columns(2)

    with imp1:
        st.markdown("**📄 Robinhood Activity CSV**")
        csv_file = st.file_uploader(
            "Drop CSV here or click to browse",
            type=["csv"],
            key="csv_up",
            label_visibility="visible",
        )
        if csv_file:
            st.markdown(f"*Uploaded: `{csv_file.name}`*")
            if st.button("⚙️ Parse & Preview Changes", key="btn_csv"):
                with st.spinner("Parsing & deduplicating…"):
                    try:
                        result = ingest_csv(csv_file, st.session_state.tx_store)

                        total_rows = result["total_rows"]
                        new_count  = result["new_count"]
                        skip_count = result["skip_count"]

                        # ── dedup summary ────────────────────────────────────
                        if new_count == 0:
                            st.warning(f"⚠️ All {total_rows} rows already in store — nothing new. Portfolio unchanged.")
                        else:
                            if skip_count > 0:
                                st.info(f"🔁 {skip_count} of {total_rows} rows already seen — skipped.")
                            st.success(f"✅ {new_count} new rows · {result['buys']} buys · {result['sells']} sells · {result['drip']} DRIP")
                        if result["cash_in"]:
                            st.info(f"💵 Cash deposits in file: {_fd(result['cash_in'])}")

                        if new_count > 0:
                            # compute what portfolio will look like after adding new rows
                            new_portfolio = recompute_portfolio(
                                result["tx_store"],
                                st.session_state.manual_overrides,
                            )

                            # ── diff: old vs new ────────────────────────────
                            diff_rows = []
                            all_tickers = set(portfolio.keys()) | set(new_portfolio.keys())
                            for t in sorted(all_tickers):
                                old_sh  = portfolio.get(t, {}).get("shares", 0)
                                new_sh  = new_portfolio.get(t, {}).get("shares", 0)
                                old_avg = portfolio.get(t, {}).get("avg_cost", 0)
                                new_avg = new_portfolio.get(t, {}).get("avg_cost", 0)
                                delta_sh = new_sh - old_sh
                                if abs(delta_sh) < 0.00001 and abs(new_avg - old_avg) < 0.001:
                                    continue
                                chg = "ADDED" if old_sh == 0 else "REMOVED" if new_sh == 0 else "MODIFIED"
                                diff_rows.append({
                                    "Ticker":       t,
                                    "Change":       chg,
                                    "Old Shares":   round(old_sh, 6),
                                    "New Shares":   round(new_sh, 6),
                                    "Δ Shares":     round(delta_sh, 6),
                                    "Old Avg Cost": _fd(old_avg),
                                    "New Avg Cost": _fd(new_avg),
                                })

                            st.markdown("**Holdings Changes Preview**")
                            if diff_rows:
                                st.dataframe(pd.DataFrame(diff_rows), use_container_width=True)
                            else:
                                st.info("New transactions found but no net holdings change.")

                            # store pending
                            st.session_state["_pending_store"]     = result["tx_store"]
                            st.session_state["_pending_portfolio"]  = new_portfolio
                            st.session_state["_pending_result"]     = result
                            st.session_state["_pending_diff"]       = diff_rows
                            st.session_state["_pending_file"]       = csv_file.name

                    except Exception as e:
                        st.error(f"Parse error: {e}")
                        import traceback
                        st.code(traceback.format_exc())

        # ── confirm apply ────────────────────────────────────────────────────
        if "_pending_store" in st.session_state:
            st.warning("⚠️ Preview ready — apply when satisfied.")
            if st.button("✅ Apply to Portfolio", key="btn_apply"):
                result   = st.session_state["_pending_result"]
                fname    = st.session_state["_pending_file"]
                diff     = st.session_state["_pending_diff"]

                # commit new tx_store — portfolio auto-recomputes next render
                st.session_state.tx_store = st.session_state["_pending_store"]

                # log
                st.session_state.import_log.append({
                    "date":    datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "file":    fname,
                    "tx_new":  result["new_count"],
                    "tx_skip": result["skip_count"],
                    "buys":    result["buys"],
                    "sells":   result["sells"],
                    "drip":    result["drip"],
                    "changes": len(diff),
                    "diff":    diff,
                })

                for k in ("_pending_store","_pending_portfolio","_pending_result","_pending_diff","_pending_file"):
                    st.session_state.pop(k, None)

                st.success(f"✅ Done! Transaction store now has {len(st.session_state.tx_store):,} rows. Refresh prices.")
                st.rerun()

    with imp2:
        st.markdown("**📜 Robinhood Crypto PDF Statement**")
        pdf_file = st.file_uploader(
            "Drop PDF here or click to browse",
            type=["pdf"],
            key="pdf_up",
            label_visibility="visible",
        )
        if pdf_file:
            st.markdown(f"*Uploaded: `{pdf_file.name}`*")
            if st.button("⚙️ Extract Crypto Data", key="btn_pdf"):
                with st.spinner("Extracting from PDF…"):
                    text = ""
                    pdf_bytes = pdf_file.read()
                    try:
                        import pdfplumber, io as _io
                        with pdfplumber.open(_io.BytesIO(pdf_bytes)) as pdf:
                            text = "\n".join(p.extract_text() or "" for p in pdf.pages)
                    except Exception:
                        try:
                            import PyPDF2, io as _io
                            reader = PyPDF2.PdfReader(_io.BytesIO(pdf_bytes))
                            text = "\n".join(pg.extract_text() or "" for pg in reader.pages)
                        except Exception as e2:
                            st.warning(f"PDF library error: {e2}. Install pdfplumber.")

                    if text:
                        st.text_area("Extracted text (first 2000 chars)", text[:2000], height=180)
                        btc_m = re.search(r"(?i)bitcoin[^\d]*([\d.]+)\s*(?:BTC)?", text)
                        xrp_m = re.search(r"(?i)(?:XRP|Ripple)[^\d]*([\d.]+)", text)
                        found = []
                        if btc_m: found.append(("BTC", float(btc_m.group(1))))
                        if xrp_m: found.append(("XRP", float(xrp_m.group(1))))
                        if found:
                            st.success(f"Detected: {', '.join(f'{t}: {s}' for t,s in found)}")
                            if st.button("✅ Update Crypto"):
                                for t, s in found:
                                    st.session_state.manual_overrides[t] = {"shares": s}
                                st.success("Crypto updated!")
                                st.rerun()
                        else:
                            st.info("Couldn't auto-detect — use Settings → Manual Override.")
                    else:
                        st.error("No text extracted. Use Settings to enter manually.")

    # import history
    if st.session_state.import_log:
        st.markdown("<div class='sec-head'>📋 Import History</div>", unsafe_allow_html=True)
        for imp in reversed(st.session_state.import_log):
            with st.expander(f"📁 {imp['date']} — {imp['file']} ({imp['changes']} changes)"):
                c1,c2,c3,c4,c5 = st.columns(5)
                c1.metric("New Rows",  imp.get("tx_new",0))
                c2.metric("Skipped",   imp.get("tx_skip",0))
                c3.metric("Buys",      imp.get("buys",0))
                c4.metric("Sells",     imp.get("sells",0))
                c5.metric("DRIP",      imp.get("drip",0))
                if imp.get("diff"):
                    st.dataframe(pd.DataFrame(imp["diff"]), use_container_width=True)



# ══════════════════════════════════════════════════
# TAB 4 — DRIP
# ══════════════════════════════════════════════════
with tabs[3]:
    st.markdown("<div class='sec-head'>📈 DRIP Analytics</div>", unsafe_allow_html=True)

    # baseline DRIP data
    drip_data = {
        "VYM":{"reinvested":65.12,"shares":0.46512,"events":8},
        "VOO":{"reinvested":36.47,"shares":0.06235,"events":6},
        "AAPL":{"reinvested":18.87,"shares":0.07762,"events":7},
        "XLE":{"reinvested":18.66,"shares":0.29084,"events":5},
        "VXUS":{"reinvested":16.15,"shares":0.21327,"events":6},
        "SCHD":{"reinvested":14.92,"shares":0.49108,"events":4},
        "QQQ":{"reinvested":12.50,"shares":0.02220,"events":4},
        "QCOM":{"reinvested":10.20,"shares":0.07860,"events":5},
    }
    # merge any live drip data
    for t, events in st.session_state.drip_log.items():
        if events:
            tot = sum(e.get("amount",0) for e in events)
            shr = sum(e.get("qty",0)    for e in events)
            if t in drip_data:
                drip_data[t]["reinvested"] += tot
                drip_data[t]["shares"]     += shr
                drip_data[t]["events"]     += len(events)
            else:
                drip_data[t] = {"reinvested":tot,"shares":shr,"events":len(events)}

    total_reinvested = sum(v["reinvested"] for v in drip_data.values())
    total_drip_ev    = sum(v["events"]     for v in drip_data.values())

    d1,d2,d3,d4 = st.columns(4)
    d1.metric("Total Events",     total_drip_ev)
    d2.metric("Total Reinvested", f"${total_reinvested:,.2f}")
    d3.metric("Total Declared",   "$290.07")
    d4.metric("Tickers w/ DRIP",  len(drip_data))

    st.markdown("**Per-Ticker DRIP Breakdown**")
    rows = sorted(drip_data.items(), key=lambda x: x[1]["reinvested"], reverse=True)
    html = "".join(f"""
    <div class='cash-card' style='margin-bottom:8px'>
      <div class='cash-head'>{t}</div>
      <div class='cash-row'><span>Reinvested</span><span class='cash-amt'>${v["reinvested"]:,.2f}</span></div>
      <div class='cash-row'><span>Shares from DRIP</span><span class='cash-amt'>{v["shares"]:.5f}</span></div>
      <div class='cash-row'><span>Events</span><span class='cash-amt'>{v["events"]}</span></div>
    </div>""" for t,v in rows)

    dr1,dr2 = st.columns(2)
    items = list(enumerate(rows))
    for i,(t,v) in items[:len(items)//2+1]:
        with dr1:
            st.markdown(f"""
            <div class='cash-card' style='margin-bottom:8px'>
              <div class='cash-head'>{t}</div>
              <div class='cash-row'><span>Reinvested</span><span class='cash-amt'>${v["reinvested"]:,.2f}</span></div>
              <div class='cash-row'><span>DRIP Shares</span><span class='cash-amt'>{v["shares"]:.5f}</span></div>
              <div class='cash-row'><span>Events</span><span class='cash-amt'>{v["events"]}</span></div>
            </div>""", unsafe_allow_html=True)
    for i,(t,v) in items[len(items)//2+1:]:
        with dr2:
            st.markdown(f"""
            <div class='cash-card' style='margin-bottom:8px'>
              <div class='cash-head'>{t}</div>
              <div class='cash-row'><span>Reinvested</span><span class='cash-amt'>${v["reinvested"]:,.2f}</span></div>
              <div class='cash-row'><span>DRIP Shares</span><span class='cash-amt'>{v["shares"]:.5f}</span></div>
              <div class='cash-row'><span>Events</span><span class='cash-amt'>{v["events"]}</span></div>
            </div>""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════
# TAB 5 — HISTORY
# ══════════════════════════════════════════════════
with tabs[4]:
    st.markdown("<div class='sec-head'>🗓 Recommendation History</div>", unsafe_allow_html=True)

    if st.button("📸 Snapshot Today's Recommendations"):
        snap = {
            "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "count": len(recs),
            "recs": [{"ticker":r["ticker"],"action":r["action"],"live":_fd(r["live_price"]),"tax":r["tax_note"],"note":r["rationale"]} for r in recs]
        }
        st.session_state.rec_history.append(snap)
        st.success("Snapshot saved!")

    if st.session_state.rec_history:
        for snap in reversed(st.session_state.rec_history):
            with st.expander(f"📅 {snap['date']} — {snap['count']} recommendations"):
                st.dataframe(pd.DataFrame(snap["recs"]), use_container_width=True)
    else:
        st.info("No snapshots yet. Hit 'Snapshot' to archive today's recommendations.")

    st.markdown("<div class='sec-head'>📋 Current Recommendations</div>", unsafe_allow_html=True)
    df_recs = pd.DataFrame([{
        "Ticker":    r["ticker"],
        "Action":    r["action"],
        "Live Price":_fd(r["live_price"]),
        "Market Val":_fd(r["market_val"]),
        "P&L":       _fd(r["gain_loss"]),
        "Tax Note":  r["tax_note"],
        "Rationale": r["rationale"],
    } for r in recs])
    st.dataframe(df_recs, use_container_width=True)


# ══════════════════════════════════════════════════
# TAB 6 — SETTINGS
# ══════════════════════════════════════════════════
with tabs[5]:
    st.markdown("<div class='sec-head'>⚙️ Settings & Manual Overrides</div>", unsafe_allow_html=True)

    s1,s2 = st.columns(2)
    with s1:
        st.markdown("**Manual Override** *(for crypto or any position not in your CSV)*")
        ov_t   = st.text_input("Ticker (e.g. BTC)", key="ov_t").upper().strip()
        ov_s   = st.number_input("Shares", value=0.0, step=0.0001, format="%.4f", key="ov_s")
        ov_c   = st.number_input("Avg Cost ($)", value=0.0, step=0.01, format="%.2f", key="ov_c")
        if st.button("💾 Save Override"):
            if ov_t:
                st.session_state.manual_overrides[ov_t] = {"shares": ov_s, "avg_cost": ov_c}
                st.success(f"Override saved for {ov_t}")
                st.rerun()

        # show current overrides
        if st.session_state.manual_overrides:
            st.markdown("**Current overrides:**")
            for t, v in st.session_state.manual_overrides.items():
                c1, c2, c3 = st.columns([2,2,1])
                c1.markdown(f"`{t}` — {v.get('shares',0):.4f} sh @ {_fd(v.get('avg_cost',0))}")
                if c3.button("✕", key=f"rm_ov_{t}"):
                    del st.session_state.manual_overrides[t]
                    st.rerun()

    with s2:
        st.markdown("**Export computed portfolio**")
        if st.button("📥 Export Portfolio CSV"):
            rows = [{"Ticker":t,"Shares":v["shares"],"Avg Cost":v["avg_cost"],
                     "Category":v["category"],"LT Date":v.get("lt_date","")}
                    for t,v in portfolio.items()]
            csv_out = pd.DataFrame(rows).to_csv(index=False)
            st.download_button("⬇️ Download", csv_out, "portfolio.csv", "text/csv")

        st.markdown("**Reset**")
        if st.button("🔄 Clear all — start fresh", type="secondary"):
            st.session_state.tx_store         = {}
            st.session_state.manual_overrides = {"BTC":{"shares":0.03433,"avg_cost":52800.00},"XRP":{"shares":1.066,"avg_cost":0.68}}
            st.session_state.prices           = {}
            st.session_state.cash             = 1042.17
            st.session_state.active_card      = None
            st.session_state.import_log       = []
            st.session_state.drip_log         = {}
            st.success("Cleared. Upload your CSV to rebuild.")
            st.rerun()

        st.markdown("**Transaction Store**")
        n = len(st.session_state.tx_store)
        st.markdown(f"<div style='color:#4a6080;font-size:12px;margin-bottom:8px'>{n:,} unique transactions stored. Clear only to re-import from scratch.</div>", unsafe_allow_html=True)
        if st.button("🗑 Clear Transaction Store", type="secondary"):
            st.session_state.tx_store = {}
            st.success("Store cleared — re-upload your CSV to rebuild.")
            st.rerun()

    st.markdown("---")
    st.markdown(f"<div style='color:#1e3a5f;font-size:11px;font-family:IBM Plex Mono,monospace'>Portfolio War Room v7.0 · {datetime.now().strftime('%b %d, %Y')} · {len(portfolio)} positions · prashanthkrishnan91</div>", unsafe_allow_html=True)

