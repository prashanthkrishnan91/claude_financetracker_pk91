"""Watchlist router — authenticated CRUD over user-defined price criteria.

Product boundaries (consolidation contract):
- The app never automatically selects watchlist stocks.
- Watchlist tickers never enter the Paycheck Advisor candidate set.
- No alerts, email, push, or background workers — criteria are evaluated
  read-only at list time from the same batched price path Positions uses.
- Requires the additive migration ``v2/database/025_watchlist.sql``; until it
  is applied every endpoint returns 503 with ``watchlist_migration_required``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from ..config import get_settings
from ..database import get_supabase_client
from ..middleware.auth import AuthenticatedUser, get_current_user
from ..models.watchlist import (
    WatchlistItemCreate,
    WatchlistItemResponse,
    WatchlistItemUpdate,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/watchlist", tags=["watchlist"])

_TABLE = "watchlist_items"


def _make_price_service():
    from ..services.price_engine import PriceService

    settings = get_settings()
    return PriceService(
        finnhub_key=settings.finnhub_api_key or "",
        alpaca_key=settings.alpaca_api_key or "",
        alpaca_secret=settings.alpaca_secret_key or "",
        polygon_key=settings.polygon_api_key or "",
    )


def _migration_missing(exc: Exception) -> bool:
    text = str(exc).lower()
    return (
        "watchlist_items" in text
        and ("does not exist" in text or "not found" in text or "pgrst205" in text or "42p01" in text)
    )


def _migration_required_error() -> HTTPException:
    logger.error("watchlist_migration_missing table=%s", _TABLE)
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "error": "watchlist_migration_required",
            "message": (
                "The Watchlist table has not been created yet. Apply "
                "v2/database/025_watchlist.sql in the Supabase SQL editor, "
                "then retry."
            ),
        },
    )


def _criteria_met(criteria_type: str, threshold: float, price: float | None) -> bool | None:
    """True/False when a trusted price exists; None (unknown) otherwise."""
    if price is None:
        return None
    if criteria_type == "price_below":
        return price < threshold
    if criteria_type == "price_above":
        return price > threshold
    return None


def _row_to_response(row: dict, prices: dict[str, tuple[float, datetime]]) -> dict:
    ticker = row.get("ticker", "")
    price_entry = prices.get(ticker)
    current_price = price_entry[0] if price_entry else None
    price_as_of = price_entry[1] if price_entry else None
    return {
        "id": row["id"],
        "ticker": ticker,
        "criteria_type": row["criteria_type"],
        "threshold": float(row["threshold"]),
        "notes": row.get("notes"),
        "created_at": row["created_at"],
        "updated_at": row.get("updated_at"),
        "current_price": round(current_price, 4) if current_price is not None else None,
        "price_as_of": price_as_of,
        "criteria_met": _criteria_met(row["criteria_type"], float(row["threshold"]), current_price),
    }


@router.get("", response_model=list[WatchlistItemResponse])
async def list_watchlist(user: AuthenticatedUser = Depends(get_current_user)):
    """List the user's watchlist with batched current prices and criteria state."""
    client = get_supabase_client()
    try:
        result = (
            client.table(_TABLE)
            .select("*")
            .eq("user_id", str(user.id))
            .order("created_at")
            .execute()
        )
    except Exception as exc:  # table missing → operational truth, not a 500
        if _migration_missing(exc):
            raise _migration_required_error()
        raise
    rows = result.data or []

    # One batched price fetch for every distinct ticker (shared cache path with
    # Positions — no per-row provider calls).
    prices: dict[str, tuple[float, datetime]] = {}
    tickers = sorted({r["ticker"] for r in rows})
    if tickers:
        try:
            ps = _make_price_service()
            price_results = await ps.fetch_prices(tickers)
            for ticker, pr in price_results.items():
                if pr.is_valid:
                    prices[ticker] = (
                        pr.mid_price,
                        datetime.fromtimestamp(pr.timestamp, tz=timezone.utc),
                    )
        except Exception:
            # Degrade gracefully: entries render with criteria_met=None (unknown).
            logger.warning("watchlist_price_fetch_failed tickers=%d", len(tickers))

    return [_row_to_response(r, prices) for r in rows]


