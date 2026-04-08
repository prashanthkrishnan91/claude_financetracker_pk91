"""Portfolio router — summaries, snapshots, targets, rebalancing."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..middleware.auth import AuthenticatedUser, get_current_user
from ..models.portfolio import (
    PortfolioSummary,
    RebalanceResult,
    SnapshotResponse,
    TargetAllocationCreate,
    TargetAllocationResponse,
)
from ..services.portfolio_service import PortfolioService

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
    limit: int = Query(default=50, le=200),
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
