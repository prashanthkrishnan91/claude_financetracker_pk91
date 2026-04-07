import streamlit as st
import pandas as pd
import plotly.express as px
import yfinance as yf
from datetime import datetime

@st.cache_data(ttl=86400)
def get_dividend_market_data(tickers):
    """Fetches annual dividend rates and next pay dates from yfinance."""
    results = {}
    for t in tickers:
        if t in ["BTC", "ETH", "XRP", "SOL", "DOGE"]: continue
        try:
            yf_ticker = t.replace('-', '.') # Fix share class formatting
            tk = yf.Ticker(yf_ticker)
            info = tk.info
            
            # Annual payout rate
            rate = info.get("dividendRate") or info.get("trailingAnnualDividendRate", 0.0)
            
            # Payout Date Logic
            next_date = "TBD"
            cal = tk.calendar
            if cal is not None and not cal.empty:
                # Calendar returns differently based on yfinance version
                d_val = cal.get("Dividend Date") if isinstance(cal, dict) else cal.loc["Dividend Date"].iloc[0]
                if d_val:
                    next_date = d_val.strftime('%Y-%m-%d')
            
            results[t] = {"rate": float(rate) if rate else 0.0, "date": next_date}
        except:
            results[t] = {"rate": 0.0, "date": "TBD"}
    return results

def clean_val(val):
    """Sanitizes strings like '$1,234.56' into floats."""
    if val is None: return 0.0
    try:
        s = str(val).replace('$', '').replace(',', '').replace(' ', '')
        return abs(float(s))
    except: return 0.0

def render_drip_dashboard(active_portfolio, tx_list, plaid_snap=None):
    st.markdown("### 💧 Dividend Intelligence")

    # --- 1. HISTORICAL EXTRACTION (Plaid + CSV) ---
    combined_history = (tx_list or [])
    if plaid_snap and plaid_snap.get("transactions"):
        combined_history = plaid_snap["transactions"] + combined_history

    hist_rows = []
    total_lifetime_earned = 0.0
    
    # Strictly target cash dividend codes
    div_codes = ['CDIV', 'DIV', 'DIVIDEND', 'CASH DIVIDEND']
    for tx in combined_history:
        code = str(tx.get('trans_code', tx.get('type', ''))).upper()
        if any(k in code for k in div_codes):
            amt = clean_val(tx.get('amount', 0.0))
            if amt > 0:
                total_lifetime_earned += amt
                hist_rows.append({
                    "Date": tx.get("date"),
                    "Ticker": tx.get("instrument") or tx.get("ticker", "Unknown"),
                    "Amount": amt
                })

    # --- 2. FUTURE PROJECTIONS (Plaid Holdings) ---
    tickers = list(active_portfolio.keys())
    market_intel = get_dividend_market_data(tickers)
    
    proj_rows = []
    total_annual_projected = 0.0

    for t, pos in active_portfolio.items():
        shares = float(pos.get('shares', 0.0))
        data = market_intel.get(t, {"rate": 0.0, "date": "TBD"})
        annual_inc = shares * data["rate"]
        
        if annual_inc > 0:
            total_annual_projected += annual_inc
            proj_rows.append({
                "Ticker": t,
                "Expected Date": data["date"],
                "Annual Income": annual_inc,
                "Yield ($/sh)": data["rate"],
                "Current Shares": shares
            })

    # --- 3. THE "COMMAND CENTER" UI ---
    k1, k2, k3 = st.columns(3)
    k1.metric("Lifetime Earned", f"${total_lifetime_earned:,.2f}", help="Sum of all CDIV entries found.")
    k2.metric("Annual Projection", f"${total_annual_projected:,.2f}", help="Expected income for 2026.")
    k3.metric("Est. Monthly Income", f"${(total_annual_projected / 12):,.2f}")

    st.divider()

    tab_f, tab_h = st.tabs(["🚀 Future Projections & Payouts", "📜 Historical Payouts"])
    
    with tab_f:
        if proj_rows:
            df_p = pd.DataFrame(proj_rows).sort_values("Expected Date")
            st.dataframe(
                df_p.style.format({"Yield ($/sh)": "${:.2f}", "Annual Income": "${:,.2f}", "Current Shares": "{:.2f}"}),
                use_container_width=True, hide_index=True
            )
            st.caption("💡 Use 'Expected Date' to time your buys and capture upcoming dividend eligibility.")
        else:
            st.info("No dividend-paying assets detected in current sync.")

    with tab_h:
        if hist_rows:
            df_h = pd.DataFrame(hist_rows)
            df_h['Date'] = pd.to_datetime(df_h['Date']).dt.date
            st.dataframe(df_h.sort_values("Date", ascending=False), use_container_width=True, hide_index=True)
        else:
            st.warning("No historical payments found. Verify 'CDIV' exists in your CSV.")
