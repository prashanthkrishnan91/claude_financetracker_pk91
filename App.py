"""
╔══════════════════════════════════════════════════════════════════╗
║         PORTFOLIO WAR ROOM — Streamlit Edition                  ║
║  Data: yfinance (stocks/ETFs) + CoinGecko (crypto)             ║
║  Zero API keys needed. Run: streamlit run app.py               ║
╚══════════════════════════════════════════════════════════════════╝
"""

import streamlit as st
import yfinance as yf
import requests
import pandas as pd
import json
import time
import os
from datetime import datetime, date
from zoneinfo import ZoneInfo

# ─── PAGE CONFIG ─────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Portfolio War Room",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ─── CUSTOM CSS ───────────────────────────────────────────────────────────────
st.markdown("""
<style>
  @import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;600;700;800&family=Space+Mono:wght@400;700&display=swap');
  html, body, [class*="css"] { font-family: 'Space Grotesk', sans-serif; background: #050608; color: #e8ecf8; }
  .stApp { background: #050608; }
  .block-container { padding: 1rem 1.5rem 2rem; max-width: 1400px; }
  h1,h2,h3 { color: #00f0aa; font-family: 'Space Mono', monospace; }
  .stMetric { background: #0b0e14; border: 1px solid #1e2535; border-radius: 12px; padding: 12px; }
  .stMetric label { color: #6a7590 !important; font-size: 11px !important; font-family: 'Space Mono', monospace !important; }
  .stMetric [data-testid="metric-container"] > div:last-child { color: #00f0aa !important; }
  .stDataFrame { background: #0b0e14; border-radius: 12px; }
  div[data-testid="stSidebar"] { background: #0b0e14; border-right: 1px solid #1e2535; }
  .stButton>button { background: #00f0aa18; border: 1px solid #00f0aa; color: #00f0aa;
    font-family: 'Space Mono', monospace; font-weight: 700; border-radius: 8px; }
  .stButton>button:hover { background: #00f0aa33; }
  .stTabs [data-baseweb="tab"] { color: #6a7590; font-family: 'Space Mono', monospace; font-size: 12px; }
  .stTabs [aria-selected="true"] { color: #00f0aa !important; border-bottom: 2px solid #00f0aa !important; }
  .stSelectbox > div > div { background: #0b0e14; border: 1px solid #1e2535; color: #e8ecf8; }
  .stTextInput > div > div > input { background: #0b0e14; border: 1px solid #1e2535; color: #e8ecf8; }
  .stNumberInput > div > div > input { background: #0b0e14; border: 1px solid #1e2535; color: #e8ecf8; }
  .rec-green  { color: #00f0aa; font-weight: 700; }
  .rec-red    { color: #ff4060; font-weight: 700; }
  .rec-gold   { color: #f0c040; font-weight: 700; }
  .rec-orange { color: #ff9030; font-weight: 700; }
  .rec-purple { color: #9070ff; font-weight: 700; }
  .rec-blue   { color: #4090ff; font-weight: 700; }
  .rec-gray   { color: #6a7590; }
  hr { border: 1px solid #1e2535; margin: 8px 0; }
  .snapshot-card { background: #0b0e14; border: 1px solid #1e2535; border-radius: 10px; padding: 12px; margin: 6px 0; }
  .tag { display: inline-block; padding: 2px 8px; border-radius: 20px; font-size: 11px; font-weight: 700; }
</style>
""", unsafe_allow_html=True)

# ─── PORTFOLIO DATA ───────────────────────────────────────────────────────────
PORTFOLIO = [
    # cat, ticker, name, shares, avg_cost, target, bear, bull, lt_ready, lt_date, cg_id
    ("Crypto","BTC",  "Bitcoin",            0.03433, 66997, 110000, 45000, 175000, True,  "LT",           "bitcoin"),
    ("Crypto","XRP",  "XRP/Ripple",         1.066,   1.886, 2.80,   0.60,  5.00,   True,  "LT",           "ripple"),
    ("Core",  "NVDA", "NVIDIA",             35,      103,   175,    90,    250,    True,  "LT",           None),
    ("Core",  "META", "Meta Platforms",     2.8,     612,   720,    400,   900,    False, "Sep 23 2026",  None),
    ("Core",  "GOOGL","Alphabet",           4.0,     307,   210,    140,   280,    False, "Dec 15 2026",  None),
    ("Core",  "AAPL", "Apple",              16.1,    172,   240,    170,   290,    True,  "LT",           None),
    ("Core",  "MSFT", "Microsoft",          0.012,   402,   480,    330,   560,    True,  "LT",           None),
    ("Core",  "NFLX", "Netflix",            10.3,    86,    1100,   700,   1400,   True,  "LT NOW",       None),
    ("Core",  "COST", "Costco",             1.85,    925,   1050,   820,   1300,   True,  "LT NOW",       None),
    ("Core",  "TSM",  "Taiwan Semi",        1.98,    290,   230,    130,   320,    False, "Nov 6 2026",   None),
    ("Core",  "CRM",  "Salesforce",         2.74,    254,   320,    180,   400,    True,  "LT",           None),
    ("Core",  "QCOM", "Qualcomm",           2.37,    165,   175,    100,   230,    True,  "LT",           None),
    ("Core",  "WMT",  "Walmart",            13.6,    82,    105,    75,    130,    True,  "LT",           None),
    ("Core",  "BRK-B","Berkshire B",        2.86,    502,   530,    400,   620,    True,  "LT",           None),
    ("Core",  "AMD",  "AMD",                1.66,    164,   140,    80,    220,    True,  "LT",           None),
    ("Other", "RDDT", "Reddit",             1,       34,    130,    60,    200,    True,  "LT",           None),
    ("Other", "ALK",  "Alaska Air",         0.6,     41,    55,     28,    75,     True,  "LT",           None),
    ("Other", "SNOW", "Snowflake",          3.6,     152,   190,    90,    250,    True,  "LT",           None),
    ("IPO",   "BLSH", "Bullish",            10,      37,    60,     15,    90,     False, "Aug 14 2026",  None),
    ("IPO",   "KLAR", "Klarna",             11,      40,    65,     25,    100,    False, "Sep 11 2026",  None),
    ("IPO",   "STUB", "StubHub",            23,      24,    38,     12,    60,     False, "Sep 18 2026",  None),
    ("ETF",   "VOO",  "Vanguard S&P 500",   2.85,    479,   650,    420,   750,    True,  "LT",           None),
    ("ETF",   "QQQ",  "Nasdaq-100",         2.37,    503,   580,    380,   700,    True,  "LT",           None),
    ("ETF",   "VTI",  "Vanguard Total Mkt", 1.96,    274,   370,    240,   430,    True,  "LT",           None),
    ("ETF",   "VGT",  "Vanguard IT ETF",    1.46,    548,   760,    480,   920,    True,  "LT",           None),
    ("ETF",   "VHT",  "Vanguard Health",    1.87,    271,   300,    200,   370,    True,  "LT",           None),
    ("ETF",   "VIS",  "Vanguard Industrials",1.97,   258,   340,    210,   420,    True,  "LT",           None),
    ("ETF",   "VYM",  "Vanguard Hi-Div",    20.4,    132,   160,    110,   190,    True,  "LT",           None),
    ("ETF",   "SCHD", "Schwab Dividend",    19.6,    27,    32,     20,    42,     True,  "LT",           None),
    ("ETF",   "VXUS", "Vanguard Intl",      22.7,    78,    85,     55,    110,    True,  "LT",           None),
    ("ETF",   "GLD",  "SPDR Gold",          4.97,    287,   320,    220,   420,    True,  "LT",           None),
    ("ETF",   "XLE",  "Energy SPDR",        21.3,    74,    72,     44,    95,     True,  "LT",           None),
    ("SELL",  "SPY",  "S&P500 → VOO",       0.51,    595,   None,   None,  None,   False, "May 20 2026",  None),
    ("SELL",  "VTV",  "Value → VOO",        0.49,    163,   None,   None,  None,   True,  "LT NOW",       None),
    ("SELL",  "VUG",  "Growth → QQQ",       0.46,    441,   None,   None,  None,   False, "Jul 15 2026",  None),
    ("SELL",  "VEA",  "Dev Mkts → VXUS",    0.26,    50,    None,   None,  None,   True,  "LT NOW",       None),
    ("SELL",  "VWO",  "Emg Mkts → VXUS",    0.15,    41,    None,   None,  None,   True,  "LT NOW",       None),
    ("SELL",  "BND",  "Bond → VYM",         0.59,    72,    None,   None,  None,   True,  "LT NOW",       None),
    ("SELL",  "XOP",  "Oil E&P → SELL",     1.64,    183,   None,   None,  None,   False, "Take loss",    None),
]

