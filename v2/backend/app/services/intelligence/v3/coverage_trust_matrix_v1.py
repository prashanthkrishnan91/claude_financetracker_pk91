"""Stage 9A — Coverage & Trust Matrix v1.

Deterministic, per-ticker mapper from Stage 5J coverage lane statuses to a
per-category matrix: STRONG / PARTIAL / WEAK / MISSING / NOT_APPLICABLE.

This is the foundation diagnostic layer for Stage 9. It maps what we already
know from Stage 5J/5K into a synthesis-gate-ready form — determining, per
ticker and per research category, whether coverage is strong enough for future
synthesis — WITHOUT running any synthesis, LLM, or provider call.

Architecture contracts (non-negotiable):
  - Pure mapper. Consumes ResearchEvidenceCoverageSummary (Stage 5J output).
    No DB reads, no LLM calls, no provider calls, no evidence runs, no writes.
  - NEVER calls decide(). NEVER imports decision_policy_v1.
  - safe_for_decision is permanently False. synthesis_ready is permanently False
    for Stage 9A (foundation layer only).
  - NOT_APPLICABLE categories never count as missing coverage for ETF/crypto.
  - Only STRONG or PARTIAL → allowed_for_synthesis=True.
  - WEAK and MISSING blocks are synthesis-suppressed.
  - No raw payloads, source URLs, fact contents, API keys, secrets, or PII.

Coverage categories:
  fundamentals, valuation, technicals, news_sentiment, sec_company_facts,
  sec_catalysts, etf_fund_composition, crypto_market_context,
  portfolio_sizing_target_weight, thesis_history

Matrix states:
  STRONG:          Grounded, fresh, primary/multi-source authority (e.g. SEC EDGAR).
  PARTIAL:         Grounded but thin / single-source / limited (e.g. yfinance READY,
                   or any LIMITED artifact).
  WEAK:            Present but low-trust: suppressed, stale, or not evaluable.
  MISSING:         No usable artifact. Synthesis must not run for this category.
  NOT_APPLICABLE:  Category does not apply to this asset type.

Mapping from Stage 5J status:
  READY + primary/official authority → STRONG
  READY + free/unofficial source    → PARTIAL
  LIMITED                           → PARTIAL
  SUPPRESSED / STALE_OR_UNKNOWN / NOT_EVALUABLE → WEAK
  MISSING                           → MISSING
  Asset-type rule triggers          → NOT_APPLICABLE
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from .research_evidence_coverage_read_model_v1 import (
    LANE_FUNDAMENTALS,
    LANE_MACRO_CONTEXT,
    LANE_NEWS_SENTIMENT,
    LANE_SEC_CATALYST_SENTIMENT,
    LANE_SEC_COMPANY_FACTS,
    LANE_TECHNICALS,
    STATUS_LIMITED,
    STATUS_MISSING,
    STATUS_NOT_EVALUABLE,
    STATUS_READY,
    STATUS_STALE_OR_UNKNOWN,
    STATUS_SUPPRESSED,
    LaneCoverage,
    ResearchEvidenceCoverageSummary,
)
from .research_evidence_decision_input_adapter_v1 import (
    INSTRUMENT_CATEGORY_CRYPTO,
    INSTRUMENT_CATEGORY_EQUITY,
    INSTRUMENT_CATEGORY_ETF,
    INSTRUMENT_CATEGORY_UNKNOWN,
    _classify_instrument_category,
)

logger = logging.getLogger(__name__)

MATRIX_VERSION = "coverage_trust_matrix.v1"

# ── Matrix states ──────────────────────────────────────────────────────────────

MATRIX_STRONG = "STRONG"
MATRIX_PARTIAL = "PARTIAL"
MATRIX_WEAK = "WEAK"
MATRIX_MISSING = "MISSING"
MATRIX_NOT_APPLICABLE = "NOT_APPLICABLE"

_SYNTHESIS_ALLOWED_STATES = frozenset({MATRIX_STRONG, MATRIX_PARTIAL})

# ── Research category constants ────────────────────────────────────────────────

CATEGORY_FUNDAMENTALS = "fundamentals"
CATEGORY_VALUATION = "valuation"
CATEGORY_TECHNICALS = "technicals"
CATEGORY_NEWS_SENTIMENT = "news_sentiment"
CATEGORY_SEC_COMPANY_FACTS = "sec_company_facts"
CATEGORY_SEC_CATALYSTS = "sec_catalysts"
CATEGORY_ETF_FUND_COMPOSITION = "etf_fund_composition"
CATEGORY_CRYPTO_MARKET_CONTEXT = "crypto_market_context"
CATEGORY_PORTFOLIO_SIZING_TARGET_WEIGHT = "portfolio_sizing_target_weight"
CATEGORY_THESIS_HISTORY = "thesis_history"

ALL_CATEGORIES: tuple[str, ...] = (
    CATEGORY_FUNDAMENTALS,
    CATEGORY_VALUATION,
    CATEGORY_TECHNICALS,
    CATEGORY_NEWS_SENTIMENT,
    CATEGORY_SEC_COMPANY_FACTS,
    CATEGORY_SEC_CATALYSTS,
    CATEGORY_ETF_FUND_COMPOSITION,
    CATEGORY_CRYPTO_MARKET_CONTEXT,
    CATEGORY_PORTFOLIO_SIZING_TARGET_WEIGHT,
    CATEGORY_THESIS_HISTORY,
)

# Source authority levels that qualify a READY lane for STRONG (vs PARTIAL).
_PRIMARY_AUTHORITY_LEVELS = frozenset({"PRIMARY_AUTHORITY", "OFFICIAL_FREE_SOURCE"})


# ── Typed output ───────────────────────────────────────────────────────────────


@dataclass
class CategoryMatrixEntry:
    """Matrix state for one (category, ticker) pair.

    Every field is safe to surface in a diagnostics response: no raw payloads,
    no source URLs, no fact contents, no API keys.
    """
    category: str
    state: str                         # STRONG | PARTIAL | WEAK | MISSING | NOT_APPLICABLE
    reason: str                        # short plain-English reason
    source_reference: Optional[str]    # lane name when derived from Stage 5J, else None
    allowed_for_synthesis: bool        # True only when state in {STRONG, PARTIAL}
    suppression_reason: Optional[str]  # set when allowed_for_synthesis is False and state != NOT_APPLICABLE

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "state": self.state,
            "reason": self.reason,
            "source_reference": self.source_reference,
            "allowed_for_synthesis": self.allowed_for_synthesis,
            "suppression_reason": self.suppression_reason,
        }


@dataclass
class TickerTrustMatrix:
    """Per-ticker Coverage & Trust Matrix.

    Diagnostic only. safe_for_decision and synthesis_ready are always False
    in Stage 9A.
    """
    ticker: str
    asset_type: str                    # equity | etf | crypto | unknown
    safe_for_decision: bool = False    # immutable
    synthesis_ready: bool = False      # immutable for Stage 9A foundation
    categories: dict[str, CategoryMatrixEntry] = field(default_factory=dict)
    allowed_blocks: list[str] = field(default_factory=list)    # STRONG/PARTIAL
    suppressed_blocks: list[str] = field(default_factory=list) # WEAK/MISSING
    strongest_gaps: list[str] = field(default_factory=list)    # WEAK/MISSING categories only

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "asset_type": self.asset_type,
            "safe_for_decision": self.safe_for_decision,
            "synthesis_ready": self.synthesis_ready,
            "categories": {k: v.to_dict() for k, v in self.categories.items()},
            "allowed_blocks": list(self.allowed_blocks),
            "suppressed_blocks": list(self.suppressed_blocks),
            "strongest_gaps": list(self.strongest_gaps),
        }


@dataclass
class CoverageTrustMatrixResult:
    """Portfolio-wide Coverage & Trust Matrix output.

    Diagnostic only. safe_for_decision is always False.
    """
    schema_version: str
    matrix_version: str
    user_id: str
    generated_at: str
    safe_for_decision: bool = False    # immutable
    synthesis_ready: bool = False      # immutable for Stage 9A
    portfolio_ticker_count: int = 0
    tickers: dict[str, TickerTrustMatrix] = field(default_factory=dict)
    portfolio_allowed_block_counts: dict[str, int] = field(default_factory=dict)
    portfolio_suppressed_block_counts: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "matrix_version": self.matrix_version,
            "user_id": self.user_id,
            "generated_at": self.generated_at,
            "safe_for_decision": self.safe_for_decision,
            "synthesis_ready": self.synthesis_ready,
            "portfolio_ticker_count": self.portfolio_ticker_count,
            "tickers": {k: v.to_dict() for k, v in self.tickers.items()},
            "portfolio_allowed_block_counts": dict(self.portfolio_allowed_block_counts),
            "portfolio_suppressed_block_counts": dict(self.portfolio_suppressed_block_counts),
            "errors": list(self.errors),
        }


# ── Public API ─────────────────────────────────────────────────────────────────


def compute_coverage_trust_matrix(
    coverage: ResearchEvidenceCoverageSummary,
    *,
    holding_context_by_ticker: Optional[dict[str, dict]] = None,
) -> CoverageTrustMatrixResult:
    """Map Stage 5J coverage output to a per-ticker Coverage & Trust Matrix.

    Args:
        coverage: Stage 5J ResearchEvidenceCoverageSummary (read-only input).
        holding_context_by_ticker: Optional {ticker: {"category": ..., "asset_type": ...}}
            used to determine asset type per ticker. When absent, conservative
            symbol-based fallback is used (via Stage 5K classifier).

    Returns:
        CoverageTrustMatrixResult — always non-None, always safe_for_decision=False,
        always synthesis_ready=False.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    ctx = holding_context_by_ticker or {}

    tickers: dict[str, TickerTrustMatrix] = {}
    portfolio_allowed: dict[str, int] = {c: 0 for c in ALL_CATEGORIES}
    portfolio_suppressed: dict[str, int] = {c: 0 for c in ALL_CATEGORIES}

    for ticker, ticker_cov in coverage.ticker_coverage.items():
        lanes = ticker_cov.lanes
        holding_ctx = ctx.get(ticker)
        asset_type = _classify_instrument_category(ticker, holding_ctx)

        category_entries = _build_all_categories(
            ticker=ticker,
            lanes=lanes,
            asset_type=asset_type,
        )

        allowed = [c for c, e in category_entries.items() if e.allowed_for_synthesis]
        suppressed = [
            c for c, e in category_entries.items()
            if not e.allowed_for_synthesis and e.state != MATRIX_NOT_APPLICABLE
        ]
        gaps = [
            c for c, e in category_entries.items()
            if e.state in (MATRIX_WEAK, MATRIX_MISSING)
        ]

        tickers[ticker] = TickerTrustMatrix(
            ticker=ticker,
            asset_type=asset_type,
            safe_for_decision=False,
            synthesis_ready=False,
            categories=category_entries,
            allowed_blocks=allowed,
            suppressed_blocks=suppressed,
            strongest_gaps=gaps,
        )

        for cat in allowed:
            portfolio_allowed[cat] = portfolio_allowed.get(cat, 0) + 1
        for cat in suppressed:
            portfolio_suppressed[cat] = portfolio_suppressed.get(cat, 0) + 1

    return CoverageTrustMatrixResult(
        schema_version=MATRIX_VERSION,
        matrix_version=MATRIX_VERSION,
        user_id=coverage.user_id,
        generated_at=now_iso,
        safe_for_decision=False,
        synthesis_ready=False,
        portfolio_ticker_count=coverage.portfolio_ticker_count,
        tickers=tickers,
        portfolio_allowed_block_counts=portfolio_allowed,
        portfolio_suppressed_block_counts=portfolio_suppressed,
        errors=list(coverage.errors),
    )


