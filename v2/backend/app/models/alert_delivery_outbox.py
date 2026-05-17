"""Pydantic models for Alert Delivery Outbox v1."""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class AlertDeliveryOutboxResponse(BaseModel):
    id: UUID
    user_id: UUID
    alert_candidate_id: UUID
    ticker: str
    channel: str
    delivery_mode: str
    severity: str
    subject: str
    plain_english_body: str
    status: str
    dedupe_key: str
    provider_message_id: Optional[str] = None
    failure_reason: Optional[str] = None
    scheduled_for: Optional[datetime] = None
    sent_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    policy_version: str
