import streamlit as st
import pandas as pd
import plotly.express as px
import yfinance as yf
from datetime import datetime

@st.cache_data(ttl=86400)
def get_dividend_market_intel(tickers):
    """Fetches yield, pay date, and ex-div date from yfinance."""
    results = {}
    for t in tickers:
        if t in ["BTC", "ETH", "XRP", "SOL", "DOGE"]: continue
        try:
            yf_ticker = t.replace('-', '.')
            tk = yf.Ticker(yf_ticker)
            info = tk.info
            
            # Annual payout rate
            rate = info.get("dividendRate") or info.get("trailingAnnualDividendRate", 0.0)
            
            # Calendar Logic: Pay Date & Ex-Div Date
            next_pay = "TBD"
            ex_div = "TBD"
            cal = tk.calendar
            if cal is not None and not cal.empty:
                # Handle dictionary or DataFrame formats from yfinance
                if isinstance(cal, dict):
                    next_pay = cal.get("Dividend Date", "TBD")
                    ex_div = cal.get("Ex-Dividend Date", "TBD")
                else:
                    if "Dividend Date" in cal.index:
                        next_pay = cal.loc["Dividend Date"].iloc[0]
                    if "Ex-Dividend Date" in cal.index:
                        ex_div = cal.loc["Ex-Dividend Date"].iloc[0]
                
                # Format dates to string
                if isinstance(next_pay, (datetime, pd.Timestamp)): next_pay = next_pay.strftime('%Y-%m-%d')
                if isinstance(ex_div, (datetime, pd.Timestamp)): ex_div = ex_div.strftime('%Y-%m-%d')

            results[t] = {"rate": float(rate) if rate else 0.0, "pay_date": next_pay, "ex_date": ex_div}
        except:
            results[t] = {"rate": 0.0, "pay_date": "TBD", "ex_date": "TBD"}
    return results

def clean_numeric(val):
    """Hardened cleaner for '$1,234.56' and '(1.23)' formats."""
    if val is None or val == "": return 0.0
    try:
        s = str(val).replace('$', '').replace(',', '').replace(' ', '')
        if '(' in s and ')' in s: # Handle negative parenthesis accounting style
            s = "-" + s.replace('(', '').replace(')', '')
        return abs(float(s))
    except:
        return 0.0

def render_drip_dashboard(active_portfolio, tx_list, plaid_snap=None):
    st.markdown("### 💧 Dividend Intelligence")

    # --- 1. DATA SOURCE PRIORITIZATION ---
    # Merge Plaid transactions and CSV store to ensure no gaps
    combined_history = (tx_list or [])
    if plaid_snap and plaid_snap.get("transactions"):
        combined_history = plaid_snap["transactions"] + combined_history

    hist_rows = []
    total_lifetime_earned = 0.0
    
    # Strictly target cash dividend codes from Robinhood
    # We check multiple possible key names for flexibility
    for tx in combined_history:
        # Check 'trans_code', 'type', or 'Activity Type'
        code = str(tx.get('trans_code') or tx.get('Trans Code') or tx.get('type') or tx.get('Activity Type') or '').upper()
        
        if any(k in code for k in ['CDIV', 'DIV']):
            amt = clean_numeric(tx.get('amount') or tx.get('Amount') or 0.0)
            if amt > 0:
                total_lifetime_earned += amt
                hist_rows.append({
                    "Date": tx.get("date") or tx.get("Date") or tx.get("Activity Date"),
                    "Ticker": tx.get("instrument") or tx.get("Ticker") or tx.get("Symbol") or "Unknown",
                    "Amount": amt
                })

    # --- 2. FUTURE PROJECTIONS (Based on Plaid Holdings) ---
    tickers = list(active_portfolio.keys())
    market_intel = get_dividend_market_intel(tickers)
    
    proj_rows = []
    total_annual_projected = 0.0

    for t, pos in active_portfolio.items():
        shares = float(pos.get('shares', 0.0))
        data = market_intel.get(t, {"rate": 0.0, "pay_date": "TBD", "ex_date": "TBD"})
        annual_inc = shares * data["rate"]
        
        if annual_inc > 0:
            total_annual_projected += annual_inc
            proj_rows.append({
                "Ticker": t,
                "Ex-Div Date": data["ex_date"],
                "Projected Pay Date": data["pay_date"],
                "Annual Income": annual_inc,
                "Yield ($/sh)": data["rate"],
                "Shares": shares
            })

    # --- 3. UI RENDERING ---
    k1, k2, k3 = st.columns(3)
    k1.metric("Lifetime Earned", f"${total_lifetime_earned:,.2f}", help="Sum of all 'CDIV' codes found.")
    k2.metric("Annual Projection", f"${total_annual_projected:,.2f}", help="Forward-looking income based on Plaid holdings.")
    k3.metric("Est. Monthly Income", f"${(total_annual_projected / 12):,.2f}")

    st.divider()

    tab_f, tab_h = st.tabs(["🚀 Future Projections & Payouts", "📜 Historical Payouts"])
    
    with tab_f:
        if proj_rows:
            df_p = pd.DataFrame(proj_rows).sort_values("Projected Pay Date")
            st.dataframe(
                df_p.style.format({"Yield ($/sh)": "${:.2f}", "Annual Income": "${:,.2f}", "Shares": "{:.2f}"}),
                use_container_width=True, hide_index=True
            )
            st.info("💡 **Ex-Div Date**: You must own shares before this date to receive the next payout.")
        else:
            st.info("No dividend-paying assets detected in your current Plaid holdings.")

    with tab_h:
        if hist_rows:
            df_h = pd.DataFrame(hist_rows)
            # Ensure date column is valid for sorting
            df_h['Date'] = pd.to_datetime(df_h['Date'], errors='coerce')
            st.dataframe(
                df_h.sort_values("Date", ascending=False).dropna(subset=['Date']), 
                use_container_width=True, hide_index=True
            )
        else:
            st.warning("No historical 'CDIV' payments found. Ensure your CSV is uploaded correctly.")
