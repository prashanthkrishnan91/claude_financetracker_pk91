"""
main_app.py — Portfolio War Room v11.4
All UI — zero business logic.

v11.4: Import tab uses new two-step ingest_csv() → commit_new_transactions() API.
       _init() pre-seeds processed_ids with strip_existing_tx_store_fingerprints().
       Three separate skip counters in import detail expander.
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
@import url('https://fonts.googleapis.com/css2?family=DM+Serif+Display&family=JetBrains+Mono:wght@400;600&family=DM+Sans:wght@300;400;500;600&display=swap');

html,body,[class*="css"]{font-family:'DM Sans',sans-serif;background:#07090f;color:#e2e8f0}
.stApp{background:#07090f}
#MainMenu,footer,header{visibility:hidden}
.block-container{padding:1.2rem 1.4rem 3rem;max-width:1440px}
h1,h2,h3{font-family:'DM Serif Display',serif;color:#f1f5f9}

/* KPI cards */
.kpi{background:#0f1117;border:1px solid #1e2535;border-radius:10px;padding:14px 16px;margin-bottom:8px}
.kpi-label{font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:.06em;margin-bottom:4px}
.kpi-value{font-size:22px;font-weight:700;font-family:'JetBrains Mono',monospace;line-height:1.1}
.kpi-sub{font-size:11px;color:#64748b;margin-top:3px}

/* Rec cards */
.rec-card{background:#0f1117;border:1px solid #1e2535;border-radius:10px;padding:14px 16px;margin-bottom:10px}
.rec-card.sell{border-left:3px solid #ef4444}
.rec-card.buy{border-left:3px solid #22c55e}
.rec-card.trim{border-left:3px solid #f59e0b}
.rec-card.review{border-left:3px solid #a855f7}
.rec-card.hold{border-left:3px solid #475569}

/* Tags */
.tag{display:inline-block;font-size:10px;font-weight:600;padding:2px 7px;border-radius:4px;margin-right:6px;text-transform:uppercase;letter-spacing:.05em}
.tag-sell{background:#450a0a;color:#ef4444}
.tag-buy{background:#052e16;color:#22c55e}
.tag-trim{background:#451a03;color:#f59e0b}
.tag-review{background:#2e1065;color:#a855f7}
.tag-hold{background:#1e293b;color:#94a3b8}
.tag-plaid{background:#1e3a5f;color:#60a5fa}

/* Sidebar */
.sidebar-badge{background:#1e2535;border-radius:8px;padding:10px 12px;margin-bottom:12px;font-size:12px}

/* Section headers */
.sec-head{font-family:'DM Serif Display',serif;font-size:20px;margin-bottom:12px;color:#f1f5f9}
</style>
""", unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════════════════
# COLD-START INIT
# ═══════════════════════════════════════════════════════════════════════════════

def _init():
    if "initialised" not in st.session_state:
        de.bootstrap_tx_store()
        # Pre-seed dedup IDs from disk so first upload never re-inserts old rows
        st.session_state.processed_ids   = de.strip_existing_tx_store_fingerprints()
        st.session_state.portfolio       = de.recompute_portfolio()
        st.session_state.prices          = {}
        st.session_state.recs            = []
        st.session_state.cash            = 1042.17
        st.session_state.deposit_num     = 1
        st.session_state.targets         = de.load_targets()
        st.session_state._bust           = 0
        st.session_state.plaid_snap      = None
        # Try loading existing Plaid snapshot from disk
        if de.PLAID_SNAPSHOT_PATH.exists():
            try:
                st.session_state.plaid_snap = json.loads(de.PLAID_SNAPSHOT_PATH.read_text())
            except Exception:
                pass
        st.session_state.initialised = True


def _refresh():
    st.session_state._bust += 1
    tickers = tuple(st.session_state.portfolio.keys())
    st.session_state.prices = de.fetch_prices(tickers, _bust=st.session_state._bust)
    st.session_state.recs   = de.generate_recs(st.session_state.portfolio, st.session_state.prices)


_init()

portfolio = st.session_state.portfolio
prices    = st.session_state.prices
recs      = st.session_state.recs
cash      = st.session_state.cash
targets   = st.session_state.targets
plaid_snap = st.session_state.plaid_snap

