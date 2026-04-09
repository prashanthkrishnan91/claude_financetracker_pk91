"""Portfolio models — snapshots, aggregates, targets."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field


# ── Portfolio Snapshot ────────────────────────────────────────────────────────

class SnapshotCreate(BaseModel):
    """Create a new portfolio snapshot."""
    total_equity: Decimal
    total_cost: Decimal
    total_pnl: Decimal
    total_pnl_pct: Optional[Decimal] = None
    cash_balance: Decimal = Decimal("0")
    positions_data: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SnapshotResponse(BaseModel):
    """Portfolio snapshot returned by API."""
    id: UUID
    user_id: UUID
    snapshot_at: datetime
    total_equity: float
    total_cost: float
    total_pnl: float
    total_pnl_pct: Optional[float] = None
    cash_balance: float
    positions_data: list[dict[str, Any]]
    metadata: dict[str, Any]
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Portfolio Summary (computed, not stored) ──────────────────────────────────

class PortfolioSummary(BaseModel):
    """Aggregated portfolio summary with live prices."""
    total_equity: float
    total_cost: float
    total_pnl: float
    total_pnl_pct: float
    cash_balance: float
    day_change: float
    day_change_pct: float

    # Breakdowns
    stocks_value: float
    etfs_value: float
    crypto_value: float
    positions_count: int

    # Data quality
    prices_fresh: int
    prices_stale: int
    last_price_fetch: Optional[datetime] = None
    last_plaid_sync: Optional[datetime] = None


# ── Target Allocations ────────────────────────────────────────────────────────

class TargetAllocationBase(BaseModel):
    """Target allocation for a single ticker."""
    ticker: str
    target_pct: Decimal = Field(ge=0, le=100, decimal_places=4)


class TargetAllocationCreate(TargetAllocationBase):
    """Create or update a target allocation."""
    pass


class TargetAllocationResponse(TargetAllocationBase):
    """Target allocation returned by API."""
    id: UUID
    user_id: UUID
    updated_at: datetime

    model_config = {"from_attributes": True}


class RebalanceResult(BaseModel):
    """Result of a rebalance calculation."""
    ticker: str
    current_pct: float
    target_pct: float
    drift_pct: float
    suggested_action: str  # "BUY $X" or "SELL $X" or "ON TARGET"
    suggested_amount: float
