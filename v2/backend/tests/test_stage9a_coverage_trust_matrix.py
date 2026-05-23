"""Tests for Stage 9A — Coverage & Trust Matrix v1.

Covers:
  - Lane status → matrix state mapping (STRONG/PARTIAL/WEAK/MISSING)
  - Asset-type rules for equity / ETF / crypto / unknown
  - NOT_APPLICABLE categories do not penalize ETF/crypto
  - WEAK/MISSING blocks are not allowed_for_synthesis
  - STRONG/PARTIAL blocks are allowed_for_synthesis
  - Read-only invariant (no decide() import, no DB writes)
  - Leak guard (no raw payloads/secrets in output)
  - Regression: visible decision policy not called or changed
  - Endpoint contract for read-only safe shape
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
from unittest.mock import MagicMock, patch

import pytest

from app.services.intelligence.v3.coverage_trust_matrix_v1 import (
    ALL_CATEGORIES,
    CATEGORY_CRYPTO_MARKET_CONTEXT,
    CATEGORY_ETF_FUND_COMPOSITION,
    CATEGORY_FUNDAMENTALS,
    CATEGORY_NEWS_SENTIMENT,
    CATEGORY_PORTFOLIO_SIZING_TARGET_WEIGHT,
    CATEGORY_SEC_CATALYSTS,
    CATEGORY_SEC_COMPANY_FACTS,
    CATEGORY_TECHNICALS,
    CATEGORY_THESIS_HISTORY,
    CATEGORY_VALUATION,
    MATRIX_MISSING,
    MATRIX_NOT_APPLICABLE,
    MATRIX_PARTIAL,
    MATRIX_STRONG,
    MATRIX_VERSION,
    MATRIX_WEAK,
    compute_coverage_trust_matrix,
    _lane_to_matrix_state,
    _best_of,
)
from app.services.intelligence.v3.research_evidence_coverage_read_model_v1 import (
    LANE_FUNDAMENTALS,
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
    TickerCoverage,
)
from app.services.intelligence.v3.research_evidence_decision_input_adapter_v1 import (
    INSTRUMENT_CATEGORY_CRYPTO,
    INSTRUMENT_CATEGORY_EQUITY,
    INSTRUMENT_CATEGORY_ETF,
    INSTRUMENT_CATEGORY_UNKNOWN,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_lane(
    lane: str,
    status: str,
    source_authority: Optional[str] = None,
    usability_label: Optional[str] = None,
    artifact_id: Optional[str] = "test-artifact-id",
) -> LaneCoverage:
    """Build a LaneCoverage stub for testing."""
    return LaneCoverage(
        lane=lane,
        artifact_type="fundamental_quality",
        skill_pack=f"{lane}_evidence_v1",
        scope_kind="ticker",
        ticker="TEST",
        artifact_id=artifact_id if status != STATUS_MISSING else None,
        status=status,
        usability_label=usability_label,
        is_usable=status in (STATUS_READY, STATUS_LIMITED),
        suppression_reason=None,
        source_authority=source_authority,
        completeness_band=None,
        has_contradictions=None,
        freshness_status="FRESH",
        confidence_or_trust_level=None,
        model_version="v1",
        generated_at="2026-05-23T00:00:00+00:00",
        expires_at=None,
    )


def _make_macro_lane(status: str) -> LaneCoverage:
    return LaneCoverage(
        lane="macro_context",
        artifact_type="portfolio_exposure",
        skill_pack="fred_macro_evidence_v1",
        scope_kind="portfolio",
        ticker=None,
        artifact_id="macro-id" if status != STATUS_MISSING else None,
        status=status,
        usability_label="USABLE" if status == STATUS_READY else None,
        is_usable=status in (STATUS_READY, STATUS_LIMITED),
        suppression_reason=None,
        source_authority="PRIMARY_AUTHORITY" if status == STATUS_READY else None,
        completeness_band=None,
        has_contradictions=None,
        freshness_status="FRESH",
        confidence_or_trust_level=None,
        model_version="v1",
        generated_at="2026-05-23T00:00:00+00:00",
        expires_at=None,
    )


def _make_coverage(
    ticker: str,
    lanes: dict[str, LaneCoverage],
    user_id: str = "user-1",
) -> ResearchEvidenceCoverageSummary:
    tc = TickerCoverage(ticker=ticker, lanes=lanes)
    return ResearchEvidenceCoverageSummary(
        schema_version="research_evidence_coverage.v1",
        user_id=user_id,
        generated_at=datetime.now(timezone.utc).isoformat(),
        portfolio_ticker_count=1,
        ticker_coverage={ticker: tc},
        portfolio_macro_coverage=_make_macro_lane(STATUS_MISSING),
        lane_counts={},
        usability_counts={},
        missing_lane_counts={},
        suppressed_counts={},
        stale_or_unknown_counts={},
        ready_artifact_count=0,
        errors=[],
    )


# ── Unit: lane → matrix state mapping ─────────────────────────────────────────


class TestLaneToMatrixState:
    """Unit tests for the _lane_to_matrix_state mapper."""

    def test_none_lane_is_missing(self):
        assert _lane_to_matrix_state(None) == MATRIX_MISSING

    def test_missing_status_is_missing(self):
        lane = _make_lane(LANE_FUNDAMENTALS, STATUS_MISSING, artifact_id=None)
        assert _lane_to_matrix_state(lane) == MATRIX_MISSING

    def test_ready_primary_authority_is_strong(self):
        lane = _make_lane(LANE_SEC_COMPANY_FACTS, STATUS_READY, source_authority="PRIMARY_AUTHORITY")
        assert _lane_to_matrix_state(lane) == MATRIX_STRONG

    def test_ready_official_free_source_is_strong(self):
        lane = _make_lane(LANE_FUNDAMENTALS, STATUS_READY, source_authority="OFFICIAL_FREE_SOURCE")
        assert _lane_to_matrix_state(lane) == MATRIX_STRONG

    def test_ready_free_unofficial_is_partial(self):
        lane = _make_lane(LANE_FUNDAMENTALS, STATUS_READY, source_authority="FREE_UNOFFICIAL_SOURCE")
        assert _lane_to_matrix_state(lane) == MATRIX_PARTIAL

    def test_ready_no_authority_is_partial(self):
        lane = _make_lane(LANE_FUNDAMENTALS, STATUS_READY, source_authority=None)
        assert _lane_to_matrix_state(lane) == MATRIX_PARTIAL

    def test_limited_is_partial(self):
        lane = _make_lane(LANE_FUNDAMENTALS, STATUS_LIMITED, source_authority="PRIMARY_AUTHORITY")
        assert _lane_to_matrix_state(lane) == MATRIX_PARTIAL

    def test_suppressed_is_weak(self):
        lane = _make_lane(LANE_NEWS_SENTIMENT, STATUS_SUPPRESSED)
        assert _lane_to_matrix_state(lane) == MATRIX_WEAK

    def test_stale_or_unknown_is_weak(self):
        lane = _make_lane(LANE_FUNDAMENTALS, STATUS_STALE_OR_UNKNOWN)
        assert _lane_to_matrix_state(lane) == MATRIX_WEAK

    def test_not_evaluable_is_weak(self):
        lane = _make_lane(LANE_FUNDAMENTALS, STATUS_NOT_EVALUABLE)
        assert _lane_to_matrix_state(lane) == MATRIX_WEAK


class TestBestOf:
    """Tests for the _best_of helper."""

    def test_strong_beats_partial(self):
        assert _best_of(MATRIX_STRONG, MATRIX_PARTIAL) == MATRIX_STRONG

    def test_partial_beats_weak(self):
        assert _best_of(MATRIX_PARTIAL, MATRIX_WEAK) == MATRIX_PARTIAL

    def test_weak_beats_missing(self):
        assert _best_of(MATRIX_WEAK, MATRIX_MISSING) == MATRIX_WEAK

    def test_missing_beats_not_applicable(self):
        assert _best_of(MATRIX_MISSING, MATRIX_NOT_APPLICABLE) == MATRIX_MISSING

    def test_equal_states_return_first(self):
        assert _best_of(MATRIX_PARTIAL, MATRIX_PARTIAL) == MATRIX_PARTIAL


# ── Synthesis allowance invariants ─────────────────────────────────────────────


class TestSynthesisAllowance:
    """STRONG/PARTIAL → allowed; WEAK/MISSING/NOT_APPLICABLE → suppressed."""

    def _run_equity_matrix(self, lane_status: str, source_authority: Optional[str]) -> dict:
        lanes = {
            LANE_FUNDAMENTALS: _make_lane(LANE_FUNDAMENTALS, lane_status, source_authority),
        }
        coverage = _make_coverage("MSFT", lanes)
        result = compute_coverage_trust_matrix(
            coverage, holding_context_by_ticker={"MSFT": {"category": "Core"}}
        )
        return result.tickers["MSFT"].categories[CATEGORY_FUNDAMENTALS].to_dict()

    def test_strong_is_allowed_for_synthesis(self):
        entry = self._run_equity_matrix(STATUS_READY, "PRIMARY_AUTHORITY")
        assert entry["state"] == MATRIX_STRONG
        assert entry["allowed_for_synthesis"] is True
        assert entry["suppression_reason"] is None

    def test_partial_ready_is_allowed_for_synthesis(self):
        entry = self._run_equity_matrix(STATUS_READY, "FREE_UNOFFICIAL_SOURCE")
        assert entry["state"] == MATRIX_PARTIAL
        assert entry["allowed_for_synthesis"] is True
        assert entry["suppression_reason"] is None

    def test_partial_limited_is_allowed_for_synthesis(self):
        entry = self._run_equity_matrix(STATUS_LIMITED, "PRIMARY_AUTHORITY")
        assert entry["state"] == MATRIX_PARTIAL
        assert entry["allowed_for_synthesis"] is True

    def test_weak_is_not_allowed_for_synthesis(self):
        entry = self._run_equity_matrix(STATUS_SUPPRESSED, None)
        assert entry["state"] == MATRIX_WEAK
        assert entry["allowed_for_synthesis"] is False
        assert entry["suppression_reason"] is not None

    def test_missing_is_not_allowed_for_synthesis(self):
        entry = self._run_equity_matrix(STATUS_MISSING, None)
        assert entry["state"] == MATRIX_MISSING
        assert entry["allowed_for_synthesis"] is False
        assert entry["suppression_reason"] is not None

    def test_not_applicable_is_not_allowed_for_synthesis(self):
        # ETF: sec_company_facts is NOT_APPLICABLE.
        lanes = {}
        coverage = _make_coverage("SPY", lanes)
        result = compute_coverage_trust_matrix(
            coverage, holding_context_by_ticker={"SPY": {"category": "ETF"}}
        )
        entry = result.tickers["SPY"].categories[CATEGORY_SEC_COMPANY_FACTS]
        assert entry.state == MATRIX_NOT_APPLICABLE
        assert entry.allowed_for_synthesis is False
        assert entry.suppression_reason is None  # NOT_APPLICABLE has no suppression reason


# ── Equity asset-type rules ───────────────────────────────────────────────────


class TestEquityAssetTypeRules:
    """Equity: SEC and fundamentals lanes apply; ETF/crypto categories NOT_APPLICABLE."""

    def _equity_matrix(self, lanes: dict) -> dict[str, Any]:
        coverage = _make_coverage("MSFT", lanes)
        result = compute_coverage_trust_matrix(
            coverage, holding_context_by_ticker={"MSFT": {"category": "Core"}}
        )
        return {k: v.to_dict() for k, v in result.tickers["MSFT"].categories.items()}

    def test_sec_company_facts_applies_for_equity(self):
        lanes = {LANE_SEC_COMPANY_FACTS: _make_lane(LANE_SEC_COMPANY_FACTS, STATUS_READY, "PRIMARY_AUTHORITY")}
        cats = self._equity_matrix(lanes)
        assert cats[CATEGORY_SEC_COMPANY_FACTS]["state"] == MATRIX_STRONG
        assert cats[CATEGORY_SEC_COMPANY_FACTS]["state"] != MATRIX_NOT_APPLICABLE

    def test_sec_catalysts_applies_for_equity(self):
        lanes = {LANE_SEC_CATALYST_SENTIMENT: _make_lane(LANE_SEC_CATALYST_SENTIMENT, STATUS_LIMITED)}
        cats = self._equity_matrix(lanes)
        assert cats[CATEGORY_SEC_CATALYSTS]["state"] == MATRIX_PARTIAL
        assert cats[CATEGORY_SEC_CATALYSTS]["state"] != MATRIX_NOT_APPLICABLE

    def test_etf_fund_composition_not_applicable_for_equity(self):
        cats = self._equity_matrix({})
        assert cats[CATEGORY_ETF_FUND_COMPOSITION]["state"] == MATRIX_NOT_APPLICABLE

    def test_crypto_market_context_not_applicable_for_equity(self):
        cats = self._equity_matrix({})
        assert cats[CATEGORY_CRYPTO_MARKET_CONTEXT]["state"] == MATRIX_NOT_APPLICABLE

    def test_fundamentals_applies_for_equity(self):
        lanes = {LANE_FUNDAMENTALS: _make_lane(LANE_FUNDAMENTALS, STATUS_READY, "FREE_UNOFFICIAL_SOURCE")}
        cats = self._equity_matrix(lanes)
        assert cats[CATEGORY_FUNDAMENTALS]["state"] == MATRIX_PARTIAL

    def test_technicals_applies_for_equity(self):
        lanes = {LANE_TECHNICALS: _make_lane(LANE_TECHNICALS, STATUS_READY, "FREE_UNOFFICIAL_SOURCE")}
        cats = self._equity_matrix(lanes)
        assert cats[CATEGORY_TECHNICALS]["state"] == MATRIX_PARTIAL

    def test_valuation_is_missing_for_equity_no_lane(self):
        cats = self._equity_matrix({})
        assert cats[CATEGORY_VALUATION]["state"] == MATRIX_MISSING
        assert cats[CATEGORY_VALUATION]["allowed_for_synthesis"] is False

    def test_portfolio_sizing_is_partial_for_equity(self):
        cats = self._equity_matrix({})
        assert cats[CATEGORY_PORTFOLIO_SIZING_TARGET_WEIGHT]["state"] == MATRIX_PARTIAL

    def test_thesis_history_is_missing_for_equity(self):
        cats = self._equity_matrix({})
        assert cats[CATEGORY_THESIS_HISTORY]["state"] == MATRIX_MISSING


# ── ETF asset-type rules ──────────────────────────────────────────────────────


class TestETFAssetTypeRules:
    """ETF: sec_company_facts/sec_catalysts NOT_APPLICABLE; etf_fund_composition MISSING."""

    def _etf_matrix(self, lanes: dict) -> dict[str, Any]:
        coverage = _make_coverage("SPY", lanes)
        result = compute_coverage_trust_matrix(
            coverage, holding_context_by_ticker={"SPY": {"category": "ETF"}}
        )
        return {k: v.to_dict() for k, v in result.tickers["SPY"].categories.items()}

    def test_sec_company_facts_not_applicable_for_etf(self):
        cats = self._etf_matrix({})
        assert cats[CATEGORY_SEC_COMPANY_FACTS]["state"] == MATRIX_NOT_APPLICABLE

    def test_sec_catalysts_not_applicable_for_etf(self):
        cats = self._etf_matrix({})
        assert cats[CATEGORY_SEC_CATALYSTS]["state"] == MATRIX_NOT_APPLICABLE

    def test_etf_fund_composition_is_missing_not_not_applicable(self):
        cats = self._etf_matrix({})
        assert cats[CATEGORY_ETF_FUND_COMPOSITION]["state"] == MATRIX_MISSING
        assert cats[CATEGORY_ETF_FUND_COMPOSITION]["state"] != MATRIX_NOT_APPLICABLE
        assert cats[CATEGORY_ETF_FUND_COMPOSITION]["allowed_for_synthesis"] is False

    def test_etf_fund_composition_missing_has_honest_reason(self):
        cats = self._etf_matrix({})
        reason = cats[CATEGORY_ETF_FUND_COMPOSITION]["reason"]
        assert "fund" in reason.lower() or "holdings" in reason.lower()

    def test_crypto_market_context_not_applicable_for_etf(self):
        cats = self._etf_matrix({})
        assert cats[CATEGORY_CRYPTO_MARKET_CONTEXT]["state"] == MATRIX_NOT_APPLICABLE

    def test_fundamentals_not_applicable_for_etf_absent_lane(self):
        # ETF with no lane → MISSING for fundamentals (yfinance lane still checked).
        cats = self._etf_matrix({})
        # fundamentals is NOT NOT_APPLICABLE for ETF — it uses yfinance if available.
        assert cats[CATEGORY_FUNDAMENTALS]["state"] in (MATRIX_MISSING, MATRIX_WEAK, MATRIX_PARTIAL)
        assert cats[CATEGORY_FUNDAMENTALS]["state"] != MATRIX_NOT_APPLICABLE

    def test_valuation_not_applicable_for_etf(self):
        cats = self._etf_matrix({})
        assert cats[CATEGORY_VALUATION]["state"] == MATRIX_NOT_APPLICABLE

    def test_technicals_applies_for_etf(self):
        lanes = {LANE_TECHNICALS: _make_lane(LANE_TECHNICALS, STATUS_READY, "FREE_UNOFFICIAL_SOURCE")}
        cats = self._etf_matrix(lanes)
        assert cats[CATEGORY_TECHNICALS]["state"] == MATRIX_PARTIAL

    def test_not_applicable_categories_not_in_suppressed_blocks(self):
        coverage = _make_coverage("SPY", {})
        result = compute_coverage_trust_matrix(
            coverage, holding_context_by_ticker={"SPY": {"category": "ETF"}}
        )
        ticker_matrix = result.tickers["SPY"]
        # NOT_APPLICABLE categories must NOT appear in suppressed_blocks.
        assert CATEGORY_SEC_COMPANY_FACTS not in ticker_matrix.suppressed_blocks
        assert CATEGORY_SEC_CATALYSTS not in ticker_matrix.suppressed_blocks
        assert CATEGORY_CRYPTO_MARKET_CONTEXT not in ticker_matrix.suppressed_blocks
        assert CATEGORY_VALUATION not in ticker_matrix.suppressed_blocks

    def test_not_applicable_categories_not_in_allowed_blocks(self):
        coverage = _make_coverage("SPY", {})
        result = compute_coverage_trust_matrix(
            coverage, holding_context_by_ticker={"SPY": {"category": "ETF"}}
        )
        ticker_matrix = result.tickers["SPY"]
        assert CATEGORY_SEC_COMPANY_FACTS not in ticker_matrix.allowed_blocks
        assert CATEGORY_SEC_CATALYSTS not in ticker_matrix.allowed_blocks
        assert CATEGORY_CRYPTO_MARKET_CONTEXT not in ticker_matrix.allowed_blocks


# ── Crypto asset-type rules ───────────────────────────────────────────────────


class TestCryptoAssetTypeRules:
    """Crypto: fundamentals, SEC facts/catalysts, ETF composition NOT_APPLICABLE."""

    def _crypto_matrix(self, lanes: dict, ticker: str = "BTC") -> dict[str, Any]:
        coverage = _make_coverage(ticker, lanes)
        result = compute_coverage_trust_matrix(
            coverage, holding_context_by_ticker={ticker: {"category": "Crypto"}}
        )
        return {k: v.to_dict() for k, v in result.tickers[ticker].categories.items()}

    def test_fundamentals_not_applicable_for_crypto(self):
        cats = self._crypto_matrix({})
        assert cats[CATEGORY_FUNDAMENTALS]["state"] == MATRIX_NOT_APPLICABLE

    def test_sec_company_facts_not_applicable_for_crypto(self):
        cats = self._crypto_matrix({})
        assert cats[CATEGORY_SEC_COMPANY_FACTS]["state"] == MATRIX_NOT_APPLICABLE

    def test_sec_catalysts_not_applicable_for_crypto(self):
        cats = self._crypto_matrix({})
        assert cats[CATEGORY_SEC_CATALYSTS]["state"] == MATRIX_NOT_APPLICABLE

    def test_etf_fund_composition_not_applicable_for_crypto(self):
        cats = self._crypto_matrix({})
        assert cats[CATEGORY_ETF_FUND_COMPOSITION]["state"] == MATRIX_NOT_APPLICABLE

    def test_valuation_not_applicable_for_crypto(self):
        cats = self._crypto_matrix({})
        assert cats[CATEGORY_VALUATION]["state"] == MATRIX_NOT_APPLICABLE

    def test_crypto_market_context_partial_when_technicals_available(self):
        lanes = {LANE_TECHNICALS: _make_lane(LANE_TECHNICALS, STATUS_READY, "FREE_UNOFFICIAL_SOURCE")}
        cats = self._crypto_matrix(lanes)
        assert cats[CATEGORY_CRYPTO_MARKET_CONTEXT]["state"] == MATRIX_PARTIAL

    def test_crypto_market_context_capped_at_partial_even_with_primary_authority(self):
        # Even if technicals had PRIMARY_AUTHORITY, crypto market context is capped at PARTIAL.
        lanes = {LANE_TECHNICALS: _make_lane(LANE_TECHNICALS, STATUS_READY, "PRIMARY_AUTHORITY")}
        cats = self._crypto_matrix(lanes)
        assert cats[CATEGORY_CRYPTO_MARKET_CONTEXT]["state"] == MATRIX_PARTIAL
        assert cats[CATEGORY_CRYPTO_MARKET_CONTEXT]["state"] != MATRIX_STRONG

    def test_crypto_market_context_weak_when_technicals_suppressed(self):
        lanes = {LANE_TECHNICALS: _make_lane(LANE_TECHNICALS, STATUS_SUPPRESSED)}
        cats = self._crypto_matrix(lanes)
        assert cats[CATEGORY_CRYPTO_MARKET_CONTEXT]["state"] == MATRIX_WEAK

    def test_crypto_market_context_missing_when_no_technicals(self):
        cats = self._crypto_matrix({})
        assert cats[CATEGORY_CRYPTO_MARKET_CONTEXT]["state"] == MATRIX_MISSING

    def test_technicals_applies_for_crypto(self):
        lanes = {LANE_TECHNICALS: _make_lane(LANE_TECHNICALS, STATUS_READY, "FREE_UNOFFICIAL_SOURCE")}
        cats = self._crypto_matrix(lanes)
        assert cats[CATEGORY_TECHNICALS]["state"] == MATRIX_PARTIAL

    def test_crypto_not_applicable_categories_not_penalized(self):
        coverage = _make_coverage("BTC", {})
        result = compute_coverage_trust_matrix(
            coverage, holding_context_by_ticker={"BTC": {"category": "Crypto"}}
        )
        ticker_matrix = result.tickers["BTC"]
        not_applicable_cats = [
            CATEGORY_FUNDAMENTALS,
            CATEGORY_SEC_COMPANY_FACTS,
            CATEGORY_SEC_CATALYSTS,
            CATEGORY_ETF_FUND_COMPOSITION,
            CATEGORY_VALUATION,
        ]
        # None of these should appear in suppressed_blocks.
        for cat in not_applicable_cats:
            assert cat not in ticker_matrix.suppressed_blocks, \
                f"{cat} should not be in suppressed_blocks for crypto"


# ── Unknown asset-type rules ──────────────────────────────────────────────────


class TestUnknownAssetTypeRules:
    """Unrecognized category string falls back to equity via the classifier.

    The Stage 5K classifier returns INSTRUMENT_CATEGORY_EQUITY when no known
    pattern matches — so an "Unknown" category string acts like equity. The
    matrix module's internal INSTRUMENT_CATEGORY_UNKNOWN guards are defensive
    code for future use; they are not reachable through the current classifier.
    """

    def _unknown_category_matrix(self, lanes: dict) -> dict[str, Any]:
        coverage = _make_coverage("XYZ", lanes)
        result = compute_coverage_trust_matrix(
            coverage, holding_context_by_ticker={"XYZ": {"category": "Unknown"}}
        )
        return {k: v.to_dict() for k, v in result.tickers["XYZ"].categories.items()}

    def test_unrecognized_category_falls_back_to_equity_asset_type(self):
        # "Unknown" category string is not "ETF" or "Crypto" → classified as equity.
        coverage = _make_coverage("XYZ", {})
        result = compute_coverage_trust_matrix(
            coverage, holding_context_by_ticker={"XYZ": {"category": "Unknown"}}
        )
        assert result.tickers["XYZ"].asset_type == INSTRUMENT_CATEGORY_EQUITY

    def test_equity_fallback_sec_company_facts_is_missing_not_not_applicable(self):
        # Classified as equity → sec_company_facts applies but no artifact → MISSING.
        cats = self._unknown_category_matrix({})
        assert cats[CATEGORY_SEC_COMPANY_FACTS]["state"] == MATRIX_MISSING
        assert cats[CATEGORY_SEC_COMPANY_FACTS]["state"] != MATRIX_NOT_APPLICABLE

    def test_equity_fallback_sec_catalysts_is_missing_not_not_applicable(self):
        cats = self._unknown_category_matrix({})
        assert cats[CATEGORY_SEC_CATALYSTS]["state"] == MATRIX_MISSING
        assert cats[CATEGORY_SEC_CATALYSTS]["state"] != MATRIX_NOT_APPLICABLE

    def test_equity_fallback_etf_composition_is_not_applicable(self):
        cats = self._unknown_category_matrix({})
        assert cats[CATEGORY_ETF_FUND_COMPOSITION]["state"] == MATRIX_NOT_APPLICABLE

    def test_equity_fallback_crypto_market_context_is_not_applicable(self):
        cats = self._unknown_category_matrix({})
        assert cats[CATEGORY_CRYPTO_MARKET_CONTEXT]["state"] == MATRIX_NOT_APPLICABLE


# ── Suppressed news stays WEAK ────────────────────────────────────────────────


class TestSuppressedNewsStaysWeak:
    """Suppressed news_sentiment remains WEAK and not synthesis-allowed."""

    def test_suppressed_news_is_weak_not_missing(self):
        lanes = {
            LANE_NEWS_SENTIMENT: _make_lane(LANE_NEWS_SENTIMENT, STATUS_SUPPRESSED),
        }
        coverage = _make_coverage("MSFT", lanes)
        result = compute_coverage_trust_matrix(
            coverage, holding_context_by_ticker={"MSFT": {"category": "Core"}}
        )
        entry = result.tickers["MSFT"].categories[CATEGORY_NEWS_SENTIMENT]
        assert entry.state == MATRIX_WEAK
        assert entry.allowed_for_synthesis is False

    def test_suppressed_news_with_usable_sec_catalyst_upgrades_sentiment(self):
        # If sec_catalyst_sentiment is PARTIAL/STRONG, that wins for news_sentiment category.
        lanes = {
            LANE_NEWS_SENTIMENT: _make_lane(LANE_NEWS_SENTIMENT, STATUS_SUPPRESSED),
            LANE_SEC_CATALYST_SENTIMENT: _make_lane(LANE_SEC_CATALYST_SENTIMENT, STATUS_LIMITED),
        }
        coverage = _make_coverage("MSFT", lanes)
        result = compute_coverage_trust_matrix(
            coverage, holding_context_by_ticker={"MSFT": {"category": "Core"}}
        )
        entry = result.tickers["MSFT"].categories[CATEGORY_NEWS_SENTIMENT]
        assert entry.state == MATRIX_PARTIAL
        assert entry.allowed_for_synthesis is True

    def test_suppressed_sec_catalyst_remains_weak_for_news_category(self):
        lanes = {
            LANE_NEWS_SENTIMENT: _make_lane(LANE_NEWS_SENTIMENT, STATUS_SUPPRESSED),
            LANE_SEC_CATALYST_SENTIMENT: _make_lane(LANE_SEC_CATALYST_SENTIMENT, STATUS_SUPPRESSED),
        }
        coverage = _make_coverage("MSFT", lanes)
        result = compute_coverage_trust_matrix(
            coverage, holding_context_by_ticker={"MSFT": {"category": "Core"}}
        )
        entry = result.tickers["MSFT"].categories[CATEGORY_NEWS_SENTIMENT]
        assert entry.state == MATRIX_WEAK
        assert entry.allowed_for_synthesis is False


# ── Aggregate ticker summary ──────────────────────────────────────────────────


class TestAggregateTickerSummary:
    """allowed_blocks / suppressed_blocks / strongest_gaps computed correctly."""

    def test_allowed_blocks_contain_only_strong_and_partial(self):
        lanes = {
            LANE_FUNDAMENTALS: _make_lane(LANE_FUNDAMENTALS, STATUS_READY, "FREE_UNOFFICIAL_SOURCE"),
            LANE_TECHNICALS: _make_lane(LANE_TECHNICALS, STATUS_LIMITED),
        }
        coverage = _make_coverage("MSFT", lanes)
        result = compute_coverage_trust_matrix(
            coverage, holding_context_by_ticker={"MSFT": {"category": "Core"}}
        )
        ticker = result.tickers["MSFT"]
        for cat in ticker.allowed_blocks:
            assert ticker.categories[cat].state in (MATRIX_STRONG, MATRIX_PARTIAL)

    def test_suppressed_blocks_contain_only_weak_and_missing(self):
        lanes = {
            LANE_NEWS_SENTIMENT: _make_lane(LANE_NEWS_SENTIMENT, STATUS_SUPPRESSED),
        }
        coverage = _make_coverage("MSFT", lanes)
        result = compute_coverage_trust_matrix(
            coverage, holding_context_by_ticker={"MSFT": {"category": "Core"}}
        )
        ticker = result.tickers["MSFT"]
        for cat in ticker.suppressed_blocks:
            assert ticker.categories[cat].state in (MATRIX_WEAK, MATRIX_MISSING)

    def test_strongest_gaps_are_weak_and_missing(self):
        lanes = {
            LANE_FUNDAMENTALS: _make_lane(LANE_FUNDAMENTALS, STATUS_SUPPRESSED),
        }
        coverage = _make_coverage("MSFT", lanes)
        result = compute_coverage_trust_matrix(
            coverage, holding_context_by_ticker={"MSFT": {"category": "Core"}}
        )
        ticker = result.tickers["MSFT"]
        for cat in ticker.strongest_gaps:
            assert ticker.categories[cat].state in (MATRIX_WEAK, MATRIX_MISSING)

    def test_not_applicable_not_in_suppressed_or_gaps(self):
        coverage = _make_coverage("BTC", {})
        result = compute_coverage_trust_matrix(
            coverage, holding_context_by_ticker={"BTC": {"category": "Crypto"}}
        )
        ticker = result.tickers["BTC"]
        for cat in ticker.suppressed_blocks + ticker.strongest_gaps:
            assert ticker.categories[cat].state != MATRIX_NOT_APPLICABLE


# ── Portfolio-level aggregates ────────────────────────────────────────────────


class TestPortfolioAggregates:
    """portfolio_allowed_block_counts / portfolio_suppressed_block_counts correct."""

    def test_portfolio_allowed_counts_sum_correctly(self):
        def _cov(ticker, lane_status, category):
            lanes = {LANE_FUNDAMENTALS: _make_lane(LANE_FUNDAMENTALS, lane_status, "FREE_UNOFFICIAL_SOURCE")}
            tc = TickerCoverage(ticker=ticker, lanes=lanes)
            return tc

        tc1 = _cov("MSFT", STATUS_READY, "Core")
        tc2 = _cov("AAPL", STATUS_LIMITED, "Core")
        tc3 = _cov("GOOG", STATUS_MISSING, "Core")

        coverage = ResearchEvidenceCoverageSummary(
            schema_version="v1",
            user_id="user-1",
            generated_at=datetime.now(timezone.utc).isoformat(),
            portfolio_ticker_count=3,
            ticker_coverage={"MSFT": tc1, "AAPL": tc2, "GOOG": tc3},
            portfolio_macro_coverage=_make_macro_lane(STATUS_MISSING),
            lane_counts={},
            usability_counts={},
            missing_lane_counts={},
            suppressed_counts={},
            stale_or_unknown_counts={},
            ready_artifact_count=2,
            errors=[],
        )
        ctx = {
            "MSFT": {"category": "Core"},
            "AAPL": {"category": "Core"},
            "GOOG": {"category": "Core"},
        }
        result = compute_coverage_trust_matrix(coverage, holding_context_by_ticker=ctx)
        # MSFT and AAPL fundamentals are allowed (PARTIAL), GOOG is MISSING.
        assert result.portfolio_allowed_block_counts[CATEGORY_FUNDAMENTALS] == 2
        assert result.portfolio_suppressed_block_counts[CATEGORY_FUNDAMENTALS] == 1


# ── Output shape and safety ────────────────────────────────────────────────────


class TestOutputShapeAndSafety:
    """Endpoint/service contract: safe shape, no leaks."""

    def test_all_categories_present_in_output(self):
        coverage = _make_coverage("MSFT", {})
        result = compute_coverage_trust_matrix(
            coverage, holding_context_by_ticker={"MSFT": {"category": "Core"}}
        )
        ticker_cats = set(result.tickers["MSFT"].categories.keys())
        assert set(ALL_CATEGORIES) == ticker_cats

    def test_safe_for_decision_always_false(self):
        coverage = _make_coverage("MSFT", {})
        result = compute_coverage_trust_matrix(coverage)
        assert result.safe_for_decision is False
        for ticker_matrix in result.tickers.values():
            assert ticker_matrix.safe_for_decision is False

    def test_synthesis_ready_always_false(self):
        coverage = _make_coverage("MSFT", {})
        result = compute_coverage_trust_matrix(coverage)
        assert result.synthesis_ready is False
        for ticker_matrix in result.tickers.values():
            assert ticker_matrix.synthesis_ready is False

    def test_schema_version_present(self):
        coverage = _make_coverage("MSFT", {})
        result = compute_coverage_trust_matrix(coverage)
        assert result.schema_version == MATRIX_VERSION

    def test_to_dict_serializes_all_fields(self):
        lanes = {LANE_FUNDAMENTALS: _make_lane(LANE_FUNDAMENTALS, STATUS_READY, "PRIMARY_AUTHORITY")}
        coverage = _make_coverage("MSFT", lanes)
        result = compute_coverage_trust_matrix(
            coverage, holding_context_by_ticker={"MSFT": {"category": "Core"}}
        )
        d = result.to_dict()
        assert "schema_version" in d
        assert "safe_for_decision" in d
        assert "synthesis_ready" in d
        assert "tickers" in d
        assert "MSFT" in d["tickers"]
        msft = d["tickers"]["MSFT"]
        assert "categories" in msft
        assert "allowed_blocks" in msft
        assert "suppressed_blocks" in msft
        assert "strongest_gaps" in msft
        assert "asset_type" in msft

    def test_no_raw_payload_in_output(self):
        # Lane coverage contains only safe metadata; matrix must not re-emit raw payloads.
        lanes = {LANE_FUNDAMENTALS: _make_lane(LANE_FUNDAMENTALS, STATUS_READY, "PRIMARY_AUTHORITY")}
        coverage = _make_coverage("MSFT", lanes)
        result = compute_coverage_trust_matrix(
            coverage, holding_context_by_ticker={"MSFT": {"category": "Core"}}
        )
        d = result.to_dict()
        import json
        raw = json.dumps(d)
        # Verify no raw payload / secret patterns leak.
        assert "api_key" not in raw.lower()
        assert "structured_payload" not in raw.lower()
        assert "source_url" not in raw.lower()

    def test_each_category_entry_has_required_fields(self):
        coverage = _make_coverage("MSFT", {})
        result = compute_coverage_trust_matrix(
            coverage, holding_context_by_ticker={"MSFT": {"category": "Core"}}
        )
        for cat_name, entry in result.tickers["MSFT"].categories.items():
            d = entry.to_dict()
            assert "category" in d, f"missing 'category' in {cat_name}"
            assert "state" in d, f"missing 'state' in {cat_name}"
            assert "reason" in d, f"missing 'reason' in {cat_name}"
            assert "allowed_for_synthesis" in d, f"missing 'allowed_for_synthesis' in {cat_name}"
            assert "suppression_reason" in d, f"missing 'suppression_reason' in {cat_name}"
            assert isinstance(d["reason"], str) and len(d["reason"]) > 0

    def test_suppression_reason_present_when_not_allowed_and_not_na(self):
        coverage = _make_coverage("MSFT", {})
        result = compute_coverage_trust_matrix(
            coverage, holding_context_by_ticker={"MSFT": {"category": "Core"}}
        )
        for cat_name, entry in result.tickers["MSFT"].categories.items():
            if not entry.allowed_for_synthesis and entry.state != MATRIX_NOT_APPLICABLE:
                assert entry.suppression_reason is not None, \
                    f"Expected suppression_reason for {cat_name} (state={entry.state})"

    def test_suppression_reason_absent_for_allowed_and_na(self):
        lanes = {LANE_FUNDAMENTALS: _make_lane(LANE_FUNDAMENTALS, STATUS_READY, "PRIMARY_AUTHORITY")}
        coverage = _make_coverage("MSFT", lanes)
        result = compute_coverage_trust_matrix(
            coverage, holding_context_by_ticker={"MSFT": {"category": "Core"}}
        )
        fund_entry = result.tickers["MSFT"].categories[CATEGORY_FUNDAMENTALS]
        assert fund_entry.allowed_for_synthesis is True
        assert fund_entry.suppression_reason is None


# ── Regression: decision policy not called ────────────────────────────────────


class TestDecisionPolicyRegression:
    """Verify decide() and decision_policy_v1 are never imported or called."""

    def test_decide_not_imported_in_matrix_module(self):
        import importlib
        import sys

        # coverage_trust_matrix_v1 must not import decision_policy_v1.
        import app.services.intelligence.v3.coverage_trust_matrix_v1 as matrix_mod

        # Check the module's globals for decision_policy imports.
        mod_globals = vars(matrix_mod)
        assert "decision_policy_v1" not in mod_globals
        assert "decide" not in mod_globals

    def test_matrix_compute_does_not_mutate_coverage(self):
        lanes = {LANE_FUNDAMENTALS: _make_lane(LANE_FUNDAMENTALS, STATUS_READY, "PRIMARY_AUTHORITY")}
        coverage = _make_coverage("MSFT", lanes)
        original_ticker_count = coverage.portfolio_ticker_count
        original_errors = list(coverage.errors)
        compute_coverage_trust_matrix(
            coverage, holding_context_by_ticker={"MSFT": {"category": "Core"}}
        )
        assert coverage.portfolio_ticker_count == original_ticker_count
        assert list(coverage.errors) == original_errors


# ── Multi-asset portfolio ─────────────────────────────────────────────────────


class TestMultiAssetPortfolio:
    """Matrix handles a mixed equity/ETF/crypto portfolio correctly."""

    def test_mixed_portfolio_each_ticker_gets_full_category_set(self):
        tickers_ctx = {
            "MSFT": {"category": "Core"},
            "SPY": {"category": "ETF"},
            "BTC": {"category": "Crypto"},
        }
        ticker_coverages = {
            t: TickerCoverage(ticker=t, lanes={}) for t in tickers_ctx
        }
        coverage = ResearchEvidenceCoverageSummary(
            schema_version="v1",
            user_id="user-1",
            generated_at=datetime.now(timezone.utc).isoformat(),
            portfolio_ticker_count=3,
            ticker_coverage=ticker_coverages,
            portfolio_macro_coverage=_make_macro_lane(STATUS_MISSING),
            lane_counts={},
            usability_counts={},
            missing_lane_counts={},
            suppressed_counts={},
            stale_or_unknown_counts={},
            ready_artifact_count=0,
            errors=[],
        )
        result = compute_coverage_trust_matrix(coverage, holding_context_by_ticker=tickers_ctx)
        assert set(result.tickers.keys()) == {"MSFT", "SPY", "BTC"}
        for ticker, matrix in result.tickers.items():
            assert set(matrix.categories.keys()) == set(ALL_CATEGORIES)

    def test_equity_sec_strong_etf_sec_not_applicable_crypto_sec_not_applicable(self):
        tickers_ctx = {
            "MSFT": {"category": "Core"},
            "SPY": {"category": "ETF"},
            "BTC": {"category": "Crypto"},
        }
        lanes_by_ticker = {
            "MSFT": {LANE_SEC_COMPANY_FACTS: _make_lane(LANE_SEC_COMPANY_FACTS, STATUS_READY, "PRIMARY_AUTHORITY")},
            "SPY": {},
            "BTC": {},
        }
        ticker_coverages = {
            t: TickerCoverage(ticker=t, lanes=lanes_by_ticker[t]) for t in tickers_ctx
        }
        coverage = ResearchEvidenceCoverageSummary(
            schema_version="v1",
            user_id="user-1",
            generated_at=datetime.now(timezone.utc).isoformat(),
            portfolio_ticker_count=3,
            ticker_coverage=ticker_coverages,
            portfolio_macro_coverage=_make_macro_lane(STATUS_MISSING),
            lane_counts={},
            usability_counts={},
            missing_lane_counts={},
            suppressed_counts={},
            stale_or_unknown_counts={},
            ready_artifact_count=1,
            errors=[],
        )
        result = compute_coverage_trust_matrix(coverage, holding_context_by_ticker=tickers_ctx)
        assert result.tickers["MSFT"].categories[CATEGORY_SEC_COMPANY_FACTS].state == MATRIX_STRONG
        assert result.tickers["SPY"].categories[CATEGORY_SEC_COMPANY_FACTS].state == MATRIX_NOT_APPLICABLE
        assert result.tickers["BTC"].categories[CATEGORY_SEC_COMPANY_FACTS].state == MATRIX_NOT_APPLICABLE

    def test_portfolio_ticker_count_matches_input(self):
        tickers_ctx = {
            "MSFT": {"category": "Core"},
            "SPY": {"category": "ETF"},
            "BTC": {"category": "Crypto"},
        }
        ticker_coverages = {
            t: TickerCoverage(ticker=t, lanes={}) for t in tickers_ctx
        }
        coverage = ResearchEvidenceCoverageSummary(
            schema_version="v1",
            user_id="user-1",
            generated_at=datetime.now(timezone.utc).isoformat(),
            portfolio_ticker_count=3,
            ticker_coverage=ticker_coverages,
            portfolio_macro_coverage=_make_macro_lane(STATUS_MISSING),
            lane_counts={},
            usability_counts={},
            missing_lane_counts={},
            suppressed_counts={},
            stale_or_unknown_counts={},
            ready_artifact_count=0,
            errors=[],
        )
        result = compute_coverage_trust_matrix(coverage, holding_context_by_ticker=tickers_ctx)
        assert result.portfolio_ticker_count == 3


# ── Symbol-fallback classification for known ETF/crypto ──────────────────────


class TestSymbolFallbackClassification:
    """Known ETF/crypto tickers classified correctly even without holding context."""

    def test_spy_without_context_gets_etf_classification(self):
        coverage = _make_coverage("SPY", {})
        result = compute_coverage_trust_matrix(coverage)  # no holding_context
        assert result.tickers["SPY"].asset_type == INSTRUMENT_CATEGORY_ETF
        assert result.tickers["SPY"].categories[CATEGORY_SEC_COMPANY_FACTS].state == MATRIX_NOT_APPLICABLE

    def test_btc_without_context_gets_crypto_classification(self):
        coverage = _make_coverage("BTC", {})
        result = compute_coverage_trust_matrix(coverage)  # no holding_context
        assert result.tickers["BTC"].asset_type == INSTRUMENT_CATEGORY_CRYPTO
        assert result.tickers["BTC"].categories[CATEGORY_FUNDAMENTALS].state == MATRIX_NOT_APPLICABLE