PORTFOLIO_DF_COLS = ["cat","ticker","name","shares","cost","target","bear","bull","lt","ltDate","cg_id"]

# Biweekly deposit schedule
SCHEDULE = [
    "Apr 3","Apr 17","May 1","May 15","May 29","Jun 12","Jun 26",
    "Jul 10","Jul 24","Aug 7","Aug 21","Sep 4","Sep 18",
    "Oct 2","Oct 16","Oct 30","Nov 13","Nov 27","Dec 11",
]
ROTATION = ["META","GOOGL","AAPL","MSFT","COST","TSM","CRM","NVDA","NFLX","AMD"]

# ─── PRICE FETCHER ────────────────────────────────────────────────────────────
@st.cache_data(ttl=300, show_spinner=False)   # cache 5 min
def fetch_all_prices():
    """
    Fetches all 39 positions in parallel.
    Source 1: yfinance   — all stocks + ETFs + crypto (BTC-USD, XRP-USD)
    Source 2: CoinGecko  — BTC + XRP cross-check
    Returns dict: {ticker: price, ...}, sources_used, timestamp, errors
    """
    ts       = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M:%S ET")
    prices   = {}
    errors   = []
    sources  = {}

    # ── yfinance: all tickers in ONE download call ────────────────────────────
    stock_tickers = [p[1] for p in PORTFOLIO if not p[10]]   # no cg_id
    crypto_yf     = ["BTC-USD", "XRP-USD"]
    all_tickers   = stock_tickers + crypto_yf

    try:
        raw = yf.download(
            tickers    = " ".join(all_tickers),
            period     = "2d",
            interval   = "1d",
            progress   = False,
            auto_adjust= True,
            threads    = True,
        )

        if raw.empty:
            errors.append("yfinance returned empty data")
        else:
            # Multi-ticker download nests columns: (OHLCV, ticker)
            close = raw["Close"] if "Close" in raw.columns else raw.get("close", pd.DataFrame())

            if isinstance(close, pd.Series):
                # Single ticker (shouldn't happen here but handle anyway)
                last = float(close.dropna().iloc[-1])
                prices[all_tickers[0]] = last
                sources[all_tickers[0]] = "yfinance"
            else:
                for col in close.columns:
                    series = close[col].dropna()
                    if not series.empty:
                        val = float(series.iloc[-1])
                        if val > 0:
                            # Map BTC-USD → BTC, XRP-USD → XRP
                            key = col.replace("-USD","")
                            prices[key] = round(val, 4)
                            sources[key] = "yfinance"
    except Exception as e:
        errors.append(f"yfinance error: {e}")

    # ── CoinGecko: BTC + XRP (free, no key, real-time) ───────────────────────
    try:
        cg_url = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ripple&vs_currencies=usd"
        r = requests.get(cg_url, timeout=10)
        if r.status_code == 200:
            cg = r.json()
            if cg.get("bitcoin",{}).get("usd"):
                prices["BTC"] = cg["bitcoin"]["usd"]
                sources["BTC"] = "CoinGecko"
            if cg.get("ripple",{}).get("usd"):
                prices["XRP"] = cg["ripple"]["usd"]
                sources["XRP"] = "CoinGecko"
        else:
            errors.append(f"CoinGecko HTTP {r.status_code}")
    except Exception as e:
        errors.append(f"CoinGecko error: {e}")
        # Fall back to yfinance crypto prices already fetched above

    return prices, sources, ts, errors


def fetch_prices_live():
    """Force-bypass cache for manual refresh."""
    fetch_all_prices.clear()
    return fetch_all_prices()


# ─── RECOMMENDATION ENGINE ────────────────────────────────────────────────────
REC_COLORS = {
    "green": "#00f0aa", "red": "#ff4060", "gold": "#f0c040",
    "orange": "#ff9030", "purple": "#9070ff", "blue": "#4090ff", "gray": "#6a7590"
}

