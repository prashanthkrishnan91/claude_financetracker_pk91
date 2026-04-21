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

    # Multi-agent fields (populated from the agent pipeline)
    investment_thesis: Optional[str] = None
    sentiment_score: Optional[float] = None
    technical_signal: Optional[str] = None
    conviction_score: Optional[float] = None
    suggested_allocation: Optional[float] = None
    agent_run_id: Optional[UUID] = None


class AgentRunStatus(BaseModel):
    """Status snapshot of an in-flight or completed agent run."""
    id: UUID
    status: str                 # queued | running | completed | failed
    current_agent: Optional[str] = None
    progress_pct: int = 0
    tickers: list[str] = []
    deposit_amount: float = 0
    sale_proceeds: float = 0
    allocation: dict = {}
    summary: Optional[str] = None
    error_message: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None


class AgentRunCreate(BaseModel):
    """Payload for POST /recommendations/refresh — kicks off a pipeline run."""
    deposit_amount: Optional[float] = None   # defaults to user.deposit_amount
    sale_proceeds: Optional[float] = 0.0


class AgentRunQueued(BaseModel):
    """Immediate response from POST /recommendations/refresh."""
    job_id: UUID
    status: str
    message: str


class AgentInsight(BaseModel):
    """Full per-ticker agent output for the drill-down view."""
    id: UUID
    run_id: Optional[UUID] = None
    ticker: str
    investment_thesis: Optional[str] = None
    sentiment_score: Optional[float] = None
    sentiment_label: Optional[str] = None
    technical_signal: Optional[str] = None
    technical_summary: Optional[str] = None
    fundamental_score: Optional[float] = None
    fundamental_summary: Optional[str] = None
    conviction_score: Optional[float] = None
    suggested_allocation: Optional[float] = None
    suggested_action: Optional[str] = None
    created_at: Optional[str] = None


class DecisionLogEntry(BaseModel):
    """Decision log entry returned by API."""
    id: UUID
    recommendation_id: Optional[UUID] = None
    ticker: str
    decision: str
    notes: Optional[str] = None
    price_at_decision: Optional[float] = None
    shares_at_decision: Optional[float] = None
    current_price: Optional[float] = None
    return_pct: Optional[float] = None
    status: str = "active"
    closed_at: Optional[datetime] = None
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
