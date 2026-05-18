"""Stage 5G — Evidence Provider Registry v1 (free-first).

Central typed registry for all data providers used by Stage 5 evidence lanes.
Each entry declares cost tier, trust tier, supported lanes, freshness
expectations, API-key requirements, default-enabled status, priority, and
limitations.

Design principles:
  - Free/official sources are preferred over paid/unofficial sources for every lane.
  - Paid providers are registered as metadata-only (default_enabled=False) unless
    an adapter is already implemented in this repo.
  - No network calls, no DB IO, no env reads at import time.
  - Disabled providers must NEVER be called by the evidence provider router.

Providers registered in Stage 5G:
  - sec_edgar       FREE / OFFICIAL            — sec_filing only (XBRL/company-facts = future lane)
  - yfinance        FREE / UNOFFICIAL_AGGREGATOR — fundamentals, technicals, news_sentiment
  - fred            FREE / OFFICIAL            — macro; metadata-only (no client yet)
  - fmp             PAID / BROAD_FINANCIAL_VENDOR — disabled metadata-only candidate
  - eodhd           LOW_COST / BROAD_FINANCIAL_VENDOR — disabled metadata-only candidate
  - alpha_vantage   LOW_COST / BROAD_FINANCIAL_VENDOR — disabled metadata-only candidate

Pure module — no IO, no provider clients instantiated here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, FrozenSet, List, Optional

EVIDENCE_PROVIDER_REGISTRY_VERSION = "stage5g_v1"


# ── Tier enums ────────────────────────────────────────────────────────────────

class CostTier(str, Enum):
    FREE = "FREE"
    LOW_COST = "LOW_COST"
    PAID = "PAID"
    EXPENSIVE = "EXPENSIVE"
    UNKNOWN = "UNKNOWN"


class TrustTier(str, Enum):
    OFFICIAL = "OFFICIAL"
    EXCHANGE_OR_MARKET_VENDOR = "EXCHANGE_OR_MARKET_VENDOR"
    BROAD_FINANCIAL_VENDOR = "BROAD_FINANCIAL_VENDOR"
    NEWS_VENDOR = "NEWS_VENDOR"
    UNOFFICIAL_AGGREGATOR = "UNOFFICIAL_AGGREGATOR"
    LLM_EXTRACTOR = "LLM_EXTRACTOR"
    UNKNOWN = "UNKNOWN"


# ── Evidence lane identifiers ─────────────────────────────────────────────────
# These match the Stage 5F lane constants and extend them with additional lanes.

LANE_FUNDAMENTALS = "fundamentals"
LANE_TECHNICALS = "technicals"
LANE_NEWS_SENTIMENT = "news_sentiment"
LANE_SEC_FILING = "sec_filing"
LANE_MACRO = "macro"
LANE_ANALYST_REVISIONS = "analyst_revisions"
LANE_COMPANY_STRATEGY = "company_strategy"
LANE_TRANSCRIPTS = "transcripts"
LANE_INSIDER_13F = "insider_13f"

ALL_LANES: FrozenSet[str] = frozenset({
    LANE_FUNDAMENTALS,
    LANE_TECHNICALS,
    LANE_NEWS_SENTIMENT,
    LANE_SEC_FILING,
    LANE_MACRO,
    LANE_ANALYST_REVISIONS,
    LANE_COMPANY_STRATEGY,
    LANE_TRANSCRIPTS,
    LANE_INSIDER_13F,
})


# ── Provider entry contract ───────────────────────────────────────────────────

@dataclass(frozen=True)
class EvidenceProviderEntry:
    """Contract for one evidence provider in the Stage 5G registry.

    Fields:
        provider_id:             Unique stable identifier.
        display_name:            Human-readable name.
        cost_tier:               CostTier enum — financial cost of using this provider.
        trust_tier:              TrustTier enum — how authoritative the source is.
        supported_lanes:         Evidence lanes this provider can serve.
        max_stale_age_hours:     Maximum acceptable staleness per lane (hours).
                                 Keys are lane identifiers; empty = no per-lane contract.
        requires_api_key:        True when a credential is needed.
        default_enabled:         True when this provider is active by default.
                                 All paid/unimplemented providers must be False.
        source_of_truth_priority: Lower = higher priority. 1 = most trusted.
        limitations:             Known caveats, restrictions, and data gaps.
        notes:                   Freeform notes about adapter status and future work.
    """
    provider_id: str
    display_name: str
    cost_tier: CostTier
    trust_tier: TrustTier
    supported_lanes: FrozenSet[str]
    max_stale_age_hours: Dict[str, float]
    requires_api_key: bool
    default_enabled: bool
    source_of_truth_priority: int
    limitations: List[str] = field(default_factory=list)
    notes: str = ""


# ── Registry ──────────────────────────────────────────────────────────────────

_REGISTRY: Dict[str, EvidenceProviderEntry] = {

    "sec_edgar": EvidenceProviderEntry(
        provider_id="sec_edgar",
        display_name="SEC EDGAR (public filings API)",
        cost_tier=CostTier.FREE,
        trust_tier=TrustTier.OFFICIAL,
        supported_lanes=frozenset({LANE_SEC_FILING}),
        max_stale_age_hours={
            LANE_SEC_FILING: 168.0,     # 7 days — filings update quarterly at most
        },
        requires_api_key=False,         # public API; User-Agent header required (not a key)
        default_enabled=True,
        source_of_truth_priority=1,     # highest priority: official government source
        limitations=[
            "Requires a declared User-Agent header per SEC terms of service.",
            "Rate-limited to approximately 10 requests/second by SEC.",
            "Not applicable to ETF/fund/crypto tickers — SEC EDGAR is company-only.",
            "CompanyFacts XBRL coverage varies; not all metrics available for every ticker.",
            "No real-time data — filings reflect last reported period.",
            (
                "XBRL company-facts (authoritative fundamental metrics) are a separate "
                "data product from the evidence_lane_runner's fundamentals lane; they "
                "will be registered as a sec_company_facts sub-lane when wired."
            ),
        ],
        notes=(
            "Existing adapter: sec_edgar_provider.py (filings + submissions + companyfacts). "
            "Currently wired only via the earnings_reviewer path "
            "(intel_v3_earnings_reviewer_sec_enabled). The evidence_lane_runner's "
            "fundamentals lane uses yfinance (the only wired adapter). SEC EDGAR "
            "company facts (XBRL) will be a separate sec_company_facts lane when "
            "that adapter is wired into the evidence lane runner."
        ),
    ),

    "yfinance": EvidenceProviderEntry(
        provider_id="yfinance",
        display_name="Yahoo Finance (yfinance, unofficial aggregator)",
        cost_tier=CostTier.FREE,
        trust_tier=TrustTier.UNOFFICIAL_AGGREGATOR,
        supported_lanes=frozenset({
            LANE_FUNDAMENTALS,
            LANE_TECHNICALS,
            LANE_NEWS_SENTIMENT,
        }),
        max_stale_age_hours={
            LANE_FUNDAMENTALS: 24.0,    # daily refresh adequate for fundamentals baseline
            LANE_TECHNICALS: 24.0,      # 3-month history refreshed daily
            LANE_NEWS_SENTIMENT: 1.0,   # news: 1-hour freshness SLA
        },
        requires_api_key=False,
        default_enabled=True,
        source_of_truth_priority=20,    # free baseline; lower priority than official sources
        limitations=[
            "Unofficial aggregator — not endorsed by Yahoo Finance; personal use per Yahoo ToS.",
            "Data may be delayed (15–20 minutes for prices; longer for fundamentals).",
            "No analyst consensus depth — only thin scalars (recommendation_mean, target_mean_price).",
            "News provides headlines only; no sentiment scoring or full article text.",
            "No options data, real-time feed, order book, or level-2 data.",
            "Rate-limited informally; circuit breaker required for production use.",
        ],
        notes=(
            "Existing implementation in data_sources.py: "
            "fetch_yfinance_fundamentals_sync, fetch_yfinance_history_sync, "
            "fetch_yfinance_news_sync. Currently the free baseline for the three "
            "Stage 5F evidence lanes (fundamentals, technicals, news_sentiment)."
        ),
    ),

    "fred": EvidenceProviderEntry(
        provider_id="fred",
        display_name="FRED (Federal Reserve Economic Data)",
        cost_tier=CostTier.FREE,
        trust_tier=TrustTier.OFFICIAL,
        supported_lanes=frozenset({LANE_MACRO}),
        max_stale_age_hours={
            LANE_MACRO: 24.0,
        },
        requires_api_key=False,         # free API; key optional for higher rate limits
        default_enabled=False,          # no FRED client adapter in repo yet
        source_of_truth_priority=2,     # official macro source; high priority when enabled
        limitations=[
            "METADATA-ONLY — no FRED client adapter implemented in this repo.",
            "No network calls until a FRED adapter is added and this entry is enabled.",
            "FRED covers macro indicators (rates, CPI, GDP) not company fundamentals.",
            "API key required for production rate limits (free key available).",
        ],
        notes="Planned for the macro evidence lane. Enable after FRED adapter is implemented.",
    ),

    "fmp": EvidenceProviderEntry(
        provider_id="fmp",
        display_name="Financial Modeling Prep (FMP)",
        cost_tier=CostTier.PAID,
        trust_tier=TrustTier.BROAD_FINANCIAL_VENDOR,
        supported_lanes=frozenset({
            LANE_FUNDAMENTALS,
            LANE_TECHNICALS,
            LANE_NEWS_SENTIMENT,
            LANE_ANALYST_REVISIONS,
            LANE_SEC_FILING,
            LANE_TRANSCRIPTS,
        }),
        max_stale_age_hours={
            LANE_FUNDAMENTALS: 24.0,
            LANE_TECHNICALS: 24.0,
            LANE_ANALYST_REVISIONS: 168.0,
        },
        requires_api_key=True,          # FMP_API_KEY required
        default_enabled=False,          # DISABLED — metadata-only; no ROI justification yet
        source_of_truth_priority=50,
        limitations=[
            "DISABLED — metadata-only candidate. Not called until explicitly enabled.",
            "No FMP client adapter implemented in this repo.",
            "Requires FMP_API_KEY environment variable.",
            "Paid tier required for most endpoints beyond the free starter plan.",
        ],
        notes=(
            "Wide lane coverage candidate. Enable only after free-source gaps are confirmed "
            "and cost model is approved. Do not activate until default_enabled=True."
        ),
    ),

    "eodhd": EvidenceProviderEntry(
        provider_id="eodhd",
        display_name="EOD Historical Data (EODHD)",
        cost_tier=CostTier.LOW_COST,
        trust_tier=TrustTier.BROAD_FINANCIAL_VENDOR,
        supported_lanes=frozenset({
            LANE_FUNDAMENTALS,
            LANE_TECHNICALS,
            LANE_ANALYST_REVISIONS,
        }),
        max_stale_age_hours={
            LANE_FUNDAMENTALS: 24.0,
            LANE_TECHNICALS: 24.0,
            LANE_ANALYST_REVISIONS: 168.0,
        },
        requires_api_key=True,          # EODHD_API_KEY required
        default_enabled=False,          # DISABLED — metadata-only candidate
        source_of_truth_priority=60,
        limitations=[
            "DISABLED — metadata-only candidate. Not called until explicitly enabled.",
            "No EODHD client adapter implemented in this repo.",
            "Requires EODHD_API_KEY environment variable.",
            "Low-cost but not free — paid subscription required.",
        ],
        notes=(
            "Strong candidate for analyst_revisions lane. Enable after free-source gaps "
            "are confirmed and cost model is approved."
        ),
    ),

    "alpha_vantage": EvidenceProviderEntry(
        provider_id="alpha_vantage",
        display_name="Alpha Vantage",
        cost_tier=CostTier.LOW_COST,
        trust_tier=TrustTier.BROAD_FINANCIAL_VENDOR,
        supported_lanes=frozenset({
            LANE_FUNDAMENTALS,
            LANE_TECHNICALS,
            LANE_MACRO,
        }),
        max_stale_age_hours={
            LANE_FUNDAMENTALS: 24.0,
            LANE_TECHNICALS: 24.0,
            LANE_MACRO: 24.0,
        },
        requires_api_key=True,          # ALPHA_VANTAGE_API_KEY required
        default_enabled=False,          # DISABLED — metadata-only candidate
        source_of_truth_priority=70,
        limitations=[
            "DISABLED — metadata-only candidate. Not called until explicitly enabled.",
            "No Alpha Vantage client adapter implemented in this repo.",
            "Requires ALPHA_VANTAGE_API_KEY environment variable.",
            "Free tier is severely rate-limited (5 calls/minute, 100 calls/day).",
            "Paid plans required for meaningful production throughput.",
        ],
        notes=(
            "Limited candidate for fundamentals/technicals. Rate limits make the free tier "
            "unsuitable for production workloads. Enable after ROI is justified."
        ),
    ),
}


# ── Read API ──────────────────────────────────────────────────────────────────

def list_providers() -> List[EvidenceProviderEntry]:
    """Return all registered provider entries."""
    return list(_REGISTRY.values())


def get_provider(provider_id: str) -> Optional[EvidenceProviderEntry]:
    """Return the provider entry for the given id, or None if not registered."""
    return _REGISTRY.get(provider_id)


def providers_for_lane(lane: str) -> List[EvidenceProviderEntry]:
    """Return all providers that support the given lane, sorted by priority.

    Includes both enabled and disabled providers.
    Sort order: (source_of_truth_priority ASC, provider_id ASC).
    """
    matches = [p for p in _REGISTRY.values() if lane in p.supported_lanes]
    return sorted(matches, key=lambda p: (p.source_of_truth_priority, p.provider_id))


def enabled_providers_for_lane(lane: str) -> List[EvidenceProviderEntry]:
    """Return only default-enabled providers for the given lane, sorted by priority.

    Disabled providers (default_enabled=False) are never included.
    """
    return [p for p in providers_for_lane(lane) if p.default_enabled]


def disabled_paid_providers() -> List[EvidenceProviderEntry]:
    """Return all providers that are disabled and have a non-FREE cost tier.

    These are the 'metadata-only candidates' — registered but never called.
    """
    return [
        p for p in _REGISTRY.values()
        if not p.default_enabled and p.cost_tier != CostTier.FREE
    ]


def build_registry_summary() -> dict:
    """Return a diagnostics-safe registry summary.

    Never includes raw metric values, provider keys, or credentials.
    safe_for_decision is always False — the registry is metadata-only.
    """
    providers = list_providers()
    enabled = [p for p in providers if p.default_enabled]
    disabled = [p for p in providers if not p.default_enabled]
    paid_disabled = disabled_paid_providers()

    lane_coverage: Dict[str, dict] = {}
    for lane in sorted(ALL_LANES):
        lane_provs = providers_for_lane(lane)
        lane_enabled = [p for p in lane_provs if p.default_enabled]
        lane_coverage[lane] = {
            "total_providers": len(lane_provs),
            "enabled_providers": len(lane_enabled),
            "primary_provider": lane_enabled[0].provider_id if lane_enabled else None,
            "has_free_official": any(
                p.cost_tier == CostTier.FREE and p.trust_tier == TrustTier.OFFICIAL
                for p in lane_enabled
            ),
        }

    return {
        "registry_version": EVIDENCE_PROVIDER_REGISTRY_VERSION,
        "safe_for_decision": False,
        "total_providers": len(providers),
        "enabled_providers": len(enabled),
        "disabled_providers": len(disabled),
        "paid_disabled_candidates": len(paid_disabled),
        "paid_disabled_ids": sorted(p.provider_id for p in paid_disabled),
        "lane_coverage": lane_coverage,
        "cost_tier_counts": {
            tier.value: sum(1 for p in providers if p.cost_tier == tier)
            for tier in CostTier
        },
        "trust_tier_counts": {
            tier.value: sum(1 for p in providers if p.trust_tier == tier)
            for tier in TrustTier
        },
        "enabled_provider_ids": sorted(p.provider_id for p in enabled),
    }