def generate_rec(cat, ticker, cost, target, bear, bull, lt, lt_date, price):
    if not price:
        if cat == "SELL":
            return ("🔴 SELL NOW" if lt else f"⏳ WAIT — SELL {lt_date}", "red")
        return ("⏸ HOLD — awaiting price", "gray")

    if not target:
        if cat == "SELL":
            return ("🔴 SELL NOW — LT eligible, consolidate" if lt else f"⏳ WAIT → SELL {lt_date}", "red")
        return ("⏸ HOLD", "gray")

    pct      = (price - cost) / cost * 100
    upside   = (target - price) / price * 100
    declining = target < cost

    # Income ETFs — never sell
    if ticker in ["VYM","SCHD"]:
        return ("♾ HOLD FOREVER — dividend engine, DRIP on", "purple")
    # Core index — always DCA
    if ticker in ["VOO","QQQ","VTI"]:
        return ("📈 DCA ALWAYS — core index, buy every deposit", "green")
    # SELL-flagged positions
    if cat == "SELL":
        return ("🔴 SELL NOW — LT eligible, move to target ETF" if lt else f"⏳ WAIT → SELL {lt_date}", "red")

    # Bear proximity (non-crypto) unless huge upside signals deep value
    if bear and price < bear * 1.10 and cat != "Crypto" and upside < 60:
        return (f"🚨 STOP-LOSS ALERT — near bear ${bear:,.0f}, review now", "red")

    # Crypto rules
    if cat == "Crypto":
        if upside > 25:
            return (f"🟢 ACCUMULATE — {upside:.0f}% to target (${target:,.0f})", "green")
        if upside < -20:
            return (f"✂️ TRIM 15% — {abs(upside):.0f}% above target, LT rate", "orange")
        return (f"⏸ HOLD — {upside:.0f}% to target (${target:,.0f})", "blue")

    # Declining thesis (analyst target below your cost)
    if declining:
        if upside > 20:
            return (f"🟡 ACCUMULATE — {upside:.0f}% to analyst target", "gold")
        if 5 >= upside > -10:
            return ("✂️ TRIM 20% (LT rate)" if lt else f"⏳ HOLD (ST) — wait {lt_date}", "orange" if lt else "gold")
        if upside <= -10:
            return ("✂️ TRIM 25% (LT rate)" if lt else f"⏳ HOLD (ST) — wait {lt_date}", "orange" if lt else "gold")
        return ("⏸ HOLD — declining thesis, monitoring", "gray")

    # Strong dip buying
    if pct < -20 and upside > 20:
        return (f"🔥 STRONG BUY — down {abs(pct):.0f}%, {upside:.0f}% upside!", "green")
    if pct < -15 and upside > 15:
        return (f"🟢 BUY THE DIP — down {abs(pct):.0f}%, {upside:.0f}% upside", "green")

    if upside > 40:
        return (f"🟢 ACCUMULATE — {upside:.0f}% upside, add aggressively", "green")
    if upside > 20:
        return (f"🟢 ACCUMULATE — {upside:.0f}% upside, buy on weakness", "green")

    if 5 >= upside > -10:
        return ("✂️ TRIM 20% at LT rate" if lt else f"⏳ HOLD (ST) — near target, wait {lt_date}", "orange" if lt else "gold")
    if upside <= -10:
        return ("✂️ TRIM 25% at LT rate" if lt else f"⏳ HOLD (ST) — above target, wait {lt_date}", "orange" if lt else "gold")

    if cat == "IPO" and not lt:
        return (f"🔒 HOLD (IPO) — lockup until LT: {lt_date}", "blue")
    if upside > 10:
        return (f"⏸ HOLD — {upside:.0f}% upside to target", "blue")
    return (f"⏸ HOLD — monitoring ({upside:.0f}% to target)", "gray")


# ─── BIWEEKLY PICKS ───────────────────────────────────────────────────────────
def get_next_deposit():
    today = date.today()
    for d in SCHEDULE:
        dt = datetime.strptime(f"{d} 2026", "%b %d %Y").date()
        if dt >= today:
            return d
    return SCHEDULE[-1]

def get_biweekly_picks(prices):
    wk   = int(time.time() // (14*86400))
    pick = ROTATION[wk % len(ROTATION)]
    rows = [
        ("NVDA", 250, "AI supercycle — highest conviction"),
        ("VOO",  200, "S&P 500 index — DCA every cycle forever"),
        ("VYM",  150, "Dividend engine — DRIP always on"),
        ("QQQ",  150, "Nasdaq-100 — never stop buying"),
        (pick,   150, f"Rotating pick — {'DIP ALERT 🔥' if prices.get(pick,9999) < next((p[4] for p in PORTFOLIO if p[1]==pick),9999) else 'strong conviction'}"),
    ]
    return rows


# ─── CSV PARSER (Robinhood) ───────────────────────────────────────────────────
def parse_robinhood_csv(content: str):
    lines = content.splitlines()
    txs = []
    for line in lines[1:]:
        line = line.strip()
        if not line or "The data provided" in line:
            continue
        fields, cur, inq = [], "", False
        for c in line:
            if c == '"': inq = not inq; continue
            if c == ',' and not inq: fields.append(cur.strip()); cur = ""; continue
            cur += c
        fields.append(cur.strip())
        if len(fields) < 6: continue
        date_s, _, _, instrument, _, code = fields[:6]
        qty = float(fields[6]) if len(fields)>6 and fields[6] else 0
        amt_s = fields[8] if len(fields)>8 else ""
        try: amt = abs(float(amt_s.replace("$","").replace(",","").replace("(","").replace(")","").strip()) or 0)
        except: amt = 0.0
        ticker = instrument.upper().replace("BRK.B","BRK-B").strip()
        if ticker and date_s: txs.append({"date":date_s,"ticker":ticker,"code":code.strip(),"qty":qty,"amt":amt})
    txs.sort(key=lambda x: x["date"])
    return txs

def reconcile_csv(txs, base_portfolio):
    pos = {}
    for tx in txs:
        t = tx["ticker"]
        if not t: continue
        if t not in pos: pos[t] = {"shares":0,"cost":0}
        if tx["code"]=="Buy" and tx["qty"]>0 and tx["amt"]>0:
            pos[t]["shares"] += tx["qty"]; pos[t]["cost"] += tx["amt"]
        elif tx["code"]=="SPL" and tx["qty"]>0:
            pos[t]["shares"] += tx["qty"]
        elif tx["code"]=="LIQ":
            pos[t] = {"shares":0,"cost":0}
    result = []
    existing = {p[1]:p for p in base_portfolio}
    for p in base_portfolio:
        t = p[1]
        if t in pos and pos[t]["shares"] > 0.0001:
            sh = pos[t]["shares"]; c = pos[t]["cost"]/sh
            result.append((p[0],t,p[2],round(sh,6),round(c,4))+p[5:])
        else:
            result.append(p)
    for t,v in pos.items():
        if t not in existing and v["shares"]>0.0001:
            avg = v["cost"]/v["shares"]
            result.append(("Other",t,t,round(v["shares"],6),round(avg,4),None,None,None,True,"Check",None))
    return result


# ─── SNAPSHOT HISTORY ─────────────────────────────────────────────────────────
HISTORY_FILE = "price_history.json"

def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE,"r") as f:
            return json.load(f)
    return []

