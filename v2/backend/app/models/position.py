"""Position models — holdings, cost basis, DRIP, tax status."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class PositionBase(BaseModel):
    """Shared position fields."""
    ticker: str = Field(max_length=10)
    name: str
    category: str = Field(pattern="^(Crypto|Core|ETF|Other|IPO|SELL)$")

    shares: Decimal = Field(default=Decimal("0"), decimal_places=6)
    avg_cost: Decimal = Field(default=Decimal("0"), decimal_places=6)

    # DRIP
    drip_shares: Decimal = Field(default=Decimal("0"), decimal_places=6)
    drip_cost: Decimal = Field(default=Decimal("0"), decimal_places=6)
    divs_received: Decimal = Field(default=Decimal("0"), decimal_places=6)

    # Analyst targets
    target_price: Optional[Decimal] = None
    bear_price: Optional[Decimal] = None
    bull_price: Optional[Decimal] = None

    # Tax
    lt_eligible: bool = False
    lt_date: Optional[date] = None

    # Crypto
    coingecko_id: Optional[str] = None


class PositionCreate(PositionBase):
    """Create a new position."""
    source: str = Field(default="manual", pattern="^(manual|plaid|csv_import|bootstrap)$")


class PositionUpdate(BaseModel):
    """Partial update for an existing position."""
    name: Optional[str] = None
    category: Optional[str] = Field(default=None, pattern="^(Crypto|Core|ETF|Other|IPO|SELL)$")
    shares: Optional[Decimal] = None
    avg_cost: Optional[Decimal] = None
    drip_shares: Optional[Decimal] = None
    drip_cost: Optional[Decimal] = None
    divs_received: Optional[Decimal] = None
    target_price: Optional[Decimal] = None
    bear_price: Optional[Decimal] = None
    bull_price: Optional[Decimal] = None
    lt_eligible: Optional[bool] = None
    lt_date: Optional[date] = None
    coingecko_id: Optional[str] = None


class PositionResponse(PositionBase):
    """Position data returned by API (includes computed fields)."""
    id: UUID
    user_id: UUID
    source: str
    last_synced_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PositionWithPrice(PositionResponse):
    """Position enriched with live price data."""
    current_price: Optional[float] = None
    market_value: Optional[float] = None
    unrealised_pnl: Optional[float] = None
    unrealised_pnl_pct: Optional[float] = None
    price_source: Optional[str] = None
    day_change: Optional[float] = None
    day_change_pct: Optional[float] = None
