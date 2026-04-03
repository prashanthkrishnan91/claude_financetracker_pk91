"""
Portfolio War Room — Main App v10.0
Modular Streamlit UI. All data logic lives in data_engine.py.
"""

import json
import os
from datetime import date, datetime

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

import data_engine as de

# ═══════════════════════════════════════════════════════════════════════════════
# Page Config
# ═══════════════════════════════════════════════════════════════════════════════
st.set_page_config(
    page_title="Portfolio War Room",
    page_icon="⚔️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ═══════════════════════════════════════════════════════════════════════════════
# Custom CSS — 2026 Premium Dark UI
# ═══════════════════════════════════════════════════════════════════════════════
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=JetBrains+Mono:wght@400;600&family=DM+Sans:wght@300;400;500;600&display=swap');

/* ── Root palette ── */
:root {
    --bg:        #0a0c10;
    --surface:   #111318;
    --card:      #16191f;
    --border:    #22262e;
    --green:     #00e676;
    --green-dim: #1a3d2b;
    --red:       #ff5252;
    --red-dim:   #3d1a1a;
    --amber:     #ffb300;
    --amber-dim: #3d2e00;
    --blue:      #448aff;
    --text-1:    #e8eaed;
    --text-2:    #9aa0ac;
    --text-3:    #5f6368;
    --radius:    12px;
    --radius-sm: 6px;
}

/* ── Base reset ── */
html, body, [data-testid="stAppViewContainer"] {
    background: var(--bg) !important;
    color: var(--text-1) !important;
    font-family: 'DM Sans', sans-serif;
}

[data-testid="stSidebar"] {
    background: var(--surface) !important;
    border-right: 1px solid var(--border);
}

/* ── Header ── */
.war-room-header {
    display: flex; align-items: center; gap: 16px;
    padding: 28px 0 20px;
    border-bottom: 1px solid var(--border);
    margin-bottom: 24px;
}
.war-room-logo {
    font-size: 2.8rem; line-height: 1;
}
.war-room-title {
    font-family: 'Instrument Serif', serif;
    font-size: 2rem; color: var(--text-1);
    letter-spacing: -0.5px; line-height: 1.1;
}
.war-room-subtitle {
    font-size: 0.75rem; color: var(--text-3);
    font-family: 'JetBrains Mono', monospace;
    letter-spacing: 0.05em; text-transform: uppercase;
}

/* ── KPI cards ── */
.kpi-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 12px; margin-bottom: 24px;
}
.kpi-card {
    background: var(--card); border: 1px solid var(--border);
    border-radius: var(--radius); padding: 18px 20px;
    transition: border-color 0.2s, box-shadow 0.2s;
}
.kpi-card:hover { border-color: var(--green); box-shadow: 0 0 12px rgba(0,230,118,0.08); }
.kpi-label {
    font-size: 0.7rem; color: var(--text-3);
    text-transform: uppercase; letter-spacing: 0.08em;
    font-family: 'JetBrains Mono', monospace;
    margin-bottom: 8px;
}
.kpi-value {
    font-family: 'Instrument Serif', serif;
    font-size: 1.6rem; color: var(--text-1); line-height: 1.1;
}
.kpi-value.green { color: var(--green); }
.kpi-value.red   { color: var(--red);   }
.kpi-value.amber { color: var(--amber); }
.kpi-sub {
    font-size: 0.7rem; color: var(--text-2); margin-top: 4px;
    font-family: 'JetBrains Mono', monospace;
}

/* ── Tabs ── */
[data-testid="stTabs"] [data-baseweb="tab-list"] {
    background: transparent !important;
    border-bottom: 1px solid var(--border) !important;
    gap: 0 !important;
}
[data-testid="stTabs"] [data-baseweb="tab"] {
    background: transparent !important;
    color: var(--text-2) !important;
    font-family: 'DM Sans', sans-serif !important;
    font-size: 0.85rem !important;
    font-weight: 500 !important;
    padding: 10px 20px !important;
    border-bottom: 2px solid transparent !important;
    border-radius: 0 !important;
    transition: all 0.15s !important;
}
[data-testid="stTabs"] [aria-selected="true"] {
    color: var(--green) !important;
    border-bottom: 2px solid var(--green) !important;
}
[data-testid="stTabs"] [data-baseweb="tab"]:hover {
    color: var(--text-1) !important;
}

