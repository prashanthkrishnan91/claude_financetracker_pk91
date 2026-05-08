"""Phase 8F — Shared SEC metric candidate classification helper.

Pure, deterministic. No DB reads, provider calls, LLM calls, or writes.

Provides classify_sec_metric_candidate() used by:
    - Phase 8D portfolio coverage diagnostics (sec_metric_portfolio_coverage_dry_run)
    - Phase 8E expansion candidate selection (sec_metric_coverage_expansion)

Phase 8F conservative diagnostic/selection override:
    Known non-company tickers are classified by symbol even when the portfolio
    category value is wrong or missing. This is an explicit, documented
    portfolio-specific list — not a general symbol master. Add new tickers
    here only after confirming they are permanently non-company assets.

Architecture invariants (non-negotiable):
    - NEVER imports or calls decide() from decision_policy_v1.
    - NEVER imports IntelV3Service, recommendation_engine, or any frontend path.
    - NEVER writes to any DB table.
    - NEVER calls any SEC provider, LLM, or external service.
    - safe_for_decision is never set in this module.
"""
from __future__ import annotations

# Category values that indicate ETF/fund asset type.
_ETF_CATEGORIES: frozenset[str] = frozenset({"ETF"})

# Category values that indicate crypto asset type.
_CRYPTO_CATEGORIES: frozenset[str] = frozenset({"Crypto"})

# Phase 8F conservative portfolio-specific override:
# Known fund/ETF-like tickers that may appear with wrong or missing category.
# VUG is included because it is miscategorized as non-ETF in some portfolio data.
KNOWN_FUND_OR_ETF_TICKERS: frozenset[str] = frozenset({
    "GLD",
    "QQQ",
    "SCHD",
    "SPY",
    "VGT",
    "VHT",
    "VIS",
    "VOO",
    "VTI",
    "VUG",   # miscategorized in portfolio data — Phase 8F override treats as fund/ETF-like
    "VXUS",
    "VYM",
    "XLE",
})

# Phase 8F conservative portfolio-specific override:
# Known crypto-like tickers that may appear with wrong or missing category.
KNOWN_CRYPTO_TICKERS: frozenset[str] = frozenset({
    "BTC",
    "XRP",
})


def classify_sec_metric_candidate(ticker: str, category: str) -> dict:
    """Classify whether a ticker is a SEC-company candidate.

    Pure, deterministic. No side effects.

    Classification priority:
      1. Category-based: ETF/Crypto category → non-company.
      2. Symbol-based override: known portfolio fund/ETF-like or crypto-like
         tickers → non-company, even if category is wrong or missing.
      3. All others: potentially SEC-company-like.

    Args:
        ticker:   Ticker symbol (uppercased internally).
        category: Position category value ("Core", "ETF", "Crypto", "Other", "").

    Returns:
        dict with:
            is_sec_company_candidate (bool) — True if eligible for SEC metric coverage.
            classification (str) — "likely_fund_or_etf" | "likely_crypto" | "sec_company_like"
            blocking_reason_codes (list[str]) — empty when is_sec_company_candidate=True.
    """
    ticker_upper = (ticker or "").upper().strip()

    # Priority 1: category-based classification.
    if category in _ETF_CATEGORIES or ticker_upper in KNOWN_FUND_OR_ETF_TICKERS:
        return {
            "is_sec_company_candidate": False,
            "classification": "likely_fund_or_etf",
            "blocking_reason_codes": ["asset_type_not_sec_company", "likely_fund_or_etf"],
        }

    if category in _CRYPTO_CATEGORIES or ticker_upper in KNOWN_CRYPTO_TICKERS:
        return {
            "is_sec_company_candidate": False,
            "classification": "likely_crypto",
            "blocking_reason_codes": ["asset_type_not_sec_company", "likely_crypto"],
        }

    return {
        "is_sec_company_candidate": True,
        "classification": "sec_company_like",
        "blocking_reason_codes": [],
    }
