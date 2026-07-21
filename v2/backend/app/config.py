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

    # ── Stage 11B — Current price truth repair (off by default) ──────────────
    # When True, POST /api/v1/diagnostics/finance-intel/current-price-truth-repair
    # is available (cert-gated). dry_run=true by default — no writes unless
    # dry_run=false is explicitly set. Writes only to price_history.
    current_price_truth_repair_enabled: bool = False

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

    # ── Intel v3 Stage 5F — Multi-lane evidence population (off by default) ────
    # Fundamentals evidence lane: yfinance fundamentals → fundamental_quality artifact.
    # Requires intel_v3_research_workers_enabled.
    intel_v3_fundamentals_evidence_enabled: bool = False
    # Technicals evidence lane: yfinance price history → technical_signal artifact.
    # Requires intel_v3_research_workers_enabled.
    intel_v3_technicals_evidence_enabled: bool = False
    # News/sentiment evidence lane: yfinance news → sentiment_event artifact.
    # Requires intel_v3_research_workers_enabled.
    intel_v3_news_sentiment_evidence_enabled: bool = False

    # ── Intel v3 Stage 5H — SEC CompanyFacts official fundamentals lane (off by default) ──
    # SEC EDGAR CompanyFacts (XBRL) → fundamental_quality artifact via
    # sec_companyfacts_adapter_v1. FREE / OFFICIAL source. No paid providers.
    # No LLM calls. artifact_type=fundamental_quality, skill_pack=sec_companyfacts_evidence_v1.
    # Distinct from yfinance fundamentals lane — does not replace it (yfinance is baseline).
    # Requires intel_v3_research_workers_enabled AND sec_edgar_user_agent to be set.
    # Tickers with no SEC company facts return honest no-artifact; no fabrication.
    intel_v3_sec_companyfacts_evidence_enabled: bool = False

    # ── Intel v3 Stage 8C PR 2 — SEC catalyst sentiment evidence lane (off by default) ──
    # SEC EDGAR filing metadata (10-K, 10-Q, 8-K) → sentiment_event artifact via
    # sec_catalyst_sentiment_adapter_v1 + sentiment_event_adapter_v2.
    # FREE / OFFICIAL source. No paid providers. No LLM calls.
    # artifact_type=sentiment_event, skill_pack=sec_catalyst_sentiment_evidence_v1.
    # source_authority=PRIMARY_AUTHORITY (confirmed CIK match, company-authored docs).
    # sentiment_polarity=None — SEC filings do not provide scored polarity.
    # Freshness windows: 10-K=180d, 10-Q=90d, 8-K=30d.
    # Requires intel_v3_research_workers_enabled AND sec_edgar_user_agent to be set.
    # Default OFF — no behavior change unless explicitly enabled.
    intel_v3_sentiment_catalyst_evidence_enabled: bool = False

    # ── Intel v3 Stage 9F.2a — SEC NPORT-P ETF holdings evidence lane (off by default) ──
    # SEC EDGAR NPORT-P regulatory filings → per-ticker ETF fund holdings artifact
    # via nport_provider_v1.py + etf_nport_adapter_v1.py.
    # FREE / OFFICIAL source (SEC EDGAR). No API key required. No paid providers. No LLM.
    # artifact_type=etf_fund_note (existing DB enum), skill_pack=etf_sec_nport_holdings_evidence_v1.
    # ETF-only guard: non-ETF tickers are skipped honestly (not written as failures).
    # NPORT-P is official but periodic/lagged (quarterly + ~60-day filing lag).
    # safe_for_decision stays False. synthesis_ready stays False.
    # Requires intel_v3_research_workers_enabled AND sec_edgar_user_agent to be set.
    # Default OFF — no behavior change unless explicitly enabled.
    intel_v3_etf_nport_evidence_enabled: bool = False

    # Stage 9F.2a diagnostic — NPORT-P live-check HTTP endpoint (default OFF).
    # When enabled, POST /api/v1/diagnostics/finance-intel/etf-nport-live-check is
    # available to operators (requires finance_runtime_cert_enabled + cert secret).
    # Does NOT write artifacts, does NOT alter decisions or snapshots.
    # Requires sec_edgar_user_agent to be set.
    intel_v3_nport_diagnostic_endpoint_enabled: bool = False

    # Stage 9K diagnostic — artifact-readiness DB read endpoint (default OFF).
    # When enabled, POST /api/v1/diagnostics/finance-intel/etf-stage9k-artifact-readiness
    # queries research_artifacts and reports per-ticker why the Stage 9K holdings-ready
    # gate passes or fails. Read-only SELECT only. No artifact writes. No provider calls.
    # No SQL migration required. Cert-gated.
    intel_v3_stage9k_artifact_readiness_diagnostic_enabled: bool = False

    # ── Intel v3 Stage 5I — FRED official macro evidence lane (off by default) ────
    # FRED (Federal Reserve Economic Data) → portfolio-scope macro evidence artifact
    # via fred_macro_adapter_v1. FREE / OFFICIAL source. No paid providers. No LLM calls.
    # Writes one portfolio_exposure artifact per explicit Intel v3 run with
    # skill_pack=fred_macro_evidence_v1 (distinct from any company fundamentals lane).
    # Requires intel_v3_research_workers_enabled AND fred_api_key to be set.
    # Allowlisted macro series only (FEDFUNDS/DFF, DGS10, DGS2, CPIAUCSL, UNRATE,
    # PAYEMS, GDP/GDPC1, optional T10Y2Y). safe_for_decision stays False — macro
    # evidence is portfolio context, never visible Buy/Hold/Trim/Sell authority.
    intel_v3_macro_evidence_enabled: bool = False
    # Free API key from https://fred.stlouisfed.org/docs/api/api_key.html
    # Required for any FRED macro lane call. If unset, the macro lane skips honestly.
    fred_api_key: Optional[str] = None

    # ── Stage 9F.3a — Alpha Vantage ETF_PROFILE diagnostic (off by default) ────
    # When True, enables POST /diagnostics/finance-intel/alpha-vantage-etf-profile-check.
    # Cert-gated. Diagnostic-only: no artifact writes, no decision mutations.
    # canonical_ready=False always. ALPHA_VANTAGE_API_KEY required — fails closed if unset.
    # Do not run more than once per day on free tier to avoid burning quota.
    intel_v3_alpha_vantage_etf_profile_diagnostics_enabled: bool = False
    # Alpha Vantage API key — required for ETF_PROFILE diagnostic.
    # Never logged or returned in any API response.
    alpha_vantage_api_key: Optional[str] = None

    # ── Stage 9F.4 — FMP ETF holdings free-key entitlement proof (no flag) ────
    # POST /diagnostics/finance-intel/fmp-etf-holdings-check is cert-gated.
    # Diagnostic-only: no artifact writes, no decision mutations.
    # canonical_ready=False always. FMP_API_KEY required — fails closed if unset.
    # No feature flag: key presence is the sole activation guard.
    fmp_api_key: Optional[str] = None

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

    # ── Intel v3 Phase 9 — SEC Metric Evidence Readiness Adapter (off by default) ──
    # When True, enables the protected readiness-adapter endpoint that classifies
    # each portfolio ticker into a typed readiness status (READY/PARTIAL/BLOCKED/
    # SKIPPED_NON_COMPANY) based on existing Phase 8 SEC metric evidence.
    # Shadow/readiness-only — does NOT feed SEC metrics into DecisionInputV3,
    # does NOT change visible decisions, does NOT invoke expansion write mode.
    # Dry-run only — safe_for_decision remains False. No visible snapshot change.
    intel_v3_sec_metric_evidence_readiness_adapter_enabled: bool = False

    # ── Intel v3 Phase 10 — Evidence Source Registry diagnostics (off by default) ──
    # When True, enables the protected evidence-source-registry diagnostics endpoint
    # that returns the governance summary of all defined evidence sources and lanes.
    # Registry-only — safe_for_decision remains False. No visible snapshot change.
    # No decision consumption, no provider calls, no LLM calls, no SQL writes.
    intel_v3_evidence_source_registry_diagnostics_enabled: bool = False

    # ── Intel v3 Phase 11 — SEC Metric Truth Adapter v1 (off by default) ─────
    # When True, the Intel v3 run_v3() path will compute Phase 9 SEC metric
    # readiness and apply governed evidence-quality signals per eligible ticker.
    # READY tickers may receive AxisBand.OK contribution to evidence_quality.
    # PARTIAL tickers may receive AxisBand.THIN (degraded) contribution only.
    # BLOCKED / SKIPPED_NON_COMPANY tickers receive no SEC fundamentals signal.
    # Governance gate: Phase 10 registry sec_companyfacts_v1 must pass all checks.
    # Decisions remain deterministic — decision_policy_v1 is the only authority.
    # No new provider calls. No LLM calls. No SQL writes.
    intel_v3_sec_metric_truth_adapter_v1_enabled: bool = False

    # When True, enables the protected Phase 11 diagnostics endpoint that returns
    # governance gate status and evidence-quality upgrade counts (aggregate only).
    # Independent of the consumption flag above — controlled solely here.
    intel_v3_sec_metric_truth_adapter_v1_diagnostics_enabled: bool = False

    # ── Intel v3 Phase 13 — Valuation Context Adapter v1 (off by default) ─────
    # When True, the Intel v3 run_v3() path will compute SEC metric readiness
    # and apply governed price-context signals to eligible company tickers.
    # READY tickers may receive PriceBand.FAIR contribution to price_context
    # (upgrade from SUPPRESSED only — never downgrades CHEAP/FULL/EXPENSIVE).
    # PARTIAL tickers may receive PriceBand.FAIR (degraded) contribution only.
    # ETF / fund / crypto tickers are always SUPPRESSED_NON_COMPANY.
    # Governance gate: Phase 10 registry valuation_ratio_computed_v1 must pass.
    # Decisions remain deterministic — decision_policy_v1 is the only authority.
    # No new provider calls. No LLM calls. No SQL writes.
    intel_v3_valuation_context_adapter_v1_enabled: bool = False

    # When True, enables the protected Phase 13 diagnostics endpoint that returns
    # governance gate status and signal-status counts per ticker category.
    # Aggregate only — no raw valuation values, no metric keys, no payloads.
    # Independent of the consumption flag above — controlled solely here.
    intel_v3_valuation_context_adapter_v1_diagnostics_enabled: bool = False

    # ── Intel v3 Phase 14A — Valuation Data Audit v1 (off by default) ─────────
    # When True, enables the protected Phase 14A read-only diagnostics endpoint
    # that audits whether existing stored data can support future valuation ratio
    # computation. Returns aggregate-only counts by evidence category.
    # Diagnostics-only — does NOT compute ratios, does NOT produce PriceBand,
    # does NOT modify DecisionInputV3. No provider/LLM calls. No SQL writes.
    intel_v3_valuation_data_audit_v1_diagnostics_enabled: bool = False

    # ── Intel v3 Phase 14B — Valuation Input Verification v1 (off by default) ──
    # When True, enables the protected Phase 14B read-only diagnostics endpoint
    # that verifies actual stored inputs needed for future FY EPS earnings-yield
    # computation: raw EPS facts, equity facts, stored price availability/freshness,
    # and financial sector availability. Returns aggregate-only counts.
    # Diagnostics-only — does NOT compute ratios, does NOT produce PriceBand,
    # does NOT modify DecisionInputV3. No provider/LLM calls. No SQL writes.
    intel_v3_valuation_input_verification_v1_diagnostics_enabled: bool = False

    # ── Intel v3 Phase 14C-Prep — Price + Sector Source Resolution v1 (off by default) ──
    # When True, enables the protected Phase 14C-Prep read-only diagnostics endpoint
    # that ranks candidate stored sources for current price and financial sector,
    # and reports a certification status for each. Diagnostics-only — does NOT
    # compute ratios, does NOT compute earnings yield, does NOT produce PriceBand,
    # does NOT modify DecisionInputV3, does NOT change visible behavior.
    # No provider/LLM calls. No SQL writes. Aggregate-only response.
    intel_v3_price_sector_source_resolution_v1_diagnostics_enabled: bool = False

    # ── Intel v3 Phase 14C — FY EPS Earnings Yield v1 (off by default) ──
    # When True, enables the protected Phase 14C read-only diagnostics endpoint
    # that computes FY EPS earnings yield from source-linked stored SEC EPS
    # facts and certified market_snapshots price. Aggregate-only response —
    # no raw EPS, raw prices, raw yields, or per-ticker rows. Shadow/diagnostic
    # only — does NOT modify DecisionInputV3, does NOT produce PriceBand,
    # does NOT change visible Buy/Hold/Trim/Sell behavior. No provider/LLM
    # calls. No SQL writes.
    intel_v3_fy_eps_earnings_yield_v1_diagnostics_enabled: bool = False

    # ── Intel v3 Phase 14C.3 — Ticker FY EPS Gap v1 Diagnostics (off by default) ──
    # When True, enables the protected ticker-level FY EPS gap diagnostic
    # endpoint. Returns per-ticker gap classification with stable gap_reason
    # enums explaining why each company ticker does or does not have usable
    # FY EPS. Operator/cert-gated only — never called by frontend page load.
    # No DB writes, no provider/LLM calls, no decision mutations.
    intel_v3_fy_eps_ticker_gap_v1_diagnostics_enabled: bool = False

    # ── Intel v3 Phase 14C.2 — SEC FY EPS Coverage Backfill (off by default) ──
    # When True, enables the protected backfill endpoint that re-runs the
    # SEC earnings reviewer for an explicit list of tickers, regenerating
    # research_artifact_facts with the Phase 14C.2 FY EPS coverage policy.
    # Guarded by the existing finance_runtime_cert_secret header.
    # dry_run=True by default — no DB writes without explicit dry_run=False.
    # Must NOT be enabled in normal app operation. Explicit, auditable only.
    intel_v3_sec_fy_eps_backfill_enabled: bool = False

    # ── Stage 10C.1 — VTI price history repair (off by default) ─────────────────
    # When True, enables POST /diagnostics/finance-intel/vti-price-history-repair.
    # Cert-gated. Manual/on-demand only. Writes VTI rows to price_history via
    # yfinance (idempotent upsert). dry_run=True by default. No workers.
    # No synthesis changes. No Buy/Hold/Trim/Sell changes.
    vti_price_history_repair_enabled: bool = False

    # ── Intel v3 Phase 14C.4 — FY EPS Raw Trace Diagnostics (off by default) ──
    # When True, enables the protected per-ticker FY EPS raw trace endpoint.
    # For each explicitly requested ticker (max 5), traces exactly where in the
    # pipeline annual FY EPS is lost: from raw SEC EDGAR companyfacts through
    # source accession linkage, parser selection, artifact write, and extractor.
    # Returns compact counts only — no raw SEC payloads, no source URLs,
    # no unrestricted DB rows. Cert-gated + explicit ticker input + read-only.
    # No DB writes. No decision mutations. No PriceBand. No TTM.
    # Provider calls allowed only within this endpoint (cert-gated, read-only).
    # Must NOT be enabled in normal app operation.
    intel_v3_fy_eps_raw_trace_v1_diagnostics_enabled: bool = False

    # ── Stage 9F.2b — ETF Holdings Provider Registry Diagnostics (off by default) ──
    # When True, enables the protected POST /diagnostics/finance-intel/
    # etf-provider-registry-check endpoint that runs the provider registry
    # diagnostic for the ETF universe (SPY/QQQ/XLE/GLD/VOO/VTI/VGT/VHT/VIS/
    # VXUS/VYM/SCHD). Tries SEC NPORT and issuer-official adapters per ticker.
    # Diagnostic-only: no artifact writes, no decision mutations, no visible
    # Buy/Hold/Trim/Sell change. canonical_ready=False for all. Cert-gated.
    # Requires FINANCE_RUNTIME_CERT_ENABLED=true + cert secret header.
    # SEC NPORT calls also require SEC_EDGAR_USER_AGENT to be set.
    intel_v3_etf_provider_registry_diagnostics_enabled: bool = False

    # ── Stage 9O — Vanguard issuer-official holdings diagnostic (off by default) ─
    # When True, enables POST /diagnostics/finance-intel/vanguard-holdings-diagnostic.
    # Cert-gated. Proof stage only: no canonical adapter, no artifact writes, no
    # synthesis, no decision integration. canonical_ready=False always.
    # Evaluates VTI, VOO, VXUS for issuer-official canonical readiness.
    # No paid providers, no LLM, no SQL, no UI changes.
    intel_v3_vanguard_holdings_diagnostic_enabled: bool = False

    # ── Intel v3 Phase 14D — PriceBand Shadow Policy v1 (off by default) ──────
    # When True, enables the protected POST /diagnostics/finance-intel/
    # priceband-shadow-v1 endpoint which classifies certified Phase 14C inputs
    # (source-linked FY EPS + fresh price + sector) into a humble valuation
    # bucket using a static governance table (policy_static_v1).
    # Cert-gated, read-only, shadow-only. NO target prices, NO fair values,
    # NO buy_below/sell_above thresholds, NO DecisionInputV3 mutation, NO
    # PriceBand wiring into the visible decision path. NO frontend wiring.
    # Must NOT be enabled in normal app operation.
    intel_v3_priceband_shadow_v1_diagnostics_enabled: bool = False

    # ── Intel v3 Build 3 PR 2B — PriceBand Visible Context v1 (off by default) ──
    # When True, fetches source-linked FY EPS + fresh price for company tickers,
    # runs Phase 14D shadow classification and Phase 14F plain-English translation,
    # and embeds the result in detail_drawer_payload.valuation_context (detail
    # drawer only — never card or list view).
    # Non-company assets (ETF/fund/crypto/bond) are always suppressed.
    # Stale price (>7 days) is always suppressed.
    # Low-confidence or negative-EPS observations are always suppressed.
    # NO LLM. NO provider calls — reads market_snapshots + research_artifact_facts only.
    # NO target price, fair value, intrinsic value, upside, downside, buy_below/sell_above.
    # NO DecisionInputV3 mutation. NO Buy/Hold/Trim/Sell authority change.
    # Default off — set INTEL_V3_PRICEBAND_VISIBLE_CONTEXT_V1_ENABLED=true to enable.
    intel_v3_priceband_visible_context_v1_enabled: bool = False

    # ── Stage 5J — Research Evidence Coverage Read Model v1 (off by default) ──
    # When True, exposes a protected diagnostics endpoint that summarizes which
    # research artifacts exist per user/lane/ticker and which are missing,
    # suppressed, or stale. READ-ONLY: never triggers an evidence run, never
    # writes artifacts, never touches intel_v3_snapshots or recommendations.
    # safe_for_decision stays False. Independent of any worker/observability flag.
    intel_v3_evidence_coverage_diagnostics_enabled: bool = False
    # When True, the explicit-run evidence lane orchestrator emits a compact
    # post-dispatch coverage summary log (read-only, fail-soft). No raw payloads.
    intel_v3_evidence_coverage_dispatch_log_enabled: bool = False

    # ── Stage 5K — Research Evidence Decision Input Adapter v1 (off by default) ──
    # When True, exposes a protected diagnostics endpoint that runs the Stage 5K
    # shadow adapter over the Stage 5J coverage read model. Shadow/diagnostic only:
    # no visible Buy/Hold/Trim/Sell change, no provider calls, no LLM calls, no writes.
    # safe_for_decision stays False. Cert-gated + this flag required.
    intel_v3_evidence_decision_readiness_diagnostics_enabled: bool = False

    # ── Stage 9A — Coverage & Trust Matrix v1 (off by default) ──────────────────
    # When True, exposes a protected diagnostics endpoint that maps Stage 5J/5K
    # coverage statuses to a per-ticker, per-category STRONG/PARTIAL/WEAK/MISSING/
    # NOT_APPLICABLE trust matrix. Diagnostic only: no LLM, no providers, no writes,
    # no visible Buy/Hold/Trim/Sell change. safe_for_decision stays False.
    # synthesis_ready stays False (foundation layer only). Cert-gated + this flag.
    intel_v3_coverage_trust_matrix_enabled: bool = False

    # ── Stage 9B — Intel Data Foundation Forensics v1 (off by default) ──────────
    # When True, exposes a protected diagnostics endpoint that inspects actual
    # persisted research artifacts per holding and classifies the primary root cause
    # explaining missing data foundation (provider gap, CIK mapping, worker gap,
    # weak artifact, no lane built, etc.). Diagnostic only: no LLM, no providers,
    # no writes, no visible Buy/Hold/Trim/Sell change. safe_for_decision stays False.
    # synthesis_ready stays False. Cert-gated + this flag required.
    intel_v3_data_foundation_forensics_enabled: bool = False

    # ── Stage 6 — Evidence-Aware Intel v3 Decision Engine Governance (off by default) ──
    # When True, applies Stage 5K evidence-readiness signals to Intel v3 decision
    # input (evidence_quality axis) before decide() is called. Deterministic policy
    # remains the final authority. No LLM, no provider, no SQL, no UI changes.
    # BUY allowed only when research evidence axes are usable enough.
    # HOLD is the safe default when evidence is weak, missing, stale, or suppressed.
    # TRIM/SELL remain governed by portfolio_fit/risk_band (existing policy).
    # Macro context adds advisory context only; never independently forces action.
    # ETF/crypto SEC not_applicable is not penalized.
    # When False: existing visible Intel v3 behavior is unchanged.
    intel_v3_evidence_aware_policy_enabled: bool = False

    # When True, enables the cert-gated Stage 6 diagnostics endpoint that returns
    # before/after decision comparison, action distribution, HOLD-collapse risk
    # indicator, and per-ticker evidence governance diagnostics.
    # Read-only: no snapshot writes, no LLM, no provider, no page-load execution.
    # Independent of intel_v3_evidence_aware_policy_enabled — can be enabled for
    # comparison analysis regardless of whether the policy flag is on.
    intel_v3_stage6_governance_diagnostics_enabled: bool = False

    # ── Deploy v3 sizing policy config (optional; policy is UNSUPPORTED if absent) ──
    # When both are set, the sizing source adapter certifies the policy for exact-dollar math.
    # deploy_minimum_trade_usd: minimum dollar threshold below which a trade is suppressed.
    # deploy_rounding_policy: one of WHOLE_DOLLAR, NEAREST_DOLLAR, NO_ROUNDING.
    deploy_minimum_trade_usd: Optional[float] = None
    deploy_rounding_policy: Optional[str] = None

    # ── Stage 3E — Alert Email Delivery (Resend provider, off by default) ────────
    # Default state: delivery OFF, dry-run ON. Both must be explicitly configured
    # for real emails to send. Never send email unless ALERT_EMAIL_DELIVERY_ENABLED
    # is explicitly true AND all required config is set AND ALERT_EMAIL_DRY_RUN=false.
    alert_email_delivery_enabled: bool = False
    alert_email_provider: str = ""          # resend | (empty = no provider)
    resend_api_key: Optional[str] = None
    alert_email_from: Optional[str] = None  # e.g. "alerts@yourdomain.com"
    alert_email_to: Optional[str] = None    # v1: single recipient
    alert_email_dry_run: bool = True        # must set =false for real sends

    # ── COST GUARD — emergency cost-control switches (all off by default) ─────────
    # Master kill switch for all background workers. When False, every worker
    # entrypoint exits 0 immediately without initializing clients or polling.
    # Set INTEL_BACKGROUND_WORKERS_ENABLED=true to allow individual worker flags
    # to take effect. Individual flags are still checked after the master.
    intel_background_workers_enabled: bool = False

    # Snapshot write guard. When False, _persist_snapshot() in intel_v3_service
    # logs and returns without writing to intel_v3_snapshots. Read paths are
    # unaffected. Manual/explicit Intel v3 runs still compute and return results
    # but do not grow the snapshots table until this is re-enabled.
    intel_v3_snapshot_writes_enabled: bool = False

    # When True, polling interval clamping is bypassed. Leave False in production
    # to ensure workers cannot poll faster than their safe minimums even if an
    # env var is set to a short interval.
    cost_guard_allow_aggressive_polling: bool = False

    # ── Stage 13B (legacy) — bounded on-demand Intel v3 evidence drain ────────
    # RETIRED from Run Intel by the distributed workflow
    # (docs/ai/RUN_INTEL_DISTRIBUTED_WORKFLOW.md): POST /intel/v3/run no
    # longer drains anything in-request. The flag is kept only so existing
    # deployments with the env var set do not fail settings validation; no
    # code path reads it for Run Intel anymore.
    intel_v3_on_demand_refresh_enabled: bool = False

    # ── Distributed Run Intel workflow (migration 027) ────────────────────────
    # Cost/concurrency controls for the durable task-graph worker supervisor.
    # The workflow itself is not flag-gated: it is THE Run Intel execution
    # path (gated by INTEL_V3_VISIBLE_SNAPSHOT_ENABLED like the whole router)
    # and degrades with an explicit retryable error until migration 027 is
    # applied. All limits are execution details — they never redefine the
    # portfolio scope of a run.
    intel_v3_distributed_max_collector_concurrency: int = 4
    intel_v3_distributed_max_llm_concurrency: int = 2
    intel_v3_distributed_max_specialist_batch: int = 5
    intel_v3_distributed_task_lease_seconds: int = 300
    intel_v3_distributed_max_task_attempts: int = 3


@lru_cache
def get_settings() -> Settings:
    """Cached settings singleton."""
    return Settings()
