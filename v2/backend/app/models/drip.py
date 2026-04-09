"""DRIP (Dividend Reinvestment Plan) models."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class DripSummary(BaseModel):
    lifetime_earned: float
    annual_projection: float
    monthly_estimate: float
    top_earner: Optional[str] = None
    positions_with_drip: int


class DripPosition(BaseModel):
    ticker: str
    name: str
    shares: float
    drip_shares: float
    drip_cost: float
    drip_value: float
    drip_gain: float
    annual_income: float
    yield_pct: float
    ex_date: str
    pay_date: str
    category: str


class DripHistoryEntry(BaseModel):
    id: str
    ticker: Optional[str] = None
    amount: float
    tx_date: str
    description: Optional[str] = None
