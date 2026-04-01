import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.express as px
from datetime import datetime

# --- SETTINGS & STYLE ---
st.set_page_config(page_title="Wealth Architect AI", page_icon="🚀", layout="wide")

# Corrected CSS logic
st.markdown("""
    <style>
    .main { background-color: #0f172a; color: white; }
    div[data-testid="stMetric"] { 
        background-color: #1e293b; 
        padding: 20px; 
        border-radius: 12px; 
        border: 1px solid #334155;
    }
    .stDataFrame { border: 1px solid #334155; border-radius: 10px; }
    </style>
    """, unsafe_allow_html=True)

# --- 1. DATA CORE: YOUR FULL ROBINHOOD PORTFOLIO ---
if 'portfolio' not in st.session_state:
    st.session_state.portfolio = {
        'NVDA': 35.5022, 'NFLX': 21.3325, 'AAPL': 16.0975, 'WMT': 13.5583,
        'KLAR': 11.0, 'STUB': 23.3561, 'RIVN': 10.0, 'BLSH': 10.0,
        'SCHD': 8.7081, 'GLD': 6.6408, 'VOO': 5.6809, 'BRK-B': 4.5154,
        'GOOGL': 4.0033, 'SNOW': 3.7353, 'AMD': 3.2234, 'QQQ': 2.7495,
        'META': 2.3024, 'VTI': 1.9418, 'VHT': 1.8845, 'VYM': 20.4402,
        'VXUS': 19.7126, 'XLE': 15.2826, 'TSM': 1.3801, 'CAVA': 1.0,
        'RDDT': 1.0, 'BMWYY': 1.0, 'ALK': 0.6087
    }

# --- CUSTOM ANALYTICS ENGINE ---
def get_live_metrics(ticker, qty):
    try:
        ytick = ticker.replace('.', '-')
        data = yf.download(ytick, period="1y", interval="1d", progress=False)
        if data.empty: return None
        
        # Pull close price
        close = data['Close'][ytick] if isinstance(data.columns, pd.MultiIndex) else data['Close']
        current_price = float(close.iloc[-1])
        
        # Calculate RSI (14-day)
        delta = close.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs.iloc[-1]))
        
        # Simple Recommendation Logic
        advice = "HOLD"
        if rsi < 35: advice = "🔥 BUY (Oversold)"
        elif rsi > 70: advice = "💰 TRIM (Overbought)"
        elif current_price > close.rolling(window=50).mean().iloc[-1]: advice = "✅ ACCUMULATE"
        
        return {
            "Price": current_price,
            "Value": current_price * qty,
            "RSI": round(rsi, 1),
            "Status": advice
        }
    except:
        return None

# --- HEADER & KPI CARDS ---
st.title("🛡️ Wealth Architect AI")
st.write("Live Portfolio Dashboard & $900 Bi-Weekly Strategy")

c1, c2, c3, c4 = st.columns(4)

# Force a data fetch on start/button
if st.button('🔄 REFRESH PORTFOLIO & FETCH LIVE PRICES'):
    rows = []
    total_val = 0
    with st.spinner('Accessing Market Data...'):
        for t, q in st.session_state.portfolio.items():
            stats = get_live_metrics(t, q)
            if stats:
                rows.append({
                    "Asset": t, "Shares": q, "Price": f"${stats['Price']:,.2f}",
                    "Total Value": stats['Value'], "RSI": stats['RSI'], "Action": stats['Status']
                })
                total_val += stats['Value']
        
    df = pd.DataFrame(rows)
    
    # KPI Fill
    c1.metric("Net Portfolio Value", f"${total_val:,.2f}")
    c2.metric("Next Deposit", "$900.00", "April 3")
    c3.metric("Asset Count", len(df))
    c4.metric("Risk Profile", "Aggressive Growth")

    st.divider()

    # --- MAIN CONTENT ---
    col_table, col_chart = st.columns([2, 1])

    with col_table:
        st.subheader("📋 Live Holdings & AI Recommendations")
        # Fix: using .map() for modern pandas compatibility
        st.dataframe(df.style.map(
            lambda x: "background-color: #064e3b; color: #34d399" if "BUY" in str(x) or "ACCUMULATE" in str(x) else 
                      ("background-color: #7f1d1d; color: #f87171" if "TRIM" in str(x) else ""), 
            subset=['Action']
        ), use_container_width=True, hide_index=True)

    with col_chart:
        st.subheader("📊 Allocation")
        fig = px.pie(df, values='Total Value', names='Asset', hole=.4, template="plotly_dark")
        fig.update_layout(showlegend=False, margin=dict(l=0, r=0, b=0, t=0))
        st.plotly_chart(fig, use_container_width=True)

    # --- STRATEGY SECTION ---
    st.divider()
    st.subheader("🎯 $900 Rebalance Strategy (Friday, April 3rd)")
    
    # AI logic: Buy what is most oversold
    best_buy = df.sort_values(by='RSI').iloc[0]
    
    b1, b2 = st.columns(2)
    with b1:
        st.info(f"**Primary Target:** {best_buy['Asset']}")
        st.write(f"Deploy your $900 here. This asset has the lowest Relative Strength Index ({best_buy['RSI']}) in your portfolio, making it the most mathematically sound value buy for this cycle.")
    
    with b2:
        st.success("**Safe Alternative:** VOO / QQQ Split")
        st.write("Alternatively, put $450 into VOO and $450 into QQQ to maintain your baseline market exposure.")

else:
    st.info("👋 Welcome! Click the **Refresh** button above to load your Robinhood data and generate live recommendations.")