/* ── Rec cards ── */
.rec-card {
    background: var(--card); border: 1px solid var(--border);
    border-radius: var(--radius); padding: 16px 20px;
    margin-bottom: 10px; transition: border-color 0.15s;
}
.rec-card.sell  { border-left: 3px solid var(--red); }
.rec-card.buy   { border-left: 3px solid var(--green); }
.rec-card.trim  { border-left: 3px solid var(--amber); }
.rec-card.hold  { border-left: 3px solid var(--border); }
.rec-card.review { border-left: 3px solid #9c27b0; }
.rec-ticker {
    font-family: 'JetBrains Mono', monospace;
    font-size: 1.1rem; font-weight: 600; color: var(--text-1);
}
.rec-action {
    font-size: 0.9rem; font-weight: 600; margin: 4px 0;
}
.rec-reason {
    font-size: 0.8rem; color: var(--text-2); line-height: 1.5;
}
.rec-meta {
    display: flex; gap: 20px; margin-top: 10px; flex-wrap: wrap;
}
.rec-meta-item {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.7rem; color: var(--text-3);
}
.rec-meta-item span { color: var(--text-1); }
.pill {
    display: inline-block; padding: 2px 10px; border-radius: 20px;
    font-size: 0.68rem; font-weight: 600; letter-spacing: 0.05em;
    font-family: 'JetBrains Mono', monospace; text-transform: uppercase;
    margin-right: 6px;
}
.pill-sell   { background: var(--red-dim);   color: var(--red);   }
.pill-buy    { background: var(--green-dim); color: var(--green); }
.pill-trim   { background: var(--amber-dim); color: var(--amber); }
.pill-hold   { background: #1e2128;          color: var(--text-2);}
.pill-review { background: #2d1b3d;          color: #ce93d8;      }
.pill-lt     { background: #1a2638;          color: var(--blue);  }
.pill-st     { background: #3d2200;          color: #ff8f00;      }

/* ── Deposit card ── */
.dep-card {
    background: var(--card); border: 1px solid var(--border);
    border-radius: var(--radius); padding: 20px;
    margin-bottom: 12px;
}
.dep-header {
    display: flex; align-items: center; justify-content: space-between;
    margin-bottom: 12px;
}
.dep-num {
    font-family: 'Instrument Serif', serif;
    font-size: 1.4rem; color: var(--green);
}
.dep-date {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.8rem; color: var(--text-2);
}
.dep-table { width: 100%; border-collapse: collapse; }
.dep-table th {
    font-size: 0.65rem; color: var(--text-3);
    text-transform: uppercase; letter-spacing: 0.08em;
    text-align: left; padding: 4px 8px;
    font-family: 'JetBrains Mono', monospace;
}
.dep-table td {
    font-size: 0.8rem; padding: 6px 8px;
    border-top: 1px solid var(--border);
    font-family: 'JetBrains Mono', monospace;
}
.dep-table td:first-child { color: var(--text-1); font-weight: 600; }

/* ── Alerts ── */
.alert-box {
    background: var(--red-dim); border: 1px solid var(--red);
    border-radius: var(--radius-sm); padding: 12px 16px;
    margin-bottom: 12px; font-size: 0.85rem; color: var(--text-1);
}
.success-box {
    background: var(--green-dim); border: 1px solid var(--green);
    border-radius: var(--radius-sm); padding: 12px 16px;
    margin-bottom: 12px; font-size: 0.85rem; color: var(--text-1);
}
.info-box {
    background: #1a2638; border: 1px solid var(--blue);
    border-radius: var(--radius-sm); padding: 12px 16px;
    margin-bottom: 12px; font-size: 0.85rem; color: var(--text-1);
}

/* ── Section titles ── */
.section-title {
    font-family: 'Instrument Serif', serif;
    font-size: 1.2rem; color: var(--text-1);
    margin: 24px 0 12px;
    display: flex; align-items: center; gap: 8px;
}
.section-title::after {
    content: '';
    flex: 1; height: 1px; background: var(--border);
}

/* ── History entry ── */
.hist-row {
    display: flex; align-items: center; justify-content: space-between;
    padding: 10px 16px; background: var(--card); border: 1px solid var(--border);
    border-radius: var(--radius-sm); margin-bottom: 6px;
    font-family: 'JetBrains Mono', monospace; font-size: 0.78rem;
}

/* ── Streamlit overrides ── */
.stButton > button {
    background: var(--surface) !important;
    border: 1px solid var(--border) !important;
    color: var(--text-1) !important;
    border-radius: var(--radius-sm) !important;
    font-family: 'DM Sans', sans-serif !important;
    font-size: 0.82rem !important;
    font-weight: 500 !important;
    transition: all 0.15s !important;
}
.stButton > button:hover {
    border-color: var(--green) !important;
    color: var(--green) !important;
}
.stButton > button[kind="primary"] {
    background: var(--green-dim) !important;
    border-color: var(--green) !important;
    color: var(--green) !important;
}
[data-testid="stMetric"] {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 12px 16px;
}
.stDataFrame {
    background: var(--surface) !important;
}
div[data-testid="stFileUploader"] {
    background: var(--card) !important;
    border: 1px dashed var(--border) !important;
    border-radius: var(--radius) !important;
}
div[data-testid="stNumberInput"] input,
div[data-testid="stTextInput"] input {
    background: var(--card) !important;
    border-color: var(--border) !important;
    color: var(--text-1) !important;
}
label, .stSlider label, .stSelectbox label {
    color: var(--text-2) !important;
    font-family: 'DM Sans', sans-serif !important;
    font-size: 0.82rem !important;
}
</style>
""", unsafe_allow_html=True)

# ═══════════════════════════════════════════════════════════════════════════════
# Session State Init
# ═══════════════════════════════════════════════════════════════════════════════
def _init_session():
    defaults = {
        "refresh_count":  0,
        "cash":           de.ROBINHOOD_CASH_DEFAULT,
        "positions":      None,
        "prices":         {},
        "rows":           [],
        "recs":           [],
        "kpis":           {},
        "targets":        de.load_targets(),
        "last_refresh":   None,
        "deposit_num":    1,
        "show_test_results": False,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

_init_session()

# ═══════════════════════════════════════════════════════════════════════════════
# Bootstrap
# ═══════════════════════════════════════════════════════════════════════════════
de.bootstrap_if_needed()

# ═══════════════════════════════════════════════════════════════════════════════
# Core data loader (cached per refresh bust)
# ═══════════════════════════════════════════════════════════════════════════════
@st.cache_data(ttl=300)
def _load_prices(tickers: tuple, bust: int) -> dict:
    return de.fetch_prices(tickers, bust=bust)

def _refresh_all():
    positions = de.recompute_portfolio()
    st.session_state["positions"] = positions

    tickers = tuple(sorted(positions.keys()))
    bust = st.session_state["refresh_count"]
    prices = _load_prices(tickers, bust)
    st.session_state["prices"] = prices

    rows = de.enrich_portfolio(positions, prices)
    recs = de.generate_recommendations(rows)
    st.session_state["rows"] = rows
    st.session_state["recs"] = recs
    st.session_state["kpis"] = de.compute_kpis(rows, st.session_state["cash"])
    st.session_state["last_refresh"] = datetime.now().strftime("%H:%M:%S")

# Auto-load on first visit
if st.session_state["positions"] is None:
    with st.spinner("Loading portfolio…"):
        _refresh_all()

# ═══════════════════════════════════════════════════════════════════════════════
# Sidebar
# ═══════════════════════════════════════════════════════════════════════════════
with st.sidebar:
    st.markdown('<div style="font-family:\'Instrument Serif\',serif;font-size:1.3rem;color:#e8eaed;padding:8px 0 16px;">⚔️ War Room</div>', unsafe_allow_html=True)

    # Refresh
    if st.button("🔄 Refresh Prices", type="primary", use_container_width=True):
        st.session_state["refresh_count"] += 1
        with st.spinner("Fetching live prices…"):
            _refresh_all()
        st.success("✓ Prices updated")

    if st.session_state["last_refresh"]:
        st.caption(f"Last refresh: {st.session_state['last_refresh']}")

    st.divider()

    # Cash balance
    st.markdown("**💵 Cash Balance**")
    new_cash = st.number_input(
        "Robinhood cash ($)",
        value=float(st.session_state["cash"]),
        step=10.0, format="%.2f",
        label_visibility="collapsed",
    )
    if new_cash != st.session_state["cash"]:
        st.session_state["cash"] = new_cash
        if st.session_state["rows"]:
            st.session_state["kpis"] = de.compute_kpis(st.session_state["rows"], new_cash)

    st.divider()

    # Deposit number
    st.markdown("**📅 Next Deposit #**")
    st.session_state["deposit_num"] = st.number_input(
        "Deposit number", min_value=1, max_value=50,
        value=st.session_state["deposit_num"],
        label_visibility="collapsed",
    )

    st.divider()

    # Target allocation
    st.markdown("**🎯 Target Allocations (%)**")
    st.caption("Set % targets for smart rebalancing. Leave 0 = use default plan.")
    targets = st.session_state["targets"]
    key_tickers = ["VOO", "NVDA", "VYM", "QQQ", "AAPL", "META", "GOOGL", "GLD", "BTC", "XRP"]
    for t in key_tickers:
        val = float(targets.get(t, 0.0))
        new_val = st.number_input(t, min_value=0.0, max_value=100.0,
                                   value=val, step=0.5, format="%.1f",
                                   key=f"target_{t}")
        targets[t] = new_val
    if st.button("💾 Save Targets", use_container_width=True):
        st.session_state["targets"] = targets
        de.save_targets(targets)
        st.success("Targets saved")

    st.divider()

    # Crypto overrides
    st.markdown("**₿ Crypto Overrides**")
    with st.expander("Edit BTC / XRP"):
        btc_sh = st.number_input("BTC Shares", value=0.03432981, format="%.8f", step=0.001)
        btc_ac = st.number_input("BTC Avg Cost", value=52800.0, step=100.0)
        xrp_sh = st.number_input("XRP Shares", value=1.066, format="%.4f", step=0.1)
        xrp_ac = st.number_input("XRP Avg Cost", value=0.68, format="%.4f", step=0.01)
        if st.button("Update Crypto", use_container_width=True):
            de.update_crypto_override("BTC", btc_sh, btc_ac, True)
            de.update_crypto_override("XRP", xrp_sh, xrp_ac, False)
            st.session_state["refresh_count"] += 1
            _refresh_all()
            st.success("Crypto updated & refreshed")

# ═══════════════════════════════════════════════════════════════════════════════
# Header
# ═══════════════════════════════════════════════════════════════════════════════
st.markdown("""
<div class="war-room-header">
  <div class="war-room-logo">⚔️</div>
  <div>
    <div class="war-room-title">Portfolio War Room</div>
    <div class="war-room-subtitle">v10.0 · Live Intelligence · Tax-Optimized · Biweekly $900 Deploy Engine</div>
  </div>
</div>
""", unsafe_allow_html=True)

# ═══════════════════════════════════════════════════════════════════════════════
# KPI Cards
# ═══════════════════════════════════════════════════════════════════════════════
kpis = st.session_state.get("kpis", {})
if kpis:
    tv    = kpis.get("total_value", 0)
    pl    = kpis.get("total_pl", 0)
    pl_p  = kpis.get("total_pl_pct", 0)
    sv    = kpis.get("stock_value", 0)
    cv    = kpis.get("crypto_value", 0)
    cash  = kpis.get("cash", 0)
    drip  = kpis.get("drip_total", 0)
    pos   = kpis.get("positions", 0)
    wins  = kpis.get("winners", 0)
    loss  = kpis.get("losers", 0)

    pl_class  = "green" if pl >= 0 else "red"
    pl_sign   = "+" if pl >= 0 else ""

    st.markdown(f"""
    <div class="kpi-grid">
      <div class="kpi-card">
        <div class="kpi-label">Total Value</div>
        <div class="kpi-value">${tv:,.0f}</div>
        <div class="kpi-sub">Stocks + Crypto + Cash</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Total P&L</div>
        <div class="kpi-value {pl_class}">{pl_sign}${pl:,.0f}</div>
        <div class="kpi-sub">{pl_sign}{pl_p:.1f}% all time</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Stocks</div>
        <div class="kpi-value">${sv:,.0f}</div>
        <div class="kpi-sub">{wins}W / {loss}L of {pos} positions</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Crypto</div>
        <div class="kpi-value">${cv:,.0f}</div>
        <div class="kpi-sub">BTC + XRP</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Cash</div>
        <div class="kpi-value amber">${cash:,.0f}</div>
        <div class="kpi-sub">Robinhood balance</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">DRIP Reinvested</div>
        <div class="kpi-value green">${drip:,.0f}</div>
        <div class="kpi-sub">Dividends auto-compounded</div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    # Urgent alerts banner
    sell_ct = kpis.get("sell_count", 0)
    if sell_ct > 0:
        st.markdown(f'<div class="alert-box">🔴 <b>{sell_ct} urgent SELL action{"s" if sell_ct>1 else ""}</b> — see Actions tab. Execute before next deposit cycle to maximize tax savings.</div>', unsafe_allow_html=True)

# ═══════════════════════════════════════════════════════════════════════════════
# Tabs
# ═══════════════════════════════════════════════════════════════════════════════
tabs = st.tabs([
    "⚡ Actions",
    "📊 Portfolio",
    "🎯 Rebalancing",
    "💰 Invest $900",
    "📅 Schedule",
    "📈 Charts",
    "🕘 History",
    "📥 Import",
    "🧪 Tests",
])

# ─────────────────────────────────────────────────────────────────────────────
# TAB 0 — ACTIONS
# ─────────────────────────────────────────────────────────────────────────────
with tabs[0]:
    recs = st.session_state.get("recs", [])
    if not recs:
        st.info("Click 🔄 Refresh in the sidebar to load recommendations.")
    else:
        # Group by priority
        sells   = [r for r in recs if r.get("badge") == "SELL"]
        reviews = [r for r in recs if r.get("badge") == "REVIEW"]
        buys    = [r for r in recs if r.get("badge") == "BUY"]
        trims   = [r for r in recs if r.get("badge") == "TRIM"]
        holds   = [r for r in recs if r.get("badge") == "HOLD"]

        badge_class = {"SELL": "sell", "BUY": "buy", "TRIM": "trim", "HOLD": "hold", "REVIEW": "review"}
        pill_class  = {"SELL": "pill-sell", "BUY": "pill-buy", "TRIM": "pill-trim", "HOLD": "pill-hold", "REVIEW": "pill-review"}

        def render_recs(rec_list, title: str):
            if not rec_list:
                return
            st.markdown(f'<div class="section-title">{title}</div>', unsafe_allow_html=True)
            for r in rec_list:
                badge  = r.get("badge", "HOLD")
                lt_pill = f'<span class="pill pill-lt">LT ✓</span>' if r.get("lt") else '<span class="pill pill-st">ST ⚠</span>'
                pl_col = "#00e676" if r["pl_pct"] >= 0 else "#ff5252"
                proceed = f' · Est. proceeds: <span style="color:#ffb300">${r["proceed_est"]:,.0f}</span>' if r.get("proceed_est", 0) > 0 else ""
                st.markdown(f"""
                <div class="rec-card {badge_class.get(badge,'hold')}">
                  <div style="display:flex;align-items:center;gap:10px;margin-bottom:6px">
                    <span class="rec-ticker">{r['ticker']}</span>
                    <span class="pill {pill_class.get(badge,'pill-hold')}">{badge}</span>
                    {lt_pill}
                  </div>
                  <div class="rec-action" style="color:{'#ff5252' if badge=='SELL' else '#00e676' if badge=='BUY' else '#ffb300' if badge=='TRIM' else '#9aa0ac'}">{r['action']}</div>
                  <div class="rec-reason">{r['reason']}</div>
                  <div class="rec-meta">
                    <div class="rec-meta-item">Price <span>${r['live_price']:,.2f}</span></div>
                    <div class="rec-meta-item">P&L <span style="color:{pl_col}">{r['pl_pct']:+.1f}%</span></div>
                    <div class="rec-meta-item">Equity <span>${r['equity']:,.0f}</span></div>
                    <div class="rec-meta-item">Target <span>${r['target']:,.0f}</span>{proceed}</div>
                  </div>
                  <div style="font-size:0.7rem;color:#5f6368;margin-top:8px">{r['tax_note']}</div>
                </div>
                """, unsafe_allow_html=True)

        render_recs(sells,   "🔴 Urgent — Sell Now")
        render_recs(reviews, "🚨 Review Required")
        render_recs(buys,    "🟢 Buy / Accumulate")
        render_recs(trims,   "✂️ Trim — Lock Gains")
        render_recs(holds,   "🟡 Hold")

        if st.button("📸 Save Snapshot to History"):
            de.save_snapshot(st.session_state["kpis"], recs)
            st.success("Snapshot saved to History tab")

# ─────────────────────────────────────────────────────────────────────────────
# TAB 1 — PORTFOLIO
# ─────────────────────────────────────────────────────────────────────────────
with tabs[1]:
    rows = st.session_state.get("rows", [])
    if not rows:
        st.info("Click 🔄 Refresh in the sidebar.")
    else:
        df = pd.DataFrame(rows)
        df = df[["ticker", "shares", "avg_cost", "live_price", "equity", "pl", "pl_pct", "lt", "drip_amount"]]
        df.columns = ["Ticker", "Shares", "Avg Cost", "Live Price", "Equity", "P&L $", "P&L %", "LT?", "DRIP $"]

        st.dataframe(
            df,
            column_config={
                "Ticker":     st.column_config.TextColumn("Ticker", width="small"),
                "Shares":     st.column_config.NumberColumn("Shares", format="%.4f"),
                "Avg Cost":   st.column_config.NumberColumn("Avg Cost", format="$%.2f"),
                "Live Price": st.column_config.NumberColumn("Live Price", format="$%.2f"),
                "Equity":     st.column_config.NumberColumn("Equity", format="$%.2f"),
                "P&L $":      st.column_config.NumberColumn("P&L $", format="$%.2f"),
                "P&L %":      st.column_config.NumberColumn("P&L %", format="%.1f%%"),
                "LT?":        st.column_config.CheckboxColumn("LT?"),
                "DRIP $":     st.column_config.NumberColumn("DRIP Reinvested", format="$%.2f"),
            },
            use_container_width=True,
            height=560,
        )

        total_equity = sum(r["equity"] for r in rows)
        total_cost   = sum(r["cost_basis"] for r in rows)
        total_pl     = sum(r["pl"] for r in rows)
        drip_total   = sum(r["drip_amount"] for r in rows)
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Equity",  f"${total_equity:,.2f}")
        c2.metric("Total Cost",    f"${total_cost:,.2f}")
        c3.metric("Total P&L",     f"${total_pl:+,.2f}", f"{total_pl/total_cost*100:+.1f}%" if total_cost>0 else "")
        c4.metric("DRIP Total",    f"${drip_total:,.2f}")

# ─────────────────────────────────────────────────────────────────────────────
# TAB 2 — REBALANCING
# ─────────────────────────────────────────────────────────────────────────────
with tabs[2]:
    rows = st.session_state.get("rows", [])
    targets = st.session_state.get("targets", {})
    prices  = st.session_state.get("prices", {})

    if not rows:
        st.info("Refresh data first.")
    else:
        total_value = st.session_state["kpis"].get("total_value", 0)
        drift_rows = de.compute_rebalancing(rows, total_value, targets)

        st.markdown('<div class="section-title">📊 Allocation vs Target</div>', unsafe_allow_html=True)
        st.markdown('<div class="info-box">Set target % in the sidebar. Green bars = underweight (buy more). Red bars = overweight (trim). The $900 deposit auto-fills the biggest gaps.</div>', unsafe_allow_html=True)

        drift_df = pd.DataFrame([{
            "Ticker":       r["ticker"],
            "Current %":    round(r["current_pct"], 2),
            "Target %":     round(r["target_pct"], 2),
            "Drift":        round(r["drift"], 2),
            "Equity":       round(r["equity"], 2),
        } for r in drift_rows if r["target_pct"] > 0 or r["current_pct"] > 0.5])

        if not drift_df.empty:
            st.dataframe(
                drift_df,
                column_config={
                    "Current %": st.column_config.NumberColumn(format="%.2f%%"),
                    "Target %":  st.column_config.NumberColumn(format="%.2f%%"),
                    "Drift":     st.column_config.NumberColumn(format="%.2f%%"),
                    "Equity":    st.column_config.NumberColumn(format="$%.2f"),
                },
                use_container_width=True,
            )

            # Drift bar chart
            fig = go.Figure()
            colors = ["#ff5252" if d > 0 else "#00e676" for d in drift_df["Drift"]]
            fig.add_bar(
                x=drift_df["Ticker"], y=drift_df["Drift"],
                marker_color=colors,
                text=[f"{d:+.1f}%" for d in drift_df["Drift"]],
                textposition="outside",
            )
            fig.update_layout(
                title="Drift from Target Allocation",
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font_color="#9aa0ac", font_family="DM Sans",
                xaxis=dict(tickfont=dict(color="#e8eaed", family="JetBrains Mono")),
                yaxis=dict(title="Drift (%)", tickformat=".1f", gridcolor="#22262e"),
                height=320, margin=dict(l=40, r=20, t=40, b=40),
            )
            st.plotly_chart(fig, use_container_width=True)

        # Next deposit allocation preview
        st.markdown('<div class="section-title">💰 Next Deposit Allocation Preview</div>', unsafe_allow_html=True)
        dep_num = st.session_state["deposit_num"]
        allocs = de.compute_deposit_allocation(900, rows, total_value, targets, dep_num, prices)
        alloc_df = pd.DataFrame(allocs)
        if not alloc_df.empty:
            st.dataframe(
                alloc_df[["ticker", "amount", "est_shares", "live_price", "reason"]],
                column_config={
                    "ticker":     st.column_config.TextColumn("Ticker"),
                    "amount":     st.column_config.NumberColumn("Amount", format="$%.2f"),
                    "est_shares": st.column_config.NumberColumn("Est. Shares", format="%.4f"),
                    "live_price": st.column_config.NumberColumn("Live Price", format="$%.2f"),
                    "reason":     st.column_config.TextColumn("Reason"),
                },
                use_container_width=True,
            )

# ─────────────────────────────────────────────────────────────────────────────
# TAB 3 — INVEST $900
# ─────────────────────────────────────────────────────────────────────────────
with tabs[3]:
    prices  = st.session_state.get("prices", {})
    rows    = st.session_state.get("rows", [])
    targets = st.session_state.get("targets", {})
    total_v = st.session_state["kpis"].get("total_value", 1) if kpis else 1

    dep_num = st.session_state["deposit_num"]
    schedule = de.get_deposit_schedule(1)
    next_date = schedule[0]["date"] if schedule else date.today()
    rotating  = de.DEPOSIT_ROTATING[(dep_num - 1) % len(de.DEPOSIT_ROTATING)]

    st.markdown(f"""
    <div class="dep-card">
      <div class="dep-header">
        <div>
          <div class="dep-num">Deposit #{dep_num}</div>
          <div class="dep-date">📅 {next_date.strftime('%A, %B %d, %Y')} · $900.00</div>
        </div>
        <div style="text-align:right">
          <div style="font-size:0.7rem;color:#5f6368;font-family:JetBrains Mono">ROTATING PICK</div>
          <div style="font-family:JetBrains Mono;font-size:1.1rem;color:#00e676">{rotating}</div>
        </div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    if rows:
        allocs = de.compute_deposit_allocation(900, rows, total_v, targets, dep_num, prices)

        st.markdown(f"""
        <table class="dep-table" style="background:#16191f;border-radius:12px;overflow:hidden;width:100%;border-collapse:collapse;font-family:JetBrains Mono;font-size:0.82rem">
          <tr>
            <th style="padding:12px 16px;color:#5f6368;text-transform:uppercase;font-size:0.65rem;letter-spacing:0.08em">Ticker</th>
            <th style="padding:12px 16px;color:#5f6368;text-transform:uppercase;font-size:0.65rem;letter-spacing:0.08em">Amount</th>
            <th style="padding:12px 16px;color:#5f6368;text-transform:uppercase;font-size:0.65rem;letter-spacing:0.08em">Est. Shares</th>
            <th style="padding:12px 16px;color:#5f6368;text-transform:uppercase;font-size:0.65rem;letter-spacing:0.08em">Live Price</th>
            <th style="padding:12px 16px;color:#5f6368;text-transform:uppercase;font-size:0.65rem;letter-spacing:0.08em">Why</th>
          </tr>
          {"".join([f'''
          <tr style="border-top:1px solid #22262e">
            <td style="padding:12px 16px;font-weight:600;color:#e8eaed">{a["ticker"]}</td>
            <td style="padding:12px 16px;color:#00e676">${a["amount"]:.2f}</td>
            <td style="padding:12px 16px;color:#9aa0ac">{a["est_shares"]:.4f}</td>
            <td style="padding:12px 16px;color:#448aff">${a["live_price"]:,.2f}</td>
            <td style="padding:12px 16px;color:#5f6368">{a["reason"]}</td>
          </tr>''' for a in allocs])}
        </table>
        """, unsafe_allow_html=True)

        st.markdown('<br>', unsafe_allow_html=True)
        col1, col2 = st.columns([2, 1])
        with col1:
            notes = st.text_input("Optional notes for this deposit", placeholder="e.g. Apr 3 deposit — executed all buys")
        with col2:
            st.markdown('<br>', unsafe_allow_html=True)
            if st.button("✅ Log This Deposit", type="primary"):
                de.log_deposit(dep_num, allocs, 900.0, notes)
                st.session_state["deposit_num"] += 1
                st.success(f"Deposit #{dep_num} logged. Next deposit #{dep_num+1} queued.")
                st.rerun()
    else:
        st.info("Refresh prices to see deposit plan.")

    st.markdown('<div class="section-title">🗓️ Full Rotating Schedule</div>', unsafe_allow_html=True)
    for i, pick in enumerate(de.DEPOSIT_ROTATING):
        pct = int(de.DEPOSIT_ROTATING_PCT * 900)
        st.markdown(f'<div style="display:flex;align-items:center;gap:12px;padding:6px 0;border-bottom:1px solid #22262e;font-family:JetBrains Mono;font-size:0.8rem"><span style="color:#5f6368">#{i+1}, {i+9}, {i+17}…</span><span style="color:#e8eaed;font-weight:600">{pick}</span><span style="color:#00e676">${pct}</span></div>', unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# TAB 4 — SCHEDULE
# ─────────────────────────────────────────────────────────────────────────────
with tabs[4]:
    schedule = de.get_deposit_schedule(16)
    today = date.today()

    st.markdown('<div class="section-title">📅 Biweekly Deposit Calendar — 2026</div>', unsafe_allow_html=True)
    st.markdown('<div class="info-box">Every other Friday. $900 per deposit. Amounts below use default allocation; update targets in sidebar for smart rebalancing.</div>', unsafe_allow_html=True)

    for s in schedule:
        d_str = s["date"].strftime("%b %d, %Y")
        is_next = s["num"] == st.session_state["deposit_num"]
        is_past = s["date"] < today
        border_color = "#00e676" if is_next else ("#22262e" if not is_past else "#2a2d35")
        opacity = "0.5" if is_past else "1.0"

        # Default allocation amounts
        alloc_str = " · ".join([
            f"NVDA $252", f"VOO $198", f"VYM $153", f"QQQ $153", f"{s['rotating']} $144"
        ])
        badge = '<span style="background:#1a3d2b;color:#00e676;padding:2px 8px;border-radius:4px;font-size:0.65rem;font-weight:600">NEXT</span>' if is_next else ""
        past_badge = '<span style="background:#1e2128;color:#5f6368;padding:2px 8px;border-radius:4px;font-size:0.65rem">PAST</span>' if is_past else ""

        st.markdown(f"""
        <div style="background:#16191f;border:1px solid {border_color};border-radius:10px;
                    padding:14px 18px;margin-bottom:8px;opacity:{opacity}">
          <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px">
            <div style="font-family:JetBrains Mono;font-size:0.9rem;font-weight:600;color:#e8eaed">
              #{s['num']} — {d_str} {badge} {past_badge}
            </div>
            <div style="font-family:JetBrains Mono;font-size:0.9rem;color:#00e676">$900</div>
          </div>
          <div style="font-family:JetBrains Mono;font-size:0.72rem;color:#5f6368">{alloc_str}</div>
        </div>
        """, unsafe_allow_html=True)

    st.markdown('<div class="section-title">🗓️ Key Action Calendar 2026</div>', unsafe_allow_html=True)
    action_calendar = [
        ("Apr 3",  "🔴 SELL", "VTV, VEA, VWO, BND — LT eligible. Reinvest into VOO+VYM same day."),
        ("Apr 3",  "💰 BUY",  "Deposit #1 — NVDA/VOO/VYM/QQQ + META"),
        ("Apr 4",  "✂️ TRIM", "GLD now LT eligible — trim 25% near $450 target"),
        ("Apr 17", "💰 BUY",  "Deposit #2 — NVDA/VOO/VYM/QQQ + GOOGL"),
        ("May 20", "🔴 SELL", "SPY turns LT → sell all, buy VOO same day (not a wash sale)"),
        ("Jul 15", "🔴 SELL", "VUG turns LT → sell all, buy QQQ same day"),
        ("Aug 14", "🔵 EVAL", "BLSH hits 1yr — trim 25% if up >20%"),
        ("Sep 11", "🔵 EVAL", "KLAR hits 1yr — trim 25% if up >20%"),
        ("Sep 18", "🔵 EVAL", "STUB hits 1yr — evaluate position"),
        ("Nov 6",  "✂️ TRIM", "TSM big lot turns LT — trim 20%"),
        ("Dec 15", "✂️ TRIM", "GOOGL big lot turns LT — trim 20%"),
        ("Dec 20", "🧾 TAX",  "Year-end: harvest losses to offset gains before Dec 31"),
    ]
    for act_date, act_type, act_desc in action_calendar:
        badge_color = {"🔴 SELL": "#ff5252", "💰 BUY": "#00e676", "✂️ TRIM": "#ffb300", "🔵 EVAL": "#448aff", "🧾 TAX": "#9c27b0"}.get(act_type, "#5f6368")
        st.markdown(f"""
        <div style="display:flex;align-items:flex-start;gap:12px;padding:10px 0;border-bottom:1px solid #22262e">
          <div style="font-family:JetBrains Mono;font-size:0.75rem;color:#5f6368;min-width:50px;padding-top:2px">{act_date}</div>
          <div style="background:{badge_color}22;color:{badge_color};padding:2px 10px;border-radius:4px;font-family:JetBrains Mono;font-size:0.7rem;white-space:nowrap">{act_type}</div>
          <div style="font-size:0.82rem;color:#9aa0ac;flex:1">{act_desc}</div>
        </div>
        """, unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# TAB 5 — CHARTS
# ─────────────────────────────────────────────────────────────────────────────
with tabs[5]:
    rows = st.session_state.get("rows", [])
    if not rows:
        st.info("Refresh to load charts.")
    else:
        total_v = st.session_state["kpis"].get("total_value", 1)

        c1, c2 = st.columns(2)

        # Donut chart
        with c1:
            fig_donut = go.Figure(go.Pie(
                labels=[r["ticker"] for r in rows],
                values=[r["equity"] for r in rows],
                hole=0.55,
                textinfo="label+percent",
                textfont=dict(family="JetBrains Mono", size=11, color="#e8eaed"),
                marker=dict(
                    colors=px.colors.qualitative.Dark24,
                    line=dict(color="#0a0c10", width=2),
                ),
            ))
            fig_donut.update_layout(
                title="Portfolio Allocation",
                paper_bgcolor="rgba(0,0,0,0)",
                font_color="#9aa0ac",
                font_family="DM Sans",
                legend=dict(font=dict(family="JetBrains Mono", size=10, color="#9aa0ac"), bgcolor="rgba(0,0,0,0)"),
                height=400, margin=dict(l=0, r=0, t=40, b=0),
                annotations=[dict(text=f"${total_v:,.0f}", x=0.5, y=0.5,
                                   font=dict(family="Instrument Serif", size=18, color="#e8eaed"),
                                   showarrow=False)],
            )
            st.plotly_chart(fig_donut, use_container_width=True)

        # P&L bar chart
        with c2:
            sorted_rows = sorted(rows, key=lambda r: r["pl_pct"])
            colors = ["#ff5252" if r["pl_pct"] < 0 else "#00e676" for r in sorted_rows]
            fig_pl = go.Figure(go.Bar(
                x=[r["ticker"] for r in sorted_rows],
                y=[r["pl_pct"] for r in sorted_rows],
                marker_color=colors,
                text=[f"{r['pl_pct']:+.1f}%" for r in sorted_rows],
                textposition="outside",
                textfont=dict(family="JetBrains Mono", size=9, color="#9aa0ac"),
            ))
            fig_pl.update_layout(
                title="P&L % by Position",
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font_color="#9aa0ac", font_family="DM Sans",
                xaxis=dict(tickfont=dict(color="#e8eaed", family="JetBrains Mono", size=9)),
                yaxis=dict(title="P&L %", gridcolor="#22262e", tickformat="+.1f"),
                height=400, margin=dict(l=40, r=20, t=40, b=60),
            )
            st.plotly_chart(fig_pl, use_container_width=True)

        # Equity waterfall
        top10 = sorted(rows, key=lambda r: r["equity"], reverse=True)[:12]
        fig_eq = go.Figure(go.Bar(
            x=[r["ticker"] for r in top10],
            y=[r["equity"] for r in top10],
            marker_color="#448aff",
            text=[f"${r['equity']:,.0f}" for r in top10],
            textposition="outside",
            textfont=dict(family="JetBrains Mono", size=10, color="#9aa0ac"),
        ))
        fig_eq.update_layout(
            title="Top Positions by Equity Value",
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font_color="#9aa0ac", font_family="DM Sans",
            xaxis=dict(tickfont=dict(color="#e8eaed", family="JetBrains Mono")),
            yaxis=dict(title="Equity ($)", gridcolor="#22262e", tickformat="$,.0f"),
            height=360, margin=dict(l=60, r=20, t=40, b=40),
        )
        st.plotly_chart(fig_eq, use_container_width=True)

# ─────────────────────────────────────────────────────────────────────────────
# TAB 6 — HISTORY
# ─────────────────────────────────────────────────────────────────────────────
with tabs[6]:
    st.markdown('<div class="section-title">📸 Portfolio Snapshots</div>', unsafe_allow_html=True)
    hist = de._load(de.REC_HIST_PATH, [])

    if not hist:
        st.info("No snapshots yet. Click 'Save Snapshot to History' in the Actions tab.")
    else:
        # Portfolio value over time chart
        if len(hist) > 1:
            ts_list = [h["ts"][:16] for h in hist]
            val_list = [h["total_value"] for h in hist]
            fig_hist = go.Figure(go.Scatter(
                x=ts_list, y=val_list,
                mode="lines+markers",
                line=dict(color="#00e676", width=2),
                marker=dict(color="#00e676", size=6),
                fill="tozeroy",
                fillcolor="rgba(0,230,118,0.05)",
            ))
            fig_hist.update_layout(
                title="Portfolio Value Over Time",
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font_color="#9aa0ac", font_family="DM Sans",
                xaxis=dict(tickfont=dict(color="#9aa0ac", size=9)),
                yaxis=dict(tickformat="$,.0f", gridcolor="#22262e"),
                height=280, margin=dict(l=60, r=20, t=40, b=40),
            )
            st.plotly_chart(fig_hist, use_container_width=True)

        # Snapshot table
        for h in reversed(hist[-50:]):
            ts  = h["ts"][:16].replace("T", " ")
            tv  = h.get("total_value", 0)
            pl  = h.get("total_pl", 0)
            pl_pct = h.get("total_pl_pct", 0)
            pl_color = "#00e676" if pl >= 0 else "#ff5252"
            st.markdown(f"""
            <div class="hist-row">
              <span style="color:#9aa0ac">{ts}</span>
              <span style="color:#e8eaed">${tv:,.0f}</span>
              <span style="color:{pl_color}">{pl:+,.0f} ({pl_pct:+.1f}%)</span>
            </div>
            """, unsafe_allow_html=True)

    st.markdown('<div class="section-title">📥 Deposit Log</div>', unsafe_allow_html=True)
    dep_log = de._load(de.DEPOSIT_LOG_PATH, [])
    if not dep_log:
        st.info("No deposits logged yet.")
    else:
        for d in reversed(dep_log[-30:]):
            ts = d["ts"][:16].replace("T", " ")
            num = d.get("deposit_num", "?")
            total = d.get("total", 0)
            allocs = d.get("allocations", [])
            tickers_str = " · ".join([f"{a['ticker']} ${a['amount']:.0f}" for a in allocs])
            st.markdown(f"""
            <div style="background:#16191f;border:1px solid #22262e;border-radius:8px;padding:12px 16px;margin-bottom:8px">
              <div style="display:flex;justify-content:space-between;margin-bottom:4px">
                <span style="font-family:JetBrains Mono;font-size:0.82rem;color:#e8eaed">Deposit #{num}</span>
                <span style="font-family:JetBrains Mono;font-size:0.82rem;color:#00e676">${total:,.0f}</span>
                <span style="font-family:JetBrains Mono;font-size:0.72rem;color:#5f6368">{ts}</span>
              </div>
              <div style="font-family:JetBrains Mono;font-size:0.72rem;color:#5f6368">{tickers_str}</div>
            </div>
            """, unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# TAB 7 — IMPORT
# ─────────────────────────────────────────────────────────────────────────────
with tabs[7]:
    st.markdown('<div class="section-title">📥 Import Robinhood CSV</div>', unsafe_allow_html=True)
    st.markdown('<div class="info-box">Upload your latest Robinhood account activity CSV. Duplicate transactions are automatically skipped via SHA-1 fingerprinting.</div>', unsafe_allow_html=True)

    uploaded = st.file_uploader(
        "Drop your Robinhood CSV here",
        type=["csv"],
        accept_multiple_files=False,
    )

    if uploaded:
        file_bytes = uploaded.read()
        with st.spinner("Parsing CSV…"):
            new_rows, skipped, errors = de.ingest_csv(file_bytes)

        if errors:
            for e in errors:
                st.error(e)
        else:
            st.markdown(f'<div class="success-box">✓ Import complete: <b>{new_rows} new rows</b> added · {skipped} duplicates skipped.</div>', unsafe_allow_html=True)

        if new_rows > 0:
            if st.button("🔄 Refresh Dashboard Now", type="primary"):
                st.session_state["refresh_count"] += 1
                with st.spinner("Recomputing portfolio…"):
                    _refresh_all()
                st.success("Dashboard updated with new transactions.")
                st.rerun()

    st.markdown('<div class="section-title">📝 Manual Position Entry</div>', unsafe_allow_html=True)
    st.caption("Use this if CSV upload fails or to add positions manually.")

    with st.expander("Add manual position"):
        col1, col2 = st.columns(2)
        with col1:
            m_ticker = st.text_input("Ticker", placeholder="e.g. NVDA").upper().strip()
            m_shares = st.number_input("Shares", min_value=0.0001, value=1.0, step=0.001, format="%.4f")
        with col2:
            m_avg    = st.number_input("Avg Cost ($)", min_value=0.01, value=100.0, step=0.01)
            m_lt     = st.checkbox("LT Eligible (>1 yr)?", value=False)

        if st.button("Add Position") and m_ticker:
            store = de._load(de.TX_STORE_PATH, {})
            import hashlib
            fp = hashlib.sha1(f"manual|{m_ticker}|{m_shares}|{m_avg}".encode()).hexdigest()
            if fp not in store:
                store[fp] = {
                    "date": str(date.today()),
                    "code": "Buy",
                    "ticker": m_ticker,
                    "qty": m_shares,
                    "price": m_avg,
                    "amount": -(m_shares * m_avg),
                    "desc": "Manual entry",
                    "lt": m_lt,
                }
                de._save(de.TX_STORE_PATH, store)
                st.success(f"Added {m_ticker} · {m_shares:.4f} shares @ ${m_avg:.2f}")
                st.session_state["refresh_count"] += 1
                _refresh_all()
                st.rerun()
            else:
                st.warning("Identical position already exists.")

# ─────────────────────────────────────────────────────────────────────────────
# TAB 8 — TESTS
# ─────────────────────────────────────────────────────────────────────────────
with tabs[8]:
    st.markdown('<div class="section-title">🧪 Live System Tests</div>', unsafe_allow_html=True)
    st.markdown('<div class="info-box">Click "Run Tests" to verify that price fetching, portfolio computation, and recommendations are working correctly with real live data.</div>', unsafe_allow_html=True)

    if st.button("▶️ Run All Tests", type="primary"):
        st.session_state["show_test_results"] = True

    if st.session_state.get("show_test_results"):
        results = []

        # Test 1: tx_store exists and has rows
        store = de._load(de.TX_STORE_PATH, {})
        results.append(("TX Store loaded", len(store) > 0, f"{len(store)} rows"))

        # Test 2: Portfolio recompute
        try:
            positions = de.recompute_portfolio()
            results.append(("Portfolio recompute", len(positions) > 10, f"{len(positions)} positions"))
        except Exception as e:
            results.append(("Portfolio recompute", False, str(e)))

        # Test 3: Key positions present
        for t in ["VOO", "NVDA", "AAPL", "VYM", "GLD"]:
            present = t in positions
            shares = positions.get(t, {}).get("shares", 0)
            results.append((f"{t} position", present and shares > 0, f"{shares:.4f} shares"))

        # Test 4: Crypto present
        results.append(("BTC position", "BTC" in positions, f"{positions.get('BTC',{}).get('shares',0):.6f} sh"))
        results.append(("XRP position", "XRP" in positions, f"{positions.get('XRP',{}).get('shares',0):.4f} sh"))

        # Test 5: Price fetch — test a few tickers
        test_tickers = ("VOO", "NVDA", "AAPL")
        with st.spinner(f"Fetching live prices for {test_tickers}…"):
            test_prices = de.fetch_prices(test_tickers, bust=99999)
        for t in test_tickers:
            p = test_prices.get(t, 0)
            results.append((f"Stock price {t}", p > 1, f"${p:,.2f}"))

        # Test 6: Crypto prices
        crypto_test = ("BTC", "XRP")
        with st.spinner("Fetching crypto prices…"):
            crypto_prices = de.fetch_prices(crypto_test, bust=99999)
        for c in crypto_test:
            p = crypto_prices.get(c, 0)
            results.append((f"Crypto price {c}", p > 0.01, f"${p:,.4f}"))

        # Test 7: Recommendation engine
        all_prices = {**test_prices, **crypto_prices}
        rows = de.enrich_portfolio(positions, all_prices)
        recs = de.generate_recommendations(rows)
        results.append(("Recs generated", len(recs) > 5, f"{len(recs)} recommendations"))
        sell_recs = [r for r in recs if r.get("badge") == "SELL"]
        buy_recs  = [r for r in recs if r.get("badge") == "BUY"]
        results.append(("SELL recs exist", True, f"{len(sell_recs)} sell signals"))
        results.append(("BUY recs exist",  True, f"{len(buy_recs)} buy signals"))

        # Test 8: KPI computation
        kpis_test = de.compute_kpis(rows, 1042.17)
        results.append(("KPI total_value > 0", kpis_test["total_value"] > 10000, f"${kpis_test['total_value']:,.0f}"))
        results.append(("Positions count",     kpis_test["positions"] > 10,      f"{kpis_test['positions']}"))

        # Test 9: Deposit schedule
        sched = de.get_deposit_schedule(3)
        results.append(("Deposit schedule", len(sched) == 3, f"Next: {sched[0]['date']} — rotating: {sched[0]['rotating']}"))

        # Test 10: Targets persistence
        de.save_targets({"VOO": 20.0, "NVDA": 15.0})
        loaded = de.load_targets()
        results.append(("Targets save/load", loaded.get("VOO") == 20.0, "R/W OK"))

        # Display results
        st.markdown('<br>', unsafe_allow_html=True)
        passed = sum(1 for _, ok, _ in results if ok)
        total  = len(results)
        pct    = passed / total * 100
        color  = "#00e676" if pct == 100 else "#ffb300" if pct >= 80 else "#ff5252"
        st.markdown(f'<div style="font-family:Instrument Serif;font-size:1.4rem;color:{color};margin-bottom:16px">{passed}/{total} tests passed ({pct:.0f}%)</div>', unsafe_allow_html=True)

        for test_name, ok, detail in results:
            icon = "✅" if ok else "❌"
            row_bg = "#1a3d2b" if ok else "#3d1a1a"
            row_col = "#00e676" if ok else "#ff5252"
            st.markdown(f"""
            <div style="display:flex;align-items:center;gap:12px;padding:8px 14px;
                        background:{row_bg};border-radius:6px;margin-bottom:4px">
              <span style="font-size:0.9rem">{icon}</span>
              <span style="font-family:JetBrains Mono;font-size:0.8rem;color:#e8eaed;flex:1">{test_name}</span>
              <span style="font-family:JetBrains Mono;font-size:0.75rem;color:{row_col}">{detail}</span>
            </div>
            """, unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# Footer
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("""
<div style="text-align:center;padding:40px 0 20px;color:#22262e;
            font-family:JetBrains Mono;font-size:0.68rem;letter-spacing:0.1em">
  PORTFOLIO WAR ROOM v10.0 · FOR INFORMATIONAL PURPOSES ONLY · NOT FINANCIAL ADVICE
</div>
""", unsafe_allow_html=True)
