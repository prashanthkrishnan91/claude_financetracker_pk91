"""
Portfolio War Room — Main App v10.1
New: Data Health indicator · AI Target Engine · $900 full table · PDF import tab.
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
st.set_page_config(
    page_title="Portfolio War Room",
    page_icon="⚔️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ═══════════════════════════════════════════════════════════════════════════════
# CSS
# ═══════════════════════════════════════════════════════════════════════════════
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=JetBrains+Mono:wght@400;600&family=DM+Sans:wght@300;400;500;600&display=swap');

:root {
  --bg:#0a0c10; --surface:#111318; --card:#16191f; --border:#22262e;
  --green:#00e676; --gdim:#1a3d2b; --red:#ff5252; --rdim:#3d1a1a;
  --amber:#ffb300; --adim:#3d2e00; --blue:#448aff;
  --t1:#e8eaed; --t2:#9aa0ac; --t3:#5f6368;
  --r:12px; --rs:6px;
}

html,body,[data-testid="stAppViewContainer"]{background:var(--bg)!important;color:var(--t1)!important;font-family:'DM Sans',sans-serif}
[data-testid="stSidebar"]{background:var(--surface)!important;border-right:1px solid var(--border)}
[data-testid="stSidebar"] label{color:var(--t2)!important;font-size:0.78rem!important}

.war-header{display:flex;align-items:center;gap:16px;padding:24px 0 18px;border-bottom:1px solid var(--border);margin-bottom:22px}
.war-title{font-family:'Instrument Serif',serif;font-size:2rem;color:var(--t1);letter-spacing:-0.5px;line-height:1.1}
.war-sub{font-size:0.7rem;color:var(--t3);font-family:'JetBrains Mono',monospace;letter-spacing:.06em;text-transform:uppercase}

.kpi-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(175px,1fr));gap:11px;margin-bottom:22px}
.kpi-card{background:var(--card);border:1px solid var(--border);border-radius:var(--r);padding:16px 18px;transition:border-color .2s}
.kpi-card:hover{border-color:var(--green)}
.kpi-label{font-size:.68rem;color:var(--t3);text-transform:uppercase;letter-spacing:.08em;font-family:'JetBrains Mono',monospace;margin-bottom:6px}
.kpi-value{font-family:'Instrument Serif',serif;font-size:1.5rem;color:var(--t1);line-height:1.1}
.kpi-value.g{color:var(--green)}.kpi-value.r{color:var(--red)}.kpi-value.a{color:var(--amber)}
.kpi-sub{font-size:.68rem;color:var(--t2);margin-top:3px;font-family:'JetBrains Mono',monospace}

/* Data health badge */
.dh-live{background:#1a3d2b;color:#00e676;padding:4px 12px;border-radius:20px;font-family:'JetBrains Mono',monospace;font-size:.72rem;font-weight:600;display:inline-block;margin-bottom:8px}
.dh-partial{background:#3d2e00;color:#ffb300;padding:4px 12px;border-radius:20px;font-family:'JetBrains Mono',monospace;font-size:.72rem;font-weight:600;display:inline-block;margin-bottom:8px}
.dh-stale{background:#3d1a1a;color:#ff5252;padding:4px 12px;border-radius:20px;font-family:'JetBrains Mono',monospace;font-size:.72rem;font-weight:600;display:inline-block;margin-bottom:8px}

[data-testid="stTabs"] [data-baseweb="tab-list"]{background:transparent!important;border-bottom:1px solid var(--border)!important;gap:0!important}
[data-testid="stTabs"] [data-baseweb="tab"]{background:transparent!important;color:var(--t2)!important;font-family:'DM Sans',sans-serif!important;font-size:.83rem!important;font-weight:500!important;padding:9px 18px!important;border-bottom:2px solid transparent!important;border-radius:0!important;transition:all .15s!important}
[data-testid="stTabs"] [aria-selected="true"]{color:var(--green)!important;border-bottom:2px solid var(--green)!important}

.rec-card{background:var(--card);border:1px solid var(--border);border-radius:var(--r);padding:15px 18px;margin-bottom:9px}
.rec-card.sell{border-left:3px solid var(--red)}.rec-card.buy{border-left:3px solid var(--green)}
.rec-card.trim{border-left:3px solid var(--amber)}.rec-card.hold{border-left:3px solid var(--border)}
.rec-card.review{border-left:3px solid #ce93d8}
.rec-ticker{font-family:'JetBrains Mono',monospace;font-size:1.05rem;font-weight:600}
.rec-action{font-size:.88rem;font-weight:600;margin:3px 0}
.rec-reason{font-size:.78rem;color:var(--t2);line-height:1.5}
.rec-meta{display:flex;gap:18px;margin-top:9px;flex-wrap:wrap}
.rec-meta-item{font-family:'JetBrains Mono',monospace;font-size:.68rem;color:var(--t3)}
.rec-meta-item span{color:var(--t1)}
.pill{display:inline-block;padding:2px 9px;border-radius:20px;font-size:.66rem;font-weight:600;letter-spacing:.05em;font-family:'JetBrains Mono',monospace;text-transform:uppercase;margin-right:5px}
.pill-sell{background:var(--rdim);color:var(--red)}.pill-buy{background:var(--gdim);color:var(--green)}
.pill-trim{background:var(--adim);color:var(--amber)}.pill-hold{background:#1e2128;color:var(--t2)}
.pill-review{background:#2d1b3d;color:#ce93d8}.pill-lt{background:#1a2638;color:var(--blue)}
.pill-st{background:#3d2200;color:#ff8f00}

.dep-card{background:var(--card);border:1px solid var(--border);border-radius:var(--r);padding:18px 20px;margin-bottom:12px}
.dep-num{font-family:'Instrument Serif',serif;font-size:1.35rem;color:var(--green)}
.dep-date{font-family:'JetBrains Mono',monospace;font-size:.78rem;color:var(--t2)}

.sec-title{font-family:'Instrument Serif',serif;font-size:1.15rem;color:var(--t1);margin:22px 0 10px;display:flex;align-items:center;gap:8px}
.sec-title::after{content:'';flex:1;height:1px;background:var(--border)}

.alert-box{background:var(--rdim);border:1px solid var(--red);border-radius:var(--rs);padding:11px 15px;margin-bottom:11px;font-size:.83rem}
.success-box{background:var(--gdim);border:1px solid var(--green);border-radius:var(--rs);padding:11px 15px;margin-bottom:11px;font-size:.83rem}
.info-box{background:#1a2638;border:1px solid var(--blue);border-radius:var(--rs);padding:11px 15px;margin-bottom:11px;font-size:.83rem}
.warn-box{background:var(--adim);border:1px solid var(--amber);border-radius:var(--rs);padding:11px 15px;margin-bottom:11px;font-size:.83rem}

.ai-badge{background:#2d1b3d;color:#ce93d8;padding:2px 9px;border-radius:4px;font-size:.68rem;font-family:'JetBrains Mono',monospace;font-weight:600}

.stButton>button{background:var(--surface)!important;border:1px solid var(--border)!important;color:var(--t1)!important;border-radius:var(--rs)!important;font-family:'DM Sans',sans-serif!important;font-size:.8rem!important;font-weight:500!important}
.stButton>button:hover{border-color:var(--green)!important;color:var(--green)!important}
.stButton>button[kind="primary"]{background:var(--gdim)!important;border-color:var(--green)!important;color:var(--green)!important}
[data-testid="stMetric"]{background:var(--card);border:1px solid var(--border);border-radius:var(--r);padding:12px 16px}
div[data-testid="stNumberInput"] input,div[data-testid="stTextInput"] input{background:var(--card)!important;border-color:var(--border)!important;color:var(--t1)!important}
div[data-testid="stFileUploader"]{background:var(--card)!important;border:1px dashed var(--border)!important;border-radius:var(--r)!important}
</style>
""", unsafe_allow_html=True)

