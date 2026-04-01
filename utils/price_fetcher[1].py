"""
Price Fetcher v4
Sources: yfinance (all stocks/ETFs, batch) + CoinGecko (BTC/XRP, no key needed)
Runs server-side → no CORS issues, no 429 rate limits.
"""
import yfinance as yf
import requests
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Dict, Tuple, List, Optional
import streamlit as st


CRYPTO_COINGECKO = {
    "BTC": "bitcoin",
    "XRP": "ripple",
}

# Ticker mapping: our ticker → yfinance ticker
YF_MAP = {
    "BRK-B": "BRK-B",  # yfinance accepts this directly
    "BTC":   "BTC-USD",
    "XRP":   "XRP-USD",
}


def _to_yf(ticker: str) -> str:
    return YF_MAP.get(ticker, ticker)


def _from_yf(yf_ticker: str) -> str:
    reverse = {v: k for k, v in YF_MAP.items()}
    return reverse.get(yf_ticker, yf_ticker)


@st.cache_data(ttl=300, show_spinner=False)
def fetch_all_prices(tickers: tuple) -> Tuple[Dict[str, float], Dict[str, str], str, List[str]]:
    """
    Fetch prices for all tickers.
    Returns: (prices, sources, timestamp, errors)

    Cache TTL: 5 minutes (avoids hammering APIs on every rerun)
    """
    ts     = datetime.now(ZoneInfo("America/New_York")).strftime("%b %d, %Y  %I:%M %p ET")
    prices = {}
    sources = {}
    errors  = []

    stock_tickers   = [t for t in tickers if t not in CRYPTO_COINGECKO]
    crypto_tickers  = [t for t in tickers if t in CRYPTO_COINGECKO]

    # ── Source 1: yfinance batch (all stocks + ETFs + crypto via -USD pairs) ──
    yf_tickers = [_to_yf(t) for t in stock_tickers] + \
                 [_to_yf(t) for t in crypto_tickers]

    try:
        raw = yf.download(
            tickers    = " ".join(yf_tickers),
            period     = "2d",
            interval   = "1d",
            progress   = False,
            auto_adjust= True,
            threads    = True,
        )

        if not raw.empty and "Close" in raw.columns:
            close = raw["Close"]
            # Multi-ticker: DataFrame; single-ticker: Series
            if isinstance(close, pd.Series):
                val = float(close.dropna().iloc[-1])
                if val > 0:
                    key = _from_yf(yf_tickers[0])
                    prices[key]  = round(val, 4)
                    sources[key] = "yfinance"
            else:
                for col in close.columns:
                    series = close[col].dropna()
                    if not series.empty:
                        val = float(series.iloc[-1])
                        if val > 0:
                            key = _from_yf(str(col))
                            prices[key]  = round(val, 4)
                            sources[key] = "yfinance"
        else:
            errors.append("yfinance returned empty data")

    except Exception as e:
        errors.append(f"yfinance error: {e}")

    # ── Source 2: CoinGecko (BTC + XRP — free, no API key, real-time) ─────────
    try:
        cg_ids = ",".join(CRYPTO_COINGECKO[t] for t in crypto_tickers if t in CRYPTO_COINGECKO)
        if cg_ids:
            r = requests.get(
                f"https://api.coingecko.com/api/v3/simple/price"
                f"?ids={cg_ids}&vs_currencies=usd",
                timeout=10, headers={"Accept": "application/json"}
            )
            if r.status_code == 200:
                cg = r.json()
                for ticker, cg_id in CRYPTO_COINGECKO.items():
                    if cg.get(cg_id, {}).get("usd"):
                        prices[ticker]  = cg[cg_id]["usd"]
                        sources[ticker] = "CoinGecko"
            else:
                errors.append(f"CoinGecko HTTP {r.status_code}")
    except Exception as e:
        errors.append(f"CoinGecko error: {e}")

    return prices, sources, ts, errors


def force_refresh_prices(tickers: tuple):
    """Bypass cache and force a fresh fetch."""
    fetch_all_prices.clear()
    return fetch_all_prices(tickers)


def get_equity_summary(positions: list, prices: Dict[str, float], cash: float) -> dict:
    """
    Compute portfolio-level equity metrics matching Robinhood's reported value.
    Includes: equity positions + crypto + cash.
    """
    total_cost    = 0.0
    equity_value  = 0.0
    crypto_value  = 0.0
    unrealized_gl = 0.0

    for p in positions:
        cat, ticker, _, shares, cost = p[0], p[1], p[2], p[3], p[4]
        price = prices.get(ticker, cost)  # fallback to cost if no price
        pos_value = shares * price
        pos_cost  = shares * cost
        gl        = pos_value - pos_cost

        total_cost    += pos_cost
        unrealized_gl += gl

        if cat == "Crypto":
            crypto_value += pos_value
        else:
            equity_value += pos_value

    total_portfolio = equity_value + crypto_value + cash

    return {
        "equity_value":    round(equity_value,  2),
        "crypto_value":    round(crypto_value,  2),
        "cash":            round(cash,           2),
        "total_portfolio": round(total_portfolio, 2),
        "total_cost":      round(total_cost,     2),
        "unrealized_gl":   round(unrealized_gl,  2),
        "gl_pct":          round(unrealized_gl / total_cost * 100, 2) if total_cost else 0,
    }
