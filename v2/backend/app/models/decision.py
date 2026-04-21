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
