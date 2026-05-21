"""Stage 8C PR 1 — Sentiment Event v2 Provider-Agnostic Adapter tests.

Verifies:
  1. editorial/yfinance-like input remains NOT_USABLE
  2. synthetic vendor-derived input with FRESH + PARTIAL + enough facts/sources → LIMITED
  3. synthetic vendor-derived input with COMPLETE + enough facts/sources → READY
  4. company-authored/primary catalyst input can become LIMITED/READY without polarity
  5. missing polarity is not treated as neutral
  6. low ticker_match_confidence prevents READY and caps or suppresses appropriately
  7. duplicate inputs collapse deterministically (dedupe key)
  8. contradicted evidence is NOT_USABLE
  9. raw/free editorial input is never promoted to VENDOR_DERIVED
 10. BTC/XRP/ETF-style ineligible assets remain INELIGIBLE
 11. Stage 8B.1 threshold behavior remains unchanged
 12. No Buy/Hold/Trim/Sell action is emitted by the adapter

No Supabase dependency. No IO. No LLM calls.
"""
from __future__ import annotations

from typing import Optional

import pytest

from app.services.intelligence.v3.sentiment_event_adapter_v2 import (
    ADAPTER_V2_VERSION,
    ALL_CATALYST_CATEGORIES,
    ALL_MATERIALITY_VALUES,
    ALL_TICKER_MATCH_VALUES,
    CATALYST_CATEGORY_EARNINGS,
    CATALYST_CATEGORY_REGULATORY,
    CATALYST_CATEGORY_UNKNOWN,
    DECISION_USEFULNESS_INELIGIBLE,
    DECISION_USEFULNESS_LIMITED,
    DECISION_USEFULNESS_NOT_USABLE,
    DECISION_USEFULNESS_READY,
    MATERIALITY_HIGH,
    MATERIALITY_UNKNOWN,
    TICKER_MATCH_HIGH,
    TICKER_MATCH_LOW,
    TICKER_MATCH_MEDIUM,
    TICKER_MATCH_UNKNOWN,
    SentimentEventV2Input,
    SentimentEventV2Output,
    generate_dedupe_key,
    normalize_catalyst_category,
    normalize_materiality,
    normalize_ticker_match_confidence,
    normalize_and_evaluate,
)
from app.services.intelligence.v3.sentiment_quality_threshold_v1 import (
    evaluate_sentiment_quality,
    SENTIMENT_QUALITY_THRESHOLD_VERSION,
)
from app.services.intelligence.research_workers.contracts import (
    WORKER_FORBIDDEN_PAYLOAD_KEYS,
)

# ── Shared test helpers ───────────────────────────────────────────────────────

_EDITORIAL_INPUT = SentimentEventV2Input(
    ticker="MSFT",
    event_id="yfin-001",
    source_authority="EDITORIAL_CONTEXT",
    source_kind="news",
    provider_name="yfinance",
    freshness_status="FRESH",
    source_count=10,
    fact_count=10,
    is_contradicted=False,
    completeness_band="THIN",
    sentiment_polarity="POSITIVE",
    catalyst_category_raw="earnings",
    materiality_raw="high",
    ticker_match_confidence_raw="high",
)

_VENDOR_PARTIAL_INPUT = SentimentEventV2Input(
    ticker="MSFT",
    event_id="vendor-002",
    source_authority="VENDOR_DERIVED",
    source_kind="vendor_fundamentals",
    provider_name="refinitiv",
    freshness_status="FRESH",
    source_count=3,
    fact_count=4,
    is_contradicted=False,
    completeness_band="PARTIAL",
    sentiment_polarity="POSITIVE",
    catalyst_category_raw="earnings",
    materiality_raw="high",
    ticker_match_confidence_raw="high",
)

_VENDOR_COMPLETE_INPUT = SentimentEventV2Input(
    ticker="MSFT",
    event_id="vendor-003",
    source_authority="VENDOR_DERIVED",
    source_kind="vendor_fundamentals",
    provider_name="refinitiv",
    freshness_status="FRESH",
    source_count=5,
    fact_count=8,
    is_contradicted=False,
    completeness_band="COMPLETE",
    sentiment_polarity="POSITIVE",
    catalyst_category_raw="earnings",
    materiality_raw="high",
    ticker_match_confidence_raw="high",
)

_COMPANY_AUTHORED_INPUT = SentimentEventV2Input(
    ticker="MSFT",
    event_id="corp-004",
    source_authority="COMPANY_AUTHORED",
    source_kind="press_release",
    provider_name="company_ir",
    freshness_status="FRESH",
    source_count=2,
    fact_count=3,
    is_contradicted=False,
    completeness_band="COMPLETE",
    sentiment_polarity=None,   # no polarity — company catalyst
    catalyst_category_raw="guidance",
    materiality_raw="high",
    ticker_match_confidence_raw="high",
)