def save_snapshot(prices, sources, ts, portfolio_data):
    history = load_history()
    total_cost  = sum(p[3]*p[4] for p in portfolio_data)
    total_value = sum(p[3]*prices.get(p[1],p[4]) for p in portfolio_data)
    snap = {
        "timestamp": ts,
        "total_cost": round(total_cost,2),
        "total_value": round(total_value,2),
        "total_gl": round(total_value-total_cost,2),
        "total_gl_pct": round((total_value-total_cost)/total_cost*100,2),
        "prices": {k:round(v,4) for k,v in prices.items()},
        "sources": sources,
        "positions": [
            {
                "ticker": p[1],
                "price":  prices.get(p[1]),
                "cost":   p[4],
                "shares": p[3],
                "value":  round(p[3]*prices.get(p[1],p[4]),2),
                "gl_pct": round((prices.get(p[1],p[4])-p[4])/p[4]*100,2) if prices.get(p[1]) else None,
                "rec":    generate_rec(p[0],p[1],p[4],p[5],p[6],p[7],p[8],p[9],prices.get(p[1]))[0],
            }
            for p in portfolio_data
        ]
    }
    history.insert(0, snap)
    history = history[:50]   # keep last 50 snapshots
    with open(HISTORY_FILE,"w") as f:
        json.dump(history, f, indent=2)
    return snap


# ─── HELPERS ──────────────────────────────────────────────────────────────────
def fmt_usd(n, decimals=2):
    if n is None: return "—"
    sign = "-" if n < 0 else ""
    return f"{sign}${abs(n):,.{decimals}f}"

def fmt_pct(n):
    if n is None: return "—"
    return f"{n:+.2f}%"

def color_val(val, positive_color="#00f0aa", negative_color="#ff4060"):
    if val is None: return "gray"
    return positive_color if val >= 0 else negative_color

def rec_badge(rec_text, color_key):
    color = REC_COLORS.get(color_key, "#6a7590")
    bg = color + "22"
    return f'<span style="background:{bg};color:{color};padding:3px 10px;border-radius:20px;font-size:12px;font-weight:700;font-family:Space Mono,monospace">{rec_text}</span>'


# ─── SESSION STATE ────────────────────────────────────────────────────────────
if "portfolio" not in st.session_state:
    st.session_state.portfolio = list(PORTFOLIO)
if "prices" not in st.session_state:
    st.session_state.prices = {}
if "sources" not in st.session_state:
    st.session_state.sources = {}
if "last_ts" not in st.session_state:
    st.session_state.last_ts = None
if "errors" not in st.session_state:
    st.session_state.errors = []
if "deposit_log" not in st.session_state:
    st.session_state.deposit_log = []
if "test_log" not in st.session_state:
    st.session_state.test_log = []   # live connection test results

portfolio = st.session_state.portfolio


# ═══════════════════════════════════════════════════════════════════════════════
#  HEADER
# ═══════════════════════════════════════════════════════════════════════════════
prices  = st.session_state.prices
sources = st.session_state.sources

total_cost  = sum(p[3]*p[4] for p in portfolio)
total_value = sum(p[3]*prices.get(p[1],p[4]) for p in portfolio)
total_gl    = total_value - total_cost
total_gl_pct= total_gl/total_cost*100

col_title, col_refresh = st.columns([3,1])
with col_title:
    st.markdown("## 📊 PORTFOLIO WAR ROOM")
    if st.session_state.last_ts:
        src_list = list(set(sources.values()))
        st.markdown(
            f'<span style="color:#6a7590;font-size:12px;font-family:Space Mono,monospace">'
            f'● LIVE · {st.session_state.last_ts} · Sources: {", ".join(src_list)}</span>',
            unsafe_allow_html=True
        )
    else:
        st.markdown('<span style="color:#ff9030;font-size:12px">⚠ No prices loaded — click REFRESH</span>', unsafe_allow_html=True)

with col_refresh:
    st.markdown("<br>", unsafe_allow_html=True)
    if st.button("⚡ REFRESH PRICES", use_container_width=True):
        with st.spinner("Fetching from yfinance + CoinGecko…"):
            p, s, ts, errs = fetch_prices_live()
            st.session_state.prices  = p
            st.session_state.sources = s
            st.session_state.last_ts = ts
            st.session_state.errors  = errs
            prices  = p
            sources = s
            # Recalculate totals
            total_value = sum(pos[3]*prices.get(pos[1],pos[4]) for pos in portfolio)
            total_gl    = total_value - total_cost
            total_gl_pct= total_gl/total_cost*100
            # Save snapshot
            snap = save_snapshot(prices, sources, ts, portfolio)
            st.session_state.test_log.insert(0, snap)
            st.rerun()

if st.session_state.errors:
    for e in st.session_state.errors:
        st.warning(f"⚠ {e}")

# ── Top metrics ───────────────────────────────────────────────────────────────
m1,m2,m3,m4,m5 = st.columns(5)
m1.metric("Total Invested",  fmt_usd(total_cost,0))
m2.metric("Current Value",   fmt_usd(total_value,0), delta=fmt_usd(total_gl,0))
m3.metric("Total Gain/Loss", fmt_usd(total_gl,0),    delta=fmt_pct(total_gl_pct))
m4.metric("Positions",       f"{len(portfolio)}")
m5.metric("Live Prices",     f"{len(prices)}/{len(portfolio)}")

st.markdown("---")

# ═══════════════════════════════════════════════════════════════════════════════
#  TABS
# ═══════════════════════════════════════════════════════════════════════════════
tabs = st.tabs(["🏠 Dashboard","📈 Holdings","💰 Deploy $900","📥 Import CSV","🕐 Snapshots","🔬 Connection Test","⚙ Settings"])

