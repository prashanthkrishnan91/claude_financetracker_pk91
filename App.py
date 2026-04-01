import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.express as px
from datetime import datetime, timedelta

# --- 1. CONFIG & THEME ---
st.set_page_config(page_title="Wealth Architect Pro", layout="wide", page_icon="📈")

st.markdown("""
    <style>
    .main { background-color: #0f172a; color: white; }
    div[data-testid="stMetric"] { background-color: #1e293b; border: 1px solid #334155; padding: 15px; border-radius: 12px; }
    .stTabs [data-baseweb="tab-list"] { gap: 8px; }
    .stTabs [data-baseweb="tab"] { background-color: #1e293b; border-radius: 4px 4px 0 0; padding: 10px 20px; color: white; }
    .stTabs [aria-selected="true"] { background-color: #3b82f6 !important; }
    </style>
    """, unsafe_allow_html=True)

# --- 2. DATA CORE (MIRRORING YOUR RECENT ACTIVITY) ---
if 'portfolio' not in st.session_state:
    # Based on your CSV: Adding Ticker, Qty, and mocked Purchase Date for Tax Logic
    st.session_state.portfolio = {
        'NVDA': {'qty': 35.50, 'cost': 120.50, 'date': '2025-06-15'},
        'NFLX': {'qty': 21.33, 'cost': 580.00, 'date': '2024-11-10'}, # Long Term
        'AAPL': {'qty': 16.10, 'cost': 175.20, 'date': '2025-08-20'},
        'VOO':  {'qty': 5.68,  'cost': 450.00, 'date': '2025-01-05'},
        'AMD':  {'qty': 3.22,  'cost': 160.00, 'date': '2025-12-01'},
        'META': {'qty': 2.30,  'cost': 480.00, 'date': '2024-03-12'}, # Long Term
        'WMT':  {'qty': 13.56, 'cost': 60.00,  'date': '2025-02-15'}
    }

# --- 3. ANALYTICS ENGINE ---
def get_analysis(ticker, data):
    try:
        ytick = ticker.replace('.', '-')
        df_live = yf.download(ytick, period="1y", interval="1d", progress=False)
        close = df_live['Close'][ytick] if isinstance(df_live.columns, pd.MultiIndex) else df_live['Close']
        curr = float(close.iloc[-1])
        
        # RSI & Tax Calculation
        delta = close.diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rsi = 100 - (100 / (1 + (gain / loss).iloc[-1]))
        
        buy_date = datetime.strptime(data['date'], '%Y-%m-%d')
        is_long = (datetime.now() - buy_date).days > 365
        lt_date = buy_date + timedelta(days=366)
        
        # Recommendations
        status = "HOLD"
        if rsi < 35: status = "🔥 STRONG BUY"
        elif rsi > 75: status = "💰 TAKE PROFITS"
        elif curr < close.rolling(200).mean().iloc[-1]: status = "⚠️ TRIM"

        return {
            "Price": curr, "Value": curr * data['qty'], "P/L": (curr - data['cost']) * data['qty'],
            "RSI": round(rsi, 1), "Status": status, "Tax": "Long" if is_long else "Short",
            "LT_Date": lt_date.strftime('%Y-%m-%d')
        }
    except: return None

# --- 4. UI TABS (JSX MIRROR) ---
st.title("🛡️ Wealth Architect AI v3.0")
t1, t2, t3, t4, t5 = st.tabs(["Dashboard", "Portfolio", "Recommendations", "Tax Playbook", "Activity"])

# GLOBAL DATA FETCH
if st.button("🔄 REFRESH LIVE MARKET DATA"):
    with st.spinner("Analyzing Portfolio..."):
        results = []
        for t, d in st.session_state.portfolio.items():
            res = get_analysis(t, d)
            if res:
                res.update({"Ticker": t, "Shares": d['qty'], "Avg Cost": d['cost']})
                results.append(res)
        st.session_state.current_df = pd.DataFrame(results)