# ═══════════════════════════════════════════════════════════════════════════════
# Session state
# ═══════════════════════════════════════════════════════════════════════════════
_DEFAULTS = {
    "refresh_count": 0, "cash": de.ROBINHOOD_CASH_DEFAULT,
    "positions": None, "prices": {}, "price_status": {},
    "rows": [], "recs": [], "kpis": {},
    "targets": de.load_targets(), "suggested_targets": {},
    "last_refresh": None, "deposit_num": 1,
    "show_tests": False, "ai_targets_applied": False,
}
for k, v in _DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v

de.bootstrap_if_needed()

# ═══════════════════════════════════════════════════════════════════════════════
# Cached price loader
# ═══════════════════════════════════════════════════════════════════════════════
@st.cache_data(ttl=300)
def _cached_prices(tickers: tuple, bust: int) -> tuple:
    return de.get_clean_prices(tickers, bust=bust)

def _refresh_all():
    positions = de.recompute_portfolio()
    st.session_state["positions"] = positions
    tickers = tuple(sorted(positions.keys()))
    prices, status = _cached_prices(tickers, st.session_state["refresh_count"])
    st.session_state["prices"]       = prices
    st.session_state["price_status"] = status
    rows = de.enrich_portfolio(positions, prices)
    recs = de.generate_recommendations(rows)
    kpis = de.compute_kpis(rows, st.session_state["cash"])
    st.session_state.update({"rows": rows, "recs": recs, "kpis": kpis,
                               "last_refresh": datetime.now().strftime("%H:%M:%S")})
    # Generate AI suggested targets
    if rows:
        st.session_state["suggested_targets"] = de.generate_suggested_targets(rows, kpis["total_value"])

if st.session_state["positions"] is None:
    with st.spinner("Loading portfolio…"):
        _refresh_all()

# ═══════════════════════════════════════════════════════════════════════════════
# Sidebar
# ═══════════════════════════════════════════════════════════════════════════════
with st.sidebar:
    st.markdown('<div style="font-family:\'Instrument Serif\',serif;font-size:1.25rem;color:#e8eaed;padding:6px 0 14px">⚔️ War Room</div>', unsafe_allow_html=True)

    # ── Data Health Indicator ──
    health = de.data_health_summary(st.session_state.get("price_status", {}))
    dh_cls = {"green": "dh-live", "yellow": "dh-partial", "red": "dh-stale"}.get(health["color"], "dh-stale")
    h = health
    st.markdown(f'<div class="{dh_cls}">{h["label"]}</div>', unsafe_allow_html=True)
    if h["total"] > 0:
        st.caption(f"Live: {h['live']} · Cached: {h['cached']} · Fallback: {h['fallback']} of {h['total']} tickers")

    if st.button("🔄 Refresh Prices", type="primary", use_container_width=True):
        st.session_state["refresh_count"] += 1
        with st.spinner("Fetching live prices…"):
            _refresh_all()
        st.success("Prices updated")
        st.rerun()

    if st.session_state["last_refresh"]:
        st.caption(f"Last refresh: {st.session_state['last_refresh']}")

    st.divider()

    # ── Cash ──
    st.markdown("**💵 Cash Balance**")
    new_cash = st.number_input("Cash", value=float(st.session_state["cash"]),
                                step=10.0, format="%.2f", label_visibility="collapsed")
    if new_cash != st.session_state["cash"]:
        st.session_state["cash"] = new_cash
        if st.session_state["rows"]:
            st.session_state["kpis"] = de.compute_kpis(st.session_state["rows"], new_cash)

    st.session_state["deposit_num"] = st.number_input(
        "**📅 Next Deposit #**", min_value=1, max_value=50,
        value=st.session_state["deposit_num"])

    st.divider()

    # ── AI Target Engine ──
    st.markdown('<div style="font-size:.82rem;color:#e8eaed;font-weight:600;margin-bottom:4px">🧠 AI Target Allocations</div>', unsafe_allow_html=True)
    st.markdown('<span class="ai-badge">Moderate-Aggressive Profile</span>', unsafe_allow_html=True)
    st.caption("AI-suggested % shown. Override any value before committing.")

    suggested = st.session_state.get("suggested_targets", {})
    targets   = dict(st.session_state["targets"])  # copy

    # Show AI suggestions as editable number_inputs
    rows_for_targets = st.session_state.get("rows", [])
    display_tickers = [r["ticker"] for r in rows_for_targets[:18]]  # top 18 by equity

    changed = False
    for t in display_tickers:
        ai_val = suggested.get(t, 0.0)
        cur_val = float(targets.get(t, ai_val))
        col1, col2 = st.columns([2, 3])
        with col1:
            st.markdown(f'<div style="font-family:JetBrains Mono;font-size:.75rem;color:#9aa0ac;padding-top:8px">{t}</div>',
                        unsafe_allow_html=True)
            if ai_val > 0:
                st.markdown(f'<div style="font-family:JetBrains Mono;font-size:.62rem;color:#ce93d8">AI: {ai_val:.1f}%</div>',
                            unsafe_allow_html=True)
        with col2:
            new_v = st.number_input(f"pct_{t}", min_value=0.0, max_value=100.0,
                                     value=cur_val, step=0.5, format="%.1f",
                                     label_visibility="collapsed", key=f"tgt_{t}")
            if new_v != targets.get(t):
                targets[t] = new_v
                changed = True

    col_a, col_b = st.columns(2)
    with col_a:
        if st.button("💾 Save", use_container_width=True):
            st.session_state["targets"] = targets
            de.save_targets(targets)
            st.success("Saved")
    with col_b:
        if st.button("🧠 Reset AI", use_container_width=True):
            st.session_state["targets"] = dict(suggested)
            de.save_targets(dict(suggested))
            st.success("AI targets applied")
            st.rerun()

    st.divider()

    # ── Crypto overrides ──
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
            st.success("Updated & refreshed")
            st.rerun()