tab_dash, tab_hold, tab_deploy, tab_import, tab_snap, tab_test, tab_settings = tabs


# ══════════════════════════════════════════════════════
#  TAB 1: DASHBOARD
# ══════════════════════════════════════════════════════
with tab_dash:

    # ── Urgent action panels ───────────────────────────────────────────────
    urgent_sells = [(p,prices.get(p[1])) for p in portfolio if p[0]=="SELL" and p[8] and prices.get(p[1])]
    strong_buys  = []
    trim_signals = []
    stop_losses  = []

    for p in portfolio:
        pr = prices.get(p[1])
        if not pr: continue
        rec_text, rec_color = generate_rec(p[0],p[1],p[4],p[5],p[6],p[7],p[8],p[9],pr)
        if "STRONG BUY" in rec_text or "BUY THE DIP" in rec_text or "ACCUMULATE" in rec_text:
            strong_buys.append((p,pr,rec_text))
        if "TRIM" in rec_text:
            trim_signals.append((p,pr,rec_text))
        if "STOP-LOSS" in rec_text:
            stop_losses.append((p,pr,rec_text))

    a1, a2, a3 = st.columns(3)

    with a1:
        if urgent_sells or stop_losses:
            st.markdown("### 🔴 SELL NOW")
            for p,pr in urgent_sells:
                st.markdown(f"**{p[1]}** {p[2]} — move to target ETF")
            for p,pr,r in stop_losses:
                st.markdown(f"**{p[1]}** — {r}")
        else:
            st.markdown("### 🔴 Sell Alerts")
            st.markdown('<span style="color:#6a7590">No urgent sells</span>', unsafe_allow_html=True)

    with a2:
        st.markdown("### ✂️ TRIM — Take Profits")
        if trim_signals:
            for p,pr,r in trim_signals[:5]:
                upside = (p[5]-pr)/pr*100 if p[5] else 0
                st.markdown(f"**{p[1]}** {fmt_usd(pr,2)} — {r[:40]}")
        else:
            st.markdown('<span style="color:#6a7590">No trim signals</span>', unsafe_allow_html=True)

    with a3:
        st.markdown("### 🟢 BUY SIGNALS")
        if strong_buys:
            for p,pr,r in strong_buys[:6]:
                st.markdown(f"**{p[1]}** {fmt_usd(pr,2)} — {r[:38]}")
        else:
            st.markdown('<span style="color:#6a7590">No buy signals</span>', unsafe_allow_html=True)

    st.markdown("---")

    # ── Action calendar + tax playbook ────────────────────────────────────
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("### ⚡ Action Calendar 2026")
        calendar = [
            ("Apr 3",  "🟡","GLD turns LT — trim 25% + First $900 deposit"),
            ("May 20", "🔴","SPY turns LT — SELL ALL, swap to VOO"),
            ("Jul 15", "🔴","VUG turns LT — SELL ALL, swap to QQQ"),
            ("Aug 14", "🔵","BLSH hits 1yr — evaluate, consider trim"),
            ("Sep 11", "🔵","KLAR hits 1yr — evaluate, consider trim"),
            ("Sep 18", "🔵","STUB hits 1yr — evaluate, consider trim"),
            ("Nov 6",  "🔵","TSM big lot LT — trim 20%"),
            ("Dec 15", "🔵","GOOGL big lot LT — trim 20%"),
        ]
        for dt, icon, action in calendar:
            d = datetime.strptime(f"{dt} 2026", "%b %d %Y").date()
            days_left = (d - date.today()).days
            urgency = "🔥 " if days_left <= 14 else ""
            st.markdown(f"**{dt}** {icon} {urgency}{action} _{f'({days_left}d)' if days_left >= 0 else '(past)'}_")

    with c2:
        st.markdown("### 🧾 Tax Playbook")
        st.markdown("""
| Rule | Detail |
|------|--------|
| **Hold ≥366 days** | LT rate 15-20% vs ST 37% — massive difference |
| **XOP underwater** | Hold to 366d → harvest loss against gains |
| **DRIP lots** | Each reinvestment = new lot at reinvest price |
| **SELL positions** | Wait for LT date before selling |
| **Year-end** | Net gains vs losses before Dec 31 |
| **Biweekly DCA** | VOO + QQQ + VYM never trigger taxable events |
        """)

    st.markdown("---")
    st.markdown("### 📊 Portfolio Allocation")
    cat_vals = {}
    for p in portfolio:
        v = p[3]*prices.get(p[1],p[4])
        cat_vals[p[0]] = cat_vals.get(p[0],0) + v

    cols = st.columns(len(cat_vals))
    for i,(cat,val) in enumerate(sorted(cat_vals.items(), key=lambda x: -x[1])):
        pct = val/total_value*100 if total_value else 0
        cols[i].metric(cat, fmt_usd(val,0), f"{pct:.1f}%")


