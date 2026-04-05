"""
main_app.py — Portfolio War Room v11.2
All UI — zero business logic.

v11.2 changes:
  1. Import tab — richer dedup reconciliation panel showing cross-session,
     intra-file and no-code skip counts; "pre-loaded IDs" badge
  2. Invest $900 tab — cash_balance fed into generate_deposit_recs() so
     Robinhood cash is included in total investable capital; KPI row shows
     breakdown of deposit + cash; rebalancing drift chart accounts for cash
  3. New Override inputs in Invest tab — number_input next to each AI rec;
     "Apply Overrides" button calls apply_overrides_to_recs(), re-renders
     the table with override deltas highlighted
  4. New Decision Log tab — sortable dataframe of all past overrides loaded
     from decision_log.json; summary metrics; CSV export button
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
import drip_analytics as drip

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
# GLOBAL CSS  (identical theme to v11.1)
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
.tag-override{background:#2d1a00;color:#fb923c}
.tag-delta-pos{background:#052e16;color:#86efac}
.tag-delta-neg{background:#3b1515;color:#fca5a5}

.stTabs [data-baseweb="tab-list"]{gap:0;border-bottom:1px solid #1e2d47}
.stTabs [data-baseweb="tab"]{padding:10px 18px;font-size:13px;font-weight:500;color:#64748b;
  border-bottom:2px solid transparent;background:transparent}
.stTabs [aria-selected="true"]{color:#38bdf8;border-bottom:2px solid #38bdf8}

.sync-badge{padding:6px 12px;border-radius:8px;font-size:12px;font-weight:600;
  font-family:'JetBrains Mono',monospace;display:inline-block;margin-top:4px}
.sync-fresh{background:#052e16;color:#86efac;border:1px solid #166534}
.sync-stale{background:#451a03;color:#fcd34d;border:1px solid #92400e}
.sync-none{background:#1e293b;color:#64748b;border:1px solid #334155}

.dedup-ok{color:#22c55e;font-weight:700}
.dedup-warn{color:#f59e0b;font-weight:700}
.override-row{background:#1a120a;border:1px solid #451a03;border-radius:8px;
  padding:10px 14px;margin-bottom:6px}

/* v13 sidebar: HIDE native collapse button — our toggle is the only way in/out */
[data-testid="collapsedControl"]{display:none !important}

/* Sidebar slide transition — never display:none so Streamlit never "collapses" it */
section[data-testid="stSidebar"]{
  transition: margin-left 0.3s ease, width 0.3s ease;
}
</style>
""", unsafe_allow_html=True)

# ═══════════════════════════════════════════════════════════════════════════════
# BOOTSTRAP + SESSION STATE INIT
# ═══════════════════════════════════════════════════════════════════════════════
de._bootstrap()

