"""
╔══════════════════════════════════════════════════════════════╗
║   PORTFOLIO WAR ROOM  v4.0                                  ║
║   56/56 unit tests pass · yfinance + CoinGecko · No API key ║
║   Run:  streamlit run app.py                                ║
╚══════════════════════════════════════════════════════════════╝
"""
import streamlit as st
import pandas as pd
import json, time, os
from datetime import datetime, date
from zoneinfo import ZoneInfo
from collections import defaultdict

# Local modules
import sys; sys.path.insert(0, os.path.dirname(__file__))
from data.portfolio    import POSITIONS, CONFIRMED_CASH, DEPOSIT_SCHEDULE, \
                              DEPOSIT_ROTATION, ACTION_CALENDAR, DRIP_SUMMARY
from utils.csv_parser  import parse_robinhood_csv, merge_csvs, reconcile
from utils.rec_engine  import generate_rec, DRIP_YIELD, INCOME_FOREVER, DCA_ALWAYS
from utils.price_fetcher import fetch_all_prices, force_refresh_prices, get_equity_summary

# ─── PAGE CONFIG ─────────────────────────────────────────────────────────────
st.set_page_config(page_title="War Room", page_icon="⚡",
                   layout="wide", initial_sidebar_state="expanded")

# ─── DESIGN SYSTEM ────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=JetBrains+Mono:wght@300;400;500;600;700&display=swap');
:root{
  --bg:#07090e;--bg1:#0d1117;--bg2:#161b22;--bg3:#1c2333;--border:#21262d;
  --acc:#00e5a0;--accD:rgba(0,229,160,.09);--gold:#f5a623;--goldD:rgba(245,166,35,.09);
  --red:#f85149;--redD:rgba(248,81,73,.09);--blue:#58a6ff;--blueD:rgba(88,166,255,.09);
  --purple:#bc8cff;--purpleD:rgba(188,140,255,.09);--orange:#e3b341;
  --text:#e6edf3;--muted:#8b949e;--dim:#30363d;
  --fn:'JetBrains Mono',monospace;--fs:'Instrument Serif',serif;
}
html,body,[class*="css"]{background:var(--bg)!important;color:var(--text)!important;}
.stApp{background:var(--bg)!important;}
.block-container{padding:1.25rem 1.75rem 3rem!important;max-width:1440px!important;}
h1,h2,h3{font-family:var(--fs)!important;font-style:italic;letter-spacing:-.01em;}
p,span,div,label,button{font-family:var(--fn)!important;}
/* Sidebar */
[data-testid="stSidebar"]{background:var(--bg1)!important;border-right:1px solid var(--border)!important;}
[data-testid="stSidebar"]>div{padding:1.25rem .9rem!important;}
/* Metrics */
[data-testid="metric-container"]{background:var(--bg2);border:1px solid var(--border);
  border-radius:10px;padding:.9rem 1.1rem!important;}
[data-testid="metric-container"] label{color:var(--muted)!important;
  font-family:var(--fn)!important;font-size:.65rem!important;letter-spacing:.08em!important;text-transform:uppercase!important;}
[data-testid="metric-container"] [data-testid="stMetricValue"]{font-family:var(--fn)!important;
  font-size:1.35rem!important;font-weight:600!important;}
/* Buttons */
.stButton>button{background:var(--accD)!important;border:1px solid var(--acc)!important;
  color:var(--acc)!important;font-family:var(--fn)!important;font-weight:600!important;
  border-radius:7px!important;font-size:.82rem!important;letter-spacing:.03em;
  padding:.45rem 1rem!important;transition:all .12s!important;}
.stButton>button:hover{background:rgba(0,229,160,.18)!important;}
/* DataFrames */
[data-testid="stDataFrame"]{border-radius:10px!important;overflow:hidden!important;}
.dataframe th{background:var(--bg3)!important;color:var(--muted)!important;
  font-family:var(--fn)!important;font-size:.68rem!important;letter-spacing:.06em!important;
  text-transform:uppercase!important;border:none!important;padding:.55rem .8rem!important;}
.dataframe td{background:var(--bg2)!important;color:var(--text)!important;
  font-family:var(--fn)!important;font-size:.8rem!important;
  border-bottom:1px solid var(--border)!important;padding:.48rem .8rem!important;}
/* Inputs */
[data-testid="stSelectbox"]>div>div,[data-testid="stNumberInput"]>div>div>input,
[data-testid="stTextInput"]>div>div>input{background:var(--bg2)!important;
  border:1px solid var(--border)!important;color:var(--text)!important;
  border-radius:7px!important;font-family:var(--fn)!important;font-size:.82rem!important;}
/* File uploader */
[data-testid="stFileUploader"]>div{background:var(--bg2)!important;
  border:2px dashed var(--border)!important;border-radius:10px!important;}
[data-testid="stFileUploader"]:hover>div{border-color:var(--acc)!important;}
/* Expander */
[data-testid="stExpander"]{background:var(--bg2)!important;
  border:1px solid var(--border)!important;border-radius:9px!important;}
/* Alerts */
[data-testid="stAlert"]{border-radius:9px!important;font-family:var(--fn)!important;font-size:.82rem!important;}
/* Scrollbar */
::-webkit-scrollbar{width:5px;height:5px;}
::-webkit-scrollbar-track{background:var(--bg1);}
::-webkit-scrollbar-thumb{background:var(--dim);border-radius:3px;}
/* Hide chrome */
#MainMenu,footer,header,[data-testid="stDecoration"],[data-testid="stToolbar"]{display:none!important;}
/* Custom card classes */
.kcard{background:var(--bg2);border:1px solid var(--border);border-radius:10px;
  padding:.9rem 1.1rem;margin:.35rem 0;}
.kcard-acc{border-left:3px solid var(--acc);}
.kcard-red{border-left:3px solid var(--red);}
.kcard-gold{border-left:3px solid var(--gold);}
.kcard-blue{border-left:3px solid var(--blue);}
.kcard-purple{border-left:3px solid var(--purple);}
.badge{display:inline-block;padding:.15rem .55rem;border-radius:20px;
  font-size:.67rem;font-weight:600;font-family:var(--fn)!important;letter-spacing:.04em;}
.bg{background:var(--accD);color:var(--acc);border:1px solid rgba(0,229,160,.22);}
.br{background:var(--redD);color:var(--red);border:1px solid rgba(248,81,73,.22);}
.bo{background:rgba(227,179,65,.1);color:var(--orange);border:1px solid rgba(227,179,65,.22);}
.bb{background:var(--blueD);color:var(--blue);border:1px solid rgba(88,166,255,.22);}
.bp{background:var(--purpleD);color:var(--purple);border:1px solid rgba(188,140,255,.22);}
.bgr{background:rgba(139,148,158,.1);color:var(--muted);border:1px solid var(--dim);}
.section-head{font-family:var(--fs)!important;font-style:italic;font-size:1.05rem;
  font-weight:400;color:var(--text);border-bottom:1px solid var(--border);
  padding-bottom:.4rem;margin-bottom:.9rem;}
