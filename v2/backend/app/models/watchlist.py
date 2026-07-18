"""Watchlist models — user-defined tickers with deterministic price criteria."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

# Conservative ticker shape: letters/digits, optional single dot or dash
# separator (BRK.B, BTC-USD). Uppercased by validation.
_TICKER_RE = re.compile(r"^[A-Z0-9]{1,10}([.-][A-Z0-9]{1,6})?$")

CriteriaType = Literal["price_below", "price_above"]


def normalize_and_validate_ticker(raw: str) -> str:
    ticker = (raw or "").strip().upper()
    if not _TICKER_RE.match(ticker):
        raise ValueError(
            "Ticker must be 1-10 letters/digits, optionally with one '.' or '-' "
            "separator (examples: VTI, BRK.B, BTC-USD)."
        )
    return ticker


class WatchlistItemCreate(BaseModel):
    ticker: str = Field(..., description="Ticker symbol to watch")
    criteria_type: CriteriaType
    threshold: float = Field(..., gt=0, le=10_000_000, description="Price threshold in USD")
    notes: Optional[str] = Field(default=None, max_length=500)

    @field_validator("ticker")
    @classmethod
    def _ticker(cls, v: str) -> str:
        return normalize_and_validate_ticker(v)

    @field_validator("notes")
    @classmethod
    def _notes(cls, v: Optional[str]) -> Optional[str]:
        v = (v or "").strip()
        return v or None


class WatchlistItemUpdate(BaseModel):
    """Edit criterion/threshold/notes. Ticker changes are delete+create."""

    criteria_type: Optional[CriteriaType] = None
    threshold: Optional[float] = Field(default=None, gt=0, le=10_000_000)
    notes: Optional[str] = Field(default=None, max_length=500)

    @field_validator("notes")
    @classmethod
    def _notes(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        return v.strip() or None


class WatchlistItemResponse(BaseModel):
    id: UUID
    ticker: str
    criteria_type: CriteriaType
    threshold: float
    notes: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    # Price enrichment (None when no trusted current price exists)
    current_price: Optional[float] = None
    price_as_of: Optional[datetime] = None
    # True/False when a trusted price exists; None (unknown) otherwise
    criteria_met: Optional[bool] = None
