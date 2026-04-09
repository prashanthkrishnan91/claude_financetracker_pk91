"""Prices router — real-time quotes and historical data.

Uses the v2 concurrent price engine (all sources fire simultaneously).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ..middleware.auth import AuthenticatedUser, get_current_user
from ..config import get_settings
from ..database import get_supabase_client
from ..models.price import (
    BatchPriceRequest,
    BatchPriceResponse,
    PriceHealthStatus,
    PriceHistoryResponse,
    PriceHistoryPoint,
    PriceQuote,
)
from ..services.price_engine import PriceService
from ..services.history_service import HistoryService
from ..services.crypto_service import decrypt_value

router = APIRouter(prefix="/prices", tags=["prices"])


def _get_price_service(user: AuthenticatedUser) -> PriceService:
    """Build a PriceService with the user's API keys."""
    settings = get_settings()
    client = get_supabase_client()

    # Load user's encrypted keys
    user_row = (
        client.table("users")
        .select("encrypted_finnhub_api_key, encrypted_polygon_api_key, "
                "encrypted_alpaca_api_key, encrypted_alpaca_secret_key")
        .eq("id", str(user.id))
        .single()
        .execute()
    )

    finnhub_key = ""
    polygon_key = ""
    alpaca_key = ""
    alpaca_secret = ""

    if user_row.data:
        data = user_row.data
        try:
            if data.get("encrypted_finnhub_api_key"):
                finnhub_key = decrypt_value(data["encrypted_finnhub_api_key"])
        except Exception:
            pass
        try:
            if data.get("encrypted_polygon_api_key"):
                polygon_key = decrypt_value(data["encrypted_polygon_api_key"])
        except Exception:
            pass
        try:
            if data.get("encrypted_alpaca_api_key"):
                alpaca_key = decrypt_value(data["encrypted_alpaca_api_key"])
        except Exception:
            pass
        try:
            if data.get("encrypted_alpaca_secret_key"):
                alpaca_secret = decrypt_value(data["encrypted_alpaca_secret_key"])
        except Exception:
            pass

    # Fall back to env-level keys for development
    return PriceService(
        finnhub_key=finnhub_key or settings.finnhub_api_key or "",
        polygon_key=polygon_key or settings.polygon_api_key or "",
        alpaca_key=alpaca_key or settings.alpaca_api_key or "",
        alpaca_secret=alpaca_secret or settings.alpaca_secret_key or "",
    )


@router.get("/{ticker}", response_model=PriceQuote)
async def get_price(
    ticker: str,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Get real-time price for a single ticker.

    Uses concurrent multi-source fetching — all available APIs
    are queried simultaneously. First valid result wins.
    """
    service = _get_price_service(user)
    try:
        result = await service.fetch_one(ticker.upper())
        return PriceQuote(
            ticker=result.ticker,
            mid_price=result.mid_price,
            bid=result.bid,
            ask=result.ask,
            last_trade=result.last_trade,
            source=result.source,
            timestamp=result.timestamp,
            error=result.error,
        )
    finally:
        await service.close()


@router.post("/batch", response_model=BatchPriceResponse)
async def get_batch_prices(
    request: BatchPriceRequest,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Get real-time prices for multiple tickers in a single call.

    Optimal for dashboard load — all tickers fetched concurrently,
    each racing multiple sources. No sequential fallback chains.
    """
    service = _get_price_service(user)
    try:
        results = await service.fetch_prices(request.tickers)

        prices = {}
        fresh = 0
        stale = 0
        errors = 0
        sources_used = set()

        for ticker, result in results.items():
            prices[ticker] = PriceQuote(
                ticker=result.ticker,
                mid_price=result.mid_price,
                bid=result.bid,
                ask=result.ask,
                last_trade=result.last_trade,
                source=result.source,
                timestamp=result.timestamp,
                error=result.error,
            )
            if result.is_valid and not result.is_stale:
                fresh += 1
                sources_used.add(result.source.split("(")[0])
            elif result.is_stale:
                stale += 1
            else:
                errors += 1

        health = PriceHealthStatus(
            total_tickers=len(request.tickers),
            fresh_count=fresh,
            stale_count=stale,
            error_count=errors,
            sources_used=sorted(sources_used),
        )

        return BatchPriceResponse(prices=prices, health=health)
    finally:
        await service.close()


@router.get("/{ticker}/history", response_model=PriceHistoryResponse)
async def get_price_history(
    ticker: str,
    period: str = Query(default="1Y", pattern="^(1W|1M|3M|6M|1Y|5Y)$"),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Get historical OHLCV data for charting.

    Data sourced from yfinance and cached in Supabase price_history table.
    Cache is auto-refreshed when stale.
    """
    supabase = get_supabase_client()
    service = HistoryService(supabase_client=supabase)

    try:
        points = await service.get_history(ticker.upper(), period)

        return PriceHistoryResponse(
            ticker=ticker.upper(),
            period=period,
            data_points=[
                PriceHistoryPoint(
                    price_date=p.date,
                    open_price=p.open,
                    high_price=p.high,
                    low_price=p.low,
                    close_price=p.close,
                    volume=p.volume,
                    source="yfinance",
                )
                for p in points
            ],
        )
    finally:
        await service.close()


@router.get("/health/status", response_model=PriceHealthStatus)
async def price_health(
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Get price source health status — circuit breaker states."""
    service = _get_price_service(user)
    try:
        health_data = service.get_health()

        open_count = sum(1 for v in health_data.values() if "open" in v["status"])
        healthy_count = len(health_data) - open_count

        return PriceHealthStatus(
            total_tickers=0,
            fresh_count=healthy_count,
            stale_count=0,
            error_count=open_count,
            sources_used=[name for name, v in health_data.items() if "healthy" in v["status"]],
        )
    finally:
        await service.close()
