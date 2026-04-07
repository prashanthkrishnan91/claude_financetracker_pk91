import streamlit as st
import pandas as pd
import plotly.express as px
import yfinance as yf

# Renamed function to bust the old poisoned cache
@st.cache_data(ttl=86400, show_spinner=False)
def fetch_live_dividend_market_data(tickers):
    """Fetches yield, pay date, and ex-div date from yfinance."""
    res = {}
    for t in tickers:
        if t in ["BTC", "ETH", "XRP", "SOL", "DOGE", "USD"]: continue
        try:
            tk = yf.Ticker(t.replace('-', '.'))
            info = tk.info
            
            # Robust Rate Extraction (Tries multiple info fields)
            rate = info.get("dividendRate") or info.get("trailingAnnualDividendRate")
            if not rate: # Fallback calculation
                dy = info.get("dividendYield")
                pc = info.get("previousClose")
                if dy and pc: rate = dy * pc
            rate = float(rate) if rate else 0.0
            
            # Robust Date Extraction
            pay_date, ex_date = "TBD", "TBD"
            cal = tk.calendar
            if isinstance(cal, dict):
                pay_date = cal.get("Dividend Date", "TBD")
                ex_date = cal.get("Ex-Dividend Date", "TBD")
            elif hasattr(cal, 'index'):
                if "Dividend Date" in cal.index: pay_date = cal.loc["Dividend Date"].iloc[0]
                if "Ex-Dividend Date" in cal.index: ex_date = cal.loc["Ex-Dividend Date"].iloc[0]
            
            # Format dates
            if pd.notnull(pay_date) and hasattr(pay_date, 'strftime'): pay_date = pay_date.strftime('%Y-%m-%d')
            if pd.notnull(ex_date) and hasattr(ex_date, 'strftime'): ex_date = ex_date.strftime('%Y-%m-%d')
            
            res[t] = {"rate": rate, "pay_date": str(pay_date), "ex_date": str(ex_date)}
        except:
            res[t] = {"rate": 0.0, "pay_date": "TBD", "ex_date": "TBD"}
    return res

def render_drip_dashboard(active_portfolio, tx_list=None, plaid_snap=None):
    st.markdown("### 💧 Dividend Intelligence")

    # --- 1. HISTORICAL DATA EXTRACTION (Plaid First, CSV Fallback) ---
    raw_history = []
    # Safely extract Plaid transactions
    if plaid_snap and isinstance(plaid_snap, dict) and "transactions" in plaid_snap:
        raw_history.extend(plaid_snap["transactions"])
    # Safely extract CSV transactions
    if tx_list:
        if isinstance(tx_list, list): raw_history.extend(tx_list)
        elif hasattr(tx_list, "to_dict"): raw_history.extend(tx_list.to_dict('records'))

    hist_rows = []
    total_lifetime_earned = 0.0
    
    # Schema-Agnostic Iterator: Scans all keys for matching data
    for tx in raw_history:
        if not isinstance(tx, dict): continue
        
        tx_values_str = " ".join([str(v).upper() for v in tx.values() if v])
        
        # Check if row is a Cash Dividend, skip if it's a DRIP Buy order
        if ('CDIV' in tx_values_str or 'DIVIDEND' in tx_values_str) and 'REINVEST' not in tx_values_str:
            
            # Dynamically find the Amount
            amt = 0.0
            for k, v in tx.items():
                if str(k).lower() in ['amount', 'net amount', 'total', 'value']:
                    # Clean currency formatting
                    s = str(v).replace('$', '').replace(',', '').replace(' ', '')
                    if '(' in s and ')' in s: s = s.replace('(', '-').replace(')', '')
                    try: 
                        parsed = abs(float(s))
                        if parsed > 0: amt = parsed
                    except: pass
            
            if amt > 0:
                total_lifetime_earned += amt
                
                # Dynamically find Date and Ticker
                d_val, t_val = "Unknown", "Unknown"
                for k, v in tx.items():
                    kl = str(k).lower()
                    if kl in ['date', 'activity date', 'process date', 'settle date']: d_val = v
                    if kl in ['instrument', 'ticker', 'symbol', 'name']: t_val = v
                
                hist_rows.append({"Date": d_val, "Ticker": t_val, "Amount": amt})

    # --- 2. FUTURE PROJECTIONS (Plaid Holdings + Market API) ---
    tickers = list(active_portfolio.keys()) if active_portfolio else []
    market_data = fetch_live_dividend_market_data(tickers)
    
    proj_rows = []
    total_annual_projected = 0.0

    for t, pos in active_portfolio.items() if active_portfolio else {}.items():
        # CRITICAL FIX: Look for 'quantity', 'qty', OR 'shares'
        shares = float(pos.get('shares', pos.get('quantity', pos.get('qty', 0.0))))
        data = market_data.get(t, {"rate": 0.0, "pay_date": "TBD", "ex_date": "TBD"})
        
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

    # --- 3. COMMAND CENTER UI ---
    k1, k2, k3 = st.columns(3)
    k1.metric("Lifetime Earned", f"${total_lifetime_earned:,.2f}")
    k2.metric("Annual Projection", f"${total_annual_projected:,.2f}")
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
            st.info("💡 **Strategy:** Buy shares *before* the **Ex-Div Date** to qualify for the next payout.")
        else:
            st.info("No dividend-paying assets detected in your current holdings.")

    with tab_h:
        if hist_rows:
            df_h = pd.DataFrame(hist_rows)
            df_h['Date'] = pd.to_datetime(df_h['Date'], errors='coerce')
            st.dataframe(df_h.sort_values("Date", ascending=False).dropna(subset=['Date']), 
                         use_container_width=True, hide_index=True)
        else:
            st.warning("No historical dividend payments found.")