.mono{font-family:var(--fn)!important;}
.up{color:var(--acc)!important;} .dn{color:var(--red)!important;}
.muted{color:var(--muted)!important;}
</style>
""", unsafe_allow_html=True)

# ─── HELPERS ─────────────────────────────────────────────────────────────────
def usd(n, d=2):
    if n is None: return "—"
    s = "-" if n < 0 else ""
    return f"{s}${abs(n):,.{d}f}"

def pct(n, d=2):
    if n is None: return "—"
    return f"{n:+.{d}f}%"

def color_val(v):
    return "up" if (v or 0) >= 0 else "dn"

def badge(text, style):
    return f'<span class="badge {style}">{text}</span>'

REC_BADGE = {
    "green":  "bg", "red":  "br", "gold": "bo",
    "blue":   "bb", "purple":"bp","orange":"bo","gray":"bgr",
}

HISTORY_FILE = "price_history.json"

def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE) as f: return json.load(f)
        except: pass
    return []

def save_snapshot(prices, sources, ts, portfolio, cash):
    h  = load_history()
    tc = sum(p[3]*p[4] for p in portfolio)
    tv = sum(p[3]*prices.get(p[1], p[4]) for p in portfolio) + cash
    snap = {
        "timestamp": ts, "total_cost": round(tc,2), "total_value": round(tv,2),
        "total_gl": round(tv-tc,2), "total_gl_pct": round((tv-tc)/tc*100,2) if tc else 0,
        "cash": round(cash,2),
        "prices": {k:round(v,4) for k,v in prices.items()},
        "positions": [{
            "ticker":p[1], "price":prices.get(p[1]), "cost":p[4], "shares":p[3],
            "value":round(p[3]*prices.get(p[1],p[4]),2),
            "gl_pct":round((prices.get(p[1],p[4])-p[4])/p[4]*100,2),
            "rec": generate_rec(
                p[0],p[1],p[4],p[5] if len(p)>5 else None,
                p[6] if len(p)>6 else None, p[7] if len(p)>7 else None,
                p[8] if len(p)>8 else True, p[9] if len(p)>9 else "LT",
                prices.get(p[1]),
                p[11] if len(p)>11 else 0, p[12] if len(p)>12 else 0,
            ).action,
        } for p in portfolio]
    }
    h.insert(0, snap)
    with open(HISTORY_FILE,"w") as f: json.dump(h[:60], f, indent=2)
    return snap

# ─── BIWEEKLY PICKS ───────────────────────────────────────────────────────────
def next_deposit():
    today = date.today()
    for d in DEPOSIT_SCHEDULE:
        try:
            dt = datetime.strptime(f"{d} 2026", "%b %d %Y").date()
            if dt >= today: return d, (dt-today).days
        except: pass
    return DEPOSIT_SCHEDULE[-1], 0

def biweekly_picks(portfolio, prices, amount=900):
    wk   = int(time.time() // (14*86400))
    pick = DEPOSIT_ROTATION[wk % len(DEPOSIT_ROTATION)]
    pos  = next((p for p in portfolio if p[1]==pick), None)
    dip  = pos and prices.get(pick, 9999) < (pos[4] if pos else 9999)
    rows = [("NVDA",250),("VOO",200),("VYM",150),("QQQ",150),(pick,150)]
    return [{
        "ticker": t, "alloc": round(amount*a/900),
        "shares": round(amount*a/900/prices[t],4) if prices.get(t) else None,
        "price":  prices.get(t),
        "note": (f"🔥 DIP — below cost!" if dip and t==pick else
                 "AI supercycle — core conviction" if t=="NVDA" else
                 "S&P 500 — DCA every deposit forever" if t=="VOO" else
                 "Dividend engine — compound income forever" if t=="VYM" else
                 "Nasdaq-100 — never stop buying" if t=="QQQ" else
                 "Rotating pick — high conviction"),
    } for t,a in rows]

# ─── SESSION STATE ────────────────────────────────────────────────────────────
def _init():
    # Convert Position dataclasses → tuples for the app
    port_tuples = [
        (p.cat, p.ticker, p.name, p.shares, p.avg_cost,
         p.target, p.bear, p.bull, p.lt_ready, p.lt_date, p.cg_id,
         p.drip_shares, p.drip_cost, p.divs_received)
        for p in POSITIONS
    ]
    defaults = {
        "portfolio":    port_tuples,
        "prices":       {},
        "sources":      {},
        "last_ts":      None,
        "errors":       [],
        "cash":         CONFIRMED_CASH,
        "deposit_log":  [],
        "page":         "Overview",
        "sel_ticker":   None,
        "drip_log":     [],   # accumulated DRIP events from all CSV imports
    }
    for k,v in defaults.items():
        if k not in st.session_state: st.session_state[k] = v

_init()
P      = st.session_state.portfolio
PRICES = st.session_state.prices
CASH   = st.session_state.cash

# ─── SIDEBAR ─────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown(
        '<div style="font-family:\'Instrument Serif\',serif;font-style:italic;'
        'font-size:1.5rem;color:var(--acc);margin-bottom:.1rem">⚡ War Room</div>'
        '<div style="font-size:.65rem;color:var(--muted);font-family:var(--fn);'
        'letter-spacing:.1em;text-transform:uppercase;margin-bottom:1.4rem">'
        'Portfolio Intelligence v4</div>',
        unsafe_allow_html=True
    )

    pages = [
        ("📊","Overview"),("📈","Holdings"),("📥","Import CSV"),
        ("💰","Deploy $900"),("🌱","DRIP Analytics"),
        ("🕐","Snapshots"),("⚙","Settings"),
    ]
    for icon, name in pages:
        active = name == st.session_state.page
        col = "var(--acc)" if active else "var(--muted)"
        bg  = "var(--accD)" if active else "transparent"
        brd = "1px solid rgba(0,229,160,.25)" if active else "1px solid transparent"
        if st.button(f"{icon}  {name}", key=f"nav_{name}", use_container_width=True):
            st.session_state.page = name; st.rerun()

    st.markdown('<div style="height:1px;background:var(--border);margin:.8rem 0"></div>',
                unsafe_allow_html=True)

    if st.button("⚡  Refresh Prices", use_container_width=True, key="sb_refresh"):
        tickers = tuple(p[1] for p in P)
        with st.spinner("Fetching from yfinance + CoinGecko…"):
            p_, s_, ts_, errs_ = force_refresh_prices(tickers)
            st.session_state.prices  = p_
            st.session_state.sources = s_
            st.session_state.last_ts = ts_
            st.session_state.errors  = errs_
            PRICES = p_
            save_snapshot(p_, s_, ts_, P, st.session_state.cash)
            st.rerun()

    if st.session_state.last_ts:
        n_prices = len(PRICES)
        srcs = list(set(st.session_state.sources.values()))
        st.markdown(
            f'<div style="font-size:.65rem;color:var(--muted);font-family:var(--fn);'
            f'margin-top:.4rem;line-height:1.7">'
            f'● {st.session_state.last_ts}<br>'
            f'{n_prices}/{len(P)} prices · {", ".join(srcs)}</div>',
            unsafe_allow_html=True
        )
        if st.session_state.errors:
            for e in st.session_state.errors[:2]:
                st.warning(e, icon="⚠")
    else:
        st.markdown(
            '<div style="font-size:.67rem;color:var(--orange);margin-top:.4rem">'
            '⚠ No prices loaded — click Refresh</div>', unsafe_allow_html=True
        )

    st.markdown('<div style="height:1px;background:var(--border);margin:.8rem 0"></div>',
                unsafe_allow_html=True)

    # Cash card
    total_eq = sum(p[3]*PRICES.get(p[1],p[4]) for p in P)
    total_c  = sum(p[3]*p[4] for p in P)
    total_gl = total_eq + CASH - total_c
    gl_pct   = total_gl/total_c*100 if total_c else 0
    gl_col   = "var(--acc)" if total_gl >= 0 else "var(--red)"

    st.markdown(f"""
