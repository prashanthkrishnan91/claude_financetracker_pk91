"""
drip_analytics.py — Portfolio War Room v13
DRIP Analytics dashboard: dividend history, projections, DRIP impact.

Functions:
  extract_dividends(tx_store_path)  → list[dict]  raw CDIV rows
  clean_dividends(raw)              → pd.DataFrame deduplicated, typed
  calculate_projections(div_df, portfolio, prices) → dict
  render_drip_dashboard(portfolio, prices)         → None  (Streamlit UI)
"""

from __future__ import annotations

import datetime
import json
from decimal import Decimal
from pathlib import Path
from typing import Optional

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────
TX_STORE_PATH = Path("tx_store.json")

# Frequency inference thresholds (days between payments)
_FREQ_THRESHOLDS = [
    (45,  "Monthly",    12),
    (75,  "Bi-monthly",  6),
    (120, "Quarterly",   4),
    (240, "Semi-annual", 2),
    (999, "Annual",      1),
]

# ─────────────────────────────────────────────────────────────────────────────
# EXTRACTION
# ─────────────────────────────────────────────────────────────────────────────

def extract_dividends(tx_store_path: Path = TX_STORE_PATH) -> list[dict]:
    """
    Read all CDIV rows from tx_store.json.
    Returns list of raw dicts; empty list if file missing or no dividends.
    """
    try:
        with open(tx_store_path, "r") as f:
            tx_store: dict = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []

    rows = []
    for fp, row in tx_store.items():
        if row.get("code", "").upper() == "CDIV":
            rows.append({
                "fingerprint": fp,
                "date":        row.get("date", ""),
                "ticker":      row.get("ticker", "").strip().upper(),
                "qty":         row.get("qty", "0") or "0",
                "price":       row.get("price", "0") or "0",
                "amount":      row.get("amount", "0") or "0",
                "description": row.get("description", ""),
                "category":    row.get("category", "Stocks"),
            })
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# CLEANING
# ─────────────────────────────────────────────────────────────────────────────

def _safe_decimal(val) -> float:
    """Convert string/number to float safely, stripping $ and commas."""
    try:
        return float(str(val).replace("$", "").replace(",", "").strip() or "0")
    except ValueError:
        return 0.0


def clean_dividends(raw: list[dict]) -> pd.DataFrame:
    """
    Normalize and deduplicate raw CDIV rows.

    Dedup key: (date, ticker, amount) — same dividend cannot appear twice.
    Returns DataFrame with typed columns, sorted by date ascending.
    """
    if not raw:
        return pd.DataFrame(columns=[
            "date", "ticker", "amount", "shares_gained",
            "price_at_div", "category", "type"
        ])

    rows = []
    seen: set[tuple] = set()

    for r in raw:
        try:
            date_parsed = pd.to_datetime(r["date"]).date()
        except Exception:
            continue

        ticker  = r["ticker"]
        amount  = round(_safe_decimal(r["amount"]), 6)
        qty     = round(_safe_decimal(r["qty"]),    6)
        price   = round(_safe_decimal(r["price"]),  4)

        dedup_key = (str(date_parsed), ticker, round(amount, 2))
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        desc = r.get("description", "").lower()
        div_type = "Reinvestment" if "reinvest" in desc else "Cash Dividend"

        rows.append({
            "date":          date_parsed,
            "ticker":        ticker,
            "amount":        amount,
            "shares_gained": qty,
            "price_at_div":  price,
            "category":      r.get("category", "Stocks"),
            "type":          div_type,
        })

    if not rows:
        return pd.DataFrame(columns=[
            "date", "ticker", "amount", "shares_gained",
            "price_at_div", "category", "type"
        ])

    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    return df


# ─────────────────────────────────────────────────────────────────────────────
# PROJECTIONS
# ─────────────────────────────────────────────────────────────────────────────