# ── 1. Editorial / yfinance-like input remains NOT_USABLE ────────────────────


class TestEditorialInputNotUsable:
    """Editorial and yfinance-like inputs must always remain NOT_USABLE."""

    def test_editorial_context_is_not_usable(self):
        out = normalize_and_evaluate(_EDITORIAL_INPUT)
        assert out.decision_usefulness_tier == DECISION_USEFULNESS_NOT_USABLE

    def test_editorial_failure_reason_authority(self):
        out = normalize_and_evaluate(_EDITORIAL_INPUT)
        assert any("source_quality_too_weak" in r for r in out.failure_reasons)

    def test_editorial_failure_reason_completeness(self):
        out = normalize_and_evaluate(_EDITORIAL_INPUT)
        assert any("completeness_too_weak" in r for r in out.failure_reasons)

    def test_yfinance_with_vendor_claimed_authority_is_not_usable(self):
        """yfinance provider cannot claim VENDOR_DERIVED — guard caps to EDITORIAL_CONTEXT."""
        inp = SentimentEventV2Input(
            ticker="MSFT",
            event_id="yfin-spoofed",
            source_authority="VENDOR_DERIVED",   # claimed but will be capped
            source_kind="news",
            provider_name="yfinance",
            freshness_status="FRESH",
            source_count=5,
            fact_count=5,
            is_contradicted=False,
            completeness_band="PARTIAL",
            sentiment_polarity="POSITIVE",
            catalyst_category_raw="earnings",
            materiality_raw="high",
            ticker_match_confidence_raw="high",
        )
        out = normalize_and_evaluate(inp)
        assert out.decision_usefulness_tier == DECISION_USEFULNESS_NOT_USABLE
        assert out.effective_source_authority == "EDITORIAL_CONTEXT"

    def test_news_source_kind_capped_to_editorial(self):
        """source_kind='news' caps effective_source_authority to EDITORIAL_CONTEXT."""
        inp = SentimentEventV2Input(
            ticker="AAPL",
            event_id="news-001",
            source_authority="PRIMARY_AUTHORITY",  # claimed
            source_kind="news",
            provider_name="bloomberg_feed",
            freshness_status="FRESH",
            source_count=3,
            fact_count=3,
            is_contradicted=False,
            completeness_band="COMPLETE",
            sentiment_polarity="POSITIVE",
            catalyst_category_raw="earnings",
            materiality_raw="high",
            ticker_match_confidence_raw="high",
        )
        out = normalize_and_evaluate(inp)
        assert out.effective_source_authority == "EDITORIAL_CONTEXT"
        assert out.decision_usefulness_tier == DECISION_USEFULNESS_NOT_USABLE

    def test_many_editorial_items_still_not_usable(self):
        """Volume of editorial items cannot compensate for weak authority."""
        inp = SentimentEventV2Input(
            ticker="MSFT",
            event_id="yfin-100",
            source_authority="EDITORIAL_CONTEXT",
            source_kind="news",
            provider_name="yfinance",
            freshness_status="FRESH",
            source_count=100,
            fact_count=200,
            is_contradicted=False,
            completeness_band="THIN",
            sentiment_polarity="POSITIVE",
            catalyst_category_raw="earnings",
            materiality_raw="high",
            ticker_match_confidence_raw="high",
        )
        out = normalize_and_evaluate(inp)
        assert out.decision_usefulness_tier == DECISION_USEFULNESS_NOT_USABLE


# ── 2. Vendor-derived PARTIAL → LIMITED ──────────────────────────────────────


class TestVendorDerivedPartialBecomesLimited:
    def test_vendor_partial_fresh_is_limited(self):
        out = normalize_and_evaluate(_VENDOR_PARTIAL_INPUT)
        assert out.decision_usefulness_tier == DECISION_USEFULNESS_LIMITED

    def test_vendor_partial_is_decision_useful(self):
        out = normalize_and_evaluate(_VENDOR_PARTIAL_INPUT)
        assert out.failure_reasons == ()

    def test_vendor_partial_effective_authority_unchanged(self):
        out = normalize_and_evaluate(_VENDOR_PARTIAL_INPUT)
        assert out.effective_source_authority == "VENDOR_DERIVED"


# ── 3. Vendor-derived COMPLETE → READY ───────────────────────────────────────


class TestVendorDerivedCompleteBecomesReady:
    def test_vendor_complete_fresh_is_ready(self):
        out = normalize_and_evaluate(_VENDOR_COMPLETE_INPUT)
        assert out.decision_usefulness_tier == DECISION_USEFULNESS_READY

    def test_vendor_complete_no_failure_reasons(self):
        out = normalize_and_evaluate(_VENDOR_COMPLETE_INPUT)
        assert out.failure_reasons == ()

    def test_primary_authority_complete_is_ready(self):
        inp = SentimentEventV2Input(
            ticker="MSFT",
            event_id="prim-001",
            source_authority="PRIMARY_AUTHORITY",
            source_kind="sec_filing",
            provider_name="sec_edgar",
            freshness_status="FRESH",
            source_count=2,
            fact_count=5,
            is_contradicted=False,
            completeness_band="COMPLETE",
            sentiment_polarity=None,
            catalyst_category_raw="regulatory",
            materiality_raw="high",
            ticker_match_confidence_raw="high",
        )
        out = normalize_and_evaluate(inp)
        assert out.decision_usefulness_tier == DECISION_USEFULNESS_READY