<div style="background:var(--bg2);border:1px solid var(--gold);border-radius:9px;
padding:.75rem .9rem;margin:.3rem 0">
  <div style="font-size:.6rem;color:var(--gold);font-family:var(--fn);
  letter-spacing:.1em;text-transform:uppercase;margin-bottom:.25rem">CASH AVAILABLE</div>
  <div style="font-size:1.2rem;font-weight:600;font-family:var(--fn);color:var(--text)">${CASH:,.2f}</div>
  <div style="font-size:.65rem;color:var(--muted);margin-top:.1rem">from sold positions</div>
</div>
<div style="background:var(--bg2);border:1px solid var(--border);border-radius:9px;
padding:.75rem .9rem;margin:.3rem 0">
  <div style="font-size:.6rem;color:var(--muted);font-family:var(--fn);
  letter-spacing:.1em;text-transform:uppercase;margin-bottom:.25rem">TOTAL PORTFOLIO</div>
  <div style="font-size:1.2rem;font-weight:600;font-family:var(--fn);
  color:{gl_col}">{usd(total_eq+CASH,0)}</div>
  <div style="font-size:.65rem;color:{gl_col};margin-top:.1rem">{pct(gl_pct)} vs cost</div>
</div>""", unsafe_allow_html=True)

# ══════════════════════════════════════════════════
page = st.session_state.page


# ══════════════════════════════════════════════════════════════════════════════
#  OVERVIEW
# ══════════════════════════════════════════════════════════════════════════════
if page == "Overview":
    nxt_d, nxt_days = next_deposit()

    c1, c2 = st.columns([4,1])
    with c1:
        st.markdown("# *Portfolio Overview*")
        st.markdown(
            f'<span style="color:var(--gold);font-size:.8rem">'
            f'Next deposit: **{nxt_d}, 2026** ({nxt_days}d) · $900</span>',
            unsafe_allow_html=True
        )
    with c2:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("⚡ Refresh", use_container_width=True, key="ov_refresh"):
            tickers = tuple(p[1] for p in P)
            with st.spinner(""):
                p_, s_, ts_, errs_ = force_refresh_prices(tickers)
                st.session_state.prices  = p_
                st.session_state.sources = s_
                st.session_state.last_ts = ts_
                st.session_state.errors  = errs_
                PRICES = p_
                save_snapshot(p_, s_, ts_, P, CASH)
                st.rerun()

    st.markdown("---")

    # ── Equity metrics ─────────────────────────────────────────────────────────
    equity_data = get_equity_summary(P, PRICES, CASH)
    eq_val   = equity_data["equity_value"]
    cry_val  = equity_data["crypto_value"]
    cash_val = equity_data["cash"]
    total_pv = equity_data["total_portfolio"]
    total_cv = equity_data["total_cost"]
    unreal   = equity_data["unrealized_gl"]
    gl_p     = equity_data["gl_pct"]

    m1,m2,m3,m4,m5,m6 = st.columns(6)
    m1.metric("Invested",      usd(total_cv,0))
    m2.metric("Equity",        usd(eq_val,0),   delta=usd(eq_val-total_cv,0))
    m3.metric("Crypto",        usd(cry_val,0))
    m4.metric("Cash",          usd(cash_val,2))
    m5.metric("Total Portfolio",usd(total_pv,0))
    m6.metric("Unrealized G/L", pct(gl_p),      delta=usd(unreal,0))

    # Reconciliation note
    rh_equity = 47246.21
    diff = total_pv - rh_equity
    if abs(diff) < 5000:
        note_col = "var(--acc)" if abs(diff) < 500 else "var(--orange)"
        st.markdown(
            f'<div style="font-size:.72rem;color:{note_col};font-family:var(--fn);'
            f'margin:.3rem 0 1rem">Robinhood reported equity: ${rh_equity:,.2f} · '
            f'War Room: {usd(total_pv,2)} · Delta: {usd(diff,2)} '
            f'(gap from live price timing)</div>',
            unsafe_allow_html=True
        )

    # ── Build recommendations ──────────────────────────────────────────────────
    sell_now, trim_sigs, buy_sigs, stop_loss = [], [], [], []
    for p in P:
        pr = PRICES.get(p[1])
        if not pr: continue
        rec = generate_rec(
            p[0],p[1],p[4],p[5],p[6],p[7],p[8],p[9],pr,
            p[11] if len(p)>11 else 0, p[12] if len(p)>12 else 0,
        )
        gl = (pr-p[4])/p[4]*100 if p[4] else 0
        row = (p[1], p[2], pr, gl, rec)
        if "SELL" in rec.action:    sell_now.append(row)
        elif "STOP" in rec.action:  stop_loss.append(row)
        elif "TRIM" in rec.action:  trim_sigs.append(row)
        elif any(x in rec.action for x in ("BUY","ACCUMULATE","DIP")):
            buy_sigs.append(row)

    c1, c2, c3 = st.columns(3)

    def alert_card(ticker, name, price, gl, rec, style):
        gl_col = "var(--acc)" if gl>=0 else "var(--red)"
        bdg    = badge(rec.action, REC_BADGE.get(rec.color,"bgr"))
        return f"""
<div class="kcard kcard-{style}">
  <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:.5rem">
    <div style="flex:1">
      <span style="font-weight:700;font-size:.95rem">{ticker}</span>
      <span style="color:var(--muted);font-size:.72rem;margin-left:.4rem">{name[:18]}</span>
      <div style="margin-top:.3rem">{bdg}</div>
      <div style="color:var(--muted);font-size:.7rem;margin-top:.25rem;line-height:1.4">
        {rec.detail[:55]}{'…' if len(rec.detail)>55 else ''}</div>
    </div>
    <div style="text-align:right;flex-shrink:0">
      <div style="font-family:var(--fn);font-size:.88rem;font-weight:600">{usd(price)}</div>
      <div style="font-size:.72rem;color:{gl_col}">{pct(gl)}</div>
    </div>
  </div>