# ── Category builders ──────────────────────────────────────────────────────────


def _build_all_categories(
    *,
    ticker: str,
    lanes: dict[str, LaneCoverage],
    asset_type: str,
) -> dict[str, CategoryMatrixEntry]:
    return {
        CATEGORY_FUNDAMENTALS: _build_fundamentals(lanes, asset_type),
        CATEGORY_VALUATION: _build_valuation(asset_type),
        CATEGORY_TECHNICALS: _build_technicals(lanes, asset_type),
        CATEGORY_NEWS_SENTIMENT: _build_news_sentiment(lanes, asset_type),
        CATEGORY_SEC_COMPANY_FACTS: _build_sec_company_facts(lanes, asset_type),
        CATEGORY_SEC_CATALYSTS: _build_sec_catalysts(lanes, asset_type),
        CATEGORY_ETF_FUND_COMPOSITION: _build_etf_fund_composition(asset_type),
        CATEGORY_CRYPTO_MARKET_CONTEXT: _build_crypto_market_context(lanes, asset_type),
        CATEGORY_PORTFOLIO_SIZING_TARGET_WEIGHT: _build_portfolio_sizing(asset_type),
        CATEGORY_THESIS_HISTORY: _build_thesis_history(asset_type),
    }


def _build_fundamentals(
    lanes: dict[str, LaneCoverage],
    asset_type: str,
) -> CategoryMatrixEntry:
    if asset_type == INSTRUMENT_CATEGORY_CRYPTO:
        return _not_applicable(
            CATEGORY_FUNDAMENTALS,
            "Single-issuer business fundamentals are not applicable for crypto assets.",
        )
    lane_cov = lanes.get(LANE_FUNDAMENTALS)
    state = _lane_to_matrix_state(lane_cov)
    return _make_entry(
        CATEGORY_FUNDAMENTALS,
        state=state,
        source_reference=LANE_FUNDAMENTALS,
        state_reasons={
            MATRIX_STRONG: "Company fundamentals data is grounded from a primary source.",
            MATRIX_PARTIAL: "Company fundamentals data is available but from a single or unofficial source.",
            MATRIX_WEAK: "Fundamentals data is present but suppressed or stale.",
            MATRIX_MISSING: "No fundamentals evidence artifact found.",
        },
    )