# --- TAB 1: DASHBOARD ---
with t1:
    if 'current_df' in st.session_state:
        df = st.session_state.current_df
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Value", f"${df['Value'].sum():,.2f}")
        c2.metric("Total Gain/Loss", f"${df['P/L'].sum():,.2f}", f"{((df['Value'].sum()/sum(d['qty']*d['cost'] for d in st.session_state.portfolio.values())-1)*100):.2f}%")
        c3.metric("Next $900 Deposit", "April 3, 2026")
        c4.metric("Market Sentiment", "Bullish" if df['RSI'].mean() < 60 else "Overextended")
        
        col_left, col_right = st.columns([2,1])
        with col_left:
            st.plotly_chart(px.line(df, x="Ticker", y="RSI", title="Relative Strength Index (Momentum)", template="plotly_dark"), use_container_width=True)
        with col_right:
            st.plotly_chart(px.pie(df, values='Value', names='Ticker', title="Asset Allocation", hole=0.5, template="plotly_dark"), use_container_width=True)
    else:
        st.info("Hit 'Refresh' to load Dashboard data.")

# --- TAB 2: PORTFOLIO ---
with t2:
    if 'current_df' in st.session_state:
        st.subheader("Holdings Analysis")
        # Direct Mirror of JSX Columns
        display_df = st.session_state.current_df[['Ticker', 'Shares', 'Avg Cost', 'Price', 'Value', 'P/L', 'RSI', 'Tax']]
        st.dataframe(display_df.style.map(
            lambda x: "color: #4ade80" if "Long" in str(x) or (isinstance(x, (int, float)) and x > 0) else ("color: #f87171" if "Short" in str(x) or (isinstance(x, (int, float)) and x < 0) else ""),
            subset=['Tax', 'P/L']
        ), use_container_width=True, hide_index=True)

# --- TAB 3: RECOMMENDATIONS ---
with t3:
    if 'current_df' in st.session_state:
        df = st.session_state.current_df
        st.subheader("🎯 $900 Bi-Weekly Deployment")
        best = df.sort_values(by="RSI").iloc[0]
        st.success(f"**NEXT BUY (Apr 3):** Allocate the $900 to **{best['Ticker']}**.")
        st.write(f"**Reasoning:** Lowest RSI ({best['RSI']}) indicates highest recovery potential.")
        
        st.divider()
        st.subheader("✂️ Suggested Trims")
        trims = df[df['Status'] == "⚠️ TRIM"]
        if not trims.empty:
            for _, row in trims.iterrows():
                st.warning(f"**TRIM {row['Ticker']}:** Sell ~10% to lock in profits or mitigate loss. RSI is currently {row['RSI']}.")
        else:
            st.write("No urgent trims required today.")

# --- TAB 4: TAX PLAYBOOK ---
with t4:
    if 'current_df' in st.session_state:
        df = st.session_state.current_df
        st.subheader("Tax Efficiency Engine")
        
        st.write("### Upcoming Long-Term Transitions")
        st.write("Selling after these dates will cut your tax bill by ~50%:")
        transitions = df[df['Tax'] == 'Short'][['Ticker', 'LT_Date']].sort_values(by='LT_Date')
        st.table(transitions)
        
        c_st, c_lt = st.columns(2)
        st_val = df[df['Tax'] == 'Short']['Value'].sum()
        lt_val = df[df['Tax'] == 'Long']['Value'].sum()
        c_st.metric("Short-Term Exposure", f"${st_val:,.2f}")
        c_lt.metric("Long-Term (Tax Safe)", f"${lt_val:,.2f}")

# --- TAB 5: ACTIVITY & HISTORY ---
with t5:
    st.subheader("Transaction & Deposit History")
    
    # Bi-Weekly Scheduler Logic
    st.write("### Upcoming $900 Deposits (2026)")
    start_date = datetime(2026, 4, 3)
    schedule = []
    for i in range(10):
        dep_date = start_date + timedelta(days=i*14)
        schedule.append({"Date": dep_date.strftime('%Y-%m-%d'), "Amount": "$900.00", "Status": "Scheduled"})
    st.table(pd.DataFrame(schedule))
    
    st.divider()
    st.subheader("Robinhood CSV Sync")
    st.file_uploader("Upload newest Account Activity to sync holdings", type="csv")