def _infer_frequency(dates: list[datetime.date]) -> tuple[str, int, float]:
    """
    Given sorted payment dates for one ticker, infer payment frequency.
    Returns (label, payments_per_year, avg_days_between).
    """
    if len(dates) < 2:
        return ("Quarterly", 4, 91.25)  # sensible default

    gaps = []
    for i in range(1, len(dates)):
        delta = (dates[i] - dates[i - 1]).days
        if delta > 0:
            gaps.append(delta)

    if not gaps:
        return ("Quarterly", 4, 91.25)

    avg_gap = sum(gaps) / len(gaps)
    for threshold, label, freq in _FREQ_THRESHOLDS:
        if avg_gap <= threshold:
            return (label, freq, avg_gap)
    return ("Annual", 1, avg_gap)


@st.cache_data(ttl=300)
def calculate_projections(
    div_json: str,          # JSON-serialised div_df (cache-safe)
    portfolio_json: str,    # JSON-serialised portfolio
    prices_json: str,       # JSON-serialised prices
) -> dict:
    """
    Core projection engine.  All args are JSON strings so st.cache_data can hash them.

    Returns dict with keys:
      total_all_time       float
      total_this_year      float
      last_dividend        dict  {ticker, date, amount}
      projected_annual     float
      per_ticker           dict  {ticker: {freq, avg_payout, next_date, annual, ...}}
      next_30/60/90        float (expected income in window)
      drip_shares          dict  {ticker: shares_gained}
      drip_value           dict  {ticker: current_value_of_drip_shares}
      monthly_series       dict  {YYYY-MM: amount}
    """
    try:
        div_df     = pd.read_json(div_json, orient="records")
        portfolio  = json.loads(portfolio_json)
        prices     = json.loads(prices_json)
    except Exception:
        return _empty_projections()

    if div_df.empty:
        return _empty_projections()

    div_df["date"] = pd.to_datetime(div_df["date"])
    today = datetime.date.today()

    # ── Aggregates ────────────────────────────────────────────────────────────
    total_all_time  = float(div_df["amount"].sum())
    this_year_mask  = div_df["date"].dt.year == today.year
    total_this_year = float(div_df.loc[this_year_mask, "amount"].sum())

    last_row  = div_df.sort_values("date").iloc[-1]
    last_div  = {
        "ticker": last_row["ticker"],
        "date":   last_row["date"].strftime("%Y-%m-%d"),
        "amount": float(last_row["amount"]),
    }

    # ── Monthly series ────────────────────────────────────────────────────────
    div_df["ym"] = div_df["date"].dt.to_period("M").astype(str)
    monthly_series = (
        div_df.groupby("ym")["amount"].sum()
        .sort_index()
        .to_dict()
    )

    # ── Per-ticker analysis ───────────────────────────────────────────────────
    per_ticker: dict[str, dict] = {}
    projected_annual = 0.0
    next_30 = next_60 = next_90 = 0.0

    for ticker, grp in div_df.groupby("ticker"):
        grp = grp.sort_values("date")
        dates  = [d.date() for d in grp["date"]]
        amounts = grp["amount"].tolist()

        freq_label, freq_n, avg_gap = _infer_frequency(dates)
        avg_payout = float(grp["amount"].mean())

        # Next dividend estimate
        last_payment = dates[-1]
        next_date    = last_payment + datetime.timedelta(days=avg_gap)
        days_to_next = (next_date - today).days

        annual_est = avg_payout * freq_n
        projected_annual += annual_est

        # DRIP shares
        total_shares_gained = float(grp["shares_gained"].sum())
        current_price = prices.get(ticker, 0.0) or 0.0
        drip_value    = total_shares_gained * current_price

        per_ticker[ticker] = {
            "freq":             freq_label,
            "freq_n":           freq_n,
            "avg_gap_days":     round(avg_gap, 1),
            "avg_payout":       round(avg_payout, 2),
            "last_payment":     str(last_payment),
            "next_date":        str(next_date),
            "days_to_next":     days_to_next,
            "annual_est":       round(annual_est, 2),
            "total_received":   round(float(grp["amount"].sum()), 2),
            "payments_count":   len(dates),
            "shares_gained":    round(total_shares_gained, 6),
            "drip_value":       round(drip_value, 2),
            "current_price":    current_price,
        }

        # Rolling income windows
        for window, ref in [(30, "next_30"), (60, "next_60"), (90, "next_90")]:
            cutoff = today + datetime.timedelta(days=window)
            d = next_date
            while d <= cutoff:
                if ref == "next_30":
                    next_30 += avg_payout
                elif ref == "next_60":
                    next_60 += avg_payout
                else:
                    next_90 += avg_payout
                d += datetime.timedelta(days=avg_gap)

    # ── DRIP total impact ─────────────────────────────────────────────────────
    drip_shares = {t: v["shares_gained"] for t, v in per_ticker.items()}
    drip_value  = {t: v["drip_value"]    for t, v in per_ticker.items()}

    return {
        "total_all_time":   round(total_all_time, 2),
        "total_this_year":  round(total_this_year, 2),
        "last_dividend":    last_div,
        "projected_annual": round(projected_annual, 2),
        "per_ticker":       per_ticker,
        "next_30":          round(next_30, 2),
        "next_60":          round(next_60, 2),
        "next_90":          round(next_90, 2),
        "drip_shares":      drip_shares,
        "drip_value":       drip_value,
        "monthly_series":   monthly_series,
    }


