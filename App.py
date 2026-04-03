"""
main_app.py — Portfolio War Room v11.1
All UI — zero business logic.

v11.1 changes vs v11.0:
  - Sidebar: "🏦 Sync Plaid" calls smart_sync_portfolio(force_plaid=True)
  - Sidebar: Smart Sync status badge shows holdings cache age + next Plaid due time
  - Header KPIs prefer Plaid snapshot when available, clearly labeled
  - All other tabs (Actions/Portfolio/Rebalancing/Invest/Schedule/Charts/History/Import/Tests)
    unchanged in structure; Tests tab updated to check HoldingsManager cache status
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

.kpi{background:linear-gradient(135deg,#0f1623 0%,#151f32 100%);border:1px solid #1e2d47;
  border-radius:14px;padding:18px 20px 14px;margin-bottom:10px}
.kpi-label{font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:.08em}
.kpi-value{font-size:26px;font-weight:700;letter-spacing:-0.03em;margin:4px 0}
.kpi-sub{font-size:12px;color:#94a3b8}

.rec-card{border-radius:12px;padding:14px 16px 10px;margin-bottom:8px;border-left:4px solid #334155}
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
.tag-cache{background:#1a1a07;color:#fbbf24}
.tag-fresh{background:#052e16;color:#86efac}
.tag-stale{background:#3b1515;color:#fca5a5}

.stTabs [data-baseweb="tab-list"]{gap:0;border-bottom:1px solid #1e2d47}
.stTabs [data-baseweb="tab"]{padding:10px 18px;font-size:13px;font-weight:500;color:#64748b;
  border-bottom:2px solid transparent;background:transparent}
.stTabs [aria-selected="true"]{color:#38bdf8;border-bottom:2px solid #38bdf8}

.sync-badge{padding:6px 12px;border-radius:8px;font-size:12px;font-weight:600;
  font-family:'JetBrains Mono',monospace;display:inline-block;margin-top:4px}
.sync-fresh{background:#052e16;color:#86efac;border:1px solid #166534}
.sync-stale{background:#451a03;color:#fcd34d;border:1px solid #92400e}
.sync-none{background:#1e293b;color:#64748b;border:1px solid #334155}
</style>
""", unsafe_allow_html=True)

# ═══════════════════════════════════════════════════════════════════════════════
# BOOTSTRAP + SESSION STATE INIT
# ═══════════════════════════════════════════════════════════════════════════════
de._bootstrap()