# ── 4. Company-authored/primary catalyst → LIMITED/READY without polarity ────


class TestCompanyAuthoredWithoutPolarity:
    def test_company_authored_complete_no_polarity_is_ready(self):
        out = normalize_and_evaluate(_COMPANY_AUTHORED_INPUT)
        assert out.decision_usefulness_tier == DECISION_USEFULNESS_READY

    def test_polarity_absent_not_treated_as_neutral(self):
        out = normalize_and_evaluate(_COMPANY_AUTHORED_INPUT)
        assert out.sentiment_polarity is None
        assert out.is_polarity_present is False

    def test_company_authored_partial_no_polarity_is_limited(self):
        inp = SentimentEventV2Input(
            ticker="MSFT",
            event_id="corp-005",
            source_authority="COMPANY_AUTHORED",
            source_kind="press_release",
            provider_name="company_ir",
            freshness_status="FRESH",
            source_count=2,
            fact_count=3,
            is_contradicted=False,
            completeness_band="PARTIAL",
            sentiment_polarity=None,
            catalyst_category_raw="guidance",
            materiality_raw="medium",
            ticker_match_confidence_raw="high",
        )
        out = normalize_and_evaluate(inp)
        assert out.decision_usefulness_tier == DECISION_USEFULNESS_LIMITED
        assert out.is_polarity_present is False


# ── 5. Missing polarity not treated as neutral ────────────────────────────────


class TestMissingPolarityHandling:
    def test_polarity_none_is_not_neutral(self):
        """None polarity is not treated as NEUTRAL and must not block LIMITED/READY."""
        out = normalize_and_evaluate(_COMPANY_AUTHORED_INPUT)
        assert out.sentiment_polarity is None
        assert out.is_polarity_present is False
        # The tier is still READY — absence of polarity doesn't block quality gate.
        assert out.decision_usefulness_tier == DECISION_USEFULNESS_READY

    def test_polarity_present_is_preserved_faithfully(self):
        inp = SentimentEventV2Input(
            ticker="MSFT",
            event_id="pol-001",
            source_authority="VENDOR_DERIVED",
            source_kind="vendor_fundamentals",
            provider_name="refinitiv",
            freshness_status="FRESH",
            source_count=2,
            fact_count=2,
            is_contradicted=False,
            completeness_band="PARTIAL",
            sentiment_polarity="NEGATIVE",
            catalyst_category_raw="earnings",
            materiality_raw="high",
            ticker_match_confidence_raw="high",
        )
        out = normalize_and_evaluate(inp)
        assert out.sentiment_polarity == "NEGATIVE"
        assert out.is_polarity_present is True

    def test_payload_preserves_polarity_absent_flag(self):
        out = normalize_and_evaluate(_COMPANY_AUTHORED_INPUT)
        assert out.structured_payload["sentiment_polarity"] is None
        assert out.structured_payload["is_polarity_present"] is False


# ── 6. Ticker match confidence cap ───────────────────────────────────────────


