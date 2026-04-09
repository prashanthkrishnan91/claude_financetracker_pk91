"""Positions router — CRUD for holdings."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..middleware.auth import AuthenticatedUser, get_current_user
from ..database import get_supabase_client
from ..models.position import (
    PositionCreate,
    PositionResponse,
    PositionUpdate,
    PositionWithPrice,
)

router = APIRouter(prefix="/positions", tags=["positions"])


@router.get("/", response_model=list[PositionWithPrice])
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

    # TODO (Phase 2): Enrich with live prices from price service
    return result.data


@router.get("/{ticker}", response_model=PositionWithPrice)
async def get_position(
    ticker: str,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Get a single position by ticker."""
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

    return result.data


@router.post("/", response_model=PositionResponse, status_code=status.HTTP_201_CREATED)
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


@router.post("/seed-v1", response_model=list[PositionResponse], status_code=status.HTTP_201_CREATED)
async def seed_from_v1(
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Seed positions from v1 bootstrap data (data/portfolio.py).

    This is a one-time migration endpoint for existing v1 users.
    Skips tickers that already exist.
    """
    from ..services.migration_service import seed_v1_positions

    return await seed_v1_positions(user_id=user.id)