</div>"""

    with c1:
        n = len(sell_now)+len(stop_loss)
        st.markdown(f'<div class="section-head">🔴 Sell Alerts <span style="font-size:.75rem;color:var(--muted);font-style:normal">({n})</span></div>',unsafe_allow_html=True)
        if sell_now or stop_loss:
            for t,n_,pr,gl,rec in sell_now:
                st.markdown(alert_card(t,n_,pr,gl,rec,"red"),unsafe_allow_html=True)
            for t,n_,pr,gl,rec in stop_loss:
                st.markdown(alert_card(t,n_,pr,gl,rec,"red"),unsafe_allow_html=True)
        else:
            st.markdown('<div style="color:var(--muted);font-size:.82rem;padding:.5rem 0">No sell alerts.</div>',unsafe_allow_html=True)

    with c2:
        st.markdown(f'<div class="section-head">✂️ Trim Signals <span style="font-size:.75rem;color:var(--muted);font-style:normal">({len(trim_sigs)})</span></div>',unsafe_allow_html=True)
        if trim_sigs:
            for t,n_,pr,gl,rec in trim_sigs[:5]:
                st.markdown(alert_card(t,n_,pr,gl,rec,"gold"),unsafe_allow_html=True)
        else:
            st.markdown('<div style="color:var(--muted);font-size:.82rem;padding:.5rem 0">No trim signals.</div>',unsafe_allow_html=True)

    with c3:
        st.markdown(f'<div class="section-head">🟢 Buy Signals <span style="font-size:.75rem;color:var(--muted);font-style:normal">({len(buy_sigs)})</span></div>',unsafe_allow_html=True)
        if buy_sigs:
            for t,n_,pr,gl,rec in buy_sigs[:5]:
                st.markdown(alert_card(t,n_,pr,gl,rec,"acc"),unsafe_allow_html=True)
        else:
            st.markdown('<div style="color:var(--muted);font-size:.82rem;padding:.5rem 0">No buy signals with current prices.</div>',unsafe_allow_html=True)

    st.markdown("---")

    # ── Calendar + Tax ─────────────────────────────────────────────────────────
    c1, c2 = st.columns(2)
    with c1:
        st.markdown('<div class="section-head">⚡ Action Calendar</div>',unsafe_allow_html=True)
        for item in ACTION_CALENDAR:
            days  = item["days"]
            col   = "var(--red)" if days<=5 else ("var(--gold)" if days<=30 else "var(--muted)")
            st.markdown(
                f'<div style="display:flex;gap:.75rem;align-items:flex-start;'
                f'padding:.35rem 0;border-bottom:1px solid var(--border)">'
                f'<span style="font-family:var(--fn);font-size:.75rem;color:{col};'
                f'min-width:52px;font-weight:600">{item["date"]}</span>'
                f'<span style="font-size:.78rem;color:var(--text);flex:1">{item["icon"]} {item["action"]}</span>'
                f'<span style="font-family:var(--fn);font-size:.65rem;color:var(--muted)">{days}d</span>'
                f'</div>',
                unsafe_allow_html=True
            )

    with c2:
        st.markdown('<div class="section-head">🧾 Tax Playbook</div>',unsafe_allow_html=True)
        rules = [
            ("Hold ≥ 366 days", "LT rate 15-20% vs ST 37% — never trigger early"),
            ("SELL list",       f"Remaining VTV/VEA/VWO/BND/VUG — sell per LT dates"),
            ("Cash: $1,042",    "Deploy per biweekly formula starting Apr 3"),
            ("DRIP lots",       f"${DRIP_SUMMARY['total_reinvested']:.0f} reinvested → each is a new lot"),
            ("SPY → VOO",       "May 20: sell SPY (LT), buy VOO same day"),
            ("VUG → QQQ",       "Jul 15: sell VUG (LT), buy QQQ same day"),
            ("Year-end harvest","Net gains vs losses before Dec 31"),
        ]
        for rule, detail in rules:
            st.markdown(
                f'<div style="display:flex;gap:.75rem;padding:.38rem 0;border-bottom:1px solid var(--border)">'
                f'<span style="font-family:var(--fn);font-size:.75rem;color:var(--acc);'
                f'font-weight:600;min-width:120px">{rule}</span>'
                f'<span style="font-size:.76rem;color:var(--muted)">{detail}</span>'
                f'</div>',
                unsafe_allow_html=True
            )


# ══════════════════════════════════════════════════════════════════════════════
#  HOLDINGS
# ══════════════════════════════════════════════════════════════════════════════
elif page == "Holdings":
    st.markdown("# *Holdings*")
    st.markdown("---")

    col_f, col_s = st.columns([2,2])
    with col_f:
        cat_filter = st.selectbox("Category", ["All","Crypto","Core","ETF","Other","IPO","SELL"])
    with col_s:
        sort_by = st.selectbox("Sort", ["Value ↓","G/L % ↓","G/L % ↑","Upside ↓","Ticker"])

    if not PRICES:
        st.info("⚡ Click **Refresh Prices** in the sidebar to load live recommendations.", icon="ℹ")

    rows = []
    for p in P:
        cat,t,name,sh,cost = p[0],p[1],p[2],p[3],p[4]
        target,bear,bull   = p[5],p[6],p[7]
        lt,ltd             = p[8],p[9]
        drip_sh            = p[11] if len(p)>11 else 0
        drip_c             = p[12] if len(p)>12 else 0
        divs               = p[13] if len(p)>13 else 0
        if cat_filter != "All" and cat != cat_filter: continue
        pr    = PRICES.get(t)
        val   = sh*pr if pr else sh*cost
        gl    = (pr-cost)/cost*100 if pr else None
        up    = (target-pr)/pr*100 if pr and target else None
        rec   = generate_rec(cat,t,cost,target,bear,bull,lt,ltd,pr,drip_sh,drip_c,divs)
        rows.append(dict(cat=cat,t=t,name=name,sh=sh,cost=cost,pr=pr,val=val,
                         gl=gl,upside=up,lt=lt,ltd=ltd,rec=rec,
                         bear=bear,bull=bull,target=target,
                         drip_sh=drip_sh,drip_c=drip_c,divs=divs))

    # Sort
    sk = {"Value ↓":lambda r:-(r["val"] or 0), "G/L % ↓":lambda r:-(r["gl"] or -999),
          "G/L % ↑":lambda r:(r["gl"] or 999), "Upside ↓":lambda r:-(r["upside"] or -999),
          "Ticker": lambda r:r["t"]}
    rows.sort(key=sk.get(sort_by, lambda r:r["t"]))

    # Table header
    st.markdown("""
<div style="display:grid;grid-template-columns:72px 1fr 88px 82px 88px 160px 100px;
align-items:center;padding:.45rem .6rem;border-bottom:1px solid var(--border);
font-family:var(--fn);font-size:.63rem;letter-spacing:.08em;text-transform:uppercase;
color:var(--muted)">
  <span>TICKER</span><span>NAME</span><span style="text-align:right">PRICE</span>
  <span style="text-align:right">G/L %</span><span style="text-align:right">VALUE</span>
  <span>RECOMMENDATION</span><span>LT STATUS</span>
