import streamlit as st
import pandas as pd
import yfinance as yf
import re

# --- 1. SYSTEM LOGIC: INDESTRUCTIBLE DATA EXTRACTION ---

@st.cache_data(ttl=86400, show_spinner=False)
def fetch_dividend_intel(tickers):
    """Fetches yield and dates, aggressively handling YF API quirks."""
    results = {}
    for t in tickers:
        if t in ["BTC", "ETH", "USD"] or not isinstance(t, str): continue
        try:
            tk = yf.Ticker(t.replace('-', '.'))
            info = tk.info
            
            rate = info.get("dividendRate") or info.get("trailingAnnualDividendRate", 0.0)
            pay, ex = "TBD", "TBD"
            
            cal = tk.calendar
            if isinstance(cal, pd.DataFrame) and not cal.empty:
                if "Dividend Date" in cal.index: pay = cal.loc["Dividend Date"].iloc[0]
                if "Ex-Dividend Date" in cal.index: ex = cal.loc["Ex-Dividend Date"].iloc[0]
            
            if hasattr(pay, 'strftime'): pay = pay.strftime('%Y-%m-%d')
            if hasattr(ex, 'strftime'): ex = ex.strftime('%Y-%m-%d')

            results[t] = {"rate": float(rate) if rate else 0.0, "pay": str(pay), "ex": str(ex)}
        except:
            results[t] = {"rate": 0.0, "pay": "TBD", "ex": "TBD"}
    return results

def extract_largest_positive_float(tx_dict):
    """Scans an entire dictionary for the highest positive currency value."""
    max_val = 0.0
    for v in tx_dict.values():
        s = str(v).replace('$', '').replace(',', '').replace(' ', '')
        if re.match(r'^-?\d+(?:\.\d+)?$', s): # Regex to find true numbers
            val = float(s)
            if val > max_val: max_val = val
    return max_val

def flatten_history(tx_data):
    """Forces any weird dictionary/list structure into a flat list of dicts."""
    if not tx_data: return []
    if isinstance(tx_data, list): return tx_data
    if isinstance(tx_data, dict):
        # If it's a dict of dicts (like a JSON store)
        if all(isinstance(v, dict) for v in tx_data.values()):
            return list(tx_data.values())
        # If it's a single dict row
        return [tx_data]
    return []

# --- 2. UI/UX: THE DASHBOARD ---

def render_drip_dashboard(active_portfolio, tx_list=None, plaid_snap=None):
    st.markdown("### 💧 Dividend Intelligence")

    # 1. Aggressive Data Flattening
    raw_history = []
    if plaid_snap and isinstance(plaid_snap, dict):
        raw_history.extend(plaid_snap.get("transactions", []))
    
    raw_history.extend(flatten_history(tx_list))

    total_earned = 0.0
    hist_rows = []
    
    # 2. The "Net" approach to finding CDIVs
    for tx in raw_history:
        if not isinstance(tx, dict): continue
        
        row_string = " | ".join(str(v).upper() for v in tx.values() if v)
        
        # If it smells like a cash dividend, extract the money
        if "CDIV" in row_string or "CASH DIVIDEND" in row_string:
            amt = extract_largest_positive_float(tx)
            if amt > 0:
                total_earned += amt
                
                # Try to guess Date and Ticker
                d_val, t_val = "Unknown Date", "Unknown Ticker"
                for k, v in tx.items():
                    kl = str(k).lower()
                    if any(x in kl for x in ['date', 'time']): d_val = str(v)
                    if any(x in kl for x in ['ticker', 'symbol', 'instrument']): t_val = str(v)
                
                hist_rows.append({"Date": d_val, "Ticker": t_val, "Amount": amt})

    # 3. Future Projections Processing
    proj_rows = []
    total_proj = 0.0
    
    if isinstance(active_portfolio, dict) and active_portfolio:
        tickers = list(active_portfolio.keys())
        market_data = fetch_dividend_intel(tickers)
        
        for t, pos in active_portfolio.items():
            if not isinstance(pos, dict): continue
            
            # Find quantity however it's named
            qty = float(pos.get('shares', pos.get('quantity', pos.get('qty', 0.0))))
            intel = market_data.get(t, {"rate": 0.0, "pay": "TBD", "ex": "TBD"})
            
            income = qty * intel["rate"]
            if income > 0:
                total_proj += income
                proj_rows.append({
                    "Ticker": t, "Ex-Div Date": intel["ex"], "Pay Date": intel["pay"],
                    "Annual Income": income, "Yield/Sh": intel["rate"], "Shares": qty
                })

    # --- UI RENDERING ---
    k1, k2, k3 = st.columns(3)
    k1.metric("Lifetime Earned", f"${total_earned:,.2f}", help="Sum of historical CDIV inflows")
    k2.metric("Annual Projection", f"${total_proj:,.2f}", help="Based on current shares × YF trailing yield")
    k3.metric("Est. Monthly", f"${(total_proj / 12):,.2f}")

    st.divider()

    col1, col2 = st.columns([1.5, 1])
    
    with col1:
        st.markdown("#### 🚀 Upcoming Payouts (Actionable)")
        if proj_rows:
            df_p = pd.DataFrame(proj_rows).sort_values("Pay Date")
            st.dataframe(
                df_p.style.format({"Yield/Sh": "${:.2f}", "Annual Income": "${:,.2f}", "Shares": "{:.2f}"}),
                use_container_width=True, hide_index=True
            )
        else:
            st.info("No dividend-yielding stocks found in active portfolio.")

    with col2:
        st.markdown("#### 📜 Historical Ledger")
        if hist_rows:
            df_h = pd.DataFrame(hist_rows)
            # Try to sort by date if possible
            try: df_h['Date'] = pd.to_datetime(df_h['Date'])
            except: pass
            st.dataframe(df_h.sort_values("Date", ascending=False) if 'Date' in df_h else df_h, 
                         use_container_width=True, hide_index=True)
        else:
            st.warning("No 'CDIV' transactions found. Check if CSV uploaded correctly.")
