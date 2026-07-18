"""Watchlist models — user-defined candidate tickers + criteria."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field

CriteriaType = Literal["price_below", "price_above"]


class WatchlistItemCreate(BaseModel):
    """Create a watchlist entry. The user defines both ticker and criterion."""
    ticker: str = Field(min_length=1, max_length=12)
    criteria_type: CriteriaType
    threshold: float = Field(gt=0)
    notes: Optional[str] = Field(default=None, max_length=500)


class WatchlistItemResponse(BaseModel):
    """Watchlist entry with live evaluation state."""
    id: UUID
    ticker: str
    criteria_type: CriteriaType
    threshold: float
    notes: Optional[str] = None
    created_at: datetime
    # Evaluation (None when no live price is available — never fabricated)
    current_price: Optional[float] = None
    criteria_met: Optional[bool] = None