</div>""", unsafe_allow_html=True)

    for r in rows:
        gl_col   = "var(--acc)" if (r["gl"] or 0) >= 0 else "var(--red)"
        rec_col  = r["rec"].color
        bdg_cls  = REC_BADGE.get(rec_col, "bgr")
        cat_col  = ("var(--red)" if r["cat"]=="SELL" else
                    "var(--purple)" if r["cat"]=="Crypto" else
                    "var(--gold)" if r["cat"]=="ETF" else "var(--text)")
        lt_bdg   = badge("✅ LT","bg") if r["lt"] else badge(r["ltd"][:12],"bo")

        # Compact row as a button
        with st.container():
            if st.button(
                f'{r["t"]}  ·  {r["name"][:24]}  ·  {usd(r["pr"]) if r["pr"] else "—"}  ·  {pct(r["gl"]) if r["gl"] else "—"}  ·  {usd(r["val"],0)}',
                key=f"h_{r['t']}", use_container_width=True
            ):
                st.session_state.sel_ticker = None if st.session_state.sel_ticker==r["t"] else r["t"]
                st.rerun()

        if st.session_state.sel_ticker == r["t"]:
            with st.container():
                d1,d2,d3,d4 = st.columns(4)
                d1.metric("Shares",       f"{r['sh']:,.4f}")
                d2.metric("Avg Cost",     usd(r["cost"]))
                d3.metric("Live Price",   usd(r["pr"]) if r["pr"] else "—")
                d4.metric("Position",     usd(r["val"],2))
                d5,d6,d7,d8 = st.columns(4)
                d5.metric("G/L %",  pct(r["gl"]), delta=usd((r["pr"]-r["cost"])*r["sh"]) if r["pr"] else None)
                d6.metric("Target", usd(r["target"]) if r["target"] else "None")
                d7.metric("Bear",   usd(r["bear"])   if r["bear"]   else "None")
                d8.metric("Bull",   usd(r["bull"])   if r["bull"]   else "None")

                # Price range bar
                if r["pr"] and r["bear"] and r["bull"]:
                    lo,hi = r["bear"]*.9, r["bull"]*1.08
                    sp = hi-lo
                    def rp(v): return max(0, min(100,(v-lo)/sp*100))
                    dot_col = "var(--acc)" if (r["pr"] or 0) >= r["cost"] else "var(--red)"
                    st.markdown(f"""
<div style="padding:.5rem 0">
  <div style="position:relative;height:5px;background:var(--bg3);border-radius:3px;margin:.4rem 0">
    <div style="position:absolute;left:{rp(r['bear']):.1f}%;
      width:{rp(r['target'] or r['bull'])-rp(r['bear']):.1f}%;
      height:100%;background:linear-gradient(90deg,rgba(248,81,73,.4),rgba(0,229,160,.4));
      border-radius:3px"></div>
    <div style="position:absolute;left:{rp(r['cost']):.1f}%;top:-4px;
      width:2px;height:13px;background:var(--gold);opacity:.8"></div>
    <div style="position:absolute;left:{rp(r['pr']):.1f}%;top:-5px;
      width:14px;height:14px;border-radius:50%;transform:translateX(-50%);
      background:{dot_col};border:2px solid var(--bg2);z-index:3"></div>
    {f'<div style="position:absolute;left:{rp(r["target"]):.1f}%;top:-4px;width:2px;height:13px;background:var(--acc);z-index:2"></div>' if r["target"] else ''}
  </div>
  <div style="display:flex;justify-content:space-between;font-family:var(--fn);font-size:.67rem;color:var(--muted)">
    <span style="color:var(--red)">Bear {usd(r['bear'],0)}</span>
    <span style="color:var(--gold)">Cost {usd(r['cost'],0)}</span>
    {f'<span style="color:var(--acc)">Target {usd(r["target"],0)}</span>' if r["target"] else ''}
    <span style="color:#4dbb7a">Bull {usd(r["bull"],0)}</span>
  </div>
</div>""", unsafe_allow_html=True)

                # Rec box
                rcc = {"green":"var(--acc)","red":"var(--red)","gold":"var(--gold)",
                       "blue":"var(--blue)","purple":"var(--purple)","orange":"var(--orange)","gray":"var(--muted)"}
                rc  = rcc.get(r["rec"].color,"var(--muted)")
                st.markdown(f"""
<div style="background:var(--bg2);border:1px solid {rc}33;border-left:3px solid {rc};
border-radius:8px;padding:.7rem 1rem;margin:.4rem 0">
  <div style="color:{rc};font-weight:700;font-size:.88rem;margin-bottom:.3rem">{r["rec"].action}</div>
  <div style="color:var(--muted);font-size:.77rem;line-height:1.55">{r["rec"].detail}</div>
  {f'<div style="color:var(--orange);font-size:.72rem;margin-top:.3rem">⚖ {r["rec"].tax_note}</div>' if r["rec"].tax_note else ''}
  {f'<div style="color:var(--acc);font-size:.72rem;margin-top:.25rem">🌱 {r["rec"].drip_note}</div>' if r["rec"].drip_note else ''}
</div>""", unsafe_allow_html=True)

                # DRIP metrics for this position
                if r["drip_sh"] > 0:
                    drip_cur_val = r["drip_sh"] * (r["pr"] or r["cost"])
                    drip_gain    = drip_cur_val - r["drip_c"]
                    yield_pct    = DRIP_YIELD.get(r["t"], 0)
                    est_ann      = (r["pr"] or r["cost"]) * r["sh"] * (yield_pct/100) if yield_pct else 0
                    dc1,dc2,dc3 = st.columns(3)
                    dc1.metric("DRIP Shares",     f"{r['drip_sh']:.5f}")
                    dc2.metric("DRIP Value",       usd(drip_cur_val,2), delta=usd(drip_gain,2))
                    dc3.metric("Est. Annual Income",usd(est_ann,2) if est_ann else "—")

        st.markdown('<div style="height:2px;background:var(--border);margin:.1rem 0"></div>',unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
#  IMPORT CSV
# ══════════════════════════════════════════════════════════════════════════════
elif page == "Import CSV":
    st.markdown("# *Import Robinhood CSV*")
    st.markdown("---")

    c1, c2 = st.columns([3,2])
    with c1:
        st.markdown("""
Export from Robinhood: **Account → Statements & History → Account Activity → All Time → Download CSV**

You can upload multiple CSVs — the app merges and deduplicates all transactions.
        """)

        uploaded_files = st.file_uploader(
            "Drop CSV(s) here", type=["csv"],
            accept_multiple_files=True,
            label_visibility="collapsed"
        )

        if uploaded_files:
            contents = [f.read().decode("utf-8", errors="ignore") for f in uploaded_files]

            if len(contents) == 1:
                parsed = parse_robinhood_csv(contents[0])
            else:
                parsed = merge_csvs(contents)

            # Summary metrics
            st.markdown(f"""
<div class="kcard kcard-acc" style="margin:.75rem 0">
  <div style="display:grid;grid-template-columns:repeat(5,1fr);gap:.75rem">
    {''.join(f'<div><div style="font-size:.62rem;color:var(--muted);font-family:var(--fn);text-transform:uppercase;letter-spacing:.08em;margin-bottom:.2rem">{l}</div><div style="font-size:1.3rem;font-weight:600;font-family:var(--fn);color:{c}">{v}</div></div>'
    for l,v,c in [
        ("Total Txns", parsed.total_tx, "var(--text)"),
        ("Buy Orders", parsed.buys, "var(--acc)"),
        ("SELL Orders", parsed.sells, "var(--red)"),
        ("DRIP Reinvests", parsed.drip_count, "var(--purple)"),
        ("Cash Dividends", parsed.cdiv_count, "var(--gold)"),
    ])}
  </div>
  <div style="margin-top:.6rem;font-size:.72rem;color:var(--muted)">
    Date range: {parsed.date_range} · Sell proceeds: {usd(parsed.sell_proceeds,2)} ·
    Deposits: {usd(parsed.cash_deposits,2)}
  </div>
