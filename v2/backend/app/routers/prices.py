"""Prices router — real-time quotes and historical data."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ..middleware.auth import AuthenticatedUser, get_current_user
from ..models.price import (
    BatchPriceRequest,
    BatchPriceResponse,
    PriceHealthStatus,
    PriceHistoryResponse,
    PriceQuote,
)
from ..services.price_service import PriceService

router = APIRouter(prefix="/prices", tags=["prices"])


@router.get("/{ticker}", response_model=PriceQuote)
async def get_price(
    ticker: str,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Get the real-time price for a single ticker."""
    service = PriceService(user_id=user.id)
    return await service.get_quote(ticker.upper())


@router.post("/batch", response_model=BatchPriceResponse)
async def get_batch_prices(
    request: BatchPriceRequest,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Get real-time prices for multiple tickers in a single call.

    Optimal for dashboard load — fetches all portfolio prices at once.
    Uses async gather to call Alpaca/Finnhub/CoinGecko in parallel.
    """
    service = PriceService(user_id=user.id)
    return await service.get_batch_quotes(request.tickers)


@router.get("/{ticker}/history", response_model=PriceHistoryResponse)
async def get_price_history(
    ticker: str,
    period: str = Query(default="1Y", pattern="^(1W|1M|3M|6M|1Y|5Y)$"),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Get historical OHLCV data for charting.

    Data sourced from yfinance/Alpaca and cached in Supabase.
    """
    service = PriceService(user_id=user.id)
    return await service.get_history(ticker.upper(), period)


@router.get("/health/status", response_model=PriceHealthStatus)
async def price_health(
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Get price data health status — how many tickers have fresh/stale prices."""
    service = PriceService(user_id=user.id)
    return await service.get_health_status()
