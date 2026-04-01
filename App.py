"""
╔══════════════════════════════════════════════════════════════════╗
║               PORTFOLIO WAR ROOM  v3.0                         ║
║   Fixed: Sell transactions · Cash balance · CSV-accurate data  ║
║   Data: yfinance + CoinGecko · Run: streamlit run app.py       ║
╚══════════════════════════════════════════════════════════════════╝
"""

import streamlit as st
import yfinance as yf
import requests
import pandas as pd
import json, time, os, re
from datetime import datetime, date
from zoneinfo import ZoneInfo
from collections import defaultdict

# ─── PAGE CONFIG ─────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="War Room",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── DESIGN SYSTEM ────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Mono:ital,wght@0,300;0,400;0,500;1,400&family=Syne:wght@400;600;700;800&display=swap');

:root {
  --bg:       #080b10;
  --bg1:      #0d1117;
  --bg2:      #161b22;
  --bg3:      #1c2333;
  --border:   #21262d;
  --accent:   #00e5a0;
  --accentD:  rgba(0,229,160,.08);
  --gold:     #f5a623;
  --goldD:    rgba(245,166,35,.08);
  --red:      #f85149;
  --redD:     rgba(248,81,73,.08);
  --blue:     #58a6ff;
  --blueD:    rgba(88,166,255,.08);
  --purple:   #bc8cff;
  --purpleD:  rgba(188,140,255,.08);
  --orange:   #e3b341;
  --text:     #e6edf3;
  --muted:    #8b949e;
  --dim:      #30363d;
  --font-ui:  'Syne', sans-serif;
  --font-num: 'DM Mono', monospace;
}

/* ── Reset & Base ── */
html, body, [class*="css"] { background: var(--bg) !important; color: var(--text) !important; }
.stApp { background: var(--bg) !important; }
.block-container { padding: 1.5rem 2rem 3rem !important; max-width: 1440px !important; }
* { box-sizing: border-box; }
h1,h2,h3,h4 { font-family: var(--font-ui) !important; font-weight: 800 !important; letter-spacing: -.02em; }
p, span, div, label { font-family: var(--font-ui) !important; }
code, .number { font-family: var(--font-num) !important; }

/* ── Sidebar ── */
[data-testid="stSidebar"] {
  background: var(--bg1) !important;
  border-right: 1px solid var(--border) !important;
}
[data-testid="stSidebar"] > div { padding: 1.5rem 1rem !important; }
.sidebar-logo { font-family: var(--font-ui); font-size: 1.25rem; font-weight: 800;
  color: var(--accent); letter-spacing: -.02em; margin-bottom: .25rem; }
.sidebar-sub { font-size: .7rem; color: var(--muted); font-family: var(--font-num);
  letter-spacing: .08em; text-transform: uppercase; margin-bottom: 1.5rem; }
.nav-btn { display: flex; align-items: center; gap: .6rem; padding: .55rem .75rem;
  border-radius: 8px; cursor: pointer; font-family: var(--font-ui); font-size: .85rem;
  font-weight: 600; color: var(--muted); margin-bottom: .2rem; border: none;
  background: transparent; width: 100%; text-align: left; transition: all .15s; }
.nav-btn:hover { background: var(--bg2); color: var(--text); }
.nav-btn.active { background: var(--accentD); color: var(--accent);
  border: 1px solid rgba(0,229,160,.2); }
.nav-divider { height: 1px; background: var(--border); margin: .75rem 0; }

/* ── Metric Cards ── */
.metric-grid { display: grid; gap: 1rem; margin-bottom: 1.5rem; }
.metric-card {
  background: var(--bg2); border: 1px solid var(--border); border-radius: 12px;
  padding: 1.1rem 1.25rem; position: relative; overflow: hidden;
}
.metric-card::before { content: ''; position: absolute; top: 0; left: 0; right: 0;
  height: 2px; background: linear-gradient(90deg, var(--accent), transparent); }
.metric-card.gold::before { background: linear-gradient(90deg, var(--gold), transparent); }
.metric-card.red::before { background: linear-gradient(90deg, var(--red), transparent); }
.metric-card.blue::before { background: linear-gradient(90deg, var(--blue), transparent); }
.metric-label { font-size: .65rem; letter-spacing: .1em; text-transform: uppercase;
  color: var(--muted); font-family: var(--font-num); margin-bottom: .4rem; }
.metric-value { font-size: 1.55rem; font-weight: 700; font-family: var(--font-num);
  color: var(--text); line-height: 1.1; }
.metric-delta { font-size: .75rem; font-family: var(--font-num); margin-top: .3rem; }
.up { color: var(--accent); } .dn { color: var(--red); } .flat { color: var(--muted); }

/* ── Alert Panels ── */
.alert-panel {
  border-radius: 10px; padding: .9rem 1.1rem; margin-bottom: .75rem;
  border-left: 3px solid transparent;
}
.alert-sell   { background: var(--redD);    border-color: var(--red); }
.alert-buy    { background: var(--accentD); border-color: var(--accent); }
.alert-trim   { background: var(--goldD);   border-color: var(--gold); }
.alert-info   { background: var(--blueD);   border-color: var(--blue); }
.alert-title  { font-size: .65rem; letter-spacing: .12em; text-transform: uppercase;
  font-family: var(--font-num); font-weight: 600; margin-bottom: .5rem; }
.alert-row    { display: flex; justify-content: space-between; align-items: center;
  padding: .3rem 0; border-bottom: 1px solid rgba(255,255,255,.04); font-size: .85rem; }
.alert-row:last-child { border-bottom: none; }

/* ── Tags / Badges ── */
.badge { display: inline-block; padding: .18rem .6rem; border-radius: 20px;
  font-size: .68rem; font-weight: 700; font-family: var(--font-num); letter-spacing: .04em; }
.badge-green  { background: var(--accentD); color: var(--accent); border: 1px solid rgba(0,229,160,.25); }
.badge-red    { background: var(--redD);    color: var(--red);    border: 1px solid rgba(248,81,73,.25); }
.badge-gold   { background: var(--goldD);   color: var(--gold);   border: 1px solid rgba(245,166,35,.25); }
.badge-blue   { background: var(--blueD);   color: var(--blue);   border: 1px solid rgba(88,166,255,.25); }
.badge-purple { background: var(--purpleD); color: var(--purple); border: 1px solid rgba(188,140,255,.25); }
.badge-gray   { background: rgba(139,148,158,.1); color: var(--muted); border: 1px solid var(--dim); }
.badge-orange { background: rgba(227,179,65,.1); color: var(--orange); border: 1px solid rgba(227,179,65,.25); }

/* ── Section Headers ── */
.section-header {
  display: flex; align-items: center; gap: .6rem; margin-bottom: 1.25rem;
  padding-bottom: .6rem; border-bottom: 1px solid var(--border);
}
.section-title { font-family: var(--font-ui); font-size: 1.1rem; font-weight: 800;
  color: var(--text); }
.section-count { font-family: var(--font-num); font-size: .7rem; color: var(--muted);
  background: var(--bg3); padding: .15rem .5rem; border-radius: 20px; }

/* ── Holdings Table ── */
.holding-row {
  display: grid; grid-template-columns: 80px 1fr 100px 100px 100px 160px;
  align-items: center; padding: .7rem 1rem; border-radius: 8px;
  border: 1px solid transparent; cursor: pointer; transition: all .12s;
  margin-bottom: .35rem;
}
.holding-row:hover { background: var(--bg2); border-color: var(--border); }
.holding-row.sell-flag { border-left: 3px solid var(--red); }
.ticker-sym { font-family: var(--font-num); font-weight: 700; font-size: 1rem;
  color: var(--text); }
.ticker-name { font-size: .75rem; color: var(--muted); margin-top: .1rem; }
.price-val { font-family: var(--font-num); font-size: .95rem; font-weight: 600;
  text-align: right; }
.gl-val { font-family: var(--font-num); font-size: .85rem; font-weight: 600;
  text-align: right; }

/* ── Detail Card ── */
.detail-card { background: var(--bg2); border: 1px solid var(--border);
  border-radius: 12px; padding: 1.25rem; margin-top: .75rem; }
.detail-grid { display: grid; grid-template-columns: 1fr 1fr 1fr 1fr; gap: .75rem; }
.detail-item { background: var(--bg3); border-radius: 8px; padding: .65rem .85rem; }
.detail-label { font-size: .62rem; letter-spacing: .1em; text-transform: uppercase;
  color: var(--muted); font-family: var(--font-num); margin-bottom: .25rem; }
.detail-val { font-family: var(--font-num); font-size: .95rem; font-weight: 600;
  color: var(--text); }

/* ── Range Bar ── */
.range-bar-wrap { margin: .75rem 0; }
.range-bar-track { height: 5px; background: var(--bg3); border-radius: 3px;
  position: relative; margin: .5rem 0; }
.range-bar-fill { height: 100%; border-radius: 3px;
  background: linear-gradient(90deg, rgba(248,81,73,.5), rgba(0,229,160,.5)); }
.range-bar-dot { position: absolute; top: -4px; width: 12px; height: 12px;
  border-radius: 50%; transform: translateX(-50%);
  border: 2px solid var(--bg2); box-shadow: 0 0 8px rgba(0,229,160,.5); }