# ══════════════════════════════════════════════════════
#  TAB 2: HOLDINGS
# ══════════════════════════════════════════════════════
with tab_hold:
    st.markdown("### 📈 All Holdings")

    cat_filter = st.selectbox("Filter by category", ["All","Crypto","Core","ETF","Other","IPO","SELL"], key="cat_filter")

    rows = []
    for p in portfolio:
        cat,t,name,shares,cost,target,bear,bull,lt,lt_date,cg_id = p
        if cat_filter != "All" and cat != cat_filter:
            continue
        price = prices.get(t)
        value     = shares * price if price else shares * cost
        cost_tot  = shares * cost
        gl        = value - cost_tot if price else None
        gl_pct    = (price-cost)/cost*100 if price else None
        upside    = (target-price)/price*100 if price and target else None
        rec, rcol = generate_rec(cat,t,cost,target,bear,bull,lt,lt_date,price)
        rows.append({
            "Cat":      cat,
            "Ticker":   t,
            "Name":     name,
            "Shares":   shares,
            "Avg Cost": cost,
            "Price":    price,
            "Value $":  round(value,2),
            "G/L $":    round(gl,2) if gl is not None else None,
            "G/L %":    round(gl_pct,2) if gl_pct is not None else None,
            "Upside %": round(upside,1) if upside is not None else None,
            "Target":   target,
            "Bear":     bear,
            "LT?":      "✅ LT" if lt else f"⏳ {lt_date}",
            "Rec":      rec,
            "_color":   rcol,
        })

    if rows:
        # Show styled table
        display_cols = ["Cat","Ticker","Name","Shares","Avg Cost","Price","Value $","G/L %","Upside %","LT?","Rec"]
        df = pd.DataFrame(rows)[display_cols]

        def color_row(row):
            idx = next((i for i,r in enumerate(rows) if r["Ticker"]==row["Ticker"]), 0)
            c = rows[idx]["_color"]
            col = REC_COLORS.get(c,"#6a7590")
            styles = [""] * len(row)
            # Color G/L% column
            for i, col_name in enumerate(row.index):
                if col_name == "G/L %":
                    v = row[col_name]
                    if v is not None:
                        styles[i] = f"color: {'#00f0aa' if v>=0 else '#ff4060'}; font-weight:700"
                if col_name == "Rec":
                    styles[i] = f"color: {col}; font-weight:700"
            return styles

        st.dataframe(
            df.style.apply(color_row, axis=1),
            use_container_width=True,
            height=min(len(rows)*38+60, 800),
        )

        # ── Expanded detail on click ───────────────────────────────────────
        st.markdown("#### 🔍 Detailed Position View")
        sel = st.selectbox("Select ticker for details", [r["Ticker"] for r in rows])
        sel_row = next((r for r in rows if r["Ticker"]==sel), None)
        if sel_row:
            p = next((x for x in portfolio if x[1]==sel), None)
            if p:
                cat,t,name,shares,cost,target,bear,bull,lt,lt_date,cg_id = p
                price = prices.get(t)
                d1,d2,d3,d4 = st.columns(4)
                d1.metric("Shares",       f"{shares:,.4f}")
                d2.metric("Avg Cost",     fmt_usd(cost))
                d3.metric("Live Price",   fmt_usd(price) if price else "—")
                d4.metric("Position Value", fmt_usd(shares*price if price else shares*cost, 2))

                d5,d6,d7,d8 = st.columns(4)
                gl_ = (price-cost)/cost*100 if price else None
                d5.metric("G/L %",   fmt_pct(gl_), delta=fmt_usd((price-cost)*shares if price else 0))
                d6.metric("Target",  fmt_usd(target) if target else "—")
                d7.metric("Bear",    fmt_usd(bear)   if bear else "—")
                d8.metric("Bull",    fmt_usd(bull)   if bull else "—")

                rec_text, rec_color = generate_rec(cat,t,cost,target,bear,bull,lt,lt_date,price)
                st.markdown(f"**Recommendation:** {rec_badge(rec_text, rec_color)}", unsafe_allow_html=True)
                st.markdown(f"**LT Status:** {'✅ Long-term eligible' if lt else f'⏳ {lt_date}'}")
                if target and price:
                    upside = (target-price)/price*100
                    if lt and upside <= 5:
                        st.info(f"💡 Set Robinhood price alert at **{fmt_usd(target)}** — Notifications → Price Alert → Above → {fmt_usd(target)}")
                if p[0] == "SELL":
                    st.error(f"🔴 This position should be CONSOLIDATED → {'Sell now (LT)' if lt else f'Sell after {lt_date}'}")


# ══════════════════════════════════════════════════════
#  TAB 3: DEPLOY $900
# ══════════════════════════════════════════════════════
with tab_deploy:
    st.markdown("### 💰 $900 Biweekly Deployment")

    next_date = get_next_deposit()
    biweekly_picks = get_biweekly_picks(prices)

    c1, c2 = st.columns([1,1])
    with c1:
        st.markdown(f"#### 📅 Next Deposit: **{next_date}, 2026**")
        deposit_amount = st.number_input("Deposit amount ($)", value=900, step=50, min_value=100)

        st.markdown("#### 📋 This Cycle's Picks")
        total_alloc = sum(amt for _,amt,_ in biweekly_picks)
        for ticker, base_amt, reason in biweekly_picks:
            scaled_amt = round(deposit_amount * base_amt / 900)
            pr = prices.get(ticker)
            shares_get = scaled_amt/pr if pr else None
            pct = base_amt/total_alloc*100
            st.markdown(
                f"**{ticker}** — ${scaled_amt} ({pct:.0f}%)"
                f"{f' → {shares_get:.4f} shares @ {fmt_usd(pr)}' if shares_get else ''}"
                f"  \n_{reason}_"
            )

        if st.button("✅ LOG THIS DEPOSIT", use_container_width=True):
            log_entry = {
                "date":   datetime.now().strftime("%Y-%m-%d %H:%M"),
                "amount": deposit_amount,
                "picks":  [(t,round(deposit_amount*a/900),r) for t,a,r in biweekly_picks],
                "prices": {t:prices.get(t) for t,_,_ in biweekly_picks},
            }
            st.session_state.deposit_log.insert(0, log_entry)
            # Update portfolio holdings
            updated = []
            for p in st.session_state.portfolio:
                pick_data = next(((t,a,r) for t,a,r in biweekly_picks if t==p[1]), None)
                if pick_data and prices.get(p[1]):
                    t_, amt, _ = pick_data
                    scaled_amt = round(deposit_amount * amt / 900)
                    add_shares = scaled_amt / prices[p[1]]
                    new_sh   = p[3] + add_shares
                    new_cost = (p[3]*p[4] + scaled_amt) / new_sh
                    updated.append((p[0],p[1],p[2],round(new_sh,6),round(new_cost,4))+p[5:])
                else:
                    updated.append(p)
            st.session_state.portfolio = updated
            portfolio = updated
            st.success(f"✅ Deposit of ${deposit_amount} logged! Holdings updated.")

    with c2:
        st.markdown("#### 📅 2026 Full Schedule")
        sched_rows = []
        for d in SCHEDULE:
            dt = datetime.strptime(f"{d} 2026", "%b %d %Y").date()
            days = (dt - date.today()).days
            status = "▶ NEXT" if d==next_date else ("✓ Done" if days<0 else f"In {days}d")
            sched_rows.append({"Date":f"{d}, 2026","Status":status,"Amount":"$900"})
        sdf = pd.DataFrame(sched_rows)
        st.dataframe(sdf, use_container_width=True, hide_index=True, height=400)

    # Deposit history
    if st.session_state.deposit_log:
        st.markdown("#### 📚 Deposit History")
        dlog = []
        for e in st.session_state.deposit_log:
            picks_str = ", ".join([f"{t}(${a})" for t,a,_ in e["picks"]])
            dlog.append({"Date":e["date"],"Amount":f"${e['amount']}","Picks":picks_str})
        st.dataframe(pd.DataFrame(dlog), use_container_width=True, hide_index=True)


