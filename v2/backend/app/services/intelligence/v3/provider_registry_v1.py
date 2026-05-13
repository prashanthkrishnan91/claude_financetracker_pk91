"""Provider Registry (Stage 3.0b v1 — §3 of north-star).

Single source of truth for every market-data / fundamentals / filings /
analyst / portfolio source the platform may consume. Future code that
needs to know "which provider serves market_price?" or "is this provider
enabled in this env?" reads the registry — it does not hard-code the
answer.

Pure module — no IO, no env reads at import time, no provider clients
instantiated here. Env-gating is *described* by the registry, not enforced
inside it; callers (the orchestrator, refresh adapters) decide what to do
when a provider is env-disabled.

Reference: `docs/ai/INVESTMENT_INTELLIGENCE_PLATFORM_NORTH_STAR.md` §3.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional


# ── Source classes (north-star §3) ───────────────────────────────────────────

# Stable names referenced across the codebase. Keep in sync with the canonical
# list in `evidence_freshness_contract_v1.SOURCE_*` where they overlap.

SC_MARKET_PRICE = "market_price"
SC_MARKET_HISTORY = "market_history"
SC_MARKET_META = "market_meta"
SC_FUNDAMENTALS = "fundamentals"
SC_FILINGS = "filings"
SC_EARNINGS_CALENDAR = "earnings_calendar"
SC_NEWS_SENTIMENT = "news_sentiment"
SC_ANALYST_ESTIMATES = "analyst_estimates"
SC_ETF_HOLDINGS = "etf_holdings"
SC_CRYPTO = "crypto"
SC_MACRO = "macro"
SC_UNIVERSE_SCREENER = "universe_screener"
SC_ANALYST_THESIS = "analyst_thesis"
SC_PORTFOLIO_STATE = "portfolio_state"

SOURCE_CLASSES: frozenset[str] = frozenset({
    SC_MARKET_PRICE, SC_MARKET_HISTORY, SC_MARKET_META,
    SC_FUNDAMENTALS, SC_FILINGS, SC_EARNINGS_CALENDAR,
    SC_NEWS_SENTIMENT, SC_ANALYST_ESTIMATES, SC_ETF_HOLDINGS,
    SC_CRYPTO, SC_MACRO, SC_UNIVERSE_SCREENER,
    SC_ANALYST_THESIS, SC_PORTFOLIO_STATE,
})


# ── Failure / cost / test enums (string-typed for diagnostics legibility) ────

FAILURE_DEGRADE = "degrade"
FAILURE_ERROR = "error"
FAILURE_CIRCUIT_BREAK = "circuit_break"

COST_FREE = "free"
COST_PAID = "paid"
COST_PERSONAL_USE_ONLY = "personal-use-only"

TEST_STUB = "stub"
TEST_RECORDED = "recorded"
TEST_LIVE_SAFE = "live_safe"


# ── Registry row ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ProviderRecord:
    provider_id: str
    display_name: str
    source_classes: tuple[str, ...]
    env_var_name: Optional[str]  # None when the provider is keyless or internal
    freshness_sla_hours: dict[str, float] = field(default_factory=dict)
    rate_limit_per_minute: Optional[int] = None
    rate_limit_per_day: Optional[int] = None
    batch_supported: bool = False
    max_batch_size: Optional[int] = None
    fallback_priority: int = 100  # lower = first try
    failure_mode: str = FAILURE_DEGRADE
    cost_tier: str = COST_FREE
    commercial_caveat: Optional[str] = None
    test_strategy: str = TEST_STUB
    notes: Optional[str] = None

    def is_enabled(self, env: Optional[dict[str, str]] = None) -> bool:
        """True iff this provider is keyless OR its env var is set and non-empty.

        Pure: pass an explicit env dict in tests; in production we read os.environ.
        """
        if self.env_var_name is None:
            return True
        env = env if env is not None else dict(os.environ)
        val = env.get(self.env_var_name)
        return bool(val and val.strip())


# ── Seed registry (known providers in this repo today) ───────────────────────
#
# Adding a new provider = adding a record below. No other surface changes.

_REGISTRY: dict[str, ProviderRecord] = {
    "yfinance": ProviderRecord(
        provider_id="yfinance",
        display_name="Yahoo Finance (yfinance)",
        source_classes=(SC_MARKET_PRICE, SC_MARKET_HISTORY, SC_MARKET_META),
        env_var_name=None,  # keyless
        freshness_sla_hours={SC_MARKET_PRICE: 0.25, SC_MARKET_HISTORY: 24.0, SC_MARKET_META: 24.0},
        rate_limit_per_minute=60,
        batch_supported=True,
        max_batch_size=200,
        fallback_priority=20,
        failure_mode=FAILURE_CIRCUIT_BREAK,
        cost_tier=COST_FREE,
        commercial_caveat="personal-use only per Yahoo ToS",
        test_strategy=TEST_RECORDED,
    ),
    "alpaca": ProviderRecord(
        provider_id="alpaca",
        display_name="Alpaca Markets",
        source_classes=(SC_MARKET_PRICE, SC_MARKET_HISTORY),
        env_var_name="ALPACA_API_KEY",
        freshness_sla_hours={SC_MARKET_PRICE: 0.25, SC_MARKET_HISTORY: 24.0},
        rate_limit_per_minute=200,
        batch_supported=True,
        max_batch_size=200,
        fallback_priority=10,
        failure_mode=FAILURE_CIRCUIT_BREAK,
        cost_tier=COST_FREE,
        test_strategy=TEST_STUB,
    ),
    "finnhub": ProviderRecord(
        provider_id="finnhub",
        display_name="Finnhub",
        source_classes=(SC_MARKET_PRICE, SC_FUNDAMENTALS, SC_NEWS_SENTIMENT),
        env_var_name="FINNHUB_API_KEY",
        freshness_sla_hours={SC_MARKET_PRICE: 0.25, SC_FUNDAMENTALS: 336.0},
        rate_limit_per_minute=60,
        batch_supported=False,
        fallback_priority=30,
        failure_mode=FAILURE_CIRCUIT_BREAK,
        cost_tier=COST_FREE,
        test_strategy=TEST_STUB,
    ),
    "polygon": ProviderRecord(
        provider_id="polygon",
        display_name="Polygon.io",
        source_classes=(SC_MARKET_PRICE, SC_MARKET_HISTORY),
        env_var_name="POLYGON_API_KEY",
        freshness_sla_hours={SC_MARKET_PRICE: 0.25, SC_MARKET_HISTORY: 24.0},
        rate_limit_per_minute=300,
        batch_supported=True,
        max_batch_size=100,
        fallback_priority=40,
        failure_mode=FAILURE_CIRCUIT_BREAK,
        cost_tier=COST_FREE,
        test_strategy=TEST_STUB,
    ),
    "coingecko": ProviderRecord(
        provider_id="coingecko",
        display_name="CoinGecko",
        source_classes=(SC_CRYPTO,),
        env_var_name=None,  # keyless
        freshness_sla_hours={SC_CRYPTO: 0.25},
        rate_limit_per_minute=30,
        batch_supported=True,
        max_batch_size=100,
        fallback_priority=10,
        failure_mode=FAILURE_DEGRADE,
        cost_tier=COST_FREE,
        commercial_caveat="rate-limit aggressive; tight TTL + stale fallback",
        test_strategy=TEST_RECORDED,
    ),
    "sec_edgar": ProviderRecord(
        provider_id="sec_edgar",
        display_name="SEC EDGAR (submissions / company facts)",
        source_classes=(SC_FILINGS, SC_FUNDAMENTALS),
        env_var_name=None,  # public; UA header required
        freshness_sla_hours={SC_FILINGS: 168.0, SC_FUNDAMENTALS: 336.0},
        rate_limit_per_minute=600,
        batch_supported=False,
        fallback_priority=10,
        failure_mode=FAILURE_DEGRADE,
        cost_tier=COST_FREE,
        test_strategy=TEST_RECORDED,
    ),
    "sec_companyfacts": ProviderRecord(
        provider_id="sec_companyfacts",
        display_name="SEC EDGAR companyfacts (XBRL)",
        source_classes=(SC_FUNDAMENTALS,),
        env_var_name=None,
        freshness_sla_hours={SC_FUNDAMENTALS: 336.0},
        rate_limit_per_minute=600,
        batch_supported=False,
        fallback_priority=10,
        failure_mode=FAILURE_DEGRADE,
        cost_tier=COST_FREE,
        test_strategy=TEST_RECORDED,
    ),
    "agent_orchestrator": ProviderRecord(
        provider_id="agent_orchestrator",
        display_name="Internal AgentOrchestrator (Anthropic)",
        source_classes=(SC_ANALYST_THESIS,),
        env_var_name="ANTHROPIC_API_KEY",
        freshness_sla_hours={SC_ANALYST_THESIS: 48.0},
        rate_limit_per_minute=10,
        batch_supported=False,
        fallback_priority=20,
        failure_mode=FAILURE_DEGRADE,
        cost_tier=COST_PAID,
        test_strategy=TEST_STUB,
        notes="Stage 3.0b v1: NOT yet injected as orchestrator.analyst_refresh; "
              "Stage 3.0b.6 wires this via a narrow adapter.",
    ),
    "claude_analyst": ProviderRecord(
        provider_id="claude_analyst",
        display_name="Claude (analyst surface — reused via AgentOrchestrator)",
        source_classes=(SC_ANALYST_THESIS,),
        env_var_name="ANTHROPIC_API_KEY",
        freshness_sla_hours={SC_ANALYST_THESIS: 48.0},
        rate_limit_per_minute=10,
        batch_supported=False,
        fallback_priority=20,
        failure_mode=FAILURE_DEGRADE,
        cost_tier=COST_PAID,
        test_strategy=TEST_STUB,
        notes="Aliased to agent_orchestrator; future Claude-direct adapters share the same source_class.",
    ),
    "research_workers": ProviderRecord(
        provider_id="research_workers",
        display_name="Internal research_workers (SEC + earnings adapters)",
        source_classes=(SC_FILINGS, SC_EARNINGS_CALENDAR, SC_FUNDAMENTALS),
        env_var_name=None,
        freshness_sla_hours={SC_FILINGS: 168.0, SC_EARNINGS_CALENDAR: 168.0, SC_FUNDAMENTALS: 336.0},
        rate_limit_per_minute=None,
        batch_supported=False,
        fallback_priority=15,
        failure_mode=FAILURE_DEGRADE,
        cost_tier=COST_FREE,
        test_strategy=TEST_RECORDED,
    ),
    "portfolio_service": ProviderRecord(
        provider_id="portfolio_service",
        display_name="Internal PortfolioService (positions / snapshots / targets)",
        source_classes=(SC_PORTFOLIO_STATE,),
        env_var_name=None,
        freshness_sla_hours={SC_PORTFOLIO_STATE: 24.0},
        rate_limit_per_minute=None,
        batch_supported=True,
        fallback_priority=0,
        failure_mode=FAILURE_ERROR,  # missing portfolio is a hard error
        cost_tier=COST_FREE,
        test_strategy=TEST_STUB,
    ),
}


# ── Read API ─────────────────────────────────────────────────────────────────

def list_providers() -> list[ProviderRecord]:
    return list(_REGISTRY.values())


def get_provider(provider_id: str) -> Optional[ProviderRecord]:
    return _REGISTRY.get(provider_id)


def providers_for_source_class(source_class: str) -> list[ProviderRecord]:
    """All providers that serve this source class, ordered by fallback_priority."""
    matches = [p for p in _REGISTRY.values() if source_class in p.source_classes]
    return sorted(matches, key=lambda p: (p.fallback_priority, p.provider_id))


def enabled_providers(env: Optional[dict[str, str]] = None) -> list[ProviderRecord]:
    return [p for p in _REGISTRY.values() if p.is_enabled(env)]


def health_summary(env: Optional[dict[str, str]] = None) -> dict[str, Any]:
    """Compact registry health summary for snapshot diagnostics.

    Returns counts per source class:
      - providers_total
      - providers_enabled
      - fallback_provider (the highest-priority enabled provider, if any)
    Plus a flat list of disabled providers (with the env var that would enable them).
    """
    env_present = env if env is not None else dict(os.environ)
    by_class: dict[str, dict[str, Any]] = {}
    for sc in sorted(SOURCE_CLASSES):
        provs = providers_for_source_class(sc)
        enabled = [p for p in provs if p.is_enabled(env_present)]
        by_class[sc] = {
            "providers_total":    len(provs),
            "providers_enabled":  len(enabled),
            "fallback_provider":  enabled[0].provider_id if enabled else None,
        }
    disabled = [
        {"provider_id": p.provider_id, "env_var_name": p.env_var_name}
        for p in _REGISTRY.values()
        if not p.is_enabled(env_present)
    ]
    return {
        "by_source_class":   by_class,
        "disabled_providers": disabled,
        "total_providers":    len(_REGISTRY),
        "enabled_providers":  sum(1 for p in _REGISTRY.values() if p.is_enabled(env_present)),
    }
