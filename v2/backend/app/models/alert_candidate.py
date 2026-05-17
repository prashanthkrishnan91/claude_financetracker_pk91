"""Pydantic models for Alert Trigger Policy v1 — alert candidates."""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class AlertCandidateResponse(BaseModel):
    id: UUID
    user_id: UUID
    ticker: str
    source_area: str
    candidate_type: str
    action_type: Optional[str] = None
    severity: str
    reason_code: str
    plain_english_reason: str
    policy_version: str
    status: str
    dedupe_key: str
    source_snapshot_id: Optional[UUID] = None
    source_run_id: Optional[UUID] = None
    expires_at: Optional[datetime] = None
    cooldown_until: Optional[datetime] = None
    created_at: datetime

    model_config = {"from_attributes": True}