def _build_valuation(asset_type: str) -> CategoryMatrixEntry:
    if asset_type in (INSTRUMENT_CATEGORY_ETF, INSTRUMENT_CATEGORY_CRYPTO):
        return _not_applicable(
            CATEGORY_VALUATION,
            "Valuation metrics are not applicable for this asset type.",
        )
    return _make_entry(
        CATEGORY_VALUATION,
        state=MATRIX_MISSING,
        source_reference=None,
        state_reasons={
            MATRIX_MISSING: (
                "No valuation evidence lane exists in Stage 5J/5K. "
                "Valuation context (price-band) is feature-flagged separately."
            ),
        },
    )


def _build_technicals(
    lanes: dict[str, LaneCoverage],
    asset_type: str,
) -> CategoryMatrixEntry:
    lane_cov = lanes.get(LANE_TECHNICALS)
    state = _lane_to_matrix_state(lane_cov)
    return _make_entry(
        CATEGORY_TECHNICALS,
        state=state,
        source_reference=LANE_TECHNICALS,
        state_reasons={
            MATRIX_STRONG: "Technical/price data is grounded from a primary source.",
            MATRIX_PARTIAL: "Technical/price data is available but from a single or unofficial source.",
            MATRIX_WEAK: "Technical data is present but suppressed or stale.",
            MATRIX_MISSING: "No technical evidence artifact found.",
        },
    )