def _init():
    defaults = {
        "bust":          0,
        "prices":        {},
        "recs":          [],
        "cash":          float(de.ROBINHOOD_CASH_DEFAULT),
        "targets":       de._load(de.TARGETS_PATH, {}),
        "processed_ids": set(),
        "plaid_snap":    de._load(de.PLAID_SNAPSHOT_PATH, None),
        "deposit_num":   len(de._load(de.DEPOSIT_LOG_PATH, [])) + 1,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v
    if "portfolio" not in st.session_state:
        tx   = de._load(de.TX_STORE_PATH, {})
        cryp = de._load(de.CRYPTO_OVR_PATH, {})
        st.session_state.portfolio = de.recompute_portfolio(tx, cryp)

_init()

portfolio  = st.session_state.portfolio
prices     = st.session_state.prices
cash       = st.session_state.cash
targets    = st.session_state.targets
plaid_snap = st.session_state.plaid_snap

# ═══════════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ═══════════════════════════════════════════════════════════════════════════════
with st.sidebar:
    st.markdown("## ⚡ Portfolio War Room")
    st.markdown(
        f"<div style='font-size:11px;color:#64748b'>"
        f"{datetime.date.today().strftime('%A, %B %d, %Y')}</div>",
        unsafe_allow_html=True,
    )
    st.markdown("---")

    # ── Refresh / Sync buttons ────────────────────────────────────────────────
    col1, col2 = st.columns(2)
    with col1:
        if st.button("🔄 Refresh", use_container_width=True,
                     help="Fetch live prices (Finnhub/Polygon/CoinGecko) — no Plaid call"):
            st.session_state.bust += 1
            tickers = tuple(sorted(portfolio.keys()))
            with st.spinner("Fetching live prices…"):
                st.session_state.prices = de.fetch_prices(tickers, _bust=st.session_state.bust)
            prices = st.session_state.prices
            st.session_state.recs = de.generate_recs(portfolio, prices)
            st.rerun()

    plaid_configured = bool(
        os.environ.get("PLAID_ACCESS_TOKEN") or
        (hasattr(st, "secrets") and "PLAID_ACCESS_TOKEN" in st.secrets)
    )
    with col2:
        if st.button("🏦 Sync Plaid", use_container_width=True,
                     disabled=not plaid_configured,
                     help="Force Plaid holdings refresh. Auto-syncs every 24h."):
            with st.spinner("Syncing Plaid holdings…"):
                snap = de.smart_sync_portfolio(force_plaid=True)
            if snap:
                st.session_state.plaid_snap = snap
                plaid_snap = snap
                st.success(f"Plaid synced ✅ — ${snap['total_equity']:,.2f}")
            else:
                st.warning("Plaid not configured. Add PLAID_ACCESS_TOKEN to Streamlit secrets.")

    # ── Smart Sync status badge ───────────────────────────────────────────────
    st.markdown("**📡 Smart Sync Status**")
    cache_status = de.get_holdings_cache_status()
    if cache_status["status"] == "unavailable":
        st.markdown("<span class='sync-badge sync-none'>v11 modules not installed</span>",
                    unsafe_allow_html=True)
    elif cache_status["status"] in ("no_cache", "error"):
        st.markdown("<span class='sync-badge sync-none'>No Plaid cache — sync to connect</span>",
                    unsafe_allow_html=True)
    elif cache_status["is_stale"]:
        age = cache_status.get("age_hours", 0)
        st.markdown(
            f"<span class='sync-badge sync-stale'>"
            f"⚠️ Holdings {age:.0f}h old — sync due</span>",
            unsafe_allow_html=True,
        )
        st.caption(f"{cache_status['holdings_count']} positions in cache")
    else:
        age  = cache_status.get("age_hours", 0)
        nxt  = cache_status.get("next_sync_in", 0)
        st.markdown(
            f"<span class='sync-badge sync-fresh'>"
            f"✅ Holdings {age:.1f}h old — next sync in {nxt:.1f}h</span>",
            unsafe_allow_html=True,
        )
        st.caption(f"{cache_status['holdings_count']} positions · cash ${cache_status.get('cash_usd',0):.2f}")

    if not plaid_configured:
        st.caption("Add PLAID_ACCESS_TOKEN to Streamlit secrets to enable Plaid sync.")

    st.markdown("---")

    # ── Cash balance ──────────────────────────────────────────────────────────
    st.markdown("**💵 Cash Balance**")
    new_cash = st.number_input(
        "Robinhood Cash ($)", value=cash, min_value=0.0, step=10.0, format="%.2f"
    )
    if new_cash != cash:
        st.session_state.cash = new_cash
        cash = new_cash

    st.markdown("---")

    # ── Price data health ─────────────────────────────────────────────────────
    st.markdown("**📊 Price Data**")
    if prices:
        live_n  = sum(1 for p in prices.values() if p and p > 0)
        total_n = len(portfolio)
        color   = "#22c55e" if live_n == total_n else ("#f59e0b" if live_n > 0 else "#ef4444")
        label   = "Live" if live_n == total_n else ("Partial" if live_n > 0 else "Stale")
        st.markdown(
            f"<span style='color:{color};font-weight:600'>{label}</span>"
            f" &nbsp; {live_n}/{total_n} tickers",
            unsafe_allow_html=True,
        )
        if plaid_snap:
            ts = plaid_snap.get("timestamp", "")
            try:
                age_m = (datetime.datetime.now() - datetime.datetime.fromisoformat(ts)).seconds // 60
                st.markdown(
                    f"<span class='tag tag-plaid'>Plaid</span> synced {age_m}m ago",
                    unsafe_allow_html=True,
                )
                cache_age = plaid_snap.get("holdings_cache_age_h", 0)
                triggered = plaid_snap.get("plaid_sync_triggered", False)
                if triggered:
                    st.caption("Last sync: fresh Plaid data")
                else:
                    st.caption(f"Last sync: cache hit ({cache_age:.1f}h old holdings)")
            except Exception:
                pass
    else:
        st.markdown(
            "<span style='color:#64748b'>Press 🔄 Refresh for live prices</span>",
            unsafe_allow_html=True,
        )

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
                    key=f"target_{t}",
                )
            c_s, c_r = st.columns(2)
            with c_s:
                if st.button("💾 Save", use_container_width=True):
                    st.session_state.targets = new_targets
                    de._save(de.TARGETS_PATH, new_targets)
                    targets = new_targets
                    st.success("Saved")
            with c_r:
                if st.button("🔁 Reset AI", use_container_width=True):
                    suggested = de.generate_suggested_targets(portfolio)
                    st.session_state.targets = suggested
                    de._save(de.TARGETS_PATH, suggested)
                    targets = suggested
                    st.rerun()

# ═══════════════════════════════════════════════════════════════════════════════
# HEADER KPIs
# ═══════════════════════════════════════════════════════════════════════════════
totals = de.portfolio_totals(portfolio, prices, cash)

# Prefer Plaid snapshot if available and not too stale (< 2h)
use_plaid = False
if plaid_snap and plaid_snap.get("total_equity", 0) > 0:
    try:
        snap_age = (
            datetime.datetime.now() -
            datetime.datetime.fromisoformat(plaid_snap["timestamp"])
        ).total_seconds() / 3600
        use_plaid = snap_age < 2.0
    except Exception:
        use_plaid = True

display_total  = plaid_snap["total_equity"]   if use_plaid else totals["total"]
display_stocks = plaid_snap["stocks_equity"]  if use_plaid else totals["stocks"]
display_crypto = plaid_snap["crypto_equity"]  if use_plaid else totals["crypto"]
pnl_color      = "#22c55e" if totals["pnl"] >= 0 else "#ef4444"
pnl_sign       = "+" if totals["pnl"] >= 0 else ""
source_badge   = (
    '<span class="tag tag-plaid">Plaid</span>'
    if use_plaid else
    '<span style="color:#64748b;font-size:10px">estimated</span>'
)

