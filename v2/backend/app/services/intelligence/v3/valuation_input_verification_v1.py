"""Phase 14B — Valuation Input Verification v1 (read-only stored-data diagnostics).

Purpose:
    Verifies the actual stored inputs needed for future FY EPS earnings-yield
    computation for each portfolio ticker. Returns aggregate-only counts by
    verification dimension.

    Key difference from Phase 14A:
        Phase 14A inferred EPS availability from Phase 9 bucket readiness.
        Phase 14B verifies raw EPS facts from actual stored research_artifact_facts
        records — a direct check of the stored data, not an inference.

    This module is DIAGNOSTICS-ONLY. It does NOT:
        - Compute valuation ratios (P/E, P/B, EV/EBITDA, FCF yield, earnings yield).
        - Produce PriceBand contributions.
        - Modify DecisionInputV3.
        - Change visible Buy/Hold/Trim/Sell decisions.
        - Call any external provider (yfinance, SEC, OpenAI, Anthropic).
        - Write to intel_v3_snapshots or any DB table.
        - Import or call decide() from decision_policy_v1.

    Verification dimensions:
        1. Raw EPS facts from stored research_artifact_facts.
           Tags: EarningsPerShareBasic, EarningsPerShareDiluted.
        2. Raw equity/book-value facts from stored research_artifact_facts.
           Tag: StockholdersEquity.
        3. Stored price availability and freshness from price_history.
           Fresh = within PRICE_STALE_THRESHOLD_DAYS. Stale = older. Missing = absent.
        4. Financial sector availability from stored records.
           NOTE: Financial sector from yfinance is NOT available from stored records
           in a per-ticker queryable form (intel_v3_snapshots stores a full payload
           blob, not per-ticker fundamentals). This is reported as a gap.

    TTM note:
        _MAX_PERIODS_PER_TAG = 2 in sec_companyfacts_parser means only up to
        2 periods per tag are stored. TTM (trailing twelve months) construction
        requires combining 4 quarterly periods. TTM is therefore blocked by the
        current period storage limit.

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
    - earnings_yield_computed is always False.
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

VALUATION_INPUT_VERIFICATION_V1_CONTRACT_VERSION = "phase14b_v1"

# Static inspection of sec_companyfacts_parser constants.
# _MAX_PERIODS_PER_TAG = 2 in the parser — TTM requires 4+ quarterly periods.
PERIOD_LIMIT_PER_TAG: int = 2
TTM_BLOCKED_BY_PERIOD_LIMIT: bool = True  # 2 < 4 periods needed for TTM

# Price freshness threshold: number of calendar days before a price is considered stale.
PRICE_STALE_THRESHOLD_DAYS: int = 7

# Source notes — stable string constants used in the response.
_STORED_PRICE_SOURCE = "price_history_table"
_FINANCIAL_SECTOR_SOURCE = (
    "not_available_only_portfolio_category"
    "_financial_sector_not_in_stored_per_ticker_records"
)


@dataclass(frozen=True)
class ValuationInputVerificationResult:
    """Aggregate-only Phase 14B valuation input verification result.

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
        earnings_yield_computed is always False.
        price_context_unchanged is always True.
    """
    adapter_version: str                          # always "phase14b_v1"
    safe_for_decision: bool                       # always False
    visible_snapshot_unchanged: bool              # always True
    read_only: bool                               # always True
    diagnostics_only: bool                        # always True
    valuation_ratios_computed: bool               # always False
    earnings_yield_computed: bool                 # always False
    price_context_unchanged: bool                 # always True

    portfolio_ticker_count: int
    company_ticker_count: int
    non_company_ticker_count: int

    sec_ready_count: int
    sec_partial_count: int
    sec_blocked_count: int

    # Raw EPS fact counts — verified from stored research_artifact_facts records.
    # This is a direct stored-record check, not a Phase 9 inference.
    raw_eps_fact_available_count: int       # tickers with any EPS fact (basic OR diluted)
    raw_eps_diluted_fact_available_count: int  # tickers with EarningsPerShareDiluted fact
    raw_eps_basic_fact_available_count: int    # tickers with EarningsPerShareBasic fact
    raw_equity_fact_available_count: int       # tickers with StockholdersEquity fact
    source_linked_eps_fact_count: int          # tickers with source-linked EPS fact
    source_linked_equity_fact_count: int       # tickers with source-linked equity fact

    # Stored price availability from price_history table.
    stored_price_available_count: int     # fresh + stale
    stored_price_fresh_count: int         # within PRICE_STALE_THRESHOLD_DAYS
    stored_price_stale_count: int         # older than PRICE_STALE_THRESHOLD_DAYS
    stored_price_missing_count: int       # no record in price_history
    stored_price_source: str              # always "price_history_table"

    # Financial sector availability.
    # NOTE: Financial sector (Technology/Healthcare/etc.) is from yfinance fundamentals
    # and is NOT available from stored records in a per-ticker queryable form.
    # intel_v3_snapshots stores a full payload blob — no per-ticker sector query.
    # positions.category provides portfolio category only (Core/ETF/etc.).
    # This is always reported as unavailable; future sector normalization is blocked.
    financial_sector_available_count: int  # always 0 (gap — not in stored per-ticker records)
    financial_sector_missing_count: int    # always company_ticker_count (gap)
    financial_sector_source: str           # always the gap note above

    # Future eligibility classification (not computed — verified-input counts only).
    # "verified eligible": raw EPS + stored price (any) + financial sector all verified.
    # "partial/degraded": some but not all inputs verified.
    # "blocked/unusable": SEC BLOCKED or no EPS + no price.
    eligible_for_future_fy_eps_yield_verified_count: int
    partial_or_degraded_input_count: int
    blocked_or_unusable_input_count: int

    non_company_excluded_count: int       # always equals non_company_ticker_count

    ttm_blocked_by_period_limit: bool     # always True (period_limit=2 < 4)
    period_limit_per_tag: int             # always 2

    errors: list[str] = field(default_factory=list)


def build_valuation_input_verification(
    readiness: "SecMetricEvidenceReadinessResult",
    eps_basic_tickers: set[str],
    eps_diluted_tickers: set[str],
    equity_tickers: set[str],
    source_linked_eps_tickers: set[str],
    source_linked_equity_tickers: set[str],
    fresh_price_tickers: set[str],
    stale_price_tickers: set[str],
    financial_sector_tickers: set[str],
    extra_errors: list[str] | None = None,
) -> ValuationInputVerificationResult:
    """Pure, deterministic, read-only Phase 14B valuation input verification.

    Verifies that actual stored inputs are present for future FY EPS earnings-yield
    computation. Returns aggregate-only counts by verification dimension.

    Args:
        readiness:                 Phase 9 SEC metric readiness result. Provides
                                   READY/PARTIAL/BLOCKED/SKIPPED counts.
        eps_basic_tickers:         Set of company tickers with a stored
                                   EarningsPerShareBasic fact record.
        eps_diluted_tickers:       Set of company tickers with a stored
                                   EarningsPerShareDiluted fact record.
        equity_tickers:            Set of company tickers with a stored
                                   StockholdersEquity fact record.
        source_linked_eps_tickers: Subset of eps tickers with a non-null source_id.
        source_linked_equity_tickers: Subset of equity tickers with a non-null source_id.
        fresh_price_tickers:       Set of company tickers with a price_history record
                                   within PRICE_STALE_THRESHOLD_DAYS.
        stale_price_tickers:       Set of company tickers with a price_history record
                                   older than PRICE_STALE_THRESHOLD_DAYS.
        financial_sector_tickers:  Set of company tickers with a verified financial
                                   sector from stored records (typically empty — gap).
        extra_errors:              Additional errors from the endpoint data-fetch layer.

    Returns:
        ValuationInputVerificationResult — always. Never raises.

    Invariants:
        safe_for_decision is always False.
        valuation_ratios_computed is always False.
        earnings_yield_computed is always False.
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
        return _build(
            readiness=readiness,
            eps_basic_tickers=eps_basic_tickers,
            eps_diluted_tickers=eps_diluted_tickers,
            equity_tickers=equity_tickers,
            source_linked_eps_tickers=source_linked_eps_tickers,
            source_linked_equity_tickers=source_linked_equity_tickers,
            fresh_price_tickers=fresh_price_tickers,
            stale_price_tickers=stale_price_tickers,
            financial_sector_tickers=financial_sector_tickers,
            extra_errors=extra_errors or [],
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("valuation_input_verification_v1_build_error error=%s", exc)
        return ValuationInputVerificationResult(
            adapter_version=VALUATION_INPUT_VERIFICATION_V1_CONTRACT_VERSION,
            safe_for_decision=False,
            visible_snapshot_unchanged=True,
            read_only=True,
            diagnostics_only=True,
            valuation_ratios_computed=False,
            earnings_yield_computed=False,
            price_context_unchanged=True,
            portfolio_ticker_count=0,
            company_ticker_count=0,
            non_company_ticker_count=0,
            sec_ready_count=0,
            sec_partial_count=0,
            sec_blocked_count=0,
            raw_eps_fact_available_count=0,
            raw_eps_diluted_fact_available_count=0,
            raw_eps_basic_fact_available_count=0,
            raw_equity_fact_available_count=0,
            source_linked_eps_fact_count=0,
            source_linked_equity_fact_count=0,
            stored_price_available_count=0,
            stored_price_fresh_count=0,
            stored_price_stale_count=0,
            stored_price_missing_count=0,
            stored_price_source=_STORED_PRICE_SOURCE,
            financial_sector_available_count=0,
            financial_sector_missing_count=0,
            financial_sector_source=_FINANCIAL_SECTOR_SOURCE,
            eligible_for_future_fy_eps_yield_verified_count=0,
            partial_or_degraded_input_count=0,
            blocked_or_unusable_input_count=0,
            non_company_excluded_count=0,
            ttm_blocked_by_period_limit=TTM_BLOCKED_BY_PERIOD_LIMIT,
            period_limit_per_tag=PERIOD_LIMIT_PER_TAG,
            errors=[f"build_error: {type(exc).__name__}: {exc}"],
        )


def _build(
    readiness: "SecMetricEvidenceReadinessResult",
    eps_basic_tickers: set[str],
    eps_diluted_tickers: set[str],
    equity_tickers: set[str],
    source_linked_eps_tickers: set[str],
    source_linked_equity_tickers: set[str],
    fresh_price_tickers: set[str],
    stale_price_tickers: set[str],
    financial_sector_tickers: set[str],
    extra_errors: list[str],
) -> ValuationInputVerificationResult:
    """Core verification logic — caller wraps in try/except."""
    errors: list[str] = list(readiness.errors) + list(extra_errors)

    # ── Company vs non-company classification ─────────────────────────────────
    non_company_ticker_count = readiness.skipped_non_company_count
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

    # ── Company ticker sets ───────────────────────────────────────────────────
    ready_set: set[str] = set(readiness.ready_tickers)
    partial_set: set[str] = set(readiness.partial_tickers_with_missing_groups.keys())
    blocked_set: set[str] = set(readiness.blocked_tickers_with_reason.keys())
    company_tickers_set: set[str] = ready_set | partial_set | blocked_set

    # ── Raw EPS and equity fact counts ────────────────────────────────────────
    # Intersect with company_tickers_set as a safety guard (endpoint filters
    # by company tickers when querying, but defensive intersection is cheap).
    eps_any_tickers = (eps_basic_tickers | eps_diluted_tickers) & company_tickers_set
    raw_eps_fact_available_count = len(eps_any_tickers)
    raw_eps_diluted_fact_available_count = len(eps_diluted_tickers & company_tickers_set)
    raw_eps_basic_fact_available_count = len(eps_basic_tickers & company_tickers_set)
    raw_equity_fact_available_count = len(equity_tickers & company_tickers_set)
    source_linked_eps_fact_count = len(source_linked_eps_tickers & company_tickers_set)
    source_linked_equity_fact_count = len(source_linked_equity_tickers & company_tickers_set)

    # ── Stored price availability from price_history ──────────────────────────
    fresh_price_company = fresh_price_tickers & company_tickers_set
    stale_price_company = stale_price_tickers & company_tickers_set
    price_any_company = fresh_price_company | stale_price_company
    stored_price_available_count = len(price_any_company)
    stored_price_fresh_count = len(fresh_price_company)
    stored_price_stale_count = len(stale_price_company)
    # Tickers in company_tickers_set with no price_history record.
    stored_price_missing_count = max(0, company_ticker_count - stored_price_available_count)

    # ── Financial sector availability ──────────────────────────────────────────
    # Financial sector (Technology, Healthcare, etc.) is stored in yfinance
    # fundamentals (intel_v3_snapshots payload). The payload is a full snapshot
    # blob — not queryable per-ticker in a diagnostics endpoint without deserializing
    # the entire payload. positions.category holds portfolio category only.
    # This gap is reported honestly: financial sector is currently unavailable
    # from stored per-ticker records without a live yfinance call.
    financial_sector_company = financial_sector_tickers & company_tickers_set
    financial_sector_available_count = len(financial_sector_company)
    financial_sector_missing_count = max(0, company_ticker_count - financial_sector_available_count)

    # ── Future eligibility classification ──────────────────────────────────────
    # "verified eligible": raw EPS + stored price (any) + financial sector all verified.
    # "partial/degraded": some but not all inputs verified.
    # "blocked/unusable": SEC BLOCKED always; or no EPS AND no price.
    eligible_for_future_fy_eps_yield_verified_count = 0
    partial_or_degraded_input_count = 0
    blocked_or_unusable_input_count = 0

    for ticker in company_tickers_set:
        if ticker in blocked_set:
            # SEC BLOCKED tickers cannot be eligible regardless of fact availability.
            blocked_or_unusable_input_count += 1
            continue

        has_eps = ticker in eps_any_tickers
        has_price = ticker in price_any_company
        has_sector = ticker in financial_sector_company

        if not has_eps and not has_price:
            # No EPS fact and no stored price — fully unusable.
            blocked_or_unusable_input_count += 1
        elif has_eps and has_price and has_sector:
            # All three inputs verified — fully eligible.
            eligible_for_future_fy_eps_yield_verified_count += 1
        else:
            # Partial: has some inputs but not all verified.
            partial_or_degraded_input_count += 1

    return ValuationInputVerificationResult(
        adapter_version=VALUATION_INPUT_VERIFICATION_V1_CONTRACT_VERSION,
        safe_for_decision=False,
        visible_snapshot_unchanged=True,
        read_only=True,
        diagnostics_only=True,
        valuation_ratios_computed=False,
        earnings_yield_computed=False,
        price_context_unchanged=True,
        portfolio_ticker_count=portfolio_ticker_count,
        company_ticker_count=company_ticker_count,
        non_company_ticker_count=non_company_ticker_count,
        sec_ready_count=sec_ready_count,
        sec_partial_count=sec_partial_count,
        sec_blocked_count=sec_blocked_count,
        raw_eps_fact_available_count=raw_eps_fact_available_count,
        raw_eps_diluted_fact_available_count=raw_eps_diluted_fact_available_count,
        raw_eps_basic_fact_available_count=raw_eps_basic_fact_available_count,
        raw_equity_fact_available_count=raw_equity_fact_available_count,
        source_linked_eps_fact_count=source_linked_eps_fact_count,
        source_linked_equity_fact_count=source_linked_equity_fact_count,
        stored_price_available_count=stored_price_available_count,
        stored_price_fresh_count=stored_price_fresh_count,
        stored_price_stale_count=stored_price_stale_count,
        stored_price_missing_count=stored_price_missing_count,
        stored_price_source=_STORED_PRICE_SOURCE,
        financial_sector_available_count=financial_sector_available_count,
        financial_sector_missing_count=financial_sector_missing_count,
        financial_sector_source=_FINANCIAL_SECTOR_SOURCE,
        eligible_for_future_fy_eps_yield_verified_count=eligible_for_future_fy_eps_yield_verified_count,
        partial_or_degraded_input_count=partial_or_degraded_input_count,
        blocked_or_unusable_input_count=blocked_or_unusable_input_count,
        non_company_excluded_count=non_company_ticker_count,
        ttm_blocked_by_period_limit=TTM_BLOCKED_BY_PERIOD_LIMIT,
        period_limit_per_tag=PERIOD_LIMIT_PER_TAG,
        errors=errors,
    )
