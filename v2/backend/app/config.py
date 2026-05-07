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

    # ── Intel v3 Artifact Observability (Phase 4) — off by default ───────────
    # Read-only diagnostics lane: aggregate counters only, zero decision drift.
    # Independent of Phase 3/3.5 worker/validation flags — controlled solely here.
    intel_v3_research_artifact_observability_enabled: bool = False
    # When True, logs one structured INFO line per observability call (aggregates only).
    intel_v3_research_artifact_observability_info_logs_enabled: bool = False

    # ── Intel v3 Phase 6A — SEC EDGAR evidence population (off by default) ────
    # When True, the Earnings Reviewer worker fetches SEC EDGAR filing metadata
    # to produce provider-backed, source-linked, freshness-classified artifacts.
    # Requires both Phase 3 flags above to also be True.
    # Dark-run only — safe_for_decision remains False. No artifact consumption.
    intel_v3_earnings_reviewer_sec_enabled: bool = False
    # User-Agent string sent to SEC EDGAR per SEC terms of service.
    # Format: "MyApp/1.0 contact@example.com"
    # If empty/unset, SEC fetches are skipped even when the flag above is True.
    sec_edgar_user_agent: Optional[str] = None

    # ── Intel v3 Phase 8A — SEC Metric Truth Adapter Dry Run (off by default) ──
    # When True, the observability endpoint runs the Phase 8A dry-run mapper that
    # normalizes source-linked metric_observation facts into internal evidence
    # buckets (aggregate counts only — no raw values, no decision consumption).
    # Requires intel_v3_research_artifact_observability_enabled to also be True.
    # Dry-run only — safe_for_decision remains False. No visible snapshot change.
    intel_v3_sec_metric_truth_adapter_dry_run_enabled: bool = False

    # ── Intel v3 Phase 8B — SEC Metric Evidence Snapshot Dry Run (off by default) ──
    # When True, converts Phase 8A bucket counts into a per-ticker diagnostic
    # contract: present/missing buckets, bucket-group coverage, future-adapter
    # readiness level, and blocking reason codes (aggregate only — no raw values,
    # no ratios, no decision consumption).
    # Requires intel_v3_sec_metric_truth_adapter_dry_run_enabled to also be True.
    # Dry-run only — safe_for_decision remains False. No visible snapshot change.
    intel_v3_sec_metric_evidence_snapshot_dry_run_enabled: bool = False

    # ── Intel v3 Phase 8D — SEC Metric Portfolio Coverage Dry Run (off by default) ──
    # When True, enables the protected portfolio-coverage diagnostics endpoint that
    # compares current portfolio tickers against Phase 8B SEC metric evidence output
    # (aggregate-only, no raw values, no decision consumption).
    # Independent of Phase 8A/8B flags — reads Phase 8B pure functions directly.
    # Dry-run only — safe_for_decision remains False. No visible snapshot change.
    intel_v3_sec_metric_portfolio_coverage_dry_run_enabled: bool = False

    # ── Intel v3 Phase 8E — SEC Metric Portfolio Coverage Expansion (off by default) ──
    # When True, enables the protected portfolio-coverage expansion endpoint that
    # attempts to create SEC CompanyFacts metric_observation research artifacts for
    # missing SEC-company portfolio tickers (skips ETF/Crypto/already-covered tickers).
    # Pre-consumption only — artifacts written but safe_for_decision stays False.
    # No visible snapshot change. No decision consumption.
    # Also requires intel_v3_research_workers_enabled and
    # intel_v3_earnings_reviewer_enabled for writes to succeed.
    intel_v3_sec_metric_portfolio_coverage_expansion_enabled: bool = False


@lru_cache
def get_settings() -> Settings:
    """Cached settings singleton."""
    return Settings()