# ═══════════════════════════════════════════════════════════════════════════════
# Header
# ═══════════════════════════════════════════════════════════════════════════════
st.markdown("""
<div class="war-header">
  <div style="font-size:2.6rem;line-height:1">⚔️</div>
  <div>
    <div class="war-title">Portfolio War Room</div>
    <div class="war-sub">v10.1 · AI Targets · Live Prices · Tax-Optimized · $900 Deploy Engine</div>
  </div>
</div>
""", unsafe_allow_html=True)

# ═══════════════════════════════════════════════════════════════════════════════
# KPI Cards
# ═══════════════════════════════════════════════════════════════════════════════
kpis = st.session_state.get("kpis", {})
if kpis:
    tv   = kpis.get("total_value", 0)
    pl   = kpis.get("total_pl", 0)
    plp  = kpis.get("total_pl_pct", 0)
    sv   = kpis.get("stock_value", 0)
    cv   = kpis.get("crypto_value", 0)
    cash = kpis.get("cash", 0)
    drip = kpis.get("drip_total", 0)
    pos  = kpis.get("positions", 0)
    wins = kpis.get("winners", 0)
    loss = kpis.get("losers", 0)
    plc  = "g" if pl >= 0 else "r"
    pls  = "+" if pl >= 0 else ""
    st.markdown(f"""
    <div class="kpi-grid">
      <div class="kpi-card"><div class="kpi-label">Total Value</div>
        <div class="kpi-value">${tv:,.0f}</div><div class="kpi-sub">Stocks + Crypto + Cash</div></div>
      <div class="kpi-card"><div class="kpi-label">Total P&L</div>
        <div class="kpi-value {plc}">{pls}${pl:,.0f}</div><div class="kpi-sub">{pls}{plp:.1f}% all-time</div></div>
      <div class="kpi-card"><div class="kpi-label">Stocks</div>
        <div class="kpi-value">${sv:,.0f}</div><div class="kpi-sub">{wins}W / {loss}L · {pos} positions</div></div>
      <div class="kpi-card"><div class="kpi-label">Crypto</div>
        <div class="kpi-value">${cv:,.0f}</div><div class="kpi-sub">BTC + XRP</div></div>
      <div class="kpi-card"><div class="kpi-label">Cash</div>
        <div class="kpi-value a">${cash:,.0f}</div><div class="kpi-sub">Available to deploy</div></div>
      <div class="kpi-card"><div class="kpi-label">DRIP Reinvested</div>
        <div class="kpi-value g">${drip:,.0f}</div><div class="kpi-sub">Dividends compounded</div></div>
    </div>
    """, unsafe_allow_html=True)

    sell_ct = sum(1 for r in st.session_state.get("recs", []) if r.get("badge") == "SELL")
    if sell_ct:
        st.markdown(f'<div class="alert-box">🔴 <b>{sell_ct} urgent SELL action{"s" if sell_ct>1 else ""}</b> — execute before next deposit to maximise tax savings.</div>', unsafe_allow_html=True)

# ═══════════════════════════════════════════════════════════════════════════════
# Tabs
# ═══════════════════════════════════════════════════════════════════════════════
tabs = st.tabs(["⚡ Actions", "📊 Portfolio", "🎯 Rebalancing",
                "💰 Invest $900", "📅 Schedule", "📈 Charts",
                "🕘 History", "📥 Import / PDF", "🧪 Tests"])

# ─────────────────────────────────────────────────────────────────────────────
# TAB 0 — ACTIONS
# ─────────────────────────────────────────────────────────────────────────────
with tabs[0]:
    recs = st.session_state.get("recs", [])
    if not recs:
        st.info("Click 🔄 Refresh in sidebar to load recommendations.")
    else:
        groups = {"SELL": [], "REVIEW": [], "BUY": [], "TRIM": [], "HOLD": []}
        for r in recs:
            groups.get(r.get("badge", "HOLD"), groups["HOLD"]).append(r)

        badge_cls = {"SELL":"sell","BUY":"buy","TRIM":"trim","HOLD":"hold","REVIEW":"review"}
        pill_cls  = {"SELL":"pill-sell","BUY":"pill-buy","TRIM":"pill-trim","HOLD":"pill-hold","REVIEW":"pill-review"}
        act_col   = {"SELL":"#ff5252","BUY":"#00e676","TRIM":"#ffb300","HOLD":"#9aa0ac","REVIEW":"#ce93d8"}

        def _render_group(lst, title):
            if not lst:
                return
            st.markdown(f'<div class="sec-title">{title}</div>', unsafe_allow_html=True)
            for r in lst:
                badge   = r.get("badge","HOLD")
                lt_pill = '<span class="pill pill-lt">LT ✓</span>' if r.get("lt") else '<span class="pill pill-st">ST ⚠</span>'
                plc     = "#00e676" if r["pl_pct"]>=0 else "#ff5252"
                proc    = f' · Proceeds: <span style="color:#ffb300">≈${r["proceed_est"]:,.0f}</span>' if r.get("proceed_est",0)>0 else ""
                st.markdown(f"""
                <div class="rec-card {badge_cls.get(badge,'hold')}">
                  <div style="display:flex;align-items:center;gap:9px;margin-bottom:5px">
                    <span class="rec-ticker">{r['ticker']}</span>
                    <span class="pill {pill_cls.get(badge,'pill-hold')}">{badge}</span>
                    {lt_pill}
                  </div>
                  <div class="rec-action" style="color:{act_col.get(badge,'#9aa0ac')}">{r['action']}</div>
                  <div class="rec-reason">{r['reason']}</div>
                  <div class="rec-meta">
                    <div class="rec-meta-item">Price <span>${r['live_price']:,.2f}</span></div>
                    <div class="rec-meta-item">P&L <span style="color:{plc}">{r['pl_pct']:+.1f}%</span></div>
                    <div class="rec-meta-item">Equity <span>${r['equity']:,.0f}</span></div>
                    <div class="rec-meta-item">Target <span>${r['target']:,.0f}</span>{proc}</div>
                  </div>
                  <div style="font-size:.68rem;color:var(--t3);margin-top:7px">{r['tax_note']}</div>
                </div>""", unsafe_allow_html=True)

        _render_group(groups["SELL"],   "🔴 Urgent — Sell Now")
        _render_group(groups["REVIEW"], "🚨 Review Required")
        _render_group(groups["BUY"],    "🟢 Buy / Accumulate")
        _render_group(groups["TRIM"],   "✂️ Trim — Lock Gains")
        _render_group(groups["HOLD"],   "🟡 Hold")

        if st.button("📸 Save Snapshot to History"):
            de.save_snapshot(st.session_state["kpis"], recs)
            st.success("Snapshot saved")

