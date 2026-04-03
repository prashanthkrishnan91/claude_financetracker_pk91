"""
price_service.py — Portfolio War Room v11.1
Real-time pricing engine — mirrors Robinhood's Mark Price (Bid/Ask midpoint).

Changes vs v11.0:
  - Fully async-native: fetch_prices_async() uses asyncio.gather() + aiohttp
  - Synchronous fetch_prices() is kept as a convenience wrapper for non-async callers
  - institution_price fallback now reads from HoldingsCache (not just memory dict)
  - Ticker mismatch map expanded
  - Rate-limit aware: Finnhub free tier batching with 1s pauses between chunks

Primary:  Finnhub  — /quote + /stock/bidask (premium)
Fallback: Polygon  — /v2/snapshot (mid = (bid + ask) / 2)
Crypto:   CoinGecko — /simple/price  (free, no key)
Safety:   institution_price from HoldingsCache then memory cache

All keys from environment variables — nothing hardcoded.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

if TYPE_CHECKING:
    from holdings_manager import HoldingsCache

logger = logging.getLogger(__name__)

_FINNHUB_BASE   = "https://finnhub.io/api/v1"
_POLYGON_BASE   = "https://api.polygon.io"
_COINGECKO_BASE = "https://api.coingecko.com/api/v3"
_TIMEOUT        = 8
_MAX_WORKERS    = 20
_FINNHUB_BATCH  = 50
_ASYNC_BATCH_PAUSE = 1.0

_TICKER_MAP: dict[str, str] = {
    "BRK.B": "BRK-B",  "BRK.A": "BRK-A",
    "BF.B":  "BF-B",   "BF.A":  "BF-A",
    "LEN.B": "LEN-B",  "MKL.A": "MKL-A",
}
_REVERSE_TICKER_MAP: dict[str, str] = {v: k for k, v in _TICKER_MAP.items()}

_CRYPTO_IDS: dict[str, str] = {
    "BTC":   "bitcoin",    "ETH":   "ethereum",
    "XRP":   "ripple",     "SOL":   "solana",
    "DOGE":  "dogecoin",   "ADA":   "cardano",
    "AVAX":  "avalanche-2","MATIC": "matic-network",
    "DOT":   "polkadot",   "LTC":   "litecoin",
}
_CRYPTO_TICKERS = set(_CRYPTO_IDS.keys())


@dataclass
class PriceResult:
    ticker:     str
    mid_price:  float
    bid:        Optional[float]
    ask:        Optional[float]
    last_trade: float
    source:     str
    timestamp:  float
    error:      Optional[str] = None

    @property
    def is_valid(self) -> bool:
        return self.mid_price > 0 and self.error is None

    @property
    def is_stale(self) -> bool:
        return self.source.startswith(("cache", "institution"))


def _make_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3, backoff_factor=0.4,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"], raise_on_status=False,
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


class PriceService:
    """
    Fetches real-time mid-prices for stocks, ETFs, and crypto.

    Mark Price: mid = (bid+ask)/2, fallback to last_trade.
    Provider cascade: Finnhub -> Polygon -> CoinGecko (crypto) ->
                      institution_price (HoldingsCache) -> memory cache.

    Two fetch modes:
        fetch_prices()        synchronous, ThreadPoolExecutor (20 workers)
        fetch_prices_async()  fully async, asyncio.gather + aiohttp

    Required env vars: FINNHUB_API_KEY, POLYGON_API_KEY (optional)
    """

    def __init__(self, holdings_cache=None) -> None:
        self._finnhub_key   = os.environ.get("FINNHUB_API_KEY", "")
        self._polygon_key   = os.environ.get("POLYGON_API_KEY", "")
        self._session       = _make_session()
        self._memory_cache: dict[str, PriceResult] = {}
        self._holdings_cache = holdings_cache

        if not self._finnhub_key:
            logger.warning("FINNHUB_API_KEY not set — falling back to Polygon/CoinGecko.")

    # ── Public: Synchronous ───────────────────────────────────────────────────

    def fetch_prices(self, tickers: list[str], institution_fallback=None) -> dict[str, PriceResult]:
        cache_ref = institution_fallback or self._holdings_cache
        normalised = [self._normalise(t) for t in tickers]
        results: dict[str, PriceResult] = {}

        with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as executor:
            future_map = {
                executor.submit(self._fetch_one, ticker, cache_ref): ticker
                for ticker in normalised
            }
            for future in as_completed(future_map):
                ticker = future_map[future]
                try:
                    result = future.result(timeout=_TIMEOUT + 2)
                    results[ticker] = result
                    if result.is_valid and not result.is_stale:
                        self._memory_cache[ticker] = result
                except Exception as exc:
                    logger.error("Unexpected error fetching %s: %s", ticker, exc)
                    results[ticker] = self._fallback(ticker, str(exc), cache_ref)

        return results

    # ── Public: Fully Async ───────────────────────────────────────────────────

    async def fetch_prices_async(self, tickers: list[str], institution_fallback=None) -> dict[str, PriceResult]:
        cache_ref  = institution_fallback or self._holdings_cache
        normalised = [self._normalise(t) for t in tickers]

        try:
            import aiohttp
            return await self._gather_aiohttp(normalised, cache_ref)
        except ImportError:
            logger.debug("aiohttp not installed — running sync fetch in executor")
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, self.fetch_prices, tickers, institution_fallback)

    async def _gather_aiohttp(self, tickers: list[str], cache_ref) -> dict[str, PriceResult]:
        import aiohttp
        results: dict[str, PriceResult] = {}
        connector = aiohttp.TCPConnector(limit=_MAX_WORKERS, limit_per_host=10)
        timeout   = aiohttp.ClientTimeout(total=_TIMEOUT)

        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            chunks = [tickers[i:i+_FINNHUB_BATCH] for i in range(0, len(tickers), _FINNHUB_BATCH)]
            for idx, chunk in enumerate(chunks):
                if idx > 0:
                    await asyncio.sleep(_ASYNC_BATCH_PAUSE)
                tasks = [
                    asyncio.create_task(self._fetch_one_async(t, session, cache_ref), name=t)
                    for t in chunk
                ]
                done = await asyncio.gather(*tasks, return_exceptions=True)
                for ticker, result in zip(chunk, done):
                    if isinstance(result, Exception):
                        results[ticker] = self._fallback(ticker, str(result), cache_ref)
                    else:
                        results[ticker] = result
                        if result.is_valid and not result.is_stale:
                            self._memory_cache[ticker] = result

        return results

    async def _fetch_one_async(self, ticker: str, session, cache_ref) -> PriceResult:
        import aiohttp
        if ticker in _CRYPTO_TICKERS:
            return await self._fetch_crypto_async(ticker, session, cache_ref)
        if self._finnhub_key:
            try:
                r = await self._fetch_finnhub_async(ticker, session)
                if r.is_valid:
                    return r
            except Exception as e:
                logger.debug("Finnhub async %s: %s", ticker, e)
        if self._polygon_key:
            try:
                r = await self._fetch_polygon_async(ticker, session)
                if r.is_valid:
                    return r
            except Exception as e:
                logger.debug("Polygon async %s: %s", ticker, e)
        return self._fallback(ticker, "All async providers failed", cache_ref)

    async def _fetch_finnhub_async(self, ticker: str, session) -> PriceResult:
        import aiohttp
        url = f"{_FINNHUB_BASE}/quote"
        async with session.get(url, params={"symbol": ticker, "token": self._finnhub_key}) as resp:
            if resp.status != 200:
                return self._error_result(ticker, "finnhub", f"HTTP {resp.status}")
            data = await resp.json()
        current = float(data.get("c") or 0)
        if current <= 0:
            return self._error_result(ticker, "finnhub", "Zero price")
        bid, ask = await self._finnhub_bidask_async(ticker, session)
        if bid and ask and bid > 0 and ask > 0:
            mid, src = (bid + ask) / 2.0, "finnhub"
        else:
            mid, src, bid, ask = current, "last_trade", None, None
        return PriceResult(ticker=ticker, mid_price=mid, bid=bid, ask=ask,
                           last_trade=current, source=src, timestamp=float(data.get("t") or time.time()))

    async def _finnhub_bidask_async(self, ticker: str, session) -> tuple[Optional[float], Optional[float]]:
        url = f"{_FINNHUB_BASE}/stock/bidask"
        try:
            async with session.get(url, params={"symbol": ticker, "token": self._finnhub_key}) as resp:
                if resp.status in (403, 404, 429):
                    return None, None
                if resp.status != 200:
                    return None, None
                data = await resp.json()
                return float(data.get("b") or 0) or None, float(data.get("a") or 0) or None
        except Exception:
            return None, None

    async def _fetch_polygon_async(self, ticker: str, session) -> PriceResult:
        url = f"{_POLYGON_BASE}/v2/snapshot/locale/us/markets/stocks/tickers/{ticker}"
        async with session.get(url, params={"apiKey": self._polygon_key}) as resp:
            if resp.status != 200:
                return self._error_result(ticker, "polygon", f"HTTP {resp.status}")
            data = await resp.json()
        snap  = data.get("ticker", {})
        lq    = snap.get("lastQuote", {})
        lt    = snap.get("lastTrade", {})
        day   = snap.get("day", {})
        last  = float(lt.get("p") or day.get("c") or 0)
        bid   = float(lq.get("p") or 0) or None
        ask   = float(lq.get("P") or 0) or None
        if last <= 0:
            return self._error_result(ticker, "polygon", "Zero last-trade")
        mid, src = ((bid + ask) / 2.0, "polygon") if (bid and ask) else (last, "last_trade")
        return PriceResult(ticker=ticker, mid_price=mid, bid=bid, ask=ask,
                           last_trade=last, source=src, timestamp=float(lt.get("t", time.time()*1e9))/1e9)

    async def _fetch_crypto_async(self, ticker: str, session, cache_ref) -> PriceResult:
        coin_id = _CRYPTO_IDS.get(ticker)
        if not coin_id:
            return self._fallback(ticker, f"No CoinGecko ID for {ticker}", cache_ref)
        url = f"{_COINGECKO_BASE}/simple/price"
        try:
            async with session.get(url, params={"ids": coin_id, "vs_currencies": "usd"}) as resp:
                if resp.status != 200:
                    return self._fallback(ticker, f"CoinGecko HTTP {resp.status}", cache_ref)
                data = await resp.json()
        except Exception as e:
            return self._fallback(ticker, f"CoinGecko: {e}", cache_ref)
        price = float(data.get(coin_id, {}).get("usd") or 0)
        if price <= 0:
            return self._fallback(ticker, "CoinGecko zero price", cache_ref)
        return PriceResult(ticker=ticker, mid_price=price, bid=None, ask=None,
                           last_trade=price, source="coingecko", timestamp=time.time())

    # ── Sync provider implementations ─────────────────────────────────────────

    def _fetch_one(self, ticker: str, cache_ref) -> PriceResult:
        ticker = ticker.upper()
        if ticker in _CRYPTO_TICKERS:
            return self._fetch_crypto(ticker, cache_ref)
        if self._finnhub_key:
            r = self._fetch_finnhub(ticker)
            if r.is_valid:
                return r
        if self._polygon_key:
            r = self._fetch_polygon(ticker)
            if r.is_valid:
                return r
        return self._fallback(ticker, "All sync providers failed", cache_ref)

    def _fetch_finnhub(self, ticker: str) -> PriceResult:
        try:
            resp = self._session.get(f"{_FINNHUB_BASE}/quote",
                                     params={"symbol": ticker, "token": self._finnhub_key},
                                     timeout=_TIMEOUT)
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
            return self._error_result(ticker, "finnhub", "Zero price")
        bid, ask = self._finnhub_bidask(ticker)
        if bid and ask and bid > 0 and ask > 0:
            mid, src = (bid + ask) / 2.0, "finnhub"
        else:
            mid, src, bid, ask = current, "last_trade", None, None
        return PriceResult(ticker=ticker, mid_price=mid, bid=bid, ask=ask,
                           last_trade=current, source=src, timestamp=float(data.get("t") or time.time()))

    def _finnhub_bidask(self, ticker: str) -> tuple[Optional[float], Optional[float]]:
        if not self._finnhub_key:
            return None, None
        try:
            resp = self._session.get(f"{_FINNHUB_BASE}/stock/bidask",
                                     params={"symbol": ticker, "token": self._finnhub_key},
                                     timeout=_TIMEOUT)
            if resp.status_code in (403, 404):
                return None, None
            resp.raise_for_status()
            data = resp.json()
            return float(data.get("b") or 0) or None, float(data.get("a") or 0) or None
        except Exception:
            return None, None

    def _fetch_polygon(self, ticker: str) -> PriceResult:
        try:
            resp = self._session.get(
                f"{_POLYGON_BASE}/v2/snapshot/locale/us/markets/stocks/tickers/{ticker}",
                params={"apiKey": self._polygon_key}, timeout=_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.Timeout:
            return self._error_result(ticker, "polygon", "Timeout")
        except requests.exceptions.HTTPError as e:
            return self._error_result(ticker, "polygon", f"HTTP {e.response.status_code}")
        except Exception as e:
            return self._error_result(ticker, "polygon", str(e))

        snap  = data.get("ticker", {})
        lq    = snap.get("lastQuote", {})
        lt    = snap.get("lastTrade", {})
        day   = snap.get("day", {})
        last  = float(lt.get("p") or day.get("c") or 0)
        bid   = float(lq.get("p") or 0) or None
        ask   = float(lq.get("P") or 0) or None
        if last <= 0:
            return self._error_result(ticker, "polygon", "Zero last-trade price")
        mid, src = ((bid + ask) / 2.0, "polygon") if (bid and ask) else (last, "last_trade")
        return PriceResult(ticker=ticker, mid_price=mid, bid=bid, ask=ask,
                           last_trade=last, source=src,
                           timestamp=float(lt.get("t", time.time()*1e9))/1e9)

    def _fetch_crypto(self, ticker: str, cache_ref) -> PriceResult:
        coin_id = _CRYPTO_IDS.get(ticker)
        if not coin_id:
            return self._fallback(ticker, f"No CoinGecko ID for {ticker}", cache_ref)
        try:
            resp = self._session.get(f"{_COINGECKO_BASE}/simple/price",
                                     params={"ids": coin_id, "vs_currencies": "usd"},
                                     timeout=_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            return self._fallback(ticker, f"CoinGecko: {e}", cache_ref)
        price = float(data.get(coin_id, {}).get("usd") or 0)
        if price <= 0:
            return self._fallback(ticker, "CoinGecko zero price", cache_ref)
        return PriceResult(ticker=ticker, mid_price=price, bid=None, ask=None,
                           last_trade=price, source="coingecko", timestamp=time.time())

    # ── Fallback chain ────────────────────────────────────────────────────────

    def _fallback(self, ticker: str, reason: str, cache_ref) -> PriceResult:
        # 1. In-memory price cache
        cached = self._memory_cache.get(ticker)
        if cached and cached.mid_price > 0:
            logger.warning("Memory-cached price for %s ($%.4f): %s", ticker, cached.mid_price, reason)
            return PriceResult(ticker=ticker, mid_price=cached.mid_price, bid=None, ask=None,
                               last_trade=cached.last_trade, source=f"cache({cached.source})",
                               timestamp=cached.timestamp, error=f"STALE — {reason}")
        # 2. institution_price from HoldingsCache
        if cache_ref:
            for h in getattr(cache_ref, "holdings", []):
                if h.ticker == ticker and h.institution_price > 0:
                    logger.warning("institution_price for %s ($%.4f): %s", ticker, h.institution_price, reason)
                    return PriceResult(ticker=ticker, mid_price=h.institution_price, bid=None, ask=None,
                                       last_trade=h.institution_price, source="institution",
                                       timestamp=time.time(), error=f"INSTITUTION_FALLBACK — {reason}")
        return self._error_result(ticker, "none", reason)

    @staticmethod
    def _normalise(ticker: str) -> str:
        return _TICKER_MAP.get(ticker.upper(), ticker.upper())

    def _error_result(self, ticker: str, source: str, msg: str) -> PriceResult:
        return PriceResult(ticker=ticker, mid_price=0.0, bid=None, ask=None,
                           last_trade=0.0, source=source, timestamp=time.time(), error=msg)
