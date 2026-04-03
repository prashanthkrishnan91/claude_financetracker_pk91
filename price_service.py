"""
price_service.py — Portfolio War Room v11.0
Real-time pricing engine — mirrors Robinhood's Mark Price (Bid/Ask midpoint).

Primary:  Finnhub  — /quote  (last, bid, ask)
Fallback: Polygon  — /v2/snapshot (mid = (bidprice + askprice) / 2)
Safety:   Last-trade price if bid/ask unavailable (extended hours / low liquidity)

All keys from environment variables — nothing hardcoded.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Optional
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

# ─── Constants ────────────────────────────────────────────────────────────────

_FINNHUB_BASE   = "https://finnhub.io/api/v1"
_POLYGON_BASE   = "https://api.polygon.io"
_COINGECKO_BASE = "https://api.coingecko.com/api/v3"
_TIMEOUT        = 8    # seconds per request
_MAX_WORKERS    = 20   # concurrent price fetches
_RATE_SLEEP     = 0.05 # 50ms between Finnhub calls (60 req/min free tier)

# CoinGecko coin ID mapping for crypto tickers
_CRYPTO_IDS: dict[str, str] = {
    "BTC":  "bitcoin",
    "ETH":  "ethereum",
    "XRP":  "ripple",
    "SOL":  "solana",
    "DOGE": "dogecoin",
    "ADA":  "cardano",
    "AVAX": "avalanche-2",
    "MATIC":"matic-network",
    "DOT":  "polkadot",
    "LTC":  "litecoin",
}

# ─── Data Structures ──────────────────────────────────────────────────────────

@dataclass
class PriceResult:
    """Price result for one ticker."""
    ticker: str
    mid_price: float      # Primary value used for portfolio valuation
    bid: Optional[float]
    ask: Optional[float]
    last_trade: float
    source: str           # 'finnhub' | 'polygon' | 'coingecko' | 'last_trade'
    timestamp: float      # Unix epoch
    error: Optional[str] = None

    @property
    def is_valid(self) -> bool:
        return self.mid_price > 0 and self.error is None


# ─── HTTP session with retries ────────────────────────────────────────────────

def _make_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=0.3,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    return session


# ─── PriceService ─────────────────────────────────────────────────────────────

class PriceService:
    """
    Fetches real-time mid-price for stocks, ETFs, and crypto.

    Mark Price formula (mirrors Robinhood):
        mid = (bid + ask) / 2
        If bid/ask unavailable → last_trade price
        If Finnhub fails → Polygon snapshot
        If Polygon fails → CoinGecko (crypto only)

    Required environment variables:
        FINNHUB_API_KEY   — Finnhub.io API key
        POLYGON_API_KEY   — Polygon.io API key (optional fallback)
    """

    def __init__(self) -> None:
        self._finnhub_key = os.environ.get("FINNHUB_API_KEY", "")
        self._polygon_key = os.environ.get("POLYGON_API_KEY", "")
        self._session     = _make_session()
        self._disk_cache: dict[str, PriceResult] = {}   # last-known prices

        if not self._finnhub_key:
            logger.warning(
                "FINNHUB_API_KEY not set. Price fetches will fall back to Polygon/CoinGecko only."
            )

    # ── Public API ────────────────────────────────────────────────────────────

    def fetch_prices(self, tickers: list[str]) -> dict[str, PriceResult]:
        """
        Fetch real-time prices for all tickers concurrently.

        Args:
            tickers: List of normalised ticker symbols.

        Returns:
            dict mapping ticker → PriceResult.
            Tickers that fail all providers get a PriceResult with error set,
            mid_price = last cached value if available, else 0.
        """
        results: dict[str, PriceResult] = {}

        with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as executor:
            future_map = {
                executor.submit(self._fetch_one, ticker): ticker
                for ticker in tickers
            }
            for future in as_completed(future_map):
                ticker = future_map[future]
                try:
                    result = future.result(timeout=_TIMEOUT + 2)
                    results[ticker] = result
                    if result.is_valid:
                        self._disk_cache[ticker] = result   # update last-known
                except Exception as exc:
                    logger.error("Unexpected error fetching %s: %s", ticker, exc)
                    results[ticker] = self._cache_or_error(ticker, str(exc))

        return results

    async def fetch_prices_async(self, tickers: list[str]) -> dict[str, PriceResult]:
        """
        Async wrapper — runs fetch_prices in a thread pool from an async context.
        Use this from async code (e.g., FastAPI routes).
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.fetch_prices, tickers)

    # ── Single-ticker fetch with provider cascade ─────────────────────────────

    def _fetch_one(self, ticker: str) -> PriceResult:
        """
        Fetch one ticker — tries Finnhub → Polygon → CoinGecko in order.
        Never raises — returns PriceResult with error set on total failure.
        """
        ticker = ticker.upper()

        # Crypto → direct to CoinGecko
        if ticker in _CRYPTO_IDS:
            return self._fetch_crypto(ticker)

        # Stocks / ETFs → Finnhub first
        if self._finnhub_key:
            result = self._fetch_finnhub(ticker)
            if result.is_valid:
                return result
            logger.debug("Finnhub failed for %s (%s) — trying Polygon", ticker, result.error)

        # Polygon fallback
        if self._polygon_key:
            result = self._fetch_polygon(ticker)
            if result.is_valid:
                return result
            logger.debug("Polygon failed for %s (%s) — no more providers", ticker, result.error)

        return self._cache_or_error(ticker, "All price providers failed")

    # ── Finnhub ───────────────────────────────────────────────────────────────

    def _fetch_finnhub(self, ticker: str) -> PriceResult:
        """
        GET /quote — returns c (current/last), h (high), l (low), o (open),
        pc (prev close), t (timestamp).
        NOTE: Finnhub free tier does NOT provide real-time bid/ask for US equities.
        Use /stock/bidask endpoint if on premium plan.
        Falls back to last trade price as mid.
        """
        url = f"{_FINNHUB_BASE}/quote"
        params = {"symbol": ticker, "token": self._finnhub_key}

        try:
            resp = self._session.get(url, params=params, timeout=_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.Timeout:
            return self._error_result(ticker, "finnhub", "Timeout")
        except requests.exceptions.HTTPError as e:
            return self._error_result(ticker, "finnhub", f"HTTP {e.response.status_code}")
        except Exception as e:
            return self._error_result(ticker, "finnhub", str(e))

        current = float(data.get("c") or 0)
        if current <= 0:
            return self._error_result(ticker, "finnhub", "Zero price returned")

        # Try bid/ask endpoint (premium) — gracefully skip if unavailable
        bid, ask = self._finnhub_bidask(ticker)

        if bid and ask and bid > 0 and ask > 0:
            mid = (bid + ask) / 2.0
            source = "finnhub"
        else:
            mid = current
            bid = ask = None
            source = "last_trade"

        return PriceResult(
            ticker=ticker,
            mid_price=mid,
            bid=bid,
            ask=ask,
            last_trade=current,
            source=source,
            timestamp=float(data.get("t") or time.time()),
        )

    def _finnhub_bidask(self, ticker: str) -> tuple[Optional[float], Optional[float]]:
        """
        GET /stock/bidask — premium endpoint. Returns (bid, ask) or (None, None).
        Silently returns None pair if not on premium plan (403/404).
        """
        if not self._finnhub_key:
            return None, None
        url = f"{_FINNHUB_BASE}/stock/bidask"
        params = {"symbol": ticker, "token": self._finnhub_key}
        try:
            resp = self._session.get(url, params=params, timeout=_TIMEOUT)
            if resp.status_code in (403, 404):
                return None, None
            resp.raise_for_status()
            data = resp.json()
            bid = float(data.get("b") or 0) or None
            ask = float(data.get("a") or 0) or None
            return bid, ask
        except Exception:
            return None, None

    # ── Polygon ───────────────────────────────────────────────────────────────

    def _fetch_polygon(self, ticker: str) -> PriceResult:
        """
        GET /v2/last/trade/{ticker} for last trade.
        GET /v2/snapshot/locale/us/markets/stocks/tickers/{ticker} for bid/ask.
        """
        # Try snapshot first (has bid/ask + last trade)
        url = f"{_POLYGON_BASE}/v2/snapshot/locale/us/markets/stocks/tickers/{ticker}"
        params = {"apiKey": self._polygon_key}

        try:
            resp = self._session.get(url, params=params, timeout=_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.Timeout:
            return self._error_result(ticker, "polygon", "Timeout")
        except requests.exceptions.HTTPError as e:
            return self._error_result(ticker, "polygon", f"HTTP {e.response.status_code}")
        except Exception as e:
            return self._error_result(ticker, "polygon", str(e))

        snap = data.get("ticker", {})
        day  = snap.get("day", {})
        last_quote = snap.get("lastQuote", {})
        last_trade = snap.get("lastTrade", {})

        last = float(last_trade.get("p") or day.get("c") or 0)
        bid  = float(last_quote.get("P") or 0) or None   # Polygon uses uppercase P for bid
        ask  = float(last_quote.get("S") or 0) or None   # uppercase S for ask size — swap:
        # Correct Polygon keys: bid = lastQuote.p, ask = lastQuote.P
        bid  = float(last_quote.get("p") or 0) or None
        ask  = float(last_quote.get("P") or 0) or None

        if last <= 0:
            return self._error_result(ticker, "polygon", "Zero last-trade price")

        if bid and ask and bid > 0 and ask > 0:
            mid = (bid + ask) / 2.0
            source = "polygon"
        else:
            mid = last
            source = "last_trade"

        return PriceResult(
            ticker=ticker,
            mid_price=mid,
            bid=bid,
            ask=ask,
            last_trade=last,
            source=source,
            timestamp=float(last_trade.get("t", time.time() * 1e9)) / 1e9,
        )

    # ── CoinGecko (crypto) ────────────────────────────────────────────────────

    def _fetch_crypto(self, ticker: str) -> PriceResult:
        """
        GET /simple/price — CoinGecko (free, no key required).
        No bid/ask for crypto — uses last market price as mid.
        """
        coin_id = _CRYPTO_IDS.get(ticker.upper())
        if not coin_id:
            return self._error_result(ticker, "coingecko", f"No CoinGecko ID for {ticker}")

        url = f"{_COINGECKO_BASE}/simple/price"
        params = {"ids": coin_id, "vs_currencies": "usd"}

        try:
            resp = self._session.get(url, params=params, timeout=_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.Timeout:
            return self._error_result(ticker, "coingecko", "Timeout")
        except requests.exceptions.HTTPError as e:
            return self._error_result(ticker, "coingecko", f"HTTP {e.response.status_code}")
        except Exception as e:
            return self._error_result(ticker, "coingecko", str(e))

        price = float(data.get(coin_id, {}).get("usd") or 0)
        if price <= 0:
            return self._error_result(ticker, "coingecko", "Zero price returned")

        return PriceResult(
            ticker=ticker,
            mid_price=price,
            bid=None,
            ask=None,
            last_trade=price,
            source="coingecko",
            timestamp=time.time(),
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _error_result(self, ticker: str, source: str, msg: str) -> PriceResult:
        return PriceResult(
            ticker=ticker,
            mid_price=0.0,
            bid=None,
            ask=None,
            last_trade=0.0,
            source=source,
            timestamp=time.time(),
            error=msg,
        )

    def _cache_or_error(self, ticker: str, msg: str) -> PriceResult:
        """Return last cached price with warning, or zero-price error result."""
        cached = self._disk_cache.get(ticker)
        if cached and cached.mid_price > 0:
            logger.warning(
                "Using cached price for %s (%.2f) — live fetch failed: %s",
                ticker, cached.mid_price, msg
            )
            return PriceResult(
                ticker=ticker,
                mid_price=cached.mid_price,
                bid=None,
                ask=None,
                last_trade=cached.last_trade,
                source=f"cache({cached.source})",
                timestamp=cached.timestamp,
                error=f"STALE — {msg}",
            )
        return self._error_result(ticker, "none", msg)
