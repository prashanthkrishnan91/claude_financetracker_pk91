import streamlit as st
import yfinance as yf
import pandas as pd
from datetime import datetime

# --- APP CONFIG ---
st.set_page_config(page_title="Hedge Fund Manager Pro", layout="wide")
st.title("📈 Global Alpha: Portfolio & Recommendation Engine")

# --- 1. DATA CORE: HOLDINGS FROM YOUR ROBINHOOD ---
if 'portfolio' not in st.session_state:
    st.session_state.portfolio = {
        'NFLX': 21.33, 'GLD': 6.64, 'AMD': 3.22, 'BRK-B': 4.51, 
        'SNOW': 3.73, 'KLAR': 11.0, 'RIVN': 10.0, 'CAVA': 1.0
    }

# --- CUSTOM RSI CALCULATOR ---
def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).ewm(alpha=1/period, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/period, adjust=False).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

# --- 2. LIVE PRICE & RECOMMENDATION ENGINE ---
def get_live_advice(ticker, qty):
    try:
        data = yf.download(ticker, period="1y", interval="1d", progress=False)
        
        # Extract scalar values correctly
        if isinstance(data.columns, pd.MultiIndex):
            close_series = data['Close'][ticker]
        else:
            close_series = data['Close']
            
        current_price = float(close_series.iloc[-1])
        
        # Technical Indicators natively in pandas
        rsi_series = calculate_rsi(close_series, 14)
        rsi = float(rsi_series.iloc[-1])
        sma_50 = float(close_series.rolling(window=50).mean().iloc[-1])
        
        # Recommendation Logic
        value = current_price * qty
        action = "HOLD"
        color = "white"
        
        if rsi < 35:
            action = "🔥 STRONG BUY (Oversold)"
        elif current_price < sma_50 * 0.90:
            action = "⚠️ TRIM / STOP LOSS"
        elif rsi > 70:
            action = "💰 TAKE PROFITS (Overbought)"
            
        return {
            "Price": round(current_price, 2),
            "Value": round(value, 2),
            "RSI": round(rsi, 2),
            "Advice": action
        }
    except Exception as e:
        return None

# --- 3. UI: THE DASHBOARD ---
st.sidebar.header("Deposit Control")
deposit_amount = st.sidebar.number_input("Bi-Weekly Deposit ($)", value=900)
next_deposit = "April 3, 2026"
st.sidebar.info(f"Next $900 Deployment: **{next_deposit}**")

if st.button('🔄 REFRESH LIVE PRICES & RE-CALCULATE'):
    rows = []
    total_val = 0
    
    with st.spinner('Syncing with Market...'):
        for t, q in st.session_state.portfolio.items():
            stats = get_live_advice(t, q)
            if stats:
                rows.append({
                    "Ticker": t,
                    "Shares": q,
                    "Live Price": stats['Price'],
                    "Position Value": stats['Value'],
                    "RSI (14d)": stats['RSI'],
                    "AI RECOMMENDATION": stats['Advice']
                })
                total_val += stats['Value']

    if rows:
        df = pd.DataFrame(rows)
        
        # KPIs
        c1, c2, c3 = st.columns(3)
        c1.metric("Total Net Worth", f"${total_val:,.2f}")
        c2.metric("Cash for April 3rd", f"${deposit_amount}")
        
        # Display Table with Highlighting
        st.dataframe(df.style.applymap(
            lambda x: "background-color: #d4edda; color: green" if "BUY" in str(x) else 
                      ("background-color: #f8d7da; color: red" if "TRIM" in str(x) else ""), 
            subset=['AI RECOMMENDATION']
        ), use_container_width=True)
        
        # Smart $900 Allocation Plan
        st.subheader("🎯 Bi-Weekly Allocation Plan (Friday, April 3rd)")
        best_buy = df.sort_values(by="RSI (14d)").iloc[0]
        st.success(f"Strategy: Deploy the $900 into **{best_buy['Ticker']}**. It has the lowest RSI ({best_buy['RSI (14d)']}), indicating the highest growth potential for this cycle.")
    else:
        st.error("Failed to fetch market data. Please try again.")

# --- 4. PORTFOLIO UPDATES ---
with st.expander("Update Holdings"):
    st.info("To update your holdings, modify the dictionary at the top of your App.py file in GitHub, or we can build a CSV upload feature here later.")