class TestTickerMatchConfidenceCap:
    def test_medium_confidence_caps_ready_to_limited(self):
        inp = SentimentEventV2Input(
            ticker="MSFT",
            event_id="tmc-001",
            source_authority="VENDOR_DERIVED",
            source_kind="vendor_fundamentals",
            provider_name="refinitiv",
            freshness_status="FRESH",
            source_count=3,
            fact_count=5,
            is_contradicted=False,
            completeness_band="COMPLETE",   # would be READY with high confidence
            sentiment_polarity="POSITIVE",
            catalyst_category_raw="earnings",
            materiality_raw="high",
            ticker_match_confidence_raw="medium",
        )
        out = normalize_and_evaluate(inp)
        assert out.ticker_match_confidence == TICKER_MATCH_MEDIUM
        assert out.decision_usefulness_tier == DECISION_USEFULNESS_LIMITED
        assert any("ticker_match_medium" in r for r in out.failure_reasons)

    def test_low_confidence_forces_not_usable(self):
        inp = SentimentEventV2Input(
            ticker="MSFT",
            event_id="tmc-002",
            source_authority="VENDOR_DERIVED",
            source_kind="vendor_fundamentals",
            provider_name="refinitiv",
            freshness_status="FRESH",
            source_count=3,
            fact_count=5,
            is_contradicted=False,
            completeness_band="PARTIAL",
            sentiment_polarity="POSITIVE",
            catalyst_category_raw="earnings",
            materiality_raw="high",
            ticker_match_confidence_raw="low",
        )
        out = normalize_and_evaluate(inp)
        assert out.ticker_match_confidence == TICKER_MATCH_LOW
        assert out.decision_usefulness_tier == DECISION_USEFULNESS_NOT_USABLE
        assert any("ticker_match_low" in r for r in out.failure_reasons)

    def test_unknown_confidence_forces_not_usable(self):
        inp = SentimentEventV2Input(
            ticker="MSFT",
            event_id="tmc-003",
            source_authority="VENDOR_DERIVED",
            source_kind="vendor_fundamentals",
            provider_name="refinitiv",
            freshness_status="FRESH",
            source_count=3,
            fact_count=5,
            is_contradicted=False,
            completeness_band="COMPLETE",
            sentiment_polarity="POSITIVE",
            catalyst_category_raw="earnings",
            materiality_raw="high",
            ticker_match_confidence_raw=None,   # unknown
        )
        out = normalize_and_evaluate(inp)
        assert out.ticker_match_confidence == TICKER_MATCH_UNKNOWN
        assert out.decision_usefulness_tier == DECISION_USEFULNESS_NOT_USABLE
        assert any("ticker_match_unknown" in r for r in out.failure_reasons)

    def test_high_confidence_does_not_cap(self):
        out = normalize_and_evaluate(_VENDOR_COMPLETE_INPUT)
        assert out.ticker_match_confidence == TICKER_MATCH_HIGH
        assert out.decision_usefulness_tier == DECISION_USEFULNESS_READY

    def test_medium_limited_stays_limited(self):
        """MEDIUM confidence + PARTIAL completeness → still LIMITED (no downgrade)."""
        inp = SentimentEventV2Input(
            ticker="MSFT",
            event_id="tmc-004",
            source_authority="VENDOR_DERIVED",
            source_kind="vendor_fundamentals",
            provider_name="refinitiv",
            freshness_status="FRESH",
            source_count=3,
            fact_count=4,
            is_contradicted=False,
            completeness_band="PARTIAL",
            sentiment_polarity="POSITIVE",
            catalyst_category_raw="earnings",
            materiality_raw="high",
            ticker_match_confidence_raw="medium",
        )
        out = normalize_and_evaluate(inp)
        assert out.decision_usefulness_tier == DECISION_USEFULNESS_LIMITED


# ── 7. Duplicate inputs collapse deterministically ───────────────────────────


class TestDedupeKeyDeterminism:
    def test_same_inputs_produce_same_dedupe_key(self):
        key1 = generate_dedupe_key("MSFT", "evt-001", "VENDOR_DERIVED", "refinitiv", "FRESH")
        key2 = generate_dedupe_key("MSFT", "evt-001", "VENDOR_DERIVED", "refinitiv", "FRESH")
        assert key1 == key2

    def test_different_event_id_produces_different_key(self):
        key1 = generate_dedupe_key("MSFT", "evt-001", "VENDOR_DERIVED", "refinitiv", "FRESH")
        key2 = generate_dedupe_key("MSFT", "evt-002", "VENDOR_DERIVED", "refinitiv", "FRESH")
        assert key1 != key2

    def test_different_ticker_produces_different_key(self):
        key1 = generate_dedupe_key("MSFT", "evt-001", "VENDOR_DERIVED", "refinitiv", "FRESH")
        key2 = generate_dedupe_key("AAPL", "evt-001", "VENDOR_DERIVED", "refinitiv", "FRESH")
        assert key1 != key2

    def test_ticker_is_case_normalised(self):
        """Ticker case differences should not create different dedupe keys."""
        key1 = generate_dedupe_key("MSFT", "evt-001", "VENDOR_DERIVED", "refinitiv", "FRESH")
        key2 = generate_dedupe_key("msft", "evt-001", "VENDOR_DERIVED", "refinitiv", "FRESH")
        assert key1 == key2

    def test_provider_name_is_case_normalised(self):
        key1 = generate_dedupe_key("MSFT", "evt-001", "VENDOR_DERIVED", "REFINITIV", "FRESH")
        key2 = generate_dedupe_key("MSFT", "evt-001", "VENDOR_DERIVED", "refinitiv", "FRESH")
        assert key1 == key2

    def test_duplicate_inputs_produce_same_output_dedupe_key(self):
        out1 = normalize_and_evaluate(_VENDOR_COMPLETE_INPUT)
        out2 = normalize_and_evaluate(_VENDOR_COMPLETE_INPUT)
        assert out1.dedupe_key == out2.dedupe_key

    def test_stale_vs_fresh_produces_different_dedupe_key(self):
        key1 = generate_dedupe_key("MSFT", "evt-001", "VENDOR_DERIVED", "refinitiv", "FRESH")
        key2 = generate_dedupe_key("MSFT", "evt-001", "VENDOR_DERIVED", "refinitiv", "STALE")
        assert key1 != key2


