"""Price models — real-time quotes and historical OHLCV."""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field


class PriceQuote(BaseModel):
    """Real-time price quote for a single ticker."""
    ticker: str
    mid_price: float
    bid: Optional[float] = None
    ask: Optional[float] = None
    last_trade: float
    source: str         # finnhub, alpaca, polygon, coingecko, cache
    timestamp: float    # Unix epoch
    error: Optional[str] = None

    @property
    def is_valid(self) -> bool:
        return self.mid_price > 0 and self.error is None


class PriceHistoryPoint(BaseModel):
    """Single OHLCV data point."""
    price_date: date
    open_price: Optional[float] = None
    high_price: Optional[float] = None
    low_price: Optional[float] = None
    close_price: float
    volume: Optional[int] = None
    source: str = "yfinance"


class PriceHistoryResponse(BaseModel):
    """Historical price data for charting."""
    ticker: str
    period: str         # "1Y", "6M", "3M", "1M", "1W"
    data_points: list[PriceHistoryPoint]
    last_updated: Optional[datetime] = None


class PriceHealthStatus(BaseModel):
    """Price data quality summary."""
    total_tickers: int
    fresh_count: int    # Fetched within last 5 minutes
    stale_count: int    # Older than 5 minutes
    error_count: int    # Failed to fetch
    last_fetch_at: Optional[datetime] = None
    sources_used: list[str] = Field(default_factory=list)


class BatchPriceRequest(BaseModel):
    """Request prices for multiple tickers."""
    tickers: list[str] = Field(min_length=1, max_length=100)


class BatchPriceResponse(BaseModel):
    """Batch price response."""
    prices: dict[str, PriceQuote]
    health: PriceHealthStatus
