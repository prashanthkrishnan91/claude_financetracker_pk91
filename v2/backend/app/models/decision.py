"""Decision feedback models."""

from __future__ import annotations

from typing import Any, Optional
from uuid import UUID
from datetime import datetime

from pydantic import BaseModel, Field


class ModifiedAction(BaseModel):
    symbol: str
    amount: float


class DecisionFeedbackRequest(BaseModel):
    type: str = Field(pattern="^(accept|modify|reject)$")
    modified_actions: Optional[list[ModifiedAction]] = None
    notes: Optional[str] = None


class DecisionResponse(BaseModel):
    id: UUID
    user_id: UUID
    decision_type: str
    status: str
    generated_actions: Optional[Any] = None
    final_actions: Optional[Any] = None
    user_feedback: Optional[Any] = None
    input_snapshot: Optional[Any] = None
    input_params: Optional[Any] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ManualDecisionLogCreate(BaseModel):
    ticker: str
    action: str = "BUY"
    amount: float
    confidence: Optional[float] = None
    reasoning: Optional[str] = None
    source: str = "manual"
    metadata: Optional[dict] = None
    strategy_tag: Optional[str] = None
    confidence_score: Optional[float] = None


class DecisionLogStatus(str):
    DRAFT = "DRAFT"
    FULLY_EXECUTED = "FULLY_EXECUTED"
    PARTIALLY_EXECUTED = "PARTIALLY_EXECUTED"
    SKIPPED = "SKIPPED"


class DecisionLogCreateRequest(BaseModel):
    source: str = "deploy"
    recommendation_snapshot: dict[str, Any]
    actual_decisions: list[dict[str, Any]] = Field(default_factory=list)
    notes: Optional[str] = None


class DecisionLogUpdateRequest(BaseModel):
    actual_decisions: Optional[list[dict[str, Any]]] = None
    notes: Optional[str] = None


class DecisionLogResponse(BaseModel):
    id: UUID
    user_id: UUID
    source: str
    status: str
    recommendation_snapshot: dict[str, Any]
    price_snapshot: Optional[dict[str, Any]] = None
    actual_decisions: list[dict[str, Any]]
    performance_snapshot: Optional[dict[str, Any]] = None
    decision_delta: Optional[dict[str, Any]] = None
    risk_behavior: Optional[str] = None
    style_shift: Optional[str] = None
    execution_gap_percent: Optional[float] = None
    realized_pnl: Optional[float] = None
    unrealized_pnl: Optional[float] = None
    review_date: Optional[datetime] = None
    notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
