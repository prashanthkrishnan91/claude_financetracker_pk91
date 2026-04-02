"""
Portfolio War Room v8.2
Fixes:
  - fetch_all_prices now accepts tuple (hashable) so @st.cache_data works correctly
  - yfinance: per-ticker fast_info fallback so single-ticker and delisted never crash
  - _refresh() centralises recompute+price+recs so CSV import shows cards immediately
  - None-price guard everywhere (_ep helper)
  - PDF crypto parser restored
  - Null-byte-safe: written in one shell heredoc pass
"""

import streamlit as st
import pandas as pd
import yfinance as yf
import requests
import hashlib
import csv
import io
import json
import re
import datetime
from pathlib import Path
import plotly.express as px
import plotly.graph_objects as go

# ── PERSISTENCE ───────────────────────────────────────────────────────────────
TX_STORE_PATH    = Path("tx_store.json")
REC_HISTORY_PATH = Path("rec_history.json")
DEPOSIT_LOG_PATH = Path("deposit_log.json")
CRYPTO_OVR_PATH  = Path("crypto_overrides.json")

def _load(path, default):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default

def _save(path, obj):
    path.write_text(json.dumps(obj), encoding="utf-8")

# ── PAGE CONFIG ───────────────────────────────────────────────────────────────
st.set_page_config(page_title="Portfolio War Room", page_icon="📈",
                   layout="wide", initial_sidebar_state="collapsed")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700&family=Inter:wght@400;600;700&display=swap');
