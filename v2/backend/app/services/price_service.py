"""Price service — real-time quotes and historical data.

Phase 2 will implement the full async price fetching from
Alpaca, Finnhub, Polygon, and CoinGecko. This is the service
skeleton with the interface that routers depend on.
"""

from __future__ import annotations

import time
from typing import Optional
from uuid import UUID

from ..database import get_supabase_client
from ..models.price import (
    BatchPriceResponse,
    PriceHealthStatus,
    PriceHistoryPoint,
    PriceHistoryResponse,
    PriceQuote,
)


# Crypto tickers → CoinGecko IDs
CRYPTO_IDS: dict[str, str] = {
    "BTC": "bitcoin", "ETH": "ethereum", "XRP": "ripple",
    "SOL": "solana", "DOGE": "dogecoin", "ADA": "cardano",
}


class PriceService:
    """Real-time pricing engine.

    Priority chain:
    1. Alpaca Markets (real-time, free for traded stocks)
    2. Finnhub (bid/ask midpoint)
    3. Polygon (snapshot fallback)
    4. CoinGecko (crypto — free, no key)
    5. Cache (last-known price from Supabase)
    """

    def __init__(self, user_id: UUID):
        self.user_id = user_id
        self.client = get_supabase_client()

    async def get_quote(self, ticker: str) -> PriceQuote:
        """Get real-time price for a single ticker.

        TODO (Phase 2): Implement full async price fetching chain.
        """
        # Phase 2: Implement Alpaca → Finnhub → Polygon → CoinGecko chain
        return PriceQuote(
            ticker=ticker,
            mid_price=0,
            last_trade=0,
            source="pending_phase2",
            timestamp=time.time(),
            error="Price service not yet implemented — Phase 2",
        )

    async def get_batch_quotes(self, tickers: list[str]) -> BatchPriceResponse:
        """Get prices for multiple tickers using async gather.

        TODO (Phase 2): Implement parallel fetching.
        """
        prices = {}
        for ticker in tickers:
            prices[ticker] = await self.get_quote(ticker)

        health = PriceHealthStatus(
            total_tickers=len(tickers),
            fresh_count=0,
            stale_count=0,
            error_count=len(tickers),
            sources_used=[],
        )

        return BatchPriceResponse(prices=prices, health=health)

    async def get_history(self, ticker: str, period: str = "1Y") -> PriceHistoryResponse:
        """Get historical OHLCV data from Supabase cache.

        If cache miss, fetches from yfinance/Alpaca and stores in price_history table.
        TODO (Phase 2): Implement yfinance/Alpaca historical data fetching.
        """
        # Check Supabase cache first
        result = (
            self.client.table("price_history")
            .select("*")
            .eq("ticker", ticker)
            .order("price_date", desc=True)
            .limit(365)
            .execute()
        )

        data_points = [
            PriceHistoryPoint(
                price_date=row["price_date"],
                open_price=row.get("open_price"),
                high_price=row.get("high_price"),
                low_price=row.get("low_price"),
                close_price=row["close_price"],
                volume=row.get("volume"),
                source=row.get("source", "yfinance"),
            )
            for row in (result.data or [])
        ]

        return PriceHistoryResponse(
            ticker=ticker,
            period=period,
            data_points=data_points,
        )

    async def get_health_status(self) -> PriceHealthStatus:
        """Get price data quality summary."""
        # TODO (Phase 2): Implement real health check
        return PriceHealthStatus(
            total_tickers=0,
            fresh_count=0,
            stale_count=0,
            error_count=0,
            sources_used=[],
        )

    async def refresh_all(self) -> dict:
        """Force refresh all prices for the user's positions.

        TODO (Phase 2): Implement full refresh.
        """
        return {"status": "pending", "message": "Price refresh not yet implemented — Phase 2"}