# ── 8. Contradicted evidence is NOT_USABLE ───────────────────────────────────


class TestContradictedEvidence:
    def test_contradicted_vendor_derived_is_not_usable(self):
        inp = SentimentEventV2Input(
            ticker="MSFT",
            event_id="contra-001",
            source_authority="VENDOR_DERIVED",
            source_kind="vendor_fundamentals",
            provider_name="refinitiv",
            freshness_status="FRESH",
            source_count=3,
            fact_count=4,
            is_contradicted=True,
            completeness_band="PARTIAL",
            sentiment_polarity="POSITIVE",
            catalyst_category_raw="earnings",
            materiality_raw="high",
            ticker_match_confidence_raw="high",
        )
        out = normalize_and_evaluate(inp)
        assert out.decision_usefulness_tier == DECISION_USEFULNESS_NOT_USABLE
        assert "evidence_contradicted" in out.failure_reasons

    def test_contradicted_company_authored_is_not_usable(self):
        inp = SentimentEventV2Input(
            ticker="MSFT",
            event_id="contra-002",
            source_authority="COMPANY_AUTHORED",
            source_kind="press_release",
            provider_name="company_ir",
            freshness_status="FRESH",
            source_count=2,
            fact_count=2,
            is_contradicted=True,
            completeness_band="COMPLETE",
            sentiment_polarity=None,
            catalyst_category_raw="guidance",
            materiality_raw="high",
            ticker_match_confidence_raw="high",
        )
        out = normalize_and_evaluate(inp)
        assert out.decision_usefulness_tier == DECISION_USEFULNESS_NOT_USABLE
        assert "evidence_contradicted" in out.failure_reasons


# ── 9. Raw/free editorial input never promoted to VENDOR_DERIVED ──────────────


class TestEditorialNotPromoted:
    def test_editorial_source_kind_caps_claimed_vendor_authority(self):
        inp = SentimentEventV2Input(
            ticker="MSFT",
            event_id="promo-001",
            source_authority="VENDOR_DERIVED",   # claimed
            source_kind="news",                   # editorial
            provider_name="some_news_api",
            freshness_status="FRESH",
            source_count=5,
            fact_count=5,
            is_contradicted=False,
            completeness_band="PARTIAL",
            sentiment_polarity="POSITIVE",
            catalyst_category_raw="earnings",
            materiality_raw="high",
            ticker_match_confidence_raw="high",
        )
        out = normalize_and_evaluate(inp)
        assert out.effective_source_authority == "EDITORIAL_CONTEXT"
        assert out.decision_usefulness_tier == DECISION_USEFULNESS_NOT_USABLE

    def test_yfinance_provider_caps_to_editorial(self):
        inp = SentimentEventV2Input(
            ticker="MSFT",
            event_id="promo-002",
            source_authority="PRIMARY_AUTHORITY",  # claimed
            source_kind="vendor_fundamentals",
            provider_name="yfinance",               # free editorial provider
            freshness_status="FRESH",
            source_count=5,
            fact_count=5,
            is_contradicted=False,
            completeness_band="COMPLETE",
            sentiment_polarity="POSITIVE",
            catalyst_category_raw="earnings",
            materiality_raw="high",
            ticker_match_confidence_raw="high",
        )
        out = normalize_and_evaluate(inp)
        assert out.effective_source_authority == "EDITORIAL_CONTEXT"
        assert out.decision_usefulness_tier == DECISION_USEFULNESS_NOT_USABLE

    def test_vendor_non_editorial_source_not_capped(self):
        """A genuine vendor source should NOT be capped to EDITORIAL_CONTEXT."""
        out = normalize_and_evaluate(_VENDOR_COMPLETE_INPUT)
        assert out.effective_source_authority == "VENDOR_DERIVED"


# ── 10. BTC/XRP/ETF ineligible assets ────────────────────────────────────────