</div>""", unsafe_allow_html=True)

            # DRIP summary
            if parsed.drip_log:
                total_drip_amt = sum(d["amt"] for d in parsed.drip_log)
                st.markdown(f"""
<div class="kcard kcard-purple" style="margin:.5rem 0">
  <div style="font-size:.7rem;color:var(--purple);letter-spacing:.08em;text-transform:uppercase;margin-bottom:.4rem">
    🌱 DRIP ANALYTICS FROM THIS IMPORT</div>
  <div style="font-size:.82rem;color:var(--text)">
    {len(parsed.drip_log)} DRIP events · ${total_drip_amt:.2f} reinvested ·
    {len(parsed.dividends)} tickers received dividends ·
    ${sum(parsed.dividends.values()):.2f} total dividends declared
  </div>
</div>""", unsafe_allow_html=True)

            # Reconcile preview
            updated, changes = reconcile(parsed, st.session_state.portfolio)

            if changes:
                st.markdown(f"#### {len(changes)} changes detected")
                st.dataframe(pd.DataFrame(changes), use_container_width=True, hide_index=True)
            else:
                st.info("No changes detected — portfolio already in sync.")

            # Sell proceeds / cash update
            new_cash = max(CONFIRMED_CASH, parsed.sell_proceeds * 0.5)
            st.markdown(f"""
<div class="kcard kcard-gold">
  <div style="font-size:.65rem;color:var(--gold);text-transform:uppercase;
  letter-spacing:.08em;margin-bottom:.35rem">💰 CASH POSITION AFTER IMPORT</div>
  <div style="display:flex;justify-content:space-between;align-items:center">
    <div>
      <div style="font-size:1.3rem;font-weight:600;font-family:var(--fn)">${CONFIRMED_CASH:,.2f}</div>
      <div style="font-size:.72rem;color:var(--muted)">Confirmed from sold positions</div>
    </div>
    <div style="text-align:right">
      <div style="font-size:.78rem;color:var(--muted)">Sell proceeds this CSV:</div>
      <div style="font-size:.9rem;font-weight:600;color:var(--gold)">{usd(parsed.sell_proceeds,2)}</div>
    </div>
  </div>
</div>""", unsafe_allow_html=True)

            if st.button("✅  Confirm Import — Update Portfolio", use_container_width=True):
                st.session_state.portfolio = updated
                st.session_state.cash      = CONFIRMED_CASH
                # Save DRIP log
                existing_drip = set(
                    (d["date"],d["ticker"],round(d["shares"],6))
                    for d in st.session_state.drip_log
                )
                for d in parsed.drip_log:
                    key = (d["date"],d["ticker"],round(d["shares"],6))
                    if key not in existing_drip:
                        st.session_state.drip_log.append(d)
                        existing_drip.add(key)
                st.success(f"✅ Portfolio updated! {len(changes)} changes · {len(parsed.drip_log)} DRIP events logged.")
                st.rerun()

    with c2:
        st.markdown("#### What gets updated")
        features = [
            ("✅ Buy orders",           "Shares + weighted avg cost"),
            ("✅ **Sell orders**",       "Reduces shares, removes closed positions"),
            ("✅ DRIP reinvestments",    "Tracked separately per ticker"),
            ("✅ Cash dividends",        "Total divs received per ticker"),
            ("✅ Stock splits (SPL)",    "Share counts adjusted"),
            ("✅ Auto-remove SELL pos",  "Sold-out positions removed from SELL list"),
            ("✅ New tickers",           "Auto-detected from history"),
            ("✅ Multiple CSV merge",    "Deduplicated by date/ticker/code"),
            ("— BTC / XRP",             "In Robinhood Crypto (separate)"),
        ]
        for feat, detail in features:
            c = "var(--acc)" if feat.startswith("✅") else "var(--muted)"
            st.markdown(
                f'<div style="display:flex;gap:.7rem;padding:.37rem 0;border-bottom:1px solid var(--border)">'
                f'<span style="color:{c};font-size:.78rem;min-width:175px;font-weight:600">{feat}</span>'
                f'<span style="font-size:.76rem;color:var(--muted)">{detail}</span></div>',
                unsafe_allow_html=True
            )

        st.markdown(f"""
<div class="kcard kcard-blue" style="margin-top:1rem">
  <div style="font-size:.67rem;color:var(--blue);letter-spacing:.08em;text-transform:uppercase;
  margin-bottom:.4rem">WHY PREVIOUS IMPORTS SHOWED "NO CHANGES"</div>
  <div style="font-size:.78rem;color:var(--muted);line-height:1.6">
    The old parser ignored all <strong style="color:var(--text)">Sell</strong> transactions.
    Your 10 sells (AMD, XOP, VTV, VEA, VWO, BND, CAVA, RIVN) were never applied,
    so share counts appeared unchanged. This v4 parser handles every transaction type.
  </div>
