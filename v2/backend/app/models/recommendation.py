"""Recommendation models — Buy/Sell/Trim/Hold engine output."""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class RecommendationBase(BaseModel):
    """Shared recommendation fields."""
    ticker: str
    action: str = Field(pattern="^(BUY|SELL|TRIM|HOLD|REVIEW)$")
    detail: str
    rationale: Optional[str] = None
    urgency: int = Field(default=0, ge=0, le=4)
    tax_note: Optional[str] = None
    drip_note: Optional[str] = None


class RecommendationResponse(RecommendationBase):
    """Recommendation returned by API."""
    id: UUID
    user_id: UUID
    is_active: bool
    created_at: datetime
    resolved_at: Optional[datetime] = None
    resolution: Optional[str] = None

    model_config = {"from_attributes": True}


class RecommendationResolve(BaseModel):
    """Resolve (accept/reject/defer) a recommendation."""
    resolution: str = Field(pattern="^(accepted|rejected|deferred|expired)$")
    notes: Optional[str] = None


class InsightCard(BaseModel):
    """Frontend-ready insight card with all display data."""
    id: UUID
    ticker: str
    name: str
    action: str
    detail: str
    rationale: str
    urgency: int
    color: str          # CSS color key: green/red/gold/blue/purple/orange/gray
    tax_note: str
    drip_note: str
    current_price: Optional[float] = None
    pnl_pct: Optional[float] = None
    category: str       # Core, ETF, Crypto, etc.


class DecisionLogEntry(BaseModel):
    """Decision log entry returned by API."""
    id: UUID
    recommendation_id: Optional[UUID] = None
    ticker: str
    decision: str
    notes: Optional[str] = None
    price_at_decision: Optional[float] = None
    shares_at_decision: Optional[float] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class DecisionLogCreate(BaseModel):
    """Create a decision log entry."""
    recommendation_id: Optional[UUID] = None
    ticker: str
    decision: str = Field(pattern="^(accepted|rejected|modified|deferred)$")
    notes: Optional[str] = None
    price_at_decision: Optional[float] = None
    shares_at_decision: Optional[float] = None
