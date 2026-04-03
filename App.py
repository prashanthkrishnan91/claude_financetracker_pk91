"""
main_app.py — Portfolio War Room v11.0
All UI — zero business logic.
Wired to: data_engine.py (all logic) + price_service.py + plaid_client.py + portfolio_aggregator.py

Tabs:
  0 Actions     — Dynamic buy/sell/trim/hold cards
  1 Portfolio   — Holdings table + Plaid sync status
  2 Rebalancing — Drift vs targets chart
  3 Invest $900 — Biweekly deposit allocation
  4 Schedule    — Full 2026 action calendar
  5 Charts      — Allocation pie + P&L bar
  6 History     — Saved snapshots
  7 Import      — CSV + crypto PDF upload
  8 Tests       — Live system health checks
"""

import datetime
import json
import os
import time

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

import data_engine as de

# ═══════════════════════════════════════════════════════════════════════════════
# PAGE CONFIG
# ═══════════════════════════════════════════════════════════════════════════════
st.set_page_config(
    page_title="Portfolio War Room",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ═══════════════════════════════════════════════════════════════════════════════
# GLOBAL CSS
# ═══════════════════════════════════════════════════════════════════════════════
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Serif+Display:ital@0;1&family=JetBrains+Mono:wght@400;600&family=DM+Sans:wght@300;400;500;600&display=swap');

html,body,[class*="css"]{font-family:'DM Sans',sans-serif;background:#07090f;color:#e2e8f0}
.stApp{background:#07090f}
#MainMenu,footer,header{visibility:hidden}
.block-container{padding:1.2rem 1.4rem 3rem;max-width:1440px}
h1,h2,h3{font-family:'DM Serif Display',serif;letter-spacing:-0.02em}
code,.mono{font-family:'JetBrains Mono',monospace;font-size:12px}

/* KPI cards */
.kpi{background:linear-gradient(135deg,#0f1623 0%,#151f32 100%);border:1px solid #1e2d47;
  border-radius:14px;padding:18px 20px 14px;margin-bottom:10px}
.kpi-label{font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:.08em}
.kpi-value{font-size:26px;font-weight:700;letter-spacing:-0.03em;margin:4px 0}
.kpi-sub{font-size:12px;color:#94a3b8}

/* Rec cards */
.rec-card{border-radius:12px;padding:14px 16px 10px;margin-bottom:8px;
  border-left:4px solid #334155}
.rec-sell{border-color:#ef4444;background:#1a0a0a}
.rec-buy{border-color:#22c55e;background:#07150c}
.rec-trim{border-color:#f59e0b;background:#140f04}
.rec-hold{border-color:#334155;background:#0d111a}
.rec-review{border-color:#a855f7;background:#120a1a}

.tag{display:inline-block;padding:2px 8px;border-radius:6px;font-size:11px;font-weight:600;
  font-family:'JetBrains Mono',monospace;margin-right:6px}
.tag-sell{background:#450a0a;color:#fca5a5}
.tag-buy{background:#052e16;color:#86efac}
.tag-trim{background:#451a03;color:#fcd34d}
.tag-hold{background:#0f172a;color:#64748b}
.tag-review{background:#2e1065;color:#d8b4fe}
.tag-plaid{background:#0c1a3d;color:#93c5fd}

/* Table */
.st-dataframe{border-radius:10px!important}

/* Tab underline style */
.stTabs [data-baseweb="tab-list"]{gap:0;border-bottom:1px solid #1e2d47}
.stTabs [data-baseweb="tab"]{padding:10px 18px;font-size:13px;font-weight:500;color:#64748b;
  border-bottom:2px solid transparent;background:transparent}
.stTabs [aria-selected="true"]{color:#38bdf8;border-bottom:2px solid #38bdf8}

/* Price source badge */
.src-live{color:#22c55e;font-size:10px}
.src-cache{color:#f59e0b;font-size:10px}
.src-stale{color:#ef4444;font-size:10px}
</style>
""", unsafe_allow_html=True)

# ═══════════════════════════════════════════════════════════════════════════════
# BOOTSTRAP + SESSION STATE INIT
# ═══════════════════════════════════════════════════════════════════════════════
de._bootstrap()

def _init():
    if "bust" not in st.session_state:
        st.session_state.bust = 0
    if "prices" not in st.session_state:
        st.session_state.prices = {}
    if "portfolio" not in st.session_state:
        tx   = de._load(de.TX_STORE_PATH, {})
        cryp = de._load(de.CRYPTO_OVR_PATH, {})
        st.session_state.portfolio = de.recompute_portfolio(tx, cryp)
    if "cash" not in st.session_state:
        st.session_state.cash = float(de.ROBINHOOD_CASH_DEFAULT)
    if "targets" not in st.session_state:
        st.session_state.targets = de._load(de.TARGETS_PATH, {})
    if "recs" not in st.session_state:
        st.session_state.recs = []
    if "processed_ids" not in st.session_state:
        st.session_state.processed_ids = set()
    if "plaid_snap" not in st.session_state:
        st.session_state.plaid_snap = de._load(de.PLAID_SNAPSHOT_PATH, None)
    if "deposit_num" not in st.session_state:
        log = de._load(de.DEPOSIT_LOG_PATH, [])
        st.session_state.deposit_num = len(log) + 1

_init()

portfolio = st.session_state.portfolio
prices    = st.session_state.prices
cash      = st.session_state.cash
targets   = st.session_state.targets
plaid_snap = st.session_state.plaid_snap

# ═══════════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ═══════════════════════════════════════════════════════════════════════════════
with st.sidebar:
    st.markdown("## ⚡ Portfolio War Room")
    st.markdown(f"<div style='font-size:11px;color:#64748b'>{datetime.date.today().strftime('%A, %B %d, %Y')}</div>", unsafe_allow_html=True)
    st.markdown("---")

    # ── Refresh buttons ───────────────────────────────────────────────────────
    col1, col2 = st.columns(2)
    with col1:
        if st.button("🔄 Refresh", use_container_width=True, help="Fetch live prices (Finnhub/Polygon/CoinGecko)"):
            st.session_state.bust += 1
            tickers = tuple(sorted(portfolio.keys()))
            with st.spinner("Fetching live prices…"):
                st.session_state.prices = de.fetch_prices(tickers, _bust=st.session_state.bust)
            prices = st.session_state.prices
            st.session_state.recs = de.generate_recs(portfolio, prices)
            st.rerun()
    with col2:
        plaid_configured = bool(os.environ.get("PLAID_ACCESS_TOKEN") or
                                 (hasattr(st, "secrets") and "PLAID_ACCESS_TOKEN" in st.secrets))
        if st.button("🏦 Sync Plaid", use_container_width=True,
                     disabled=not plaid_configured,
                     help="Sync holdings from Plaid (requires PLAID_ACCESS_TOKEN in secrets)"):
            with st.spinner("Syncing with Plaid…"):
                snap = de.sync_live_portfolio(bust=st.session_state.bust)
            if snap:
                st.session_state.plaid_snap = snap
                plaid_snap = snap
                st.success(f"Plaid synced ✅ — ${snap['total_equity']:,.2f}")
            else:
                st.warning("Plaid not configured or sync failed. Set PLAID_ACCESS_TOKEN in Streamlit secrets.")

    if not plaid_configured:
        st.caption("ℹ️ Add PLAID_ACCESS_TOKEN to Streamlit secrets to enable Plaid sync.")

    st.markdown("---")

    # ── Cash balance ──────────────────────────────────────────────────────────
    st.markdown("**💵 Cash Balance**")
    new_cash = st.number_input("Robinhood Cash ($)", value=cash, min_value=0.0, step=10.0, format="%.2f")
    if new_cash != cash:
        st.session_state.cash = new_cash
        cash = new_cash

    st.markdown("---")

    # ── Data health ───────────────────────────────────────────────────────────
    st.markdown("**📡 Data Health**")
    if prices:
        live_count   = sum(1 for p in prices.values() if p and p > 0)
        total_tickers = len(portfolio)
        health_color = "#22c55e" if live_count == total_tickers else ("#f59e0b" if live_count > 0 else "#ef4444")
        health_label = "Live" if live_count == total_tickers else ("Partial" if live_count > 0 else "Stale")
        st.markdown(f"<span style='color:{health_color};font-weight:600'>{health_label}</span> &nbsp; {live_count}/{total_tickers} tickers", unsafe_allow_html=True)
        if plaid_snap:
            ts = plaid_snap.get("timestamp", "")
            try:
                ts_dt = datetime.datetime.fromisoformat(ts)
                age = (datetime.datetime.now() - ts_dt).seconds // 60
                st.markdown(f"<span class='tag tag-plaid'>Plaid</span> synced {age}m ago", unsafe_allow_html=True)
            except Exception:
                st.markdown(f"<span class='tag tag-plaid'>Plaid</span> synced", unsafe_allow_html=True)
    else:
        st.markdown("<span style='color:#64748b'>Press 🔄 Refresh to load live prices</span>", unsafe_allow_html=True)

    st.markdown("---")

    # ── Reconciliation summary ────────────────────────────────────────────────
    st.markdown("**🗂️ Reconciliation**")
    tx_count = len(de._load(de.TX_STORE_PATH, {}))
    st.markdown(f"<code>{tx_count}</code> rows in tx_store", unsafe_allow_html=True)
    recon = de._load(de.RECON_LOG_PATH, [])
    if recon:
        last = recon[-1]
        with st.expander("Last upload detail"):
            st.write(f"**{last.get('new', 0)}** new rows added")
            st.write(f"**{last.get('dupes', 0)}** duplicates skipped")
            st.write(f"Total in file: **{last.get('total_rows', 0)}**")

    st.markdown("---")

    # ── AI Target Engine ──────────────────────────────────────────────────────
    st.markdown("**🤖 AI Target Weights**")
    if st.button("✨ Generate AI Targets", use_container_width=True):
        suggested = de.generate_suggested_targets(portfolio)
        # Pre-populate number_inputs via session_state
        for t, w in suggested.items():
            st.session_state[f"target_{t}"] = w
        st.session_state.targets = suggested
        de._save(de.TARGETS_PATH, suggested)
        targets = suggested
        st.success("AI targets generated ✅")

    if targets:
        with st.expander(f"Edit targets ({len(targets)} tickers)"):
            new_targets = {}
            for t in sorted(targets.keys()):
                new_targets[t] = st.number_input(
                    t, value=float(targets.get(t, 0)),
                    min_value=0.0, max_value=100.0, step=0.5,
                    key=f"target_{t}"
                )
            col_s, col_r = st.columns(2)
            with col_s:
                if st.button("💾 Save", use_container_width=True):
                    st.session_state.targets = new_targets
                    de._save(de.TARGETS_PATH, new_targets)
                    targets = new_targets
                    st.success("Saved")
            with col_r:
                if st.button("🔁 Reset AI", use_container_width=True):
                    suggested = de.generate_suggested_targets(portfolio)
                    st.session_state.targets = suggested
                    de._save(de.TARGETS_PATH, suggested)
                    targets = suggested
                    st.rerun()

# ═══════════════════════════════════════════════════════════════════════════════
# HEADER
# ═══════════════════════════════════════════════════════════════════════════════
totals = de.portfolio_totals(portfolio, prices, cash)

# If Plaid snapshot available and fresher, use its total_equity for the header
if plaid_snap and plaid_snap.get("total_equity", 0) > 0:
    plaid_total   = plaid_snap["total_equity"]
    plaid_stocks  = plaid_snap.get("stocks_equity", 0)
    plaid_crypto  = plaid_snap.get("crypto_equity", 0)
    plaid_cash    = plaid_snap.get("cash_usd", cash)
else:
    plaid_total = plaid_stocks = plaid_crypto = None

st.markdown("<h1 style='margin-bottom:2px'>⚡ Portfolio War Room</h1>", unsafe_allow_html=True)
st.markdown(f"<div style='color:#64748b;font-size:13px;margin-bottom:18px'>{len(portfolio)} positions · v11.0 · Plaid + Finnhub real-time engine</div>", unsafe_allow_html=True)

# ── KPI cards ─────────────────────────────────────────────────────────────────
display_total   = plaid_total  if plaid_total  else totals["total"]
display_stocks  = plaid_stocks if plaid_stocks else totals["stocks"]
display_crypto  = plaid_snap["crypto_equity"] if plaid_snap else totals["crypto"]
pnl_color       = "#22c55e" if totals["pnl"] >= 0 else "#ef4444"
pnl_sign        = "+" if totals["pnl"] >= 0 else ""
source_badge    = '<span class="tag tag-plaid">Plaid</span>' if plaid_total else '<span style="color:#64748b;font-size:10px">est.</span>'

c1, c2, c3, c4, c5 = st.columns(5)
for col, label, value, sub in [
    (c1, "Total Equity",      f"${display_total:,.2f}",  source_badge),
    (c2, "Stocks & ETFs",     f"${display_stocks:,.2f}", ""),
    (c3, "Crypto",            f"${display_crypto:,.2f}", ""),
    (c4, "Cash",              f"${cash:,.2f}",            ""),
    (c5, "Unrealised P&L",    f"{pnl_sign}{totals['pnl_pct']:.1f}%", f"{pnl_sign}${abs(totals['pnl']):,.0f}"),
]:
    with col:
        color = pnl_color if label == "Unrealised P&L" else "#e2e8f0"
        st.markdown(f"""<div class='kpi'>
          <div class='kpi-label'>{label}</div>
          <div class='kpi-value' style='color:{color}'>{value}</div>
          <div class='kpi-sub'>{sub}</div>
        </div>""", unsafe_allow_html=True)

st.markdown("---")

# ═══════════════════════════════════════════════════════════════════════════════
# TABS
# ═══════════════════════════════════════════════════════════════════════════════
tabs = st.tabs(["🎯 Actions", "📊 Portfolio", "⚖️ Rebalancing",
                "💰 Invest $900", "📅 Schedule", "📈 Charts",
                "🕐 History", "📥 Import", "🧪 Tests"])

# ─────────────────────────────────────────────────────────────
# TAB 0 — ACTIONS
# ─────────────────────────────────────────────────────────────
with tabs[0]:
    if not prices:
        st.info("👆 Press **🔄 Refresh** in the sidebar to load live prices and generate recommendations.")
    else:
        recs = de.generate_recs(portfolio, prices)
        st.session_state.recs = recs

        # Summary line
        sells  = [r for r in recs if r["cat"] == "sell"]
        buys   = [r for r in recs if r["cat"] == "buy"]
        trims  = [r for r in recs if r["cat"] == "trim"]
        holds  = [r for r in recs if r["cat"] in ("hold",)]
        reviews = [r for r in recs if r["cat"] == "review"]

        s1, s2, s3, s4, s5 = st.columns(5)
        for col, label, items, color in [
            (s1, "SELL",   sells,   "#ef4444"),
            (s2, "BUY",    buys,    "#22c55e"),
            (s3, "TRIM",   trims,   "#f59e0b"),
            (s4, "REVIEW", reviews, "#a855f7"),
            (s5, "HOLD",   holds,   "#64748b"),
        ]:
            with col:
                st.markdown(f"<div style='text-align:center;padding:8px;border-radius:10px;"
                            f"background:#0f172a;border:1px solid #1e2d47'>"
                            f"<div style='color:{color};font-weight:700;font-size:20px'>{len(items)}</div>"
                            f"<div style='font-size:10px;color:#64748b'>{label}</div></div>",
                            unsafe_allow_html=True)
        st.markdown("")

        def _rcard(r: dict):
            cat   = r["cat"]
            css   = {"sell":"rec-sell","buy":"rec-buy","trim":"rec-trim",
                     "hold":"rec-hold","review":"rec-review"}.get(cat, "rec-hold")
            tag   = {"sell":"tag-sell","buy":"tag-buy","trim":"tag-trim",
                     "hold":"tag-hold","review":"tag-review"}.get(cat, "tag-hold")
            pnl_c = "#22c55e" if r["pnl_pct"] >= 0 else "#ef4444"
            proc  = f" · Est. proceeds: <b>${r['proceeds']:,.0f}</b>" if r["proceeds"] > 0 else ""
            src   = prices.get(r["ticker"])
            price_note = f"${r['price']:,.2f}" if src else f"${r['cost']:,.2f} (cost)"
            st.markdown(f"""<div class='rec-card {css}'>
              <span class='tag {tag}'>{cat.upper()}</span>
              <b style='font-size:15px'>{r['ticker']}</b>
              <span style='color:#64748b;font-size:12px'> · {r['shares']:.4f} sh · {price_note} · {r['category']}</span>
              <br/>
              <span style='font-size:14px;font-weight:600'>{r['action']}</span>
              <span style='color:{pnl_c};font-size:12px'> · {r['pnl_pct']:+.1f}%{proc}</span>
              <br/>
              <span style='color:#94a3b8;font-size:12px'>📝 {r['plain']}</span>
              <br/>
              <span style='color:#64748b;font-size:11px'>💡 {r['why']} · {r['tax']}</span>
            </div>""", unsafe_allow_html=True)

        for group, label in [(sells, "🔴 Sell Now"), (buys, "🟢 Buy / Accumulate"),
                              (trims, "🟡 Trim"), (reviews, "🟣 Review"),
                              (holds, "⚫ Hold")]:
            if group:
                st.markdown(f"#### {label}")
                for r in group:
                    _rcard(r)

        if st.button("📸 Save Snapshot", key="snap_btn"):
            de.snapshot_portfolio(portfolio, prices, cash, recs)
            st.success("Snapshot saved to history ✅")

# ─────────────────────────────────────────────────────────────
# TAB 1 — PORTFOLIO
# ─────────────────────────────────────────────────────────────
with tabs[1]:
    st.markdown("### Holdings")

    # Show Plaid position data if available
    if plaid_snap and plaid_snap.get("positions"):
        st.markdown(f"<span class='tag tag-plaid'>Plaid</span> **{len(plaid_snap['positions'])} positions** · synced · quantities are authoritative", unsafe_allow_html=True)
        rows = []
        for pos in plaid_snap["positions"]:
            src_class = "src-live" if "cache" not in pos.get("price_source","") else "src-cache"
            rows.append({
                "Ticker":        pos["ticker"],
                "Shares":        round(pos["quantity"], 6),
                "Mid Price":     f"${pos['mid_price']:,.2f}",
                "Market Value":  round(pos["market_value"], 2),
                "Avg Cost":      round(pos["avg_cost_basis"], 2),
                "Unreal P&L":    round(pos["unrealised_pnl"], 2),
                "P&L %":         f"{pos['unrealised_pct']:+.1f}%",
                "Source":        pos.get("price_source", "?"),
                "Type":          pos.get("security_type", ""),
            })
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, height=500)

    else:
        # tx_store computed portfolio + fetched prices
        rows = []
        for ticker, pos in sorted(portfolio.items(), key=lambda x: -de._safe_price(x[0], x[1], prices) * x[1]["shares"]):
            p     = de._safe_price(ticker, pos, prices)
            mkt   = p * pos["shares"]
            cost  = pos["avg_cost"]
            pnl   = (p - cost) / cost * 100 if cost > 0 else 0
            lt    = de.is_lt_eligible(pos.get("first_buy_date", ""))
            rows.append({
                "Ticker":       ticker,
                "Shares":       round(pos["shares"], 6),
                "Avg Cost":     round(cost, 2),
                "Live Price":   round(p, 2),
                "Market Value": round(mkt, 2),
                "P&L %":        round(pnl, 1),
                "LT?":          "✅" if lt else f"⏳ {de.days_to_lt(pos.get('first_buy_date',''))}d",
                "Category":     pos.get("category", "Stocks"),
            })
        df = pd.DataFrame(rows)

        def _color_pnl(val):
            if isinstance(val, float):
                if val > 0:   return "color: #22c55e"
                if val < 0:   return "color: #ef4444"
            return ""

        styled = df.style.applymap(_color_pnl, subset=["P&L %"])
        st.dataframe(styled, use_container_width=True, height=520,
                     column_config={
                         "Market Value": st.column_config.NumberColumn(format="$%.2f"),
                         "Avg Cost":     st.column_config.NumberColumn(format="$%.2f"),
                         "Live Price":   st.column_config.NumberColumn(format="$%.2f"),
                     })

    st.markdown("---")
    # Per-position detail expanders
    with st.expander("🔍 Position Detail (select ticker)"):
        sel = st.selectbox("Ticker", sorted(portfolio.keys()))
        if sel:
            pos = portfolio[sel]
            p   = de._safe_price(sel, pos, prices)
            mkt = p * pos["shares"]
            pnl = (p - pos["avg_cost"]) / pos["avg_cost"] * 100 if pos["avg_cost"] > 0 else 0
            st.metric("Live Price",    f"${p:,.4f}")
            st.metric("Market Value",  f"${mkt:,.2f}")
            st.metric("P&L",           f"{pnl:+.1f}%", delta=f"{pnl:+.1f}%")
            st.metric("Avg Cost",      f"${pos['avg_cost']:,.4f}")
            st.metric("Shares",        f"{pos['shares']:.6f}")
            st.metric("LT Eligible?",  "Yes ✅" if de.is_lt_eligible(pos.get("first_buy_date","")) else
                                       f"No — {de.days_to_lt(pos.get('first_buy_date',''))} days left")
            target = de.TARGETS.get(sel)
            if target:
                upside = (target - p) / p * 100 if p > 0 else 0
                st.metric("Analyst Target", f"${target:,.0f}", delta=f"{upside:+.0f}% upside")

# ─────────────────────────────────────────────────────────────
# TAB 2 — REBALANCING
# ─────────────────────────────────────────────────────────────
with tabs[2]:
    if not targets:
        st.info("No targets set yet. Click **✨ Generate AI Targets** in the sidebar to get AI-suggested weights.")
    elif not prices:
        st.info("Press 🔄 Refresh to load prices first.")
    else:
        rebal = de.compute_rebalancing(portfolio, prices, targets)
        st.markdown("### Portfolio Drift vs Targets")
        st.caption("Green bars = underweight (buy more). Red bars = overweight (trim).")

        colors = ["#22c55e" if r["drift"] < 0 else "#ef4444" for r in rebal]
        fig = go.Figure(go.Bar(
            x=[r["drift"] for r in rebal],
            y=[r["ticker"] for r in rebal],
            orientation="h",
            marker_color=colors,
            text=[f"{r['action']} ({r['drift']:+.1f}%)" for r in rebal],
            textposition="outside",
        ))
        fig.update_layout(
            template="plotly_dark", paper_bgcolor="#07090f", plot_bgcolor="#07090f",
            height=max(300, len(rebal) * 28),
            xaxis_title="Drift (Current % − Target %)",
            yaxis_title="",
            margin=dict(l=80, r=40, t=20, b=40),
            font=dict(family="DM Sans", size=12),
        )
        st.plotly_chart(fig, use_container_width=True)

        rebal_df = pd.DataFrame(rebal)[["ticker","current_pct","target_pct","drift","action","market_value"]]
        rebal_df.columns = ["Ticker","Current %","Target %","Drift %","Action","Market Value"]
        st.dataframe(rebal_df, use_container_width=True,
                     column_config={"Market Value": st.column_config.NumberColumn(format="$%.0f")})

# ─────────────────────────────────────────────────────────────
# TAB 3 — INVEST $900
# ─────────────────────────────────────────────────────────────
with tabs[3]:
    dep_num = st.session_state.deposit_num
    start_date = datetime.date(2026, 4, 3)
    fridays = de.get_biweekly_dates(start_date, n=18)
    today = datetime.date.today()

    # Current deposit date
    current_idx = 0
    for i, d in enumerate(fridays):
        if d >= today:
            current_idx = i
            break
    current_date = fridays[current_idx] if current_idx < len(fridays) else fridays[-1]

    st.markdown(f"### 💰 Deposit #{dep_num} — {current_date.strftime('%B %d, %Y')}")
    if not prices:
        st.info("Press 🔄 Refresh to load live prices for accurate share estimates.")

    dep_recs = de.generate_deposit_recs(dep_num, portfolio, prices, targets, amount=900.0)

    # Display as clean table
    dep_df = pd.DataFrame(dep_recs)
    dep_df.columns = [c.title() for c in dep_df.columns]
    st.dataframe(dep_df, use_container_width=True,
                 column_config={
                     "Amount":     st.column_config.NumberColumn(format="$%.2f"),
                     "Price":      st.column_config.NumberColumn(format="$%.2f"),
                     "Est_Shares": st.column_config.NumberColumn(format="%.4f"),
                 })

    col_tot, col_btn = st.columns([3, 1])
    with col_tot:
        st.metric("Total Deposit", "$900.00")
    with col_btn:
        if st.button("✅ Mark Deposit Done", use_container_width=True):
            de.log_deposit(dep_num, str(current_date), dep_recs, 900.0)
            st.session_state.deposit_num += 1
            dep_num = st.session_state.deposit_num
            st.success(f"Deposit #{dep_num - 1} logged! Next: #{dep_num}")
            st.rerun()

    st.markdown("---")
    st.markdown("### 📅 All Deposits — 2026 Schedule")
    rotation_cycle = [de.DEPOSIT_ROTATION[i % len(de.DEPOSIT_ROTATION)] for i in range(len(fridays))]
    sched_data = []
    log = de._load(de.DEPOSIT_LOG_PATH, [])
    logged_nums = {entry["num"] for entry in log}
    for i, (d, pick) in enumerate(zip(fridays, rotation_cycle)):
        num = i + 1
        sched_data.append({
            "#": num,
            "Date": d.strftime("%b %d, %Y"),
            "NVDA ($252)": "✓", "VOO ($198)": "✓", "VYM ($153)": "✓",
            "QQQ ($153)": "✓", "Rotating ($144)": pick,
            "Done": "✅" if num in logged_nums else ("📍 TODAY" if d == today else ""),
        })
    st.dataframe(pd.DataFrame(sched_data), use_container_width=True, hide_index=True)

# ─────────────────────────────────────────────────────────────
# TAB 4 — SCHEDULE
# ─────────────────────────────────────────────────────────────
with tabs[4]:
    st.markdown("### 📅 2026 Action Calendar")
    calendar_events = [
        ("Apr 3",  "SELL",  "VTV, VEA, VWO, BND",   "LT eligible now — pay 15%. Reinvest into VOO+VYM same day."),
        ("Apr 3",  "BUY",   "Deposit #1",            "$900 → NVDA($252), VOO($198), VYM($153), QQQ($153), META($144)"),
        ("Apr 4",  "TRIM",  "GLD",                   "GLD turns LT — trim 25% near $450 target"),
        ("Apr 17", "BUY",   "Deposit #2",            "$900 → NVDA, VOO, VYM, QQQ, GOOGL"),
        ("May 1",  "BUY",   "Deposit #3",            "$900 → NVDA, VOO, VYM, QQQ, AAPL"),
        ("May 20", "SELL",  "SPY",                   "SPY turns LT — sell all, buy VOO same day (no wash sale)"),
        ("Jul 15", "SELL",  "VUG",                   "VUG turns LT — sell all, buy QQQ same day"),
        ("Aug 14", "EVAL",  "BLSH",                  "BLSH hits 1yr — trim 25% if up >20%"),
        ("Sep 11", "EVAL",  "KLAR",                  "KLAR hits 1yr — trim 25% if up >20%"),
        ("Sep 18", "EVAL",  "STUB",                  "STUB hits 1yr — evaluate position"),
        ("Nov 6",  "TRIM",  "TSM",                   "Big TSM lot turns LT — trim 20%"),
        ("Dec 15", "TRIM",  "GOOGL",                 "Big GOOGL lot turns LT — trim 20%"),
        ("Dec 20", "TAX",   "Year-end Harvest",      "Net realized gains vs losses before Dec 31"),
    ]
    tag_map = {"SELL":"tag-sell","BUY":"tag-buy","TRIM":"tag-trim","EVAL":"tag-hold","TAX":"tag-review"}
    for date_s, etype, ticker, notes in calendar_events:
        event_date_str = f"2026-{date_s.replace(' ', '-').replace('Apr','04').replace('May','05').replace('Jul','07').replace('Aug','08').replace('Sep','09').replace('Nov','11').replace('Dec','12')}"
        try:
            event_date = datetime.date.fromisoformat(event_date_str)
            is_past = event_date < today
            opacity = "0.45" if is_past else "1.0"
        except Exception:
            opacity = "1.0"
        tag_css = tag_map.get(etype, "tag-hold")
        past_label = " · <span style='color:#475569'>done</span>" if opacity == "0.45" else ""
        st.markdown(
            f"<div style='opacity:{opacity};margin-bottom:8px;padding:10px 14px;"
            f"border-radius:10px;background:#0d111a;border:1px solid #1e2d47'>"
            f"<span class='tag {tag_css}'>{etype}</span>"
            f"<b>{date_s}</b> &nbsp; {ticker}{past_label}"
            f"<br/><span style='color:#94a3b8;font-size:12px'>{notes}</span>"
            f"</div>", unsafe_allow_html=True
        )

    st.markdown("---")
    st.markdown("### 🧾 Tax Playbook")
    tax_rules = [
        ("Rule #1", "Never sell a position held <1 year", "Pay 37% (ST) vs 15% (LT). The wait is always worth it."),
        ("Rule #2", "ETF swaps are NOT wash sales",       "Selling SPY → buying VOO same day is allowed. Lock gains."),
        ("Rule #3", "DRIP creates new lots",              "Each reinvestment is a new tax lot at that day's price. Track individually."),
        ("Rule #4", "Crypto: never sell short-term",      "BTC/XRP both held >1yr now. LT rate applies."),
        ("Rule #5", "Year-end harvest",                   "Net realized gains vs losses before Dec 31. Offset gains with any losses."),
    ]
    for rule, title, detail in tax_rules:
        st.markdown(
            f"<div style='margin-bottom:8px;padding:10px 14px;border-radius:10px;"
            f"background:#07100d;border-left:3px solid #22c55e'>"
            f"<span style='color:#22c55e;font-size:11px;font-weight:700'>{rule}</span> &nbsp;"
            f"<b>{title}</b><br/>"
            f"<span style='color:#94a3b8;font-size:12px'>{detail}</span>"
            f"</div>", unsafe_allow_html=True
        )

# ─────────────────────────────────────────────────────────────
# TAB 5 — CHARTS
# ─────────────────────────────────────────────────────────────
with tabs[5]:
    if not prices:
        st.info("Press 🔄 Refresh to load live prices for charts.")
    else:
        col_a, col_b = st.columns(2)

        with col_a:
            st.markdown("#### Allocation by Ticker")
            labels = []
            vals   = []
            for ticker, pos in portfolio.items():
                p   = de._safe_price(ticker, pos, prices)
                mkt = p * pos["shares"]
                if mkt > 0:
                    labels.append(ticker)
                    vals.append(mkt)
            fig_pie = px.pie(
                values=vals, names=labels,
                color_discrete_sequence=px.colors.qualitative.Set3,
                hole=0.42,
            )
            fig_pie.update_layout(
                template="plotly_dark", paper_bgcolor="#07090f",
                margin=dict(l=10,r=10,t=20,b=20),
                legend=dict(font=dict(size=10)),
                height=420,
            )
            fig_pie.update_traces(textinfo="label+percent", textfont_size=10)
            st.plotly_chart(fig_pie, use_container_width=True)

        with col_b:
            st.markdown("#### P&L % by Position")
            pnl_data = []
            for ticker, pos in portfolio.items():
                p    = de._safe_price(ticker, pos, prices)
                cost = pos["avg_cost"]
                pnl  = (p - cost) / cost * 100 if cost > 0 else 0
                pnl_data.append({"ticker": ticker, "pnl": pnl})
            pnl_df = pd.DataFrame(pnl_data).sort_values("pnl")
            colors = ["#22c55e" if v >= 0 else "#ef4444" for v in pnl_df["pnl"]]
            fig_bar = go.Figure(go.Bar(
                x=pnl_df["pnl"], y=pnl_df["ticker"],
                orientation="h", marker_color=colors,
                text=[f"{v:+.1f}%" for v in pnl_df["pnl"]],
                textposition="outside",
            ))
            fig_bar.update_layout(
                template="plotly_dark", paper_bgcolor="#07090f", plot_bgcolor="#07090f",
                height=420, margin=dict(l=60,r=60,t=20,b=20),
                xaxis_title="P&L %", yaxis_title="",
                font=dict(family="DM Sans", size=11),
            )
            st.plotly_chart(fig_bar, use_container_width=True)

        # Category breakdown
        st.markdown("#### Allocation by Category")
        cat_data: dict[str, float] = {}
        for ticker, pos in portfolio.items():
            p   = de._safe_price(ticker, pos, prices)
            mkt = p * pos["shares"]
            cat = pos.get("category", "Stocks")
            cat_data[cat] = cat_data.get(cat, 0) + mkt
        cat_df = pd.DataFrame(list(cat_data.items()), columns=["Category","Value"])
        fig_cat = px.bar(cat_df, x="Category", y="Value",
                         color="Category",
                         color_discrete_sequence=["#38bdf8","#22c55e","#f59e0b"],
                         text_auto="$.0f")
        fig_cat.update_layout(template="plotly_dark", paper_bgcolor="#07090f",
                              plot_bgcolor="#07090f", height=280,
                              margin=dict(l=20,r=20,t=20,b=20), showlegend=False)
        st.plotly_chart(fig_cat, use_container_width=True)

# ─────────────────────────────────────────────────────────────
# TAB 6 — HISTORY
# ─────────────────────────────────────────────────────────────
with tabs[6]:
    st.markdown("### 📸 Recommendation History")
    history = de._load(de.REC_HISTORY_PATH, [])
    if not history:
        st.info("No snapshots yet. Click **Save Snapshot** in the Actions tab.")
    else:
        h_df = []
        for snap in reversed(history[-50:]):
            t = snap.get("totals", {})
            h_df.append({
                "Timestamp":     snap.get("timestamp", "")[:16].replace("T", " "),
                "Total Equity":  round(t.get("total", 0), 2),
                "Stocks":        round(t.get("stocks", 0), 2),
                "Crypto":        round(t.get("crypto", 0), 2),
                "P&L %":         round(t.get("pnl_pct", 0), 2),
                "# Recs":        len(snap.get("recs", [])),
            })
        st.dataframe(pd.DataFrame(h_df), use_container_width=True,
                     column_config={
                         "Total Equity": st.column_config.NumberColumn(format="$%.2f"),
                         "Stocks":       st.column_config.NumberColumn(format="$%.2f"),
                         "Crypto":       st.column_config.NumberColumn(format="$%.2f"),
                     })

        # Equity over time line chart
        if len(h_df) > 1:
            ts_df = pd.DataFrame(h_df)[["Timestamp", "Total Equity"]]
            fig_line = px.line(ts_df, x="Timestamp", y="Total Equity",
                               markers=True, line_shape="spline",
                               color_discrete_sequence=["#38bdf8"])
            fig_line.update_layout(template="plotly_dark", paper_bgcolor="#07090f",
                                   plot_bgcolor="#07090f", height=280,
                                   margin=dict(l=20,r=20,t=20,b=20))
            st.plotly_chart(fig_line, use_container_width=True)

    st.markdown("---")
    st.markdown("### 📋 Deposit Log")
    dep_log = de._load(de.DEPOSIT_LOG_PATH, [])
    if dep_log:
        st.dataframe(pd.DataFrame(dep_log)[["num","date","total"]], use_container_width=True)
    else:
        st.info("No deposits logged yet.")

# ─────────────────────────────────────────────────────────────
# TAB 7 — IMPORT
# ─────────────────────────────────────────────────────────────
with tabs[7]:
    st.markdown("### 📥 Import Robinhood Activity")

    col_csv, col_pdf = st.columns(2)

    with col_csv:
        st.markdown("#### CSV — Transaction History")
        st.caption("Download from Robinhood → Account → History → Export CSV")
        uploaded_csv = st.file_uploader("Drop CSV here", type=["csv"], key="csv_uploader", label_visibility="collapsed")
        if uploaded_csv:
            existing = set(de._load(de.TX_STORE_PATH, {}).keys()) | st.session_state.processed_ids
            stats, new_ids = de.ingest_csv(uploaded_csv.read(), existing)
            st.session_state.processed_ids |= new_ids
            if stats.new_rows_added > 0:
                # Recompute portfolio from updated store
                tx   = de._load(de.TX_STORE_PATH, {})
                cryp = de._load(de.CRYPTO_OVR_PATH, {})
                st.session_state.portfolio = de.recompute_portfolio(tx, cryp)
                portfolio = st.session_state.portfolio
                st.success(f"✅ {stats.new_rows_added} new rows added. Portfolio updated.")
            else:
                st.info(f"No new rows — {stats.duplicate_rows_skipped} duplicates skipped. Portfolio unchanged.")
            st.json({
                "Total rows in file": stats.total_rows_in_file,
                "New rows added":     stats.new_rows_added,
                "Duplicates skipped": stats.duplicate_rows_skipped,
                "Errors":             stats.errors,
            })

    with col_pdf:
        st.markdown("#### PDF — Crypto Statement")
        st.caption("Upload Robinhood Crypto monthly statement PDF to update BTC/XRP holdings")
        uploaded_pdf = st.file_uploader("Drop PDF here", type=["pdf"], key="pdf_uploader", label_visibility="collapsed")
        if uploaded_pdf:
            overrides = de.parse_crypto_pdf(uploaded_pdf.read())
            if overrides:
                existing_ovr = de._load(de.CRYPTO_OVR_PATH, {})
                existing_ovr.update(overrides)
                de._save(de.CRYPTO_OVR_PATH, existing_ovr)
                tx   = de._load(de.TX_STORE_PATH, {})
                st.session_state.portfolio = de.recompute_portfolio(tx, existing_ovr)
                portfolio = st.session_state.portfolio
                st.success(f"✅ Crypto updated: {', '.join(overrides.keys())}")
                st.json(overrides)
            else:
                st.warning("Could not extract crypto data from PDF. Try uploading a different month or manually set shares below.")

        st.markdown("---")
        st.markdown("**Manual Crypto Override**")
        ovr = de._load(de.CRYPTO_OVR_PATH, {})
        for coin in ["BTC", "XRP", "ETH", "SOL"]:
            cur = ovr.get(coin, {})
            c1, c2 = st.columns(2)
            with c1:
                sh = st.number_input(f"{coin} shares", value=float(cur.get("shares", 0)), min_value=0.0, format="%.8f", key=f"ovr_sh_{coin}")
            with c2:
                ac = st.number_input(f"{coin} avg cost", value=float(cur.get("avg_cost", 0)), min_value=0.0, format="%.4f", key=f"ovr_ac_{coin}")
            if sh > 0:
                ovr[coin] = {"shares": sh, "avg_cost": ac, "first_buy_date": cur.get("first_buy_date", "")}
        if st.button("💾 Save Crypto Overrides"):
            de._save(de.CRYPTO_OVR_PATH, ovr)
            tx = de._load(de.TX_STORE_PATH, {})
            st.session_state.portfolio = de.recompute_portfolio(tx, ovr)
            st.success("Crypto overrides saved ✅")

# ─────────────────────────────────────────────────────────────
# TAB 8 — TESTS
# ─────────────────────────────────────────────────────────────
with tabs[8]:
    st.markdown("### 🧪 Live System Tests")
    st.caption("Runs against your actual portfolio data and live prices.")

    if st.button("▶️ Run All Tests", use_container_width=False):
        results = []

        def _test(name: str, passed: bool, detail: str = ""):
            status = "✅ PASS" if passed else "❌ FAIL"
            results.append({"Test": name, "Status": status, "Detail": detail})

        # 1. tx_store loaded
        tx = de._load(de.TX_STORE_PATH, {})
        _test("tx_store loaded from disk", len(tx) > 0, f"{len(tx)} rows")

        # 2. Portfolio has positions
        _test("Portfolio has positions", len(portfolio) > 0, f"{len(portfolio)} tickers")

        # 3. No zero-share positions
        zero_sh = [t for t, p in portfolio.items() if p["shares"] <= 0]
        _test("No zero-share positions", len(zero_sh) == 0, ", ".join(zero_sh) if zero_sh else "none")

        # 4. Key tickers present
        for t in ["NVDA", "VOO", "VYM"]:
            _test(f"{t} in portfolio", t in portfolio, f"{portfolio.get(t, {}).get('shares', 0):.4f} sh")

        # 5. Prices fetch (if Finnhub key available)
        if os.environ.get("FINNHUB_API_KEY"):
            svc = de.PriceService() if de._V11_AVAILABLE else None
            if svc:
                res = svc.fetch_prices(["NVDA", "BTC"])
                nvda_ok = res.get("NVDA", de.PriceResult("NVDA",0,None,None,0,"?",0,None)).mid_price > 0
                btc_ok  = res.get("BTC",  de.PriceResult("BTC", 0,None,None,0,"?",0,None)).mid_price > 0
                _test("Finnhub NVDA price > 0", nvda_ok, f"${res.get('NVDA', type('', (), {'mid_price':0})()).mid_price if hasattr(res.get('NVDA'), 'mid_price') else 0:,.2f}")
                _test("CoinGecko BTC price > 0", btc_ok)
        else:
            _test("Finnhub key configured", bool(os.environ.get("FINNHUB_API_KEY")), "Set FINNHUB_API_KEY in Streamlit secrets")

        # 6. Total equity > cost basis
        totals_t = de.portfolio_totals(portfolio, prices, cash)
        _test("Total equity > $0", totals_t["total"] > 0, f"${totals_t['total']:,.2f}")

        # 7. Recommendation engine returns recs for all positions
        dummy_prices = {t: p["avg_cost"] for t, p in portfolio.items()}
        recs_t = de.generate_recs(portfolio, dummy_prices)
        _test("Recs generated for all positions", len(recs_t) == len(portfolio),
              f"{len(recs_t)}/{len(portfolio)}")

        # 8. Every rec has required fields
        required = {"ticker", "action", "cat", "plain", "why", "pnl_pct"}
        missing_fields = [r["ticker"] for r in recs_t if not required.issubset(r.keys())]
        _test("All recs have required fields", len(missing_fields) == 0, ", ".join(missing_fields) if missing_fields else "ok")

        # 9. LT eligibility logic
        _test("LT eligibility — 2023 date", de.is_lt_eligible("2023-01-01"), "should be True")
        _test("LT eligibility — 2030 date", not de.is_lt_eligible("2030-01-01"), "should be False")

        # 10. VTV/VEA/VWO/BND flagged as SELL if in portfolio and LT
        for sell_t in de.SELL_LIST:
            if sell_t in portfolio:
                pos_lt = de.is_lt_eligible(portfolio[sell_t].get("first_buy_date",""))
                if pos_lt:
                    r = next((r for r in recs_t if r["ticker"] == sell_t), None)
                    _test(f"{sell_t} rec = SELL (LT eligible)", r and r["cat"] == "sell", r["action"] if r else "not found")

        # 11. Deposit recs sum to $900
        drecs = de.generate_deposit_recs(1, portfolio, dummy_prices, {}, 900.0)
        total_alloc = sum(r["amount"] for r in drecs)
        _test("Deposit recs sum to $900", abs(total_alloc - 900) < 0.01, f"${total_alloc:.2f}")

        # 12. Plaid snapshot (if configured)
        plaid_ok = plaid_snap is not None and plaid_snap.get("total_equity", 0) > 0
        _test("Plaid snapshot available", plaid_ok, f"${plaid_snap.get('total_equity',0):,.2f}" if plaid_snap else "Not synced")

        # Show results
        res_df = pd.DataFrame(results)
        pass_count = sum(1 for r in results if "PASS" in r["Status"])
        fail_count = len(results) - pass_count
        color = "#22c55e" if fail_count == 0 else "#f59e0b"
        st.markdown(f"<div style='color:{color};font-weight:700;font-size:16px'>{pass_count}/{len(results)} tests passing</div>", unsafe_allow_html=True)

        def _style_status(val):
            return "color: #22c55e" if "PASS" in str(val) else "color: #ef4444"

        st.dataframe(res_df.style.applymap(_style_status, subset=["Status"]),
                     use_container_width=True, hide_index=True)

# ─────────────────────────────────────────────────────────────
# FOOTER
# ─────────────────────────────────────────────────────────────
st.markdown(f"""<div style='margin-top:40px;padding:14px;border-top:1px solid #1e2d47;
  color:#475569;font-size:11px;text-align:center'>
  Portfolio War Room v11.0 · {datetime.date.today().strftime('%B %d, %Y')} ·
  {len(portfolio)} positions · Plaid + Finnhub real-time · prashanthkrishnan91
</div>""", unsafe_allow_html=True)