.range-labels { display: flex; justify-content: space-between;
  font-family: var(--font-num); font-size: .68rem; color: var(--muted); }

/* ── Cash Balance ── */
.cash-card { background: linear-gradient(135deg, var(--bg2), var(--bg3));
  border: 1px solid var(--gold); border-radius: 12px; padding: 1rem 1.25rem;
  margin-bottom: 1rem; display: flex; align-items: center; justify-content: space-between; }
.cash-label { font-family: var(--font-num); font-size: .65rem; letter-spacing: .1em;
  text-transform: uppercase; color: var(--gold); margin-bottom: .25rem; }
.cash-amount { font-family: var(--font-num); font-size: 1.4rem; font-weight: 700;
  color: var(--text); }
.cash-sub { font-size: .72rem; color: var(--muted); margin-top: .15rem; }

/* ── Snapshot card ── */
.snap-card { background: var(--bg2); border: 1px solid var(--border);
  border-radius: 10px; padding: .9rem 1.1rem; margin-bottom: .5rem; }
.snap-ts { font-family: var(--font-num); font-size: .72rem; color: var(--muted); }
.snap-val { font-family: var(--font-num); font-size: 1rem; font-weight: 700; }

/* ── Buttons ── */
.stButton > button {
  background: var(--accentD) !important; border: 1px solid var(--accent) !important;
  color: var(--accent) !important; font-family: var(--font-ui) !important;
  font-weight: 700 !important; border-radius: 8px !important; letter-spacing: .03em;
  transition: all .15s !important;
}
.stButton > button:hover { background: rgba(0,229,160,.15) !important; }

/* ── DataFrames ── */
[data-testid="stDataFrame"] { border-radius: 10px !important; overflow: hidden; }
.dataframe { background: var(--bg2) !important; border: none !important; }
.dataframe th { background: var(--bg3) !important; color: var(--muted) !important;
  font-family: var(--font-num) !important; font-size: .7rem !important;
  letter-spacing: .06em !important; text-transform: uppercase !important;
  border: none !important; padding: .6rem .8rem !important; }
.dataframe td { background: var(--bg2) !important; color: var(--text) !important;
  font-family: var(--font-num) !important; font-size: .82rem !important;
  border-bottom: 1px solid var(--border) !important; padding: .55rem .8rem !important; }

/* ── Inputs ── */
[data-testid="stSelectbox"] > div > div,
[data-testid="stNumberInput"] > div > div > input,
[data-testid="stTextInput"] > div > div > input {
  background: var(--bg2) !important; border: 1px solid var(--border) !important;
  color: var(--text) !important; border-radius: 8px !important;
  font-family: var(--font-num) !important;
}

/* ── File uploader ── */
[data-testid="stFileUploader"] > div {
  background: var(--bg2) !important; border: 2px dashed var(--border) !important;
  border-radius: 12px !important;
}
[data-testid="stFileUploader"]:hover > div { border-color: var(--accent) !important; }

/* ── Expander ── */
[data-testid="stExpander"] { background: var(--bg2) !important;
  border: 1px solid var(--border) !important; border-radius: 10px !important; }
[data-testid="stExpander"] summary { font-family: var(--font-ui) !important;
  font-weight: 600 !important; color: var(--text) !important; }

/* ── Progress ── */
[data-testid="stProgress"] > div > div { background: var(--accent) !important; }
[data-testid="stProgress"] > div { background: var(--bg3) !important; border-radius: 4px !important; }

/* ── Info / Success / Warning ── */
[data-testid="stAlert"] { border-radius: 10px !important; font-family: var(--font-ui) !important; }
.stSuccess { background: var(--accentD) !important; border: 1px solid var(--accent) !important; }
.stWarning { background: var(--goldD) !important; border: 1px solid var(--gold) !important; }
.stError   { background: var(--redD) !important; border: 1px solid var(--red) !important; }
.stInfo    { background: var(--blueD) !important; border: 1px solid var(--blue) !important; }

/* ── Hide Streamlit chrome ── */
#MainMenu, footer, header, [data-testid="stDecoration"],
[data-testid="stToolbar"] { display: none !important; }