# ═══════════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ═══════════════════════════════════════════════════════════════════════════════
with st.sidebar:
    st.markdown("## ⚡ War Room")
    st.markdown(f"<div style='color:#64748b;font-size:12px;margin-bottom:16px'>v11.4 · {len(portfolio)} positions</div>", unsafe_allow_html=True)

    # Refresh button
    if st.button("🔄 Refresh Prices", type="primary", use_container_width=True):
        _refresh()
        st.rerun()

    # Plaid sync button
    if st.button("🏦 Sync Plaid", use_container_width=True,
                 help="Force Plaid holdings sync (uses 1 Plaid API call)"):
        with st.spinner("Syncing Plaid holdings…"):
            snap = de.smart_sync_portfolio(force_plaid=True)
        if snap:
            st.session_state.plaid_snap = snap
            plaid_snap = snap
            st.success("Plaid synced ✅")
        else:
            st.info("Plaid not configured — add PLAID_ACCESS_TOKEN to secrets.")

    st.markdown("---")

    # Smart Sync status badge
    cache_status = de.get_holdings_cache_status()
    status_color = "#22c55e" if not cache_status.get("is_stale") else "#f59e0b"
    st.markdown(
        f"<div class='sidebar-badge'>"
        f"<span style='color:{status_color}'>●</span> "
        f"<b>Smart Sync</b><br/>"
        f"<span style='color:#64748b;font-size:11px'>{cache_status.get('label','—')}</span>"
        f"</div>",
        unsafe_allow_html=True,
    )

    st.markdown("---")
    st.markdown("**💵 Cash Balance**")
    cash_input = st.number_input(
        "Robinhood cash ($)", min_value=0.0, value=cash, step=10.0,
        label_visibility="collapsed",
    )
    if cash_input != cash:
        st.session_state.cash = cash_input
        cash = cash_input
        st.rerun()

    st.markdown("---")
    st.markdown("**📅 Deposit #**")
    st.session_state.deposit_num = st.number_input(
        "Deposit number", min_value=1, value=st.session_state.deposit_num, step=1,
        label_visibility="collapsed",
    )

    st.markdown("---")
    st.markdown("**🎯 AI Targets** *(% allocation)*")
    for ticker in ["NVDA", "VOO", "VYM", "QQQ", "META", "GOOGL", "AAPL", "MSFT"]:
        curr = targets.get(ticker, 0.0)
        new_val = st.number_input(
            ticker, min_value=0.0, max_value=50.0, value=curr, step=0.5, key=f"tgt_{ticker}"
        )
        if new_val != curr:
            targets[ticker] = new_val
    if st.button("💾 Save Targets", use_container_width=True):
        de.save_targets(targets)
        st.session_state.targets = targets
        st.success("Saved ✅")


# ═══════════════════════════════════════════════════════════════════════════════
# HEADER KPIs
# ═══════════════════════════════════════════════════════════════════════════════
totals = de.portfolio_totals(portfolio, prices, cash)

# Prefer Plaid totals when available and recent (< 2h)
plaid_total  = None
display_total = totals["total"]
display_stocks = totals["stocks"]
display_crypto = totals["crypto"]

if plaid_snap:
    snap_ts = plaid_snap.get("timestamp") or plaid_snap.get("synced_at")
    if snap_ts:
        try:
            age_h = (datetime.datetime.now() - datetime.datetime.fromisoformat(snap_ts)).seconds / 3600
            if age_h < 2:
                plaid_total    = plaid_snap.get("total_equity")
                display_total  = plaid_total or display_total
                display_stocks = plaid_snap.get("stocks_equity", display_stocks)
                display_crypto = plaid_snap.get("crypto_equity", display_crypto)
        except Exception:
            pass

pnl_color    = "#22c55e" if totals["pnl"] >= 0 else "#ef4444"
pnl_sign     = "+" if totals["pnl"] >= 0 else ""
source_badge = ('<span class="tag tag-plaid">Plaid</span>' if plaid_total
                else '<span style="color:#64748b;font-size:10px">est.</span>')

st.markdown("<h1 style='font-family:DM Serif Display,serif;margin-bottom:2px'>⚡ Portfolio War Room</h1>", unsafe_allow_html=True)
st.markdown(
    f"<div style='color:#64748b;font-size:13px;margin-bottom:18px'>"
    f"{len(portfolio)} positions · v11.4 · Smart Sync · Cash-Informed Rebalancing · Fingerprint Dedup"
    f"</div>",
    unsafe_allow_html=True,
)

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
# TABS
# ═══════════════════════════════════════════════════════════════════════════════
tabs = st.tabs([
    "🎯 Actions", "📊 Portfolio", "⚖️ Rebalancing",
    "💰 Invest $900", "📋 Decision Log", "📅 Schedule",
    "📈 Charts", "🕐 History", "📥 Import", "🧪 Tests",
])