class TestIneligibleAssets:
    def _make_crypto_input(self, ticker: str) -> SentimentEventV2Input:
        return SentimentEventV2Input(
            ticker=ticker,
            event_id="crypto-001",
            source_authority="VENDOR_DERIVED",
            source_kind="vendor_fundamentals",
            provider_name="refinitiv",
            freshness_status="FRESH",
            source_count=3,
            fact_count=4,
            is_contradicted=False,
            completeness_band="COMPLETE",
            sentiment_polarity="POSITIVE",
            catalyst_category_raw="macro",
            materiality_raw="high",
            ticker_match_confidence_raw="high",
        )

    def test_btc_is_ineligible(self):
        out = normalize_and_evaluate(self._make_crypto_input("BTC"))
        assert out.decision_usefulness_tier == DECISION_USEFULNESS_INELIGIBLE

    def test_xrp_is_ineligible(self):
        out = normalize_and_evaluate(self._make_crypto_input("XRP"))
        assert out.decision_usefulness_tier == DECISION_USEFULNESS_INELIGIBLE

    def test_spy_etf_is_ineligible(self):
        inp = SentimentEventV2Input(
            ticker="SPY",
            event_id="etf-001",
            source_authority="VENDOR_DERIVED",
            source_kind="vendor_fundamentals",
            provider_name="refinitiv",
            freshness_status="FRESH",
            source_count=3,
            fact_count=4,
            is_contradicted=False,
            completeness_band="COMPLETE",
            sentiment_polarity=None,
            catalyst_category_raw="macro",
            materiality_raw="high",
            ticker_match_confidence_raw="high",
        )
        out = normalize_and_evaluate(inp)
        assert out.decision_usefulness_tier == DECISION_USEFULNESS_INELIGIBLE

    def test_holding_context_crypto_category_is_ineligible(self):
        inp = SentimentEventV2Input(
            ticker="DOGE",
            event_id="crypto-002",
            source_authority="VENDOR_DERIVED",
            source_kind="vendor_fundamentals",
            provider_name="refinitiv",
            freshness_status="FRESH",
            source_count=3,
            fact_count=4,
            is_contradicted=False,
            completeness_band="COMPLETE",
            sentiment_polarity="POSITIVE",
            catalyst_category_raw="macro",
            materiality_raw="high",
            ticker_match_confidence_raw="high",
            holding_context={"category": "Crypto"},
        )
        out = normalize_and_evaluate(inp)
        assert out.decision_usefulness_tier == DECISION_USEFULNESS_INELIGIBLE

    def test_holding_context_etf_category_is_ineligible(self):
        inp = SentimentEventV2Input(
            ticker="QQQ",
            event_id="etf-002",
            source_authority="VENDOR_DERIVED",
            source_kind="vendor_fundamentals",
            provider_name="refinitiv",
            freshness_status="FRESH",
            source_count=3,
            fact_count=4,
            is_contradicted=False,
            completeness_band="PARTIAL",
            sentiment_polarity=None,
            catalyst_category_raw="macro",
            materiality_raw="medium",
            ticker_match_confidence_raw="high",
            holding_context={"category": "ETF"},
        )
        out = normalize_and_evaluate(inp)
        assert out.decision_usefulness_tier == DECISION_USEFULNESS_INELIGIBLE

    def test_equity_not_ineligible(self):
        """Equity tickers should not be blocked by the ineligibility guard."""
        out = normalize_and_evaluate(_VENDOR_COMPLETE_INPUT)
        assert out.decision_usefulness_tier != DECISION_USEFULNESS_INELIGIBLE

    def test_ineligible_has_failure_reason(self):
        out = normalize_and_evaluate(self._make_crypto_input("BTC"))
        assert "asset_type_ineligible_for_sentiment" in out.failure_reasons


# ── 11. Stage 8B.1 threshold behavior remains unchanged ──────────────────────


class TestStage8B1ThresholdUnchanged:
    """The underlying evaluate_sentiment_quality() must not have been changed."""

    def test_editorial_thin_not_usable_via_threshold(self):
        result = evaluate_sentiment_quality(
            freshness_status="FRESH",
            source_authority="EDITORIAL_CONTEXT",
            completeness_band="THIN",
            is_contradicted=False,
            source_count=10,
            fact_count=10,
        )
        assert result.quality_tier == "NOT_USABLE"
        assert result.is_decision_useful is False

    def test_vendor_partial_fresh_limited_via_threshold(self):
        result = evaluate_sentiment_quality(
            freshness_status="FRESH",
            source_authority="VENDOR_DERIVED",
            completeness_band="PARTIAL",
            is_contradicted=False,
            source_count=2,
            fact_count=3,
        )
        assert result.quality_tier == "LIMITED"
        assert result.is_decision_useful is True

    def test_vendor_complete_fresh_ready_via_threshold(self):
        result = evaluate_sentiment_quality(
            freshness_status="FRESH",
            source_authority="VENDOR_DERIVED",
            completeness_band="COMPLETE",
            is_contradicted=False,
            source_count=3,
            fact_count=5,
        )
        assert result.quality_tier == "READY"
        assert result.is_decision_useful is True

    def test_stale_vendor_not_usable_via_threshold(self):
        result = evaluate_sentiment_quality(
            freshness_status="STALE",
            source_authority="VENDOR_DERIVED",
            completeness_band="COMPLETE",
            is_contradicted=False,
            source_count=3,
            fact_count=5,
        )
        assert result.quality_tier == "NOT_USABLE"
        assert any("freshness_not_acceptable" in r for r in result.failure_reasons)

    def test_threshold_version_unchanged(self):
        result = evaluate_sentiment_quality(
            freshness_status="FRESH",
            source_authority="VENDOR_DERIVED",
            completeness_band="PARTIAL",
            is_contradicted=False,
            source_count=1,
            fact_count=1,
        )
        assert result.version == SENTIMENT_QUALITY_THRESHOLD_VERSION

    def test_adapter_wraps_quality_gate_for_vendor_derived(self):
        """Adapter result tiers must match quality gate result for vendor sources."""
        out = normalize_and_evaluate(_VENDOR_PARTIAL_INPUT)
        assert out.decision_usefulness_tier == DECISION_USEFULNESS_LIMITED

        out2 = normalize_and_evaluate(_VENDOR_COMPLETE_INPUT)
        assert out2.decision_usefulness_tier == DECISION_USEFULNESS_READY


