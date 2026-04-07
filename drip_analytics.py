import streamlit as st
import pandas as pd
import yfinance as yf

@st.cache_data(ttl=86400, show_spinner=False)
def fetch_live_dividend_market_data(tickers):
    """Fetches yield, pay date, and ex-div date from yfinance."""
    res = {}
    for t in tickers:
        if t in ["BTC", "ETH", "XRP", "SOL", "DOGE", "USD"]: continue
        try:
            tk = yf.Ticker(t.replace('-', '.'))
            info = tk.info
            
            rate = info.get("dividendRate") or info.get("trailingAnnualDividendRate", 0.0)
            
            pay_date, ex_date = "TBD", "TBD"
            cal = tk.calendar
            if isinstance(cal, dict):
                pay_date = cal.get("Dividend Date", "TBD")
                ex_date = cal.get("Ex-Dividend Date", "TBD")
            elif hasattr(cal, 'index'):
                if "Dividend Date" in cal.index: pay_date = cal.loc["Dividend Date"].iloc[0]
                if "Ex-Dividend Date" in cal.index: ex_date = cal.loc["Ex-Dividend Date"].iloc[0]
            
            if pd.notnull(pay_date) and hasattr(pay_date, 'strftime'): pay_date = pay_date.strftime('%Y-%m-%d')
            if pd.notnull(ex_date) and hasattr(ex_date, 'strftime'): ex_date = ex_date.strftime('%Y-%m-%d')
            
            res[t] = {"rate": float(rate) if rate else 0.0, "pay_date": str(pay_date), "ex_date": str(ex_date)}
        except:
            res[t] = {"rate": 0.0, "pay_date": "TBD", "ex_date": "TBD"}
    return res

def clean_amount(val):
    """Safely converts currency string to float WITHOUT removing negative signs."""
    if val is None or val == "": return 0.0
    try:
        s = str(val).replace('$', '').replace(',', '').replace(' ', '')
        # Handle accounting format for negative numbers: (5.00) -> -5.00
        if '(' in s and ')' in s:
            return -float(s.replace('(', '').replace(')', ''))
        return float(s)
    except:
        return 0.0

def render_drip_dashboard(active_portfolio, tx_list=None, plaid_snap=None):
    st.markdown("### 💧 Dividend Intelligence")

    # --- 1. HISTORICAL DATA EXTRACTION (STRICT FILTERING) ---
    raw_history = []
    if plaid_snap and isinstance(plaid_snap, dict) and "transactions" in plaid_snap:
        raw_history.extend(plaid_snap["transactions"])
    if tx_list:
        if isinstance(tx_list, list): raw_history.extend(tx_list)
        elif hasattr(tx_list, "to_dict"): raw_history.extend(tx_list.to_dict('records'))

    hist_rows = []
    total_lifetime_earned = 0.0
    
    for tx in raw_history:
        if not isinstance(tx, dict): continue
        
        # 1. STRICT MATCH: Isolate the transaction code
        code = str(tx.get('trans_code', tx.get('Trans Code', tx.get('Activity Type', tx.get('type', ''))))).upper().strip()
        
        # We only care if the code is explicitly a cash dividend
        if code in ['CDIV', 'CASH DIVIDEND']:
            
            # 2. STRICT MATH: Get the exact float value (preserving negatives)
            amt_raw = tx.get('amount', tx.get('Amount', tx.get('Net Amount', 0.0)))
            amt = clean_amount(amt_raw)
            
            # 3. STRICT ADDITION: Only add actual positive cash inflows
            if amt > 0:
                total_lifetime_earned += amt
                
                # Safely extract Date and Ticker for the UI table
                d_val = tx.get('date', tx.get('Activity Date', tx.get('Process Date', 'Unknown')))
                t_val = tx.get('instrument', tx.get('Ticker', tx.get('Symbol', 'Unknown')))
                
                hist_rows.append({"Date": d_val, "Ticker": t_val, "Amount": amt})

    # --- 2. FUTURE PROJECTIONS (Plaid Holdings + Market API) ---
    tickers = list(active_portfolio.keys()) if active_portfolio else []
    market_data = fetch_live_dividend_market_data(tickers)
    
    proj_rows = []
    total_annual_projected = 0.0

    for t, pos in (active_portfolio.items() if active_portfolio else {}):
        # Reliably get share count regardless of Plaid's exact key
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

    # --- 3. UI RENDERING ---
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
            st.warning("No historical 'CDIV' payments found.")
