"""Transaction models — audit trail with SHA-256 fingerprints."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class TransactionBase(BaseModel):
    """Shared transaction fields."""
    ticker: Optional[str] = None
    tx_type: str = Field(pattern="^(Buy|Sell|CDIV|DRIP|SPL|ACH|RTP|Other)$")
    quantity: Optional[Decimal] = Field(default=None, decimal_places=6)
    price: Optional[Decimal] = Field(default=None, decimal_places=6)
    amount: Optional[Decimal] = Field(default=None, decimal_places=6)
    tx_date: date
    settle_date: Optional[date] = None
    description: Optional[str] = None


class TransactionCreate(TransactionBase):
    """Create a new transaction (fingerprint computed server-side)."""
    raw_data: Optional[dict[str, Any]] = None


class TransactionResponse(TransactionBase):
    """Transaction returned by API."""
    id: UUID
    user_id: UUID
    fingerprint: str
    raw_data: Optional[dict[str, Any]] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class TransactionImportResult(BaseModel):
    """Result of a CSV import operation."""
    total_rows: int
    new_rows: int
    duplicates_skipped: int
    errors: int
    error_details: list[str] = Field(default_factory=list)


class DividendSummary(BaseModel):
    """Dividend analytics summary."""
    lifetime_earned: float
    annual_projection: float
    monthly_estimate: float
    dividend_history: list[dict[str, Any]]
    upcoming_payouts: list[dict[str, Any]]
