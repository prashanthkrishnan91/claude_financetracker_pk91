"""Portfolio router — summaries, snapshots, targets, rebalancing."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..middleware.auth import AuthenticatedUser, get_current_user
from ..database import get_supabase_client
from ..models.portfolio import (
    PortfolioSummary,
    RebalanceResult,
    SnapshotResponse,
    TargetAllocationCreate,
    TargetAllocationResponse,
)
from ..services.portfolio_service import PortfolioService


class CashOverrideUpdate(BaseModel):
    """Body for PATCH /portfolio/cash — set or clear cash override."""
    amount: Optional[float] = None

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


@router.get("/summary", response_model=PortfolioSummary)
async def get_portfolio_summary(
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Get aggregated portfolio summary with live prices.

    This is the primary dashboard endpoint — combines positions
    with real-time prices to compute total equity, P&L, etc.
    """
    service = PortfolioService(user_id=user.id)
    return await service.get_summary()


@router.get("/snapshots", response_model=list[SnapshotResponse])
async def list_snapshots(
    limit: int = Query(default=50, le=500),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """List portfolio snapshots, newest first."""
    service = PortfolioService(user_id=user.id)
    return await service.list_snapshots(limit=limit)


@router.post("/snapshots", response_model=SnapshotResponse, status_code=201)
async def create_snapshot(
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Save a point-in-time portfolio snapshot (uses current live prices)."""
    service = PortfolioService(user_id=user.id)
    return await service.create_snapshot()


@router.get("/targets", response_model=list[TargetAllocationResponse])
async def list_targets(
    user: AuthenticatedUser = Depends(get_current_user),
):
    """List all target allocations for the current user."""
    service = PortfolioService(user_id=user.id)
    return await service.list_targets()


@router.put("/targets", response_model=list[TargetAllocationResponse])
async def set_targets(
    targets: list[TargetAllocationCreate],
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Set target allocations (upserts — replaces existing for same ticker)."""
    service = PortfolioService(user_id=user.id)
    return await service.set_targets(targets)


@router.get("/rebalance", response_model=list[RebalanceResult])
async def calculate_rebalance(
    cash_to_deploy: Optional[float] = Query(default=None, description="Extra cash to deploy"),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Calculate rebalance suggestions based on targets vs current allocation."""
    service = PortfolioService(user_id=user.id)
    return await service.calculate_rebalance(cash_to_deploy=cash_to_deploy)


@router.get("/cash")
async def get_cash_balance(
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict:
    """Get the current cash balance.

    Checks users.cash_override first (manual override).
    Falls back to the most recent successful Plaid sync.
    Returns source indicating where the value came from.
    """
    client = get_supabase_client()

    # Check for manual override in users table
    user_row = (
        client.table("users")
        .select("cash_override")
        .eq("id", str(user.id))
        .single()
        .execute()
    ).data

    manual_override: Optional[float] = None
    if user_row and user_row.get("cash_override") is not None:
        manual_override = float(user_row["cash_override"])
        return {
            "cash_balance": manual_override,
            "source": "manual",
            "manual_override": manual_override,
        }

    # Fall back to last Plaid sync
    last_sync = (
        client.table("plaid_sync_log")
        .select("cash_balance")
        .eq("user_id", str(user.id))
        .eq("status", "success")
        .order("synced_at", desc=True)
        .limit(1)
        .execute()
    ).data

    if last_sync and last_sync[0].get("cash_balance") is not None:
        cash = float(last_sync[0]["cash_balance"])
        return {
            "cash_balance": cash,
            "source": "plaid",
            "manual_override": None,
        }

    return {
        "cash_balance": 0.0,
        "source": "none",
        "manual_override": None,
    }


@router.patch("/cash")
async def update_cash_override(
    body: CashOverrideUpdate,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict:
    """Set or clear the manual cash override in users.cash_override.

    Pass amount=null to clear the override and revert to Plaid data.
    """
    client = get_supabase_client()

    client.table("users").update(
        {"cash_override": body.amount}
    ).eq("id", str(user.id)).execute()

    return {
        "cash_balance": body.amount,
        "source": "manual" if body.amount is not None else "none",
        "manual_override": body.amount,
    }
