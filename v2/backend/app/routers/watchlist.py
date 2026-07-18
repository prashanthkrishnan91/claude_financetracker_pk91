"""Watchlist router — user-defined candidate tickers with user-defined criteria.

The app surfaces candidates whose criteria are met; it never picks stocks.
Evaluation is a deterministic price comparison; a missing live price yields
criteria_met=None (honestly unknown), never a fabricated flag.
"""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from ..config import get_settings
from ..database import get_supabase_client
from ..middleware.auth import AuthenticatedUser, get_current_user
from ..models.watchlist import WatchlistItemCreate, WatchlistItemResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/watchlist", tags=["watchlist"])


def _make_price_service():
    from ..services.price_engine import PriceService

    settings = get_settings()
    return PriceService(
        finnhub_key=settings.finnhub_api_key or "",
        alpaca_key=settings.alpaca_api_key or "",
        alpaca_secret=settings.alpaca_secret_key or "",
        polygon_key=settings.polygon_api_key or "",
    )


def _evaluate(criteria_type: str, threshold: float, price: float | None) -> bool | None:
    if price is None:
        return None
    if criteria_type == "price_below":
        return price <= threshold
    if criteria_type == "price_above":
        return price >= threshold
    return None


@router.get("", response_model=list[WatchlistItemResponse])
async def list_watchlist(
    user: AuthenticatedUser = Depends(get_current_user),
):
    """List watchlist entries with live criteria evaluation."""
    client = get_supabase_client()
    result = (
        client.table("watchlist_items")
        .select("*")
        .eq("user_id", str(user.id))
        .order("created_at")
        .execute()
    )
    rows = result.data or []
    if not rows:
        return []

    prices: dict[str, float] = {}
    try:
        ps = _make_price_service()
        price_results = await ps.fetch_prices(sorted({r["ticker"] for r in rows}))
        for t, pr in price_results.items():
            if pr.is_valid:
                prices[t] = pr.mid_price
    except Exception:
        pass  # Degrade gracefully — entries evaluate to criteria_met=None

    out = []
    for r in rows:
        price = prices.get(r["ticker"])
        out.append({
            **r,
            "current_price": round(price, 4) if price is not None else None,
            "criteria_met": _evaluate(r["criteria_type"], float(r["threshold"]), price),
        })
    return out


@router.post("", response_model=WatchlistItemResponse, status_code=status.HTTP_201_CREATED)
async def create_watchlist_item(
    item: WatchlistItemCreate,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Add a user-defined ticker + criterion to the watchlist."""
    client = get_supabase_client()
    row = {
        "user_id": str(user.id),
        "ticker": item.ticker.strip().upper(),
        "criteria_type": item.criteria_type,
        "threshold": item.threshold,
        "notes": item.notes,
    }
    result = client.table("watchlist_items").insert(row).execute()
    if not result.data:
        raise HTTPException(status_code=500, detail="Failed to create watchlist item")
    created = result.data[0]
    return {**created, "current_price": None, "criteria_met": None}


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_watchlist_item(
    item_id: UUID,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Remove a watchlist entry."""
    client = get_supabase_client()
    result = (
        client.table("watchlist_items")
        .delete()
        .eq("user_id", str(user.id))
        .eq("id", str(item_id))
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="Watchlist item not found")
    return None
