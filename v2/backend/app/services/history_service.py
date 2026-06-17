"""
Historical price data service — 1Y+ OHLCV for charting.

Sources:
  1. Supabase cache (price_history table) — first check
  2. yfinance — backfill if cache is stale or missing
  3. Alpaca — alternative source for intraday/recent data

Data is cached in Supabase price_history table.
Cache is refreshed if the latest date in the cache is older than today.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# Period → yfinance range parameter
_PERIOD_MAP = {
    "1W": "5d",
    "1M": "1mo",
    "3M": "3mo",
    "6M": "6mo",
    "1Y": "1y",
    "5Y": "5y",
}

# Period → approximate number of data points expected
_PERIOD_DAYS = {
    "1W": 5, "1M": 22, "3M": 66, "6M": 130, "1Y": 252, "5Y": 1260,
}

# Crypto ticker → yfinance symbol
_CRYPTO_YF = {
    "BTC": "BTC-USD", "ETH": "ETH-USD", "XRP": "XRP-USD",
    "SOL": "SOL-USD", "DOGE": "DOGE-USD", "ADA": "ADA-USD",
}

_TICKER_YF_MAP = {"BRK-B": "BRK-B", "BF-B": "BF-B"}


class HistoryPoint:
    """Single OHLCV data point."""
    __slots__ = ("date", "open", "high", "low", "close", "volume")

    def __init__(self, date: str, open: float, high: float, low: float,
                 close: float, volume: int):
        self.date = date
        self.open = open
        self.high = high
        self.low = low
        self.close = close
        self.volume = volume

    def to_dict(self) -> dict:
        return {
            "price_date": self.date,
            "open_price": self.open,
            "high_price": self.high,
            "low_price": self.low,
            "close_price": self.close,
            "volume": self.volume,
        }


class HistoryService:
    """Fetch and cache historical OHLCV data."""

    def __init__(self, supabase_client=None):
        self._supabase = supabase_client
        self._http: Optional[httpx.AsyncClient] = None

    async def _get_http(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(
                timeout=httpx.Timeout(15.0),
                headers={"User-Agent": "PortfolioIntelligence/2.0"},
            )
        return self._http

    async def close(self):
        if self._http and not self._http.is_closed:
            await self._http.aclose()

    async def get_history(
        self, ticker: str, period: str = "1Y"
    ) -> list[HistoryPoint]:
        """Get historical price data, using Supabase cache when fresh.

        Returns a list of HistoryPoint sorted by date ascending.
        """
        expected_days = _PERIOD_DAYS.get(period, 252)

        # 1. Check Supabase cache
        if self._supabase:
            cached = await self._read_cache(ticker, expected_days)
            if cached and self._cache_is_fresh(cached):
                logger.debug("%s: returning %d cached points", ticker, len(cached))
                return cached

        # 2. Fetch from yfinance
        fresh = await self._fetch_yfinance(ticker, period)
        if fresh:
            # 3. Write to Supabase cache (fire-and-forget)
            if self._supabase:
                asyncio.create_task(self._write_cache(ticker, fresh))
            return fresh

        # 4. Return stale cache if available
        if self._supabase:
            cached = await self._read_cache(ticker, expected_days)
            if cached:
                logger.warning("%s: returning stale cache (%d points)", ticker, len(cached))
                return cached

        return []

    async def get_batch_history(
        self, tickers: list[str], period: str = "1Y"
    ) -> dict[str, list[HistoryPoint]]:
        """Fetch history for multiple tickers concurrently."""
        tasks = [self.get_history(t, period) for t in tickers]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return {
            ticker: (r if isinstance(r, list) else [])
            for ticker, r in zip(tickers, results)
        }

    # ── yfinance fetch ────────────────────────────────────────────────────────

    async def _fetch_yfinance(self, ticker: str, period: str) -> list[HistoryPoint]:
        """Fetch OHLCV from Yahoo Finance v8 chart API."""
        yf_ticker = _CRYPTO_YF.get(ticker) or _TICKER_YF_MAP.get(ticker, ticker)
        yf_range = _PERIOD_MAP.get(period, "1y")

        client = await self._get_http()
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yf_ticker}"
        params = {"interval": "1d", "range": yf_range}

        try:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.error("yfinance history %s: %s", ticker, e)
            return []

        chart = data.get("chart", {}).get("result", [])
        if not chart:
            return []

        result = chart[0]
        timestamps = result.get("timestamp", [])
        indicators = result.get("indicators", {}).get("quote", [{}])[0]

        opens = indicators.get("open", [])
        highs = indicators.get("high", [])
        lows = indicators.get("low", [])
        closes = indicators.get("close", [])
        volumes = indicators.get("volume", [])

        points = []
        for i, ts in enumerate(timestamps):
            close_val = closes[i] if i < len(closes) else None
            if close_val is None:
                continue

            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            points.append(HistoryPoint(
                date=dt.strftime("%Y-%m-%d"),
                open=float(opens[i] or 0) if i < len(opens) else 0,
                high=float(highs[i] or 0) if i < len(highs) else 0,
                low=float(lows[i] or 0) if i < len(lows) else 0,
                close=float(close_val),
                volume=int(volumes[i] or 0) if i < len(volumes) else 0,
            ))

        return points

    # ── Supabase cache ────────────────────────────────────────────────────────

    async def _read_cache(self, ticker: str, limit: int) -> list[HistoryPoint]:
        """Read cached price history from Supabase."""
        try:
            result = (
                self._supabase.table("price_history")
                .select("price_date, open_price, high_price, low_price, close_price, volume")
                .eq("ticker", ticker)
                .order("price_date", desc=True)
                .limit(limit)
                .execute()
            )
            if not result.data:
                return []

            return [
                HistoryPoint(
                    date=row["price_date"],
                    open=float(row.get("open_price") or 0),
                    high=float(row.get("high_price") or 0),
                    low=float(row.get("low_price") or 0),
                    close=float(row["close_price"]),
                    volume=int(row.get("volume") or 0),
                )
                for row in reversed(result.data)  # reverse to get ascending order
            ]
        except Exception as e:
            logger.error("Cache read %s: %s", ticker, e)
            return []

    async def _write_cache(self, ticker: str, points: list[HistoryPoint]):
        """Upsert price history into Supabase (fire-and-forget)."""
        try:
            rows = [
                {
                    "ticker": ticker,
                    "price_date": p.date,
                    "open_price": p.open,
                    "high_price": p.high,
                    "low_price": p.low,
                    "close_price": p.close,
                    "volume": p.volume,
                    "source": "yfinance",
                }
                for p in points
            ]

            # Batch upsert (on conflict: ticker + price_date)
            batch_size = 100
            for i in range(0, len(rows), batch_size):
                batch = rows[i:i + batch_size]
                self._supabase.table("price_history").upsert(
                    batch, on_conflict="ticker,price_date"
                ).execute()

            logger.debug("Cached %d points for %s", len(rows), ticker)
        except Exception as e:
            logger.error("Cache write %s: %s", ticker, e)

    async def fetch_prices_from_provider(
        self, ticker: str, period: str = "5Y"
    ) -> list[HistoryPoint]:
        """Fetch historical prices directly from yfinance, bypassing the cache.

        Used by repair/backfill services that need direct provider access without
        triggering the normal cache read-or-fetch flow. Returns empty list on any
        provider failure — never raises.
        """
        return await self._fetch_yfinance(ticker, period)

    @staticmethod
    def _cache_is_fresh(points: list[HistoryPoint]) -> bool:
        """Cache is fresh if the latest point is from today or yesterday."""
        if not points:
            return False
        latest = points[-1].date
        today = date.today().isoformat()
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        # Weekend handling: Friday data is fresh on Saturday/Sunday
        days_since = (date.today() - date.fromisoformat(latest)).days
        return days_since <= 3  # Covers weekends