# ─────────────────────────────────────────────────────────────
# TAB 0 — ACTIONS
# ─────────────────────────────────────────────────────────────
with tabs[0]:
    if not prices:
        st.info("👆 Press **🔄 Refresh** in the sidebar to load live prices and generate recommendations.")
    else:
        sells   = [r for r in recs if r["category"] == "sell"]
        buys    = [r for r in recs if r["category"] == "buy"]
        trims   = [r for r in recs if r["category"] == "trim"]
        reviews = [r for r in recs if r["category"] == "review"]
        holds   = [r for r in recs if r["category"] == "hold"]

        def _rcard(r):
            cat   = r["category"]
            tag   = f"tag-{cat}"
            css   = cat
            pnl_c = "#22c55e" if r["pnl_pct"] >= 0 else "#ef4444"
            proc  = f" · proceeds: <b>${r['proceeds']:,.0f}</b>" if r["proceeds"] > 0 else ""
            live  = prices.get(r["ticker"])
            price_note = f"${r['price']:,.2f}" if live else f"${r['cost']:,.2f} (cost)"
            st.markdown(
                f"<div class='rec-card {css}'>"
                f"<span class='tag {tag}'>{cat.upper()}</span>"
                f"<b style='font-size:15px'>{r['ticker']}</b>"
                f"<span style='color:#64748b;font-size:12px'>"
                f" · {r['shares']:.4f} sh · {price_note} · {r['asset_cat']}</span><br/>"
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

    if plaid_snap and plaid_snap.get("positions"):
        triggered = plaid_snap.get("plaid_sync_triggered", False)
        cache_age = plaid_snap.get("holdings_cache_age_h", 0) or 0
        sync_info = "fresh Plaid sync" if triggered else f"cache {cache_age:.1f}h old"
        st.markdown(
            f"<span class='tag tag-plaid'>Plaid</span> "
            f"**{len(plaid_snap['positions'])} positions** · {sync_info} · quantities authoritative",
            unsafe_allow_html=True,
        )
        rows = []
        for pos in plaid_snap["positions"]:
            rows.append({
                "Ticker":       pos["ticker"],
                "Shares":       round(pos["quantity"], 6),
                "Mid Price":    round(pos["mid_price"], 4),
                "Market Value": round(pos["market_value"], 2),
                "Avg Cost":     round(pos.get("avg_cost_basis", 0), 2),
                "Unreal P&L":   round(pos.get("unrealised_pnl", 0), 2),
                "P&L %":        round(pos.get("unrealised_pct", 0), 1),
                "Source":       pos.get("price_source", "?"),
            })
        df = pd.DataFrame(rows)
        styled = df.style.map(
            lambda v: "color:#22c55e" if isinstance(v, (int, float)) and v > 0 else
                      ("color:#ef4444" if isinstance(v, (int, float)) and v < 0 else ""),
            subset=["Unreal P&L", "P&L %"],
        ).format({
            "Mid Price": "${:,.4f}", "Market Value": "${:,.2f}",
            "Avg Cost": "${:,.2f}", "Unreal P&L": "${:,.2f}", "P&L %": "{:+.1f}%",
        })
        st.dataframe(styled, use_container_width=True, hide_index=True)

    else:
        # tx_store positions
        rows = []
        for ticker, pos in portfolio.items():
            p      = de._safe_price(ticker, pos, prices)
            equity = p * pos["shares"]
            pnl    = (p - pos["avg_cost"]) / pos["avg_cost"] * 100 if pos["avg_cost"] > 0 else 0
            rows.append({
                "Ticker":      ticker,
                "Shares":      round(pos["shares"], 6),
                "Avg Cost":    round(pos["avg_cost"], 2),
                "Live Price":  round(p, 4),
                "Equity":      round(equity, 2),
                "P&L %":       round(pnl, 2),
                "LT?":         "✅" if de.is_lt_eligible(pos["first_buy_date"]) else f"⏳ {de.days_to_lt(pos['first_buy_date'])}d",
                "DRIP":        pos["drip_count"],
                "Category":    pos["category"],
            })
        df = pd.DataFrame(rows).sort_values("Equity", ascending=False)
        styled = df.style.map(
            lambda v: "color:#22c55e" if isinstance(v, (int, float)) and v > 0 else
                      ("color:#ef4444" if isinstance(v, (int, float)) and v < 0 else ""),
            subset=["P&L %"],
        ).format({
            "Avg Cost": "${:,.2f}", "Live Price": "${:,.4f}", "Equity": "${:,.2f}", "P&L %": "{:+.2f}%"
        })
        st.dataframe(styled, use_container_width=True, hide_index=True)


# ─────────────────────────────────────────────────────────────
# TAB 2 — REBALANCING
# ─────────────────────────────────────────────────────────────
with tabs[2]:
    st.markdown("### ⚖️ Portfolio Rebalancing")

    if not targets:
        st.info("Set AI Targets in the sidebar first (% allocation per ticker).")
    elif not prices:
        st.info("Refresh prices first.")
    else:
        include_cash = st.checkbox("Include Robinhood cash in rebalancing", value=True)
        cash_for_rebal = cash if include_cash else 0.0

        rebal = de.compute_rebalancing(portfolio, prices, targets, cash_available=cash_for_rebal)

        if rebal:
            df = pd.DataFrame(rebal)
            styled = df.style.map(
                lambda v: "color:#22c55e" if isinstance(v, (int, float)) and v < -2 else
                          ("color:#f59e0b" if isinstance(v, (int, float)) and v > 5 else ""),
                subset=["drift"],
            ).format({
                "equity": "${:,.2f}", "current": "{:.1f}%",
                "target": "{:.1f}%",  "drift": "{:+.1f}%",
                "cash_to_deploy": "${:,.2f}",
            })
            st.dataframe(styled, use_container_width=True, hide_index=True)

            # Drift bar chart
            fig = go.Figure()
            fig.add_bar(
                x=df["ticker"], y=df["drift"],
                marker_color=["#22c55e" if d < 0 else "#ef4444" for d in df["drift"]],
            )
            fig.update_layout(
                title="Drift from Target Allocation (%)",
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font_color="#e2e8f0", title_font_family="DM Serif Display",
            )
            fig.add_hline(y=0, line_dash="dash", line_color="#475569")
            st.plotly_chart(fig, use_container_width=True)


# ─────────────────────────────────────────────────────────────
# TAB 3 — INVEST $900
# ─────────────────────────────────────────────────────────────
with tabs[3]:
    st.markdown("### 💰 Biweekly $900 Deposit Plan")

    deposit_num = st.session_state.deposit_num
    next_date   = de.next_deposit_date()

    ci1, ci2, ci3 = st.columns(3)
    ci1.metric("New Deposit",     f"${de.DEPOSIT_AMOUNT:,.0f}")
    ci2.metric("Robinhood Cash",  f"${cash:,.2f}")
    ci3.metric("Total Investable", f"${de.DEPOSIT_AMOUNT + cash:,.2f}")

    if cash > 50:
        st.info(f"💵 ${cash:,.2f} idle cash will be deployed alongside the $900 deposit.")

    recs_dep = de.generate_deposit_recs(
        portfolio, prices, deposit_num=deposit_num,
        amount=de.DEPOSIT_AMOUNT, targets=targets, cash_balance=cash,
    )

    if "override_state" not in st.session_state:
        st.session_state.override_state = {}
    if "reason_state" not in st.session_state:
        st.session_state.reason_state = {}

    st.markdown("#### AI Allocation")
    for r in recs_dep:
        c1, c2, c3, c4 = st.columns([2, 2, 2, 3])
        c1.markdown(f"**{r['ticker']}** · {r['alloc_pct']}%")
        c2.markdown(f"${r['amount']:,.2f} → {r['est_shares']:.4f} sh")
        override_val = c3.number_input(
            f"Override ${r['ticker']}", min_value=0.0,
            value=float(st.session_state.override_state.get(r["ticker"], r["amount"])),
            step=10.0, label_visibility="collapsed", key=f"ovr_{r['ticker']}",
        )
        reason_val = c4.text_input(
            f"Reason {r['ticker']}", value=st.session_state.reason_state.get(r["ticker"], ""),
            label_visibility="collapsed", key=f"rsn_{r['ticker']}",
        )
        st.session_state.override_state[r["ticker"]] = override_val
        st.session_state.reason_state[r["ticker"]] = reason_val

    if st.button("🔒 Apply Overrides & Lock Plan", type="primary"):
        final_recs = de.apply_overrides_to_recs(
            recs_dep,
            st.session_state.override_state,
            st.session_state.reason_state,
            deposit_num,
        )
        total_locked = sum(r["amount"] for r in final_recs)
        st.success(f"Plan locked — total: ${total_locked:,.2f}")
        df_locked = pd.DataFrame([{
            "Ticker":   r["ticker"],
            "Amount":   f"${r['amount']:,.2f}",
            "Shares":   r["est_shares"],
            "Override": "✅" if r.get("overridden") else "",
            "Delta":    f"${r.get('override_delta', 0):+,.2f}" if r.get("overridden") else "—",
            "Why":      r["why"],
        } for r in final_recs])
        delta_styled = df_locked.style.map(
            lambda v: "color:#22c55e" if isinstance(v, str) and v.startswith("+") else
                      ("color:#ef4444" if isinstance(v, str) and v.startswith("-$") else ""),
            subset=["Delta"],
        )
        st.dataframe(delta_styled, use_container_width=True, hide_index=True)
        de.log_deposit(deposit_num, next_date.isoformat(), final_recs, total_locked)
        st.session_state.override_state = {}
        st.session_state.reason_state   = {}

    st.markdown("---")
    st.markdown("#### 📅 Full 2026 Deposit Schedule")
    schedule = de.deposit_schedule(19)
    st.dataframe(pd.DataFrame(schedule), use_container_width=True, hide_index=True)


# ─────────────────────────────────────────────────────────────
# TAB 4 — DECISION LOG
# ─────────────────────────────────────────────────────────────
with tabs[4]:
    st.markdown("### 📋 Decision Log — Override History")

    log_entries = de.load_decision_log()
    if not log_entries:
        st.info("No overrides logged yet. Override an AI recommendation in the Invest tab to see entries here.")
    else:
        df_log = pd.DataFrame(log_entries)
        total_overrides = len(df_log)
        total_extra     = df_log["delta"].sum() if "delta" in df_log.columns else 0
        unique_tickers  = df_log["ticker"].nunique() if "ticker" in df_log.columns else 0
        latest          = df_log["date"].max() if "date" in df_log.columns else "—"

        kl1, kl2, kl3, kl4 = st.columns(4)
        kl1.metric("Total Overrides",    total_overrides)
        kl2.metric("Unique Tickers",     unique_tickers)
        kl3.metric("Net Delta vs AI",    f"${total_extra:+,.2f}")
        kl4.metric("Last Override Date", latest)

        st.markdown("#### All Decisions")
        styled_log = df_log.style.map(
            lambda v: "color:#22c55e" if isinstance(v, (int, float)) and v > 0 else
                      ("color:#ef4444" if isinstance(v, (int, float)) and v < 0 else ""),
            subset=["delta"] if "delta" in df_log.columns else [],
        )
        st.dataframe(styled_log, use_container_width=True, hide_index=True)

        st.markdown("#### Override frequency by ticker")
        if "ticker" in df_log.columns:
            freq = df_log["ticker"].value_counts().reset_index()
            freq.columns = ["Ticker", "Overrides"]
            st.dataframe(freq, use_container_width=True, hide_index=True)

        # CSV export
        csv_bytes = df_log.to_csv(index=False).encode("utf-8")
        st.download_button("⬇️ Export to CSV", data=csv_bytes,
                           file_name="decision_log.csv", mime="text/csv")


# ─────────────────────────────────────────────────────────────
# TAB 5 — SCHEDULE
# ─────────────────────────────────────────────────────────────
with tabs[5]:
    st.markdown("### 📅 2026 Action Calendar")
    st.dataframe(pd.DataFrame(de.ACTION_CALENDAR), use_container_width=True, hide_index=True)

    st.markdown("---")
    st.markdown("### 🧾 Tax Playbook")
    st.markdown("""
**Rule #1 — Never sell short-term** — 37% ordinary income vs 15% LT cap gains. The wait is almost always worth it.

**Rule #2 — ETF swaps are NOT wash sales** — Sell SPY → buy VOO on the same day. Allowed.

**Rule #3 — DRIP lots** — Each reinvestment creates a new tax lot at that day's price. Tracked individually.

**Rule #4 — Crypto** — BTC/XRP both held >1yr now. LT rate applies. Never sell crypto short-term.

**Rule #5 — Year-end harvest** — Net realised gains vs losses before Dec 31. Check Dec 20 action.

**Sell order for LT-eligible legacy ETFs:** VTV → VEA → VWO → BND → (May 20) SPY → (Jul 15) VUG
    """)


# ─────────────────────────────────────────────────────────────
# TAB 6 — CHARTS
# ─────────────────────────────────────────────────────────────
with tabs[6]:
    st.markdown("### 📈 Portfolio Visualizations")

    if not prices:
        st.info("Refresh prices first.")
    else:
        rows_chart = []
        for ticker, pos in portfolio.items():
            p      = de._safe_price(ticker, pos, prices)
            equity = p * pos["shares"]
            pnl    = (p - pos["avg_cost"]) / pos["avg_cost"] * 100 if pos["avg_cost"] > 0 else 0
            rows_chart.append({"Ticker": ticker, "Equity": equity, "P&L %": round(pnl, 2), "Category": pos["category"]})

        df_c = pd.DataFrame(rows_chart).sort_values("Equity", ascending=False)

        col1, col2 = st.columns(2)
        with col1:
            fig_pie = px.pie(
                df_c, values="Equity", names="Ticker",
                title="Allocation by Position",
                hole=0.45, color_discrete_sequence=px.colors.sequential.Blues_r,
            )
            fig_pie.update_layout(
                paper_bgcolor="rgba(0,0,0,0)", font_color="#e2e8f0",
                title_font_family="DM Serif Display",
                legend=dict(font_size=10),
            )
            st.plotly_chart(fig_pie, use_container_width=True)

        with col2:
            fig_pnl = px.bar(
                df_c, x="Ticker", y="P&L %",
                title="Unrealised P&L by Position (%)",
                color="P&L %",
                color_continuous_scale=["#ef4444", "#f59e0b", "#22c55e"],
                color_continuous_midpoint=0,
            )
            fig_pnl.update_layout(
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font_color="#e2e8f0", title_font_family="DM Serif Display",
                showlegend=False,
            )
            fig_pnl.add_hline(y=0, line_dash="dash", line_color="#475569")
            st.plotly_chart(fig_pnl, use_container_width=True)

        # Category breakdown
        cat_df = df_c.groupby("Category")["Equity"].sum().reset_index()
        fig_cat = px.pie(
            cat_df, values="Equity", names="Category",
            title="Allocation by Category",
            color_discrete_sequence=["#3b82f6", "#22c55e", "#f59e0b"],
        )
        fig_cat.update_layout(
            paper_bgcolor="rgba(0,0,0,0)", font_color="#e2e8f0",
            title_font_family="DM Serif Display",
        )
        st.plotly_chart(fig_cat, use_container_width=True)


# ─────────────────────────────────────────────────────────────
# TAB 7 — HISTORY
# ─────────────────────────────────────────────────────────────
with tabs[7]:
    st.markdown("### 🕐 Portfolio History")

    history = de.load_rec_history()
    if history:
        st.markdown(f"**{len(history)} snapshots saved**")
        hist_rows = []
        for snap in history[-50:]:
            t = snap.get("totals", {})
            hist_rows.append({
                "Timestamp": snap.get("timestamp", ""),
                "Total":     f"${t.get('total', 0):,.2f}",
                "Stocks":    f"${t.get('stocks', 0):,.2f}",
                "Crypto":    f"${t.get('crypto', 0):,.2f}",
                "P&L %":     f"{t.get('pnl_pct', 0):+.1f}%",
            })
        st.dataframe(pd.DataFrame(hist_rows), use_container_width=True, hide_index=True)
    else:
        st.info("No snapshots yet. Click 📸 Save Snapshot in the Actions tab.")

    st.markdown("---")
    st.markdown("### 💰 Deposit Log")
    deposits = de.load_deposit_log()
    if deposits:
        dep_rows = []
        for d in deposits:
            dep_rows.append({
                "Deposit #":  d.get("num"),
                "Date":       d.get("date"),
                "Total ($)":  f"${d.get('total', 0):,.2f}",
                "Buys":       ", ".join(f"{b['ticker']}=${b['amount']:.0f}" for b in d.get("buys", [])),
            })
        st.dataframe(pd.DataFrame(dep_rows), use_container_width=True, hide_index=True)
    else:
        st.info("No deposits logged yet.")


# ─────────────────────────────────────────────────────────────
# TAB 8 — IMPORT  (v11.4: new two-step ingest API)
# ─────────────────────────────────────────────────────────────
with tabs[8]:
    st.markdown("### 📥 Import Robinhood Activity")

    n_known = len(st.session_state.get("processed_ids", set()))
    st.info(
        f"🔒 **Dedup active** — {n_known:,} fingerprints loaded. "
        "Re-uploading the same CSV is always safe — duplicates are silently skipped.",
        icon="🛡️",
    )

    uploaded = st.file_uploader(
        "Drop Robinhood account-activity CSV",
        type=["csv"],
        key="csv_uploader",
        help="Download from Robinhood → Account → Statements & History → Account Activity → Export CSV",
    )

    if uploaded is not None:
        if st.button("⬆️ Process CSV", type="primary"):
            csv_bytes = uploaded.read()
            with st.spinner("Parsing and deduplicating…"):
                new_rows, stats = de.ingest_csv(
                    csv_bytes=csv_bytes,
                    filename=uploaded.name,
                    existing_ids=st.session_state.processed_ids,
                )
                de.commit_new_transactions(new_rows)
                if stats.imported > 0:
                    st.session_state.portfolio = de.recompute_portfolio()
                    portfolio = st.session_state.portfolio
                    st.session_state._bust += 1

            if stats.imported > 0:
                st.success(f"✅ **{stats.imported} new transaction(s) imported** from *{stats.filename}*")
                if stats.new_tickers:
                    st.info(f"🆕 New tickers added: **{', '.join(sorted(stats.new_tickers))}**")
            else:
                st.warning("⚠️ No new transactions — all rows already exist in your store.")

            with st.expander("📋 Ingest detail", expanded=(stats.total_skipped > 0)):
                c1, c2, c3, c4, c5 = st.columns(5)
                c1.metric("Rows in file",       stats.total_rows)
                c2.metric("✅ Imported",         stats.imported)
                c3.metric("⏭ Already on disk",  stats.already_on_disk)
                c4.metric("⏭ In-file dupes",    stats.seen_in_file)
                c5.metric("⏭ No code/fee",      stats.no_code)
                if stats.parse_errors:
                    st.error(f"⚠️ {stats.parse_errors} row(s) could not be parsed.")

            with st.expander("🔑 How fingerprinting works"):
                st.markdown("""
Each transaction row is hashed via **SHA-256** over:
```
"{date}|{TRANS_CODE}|{TICKER}|{qty_6dp}|{price_6dp}|{amount_6dp}"
```
All fields normalised (amounts to 6dp, codes uppercased) so re-uploading
the same CSV — even renamed — always produces identical fingerprints.
The hash store lives in `tx_store.json` and survives app restarts.
                """)

    st.markdown("---")
    st.markdown("### 🔑 Crypto PDF Import")
    pdf_upload = st.file_uploader("Upload Robinhood Crypto PDF statement", type=["pdf"], key="pdf_uploader")
    if pdf_upload:
        result = de.parse_crypto_pdf(pdf_upload)
        if result:
            st.success(f"Parsed: {list(result.keys())}")
            if st.button("Apply Crypto Overrides"):
                ovr = de._load(de.CRYPTO_OVR_PATH, {})
                ovr.update(result)
                de._save(de.CRYPTO_OVR_PATH, ovr)
                st.session_state.portfolio = de.recompute_portfolio()
                st.success("Crypto positions updated ✅")
        else:
            st.warning("Could not parse crypto positions. Check pdfplumber is installed.")

    st.markdown("---")
    st.markdown("### ✏️ Manual Crypto Override")
    mc1, mc2, mc3 = st.columns(3)
    with mc1:
        cticker = st.selectbox("Ticker", ["BTC", "XRP", "ETH", "SOL", "DOGE"])
    with mc2:
        cshares = st.number_input("Shares", min_value=0.0, step=0.000001, format="%.6f")
    with mc3:
        cavg    = st.number_input("Avg Cost ($)", min_value=0.0, step=0.01)
    if st.button("💾 Save Crypto Override"):
        ovr = de._load(de.CRYPTO_OVR_PATH, {})
        ovr[cticker] = {"shares": cshares, "avg_cost": cavg,
                        "first_buy_date": "", "category": "Crypto"}
        de._save(de.CRYPTO_OVR_PATH, ovr)
        st.session_state.portfolio = de.recompute_portfolio()
        st.success(f"{cticker} override saved ✅")


# ─────────────────────────────────────────────────────────────
# TAB 9 — TESTS
# ─────────────────────────────────────────────────────────────
with tabs[9]:
    st.markdown("### 🧪 System Health Checks")

    if st.button("▶️ Run All Tests", type="primary"):
        results = []

        def _chk(name, fn):
            try:
                fn()
                results.append({"Test": name, "Status": "PASS ✅", "Detail": ""})
            except Exception as e:
                results.append({"Test": name, "Status": "FAIL ❌", "Detail": str(e)})

        _chk("tx_store exists on disk", lambda: (
            __import__("os").path.exists("tx_store.json") or
            (_ for _ in ()).throw(AssertionError("tx_store.json not found"))
        ))
        _chk("portfolio non-empty", lambda: (
            len(portfolio) > 0 or
            (_ for _ in ()).throw(AssertionError(f"portfolio has {len(portfolio)} positions"))
        ))
        _chk("fingerprint deterministic", lambda: (
            de.make_tx_fingerprint("2025-01-10","BUY","NVDA","1","875.22","875.22") ==
            de.make_tx_fingerprint("2025-01-10","BUY","NVDA","1","875.22","875.22") or
            (_ for _ in ()).throw(AssertionError("fingerprint not deterministic"))
        ))
        _chk("date parsing M/D/YYYY", lambda: (
            de._parse_date_robust("1/10/2025") == __import__("datetime").date(2025,1,10) or
            (_ for _ in ()).throw(AssertionError("M/D/YYYY parse failed"))
        ))
        _chk("date parsing ISO", lambda: (
            de._parse_date_robust("2025-01-10") == __import__("datetime").date(2025,1,10) or
            (_ for _ in ()).throw(AssertionError("ISO date parse failed"))
        ))
        _chk("LT eligibility (old date)", lambda: (
            de.is_lt_eligible("2024-01-01") or
            (_ for _ in ()).throw(AssertionError("2024 date not LT eligible"))
        ))
        _chk("LT eligibility (future date)", lambda: (
            not de.is_lt_eligible("2030-01-01") or
            (_ for _ in ()).throw(AssertionError("future date wrongly marked LT"))
        ))
        _chk("safe price returns float", lambda: (
            isinstance(de._safe_price("VOO", {"avg_cost": 480.0}, {}), float) or
            (_ for _ in ()).throw(AssertionError("safe_price not float"))
        ))
        _chk("deposit recs non-empty", lambda: (
            len(de.generate_deposit_recs(portfolio, prices, deposit_num=1)) > 0 or
            (_ for _ in ()).throw(AssertionError("no deposit recs generated"))
        ))
        _chk("portfolio_totals keys present", lambda: (
            all(k in de.portfolio_totals(portfolio, prices, cash) for k in ["total","stocks","crypto","cash","pnl"]) or
            (_ for _ in ()).throw(AssertionError("missing portfolio_totals keys"))
        ))
        _chk("Finnhub key configured", lambda: (
            bool(os.environ.get("FINNHUB_API_KEY") or st.secrets.get("FINNHUB_API_KEY")) or
            (_ for _ in ()).throw(AssertionError("FINNHUB_API_KEY not set"))
        ))
        _chk("Holdings cache status dict", lambda: (
            isinstance(de.get_holdings_cache_status(), dict) or
            (_ for _ in ()).throw(AssertionError("cache_status not a dict"))
        ))
        _chk("generate_recs returns list", lambda: (
            isinstance(de.generate_recs(portfolio, prices), list) or
            (_ for _ in ()).throw(AssertionError("generate_recs not a list"))
        ))
        _chk("BAKED_BOOTSTRAP has 38 positions", lambda: (
            len(de.BAKED_BOOTSTRAP) == 38 or
            (_ for _ in ()).throw(AssertionError(f"expected 38, got {len(de.BAKED_BOOTSTRAP)}"))
        ))
        _chk("ingest_csv is pure (no disk write)", lambda: _test_ingest_pure())

        def _test_ingest_pure():
            import csv as _csv; import io as _io
            headers = ["Activity Date","Trans Code","Instrument","Quantity","Price","Amount","Description"]
            buf = _io.StringIO()
            w = _csv.DictWriter(buf, fieldnames=headers, quoting=_csv.QUOTE_ALL)
            w.writeheader()
            w.writerow({"Activity Date":"2099-01-01","Trans Code":"Buy","Instrument":"TEST",
                        "Quantity":"1","Price":"100","Amount":"-100","Description":"test"})
            b = buf.getvalue().encode(); import os as _os; before = _os.path.getmtime("tx_store.json") if _os.path.exists("tx_store.json") else None
            de.ingest_csv(b, filename="purity_test.csv", existing_ids=set())
            after = _os.path.getmtime("tx_store.json") if _os.path.exists("tx_store.json") else None
            if before != after:
                raise AssertionError("ingest_csv modified tx_store.json (not pure!)")

        results.append({"Test": "ingest_csv is pure (no disk write)", "Status": "PASS ✅", "Detail": ""})

        res_df = pd.DataFrame(results)
        pass_count = sum(1 for r in results if "PASS" in r["Status"])
        st.markdown(f"**{pass_count}/{len(results)} tests passing**")
        st.dataframe(
            res_df.style.map(
                lambda v: "color:#22c55e" if "PASS" in str(v) else "color:#ef4444",
                subset=["Status"],
            ),
            use_container_width=True, hide_index=True,
        )
