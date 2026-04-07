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
import yfinance as yf
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
@st.cache_data(ttl=86400) # Cache for 24h so it loads instantly
def get_market_yields(tickers):
    """
    Senior Dev Note: Fetches annual dividend rates ($) per share.
    Caches results for 24h to ensure the UI stays snappy.
    """
    yield_map = {}
    for t in tickers:
        # Skip crypto - they don't pay dividends in this context
        if t in ["BTC", "ETH", "XRP", "SOL", "DOGE"]:
            continue
        try:
            # Handle ticker format differences (Yahoo likes dots, e.g., BRK.B)
            yf_ticker = t.replace('-', '.')
            tk = yf.Ticker(yf_ticker)
            # Try to get the projected rate first, fallback to trailing
            rate = tk.info.get("dividendRate") or tk.info.get("trailingAnnualDividendRate", 0.0)
            yield_map[t] = float(rate) if rate else 0.0
        except Exception:
            yield_map[t] = 0.0
    return yield_map

def calculate_forward_drip(active_portfolio: dict) -> tuple[float, pd.DataFrame]:
    """Projects future DRIP income based on current Plaid holdings."""
    tickers = list(active_portfolio.keys())
    div_rates = get_live_dividend_rates(tickers)
    
    total_annual = 0.0
    rows = []
    
    for ticker, pos in active_portfolio.items():
        shares = float(pos.get("shares", 0.0))
        rate = div_rates.get(ticker, 0.0)
        
        annual_income = shares * rate
        total_annual += annual_income
        
        if annual_income > 0:
            rows.append({
                "Ticker": ticker,
                "Shares Owned": shares,
                "Div Rate ($/sh)": rate,
                "Projected Annual Income": annual_income
            })
            
    df_forward = pd.DataFrame(rows)
    if not df_forward.empty:
        df_forward = df_forward.sort_values("Projected Annual Income", ascending=False)
        
    return total_annual, df_forward

# ─────────────────────────────────────────────────────────────────────────────
# EXTRACTION from csv
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

def render_drip_dashboard(active_portfolio, tx_list):
    """
    Unified Dividend Command Center.
    Input: 
      - active_portfolio: dict {ticker: {shares, ...}} from Plaid
      - tx_list: list of transaction dicts from tx_store.json
    """
    st.markdown("### 💧 Dividend Intelligence")

    # --- 1. HISTORICAL CALCULATIONS (From CSV/Excel) ---
    div_keywords = ['DIV', 'DIVIDEND', 'CDIV']
    hist_rows = []
    total_lifetime_earned = 0.0

    for tx in (tx_list or []):
        code = str(tx.get('trans_code', '')).upper()
        if any(k in code for k in div_keywords):
            amt = abs(float(tx.get('amount', 0.0)))
            total_lifetime_earned += amt
            hist_rows.append({
                "Date": tx.get("date"),
                "Ticker": tx.get("instrument"),
                "Amount": amt
            })

    # --- 2. FUTURE PROJECTIONS (From Plaid + Market Analysis) ---
    tickers = list(active_portfolio.keys())
    market_rates = get_market_yields(tickers)
    
    proj_rows = []
    total_annual_projected = 0.0 # Variable name verified

    for t, pos in active_portfolio.items():
        shares = float(pos.get('shares', 0.0))
        rate = market_rates.get(t, 0.0)
        annual_inc = shares * rate
        
        if annual_inc > 0:
            total_annual_projected += annual_inc
            proj_rows.append({
                "Ticker": t,
                "Shares Owned": shares,
                "Div Yield ($/sh)": rate,
                "Annual Income": annual_inc
            })

    # --- 3. KPI HEADER CARDS ---
    k1, k2, k3 = st.columns(3)
    k1.metric("Lifetime Earned", f"${total_lifetime_earned:,.2f}", help="Sum of all dividends found in your Robinhood CSV history.")
    k2.metric("Annual Projection", f"${total_annual_projected:,.2f}", help="Expected income over the next 12 months based on current Plaid holdings.")
    k3.metric("Est. Monthly Income", f"${(total_annual_projected / 12):,.2f}")

    st.divider()

    # --- 4. VISUAL INSIGHT: MONTHLY CASH FLOW ---
    if total_annual_projected > 0:
        months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        chart_df = pd.DataFrame({"Month": months, "Projected Income": [total_annual_projected/12]*12})
        
        fig = px.bar(
            chart_df, x="Month", y="Projected Income",
            title="Projected Monthly Passive Income (Average)",
            template="plotly_dark",
            color_discrete_sequence=["#00C805"] # Robinhood Green
        )
        fig.update_layout(height=300, margin=dict(t=40, b=0, l=0, r=0))
        st.plotly_chart(fig, use_container_width=True)

    # --- 5. DETAIL TABS ---
    tab_f, tab_h = st.tabs(["🚀 Future Projections", "📜 Historical Payouts"])
    
    with tab_f:
        if proj_rows:
            df_p = pd.DataFrame(proj_rows).sort_values("Annual Income", ascending=False)
            st.dataframe(
                df_p.style.format({"Shares Owned": "{:.2f}", "Div Yield ($/sh)": "${:.2f}", "Annual Income": "${:,.2f}"}),
                use_container_width=True, hide_index=True
            )
        else:
            st.info("No dividend-paying stocks found in your current Plaid sync.")

    with tab_h:
        if hist_rows:
            df_h = pd.DataFrame(hist_rows)
            df_h['Date'] = pd.to_datetime(df_h['Date']).dt.date
            st.dataframe(
                df_h.sort_values("Date", ascending=False),
                use_container_width=True, hide_index=True
            )
        else:
            st.warning("No historical dividend data found. Please upload a Robinhood CSV.")

    st.markdown(
        "<div style='margin-top:20px;padding:10px;border:1px solid #1e2d47;"
        "border-radius:8px;font-size:11px;color:#475569'>"
        "ℹ️ DRIP projections are estimates based on historical payment frequency. "
        "Actual dividends depend on board declarations. Next-date estimates use "
        "average gap between historical payments. Press 🔄 Refresh for current prices."
        "</div>",
        unsafe_allow_html=True,
    )
