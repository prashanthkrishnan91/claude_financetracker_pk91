"""Sync router — Plaid sync, CSV import, data refresh."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File

from ..middleware.auth import AuthenticatedUser, get_current_user
from ..models.transaction import TransactionImportResult
from ..services.plaid_service import PlaidService

router = APIRouter(prefix="/sync", tags=["sync"])


@router.post("/plaid")
async def sync_plaid(
    force: bool = False,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Sync holdings from Plaid (Robinhood).

    Respects 24h TTL cache unless force=True.
    Updates positions table with authoritative share quantities from Plaid.
    """
    service = PlaidService(user_id=user.id)

    if not force:
        last_sync = await service.get_last_sync()
        if last_sync and last_sync.is_fresh:
            return {
                "status": "cached",
                "message": f"Synced {last_sync.age_hours:.1f}h ago — use force=true to re-sync",
                "last_synced_at": last_sync.synced_at,
                "holdings_count": last_sync.holdings_count,
            }

    result = await service.sync_holdings()
    return result


@router.get("/plaid/status")
async def plaid_sync_status(
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Get Plaid sync status — last sync time, cache freshness."""
    service = PlaidService(user_id=user.id)
    return await service.get_sync_status()


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
    service = CsvImportService(user_id=user.id)
    return await service.import_robinhood_csv(text)


@router.post("/prices/refresh")
async def refresh_prices(
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Force refresh all prices for the user's positions.

    Fetches from Alpaca/Finnhub/CoinGecko and updates the price cache.
    """
    from ..services.price_service import PriceService
    service = PriceService(user_id=user.id)
    return await service.refresh_all()