</div>""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
#  DEPLOY $900
# ══════════════════════════════════════════════════════════════════════════════
elif page == "Deploy $900":
    st.markdown("# *Deploy $900 Biweekly*")
    st.markdown("---")

    c1, c2 = st.columns([3,2])
    with c1:
        nxt_d, nxt_days = next_deposit()
        deposit_amt = st.number_input("Deposit amount ($)", value=900, step=50, min_value=100)

        st.markdown(f"""
<div class="kcard kcard-gold">
  <div style="font-size:.63rem;color:var(--gold);letter-spacing:.1em;text-transform:uppercase;margin-bottom:.35rem">📅 NEXT DEPOSIT FRIDAY</div>
  <div style="font-size:1.3rem;font-weight:600;font-family:var(--fn)">{nxt_d}, 2026</div>
  <div style="font-size:.75rem;color:var(--muted);margin-top:.2rem">{nxt_days} days · ${deposit_amt:,} to deploy · Cash available: ${CASH:,.2f}</div>
</div>""", unsafe_allow_html=True)

        st.markdown("#### This Cycle's Allocation")
        picks = biweekly_picks(P, PRICES, deposit_amt)
        total_alloc = sum(pk["alloc"] for pk in picks)

        for pk in picks:
            dip   = "🔥" if "DIP" in pk["note"] else ""
            sh_str = f"→ {pk['shares']:.4f} shares @ {usd(pk['price'])}" if pk["shares"] else "→ price not loaded"
            pct_alloc = pk["alloc"]/total_alloc*100

            st.markdown(f"""
<div class="kcard kcard-acc" style="margin:.35rem 0">
  <div style="display:flex;justify-content:space-between;align-items:center">
    <div style="flex:1">
      <div style="font-weight:700;font-size:1rem">{dip} {pk['ticker']}
        <span style="font-size:.72rem;font-weight:400;color:var(--muted);margin-left:.5rem">{pct_alloc:.0f}%</span>
      </div>
      <div style="font-size:.73rem;color:var(--muted);margin-top:.2rem">{pk['note']}</div>
      <div style="font-family:var(--fn);font-size:.7rem;color:var(--acc);margin-top:.15rem">{sh_str}</div>
    </div>
    <div style="font-family:var(--fn);font-size:1.2rem;font-weight:700">${pk['alloc']}</div>
  </div>
</div>""", unsafe_allow_html=True)

        st.markdown(f"""
<div style="display:flex;justify-content:space-between;padding:.6rem 0;
border-top:1px solid var(--border);font-family:var(--fn)">
  <span style="font-weight:600">TOTAL DEPLOYED</span>
  <span style="font-size:1.2rem;font-weight:700;color:var(--gold)">${total_alloc}</span>
</div>""", unsafe_allow_html=True)

        if st.button("✅  Log This Deposit", use_container_width=True):
            entry = {
                "date":   datetime.now().strftime("%b %d, %Y  %H:%M"),
                "amount": deposit_amt,
                "picks":  [(pk["ticker"],pk["alloc"],pk["note"]) for pk in picks],
                "prices": {pk["ticker"]:pk["price"] for pk in picks},
            }
            st.session_state.deposit_log.insert(0, entry)
            # Update holdings
            updated = []
            for p in st.session_state.portfolio:
                pk_m = next((pk for pk in picks if pk["ticker"]==p[1] and pk["price"]), None)
                if pk_m:
                    add_sh  = pk_m["alloc"] / pk_m["price"]
                    new_sh  = p[3] + add_sh
                    new_c   = (p[3]*p[4] + pk_m["alloc"]) / new_sh
                    updated.append((p[0],p[1],p[2],round(new_sh,6),round(new_c,4))+p[5:])
                else:
                    updated.append(p)
            st.session_state.portfolio = updated
            st.success(f"✅ ${deposit_amt:,} logged and holdings updated!")
            st.rerun()

    with c2:
        st.markdown("#### 📅 2026 Schedule")
        today = date.today()
        nxt_d_, _ = next_deposit()
        sched_html = ""
        for d in DEPOSIT_SCHEDULE:
            try:
                dt     = datetime.strptime(f"{d} 2026","%b %d %Y").date()
                days_r = (dt - today).days
                is_nxt = d == nxt_d_
                is_past= days_r < 0
                bg   = "var(--goldD)" if is_nxt else "transparent"
                brd  = "var(--gold)" if is_nxt else "var(--border)"
                col_ = "var(--gold)" if is_nxt else ("var(--dim)" if is_past else "var(--muted)")
                lbl  = "▶ NEXT" if is_nxt else ("✓" if is_past else f"+{days_r}d")
                sched_html += (
                    f'<div style="display:flex;justify-content:space-between;align-items:center;'
                    f'padding:.3rem .65rem;background:{bg};border:1px solid {brd};'
                    f'border-radius:6px;margin-bottom:.18rem">'
                    f'<span style="font-family:var(--fn);font-size:.76rem;color:{col_}">{d}, 2026</span>'
                    f'<span style="font-family:var(--fn);font-size:.63rem;color:{col_}">{lbl}</span>'
                    f'</div>'
                )
            except: pass
        st.markdown(sched_html, unsafe_allow_html=True)

    if st.session_state.deposit_log:
        st.markdown("---")
        st.markdown("#### 📚 Deposit History")
        log_rows = []
        for e in st.session_state.deposit_log:
            log_rows.append({
                "Date": e["date"],
                "Amount": f"${e['amount']:,}",
                "Allocation": ", ".join(f"{t}(${a})" for t,a,_ in e["picks"]),
            })
        st.dataframe(pd.DataFrame(log_rows), use_container_width=True, hide_index=True)


# ══════════════════════════════════════════════════════════════════════════════
#  DRIP ANALYTICS
# ══════════════════════════════════════════════════════════════════════════════
elif page == "DRIP Analytics":
    st.markdown("# *DRIP Analytics*")
    st.markdown("Dividend Reinvestment tracking — compound growth engine")
    st.markdown("---")

    # Portfolio-level DRIP summary
    total_drip_invested = sum(
        p[12] if len(p)>12 else 0 for p in P
    )
    total_drip_shares_now_val = sum(
        (p[11] if len(p)>11 else 0) * PRICES.get(p[1], p[4])
        for p in P
    )
    total_divs = sum(p[13] if len(p)>13 else 0 for p in P)
    drip_gain  = total_drip_shares_now_val - total_drip_invested

    m1,m2,m3,m4 = st.columns(4)
    m1.metric("Total DRIP Reinvested", usd(total_drip_invested or DRIP_SUMMARY["total_reinvested"],2))
    m2.metric("DRIP Shares Value Now", usd(total_drip_shares_now_val,2) if total_drip_shares_now_val else "Load prices")
    m3.metric("DRIP Gain vs Cost",    usd(drip_gain,2) if total_drip_shares_now_val else "—")
    m4.metric("Total Dividends Declared", usd(total_divs or DRIP_SUMMARY["total_divs"],2))

    st.markdown("---")

    # Per-position DRIP table
    st.markdown('<div class="section-head">DRIP Breakdown by Position</div>', unsafe_allow_html=True)
    drip_rows = []
    for p in P:
        drip_sh  = p[11] if len(p)>11 else 0
        drip_c   = p[12] if len(p)>12 else 0
        divs     = p[13] if len(p)>13 else 0
        if drip_sh < 0.00001 and divs < 0.01: continue
        pr       = PRICES.get(p[1], p[4])
        drip_val = drip_sh * pr if pr else drip_c
        drip_gl  = drip_val - drip_c if pr else 0
        yld_pct  = DRIP_YIELD.get(p[1], 0)
        est_ann  = pr * p[3] * (yld_pct/100) if pr and yld_pct else 0
        drip_rows.append({
            "Ticker":         p[1],
            "Name":           p[2][:22],
            "DRIP Shares":    f"{drip_sh:.5f}",
            "DRIP Cost":      usd(drip_c,2),
            "DRIP Value Now": usd(drip_val,2) if pr else "—",
            "DRIP G/L":       usd(drip_gl,2) if pr else "—",
            "Divs Received":  usd(divs,2),
            "Est. Ann. Income": usd(est_ann,2) if est_ann else "—",
            "Yield %":        f"{yld_pct:.1f}%" if yld_pct else "—",
        })
    drip_rows.sort(key=lambda r: -float(r["DRIP Cost"].replace("$","").replace(",","") or 0))
    if drip_rows:
        st.dataframe(pd.DataFrame(drip_rows), use_container_width=True, hide_index=True, height=500)
    else:
        st.info("Import a CSV with DRIP transactions to populate this table.")

    st.markdown("---")

    # DRIP event log (from imported CSVs)
    st.markdown('<div class="section-head">DRIP Event Log</div>', unsafe_allow_html=True)
    if st.session_state.drip_log:
        log_df = pd.DataFrame(st.session_state.drip_log)
        log_df = log_df.sort_values("date", ascending=False)
        log_df["amt"] = log_df["amt"].map(lambda x: usd(x,2))
        log_df["shares"] = log_df["shares"].map(lambda x: f"{x:.6f}")
        log_df.columns = [c.title() for c in log_df.columns]
        st.dataframe(log_df, use_container_width=True, hide_index=True, height=400)
    else:
        st.info("Import a CSV to see the DRIP event log. Each reinvestment is shown with date, ticker, shares, and price paid.")

    # Projection
    st.markdown("---")
    st.markdown('<div class="section-head">Compound Growth Projection</div>', unsafe_allow_html=True)
    st.markdown("""
DRIP turns dividends into more shares, which generate more dividends — compounding over time.
    """)
    proj_years = st.slider("Projection years", 1, 30, 10)
    proj_rows = []
    for p in P:
        yld = DRIP_YIELD.get(p[1], 0)
        if yld < 0.5: continue
        pr = PRICES.get(p[1], p[4])
        current_val = p[3] * pr
        projected   = current_val * (1 + yld/100) ** proj_years
        gain        = projected - current_val
        proj_rows.append({
            "Ticker": p[1],
            "Current Value": usd(current_val,0),
            f"Value in {proj_years}yr": usd(projected,0),
            "Projected Gain": usd(gain,0),
            "Yield %": f"{yld:.1f}%",
        })
    proj_rows.sort(key=lambda r: -float(r[f"Value in {proj_years}yr"].replace("$","").replace(",","") or 0))
    if proj_rows:
        st.dataframe(pd.DataFrame(proj_rows), use_container_width=True, hide_index=True)