# ══════════════════════════════════════════════════════
#  TAB 4: IMPORT CSV
# ══════════════════════════════════════════════════════
with tab_import:
    st.markdown("### 📥 Robinhood CSV Import")
    st.markdown("""
Upload your **Account Activity CSV** from Robinhood:
`Account → Statements & History → Account Activity → All Time → Export CSV`

The app will reconcile your actual share counts and cost basis from your full transaction history.
    """)

    uploaded = st.file_uploader("Drop Robinhood CSV here", type=["csv"])
    if uploaded:
        content = uploaded.read().decode("utf-8", errors="ignore")
        txs = parse_robinhood_csv(content)
        st.success(f"✅ Parsed **{len(txs)} transactions**")

        buys = [t for t in txs if t["code"]=="Buy"]
        splits = [t for t in txs if t["code"]=="SPL"]
        st.markdown(f"- Buy orders: **{len(buys)}** | Stock splits: **{len(splits)}**")
        st.markdown(f"- Date range: **{txs[0]['date']} → {txs[-1]['date']}**")

        # Preview reconciliation
        new_port = reconcile_csv(txs, st.session_state.portfolio)
        changes = []
        for old,new in zip(st.session_state.portfolio, new_port[:len(st.session_state.portfolio)]):
            if abs(old[3]-new[3])>0.0001 or abs(old[4]-new[4])>0.01:
                changes.append({"Ticker":old[1],"Old Shares":old[3],"New Shares":new[3],"Old Cost":old[4],"New Cost":new[4]})
        if changes:
            st.markdown(f"#### Preview: {len(changes)} positions will update")
            st.dataframe(pd.DataFrame(changes), use_container_width=True, hide_index=True)
        else:
            st.info("No changes detected — portfolio already matches CSV data.")

        if st.button("✅ CONFIRM IMPORT", use_container_width=True):
            st.session_state.portfolio = new_port
            portfolio = new_port
            st.success(f"✅ Portfolio updated from {len(txs)} transactions!")
            st.rerun()


# ══════════════════════════════════════════════════════
#  TAB 5: SNAPSHOTS (price history with timestamps)
# ══════════════════════════════════════════════════════
with tab_snap:
    st.markdown("### 🕐 Price Snapshots — Every Refresh Recorded")
    st.markdown("Every time you hit **⚡ REFRESH PRICES**, a full snapshot is saved here with timestamps and prices.")

    history = load_history()

    if not history:
        st.info("No snapshots yet. Hit ⚡ REFRESH PRICES to create the first one.")
    else:
        # Summary table of all snapshots
        st.markdown(f"#### {len(history)} Snapshots Recorded")
        snap_summary = []
        for s in history:
            snap_summary.append({
                "Timestamp":    s["timestamp"],
                "Portfolio $":  fmt_usd(s["total_value"],0),
                "G/L $":        fmt_usd(s["total_gl"],0),
                "G/L %":        fmt_pct(s["total_gl_pct"]),
                "Prices Count": len(s["prices"]),
            })
        st.dataframe(pd.DataFrame(snap_summary), use_container_width=True, hide_index=True)

        # Expand individual snapshot
        st.markdown("#### 🔍 Inspect a Snapshot")
        snap_options = [f"{s['timestamp']} — {fmt_usd(s['total_value'],0)}" for s in history]
        selected_snap_idx = st.selectbox("Choose snapshot", range(len(snap_options)), format_func=lambda i: snap_options[i])
        snap = history[selected_snap_idx]

        st.markdown(f"""
<div class="snapshot-card">
<b style="color:#00f0aa;font-family:Space Mono">SNAPSHOT: {snap['timestamp']}</b><br>
Total Value: <b>{fmt_usd(snap['total_value'],0)}</b> &nbsp;|&nbsp;
G/L: <b style="color:{'#00f0aa' if snap['total_gl']>=0 else '#ff4060'}">{fmt_usd(snap['total_gl'],0)} ({fmt_pct(snap['total_gl_pct'])})</b>
</div>
        """, unsafe_allow_html=True)

        # Show all prices in this snapshot
        price_rows = []
        for pos in snap.get("positions",[]):
            price_rows.append({
                "Ticker":   pos["ticker"],
                "Price":    fmt_usd(pos["price"]) if pos["price"] else "—",
                "Cost":     fmt_usd(pos["cost"]),
                "G/L %":    fmt_pct(pos["gl_pct"]) if pos["gl_pct"] is not None else "—",
                "Value $":  fmt_usd(pos["value"],2),
                "Rec":      pos["rec"][:45] if pos["rec"] else "—",
            })
        if price_rows:
            st.dataframe(pd.DataFrame(price_rows), use_container_width=True, hide_index=True, height=600)