# ── 12. No Buy/Hold/Trim/Sell emitted ────────────────────────────────────────


class TestNoActionAuthorityEmitted:
    """Adapter must never emit Buy/Hold/Trim/Sell or any decision-authority keys."""

    def _check_no_forbidden_keys(self, payload: dict) -> None:
        """Recursively check no forbidden keys appear anywhere in the payload."""
        if isinstance(payload, dict):
            for k, v in payload.items():
                assert k.lower() not in WORKER_FORBIDDEN_PAYLOAD_KEYS, (
                    f"Forbidden key '{k}' found in structured_payload"
                )
                self._check_no_forbidden_keys(v)
        elif isinstance(payload, list):
            for item in payload:
                self._check_no_forbidden_keys(item)

    def test_editorial_payload_no_forbidden_keys(self):
        out = normalize_and_evaluate(_EDITORIAL_INPUT)
        self._check_no_forbidden_keys(out.structured_payload)

    def test_vendor_partial_payload_no_forbidden_keys(self):
        out = normalize_and_evaluate(_VENDOR_PARTIAL_INPUT)
        self._check_no_forbidden_keys(out.structured_payload)

    def test_vendor_complete_payload_no_forbidden_keys(self):
        out = normalize_and_evaluate(_VENDOR_COMPLETE_INPUT)
        self._check_no_forbidden_keys(out.structured_payload)

    def test_company_authored_payload_no_forbidden_keys(self):
        out = normalize_and_evaluate(_COMPANY_AUTHORED_INPUT)
        self._check_no_forbidden_keys(out.structured_payload)

    def test_polarity_field_never_implies_action(self):
        """sentiment_polarity is preserved faithfully but never implies an action."""
        out = normalize_and_evaluate(_VENDOR_COMPLETE_INPUT)
        payload = out.structured_payload
        assert "sentiment_polarity" in payload
        # polarity string must not be an action string
        polarity = payload["sentiment_polarity"]
        if polarity is not None:
            assert polarity.lower() not in {"buy", "sell", "trim", "hold"}


# ── 13. Normalization helpers ────────────────────────────────────────────────


class TestNormalizationHelpers:
    def test_catalyst_category_known_aliases(self):
        assert normalize_catalyst_category("earnings_report") == CATALYST_CATEGORY_EARNINGS
        assert normalize_catalyst_category("Earnings_Report") == CATALYST_CATEGORY_EARNINGS
        assert normalize_catalyst_category("sec_filing") == CATALYST_CATEGORY_REGULATORY
        assert normalize_catalyst_category("merger") == "corporate_action"

    def test_catalyst_category_unknown_for_unrecognised(self):
        assert normalize_catalyst_category("random_garbage_xyz") == CATALYST_CATEGORY_UNKNOWN
        assert normalize_catalyst_category(None) == CATALYST_CATEGORY_UNKNOWN
        assert normalize_catalyst_category("") == CATALYST_CATEGORY_UNKNOWN

    def test_materiality_known_aliases(self):
        assert normalize_materiality("material") == MATERIALITY_HIGH
        assert normalize_materiality("SIGNIFICANT") == MATERIALITY_HIGH
        assert normalize_materiality("moderate") == "MEDIUM"
        assert normalize_materiality("immaterial") == "LOW"

    def test_materiality_unknown_for_unrecognised(self):
        assert normalize_materiality("garbage") == MATERIALITY_UNKNOWN
        assert normalize_materiality(None) == MATERIALITY_UNKNOWN

    def test_ticker_match_known_aliases(self):
        assert normalize_ticker_match_confidence("confirmed") == TICKER_MATCH_HIGH
        assert normalize_ticker_match_confidence("PROBABLE") == TICKER_MATCH_MEDIUM
        assert normalize_ticker_match_confidence("uncertain") == TICKER_MATCH_LOW

    def test_ticker_match_unknown_for_none(self):
        assert normalize_ticker_match_confidence(None) == TICKER_MATCH_UNKNOWN
        assert normalize_ticker_match_confidence("") == TICKER_MATCH_UNKNOWN

    def test_all_canonical_catalyst_categories_present(self):
        assert ALL_CATALYST_CATEGORIES >= {
            "earnings", "guidance", "regulatory", "macro",
            "corporate_action", "analyst_action", "product", "unknown",
        }

    def test_all_canonical_materiality_values_present(self):
        assert ALL_MATERIALITY_VALUES >= {"HIGH", "MEDIUM", "LOW", "UNKNOWN"}

    def test_all_canonical_ticker_match_values_present(self):
        assert ALL_TICKER_MATCH_VALUES >= {"HIGH", "MEDIUM", "LOW", "UNKNOWN"}