def _build_news_sentiment(
    lanes: dict[str, LaneCoverage],
    asset_type: str,
) -> CategoryMatrixEntry:
    # SEC catalyst sentiment takes priority; news_sentiment is fallback.
    sec_cov = lanes.get(LANE_SEC_CATALYST_SENTIMENT)
    news_cov = lanes.get(LANE_NEWS_SENTIMENT)

    # For ETF/crypto: sec_catalyst lane is not applicable.
    if asset_type in (INSTRUMENT_CATEGORY_ETF, INSTRUMENT_CATEGORY_CRYPTO):
        sec_cov = None

    # Best usable lane wins.
    sec_state = _lane_to_matrix_state(sec_cov)
    news_state = _lane_to_matrix_state(news_cov)

    state = _best_of(sec_state, news_state)
    source = (
        LANE_SEC_CATALYST_SENTIMENT if sec_state in _SYNTHESIS_ALLOWED_STATES
        else LANE_NEWS_SENTIMENT if news_state in _SYNTHESIS_ALLOWED_STATES
        else LANE_NEWS_SENTIMENT if news_state != MATRIX_MISSING
        else LANE_SEC_CATALYST_SENTIMENT if sec_cov is not None
        else LANE_NEWS_SENTIMENT
    )

    return _make_entry(
        CATEGORY_NEWS_SENTIMENT,
        state=state,
        source_reference=source,
        state_reasons={
            MATRIX_STRONG: "News/sentiment data is grounded from a primary or official source.",
            MATRIX_PARTIAL: "Some news or catalyst sentiment data is available.",
            MATRIX_WEAK: (
                "Sentiment data is present but suppressed (e.g. editorial context "
                "is not decision-useful) or stale."
            ),
            MATRIX_MISSING: "No news or sentiment evidence artifact found.",
        },
    )