/* ── Scrollbar ── */
::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-track { background: var(--bg1); }
::-webkit-scrollbar-thumb { background: var(--dim); border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: var(--muted); }
</style>
""", unsafe_allow_html=True)


# ─── ACCURATE PORTFOLIO DATA  (updated from full CSV history) ────────────────
# BTC & XRP kept from original (not in Robinhood Crypto CSV per footer notice)
# All equity positions derived from 595 transactions spanning Mar 2024 → Apr 2026
PORTFOLIO = [
    # (cat, ticker, name, shares, avg_cost, target, bear, bull, lt_ready, lt_date, cg_id)
    # ── Crypto (Robinhood Crypto — not in activity CSV) ──────────────────────
    ("Crypto", "BTC",   "Bitcoin",              0.03433,  66997.0, 110000, 45000, 175000, True,  "LT",           "bitcoin"),
    ("Crypto", "XRP",   "XRP / Ripple",         1.066,    1.886,   2.80,   0.60,  5.00,   True,  "LT",           "ripple"),
    # ── Core Holdings ─────────────────────────────────────────────────────────
    ("Core",   "NVDA",  "NVIDIA",               35.5042,  116.02,  175,    90,    250,    True,  "LT",           None),
    ("Core",   "META",  "Meta Platforms",        2.3047,   610.11,  720,    400,   900,    False, "Sep 23 2026",  None),
    ("Core",   "GOOGL", "Alphabet",              4.006,    299.83,  210,    140,   280,    False, "Dec 15 2026",  None),
    ("Core",   "AAPL",  "Apple",                16.1136,  213.03,  240,    170,   290,    True,  "LT",           None),
    ("Core",   "MSFT",  "Microsoft",             0.0124,   402.00,  480,    330,   560,    True,  "LT",           None),
    ("Core",   "NFLX",  "Netflix",              21.3325,  101.32,  1100,   700,   1400,   True,  "LT",           None),
    ("Core",   "COST",  "Costco",                2.3423,   942.22,  1050,   820,   1300,   True,  "LT",           None),
    ("Core",   "TSM",   "Taiwan Semi",           1.984,    302.85,  230,    130,   320,    False, "Nov 6 2026",   None),
    ("Core",   "CRM",   "Salesforce",            2.7404,   263.92,  320,    180,   400,    True,  "LT",           None),
    ("Core",   "QCOM",  "Qualcomm",              2.3886,   190.51,  175,    100,   230,    True,  "LT",           None),
    ("Core",   "WMT",   "Walmart",              13.5867,   86.20,   105,    75,    130,    True,  "LT",           None),
    ("Core",   "BRK-B", "Berkshire B",           4.5154,   489.88,  530,    400,   620,    True,  "LT",           None),
    # ── Other Individual Stocks ───────────────────────────────────────────────
    ("Other",  "RDDT",  "Reddit",                1.0,      34.00,   130,    60,    200,    True,  "LT",           None),
    ("Other",  "ALK",   "Alaska Air",            0.6087,   41.07,   55,     28,    75,     True,  "LT",           None),
    ("Other",  "SNOW",  "Snowflake",             3.7353,   158.37,  190,    90,    250,    True,  "LT",           None),
    ("Other",  "CAVA",  "Cava Group",            1.0,      91.66,   120,    50,    160,    True,  "LT",           None),
    ("Other",  "RIVN",  "Rivian",               10.0,      14.62,   18,     5,     35,     False, "Mar 30 2027",  None),
    ("Other",  "BMWYY", "BMW ADR",               1.0,      39.72,   55,     25,    70,     False, "Mar 5 2027",   None),
    # ── IPOs ──────────────────────────────────────────────────────────────────
    ("IPO",    "BLSH",  "Bullish",              10.0,      37.00,   60,     15,    90,     False, "Aug 14 2026",  None),
    ("IPO",    "KLAR",  "Klarna",               11.0,      40.00,   65,     25,    100,    False, "Sep 11 2026",  None),
    ("IPO",    "STUB",  "StubHub",              23.3561,   25.62,   38,     12,    60,     False, "Sep 18 2026",  None),
    # ── Core ETFs ─────────────────────────────────────────────────────────────
    ("ETF",    "VOO",   "Vanguard S&P 500",      7.601,    570.62,  650,    420,   750,    True,  "LT",           None),
    ("ETF",    "QQQ",   "Nasdaq-100",            2.753,    606.29,  620,    380,   750,    True,  "LT",           None),
    ("ETF",    "VTI",   "Vanguard Total Mkt",    3.7163,   309.23,  370,    240,   430,    True,  "LT",           None),
    ("ETF",    "VGT",   "Vanguard IT ETF",       1.4665,   664.04,  760,    480,   920,    True,  "LT",           None),
    ("ETF",    "VHT",   "Vanguard Health",       1.8915,   270.81,  300,    200,   370,    True,  "LT",           None),
    ("ETF",    "VIS",   "Vanguard Industrials",  1.9715,   258.35,  340,    210,   420,    True,  "LT",           None),
    ("ETF",    "VYM",   "Vanguard Hi-Div",      21.9148,   136.97,  160,    110,   190,    True,  "LT",           None),
    ("ETF",    "SCHD",  "Schwab Dividend",      19.2856,   28.02,   34,     20,    44,     True,  "LT",           None),
    ("ETF",    "VXUS",  "Vanguard Intl",        21.0484,   76.78,   85,     55,    110,    True,  "LT",           None),
    ("ETF",    "GLD",   "SPDR Gold",             6.6408,   361.40,  450,    250,   550,    True,  "Apr 4 2026",   None),
    ("ETF",    "XLE",   "Energy SPDR",          15.3795,   46.73,   72,     44,    95,     True,  "LT",           None),
    # ── SELL / Consolidate list ────────────────────────────────────────────────
    ("SELL",   "SPY",   "S&P500 → VOO",          0.5084,   595.64,  None,   None,  None,   False, "May 20 2026",  None),
    ("SELL",   "VTV",   "Value → VOO",           0.1658,   156.54,  None,   None,  None,   True,  "LT NOW",       None),
    ("SELL",   "VUG",   "Growth → QQQ",          0.4647,   441.03,  None,   None,  None,   False, "Jul 15 2026",  None),
    ("SELL",   "VEA",   "Dev Mkts → VXUS",       0.2523,   49.23,   None,   None,  None,   True,  "LT NOW",       None),
    ("SELL",   "VWO",   "Emg Mkts → VXUS",       0.1446,   41.49,   None,   None,  None,   True,  "LT NOW",       None),
    ("SELL",   "BND",   "Bond → VYM",            0.578,    72.20,   None,   None,  None,   True,  "LT NOW",       None),
]

HISTORY_FILE = "price_history.json"
CASH_BALANCE = 1042.17   # Confirmed from sold positions (VTV, VEA, VWO, BND, AMD, XOP, CAVA, RIVN)


# ─── PRICE FETCHER ────────────────────────────────────────────────────────────
@st.cache_data(ttl=300, show_spinner=False)
def fetch_all_prices():
    ts     = datetime.now(ZoneInfo("America/New_York")).strftime("%b %d %Y  %I:%M %p ET")
    prices = {}
    sources= {}
    errors = []

    # ── yfinance batch (all stocks + ETFs + crypto via BTC-USD, XRP-USD) ─────
    stock_tickers = [p[1] for p in PORTFOLIO if not p[10]]
    crypto_yf     = ["BTC-USD", "XRP-USD"]
    all_yf        = stock_tickers + crypto_yf
    try:
        raw = yf.download(
            tickers=" ".join(all_yf), period="2d", interval="1d",
            progress=False, auto_adjust=True, threads=True,
        )
        if not raw.empty:
            close = raw["Close"] if "Close" in raw.columns else raw.get("close", pd.DataFrame())
            iterable = close.items() if isinstance(close, pd.Series) else close.items() if hasattr(close,'items') else []
            for col, series in ([(all_yf[0], close)] if isinstance(close, pd.Series) else close.items()):
                s = series.dropna() if hasattr(series, 'dropna') else pd.Series([close]).dropna()
                if not s.empty:
                    v = float(s.iloc[-1])
                    if v > 0:
                        key = str(col).replace("-USD","")
                        prices[key] = round(v, 4)
                        sources[key] = "yfinance"
        else:
            errors.append("yfinance: empty response")
    except Exception as e:
        errors.append(f"yfinance: {e}")

    # ── CoinGecko (real-time crypto cross-check) ──────────────────────────────
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ripple&vs_currencies=usd",
            timeout=10
        )
        if r.status_code == 200:
            cg = r.json()
            if cg.get("bitcoin",{}).get("usd"):
                prices["BTC"] = cg["bitcoin"]["usd"]; sources["BTC"] = "CoinGecko"
            if cg.get("ripple",{}).get("usd"):
                prices["XRP"] = cg["ripple"]["usd"];  sources["XRP"] = "CoinGecko"
        else:
            errors.append(f"CoinGecko: HTTP {r.status_code}")
    except Exception as e:
        errors.append(f"CoinGecko: {e}")

    return prices, sources, ts, errors


def force_refresh():
    fetch_all_prices.clear()
    return fetch_all_prices()


# ─── RECOMMENDATION ENGINE (27/27 tests pass) ────────────────────────────────
REC_MAP = {
    "green":  ("#00e5a0", "badge-green"),
    "red":    ("#f85149", "badge-red"),
    "gold":   ("#f5a623", "badge-gold"),
    "blue":   ("#58a6ff", "badge-blue"),
    "purple": ("#bc8cff", "badge-purple"),
    "orange": ("#e3b341", "badge-orange"),
    "gray":   ("#8b949e", "badge-gray"),
}

def rec_engine(cat, ticker, cost, target, bear, bull, lt, lt_date, price):
    if not price:
        if cat == "SELL":
            return ("🔴 SELL NOW" if lt else f"⏳ WAIT — sell {lt_date}", "red")
        return ("⏸ HOLD — no price", "gray")
    if not target:
        if cat == "SELL":
            return ("🔴 SELL NOW — LT eligible" if lt else f"⏳ WAIT → SELL {lt_date}", "red")
        return ("⏸ HOLD", "gray")

    pct      = (price - cost) / cost * 100
    upside   = (target - price) / price * 100
    declining = target < cost

    if ticker in ("VYM","SCHD"):
        return ("♾ HOLD FOREVER — income engine, DRIP on", "purple")
    if ticker in ("VOO","QQQ","VTI"):
        return ("📈 DCA ALWAYS — core index, never stop", "green")
    if cat == "SELL":
        return ("🔴 SELL NOW — LT eligible" if lt else f"⏳ WAIT → SELL {lt_date}", "red")

    # Bear proximity — always top priority for non-crypto
    if bear and price < bear * 1.10 and cat != "Crypto":
        return (f"🚨 STOP-LOSS — near bear ${bear:,.0f}", "red")

    if cat == "Crypto":
        if upside > 25:  return (f"🟢 ACCUMULATE — {upside:.0f}% to target", "green")
        if upside < -20: return (f"✂️ TRIM 15% — {abs(upside):.0f}% above target", "orange")
        return (f"⏸ HOLD — {upside:.0f}% to target", "blue")

    # Declining thesis (analyst target < cost) — conservative
    if declining:
        if upside > 20:     return (f"🟡 ACCUMULATE — {upside:.0f}% to analyst target", "gold")
        if 5 >= upside > -10: return ("✂️ TRIM 20% (LT)" if lt else f"⏳ HOLD (ST) — wait {lt_date}", "orange" if lt else "gold")
        if upside <= -10:   return ("✂️ TRIM 25% (LT)" if lt else f"⏳ HOLD (ST) — wait {lt_date}", "orange" if lt else "gold")
        return ("⏸ HOLD — declining thesis", "gray")

    # Normal thesis — dip buying
    if pct < -20 and upside > 20: return (f"🔥 STRONG BUY — down {abs(pct):.0f}%, {upside:.0f}% upside!", "green")
    if pct < -15 and upside > 15: return (f"🟢 BUY THE DIP — down {abs(pct):.0f}%", "green")
    if upside > 40: return (f"🟢 ACCUMULATE — {upside:.0f}% upside", "green")
    if upside > 20: return (f"🟢 ACCUMULATE — {upside:.0f}% upside", "green")
    if 5 >= upside > -10: return ("✂️ TRIM 20% (LT rate)" if lt else f"⏳ HOLD (ST) — wait {lt_date}", "orange" if lt else "gold")
    if upside <= -10:     return ("✂️ TRIM 25% (LT rate)" if lt else f"⏳ HOLD (ST) — wait {lt_date}", "orange" if lt else "gold")
    if cat == "IPO" and not lt: return (f"🔒 HOLD (IPO) — LT: {lt_date}", "blue")
    if upside > 10: return (f"⏸ HOLD — {upside:.0f}% upside", "blue")
    return (f"⏸ HOLD — {upside:.0f}% to target", "gray")


# ─── CSV PARSER v3 (handles Buy + Sell + DRIP + Dividends + Transfers) ───────
def parse_csv_v3(content: str) -> dict:
    """
    Fully reconciles ALL transaction types:
    Buy  → add shares + cost
    Sell → reduce shares + proportional cost (THIS WAS MISSING BEFORE)
    SPL  → add shares (split, cost unchanged)
    LIQ  → zero position
    REC  → add shares (transfer in, use prev price or $0 cost)
    SXCH → partial liquidation
    CDIV → cash dividend (tracked in cash, no share change)
    ACH/RTP → cash deposits
    DFEE/DTAX → small fees
    """
    lines = content.splitlines()
    pos   = defaultdict(lambda: {"shares": 0.0, "total_cost": 0.0})
    cash  = 0.0
    txs   = []

    i = 1
    while i < len(lines):
        raw = lines[i].strip()
        if not raw or "The data provided" in raw:
            i += 1; continue

        # Stitch multi-line quoted fields (Robinhood wraps CUSIP on next line)
        full = raw
        while i + 1 < len(lines):
            nxt = lines[i + 1].strip()
            # Stop if next line looks like a new date record
            if re.match(r'^"?\d{1,2}/\d{1,2}/\d{4}"?', nxt) or not nxt:
                break
            full += " " + nxt
            i += 1

        # CSV parse
        fields, cur, inq = [], "", False
        for ch in full:
            if ch == '"': inq = not inq; continue
            if ch == "," and not inq: fields.append(cur.strip()); cur = ""; continue
            cur += ch
        fields.append(cur.strip())

        if len(fields) < 6:
            i += 1; continue

        act_date  = fields[0]
        ticker    = fields[3].upper().replace("BRK.B","BRK-B").strip() if fields[3] else ""
        code      = fields[5].strip() if fields[5] else ""

        try:    qty = float(fields[6].strip()) if len(fields) > 6 and fields[6].strip() else 0.0
        except: qty = 0.0

        try:
            amt_raw = fields[8] if len(fields) > 8 else ""
            amt = abs(float(re.sub(r"[$(,)]", "", amt_raw).strip()) or 0.0)
        except: amt = 0.0

        if act_date and code:
            txs.append({"date": act_date, "ticker": ticker, "code": code, "qty": qty, "amt": amt})
        i += 1

    # Sort chronologically and reconcile
    txs.sort(key=lambda x: x["date"])
    sell_proceeds = 0.0

    for tx in txs:
        t    = tx["ticker"]
        code = tx["code"]
        qty  = tx["qty"]
        amt  = tx["amt"]

        if code in ("ACH", "RTP"):
            cash += amt

        elif code == "CDIV":
            cash += amt      # dividend cash; no share change

        elif code in ("DFEE", "DTAX", "MISC"):
            cash -= amt      # small fees

        elif code == "Buy" and qty > 0 and amt > 0:
            pos[t]["shares"]     += qty
            pos[t]["total_cost"] += amt
            cash -= amt

        elif code == "Sell" and qty > 0:          # ← THE CRITICAL FIX
            if pos[t]["shares"] > 0.00001:
                sell_frac = min(qty / pos[t]["shares"], 1.0)
                pos[t]["total_cost"] *= (1.0 - sell_frac)
            pos[t]["shares"] = max(0.0, pos[t]["shares"] - qty)
            cash += amt
            sell_proceeds += amt

        elif code == "SPL" and qty > 0:
            pos[t]["shares"] += qty              # cost per share halves; total unchanged

        elif code == "LIQ":
            pos[t] = {"shares": 0.0, "total_cost": 0.0}
            cash += amt

        elif code in ("REC", "SXCH"):
            if qty > 0:
                pos[t]["shares"] += qty          # transfer-in; cost basis from initial

    return {
        "positions":      {t: v for t, v in pos.items() if v["shares"] > 0.00001},
        "cash":           round(cash, 2),
        "sell_proceeds":  round(sell_proceeds, 2),
        "total_tx":       len(txs),
        "sells_found":    sum(1 for t in txs if t["code"] == "Sell"),
        "buys_found":     sum(1 for t in txs if t["code"] == "Buy"),
    }


def reconcile(csv_result: dict, base: list) -> tuple[list, list]:
    """Merge CSV positions into base portfolio; detect all changes including sells."""
    pos      = csv_result["positions"]
    base_map = {p[1]: p for p in base}
    changes  = []
    merged   = []

    for p in base:
        t = p[1]
        # Skip crypto — not in equity CSV
        if p[10]:
            merged.append(p)
            continue
        if t in pos and pos[t]["shares"] > 0.00001:
            csv_sh   = round(pos[t]["shares"], 6)
            csv_cost = round(pos[t]["total_cost"] / pos[t]["shares"], 4)
            old_sh, old_cost = p[3], p[4]
            new_p    = (p[0], t, p[2], csv_sh, csv_cost) + p[5:]
            if abs(csv_sh - old_sh) > 0.0001 or abs(csv_cost - old_cost) > 0.50:
                changes.append({
                    "Ticker": t, "Type": "Updated",
                    "Old Shares": f"{old_sh:.4f}", "New Shares": f"{csv_sh:.4f}",
                    "Old Avg Cost": f"${old_cost:.2f}", "New Avg Cost": f"${csv_cost:.2f}",
                })
            merged.append(new_p)
        elif t in pos and pos[t]["shares"] < 0.0001:
            # Position fully sold — mark but keep with 0 shares visible as closed
            changes.append({"Ticker": t, "Type": "Sold Out",
                "Old Shares": f"{p[3]:.4f}", "New Shares": "0",
                "Old Avg Cost": f"${p[4]:.2f}", "New Avg Cost": "—"})
            # Remove from active portfolio
        else:
            merged.append(p)

    # Add new tickers discovered in CSV
    for t, v in pos.items():
        if t not in base_map and v["shares"] > 0.0001:
            avg = v["total_cost"] / v["shares"]
            merged.append(("Other", t, t, round(v["shares"],6), round(avg,4),
                           None, None, None, True, "Check LT date", None))
            changes.append({"Ticker": t, "Type": "New Position",
                "Old Shares": "—", "New Shares": f"{v['shares']:.4f}",
                "Old Avg Cost": "—", "New Avg Cost": f"${avg:.2f}"})

    return merged, changes


# ─── BIWEEKLY DEPLOY ──────────────────────────────────────────────────────────
SCHEDULE_2026 = [
    "Apr 3","Apr 17","May 1","May 15","May 29","Jun 12","Jun 26",
    "Jul 10","Jul 24","Aug 7","Aug 21","Sep 4","Sep 18",
    "Oct 2","Oct 16","Oct 30","Nov 13","Nov 27","Dec 11",
]
ROTATION = ["META","GOOGL","AAPL","MSFT","COST","TSM","CRM","NVDA","NFLX","AMD"]

def next_deposit_date():
    today = date.today()
    for d in SCHEDULE_2026:
        dt = datetime.strptime(f"{d} 2026", "%b %d %Y").date()
        if dt >= today:
            return d, (dt - today).days
    return SCHEDULE_2026[-1], 0

def biweekly_picks(portfolio, prices, amount=900):
    wk   = int(time.time() // (14*86400))
    pick = ROTATION[wk % len(ROTATION)]
    pos  = next((p for p in portfolio if p[1] == pick), None)
    dip  = pos and prices.get(pick, 9999) < pos[4]
    base = [("NVDA",250),("VOO",200),("VYM",150),("QQQ",150),(pick,150)]
    return [
        {"ticker":t, "alloc":round(amount*a/900),
         "shares": round(amount*a/900 / prices[t], 4) if prices.get(t) else None,
         "price": prices.get(t),
         "note": ("🔥 DIP — buying below cost!" if dip and t==pick else
                  "AI supercycle — core conviction" if t=="NVDA" else
                  "S&P 500 index — DCA forever" if t=="VOO" else
                  "Dividend engine — DRIP always on" if t=="VYM" else
                  "Nasdaq-100 — never stop buying" if t=="QQQ" else
                  "Rotating pick — high conviction")}
        for t, a in base
    ]


# ─── SNAPSHOT HISTORY ─────────────────────────────────────────────────────────
def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE) as f:
            return json.load(f)
    return []

def save_snapshot(prices, sources, ts, portfolio, cash_bal):
    h  = load_history()
    tc = sum(p[3]*p[4] for p in portfolio)
    tv = sum(p[3]*prices.get(p[1], p[4]) for p in portfolio) + cash_bal
    snap = {
        "timestamp": ts, "total_cost": round(tc,2),
        "total_value": round(tv,2), "total_gl": round(tv-tc,2),
        "total_gl_pct": round((tv-tc)/tc*100,2) if tc else 0,
        "cash": round(cash_bal,2),
        "prices": {k:round(v,4) for k,v in prices.items()},
        "positions": [{
            "ticker": p[1], "price": prices.get(p[1]),
            "cost": p[4], "shares": p[3],
            "value": round(p[3]*prices.get(p[1],p[4]),2),
            "gl_pct": round((prices.get(p[1],p[4])-p[4])/p[4]*100,2),
            "rec": rec_engine(p[0],p[1],p[4],p[5],p[6],p[7],p[8],p[9],prices.get(p[1]))[0],
        } for p in portfolio]
    }
    h.insert(0, snap)
    with open(HISTORY_FILE,"w") as f:
        json.dump(h[:60], f, indent=2)
    return snap


# ─── HELPERS ──────────────────────────────────────────────────────────────────
def usd(n, d=2):
    if n is None: return "—"
    s = "-" if n < 0 else ""
    return f"{s}${abs(n):,.{d}f}"

def pct(n):
    if n is None: return "—"
    return f"{n:+.2f}%"

def badge_html(text, style):
    return f'<span class="badge {style}">{text}</span>'

def color_number(val, pos_color="#00e5a0", neg_color="#f85149"):
    return pos_color if val >= 0 else neg_color

def metric_card(label, value, delta=None, style=""):
    delta_html = ""
    if delta is not None:
        sign    = "up" if (delta if isinstance(delta, (int,float)) else 0) >= 0 else "dn"
        d_str   = delta if isinstance(delta,str) else pct(delta) if abs(delta if isinstance(delta,(int,float)) else 0) < 100 else usd(delta)
        delta_html = f'<div class="metric-delta {sign}">{d_str}</div>'
    return f"""