html,body,[class*="css"]{background:#0a0e1a;color:#e2e8f0;font-family:'Inter',sans-serif}
.stApp{background:#0a0e1a}
h1,h2,h3{color:#f8fafc}
.kcard{background:linear-gradient(135deg,#1e2538,#151929);border:1px solid #2d3748;
       border-radius:12px;padding:18px 22px;margin:4px 0}
.kval{font-family:'JetBrains Mono',monospace;font-size:1.55rem;font-weight:700}
.klbl{font-size:.72rem;color:#94a3b8;text-transform:uppercase;letter-spacing:.08em}
.pbadge{background:#14532d;color:#86efac;padding:4px 10px;border-radius:6px;
        font-size:.72rem;font-family:'JetBrains Mono',monospace}
div[data-testid="stDataFrame"]{border-radius:8px;overflow:hidden}
</style>
""", unsafe_allow_html=True)

# ── CONSTANTS ─────────────────────────────────────────────────────────────────
CRYPTO_TICKERS = {"BTC","XRP","ETH","SOL","DOGE"}
FOREVER_HOLD   = {"VYM","SCHD","VTI"}
DCA_ALWAYS     = {"VOO","QQQ"}
SELL_LIST      = {"VTV","VEA","VWO","BND"}
TARGETS = {
    "NVDA":200,"AAPL":240,"NFLX":900,"WMT":110,"GLD":450,"XLE":75,
    "VXUS":90,"QQQ":620,"VOO":620,"VYM":165,"SCHD":38,"BTC":120000,
    "XRP":5.0,"META":700,"GOOGL":220,"MSFT":460,"VGT":750,"VHT":290,
    "VIS":340,"QCOM":170,"COST":1100,"TSM":220,
}
BIWEEKLY_PLAN = [
    {"ticker":"NVDA","pct":.28,"rationale":"AI supercycle"},
    {"ticker":"VOO", "pct":.22,"rationale":"S&P 500 — DCA forever"},
    {"ticker":"VYM", "pct":.17,"rationale":"Dividend engine"},
    {"ticker":"QQQ", "pct":.17,"rationale":"Nasdaq-100"},
    {"ticker":"META","pct":.16,"rationale":"Rotating pick"},
]
ROTATING      = ["META","GOOGL","AAPL","MSFT","COST","TSM","CRM","NFLX"]
DEPOSIT_AMT   = 900
DEPOSIT_START = datetime.date(2026,4,3)
COIN_MAP = {
    "bitcoin":"BTC","ethereum":"ETH","solana":"SOL","dogecoin":"DOGE",
    "xrp":"XRP","litecoin":"LTC","cardano":"ADA","avalanche":"AVAX",
}
ACTION_CALENDAR = [
    {"Date":"Apr 3", "Action":"🔴 SELL VTV,VEA,VWO,BND — LT eligible → reinvest VOO/VYM"},
    {"Date":"Apr 3", "Action":"💰 Deposit #1 — NVDA/VOO/VYM/QQQ + META"},
    {"Date":"Apr 17","Action":"💰 Deposit #2 — NVDA/VOO/VYM/QQQ + GOOGL"},
    {"Date":"May 20","Action":"🔴 SPY → LT — sell all, buy VOO same day"},
    {"Date":"Jul 15","Action":"🔴 VUG → LT — sell all, buy QQQ same day"},
    {"Date":"Nov 6", "Action":"🔵 TSM big lot → LT — trim 20%"},
    {"Date":"Dec 15","Action":"🔵 GOOGL big lot → LT — trim 20%"},
    {"Date":"Dec 20","Action":"🧾 Year-end: net gains vs losses before Dec 31"},
]

# ── HELPERS ───────────────────────────────────────────────────────────────────
def _dollar(s) -> float:
    try:
        return float(str(s).strip().replace("$","").replace(",","")
                     .replace("(", "-").replace(")",""))
    except Exception:
        return 0.0

def _qty(s) -> float:
    try:
        return float(str(s).strip().replace(",",""))
    except Exception:
        return 0.0

def _date(s):
    for fmt in ("%m/%d/%Y","%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(str(s).strip(), fmt).date()
        except Exception:
            pass
    return None

def _ep(ticker, pos, prices):
    """Safe effective price: live price or fall back to avg_cost."""
    p = prices.get(ticker)
    if p is None or (isinstance(p, float) and p != p):   # None or NaN
        return float(pos["avg_cost"])
    return float(p)

def lt_ok(fbd):
    if fbd is None:
        return False
    return (datetime.date.today() - fbd).days >= 366

# ── CSV INGESTION ─────────────────────────────────────────────────────────────
def _fp(row):
    key = "|".join([row.get("Activity Date",""), row.get("Trans Code",""),
                    row.get("Instrument",""),    row.get("Quantity",""),
                    row.get("Amount",""),        row.get("Price","")])
    return hashlib.sha1(key.encode()).hexdigest()

def ingest_csv(source, store):
    if hasattr(source,"read"):
        raw = source.read()
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8-sig", errors="replace")
    else:
        raw = Path(source).read_text(encoding="utf-8-sig")

    clean = []
    for line in raw.splitlines():
        s = line.strip().strip('"')
        if not s or s.startswith("The data provided"):
            if s.startswith("The data provided"):
                break
            continue
        clean.append(line)

    reader  = csv.DictReader(io.StringIO("\n".join(clean)), quoting=csv.QUOTE_ALL)
    new     = dict(store)
    added   = skipped = 0
    OK_CODES = {"Buy","Sell","CDIV","SPL","REC","LIQ","ACH","RTP","JNLS","MISC","ACATS"}
    for row in reader:
        fp   = _fp(row)
        code = str(row.get("Trans Code","")).strip()
        if fp in new or code not in OK_CODES:
            skipped += 1
            continue
        new[fp] = {k: str(v) for k,v in row.items()}
        added += 1
    return new, {"added":added,"skipped":skipped,"total":len(new)}

# ── CRYPTO PDF PARSER ─────────────────────────────────────────────────────────
def parse_crypto_pdf(file_obj):
    try:
        import pdfplumber
    except ImportError:
        return None
    try:
        with pdfplumber.open(file_obj) as pdf:
            text = "\n".join(p.extract_text() or "" for p in pdf.pages)
        result = {}
        pat = re.compile(
            r"([A-Za-z][A-Za-z ]{1,20}?)\s+([\d]+\.[\d]+)\s+([A-Z]{2,6})"
            r"\s+\$([\d,]+\.[\d]{2})\s+[\d.]+%"
        )
        for m in pat.finditer(text):
            name   = m.group(1).strip().lower()
            qty    = float(m.group(2))
            symbol = m.group(3).upper()
            mktval = float(m.group(4).replace(",",""))
            ticker = symbol if symbol in CRYPTO_TICKERS else COIN_MAP.get(name, symbol)
            if qty > 0:
                result[ticker] = {"shares": qty, "market_value": mktval}
        if not result:
            for line in text.splitlines():
                ll = line.lower()
                for name, ticker in COIN_MAP.items():
                    if name in ll:
                        nums = re.findall(r"[\d]+\.[\d]+", line)
                        if nums:
                            result[ticker] = {
                                "shares": float(nums[0]),
                                "market_value": float(nums[1].replace(",","")) if len(nums)>1 else 0.0
                            }
        return result or None
    except Exception:
        return None

# ── PORTFOLIO RECOMPUTE ───────────────────────────────────────────────────────
def recompute(store, crypto_ovr=None):
    rows = sorted(store.values(),
                  key=lambda r: _date(r.get("Activity Date","")) or datetime.date(2000,1,1))
    pf = {}
    for row in rows:
        code   = str(row.get("Trans Code","")).strip()
        ticker = str(row.get("Instrument","")).strip().upper()
        qty    = _qty(row.get("Quantity",""))
        price  = _dollar(row.get("Price",""))
        amount = abs(_dollar(row.get("Amount","")))
        desc   = str(row.get("Description","")).lower()
        dt     = _date(row.get("Activity Date",""))

        if code in ("ACH","RTP","CDIV"):
            continue

        if code in ("Buy","REC","SPL"):
            if not ticker:
                continue
            is_drip = "reinvestment" in desc
            if ticker not in pf:
                pf[ticker] = {"shares":0.0,"avg_cost":0.0,"first_buy_date":dt,
                               "drip_count":0,"drip_total":0.0}
            pos  = pf[ticker]
            cost = (price * qty) if (price and qty) else amount
            ns   = pos["shares"] + qty
            if ns > 0:
                pos["avg_cost"] = (pos["shares"] * pos["avg_cost"] + cost) / ns
            pos["shares"] = ns
            if is_drip:
                pos["drip_count"] += 1
                pos["drip_total"]  = round(pos["drip_total"] + cost, 4)
            if pos["first_buy_date"] is None or (dt and dt < pos["first_buy_date"]):
                pos["first_buy_date"] = dt

        elif code == "Sell":
            if ticker in pf:
                pf[ticker]["shares"] = max(0.0, pf[ticker]["shares"] - qty)

        elif code == "LIQ":
            if ticker in pf:
                pf[ticker]["shares"] = 0.0

    if crypto_ovr:
        for t, info in crypto_ovr.items():
            pf[t] = {"shares": info.get("shares",0), "avg_cost": info.get("avg_cost",0),
                     "first_buy_date": None, "drip_count":0, "drip_total":0.0}

    return {k:v for k,v in pf.items() if v["shares"] > 0.0001}

# ── PRICE FETCHER ─────────────────────────────────────────────────────────────
# IMPORTANT: argument must be a tuple (hashable) for @st.cache_data to work.
@st.cache_data(ttl=120, show_spinner="Fetching live prices…")
def fetch_prices(tickers: tuple) -> dict:
    """Fetch live prices for all tickers. tickers MUST be a tuple."""
    prices = {}
    stocks  = [t for t in tickers if t not in CRYPTO_TICKERS]
    cryptos = [t for t in tickers if t in CRYPTO_TICKERS]

    # Per-ticker fetch via fast_info — works for 1 or 100 tickers, never crashes
    for t in stocks:
        try:
            info = yf.Ticker(t).fast_info
            p = info.get("last_price") or info.get("regularMarketPrice")
            prices[t] = round(float(p), 2) if p else None
        except Exception:
            prices[t] = None

    # CoinGecko for crypto
    cg = {"BTC":"bitcoin","XRP":"ripple","ETH":"ethereum","SOL":"solana","DOGE":"dogecoin"}
    for t in cryptos:
        cid = cg.get(t)
        if not cid:
            prices[t] = None
            continue
        try:
            r = requests.get(
                f"https://api.coingecko.com/api/v3/simple/price?ids={cid}&vs_currencies=usd",
                timeout=8)
            prices[t] = round(float(r.json()[cid]["usd"]), 4)
        except Exception:
            prices[t] = None

    return prices

# ── RECOMMENDATION ENGINE ─────────────────────────────────────────────────────
def generate_recs(pf, prices):
    recs = []
    for ticker, pos in pf.items():
        price  = _ep(ticker, pos, prices)
        shares = pos["shares"]
        cost   = pos["avg_cost"]
        fbd    = pos["first_buy_date"]
        is_lt  = lt_ok(fbd)
        equity = price * shares
        pnl    = (price - cost) / cost * 100 if cost > 0 else 0.0
        target = TARGETS.get(ticker, cost * 1.25)
        upside = (target - price) / price * 100 if price > 0 else 0.0
        lt_date = (fbd + datetime.timedelta(days=366)) if fbd else None
        tax    = ("✅ LT (15%)" if is_lt
                  else f"⏳ ST — LT from {lt_date}" if lt_date else "⏳ ST")

        if ticker in FOREVER_HOLD:
            action = "♾ HOLD FOREVER — DRIP on"
            rat    = "Income ETF — reinvest every dividend"
        elif ticker in DCA_ALWAYS:
            action = "📈 DCA ALWAYS"
            rat    = "Core index — never stop accumulating"
        elif ticker in SELL_LIST:
            if is_lt:
                action = "🔴 SELL NOW — LT eligible"
                rat    = "Exit → reinvest VOO/VYM same day"
            else:
                action = f"⏳ WAIT → SELL {lt_date}" if lt_date else "⏳ WAIT → SELL"
                rat    = "Hold for LT treatment"
        elif ticker in CRYPTO_TICKERS:
            if upside > 25:
                action = "🚀 ACCUMULATE — crypto upside"
                rat    = f"Target ${target:,.0f} — {upside:.0f}% upside"
            elif pnl > 20 and is_lt:
                action = "✂️ TRIM 25% — LT crypto"
                rat    = "Lock gains; keep 75%"
            else:
                action = "🟡 HOLD — crypto core"
                rat    = "Within normal range"
        elif pnl < -20:
            action = "🚨 STOP-LOSS REVIEW"
            rat    = f"Down {pnl:.1f}% — review position"
        elif pnl < -8 and upside > 20:
            action = "💎 STRONG BUY — dip"
            rat    = f"Down {pnl:.1f}%, {upside:.0f}% to target"
        elif upside > 20:
            action = "🟢 ACCUMULATE"
            rat    = f"{upside:.0f}% to target ${target}"
        elif pnl > 20 and is_lt:
            action = "✂️ TRIM 20% — LT gains"
            rat    = f"Up {pnl:.1f}% — harvest at LT rates"
        elif pnl > 20:
            action = "🟡 HOLD — wait for LT"
            rat    = f"Up {pnl:.1f}% but ST"
        else:
            action = "🟡 HOLD"
            rat    = f"{upside:.0f}% to target; P&L {pnl:+.1f}%"

        proceeds = None
        if "TRIM" in action:
            proceeds = round(shares * price * 0.20, 2)
        elif "SELL NOW" in action:
            proceeds = round(shares * price, 2)

        recs.append({"ticker":ticker,"shares":round(shares,4),"avg_cost":round(cost,2),
                     "price":prices.get(ticker),"pnl_pct":round(pnl,2),
                     "equity":round(equity,2),"action":action,"rationale":rat,
                     "tax_note":tax,"proceeds":proceeds})

    priority = {"SELL NOW":0,"STOP-LOSS":1,"STRONG BUY":2,"TRIM":3,
                "ACCUMULATE":4,"DCA":5,"HOLD FOREVER":6}
    recs.sort(key=lambda r: next((v for k,v in priority.items() if k in r["action"]), 7))
    return recs

# ── BIWEEKLY SCHEDULE ─────────────────────────────────────────────────────────
def deploy_schedule(prices):
    rows = []
    for i in range(18):
        d   = DEPOSIT_START + datetime.timedelta(days=14*i)
        rot = ROTATING[i % len(ROTATING)]
        plan = BIWEEKLY_PLAN[:-1] + [{"ticker":rot,"pct":.16,"rationale":"Rotating pick"}]
        for s in plan:
            t, amt = s["ticker"], round(DEPOSIT_AMT * s["pct"], 2)
            p = prices.get(t)
            rows.append({"Date":d.strftime("%b %d, %Y"),"Ticker":t,
                         "Amount":f"${amt:,.2f}",
                         "Est Shares": round(amt/p,4) if p else "—",
                         "Rationale":s["rationale"]})
    return pd.DataFrame(rows)

# ── SESSION STATE ─────────────────────────────────────────────────────────────
if "tx_store"       not in st.session_state:
    st.session_state.tx_store       = _load(TX_STORE_PATH, {})
if "crypto_ovr"     not in st.session_state:
    st.session_state.crypto_ovr     = _load(CRYPTO_OVR_PATH,
        {"BTC":{"shares":0.03432981,"avg_cost":52800.0},
         "XRP":{"shares":1.066,     "avg_cost":0.68}})
if "prices"         not in st.session_state: st.session_state.prices         = {}
if "portfolio"      not in st.session_state: st.session_state.portfolio      = {}
if "recs"           not in st.session_state: st.session_state.recs           = []
if "rec_history"    not in st.session_state: st.session_state.rec_history    = _load(REC_HISTORY_PATH,[])
if "deposit_log"    not in st.session_state: st.session_state.deposit_log    = _load(DEPOSIT_LOG_PATH,[])

# ── CENTRAL REFRESH ───────────────────────────────────────────────────────────
def _refresh():
    """Recompute portfolio, fetch prices, generate recs — all in one call."""
    pf = recompute(st.session_state.tx_store, st.session_state.crypto_ovr)
    st.session_state.portfolio = pf
    # tuple so @st.cache_data can hash it
    tickers = tuple(sorted(pf.keys()))
    prices  = fetch_prices(tickers)
    st.session_state.prices = prices
    st.session_state.recs   = generate_recs(pf, prices)

# Auto-load on first run if we have stored transactions
if st.session_state.tx_store and not st.session_state.portfolio:
    _refresh()

# ── HEADER ────────────────────────────────────────────────────────────────────
st.markdown("## 📈 Portfolio War Room")
hc1, hc2 = st.columns([4,1])
with hc1:
    n = len(st.session_state.tx_store)
    if n:
        st.markdown(f'<span class="pbadge">✅ {n} transactions on disk — no re-upload needed</span>',
                    unsafe_allow_html=True)
    else:
        st.info("No transactions yet — go to **Import** to upload your Robinhood CSV.")
with hc2:
    if st.button("🔄 Refresh Prices", type="primary", use_container_width=True):
        st.cache_data.clear()
        _refresh()
        st.rerun()

# ── TABS ──────────────────────────────────────────────────────────────────────
tabs = st.tabs(["📊 Overview","📋 Holdings","💰 Deploy","📥 Import","🌱 DRIP","🕐 History","⚙️ Settings"])

# ════════════════════ TAB 1 — OVERVIEW ═══════════════════════════════════════
with tabs[0]:
    pf    = st.session_state.portfolio
    pr    = st.session_state.prices
    recs  = st.session_state.recs

    if not pf:
        st.info("Upload your CSV in the **Import** tab, then click **🔄 Refresh Prices**.")
    else:
        total_equity = sum(_ep(t, pos, pr) * pos["shares"] for t, pos in pf.items())
        total_cost   = sum(pos["avg_cost"] * pos["shares"]  for pos in pf.values())
        total_pnl    = total_equity - total_cost
        pnl_pct      = total_pnl / total_cost * 100 if total_cost else 0

        sells  = [r for r in recs if "SELL NOW"    in r["action"]]
        buys   = [r for r in recs if "STRONG BUY"  in r["action"] or "ACCUMULATE" in r["action"]]
        alerts = [r for r in recs if "STOP-LOSS"   in r["action"]]

        c1,c2,c3,c4,c5 = st.columns(5)
        for col, lbl, val, color in [
            (c1,"💼 Portfolio Value", f"${total_equity:,.0f}",         "#60a5fa"),
            (c2,"📈 Total P&L",
             f"{'+'if total_pnl>0 else ''}{total_pnl:,.0f} ({pnl_pct:+.1f}%)",
             "#34d399" if total_pnl>=0 else "#f87171"),
            (c3,"🔴 Sell Alerts",   str(len(sells)),  "#f87171"),
            (c4,"🟢 Buy Signals",   str(len(buys)),   "#34d399"),
            (c5,"🚨 Stop-Loss",     str(len(alerts)), "#fbbf24"),
        ]:
            with col:
                st.markdown(
                    f'<div class="kcard"><div class="klbl">{lbl}</div>'
                    f'<div class="kval" style="color:{color}">{val}</div></div>',
                    unsafe_allow_html=True)

        st.markdown("---")
        st.markdown("### 🎯 Live Recommendations")
        if recs:
            st.dataframe(pd.DataFrame([{
                "Ticker":  r["ticker"],
                "Shares":  r["shares"],
                "Avg Cost":f"${r['avg_cost']:,.2f}",
                "Price":   f"${r['price']:,.2f}" if r["price"] is not None else "—",
                "P&L %":   f"{r['pnl_pct']:+.1f}%",
                "Equity":  f"${r['equity']:,.0f}",
                "Action":  r["action"],
                "Tax":     r["tax_note"],
            } for r in recs]), use_container_width=True, hide_index=True)

        st.markdown("---")
        st.markdown("### 📅 Action Calendar 2026")
        st.dataframe(pd.DataFrame(ACTION_CALENDAR), use_container_width=True, hide_index=True)

        with st.expander("🧾 Tax Playbook"):
            st.markdown("""
**Never sell ST** — 37% ordinary income vs 15–20% LT cap gains.  
**SELL order:** VTV, VEA, VWO, BND (LT now) → SPY (May 20) → VUG (Jul 15).  
**After each SELL:** Reinvest same day into target ETF (ETF swaps ≠ wash sales).  
**DRIP lots:** Each reinvestment = new tax lot.  
**Year-end:** Net realized gains vs losses before Dec 31.
""")

# ════════════════════ TAB 2 — HOLDINGS ═══════════════════════════════════════
with tabs[1]:
    pf = st.session_state.portfolio
    pr = st.session_state.prices
    if not pf:
        st.info("Load transactions and refresh prices.")
    else:
        rows = []
        for ticker, pos in pf.items():
            price  = _ep(ticker, pos, pr)
            equity = price * pos["shares"]
            pnl    = (price - pos["avg_cost"]) / pos["avg_cost"] * 100 if pos["avg_cost"] > 0 else 0
            rows.append({"Ticker":ticker,"Shares":round(pos["shares"],4),
                         "Avg Cost":pos["avg_cost"],"Price":price,
                         "Equity":round(equity,2),"P&L %":round(pnl,2),
                         "First Buy":str(pos["first_buy_date"]) if pos["first_buy_date"] else "—",
                         "LT?":"✅" if lt_ok(pos["first_buy_date"]) else "❌"})

        df = pd.DataFrame(rows).sort_values("Equity", ascending=False)

        def _color(row):
            return (["background-color:#3d0000;color:#fca5a5"]*len(row)
                    if row["P&L %"] < 0 else [""]*len(row))

        st.markdown("### 📋 Current Holdings")
        st.dataframe(
            df.style.apply(_color, axis=1).format(
                {"Avg Cost":"${:,.2f}","Price":"${:,.2f}","Equity":"${:,.0f}","P&L %":"{:+.2f}%"}),
            use_container_width=True, hide_index=True)

        cp, cb = st.columns(2)
        with cp:
            fig = px.pie(df, values="Equity", names="Ticker", title="Allocation",
                         color_discrete_sequence=px.colors.qualitative.Set3)
            fig.update_layout(paper_bgcolor="#0a0e1a", font_color="#e2e8f0")
            st.plotly_chart(fig, use_container_width=True)
        with cb:
            colors = ["#34d399" if v>=0 else "#f87171" for v in df["P&L %"]]
            fig2 = go.Figure(go.Bar(x=df["Ticker"], y=df["P&L %"], marker_color=colors,
                                    text=[f"{v:+.1f}%" for v in df["P&L %"]],
                                    textposition="outside"))
            fig2.update_layout(title="P&L by Position (%)", paper_bgcolor="#0a0e1a",
                                plot_bgcolor="#0a0e1a", font_color="#e2e8f0")
            st.plotly_chart(fig2, use_container_width=True)

# ════════════════════ TAB 3 — DEPLOY ═════════════════════════════════════════
with tabs[2]:
    pr = st.session_state.prices
    st.markdown("### 💰 Biweekly $900 Deploy Schedule")
    st.caption(f"Starting {DEPOSIT_START.strftime('%B %d, %Y')} — every other Friday")
    sched = deploy_schedule(pr)
    if not pr:
        sched["Est Shares"] = "—"
        st.info("Refresh prices to see estimated share counts.")
    st.dataframe(sched, use_container_width=True, hide_index=True)

    st.markdown("---")
    st.markdown("### 📝 Log a Deposit")
    with st.form("dep_form"):
        d1,d2,d3,d4 = st.columns([1,1,1,2])
        dep_date   = d1.date_input("Date", value=datetime.date.today())
        dep_ticker = d2.selectbox("Ticker", [p["ticker"] for p in BIWEEKLY_PLAN]+["OTHER"])
        dep_amount = d3.number_input("Amount ($)", value=900.0, step=50.0)
        dep_note   = d4.text_input("Note")
        if st.form_submit_button("Log Deposit"):
            entry = {"date":str(dep_date),"ticker":dep_ticker,
                     "amount":dep_amount,"note":dep_note,
                     "logged_at":datetime.datetime.now().isoformat()}
            st.session_state.deposit_log.append(entry)
            _save(DEPOSIT_LOG_PATH, st.session_state.deposit_log)
            st.success(f"Logged ${dep_amount:,.2f} → {dep_ticker} on {dep_date}")

    if st.session_state.deposit_log:
        st.markdown("#### Deposit History")
        st.dataframe(pd.DataFrame(st.session_state.deposit_log),
                     use_container_width=True, hide_index=True)

# ════════════════════ TAB 4 — IMPORT ═════════════════════════════════════════
with tabs[3]:
    st.markdown("### 📥 Import Robinhood Activity CSV")
    n = len(st.session_state.tx_store)
    if n:
        st.markdown(f'<span class="pbadge">✅ {n} transactions on disk — upload only for NEW activity</span>',
                    unsafe_allow_html=True)
        st.markdown("")

    up_csv = st.file_uploader("Robinhood activity CSV", type=["csv"], key="csv_up")
    if up_csv:
        new_store, stats = ingest_csv(up_csv, st.session_state.tx_store)
        st.markdown(f"**Preview:** {stats['added']} new rows · {stats['skipped']} skipped · "
                    f"{stats['total']} total")
        if stats["added"] > 0:
            if st.button("✅ Apply CSV Import", type="primary"):
                st.session_state.tx_store = new_store
                _save(TX_STORE_PATH, new_store)
                _refresh()           # recompute + prices + recs before rerun
                st.success(f"✅ {stats['added']} rows imported. Portfolio updated.")
                st.rerun()
        else:
            st.info("All rows already imported — nothing new to add.")

    st.markdown("---")
    st.markdown("### 🪙 Import Robinhood Crypto PDF Statement")
    st.caption("Auto-updates BTC/XRP share counts from your monthly statement.")

    up_pdf = st.file_uploader("Robinhood Crypto PDF", type=["pdf"], key="pdf_up")
    if up_pdf:
        with st.spinner("Parsing PDF…"):
            parsed = parse_crypto_pdf(up_pdf)
        if not parsed:
            st.error("Could not extract holdings. Enter manually in ⚙️ Settings instead.")
        else:
            preview = [{"Ticker":t,"Shares":v["shares"],
                        "Market Value":f"${v['market_value']:,.2f}"} for t,v in parsed.items()]
            st.dataframe(pd.DataFrame(preview), use_container_width=True, hide_index=True)
            st.info("Avg cost is preserved from Settings — only share counts update from PDF.")
            if st.button("✅ Apply Crypto Holdings", type="primary"):
                ovr = st.session_state.crypto_ovr
                for t, info in parsed.items():
                    ovr[t] = {"shares": info["shares"],
                              "avg_cost": ovr.get(t,{}).get("avg_cost", 0)}
                st.session_state.crypto_ovr = ovr
                _save(CRYPTO_OVR_PATH, ovr)
                _refresh()
                summary = ", ".join(f"{t}={v['shares']}" for t,v in parsed.items())
                st.success(f"✅ Crypto updated: {summary}")
                st.rerun()

    st.markdown("---")
    st.markdown("""
**Why you won't need to re-upload:** CSV rows are saved to `tx_store.json` on disk.
The app reloads them automatically on every restart. Only upload again when you have new activity.
Duplicates are always filtered by SHA-1 content hash.
""")

# ════════════════════ TAB 5 — DRIP ═══════════════════════════════════════════
with tabs[4]:
    pf = st.session_state.portfolio
    st.markdown("### 🌱 DRIP Summary")
    drip = [{"Ticker":t,"Events":pos["drip_count"],
             "Total Reinvested":f"${pos['drip_total']:,.2f}"}
            for t,pos in pf.items() if pos["drip_count"]>0]
    if drip:
        c1,c2 = st.columns(2)
        c1.metric("Total DRIP Events",  sum(pos["drip_count"]  for pos in pf.values()))
        c2.metric("Total Reinvested", f"${sum(pos['drip_total'] for pos in pf.values()):,.2f}")
        st.dataframe(pd.DataFrame(drip).sort_values("Total Reinvested", ascending=False),
                     use_container_width=True, hide_index=True)
    else:
        st.info("No DRIP activity. Import your Robinhood CSV.")

# ════════════════════ TAB 6 — HISTORY ════════════════════════════════════════
with tabs[5]:
    st.markdown("### 🕐 Recommendation History")
    if st.button("📸 Snapshot Current Recommendations"):
        if st.session_state.recs:
            snap = {"timestamp":datetime.datetime.now().isoformat(),"recs":st.session_state.recs}
            st.session_state.rec_history.append(snap)
            _save(REC_HISTORY_PATH, st.session_state.rec_history)
            st.success("Snapshot saved.")
        else:
            st.warning("Refresh prices first.")
    history = st.session_state.rec_history
    if not history:
        st.info("No snapshots yet.")
    else:
        for i, snap in enumerate(reversed(history)):
            ts = snap["timestamp"][:16].replace("T"," ")
            with st.expander(f"📸 Snapshot {len(history)-i} — {ts}"):
                st.dataframe(pd.DataFrame([{
                    "Ticker":r["ticker"],
                    "Price": f"${r['price']:,.2f}" if r["price"] is not None else "—",
                    "P&L %":f"{r['pnl_pct']:+.1f}%","Action":r["action"]
                } for r in snap["recs"]]), use_container_width=True, hide_index=True)

# ════════════════════ TAB 7 — SETTINGS ═══════════════════════════════════════
with tabs[6]:
    st.markdown("### ⚙️ Settings & Manual Overrides")
    st.markdown("#### 🪙 Crypto Overrides")
    ovr = st.session_state.crypto_ovr
    c1,c2 = st.columns(2)
    with c1:
        btc_sh = st.number_input("BTC Shares",   value=float(ovr.get("BTC",{}).get("shares",0.03432981)), format="%.8f")
        btc_co = st.number_input("BTC Avg Cost", value=float(ovr.get("BTC",{}).get("avg_cost",52800)), step=100.0)
    with c2:
        xrp_sh = st.number_input("XRP Shares",   value=float(ovr.get("XRP",{}).get("shares",1.066)), format="%.4f")
        xrp_co = st.number_input("XRP Avg Cost", value=float(ovr.get("XRP",{}).get("avg_cost",0.68)), format="%.4f")
    if st.button("💾 Save Crypto Overrides"):
        new_ovr = {"BTC":{"shares":btc_sh,"avg_cost":btc_co},
                   "XRP":{"shares":xrp_sh,"avg_cost":xrp_co}}
        st.session_state.crypto_ovr = new_ovr
        _save(CRYPTO_OVR_PATH, new_ovr)
        _refresh()
        st.success("Saved. Portfolio recalculated.")

    st.markdown("---")
    st.markdown("#### 📤 Export")
    if st.session_state.portfolio:
        pr = st.session_state.prices
        exp = [{"Ticker":t,"Shares":pos["shares"],"Avg Cost":pos["avg_cost"],
                "Price":_ep(t,pos,pr),"Equity":_ep(t,pos,pr)*pos["shares"],
                "First Buy":str(pos["first_buy_date"])}
               for t,pos in st.session_state.portfolio.items()]
        st.download_button("⬇️ Download Portfolio CSV",
                           pd.DataFrame(exp).to_csv(index=False).encode(),
                           "portfolio.csv","text/csv")

    st.markdown("---")
    st.markdown("#### 🗑️ Danger Zone")
    if st.button("Clear ALL Transactions (irreversible)", type="secondary"):
        st.session_state.tx_store  = {}
        st.session_state.portfolio = {}
        st.session_state.prices    = {}
        st.session_state.recs      = []
        _save(TX_STORE_PATH, {})
        st.warning("All transactions cleared.")
        st.rerun()

    st.markdown("---")
    st.markdown("#### 💾 Disk Status")
    st.markdown(f"- `tx_store.json` on disk: **{'✅' if TX_STORE_PATH.exists() else '❌'}**")
    st.markdown(f"- Transactions loaded: **{len(st.session_state.tx_store)}**")
    st.markdown(f"- Portfolio positions: **{len(st.session_state.portfolio)}**")
    st.markdown(f"- Prices fetched: **{sum(1 for v in st.session_state.prices.values() if v is not None)}** / {len(st.session_state.prices)}")
    st.markdown(f"- Rec snapshots: **{len(st.session_state.rec_history)}**")
    st.markdown(f"- Deposit log entries: **{len(st.session_state.deposit_log)}**")