@router.post("", response_model=WatchlistItemResponse, status_code=status.HTTP_201_CREATED)
async def create_watchlist_item(
    payload: WatchlistItemCreate,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Add a ticker with one deterministic price criterion."""
    client = get_supabase_client()
    try:
        existing = (
            client.table(_TABLE)
            .select("id")
            .eq("user_id", str(user.id))
            .eq("ticker", payload.ticker)
            .eq("criteria_type", payload.criteria_type)
            .execute()
        )
    except Exception as exc:
        if _migration_missing(exc):
            raise _migration_required_error()
        raise
    if existing.data:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "duplicate_watchlist_entry",
                "message": (
                    f"{payload.ticker} already has a {payload.criteria_type.replace('_', ' ')} "
                    "entry. Edit that entry instead of adding a duplicate."
                ),
            },
        )

    insert = {
        "user_id": str(user.id),
        "ticker": payload.ticker,
        "criteria_type": payload.criteria_type,
        "threshold": payload.threshold,
        "notes": payload.notes,
    }
    result = client.table(_TABLE).insert(insert).execute()
    if not result.data:
        raise HTTPException(status_code=500, detail="Failed to create watchlist entry")
    row = result.data[0]
    logger.info("watchlist_created ticker=%s criteria=%s", payload.ticker, payload.criteria_type)
    return _row_to_response(row, {})


@router.patch("/{item_id}", response_model=WatchlistItemResponse)
async def update_watchlist_item(
    item_id: UUID,
    payload: WatchlistItemUpdate,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Edit an entry's criterion, threshold, or note (owner only)."""
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=422, detail="No fields to update")
    client = get_supabase_client()
    try:
        existing = (
            client.table(_TABLE)
            .select("*")
            .eq("id", str(item_id))
            .eq("user_id", str(user.id))
            .execute()
        )
    except Exception as exc:
        if _migration_missing(exc):
            raise _migration_required_error()
        raise
    if not existing.data:
        raise HTTPException(status_code=404, detail="Watchlist entry not found")
    row = existing.data[0]

    new_criteria = updates.get("criteria_type", row["criteria_type"])
    if new_criteria != row["criteria_type"]:
        dup = (
            client.table(_TABLE)
            .select("id")
            .eq("user_id", str(user.id))
            .eq("ticker", row["ticker"])
            .eq("criteria_type", new_criteria)
            .neq("id", str(item_id))
            .execute()
        )
        if dup.data:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "duplicate_watchlist_entry",
                    "message": (
                        f"{row['ticker']} already has a {new_criteria.replace('_', ' ')} entry."
                    ),
                },
            )

    updates["updated_at"] = datetime.now(timezone.utc).isoformat()
    result = (
        client.table(_TABLE)
        .update(updates)
        .eq("id", str(item_id))
        .eq("user_id", str(user.id))
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="Watchlist entry not found")
    logger.info("watchlist_updated id=%s fields=%s", item_id, sorted(updates))
    return _row_to_response(result.data[0], {})


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_watchlist_item(
    item_id: UUID,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Delete an entry (owner only)."""
    client = get_supabase_client()
    try:
        existing = (
            client.table(_TABLE)
            .select("id")
            .eq("id", str(item_id))
            .eq("user_id", str(user.id))
            .execute()
        )
    except Exception as exc:
        if _migration_missing(exc):
            raise _migration_required_error()
        raise
    if not existing.data:
        raise HTTPException(status_code=404, detail="Watchlist entry not found")
    client.table(_TABLE).delete().eq("id", str(item_id)).eq("user_id", str(user.id)).execute()
    logger.info("watchlist_deleted id=%s", item_id)
    return None
