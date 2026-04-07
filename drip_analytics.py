import streamlit as st
import pandas as pd
import plotly.express as px
import yfinance as yf
from datetime import datetime

@st.cache_data(ttl=86400)
def get_market_dividend_data(tickers):
    """Fetches yield and next payout date via yfinance."""
    results = {}
    for t in tickers:
        if t in ["BTC", "ETH", "XRP", "SOL", "DOGE"]: continue
        try:
            yf_ticker = t.replace('-', '.')
            tk = yf.Ticker(yf_ticker)
            info = tk.info
            
            # Get Rate
            rate = info.get("dividendRate") or info.get("trailingAnnualDividendRate", 0.0)
            
            # Get Next Date from Calendar
            next_date = "TBD"
            calendar = tk.calendar
            if calendar is not None and not calendar.empty:
                # Some tickers return a dict, some a DataFrame
                if isinstance(calendar, dict) and "Dividend Date" in calendar:
                    next_date = calendar["Dividend Date"].strftime('%Y-%m-%d')
                elif "Dividend Date" in calendar.index:
                    next_date = calendar.loc["Dividend Date"].iloc[0].strftime('%Y-%m-%d')

            results[t] = {"rate": float(rate) if rate else 0.0, "date": next_date}
        except:
            results[t] = {"rate": 0.0, "date": "TBD"}
    return results

def clean_numeric(value):
    if value is None: return 0.0
    try:
        s = str(value).replace('$', '').replace(',', '').replace(' ', '')
        if '(' in s: s = '-' + s.replace('(', '').replace(')', '')
        return float(s)
    except: return 0.0

def render_drip_dashboard(active_portfolio, tx_list):
    st.markdown("### 💧 Dividend Intelligence")

    # --- 1. STRICT HISTORICAL FILTER (Matches your $294.14 check) ---
    hist_rows = []
    total_lifetime_earned = 0.0
    
    # We strictly target CDIV or DIV to avoid counting the 'Buy' leg of a DRIP
    for tx in (tx_list or []):
        code = str(tx.get('trans_code', '')).upper()
        if code in ['CDIV', 'DIV']:
            amt = abs(clean_numeric(tx.get('amount', 0.0)))
            if amt > 0:
                total_lifetime_earned += amt
                hist_rows.append({
                    "Date": tx.get("date"),
                    "Ticker": tx.get("instrument") or tx.get("ticker", "Unknown"),
                    "Amount": amt
                })

    # --- 2. FUTURE PROJECTIONS (Plaid + Calendar Analysis) ---
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

    # --- 3. UI RENDERING ---
    k1, k2, k3 = st.columns(3)
    k1.metric("Lifetime Earned", f"${total_lifetime_earned:,.2f}", help="Sum of CDIV/DIV codes in your CSV")
    k2.metric("Annual Projection", f"${total_annual_projected:,.2f}")
    k3.metric("Est. Monthly Income", f"${(total_annual_projected / 12):,.2f}")

    st.divider()

    tab_f, tab_h = st.tabs(["🚀 Future Projections", "📜 Historical Payouts"])
    
    with tab_f:
        if proj_rows:
            df_p = pd.DataFrame(proj_rows).sort_values("Projected Date")
            st.dataframe(
                df_p.style.format({"Yield ($/sh)": "${:.2f}", "Annual Income": "${:,.2f}", "Shares": "{:.2f}"}),
                use_container_width=True, hide_index=True
            )
        else:
            st.info("No upcoming dividends detected for your Plaid holdings.")

    with tab_h:
        if hist_rows:
            df_h = pd.DataFrame(hist_rows)
            df_h['Date'] = pd.to_datetime(df_h['Date']).dt.date
            st.dataframe(df_h.sort_values("Date", ascending=False), use_container_width=True, hide_index=True)
        else:
            st.warning("No CDIV transactions found. Ensure your Robinhood CSV is uploaded.")