def _empty_projections() -> dict:
    return {
        "total_all_time": 0.0, "total_this_year": 0.0,
        "last_dividend": None, "projected_annual": 0.0,
        "per_ticker": {}, "next_30": 0.0, "next_60": 0.0, "next_90": 0.0,
        "drip_shares": {}, "drip_value": {}, "monthly_series": {},
    }


# ─────────────────────────────────────────────────────────────────────────────
# UI HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _kpi(label: str, value: str, sub: str = "", color: str = "#e2e8f0") -> None:
    st.markdown(
        f"<div class='kpi'>"
        f"<div class='kpi-label'>{label}</div>"
        f"<div class='kpi-value' style='color:{color}'>{value}</div>"
        f"<div class='kpi-sub'>{sub}</div>"
        f"</div>",
        unsafe_allow_html=True,
    )


def _section(title: str) -> None:
    st.markdown(
        f"<h3 style='color:#38bdf8;margin-top:28px;margin-bottom:6px;"
        f"font-family:\"DM Serif Display\",serif'>{title}</h3>",
        unsafe_allow_html=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# MAIN DASHBOARD RENDERER
# ─────────────────────────────────────────────────────────────────────────────

def render_drip_dashboard(portfolio: dict, prices: dict) -> None:
    """
    Render the full DRIP Analytics page inside the calling Streamlit context.
    Reads tx_store.json directly; no external deps beyond data already in memory.
    """
    # ── Load + clean data ────────────────────────────────────────────────────
    raw      = extract_dividends(TX_STORE_PATH)
    div_df   = clean_dividends(raw)

    if div_df.empty:
        st.info(
            "No dividend records found yet. Upload a Robinhood CSV containing "
            "CDIV transactions in the **📥 Import** tab to populate DRIP Analytics."
        )
        return

    # Serialize for cache-safe projection call
    proj = calculate_projections(
        div_df.to_json(orient="records", date_format="iso"),
        json.dumps(portfolio),
        json.dumps({k: float(v) if v else 0.0 for k, v in prices.items()}),
    )

    today = datetime.date.today()

    # ═══ TOP KPI CARDS ═══════════════════════════════════════════════════════
    _section("💸 Dividend Summary")
    c1, c2, c3, c4 = st.columns(4)

    with c1:
        _kpi("Total Dividends (All-Time)",
             f"${proj['total_all_time']:,.2f}",
             f"{len(div_df)} payments · {div_df['ticker'].nunique()} tickers")

    with c2:
        _kpi("This Year",
             f"${proj['total_this_year']:,.2f}",
             str(today.year))

    with c3:
        ld = proj["last_dividend"]
        if ld:
            _kpi("Last Dividend",
                 f"${ld['amount']:,.2f}",
                 f"{ld['ticker']} · {ld['date']}")
        else:
            _kpi("Last Dividend", "—", "")

    with c4:
        _kpi("Projected Annual Income",
             f"${proj['projected_annual']:,.2f}",
             "based on inferred frequency",
             "#22c55e")

    # ═══ DIVIDEND HISTORY ════════════════════════════════════════════════════
    _section("📋 Dividend History")

    # Filter controls
    fc1, fc2, fc3 = st.columns([2, 2, 3])
    all_tickers = sorted(div_df["ticker"].unique().tolist())

    with fc1:
        sel_tickers = st.multiselect(
            "Filter by ticker", all_tickers,
            default=[], key="drip_ticker_filter",
            placeholder="All tickers"
        )
    with fc2:
        min_date = div_df["date"].min().date()
        max_date = div_df["date"].max().date()
        date_range = st.date_input(
            "Date range",
            value=(min_date, max_date),
            min_value=min_date, max_value=max_date,
            key="drip_date_filter",
        )
    with fc3:
        sort_col = st.selectbox(
            "Sort by", ["date", "amount", "ticker"],
            key="drip_sort_col"
        )

    # Apply filters
    filtered = div_df.copy()
    if sel_tickers:
        filtered = filtered[filtered["ticker"].isin(sel_tickers)]
    if isinstance(date_range, (tuple, list)) and len(date_range) == 2:
        d_from, d_to = date_range
        filtered = filtered[
            (filtered["date"].dt.date >= d_from) &
            (filtered["date"].dt.date <= d_to)
        ]
    filtered = filtered.sort_values(sort_col, ascending=(sort_col != "date"))

    display = filtered.copy()
    display["date"]   = display["date"].dt.strftime("%Y-%m-%d")
    display["amount"] = display["amount"].apply(lambda x: f"${x:,.4f}")
    display["shares_gained"] = display["shares_gained"].apply(
        lambda x: f"{x:.6f}" if x > 0 else "—"
    )
    display["price_at_div"] = display["price_at_div"].apply(
        lambda x: f"${x:,.2f}" if x > 0 else "—"
    )
    display = display.rename(columns={
        "date": "Date", "ticker": "Ticker", "amount": "Amount",
        "shares_gained": "Shares (DRIP)", "price_at_div": "Price",
        "type": "Type", "category": "Category",
    })

    st.dataframe(
        display[["Date", "Ticker", "Amount", "Shares (DRIP)", "Price", "Type"]],
        use_container_width=True, hide_index=True,
        height=min(400, 40 + len(display) * 35),
    )
    st.caption(f"Showing {len(display):,} of {len(div_df):,} records · deduplicated by (date, ticker, amount)")

    # ═══ CHARTS ══════════════════════════════════════════════════════════════
    _section("📈 Dividend Charts")

    ch1, ch2 = st.columns(2)

    with ch1:
        # Dividends over time — monthly/quarterly/yearly toggle
        agg_period = st.radio(
            "Aggregate by", ["Monthly", "Quarterly", "Yearly"],
            horizontal=True, key="drip_agg_period"
        )
        time_df = div_df.copy()
        if agg_period == "Monthly":
            time_df["period"] = time_df["date"].dt.to_period("M").astype(str)
        elif agg_period == "Quarterly":
            time_df["period"] = time_df["date"].dt.to_period("Q").astype(str)
        else:
            time_df["period"] = time_df["date"].dt.year.astype(str)

        time_agg = time_df.groupby("period")["amount"].sum().reset_index()
        time_agg.columns = ["Period", "Dividends ($)"]

        fig_time = px.bar(
            time_agg, x="Period", y="Dividends ($)",
            color_discrete_sequence=["#38bdf8"],
            template="plotly_dark",
            title=f"Dividend Income — {agg_period}",
        )
        fig_time.update_layout(
            plot_bgcolor="#07090f", paper_bgcolor="#07090f",
            font_color="#94a3b8", title_font_color="#e2e8f0",
            margin=dict(l=10, r=10, t=40, b=10),
            xaxis_tickangle=-45,
        )
        st.plotly_chart(fig_time, use_container_width=True)

    with ch2:
        # Dividend income by stock (total all-time)
        by_ticker = (
            div_df.groupby("ticker")["amount"]
            .sum()
            .sort_values(ascending=False)
            .reset_index()
        )
        by_ticker.columns = ["Ticker", "Total ($)"]

        fig_ticker = px.bar(
            by_ticker, x="Ticker", y="Total ($)",
            color="Total ($)",
            color_continuous_scale=["#1e2d47", "#38bdf8"],
            template="plotly_dark",
            title="Total Dividends by Stock",
        )
        fig_ticker.update_layout(
            plot_bgcolor="#07090f", paper_bgcolor="#07090f",
            font_color="#94a3b8", title_font_color="#e2e8f0",
            coloraxis_showscale=False,
            margin=dict(l=10, r=10, t=40, b=10),
        )
        st.plotly_chart(fig_ticker, use_container_width=True)

    # ═══ FUTURE PROJECTIONS ══════════════════════════════════════════════════
    _section("🔮 Future Projections")

    # 30/60/90 day income cards
    w1, w2, w3 = st.columns(3)
    with w1:
        _kpi("Next 30 Days", f"${proj['next_30']:,.2f}",
             "expected dividends", "#f59e0b")
    with w2:
        _kpi("Next 60 Days", f"${proj['next_60']:,.2f}",
             "expected dividends", "#f59e0b")
    with w3:
        _kpi("Next 90 Days", f"${proj['next_90']:,.2f}",
             "expected dividends", "#f59e0b")

    st.markdown("")

    # Per-ticker projection table
    if proj["per_ticker"]:
        proj_rows = []
        for ticker, p in sorted(proj["per_ticker"].items()):
            days = p["days_to_next"]
            urgency = "🟢" if days <= 14 else ("🟡" if days <= 45 else "⚪")
            proj_rows.append({
                "": urgency,
                "Ticker":       ticker,
                "Frequency":    p["freq"],
                "Avg Payout":   f"${p['avg_payout']:,.2f}",
                "Last Payment": p["last_payment"],
                "Next Est.":    p["next_date"],
                "Days Away":    f"{max(0, days)}d",
                "Annual Est.":  f"${p['annual_est']:,.2f}",
                "# Payments":   p["payments_count"],
            })

        proj_df = pd.DataFrame(proj_rows)
        st.dataframe(proj_df, use_container_width=True, hide_index=True)
        st.caption("🟢 due within 14 days · 🟡 due within 45 days · ⚪ further out")

        # Timeline chart — next payments
        upcoming = [
            {"ticker": t, "next_date": p["next_date"], "amount": p["avg_payout"]}
            for t, p in proj["per_ticker"].items()
            if p["days_to_next"] <= 120
        ]
        if upcoming:
            up_df = pd.DataFrame(upcoming)
            up_df["next_date"] = pd.to_datetime(up_df["next_date"])
            up_df = up_df.sort_values("next_date")

            fig_up = px.scatter(
                up_df, x="next_date", y="ticker",
                size="amount", color="amount",
                color_continuous_scale=["#1e2d47", "#22c55e"],
                size_max=30, template="plotly_dark",
                title="Upcoming Dividends (next 120 days)",
                labels={"next_date": "Expected Date", "ticker": "Ticker",
                        "amount": "Est. Payout ($)"},
            )
            fig_up.update_layout(
                plot_bgcolor="#07090f", paper_bgcolor="#07090f",
                font_color="#94a3b8", title_font_color="#e2e8f0",
                coloraxis_showscale=False,
                margin=dict(l=10, r=10, t=40, b=10),
            )
            st.plotly_chart(fig_up, use_container_width=True)

    # ═══ DRIP IMPACT ═════════════════════════════════════════════════════════
    _section("🔄 DRIP Impact")

    total_drip_shares_value = sum(proj["drip_value"].values())
    total_drip_shares_count = sum(proj["drip_shares"].values())

    di1, di2 = st.columns(2)
    with di1:
        _kpi("Total DRIP Shares Gained",
             f"{total_drip_shares_count:,.4f} shares",
             "across all dividend-paying positions",
             "#a78bfa")
    with di2:
        _kpi("Current Value of DRIP Shares",
             f"${total_drip_shares_value:,.2f}",
             "at live prices (requires Refresh)",
             "#22c55e")

    # Per-ticker DRIP table
    if proj["drip_shares"]:
        drip_rows = []
        for ticker in sorted(proj["drip_shares"].keys()):
            shares = proj["drip_shares"][ticker]
            val    = proj["drip_value"][ticker]
            pt     = proj["per_ticker"].get(ticker, {})
            total_recv = pt.get("total_received", 0)
            curr_price = pt.get("current_price", 0)

            # Value WITH reinvestment = original position value + DRIP shares value
            pos_shares = portfolio.get(ticker, {}).get("shares", 0)
            pos_val    = pos_shares * curr_price if curr_price else 0
            without_drip = pos_val - val  # remove DRIP share value

            drip_rows.append({
                "Ticker":          ticker,
                "DRIP Shares":     f"{shares:,.6f}",
                "DRIP Value":      f"${val:,.2f}",
                "Cash Received":   f"${total_recv:,.2f}",
                "Portfolio Value": f"${pos_val:,.2f}",
                "Without DRIP":    f"${without_drip:,.2f}",
                "DRIP Boost":      f"+${val:,.2f}",
            })

        drip_df = pd.DataFrame(drip_rows)
        st.dataframe(drip_df, use_container_width=True, hide_index=True)

        # DRIP value comparison chart
        if any(proj["drip_value"].get(t, 0) > 0 for t in proj["drip_shares"]):
            comp_rows = []
            for ticker in sorted(proj["drip_shares"].keys()):
                drip_val = proj["drip_value"].get(ticker, 0)
                if drip_val <= 0:
                    continue
                pt       = proj["per_ticker"].get(ticker, {})
                curr_p   = pt.get("current_price", 0)
                pos_s    = portfolio.get(ticker, {}).get("shares", 0)
                pos_val  = pos_s * curr_p if curr_p else 0
                wo_drip  = max(0, pos_val - drip_val)
                comp_rows.extend([
                    {"Ticker": ticker, "Type": "Original Position", "Value": round(wo_drip, 2)},
                    {"Ticker": ticker, "Type": "DRIP Shares",       "Value": round(drip_val, 2)},
                ])

            if comp_rows:
                comp_df = pd.DataFrame(comp_rows)
                fig_drip = px.bar(
                    comp_df, x="Ticker", y="Value", color="Type",
                    color_discrete_map={
                        "Original Position": "#1e40af",
                        "DRIP Shares":       "#22c55e",
                    },
                    barmode="stack", template="plotly_dark",
                    title="Portfolio Value: Original vs DRIP Boost",
                    labels={"Value": "Value ($)"},
                )
                fig_drip.update_layout(
                    plot_bgcolor="#07090f", paper_bgcolor="#07090f",
                    font_color="#94a3b8", title_font_color="#e2e8f0",
                    legend=dict(bgcolor="rgba(0,0,0,0)"),
                    margin=dict(l=10, r=10, t=40, b=10),
                )
                st.plotly_chart(fig_drip, use_container_width=True)

    st.markdown(
        "<div style='margin-top:20px;padding:10px;border:1px solid #1e2d47;"
        "border-radius:8px;font-size:11px;color:#475569'>"
        "ℹ️ DRIP projections are estimates based on historical payment frequency. "
        "Actual dividends depend on board declarations. Next-date estimates use "
        "average gap between historical payments. Press 🔄 Refresh for current prices."
        "</div>",
        unsafe_allow_html=True,
    )