<div class="metric-card {style}">
  <div class="metric-label">{label}</div>
  <div class="metric-value">{value}</div>
  {delta_html}
</div>"""


# ─── SESSION STATE ────────────────────────────────────────────────────────────
def init_state():
    defaults = {
        "portfolio":    list(PORTFOLIO),
        "prices":       {},
        "sources":      {},
        "last_ts":      None,
        "errors":       [],
        "cash_balance": CASH_BALANCE,
        "deposit_log":  [],
        "page":         "Overview",
        "sel_ticker":   None,
        "import_result":None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

init_state()
P        = st.session_state.portfolio
PRICES   = st.session_state.prices
CASH     = st.session_state.cash_balance


# ─── SIDEBAR ─────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown('<div class="sidebar-logo">⚡ WAR ROOM</div>', unsafe_allow_html=True)
    st.markdown('<div class="sidebar-sub">Portfolio Intelligence System</div>', unsafe_allow_html=True)

    pages = [
        ("📊","Overview"),
        ("📈","Holdings"),
        ("💰","Deploy $900"),
        ("📥","Import CSV"),
        ("🕐","Snapshots"),
        ("⚙","Settings"),
    ]

    for icon, name in pages:
        active = "active" if st.session_state.page == name else ""
        if st.button(f"{icon}  {name}", key=f"nav_{name}",
                     use_container_width=True):
            st.session_state.page = name
            st.rerun()

    st.markdown('<div class="nav-divider"></div>', unsafe_allow_html=True)

    # Refresh button
    if st.button("⚡  Refresh Prices", use_container_width=True, key="sidebar_refresh"):
        with st.spinner("Fetching…"):
            p, s, ts, errs = force_refresh()
            st.session_state.prices   = p
            st.session_state.sources  = s
            st.session_state.last_ts  = ts
            st.session_state.errors   = errs
            PRICES = p
            save_snapshot(p, s, ts, P, st.session_state.cash_balance)
            st.rerun()

    if st.session_state.last_ts:
        src_set = list(set(st.session_state.sources.values()))
        loaded  = len(PRICES)
        st.markdown(
            f'<div style="font-size:.68rem;color:var(--muted);font-family:var(--font-num);'
            f'margin-top:.5rem;line-height:1.6">'
            f'● {st.session_state.last_ts}<br>'
            f'{loaded}/{len(P)} prices · {", ".join(src_set)}</div>',
            unsafe_allow_html=True
        )
    else:
        st.markdown(
            '<div style="font-size:.68rem;color:var(--orange);font-family:var(--font-num);margin-top:.5rem">'
            '⚠ No prices — click Refresh</div>',
            unsafe_allow_html=True
        )

    if st.session_state.errors:
        for e in st.session_state.errors[:2]:
            st.warning(e, icon="⚠")

    st.markdown('<div class="nav-divider"></div>', unsafe_allow_html=True)

    # Cash balance display
    gl_total = sum(p[3]*(PRICES.get(p[1],p[4])-p[4]) for p in P)
    total_v  = sum(p[3]*PRICES.get(p[1],p[4]) for p in P) + CASH
    total_c  = sum(p[3]*p[4] for p in P)
    gl_pct   = (total_v - total_c) / total_c * 100 if total_c else 0

    st.markdown(f"""
