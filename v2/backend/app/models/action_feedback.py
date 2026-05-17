"""Pydantic models for Action Feedback Foundation v1."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field


FEEDBACK_TYPES = Literal[
    "executed", "skipped", "ignored", "snoozed", "too_risky", "not_relevant", "user_note"
]
SOURCE_AREAS = Literal["intel", "deploy", "watchtower", "alert"]
ACTION_TYPES = Literal["BUY", "HOLD", "TRIM", "SELL", "DEPLOY_ACTION"]


class ActionFeedbackCreateRequest(BaseModel):
    feedback_type: FEEDBACK_TYPES
    source_area: SOURCE_AREAS
    idempotency_key: str = Field(min_length=1, max_length=200)
    ticker: Optional[str] = Field(default=None, max_length=20)
    action_type: Optional[ACTION_TYPES] = None
    agent_run_id: Optional[UUID] = None
    snapshot_id: Optional[UUID] = None
    note: Optional[str] = Field(default=None, max_length=2000)
    cooldown_until: Optional[datetime] = None


class ActionFeedbackResponse(BaseModel):
    id: UUID
    user_id: UUID
    feedback_type: str
    source_area: str
    idempotency_key: str
    ticker: Optional[str] = None
    action_type: Optional[str] = None
    agent_run_id: Optional[UUID] = None
    snapshot_id: Optional[UUID] = None
    note: Optional[str] = None
    cooldown_until: Optional[datetime] = None
    created_at: datetime

    model_config = {"from_attributes": True}
