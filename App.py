import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.express as px
from datetime import datetime, timedelta

# --- 1. ARCHITECTURAL CONFIG & THEME ---
st.set_page_config(page_title="Wealth Architect Pro", layout="wide", page_icon="🏦")

# Enhanced CSS for the "Wall Street" Look
st.markdown("""
    <style>
    .main { background-color: #0f172a; color: white; }
    div[data-testid="stMetric"] { background-color: #1e293b; border: 1px solid #334155; padding: 20px; border-radius: 12px; }
    .stTabs [data-baseweb="tab-list"] { gap: 12px; }
    .stTabs [data-baseweb="tab"] { background-color: #1e293b; padding: 12px 24px; border-radius: 8px 8px 0 0; }
    .stTabs [aria-selected="true"] { background-color: #2563eb !important; border-bottom: 3px solid #60a5fa; }
    .group-header { font-size: 1.2rem; font-weight: bold; margin-top: 20px; color: #94a3b8; border-left: 4px solid #2563eb; padding-left: 10px; }
    </style>
    """, unsafe_allow_html=True)

# --- 2. DATA ENGINE: CATEGORIZED HOLDINGS ---
# Mirroring the specific groupings and data points from your JSX logic
if 'portfolio' not in st.session_state:
    st.session_state.portfolio = {
        # CORE HOLDINGS
        'NVDA': {'qty': 35.5022, 'cost': 120.50, 'date': '2025-06-15', 'cat': 'Core'},
        'META': {'qty': 2.3024, 'cost': 480.00, 'date': '2024-03-12', 'cat': 'Core'},
        'AAPL': {'qty': 16.0975, 'cost': 175.20, 'date': '2025-08-20', 'cat': 'Core'},
        'NFLX': {'qty': 21.3325, 'cost': 580.00, 'date': '2024-11-10', 'cat': 'Core'},
        # ETFS
        'VOO':  {'qty': 5.6809,  'cost': 450.00, 'date': '2025-01-05', 'cat': 'ETF'},
        'QQQ':  {'qty': 2.7495,  'cost': 400.00, 'date': '2025-02-10', 'cat': 'ETF'},
        'SCHD': {'qty': 8.7081,  'cost': 75.00,  'date': '2025-03-01', 'cat': 'ETF'},
        # GROWTH / SPECULATIVE
        'AMD':  {'qty': 3.2234,  'cost': 160.00, 'date': '2025-12-01', 'cat': 'Growth'},
        'SNOW': {'qty': 3.7353,  'cost': 185.00, 'date': '2025-09-15', 'cat': 'Growth'},
        'RIVN': {'qty': 10.0000, 'cost': 15.50,  'date': '2025-10-01', 'cat': 'Growth'},
        # CRYPTO / OTHER
        'GLD':  {'qty': 6.6408,  'cost': 210.00, 'date': '2025-01-20', 'cat': 'Hedge'}
    }