def _build_sec_company_facts(
    lanes: dict[str, LaneCoverage],
    asset_type: str,
) -> CategoryMatrixEntry:
    if asset_type in (INSTRUMENT_CATEGORY_ETF, INSTRUMENT_CATEGORY_CRYPTO):
        return _not_applicable(
            CATEGORY_SEC_COMPANY_FACTS,
            "SEC company facts are not applicable for ETF/fund or crypto assets.",
        )
    if asset_type == INSTRUMENT_CATEGORY_UNKNOWN:
        # Conservative for unknown: treat as MISSING since we cannot confirm equity.
        return _make_entry(
            CATEGORY_SEC_COMPANY_FACTS,
            state=MATRIX_MISSING,
            source_reference=LANE_SEC_COMPANY_FACTS,
            state_reasons={
                MATRIX_MISSING: (
                    "Asset type is unknown; SEC company facts cannot be confirmed "
                    "as applicable. Treated conservatively as missing."
                ),
            },
        )
    lane_cov = lanes.get(LANE_SEC_COMPANY_FACTS)
    state = _lane_to_matrix_state(lane_cov)
    return _make_entry(
        CATEGORY_SEC_COMPANY_FACTS,
        state=state,
        source_reference=LANE_SEC_COMPANY_FACTS,
        state_reasons={
            MATRIX_STRONG: "SEC company facts (XBRL) are available from the official SEC EDGAR source.",
            MATRIX_PARTIAL: "SEC company facts are available but with limited coverage.",
            MATRIX_WEAK: "SEC company facts are present but suppressed or stale.",
            MATRIX_MISSING: "No SEC company facts artifact found.",
        },
    )