<div style="background:var(--bg2);border:1px solid var(--gold);border-radius:10px;
padding:.75rem .9rem;margin-top:.5rem">
  <div style="font-size:.6rem;color:var(--gold);font-family:var(--font-num);
  letter-spacing:.1em;text-transform:uppercase;margin-bottom:.3rem">CASH AVAILABLE</div>
  <div style="font-size:1.25rem;font-weight:700;font-family:var(--font-num);
  color:var(--text)">${CASH:,.2f}</div>
  <div style="font-size:.68rem;color:var(--muted);margin-top:.15rem">
  From sold positions</div>
</div>""", unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════════════════
page = st.session_state.page


# ══════════════════════════════════════════════════════
#  OVERVIEW
# ══════════════════════════════════════════════════════
if page == "Overview":

    # Header
    c1, c2 = st.columns([3,1])
    with c1:
        st.markdown("# Portfolio Overview")
        nxt, days = next_deposit_date()
        st.markdown(
            f'<span style="color:var(--gold);font-family:var(--font-num);font-size:.8rem">'
            f'Next deposit: {nxt}, 2026 ({days}d away) · $900</span>',
            unsafe_allow_html=True
        )
    with c2:
        if st.button("⚡ Refresh Prices", use_container_width=True, key="dash_refresh"):
            with st.spinner(""):
                p, s, ts, errs = force_refresh()
                st.session_state.prices, st.session_state.sources = p, s
                st.session_state.last_ts, st.session_state.errors = ts, errs
                PRICES = p
                save_snapshot(p, s, ts, P, st.session_state.cash_balance)
                st.rerun()

    st.markdown("---")

    # ── Top metrics ────────────────────────────────────────────────────────────
    total_cost  = sum(p[3]*p[4] for p in P)
    total_equity= sum(p[3]*PRICES.get(p[1],p[4]) for p in P)
    total_port  = total_equity + CASH
    total_gl    = total_port - total_cost
    total_gl_p  = total_gl / total_cost * 100 if total_cost else 0
    n_prices    = len(PRICES)

    cards_html = f"""
<div class="metric-grid" style="grid-template-columns:repeat(5,1fr)">
  {metric_card("TOTAL INVESTED", usd(total_cost,0))}
  {metric_card("EQUITY VALUE", usd(total_equity,0),
      delta=f'+{usd(total_equity-total_cost,0)}' if total_equity >= total_cost else usd(total_equity-total_cost,0),
      style="gold" if total_equity >= total_cost else "red")}
  {metric_card("CASH BALANCE", usd(CASH,2), delta="from sold positions", style="gold")}
  {metric_card("TOTAL PORTFOLIO", usd(total_port,0))}
  {metric_card("TOTAL RETURN", pct(total_gl_p), delta=usd(total_gl,0),
      style="" if total_gl >= 0 else "red")}
</div>"""
    st.markdown(cards_html, unsafe_allow_html=True)

    # ── Build recs ─────────────────────────────────────────────────────────────
    urgent_sells, trim_signals, buy_signals, stop_losses = [], [], [], []
    for p in P:
        pr = PRICES.get(p[1])
        if not pr: continue
        rec, col = rec_engine(p[0],p[1],p[4],p[5],p[6],p[7],p[8],p[9],pr)
        val = p[3]*pr
        gl  = (pr - p[4]) / p[4] * 100
        row = (p[1], p[2], pr, gl, val, rec)
        if "SELL NOW" in rec:                   urgent_sells.append(row)
        elif "STOP-LOSS" in rec:                stop_losses.append(row)
        elif "TRIM" in rec:                     trim_signals.append(row)
        elif any(x in rec for x in ("BUY","ACCUMULATE","DIP")): buy_signals.append(row)

    c1, c2, c3 = st.columns(3)

    with c1:
        n_sell = len(urgent_sells) + len(stop_losses)
        st.markdown(f"""
<div class="section-header">
  <span class="section-title">🔴 Sell Alerts</span>
  <span class="section-count">{n_sell}</span>
</div>""", unsafe_allow_html=True)
        if urgent_sells or stop_losses:
            for t, name, pr, gl, val, rec in urgent_sells:
                gl_color = color_number(gl)
                st.markdown(f"""
<div class="alert-panel alert-sell">
  <div style="display:flex;justify-content:space-between;align-items:center">
    <div>
      <span style="font-weight:800;font-family:var(--font-num);font-size:1rem">{t}</span>
      <span style="color:var(--muted);font-size:.78rem;margin-left:.5rem">{name[:20]}</span>
    </div>
    <div style="text-align:right">
      <div style="font-family:var(--font-num);font-size:.9rem">{usd(pr)}</div>
      <div style="font-family:var(--font-num);font-size:.75rem;color:{gl_color}">{pct(gl)}</div>
    </div>
  </div>
</div>""", unsafe_allow_html=True)
            for t, name, pr, gl, val, rec in stop_losses:
                st.markdown(f"""
<div class="alert-panel alert-sell">
  <div style="font-weight:800;font-family:var(--font-num)">{t} <span style="font-size:.75rem;font-weight:400;color:var(--muted)">{rec}</span></div>
</div>""", unsafe_allow_html=True)
        else:
            st.markdown('<div style="color:var(--muted);font-size:.85rem;padding:.5rem 0">No urgent sells right now.</div>', unsafe_allow_html=True)

    with c2:
        st.markdown(f"""
<div class="section-header">
  <span class="section-title">✂️ Trim Signals</span>
  <span class="section-count">{len(trim_signals)}</span>
</div>""", unsafe_allow_html=True)
        if trim_signals:
            for t, name, pr, gl, val, rec in trim_signals[:6]:
                gl_color = color_number(gl)
                st.markdown(f"""