def _init():
    defaults = {
        "bust":            0,
        "prices":          {},
        "recs":            [],
        "cash":            float(de.ROBINHOOD_CASH_DEFAULT),
        "targets":         de._load(de.TARGETS_PATH, {}),
        "plaid_snap":      de._load(de.PLAID_SNAPSHOT_PATH, None),
        "deposit_num":     len(de._load(de.DEPOSIT_LOG_PATH, [])) + 1,
        # v13: sidebar state — persisted so it never disappears
        "sidebar_open":    True,
        # ── v11.4: full historical fingerprint seeding ───────────────────────
        # seed_processed_ids_from_history() returns the union of:
        #   (a) fingerprints derived from BAKED_BOOTSTRAP via make_tx_fingerprint
        #       — these now match what ingest_csv() produces for the same rows
        #   (b) fingerprints already on disk in tx_store.json
        # Total on a fresh install: ~34 (bootstrap positions)
        # Total after CSV import:   ~600+ (full transaction history)
        # The sidebar badge shows this count so the user can verify dedup is live.
        "processed_ids":   de.seed_processed_ids_from_history(),
        # Override state for the current Invest session
        "dep_overrides":   {},    # {ticker: manual_amount}
        "dep_reasons":     {},    # {ticker: reason_text}
        "dep_recs_final":  [],    # after apply_overrides_to_recs()
        "overrides_applied": False,
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
# SIDEBAR VISIBILITY (v12)
# ═══════════════════════════════════════════════════════════════════════════════
# Inject CSS to shift sidebar off-screen when closed.
# Native collapsedControl is hidden (display:none) — our ☰ button is the only toggle,
# so the sidebar can never be put into Streamlit's internal "collapsed" state.
if not st.session_state.sidebar_open:
    st.markdown(
        "<style>"
        "section[data-testid='stSidebar']{"
        "  margin-left:-21rem !important;"
        "  min-width:0 !important;"
        "  width:0 !important;"
        "  overflow:hidden !important;"
        "}"
        "</style>",
        unsafe_allow_html=True,
    )

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

    # v12: system mode badge
    _sys_mode = de.get_system_mode()
    if _sys_mode == "bootstrap":
        st.markdown(
            "<span style='background:#451a03;color:#fcd34d;border:1px solid #92400e;"
            "border-radius:6px;padding:3px 8px;font-size:11px;font-weight:600'>"
            "⚠️ BOOTSTRAP MODE — upload CSV to go live</span>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            "<span style='background:#052e16;color:#86efac;border:1px solid #166534;"
            "border-radius:6px;padding:3px 8px;font-size:11px;font-weight:600'>"
            "✅ LIVE MODE</span>",
            unsafe_allow_html=True,
        )
    st.markdown("---")

    col1, col2 = st.columns(2)
    with col1:
        if st.button("🔄 Refresh", use_container_width=True,
                     help="Fetch live prices — no Plaid call"):
            st.session_state.bust += 1
            with st.spinner("Fetching live prices…"):
                st.session_state.prices = de.fetch_prices(
                    tuple(sorted(portfolio.keys())), _bust=st.session_state.bust
                )
            prices = st.session_state.prices
            st.session_state.recs = de.generate_recs(portfolio, prices)
            # Reset override state so new prices re-generate clean recs
            st.session_state.dep_recs_final  = []
            st.session_state.overrides_applied = False
            st.rerun()

    plaid_configured = bool(
        os.environ.get("PLAID_ACCESS_TOKEN") or
        (hasattr(st, "secrets") and "PLAID_ACCESS_TOKEN" in st.secrets)
    )
    with col2:
        if st.button("🏦 Sync Plaid", use_container_width=True,
                     disabled=not plaid_configured,
                     help="Force Plaid refresh. Auto-syncs every 24 h."):
            with st.spinner("Syncing Plaid holdings…"):
                snap = de.smart_sync_portfolio(force_plaid=True)
            if snap:
                st.session_state.plaid_snap = snap
                plaid_snap = snap
                st.success(f"Plaid synced ✅  ${snap['total_equity']:,.2f}")
            else:
                st.warning("Plaid not configured. Add PLAID_ACCESS_TOKEN to secrets.")

    # Smart Sync status badge
    st.markdown("**📡 Smart Sync Status**")
    cs = de.get_holdings_cache_status()
    if cs["status"] in ("unavailable", "no_cache", "error"):
        st.markdown("<span class='sync-badge sync-none'>No Plaid cache — sync to connect</span>",
                    unsafe_allow_html=True)
    elif cs["is_stale"]:
        st.markdown(
            f"<span class='sync-badge sync-stale'>⚠️ Holdings {cs.get('age_hours',0):.0f}h old — sync due</span>",
            unsafe_allow_html=True,
        )
    else:
        age = cs.get("age_hours", 0); nxt = cs.get("next_sync_in", 0)
        st.markdown(
            f"<span class='sync-badge sync-fresh'>✅ {age:.1f}h old · next in {nxt:.1f}h</span>",
            unsafe_allow_html=True,
        )
        st.caption(f"{cs['holdings_count']} positions · cash ${cs.get('cash_usd',0):.2f}")

    if not plaid_configured:
        st.caption("Add PLAID_ACCESS_TOKEN to Streamlit secrets to enable Plaid sync.")

    st.markdown("---")

    # Cash balance
    st.markdown("**💵 Cash Balance**")
    new_cash = st.number_input("Robinhood Cash ($)", value=cash, min_value=0.0, step=10.0, format="%.2f")
    if new_cash != cash:
        st.session_state.cash = new_cash
        cash = new_cash
        # Reset override state — cash change affects all allocations
        st.session_state.dep_recs_final  = []
        st.session_state.overrides_applied = False

    st.markdown("---")

    # Price data health
    st.markdown("**📊 Price Data**")
    if prices:
        live_n  = sum(1 for p in prices.values() if p and p > 0)
        total_n = len(portfolio)
        color   = "#22c55e" if live_n == total_n else ("#f59e0b" if live_n > 0 else "#ef4444")
        label   = "Live" if live_n == total_n else ("Partial" if live_n > 0 else "Stale")
        st.markdown(f"<span style='color:{color};font-weight:600'>{label}</span> &nbsp; {live_n}/{total_n} tickers",
                    unsafe_allow_html=True)
        if plaid_snap:
            try:
                age_m = (datetime.datetime.now() - datetime.datetime.fromisoformat(plaid_snap["timestamp"])).seconds // 60
                st.markdown(f"<span class='tag tag-plaid'>Plaid</span> synced {age_m}m ago", unsafe_allow_html=True)
            except Exception:
                pass
    else:
        st.markdown("<span style='color:#64748b'>Press 🔄 Refresh for live prices</span>", unsafe_allow_html=True)

    st.markdown("---")

    # Reconciliation summary  (v11.2: shows intra-file dupes separately)
    st.markdown("**🗂️ Reconciliation**")
    tx_count = len(de._load(de.TX_STORE_PATH, {}))
    st.markdown(f"<code>{tx_count}</code> rows in tx_store", unsafe_allow_html=True)
    recon = de._load(de.RECON_LOG_PATH, [])
    if recon:
        last = recon[-1]
        with st.expander("Last upload detail"):
            st.write(f"**{last.get('new', 0)}** new rows added")
            st.write(f"**{last.get('cross_dupes', last.get('dupes', 0))}** cross-session dupes skipped")
            st.write(f"**{last.get('intra_dupes', 0)}** intra-file dupes skipped")
            st.write(f"**{last.get('no_code', 0)}** blank/footer rows skipped")
            st.write(f"Total rows in file: **{last.get('total_rows', 0)}**")

    pre_loaded = len(st.session_state.processed_ids)
    st.markdown(
        f"<span style='font-size:11px;color:#64748b'>"
        f"Pre-loaded: <code>{pre_loaded}</code> fingerprints in session</span>",
        unsafe_allow_html=True,
    )

    st.markdown("---")

    # AI Target Engine
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
            cs_save, cs_reset = st.columns(2)
            with cs_save:
                if st.button("💾 Save", use_container_width=True):
                    st.session_state.targets = new_targets
                    de._save(de.TARGETS_PATH, new_targets)
                    targets = new_targets
                    st.success("Saved")
            with cs_reset:
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

use_plaid = False
if plaid_snap and plaid_snap.get("total_equity", 0) > 0:
    try:
        snap_age = (datetime.datetime.now() - datetime.datetime.fromisoformat(plaid_snap["timestamp"])).total_seconds() / 3600
        use_plaid = snap_age < 2.0
    except Exception:
        use_plaid = True

display_total  = plaid_snap["total_equity"]   if use_plaid else totals["total"]
display_stocks = plaid_snap["stocks_equity"]  if use_plaid else totals["stocks"]
display_crypto = plaid_snap["crypto_equity"]  if use_plaid else totals["crypto"]
pnl_color      = "#22c55e" if totals["pnl"] >= 0 else "#ef4444"
pnl_sign       = "+" if totals["pnl"] >= 0 else ""
source_badge   = (
    '<span class="tag tag-plaid">Plaid</span>' if use_plaid
    else '<span style="color:#64748b;font-size:10px">estimated</span>'
)

_hdr_left, _hdr_right = st.columns([8, 1])
with _hdr_left:
    st.markdown("<h1 style='margin-bottom:2px'>⚡ Portfolio War Room</h1>", unsafe_allow_html=True)
    st.markdown(
        f"<div style='color:#64748b;font-size:13px;margin-bottom:18px'>"
        f"{len(portfolio)} positions · v13 · Smart Sync · DRIP Analytics · Override Log"
        f"</div>",
        unsafe_allow_html=True,
    )
with _hdr_right:
    # v13: ☰ toggle — always in main area, sidebar is NEVER unrecoverable
    if st.button("☰", key="sidebar_toggle", help="Toggle sidebar",
                 use_container_width=True):
        st.session_state.sidebar_open = not st.session_state.sidebar_open
        st.rerun()

c1, c2, c3, c4, c5 = st.columns(5)
for col, label, value, sub, vcol in [
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
            f"<div class='kpi-value' style='color:{vcol}'>{value}</div>"
            f"<div class='kpi-sub'>{sub}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )

st.markdown("---")

# ═══════════════════════════════════════════════════════════════════════════════
# TABS  (11 tabs — DRIP Analytics added as tab 10)
# ═══════════════════════════════════════════════════════════════════════════════
tabs = st.tabs([
    "🎯 Actions", "📊 Portfolio", "⚖️ Rebalancing",
    "💰 Invest $900", "📋 Decision Log", "📅 Schedule",
    "📈 Charts", "🕐 History", "📥 Import", "🧪 Tests",
    "💸 DRIP Analytics",
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
            cat   = r["cat"]
            css   = {"sell":"rec-sell","buy":"rec-buy","trim":"rec-trim",
                     "hold":"rec-hold","review":"rec-review"}.get(cat, "rec-hold")
            tag   = {"sell":"tag-sell","buy":"tag-buy","trim":"tag-trim",
                     "hold":"tag-hold","review":"tag-review"}.get(cat, "tag-hold")
            pnl_c = "#22c55e" if r["pnl_pct"] >= 0 else "#ef4444"
            proc  = f" · Est. proceeds: <b>${r['proceeds']:,.0f}</b>" if r["proceeds"] > 0 else ""
            pn    = f"${r['price']:,.2f}" if prices.get(r["ticker"]) else f"${r['cost']:,.2f} (cost)"
            st.markdown(
                f"<div class='rec-card {css}'>"
                f"<span class='tag {tag}'>{cat.upper()}</span>"
                f"<b style='font-size:15px'>{r['ticker']}</b>"
                f"<span style='color:#64748b;font-size:12px'> · {r['shares']:.4f} sh · {pn} · {r['category']}</span><br/>"
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
            st.success("Snapshot saved ✅")

# ─────────────────────────────────────────────────────────────
# TAB 1 — PORTFOLIO
# ─────────────────────────────────────────────────────────────
with tabs[1]:
    st.markdown("### Holdings")

    if plaid_snap and plaid_snap.get("positions"):
        triggered = plaid_snap.get("plaid_sync_triggered", False)
        cache_age = plaid_snap.get("holdings_cache_age_h", 0)
        sync_info = "fresh Plaid sync" if triggered else f"holdings cache {cache_age:.1f}h old"
        st.markdown(
            f"<span class='tag tag-plaid'>Plaid</span> "
            f"**{len(plaid_snap['positions'])} positions** · {sync_info} · quantities authoritative",
            unsafe_allow_html=True,
        )
        rows = [{
            "Ticker":       p["ticker"],
            "Shares":       round(p["quantity"], 6),
            "Mid Price":    round(p["mid_price"], 4),
            "Market Value": round(p["market_value"], 2),
            "Avg Cost":     round(p["avg_cost_basis"], 2),
            "Unreal P&L":   round(p["unrealised_pnl"], 2),
            "P&L %":        round(p["unrealised_pct"], 1),
            "Source":       p.get("price_source", "?"),
        } for p in plaid_snap["positions"]]
        df = pd.DataFrame(rows)
        st.dataframe(
            df.style.map(
                lambda v: ("color:#22c55e" if isinstance(v,float) and v>0
                           else "color:#ef4444" if isinstance(v,float) and v<0 else ""),
                subset=["P&L %", "Unreal P&L"],
            ),
            use_container_width=True, height=500,
            column_config={
                "Market Value": st.column_config.NumberColumn(format="$%.2f"),
                "Avg Cost":     st.column_config.NumberColumn(format="$%.4f"),
                "Mid Price":    st.column_config.NumberColumn(format="$%.4f"),
                "Unreal P&L":   st.column_config.NumberColumn(format="$%.2f"),
            },
        )
    else:
        rows = []
        for ticker, pos in sorted(portfolio.items(),
                                  key=lambda x: -de._safe_price(x[0],x[1],prices)*x[1]["shares"]):
            p   = de._safe_price(ticker, pos, prices)
            mkt = p * pos["shares"]
            pnl = (p - pos["avg_cost"]) / pos["avg_cost"] * 100 if pos["avg_cost"] > 0 else 0
            lt  = de.is_lt_eligible(pos.get("first_buy_date",""))
            rows.append({
                "Ticker":       ticker,
                "Shares":       round(pos["shares"], 6),
                "Avg Cost":     round(pos["avg_cost"], 4),
                "Live Price":   round(p, 4),
                "Market Value": round(mkt, 2),
                "P&L %":        round(pnl, 1),
                "LT?":          "✅" if lt else f"⏳ {de.days_to_lt(pos.get('first_buy_date',''))}d",
                "Category":     pos.get("category","Stocks"),
            })
        df = pd.DataFrame(rows)
        st.dataframe(
            df.style.map(
                lambda v: ("color:#22c55e" if isinstance(v,float) and v>0
                           else "color:#ef4444" if isinstance(v,float) and v<0 else ""),
                subset=["P&L %"],
            ),
            use_container_width=True, height=520,
            column_config={
                "Market Value": st.column_config.NumberColumn(format="$%.2f"),
                "Avg Cost":     st.column_config.NumberColumn(format="$%.4f"),
                "Live Price":   st.column_config.NumberColumn(format="$%.4f"),
            },
        )

    with st.expander("🔍 Position Detail"):
        sel = st.selectbox("Ticker", sorted(portfolio.keys()), key="pos_detail")
        if sel:
            pos = portfolio[sel]
            p   = de._safe_price(sel, pos, prices)
            mkt = p * pos["shares"]
            pnl = (p - pos["avg_cost"]) / pos["avg_cost"] * 100 if pos["avg_cost"] > 0 else 0
            r1, r2, r3 = st.columns(3)
            r1.metric("Live Price",   f"${p:,.4f}")
            r2.metric("Market Value", f"${mkt:,.2f}")
            r3.metric("P&L",          f"{pnl:+.1f}%")
            r4, r5, r6 = st.columns(3)
            r4.metric("Avg Cost",     f"${pos['avg_cost']:,.4f}")
            r5.metric("Shares",       f"{pos['shares']:.6f}")
            r6.metric("LT Eligible?", "Yes ✅" if de.is_lt_eligible(pos.get("first_buy_date",""))
                      else f"No — {de.days_to_lt(pos.get('first_buy_date',''))} days")
            tgt = de.TARGETS.get(sel)
            if tgt:
                up = (tgt - p) / p * 100 if p > 0 else 0
                st.metric("Analyst Target", f"${tgt:,.0f}", delta=f"{up:+.0f}% upside")

# ─────────────────────────────────────────────────────────────
# TAB 2 — REBALANCING  (v11.2: cash_available wired in)
# ─────────────────────────────────────────────────────────────
with tabs[2]:
    if not targets:
        st.info("Click **✨ Generate AI Targets** in the sidebar first.")
    elif not prices:
        st.info("Press 🔄 Refresh to load prices.")
    else:
        st.markdown("### ⚖️ Portfolio Drift vs Targets")

        # Cash toggle
        include_cash_rebal = st.checkbox(
            f"Include Robinhood cash (${cash:,.2f}) in drift calculation",
            value=True,
            help="When checked, cash is treated as part of total assets so underweight "
                 "positions get a 'cash_to_deploy' allocation from available balance.",
        )
        cash_for_rebal = cash if include_cash_rebal else 0.0

        rebal = de.compute_rebalancing(portfolio, prices, targets, cash_available=cash_for_rebal)

        if cash_for_rebal > 0:
            st.caption(
                f"💵 ${cash_for_rebal:,.2f} idle cash distributed across "
                f"{sum(1 for r in rebal if r['cash_to_deploy']>0)} underweight positions"
            )

        # Drift chart
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

        # Drift table with cash_to_deploy column
        df_r = pd.DataFrame(rebal)
        cols_show = ["ticker","current_pct","target_pct","drift","action","market_value","cash_to_deploy"]
        df_r = df_r[[c for c in cols_show if c in df_r.columns]]
        df_r.columns = [c.replace("_"," ").title() for c in df_r.columns]
        st.dataframe(
            df_r,
            use_container_width=True,
            column_config={
                "Market Value":    st.column_config.NumberColumn(format="$%.0f"),
                "Cash To Deploy":  st.column_config.NumberColumn(format="$%.2f"),
            },
        )

# ─────────────────────────────────────────────────────────────
# TAB 3 — INVEST $900  (v11.2: cash-informed + override inputs)
# ─────────────────────────────────────────────────────────────
with tabs[3]:
    dep_num  = st.session_state.deposit_num
    fridays  = de.get_biweekly_dates(datetime.date(2026, 4, 3), n=18)
    today    = datetime.date.today()
    cur_idx  = next((i for i, d in enumerate(fridays) if d >= today), 0)
    cur_date = fridays[cur_idx] if cur_idx < len(fridays) else fridays[-1]

    st.markdown(f"### 💰 Deposit #{dep_num} — {cur_date.strftime('%B %d, %Y')}")

    # ── Capital summary ───────────────────────────────────────────────────────
    dep_amount = st.number_input(
        "New deposit amount ($)", value=900.0, min_value=0.0, step=50.0, format="%.2f",
        help="Change this if your biweekly deposit differs from $900",
    )
    total_investable = dep_amount + cash

    ki1, ki2, ki3 = st.columns(3)
    ki1.metric("New Deposit",         f"${dep_amount:,.2f}")
    ki2.metric("Robinhood Cash",       f"${cash:,.2f}")
    ki3.metric("Total Investable 💡",  f"${total_investable:,.2f}",
               help="Both the deposit AND existing cash are put to work in the allocations below.")

    if cash > 0:
        st.info(
            f"💵 Your **${cash:,.2f}** Robinhood cash is included in this allocation. "
            f"Total investable capital: **${total_investable:,.2f}**."
        )
    if not prices:
        st.caption("⚠️ Press 🔄 Refresh for accurate share estimates.")

    st.markdown("---")

    # ── Generate base AI recs ─────────────────────────────────────────────────
    base_recs = de.generate_deposit_recs(
        dep_num, portfolio, prices, targets,
        amount=dep_amount, cash_balance=cash,
    )

    # Use final recs (post-override) if already applied, else base
    active_recs = (
        st.session_state.dep_recs_final
        if st.session_state.overrides_applied and st.session_state.dep_recs_final
        else base_recs
    )

    # ── Override input form ───────────────────────────────────────────────────
    st.markdown("#### AI Recommendations + Manual Overrides")
    st.caption(
        "Enter a custom dollar amount in the **Override ($)** column to deviate "
        "from the AI recommendation. Leave blank (0) to accept the AI amount. "
        "All overrides are logged to the **📋 Decision Log** tab."
    )

    override_inputs: dict[str, float] = {}
    reason_inputs:   dict[str, str]   = {}

    for r in base_recs:
        ticker = r["ticker"]
        ai_amt = r["amount"]
        price  = r.get("price", 0)
        fc     = r.get("from_cash", 0)
        est_sh = r.get("est_shares", 0)
        why    = r.get("why", "")
        ovd    = st.session_state.dep_overrides.get(ticker, 0.0)

        with st.container():
            col_t, col_ai, col_ov, col_why = st.columns([2, 2, 2, 3])
            with col_t:
                st.markdown(
                    f"<div style='padding-top:28px'>"
                    f"<b style='font-size:15px'>{ticker}</b>"
                    f"<br/><span style='font-size:11px;color:#64748b'>"
                    f"${price:,.2f} · {why}</span></div>",
                    unsafe_allow_html=True,
                )
            with col_ai:
                st.markdown(
                    f"<div style='padding-top:8px'>"
                    f"<div style='font-size:10px;color:#64748b;text-transform:uppercase'>AI Rec</div>"
                    f"<div style='font-size:18px;font-weight:700;color:#38bdf8'>${ai_amt:,.2f}</div>"
                    f"<div style='font-size:11px;color:#64748b'>~{est_sh} sh"
                    f"{'  · ' + f'${fc:.2f} cash' if fc > 0 else ''}</div></div>",
                    unsafe_allow_html=True,
                )
            with col_ov:
                override_inputs[ticker] = st.number_input(
                    "Override ($)", value=ovd, min_value=0.0, step=10.0,
                    format="%.2f", key=f"ovd_{ticker}",
                    label_visibility="visible",
                )
            with col_why:
                reason_inputs[ticker] = st.text_input(
                    "Reason (optional)", value=st.session_state.dep_reasons.get(ticker, ""),
                    key=f"rsn_{ticker}", placeholder="e.g. waiting for earnings",
                )
        st.markdown("<hr style='border:none;border-top:1px solid #1e2d47;margin:4px 0'>",
                    unsafe_allow_html=True)

    # ── Override summary before applying ─────────────────────────────────────
    active_overrides = {t: v for t, v in override_inputs.items() if v > 0}
    if active_overrides:
        st.markdown(
            f"<span class='tag tag-override'>OVERRIDES PENDING</span> "
            f"{len(active_overrides)} position(s) modified",
            unsafe_allow_html=True,
        )

    col_apply, col_reset, col_mark = st.columns([2, 1, 2])
    with col_apply:
        if st.button("✅ Apply Overrides & Lock Plan", use_container_width=True,
                     type="primary" if active_overrides else "secondary"):
            # Only apply overrides where the user entered a non-zero value
            final_overrides = {t: v for t, v in override_inputs.items() if v > 0}
            final_reasons   = {t: reason_inputs.get(t,"") for t in final_overrides}
            st.session_state.dep_overrides     = final_overrides
            st.session_state.dep_reasons       = final_reasons
            st.session_state.dep_recs_final    = de.apply_overrides_to_recs(
                base_recs, final_overrides, final_reasons, dep_num
            )
            st.session_state.overrides_applied = True
            n = len(final_overrides)
            st.success(
                f"Plan locked ✅  {n} override{'s' if n!=1 else ''} logged to Decision Log."
                if n else "Plan locked ✅  No overrides — using AI allocations."
            )
            st.rerun()

    with col_reset:
        if st.button("🔁 Reset", use_container_width=True):
            st.session_state.dep_overrides     = {}
            st.session_state.dep_reasons       = {}
            st.session_state.dep_recs_final    = []
            st.session_state.overrides_applied = False
            st.rerun()

    # ── Final allocation table ────────────────────────────────────────────────
    st.markdown("---")
    st.markdown(
        f"#### {'🔒 Locked Plan (with overrides)' if st.session_state.overrides_applied else '📊 AI Plan Preview'}"
    )

    display_recs = active_recs
    rows_disp = []
    for r in display_recs:
        ovd_flag = r.get("overridden", False)
        delta    = r.get("override_delta", 0.0)
        rows_disp.append({
            "Ticker":        r["ticker"],
            "Amount ($)":    r["amount"],
            "AI Amount ($)": r.get("amount", 0) if not ovd_flag else
                             round(r["amount"] - delta, 2),
            "Override":      "✏️ YES" if ovd_flag else "—",
            "Delta ($)":     delta if ovd_flag else 0.0,
            "Est. Shares":   r["est_shares"],
            "Price":         r.get("price", 0),
            "From Cash":     r.get("from_cash", 0),
            "Why":           r["why"],
        })

    df_dep = pd.DataFrame(rows_disp)
    totals_row_amount  = df_dep["Amount ($)"].sum()
    totals_row_from_cash = df_dep["From Cash"].sum()

    def _color_delta(v):
        if isinstance(v, float) and v != 0:
            return "color:#22c55e" if v > 0 else "color:#ef4444"
        return ""

    st.dataframe(
        df_dep.style.map(_color_delta, subset=["Delta ($)"]),
        use_container_width=True,
        hide_index=True,
        column_config={
            "Amount ($)":    st.column_config.NumberColumn(format="$%.2f"),
            "AI Amount ($)": st.column_config.NumberColumn(format="$%.2f"),
            "Delta ($)":     st.column_config.NumberColumn(format="$%.2f"),
            "Est. Shares":   st.column_config.NumberColumn(format="%.4f"),
            "Price":         st.column_config.NumberColumn(format="$%.2f"),
            "From Cash":     st.column_config.NumberColumn(format="$%.2f"),
        },
    )

    # Totals row
    tc1, tc2, tc3, tc4 = st.columns(4)
    tc1.metric("Total Deployed",    f"${totals_row_amount:,.2f}")
    tc2.metric("From New Deposit",  f"${totals_row_amount - totals_row_from_cash:,.2f}")
    tc3.metric("From Cash Balance", f"${totals_row_from_cash:,.2f}")
    tc4.metric("Remaining Cash",    f"${max(0, cash - totals_row_from_cash):,.2f}")

    with col_mark:
        if st.button("📌 Mark Deposit Done", use_container_width=True):
            de.log_deposit(dep_num, str(cur_date), display_recs, totals_row_amount)
            st.session_state.deposit_num    += 1
            st.session_state.dep_overrides   = {}
            st.session_state.dep_reasons     = {}
            st.session_state.dep_recs_final  = []
            st.session_state.overrides_applied = False
            st.success(f"Deposit #{dep_num} logged! Next: #{dep_num+1}")
            st.rerun()

    st.markdown("---")
    st.markdown("### 📅 All Deposits — 2026 Schedule")
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
# TAB 4 — DECISION LOG  (new in v11.2)
# ─────────────────────────────────────────────────────────────
with tabs[4]:
    st.markdown("### 📋 Decision Log — Manual Override History")
    st.caption(
        "Every time you override an AI recommendation in the Invest tab and "
        "click **Apply Overrides**, the decision is permanently saved here."
    )

    log_entries = de.load_decision_log()

    if not log_entries:
        st.info("No override decisions logged yet. Use the **Override ($)** inputs in the **💰 Invest $900** tab.")
    else:
        # Summary metrics
        total_decisions = len(log_entries)
        tickers_overridden = len({e["ticker"] for e in log_entries})
        total_delta        = sum(e.get("delta", 0) for e in log_entries)
        avg_delta          = total_delta / total_decisions if total_decisions else 0

        dm1, dm2, dm3, dm4 = st.columns(4)
        dm1.metric("Total Decisions",  total_decisions)
        dm2.metric("Unique Tickers",   tickers_overridden)
        dm3.metric("Net Delta",        f"${total_delta:+,.2f}",
                   help="Sum of (Manual − AI) across all overrides. + = you invested more than AI suggested.")
        dm4.metric("Avg Override Delta", f"${avg_delta:+,.2f}")

        st.markdown("---")

        # Dataframe
        df_log = pd.DataFrame(log_entries)

        # Pretty column ordering and naming
        col_order = ["date","ticker","deposit_num","ai_rec_amount","manual_amount",
                     "delta","reason","action_type","timestamp"]
        df_log = df_log[[c for c in col_order if c in df_log.columns]]
        df_log.columns = [c.replace("_"," ").title() for c in df_log.columns]

        def _color_delta_log(v):
            if isinstance(v, (int,float)) and v != 0:
                return "color:#22c55e;font-weight:600" if v > 0 else "color:#ef4444;font-weight:600"
            return ""

        st.dataframe(
            df_log.style.map(_color_delta_log, subset=["Delta"] if "Delta" in df_log.columns else []),
            use_container_width=True, hide_index=True,
            column_config={
                "Ai Rec Amount":   st.column_config.NumberColumn(format="$%.2f"),
                "Manual Amount":   st.column_config.NumberColumn(format="$%.2f"),
                "Delta":           st.column_config.NumberColumn(format="$%.2f"),
            },
        )

        # CSV export
        csv_bytes = df_log.to_csv(index=False).encode("utf-8")
        st.download_button(
            label="⬇️ Export Decision Log (CSV)",
            data=csv_bytes,
            file_name=f"decision_log_{datetime.date.today()}.csv",
            mime="text/csv",
        )

        # Per-ticker summary
        st.markdown("---")
        st.markdown("#### Override Frequency by Ticker")
        ticker_counts = df_log.groupby("Ticker")["Delta"].agg(
            count="count", total_delta="sum", avg_delta="mean"
        ).reset_index()
        ticker_counts.columns = ["Ticker","Override Count","Total Delta ($)","Avg Delta ($)"]
        st.dataframe(ticker_counts.sort_values("Override Count", ascending=False),
                     use_container_width=True, hide_index=True)

# ─────────────────────────────────────────────────────────────
# TAB 5 — SCHEDULE
# ─────────────────────────────────────────────────────────────
with tabs[5]:
    st.markdown("### 📅 2026 Action Calendar")
    calendar_events = [
        ("Apr 3",  "SELL",  "VTV, VEA, VWO, BND",  "LT eligible now — pay 15%. Reinvest into VOO+VYM same day."),
        ("Apr 3",  "BUY",   "Deposit #1",           "$900 → NVDA($252), VOO($198), VYM($153), QQQ($153), META($144)"),
        ("Apr 4",  "TRIM",  "GLD",                  "GLD turns LT — trim 25% near $450 target"),
        ("Apr 17", "BUY",   "Deposit #2",           "$900 → NVDA, VOO, VYM, QQQ, GOOGL"),
        ("May 20", "SELL",  "SPY",                  "SPY turns LT — sell, buy VOO same day"),
        ("Jul 15", "SELL",  "VUG",                  "VUG turns LT — sell, buy QQQ same day"),
        ("Aug 14", "EVAL",  "BLSH",                 "BLSH hits 1yr — trim 25% if up >20%"),
        ("Sep 11", "EVAL",  "KLAR",                 "KLAR hits 1yr — trim 25% if up >20%"),
        ("Nov 6",  "TRIM",  "TSM",                  "Big TSM lot turns LT — trim 20%"),
        ("Dec 15", "TRIM",  "GOOGL",                "Big GOOGL lot turns LT — trim 20%"),
        ("Dec 20", "TAX",   "Year-end Harvest",     "Net realized gains vs losses before Dec 31"),
    ]
    _mm = {"Apr":"04","May":"05","Jun":"06","Jul":"07","Aug":"08",
           "Sep":"09","Oct":"10","Nov":"11","Dec":"12"}
    tag_map = {"SELL":"tag-sell","BUY":"tag-buy","TRIM":"tag-trim","EVAL":"tag-hold","TAX":"tag-review"}
    for date_s, etype, ticker, notes in calendar_events:
        parts = date_s.split()
        try:
            ev = datetime.date(2026, int(_mm.get(parts[0][:3],"04")), int(parts[1]))
            opacity = "0.4" if ev < today else "1.0"
        except Exception:
            opacity = "1.0"
        past = " · <span style='color:#475569'>done</span>" if opacity == "0.4" else ""
        st.markdown(
            f"<div style='opacity:{opacity};margin-bottom:8px;padding:10px 14px;"
            f"border-radius:10px;background:#0d111a;border:1px solid #1e2d47'>"
            f"<span class='tag {tag_map.get(etype, 'tag-hold')}'>{etype}</span>"
            f"<b>{date_s}</b> &nbsp; {ticker}{past}<br/>"
            f"<span style='color:#94a3b8;font-size:12px'>{notes}</span></div>",
            unsafe_allow_html=True,
        )
    st.markdown("---")
    st.markdown("### 🧾 Tax Playbook")
    for rule, title, detail in [
        ("Rule #1","Never sell < 1 year","Pay 37% (ST) vs 15% (LT). Always worth the wait."),
        ("Rule #2","ETF swaps are NOT wash sales","Selling SPY → buying VOO same day is allowed."),
        ("Rule #3","DRIP creates new lots","Each reinvestment is a separate tax lot."),
        ("Rule #4","Crypto: never sell short-term","BTC/XRP both held >1yr. LT rate applies."),
        ("Rule #5","Year-end harvest","Net gains vs losses before Dec 31."),
    ]:
        st.markdown(
            f"<div style='margin-bottom:8px;padding:10px 14px;border-radius:10px;"
            f"background:#07100d;border-left:3px solid #22c55e'>"
            f"<span style='color:#22c55e;font-size:11px;font-weight:700'>{rule}</span>"
            f" &nbsp;<b>{title}</b><br/>"
            f"<span style='color:#94a3b8;font-size:12px'>{detail}</span></div>",
            unsafe_allow_html=True,
        )

# ─────────────────────────────────────────────────────────────
# TAB 6 — CHARTS
# ─────────────────────────────────────────────────────────────
with tabs[6]:
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
                marker_color=["#22c55e" if v >= 0 else "#ef4444" for v in pnl_df["pnl"]],
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
            cat = pos.get("category","Stocks")
            cat_data[cat] = cat_data.get(cat, 0) + mkt
        cat_df = pd.DataFrame(list(cat_data.items()), columns=["Category","Value"])
        fig_cat = px.bar(cat_df, x="Category", y="Value", text_auto="$.0f",
                         color="Category", color_discrete_sequence=["#38bdf8","#22c55e","#f59e0b"])
        fig_cat.update_layout(
            template="plotly_dark", paper_bgcolor="#07090f", plot_bgcolor="#07090f",
            height=280, margin=dict(l=20,r=20,t=20,b=20), showlegend=False,
        )
        st.plotly_chart(fig_cat, use_container_width=True)

# ─────────────────────────────────────────────────────────────
# TAB 7 — HISTORY
# ─────────────────────────────────────────────────────────────
with tabs[7]:
    st.markdown("### 📸 Recommendation History")
    history = de._load(de.REC_HISTORY_PATH, [])
    if not history:
        st.info("No snapshots yet. Click **Save Snapshot** in the Actions tab.")
    else:
        h_rows = []
        for snap in reversed(history[-50:]):
            t = snap.get("totals", {})
            h_rows.append({
                "Timestamp":    snap.get("timestamp","")[:16].replace("T"," "),
                "Total Equity": round(t.get("total",0),2),
                "Stocks":       round(t.get("stocks",0),2),
                "Crypto":       round(t.get("crypto",0),2),
                "P&L %":        round(t.get("pnl_pct",0),2),
                "# Recs":       len(snap.get("recs",[])),
            })
        h_df = pd.DataFrame(h_rows)
        st.dataframe(h_df, use_container_width=True,
                     column_config={
                         "Total Equity": st.column_config.NumberColumn(format="$%.2f"),
                         "Stocks":       st.column_config.NumberColumn(format="$%.2f"),
                         "Crypto":       st.column_config.NumberColumn(format="$%.2f"),
                     })
        if len(h_rows) > 1:
            fig_line = px.line(pd.DataFrame(h_rows)[["Timestamp","Total Equity"]],
                               x="Timestamp", y="Total Equity",
                               markers=True, line_shape="spline",
                               color_discrete_sequence=["#38bdf8"])
            fig_line.update_layout(
                template="plotly_dark", paper_bgcolor="#07090f", plot_bgcolor="#07090f",
                height=280, margin=dict(l=20,r=20,t=20,b=20),
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
# TAB 8 — IMPORT  (v11.2: richer dedup panel)
# ─────────────────────────────────────────────────────────────
with tabs[8]:
    st.markdown("### 📥 Import Robinhood Activity")
    c_csv, c_pdf = st.columns(2)

    with c_csv:
        st.markdown("#### CSV — Transaction History")
        st.caption("Robinhood → Account → History → Export CSV")

        # v11.2: show existing fingerprint count so user knows dedup is active
        existing_fp_count = len(st.session_state.processed_ids)
        st.markdown(
            f"<div style='margin-bottom:8px;padding:8px 12px;border-radius:8px;"
            f"background:#0f172a;border:1px solid #1e2d47;font-size:12px'>"
            f"<span class='dedup-ok'>🔒 Dedup active</span> — "
            f"<code>{existing_fp_count}</code> fingerprints loaded. "
            f"Duplicate rows will be automatically skipped.</div>",
            unsafe_allow_html=True,
        )

        uploaded_csv = st.file_uploader(
            "Drop CSV here", type=["csv"], key="csv_up", label_visibility="collapsed"
        )
        if uploaded_csv:
            # Always pass the full set: disk + session.
            # ingest_csv() will clear existing_ids in-place if bootstrap→live.
            existing = de.strip_existing_tx_store_fingerprints() | st.session_state.processed_ids
            stats, new_ids = de.ingest_csv(uploaded_csv.read(), existing)

            if stats.mode_transitioned:
                # Bootstrap rows were wiped; session must start fresh
                st.session_state.processed_ids = new_ids
                st.info("🔄 Bootstrap data cleared. Rebuilding from real transactions…")
            else:
                st.session_state.processed_ids |= new_ids

            if stats.new_rows_added > 0:
                tx   = de._load(de.TX_STORE_PATH, {})
                cryp = de._load(de.CRYPTO_OVR_PATH, {})
                st.session_state.portfolio = de.recompute_portfolio(tx, cryp)
                portfolio = st.session_state.portfolio
                st.success(f"✅ {stats.new_rows_added} new rows added. Portfolio updated.")
            else:
                st.info("No new rows — all duplicates skipped. Portfolio unchanged.")

            # Detailed breakdown
            with st.expander("📊 Ingest Detail", expanded=stats.new_rows_added > 0):
                d1, d2, d3, d4 = st.columns(4)
                d1.metric("Total in file",       stats.total_rows_in_file)
                d2.metric("New rows added",       stats.new_rows_added,
                          delta=str(stats.new_rows_added) if stats.new_rows_added else None)
                d3.metric("Cross-session dupes",  stats.duplicate_rows_skipped)
                d4.metric("Intra-file dupes",     stats.seen_in_file)
                if stats.skipped_no_code:
                    st.caption(f"{stats.skipped_no_code} blank/footer rows skipped (no Trans Code)")
                if stats.errors:
                    st.warning(f"Errors: {stats.errors}")

    with c_pdf:
        st.markdown("#### PDF — Crypto Statement")
        st.caption("Robinhood Crypto monthly PDF")
        uploaded_pdf = st.file_uploader(
            "Drop PDF here", type=["pdf"], key="pdf_up", label_visibility="collapsed"
        )
        if uploaded_pdf:
            # v12: PDF import also triggers bootstrap→live if first real import
            if de.get_system_mode() == "bootstrap":
                de.transition_to_live()
                st.session_state.processed_ids = set()
                st.info("🔄 Bootstrap data cleared. Rebuilding from real data…")

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
                st.warning("Could not extract crypto data. Use manual override below.")

        st.markdown("---")
        st.markdown("**Manual Crypto Override**")
        ovr = de._load(de.CRYPTO_OVR_PATH, {})
        for coin in ["BTC","XRP","ETH","SOL"]:
            cur = ovr.get(coin, {})
            mc1, mc2 = st.columns(2)
            with mc1:
                sh = st.number_input(f"{coin} shares", value=float(cur.get("shares",0)),
                                     min_value=0.0, format="%.8f", key=f"ovr_sh_{coin}")
            with mc2:
                ac = st.number_input(f"{coin} avg cost", value=float(cur.get("avg_cost",0)),
                                     min_value=0.0, format="%.4f", key=f"ovr_ac_{coin}")
            if sh > 0:
                ovr[coin] = {"shares": sh, "avg_cost": ac, "first_buy_date": cur.get("first_buy_date","")}
        if st.button("💾 Save Crypto Overrides"):
            de._save(de.CRYPTO_OVR_PATH, ovr)
            tx = de._load(de.TX_STORE_PATH, {})
            st.session_state.portfolio = de.recompute_portfolio(tx, ovr)
            st.success("Crypto overrides saved ✅")

# ─────────────────────────────────────────────────────────────
# TAB 9 — TESTS
# ─────────────────────────────────────────────────────────────
with tabs[9]:
    st.markdown("### 🧪 Live System Tests")
    if st.button("▶️ Run All Tests"):
        results = []
        def _t(name, passed, detail=""):
            results.append({"Test": name, "Status": "✅ PASS" if passed else "❌ FAIL", "Detail": detail})

        # 1. tx_store
        tx = de._load(de.TX_STORE_PATH, {})
        _t("tx_store loaded", len(tx) > 0, f"{len(tx)} rows")

        # 2. Portfolio positions
        _t("Portfolio has positions", len(portfolio) > 0, f"{len(portfolio)} tickers")

        # 3. No zero-share positions
        zero = [t for t, p in portfolio.items() if p["shares"] <= 0]
        _t("No zero-share positions", len(zero) == 0, ", ".join(zero) or "none")

        # 4. Key tickers
        for t in ["NVDA","VOO","VYM"]:
            _t(f"{t} present", t in portfolio, f"{portfolio.get(t,{}).get('shares',0):.4f} sh")

        # 5. Dedup fingerprint function
        fp1 = de.make_tx_fingerprint("2024-01-01","Buy","NVDA","10","100","1000","2024-01-03")
        fp2 = de.make_tx_fingerprint("2024-01-01","Buy","NVDA","10","100","1000","2024-01-03")
        fp3 = de.make_tx_fingerprint("2024-01-01","Buy","NVDA","11","100","1100","2024-01-03")
        _t("Fingerprint deterministic", fp1 == fp2, f"hash={fp1[:12]}…")
        _t("Different rows ≠ same hash", fp1 != fp3, "different qty produces different hash")

        # 6. strip_existing fingerprints
        known = de.strip_existing_tx_store_fingerprints()
        _t("strip_existing returns set", isinstance(known, set), f"{len(known)} fingerprints")

        # 7. Smart sync status
        cs2 = de.get_holdings_cache_status()
        _t("HoldingsManager available", cs2["status"] not in ("unavailable","error"), cs2.get("label",""))

        # 8. Plaid + Finnhub keys
        de._load_env_from_secrets()
        _t("Plaid token set",  bool(os.environ.get("PLAID_ACCESS_TOKEN")), "Set in secrets")
        _t("Finnhub key set",  bool(os.environ.get("FINNHUB_API_KEY")),    "Set in secrets")

        # 9. Total equity > 0
        tt = de.portfolio_totals(portfolio, prices, cash)
        _t("Total equity > $0", tt["total"] > 0, f"${tt['total']:,.2f}")

        # 10. Recs
        dummy  = {t: p["avg_cost"] for t, p in portfolio.items()}
        recs_t = de.generate_recs(portfolio, dummy)
        _t("Recs for all positions", len(recs_t) == len(portfolio), f"{len(recs_t)}/{len(portfolio)}")
        required = {"ticker","action","cat","plain","why","pnl_pct"}
        missing  = [r["ticker"] for r in recs_t if not required.issubset(r.keys())]
        _t("All recs have required fields", len(missing) == 0, ", ".join(missing) or "ok")

        # 11. LT logic
        _t("LT logic 2023-01-01", de.is_lt_eligible("2023-01-01"), "should be True")
        _t("LT logic 2030-01-01", not de.is_lt_eligible("2030-01-01"), "should be False")

        # 12. Cash-informed deposit recs
        drecs_no_cash   = de.generate_deposit_recs(1, portfolio, dummy, {}, 900.0, cash_balance=0.0)
        drecs_with_cash = de.generate_deposit_recs(1, portfolio, dummy, {}, 900.0, cash_balance=100.0)
        total_no_cash   = sum(r["amount"] for r in drecs_no_cash)
        total_with_cash = sum(r["amount"] for r in drecs_with_cash)
        _t("Deposit no-cash sums $900",  abs(total_no_cash - 900) < 0.02,   f"${total_no_cash:.2f}")
        _t("Deposit with-cash > $900",   total_with_cash > 900,             f"${total_with_cash:.2f}")
        _t("from_cash field populated",  any(r.get("from_cash",0) > 0 for r in drecs_with_cash),
           "cash_balance flows into recs")

        # 13. apply_overrides
        test_recs      = [{"ticker":"NVDA","amount":252.0,"price":875.0,"est_shares":0.2880,"why":"test","from_cash":0}]
        overridden      = de.apply_overrides_to_recs(test_recs, {"NVDA": 300.0}, {"NVDA": "test reason"}, 99)
        _t("Override amount substituted", overridden[0]["amount"] == 300.0, f"${overridden[0]['amount']}")
        _t("Override delta correct",      abs(overridden[0]["override_delta"] - 48.0) < 0.01,
           f"Δ=${overridden[0]['override_delta']}")
        _t("Override flag set",           overridden[0]["overridden"] is True, "overridden=True")

        # 14. Decision log persisted
        dlog = de.load_decision_log()
        _t("Decision log readable", isinstance(dlog, list), f"{len(dlog)} entries on disk")

        # 15. Plaid snapshot
        _t("Plaid snapshot on disk",
           plaid_snap is not None and plaid_snap.get("total_equity",0) > 0,
           f"${plaid_snap.get('total_equity',0):,.2f}" if plaid_snap else "Not synced yet")

        # Render
        res_df  = pd.DataFrame(results)
        pass_n  = sum(1 for r in results if "PASS" in r["Status"])
        color   = "#22c55e" if pass_n == len(results) else "#f59e0b"
        st.markdown(
            f"<div style='color:{color};font-weight:700;font-size:16px'>"
            f"{pass_n}/{len(results)} tests passing</div>",
            unsafe_allow_html=True,
        )
        st.dataframe(
            res_df.style.map(
                lambda v: "color:#22c55e" if "PASS" in str(v) else "color:#ef4444",
                subset=["Status"],
            ),
            use_container_width=True, hide_index=True,
        )

# ─────────────────────────────────────────────────────────────
# TAB 10 — DRIP ANALYTICS
# ─────────────────────────────────────────────────────────────
with tabs[10]:
    drip.render_drip_dashboard(portfolio, prices)

# ─────────────────────────────────────────────────────────────
# FOOTER
# ─────────────────────────────────────────────────────────────
st.markdown(
    f"<div style='margin-top:40px;padding:14px;border-top:1px solid #1e2d47;"
    f"color:#475569;font-size:11px;text-align:center'>"
    f"Portfolio War Room v13 · {datetime.date.today().strftime('%B %d, %Y')} · "
    f"{len(portfolio)} positions · DRIP Analytics · sidebar fix · 3-layer dedup · "
    f"prashanthkrishnan91"
    f"</div>",
    unsafe_allow_html=True,
)
