import streamlit as st
import pandas as pd
import plotly.express as px
import yfinance as yf
from datetime import datetime

@st.cache_data(ttl=86400)
def get_dividend_market_intelligence(tickers):
    """
    Fetches annual dividend rates and next pay dates from yfinance.
    """
    results = {}
    for t in tickers:
        if t in ["BTC", "ETH", "XRP", "SOL", "DOGE"]: continue
        try:
            # yfinance prefers dots for share classes (e.g., BRK-B -> BRK.B)
            yf_ticker = t.replace('-', '.')
            tk = yf.Ticker(yf_ticker)
            
            # 1. Get the Payout Rate ($ per share)
            info = tk.info
            rate = info.get("dividendRate") or info.get("trailingAnnualDividendRate", 0.0)
            
            # 2. Get the Next Payout Date from the Calendar
            next_date = "TBD"
            cal = tk.calendar
            if cal is not None and not cal.empty:
                # Handle dictionary or DataFrame return types from yfinance
                d_val = cal.get("Dividend Date") if isinstance(cal, dict) else cal.loc["Dividend Date"].iloc[0]
                if d_val:
                    next_date = d_val.strftime('%Y-%m-%d')
            
            results[t] = {"rate": float(rate) if rate else 0.0, "date": next_date}
        except:
            results[t] = {"rate": 0.0, "date": "TBD"}
    return results

def render_drip_dashboard(active_portfolio, tx_list, plaid_snap=None):
    st.markdown("### 💧 Dividend Intelligence")

    # --- 1. DATA SOURCE PRIORITIZATION ---
    # Priority 1: Plaid Cache Transactions | Priority 2: Robinhood CSV
    raw_history = []
    if plaid_snap and plaid_snap.get("transactions"):
        raw_history = plaid_snap["transactions"]
    elif tx_list:
        raw_history = tx_list

    # --- 2. HISTORICAL CALCULATION (Strict Payout Only) ---
    hist_rows = []
    total_lifetime_earned = 0.0
    
    for tx in (raw_history or []):
        code = str(tx.get('trans_code', tx.get('type', ''))).upper()
        # We STRICTLY look for CDIV (Cash Dividend) to match user's CSV check
        if code in ['CDIV', 'DIV', 'DIVIDEND']:
            # Robinhood CSV amounts for CDIV are positive credits
            try:
                amt_str = str(tx.get('amount', '0')).replace('$', '').replace(',', '').replace(' ', '')
                amt = float(amt_str)
                # Ensure we only count positive inflows, ignoring the 'buy' leg of reinvestments
                if amt > 0:
                    total_lifetime_earned += amt
                    hist_rows.append({
                        "Date": tx.get("date"),
                        "Ticker": tx.get("instrument") or tx.get("ticker", "Unknown"),
                        "Amount": amt
                    })
            except: continue

    # --- 3. FUTURE PROJECTIONS (Plaid Holdings + Market Intel) ---
    tickers = list(active_portfolio.keys())
    intel = get_dividend_market_intelligence(tickers)
    
    proj_rows = []
    total_annual_projected = 0.0 # Standardized variable name

    for t, pos in active_portfolio.items():
        shares = float(pos.get('shares', 0.0))
        m_data = intel.get(t, {"rate": 0.0, "date": "TBD"})
        annual_inc = shares * m_data["rate"]
        
        if annual_inc > 0:
            total_annual_projected += annual_inc
            proj_rows.append({
                "Ticker": t,
                "Next Est. Date": m_data["date"],
                "Annual Income": annual_inc,
                "Yield ($/sh)": m_data["rate"],
                "Shares": shares
            })

    # --- 4. UI: KPI SECTION ---
    k1, k2, k3 = st.columns(3)
    # This should now match the user's $294.14 expectation
    k1.metric("Lifetime Earned", f"${total_lifetime_earned:,.2f}", help="Sum of all CDIV/DIV codes.")
    k2.metric("Annual Projection", f"${total_annual_projected:,.2f}", help="Forward-looking 12 months.")
    k3.metric("Est. Monthly Income", f"${(total_annual_projected / 12):,.2f}")

    st.divider()

    # --- 5. DATA TABS ---
    tab_f, tab_h = st.tabs(["🚀 Future Projections", "📜 Historical Payouts"])
    
    with tab_f:
        if proj_rows:
            df_p = pd.DataFrame(proj_rows).sort_values("Next Est. Date")
            st.dataframe(
                df_p.style.format({"Yield ($/sh)": "${:.2f}", "Annual Income": "${:,.2f}", "Shares": "{:.2f}"}),
                use_container_width=True, hide_index=True
            )
        else:
            st.info("No dividend-paying assets detected in current holdings.")

    with tab_h:
        if hist_rows:
            df_h = pd.DataFrame(hist_rows)
            df_h['Date'] = pd.to_datetime(df_h['Date']).dt.date
            st.dataframe(df_h.sort_values("Date", ascending=False), use_container_width=True, hide_index=True)
        else:
            st.warning("No historical dividend transactions (CDIV) found.")