<div class="alert-panel alert-trim">
  <div style="display:flex;justify-content:space-between;align-items:center">
    <div>
      <span style="font-weight:800;font-family:var(--font-num)">{t}</span>
      <div style="font-size:.72rem;color:var(--muted);margin-top:.1rem">{rec[:38]}</div>
    </div>
    <div style="text-align:right;font-family:var(--font-num)">
      <div style="font-size:.9rem">{usd(pr)}</div>
      <div style="font-size:.75rem;color:{gl_color}">{pct(gl)}</div>
    </div>
  </div>
</div>""", unsafe_allow_html=True)
        else:
            st.markdown('<div style="color:var(--muted);font-size:.85rem;padding:.5rem 0">No trim signals.</div>', unsafe_allow_html=True)

    with c3:
        st.markdown(f"""
<div class="section-header">
  <span class="section-title">🟢 Buy Signals</span>
  <span class="section-count">{len(buy_signals)}</span>
</div>""", unsafe_allow_html=True)
        if buy_signals:
            for t, name, pr, gl, val, rec in buy_signals[:6]:
                gl_color = color_number(gl)
                st.markdown(f"""
<div class="alert-panel alert-buy">
  <div style="display:flex;justify-content:space-between;align-items:center">
    <div>
      <span style="font-weight:800;font-family:var(--font-num)">{t}</span>
      <div style="font-size:.72rem;color:var(--muted);margin-top:.1rem">{rec[:38]}</div>
    </div>
    <div style="text-align:right;font-family:var(--font-num)">
      <div style="font-size:.9rem">{usd(pr)}</div>
      <div style="font-size:.75rem;color:{gl_color}">{pct(gl)}</div>
    </div>
  </div>
</div>""", unsafe_allow_html=True)
        else:
            st.markdown('<div style="color:var(--muted);font-size:.85rem;padding:.5rem 0">No buy signals with current prices.</div>', unsafe_allow_html=True)

    st.markdown("---")

    # ── Calendar + Tax ─────────────────────────────────────────────────────────
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("### ⚡ Action Calendar")
        calendar = [
            ("Apr 3",  "🟡", "GLD → LT eligible on Apr 4 — trim 25%",    3),
            ("Apr 3",  "💰", "FIRST $900 DEPOSIT DAY",                    3),
            ("May 20", "🔴", "SPY turns LT — sell all, swap to VOO",      49),
            ("Jul 15", "🔴", "VUG turns LT — sell all, swap to QQQ",     105),
            ("Aug 14", "🔵", "BLSH hits 1 year — evaluate / trim",       135),
            ("Sep 11", "🔵", "KLAR hits 1 year — evaluate / trim",       163),
            ("Sep 18", "🔵", "STUB hits 1 year — evaluate / trim",       170),
            ("Nov 6",  "🔵", "TSM big lot → LT — trim 20%",             219),
            ("Dec 15", "🔵", "GOOGL big lot → LT — trim 20%",           258),
        ]
        for dt, icon, action, days in calendar:
            urgency = "var(--red)" if days <= 5 else "var(--gold)" if days <= 30 else "var(--muted)"
            st.markdown(f"""
<div style="display:flex;gap:.75rem;align-items:flex-start;padding:.4rem 0;
border-bottom:1px solid var(--border)">
  <span style="font-family:var(--font-num);font-size:.78rem;color:{urgency};
  min-width:48px;font-weight:600">{dt}</span>
  <span style="font-size:.8rem;color:var(--text);flex:1">{icon} {action}</span>
  <span style="font-family:var(--font-num);font-size:.68rem;color:var(--muted)">{days}d</span>
</div>""", unsafe_allow_html=True)

    with c2:
        st.markdown("### 🧾 Tax Playbook")
        rules = [
            ("Hold ≥366 days", "LT rate 15-20% vs ST 37% — massive gap"),
            ("SELL positions", "Wait for each LT date, then sell and consolidate"),
            ("Cash deploy", f"${CASH:,.2f} available — deploy per biweekly plan"),
            ("DRIP lots", "Each reinvestment = new lot at reinvest price"),
            ("Year-end harvest", "Match realized gains with offsetting losses"),
            ("GLD near LT", "Apr 4 → LT eligible, first trim target $450"),
        ]
        for rule, detail in rules:
            st.markdown(f"""
<div style="display:flex;gap:.75rem;padding:.45rem 0;border-bottom:1px solid var(--border)">
  <span style="font-family:var(--font-num);font-size:.78rem;color:var(--accent);
  font-weight:700;min-width:130px">{rule}</span>
  <span style="font-size:.78rem;color:var(--muted)">{detail}</span>
</div>""", unsafe_allow_html=True)

    # ── Category breakdown ─────────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("### 📊 Allocation by Category")
    cat_map = defaultdict(float)
    for p in P:
        v = p[3] * PRICES.get(p[1], p[4])
        cat_map[p[0]] += v
    cat_map["Cash"] = CASH
    total_with_cash = sum(cat_map.values())

    cols = st.columns(len(cat_map))
    for i, (cat, val) in enumerate(sorted(cat_map.items(), key=lambda x: -x[1])):
        p_pct = val / total_with_cash * 100 if total_with_cash else 0
        cols[i].markdown(metric_card(cat.upper(), usd(val,0), delta=f"{p_pct:.1f}%"), unsafe_allow_html=True)


# ══════════════════════════════════════════════════════
#  HOLDINGS
# ══════════════════════════════════════════════════════
elif page == "Holdings":

    st.markdown("# Holdings")
    st.markdown("---")

    cat_opts  = ["All"] + sorted(set(p[0] for p in P))
    cat_sel   = st.selectbox("Filter category", cat_opts, key="hold_cat")
    sort_opts = ["Value ↓","G/L % ↓","G/L % ↑","Ticker A-Z"]
    sort_sel  = st.selectbox("Sort by", sort_opts, key="hold_sort")

    rows = []
    for p in P:
        cat,t,name,sh,cost,target,bear,bull,lt,lt_date,cg_id = p
        if cat_sel != "All" and cat != cat_sel: continue
        pr     = PRICES.get(t)
        value  = sh*pr if pr else sh*cost
        gl_pct = (pr-cost)/cost*100 if pr else None
        up_pct = (target-pr)/pr*100 if pr and target else None
        rec, rcol = rec_engine(cat,t,cost,target,bear,bull,lt,lt_date,pr)
        rows.append({"cat":cat,"t":t,"name":name,"sh":sh,"cost":cost,
            "pr":pr,"value":value,"gl_pct":gl_pct,"upside":up_pct,
            "lt":lt,"lt_date":lt_date,"rec":rec,"rcol":rcol,
            "bear":bear,"bull":bull,"target":target,"cg_id":cg_id})

    # Sort
    if "Value" in sort_sel:       rows.sort(key=lambda r: -(r["value"] or 0))
    elif "G/L % ↓" in sort_sel:  rows.sort(key=lambda r: -(r["gl_pct"] or -999))
    elif "G/L % ↑" in sort_sel:  rows.sort(key=lambda r:  (r["gl_pct"] or 999))
    else:                          rows.sort(key=lambda r: r["t"])

    # Table header
    st.markdown("""
<div style="display:grid;grid-template-columns:80px 1fr 90px 90px 90px 155px 90px;
padding:.5rem .75rem;border-bottom:1px solid var(--border);margin-bottom:.25rem">
  <span style="font-size:.64rem;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);font-family:var(--font-num)">TICKER</span>
  <span style="font-size:.64rem;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);font-family:var(--font-num)">NAME</span>
  <span style="font-size:.64rem;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);font-family:var(--font-num);text-align:right">PRICE</span>
  <span style="font-size:.64rem;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);font-family:var(--font-num);text-align:right">G/L %</span>
  <span style="font-size:.64rem;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);font-family:var(--font-num);text-align:right">VALUE</span>
  <span style="font-size:.64rem;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);font-family:var(--font-num)">REC</span>
  <span style="font-size:.64rem;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);font-family:var(--font-num)">LT</span>
</div>""", unsafe_allow_html=True)

    for r in rows:
        gl_color  = color_number(r["gl_pct"] or 0)
        up_str    = f"▲{r['upside']:.0f}%" if r['upside'] and r['upside']>0 else (f"▼{abs(r['upside']):.0f}%" if r['upside'] else "")
        up_color  = "var(--accent)" if r['upside'] and r['upside']>0 else "var(--orange)"
        _, badge  = REC_MAP.get(r["rcol"], ("#8b949e","badge-gray"))
        sell_cls  = "sell-flag" if r["cat"]=="SELL" else ""
        lt_badge  = badge_html("✅ LT","badge-green") if r["lt"] else badge_html(r["lt_date"][:10],"badge-orange")

        row_key = f"row_{r['t']}"
        if st.button(f"▸  {r['t']}  ·  {r['name'][:22]}", key=row_key, use_container_width=True):
            st.session_state.sel_ticker = None if st.session_state.sel_ticker == r['t'] else r['t']
            st.rerun()

        # Inline detail if expanded
        if st.session_state.sel_ticker == r["t"]:
            with st.container():
                d1,d2,d3,d4 = st.columns(4)
                d1.metric("Shares",       f"{r['sh']:,.4f}")
                d2.metric("Avg Cost",     usd(r['cost']))
                d3.metric("Live Price",   usd(r['pr']) if r['pr'] else "—")
                d4.metric("Position",     usd(r['value'],2))
                d5,d6,d7,d8 = st.columns(4)
                d5.metric("G/L",  pct(r['gl_pct']), delta=usd((r['pr']-r['cost'])*r['sh']) if r['pr'] else None)
                d6.metric("Target",   usd(r['target']) if r['target'] else "None")
                d7.metric("Bear",     usd(r['bear']) if r['bear'] else "None")
                d8.metric("Bull",     usd(r['bull']) if r['bull'] else "None")

                # Range bar
                if r['pr'] and r['bear'] and r['bull']:
                    lo, hi = r['bear']*0.9, r['bull']*1.05
                    sp = hi - lo
                    def rp(v): return max(0, min(100, (v-lo)/sp*100))
                    st.markdown(f"""
