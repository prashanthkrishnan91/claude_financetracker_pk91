"""Stage 8C PR 1 — Sentiment Event v2 Provider-Agnostic Adapter.

Normalizes arbitrary external sentiment inputs (SEC catalyst, company announcement,
vendor-scored sentiment) into a structured, classified output that can pass or fail
the Stage 8B.1 quality gate and be embedded in an existing research artifact payload.

This module is a pure classification and normalization layer.  It does NOT:
  - call any external API or provider
  - write to any database
  - import or call decide()
  - emit Buy/Hold/Trim/Sell actions
  - treat missing polarity as neutral
  - auto-promote editorial/free news to a stronger authority band
  - change Stage 8B.1 quality criteria
  - add new DB columns

Decision-usefulness tiers emitted:
  NOT_USABLE  — evidence fails one or more quality criteria.
  LIMITED     — all criteria pass; completeness is PARTIAL.
  READY       — all criteria pass; completeness is COMPLETE.
  INELIGIBLE  — asset type (crypto/ETF) makes sentiment scoring too conservative.

Hard invariants (non-negotiable):
  - EDITORIAL_CONTEXT authority always yields NOT_USABLE.
  - THIN or NOT_EVALUABLE completeness always yields NOT_USABLE.
  - ticker_match_confidence < HIGH caps usefulness to NOT_USABLE (LOW/UNKNOWN)
    or LIMITED at most (MEDIUM); cannot produce READY.
  - missing polarity (None) is NOT treated as neutral — polarity absence is
    recorded explicitly and does not contribute to usefulness.
  - sentiment_polarity is NEVER mapped to a Buy/Hold/Trim/Sell action.
  - raw/free editorial sources (source_kind="news", provider_name="yfinance")
    cannot be promoted beyond EDITORIAL_CONTEXT regardless of claimed authority.
  - crypto/ETF assets are INELIGIBLE — conservative guardrail preserved.
  - safe_for_decision is never touched — remains False as DB enforces.

FRED/Stage 5 artifact precedent:
  Outputs use existing SourceRecord/FactRecord patterns and the structured_payload
  payload format already used by news_sentiment_evidence_v1 artifacts.
  No new DB columns.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

from app.services.intelligence.v3.sentiment_quality_threshold_v1 import (
    SentimentQualityResult,
    evaluate_sentiment_quality,
)

ADAPTER_V2_VERSION = "sentiment_event_adapter.v2"

# ── Decision-usefulness tier constants ────────────────────────────────────────

DECISION_USEFULNESS_NOT_USABLE = "NOT_USABLE"
DECISION_USEFULNESS_LIMITED = "LIMITED"
DECISION_USEFULNESS_READY = "READY"
DECISION_USEFULNESS_INELIGIBLE = "INELIGIBLE"

# ── Catalyst category constants ───────────────────────────────────────────────

CATALYST_CATEGORY_EARNINGS = "earnings"
CATALYST_CATEGORY_GUIDANCE = "guidance"
CATALYST_CATEGORY_REGULATORY = "regulatory"
CATALYST_CATEGORY_MACRO = "macro"
CATALYST_CATEGORY_CORPORATE_ACTION = "corporate_action"
CATALYST_CATEGORY_ANALYST_ACTION = "analyst_action"
CATALYST_CATEGORY_PRODUCT = "product"
CATALYST_CATEGORY_UNKNOWN = "unknown"

ALL_CATALYST_CATEGORIES: frozenset[str] = frozenset({
    CATALYST_CATEGORY_EARNINGS,
    CATALYST_CATEGORY_GUIDANCE,
    CATALYST_CATEGORY_REGULATORY,
    CATALYST_CATEGORY_MACRO,
    CATALYST_CATEGORY_CORPORATE_ACTION,
    CATALYST_CATEGORY_ANALYST_ACTION,
    CATALYST_CATEGORY_PRODUCT,
    CATALYST_CATEGORY_UNKNOWN,
})

_CATALYST_CATEGORY_ALIASES: dict[str, str] = {
    "earnings": CATALYST_CATEGORY_EARNINGS,
    "earnings_report": CATALYST_CATEGORY_EARNINGS,
    "earnings_release": CATALYST_CATEGORY_EARNINGS,
    "eps": CATALYST_CATEGORY_EARNINGS,
    "quarterly_results": CATALYST_CATEGORY_EARNINGS,
    "guidance": CATALYST_CATEGORY_GUIDANCE,
    "forward_guidance": CATALYST_CATEGORY_GUIDANCE,
    "outlook": CATALYST_CATEGORY_GUIDANCE,
    "forecast": CATALYST_CATEGORY_GUIDANCE,
    "regulatory": CATALYST_CATEGORY_REGULATORY,
    "sec_filing": CATALYST_CATEGORY_REGULATORY,
    "fda": CATALYST_CATEGORY_REGULATORY,
    "regulatory_approval": CATALYST_CATEGORY_REGULATORY,
    "compliance": CATALYST_CATEGORY_REGULATORY,
    "macro": CATALYST_CATEGORY_MACRO,
    "macro_event": CATALYST_CATEGORY_MACRO,
    "economic_data": CATALYST_CATEGORY_MACRO,
    "rate_decision": CATALYST_CATEGORY_MACRO,
    "corporate_action": CATALYST_CATEGORY_CORPORATE_ACTION,
    "merger": CATALYST_CATEGORY_CORPORATE_ACTION,
    "acquisition": CATALYST_CATEGORY_CORPORATE_ACTION,
    "spin_off": CATALYST_CATEGORY_CORPORATE_ACTION,
    "buyback": CATALYST_CATEGORY_CORPORATE_ACTION,
    "dividend": CATALYST_CATEGORY_CORPORATE_ACTION,
    "analyst_action": CATALYST_CATEGORY_ANALYST_ACTION,
    "upgrade": CATALYST_CATEGORY_ANALYST_ACTION,
    "downgrade": CATALYST_CATEGORY_ANALYST_ACTION,
    "coverage_initiated": CATALYST_CATEGORY_ANALYST_ACTION,
    "price_target": CATALYST_CATEGORY_ANALYST_ACTION,
    "product": CATALYST_CATEGORY_PRODUCT,
    "product_launch": CATALYST_CATEGORY_PRODUCT,
    "product_update": CATALYST_CATEGORY_PRODUCT,
    "announcement": CATALYST_CATEGORY_PRODUCT,
    "unknown": CATALYST_CATEGORY_UNKNOWN,
}

# ── Materiality constants ─────────────────────────────────────────────────────

MATERIALITY_HIGH = "HIGH"
MATERIALITY_MEDIUM = "MEDIUM"
MATERIALITY_LOW = "LOW"
MATERIALITY_UNKNOWN = "UNKNOWN"

ALL_MATERIALITY_VALUES: frozenset[str] = frozenset({
    MATERIALITY_HIGH,
    MATERIALITY_MEDIUM,
    MATERIALITY_LOW,
    MATERIALITY_UNKNOWN,
})

_MATERIALITY_ALIASES: dict[str, str] = {
    "high": MATERIALITY_HIGH,
    "material": MATERIALITY_HIGH,
    "significant": MATERIALITY_HIGH,
    "large": MATERIALITY_HIGH,
    "major": MATERIALITY_HIGH,
    "medium": MATERIALITY_MEDIUM,
    "moderate": MATERIALITY_MEDIUM,
    "mid": MATERIALITY_MEDIUM,
    "low": MATERIALITY_LOW,
    "minor": MATERIALITY_LOW,
    "immaterial": MATERIALITY_LOW,
    "small": MATERIALITY_LOW,
    "unknown": MATERIALITY_UNKNOWN,
    "": MATERIALITY_UNKNOWN,
    "none": MATERIALITY_UNKNOWN,
}

# ── Ticker match confidence constants ─────────────────────────────────────────

TICKER_MATCH_HIGH = "HIGH"
TICKER_MATCH_MEDIUM = "MEDIUM"
TICKER_MATCH_LOW = "LOW"
TICKER_MATCH_UNKNOWN = "UNKNOWN"

ALL_TICKER_MATCH_VALUES: frozenset[str] = frozenset({
    TICKER_MATCH_HIGH,
    TICKER_MATCH_MEDIUM,
    TICKER_MATCH_LOW,
    TICKER_MATCH_UNKNOWN,
})

_TICKER_MATCH_ALIASES: dict[str, str] = {
    "high": TICKER_MATCH_HIGH,
    "confirmed": TICKER_MATCH_HIGH,
    "exact": TICKER_MATCH_HIGH,
    "direct": TICKER_MATCH_HIGH,
    "medium": TICKER_MATCH_MEDIUM,
    "probable": TICKER_MATCH_MEDIUM,
    "likely": TICKER_MATCH_MEDIUM,
    "inferred": TICKER_MATCH_MEDIUM,
    "low": TICKER_MATCH_LOW,
    "possible": TICKER_MATCH_LOW,
    "uncertain": TICKER_MATCH_LOW,
    "indirect": TICKER_MATCH_LOW,
    "unknown": TICKER_MATCH_UNKNOWN,
    "": TICKER_MATCH_UNKNOWN,
    "none": TICKER_MATCH_UNKNOWN,
}

# ── Editorial promotion guard ─────────────────────────────────────────────────
# Source kinds and provider names that cannot be promoted beyond EDITORIAL_CONTEXT,
# regardless of the claimed source_authority in the input.

_EDITORIAL_SOURCE_KINDS: frozenset[str] = frozenset({"news"})
_FREE_EDITORIAL_PROVIDERS: frozenset[str] = frozenset({"yfinance"})

# ── Legally-safe URL source kinds ────────────────────────────────────────────
# Only these source_kinds may have their source_url passed through to the output.
# Editorial/news URLs are excluded (copyright / redistribution risk).

_URL_SAFE_SOURCE_KINDS: frozenset[str] = frozenset({
    "sec_filing",
    "company_disclosure",
    "vendor_fundamentals",
    "vendor_estimates",
    "vendor_calendar",
    "press_release",
})

# ── Asset ineligibility guard ─────────────────────────────────────────────────
# Crypto and ETF tickers are too conservative for sentiment scoring.

_INELIGIBLE_CATEGORIES: frozenset[str] = frozenset({"Crypto", "ETF"})
_KNOWN_CRYPTO_TICKERS: frozenset[str] = frozenset({"BTC", "XRP", "ETH", "DOGE", "SOL", "LTC"})
_KNOWN_ETF_TICKERS: frozenset[str] = frozenset({
    "SPY", "QQQ", "IWM", "GLD", "TLT", "VTI", "VOO", "AGG",
    "EFA", "EEM", "HYG", "LQD", "XLF", "XLE", "XLK",
})

# ── Input contract ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SentimentEventV2Input:
    """Provider-agnostic normalized input for the v2 sentiment event adapter.

    All fields are set by the caller.  The adapter normalizes and classifies;
    it does not derive authority from content or auto-promote weak sources.
    """
    ticker: str
    event_id: str                       # provider-assigned unique event identifier

    # Source provenance
    source_authority: str               # claimed authority (may be capped by guard)
    source_kind: str                    # matches DB source_kind enum
    provider_name: str                  # e.g. "sec_edgar", "refinitiv", "factset"

    # Evidence quality signals
    freshness_status: str               # FRESH | STALE | UNKNOWN
    source_count: int
    fact_count: int
    is_contradicted: bool
    completeness_band: str              # COMPLETE | PARTIAL | THIN | NOT_EVALUABLE

    # Classification fields
    sentiment_polarity: Optional[str]   # POSITIVE | NEGATIVE | NEUTRAL | None
    catalyst_category_raw: Optional[str]
    materiality_raw: Optional[str]
    ticker_match_confidence_raw: Optional[str]

    # Optional
    event_published_at: Optional[str] = None   # ISO 8601
    source_url: Optional[str] = None            # included only if legally safe
    holding_context: Optional[dict[str, Any]] = None  # for asset-type eligibility


# ── Output contract ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SentimentEventV2Output:
    """Normalized, classified output from the v2 sentiment event adapter.

    Safe for diagnostics: no raw payloads, no raw source URLs beyond legally-safe
    source_url, no API keys, no PII.  Never contains Buy/Hold/Trim/Sell.
    """
    version: str
    ticker: str
    event_id: str
    decision_usefulness_tier: str       # NOT_USABLE | LIMITED | READY | INELIGIBLE
    effective_source_authority: str     # may differ from claimed if guard fired
    catalyst_category: str              # normalized canonical value
    materiality: str                    # HIGH | MEDIUM | LOW | UNKNOWN
    ticker_match_confidence: str        # HIGH | MEDIUM | LOW | UNKNOWN
    sentiment_polarity: Optional[str]   # preserved from input; None = absent
    is_polarity_present: bool           # False when polarity is None
    dedupe_key: str                     # deterministic dedupe key
    failure_reasons: tuple[str, ...]    # empty when tier is LIMITED or READY
    safe_source_url: Optional[str]      # None unless source_kind is in safe set
    structured_payload: dict[str, Any]  # embeddable in FactRecord.structured_payload


# ── Classification helpers ────────────────────────────────────────────────────


def normalize_catalyst_category(raw: Optional[str]) -> str:
    """Normalize raw catalyst category string to a canonical value.

    Returns CATALYST_CATEGORY_UNKNOWN for None, empty, or unrecognised inputs.
    """
    if not raw:
        return CATALYST_CATEGORY_UNKNOWN
    key = raw.strip().lower()
    return _CATALYST_CATEGORY_ALIASES.get(key, CATALYST_CATEGORY_UNKNOWN)


def normalize_materiality(raw: Optional[str]) -> str:
    """Normalize raw materiality string to a canonical value.

    Returns MATERIALITY_UNKNOWN for None, empty, or unrecognised inputs.
    """
    if not raw:
        return MATERIALITY_UNKNOWN
    key = raw.strip().lower()
    return _MATERIALITY_ALIASES.get(key, MATERIALITY_UNKNOWN)


def normalize_ticker_match_confidence(raw: Optional[str]) -> str:
    """Normalize raw ticker match confidence to a canonical value.

    Returns TICKER_MATCH_UNKNOWN for None, empty, or unrecognised inputs.
    """
    if not raw:
        return TICKER_MATCH_UNKNOWN
    key = raw.strip().lower()
    return _TICKER_MATCH_ALIASES.get(key, TICKER_MATCH_UNKNOWN)


def _is_editorial_source(source_kind: str, provider_name: str) -> bool:
    """Return True if source should be capped at EDITORIAL_CONTEXT authority."""
    return (
        source_kind.lower() in _EDITORIAL_SOURCE_KINDS
        or provider_name.lower() in _FREE_EDITORIAL_PROVIDERS
    )


def _resolve_effective_authority(
    claimed_authority: str,
    source_kind: str,
    provider_name: str,
) -> str:
    """Return effective authority, capping editorial sources at EDITORIAL_CONTEXT.

    Raw/free editorial inputs (source_kind="news" or provider_name="yfinance")
    cannot be promoted to VENDOR_DERIVED or higher, regardless of claimed value.
    """
    if _is_editorial_source(source_kind, provider_name):
        return "EDITORIAL_CONTEXT"
    return claimed_authority


def _is_asset_ineligible(
    ticker: str,
    holding_context: Optional[dict[str, Any]],
) -> bool:
    """Return True if the asset is too conservative for sentiment scoring."""
    if holding_context:
        cat = (holding_context.get("category") or "").strip()
        asset_type = (holding_context.get("asset_type") or "").strip()
        if cat in _INELIGIBLE_CATEGORIES or asset_type in _INELIGIBLE_CATEGORIES:
            return True
    ticker_upper = ticker.upper()
    if ticker_upper in _KNOWN_CRYPTO_TICKERS or ticker_upper in _KNOWN_ETF_TICKERS:
        return True
    return False


def _resolve_safe_url(source_url: Optional[str], source_kind: str) -> Optional[str]:
    """Return source_url only if source_kind is in the legally-safe set; else None."""
    if not source_url:
        return None
    if source_kind.lower() in _URL_SAFE_SOURCE_KINDS:
        return source_url
    return None


def generate_dedupe_key(
    ticker: str,
    event_id: str,
    source_authority: str,
    provider_name: str,
    freshness_status: str,
) -> str:
    """Deterministic dedupe key — identical inputs produce an identical key.

    Used to collapse duplicate synthetic or provider inputs before writing.
    """
    raw = json.dumps(
        [
            ticker.upper(),
            event_id,
            source_authority,
            provider_name.lower(),
            freshness_status,
        ],
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _apply_ticker_match_cap(
    quality_tier: str,
    ticker_match_confidence: str,
) -> tuple[str, list[str]]:
    """Apply ticker_match_confidence cap on top of the quality gate result.

    Rules:
      HIGH    → no additional cap.
      MEDIUM  → tier capped at LIMITED (READY → LIMITED).
      LOW     → tier forced to NOT_USABLE.
      UNKNOWN → tier forced to NOT_USABLE (conservative).

    Returns (capped_tier, extra_failure_reasons).
    """
    extra_reasons: list[str] = []
    if quality_tier == DECISION_USEFULNESS_NOT_USABLE:
        return quality_tier, extra_reasons

    if ticker_match_confidence == TICKER_MATCH_HIGH:
        return quality_tier, extra_reasons

    if ticker_match_confidence == TICKER_MATCH_MEDIUM:
        if quality_tier == DECISION_USEFULNESS_READY:
            extra_reasons.append(
                "ticker_match_medium:capped_from_ready_to_limited"
            )
            return DECISION_USEFULNESS_LIMITED, extra_reasons
        return quality_tier, extra_reasons

    if ticker_match_confidence == TICKER_MATCH_LOW:
        extra_reasons.append("ticker_match_low:suppressed_to_not_usable")
        return DECISION_USEFULNESS_NOT_USABLE, extra_reasons

    # TICKER_MATCH_UNKNOWN — conservative
    extra_reasons.append("ticker_match_unknown:suppressed_to_not_usable")
    return DECISION_USEFULNESS_NOT_USABLE, extra_reasons


# ── Main adapter function ─────────────────────────────────────────────────────


def normalize_and_evaluate(inp: SentimentEventV2Input) -> SentimentEventV2Output:
    """Normalize and classify a provider-agnostic sentiment event input.

    Steps:
      1. Ineligibility check (crypto/ETF → INELIGIBLE immediately).
      2. Editorial promotion guard (cap authority for editorial sources).
      3. Normalize catalyst_category, materiality, ticker_match_confidence.
      4. Compute dedupe key.
      5. Run Stage 8B.1 quality gate (evaluate_sentiment_quality).
      6. Apply ticker_match_confidence cap.
      7. Build structured_payload for artifact embedding.
      8. Return SentimentEventV2Output.

    Hard invariants:
      - EDITORIAL_CONTEXT always → NOT_USABLE.
      - THIN/NOT_EVALUABLE always → NOT_USABLE.
      - Missing polarity is NOT neutral — recorded as is_polarity_present=False.
      - No Buy/Hold/Trim/Sell emitted.
      - safe_for_decision never touched.
    """
    # ── Step 1: Asset ineligibility ───────────────────────────────────────────
    if _is_asset_ineligible(inp.ticker, inp.holding_context):
        dedupe_key = generate_dedupe_key(
            inp.ticker,
            inp.event_id,
            inp.source_authority,
            inp.provider_name,
            inp.freshness_status,
        )
        catalyst_category = normalize_catalyst_category(inp.catalyst_category_raw)
        materiality = normalize_materiality(inp.materiality_raw)
        ticker_match_confidence = normalize_ticker_match_confidence(inp.ticker_match_confidence_raw)
        safe_url = _resolve_safe_url(inp.source_url, inp.source_kind)
        payload = _build_structured_payload(
            inp=inp,
            effective_authority="EDITORIAL_CONTEXT",
            catalyst_category=catalyst_category,
            materiality=materiality,
            ticker_match_confidence=ticker_match_confidence,
            decision_usefulness_tier=DECISION_USEFULNESS_INELIGIBLE,
            failure_reasons=("asset_type_ineligible_for_sentiment",),
            safe_url=safe_url,
        )
        return SentimentEventV2Output(
            version=ADAPTER_V2_VERSION,
            ticker=inp.ticker,
            event_id=inp.event_id,
            decision_usefulness_tier=DECISION_USEFULNESS_INELIGIBLE,
            effective_source_authority=inp.source_authority,
            catalyst_category=catalyst_category,
            materiality=materiality,
            ticker_match_confidence=ticker_match_confidence,
            sentiment_polarity=inp.sentiment_polarity,
            is_polarity_present=(inp.sentiment_polarity is not None),
            dedupe_key=dedupe_key,
            failure_reasons=("asset_type_ineligible_for_sentiment",),
            safe_source_url=safe_url,
            structured_payload=payload,
        )

    # ── Step 2: Editorial promotion guard ─────────────────────────────────────
    effective_authority = _resolve_effective_authority(
        inp.source_authority,
        inp.source_kind,
        inp.provider_name,
    )

    # ── Step 3: Normalize classification fields ───────────────────────────────
    catalyst_category = normalize_catalyst_category(inp.catalyst_category_raw)
    materiality = normalize_materiality(inp.materiality_raw)
    ticker_match_confidence = normalize_ticker_match_confidence(inp.ticker_match_confidence_raw)

    # ── Step 4: Dedupe key ────────────────────────────────────────────────────
    dedupe_key = generate_dedupe_key(
        inp.ticker,
        inp.event_id,
        effective_authority,
        inp.provider_name,
        inp.freshness_status,
    )

    # ── Step 5: Stage 8B.1 quality gate ──────────────────────────────────────
    quality_result: SentimentQualityResult = evaluate_sentiment_quality(
        freshness_status=inp.freshness_status,
        source_authority=effective_authority,
        completeness_band=inp.completeness_band,
        is_contradicted=inp.is_contradicted,
        source_count=inp.source_count,
        fact_count=inp.fact_count,
    )

    failure_reasons: list[str] = list(quality_result.failure_reasons)

    # Derive initial tier from quality gate.
    if not quality_result.is_decision_useful:
        initial_tier = DECISION_USEFULNESS_NOT_USABLE
    elif quality_result.quality_tier == "READY":
        initial_tier = DECISION_USEFULNESS_READY
    else:
        initial_tier = DECISION_USEFULNESS_LIMITED

    # ── Step 6: Ticker match confidence cap ───────────────────────────────────
    final_tier, cap_reasons = _apply_ticker_match_cap(initial_tier, ticker_match_confidence)
    failure_reasons.extend(cap_reasons)

    # ── Step 7: Safe URL ──────────────────────────────────────────────────────
    safe_url = _resolve_safe_url(inp.source_url, inp.source_kind)

    # ── Step 8: Structured payload ────────────────────────────────────────────
    payload = _build_structured_payload(
        inp=inp,
        effective_authority=effective_authority,
        catalyst_category=catalyst_category,
        materiality=materiality,
        ticker_match_confidence=ticker_match_confidence,
        decision_usefulness_tier=final_tier,
        failure_reasons=tuple(failure_reasons),
        safe_url=safe_url,
    )

    return SentimentEventV2Output(
        version=ADAPTER_V2_VERSION,
        ticker=inp.ticker,
        event_id=inp.event_id,
        decision_usefulness_tier=final_tier,
        effective_source_authority=effective_authority,
        catalyst_category=catalyst_category,
        materiality=materiality,
        ticker_match_confidence=ticker_match_confidence,
        sentiment_polarity=inp.sentiment_polarity,
        is_polarity_present=(inp.sentiment_polarity is not None),
        dedupe_key=dedupe_key,
        failure_reasons=tuple(failure_reasons),
        safe_source_url=safe_url,
        structured_payload=payload,
    )


def _build_structured_payload(
    *,
    inp: SentimentEventV2Input,
    effective_authority: str,
    catalyst_category: str,
    materiality: str,
    ticker_match_confidence: str,
    decision_usefulness_tier: str,
    failure_reasons: tuple[str, ...],
    safe_url: Optional[str],
) -> dict[str, Any]:
    """Build the structured_payload dict for FactRecord or artifact_payload embedding.

    Follows the existing news_sentiment_evidence_v1 payload structure.
    Never contains forbidden keys (buy/sell/trim/hold/recommendation/action/etc.).
    Never contains raw API keys, PII, or raw payloads.
    """
    return {
        "adapter_version": ADAPTER_V2_VERSION,
        "ticker": inp.ticker,
        "event_id": inp.event_id,
        "source_authority": effective_authority,
        "freshness_status": inp.freshness_status,
        "source_count": inp.source_count,
        "fact_count": inp.fact_count,
        "is_contradicted": inp.is_contradicted,
        "completeness_band": inp.completeness_band,
        "catalyst_category": catalyst_category,
        "materiality": materiality,
        "ticker_match_confidence": ticker_match_confidence,
        # Polarity: explicit null distinction — None is NOT treated as neutral.
        "sentiment_polarity": inp.sentiment_polarity,
        "is_polarity_present": (inp.sentiment_polarity is not None),
        "decision_usefulness_tier": decision_usefulness_tier,
        "failure_reasons": list(failure_reasons),
        # Source URL only if legally safe.
        "source_url": safe_url,
        # Provider metadata (no keys/secrets).
        "provider_name": inp.provider_name,
        "source_kind": inp.source_kind,
        "event_published_at": inp.event_published_at,
    }