def _build_sec_catalysts(
    lanes: dict[str, LaneCoverage],
    asset_type: str,
) -> CategoryMatrixEntry:
    if asset_type in (INSTRUMENT_CATEGORY_ETF, INSTRUMENT_CATEGORY_CRYPTO):
        return _not_applicable(
            CATEGORY_SEC_CATALYSTS,
            "SEC filing catalysts are not applicable for ETF/fund or crypto assets.",
        )
    if asset_type == INSTRUMENT_CATEGORY_UNKNOWN:
        return _make_entry(
            CATEGORY_SEC_CATALYSTS,
            state=MATRIX_MISSING,
            source_reference=LANE_SEC_CATALYST_SENTIMENT,
            state_reasons={
                MATRIX_MISSING: (
                    "Asset type is unknown; SEC catalysts cannot be confirmed "
                    "as applicable. Treated conservatively as missing."
                ),
            },
        )
    lane_cov = lanes.get(LANE_SEC_CATALYST_SENTIMENT)
    state = _lane_to_matrix_state(lane_cov)
    return _make_entry(
        CATEGORY_SEC_CATALYSTS,
        state=state,
        source_reference=LANE_SEC_CATALYST_SENTIMENT,
        state_reasons={
            MATRIX_STRONG: "SEC filing catalyst data is available from the official SEC EDGAR source.",
            MATRIX_PARTIAL: "SEC filing catalyst data is available with limited coverage.",
            MATRIX_WEAK: "SEC catalyst data is present but suppressed or stale.",
            MATRIX_MISSING: "No SEC catalyst evidence artifact found.",
        },
    )


def _build_etf_fund_composition(asset_type: str) -> CategoryMatrixEntry:
    if asset_type == INSTRUMENT_CATEGORY_ETF:
        return _make_entry(
            CATEGORY_ETF_FUND_COMPOSITION,
            state=MATRIX_MISSING,
            source_reference=None,
            state_reasons={
                MATRIX_MISSING: (
                    "No ETF/fund holdings data source exists in Stage 5J/5K. "
                    "Holdings, sector exposure, and expense ratio require a "
                    "dedicated fund-data provider lane (Stage 9C gated)."
                ),
            },
        )
    return _not_applicable(
        CATEGORY_ETF_FUND_COMPOSITION,
        "ETF/fund composition is not applicable for non-ETF assets.",
    )


def _build_crypto_market_context(
    lanes: dict[str, LaneCoverage],
    asset_type: str,
) -> CategoryMatrixEntry:
    if asset_type != INSTRUMENT_CATEGORY_CRYPTO:
        return _not_applicable(
            CATEGORY_CRYPTO_MARKET_CONTEXT,
            "Crypto market context is not applicable for non-crypto assets.",
        )
    # Crypto: use technicals lane as a proxy for price/volatility context.
    # This is PARTIAL at best (price-derived, not a dedicated crypto market source).
    lane_cov = lanes.get(LANE_TECHNICALS)
    raw_state = _lane_to_matrix_state(lane_cov)
    # Cap at PARTIAL: technicals is a price-data proxy, not a full crypto market source.
    if raw_state == MATRIX_STRONG:
        state = MATRIX_PARTIAL
        reason = (
            "Price/volatility data available as a proxy for crypto market context. "
            "A dedicated crypto market-data source would provide stronger coverage."
        )
    elif raw_state == MATRIX_PARTIAL:
        state = MATRIX_PARTIAL
        reason = (
            "Some price/volatility data available as a proxy for crypto market context. "
            "Coverage is limited; a dedicated crypto market-data source is needed."
        )
    elif raw_state == MATRIX_WEAK:
        state = MATRIX_WEAK
        reason = "Price/volatility data is present but suppressed or stale. Crypto market context is weak."
    else:
        state = MATRIX_MISSING
        reason = (
            "No price or technical artifact found. Crypto market context is missing. "
            "No dedicated crypto market-data source exists in Stage 5J/5K."
        )

    allowed = state in _SYNTHESIS_ALLOWED_STATES
    supp_reason = None if allowed else _suppression_reason(state)
    return CategoryMatrixEntry(
        category=CATEGORY_CRYPTO_MARKET_CONTEXT,
        state=state,
        reason=reason,
        source_reference=LANE_TECHNICALS if lane_cov is not None else None,
        allowed_for_synthesis=allowed,
        suppression_reason=supp_reason,
    )