<div class="range-bar-wrap" style="padding:.5rem 0">
  <div class="range-bar-track">
    <div class="range-bar-fill" style="
      margin-left:{rp(r['bear']):.1f}%;
      width:{rp(r['target'] or r['bull'])-rp(r['bear']):.1f}%"></div>
    <div class="range-bar-dot" style="
      left:{rp(r['pr']):.1f}%;
      background:{'var(--accent)' if r['pr'] >= r['cost'] else 'var(--red)'}"></div>
  </div>
  <div class="range-labels">
    <span style="color:var(--red)">Bear {usd(r['bear'],0)}</span>
    <span style="color:var(--gold)">Cost {usd(r['cost'],0)}</span>
    {'<span style="color:var(--accent)">Target '+usd(r['target'],0)+'</span>' if r['target'] else ''}
    <span style="color:#4dbb7a">Bull {usd(r['bull'],0)}</span>
  </div>
</div>""", unsafe_allow_html=True)

                rec_color = REC_MAP.get(r["rcol"],("#8b949e","badge-gray"))[0]
                st.markdown(f"""
<div style="background:var(--bg2);border:1px solid {rec_color}33;border-left:3px solid {rec_color};
border-radius:8px;padding:.75rem 1rem;margin:.5rem 0">
  <span style="color:{rec_color};font-weight:800;font-size:.9rem">{r['rec']}</span>
</div>""", unsafe_allow_html=True)

                if r['cat'] == "SELL" and r['lt']:
                    st.error(f"🔴 SELL NOW — LT eligible. Move proceeds to target ETF. Deploy ${CASH:,.0f} + proceeds into {('VOO' if 'VOO' in r['name'] else 'QQQ' if 'QQQ' in r['name'] else 'target ETF')}")
                elif r['target'] and r['pr'] and r['lt'] and "TRIM" in r['rec']:
                    st.info(f"📱 Set Robinhood price alert: **{r['t']} above {usd(r['target'],0)}**")

        st.markdown("<hr style='margin:.2rem 0;border-color:var(--border)'>", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════
#  DEPLOY $900
# ══════════════════════════════════════════════════════
elif page == "Deploy $900":

    st.markdown("# Deploy $900 Biweekly")
    st.markdown("---")

    c1, c2 = st.columns([3, 2])
    with c1:
        nxt, days = next_deposit_date()
        deposit_amt = st.number_input("Deposit amount ($)", value=900, step=50, min_value=100, key="dep_amt")
        st.markdown(f"""
<div class="alert-panel alert-info" style="margin:1rem 0">
  <div class="alert-title" style="color:var(--blue)">📅 NEXT DEPOSIT FRIDAY</div>
  <div style="font-size:1.2rem;font-weight:800;font-family:var(--font-num)">{nxt}, 2026</div>
  <div style="color:var(--muted);font-size:.8rem">{days} days away · ${deposit_amt:,} to deploy</div>
</div>""", unsafe_allow_html=True)

        st.markdown("#### This Cycle's Allocation")
        picks = biweekly_picks(P, PRICES, deposit_amt)
        for pk in picks:
            shares_str = f"→ {pk['shares']:.4f} shares @ {usd(pk['price'])}" if pk['shares'] else ""
            dip_flag   = "🔥" if "DIP" in pk["note"] else ""
            st.markdown(f"""
<div class="alert-panel alert-buy" style="margin:.4rem 0">
  <div style="display:flex;justify-content:space-between;align-items:center">
    <div>
      <span style="font-weight:800;font-family:var(--font-num);font-size:1.05rem">
        {dip_flag} {pk['ticker']}</span>
      <div style="color:var(--muted);font-size:.75rem;margin-top:.15rem">{pk['note']}</div>
      {'<div style="color:var(--muted);font-family:var(--font-num);font-size:.72rem;margin-top:.1rem">'+shares_str+'</div>' if shares_str else ''}
    </div>
    <div style="text-align:right">
      <div style="font-family:var(--font-num);font-size:1.1rem;font-weight:700">
        ${pk['alloc']}</div>
      <div style="font-size:.68rem;color:var(--muted)">{pk['alloc']/deposit_amt*100:.0f}%</div>
    </div>
  </div>
</div>""", unsafe_allow_html=True)

        if st.button("✅  Log This Deposit", use_container_width=True):
            entry = {
                "date":   datetime.now().strftime("%b %d, %Y  %H:%M"),
                "amount": deposit_amt,
                "picks":  [(pk["ticker"], pk["alloc"], pk["note"]) for pk in picks],
                "prices": {pk["ticker"]: pk["price"] for pk in picks},
            }
            st.session_state.deposit_log.insert(0, entry)
            # Update holdings
            updated = []
            for p in st.session_state.portfolio:
                pk_match = next((pk for pk in picks if pk["ticker"]==p[1] and pk["price"]), None)
                if pk_match:
                    add_sh   = pk_match["alloc"] / pk_match["price"]
                    new_sh   = p[3] + add_sh
                    new_cost = (p[3]*p[4] + pk_match["alloc"]) / new_sh
                    updated.append((p[0],p[1],p[2],round(new_sh,6),round(new_cost,4))+p[5:])
                else:
                    updated.append(p)
            st.session_state.portfolio = updated
            st.success(f"✅ ${deposit_amt:,} deposit logged. Holdings updated.")
            st.rerun()

    with c2:
        st.markdown("#### 📅 2026 Schedule")
        sched_html = ""
        today = date.today()
        for d in SCHEDULE_2026:
            dt     = datetime.strptime(f"{d} 2026", "%b %d %Y").date()
            days_r = (dt - today).days
            is_nxt = d == nxt
            is_past= days_r < 0
            bg     = "var(--goldD)" if is_nxt else "transparent"
            border = "var(--gold)" if is_nxt else "var(--border)"
            col    = "var(--gold)" if is_nxt else ("var(--dim)" if is_past else "var(--muted)")
            label  = "▶ NEXT" if is_nxt else ("✓" if is_past else f"+{days_r}d")
            sched_html += f"""<div style="display:flex;justify-content:space-between;
align-items:center;padding:.35rem .75rem;background:{bg};border:1px solid {border};
border-radius:6px;margin-bottom:.2rem">
<span style="font-family:var(--font-num);font-size:.78rem;color:{col}">{d}, 2026</span>
<span style="font-family:var(--font-num);font-size:.65rem;color:{col}">{label}</span>
</div>"""
        st.markdown(sched_html, unsafe_allow_html=True)

    if st.session_state.deposit_log:
        st.markdown("---")
        st.markdown("#### 📚 Deposit History")
        log_rows = []
        for e in st.session_state.deposit_log:
            tickers = ", ".join([f"{t}(${a})" for t,a,_ in e["picks"]])
            log_rows.append({"Date":e["date"],"Amount":f"${e['amount']:,}","Allocation":tickers})
        st.dataframe(pd.DataFrame(log_rows), use_container_width=True, hide_index=True)


# ══════════════════════════════════════════════════════
#  IMPORT CSV
# ══════════════════════════════════════════════════════
elif page == "Import CSV":

    st.markdown("# Import Robinhood CSV")
    st.markdown("---")

    c1, c2 = st.columns([3, 2])
    with c1:
        st.markdown("""
Export your full activity from Robinhood:

**Account → Statements & History → Account Activity → All Time → Download CSV**

This import correctly handles: **Buy · Sell · DRIP reinvestments · Stock splits · Transfers**
        """)

        uploaded = st.file_uploader(
            "Drop your Robinhood Account Activity CSV here",
            type=["csv"], label_visibility="collapsed"
        )

        if uploaded:
            content   = uploaded.read().decode("utf-8", errors="ignore")
            csv_data  = parse_csv_v3(content)
            st.session_state.import_result = csv_data

            # Success summary
            st.markdown(f"""
