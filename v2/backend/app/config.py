"""Application configuration — loaded from environment variables."""

from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All config loaded from env vars or .env file. No secrets in code."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ── App ───────────────────────────────────────────────────────────────────
    app_name: str = "Portfolio Intelligence Platform"
    app_version: str = "2.0.0"
    debug: bool = False
    log_level: str = "INFO"

    # ── Supabase ──────────────────────────────────────────────────────────────
    supabase_url: str
    supabase_anon_key: str
    supabase_service_role_key: str  # Server-side only — never expose to frontend
    supabase_jwt_secret: str        # For JWT validation

    # ── Encryption (for API key storage) ──────────────────────────────────────
    # 32-byte hex key for AES-256-GCM encryption of user API keys
    encryption_key: str

    # ── CORS ──────────────────────────────────────────────────────────────────
    # Comma-separated JSON array of allowed origins.
    # Production: set CORS_ORIGINS=["https://your-app.vercel.app"]
    cors_origins: list[str] = [
        "http://localhost:3000",     # Next.js dev
        "http://localhost:8000",     # FastAPI docs
        "https://claude-financetracker-pk91-bku3zw5wg.vercel.app",  # Production Vercel
    ]
    # Set CORS_ALLOW_ALL=true to allow * (useful in development; disables credentials)
    cors_allow_all: bool = False

    # ── Rate Limiting ─────────────────────────────────────────────────────────
    rate_limit_per_minute: int = 60

    # ── External API defaults (per-user keys stored encrypted in DB) ─────────
    # These are optional fallback keys for development/testing
    finnhub_api_key: Optional[str] = None
    polygon_api_key: Optional[str] = None
    alpaca_api_key: Optional[str] = None
    alpaca_secret_key: Optional[str] = None

    # ── AI / Anthropic ─────────────────────────────────────────────────────────
    anthropic_api_key: Optional[str] = None

    # ── Plaid ─────────────────────────────────────────────────────────────────
    plaid_client_id: Optional[str] = None
    plaid_secret: Optional[str] = None
    plaid_env: str = "sandbox"

    # ── Cache TTLs (seconds) ──────────────────────────────────────────────────
    price_cache_ttl: int = 300           # 5 minutes
    holdings_cache_ttl: int = 86400      # 24 hours
    price_history_cache_ttl: int = 3600  # 1 hour
    analyst_verdict_reuse_ttl_seconds: int = 21600  # 6 hours (override for tests/ops)

    # ── Runtime certification harness (ops-only) ─────────────────────────────
    finance_runtime_cert_enabled: bool = False
    finance_runtime_cert_secret: Optional[str] = None
    finance_runtime_cert_user_id: Optional[str] = None
    finance_runtime_cert_user_email: Optional[str] = None

    # ── Intel v3 Research Workers (Phase 3) — dark-run, off by default ────────
    # Kill switch: if False, no research worker of any kind runs.
    intel_v3_research_workers_enabled: bool = False
    # Per-worker flag: Earnings Reviewer scaffold.
    intel_v3_earnings_reviewer_enabled: bool = False

    # ── Intel v3 Validation Harness (Phase 3.5) — off by default ─────────────
    # Must be True AND both Phase 3 flags above must be True for any validation run.
    intel_v3_research_worker_validation_enabled: bool = False
    # When True, logs structured INFO per validation run (aggregate only, no payloads).
    intel_v3_research_worker_validation_info_logs_enabled: bool = False


@lru_cache
def get_settings() -> Settings:
    """Cached settings singleton."""
    return Settings()
