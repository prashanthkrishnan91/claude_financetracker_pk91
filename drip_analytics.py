import streamlit as st
import pandas as pd
import plotly.express as px
import yfinance as yf
import re

@st.cache_data(ttl=86400)
def get_market_yields(tickers):
    yield_map = {}
    for t in tickers:
        if t in ["BTC", "ETH", "XRP", "SOL", "DOGE"]: continue
        try:
            yf_ticker = t.replace('-', '.')
            tk = yf.Ticker(yf_ticker)
            rate = tk.info.get("dividendRate") or tk.info.get("trailingAnnualDividendRate", 0.0)
            yield_map[t] = float(rate) if rate else 0.0
        except:
            yield_map[t] = 0.0
    return yield_map

def clean_numeric(value):
    """Senior Dev Fix: Handles '$1,200.00', '(5.00)', and None types."""
    if value is None: return 0.0
    try:
        s = str(value).replace('$', '').replace(',', '').replace(' ', '')
        if '(' in s: s = '-' + s.replace('(', '').replace(')', '')
        return float(s)
    except:
        return 0.0

def render_drip_dashboard(active_portfolio, tx_list):
    st.markdown("### 💧 Dividend Intelligence")

    # --- 1. ROBUST HISTORICAL FILTERING ---
    hist_rows = []
    total_lifetime_earned = 0.0
    
    # Robinhood codes: CDIV, DIV, Dividend, Record Day Dividend
    div_pattern = re.compile(r"(DIV|CDIV|REINVEST|DIVIDEND)", re.IGNORECASE)

    for tx in (tx_list or []):
        # We check both trans_code AND description in case of Robinhood formatting shifts
        content_to_check = f"{tx.get('trans_code', '')} {tx.get('description', '')}".upper()
        
        if div_pattern.search(content_to_check):
            amt = clean_numeric(tx.get('amount', 0.0))
            # Dividends are credits, but some CSVs export them as negative or positive
            amt = abs(amt) 
            
            if amt > 0:
                total_lifetime_earned += amt
                hist_rows.append({
                    "Date": tx.get("date"),
                    "Ticker": tx.get("instrument") or tx.get("ticker", "Unknown"),
                    "Amount": amt
                })

    # --- 2. FUTURE PROJECTIONS (Plaid holdings) ---
    tickers = list(active_portfolio.keys())
    market_rates = get_market_yields(tickers)
    
    proj_rows = []
    total_annual_projected = 0.0

    for t, pos in active_portfolio.items():
        shares = float(pos.get('shares', 0.0))
        rate = market_rates.get(t, 0.0)
        annual_inc = shares * rate
        if annual_inc > 0:
            total_annual_projected += annual_inc
            proj_rows.append({"Ticker": t, "Shares": shares, "Yield ($/sh)": rate, "Annual Inc.": annual_inc})

    # --- 3. UI RENDERING ---
    k1, k2, k3 = st.columns(3)
    k1.metric("Lifetime Earned", f"${total_lifetime_earned:,.2f}")
    k2.metric("Annual Projection", f"${total_annual_projected:,.2f}")
    k3.metric("Est. Monthly Income", f"${(total_annual_projected / 12):,.2f}")

    st.divider()

    if total_annual_projected > 0:
        months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        chart_df = pd.DataFrame({"Month": months, "Projected Income": [total_annual_projected/12]*12})
        fig = px.bar(chart_df, x="Month", y="Projected Income", title="Monthly Passive Income Projection (Avg)",
                     template="plotly_dark", color_discrete_sequence=["#00C805"])
        fig.update_layout(height=280, margin=dict(t=40, b=0, l=0, r=0))
        st.plotly_chart(fig, use_container_width=True)

    tab_f, tab_h = st.tabs(["🚀 Future Projections", "📜 Historical Payouts"])
    with tab_f:
        if proj_rows:
            st.dataframe(pd.DataFrame(proj_rows).sort_values("Annual Inc.", ascending=False), 
                         use_container_width=True, hide_index=True)
        else: st.info("No dividend holdings in Plaid sync.")

    with tab_h:
        if hist_rows:
            df_h = pd.DataFrame(hist_rows)
            df_h['Date'] = pd.to_datetime(df_h['Date']).dt.date
            st.dataframe(df_h.sort_values("Date", ascending=False), use_container_width=True, hide_index=True)
        else: st.warning("603 rows found in store, but none matched 'Dividend' keywords. Check CSV headers.")