# --- 3. CORE ANALYTICS (RSI + TAX + MOMENTUM) ---
def analyze_portfolio():
    results = []
    tickers = list(st.session_state.portfolio.keys())
    # Bulk fetch to avoid timeout
    data = yf.download([t.replace('.', '-') for t in tickers], period="1y", interval="1d", progress=False)
    
    for t in tickers:
        p = st.session_state.portfolio[t]
        try:
            # Handle Single vs Multi-Index Columns
            close = data['Close'][t.replace('.', '-')] if len(tickers) > 1 else data['Close']
            curr = float(close.iloc[-1])
            
            # RSI Calculation
            delta = close.diff()
            gain = (delta.where(delta > 0, 0)).rolling(14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
            rsi = 100 - (100 / (1 + (gain / loss).iloc[-1]))
            
            # Tax Logic
            buy_date = datetime.strptime(p['date'], '%Y-%m-%d')
            is_long = (datetime.now() - buy_date).days > 365
            lt_conversion = buy_date + timedelta(days=366)
            
            results.append({
                "Ticker": t, "Cat": p['cat'], "Shares": p['qty'], "Price": curr,
                "Value": curr * p['qty'], "P/L": (curr - p['cost']) * p['qty'],
                "RSI": round(rsi, 1), "Tax": "Long" if is_long else "Short",
                "LT_Date": lt_conversion.strftime('%Y-%m-%d')
            })
        except: continue
    return pd.DataFrame(results)

# --- 4. THE INTERFACE (JSX MIRROR) ---
st.title("🛡️ Wealth Architect AI v4.0")
st.caption("Wall Street Portfolio Intelligence & Tax Optimization Engine")

if st.button("📊 SYNC PORTFOLIO & CALCULATE TAX DATA"):
    st.session_state.df = analyze_portfolio()

if 'df' in st.session_state:
    df = st.session_state.df
    tabs = st.tabs(["Dashboard", "Full Inventory", "Diversified Buy Plan", "Tax Strategy", "History"])

    # --- TAB 1: DASHBOARD ---
    with tabs[0]:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Net Worth", f"${df['Value'].sum():,.2f}")
        c2.metric("Total P/L", f"${df['P/L'].sum():,.2f}")
        c3.metric("Next Deposit", "$900.00", "Apr 3rd")
        c4.metric("Avg RSI", f"{df['RSI'].mean():.1f}")
        
        st.plotly_chart(px.pie(df, values='Value', names='Cat', hole=0.5, template="plotly_dark", title="Portfolio Mix"), use_container_width=True)

    # --- TAB 2: INVENTORY (GROUPED) ---
    with tabs[1]:
        for category in df['Cat'].unique():
            st.markdown(f"<div class='group-header'>{category.upper()}</div>", unsafe_allow_html=True)
            sub = df[df['Cat'] == category][['Ticker', 'Shares', 'Price', 'Value', 'P/L', 'RSI', 'Tax']]
            st.dataframe(sub.style.map(lambda x: "color: #4ade80" if (isinstance(x, (int, float)) and x > 0) else ("color: #f87171" if (isinstance(x, (int, float)) and x < 0) else ""), subset=['P/L']), use_container_width=True, hide_index=True)

    # --- TAB 3: DIVERSIFIED $900 PLAN ---
    with tabs[2]:
        st.subheader("🎯 Smart Allocation for Friday, April 3rd")
        st.write("We are splitting the $900 across your categories to maintain diversification while buying 'the dip'.")
        
        # Diversification Logic: Split across top value (low RSI) in each category
        best_etf = df[df['Cat'] == 'ETF'].sort_values(by='RSI').iloc[0]['Ticker']
        best_core = df[df['Cat'] == 'Core'].sort_values(by='RSI').iloc[0]['Ticker']
        best_growth = df[df['Cat'] == 'Growth'].sort_values(by='RSI').iloc[0]['Ticker']
        
        ac1, ac2, ac3 = st.columns(3)
        ac1.metric(f"40% -> {best_etf}", "$360.00", "ETF Stability")
        ac2.metric(f"30% -> {best_core}", "$270.00", "Core Growth")
        ac3.metric(f"30% -> {best_growth}", "$270.00", "High Alpha")
        
        st.info(f"**Strategy Analysis:** By diversifying into **{best_etf}**, **{best_core}**, and **{best_growth}**, we capture broad market growth while averaging down on your most oversold assets.")

    # --- TAB 4: TAX PLAYBOOK ---
    with tabs[3]:
        st.subheader("📅 Capital Gains Playbook")
        st.write("The following assets are approaching **Long Term (15% Tax)** status. Avoid selling until the conversion date.")
        
        # Sort by conversion date
        tax_logic = df[df['Tax'] == 'Short'][['Ticker', 'LT_Date', 'P/L']].sort_values(by='LT_Date')
        st.table(tax_logic)
        
        st.divider()
        st.write("### ✂️ Trim/Sell Logic (Based on RSI > 70)")
        overbought = df[df['RSI'] > 70]
        if not overbought.empty:
            for _, r in overbought.iterrows():
                st.warning(f"**{r['Ticker']}** is Overbought (RSI: {r['RSI']}). Consider trimming after {r['LT_Date']} for tax efficiency.")
        else:
            st.write("No assets are currently overbought. Maintenance mode active.")

    # --- TAB 5: HISTORY ---
    with tabs[4]:
        st.subheader("Recommendation Log")
        hist = pd.DataFrame([
            {"Date": "2026-04-03", "Action": f"Split: {best_etf}/{best_core}/{best_growth}", "Total": "$900"},
            {"Date": "2026-03-20", "Action": "Buy: NVDA", "Total": "$900"},
            {"Date": "2026-03-06", "Action": "Buy: VOO/QQQ Split", "Total": "$900"},
        ])
        st.table(hist)
        
        st.divider()
        st.file_uploader("Upload New CSV for Master Update", type="csv")

else:
    st.info("👋 Welcome, Architect. Click the **Sync** button above to run the Wall Street Analysis engine.")