def _build_portfolio_sizing(asset_type: str) -> CategoryMatrixEntry:
    if asset_type == INSTRUMENT_CATEGORY_UNKNOWN:
        return _make_entry(
            CATEGORY_PORTFOLIO_SIZING_TARGET_WEIGHT,
            state=MATRIX_MISSING,
            source_reference=None,
            state_reasons={
                MATRIX_MISSING: (
                    "Asset type is unknown; portfolio sizing context cannot be "
                    "confirmed. Treated conservatively as missing."
                ),
            },
        )
    # For all known asset types: current position weight is always available
    # for portfolio holdings; target weight may not be set.
    return _make_entry(
        CATEGORY_PORTFOLIO_SIZING_TARGET_WEIGHT,
        state=MATRIX_PARTIAL,
        source_reference=None,
        state_reasons={
            MATRIX_PARTIAL: (
                "Current position weight is available from portfolio data. "
                "Target allocation weight is not evaluated by Stage 5J/5K "
                "and may not be set."
            ),
        },
    )


def _build_thesis_history(asset_type: str) -> CategoryMatrixEntry:
    # Thesis/decision history is not covered by Stage 5J/5K evidence lanes.
    # Honest MISSING for all asset types at Stage 9A.
    return _make_entry(
        CATEGORY_THESIS_HISTORY,
        state=MATRIX_MISSING,
        source_reference=None,
        state_reasons={
            MATRIX_MISSING: (
                "Thesis/decision history is not mapped to Stage 5J/5K evidence "
                "lanes at Stage 9A. Decision history integration is deferred."
            ),
        },
    )


# ── Lane → matrix state helpers ────────────────────────────────────────────────


def _lane_to_matrix_state(lane_cov: Optional[LaneCoverage]) -> str:
    """Map a Stage 5J LaneCoverage entry to a matrix state.

    READY + primary/official authority → STRONG
    READY + other source              → PARTIAL
    LIMITED                           → PARTIAL
    SUPPRESSED / STALE_OR_UNKNOWN / NOT_EVALUABLE → WEAK
    MISSING (or no lane)              → MISSING
    """
    if lane_cov is None or lane_cov.status == STATUS_MISSING:
        return MATRIX_MISSING
    status = lane_cov.status
    if status == STATUS_READY:
        authority = lane_cov.source_authority or ""
        if authority in _PRIMARY_AUTHORITY_LEVELS:
            return MATRIX_STRONG
        return MATRIX_PARTIAL
    if status == STATUS_LIMITED:
        return MATRIX_PARTIAL
    # SUPPRESSED | STALE_OR_UNKNOWN | NOT_EVALUABLE
    return MATRIX_WEAK


def _best_of(state_a: str, state_b: str) -> str:
    """Return the more favourable of two matrix states."""
    order = {
        MATRIX_STRONG: 0,
        MATRIX_PARTIAL: 1,
        MATRIX_WEAK: 2,
        MATRIX_MISSING: 3,
        MATRIX_NOT_APPLICABLE: 4,
    }
    return state_a if order.get(state_a, 99) <= order.get(state_b, 99) else state_b


def _suppression_reason(state: str) -> Optional[str]:
    if state == MATRIX_WEAK:
        return "Coverage is weak (suppressed, stale, or not evaluable). Synthesis not allowed."
    if state == MATRIX_MISSING:
        return "No evidence artifact found. Synthesis not allowed for missing coverage."
    return None


def _not_applicable(category: str, reason: str) -> CategoryMatrixEntry:
    return CategoryMatrixEntry(
        category=category,
        state=MATRIX_NOT_APPLICABLE,
        reason=reason,
        source_reference=None,
        allowed_for_synthesis=False,
        suppression_reason=None,
    )


def _make_entry(
    category: str,
    *,
    state: str,
    source_reference: Optional[str],
    state_reasons: dict[str, str],
) -> CategoryMatrixEntry:
    reason = state_reasons.get(state, f"Coverage state: {state}.")
    allowed = state in _SYNTHESIS_ALLOWED_STATES
    supp_reason = None if allowed else _suppression_reason(state)
    return CategoryMatrixEntry(
        category=category,
        state=state,
        reason=reason,
        source_reference=source_reference,
        allowed_for_synthesis=allowed,
        suppression_reason=supp_reason,
    )
