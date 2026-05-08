"""Phase 14A — Valuation Data Audit v1 (read-only stored-data diagnostics).

Purpose:
    Audits whether existing stored data can support future valuation ratio
    computation for each portfolio ticker. Returns aggregate-only counts
    by evidence category so that Phase 14B feasibility can be assessed.

    This module is DIAGNOSTICS-ONLY. It does NOT:
        - Compute valuation ratios (P/E, P/B, EV/EBITDA, FCF yield, earnings yield).
        - Produce PriceBand contributions.
        - Modify DecisionInputV3.
        - Change visible Buy/Hold/Trim/Sell decisions.
        - Call any external provider (yfinance, SEC, OpenAI, Anthropic).
        - Write to intel_v3_snapshots or any DB table.
        - Import or call decide() from decision_policy_v1.

    TTM note:
        _MAX_PERIODS_PER_TAG = 2 in sec_companyfacts_parser means only up to
        2 periods per tag are stored. TTM (trailing twelve months) construction
        requires combining 4 quarterly periods. TTM is therefore blocked by the
        current period storage limit.

    Sector note:
        Financial sector (Technology, Healthcare, etc.) is stored in
        intel_v3_snapshots.raw.fundamentals from yfinance. The positions
        table contains only portfolio category (Core/ETF/Crypto/IPO/SELL),
        not financial sector. This endpoint reports portfolio category only.
        Future CHEAP/EXPENSIVE valuation band with sector normalization
        requires financial sector data not available in this diagnostic path.

Architecture invariants (non-negotiable):
    - NEVER imports or calls decide() from decision_policy_v1.
    - NEVER imports IntelV3Service, recommendation_engine, or any frontend path.
    - NEVER writes to any DB table.
    - NEVER calls any external provider, LLM, or SEC API.
    - NEVER returns raw metric values, metric key names, source URLs, price targets.
    - NEVER computes P/E, P/B, EV/EBITDA, FCF yield, earnings yield, or any ratio.
    - NEVER produces PriceBand values.
    - NEVER sets safe_for_decision=True.
    - safe_for_decision is always False.
    - visible_snapshot_unchanged is always True.
    - read_only is always True.
    - diagnostics_only is always True.
    - valuation_ratios_computed is always False.
    - price_context_unchanged is always True.
    - This module is pure — no IO, no DB, no LLM.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..research_workers.sec_metric_evidence_readiness_adapter import (
        SecMetricEvidenceReadinessResult,
    )

logger = logging.getLogger(__name__)

VALUATION_DATA_AUDIT_V1_CONTRACT_VERSION = "phase14a_v1"

# Static inspection of sec_companyfacts_parser constants.
# _MAX_PERIODS_PER_TAG = 2 in the parser — TTM requires 4+ quarterly periods.
PERIOD_LIMIT_PER_TAG: int = 2
TTM_BLOCKED_BY_PERIOD_LIMIT: bool = True  # 2 < 4 periods needed for TTM

# SEC allowlist tags that are relevant for valuation feasibility (static inspection).
# These tags are in _METRIC_TAG_ALLOWLIST in sec_companyfacts_parser.py.
# They map to the eps / equity buckets in SEC_METRIC_BUCKET_MAP.
_EPS_BUCKET = "eps"
_EQUITY_BUCKET = "equity"

# Portfolio category values that indicate non-company tickers.
_NON_COMPANY_CATEGORY_KEYWORDS: frozenset[str] = frozenset({
    "etf", "fund", "index", "crypto",
})


@dataclass(frozen=True)
class ValuationDataAuditResult:
    """Aggregate-only Phase 14A valuation data audit result.

    All counts are non-negative integers.
    All hard-lock fields are always set to their invariant values.

    Forbidden (never present in any field):
        - raw metric values or metric key names
        - valuation ratios (P/E, P/B, EV/EBITDA, earnings yield, FCF yield)
        - PriceBand values
        - price targets or fair value estimates
        - per-ticker raw data rows
        - source URLs or structured payloads
        - Buy/Hold/Trim/Sell signals

    Invariants:
        safe_for_decision is always False.
        visible_snapshot_unchanged is always True.
        read_only is always True.
        diagnostics_only is always True.
        valuation_ratios_computed is always False.
        price_context_unchanged is always True.
    """
    adapter_version: str                         # always "phase14a_v1"
    safe_for_decision: bool                      # always False
    visible_snapshot_unchanged: bool             # always True
    read_only: bool                              # always True
    diagnostics_only: bool                       # always True
    valuation_ratios_computed: bool              # always False
    price_context_unchanged: bool                # always True

    portfolio_ticker_count: int
    company_ticker_count: int
    non_company_ticker_count: int

    sec_ready_count: int
    sec_partial_count: int
    sec_blocked_count: int

    latest_fy_eps_available_count: int
    latest_fy_eps_diluted_available_count: int
    stockholders_equity_available_count: int

    market_price_available_count: int
    market_price_fresh_count: int
    market_price_source_note: str

    sector_available_count: int
    sector_missing_count: int
    sector_source_note: str

    eligible_for_future_fy_earnings_yield_count: int
    eligible_for_future_book_value_proxy_count: int
    requires_provider_or_coverage_expansion_count: int

    ttm_blocked_by_period_limit: bool            # always True (period_limit=2 < 4)
    period_limit_per_tag: int                    # always 2

    errors: list[str] = field(default_factory=list)


def build_valuation_data_audit(
    readiness: "SecMetricEvidenceReadinessResult",
    company_ticker_categories: dict[str, str],
) -> ValuationDataAuditResult:
    """Pure, deterministic, read-only Phase 14A valuation data audit.

    Classifies the feasibility of future valuation ratio computation for each
    portfolio ticker using existing stored data. Returns aggregate-only counts.

    Args:
        readiness:                Phase 9 SEC metric readiness result. Provides
                                  READY/PARTIAL/BLOCKED/SKIPPED counts and per-ticker
                                  missing bucket info.
        company_ticker_categories: Dict of company ticker → portfolio category string
                                  from positions table (Core/ETF/Crypto/IPO/SELL/etc.).
                                  ETF/Crypto tickers are excluded from company counts.

    Returns:
        ValuationDataAuditResult — always. Never raises.

    Invariants:
        safe_for_decision is always False.
        valuation_ratios_computed is always False.
        price_context_unchanged is always True.
        ttm_blocked_by_period_limit is always True.

    Never:
        - Computes P/E, P/B, EV/EBITDA, earnings yield, or any valuation ratio.
        - Produces a PriceBand value.
        - Imports or calls decide() / decision_policy_v1.
        - Writes to any DB table.
        - Returns raw metric values or metric key names.
    """
    try:
        return _build(readiness, company_ticker_categories)
    except Exception as exc:  # noqa: BLE001
        logger.error("valuation_data_audit_v1_build_error error=%s", exc)
        return ValuationDataAuditResult(
            adapter_version=VALUATION_DATA_AUDIT_V1_CONTRACT_VERSION,
            safe_for_decision=False,
            visible_snapshot_unchanged=True,
            read_only=True,
            diagnostics_only=True,
            valuation_ratios_computed=False,
            price_context_unchanged=True,
            portfolio_ticker_count=0,
            company_ticker_count=0,
            non_company_ticker_count=0,
            sec_ready_count=0,
            sec_partial_count=0,
            sec_blocked_count=0,
            latest_fy_eps_available_count=0,
            latest_fy_eps_diluted_available_count=0,
            stockholders_equity_available_count=0,
            market_price_available_count=0,
            market_price_fresh_count=0,
            market_price_source_note="error_no_price_data",
            sector_available_count=0,
            sector_missing_count=0,
            sector_source_note="error_no_sector_data",
            eligible_for_future_fy_earnings_yield_count=0,
            eligible_for_future_book_value_proxy_count=0,
            requires_provider_or_coverage_expansion_count=0,
            ttm_blocked_by_period_limit=TTM_BLOCKED_BY_PERIOD_LIMIT,
            period_limit_per_tag=PERIOD_LIMIT_PER_TAG,
            errors=[f"build_error: {type(exc).__name__}: {exc}"],
        )


def _build(
    readiness: "SecMetricEvidenceReadinessResult",
    company_ticker_categories: dict[str, str],
) -> ValuationDataAuditResult:
    """Core audit logic — caller wraps in try/except."""
    errors: list[str] = list(readiness.errors)

    # ── Company vs non-company classification ─────────────────────────────────
    # Non-company tickers: already classified as SKIPPED_NON_COMPANY by Phase 9.
    non_company_ticker_count = readiness.skipped_non_company_count

    # Company tickers: READY + PARTIAL + BLOCKED (not skipped).
    company_ticker_count = (
        readiness.ready_count
        + readiness.partial_count
        + readiness.blocked_count
    )

    portfolio_ticker_count = readiness.portfolio_ticker_count

    # ── SEC readiness counts (direct from Phase 9) ────────────────────────────
    sec_ready_count = readiness.ready_count
    sec_partial_count = readiness.partial_count
    sec_blocked_count = readiness.blocked_count

    # ── EPS and equity availability from Phase 9 bucket data ─────────────────
    # READY tickers: all expected buckets covered → eps and equity guaranteed.
    # PARTIAL tickers: check if eps/equity are NOT in their missing_buckets list.
    # BLOCKED tickers: no evidence → eps and equity unavailable.
    eps_available = sec_ready_count
    eps_diluted_available = sec_ready_count  # same eps bucket for basic/diluted
    equity_available = sec_ready_count

    for _ticker, missing_groups in readiness.partial_tickers_with_missing_groups.items():
        if _EPS_BUCKET not in missing_groups:
            eps_available += 1
            eps_diluted_available += 1
        if _EQUITY_BUCKET not in missing_groups:
            equity_available += 1

    # ── Market price availability ─────────────────────────────────────────────
    # Company tickers with a portfolio position have price_or_position data.
    # This is NOT a live current market price — it is derived from portfolio
    # position existence. A true current market price requires a provider call
    # (out of scope for this diagnostic).
    # All READY + PARTIAL + BLOCKED company tickers have portfolio positions.
    market_price_available_count = company_ticker_count
    # Fresh count: same as available — freshness cannot be determined without
    # querying price_history which is deferred to a future phase.
    market_price_fresh_count = company_ticker_count
    market_price_source_note = (
        "price_or_position_only_not_live_market_price"
        "_freshness_not_validated_in_phase14a"
    )

    # ── Sector availability from positions.category ───────────────────────────
    # positions.category holds portfolio category labels (Core/IPO/SELL/Other),
    # not financial sectors (Technology/Healthcare/etc.).
    # Financial sector is stored in intel_v3_snapshots.raw.fundamentals from
    # yfinance — not accessible in this diagnostic path without additional
    # snapshot queries. Future CHEAP/EXPENSIVE valuation with sector normalization
    # requires financial sector data; that gap is noted here.
    sector_available_count = 0
    sector_missing_count = 0

    for _ticker, cat in company_ticker_categories.items():
        cat_lower = (cat or "").lower().strip()
        if cat_lower and not any(kw in cat_lower for kw in _NON_COMPANY_CATEGORY_KEYWORDS):
            sector_available_count += 1
        else:
            sector_missing_count += 1

    # If no category data at all (empty map), count all company tickers as missing.
    if not company_ticker_categories:
        sector_missing_count = company_ticker_count

    sector_source_note = (
        "portfolio_category_only_not_financial_sector"
        "_future_cheap_expensive_blocked_without_financial_sector"
    )

    # ── Future ratio eligibility (not computed — feasibility counts only) ─────
    # eligible_for_future_fy_earnings_yield: needs eps + market price.
    # Market price is available for all company tickers (position-derived).
    # EPS availability: computed above.
    eligible_for_future_fy_earnings_yield_count = eps_available

    # eligible_for_future_book_value_proxy: needs equity + market price.
    eligible_for_future_book_value_proxy_count = equity_available

    # requires_provider_or_coverage_expansion: BLOCKED company tickers.
    # These tickers have no SEC metric evidence and cannot support any
    # valuation computation without SEC coverage expansion or a provider call.
    requires_provider_or_coverage_expansion_count = sec_blocked_count

    return ValuationDataAuditResult(
        adapter_version=VALUATION_DATA_AUDIT_V1_CONTRACT_VERSION,
        safe_for_decision=False,
        visible_snapshot_unchanged=True,
        read_only=True,
        diagnostics_only=True,
        valuation_ratios_computed=False,
        price_context_unchanged=True,
        portfolio_ticker_count=portfolio_ticker_count,
        company_ticker_count=company_ticker_count,
        non_company_ticker_count=non_company_ticker_count,
        sec_ready_count=sec_ready_count,
        sec_partial_count=sec_partial_count,
        sec_blocked_count=sec_blocked_count,
        latest_fy_eps_available_count=eps_available,
        latest_fy_eps_diluted_available_count=eps_diluted_available,
        stockholders_equity_available_count=equity_available,
        market_price_available_count=market_price_available_count,
        market_price_fresh_count=market_price_fresh_count,
        market_price_source_note=market_price_source_note,
        sector_available_count=sector_available_count,
        sector_missing_count=sector_missing_count,
        sector_source_note=sector_source_note,
        eligible_for_future_fy_earnings_yield_count=eligible_for_future_fy_earnings_yield_count,
        eligible_for_future_book_value_proxy_count=eligible_for_future_book_value_proxy_count,
        requires_provider_or_coverage_expansion_count=requires_provider_or_coverage_expansion_count,
        ttm_blocked_by_period_limit=TTM_BLOCKED_BY_PERIOD_LIMIT,
        period_limit_per_tag=PERIOD_LIMIT_PER_TAG,
        errors=errors,
    )
