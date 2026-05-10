"""Positions router — CRUD for holdings."""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..middleware.auth import AuthenticatedUser, get_current_user
from ..config import get_settings
from ..database import get_supabase_client
from ..models.position import (
    PositionCreate,
    PositionResponse,
    PositionUpdate,
    PositionWithPrice,
)

router = APIRouter(prefix="/positions", tags=["positions"])


def _make_price_service():
    """Instantiate PriceService from current settings."""
    from ..services.price_engine import PriceService
    settings = get_settings()
    return PriceService(
        finnhub_key=settings.finnhub_api_key or "",
        alpaca_key=settings.alpaca_api_key or "",
        alpaca_secret=settings.alpaca_secret_key or "",
        polygon_key=settings.polygon_api_key or "",
    )


def _enrich_position(row: dict, prices: dict[str, float]) -> dict:
    """Add current_price, market_value, unrealised_pnl, unrealised_pnl_pct to a position row."""
    ticker = row.get("ticker", "")
    shares = float(row.get("shares") or 0)
    avg_cost = float(row.get("avg_cost") or 0)
    cost_basis = shares * avg_cost

    current_price = prices.get(ticker)
    if current_price is not None:
        market_value = shares * current_price
        unrealised_pnl = market_value - cost_basis
        unrealised_pnl_pct = (
            (unrealised_pnl / cost_basis * 100) if cost_basis > 0 else 0.0
        )
        enriched = {
            **row,
            "current_price": round(current_price, 4),
            "market_value": round(market_value, 2),
            "unrealised_pnl": round(unrealised_pnl, 2),
            "unrealised_pnl_pct": round(unrealised_pnl_pct, 4),
        }
    else:
        enriched = {
            **row,
            "current_price": None,
            "market_value": None,
            "unrealised_pnl": None,
            "unrealised_pnl_pct": None,
        }
    return enriched


@router.get("", response_model=list[PositionWithPrice])
async def list_positions(
    category: str | None = Query(default=None, description="Filter by category"),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """List all positions with live prices for the current user."""
    client = get_supabase_client()

    query = client.table("positions").select("*").eq("user_id", str(user.id))
    if category:
        query = query.eq("category", category)

    result = query.order("ticker").execute()
    rows = result.data or []

    if not rows:
        return []

    # Fetch live prices for all tickers
    tickers = [r["ticker"] for r in rows]
    prices: dict[str, float] = {}
    try:
        ps = _make_price_service()
        price_results = await ps.fetch_prices(tickers)
        for ticker, pr in price_results.items():
            if pr.is_valid:
                prices[ticker] = pr.mid_price
    except Exception:
        pass  # Degrade gracefully — return positions without live prices

    return [_enrich_position(r, prices) for r in rows]


@router.get("/{ticker}", response_model=PositionWithPrice)
async def get_position(
    ticker: str,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Get a single position by ticker with live price."""
    client = get_supabase_client()

    result = (
        client.table("positions")
        .select("*")
        .eq("user_id", str(user.id))
        .eq("ticker", ticker.upper())
        .single()
        .execute()
    )

    if not result.data:
        raise HTTPException(status_code=404, detail=f"Position {ticker} not found")

    row = result.data
    prices: dict[str, float] = {}
    try:
        ps = _make_price_service()
        price_results = await ps.fetch_prices([ticker.upper()])
        for t, pr in price_results.items():
            if pr.is_valid:
                prices[t] = pr.mid_price
    except Exception:
        pass

    return _enrich_position(row, prices)


@router.post("", response_model=PositionResponse, status_code=status.HTTP_201_CREATED)
async def create_position(
    position: PositionCreate,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Create a new position."""
    client = get_supabase_client()

    data = position.model_dump(mode="json")
    data["user_id"] = str(user.id)
    data["ticker"] = data["ticker"].upper()

    try:
        result = client.table("positions").insert(data).execute()
    except Exception as e:
        if "unique" in str(e).lower():
            raise HTTPException(status_code=409, detail=f"Position {data['ticker']} already exists")
        raise

    return result.data[0]


@router.patch("/{ticker}", response_model=PositionResponse)
async def update_position(
    ticker: str,
    updates: PositionUpdate,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Update an existing position."""
    client = get_supabase_client()

    update_data = updates.model_dump(exclude_unset=True, mode="json")
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")

    result = (
        client.table("positions")
        .update(update_data)
        .eq("user_id", str(user.id))
        .eq("ticker", ticker.upper())
        .execute()
    )

    if not result.data:
        raise HTTPException(status_code=404, detail=f"Position {ticker} not found")

    return result.data[0]


@router.delete("/{ticker}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_position(
    ticker: str,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Delete a position."""
    client = get_supabase_client()

    result = (
        client.table("positions")
        .delete()
        .eq("user_id", str(user.id))
        .eq("ticker", ticker.upper())
        .execute()
    )

    if not result.data:
        raise HTTPException(status_code=404, detail=f"Position {ticker} not found")
