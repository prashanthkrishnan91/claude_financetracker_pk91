"""Deposit plan models — biweekly deployment schedule."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class DepositPlanBase(BaseModel):
    """Shared deposit plan fields."""
    deposit_date: date
    amount: Decimal = Field(gt=0, decimal_places=2)
    allocation: dict[str, float] = Field(default_factory=dict)  # {"NVDA": 252.00, ...}
    rotating_pick: Optional[str] = None


class DepositPlanCreate(DepositPlanBase):
    """Create a new deposit plan."""
    pass


class DepositPlanResponse(DepositPlanBase):
    """Deposit plan returned by API."""
    id: UUID
    user_id: UUID
    executed: bool
    executed_at: Optional[datetime] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class DepositPlanExecute(BaseModel):
    """Mark a deposit plan as executed."""
    executed_at: Optional[datetime] = None  # Defaults to now


class DepositSchedule(BaseModel):
    """Full deposit schedule with upcoming dates and rotation."""
    upcoming: list[DepositPlanResponse]
    executed: list[DepositPlanResponse]
    next_deposit_date: Optional[date] = None
    next_rotating_pick: Optional[str] = None
    total_deployed_ytd: float
    total_remaining_ytd: float


class DepositAllocationFormula(BaseModel):
    """The fixed allocation formula for deposits."""
    amount: float
    breakdown: dict[str, float]  # {"NVDA": 0.28, "VOO": 0.22, ...}
    rotating_pct: float = 0.16
    rotation_order: list[str]