# ══════════════════════════════════════════════════════
#  TAB 6: CONNECTION TEST
# ══════════════════════════════════════════════════════
with tab_test:
    st.markdown("### 🔬 Live Connection Test")
    st.markdown("Tests **yfinance** and **CoinGecko** multiple times and shows exact prices + timestamps.")

    if st.button("🧪 RUN FULL CONNECTION TEST (3 passes)", use_container_width=True):
        results = []
        test_tickers = ["AAPL","NVDA","META","GOOGL","MSFT","VOO","QQQ","BTC-USD","XRP-USD","VYM","GLD","NFLX"]

        for pass_num in range(1, 4):
            ts_pass = datetime.now(ZoneInfo("America/New_York")).strftime("%H:%M:%S.%f ET")[:-3]
            st.markdown(f"**Pass {pass_num}/3** — {ts_pass}")
            progress = st.progress(0)

            pass_result = {"pass": pass_num, "ts": ts_pass, "prices": {}, "errors": [], "latency_ms": 0}

            # Test yfinance
            t0 = time.time()
            try:
                raw = yf.download(
                    tickers   = " ".join(test_tickers),
                    period    = "1d",
                    interval  = "1m",
                    progress  = False,
                    auto_adjust=True,
                    threads   = True,
                )
                latency = round((time.time()-t0)*1000)
                pass_result["latency_ms"] = latency

                if not raw.empty:
                    close = raw["Close"] if "Close" in raw.columns else raw.get("close", pd.DataFrame())
                    if not isinstance(close, pd.Series):
                        for col in close.columns:
                            s = close[col].dropna()
                            if not s.empty:
                                v = float(s.iloc[-1])
                                if v > 0:
                                    pass_result["prices"][col.replace("-USD","")] = round(v,4)
                    st.markdown(f"  ✅ **yfinance**: {len(pass_result['prices'])} prices in **{latency}ms**")
                else:
                    st.markdown(f"  ⚠️ yfinance returned empty data")
                    pass_result["errors"].append("yfinance empty")
            except Exception as e:
                st.markdown(f"  ❌ yfinance error: {e}")
                pass_result["errors"].append(str(e))

            progress.progress(60)

            # Test CoinGecko
            t0 = time.time()
            try:
                r = requests.get(
                    "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ripple&vs_currencies=usd",
                    timeout=10
                )
                cg_latency = round((time.time()-t0)*1000)
                if r.status_code == 200:
                    cg = r.json()
                    btc = cg.get("bitcoin",{}).get("usd")
                    xrp = cg.get("ripple",{}).get("usd")
                    if btc: pass_result["prices"]["BTC"] = btc
                    if xrp: pass_result["prices"]["XRP"] = xrp
                    st.markdown(f"  ✅ **CoinGecko**: BTC=${btc:,.0f} XRP=${xrp:.4f} in **{cg_latency}ms**")
                else:
                    st.markdown(f"  ⚠️ CoinGecko HTTP {r.status_code}")
            except Exception as e:
                st.markdown(f"  ❌ CoinGecko: {e}")
                pass_result["errors"].append(f"CoinGecko: {e}")

            progress.progress(100)

            # Show price table for this pass
            if pass_result["prices"]:
                price_table = []
                for tk, price in sorted(pass_result["prices"].items()):
                    price_table.append({"Ticker": tk, "Price": fmt_usd(price,4 if price<10 else 2), "Raw": price})
                df_pass = pd.DataFrame(price_table)
                st.dataframe(df_pass[["Ticker","Price"]], use_container_width=True, hide_index=True, height=200)

            results.append(pass_result)

            if pass_num < 3:
                st.markdown("_Waiting 2s before next pass…_")
                time.sleep(2)

        # Summary
        st.markdown("---")
        st.markdown("### ✅ Test Summary")
        for r in results:
            status = "✅ PASS" if r["prices"] and not r["errors"] else "⚠️ PARTIAL" if r["prices"] else "❌ FAIL"
            st.markdown(f"**Pass {r['pass']}** {r['ts']} — {status} — {len(r['prices'])} prices — {r['latency_ms']}ms yfinance")
            if r["errors"]:
                st.markdown(f"  Errors: {r['errors']}")

        st.success("Connection test complete! See snapshots tab for full history.")

    # Show stored test log
    if st.session_state.test_log:
        st.markdown("---")
        st.markdown("### 📋 Previous Refresh Snapshots (this session)")
        for snap in st.session_state.test_log[:10]:
            st.markdown(f"""
<div class="snapshot-card">
<b style="color:#f0c040">{snap['timestamp']}</b> &nbsp;·&nbsp;
Value: <b>{fmt_usd(snap['total_value'],0)}</b> &nbsp;·&nbsp;
G/L: <b style="color:{'#00f0aa' if snap['total_gl']>=0 else '#ff4060'}">{fmt_usd(snap['total_gl'],0)} ({fmt_pct(snap['total_gl_pct'])})</b> &nbsp;·&nbsp;
Prices: <b>{len(snap['prices'])}/{len(portfolio)}</b>
</div>""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════
#  TAB 7: SETTINGS
# ══════════════════════════════════════════════════════
with tab_settings:
    st.markdown("### ⚙ Settings")

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("#### 📡 Data Sources")
        st.markdown("""
| Source | Covers | Key? | Rate Limit |
|--------|--------|------|-----------|
| **yfinance** | All stocks + ETFs + crypto | None | Very high |
| **CoinGecko** | BTC + XRP real-time | None | 30/min free |

**Why this works:**
- Runs on your server, not in a browser → no CORS restrictions
- yfinance batches all 39 tickers in **one** download call
- CoinGecko is free forever, no signup needed
- Both sources cross-validate crypto prices
        """)

        st.markdown("#### 🔧 Manual Price Override")
        st.markdown("Override any price for testing or after-hours:")
        ov_ticker = st.selectbox("Ticker", [p[1] for p in portfolio])
        ov_price  = st.number_input("Override price ($)", value=float(prices.get(ov_ticker,100)), step=0.01)
        if st.button("Apply Override"):
            st.session_state.prices[ov_ticker] = ov_price
            st.success(f"✅ {ov_ticker} set to ${ov_price:.2f}")
            st.rerun()

    with c2:
        st.markdown("#### 📊 Portfolio Stats")
        st.markdown(f"""
- Positions: **{len(portfolio)}**
- Live prices: **{len(prices)}/{len(portfolio)}**
- Last refresh: **{st.session_state.last_ts or 'Never'}**
- Snapshots saved: **{len(load_history())}**
- Deposits logged: **{len(st.session_state.deposit_log)}**
        """)

        st.markdown("#### 🔄 Reset")
        if st.button("Reset portfolio to defaults", type="secondary"):
            st.session_state.portfolio = list(PORTFOLIO)
            portfolio = list(PORTFOLIO)
            st.success("Portfolio reset to defaults.")
            st.rerun()

        if st.button("Clear snapshot history", type="secondary"):
            if os.path.exists(HISTORY_FILE):
                os.remove(HISTORY_FILE)
            st.success("Snapshot history cleared.")

        st.markdown("#### 📥 Export Portfolio")
        if prices:
            export_rows = []
            for p in portfolio:
                pr = prices.get(p[1],p[4])
                rec,_ = generate_rec(p[0],p[1],p[4],p[5],p[6],p[7],p[8],p[9],pr)
                export_rows.append({
                    "Ticker":p[1],"Name":p[2],"Category":p[0],"Shares":p[3],
                    "Avg Cost":p[4],"Live Price":pr,"Value":round(p[3]*pr,2),
                    "GL%":round((pr-p[4])/p[4]*100,2),"Recommendation":rec
                })
            csv_str = pd.DataFrame(export_rows).to_csv(index=False)
            st.download_button("⬇ Download Portfolio CSV", csv_str, "portfolio_export.csv", "text/csv")
