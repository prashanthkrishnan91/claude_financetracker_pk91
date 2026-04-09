"""Sync router — Plaid sync, CSV import, price refresh."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File

from ..middleware.auth import AuthenticatedUser, get_current_user
from ..database import get_supabase_client
from ..models.transaction import TransactionImportResult
from ..services.plaid_service import PlaidSyncService
from ..services.crypto_service import decrypt_value

router = APIRouter(prefix="/sync", tags=["sync"])


@router.post("/plaid")
async def sync_plaid(
    force: bool = False,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Sync holdings from Plaid (Robinhood).

    Respects 24h TTL cache unless force=True.
    Updates positions table with authoritative share quantities.
    """
    client = get_supabase_client()
    service = PlaidSyncService(
        user_id=user.id,
        supabase_client=client,
        decrypt_fn=decrypt_value,
    )

    result = await service.sync_holdings(force=force)

    return {
        "status": result.status,
        "message": result.message,
        "holdings_count": result.holdings_count,
        "cash_balance": result.cash_balance,
        "positions_updated": result.positions_updated,
        "positions_created": result.positions_created,
        "synced_at": result.synced_at.isoformat() if result.synced_at else None,
        "duration_ms": result.duration_ms,
    }


@router.get("/plaid/status")
async def plaid_sync_status(
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Get Plaid sync status — last sync time, cache freshness."""
    client = get_supabase_client()
    service = PlaidSyncService(
        user_id=user.id,
        supabase_client=client,
        decrypt_fn=decrypt_value,
    )

    status = await service.get_sync_status()
    return {
        "status": "fresh" if status.is_fresh else ("stale" if status.synced_at else "never_synced"),
        "last_synced_at": status.synced_at.isoformat() if status.synced_at else None,
        "age_hours": round(status.age_hours, 1),
        "holdings_count": status.holdings_count,
        "cash_balance": status.cash_balance,
        "next_sync_in_hours": max(0, round(24 - status.age_hours, 1)) if status.synced_at else 0,
    }


@router.post("/csv/import", response_model=TransactionImportResult)
async def import_csv(
    file: UploadFile = File(..., description="Robinhood CSV export"),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Import transactions from a Robinhood CSV export.

    Uses SHA-256 canonical fingerprinting for dedup — safe to import
    the same file multiple times.
    """
    if not file.filename or not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="File must be a .csv")

    contents = await file.read()
    try:
        text = contents.decode("utf-8")
    except UnicodeDecodeError:
        text = contents.decode("latin-1")

    from ..services.import_service import CsvImportService
    client = get_supabase_client()
    service = CsvImportService(user_id=user.id, supabase_client=client)
    return await service.import_robinhood_csv(text)


@router.post("/prices/refresh")
async def refresh_prices(
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Force refresh all prices for the user's positions.

    Uses the v2 concurrent price engine — all sources fire simultaneously.
    """
    from ..config import get_settings
    from ..services.price_engine import PriceService
    from ..services.crypto_service import decrypt_value

    client = get_supabase_client()
    settings = get_settings()

    # Get all position tickers
    positions = (
        client.table("positions")
        .select("ticker")
        .eq("user_id", str(user.id))
        .execute()
    ).data

    if not positions:
        return {"status": "ok", "message": "No positions to refresh", "count": 0}

    tickers = [p["ticker"] for p in positions]

    # Build price service with user's keys
    from .prices import _get_price_service
    service = _get_price_service(user)

    try:
        results = await service.fetch_prices(tickers)

        fresh = sum(1 for r in results.values() if r.is_valid and not r.is_stale)
        stale = sum(1 for r in results.values() if r.is_stale)
        errors = sum(1 for r in results.values() if not r.is_valid)
        sources = set(r.source.split("(")[0] for r in results.values() if r.is_valid)

        return {
            "status": "ok",
            "total": len(tickers),
            "fresh": fresh,
            "stale": stale,
            "errors": errors,
            "sources_used": sorted(sources),
        }
    finally:
        await service.close()
