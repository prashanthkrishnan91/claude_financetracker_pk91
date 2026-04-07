import streamlit as st
import pandas as pd
import yfinance as yf

@st.cache_data(ttl=86400, show_spinner=False)
def fetch_dividend_intel(tickers):
    """Fetches yield, pay date, and ex-div date from yfinance."""
    results = {}
    for t in tickers:
        if t in ["BTC", "ETH", "XRP", "SOL", "DOGE", "USD"]: continue
        try:
            tk = yf.Ticker(t.replace('-', '.'))
            info = tk.info
            rate = info.get("dividendRate") or info.get("trailingAnnualDividendRate", 0.0)
            
            pay_date, ex_date = "TBD", "TBD"
            cal = tk.calendar
            if isinstance(cal, pd.DataFrame) and not cal.empty:
                if "Dividend Date" in cal.index: pay_date = cal.loc["Dividend Date"].iloc[0]
                if "Ex-Dividend Date" in cal.index: ex_date = cal.loc["Ex-Dividend Date"].iloc[0]
            
            # Format dates
            if hasattr(pay_date, 'strftime'): pay_date = pay_date.strftime('%Y-%m-%d')
            if hasattr(ex_date, 'strftime'): ex_date = ex_date.strftime('%Y-%m-%d')

            results[t] = {"rate": float(rate), "pay": str(pay_date), "ex": str(ex_date)}
        except:
            results[t] = {"rate": 0.0, "pay": "TBD", "ex": "TBD"}
    return results

def render_drip_dashboard(active_portfolio, tx_list=None, plaid_snap=None):
    st.markdown("### 💧 Dividend Intelligence")

    # --- HISTORICAL (Strict CDIV Filter) ---
    raw_history = []
    if plaid_snap and "transactions" in plaid_snap:
        raw_history.extend(plaid_snap["transactions"])
    if tx_list:
        raw_history.extend(tx_list if isinstance(tx_list, list) else list(tx_list.values()))

    total_lifetime_earned = 0.0
    hist_rows = []
    
    for tx in raw_history:
        if not isinstance(tx, dict): continue
        # Unified key check for 'trans_code' or 'Activity Type'
        code = str(tx.get('trans_code') or tx.get('Activity Type') or tx.get('type') or '').upper()
        if code in ['CDIV', 'CASH DIVIDEND']:
            # Clean numeric string (handles '$', ',', and '()')
            val_str = str(tx.get('amount') or tx.get('Amount') or '0').replace('$','').replace(',','')
            try:
                amt = float(val_str.replace('(','-').replace(')',''))
                if amt > 0:
                    total_lifetime_earned += amt
                    hist_rows.append({"Date": tx.get('date'), "Ticker": tx.get('instrument'), "Amount": amt})
            except: continue

    # --- FUTURE PROJECTIONS (FIXED: Removed double .items()) ---
    tickers = list(active_portfolio.keys()) if active_portfolio else []
    market_intel = fetch_dividend_intel(tickers)
    
    proj_rows = []
    total_annual_proj = 0.0

    # FIX: Corrected iteration to prevent AttributeError
    if active_portfolio:
        for t, pos in active_portfolio.items():
            # Handle both 'shares' and 'quantity' keys
            qty = float(pos.get('shares', pos.get('quantity', 0.0)))
            intel = market_intel.get(t, {"rate": 0.0, "pay": "TBD", "ex": "TBD"})
            
            income = qty * intel["rate"]
            if income > 0:
                total_annual_proj += income
                proj_rows.append({
                    "Ticker": t,
                    "Ex-Div Date": intel["ex"],
                    "Next Pay Date": intel["pay"],
                    "Annual Income": income,
                    "Yield ($/sh)": intel["rate"],
                    "Shares": qty
                })

    # --- UI ---
    k1, k2, k3 = st.columns(3)
    k1.metric("Lifetime Earned", f"${total_lifetime_earned:,.2f}") # Targets your $294.14
    k2.metric("Annual Projection", f"${total_annual_proj:,.2f}")
    k3.metric("Est. Monthly Income", f"${(total_annual_proj / 12):,.2f}")

    tab_f, tab_h = st.tabs(["🚀 Projections", "📜 History"])
    with tab_f:
        if proj_rows:
            st.dataframe(pd.DataFrame(proj_rows).sort_values("Next Pay Date"), use_container_width=True, hide_index=True)
        else: st.info("No dividend-paying assets detected.")
    with tab_h:
        if hist_rows:
            st.dataframe(pd.DataFrame(hist_rows).sort_values("Date", ascending=False), use_container_width=True, hide_index=True)
