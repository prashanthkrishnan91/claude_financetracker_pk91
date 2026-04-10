"""Sync router — Plaid sync, CSV import, PDF import, price refresh."""

from __future__ import annotations

import io
import re

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File

from ..middleware.auth import AuthenticatedUser, get_current_user
from ..database import get_supabase_client
from ..models.transaction import TransactionImportResult
from ..services.plaid_service import PlaidSyncService
from ..services.crypto_service import decrypt_value

_CRYPTO_NAMES: dict[str, str] = {
    "BTC": "Bitcoin",
    "ETH": "Ethereum",
    "XRP": "XRP",
    "SOL": "Solana",
    "DOGE": "Dogecoin",
    "ADA": "Cardano",
    "LTC": "Litecoin",
    "AVAX": "Avalanche",
}


def _parse_crypto_pdf(file_bytes: bytes) -> dict[str, dict]:
    """Parse a Robinhood Crypto monthly statement PDF.

    Statement table format:
      "Bitcoin  0.03432981  BTC  $2301.45  99.94%"
      "XRP      1.066       XRP  $1.47     0.06%"

    Returns {ticker: {shares, avg_cost}} where avg_cost is 0.0 (not in PDF).
    Ported from v1/data_engine.py parse_crypto_pdf().
    """
    overrides: dict[str, dict] = {}
    try:
        import pdfplumber  # type: ignore
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    except Exception:
        return overrides

    # Primary pattern: named coin + qty + ticker symbol + dollar value
    primary = re.compile(
        r"(?:Bitcoin|Ethereum|XRP|Ripple|Solana|Dogecoin|Cardano|Litecoin|Avalanche)"
        r"\s+([\d]+\.[\d]+)"
        r"\s+(BTC|ETH|XRP|SOL|DOGE|ADA|LTC|AVAX)"
        r"\s+\$[\d,]+\.[\d]+",
        re.IGNORECASE,
    )
    for m in primary.finditer(text):
        try:
            qty = float(m.group(1).replace(",", ""))
            ticker = m.group(2).upper()
            if qty > 0:
                overrides[ticker] = {"shares": qty, "avg_cost": 0.0}
        except Exception:
            pass

    # Fallback: "0.03432981 BTC" (>=4 decimal places identifies crypto qty)
    if not overrides:
        fallback = re.compile(
            r"([\d]+\.[\d]{4,})\s+(BTC|ETH|XRP|SOL|DOGE|ADA|LTC|AVAX)\b",
            re.IGNORECASE,
        )
        for m in fallback.finditer(text):
            try:
                qty = float(m.group(1))
                ticker = m.group(2).upper()
                if qty > 0 and ticker not in overrides:
                    overrides[ticker] = {"shares": qty, "avg_cost": 0.0}
            except Exception:
                pass

    return overrides

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


@router.post("/pdf/import")
async def import_crypto_pdf(
    file: UploadFile = File(..., description="Robinhood Crypto PDF monthly statement"),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Import crypto positions from a Robinhood Crypto PDF monthly statement.

    Parses BTC, ETH, XRP, SOL etc. from the holdings table in the statement PDF
    and upserts them into the positions table with category='Crypto'.
    avg_cost is set to 0.0 (not available in the statement — update manually).
    """
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="File must be a .pdf")

    contents = await file.read()
    overrides = _parse_crypto_pdf(contents)

    if not overrides:
        raise HTTPException(
            status_code=422,
            detail=(
                "No crypto positions found in PDF. "
                "Ensure this is a Robinhood Crypto monthly statement."
            ),
        )

    client = get_supabase_client()
    upserted = 0
    created = 0
    errors: list[str] = []

    for ticker, data in overrides.items():
        try:
            existing = (
                client.table("positions")
                .select("id")
                .eq("user_id", str(user.id))
                .eq("ticker", ticker)
                .execute()
            ).data

            if existing:
                client.table("positions").update({
                    "shares": data["shares"],
                    "source": "pdf_import",
                }).eq("id", existing[0]["id"]).execute()
                upserted += 1
            else:
                client.table("positions").insert({
                    "user_id": str(user.id),
                    "ticker": ticker,
                    "name": _CRYPTO_NAMES.get(ticker, ticker),
                    "category": "Crypto",
                    "shares": data["shares"],
                    "avg_cost": data.get("avg_cost", 0.0),
                    "source": "pdf_import",
                    "lt_eligible": False,
                }).execute()
                created += 1
        except Exception as exc:
            errors.append(f"{ticker}: {exc}")

    return {
        "tickers_found": list(overrides.keys()),
        "positions_updated": upserted,
        "positions_created": created,
        "errors": errors,
    }