# ══════════════════════════════════════════════════════════════════════════════
#  SNAPSHOTS
# ══════════════════════════════════════════════════════════════════════════════
elif page == "Snapshots":
    st.markdown("# *Price Snapshots*")
    st.markdown("Every price refresh is saved here with exact timestamps and all prices.")
    st.markdown("---")

    history = load_history()
    if not history:
        st.info("No snapshots yet — click ⚡ Refresh Prices to create the first one.")
    else:
        # Summary
        summary = []
        for s in history:
            summary.append({
                "Timestamp":   s["timestamp"],
                "Total $":     usd(s.get("total_value",0),0),
                "G/L $":       usd(s.get("total_gl",0),0),
                "G/L %":       pct(s.get("total_gl_pct",0)),
                "Cash":        usd(s.get("cash",0),2),
                "Prices":      len(s.get("prices",{})),
            })
        st.dataframe(pd.DataFrame(summary), use_container_width=True, hide_index=True)

        st.markdown("---")
        st.markdown("#### Inspect Snapshot")
        opts = [f"{s['timestamp']}  ·  {usd(s.get('total_value',0),0)}" for s in history]
        idx  = st.selectbox("Choose snapshot", range(len(opts)), format_func=lambda i: opts[i])
        snap = history[idx]

        c1,c2,c3,c4 = st.columns(4)
        c1.metric("Timestamp",    snap["timestamp"][:16])
        c2.metric("Total Value",  usd(snap.get("total_value",0),0))
        c3.metric("G/L",          usd(snap.get("total_gl",0),0), delta=pct(snap.get("total_gl_pct",0)))
        c4.metric("Cash",         usd(snap.get("cash",0),2))

        price_rows = [{"Ticker":pos["ticker"],"Price":usd(pos.get("price")),"Cost":usd(pos["cost"]),
            "G/L %":pct(pos.get("gl_pct")) if pos.get("gl_pct") is not None else "—",
            "Value":usd(pos["value"],2),"Rec":(pos.get("rec","—") or "—")[:45]}
            for pos in snap.get("positions",[])]
        if price_rows:
            st.dataframe(pd.DataFrame(price_rows),use_container_width=True,hide_index=True,height=600)


# ══════════════════════════════════════════════════════════════════════════════
#  SETTINGS
# ══════════════════════════════════════════════════════════════════════════════
elif page == "Settings":
    st.markdown("# *Settings*")
    st.markdown("---")

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("#### 📡 Data Sources")
        st.markdown("""
| Source | Covers | Key? | Limit |
|--------|--------|------|-------|
| **yfinance** | 39 stocks/ETFs/crypto | None | Very high |
| **CoinGecko** | BTC + XRP real-time | None | 30/min |

Server-side Python → no browser CORS issues, no 429 rate-limit errors.
Price cache: 5 minutes. Force refresh clears cache instantly.
        """)

        st.markdown("#### 🔧 Manual Override")
        ov_t = st.selectbox("Ticker", [p[1] for p in P])
        ov_p = st.number_input("Price ($)", value=float(PRICES.get(ov_t, P[0][4])), step=0.01)
        if st.button("Apply Override"):
            st.session_state.prices[ov_t] = ov_p
            st.success(f"✅ {ov_t} → ${ov_p:.2f}")
            st.rerun()

        st.markdown("#### 💵 Cash Balance")
        nc = st.number_input("Cash ($)", value=float(st.session_state.cash), step=1.0)
        if st.button("Update Cash"):
            st.session_state.cash = nc
            st.success(f"Cash: ${nc:,.2f}")
            st.rerun()

    with c2:
        st.markdown("#### 📊 Status")
        st.markdown(f"""
| Item | Value |
|------|-------|
| Positions | {len(P)} |
| Live prices loaded | {len(PRICES)}/{len(P)} |
| Cash balance | ${st.session_state.cash:,.2f} |
| Snapshots saved | {len(load_history())} |
| Deposits logged | {len(st.session_state.deposit_log)} |
| DRIP events logged | {len(st.session_state.drip_log)} |
| Last refresh | {st.session_state.last_ts or 'Never'} |
        """)

        st.markdown("#### ⬇ Export")
        if PRICES:
            export = []
            for p in P:
                pr = PRICES.get(p[1], p[4])
                rec = generate_rec(p[0],p[1],p[4],p[5],p[6],p[7],p[8],p[9],pr,
                                   p[11] if len(p)>11 else 0, p[12] if len(p)>12 else 0)
                export.append({"Ticker":p[1],"Name":p[2],"Cat":p[0],"Shares":p[3],
                    "AvgCost":p[4],"Price":pr,"Value":round(p[3]*pr,2),
                    "GL%":round((pr-p[4])/p[4]*100,2),"Rec":rec.action,
                    "DRIPShares":p[11] if len(p)>11 else 0})
            st.download_button("⬇ Portfolio CSV",
                pd.DataFrame(export).to_csv(index=False),"portfolio.csv","text/csv")

        if st.button("♻️ Reset to defaults", type="secondary"):
            for k in ["portfolio","cash","prices","sources","last_ts","errors",
                      "deposit_log","drip_log","sel_ticker"]:
                if k in st.session_state: del st.session_state[k]
            _init()
            st.success("Reset complete.")
            st.rerun()

        if st.button("🗑 Clear snapshot history", type="secondary"):
            if os.path.exists(HISTORY_FILE): os.remove(HISTORY_FILE)
            st.success("History cleared.")