# ── 14. Safe URL handling ────────────────────────────────────────────────────


class TestSafeUrlHandling:
    def test_news_source_url_not_passed_through(self):
        inp = SentimentEventV2Input(
            ticker="MSFT",
            event_id="url-001",
            source_authority="EDITORIAL_CONTEXT",
            source_kind="news",
            provider_name="yfinance",
            freshness_status="FRESH",
            source_count=3,
            fact_count=3,
            is_contradicted=False,
            completeness_band="THIN",
            sentiment_polarity="POSITIVE",
            catalyst_category_raw="earnings",
            materiality_raw="high",
            ticker_match_confidence_raw="high",
            source_url="https://example-news-article.com/article123",
        )
        out = normalize_and_evaluate(inp)
        assert out.safe_source_url is None
        assert out.structured_payload["source_url"] is None

    def test_sec_filing_url_passed_through(self):
        inp = SentimentEventV2Input(
            ticker="MSFT",
            event_id="url-002",
            source_authority="PRIMARY_AUTHORITY",
            source_kind="sec_filing",
            provider_name="sec_edgar",
            freshness_status="FRESH",
            source_count=2,
            fact_count=4,
            is_contradicted=False,
            completeness_band="COMPLETE",
            sentiment_polarity=None,
            catalyst_category_raw="regulatory",
            materiality_raw="high",
            ticker_match_confidence_raw="high",
            source_url="https://www.sec.gov/Archives/edgar/data/789019/000078901924000001/0000789019-24-000001-index.htm",
        )
        out = normalize_and_evaluate(inp)
        assert out.safe_source_url is not None
        assert "sec.gov" in out.safe_source_url

    def test_press_release_url_passed_through(self):
        inp = SentimentEventV2Input(
            ticker="MSFT",
            event_id="url-003",
            source_authority="COMPANY_AUTHORED",
            source_kind="press_release",
            provider_name="ir_site",
            freshness_status="FRESH",
            source_count=1,
            fact_count=2,
            is_contradicted=False,
            completeness_band="PARTIAL",
            sentiment_polarity=None,
            catalyst_category_raw="guidance",
            materiality_raw="high",
            ticker_match_confidence_raw="high",
            source_url="https://news.microsoft.com/press-release/abc",
        )
        out = normalize_and_evaluate(inp)
        assert out.safe_source_url is not None

    def test_none_url_is_none(self):
        out = normalize_and_evaluate(_VENDOR_COMPLETE_INPUT)
        assert out.safe_source_url is None


# ── 15. Output invariants ────────────────────────────────────────────────────


class TestOutputInvariants:
    def test_output_version_is_set(self):
        out = normalize_and_evaluate(_VENDOR_COMPLETE_INPUT)
        assert out.version == ADAPTER_V2_VERSION

    def test_structured_payload_contains_ticker(self):
        out = normalize_and_evaluate(_VENDOR_COMPLETE_INPUT)
        assert out.structured_payload["ticker"] == "MSFT"

    def test_structured_payload_contains_adapter_version(self):
        out = normalize_and_evaluate(_VENDOR_COMPLETE_INPUT)
        assert out.structured_payload["adapter_version"] == ADAPTER_V2_VERSION

    def test_structured_payload_contains_decision_tier(self):
        out = normalize_and_evaluate(_VENDOR_COMPLETE_INPUT)
        assert "decision_usefulness_tier" in out.structured_payload
        assert out.structured_payload["decision_usefulness_tier"] == DECISION_USEFULNESS_READY

    def test_not_usable_has_failure_reasons(self):
        out = normalize_and_evaluate(_EDITORIAL_INPUT)
        assert len(out.failure_reasons) > 0

    def test_ready_has_no_failure_reasons(self):
        out = normalize_and_evaluate(_VENDOR_COMPLETE_INPUT)
        assert out.failure_reasons == ()

    def test_limited_has_no_failure_reasons(self):
        out = normalize_and_evaluate(_VENDOR_PARTIAL_INPUT)
        assert out.failure_reasons == ()

    def test_catalyst_category_set_on_output(self):
        out = normalize_and_evaluate(_VENDOR_COMPLETE_INPUT)
        assert out.catalyst_category == CATALYST_CATEGORY_EARNINGS

    def test_materiality_set_on_output(self):
        out = normalize_and_evaluate(_VENDOR_COMPLETE_INPUT)
        assert out.materiality == MATERIALITY_HIGH

    def test_dedupe_key_is_32_chars(self):
        out = normalize_and_evaluate(_VENDOR_COMPLETE_INPUT)
        assert len(out.dedupe_key) == 32
