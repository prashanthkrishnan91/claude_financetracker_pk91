import streamlit as st
import pandas as pd
import plotly.express as px
import yfinance as yf
from datetime import datetime

@st.cache_data(ttl=86400)
def get_market_dividend_data(tickers):
    """Fetches yield and projected payment date from yfinance."""
    results = {}
    for t in tickers:
        if t in ["BTC", "ETH", "XRP", "SOL", "DOGE"]: continue
        try:
            # Clean ticker for Yahoo (e.g., BRK-B -> BRK.B)
            yf_ticker = t.replace('-', '.')
            tk = yf.Ticker(yf_ticker)
            
            # Get Annual Rate
            rate = tk.info.get("dividendRate") or tk.info.get("trailingAnnualDividendRate", 0.0)
            
            # Get Next Dividend Date from Calendar
            next_date = "TBD"
            cal = tk.calendar
            if cal is not None and not cal.empty:
                # Calendar can be a dict or DataFrame depending on yfinance version
                date_val = cal.get("Dividend Date") if isinstance(cal, dict) else cal.loc["Dividend Date"].iloc[0]
                if date_val:
                    next_date = date_val.strftime('%Y-%m-%d')

            results[t] = {"rate": float(rate) if rate else 0.0, "date": next_date}
        except:
            results[t] = {"rate": 0.0, "date": "TBD"}
    return results

def clean_numeric(value):
    """Senior Dev Fix: Strips currency symbols and handles numeric conversion."""
    if value is None: return 0.0
    try:
        s = str(value).replace('$', '').replace(',', '').replace(' ', '')
        return abs(float(s))
    except: return 0.0

def render_drip_dashboard(active_portfolio, tx_list, plaid_snap=None):
    """
    Unified Dashboard:
    1. Checks Plaid Cache for Transactions.
    2. Falls back to CSV (tx_list) if Plaid is empty.
    3. Projects future dividends with 'Projected Date'.
    """
    st.markdown("### 💧 Dividend Intelligence")

    # --- 1. HISTORICAL DATA (Plaid Priority -> CSV Fallback) ---
    raw_div_source = []
    source_label = "CSV"

    # Step A: Check Plaid Cache for transaction data
    if plaid_snap and plaid_snap.get("transactions"):
        raw_div_source = plaid_snap["transactions"]
        source_label = "Plaid Cache"
    
    # Step B: Fallback to CSV if Plaid has no transaction history
    if not raw_div_source and tx_list:
        raw_div_source = tx_list
        source_label = "Robinhood CSV"

    # Step C: Filter for strict Dividend codes (CDIV, DIV)
    hist_rows = []
    total_lifetime_earned = 0.0
    for tx in raw_div_source:
        code = str(tx.get('trans_code', tx.get('type', ''))).upper()
        if any(k in code for k in ['CDIV', 'DIV']):
            amt = clean_numeric(tx.get('amount', 0.0))
            if amt > 0:
                total_lifetime_earned += amt
                hist_rows.append({
                    "Date": tx.get("date"),
                    "Ticker": tx.get("instrument") or tx.get("ticker", "Unknown"),
                    "Amount": amt
                })

    # --- 2. FUTURE PROJECTIONS (Plaid Holdings + yfinance) ---
    tickers = list(active_portfolio.keys())
    market_data = get_market_dividend_data(tickers)
    
    proj_rows = []
    total_annual_projected = 0.0

    for t, pos in active_portfolio.items():
        shares = float(pos.get('shares', 0.0))
        m_data = market_data.get(t, {"rate": 0.0, "date": "TBD"})
        annual_inc = shares * m_data["rate"]
        
        if annual_inc > 0:
            total_annual_projected += annual_inc
            proj_rows.append({
                "Ticker": t,
                "Projected Date": m_data["date"],
                "Annual Income": annual_inc,
                "Yield ($/sh)": m_data["rate"],
                "Shares": shares
            })

    # --- 3. UI: KPI CARDS ---
    if not hist_rows and not proj_rows:
        st.warning("No dividend data found. Please sync with Plaid or upload a CSV.")
        return

    k1, k2, k3 = st.columns(3)
    k1.metric("Lifetime Earned", f"${total_lifetime_earned:,.2f}", help=f"Source: {source_label}")
    k2.metric("Annual Projection", f"${total_annual_projected:,.2f}")
    k3.metric("Est. Monthly Income", f"${(total_annual_projected / 12):,.2f}")

    st.divider()

    # --- 4. UI: TABS ---
    tab_f, tab_h = st.tabs(["🚀 Future Projections", "📜 Historical Payouts"])
    
    with tab_f:
        if proj_rows:
            # Sort by date so user knows what's coming next
            df_p = pd.DataFrame(proj_rows).sort_values("Projected Date")
            st.dataframe(
                df_p.style.format({"Yield ($/sh)": "${:.2f}", "Annual Income": "${:,.2f}", "Shares": "{:.2f}"}),
                use_container_width=True, hide_index=True
            )
        else:
            st.info("No upcoming dividends detected.")

    with tab_h:
        if hist_rows:
            df_h = pd.DataFrame(hist_rows)
            df_h['Date'] = pd.to_datetime(df_h['Date']).dt.date
            st.dataframe(df_h.sort_values("Date", ascending=False), use_container_width=True, hide_index=True)
        else:
            st.info("No historical dividend payments found.")
