"""
Portfolio War Room v5.0 - Enhanced Edition
Mobile-first investment tracker with live pricing and intelligent recommendations
"""

import streamlit as st
import pandas as pd
import yfinance as yf
import requests
from datetime import datetime, timedelta
import json
import time
from typing import Dict, List, Tuple, Optional
import io
import base64

# ════════════════════════════════════════════════════════════════════════════
# CONFIGURATION & STYLING
# ════════════════════════════════════════════════════════════════════════════

st.set_page_config(
    page_title="Portfolio War Room",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# Enhanced CSS for mobile and desktop responsiveness
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700;800&family=Instrument+Serif:ital@0;1&display=swap');
    
    /* Base theme */
    :root {
        --bg-primary: #0a0e14;
        --bg-secondary: #131820;
        --bg-card: #1a1f2e;
        --border: #2a3344;
        --accent: #00f0aa;
        --accent-dim: rgba(0, 240, 170, 0.15);
        --gold: #f0c040;
        --gold-dim: rgba(240, 192, 64, 0.15);
        --red: #ff4060;
        --red-dim: rgba(255, 64, 96, 0.15);
        --blue: #4090ff;
        --blue-dim: rgba(64, 144, 255, 0.15);
        --text: #e8ecf8;
        --text-dim: #6a7590;
    }
    
    /* Remove default Streamlit padding */
    .block-container {
        padding-top: 2rem;
        padding-bottom: 0rem;
        max-width: 100%;
    }
    
    /* Hide Streamlit branding */
    #MainMenu, footer, header {visibility: hidden;}
    
    /* Typography */
    * {
        font-family: 'JetBrains Mono', monospace;
    }
    
    h1, h2, h3 {
        font-family: 'Instrument Serif', serif;
        color: var(--accent);
    }
    
    /* Mobile-first responsive grid */
    .card-grid {
        display: grid;
        grid-template-columns: 1fr;
        gap: 1rem;
        margin-bottom: 1.5rem;
    }
    
    @media (min-width: 768px) {
        .card-grid {
            grid-template-columns: repeat(2, 1fr);
        }
    }
    
    @media (min-width: 1200px) {
        .card-grid {
            grid-template-columns: repeat(4, 1fr);
        }
    }
    
    /* Clickable cards */
    .metric-card {
        background: var(--bg-card);
        border: 1px solid var(--border);
        border-radius: 12px;
        padding: 1.25rem;
        cursor: pointer;
        transition: all 0.2s ease;
        position: relative;
        overflow: hidden;
    }
    
    .metric-card:hover {
        border-color: var(--accent);
        transform: translateY(-2px);
        box-shadow: 0 8px 24px rgba(0, 240, 170, 0.12);
    }
    
    .metric-card::before {
        content: '';
        position: absolute;
        top: 0;
        left: 0;
        right: 0;
        height: 3px;
        background: var(--accent);
        transform: scaleX(0);
        transition: transform 0.2s ease;
    }
    
    .metric-card:hover::before {
        transform: scaleX(1);
    }
    
    .metric-label {
        font-size: 0.7rem;
        letter-spacing: 0.1em;
        color: var(--text-dim);
        font-weight: 800;
        text-transform: uppercase;
        margin-bottom: 0.5rem;
    }
    
    .metric-value {
        font-size: 1.8rem;
        font-weight: 800;
        color: var(--text);
        margin-bottom: 0.25rem;
    }
    
    .metric-subtext {
        font-size: 0.75rem;
        color: var(--text-dim);
    }
    
    /* Holdings table enhancements */
    .holdings-row-loss {
        background: var(--red-dim);
        border-left: 3px solid var(--red);
    }
    
    .holdings-row-gain {
        background: var(--accent-dim);
        border-left: 3px solid var(--accent);
    }
    
    /* Status badges */
    .status-badge {
        display: inline-block;
        padding: 0.25rem 0.75rem;
        border-radius: 6px;
        font-size: 0.7rem;
        font-weight: 800;
        letter-spacing: 0.05em;
    }
    
    .status-buy {
        background: var(--accent-dim);
        color: var(--accent);
        border: 1px solid var(--accent);
    }
    
    .status-sell {
        background: var(--red-dim);
        color: var(--red);
        border: 1px solid var(--red);
    }
    
    .status-trim {
        background: var(--gold-dim);
        color: var(--gold);
        border: 1px solid var(--gold);
    }
    
    .status-hold {
        background: var(--blue-dim);
        color: var(--blue);
        border: 1px solid var(--blue);
    }
    
    /* Upload button fix */
    .stButton > button {
        width: 100%;
        background: var(--accent-dim);
        border: 1px solid var(--accent);
        color: var(--accent);
        border-radius: 8px;
        padding: 0.75rem 1rem;
        font-weight: 800;
        letter-spacing: 0.05em;
        transition: all 0.2s ease;
    }
    
    .stButton > button:hover {
        background: var(--accent);
        color: var(--bg-primary);
        transform: translateY(-1px);
    }
    
    /* File uploader styling */
    [data-testid="stFileUploader"] {
        background: var(--bg-card);
        border: 1px dashed var(--border);
        border-radius: 12px;
        padding: 1.5rem;
    }
    
    [data-testid="stFileUploader"]:hover {
        border-color: var(--accent);
    }
    
    /* Mobile optimization */
    @media (max-width: 768px) {
        .metric-value {
            font-size: 1.5rem;
        }
        
        .block-container {
            padding-left: 1rem;
            padding-right: 1rem;
        }
    }
    
    /* Tab styling */
    .stTabs [data-baseweb="tab-list"] {
        gap: 0.5rem;
        background: var(--bg-secondary);
        border-radius: 12px;
        padding: 0.5rem;
    }
    
    .stTabs [data-baseweb="tab"] {
        border-radius: 8px;
        color: var(--text-dim);
        font-weight: 700;
        font-size: 0.8rem;
        letter-spacing: 0.05em;
    }
    
    .stTabs [aria-selected="true"] {
        background: var(--accent-dim);
        color: var(--accent);
    }
    
    /* Expander styling */
    .streamlit-expanderHeader {
        background: var(--bg-card);
        border: 1px solid var(--border);
        border-radius: 8px;
        color: var(--text);
        font-weight: 700;
    }
    
    .streamlit-expanderHeader:hover {
        border-color: var(--accent);
    }
</style>
""", unsafe_allow_html=True)

# ════════════════════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ════════════════════════════════════════════════════════════════════════════

# Initialize session state for portfolio data
if 'portfolio' not in st.session_state:
    st.session_state.portfolio = {
        "BTC": {"shares": 0.03433, "cost": 66997, "target": 110000, "bear": 45000, "bull": 175000, "category": "Crypto", "name": "Bitcoin", "lt_ready": True},
        "XRP": {"shares": 1.066, "cost": 1.886, "target": 2.80, "bear": 0.60, "bull": 5.00, "category": "Crypto", "name": "XRP / Ripple", "lt_ready": True},
        "NVDA": {"shares": 35.5042, "cost": 103, "target": 175, "bear": 90, "bull": 250, "category": "Core", "name": "NVIDIA", "lt_ready": True},
        "META": {"shares": 2.8, "cost": 612, "target": 720, "bear": 400, "bull": 900, "category": "Core", "name": "Meta Platforms", "lt_ready": False, "lt_date": "Sep 23 2026"},
        "GOOGL": {"shares": 4.0, "cost": 307, "target": 210, "bear": 140, "bull": 280, "category": "Core", "name": "Alphabet", "lt_ready": False, "lt_date": "Dec 15 2026"},
        "AAPL": {"shares": 16.1, "cost": 172, "target": 240, "bear": 170, "bull": 290, "category": "Core", "name": "Apple", "lt_ready": True},
        "MSFT": {"shares": 0.012, "cost": 402, "target": 480, "bear": 330, "bull": 560, "category": "Core", "name": "Microsoft", "lt_ready": True},
        "NFLX": {"shares": 21.3325, "cost": 86, "target": 1100, "bear": 700, "bull": 1400, "category": "Core", "name": "Netflix", "lt_ready": True},
        "COST": {"shares": 1.85, "cost": 925, "target": 1050, "bear": 820, "bull": 1300, "category": "Core", "name": "Costco", "lt_ready": True},
        "TSM": {"shares": 1.98, "cost": 290, "target": 230, "bear": 130, "bull": 320, "category": "Core", "name": "Taiwan Semi", "lt_ready": False, "lt_date": "Nov 6 2026"},
        "CRM": {"shares": 2.74, "cost": 254, "target": 320, "bear": 180, "bull": 400, "category": "Core", "name": "Salesforce", "lt_ready": True},
        "QCOM": {"shares": 2.37, "cost": 165, "target": 175, "bear": 100, "bull": 230, "category": "Core", "name": "Qualcomm", "lt_ready": True},
        "WMT": {"shares": 13.6, "cost": 82, "target": 105, "bear": 75, "bull": 130, "category": "Core", "name": "Walmart", "lt_ready": True},
        "BRK-B": {"shares": 4.5154, "cost": 502, "target": 530, "bear": 400, "bull": 620, "category": "Core", "name": "Berkshire B", "lt_ready": True},
        "AMD": {"shares": 0, "cost": 164, "target": 140, "bear": 80, "bull": 220, "category": "Core", "name": "AMD", "lt_ready": True},
        "RDDT": {"shares": 1, "cost": 34, "target": 130, "bear": 60, "bull": 200, "category": "Other", "name": "Reddit", "lt_ready": True},
        "ALK": {"shares": 0.6, "cost": 41, "target": 55, "bear": 28, "bull": 75, "category": "Other", "name": "Alaska Air", "lt_ready": True},
        "SNOW": {"shares": 3.6, "cost": 152, "target": 190, "bear": 90, "bull": 250, "category": "Other", "name": "Snowflake", "lt_ready": True},
        "CAVA": {"shares": 0, "cost": 91.66, "target": 120, "bear": 60, "bull": 180, "category": "Other", "name": "CAVA Group", "lt_ready": True},
        "RIVN": {"shares": 0, "cost": 14.62, "target": 25, "bear": 5, "bull": 45, "category": "Other", "name": "Rivian", "lt_ready": True},
        "BMWYY": {"shares": 1.0, "cost": 39.72, "target": 50, "bear": 25, "bull": 65, "category": "Other", "name": "BMW", "lt_ready": True},
        "BLSH": {"shares": 10.0, "cost": 37, "target": 60, "bear": 15, "bull": 90, "category": "IPO", "name": "Bullish", "lt_ready": False, "lt_date": "Aug 14 2026"},
        "KLAR": {"shares": 11.0, "cost": 40, "target": 65, "bear": 25, "bull": 100, "category": "IPO", "name": "Klarna", "lt_ready": False, "lt_date": "Sep 11 2026"},
        "STUB": {"shares": 23.3561, "cost": 25.62, "target": 38, "bear": 12, "bull": 60, "category": "IPO", "name": "StubHub", "lt_ready": False, "lt_date": "Sep 18 2026"},
        "VOO": {"shares": 7.601, "cost": 479, "target": 650, "bear": 420, "bull": 750, "category": "ETF✓", "name": "Vanguard S&P 500", "lt_ready": True},
        "QQQ": {"shares": 2.37, "cost": 503, "target": 580, "bear": 380, "bull": 700, "category": "ETF✓", "name": "Invesco Nasdaq-100", "lt_ready": True},
        "VTI": {"shares": 1.96, "cost": 274, "target": 370, "bear": 240, "bull": 430, "category": "ETF✓", "name": "Vanguard Total Market", "lt_ready": True},
        "VGT": {"shares": 1.46, "cost": 548, "target": 760, "bear": 480, "bull": 920, "category": "ETF✓", "name": "Vanguard IT ETF", "lt_ready": True},
        "VHT": {"shares": 1.87, "cost": 271, "target": 300, "bear": 200, "bull": 370, "category": "ETF✓", "name": "Vanguard Health Care", "lt_ready": True},
        "VIS": {"shares": 1.97, "cost": 258, "target": 340, "bear": 210, "bull": 420, "category": "ETF✓", "name": "Vanguard Industrials", "lt_ready": True},
        "VYM": {"shares": 20.4, "cost": 132, "target": 160, "bear": 110, "bull": 190, "category": "ETF✓", "name": "Vanguard Hi-Div", "lt_ready": True},
        "SCHD": {"shares": 19.6, "cost": 27, "target": 32, "bear": 20, "bull": 42, "category": "ETF✓", "name": "Schwab Dividend", "lt_ready": True},
        "VXUS": {"shares": 22.7, "cost": 78, "target": 85, "bear": 55, "bull": 110, "category": "ETF✓", "name": "Vanguard Intl", "lt_ready": True},
        "GLD": {"shares": 6.6408, "cost": 287, "target": 320, "bear": 220, "bull": 420, "category": "ETF✓", "name": "SPDR Gold", "lt_ready": False, "lt_date": "Apr 4 2026"},
        "XLE": {"shares": 21.3, "cost": 74, "target": 72, "bear": 44, "bull": 95, "category": "ETF✓", "name": "Energy SPDR", "lt_ready": True},
        "SPY": {"shares": 0.51, "cost": 595, "target": None, "bear": None, "bull": None, "category": "ETF🔴", "name": "SPDR S&P 500 → VOO", "lt_ready": False, "lt_date": "May 20 2026"},
        "VTV": {"shares": 0.1658, "cost": 163, "target": None, "bear": None, "bull": None, "category": "ETF🔴", "name": "Vanguard Value → VOO", "lt_ready": True},
        "VUG": {"shares": 0.46, "cost": 441, "target": None, "bear": None, "bull": None, "category": "ETF🔴", "name": "Vanguard Growth → QQQ", "lt_ready": False, "lt_date": "Jul 15 2026"},
        "VEA": {"shares": 0.2523, "cost": 50, "target": None, "bear": None, "bull": None, "category": "ETF🔴", "name": "Dev Markets → VXUS", "lt_ready": True},
        "VWO": {"shares": 0.1446, "cost": 41, "target": None, "bear": None, "bull": None, "category": "ETF🔴", "name": "Emg Markets → VXUS", "lt_ready": True},
        "BND": {"shares": 0.578, "cost": 72, "target": None, "bear": None, "bull": None, "category": "ETF🔴", "name": "Total Bond → VYM", "lt_ready": True},
    }

if 'cash_balance' not in st.session_state:
    st.session_state.cash_balance = 1042.17

if 'prices' not in st.session_state:
    st.session_state.prices = {}

if 'last_update' not in st.session_state:
    st.session_state.last_update = None

if 'recommendation_history' not in st.session_state:
    st.session_state.recommendation_history = []

# ════════════════════════════════════════════════════════════════════════════
# PRICE FETCHING
# ════════════════════════════════════════════════════════════════════════════

def fetch_live_prices(tickers: List[str]) -> Dict[str, float]:
    """Fetch live prices for stocks/ETFs (yfinance) and crypto (CoinGecko)"""
    prices = {}
    
    # Separate crypto and stocks
    crypto_map = {"BTC": "bitcoin", "XRP": "ripple"}
    crypto_tickers = [t for t in tickers if t in crypto_map]
    stock_tickers = [t for t in tickers if t not in crypto_map and st.session_state.portfolio[t]["shares"] > 0]
    
    # Fetch crypto prices
    if crypto_tickers:
        try:
            cg_ids = [crypto_map[t] for t in crypto_tickers]
            response = requests.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": ",".join(cg_ids), "vs_currencies": "usd"},
                timeout=10
            )
            if response.status_code == 200:
                data = response.json()
                for ticker in crypto_tickers:
                    cg_id = crypto_map[ticker]
                    if cg_id in data and "usd" in data[cg_id]:
                        prices[ticker] = data[cg_id]["usd"]
        except Exception as e:
            st.error(f"Crypto price fetch error: {e}")
    
    # Fetch stock prices in batch
    if stock_tickers:
        try:
            data = yf.download(stock_tickers, period="1d", interval="1d", progress=False, threads=True)
            if len(stock_tickers) == 1:
                prices[stock_tickers[0]] = data['Close'].iloc[-1] if not data.empty else None
            else:
                for ticker in stock_tickers:
                    if ticker in data['Close'].columns:
                        prices[ticker] = data['Close'][ticker].iloc[-1]
        except Exception as e:
            st.error(f"Stock price fetch error: {e}")
    
    return prices

# ════════════════════════════════════════════════════════════════════════════
# RECOMMENDATION ENGINE
# ════════════════════════════════════════════════════════════════════════════

def generate_recommendation(ticker: str, data: dict, price: float) -> dict:
    """Generate buy/sell/hold recommendation with tax awareness"""
    
    if data["shares"] == 0:
        return {"action": "SOLD", "detail": "Position closed", "color": "#6a7590", "urgency": 0}
    
    # SELL list override
    if data["category"] == "ETF🔴":
        if data["lt_ready"]:
            return {"action": "SELL NOW", "detail": f"LT eligible - consolidate position", "color": "#ff4060", "urgency": 5}
        else:
            return {"action": "WAIT → SELL", "detail": f"LT on {data.get('lt_date', 'TBD')}", "color": "#ff9030", "urgency": 3}
    
    if not price or not data.get("target"):
        return {"action": "HOLD", "detail": "Awaiting price data", "color": "#6a7590", "urgency": 0}
    
    # Calculate metrics
    gain_pct = ((price - data["cost"]) / data["cost"]) * 100
    upside_pct = ((data["target"] - price) / price) * 100 if data["target"] else 0
    downside_pct = ((price - data["bear"]) / price) * 100 if data.get("bear") else 100
    
    is_crypto = data["category"] == "Crypto"
    is_income_etf = ticker in ["VYM", "SCHD"]
    is_core_etf = ticker in ["VOO", "QQQ", "VTI"]
    is_lt = data["lt_ready"]
    
    # Income ETFs - never sell
    if is_income_etf:
        return {"action": "HOLD ♾", "detail": "Income engine — DRIP forever", "color": "#9070ff", "urgency": 0}
    
    # Core ETFs - always DCA
    if is_core_etf:
        allocation = {"VOO": "$200", "QQQ": "$150", "VTI": "$100"}.get(ticker, "$—")
        return {"action": "DCA ♾", "detail": f"Add {allocation} biweekly", "color": "#00f0aa", "urgency": 0}
    
    # Crypto special handling
    if is_crypto:
        if upside_pct > 25:
            return {"action": "ACCUMULATE", "detail": f"{upside_pct:.0f}% to target — keep stacking", "color": "#00f0aa", "urgency": 4}
        elif upside_pct < -20:
            return {"action": "TRIM 20%", "detail": f"{abs(upside_pct):.0f}% above target — take profits", "color": "#f0c040", "urgency": 3}
    
    # Bear case proximity (non-crypto)
    if not is_crypto and data.get("bear") and price < data["bear"] * 1.10:
        return {"action": "STOP LOSS", "detail": f"Within 10% of bear case (${data['bear']:.0f})", "color": "#ff4060", "urgency": 5}
    
    # Strong buy zone
    if upside_pct > 60 and gain_pct < -15:
        return {"action": "STRONG BUY", "detail": f"{upside_pct:.0f}% to target, deep value", "color": "#00f0aa", "urgency": 5}
    
    # Accumulate zone
    if upside_pct > 40:
        return {"action": "ACCUMULATE", "detail": f"{upside_pct:.0f}% upside to analyst target", "color": "#00f0aa", "urgency": 4}
    
    # Buy dip
    if upside_pct > 20 and gain_pct < -15:
        return {"action": "BUY DIP", "detail": f"Down {abs(gain_pct):.0f}%, {upside_pct:.0f}% to target", "color": "#20d080", "urgency": 4}
    
    # At target - trim if LT
    if -10 < upside_pct < 5:
        if not is_lt:
            return {"action": "HOLD (ST)", "detail": f"Near target — wait for LT: {data.get('lt_date', 'TBD')}", "color": "#ff9030", "urgency": 2}
        return {"action": "TRIM 20%", "detail": "At analyst target — partial profits (LT)", "color": "#f0c040", "urgency": 3}
    
    # Above target
    if upside_pct <= -10:
        if not is_lt:
            return {"action": "HOLD (ST)", "detail": f"Above target — wait for LT to avoid 37% tax", "color": "#ff9030", "urgency": 2}
        return {"action": "TRIM 25%", "detail": f"{abs(upside_pct):.0f}% above target — lock gains (LT)", "color": "#f0c040", "urgency": 3}
    
    # Default hold
    return {"action": "HOLD", "detail": f"{upside_pct:.0f}% to target", "color": "#4090ff", "urgency": 1}

def generate_cash_deployment_recs(cash: float, prices: dict) -> List[dict]:
    """Generate recommendations for deploying available cash"""
    recommendations = []
    
    # Base $900 allocation template
    base_allocation = [
        {"ticker": "NVDA", "pct": 0.28, "rationale": "AI supercycle core conviction"},
        {"ticker": "VOO", "pct": 0.22, "rationale": "S&P 500 DCA forever"},
        {"ticker": "VYM", "pct": 0.17, "rationale": "Dividend compound engine"},
        {"ticker": "QQQ", "pct": 0.17, "rationale": "Nasdaq-100 tech exposure"},
    ]
    
    # Dynamic rotation pick based on current opportunities
    portfolio = st.session_state.portfolio
    rotation_candidates = ["META", "GOOGL", "AAPL", "MSFT", "COST", "TSM", "CRM", "NFLX", "AMD"]
    
    # Find best rotation pick (highest upside with shares > 0)
    best_pick = None
    best_upside = 0
    
    for ticker in rotation_candidates:
        data = portfolio.get(ticker, {})
        if data.get("shares", 0) > 0 and ticker in prices and data.get("target"):
            price = prices[ticker]
            upside = ((data["target"] - price) / price) * 100
            if upside > best_upside:
                best_upside = upside
                best_pick = ticker
    
    if best_pick:
        base_allocation.append({
            "ticker": best_pick,
            "pct": 0.16,
            "rationale": f"{best_upside:.0f}% upside — rotation opportunity"
        })
    
    # Scale to available cash
    for alloc in base_allocation:
        ticker = alloc["ticker"]
        amount = cash * alloc["pct"]
        
        if ticker in prices:
            price = prices[ticker]
            shares = amount / price
            recommendations.append({
                "ticker": ticker,
                "amount": amount,
                "shares": shares,
                "price": price,
                "rationale": alloc["rationale"]
            })
    
    return recommendations

# ════════════════════════════════════════════════════════════════════════════
# CSV/PDF PARSING
# ════════════════════════════════════════════════════════════════════════════

def parse_robinhood_csv(file) -> Tuple[pd.DataFrame, dict]:
    """Parse Robinhood CSV and return transactions + updated positions"""
    df = pd.read_csv(file)
    
    # Parse transactions
    transactions = []
    for _, row in df.iterrows():
        trans = {
            "date": row.get("Activity Date"),
            "ticker": row.get("Instrument"),
            "type": row.get("Trans Code"),
            "quantity": float(row.get("Quantity", 0) or 0),
            "price": float(str(row.get("Price", "0")).replace("$", "").replace(",", "") or 0),
            "amount": float(str(row.get("Amount", "0")).replace("$", "").replace("(", "").replace(")", "").replace(",", "") or 0),
        }
        transactions.append(trans)
    
    # Update positions based on Buy/Sell
    updates = {}
    for trans in transactions:
        ticker = trans["ticker"]
        if ticker and ticker in st.session_state.portfolio:
            if trans["type"] == "Buy":
                current = st.session_state.portfolio[ticker]["shares"]
                updates[ticker] = current + trans["quantity"]
            elif trans["type"] == "Sell":
                current = st.session_state.portfolio[ticker]["shares"]
                updates[ticker] = max(0, current - trans["quantity"])
    
    return pd.DataFrame(transactions), updates

def parse_crypto_pdf(file) -> dict:
    """Parse crypto statement PDF (placeholder - requires PyPDF2)"""
    # TODO: Implement PDF parsing for crypto statements
    st.warning("PDF parsing for crypto statements will be implemented in next update")
    return {}

# ════════════════════════════════════════════════════════════════════════════
# MAIN APP
# ════════════════════════════════════════════════════════════════════════════

def main():
    # Header
    st.markdown("""
        <h1 style='margin-bottom: 0; font-size: 2.5rem;'>⚡ Portfolio War Room</h1>
        <p style='color: var(--text-dim); margin-top: 0.5rem; font-size: 0.9rem;'>
            Real-time portfolio intelligence · Tax-optimized recommendations · $900 biweekly deploy
        </p>
    """, unsafe_allow_html=True)
    
    # Refresh prices button (prominent)
    col1, col2, col3 = st.columns([2, 1, 1])
    with col1:
        if st.button("🔄 REFRESH PRICES", use_container_width=True):
            with st.spinner("Fetching live prices..."):
                active_tickers = [t for t, d in st.session_state.portfolio.items() if d["shares"] > 0]
                st.session_state.prices = fetch_live_prices(active_tickers)
                st.session_state.last_update = datetime.now().strftime("%b %d, %Y %I:%M %p")
                st.success(f"✓ Updated {len(st.session_state.prices)} positions")
                st.rerun()
    
    with col2:
        st.metric("Cash", f"${st.session_state.cash_balance:,.2f}")
    
    with col3:
        if st.session_state.last_update:
            st.caption(f"Updated: {st.session_state.last_update}")
    
    st.markdown("---")
    
    # Tab navigation
    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
        "📊 Overview",
        "💼 Holdings",
        "📥 Import Data",
        "💰 Deploy $900",
        "📈 Performance",
        "⚙️ Settings"
    ])
    
    # ════════════════════════════════════════════════════════════════════════════
    # TAB 1: OVERVIEW
    # ════════════════════════════════════════════════════════════════════════════
    
    with tab1:
        # Calculate metrics
        portfolio = st.session_state.portfolio
        prices = st.session_state.prices
        
        total_equity = st.session_state.cash_balance
        total_cost = 0
        position_count = 0
        
        sell_positions = []
        trim_positions = []
        buy_positions = []
        
        for ticker, data in portfolio.items():
            if data["shares"] > 0:
                position_count += 1
                cost_basis = data["shares"] * data["cost"]
                total_cost += cost_basis
                
                if ticker in prices:
                    equity = data["shares"] * prices[ticker]
                    total_equity += equity
                    
                    # Generate recommendation
                    rec = generate_recommendation(ticker, data, prices[ticker])
                    
                    if "SELL" in rec["action"]:
                        sell_positions.append({"ticker": ticker, "rec": rec, "equity": equity})
                    elif "TRIM" in rec["action"]:
                        trim_positions.append({"ticker": ticker, "rec": rec, "equity": equity})
                    elif rec["action"] in ["STRONG BUY", "BUY DIP", "ACCUMULATE"]:
                        buy_positions.append({"ticker": ticker, "rec": rec, "upside": ((data["target"] - prices[ticker]) / prices[ticker] * 100) if data.get("target") else 0})
        
        total_gain = total_equity - total_cost
        total_gain_pct = (total_gain / total_cost * 100) if total_cost > 0 else 0
        
        # Clickable metric cards
        st.markdown('<div class="card-grid">', unsafe_allow_html=True)
        
        # Card 1: Total Equity (expandable)
        with st.expander(f"💰 **TOTAL EQUITY** ${total_equity:,.2f}", expanded=False):
            st.markdown(f"""
            **Cost Basis:** ${total_cost:,.2f}  
            **Total Gain:** ${total_gain:,.2f} ({total_gain_pct:+.1f}%)  
            **Active Positions:** {position_count}  
            **Cash Available:** ${st.session_state.cash_balance:,.2f}
            """)
        
        # Card 2: Sell Alerts (expandable)
        with st.expander(f"🔴 **SELL NOW** ({len(sell_positions)} positions)", expanded=False):
            if sell_positions:
                for pos in sorted(sell_positions, key=lambda x: x["equity"], reverse=True):
                    st.markdown(f"""
                    **{pos['ticker']}** — ${pos['equity']:,.2f}  
                    _{pos['rec']['detail']}_
                    """)
                st.info("💡 Sell these positions and reinvest proceeds per tax playbook")
            else:
                st.success("No immediate sell actions needed")
        
        # Card 3: Trim Alerts (expandable)
        with st.expander(f"⚠️ **TRIM POSITIONS** ({len(trim_positions)} positions)", expanded=False):
            if trim_positions:
                for pos in sorted(trim_positions, key=lambda x: x["equity"], reverse=True):
                    st.markdown(f"""
                    **{pos['ticker']}** — ${pos['equity']:,.2f}  
                    _{pos['rec']['detail']}_
                    """)
                st.info("💡 Lock in partial profits while maintaining long-term position")
            else:
                st.success("No trim actions needed")
        
        # Card 4: Buy Opportunities (expandable)
        with st.expander(f"📈 **BUY OPPORTUNITIES** ({len(buy_positions)} positions)", expanded=False):
            if buy_positions:
                for pos in sorted(buy_positions, key=lambda x: x["upside"], reverse=True)[:5]:
                    st.markdown(f"""
                    **{pos['ticker']}** — {pos['rec']['action']}  
                    _{pos['rec']['detail']}_  
                    _Upside: {pos['upside']:.0f}%_
                    """)
                st.info("💡 Best opportunities based on analyst targets and current valuations")
            else:
                st.info("No strong buy signals at current prices")
        
        st.markdown('</div>', unsafe_allow_html=True)
        
        # Cash deployment recommendations
        st.markdown("### 💵 Cash Deployment Plan")
        if st.session_state.cash_balance > 100:
            cash_recs = generate_cash_deployment_recs(st.session_state.cash_balance, prices)
            
            if cash_recs:
                rec_df = pd.DataFrame(cash_recs)
                rec_df["amount"] = rec_df["amount"].apply(lambda x: f"${x:,.2f}")
                rec_df["shares"] = rec_df["shares"].apply(lambda x: f"{x:.4f}")
                rec_df["price"] = rec_df["price"].apply(lambda x: f"${x:,.2f}")
                
                st.dataframe(
                    rec_df[["ticker", "amount", "shares", "price", "rationale"]],
                    use_container_width=True,
                    hide_index=True
                )
                
                total_deployed = sum([r["amount"] for r in cash_recs])
                st.info(f"💡 Total deployment: ${total_deployed:,.2f} of ${st.session_state.cash_balance:,.2f} available")
        else:
            st.info("🎯 Awaiting next biweekly deposit ($900 on Friday)")
        
        # Action Calendar
        st.markdown("### 📅 Upcoming Actions")
        actions = [
            {"date": "Apr 3, 2026", "action": "💰 First $900 deposit", "priority": "high"},
            {"date": "Apr 4, 2026", "action": "🟡 GLD → LT eligible, trim 25% at $320 target", "priority": "medium"},
            {"date": "May 20, 2026", "action": "🔴 SPY turns LT → sell all, reinvest to VOO", "priority": "high"},
            {"date": "Jul 15, 2026", "action": "🔴 VUG turns LT → sell all, reinvest to QQQ", "priority": "high"},
        ]
        
        for action in actions:
            color = "#ff4060" if action["priority"] == "high" else "#f0c040"
            st.markdown(f"""
            <div style='background: {color}18; border-left: 3px solid {color}; padding: 0.75rem; margin-bottom: 0.5rem; border-radius: 4px;'>
                <strong>{action['date']}</strong> — {action['action']}
            </div>
            """, unsafe_allow_html=True)
    
    # ════════════════════════════════════════════════════════════════════════════
    # TAB 2: HOLDINGS
    # ════════════════════════════════════════════════════════════════════════════
    
    with tab2:
        st.markdown("### 📊 All Holdings")
        
        # Build holdings table
        holdings_data = []
        for ticker, data in portfolio.items():
            if data["shares"] == 0:
                continue
                
            price = prices.get(ticker, 0)
            cost_basis = data["shares"] * data["cost"]
            equity = data["shares"] * price if price else 0
            gain = equity - cost_basis
            gain_pct = (gain / cost_basis * 100) if cost_basis > 0 else 0
            
            rec = generate_recommendation(ticker, data, price) if price else {"action": "—", "detail": "No price", "color": "#6a7590"}
            
            holdings_data.append({
                "Ticker": ticker,
                "Name": data["name"],
                "Category": data["category"],
                "Shares": f"{data['shares']:.4f}",
                "Avg Cost": f"${data['cost']:.2f}",
                "Price": f"${price:.2f}" if price else "—",
                "Equity": f"${equity:,.2f}",
                "Gain": f"${gain:,.2f}",
                "Gain %": f"{gain_pct:+.1f}%",
                "Action": rec["action"],
                "Detail": rec["detail"],
                "gain_raw": gain,
                "rec_action": rec["action"]
            })
        
        if holdings_data:
            df = pd.DataFrame(holdings_data)
            
            # Filter options
            col1, col2, col3 = st.columns(3)
            with col1:
                category_filter = st.selectbox("Category", ["All"] + sorted(df["Category"].unique().tolist()))
            with col2:
                action_filter = st.selectbox("Action", ["All"] + sorted(df["Action"].unique().tolist()))
            with col3:
                sort_by = st.selectbox("Sort by", ["Equity", "Gain %", "Ticker"])
            
            # Apply filters
            filtered_df = df.copy()
            if category_filter != "All":
                filtered_df = filtered_df[filtered_df["Category"] == category_filter]
            if action_filter != "All":
                filtered_df = filtered_df[filtered_df["Action"] == action_filter]
            
            # Sort
            if sort_by == "Equity":
                filtered_df = filtered_df.sort_values("gain_raw", ascending=False)
            elif sort_by == "Gain %":
                filtered_df = filtered_df.sort_values("Gain %", ascending=False)
            else:
                filtered_df = filtered_df.sort_values("Ticker")
            
            # Display with color coding
            display_df = filtered_df[["Ticker", "Name", "Category", "Shares", "Price", "Equity", "Gain", "Gain %", "Action", "Detail"]]
            
            # Color code rows
            def highlight_row(row):
                if row["gain_raw"] < 0:
                    return ['background-color: rgba(255, 64, 96, 0.15); border-left: 3px solid #ff4060'] * len(row)
                else:
                    return ['background-color: rgba(0, 240, 170, 0.08); border-left: 3px solid #00f0aa'] * len(row)
            
            st.dataframe(
                display_df,
                use_container_width=True,
                hide_index=True,
                height=600
            )
            
            # Summary stats
            col1, col2, col3 = st.columns(3)
            with col1:
                total_equity_shown = filtered_df["Equity"].apply(lambda x: float(x.replace("$", "").replace(",", ""))).sum()
                st.metric("Total Equity (Filtered)", f"${total_equity_shown:,.2f}")
            with col2:
                avg_gain_pct = filtered_df["Gain %"].apply(lambda x: float(x.replace("%", "").replace("+", ""))).mean()
                st.metric("Avg Gain %", f"{avg_gain_pct:+.1f}%")
            with col3:
                st.metric("Positions Shown", len(filtered_df))
        else:
            st.info("No active positions")
    
    # ════════════════════════════════════════════════════════════════════════════
    # TAB 3: IMPORT DATA
    # ════════════════════════════════════════════════════════════════════════════
    
    with tab3:
        st.markdown("### 📥 Import Robinhood Data")
        
        col1, col2 = st.columns(2)
        
        with col1:
            st.markdown("#### CSV Upload")
            csv_file = st.file_uploader(
                "Upload Robinhood account activity CSV",
                type=["csv"],
                help="Download from Robinhood: Account → History → Export"
            )
            
            if csv_file:
                if st.button("Parse CSV & Update Portfolio", use_container_width=True):
                    with st.spinner("Parsing transactions..."):
                        transactions_df, position_updates = parse_robinhood_csv(csv_file)
                        
                        # Show preview
                        st.success(f"✓ Parsed {len(transactions_df)} transactions")
                        st.dataframe(transactions_df.head(20), use_container_width=True)
                        
                        # Show position updates
                        if position_updates:
                            st.markdown("#### Position Updates")
                            for ticker, new_shares in position_updates.items():
                                old_shares = st.session_state.portfolio[ticker]["shares"]
                                diff = new_shares - old_shares
                                st.markdown(f"**{ticker}**: {old_shares:.4f} → {new_shares:.4f} ({diff:+.4f})")
                            
                            if st.button("✓ Confirm & Apply Updates", use_container_width=True):
                                for ticker, new_shares in position_updates.items():
                                    st.session_state.portfolio[ticker]["shares"] = new_shares
                                st.success("Portfolio updated!")
                                st.rerun()
        
        with col2:
            st.markdown("#### PDF Upload (Crypto Statements)")
            pdf_file = st.file_uploader(
                "Upload crypto statement PDF",
                type=["pdf"],
                help="Upload Robinhood Crypto statement for BTC/XRP transactions"
            )
            
            if pdf_file:
                st.info("PDF parsing will be available in next update")
    
    # ════════════════════════════════════════════════════════════════════════════
    # TAB 4: DEPLOY $900
    # ════════════════════════════════════════════════════════════════════════════
    
    with tab4:
        st.markdown("### 💰 Biweekly $900 Deployment")
        
        st.markdown("""
        **Next deposit:** Friday, April 3, 2026  
        **Amount:** $900  
        **Strategy:** DCA into core positions + rotating opportunity pick
        """)
        
        # Show allocation
        if prices:
            cash_recs = generate_cash_deployment_recs(900, prices)
            
            if cash_recs:
                st.markdown("#### Recommended Allocation")
                
                for rec in cash_recs:
                    col1, col2, col3, col4 = st.columns([2, 1, 1, 3])
                    with col1:
                        st.markdown(f"**{rec['ticker']}**")
                    with col2:
                        st.markdown(f"${rec['amount']:.2f}")
                    with col3:
                        st.markdown(f"{rec['shares']:.4f} sh")
                    with col4:
                        st.markdown(f"_{rec['rationale']}_")
                
                st.markdown("---")
                
                if st.button("✓ Log This Deposit", use_container_width=True):
                    # Update holdings
                    for rec in cash_recs:
                        ticker = rec['ticker']
                        st.session_state.portfolio[ticker]["shares"] += rec['shares']
                    
                    # Update cash
                    st.session_state.cash_balance -= 900
                    
                    # Log to history
                    st.session_state.recommendation_history.append({
                        "date": datetime.now().strftime("%Y-%m-%d"),
                        "amount": 900,
                        "allocation": cash_recs
                    })
                    
                    st.success("Deposit logged! Portfolio updated.")
                    st.rerun()
        else:
            st.warning("Refresh prices to see deployment recommendations")
        
        # Show 2026 deposit schedule
        st.markdown("#### 2026 Deposit Calendar")
        deposit_dates = [
            "Apr 3", "Apr 17", "May 1", "May 15", "May 29", "Jun 12", "Jun 26",
            "Jul 10", "Jul 24", "Aug 7", "Aug 21", "Sep 4", "Sep 18", "Oct 2",
            "Oct 16", "Oct 30", "Nov 13", "Nov 27", "Dec 11"
        ]
        
        cols = st.columns(5)
        for i, date in enumerate(deposit_dates):
            with cols[i % 5]:
                st.markdown(f"**{date}**")
    
    # ════════════════════════════════════════════════════════════════════════════
    # TAB 5: PERFORMANCE
    # ════════════════════════════════════════════════════════════════════════════
    
    with tab5:
        st.markdown("### 📈 Performance Tracking")
        
        # Show recommendation history
        if st.session_state.recommendation_history:
            st.markdown("#### Deposit History")
            
            for entry in reversed(st.session_state.recommendation_history):
                with st.expander(f"{entry['date']} — ${entry['amount']} deployed"):
                    for alloc in entry['allocation']:
                        st.markdown(f"- **{alloc['ticker']}**: ${alloc['amount']:.2f} ({alloc['shares']:.4f} shares)")
        else:
            st.info("No deposits logged yet")
    
    # ════════════════════════════════════════════════════════════════════════════
    # TAB 6: SETTINGS
    # ════════════════════════════════════════════════════════════════════════════
    
    with tab6:
        st.markdown("### ⚙️ Settings")
        
        # Manual cash update
        st.markdown("#### Update Cash Balance")
        new_cash = st.number_input("Cash available", value=st.session_state.cash_balance, step=100.0)
        if st.button("Update Cash"):
            st.session_state.cash_balance = new_cash
            st.success(f"Cash updated to ${new_cash:,.2f}")
        
        st.markdown("---")
        
        # Export data
        st.markdown("#### Export Data")
        if st.button("📥 Download Portfolio as JSON"):
            export_data = {
                "portfolio": st.session_state.portfolio,
                "cash_balance": st.session_state.cash_balance,
                "prices": st.session_state.prices,
                "last_update": st.session_state.last_update,
                "history": st.session_state.recommendation_history
            }
            
            st.download_button(
                label="Download JSON",
                data=json.dumps(export_data, indent=2),
                file_name=f"portfolio_backup_{datetime.now().strftime('%Y%m%d')}.json",
                mime="application/json"
            )
        
        st.markdown("---")
        
        # Reset warning
        st.markdown("#### Danger Zone")
        if st.button("🔴 Reset All Data", use_container_width=True):
            st.warning("This will reset all holdings to defaults. This action cannot be undone.")
            if st.button("Confirm Reset"):
                # Reset to initial state
                st.session_state.portfolio = {
                    # Re-initialize with original data
                }
                st.session_state.cash_balance = 1042.17
                st.session_state.prices = {}
                st.session_state.recommendation_history = []
                st.success("Data reset complete")
                st.rerun()

if __name__ == "__main__":
    main()
