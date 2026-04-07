import streamlit as st
import pandas as pd
import plotly.express as px
import yfinance as yf
from datetime import datetime

@st.cache_data(ttl=86400, show_spinner=False)
def fetch_dividend_market_intel(tickers):
    """Fetches yield, pay date, and ex-div date from yfinance."""
    results = {}
    for t in tickers:
        if t in ["BTC", "ETH", "XRP", "SOL", "DOGE", "USD"]: continue
        try:
            yf_ticker = t.replace('-', '.')
            tk = yf.Ticker(yf_ticker)
            info = tk.info
            
            # 1. Extraction of Annual Rate
            rate = info.get("dividendRate") or info.get("trailingAnnualDividendRate", 0.0)
            
            # 2. Extraction of Calendar Dates (Ex-Div & Pay Date)
            pay_date, ex_date = "TBD", "TBD"
            cal = tk.calendar
            if isinstance(cal, dict):
                pay_date = cal.get("Dividend Date", "TBD")
                ex_date = cal.get("Ex-Dividend Date", "TBD")
            elif hasattr(cal, 'index'):
                if "Dividend Date" in cal.index: pay_date = cal.loc["Dividend Date"].iloc[0]
                if "Ex-Dividend Date" in cal.index: ex_date = cal.loc["Ex-Dividend Date"].iloc[0]
            
            # Format to clean string
            if pd.notnull(pay_date) and hasattr(pay_date, 'strftime'): pay_date = pay_date.strftime('%Y-%m-%d')
            if pd.notnull(ex_date) and hasattr(ex_date, 'strftime'): ex_date = ex_date.strftime('%Y-%m-%d')

            results[t] = {"rate": float(rate) if rate else 0.0, "pay": str(pay_date), "ex": str(ex_date)}
        except:
            results[t] = {"rate": 0.0, "pay": "TBD", "ex": "TBD"}
    return results

def clean_amt(val):
    """Cleans currency strings: '$1,234.56' -> 1234.56."""
    if val is None or val == "": return 0.0
    try:
        s = str(val).replace('$', '').replace(',', '').replace(' ', '')
        if '(' in s and ')' in s: s = "-" + s.replace('(', '').replace(')', '')
        return float(s)
    except: return 0.0

def render_drip_dashboard(active_portfolio, tx_list=None, plaid_snap=None):
    st.markdown("### 💧 Dividend Intelligence")

    # --- 1. HISTORICAL ANALYSIS (Plaid First -> CSV Fallback) ---
    raw_history = []
    if plaid_snap and isinstance(plaid_snap, dict) and "transactions" in plaid_snap:
        raw_history.extend(plaid_snap["transactions"])
    if tx_list:
        if isinstance(tx_list, list): raw_history.extend(tx_list)
        elif hasattr(tx_list, "to_dict"): raw_history.extend(tx_list.to_dict('records'))

    hist_rows = []
    total_lifetime_earned = 0.0
    
    # Senior Dev Logic: Schema-Agnostic extraction
    for tx in raw_history:
        if not isinstance(tx, dict): continue
        
        # Determine Code and Amount by scanning all likely keys
        code, amt = "", 0.0
        for k, v in tx.items():
            kl = str(k).lower().strip()
            if any(x in kl for x in ['code', 'type', 'activity', 'description']):
                v_str = str(v).upper().strip()
                if v_str == "CDIV" or "CASH DIVIDEND" in v_str: code = "CDIV"
            if any(x in kl for x in ['amount', 'net', 'total', 'value']):
                val = clean_amt(v)
                if val > 0: amt = val # Ignore reinvestment outflows

        if code == "CDIV" and amt > 0:
            total_lifetime_earned += amt
            d_val = tx.get('date', tx.get('Activity Date', tx.get('Date', 'Unknown')))
            t_val = tx.get('instrument', tx.get('Ticker', tx.get('Symbol', 'Unknown')))
            hist_rows.append({"Date": d_val, "Ticker": t_val, "Amount": amt})

    # --- 2. FUTURE PROJECTIONS & MARKET RESEARCH ---
    tickers = list(active_portfolio.keys()) if active_portfolio else []
    market_intel = fetch_dividend_market_intel(tickers)
    
    proj_rows, total_annual_proj = [], 0.0
    for t, pos in (active_portfolio.items() if active_portfolio else {}).items():
        qty = float(pos.get('shares', pos.get('quantity', pos.get('qty', 0.0))))
        intel = market_intel.get(t, {"rate": 0.0, "pay": "TBD", "ex": "TBD"})
        
        income = qty * intel["rate"]
        if income > 0:
            total_annual_proj += income
            proj_rows.append({
                "Ticker": t, "Ex-Div Date": intel["ex"], "Next Pay Date": intel["pay"],
                "Annual Income": income, "Yield ($/sh)": intel["rate"], "Shares": qty
            })

    # --- 3. UI: COMMAND CENTER ---
    k1, k2, k3 = st.columns(3)
    k1.metric("Lifetime Earned", f"${total_lifetime_earned:,.2f}", help="Sum of positive CDIV cash inflows.")
    k2.metric("Annual Projection", f"${total_annual_proj:,.2f}")
    k3.metric("Est. Monthly Income", f"${(total_annual_proj / 12):,.2f}")

    st.divider()

    tab_f, tab_h = st.tabs(["🚀 Future Projections & Payouts", "📜 Historical Payouts"])
    
    with tab_f:
        if proj_rows:
            st.dataframe(pd.DataFrame(proj_rows).sort_values("Next Pay Date"), use_container_width=True, hide_index=True)
            st.info("💡 **Strategy**: Buy shares before the **Ex-Div Date** to capture the next payment.")
        else: st.info("No dividend-paying assets in your current sync.")

    with tab_h:
        if hist_rows:
            df_h = pd.DataFrame(hist_rows)
            df_h['Date'] = pd.to_datetime(df_h['Date'], errors='coerce')
            st.dataframe(df_h.sort_values("Date", ascending=False).dropna(subset=['Date']), use_container_width=True, hide_index=True)
        else: st.warning("No historical 'CDIV' payments found. Check CSV upload.")