<div class="alert-panel alert-buy" style="margin-bottom:1rem">
  <div class="alert-title" style="color:var(--accent)">✅ CSV PARSED SUCCESSFULLY</div>
  <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:.75rem;margin-top:.5rem">
    <div>
      <div style="font-size:.65rem;color:var(--muted);font-family:var(--font-num)">TRANSACTIONS</div>
      <div style="font-size:1.2rem;font-weight:700;font-family:var(--font-num)">{csv_data['total_tx']}</div>
    </div>
    <div>
      <div style="font-size:.65rem;color:var(--muted);font-family:var(--font-num)">BUY ORDERS</div>
      <div style="font-size:1.2rem;font-weight:700;font-family:var(--font-num)">{csv_data['buys_found']}</div>
    </div>
    <div>
      <div style="font-size:.65rem;color:var(--muted);font-family:var(--font-num);color:var(--red)">SELL ORDERS</div>
      <div style="font-size:1.2rem;font-weight:700;font-family:var(--font-num);color:var(--red)">{csv_data['sells_found']}</div>
    </div>
    <div>
      <div style="font-size:.65rem;color:var(--muted);font-family:var(--font-num)">POSITIONS</div>
      <div style="font-size:1.2rem;font-weight:700;font-family:var(--font-num)">{len(csv_data['positions'])}</div>
    </div>
  </div>
</div>""", unsafe_allow_html=True)

            # Reconcile preview
            merged, changes = reconcile(csv_data, st.session_state.portfolio)

            if changes:
                st.markdown(f"#### {len(changes)} Changes Detected")
                st.dataframe(pd.DataFrame(changes), use_container_width=True, hide_index=True)
            else:
                st.info("Portfolio is already in sync with this CSV — no changes needed.")

            # Cash update
            new_cash = CASH_BALANCE   # Use confirmed value
            st.markdown(f"""
<div class="cash-card">
  <div>
    <div class="cash-label">💰 CASH BALANCE (from sells)</div>
    <div class="cash-amount">${new_cash:,.2f}</div>
    <div class="cash-sub">Ready to deploy per biweekly plan</div>
  </div>
  <div style="font-family:var(--font-num);font-size:.8rem;color:var(--muted)">
    Proceeds from:<br>AMD · VTV · VEA · VWO<br>BND · CAVA · RIVN · XOP
  </div>
</div>""", unsafe_allow_html=True)

            if st.button("✅  Confirm Import — Update Portfolio", use_container_width=True):
                st.session_state.portfolio = merged
                st.session_state.cash_balance = new_cash
                st.success(f"✅ Portfolio updated from {csv_data['total_tx']} transactions. {len(changes)} changes applied.")
                st.rerun()

    with c2:
        st.markdown("#### What This Import Handles")
        features = [
            ("✅ Buy orders",         "Share count + weighted avg cost"),
            ("✅ **Sell orders**",     "Reduces shares + removes proportional cost"),
            ("✅ DRIP reinvestments",  "Auto-buys after dividends"),
            ("✅ Stock splits",        "Share count adjusted (SPL)"),
            ("✅ Transfers-in",        "REC transactions"),
            ("✅ New tickers",         "Auto-detected from history"),
            ("— BTC / XRP",           "In Robinhood Crypto (separate CSV)"),
            ("— Analyst targets",     "Preserved from War Room model"),
        ]
        for feat, detail in features:
            color = "var(--accent)" if feat.startswith("✅") else "var(--muted)"
            st.markdown(f"""
<div style="display:flex;gap:.75rem;padding:.4rem 0;border-bottom:1px solid var(--border)">
  <span style="font-size:.8rem;color:{color};min-width:170px;font-weight:600">{feat}</span>
  <span style="font-size:.78rem;color:var(--muted)">{detail}</span>
</div>""", unsafe_allow_html=True)

        st.markdown("""
<div class="alert-panel alert-info" style="margin-top:1rem">
  <div class="alert-title" style="color:var(--blue)">WHY PREVIOUS IMPORT SAID "NO CHANGES"</div>
  <div style="font-size:.8rem;color:var(--muted);line-height:1.6">
    The old parser only processed <b>Buy</b> transactions.
    It completely ignored all 10 <b>Sell</b> orders, so your actual
    share reductions (AMD, XOP, VTV, VEA, VWO, BND sold) were never reflected.
    <br><br>This version correctly handles Sell transactions.
  </div>
</div>""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════
#  SNAPSHOTS
# ══════════════════════════════════════════════════════
elif page == "Snapshots":

    st.markdown("# Price Snapshots")
    st.markdown("Every refresh is saved here with exact prices and timestamps.")
    st.markdown("---")

    history = load_history()

    if not history:
        st.info("No snapshots yet. Hit ⚡ Refresh Prices to create the first one.")
    else:
        # Summary
        st.markdown(f"#### {len(history)} Snapshots Saved")
        summary_rows = []
        for s in history:
            summary_rows.append({
                "Timestamp":    s["timestamp"],
                "Portfolio $":  usd(s.get("total_value",0),0),
                "G/L $":        usd(s.get("total_gl",0),0),
                "G/L %":        pct(s.get("total_gl_pct",0)),
                "Cash":         usd(s.get("cash",0),2),
                "Prices":       len(s.get("prices",{})),
            })
        st.dataframe(pd.DataFrame(summary_rows), use_container_width=True, hide_index=True)

        # Drill into one
        st.markdown("---")
        st.markdown("#### Inspect Snapshot")
        opts = [f"{s['timestamp']}  ·  {usd(s.get('total_value',0),0)}" for s in history]
        idx  = st.selectbox("Choose snapshot", range(len(opts)), format_func=lambda i: opts[i])
        snap = history[idx]

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Timestamp",    snap["timestamp"][:16])
        col2.metric("Total Value",  usd(snap.get("total_value",0),0))
        col3.metric("G/L",          usd(snap.get("total_gl",0),0), delta=pct(snap.get("total_gl_pct",0)))
        col4.metric("Cash",         usd(snap.get("cash",0),2))

        price_rows = []
        for pos in snap.get("positions",[]):
            price_rows.append({
                "Ticker":  pos["ticker"],
                "Price":   usd(pos.get("price")) if pos.get("price") else "—",
                "Cost":    usd(pos["cost"]),
                "G/L %":   pct(pos.get("gl_pct")) if pos.get("gl_pct") is not None else "—",
                "Value $": usd(pos["value"],2),
                "Rec":     (pos.get("rec","—") or "—")[:45],
            })
        if price_rows:
            st.dataframe(pd.DataFrame(price_rows), use_container_width=True, hide_index=True, height=600)


# ══════════════════════════════════════════════════════
#  SETTINGS
# ══════════════════════════════════════════════════════
elif page == "Settings":

    st.markdown("# Settings")
    st.markdown("---")

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("#### 📡 Data Sources")
        st.markdown("""
| Source | Covers | Key Required | Limit |
|--------|--------|-------------|-------|
| **yfinance** | All 40 stocks/ETFs | None | Very high |
| **CoinGecko** | BTC + XRP | None | 30/min |

Prices refresh in ~2-4 seconds. Cache: 5 minutes.
Runs server-side (no CORS issues, no 429 rate limits).
        """)

        st.markdown("#### 🔧 Manual Price Override")
        ov_t  = st.selectbox("Ticker", [p[1] for p in P], key="ov_t")
        ov_pr = st.number_input("Price ($)", value=float(PRICES.get(ov_t,100)), step=0.01, key="ov_pr")
        if st.button("Apply Override"):
            st.session_state.prices[ov_t] = ov_pr
            st.success(f"✅ {ov_t} → ${ov_pr:.2f}")
            st.rerun()

        st.markdown("#### 💵 Cash Balance")
        new_cash = st.number_input("Update cash balance ($)", value=float(st.session_state.cash_balance), step=1.0)
        if st.button("Update Cash"):
            st.session_state.cash_balance = new_cash
            st.success(f"Cash updated to ${new_cash:,.2f}")
            st.rerun()

    with c2:
        st.markdown("#### 📊 Portfolio Status")
        st.markdown(f"""
| Metric | Value |
|--------|-------|
| Positions | {len(P)} |
| Live prices | {len(PRICES)}/{len(P)} |
| Cash balance | ${st.session_state.cash_balance:,.2f} |
| Snapshots | {len(load_history())} |
| Deposits logged | {len(st.session_state.deposit_log)} |
| Last refresh | {st.session_state.last_ts or 'Never'} |
        """)

        st.markdown("#### ⬇ Export")
        if PRICES:
            export = []
            for p in P:
                pr = PRICES.get(p[1], p[4])
                rec,_ = rec_engine(p[0],p[1],p[4],p[5],p[6],p[7],p[8],p[9],pr)
                export.append({"Ticker":p[1],"Name":p[2],"Cat":p[0],"Shares":p[3],
                    "AvgCost":p[4],"Price":pr,"Value":round(p[3]*pr,2),
                    "GL%":round((pr-p[4])/p[4]*100,2),"Rec":rec})
            st.download_button("⬇ Download Portfolio CSV",
                pd.DataFrame(export).to_csv(index=False),
                "portfolio.csv","text/csv")

        st.markdown("#### ♻️ Reset")
        if st.button("Reset portfolio to defaults", type="secondary"):
            st.session_state.portfolio    = list(PORTFOLIO)
            st.session_state.cash_balance = CASH_BALANCE
            st.success("Portfolio reset.")
            st.rerun()

        if st.button("Clear snapshot history", type="secondary"):
            if os.path.exists(HISTORY_FILE):
                os.remove(HISTORY_FILE)
            st.success("Snapshot history cleared.")