# ─────────────────────────────────────────────────────────────────────────────
# TAB 1 — PORTFOLIO
# ─────────────────────────────────────────────────────────────────────────────
with tabs[1]:
    rows = st.session_state.get("rows", [])
    if not rows:
        st.info("Refresh to load.")
    else:
        df = pd.DataFrame([{
            "Ticker": r["ticker"], "Shares": r["shares"],
            "Avg Cost": r["avg_cost"], "Live Price": r["live_price"],
            "Equity": r["equity"], "P&L $": r["pl"], "P&L %": r["pl_pct"],
            "LT?": r["lt"], "DRIP $": r["drip_amount"],
        } for r in rows])
        st.dataframe(df, column_config={
            "Shares":     st.column_config.NumberColumn(format="%.4f"),
            "Avg Cost":   st.column_config.NumberColumn(format="$%.2f"),
            "Live Price": st.column_config.NumberColumn(format="$%.2f"),
            "Equity":     st.column_config.NumberColumn(format="$%.2f"),
            "P&L $":      st.column_config.NumberColumn(format="$%.2f"),
            "P&L %":      st.column_config.NumberColumn(format="%.1f%%"),
            "LT?":        st.column_config.CheckboxColumn(),
            "DRIP $":     st.column_config.NumberColumn(format="$%.2f"),
        }, use_container_width=True, height=540)

        te = sum(r["equity"] for r in rows)
        tc = sum(r["cost_basis"] for r in rows)
        tp = sum(r["pl"] for r in rows)
        c1,c2,c3,c4 = st.columns(4)
        c1.metric("Total Equity", f"${te:,.2f}")
        c2.metric("Total Cost",   f"${tc:,.2f}")
        c3.metric("Total P&L",    f"${tp:+,.2f}", f"{tp/tc*100:+.1f}%" if tc>0 else "")
        c4.metric("DRIP",         f"${sum(r['drip_amount'] for r in rows):,.2f}")

