"""User models — profile, API keys, preferences."""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


# ── API Key models (encrypted at app layer) ──────────────────────────────────

class PlaidCredentials(BaseModel):
    """Plaid API credentials — encrypted before storage."""
    access_token: str
    client_id: str
    secret: str
    env: str = Field(default="sandbox", pattern="^(sandbox|development|production)$")


class ApiKeys(BaseModel):
    """All third-party API keys for a user."""
    plaid: Optional[PlaidCredentials] = None
    finnhub_api_key: Optional[str] = None
    polygon_api_key: Optional[str] = None
    alpaca_api_key: Optional[str] = None
    alpaca_secret_key: Optional[str] = None


# ── User models ──────────────────────────────────────────────────────────────

class UserBase(BaseModel):
    """Shared user fields."""
    email: EmailStr
    display_name: Optional[str] = None
    deposit_amount: float = 900.00
    deposit_frequency: str = Field(default="biweekly", pattern="^(weekly|biweekly|monthly)$")
    theme: str = Field(default="dark", pattern="^(dark|light)$")
    default_currency: str = "USD"


class UserCreate(UserBase):
    """Fields required to create a new user (via Supabase Auth signup)."""
    password: str = Field(min_length=8, description="Min 8 characters")


class UserUpdate(BaseModel):
    """Updatable user fields (all optional)."""
    display_name: Optional[str] = None
    deposit_amount: Optional[float] = Field(default=None, gt=0)
    deposit_frequency: Optional[str] = Field(default=None, pattern="^(weekly|biweekly|monthly)$")
    theme: Optional[str] = Field(default=None, pattern="^(dark|light)$")


class UserResponse(UserBase):
    """User data returned by API (no secrets)."""
    id: UUID
    has_plaid: bool = False
    has_finnhub: bool = False
    has_polygon: bool = False
    has_alpaca: bool = False
    has_anthropic: bool = False
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class UserApiKeysUpdate(BaseModel):
    """Update API keys — values encrypted before storage."""
    plaid_access_token: Optional[str] = None
    plaid_client_id: Optional[str] = None
    plaid_secret: Optional[str] = None
    plaid_env: Optional[str] = Field(default=None, pattern="^(sandbox|development|production)$")
    finnhub_api_key: Optional[str] = None
    polygon_api_key: Optional[str] = None
    alpaca_api_key: Optional[str] = None
    alpaca_secret_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None