st.markdown("<h1 style='margin-bottom:2px'>⚡ Portfolio War Room</h1>", unsafe_allow_html=True)
st.markdown(
    f"<div style='color:#64748b;font-size:13px;margin-bottom:18px'>"
    f"{len(portfolio)} positions · v11.1 · Smart Sync (Plaid 24h cache · Finnhub live prices)"
    f"</div>",
    unsafe_allow_html=True,
)

c1, c2, c3, c4, c5 = st.columns(5)
for col, label, value, sub, val_color in [
    (c1, "Total Equity",   f"${display_total:,.2f}",  source_badge,  "#e2e8f0"),
    (c2, "Stocks & ETFs",  f"${display_stocks:,.2f}", "",            "#e2e8f0"),
    (c3, "Crypto",         f"${display_crypto:,.2f}", "",            "#e2e8f0"),
    (c4, "Cash",           f"${cash:,.2f}",            "",            "#e2e8f0"),
    (c5, "Unrealised P&L", f"{pnl_sign}{totals['pnl_pct']:.1f}%",
     f"{pnl_sign}${abs(totals['pnl']):,.0f}", pnl_color),
]:
    with col:
        st.markdown(
            f"<div class='kpi'>"
            f"<div class='kpi-label'>{label}</div>"
            f"<div class='kpi-value' style='color:{val_color}'>{value}</div>"
            f"<div class='kpi-sub'>{sub}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )

st.markdown("---")

# ═══════════════════════════════════════════════════════════════════════════════
# TABS
# ═══════════════════════════════════════════════════════════════════════════════
tabs = st.tabs([
    "🎯 Actions", "📊 Portfolio", "⚖️ Rebalancing",
    "💰 Invest $900", "📅 Schedule", "📈 Charts",
    "🕐 History", "📥 Import", "🧪 Tests",
])

# ─────────────────────────────────────────────────────────────
# TAB 0 — ACTIONS
# ─────────────────────────────────────────────────────────────
with tabs[0]:
    if not prices:
        st.info("👆 Press **🔄 Refresh** in the sidebar to load live prices and generate recommendations.")
    else:
        recs = de.generate_recs(portfolio, prices)
        st.session_state.recs = recs

        sells   = [r for r in recs if r["cat"] == "sell"]
        buys    = [r for r in recs if r["cat"] == "buy"]
        trims   = [r for r in recs if r["cat"] == "trim"]
        holds   = [r for r in recs if r["cat"] == "hold"]
        reviews = [r for r in recs if r["cat"] == "review"]

        s1, s2, s3, s4, s5 = st.columns(5)
        for col, lbl, items, color in [
            (s1, "SELL",   sells,   "#ef4444"),
            (s2, "BUY",    buys,    "#22c55e"),
            (s3, "TRIM",   trims,   "#f59e0b"),
            (s4, "REVIEW", reviews, "#a855f7"),
            (s5, "HOLD",   holds,   "#64748b"),
        ]:
            with col:
                st.markdown(
                    f"<div style='text-align:center;padding:8px;border-radius:10px;"
                    f"background:#0f172a;border:1px solid #1e2d47'>"
                    f"<div style='color:{color};font-weight:700;font-size:20px'>{len(items)}</div>"
                    f"<div style='font-size:10px;color:#64748b'>{lbl}</div></div>",
                    unsafe_allow_html=True,
                )
        st.markdown("")

        def _rcard(r: dict):
            cat    = r["cat"]
            css    = {"sell":"rec-sell","buy":"rec-buy","trim":"rec-trim",
                      "hold":"rec-hold","review":"rec-review"}.get(cat, "rec-hold")
            tag    = {"sell":"tag-sell","buy":"tag-buy","trim":"tag-trim",
                      "hold":"tag-hold","review":"tag-review"}.get(cat, "tag-hold")
            pnl_c  = "#22c55e" if r["pnl_pct"] >= 0 else "#ef4444"
            proc   = f" · Est. proceeds: <b>${r['proceeds']:,.0f}</b>" if r["proceeds"] > 0 else ""
            live   = prices.get(r["ticker"])
            price_note = f"${r['price']:,.2f}" if live else f"${r['cost']:,.2f} (cost)"
            st.markdown(
                f"<div class='rec-card {css}'>"
                f"<span class='tag {tag}'>{cat.upper()}</span>"
                f"<b style='font-size:15px'>{r['ticker']}</b>"
                f"<span style='color:#64748b;font-size:12px'>"
                f" · {r['shares']:.4f} sh · {price_note} · {r['category']}</span><br/>"
                f"<span style='font-size:14px;font-weight:600'>{r['action']}</span>"
                f"<span style='color:{pnl_c};font-size:12px'> · {r['pnl_pct']:+.1f}%{proc}</span><br/>"
                f"<span style='color:#94a3b8;font-size:12px'>📝 {r['plain']}</span><br/>"
                f"<span style='color:#64748b;font-size:11px'>💡 {r['why']} · {r['tax']}</span>"
                f"</div>",
                unsafe_allow_html=True,
            )

        for group, label in [
            (sells,   "🔴 Sell Now"),
            (buys,    "🟢 Buy / Accumulate"),
            (trims,   "🟡 Trim"),
            (reviews, "🟣 Review"),
            (holds,   "⚫ Hold"),
        ]:
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

    # Prefer Plaid snapshot positions if available
    if plaid_snap and plaid_snap.get("positions"):
        triggered = plaid_snap.get("plaid_sync_triggered", False)
        cache_age = plaid_snap.get("holdings_cache_age_h", 0)
        sync_info = (
            "fresh Plaid sync" if triggered
            else f"holdings cache {cache_age:.1f}h old"
        )
        st.markdown(
            f"<span class='tag tag-plaid'>Plaid</span> "
            f"**{len(plaid_snap['positions'])} positions** · {sync_info} · "
            f"quantities authoritative",
            unsafe_allow_html=True,
        )
        rows = []
        for pos in plaid_snap["positions"]:
            rows.append({
                "Ticker":       pos["ticker"],
                "Shares":       round(pos["quantity"], 6),
                "Mid Price":    round(pos["mid_price"], 4),
                "Market Value": round(pos["market_value"], 2),
                "Avg Cost":     round(pos["avg_cost_basis"], 2),
                "Unreal P&L":   round(pos["unrealised_pnl"], 2),
                "P&L %":        round(pos["unrealised_pct"], 1),
                "Source":       pos.get("price_source", "?"),
                "Type":         pos.get("security_type", ""),
            })
        df = pd.DataFrame(rows)
        def _cpnl(v):
            if isinstance(v, float):
                return "color: #22c55e" if v > 0 else ("color: #ef4444" if v < 0 else "")
            return ""
        st.dataframe(
            df.style.applymap(_cpnl, subset=["P&L %", "Unreal P&L"]),
            use_container_width=True, height=500,
            column_config={
                "Market Value": st.column_config.NumberColumn(format="$%.2f"),
                "Avg Cost":     st.column_config.NumberColumn(format="$%.4f"),
                "Mid Price":    st.column_config.NumberColumn(format="$%.4f"),
                "Unreal P&L":   st.column_config.NumberColumn(format="$%.2f"),
            },
        )
    else:
        # tx_store computed positions
        rows = []
        for ticker, pos in sorted(
            portfolio.items(),
            key=lambda x: -de._safe_price(x[0], x[1], prices) * x[1]["shares"],
        ):
            p   = de._safe_price(ticker, pos, prices)
            mkt = p * pos["shares"]
            pnl = (p - pos["avg_cost"]) / pos["avg_cost"] * 100 if pos["avg_cost"] > 0 else 0
            lt  = de.is_lt_eligible(pos.get("first_buy_date", ""))
            rows.append({
                "Ticker":       ticker,
                "Shares":       round(pos["shares"], 6),
                "Avg Cost":     round(pos["avg_cost"], 4),
                "Live Price":   round(p, 4),
                "Market Value": round(mkt, 2),
                "P&L %":        round(pnl, 1),
                "LT?":          "✅" if lt else f"⏳ {de.days_to_lt(pos.get('first_buy_date',''))}d",
                "Category":     pos.get("category", "Stocks"),
            })
        df = pd.DataFrame(rows)
        st.dataframe(
            df.style.applymap(lambda v: "color:#22c55e" if isinstance(v,float) and v>0
                              else ("color:#ef4444" if isinstance(v,float) and v<0 else ""),
                              subset=["P&L %"]),
            use_container_width=True, height=520,
            column_config={
                "Market Value": st.column_config.NumberColumn(format="$%.2f"),
                "Avg Cost":     st.column_config.NumberColumn(format="$%.4f"),
                "Live Price":   st.column_config.NumberColumn(format="$%.4f"),
            },
        )

    with st.expander("🔍 Position Detail"):
        sel = st.selectbox("Ticker", sorted(portfolio.keys()), key="pos_detail_sel")
        if sel:
            pos = portfolio[sel]
            p   = de._safe_price(sel, pos, prices)
            mkt = p * pos["shares"]
            pnl = (p - pos["avg_cost"]) / pos["avg_cost"] * 100 if pos["avg_cost"] > 0 else 0
            r1, r2, r3 = st.columns(3)
            r1.metric("Live Price",   f"${p:,.4f}")
            r2.metric("Market Value", f"${mkt:,.2f}")
            r3.metric("P&L",          f"{pnl:+.1f}%", delta=f"{pnl:+.1f}%")
            r4, r5, r6 = st.columns(3)
            r4.metric("Avg Cost",     f"${pos['avg_cost']:,.4f}")
            r5.metric("Shares",       f"{pos['shares']:.6f}")
            r6.metric("LT Eligible?", "Yes ✅" if de.is_lt_eligible(pos.get("first_buy_date",""))
                      else f"No — {de.days_to_lt(pos.get('first_buy_date',''))} days")
            target = de.TARGETS.get(sel)
            if target:
                upside = (target - p) / p * 100 if p > 0 else 0
                st.metric("Analyst Target", f"${target:,.0f}", delta=f"{upside:+.0f}% upside")

# ─────────────────────────────────────────────────────────────
# TAB 2 — REBALANCING
# ─────────────────────────────────────────────────────────────
with tabs[2]:
    if not targets:
        st.info("Click **✨ Generate AI Targets** in the sidebar first.")
    elif not prices:
        st.info("Press 🔄 Refresh to load prices.")
    else:
        rebal = de.compute_rebalancing(portfolio, prices, targets)
        st.markdown("### Portfolio Drift vs Targets")
        st.caption("Green = underweight (buy). Red = overweight (trim).")

        colors = ["#22c55e" if r["drift"] < 0 else "#ef4444" for r in rebal]
        fig = go.Figure(go.Bar(
            x=[r["drift"] for r in rebal],
            y=[r["ticker"] for r in rebal],
            orientation="h", marker_color=colors,
            text=[f"{r['action']} ({r['drift']:+.1f}%)" for r in rebal],
            textposition="outside",
        ))
        fig.update_layout(
            template="plotly_dark", paper_bgcolor="#07090f", plot_bgcolor="#07090f",
            height=max(300, len(rebal)*28), xaxis_title="Drift (Current% − Target%)",
            margin=dict(l=80,r=40,t=20,b=40), font=dict(family="DM Sans",size=12),
        )
        st.plotly_chart(fig, use_container_width=True)

        df_r = pd.DataFrame(rebal)[["ticker","current_pct","target_pct","drift","action","market_value"]]
        df_r.columns = ["Ticker","Current %","Target %","Drift %","Action","Market Value"]
        st.dataframe(df_r, use_container_width=True,
                     column_config={"Market Value": st.column_config.NumberColumn(format="$%.0f")})

# ─────────────────────────────────────────────────────────────
# TAB 3 — INVEST $900
# ─────────────────────────────────────────────────────────────
with tabs[3]:
    dep_num = st.session_state.deposit_num
    fridays = de.get_biweekly_dates(datetime.date(2026, 4, 3), n=18)
    today   = datetime.date.today()

    cur_idx = next((i for i, d in enumerate(fridays) if d >= today), 0)
    cur_date = fridays[cur_idx] if cur_idx < len(fridays) else fridays[-1]

    st.markdown(f"### 💰 Deposit #{dep_num} — {cur_date.strftime('%B %d, %Y')}")
    if not prices:
        st.info("Press 🔄 Refresh for accurate share estimates.")

    dep_recs = de.generate_deposit_recs(dep_num, portfolio, prices, targets, 900.0)
    dep_df   = pd.DataFrame(dep_recs)
    dep_df.columns = [c.replace("_", " ").title() for c in dep_df.columns]
    st.dataframe(dep_df, use_container_width=True,
                 column_config={
                     "Amount":     st.column_config.NumberColumn(format="$%.2f"),
                     "Price":      st.column_config.NumberColumn(format="$%.2f"),
                     "Est Shares": st.column_config.NumberColumn(format="%.4f"),
                 })

    c_tot, c_btn = st.columns([3, 1])
    with c_tot:
        st.metric("Total Deposit", "$900.00")
    with c_btn:
        if st.button("✅ Mark Done", use_container_width=True):
            de.log_deposit(dep_num, str(cur_date), dep_recs, 900.0)
            st.session_state.deposit_num += 1
            st.success(f"Deposit #{dep_num} logged! Next: #{dep_num+1}")
            st.rerun()

    st.markdown("---")
    st.markdown("### 📅 All Deposits — 2026")
    rotation_cycle = [de.DEPOSIT_ROTATION[i % len(de.DEPOSIT_ROTATION)] for i in range(len(fridays))]
    logged_nums    = {e["num"] for e in de._load(de.DEPOSIT_LOG_PATH, [])}
    sched_rows = []
    for i, (d, pick) in enumerate(zip(fridays, rotation_cycle)):
        num = i + 1
        sched_rows.append({
            "#": num, "Date": d.strftime("%b %d, %Y"),
            "NVDA ($252)": "✓", "VOO ($198)": "✓", "VYM ($153)": "✓",
            "QQQ ($153)": "✓", "Rotating ($144)": pick,
            "Done": "✅" if num in logged_nums else ("📍 TODAY" if d == today else ""),
        })
    st.dataframe(pd.DataFrame(sched_rows), use_container_width=True, hide_index=True)

# ─────────────────────────────────────────────────────────────
# TAB 4 — SCHEDULE
# ─────────────────────────────────────────────────────────────
with tabs[4]:
    st.markdown("### 📅 2026 Action Calendar")
    calendar_events = [
        ("Apr 3",  "SELL",  "VTV, VEA, VWO, BND",  "LT eligible now — pay 15%. Reinvest into VOO+VYM same day."),
        ("Apr 3",  "BUY",   "Deposit #1",           "$900 → NVDA($252), VOO($198), VYM($153), QQQ($153), META($144)"),
        ("Apr 4",  "TRIM",  "GLD",                  "GLD turns LT — trim 25% near $450 target"),
        ("Apr 17", "BUY",   "Deposit #2",           "$900 → NVDA, VOO, VYM, QQQ, GOOGL"),
        ("May 1",  "BUY",   "Deposit #3",           "$900 → NVDA, VOO, VYM, QQQ, AAPL"),
        ("May 20", "SELL",  "SPY",                  "SPY turns LT — sell, buy VOO same day (no wash sale)"),
        ("Jul 15", "SELL",  "VUG",                  "VUG turns LT — sell, buy QQQ same day"),
        ("Aug 14", "EVAL",  "BLSH",                 "BLSH hits 1yr — trim 25% if up >20%"),
        ("Sep 11", "EVAL",  "KLAR",                 "KLAR hits 1yr — trim 25% if up >20%"),
        ("Sep 18", "EVAL",  "STUB",                 "STUB hits 1yr — evaluate position"),
        ("Nov 6",  "TRIM",  "TSM",                  "Big TSM lot turns LT — trim 20%"),
        ("Dec 15", "TRIM",  "GOOGL",                "Big GOOGL lot turns LT — trim 20%"),
        ("Dec 20", "TAX",   "Year-end Harvest",     "Net realized gains vs losses before Dec 31"),
    ]
    _month_map = {"Apr":"04","May":"05","Jun":"06","Jul":"07","Aug":"08",
                  "Sep":"09","Oct":"10","Nov":"11","Dec":"12","Jan":"01"}
    tag_map = {"SELL":"tag-sell","BUY":"tag-buy","TRIM":"tag-trim","EVAL":"tag-hold","TAX":"tag-review"}
    for date_s, etype, ticker, notes in calendar_events:
        parts = date_s.split()
        try:
            ev_date = datetime.date(2026, int(_month_map.get(parts[0][:3], "04")), int(parts[1]))
            opacity = "0.4" if ev_date < today else "1.0"
        except Exception:
            opacity = "1.0"
        past = " · <span style='color:#475569'>done</span>" if opacity == "0.4" else ""
        st.markdown(
            f"<div style='opacity:{opacity};margin-bottom:8px;padding:10px 14px;"
            f"border-radius:10px;background:#0d111a;border:1px solid #1e2d47'>"
            f"<span class='tag {tag_map.get(etype,'tag-hold')}'>{etype}</span>"
            f"<b>{date_s}</b> &nbsp; {ticker}{past}<br/>"
            f"<span style='color:#94a3b8;font-size:12px'>{notes}</span></div>",
            unsafe_allow_html=True,
        )

    st.markdown("---")
    st.markdown("### 🧾 Tax Playbook")
    for rule, title, detail in [
        ("Rule #1", "Never sell < 1 year held",    "Pay 37% (ST) vs 15% (LT). Always worth the wait."),
        ("Rule #2", "ETF swaps are NOT wash sales", "Selling SPY → buying VOO same day is allowed."),
        ("Rule #3", "DRIP creates new lots",        "Each reinvestment is a new tax lot. Track individually."),
        ("Rule #4", "Crypto: never sell short-term","BTC/XRP both held >1yr. LT rate applies."),
        ("Rule #5", "Year-end harvest",             "Net gains vs losses before Dec 31."),
    ]:
        st.markdown(
            f"<div style='margin-bottom:8px;padding:10px 14px;border-radius:10px;"
            f"background:#07100d;border-left:3px solid #22c55e'>"
            f"<span style='color:#22c55e;font-size:11px;font-weight:700'>{rule}</span>"
            f" &nbsp; <b>{title}</b><br/>"
            f"<span style='color:#94a3b8;font-size:12px'>{detail}</span></div>",
            unsafe_allow_html=True,
        )

# ─────────────────────────────────────────────────────────────
# TAB 5 — CHARTS
# ─────────────────────────────────────────────────────────────
with tabs[5]:
    if not prices:
        st.info("Press 🔄 Refresh to load prices.")
    else:
        ca, cb = st.columns(2)
        with ca:
            st.markdown("#### Allocation by Ticker")
            labels, vals = [], []
            for ticker, pos in portfolio.items():
                mkt = de._safe_price(ticker, pos, prices) * pos["shares"]
                if mkt > 0:
                    labels.append(ticker); vals.append(mkt)
            fig_pie = px.pie(
                values=vals, names=labels, hole=0.42,
                color_discrete_sequence=px.colors.qualitative.Set3,
            )
            fig_pie.update_layout(
                template="plotly_dark", paper_bgcolor="#07090f",
                margin=dict(l=10,r=10,t=20,b=20), height=420,
                legend=dict(font=dict(size=10)),
            )
            fig_pie.update_traces(textinfo="label+percent", textfont_size=10)
            st.plotly_chart(fig_pie, use_container_width=True)

        with cb:
            st.markdown("#### P&L % by Position")
            pnl_rows = []
            for ticker, pos in portfolio.items():
                p    = de._safe_price(ticker, pos, prices)
                cost = pos["avg_cost"]
                pnl  = (p - cost) / cost * 100 if cost > 0 else 0
                pnl_rows.append({"ticker": ticker, "pnl": pnl})
            pnl_df = pd.DataFrame(pnl_rows).sort_values("pnl")
            fig_bar = go.Figure(go.Bar(
                x=pnl_df["pnl"], y=pnl_df["ticker"], orientation="h",
                marker_color=["#22c55e" if v>=0 else "#ef4444" for v in pnl_df["pnl"]],
                text=[f"{v:+.1f}%" for v in pnl_df["pnl"]], textposition="outside",
            ))
            fig_bar.update_layout(
                template="plotly_dark", paper_bgcolor="#07090f", plot_bgcolor="#07090f",
                height=420, margin=dict(l=60,r=60,t=20,b=20),
                xaxis_title="P&L %", font=dict(family="DM Sans",size=11),
            )
            st.plotly_chart(fig_bar, use_container_width=True)

        st.markdown("#### Allocation by Category")
        cat_data: dict[str, float] = {}
        for ticker, pos in portfolio.items():
            mkt = de._safe_price(ticker, pos, prices) * pos["shares"]
            cat = pos.get("category", "Stocks")
            cat_data[cat] = cat_data.get(cat, 0) + mkt
        cat_df = pd.DataFrame(list(cat_data.items()), columns=["Category", "Value"])
        fig_cat = px.bar(cat_df, x="Category", y="Value", text_auto="$.0f",
                         color="Category",
                         color_discrete_sequence=["#38bdf8","#22c55e","#f59e0b"])
        fig_cat.update_layout(
            template="plotly_dark", paper_bgcolor="#07090f", plot_bgcolor="#07090f",
            height=280, margin=dict(l=20,r=20,t=20,b=20), showlegend=False,
        )
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
        h_rows = []
        for snap in reversed(history[-50:]):
            t = snap.get("totals", {})
            h_rows.append({
                "Timestamp":    snap.get("timestamp", "")[:16].replace("T", " "),
                "Total Equity": round(t.get("total", 0), 2),
                "Stocks":       round(t.get("stocks", 0), 2),
                "Crypto":       round(t.get("crypto", 0), 2),
                "P&L %":        round(t.get("pnl_pct", 0), 2),
                "# Recs":       len(snap.get("recs", [])),
            })
        h_df = pd.DataFrame(h_rows)
        st.dataframe(h_df, use_container_width=True,
                     column_config={
                         "Total Equity": st.column_config.NumberColumn(format="$%.2f"),
                         "Stocks":       st.column_config.NumberColumn(format="$%.2f"),
                         "Crypto":       st.column_config.NumberColumn(format="$%.2f"),
                     })
        if len(h_rows) > 1:
            ts_df = pd.DataFrame(h_rows)[["Timestamp","Total Equity"]]
            fig_line = px.line(ts_df, x="Timestamp", y="Total Equity",
                               markers=True, line_shape="spline",
                               color_discrete_sequence=["#38bdf8"])
            fig_line.update_layout(
                template="plotly_dark", paper_bgcolor="#07090f",
                plot_bgcolor="#07090f", height=280,
                margin=dict(l=20,r=20,t=20,b=20),
            )
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
    c_csv, c_pdf = st.columns(2)

    with c_csv:
        st.markdown("#### CSV — Transaction History")
        st.caption("Robinhood → Account → History → Export CSV")
        uploaded_csv = st.file_uploader(
            "Drop CSV here", type=["csv"], key="csv_up", label_visibility="collapsed"
        )
        if uploaded_csv:
            existing = set(de._load(de.TX_STORE_PATH, {}).keys()) | st.session_state.processed_ids
            stats, new_ids = de.ingest_csv(uploaded_csv.read(), existing)
            st.session_state.processed_ids |= new_ids
            if stats.new_rows_added > 0:
                tx   = de._load(de.TX_STORE_PATH, {})
                cryp = de._load(de.CRYPTO_OVR_PATH, {})
                st.session_state.portfolio = de.recompute_portfolio(tx, cryp)
                portfolio = st.session_state.portfolio
                st.success(f"✅ {stats.new_rows_added} new rows added. Portfolio updated.")
            else:
                st.info(f"No new rows — {stats.duplicate_rows_skipped} duplicates skipped.")
            st.json({
                "Total rows in file": stats.total_rows_in_file,
                "New rows added":     stats.new_rows_added,
                "Duplicates skipped": stats.duplicate_rows_skipped,
            })

    with c_pdf:
        st.markdown("#### PDF — Crypto Statement")
        st.caption("Robinhood Crypto monthly statement PDF")
        uploaded_pdf = st.file_uploader(
            "Drop PDF here", type=["pdf"], key="pdf_up", label_visibility="collapsed"
        )
        if uploaded_pdf:
            overrides = de.parse_crypto_pdf(uploaded_pdf.read())
            if overrides:
                existing_ovr = de._load(de.CRYPTO_OVR_PATH, {})
                existing_ovr.update(overrides)
                de._save(de.CRYPTO_OVR_PATH, existing_ovr)
                tx = de._load(de.TX_STORE_PATH, {})
                st.session_state.portfolio = de.recompute_portfolio(tx, existing_ovr)
                portfolio = st.session_state.portfolio
                st.success(f"✅ Crypto updated: {', '.join(overrides.keys())}")
                st.json(overrides)
            else:
                st.warning("Could not extract crypto data from PDF. Use manual override below.")

        st.markdown("---")
        st.markdown("**Manual Crypto Override**")
        ovr = de._load(de.CRYPTO_OVR_PATH, {})
        for coin in ["BTC", "XRP", "ETH", "SOL"]:
            cur = ovr.get(coin, {})
            mc1, mc2 = st.columns(2)
            with mc1:
                sh = st.number_input(f"{coin} shares", value=float(cur.get("shares", 0)),
                                     min_value=0.0, format="%.8f", key=f"ovr_sh_{coin}")
            with mc2:
                ac = st.number_input(f"{coin} avg cost", value=float(cur.get("avg_cost", 0)),
                                     min_value=0.0, format="%.4f", key=f"ovr_ac_{coin}")
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
    st.caption("Runs against your actual portfolio data and live environment.")

    if st.button("▶️ Run All Tests", use_container_width=False):
        results = []

        def _t(name, passed, detail=""):
            results.append({
                "Test":   name,
                "Status": "✅ PASS" if passed else "❌ FAIL",
                "Detail": detail,
            })

        # 1. tx_store
        tx = de._load(de.TX_STORE_PATH, {})
        _t("tx_store loaded", len(tx) > 0, f"{len(tx)} rows")

        # 2. Portfolio positions
        _t("Portfolio has positions", len(portfolio) > 0, f"{len(portfolio)} tickers")

        # 3. No zero-share positions
        zero = [t for t, p in portfolio.items() if p["shares"] <= 0]
        _t("No zero-share positions", len(zero) == 0, ", ".join(zero) or "none")

        # 4. Key tickers
        for t in ["NVDA", "VOO", "VYM"]:
            _t(f"{t} present", t in portfolio, f"{portfolio.get(t,{}).get('shares',0):.4f} sh")

        # 5. Smart Sync cache status
        cs = de.get_holdings_cache_status()
        _t("HoldingsManager available",
           cs["status"] not in ("unavailable", "error"),
           cs.get("label", ""))
        if cs["status"] not in ("unavailable","error","no_cache"):
            _t("Holdings cache has positions",
               cs["holdings_count"] > 0,
               f"{cs['holdings_count']} positions · {cs.get('age_hours',0):.1f}h old")

        # 6. Plaid configured
        _t("Plaid access token set",
           bool(os.environ.get("PLAID_ACCESS_TOKEN")),
           "Set PLAID_ACCESS_TOKEN in Streamlit secrets")

        # 7. Finnhub key
        de._load_env_from_secrets()
        _t("Finnhub key configured",
           bool(os.environ.get("FINNHUB_API_KEY")),
           "Set FINNHUB_API_KEY in Streamlit secrets")

        # 8. Total equity > 0
        totals_t = de.portfolio_totals(portfolio, prices, cash)
        _t("Total equity > $0", totals_t["total"] > 0, f"${totals_t['total']:,.2f}")

        # 9. Recs generated for all positions
        dummy  = {t: p["avg_cost"] for t, p in portfolio.items()}
        recs_t = de.generate_recs(portfolio, dummy)
        _t("Recs for all positions",
           len(recs_t) == len(portfolio), f"{len(recs_t)}/{len(portfolio)}")

        # 10. Required rec fields
        required = {"ticker","action","cat","plain","why","pnl_pct"}
        missing  = [r["ticker"] for r in recs_t if not required.issubset(r.keys())]
        _t("All recs have required fields", len(missing)==0, ", ".join(missing) or "ok")

        # 11. LT eligibility
        _t("LT logic 2023-01-01", de.is_lt_eligible("2023-01-01"), "should be True")
        _t("LT logic 2030-01-01", not de.is_lt_eligible("2030-01-01"), "should be False")

        # 12. Deposit recs sum to $900
        d_recs = de.generate_deposit_recs(1, portfolio, dummy, {}, 900.0)
        total_alloc = sum(r["amount"] for r in d_recs)
        _t("Deposit recs sum $900", abs(total_alloc - 900) < 0.02, f"${total_alloc:.2f}")

        # 13. Plaid snapshot present
        _t("Plaid snapshot on disk",
           plaid_snap is not None and plaid_snap.get("total_equity",0) > 0,
           f"${plaid_snap.get('total_equity',0):,.2f}" if plaid_snap else "Not synced yet")

        # Display
        res_df = pd.DataFrame(results)
        pass_n = sum(1 for r in results if "PASS" in r["Status"])
        color  = "#22c55e" if pass_n == len(results) else "#f59e0b"
        st.markdown(
            f"<div style='color:{color};font-weight:700;font-size:16px'>"
            f"{pass_n}/{len(results)} tests passing</div>",
            unsafe_allow_html=True,
        )
        st.dataframe(
            res_df.style.applymap(
                lambda v: "color:#22c55e" if "PASS" in str(v) else "color:#ef4444",
                subset=["Status"],
            ),
            use_container_width=True, hide_index=True,
        )

# ─────────────────────────────────────────────────────────────
# FOOTER
# ─────────────────────────────────────────────────────────────
st.markdown(
    f"<div style='margin-top:40px;padding:14px;border-top:1px solid #1e2d47;"
    f"color:#475569;font-size:11px;text-align:center'>"
    f"Portfolio War Room v11.1 · {datetime.date.today().strftime('%B %d, %Y')} · "
    f"{len(portfolio)} positions · Smart Sync (Plaid 24h cache + Finnhub live) · "
    f"prashanthkrishnan91"
    f"</div>",
    unsafe_allow_html=True,
)