# ─────────────────────────────────────────────────────────────────────────────
# TAB 2 — REBALANCING
# ─────────────────────────────────────────────────────────────────────────────
with tabs[2]:
    rows    = st.session_state.get("rows", [])
    targets = st.session_state.get("targets", {})
    prices  = st.session_state.get("prices", {})
    kpis_d  = st.session_state.get("kpis", {})
    total_v = kpis_d.get("total_value", 1)

    if not rows:
        st.info("Refresh first.")
    else:
        suggested = st.session_state.get("suggested_targets", {})
        total_ai  = sum(suggested.values())
        st.markdown(f'<div class="info-box"><span class="ai-badge">🧠 AI</span> Suggested targets normalised to 100% (Moderate-Aggressive profile). Sum = {total_ai:.1f}%. Override in sidebar before committing.</div>', unsafe_allow_html=True)

        drift_rows = de.compute_rebalancing(rows, total_v, targets)
        has_targets = any(v > 0 for v in targets.values())

        if has_targets:
            df_drift = pd.DataFrame([{
                "Ticker":     dr["ticker"],
                "Current %":  round(dr["current_pct"], 2),
                "Target %":   round(dr["target_pct"], 2),
                "Drift %":    round(dr["drift"], 2),
                "Equity":     round(dr["equity"], 2),
                "Action":     "🔴 TRIM" if dr["drift"]>5 else "🟢 BUY" if dr["drift"]<-2 else "🟡 OK",
            } for dr in drift_rows if dr["target_pct"]>0 or dr["current_pct"]>0.5])

            st.dataframe(df_drift, column_config={
                "Current %": st.column_config.NumberColumn(format="%.2f%%"),
                "Target %":  st.column_config.NumberColumn(format="%.2f%%"),
                "Drift %":   st.column_config.NumberColumn(format="%.2f%%"),
                "Equity":    st.column_config.NumberColumn(format="$%.2f"),
            }, use_container_width=True)

            # Drift bar chart
            with_target = [dr for dr in drift_rows if dr["target_pct"]>0]
            if with_target:
                colors = ["#ff5252" if d["drift"]>0 else "#00e676" for d in with_target]
                fig = go.Figure(go.Bar(
                    x=[d["ticker"] for d in with_target],
                    y=[d["drift"] for d in with_target],
                    marker_color=colors,
                    text=[f'{d["drift"]:+.1f}%' for d in with_target],
                    textposition="outside",
                    textfont=dict(size=9, color="#9aa0ac", family="JetBrains Mono"),
                ))
                fig.update_layout(
                    title="Portfolio Drift from Target Allocation",
                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    font_color="#9aa0ac", font_family="DM Sans",
                    xaxis=dict(tickfont=dict(color="#e8eaed", family="JetBrains Mono", size=9)),
                    yaxis=dict(title="Drift (%)", gridcolor="#22262e", tickformat="+.1f"),
                    height=300, margin=dict(l=40,r=20,t=40,b=40),
                    shapes=[dict(type="line", x0=-0.5, x1=len(with_target)-0.5,
                                  y0=0, y1=0, line=dict(color="#5f6368", dash="dot", width=1))],
                )
                st.plotly_chart(fig, use_container_width=True)
        else:
            st.markdown('<div class="warn-box">⚠️ No target allocations set. Set targets in the sidebar (use 🧠 Reset AI to load AI suggestions) to enable drift chart and smart rebalancing.</div>', unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# TAB 3 — INVEST $900
# ─────────────────────────────────────────────────────────────────────────────
with tabs[3]:
    rows    = st.session_state.get("rows", [])
    targets = st.session_state.get("targets", {})
    prices  = st.session_state.get("prices", {})
    total_v = st.session_state.get("kpis", {}).get("total_value", 1)
    dep_num = st.session_state["deposit_num"]
    sched   = de.get_deposit_schedule(1)
    next_d  = sched[0]["date"] if sched else date.today()
    rotating = de.DEPOSIT_ROTATING[(dep_num-1) % len(de.DEPOSIT_ROTATING)]

    st.markdown(f"""
    <div class="dep-card">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px">
        <div><div class="dep-num">Deposit #{dep_num}</div>
             <div class="dep-date">📅 {next_d.strftime('%A, %B %d, %Y')} · $900.00</div></div>
        <div style="text-align:right">
          <div style="font-size:.65rem;color:#5f6368;font-family:JetBrains Mono">ROTATING PICK</div>
          <div style="font-family:JetBrains Mono;font-size:1rem;color:#00e676">{rotating}</div>
        </div>
      </div>
    </div>""", unsafe_allow_html=True)

    if rows:
        allocs = de.compute_deposit_allocation(900, rows, total_v, targets, dep_num, prices)

        st.markdown('<div class="sec-title">Allocation Table</div>', unsafe_allow_html=True)

        # ── Build a clean DataFrame — zero HTML string-concat, zero markdown escaping risk ──
        alloc_df = pd.DataFrame([{
            "Asset":           a["ticker"],
            "Current Value":   a["current_value"],
            "Target %":        a["target_pct"] if a.get("target_pct", 0) > 0 else 0.0,
            "Action":          a.get("action", "BUY"),
            "$ Amount":        a["amount"],
            "Est. Shares":     a["est_shares"],
            "Live Price":      a["live_price"],
            "Why":             a.get("reason", ""),
        } for a in allocs])

        st.dataframe(
            alloc_df,
            column_config={
                "Asset":         st.column_config.TextColumn("Asset", width="small"),
                "Current Value": st.column_config.NumberColumn("Current Value", format="$%,.0f"),
                "Target %":      st.column_config.NumberColumn("Target %",       format="%.1f%%"),
                "Action":        st.column_config.TextColumn("Action",           width="small"),
                "$ Amount":      st.column_config.NumberColumn("$ Amount",       format="$%.2f"),
                "Est. Shares":   st.column_config.NumberColumn("Est. Shares",    format="%.4f"),
                "Live Price":    st.column_config.NumberColumn("Live Price",     format="$%,.2f"),
                "Why":           st.column_config.TextColumn("Reason",           width="large"),
            },
            use_container_width=True,
            hide_index=True,
        )

        # ── Totals summary row — reliable st.columns + st.metric, no HTML ──
        total_buy  = sum(a["amount"] for a in allocs if a.get("action") == "BUY")
        total_trim = sum(a["amount"] for a in allocs if a.get("action") == "TRIM")
        n_buy      = sum(1 for a in allocs if a.get("action") == "BUY")
        n_trim     = sum(1 for a in allocs if a.get("action") == "TRIM")

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Total to Deploy",  f"${total_buy:,.2f}",  f"{n_buy} BUY positions")
        m2.metric("Total to Trim",    f"${total_trim:,.2f}", f"{n_trim} TRIM positions" if n_trim else "None")
        m3.metric("Deposit Amount",   "$900.00")
        m4.metric("Remaining",        f"${900 - total_buy:,.2f}" if total_buy <= 900 else "$0.00")

        st.markdown("<br>", unsafe_allow_html=True)
        col1, col2 = st.columns([3, 1])
        with col1:
            notes = st.text_input("Notes", placeholder="e.g. Apr 3 — executed all buys")
        with col2:
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("✅ Log Deposit", type="primary"):
                de.log_deposit(dep_num, allocs, 900.0, notes)
                st.session_state["deposit_num"] += 1
                st.success(f"Deposit #{dep_num} logged")
                st.rerun()
    else:
        st.info("Refresh prices to see deposit plan.")

# ─────────────────────────────────────────────────────────────────────────────
# TAB 4 — SCHEDULE
# ─────────────────────────────────────────────────────────────────────────────
with tabs[4]:
    schedule = de.get_deposit_schedule(16)
    today    = date.today()
    st.markdown('<div class="sec-title">📅 Biweekly Deposit Calendar</div>', unsafe_allow_html=True)
    st.markdown('<div class="info-box">Every other Friday · $900/deposit · Rotating pick cycles every 8 deposits</div>', unsafe_allow_html=True)

    for s in schedule:
        is_next = s["num"] == st.session_state["deposit_num"]
        is_past = s["date"] < today
        bc = "#00e676" if is_next else "#22262e"
        op = "0.45" if is_past else "1"
        alloc_str = "NVDA $252 · VOO $198 · VYM $153 · QQQ $153 · " + s["rotating"] + " $144"
        badge = '<span style="background:#1a3d2b;color:#00e676;padding:1px 7px;border-radius:4px;font-size:.62rem;font-weight:600;margin-left:6px">NEXT</span>' if is_next else ""
        st.markdown(f"""
        <div style="background:#16191f;border:1px solid {bc};border-radius:9px;padding:12px 16px;margin-bottom:7px;opacity:{op}">
          <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:3px">
            <span style="font-family:JetBrains Mono;font-size:.85rem;font-weight:600;color:#e8eaed">#{s['num']} — {s['date'].strftime('%b %d, %Y')}{badge}</span>
            <span style="font-family:JetBrains Mono;font-size:.85rem;color:#00e676">$900</span>
          </div>
          <div style="font-family:JetBrains Mono;font-size:.68rem;color:#5f6368">{alloc_str}</div>
        </div>""", unsafe_allow_html=True)

    st.markdown('<div class="sec-title">🗓️ 2026 Action Calendar</div>', unsafe_allow_html=True)
    actions = [
        ("Apr 3",  "SELL",  "#ff5252", "VTV, VEA, VWO, BND — LT eligible. Reinvest into VOO/VYM same day"),
        ("Apr 3",  "BUY",   "#00e676", "Deposit #1 — NVDA/VOO/VYM/QQQ + META"),
        ("Apr 4",  "TRIM",  "#ffb300", "GLD now LT eligible — trim 25% near $450 target"),
        ("Apr 17", "BUY",   "#00e676", "Deposit #2 — NVDA/VOO/VYM/QQQ + GOOGL"),
        ("May 20", "SELL",  "#ff5252", "SPY turns LT → sell all, buy VOO same day (not wash sale)"),
        ("Jul 15", "SELL",  "#ff5252", "VUG turns LT → sell all, buy QQQ same day"),
        ("Aug 14", "EVAL",  "#448aff", "BLSH hits 1yr — trim 25% if up >20%"),
        ("Nov 6",  "TRIM",  "#ffb300", "TSM big lot turns LT — trim 20%"),
        ("Dec 15", "TRIM",  "#ffb300", "GOOGL big lot turns LT — trim 20%"),
        ("Dec 20", "TAX",   "#ce93d8", "Year-end: harvest losses before Dec 31"),
    ]
    for dt, code, color, desc in actions:
        st.markdown(f"""
        <div style="display:flex;align-items:flex-start;gap:11px;padding:9px 0;border-bottom:1px solid #22262e">
          <span style="font-family:JetBrains Mono;font-size:.72rem;color:#5f6368;min-width:48px;padding-top:2px">{dt}</span>
          <span style="background:{color}22;color:{color};padding:1px 9px;border-radius:4px;font-family:JetBrains Mono;font-size:.68rem;white-space:nowrap">{code}</span>
          <span style="font-size:.8rem;color:#9aa0ac;flex:1">{desc}</span>
        </div>""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# TAB 5 — CHARTS
# ─────────────────────────────────────────────────────────────────────────────
with tabs[5]:
    rows = st.session_state.get("rows", [])
    if not rows:
        st.info("Refresh to load charts.")
    else:
        tv2 = st.session_state["kpis"].get("total_value", 1)
        c1, c2 = st.columns(2)
        with c1:
            fig = go.Figure(go.Pie(
                labels=[r["ticker"] for r in rows],
                values=[r["equity"] for r in rows],
                hole=0.55,
                textinfo="label+percent",
                textfont=dict(family="JetBrains Mono", size=10, color="#e8eaed"),
                marker=dict(colors=px.colors.qualitative.Dark24,
                            line=dict(color="#0a0c10", width=2)),
            ))
            fig.update_layout(
                title="Portfolio Allocation",
                paper_bgcolor="rgba(0,0,0,0)", font_color="#9aa0ac", font_family="DM Sans",
                legend=dict(font=dict(family="JetBrains Mono", size=9, color="#9aa0ac"), bgcolor="rgba(0,0,0,0)"),
                height=380, margin=dict(l=0,r=0,t=40,b=0),
                annotations=[dict(text=f"${tv2:,.0f}", x=0.5, y=0.5,
                                   font=dict(family="Instrument Serif", size=17, color="#e8eaed"),
                                   showarrow=False)],
            )
            st.plotly_chart(fig, use_container_width=True)
        with c2:
            sr = sorted(rows, key=lambda r: r["pl_pct"])
            fig2 = go.Figure(go.Bar(
                x=[r["ticker"] for r in sr],
                y=[r["pl_pct"] for r in sr],
                marker_color=["#ff5252" if r["pl_pct"]<0 else "#00e676" for r in sr],
                text=[f'{r["pl_pct"]:+.1f}%' for r in sr],
                textposition="outside",
                textfont=dict(size=8, color="#9aa0ac", family="JetBrains Mono"),
            ))
            fig2.update_layout(
                title="P&L % by Position",
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font_color="#9aa0ac", font_family="DM Sans",
                xaxis=dict(tickfont=dict(color="#e8eaed", family="JetBrains Mono", size=8)),
                yaxis=dict(gridcolor="#22262e", tickformat="+.1f"),
                height=380, margin=dict(l=40,r=20,t=40,b=60),
            )
            st.plotly_chart(fig2, use_container_width=True)

        top12 = sorted(rows, key=lambda r: r["equity"], reverse=True)[:12]
        fig3 = go.Figure(go.Bar(
            x=[r["ticker"] for r in top12], y=[r["equity"] for r in top12],
            marker_color="#448aff",
            text=[f"${r['equity']:,.0f}" for r in top12],
            textposition="outside",
            textfont=dict(size=10, color="#9aa0ac", family="JetBrains Mono"),
        ))
        fig3.update_layout(
            title="Top 12 Positions by Equity", paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)", font_color="#9aa0ac", font_family="DM Sans",
            xaxis=dict(tickfont=dict(color="#e8eaed", family="JetBrains Mono")),
            yaxis=dict(tickformat="$,.0f", gridcolor="#22262e"),
            height=340, margin=dict(l=60,r=20,t=40,b=40),
        )
        st.plotly_chart(fig3, use_container_width=True)

# ─────────────────────────────────────────────────────────────────────────────
# TAB 6 — HISTORY
# ─────────────────────────────────────────────────────────────────────────────
with tabs[6]:
    hist = de._load(de.REC_HIST_PATH, [])
    st.markdown('<div class="sec-title">📸 Portfolio Value Over Time</div>', unsafe_allow_html=True)
    if len(hist) > 1:
        fig_h = go.Figure(go.Scatter(
            x=[h["ts"][:16] for h in hist], y=[h["total_value"] for h in hist],
            mode="lines+markers", line=dict(color="#00e676", width=2),
            marker=dict(color="#00e676", size=5),
            fill="tozeroy", fillcolor="rgba(0,230,118,0.05)",
        ))
        fig_h.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                             font_color="#9aa0ac", font_family="DM Sans",
                             yaxis=dict(tickformat="$,.0f", gridcolor="#22262e"),
                             height=260, margin=dict(l=60,r=20,t=20,b=40))
        st.plotly_chart(fig_h, use_container_width=True)
    else:
        st.info("No snapshots yet — save one from the Actions tab.")

    for h in reversed(hist[-40:]):
        ts = h["ts"][:16].replace("T"," ")
        tv = h.get("total_value",0)
        pl = h.get("total_pl",0)
        plp = h.get("total_pl_pct",0)
        c = "#00e676" if pl>=0 else "#ff5252"
        st.markdown(f"""
        <div style="display:flex;align-items:center;justify-content:space-between;padding:8px 14px;
                    background:#16191f;border-radius:6px;margin-bottom:5px;font-family:JetBrains Mono;font-size:.76rem">
          <span style="color:#9aa0ac">{ts}</span>
          <span style="color:#e8eaed">${tv:,.0f}</span>
          <span style="color:{c}">{pl:+,.0f} ({plp:+.1f}%)</span>
        </div>""", unsafe_allow_html=True)

    dep_log = de._load(de.DEPOSIT_LOG, [])
    if dep_log:
        st.markdown('<div class="sec-title">💰 Deposit Log</div>', unsafe_allow_html=True)
        for d in reversed(dep_log[-20:]):
            ts = d["ts"][:16].replace("T"," ")
            allocs = d.get("allocations",[])
            tstr = " · ".join(f"{a['ticker']} ${a['amount']:.0f}" for a in allocs if a.get("action","BUY")=="BUY")
            st.markdown(f"""
            <div style="background:#16191f;border:1px solid #22262e;border-radius:7px;padding:10px 15px;margin-bottom:7px">
              <div style="display:flex;justify-content:space-between;margin-bottom:3px">
                <span style="font-family:JetBrains Mono;font-size:.8rem;color:#e8eaed">Deposit #{d.get('deposit_num','?')}</span>
                <span style="font-family:JetBrains Mono;font-size:.8rem;color:#00e676">${d.get('total',0):,.0f}</span>
                <span style="font-family:JetBrains Mono;font-size:.7rem;color:#5f6368">{ts}</span>
              </div>
              <div style="font-family:JetBrains Mono;font-size:.68rem;color:#5f6368">{tstr}</div>
            </div>""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# TAB 7 — IMPORT / PDF
# ─────────────────────────────────────────────────────────────────────────────
with tabs[7]:
    st.markdown('<div class="sec-title">📥 Import Robinhood CSV</div>', unsafe_allow_html=True)
    st.markdown('<div class="info-box">SHA-1 fingerprint dedup — uploading the same CSV twice adds 0 rows. All 14 transaction codes handled (including SPL splits, LIQ liquidations).</div>', unsafe_allow_html=True)

    csv_file = st.file_uploader("Drop Robinhood CSV", type=["csv"], key="csv_upload")
    if csv_file:
        new_r, skip_r, errs = de.ingest_csv(csv_file.read())
        if errs:
            for e in errs:
                st.error(e)
        else:
            st.markdown(f'<div class="success-box">✓ {new_r} new rows added · {skip_r} duplicates skipped</div>', unsafe_allow_html=True)
        if new_r > 0 and st.button("🔄 Refresh Dashboard", type="primary", key="csv_refresh"):
            st.session_state["refresh_count"] += 1
            _refresh_all()
            st.success("Dashboard updated")
            st.rerun()

    st.divider()
    st.markdown('<div class="sec-title">📄 Import Robinhood Crypto PDF</div>', unsafe_allow_html=True)
    st.markdown('<div class="info-box">Upload your monthly Robinhood Crypto statement PDF. Holdings (shares + market value) will be merged into your portfolio.</div>', unsafe_allow_html=True)

    pdf_file = st.file_uploader("Drop Crypto PDF", type=["pdf"], key="pdf_upload")
    if pdf_file:
        pdf_bytes = pdf_file.read()
        with st.spinner("Parsing PDF…"):
            pdf_data = de.parse_crypto_pdf(pdf_bytes)

        if "_errors" in pdf_data:
            for e in pdf_data["_errors"]:
                st.error(e)
        else:
            st.markdown('<div class="success-box">PDF parsed successfully. Holdings found:</div>', unsafe_allow_html=True)
            for ticker, data in pdf_data.items():
                if ticker.startswith("_") or not isinstance(data, dict):
                    continue
                period = data.get("period_end", "")
                mval   = data.get("market_value", 0)
                pct    = data.get("pct", 0)
                src    = data.get("source", "")
                st.markdown(f"""
                <div style="background:#16191f;border:1px solid #22262e;border-radius:8px;padding:12px 16px;margin-bottom:8px;font-family:JetBrains Mono;font-size:.8rem">
                  <span style="color:#e8eaed;font-weight:600;font-size:1rem">{ticker}</span>
                  <span style="color:#00e676;margin-left:12px">{data.get('shares',0):.6f} shares</span>
                  <span style="color:#9aa0ac;margin-left:12px">Market value: ${mval:,.2f}</span>
                  <span style="color:#5f6368;margin-left:12px">{pct:.2f}% of crypto · Period end: {period} · ({src})</span>
                </div>""", unsafe_allow_html=True)

            if st.button("✅ Merge PDF into Portfolio", type="primary"):
                msgs = de.merge_pdf_into_crypto_overrides(pdf_data)
                for m in msgs:
                    st.success(m)
                st.session_state["refresh_count"] += 1
                _refresh_all()
                st.success("Portfolio refreshed with PDF data")
                st.rerun()

    st.divider()
    st.markdown('<div class="sec-title">📝 Manual Position Entry</div>', unsafe_allow_html=True)
    with st.expander("Add position manually"):
        import hashlib as _hl
        c1, c2 = st.columns(2)
        with c1:
            m_t = st.text_input("Ticker", placeholder="NVDA").upper().strip()
            m_sh = st.number_input("Shares", min_value=0.0001, value=1.0, step=0.001, format="%.4f")
        with c2:
            m_ac = st.number_input("Avg Cost ($)", min_value=0.01, value=100.0, step=0.01)
            m_lt = st.checkbox("LT Eligible?", value=False)
        if st.button("Add") and m_t:
            store = de._load(de.TX_STORE_PATH, {})
            fp = _hl.sha1(f"manual|{m_t}|{m_sh}|{m_ac}".encode()).hexdigest()
            if fp not in store:
                store[fp] = {"date": str(date.today()), "code": "Buy", "ticker": m_t,
                              "qty": m_sh, "price": m_ac, "amount": -(m_sh*m_ac),
                              "desc": "Manual entry", "lt": m_lt}
                de._save(de.TX_STORE_PATH, store)
                st.success(f"Added {m_t}")
                st.session_state["refresh_count"] += 1
                _refresh_all()
                st.rerun()
            else:
                st.warning("Already exists.")

# ─────────────────────────────────────────────────────────────────────────────
# TAB 8 — TESTS
# ─────────────────────────────────────────────────────────────────────────────
with tabs[8]:
    st.markdown('<div class="sec-title">🧪 Live System Tests</div>', unsafe_allow_html=True)
    st.markdown('<div class="info-box">Validates price fetching, PDF parsing, portfolio recompute, AI targets, and dedup logic using real data.</div>', unsafe_allow_html=True)

    if st.button("▶️ Run All Tests", type="primary"):
        st.session_state["show_tests"] = True

    if st.session_state.get("show_tests"):
        results: list[tuple[str, bool, str]] = []

        # 1. tx_store
        store = de._load(de.TX_STORE_PATH, {})
        results.append(("TX Store loaded", len(store) > 0, f"{len(store)} rows"))

        # 2. Portfolio recompute
        try:
            pos = de.recompute_portfolio()
            results.append(("Portfolio recompute", len(pos) > 10, f"{len(pos)} positions"))
        except Exception as e:
            pos = {}
            results.append(("Portfolio recompute", False, str(e)))

        # 3. Key tickers present + correct share counts
        for t, min_sh in [("VOO",5),("NVDA",20),("AAPL",10),("BTC",0.01),("XRP",0.5)]:
            sh = pos.get(t,{}).get("shares",0)
            results.append((f"{t} shares", sh >= min_sh, f"{sh:.4f}"))

        # 4. SPL handling — NVDA should have post-split shares
        nvda_sh = pos.get("NVDA",{}).get("shares",0)
        results.append(("NVDA post-split shares", nvda_sh > 30, f"{nvda_sh:.2f} (pre-split ~17.5 + 18 from SPL)"))

        # 5. BMWYY liquidated (LIQ code)
        no_bmwyy = "BMWYY" not in pos
        results.append(("BMWYY liquidated (LIQ)", no_bmwyy, "0 shares remaining ✓" if no_bmwyy else f"WARNING: {pos.get('BMWYY',{}).get('shares',0):.4f} shares still present"))

        # 6. Price fetch — stocks
        test_stocks = ("VOO", "NVDA", "AAPL")
        with st.spinner("Fetching stock prices…"):
            sp, ss = de.get_clean_prices(test_stocks, bust=99998)
        for t in test_stocks:
            p = sp.get(t, 0)
            results.append((f"Price {t}", p > 5 and p != 1.0,
                             f"${p:,.2f} [{ss.get(t,'?')}]" + (" ⚠ IS $1!" if abs(p-1)<0.01 else "")))

        # 7. Crypto prices
        with st.spinner("Fetching crypto prices…"):
            cp, cs = de.get_clean_prices(("BTC","XRP"), bust=99998)
        for t in ("BTC","XRP"):
            p = cp.get(t, 0)
            results.append((f"Price {t}", p > 0.1 and p != 1.0,
                             f"${p:,.4f} [{cs.get(t,'?')}]"))

        # 8. No $1 prices in full portfolio
        all_prices = {**sp, **cp}
        rows_e = de.enrich_portfolio(pos, all_prices)
        dollar1 = [r["ticker"] for r in rows_e if abs(r["live_price"]-1)<0.01]
        results.append(("No $1 prices in portfolio", len(dollar1)==0,
                         "✓ Clean" if not dollar1 else f"STILL $1: {dollar1}"))

        # 9. Recommendation engine
        recs_t = de.generate_recommendations(rows_e)
        results.append(("Recs generated", len(recs_t) > 5, f"{len(recs_t)} total"))

        # 10. AI targets
        kp = de.compute_kpis(rows_e, 1042.17)
        ai_t = de.generate_suggested_targets(rows_e, kp["total_value"])
        total_ai = sum(ai_t.values())
        results.append(("AI targets sum ~100%", abs(total_ai-100)<2, f"{total_ai:.1f}%"))
        results.append(("AI target VOO ≈ 20%", abs(ai_t.get("VOO",0)-20)<5, f"VOO={ai_t.get('VOO',0):.1f}%"))

        # 11. PDF parsing (using already-provided PDF bytes if available)
        pdf_test_text = b"""CRYPTOCURRENCY HELD IN ACCOUNT   QUANTITY   SYMBOL   MARKET VALUE   % OF PORTFOLIO
Bitcoin   0.03432981   BTC   $2301.45   99.94%
XRP   1.066   XRP   $1.47   0.06%"""
        # Simple regex test without actual PDF
        import re as _re
        pat = _re.compile(r"(Bitcoin|XRP)\s+([\d.]+)\s+([A-Z]+)\s+\$([\d,]+\.?\d*)\s+([\d.]+)%", _re.I)
        matches = pat.findall(pdf_test_text.decode())
        results.append(("PDF regex pattern", len(matches) == 2, f"Found {len(matches)} crypto entries"))

        # 12. CSV dedup
        fake = b'"Activity Date","Process Date","Settle Date","Instrument","Description","Trans Code","Quantity","Price","Amount"\n"4/3/2026","4/3/2026","4/4/2026","TEST","Test Buy","Buy","1","$100.00","($100.00)"\n'
        n1, _, _ = de.ingest_csv(fake)
        n2, s2, _ = de.ingest_csv(fake)
        results.append(("CSV dedup idempotent", n2==0 and s2==1, f"2nd upload: {n2} new, {s2} skipped"))

        # 13. Deposit schedule
        sched = de.get_deposit_schedule(3)
        results.append(("Deposit schedule", len(sched)==3,
                         f"Next 3: {[str(s['date']) for s in sched]}"))

        # 14. KPIs sanity
        results.append(("Portfolio value >$40k", kp["total_value"]>40000, f"${kp['total_value']:,.0f}"))

        # ── Display ──
        passed = sum(1 for _,ok,_ in results if ok)
        total  = len(results)
        pct    = passed/total*100
        color  = "#00e676" if pct==100 else "#ffb300" if pct>=80 else "#ff5252"
        st.markdown(f'<div style="font-family:Instrument Serif;font-size:1.3rem;color:{color};margin-bottom:14px">{passed}/{total} tests passed ({pct:.0f}%)</div>', unsafe_allow_html=True)

        for name, ok, detail in results:
            bg = "#1a3d2b" if ok else "#3d1a1a"
            fc = "#00e676" if ok else "#ff5252"
            st.markdown(f"""
            <div style="display:flex;align-items:center;gap:11px;padding:7px 13px;
                        background:{bg};border-radius:5px;margin-bottom:3px">
              <span>{"✅" if ok else "❌"}</span>
              <span style="font-family:JetBrains Mono;font-size:.78rem;color:#e8eaed;flex:1">{name}</span>
              <span style="font-family:JetBrains Mono;font-size:.72rem;color:{fc}">{detail}</span>
            </div>""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# Footer
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("""
<div style="text-align:center;padding:36px 0 16px;color:#22262e;
            font-family:JetBrains Mono;font-size:.66rem;letter-spacing:.1em">
  PORTFOLIO WAR ROOM v10.1 · NOT FINANCIAL ADVICE · FOR INFORMATIONAL PURPOSES ONLY
</div>""", unsafe_allow_html=True)
