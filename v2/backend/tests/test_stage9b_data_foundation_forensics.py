"""Tests for Stage 9B — Intel Data Foundation Forensics v1.

Covers:
  - Root cause bucket classification (all 11 buckets, deterministic priority)
  - Artifact missing vs artifact exists but weak distinction
  - ETF provider-not-built classification
  - Valuation lane-not-built classification (always for equity at Stage 9B)
  - Crypto provider-not-built classification
  - No decision policy import/call (isolation guard)
  - Leak guard: no raw payload bodies, secrets, or provider dumps in output
  - Contract test: response shape has all required fields
  - Portfolio aggregates: holdings_by_asset_type, artifacts_by_lane, bucket_counts
  - provider_limited vs implementation_limited vs normalization_limited counts
  - Fail-soft: DB errors captured in errors[], not raised
  - safe_for_decision=False and synthesis_ready=False hardcoded
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
from unittest.mock import MagicMock, patch

import pytest

from app.services.intelligence.v3.intel_data_foundation_forensics_v1 import (
    ALL_BUCKETS,
    BUCKET_CRYPTO_NOT_BUILT,
    BUCKET_DATA_NEEDS_NORMALIZATION,
    BUCKET_ETF_NOT_BUILT,
    BUCKET_NEWS_SUPPRESSED,
    BUCKET_NOT_APPLICABLE,
    BUCKET_SEC_EXISTS_WEAK,
    BUCKET_SEC_MISSING_CIK,
    BUCKET_SEC_MISSING_WORKER,
    BUCKET_TARGET_WEIGHT_NOT_BUILT,
    BUCKET_THESIS_NOT_BUILT,
    BUCKET_VALUATION_NOT_BUILT,
    FORENSICS_VERSION,
    DataFoundationForensicsResult,
    HoldingForensicsRow,
    _SupplementalData,
    _artifact_exists,
    _build_holding_row,
    _classify_all_gaps,
    _classify_root_cause,
    _get_sec_reason_not_strong,
    _get_valuation_summary,
    compute_data_foundation_forensics,
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


# ── Test helpers ──────────────────────────────────────────────────────────────


def _make_lane(
    lane: str,
    status: str,
    artifact_id: Optional[str] = "art-001",
    usability_label: Optional[str] = None,
    source_authority: Optional[str] = None,
    is_usable: Optional[bool] = None,
) -> LaneCoverage:
    """Build a LaneCoverage stub for testing."""
    effective_is_usable = is_usable if is_usable is not None else status in (STATUS_READY, STATUS_LIMITED)
    return LaneCoverage(
        lane=lane,
        artifact_type="fundamental_quality",
        skill_pack=f"{lane}_evidence_v1",
        scope_kind="ticker",
        ticker="TEST",
        artifact_id=artifact_id if status != STATUS_MISSING else None,
        status=status,
        usability_label=usability_label,
        is_usable=effective_is_usable,
        suppression_reason=None,
        source_authority=source_authority,
        completeness_band=None,
        has_contradictions=None,
        freshness_status="FRESH",
        confidence_or_trust_level=None,
        model_version="v1",
        generated_at="2026-05-24T00:00:00+00:00",
        expires_at=None,
    )


def _empty_supplemental(
    *,
    target_tickers: frozenset = frozenset(),
    recommendation_tickers: frozenset = frozenset(),
    fact_counts: Optional[dict] = None,
    has_portfolio_snapshot: bool = True,
) -> _SupplementalData:
    return _SupplementalData(
        target_tickers=target_tickers,
        recommendation_tickers=recommendation_tickers,
        fact_counts=fact_counts or {},
        has_portfolio_snapshot=has_portfolio_snapshot,
        sec_fact_records={},
    )


def _make_coverage(
    ticker: str,
    lanes: dict[str, LaneCoverage],
    asset_type_hint: str = INSTRUMENT_CATEGORY_EQUITY,
) -> ResearchEvidenceCoverageSummary:
    ticker_cov = TickerCoverage(ticker=ticker, lanes=lanes)
    return ResearchEvidenceCoverageSummary(
        schema_version="v1",
        user_id="user-test",
        generated_at=datetime.now(timezone.utc).isoformat(),
        portfolio_ticker_count=1,
        ticker_coverage={ticker: ticker_cov},
        portfolio_macro_coverage=_make_lane(LANE_NEWS_SENTIMENT, STATUS_MISSING),
        lane_counts={},
        usability_counts={},
        missing_lane_counts={},
        suppressed_counts={},
        stale_or_unknown_counts={},
        ready_artifact_count=0,
        errors=[],
    )


# ── Root cause classification ─────────────────────────────────────────────────


class TestClassifyRootCauseBuckets:
    """All 11 deterministic buckets are reachable."""

    def test_etf_returns_etf_provider_not_built(self):
        bucket, fix = _classify_root_cause(
            asset_type=INSTRUMENT_CATEGORY_ETF,
            has_fundamentals_artifact=False,
            has_technical_artifact=False,
            has_sec_companyfacts_artifact=False,
            sec_companyfacts_usability=None,
            has_sec_catalyst_artifact=False,
            has_news_sentiment_artifact=False,
            news_sentiment_usability=None,
            has_target_weight=False,
            has_thesis_history=False,
        )
        assert bucket == BUCKET_ETF_NOT_BUILT
        assert bucket in ALL_BUCKETS

    def test_etf_with_all_data_still_returns_etf_provider_not_built(self):
        """ETF is always provider-not-built regardless of other artifacts."""
        bucket, _ = _classify_root_cause(
            asset_type=INSTRUMENT_CATEGORY_ETF,
            has_fundamentals_artifact=True,
            has_technical_artifact=True,
            has_sec_companyfacts_artifact=True,
            sec_companyfacts_usability="USABLE",
            has_sec_catalyst_artifact=True,
            has_news_sentiment_artifact=True,
            news_sentiment_usability="USABLE",
            has_target_weight=True,
            has_thesis_history=True,
            valuation_lane_exists=True,
        )
        assert bucket == BUCKET_ETF_NOT_BUILT

    def test_crypto_returns_crypto_provider_not_built(self):
        bucket, fix = _classify_root_cause(
            asset_type=INSTRUMENT_CATEGORY_CRYPTO,
            has_fundamentals_artifact=False,
            has_technical_artifact=True,
            has_sec_companyfacts_artifact=False,
            sec_companyfacts_usability=None,
            has_sec_catalyst_artifact=False,
            has_news_sentiment_artifact=False,
            news_sentiment_usability=None,
            has_target_weight=True,
            has_thesis_history=True,
        )
        assert bucket == BUCKET_CRYPTO_NOT_BUILT
        assert "crypto" in fix.lower()

    def test_unknown_returns_not_applicable(self):
        bucket, _ = _classify_root_cause(
            asset_type=INSTRUMENT_CATEGORY_UNKNOWN,
            has_fundamentals_artifact=False,
            has_technical_artifact=False,
            has_sec_companyfacts_artifact=False,
            sec_companyfacts_usability=None,
            has_sec_catalyst_artifact=False,
            has_news_sentiment_artifact=False,
            news_sentiment_usability=None,
            has_target_weight=False,
            has_thesis_history=False,
        )
        assert bucket == BUCKET_NOT_APPLICABLE

    def test_equity_no_artifacts_returns_worker_gap(self):
        """No artifacts at all → worker never ran."""
        bucket, fix = _classify_root_cause(
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            has_fundamentals_artifact=False,
            has_technical_artifact=False,
            has_sec_companyfacts_artifact=False,
            sec_companyfacts_usability=None,
            has_sec_catalyst_artifact=False,
            has_news_sentiment_artifact=False,
            news_sentiment_usability=None,
            has_target_weight=False,
            has_thesis_history=False,
        )
        assert bucket == BUCKET_SEC_MISSING_WORKER
        assert "POST /intel/v3/run" in fix

    def test_equity_other_artifacts_but_no_sec_returns_cik_gap(self):
        """Fundamentals and technicals ran but SEC was skipped → CIK/mapping issue."""
        bucket, fix = _classify_root_cause(
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            has_fundamentals_artifact=True,
            has_technical_artifact=True,
            has_sec_companyfacts_artifact=False,
            sec_companyfacts_usability=None,
            has_sec_catalyst_artifact=False,
            has_news_sentiment_artifact=True,
            news_sentiment_usability="SUPPRESSED_INCOMPLETE",
            has_target_weight=False,
            has_thesis_history=False,
        )
        assert bucket == BUCKET_SEC_MISSING_CIK
        assert "CIK" in fix

    def test_equity_technicals_only_but_no_sec_returns_cik_gap(self):
        """Technicals ran but SEC skipped → CIK gap (other lane ran)."""
        bucket, _ = _classify_root_cause(
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            has_fundamentals_artifact=False,
            has_technical_artifact=True,
            has_sec_companyfacts_artifact=False,
            sec_companyfacts_usability=None,
            has_sec_catalyst_artifact=False,
            has_news_sentiment_artifact=False,
            news_sentiment_usability=None,
            has_target_weight=False,
            has_thesis_history=False,
        )
        assert bucket == BUCKET_SEC_MISSING_CIK

    def test_equity_sec_suppressed_returns_exists_weak(self):
        """SEC artifact exists but SUPPRESSED_INCOMPLETE → exists-but-weak."""
        bucket, fix = _classify_root_cause(
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            has_fundamentals_artifact=True,
            has_technical_artifact=True,
            has_sec_companyfacts_artifact=True,
            sec_companyfacts_usability="SUPPRESSED_INCOMPLETE",
            has_sec_catalyst_artifact=False,
            has_news_sentiment_artifact=True,
            news_sentiment_usability="SUPPRESSED_INCOMPLETE",
            has_target_weight=False,
            has_thesis_history=False,
        )
        assert bucket == BUCKET_SEC_EXISTS_WEAK

    def test_equity_sec_suppressed_contradicted_returns_exists_weak(self):
        bucket, _ = _classify_root_cause(
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            has_fundamentals_artifact=True,
            has_technical_artifact=True,
            has_sec_companyfacts_artifact=True,
            sec_companyfacts_usability="SUPPRESSED_CONTRADICTED",
            has_sec_catalyst_artifact=False,
            has_news_sentiment_artifact=False,
            news_sentiment_usability=None,
            has_target_weight=False,
            has_thesis_history=False,
        )
        assert bucket == BUCKET_SEC_EXISTS_WEAK

    def test_equity_sec_not_evaluable_returns_exists_weak(self):
        bucket, _ = _classify_root_cause(
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            has_fundamentals_artifact=True,
            has_technical_artifact=True,
            has_sec_companyfacts_artifact=True,
            sec_companyfacts_usability="NOT_EVALUABLE",
            has_sec_catalyst_artifact=False,
            has_news_sentiment_artifact=False,
            news_sentiment_usability=None,
            has_target_weight=False,
            has_thesis_history=False,
        )
        assert bucket == BUCKET_SEC_EXISTS_WEAK

    def test_equity_sec_no_usability_label_returns_exists_weak(self):
        """SEC artifact exists (artifact_id not None) but no usability label → weak."""
        bucket, _ = _classify_root_cause(
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            has_fundamentals_artifact=True,
            has_technical_artifact=True,
            has_sec_companyfacts_artifact=True,
            sec_companyfacts_usability=None,
            has_sec_catalyst_artifact=False,
            has_news_sentiment_artifact=False,
            news_sentiment_usability=None,
            has_target_weight=False,
            has_thesis_history=False,
        )
        assert bucket == BUCKET_SEC_EXISTS_WEAK

    def test_equity_sec_usable_returns_valuation_not_built(self):
        """SEC USABLE + no valuation lane → VALUATION_LANE_NOT_BUILT."""
        bucket, fix = _classify_root_cause(
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            has_fundamentals_artifact=True,
            has_technical_artifact=True,
            has_sec_companyfacts_artifact=True,
            sec_companyfacts_usability="USABLE",
            has_sec_catalyst_artifact=False,
            has_news_sentiment_artifact=True,
            news_sentiment_usability="SUPPRESSED_INCOMPLETE",
            has_target_weight=True,
            has_thesis_history=True,
            valuation_lane_exists=False,  # always False at Stage 9D
        )
        assert bucket == BUCKET_VALUATION_NOT_BUILT
        # Stage 9D: message now references canonical equity dataset as built
        assert "valuation" in fix.lower()

    def test_equity_sec_usable_with_limitations_also_passes_to_valuation(self):
        """USABLE_WITH_LIMITATIONS is considered usable — passes through to next priority."""
        bucket, _ = _classify_root_cause(
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            has_fundamentals_artifact=True,
            has_technical_artifact=True,
            has_sec_companyfacts_artifact=True,
            sec_companyfacts_usability="USABLE_WITH_LIMITATIONS",
            has_sec_catalyst_artifact=False,
            has_news_sentiment_artifact=False,
            news_sentiment_usability=None,
            has_target_weight=True,
            has_thesis_history=True,
            valuation_lane_exists=False,
        )
        assert bucket == BUCKET_VALUATION_NOT_BUILT

    def test_equity_news_suppressed_no_catalyst_when_valuation_exists(self):
        """When valuation lane exists, news suppression + no SEC catalyst → news suppressed."""
        bucket, fix = _classify_root_cause(
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            has_fundamentals_artifact=True,
            has_technical_artifact=True,
            has_sec_companyfacts_artifact=True,
            sec_companyfacts_usability="USABLE",
            has_sec_catalyst_artifact=False,
            has_news_sentiment_artifact=True,
            news_sentiment_usability="SUPPRESSED_INCOMPLETE",
            has_target_weight=True,
            has_thesis_history=True,
            valuation_lane_exists=True,
        )
        assert bucket == BUCKET_NEWS_SUPPRESSED
        assert "editorial context" in fix.lower() or "sentiment" in fix.lower()

    def test_equity_news_suppressed_but_has_catalyst_skips_news_bucket(self):
        """If SEC catalyst exists, news suppression is covered — move to next priority."""
        bucket, _ = _classify_root_cause(
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            has_fundamentals_artifact=True,
            has_technical_artifact=True,
            has_sec_companyfacts_artifact=True,
            sec_companyfacts_usability="USABLE",
            has_sec_catalyst_artifact=True,
            has_news_sentiment_artifact=True,
            news_sentiment_usability="SUPPRESSED_INCOMPLETE",
            has_target_weight=False,
            has_thesis_history=True,
            valuation_lane_exists=True,
        )
        assert bucket == BUCKET_TARGET_WEIGHT_NOT_BUILT

    def test_equity_target_weight_not_set_returns_target_weight_bucket(self):
        """SEC + valuation + news ok, but no target weight → TARGET_WEIGHT_NOT_BUILT."""
        bucket, fix = _classify_root_cause(
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            has_fundamentals_artifact=True,
            has_technical_artifact=True,
            has_sec_companyfacts_artifact=True,
            sec_companyfacts_usability="USABLE",
            has_sec_catalyst_artifact=True,
            has_news_sentiment_artifact=True,
            news_sentiment_usability="USABLE",
            has_target_weight=False,
            has_thesis_history=True,
            valuation_lane_exists=True,
        )
        assert bucket == BUCKET_TARGET_WEIGHT_NOT_BUILT
        assert "target" in fix.lower()

    def test_equity_no_thesis_history_returns_thesis_not_built(self):
        """All evidence present + target weight set, but no thesis → THESIS_HISTORY_NOT_BUILT."""
        bucket, fix = _classify_root_cause(
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            has_fundamentals_artifact=True,
            has_technical_artifact=True,
            has_sec_companyfacts_artifact=True,
            sec_companyfacts_usability="USABLE",
            has_sec_catalyst_artifact=True,
            has_news_sentiment_artifact=True,
            news_sentiment_usability="USABLE",
            has_target_weight=True,
            has_thesis_history=False,
            valuation_lane_exists=True,
        )
        assert bucket == BUCKET_THESIS_NOT_BUILT
        assert "recommendation" in fix.lower() or "Intel" in fix

    def test_equity_all_present_returns_data_needs_normalization(self):
        """Everything present and usable → DATA_PRESENT_NEEDS_CANONICAL_NORMALIZATION."""
        bucket, fix = _classify_root_cause(
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            has_fundamentals_artifact=True,
            has_technical_artifact=True,
            has_sec_companyfacts_artifact=True,
            sec_companyfacts_usability="USABLE",
            has_sec_catalyst_artifact=True,
            has_news_sentiment_artifact=True,
            news_sentiment_usability="USABLE",
            has_target_weight=True,
            has_thesis_history=True,
            valuation_lane_exists=True,
        )
        assert bucket == BUCKET_DATA_NEEDS_NORMALIZATION
        assert "normalize" in fix.lower() or "canonical" in fix.lower()


class TestAllBucketsReachable:
    """Verify all 11 defined buckets appear in the test matrix above."""

    def test_all_buckets_are_distinct_strings(self):
        assert len(ALL_BUCKETS) == 11

    def test_no_bucket_string_duplicates_in_all_buckets(self):
        bucket_list = [
            BUCKET_SEC_EXISTS_WEAK, BUCKET_SEC_MISSING_CIK, BUCKET_SEC_MISSING_WORKER,
            BUCKET_VALUATION_NOT_BUILT, BUCKET_ETF_NOT_BUILT, BUCKET_CRYPTO_NOT_BUILT,
            BUCKET_TARGET_WEIGHT_NOT_BUILT, BUCKET_THESIS_NOT_BUILT, BUCKET_NEWS_SUPPRESSED,
            BUCKET_DATA_NEEDS_NORMALIZATION, BUCKET_NOT_APPLICABLE,
        ]
        assert len(set(bucket_list)) == 11
        assert set(bucket_list) == ALL_BUCKETS


# ── Artifact exists vs weak distinction ───────────────────────────────────────


class TestArtifactExistsVsWeak:
    """Diagnostic correctly distinguishes 'artifact missing' from 'artifact exists but weak'."""

    def test_sec_missing_lane_gives_sec_missing_bucket(self):
        """No artifact in Stage 5J → SEC_MISSING bucket (not EXISTS_WEAK)."""
        row = _build_holding_row(
            ticker="CRM",
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            lanes={
                LANE_FUNDAMENTALS: _make_lane(LANE_FUNDAMENTALS, STATUS_READY, usability_label="USABLE"),
                LANE_TECHNICALS: _make_lane(LANE_TECHNICALS, STATUS_READY, usability_label="USABLE"),
                LANE_SEC_COMPANY_FACTS: _make_lane(LANE_SEC_COMPANY_FACTS, STATUS_MISSING, artifact_id=None),
            },
            supplemental=_empty_supplemental(),
        )
        assert row.sec_companyfacts_artifact_exists is False
        assert row.root_cause_bucket == BUCKET_SEC_MISSING_CIK

    def test_sec_artifact_suppressed_gives_exists_weak_bucket(self):
        """Artifact exists but suppressed → EXISTS_WEAK bucket."""
        row = _build_holding_row(
            ticker="CRM",
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            lanes={
                LANE_FUNDAMENTALS: _make_lane(LANE_FUNDAMENTALS, STATUS_READY, usability_label="USABLE"),
                LANE_SEC_COMPANY_FACTS: _make_lane(
                    LANE_SEC_COMPANY_FACTS, STATUS_SUPPRESSED,
                    artifact_id="art-sec-001",
                    usability_label="SUPPRESSED_INCOMPLETE",
                ),
            },
            supplemental=_empty_supplemental(),
        )
        assert row.sec_companyfacts_artifact_exists is True
        assert row.sec_companyfacts_status == "SUPPRESSED_INCOMPLETE"
        assert row.root_cause_bucket == BUCKET_SEC_EXISTS_WEAK

    def test_sec_artifact_usable_passes_to_valuation(self):
        """Artifact exists and USABLE → moves past SEC checks to VALUATION_NOT_BUILT."""
        row = _build_holding_row(
            ticker="NVDA",
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            lanes={
                LANE_SEC_COMPANY_FACTS: _make_lane(
                    LANE_SEC_COMPANY_FACTS, STATUS_READY,
                    artifact_id="art-sec-002",
                    usability_label="USABLE",
                    source_authority="PRIMARY_AUTHORITY",
                ),
            },
            supplemental=_empty_supplemental(
                target_tickers=frozenset({"NVDA"}),
                recommendation_tickers=frozenset({"NVDA"}),
            ),
        )
        assert row.sec_companyfacts_artifact_exists is True
        assert row.root_cause_bucket == BUCKET_VALUATION_NOT_BUILT

    def _empty_supplemental(
        self,
        *,
        has_target_weight: bool = False,
        has_thesis_history: bool = False,
    ) -> _SupplementalData:
        return _SupplementalData(
            target_tickers=frozenset({"NVDA"}) if has_target_weight else frozenset(),
            recommendation_tickers=frozenset({"NVDA"}) if has_thesis_history else frozenset(),
            fact_counts={},
            has_portfolio_snapshot=True,
            sec_fact_records={},
        )


# ── ETF classification tests ──────────────────────────────────────────────────


class TestETFClassification:
    """ETF holdings always get ETF_PROVIDER_NOT_BUILT regardless of available artifacts."""

    def test_etf_holding_gets_etf_provider_bucket(self):
        row = _build_holding_row(
            ticker="VTI",
            asset_type=INSTRUMENT_CATEGORY_ETF,
            lanes={
                LANE_TECHNICALS: _make_lane(LANE_TECHNICALS, STATUS_READY, usability_label="USABLE"),
            },
            supplemental=_empty_supplemental(target_tickers=frozenset({"VTI"})),
        )
        assert row.asset_type == INSTRUMENT_CATEGORY_ETF
        assert row.root_cause_bucket == BUCKET_ETF_NOT_BUILT
        assert row.etf_fund_composition_artifact_exists is False
        assert row.sec_companyfacts_artifact_exists is False

    def test_schd_etf_gets_etf_provider_bucket(self):
        row = _build_holding_row(
            ticker="SCHD",
            asset_type=INSTRUMENT_CATEGORY_ETF,
            lanes={},
            supplemental=_empty_supplemental(),
        )
        assert row.root_cause_bucket == BUCKET_ETF_NOT_BUILT

    def test_etf_fund_composition_artifact_always_false(self):
        """No ETF fund data provider exists — etf_fund_composition_artifact_exists is always False."""
        for ticker in ("VTI", "SCHD", "QQQ", "SPY"):
            row = _build_holding_row(
                ticker=ticker,
                asset_type=INSTRUMENT_CATEGORY_ETF,
                lanes={},
                supplemental=_empty_supplemental(),
            )
            assert row.etf_fund_composition_artifact_exists is False

    def test_etf_next_required_fix_mentions_provider(self):
        row = _build_holding_row(
            ticker="VTI",
            asset_type=INSTRUMENT_CATEGORY_ETF,
            lanes={},
            supplemental=_empty_supplemental(),
        )
        assert "provider" in row.next_required_fix.lower() or "fund-data" in row.next_required_fix.lower()


# ── Valuation lane tests ──────────────────────────────────────────────────────


class TestValuationLaneNotBuilt:
    """Diagnostic proves valuation is missing because no lane exists, not a UI issue."""

    def test_valuation_lane_exists_always_false(self):
        """valuation_lane_exists is always False at Stage 9B — no lane in Stage 5J/5K."""
        row = _build_holding_row(
            ticker="CRM",
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            lanes={
                LANE_SEC_COMPANY_FACTS: _make_lane(
                    LANE_SEC_COMPANY_FACTS, STATUS_READY,
                    artifact_id="art-001", usability_label="USABLE",
                ),
            },
            supplemental=_empty_supplemental(),
        )
        assert row.valuation_lane_exists is False

    def test_equity_with_usable_sec_gets_valuation_not_built_bucket(self):
        """Equity with usable SEC data → primary gap is valuation lane not built."""
        row = _build_holding_row(
            ticker="NVDA",
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            lanes={
                LANE_SEC_COMPANY_FACTS: _make_lane(
                    LANE_SEC_COMPANY_FACTS, STATUS_READY,
                    artifact_id="art-002", usability_label="USABLE",
                ),
                LANE_FUNDAMENTALS: _make_lane(LANE_FUNDAMENTALS, STATUS_READY, usability_label="USABLE"),
            },
            supplemental=_empty_supplemental(),
        )
        assert row.root_cause_bucket == BUCKET_VALUATION_NOT_BUILT

    def test_valuation_summary_explains_no_lane_for_equity(self):
        row = _build_holding_row(
            ticker="CRM",
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            lanes={},
            supplemental=_empty_supplemental(),
        )
        summary = row.valuation_inputs_available_summary
        assert "valuation" in summary.lower()

    def test_valuation_not_applicable_for_etf(self):
        row = _build_holding_row(
            ticker="VTI",
            asset_type=INSTRUMENT_CATEGORY_ETF,
            lanes={},
            supplemental=_empty_supplemental(),
        )
        assert "not applicable" in row.valuation_inputs_available_summary.lower()

    def test_valuation_not_applicable_for_crypto(self):
        row = _build_holding_row(
            ticker="BTC",
            asset_type=INSTRUMENT_CATEGORY_CRYPTO,
            lanes={},
            supplemental=_empty_supplemental(),
        )
        assert "not applicable" in row.valuation_inputs_available_summary.lower()


# ── Crypto classification tests ───────────────────────────────────────────────


class TestCryptoClassification:
    """Crypto holdings always get CRYPTO_PROVIDER_NOT_BUILT."""

    def test_btc_gets_crypto_provider_bucket(self):
        row = _build_holding_row(
            ticker="BTC",
            asset_type=INSTRUMENT_CATEGORY_CRYPTO,
            lanes={
                LANE_TECHNICALS: _make_lane(LANE_TECHNICALS, STATUS_READY, usability_label="USABLE"),
            },
            supplemental=_empty_supplemental(target_tickers=frozenset({"BTC"})),
        )
        assert row.asset_type == INSTRUMENT_CATEGORY_CRYPTO
        assert row.root_cause_bucket == BUCKET_CRYPTO_NOT_BUILT

    def test_xrp_gets_crypto_provider_bucket(self):
        row = _build_holding_row(
            ticker="XRP",
            asset_type=INSTRUMENT_CATEGORY_CRYPTO,
            lanes={},
            supplemental=_empty_supplemental(),
        )
        assert row.root_cause_bucket == BUCKET_CRYPTO_NOT_BUILT

    def test_crypto_market_context_uses_technical_proxy(self):
        """Crypto market context is the technical artifact as proxy."""
        row = _build_holding_row(
            ticker="BTC",
            asset_type=INSTRUMENT_CATEGORY_CRYPTO,
            lanes={
                LANE_TECHNICALS: _make_lane(LANE_TECHNICALS, STATUS_READY, usability_label="USABLE"),
            },
            supplemental=_empty_supplemental(),
        )
        assert row.crypto_market_context_artifact_exists is True

    def test_crypto_no_technicals_gives_false_context(self):
        row = _build_holding_row(
            ticker="BTC",
            asset_type=INSTRUMENT_CATEGORY_CRYPTO,
            lanes={},
            supplemental=_empty_supplemental(),
        )
        assert row.crypto_market_context_artifact_exists is False

    def test_crypto_next_fix_mentions_no_equity_fundamentals(self):
        row = _build_holding_row(
            ticker="BTC",
            asset_type=INSTRUMENT_CATEGORY_CRYPTO,
            lanes={},
            supplemental=_empty_supplemental(),
        )
        assert "NOT_APPLICABLE" in row.next_required_fix or "not applicable" in row.next_required_fix.lower()


# ── No decision policy import test ───────────────────────────────────────────


class TestNoPolicyImport:
    """The forensics module must never import or call decision_policy_v1."""

    def test_decision_policy_not_imported_in_forensics_module(self):
        import app.services.intelligence.v3.intel_data_foundation_forensics_v1 as mod
        source_file = mod.__file__
        with open(source_file) as f:
            content = f.read()
        assert "decision_policy_v1" not in content, (
            "forensics module must not import decision_policy_v1"
        )

    def test_decide_not_called_in_forensics_module(self):
        import app.services.intelligence.v3.intel_data_foundation_forensics_v1 as mod
        source_file = mod.__file__
        with open(source_file) as f:
            content = f.read()
        assert "decide(" not in content, (
            "forensics module must not call decide()"
        )

    def test_classify_root_cause_is_pure_deterministic(self):
        """Same inputs always produce same output — no side effects."""
        kwargs = dict(
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            has_fundamentals_artifact=True,
            has_technical_artifact=True,
            has_sec_companyfacts_artifact=False,
            sec_companyfacts_usability=None,
            has_sec_catalyst_artifact=False,
            has_news_sentiment_artifact=True,
            news_sentiment_usability="SUPPRESSED_INCOMPLETE",
            has_target_weight=False,
            has_thesis_history=False,
        )
        result1 = _classify_root_cause(**kwargs)
        result2 = _classify_root_cause(**kwargs)
        assert result1 == result2


# ── Leak guard tests ──────────────────────────────────────────────────────────


class TestLeakGuard:
    """No raw payloads, secrets, URLs, or provider dumps in output."""

    _FORBIDDEN_PATTERNS = [
        "payload", "api_key", "secret", "password", "token",
        "https://", "http://", ".com/api", "EDGAR_URL",
        "fact_content", "raw_fact", "source_quote",
    ]

    def test_holding_row_dict_contains_no_forbidden_patterns(self):
        row = _build_holding_row(
            ticker="CRM",
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            lanes={
                LANE_SEC_COMPANY_FACTS: _make_lane(
                    LANE_SEC_COMPANY_FACTS, STATUS_READY,
                    artifact_id="art-sec-001",
                    usability_label="USABLE",
                    source_authority="PRIMARY_AUTHORITY",
                ),
            },
            supplemental=_empty_supplemental(),
        )
        output_str = str(row.to_dict())
        for pattern in self._FORBIDDEN_PATTERNS:
            assert pattern not in output_str.lower(), (
                f"Forbidden pattern '{pattern}' found in holding row output"
            )

    def test_result_to_dict_contains_no_forbidden_patterns(self):
        """Full result dict exposes no secrets or raw provider content."""
        row = _build_holding_row(
            ticker="NVDA",
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            lanes={},
            supplemental=_empty_supplemental(),
        )
        result = DataFoundationForensicsResult(
            schema_version=FORENSICS_VERSION,
            user_id="user-test",
            generated_at="2026-05-24T00:00:00+00:00",
            holdings=[row],
        )
        output_str = str(result.to_dict())
        for pattern in self._FORBIDDEN_PATTERNS:
            assert pattern not in output_str.lower(), (
                f"Forbidden pattern '{pattern}' found in forensics result output"
            )

    def test_sec_reason_not_strong_contains_no_raw_data(self):
        lane = _make_lane(LANE_SEC_COMPANY_FACTS, STATUS_SUPPRESSED, usability_label="SUPPRESSED_INCOMPLETE")
        reason = _get_sec_reason_not_strong(lane, INSTRUMENT_CATEGORY_EQUITY)
        assert reason is not None
        for pattern in self._FORBIDDEN_PATTERNS:
            assert pattern not in (reason or "").lower()


# ── Contract test: response shape ─────────────────────────────────────────────


class TestResponseShape:
    """All required fields present in the output; safety fields always False."""

    _REQUIRED_HOLDING_FIELDS = {
        "ticker", "asset_type",
        "current_position_available", "current_weight_available", "target_weight_available",
        "yfinance_fundamentals_artifact_exists", "yfinance_fundamentals_status",
        "technical_artifact_exists", "technical_status",
        "news_sentiment_artifact_exists", "news_sentiment_status",
        "sec_companyfacts_artifact_exists", "sec_companyfacts_status",
        "sec_companyfacts_observation_count", "sec_companyfacts_reason_not_strong",
        "sec_catalyst_artifact_exists", "sec_catalyst_status", "sec_catalyst_count",
        "valuation_lane_exists", "valuation_inputs_available_summary",
        "etf_fund_composition_artifact_exists", "crypto_market_context_artifact_exists",
        "thesis_history_exists", "root_cause_bucket", "next_required_fix",
        # multi-gap fields
        "blocking_gap_buckets", "blocking_gap_count", "next_required_fixes",
    }

    _REQUIRED_RESULT_FIELDS = {
        "schema_version", "user_id", "generated_at",
        "safe_for_decision", "synthesis_ready", "holdings",
        "holdings_by_asset_type",
        "artifacts_existing_by_lane", "artifacts_usable_by_lane", "artifacts_strong_by_lane",
        "root_cause_bucket_counts", "blocking_gap_bucket_counts",
        "provider_limited_count", "implementation_limited_count", "normalization_limited_count",
        "errors",
    }

    def test_holding_row_dict_has_all_required_fields(self):
        row = _build_holding_row(
            ticker="TEST",
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            lanes={},
            supplemental=_empty_supplemental(),
        )
        d = row.to_dict()
        missing = self._REQUIRED_HOLDING_FIELDS - set(d.keys())
        assert not missing, f"Missing fields in HoldingForensicsRow.to_dict(): {missing}"

    def test_result_dict_has_all_required_fields(self):
        row = _build_holding_row(
            ticker="TEST",
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            lanes={},
            supplemental=_empty_supplemental(),
        )
        result = DataFoundationForensicsResult(
            schema_version=FORENSICS_VERSION,
            user_id="user-test",
            generated_at="2026-05-24T00:00:00+00:00",
            holdings=[row],
        )
        d = result.to_dict()
        missing = self._REQUIRED_RESULT_FIELDS - set(d.keys())
        assert not missing, f"Missing fields in DataFoundationForensicsResult.to_dict(): {missing}"

    def test_safe_for_decision_always_false(self):
        result = DataFoundationForensicsResult(
            schema_version=FORENSICS_VERSION,
            user_id="user-test",
            generated_at="2026-05-24T00:00:00+00:00",
        )
        d = result.to_dict()
        assert d["safe_for_decision"] is False

    def test_synthesis_ready_always_false(self):
        result = DataFoundationForensicsResult(
            schema_version=FORENSICS_VERSION,
            user_id="user-test",
            generated_at="2026-05-24T00:00:00+00:00",
        )
        d = result.to_dict()
        assert d["synthesis_ready"] is False

    def test_root_cause_bucket_in_all_buckets(self):
        for asset_type in (INSTRUMENT_CATEGORY_EQUITY, INSTRUMENT_CATEGORY_ETF,
                           INSTRUMENT_CATEGORY_CRYPTO, INSTRUMENT_CATEGORY_UNKNOWN):
            row = _build_holding_row(
                ticker="T",
                asset_type=asset_type,
                lanes={},
                supplemental=_empty_supplemental(),
            )
            assert row.root_cause_bucket in ALL_BUCKETS, (
                f"root_cause_bucket '{row.root_cause_bucket}' not in ALL_BUCKETS"
            )

    def test_valuation_lane_exists_always_false_in_output(self):
        for asset_type in (INSTRUMENT_CATEGORY_EQUITY, INSTRUMENT_CATEGORY_ETF,
                           INSTRUMENT_CATEGORY_CRYPTO):
            row = _build_holding_row(
                ticker="T",
                asset_type=asset_type,
                lanes={},
                supplemental=_empty_supplemental(),
            )
            assert row.valuation_lane_exists is False

    def test_etf_fund_composition_always_false_in_output(self):
        for asset_type in (INSTRUMENT_CATEGORY_EQUITY, INSTRUMENT_CATEGORY_ETF,
                           INSTRUMENT_CATEGORY_CRYPTO):
            row = _build_holding_row(
                ticker="T",
                asset_type=asset_type,
                lanes={},
                supplemental=_empty_supplemental(),
            )
            assert row.etf_fund_composition_artifact_exists is False


# ── Portfolio aggregate tests ─────────────────────────────────────────────────


class TestPortfolioAggregates:
    """Portfolio-level aggregate counts are correctly computed."""

    def _build_multi_holding_result(self) -> DataFoundationForensicsResult:
        """Simulate a mixed portfolio: 1 equity, 1 ETF, 1 crypto."""
        crm_row = _build_holding_row(
            ticker="CRM",
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            lanes={
                LANE_FUNDAMENTALS: _make_lane(LANE_FUNDAMENTALS, STATUS_READY, usability_label="USABLE"),
                LANE_TECHNICALS: _make_lane(LANE_TECHNICALS, STATUS_READY, usability_label="USABLE"),
            },
            supplemental=_empty_supplemental(),
        )
        vti_row = _build_holding_row(
            ticker="VTI",
            asset_type=INSTRUMENT_CATEGORY_ETF,
            lanes={
                LANE_TECHNICALS: _make_lane(LANE_TECHNICALS, STATUS_READY, usability_label="USABLE"),
            },
            supplemental=_empty_supplemental(),
        )
        btc_row = _build_holding_row(
            ticker="BTC",
            asset_type=INSTRUMENT_CATEGORY_CRYPTO,
            lanes={},
            supplemental=_empty_supplemental(),
        )
        return DataFoundationForensicsResult(
            schema_version=FORENSICS_VERSION,
            user_id="user-test",
            generated_at="2026-05-24T00:00:00+00:00",
            holdings=[crm_row, vti_row, btc_row],
        )

    def test_holdings_by_asset_type_from_result(self):
        result = self._build_multi_holding_result()
        d = result.to_dict()
        # Holdings are there; aggregates need the _build_aggregates path
        assert len(d["holdings"]) == 3
        # Check asset_type field on each holding
        asset_types = [h["asset_type"] for h in d["holdings"]]
        assert INSTRUMENT_CATEGORY_EQUITY in asset_types
        assert INSTRUMENT_CATEGORY_ETF in asset_types
        assert INSTRUMENT_CATEGORY_CRYPTO in asset_types

    def test_provider_limited_bucket_constants(self):
        from app.services.intelligence.v3.intel_data_foundation_forensics_v1 import (
            _PROVIDER_LIMITED_BUCKETS,
            _IMPLEMENTATION_LIMITED_BUCKETS,
            _NORMALIZATION_LIMITED_BUCKETS,
        )
        assert BUCKET_ETF_NOT_BUILT in _PROVIDER_LIMITED_BUCKETS
        assert BUCKET_CRYPTO_NOT_BUILT in _PROVIDER_LIMITED_BUCKETS
        assert BUCKET_SEC_MISSING_WORKER in _IMPLEMENTATION_LIMITED_BUCKETS
        assert BUCKET_SEC_MISSING_CIK in _IMPLEMENTATION_LIMITED_BUCKETS
        assert BUCKET_VALUATION_NOT_BUILT in _IMPLEMENTATION_LIMITED_BUCKETS
        assert BUCKET_DATA_NEEDS_NORMALIZATION in _NORMALIZATION_LIMITED_BUCKETS

    def test_bucket_sets_are_disjoint(self):
        from app.services.intelligence.v3.intel_data_foundation_forensics_v1 import (
            _PROVIDER_LIMITED_BUCKETS,
            _IMPLEMENTATION_LIMITED_BUCKETS,
            _NORMALIZATION_LIMITED_BUCKETS,
        )
        assert not (_PROVIDER_LIMITED_BUCKETS & _IMPLEMENTATION_LIMITED_BUCKETS)
        assert not (_PROVIDER_LIMITED_BUCKETS & _NORMALIZATION_LIMITED_BUCKETS)
        assert not (_IMPLEMENTATION_LIMITED_BUCKETS & _NORMALIZATION_LIMITED_BUCKETS)


# ── Fail-soft and DB error tests ──────────────────────────────────────────────


class TestFailSoft:
    """DB errors are captured in errors[], never raised."""

    def test_empty_tickers_returns_result_with_error(self):
        db = MagicMock()
        result = compute_data_foundation_forensics(
            user_id="user-1",
            tickers=[],
            holding_context_by_ticker={},
            db_client=db,
        )
        assert result.safe_for_decision is False
        assert result.synthesis_ready is False
        assert len(result.errors) >= 1
        assert len(result.holdings) == 0

    def test_coverage_compute_called_with_user_id(self):
        """compute_research_evidence_coverage is called with user_id and tickers."""
        with patch(
            "app.services.intelligence.v3.intel_data_foundation_forensics_v1"
            ".compute_research_evidence_coverage"
        ) as mock_cov:
            # Return a minimal coverage with one ticker
            mock_cov.return_value = ResearchEvidenceCoverageSummary(
                schema_version="v1",
                user_id="user-1",
                generated_at="2026-05-24T00:00:00+00:00",
                portfolio_ticker_count=1,
                ticker_coverage={
                    "NVDA": TickerCoverage(ticker="NVDA", lanes={}),
                },
                portfolio_macro_coverage=_make_lane(LANE_TECHNICALS, STATUS_MISSING),
                lane_counts={},
                usability_counts={},
                missing_lane_counts={},
                suppressed_counts={},
                stale_or_unknown_counts={},
                ready_artifact_count=0,
                errors=[],
            )
            db = MagicMock()
            db.table.return_value.select.return_value.eq.return_value.execute.return_value.data = []
            db.table.return_value.select.return_value.in_.return_value.limit.return_value.execute.return_value.data = []
            db.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = []

            result = compute_data_foundation_forensics(
                user_id="user-1",
                tickers=["NVDA"],
                holding_context_by_ticker={"NVDA": {"category": "Core"}},
                db_client=db,
            )
            mock_cov.assert_called_once()
            call_kwargs = mock_cov.call_args.kwargs
            assert call_kwargs["user_id"] == "user-1"
            assert "NVDA" in call_kwargs["tickers"]
            assert result.safe_for_decision is False

    def test_target_allocations_query_failure_captured_in_errors(self):
        """If target_allocations query fails, error is captured and result is still returned."""
        with patch(
            "app.services.intelligence.v3.intel_data_foundation_forensics_v1"
            ".compute_research_evidence_coverage"
        ) as mock_cov:
            mock_cov.return_value = ResearchEvidenceCoverageSummary(
                schema_version="v1",
                user_id="user-1",
                generated_at="2026-05-24T00:00:00+00:00",
                portfolio_ticker_count=1,
                ticker_coverage={
                    "CRM": TickerCoverage(ticker="CRM", lanes={}),
                },
                portfolio_macro_coverage=_make_lane(LANE_TECHNICALS, STATUS_MISSING),
                lane_counts={},
                usability_counts={},
                missing_lane_counts={},
                suppressed_counts={},
                stale_or_unknown_counts={},
                ready_artifact_count=0,
                errors=[],
            )

            db = MagicMock()
            # Make target_allocations query raise
            db.table.return_value.select.return_value.eq.return_value.execute.side_effect = Exception("DB error")
            db.table.return_value.select.return_value.in_.return_value.limit.return_value.execute.return_value.data = []
            db.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = []

            result = compute_data_foundation_forensics(
                user_id="user-1",
                tickers=["CRM"],
                holding_context_by_ticker={"CRM": {"category": "Core"}},
                db_client=db,
            )
            assert result.safe_for_decision is False
            assert result.synthesis_ready is False
            assert len(result.holdings) == 1  # still returns holding


# ── Observation count tests ───────────────────────────────────────────────────


class TestObservationCounts:
    """Safe fact counts are included when available."""

    def test_sec_observation_count_from_fact_counts(self):
        artifact_id = "art-sec-test"
        supplemental = _SupplementalData(
            target_tickers=frozenset(),
            recommendation_tickers=frozenset(),
            fact_counts={artifact_id: 42},
            has_portfolio_snapshot=True,
            sec_fact_records={},
        )
        row = _build_holding_row(
            ticker="CRM",
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            lanes={
                LANE_SEC_COMPANY_FACTS: _make_lane(
                    LANE_SEC_COMPANY_FACTS, STATUS_SUPPRESSED,
                    artifact_id=artifact_id,
                    usability_label="SUPPRESSED_INCOMPLETE",
                ),
            },
            supplemental=supplemental,
        )
        assert row.sec_companyfacts_artifact_exists is True
        assert row.sec_companyfacts_observation_count == 42

    def test_sec_observation_count_none_when_artifact_missing(self):
        row = _build_holding_row(
            ticker="CRM",
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            lanes={
                LANE_SEC_COMPANY_FACTS: _make_lane(LANE_SEC_COMPANY_FACTS, STATUS_MISSING, artifact_id=None),
            },
            supplemental=_empty_supplemental(),
        )
        assert row.sec_companyfacts_artifact_exists is False
        assert row.sec_companyfacts_observation_count is None

    def test_catalyst_count_from_fact_counts(self):
        artifact_id = "art-cat-test"
        supplemental = _SupplementalData(
            target_tickers=frozenset(),
            recommendation_tickers=frozenset(),
            fact_counts={artifact_id: 5},
            has_portfolio_snapshot=True,
            sec_fact_records={},
        )
        row = _build_holding_row(
            ticker="CRM",
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            lanes={
                LANE_SEC_CATALYST_SENTIMENT: _make_lane(
                    LANE_SEC_CATALYST_SENTIMENT, STATUS_LIMITED,
                    artifact_id=artifact_id,
                    usability_label="USABLE_WITH_LIMITATIONS",
                ),
            },
            supplemental=supplemental,
        )
        assert row.sec_catalyst_artifact_exists is True
        assert row.sec_catalyst_count == 5


# ── Example fixture summary ───────────────────────────────────────────────────


class TestExampleFixtureOutputs:
    """
    Example diagnostic output patterns for CRM, NVDA (equity), VTI/SCHD (ETF), BTC/XRP (crypto).
    These tests document expected output shapes for the PR body validation examples.
    """

    def test_crm_equity_no_sec_artifact_gets_cik_bucket(self):
        """CRM with fundamentals+technicals but no SEC → CIK mapping gap."""
        row = _build_holding_row(
            ticker="CRM",
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            lanes={
                LANE_FUNDAMENTALS: _make_lane(LANE_FUNDAMENTALS, STATUS_READY, usability_label="USABLE"),
                LANE_TECHNICALS: _make_lane(LANE_TECHNICALS, STATUS_READY, usability_label="USABLE"),
                LANE_NEWS_SENTIMENT: _make_lane(LANE_NEWS_SENTIMENT, STATUS_SUPPRESSED,
                                                usability_label="SUPPRESSED_INCOMPLETE"),
            },
            supplemental=_empty_supplemental(),
        )
        assert row.ticker == "CRM"
        assert row.asset_type == INSTRUMENT_CATEGORY_EQUITY
        assert row.yfinance_fundamentals_artifact_exists is True
        assert row.sec_companyfacts_artifact_exists is False
        assert row.root_cause_bucket == BUCKET_SEC_MISSING_CIK
        assert row.valuation_lane_exists is False
        assert row.etf_fund_composition_artifact_exists is False

    def test_nvda_equity_sec_suppressed_gets_exists_weak(self):
        """NVDA with suppressed SEC artifact → exists-but-weak."""
        row = _build_holding_row(
            ticker="NVDA",
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            lanes={
                LANE_FUNDAMENTALS: _make_lane(LANE_FUNDAMENTALS, STATUS_READY, usability_label="USABLE"),
                LANE_TECHNICALS: _make_lane(LANE_TECHNICALS, STATUS_READY, usability_label="USABLE"),
                LANE_SEC_COMPANY_FACTS: _make_lane(
                    LANE_SEC_COMPANY_FACTS, STATUS_SUPPRESSED,
                    artifact_id="art-nvda",
                    usability_label="SUPPRESSED_INCOMPLETE",
                ),
            },
            supplemental=_empty_supplemental(),
        )
        assert row.ticker == "NVDA"
        assert row.sec_companyfacts_artifact_exists is True
        assert row.sec_companyfacts_status == "SUPPRESSED_INCOMPLETE"
        assert row.root_cause_bucket == BUCKET_SEC_EXISTS_WEAK
        assert row.sec_companyfacts_reason_not_strong is not None

    def test_vti_etf_gets_etf_provider_bucket(self):
        """VTI ETF → ETF_PROVIDER_NOT_BUILT, etf_fund_composition_artifact_exists=False."""
        row = _build_holding_row(
            ticker="VTI",
            asset_type=INSTRUMENT_CATEGORY_ETF,
            lanes={
                LANE_TECHNICALS: _make_lane(LANE_TECHNICALS, STATUS_READY, usability_label="USABLE"),
            },
            supplemental=_empty_supplemental(target_tickers=frozenset({"VTI"})),
        )
        assert row.ticker == "VTI"
        assert row.asset_type == INSTRUMENT_CATEGORY_ETF
        assert row.root_cause_bucket == BUCKET_ETF_NOT_BUILT
        assert row.etf_fund_composition_artifact_exists is False
        assert row.sec_companyfacts_artifact_exists is False

    def test_schd_etf_gets_etf_provider_bucket(self):
        row = _build_holding_row(
            ticker="SCHD",
            asset_type=INSTRUMENT_CATEGORY_ETF,
            lanes={},
            supplemental=_empty_supplemental(),
        )
        assert row.root_cause_bucket == BUCKET_ETF_NOT_BUILT

    def test_btc_crypto_gets_crypto_provider_bucket(self):
        """BTC crypto → CRYPTO_PROVIDER_NOT_BUILT, crypto context uses technical proxy."""
        row = _build_holding_row(
            ticker="BTC",
            asset_type=INSTRUMENT_CATEGORY_CRYPTO,
            lanes={
                LANE_TECHNICALS: _make_lane(LANE_TECHNICALS, STATUS_READY, usability_label="USABLE"),
            },
            supplemental=_empty_supplemental(target_tickers=frozenset({"BTC"})),
        )
        assert row.ticker == "BTC"
        assert row.asset_type == INSTRUMENT_CATEGORY_CRYPTO
        assert row.root_cause_bucket == BUCKET_CRYPTO_NOT_BUILT
        assert row.crypto_market_context_artifact_exists is True
        assert row.yfinance_fundamentals_artifact_exists is False

    def test_xrp_crypto_gets_crypto_provider_bucket(self):
        row = _build_holding_row(
            ticker="XRP",
            asset_type=INSTRUMENT_CATEGORY_CRYPTO,
            lanes={},
            supplemental=_empty_supplemental(),
        )
        assert row.root_cause_bucket == BUCKET_CRYPTO_NOT_BUILT
        assert row.crypto_market_context_artifact_exists is False


# ── _artifact_exists helper ───────────────────────────────────────────────────


class TestArtifactExistsHelper:
    def test_none_lane_gives_false(self):
        assert _artifact_exists(None) is False

    def test_missing_status_gives_false(self):
        lane = _make_lane(LANE_FUNDAMENTALS, STATUS_MISSING, artifact_id=None)
        assert _artifact_exists(lane) is False

    def test_ready_with_artifact_id_gives_true(self):
        lane = _make_lane(LANE_FUNDAMENTALS, STATUS_READY, artifact_id="art-001")
        assert _artifact_exists(lane) is True

    def test_suppressed_with_artifact_id_gives_true(self):
        lane = _make_lane(LANE_SEC_COMPANY_FACTS, STATUS_SUPPRESSED, artifact_id="art-002")
        assert _artifact_exists(lane) is True


# ── _get_sec_reason_not_strong ────────────────────────────────────────────────


class TestSecReasonNotStrong:
    def test_usable_with_primary_authority_gives_none(self):
        lane = _make_lane(LANE_SEC_COMPANY_FACTS, STATUS_READY,
                          usability_label="USABLE", source_authority="PRIMARY_AUTHORITY")
        reason = _get_sec_reason_not_strong(lane, INSTRUMENT_CATEGORY_EQUITY)
        assert reason is None

    def test_suppressed_incomplete_gives_reason(self):
        lane = _make_lane(LANE_SEC_COMPANY_FACTS, STATUS_SUPPRESSED,
                          usability_label="SUPPRESSED_INCOMPLETE")
        reason = _get_sec_reason_not_strong(lane, INSTRUMENT_CATEGORY_EQUITY)
        assert reason is not None
        assert "THIN" in reason or "SUPPRESSED" in reason or "completeness" in reason.lower()

    def test_suppressed_contradicted_gives_reason(self):
        lane = _make_lane(LANE_SEC_COMPANY_FACTS, STATUS_SUPPRESSED,
                          usability_label="SUPPRESSED_CONTRADICTED")
        reason = _get_sec_reason_not_strong(lane, INSTRUMENT_CATEGORY_EQUITY)
        assert reason is not None
        assert "contradiction" in reason.lower() or "XBRL" in reason

    def test_not_evaluable_gives_reason(self):
        lane = _make_lane(LANE_SEC_COMPANY_FACTS, STATUS_NOT_EVALUABLE,
                          usability_label="NOT_EVALUABLE")
        reason = _get_sec_reason_not_strong(lane, INSTRUMENT_CATEGORY_EQUITY)
        assert reason is not None
        assert "evaluable" in reason.lower() or "NOT_EVALUABLE" in reason

    def test_missing_artifact_gives_reason(self):
        lane = _make_lane(LANE_SEC_COMPANY_FACTS, STATUS_MISSING, artifact_id=None)
        reason = _get_sec_reason_not_strong(lane, INSTRUMENT_CATEGORY_EQUITY)
        assert reason is not None
        assert "No artifact" in reason or "missing" in reason.lower()

    def test_etf_gets_not_applicable_reason(self):
        lane = _make_lane(LANE_SEC_COMPANY_FACTS, STATUS_READY)
        reason = _get_sec_reason_not_strong(lane, INSTRUMENT_CATEGORY_ETF)
        assert reason is not None
        assert "not applicable" in reason.lower()

    def test_crypto_gets_not_applicable_reason(self):
        lane = _make_lane(LANE_SEC_COMPANY_FACTS, STATUS_MISSING, artifact_id=None)
        reason = _get_sec_reason_not_strong(lane, INSTRUMENT_CATEGORY_CRYPTO)
        assert reason is not None
        assert "not applicable" in reason.lower()


# ── Schema version integrity ──────────────────────────────────────────────────


class TestSchemaVersion:
    def test_forensics_version_string_format(self):
        assert FORENSICS_VERSION == "intel_data_foundation_forensics.v1"

    def test_result_schema_version_matches_constant(self):
        result = DataFoundationForensicsResult(
            schema_version=FORENSICS_VERSION,
            user_id="u1",
            generated_at="2026-05-24T00:00:00+00:00",
        )
        assert result.to_dict()["schema_version"] == FORENSICS_VERSION


# ── helper used across tests ──────────────────────────────────────────────────


def _empty_supplemental(
    *,
    target_tickers: frozenset = frozenset(),
    recommendation_tickers: frozenset = frozenset(),
    fact_counts: Optional[dict] = None,
    has_portfolio_snapshot: bool = True,
) -> _SupplementalData:
    return _SupplementalData(
        target_tickers=target_tickers,
        recommendation_tickers=recommendation_tickers,
        fact_counts=fact_counts or {},
        has_portfolio_snapshot=has_portfolio_snapshot,
        sec_fact_records={},
    )


# ── Multi-gap tests ────────────────────────────────────────────────────────────


class TestMultiGapEquity:
    """Equity holdings expose all applicable blocking gaps simultaneously."""

    def test_equity_usable_sec_no_valuation_no_target_no_thesis_returns_three_gaps(self):
        """Primary test: usable SEC → [VALUATION_NOT_BUILT, TARGET_WEIGHT, THESIS], primary first."""
        row = _build_holding_row(
            ticker="MSFT",
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            lanes={
                LANE_SEC_COMPANY_FACTS: _make_lane(
                    LANE_SEC_COMPANY_FACTS, STATUS_READY,
                    artifact_id="art-sec-001",
                    usability_label="USABLE",
                    source_authority="PRIMARY_AUTHORITY",
                ),
            },
            supplemental=_empty_supplemental(),
        )
        assert row.blocking_gap_buckets == [
            BUCKET_VALUATION_NOT_BUILT,
            BUCKET_TARGET_WEIGHT_NOT_BUILT,
            BUCKET_THESIS_NOT_BUILT,
        ]
        assert row.root_cause_bucket == BUCKET_VALUATION_NOT_BUILT
        assert row.blocking_gap_count == 3
        assert len(row.next_required_fixes) == 3

    def test_root_cause_bucket_is_always_first_blocking_gap(self):
        row = _build_holding_row(
            ticker="AAPL",
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            lanes={
                LANE_SEC_COMPANY_FACTS: _make_lane(
                    LANE_SEC_COMPANY_FACTS, STATUS_READY,
                    artifact_id="art-1",
                    usability_label="USABLE",
                    source_authority="PRIMARY_AUTHORITY",
                ),
            },
            supplemental=_empty_supplemental(),
        )
        assert row.root_cause_bucket == row.blocking_gap_buckets[0]
        assert row.next_required_fix == row.next_required_fixes[0]

    def test_blocking_gap_count_matches_list_length(self):
        row = _build_holding_row(
            ticker="GOOG",
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            lanes={},
            supplemental=_empty_supplemental(),
        )
        assert row.blocking_gap_count == len(row.blocking_gap_buckets)
        assert row.blocking_gap_count == len(row.next_required_fixes)

    def test_equity_no_sec_artifact_still_exposes_valuation_target_thesis_gaps(self):
        """Even with SEC missing, valuation + target-weight + thesis gaps are exposed."""
        row = _build_holding_row(
            ticker="CRM",
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            lanes={
                LANE_FUNDAMENTALS: _make_lane(LANE_FUNDAMENTALS, STATUS_READY, usability_label="USABLE"),
            },
            supplemental=_empty_supplemental(),
        )
        assert row.blocking_gap_buckets[0] == BUCKET_SEC_MISSING_CIK
        assert BUCKET_VALUATION_NOT_BUILT in row.blocking_gap_buckets
        assert BUCKET_TARGET_WEIGHT_NOT_BUILT in row.blocking_gap_buckets
        assert BUCKET_THESIS_NOT_BUILT in row.blocking_gap_buckets
        assert row.blocking_gap_count >= 3

    def test_equity_sec_weak_exposes_valuation_target_thesis_secondary(self):
        """Weak SEC → SEC_EXISTS_WEAK primary + valuation + target + thesis secondary."""
        row = _build_holding_row(
            ticker="NVDA",
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            lanes={
                LANE_SEC_COMPANY_FACTS: _make_lane(
                    LANE_SEC_COMPANY_FACTS, STATUS_SUPPRESSED,
                    artifact_id="art-sec-002",
                    usability_label="SUPPRESSED_INCOMPLETE",
                ),
            },
            supplemental=_empty_supplemental(),
        )
        assert row.blocking_gap_buckets[0] == BUCKET_SEC_EXISTS_WEAK
        assert BUCKET_VALUATION_NOT_BUILT in row.blocking_gap_buckets
        assert BUCKET_TARGET_WEIGHT_NOT_BUILT in row.blocking_gap_buckets
        assert BUCKET_THESIS_NOT_BUILT in row.blocking_gap_buckets

    def test_equity_usable_sec_with_target_set_has_two_gaps(self):
        """SEC usable, target set, no thesis → VALUATION_NOT_BUILT + THESIS_NOT_BUILT."""
        row = _build_holding_row(
            ticker="WMT",
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            lanes={
                LANE_SEC_COMPANY_FACTS: _make_lane(
                    LANE_SEC_COMPANY_FACTS, STATUS_READY,
                    artifact_id="art-3",
                    usability_label="USABLE",
                    source_authority="PRIMARY_AUTHORITY",
                ),
            },
            supplemental=_empty_supplemental(target_tickers=frozenset({"WMT"})),
        )
        assert BUCKET_VALUATION_NOT_BUILT in row.blocking_gap_buckets
        assert BUCKET_TARGET_WEIGHT_NOT_BUILT not in row.blocking_gap_buckets
        assert BUCKET_THESIS_NOT_BUILT in row.blocking_gap_buckets

    def test_equity_all_resolved_single_normalization_gap(self):
        """When all other gaps resolved (via valuation_lane_exists=True), only DATA_NEEDS_NORMALIZATION."""
        gaps = _classify_all_gaps(
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            has_fundamentals_artifact=True,
            has_technical_artifact=True,
            has_sec_companyfacts_artifact=True,
            sec_companyfacts_usability="USABLE",
            has_sec_catalyst_artifact=True,
            has_news_sentiment_artifact=False,
            news_sentiment_usability=None,
            has_target_weight=True,
            has_thesis_history=True,
            valuation_lane_exists=True,
        )
        assert len(gaps) == 1
        assert gaps[0][0] == BUCKET_DATA_NEEDS_NORMALIZATION


class TestETFMultiGap:
    """ETF holdings expose ETF_PROVIDER_NOT_BUILT plus applicable secondary gaps."""

    def test_etf_missing_target_and_thesis_returns_three_gaps(self):
        row = _build_holding_row(
            ticker="VTI",
            asset_type=INSTRUMENT_CATEGORY_ETF,
            lanes={},
            supplemental=_empty_supplemental(),
        )
        assert row.blocking_gap_buckets == [
            BUCKET_ETF_NOT_BUILT,
            BUCKET_TARGET_WEIGHT_NOT_BUILT,
            BUCKET_THESIS_NOT_BUILT,
        ]
        assert row.root_cause_bucket == BUCKET_ETF_NOT_BUILT
        assert row.blocking_gap_count == 3

    def test_etf_with_target_set_returns_two_gaps(self):
        row = _build_holding_row(
            ticker="SCHD",
            asset_type=INSTRUMENT_CATEGORY_ETF,
            lanes={},
            supplemental=_empty_supplemental(target_tickers=frozenset({"SCHD"})),
        )
        assert BUCKET_ETF_NOT_BUILT in row.blocking_gap_buckets
        assert BUCKET_TARGET_WEIGHT_NOT_BUILT not in row.blocking_gap_buckets
        assert BUCKET_THESIS_NOT_BUILT in row.blocking_gap_buckets
        assert row.blocking_gap_count == 2

    def test_etf_both_target_and_thesis_set_only_provider_gap(self):
        row = _build_holding_row(
            ticker="SPY",
            asset_type=INSTRUMENT_CATEGORY_ETF,
            lanes={},
            supplemental=_empty_supplemental(
                target_tickers=frozenset({"SPY"}),
                recommendation_tickers=frozenset({"SPY"}),
            ),
        )
        assert row.blocking_gap_buckets == [BUCKET_ETF_NOT_BUILT]
        assert row.blocking_gap_count == 1

    def test_etf_has_no_equity_sec_or_valuation_gaps(self):
        """SEC, valuation, and fundamentals gaps must NEVER appear for ETF."""
        row = _build_holding_row(
            ticker="QQQ",
            asset_type=INSTRUMENT_CATEGORY_ETF,
            lanes={},
            supplemental=_empty_supplemental(),
        )
        equity_only_buckets = {
            BUCKET_SEC_EXISTS_WEAK,
            BUCKET_SEC_MISSING_CIK,
            BUCKET_SEC_MISSING_WORKER,
            BUCKET_VALUATION_NOT_BUILT,
        }
        for bucket in row.blocking_gap_buckets:
            assert bucket not in equity_only_buckets, (
                f"ETF must not get equity-specific gap {bucket}"
            )


class TestCryptoMultiGap:
    """Crypto holdings expose CRYPTO_PROVIDER_NOT_BUILT plus applicable secondary gaps."""

    def test_crypto_missing_target_and_thesis_returns_three_gaps(self):
        row = _build_holding_row(
            ticker="BTC",
            asset_type=INSTRUMENT_CATEGORY_CRYPTO,
            lanes={},
            supplemental=_empty_supplemental(),
        )
        assert row.blocking_gap_buckets == [
            BUCKET_CRYPTO_NOT_BUILT,
            BUCKET_TARGET_WEIGHT_NOT_BUILT,
            BUCKET_THESIS_NOT_BUILT,
        ]
        assert row.root_cause_bucket == BUCKET_CRYPTO_NOT_BUILT
        assert row.blocking_gap_count == 3

    def test_crypto_both_target_and_thesis_set_only_provider_gap(self):
        row = _build_holding_row(
            ticker="XRP",
            asset_type=INSTRUMENT_CATEGORY_CRYPTO,
            lanes={},
            supplemental=_empty_supplemental(
                target_tickers=frozenset({"XRP"}),
                recommendation_tickers=frozenset({"XRP"}),
            ),
        )
        assert row.blocking_gap_buckets == [BUCKET_CRYPTO_NOT_BUILT]
        assert row.blocking_gap_count == 1

    def test_crypto_has_no_equity_sec_or_valuation_gaps(self):
        """SEC, valuation, and fundamentals gaps must NEVER appear for crypto."""
        row = _build_holding_row(
            ticker="ETH",
            asset_type=INSTRUMENT_CATEGORY_CRYPTO,
            lanes={},
            supplemental=_empty_supplemental(),
        )
        equity_only_buckets = {
            BUCKET_SEC_EXISTS_WEAK,
            BUCKET_SEC_MISSING_CIK,
            BUCKET_SEC_MISSING_WORKER,
            BUCKET_VALUATION_NOT_BUILT,
        }
        for bucket in row.blocking_gap_buckets:
            assert bucket not in equity_only_buckets, (
                f"Crypto must not get equity-specific gap {bucket}"
            )


class TestBlockingGapBucketCounts:
    """blocking_gap_bucket_counts aggregates secondary gaps across all holdings."""

    def test_blocking_gap_bucket_counts_includes_secondary_gaps(self):
        """An ETF holding with 3 gaps contributes 3 entries to blocking_gap_bucket_counts."""
        from app.services.intelligence.v3.intel_data_foundation_forensics_v1 import _build_aggregates
        row = _build_holding_row(
            ticker="VTI",
            asset_type=INSTRUMENT_CATEGORY_ETF,
            lanes={},
            supplemental=_empty_supplemental(),
        )
        mock_coverage = MagicMock()
        mock_coverage.ticker_coverage = {}
        aggs = _build_aggregates([row], mock_coverage)
        bgbc = aggs["blocking_gap_bucket_counts"]
        assert bgbc.get(BUCKET_ETF_NOT_BUILT, 0) == 1
        assert bgbc.get(BUCKET_TARGET_WEIGHT_NOT_BUILT, 0) == 1
        assert bgbc.get(BUCKET_THESIS_NOT_BUILT, 0) == 1

    def test_root_cause_bucket_counts_only_primary(self):
        """root_cause_bucket_counts counts only the primary gap — backward compat."""
        from app.services.intelligence.v3.intel_data_foundation_forensics_v1 import _build_aggregates
        row = _build_holding_row(
            ticker="VTI",
            asset_type=INSTRUMENT_CATEGORY_ETF,
            lanes={},
            supplemental=_empty_supplemental(),
        )
        mock_coverage = MagicMock()
        mock_coverage.ticker_coverage = {}
        aggs = _build_aggregates([row], mock_coverage)
        rcbc = aggs["root_cause_bucket_counts"]
        assert rcbc.get(BUCKET_ETF_NOT_BUILT, 0) == 1
        assert rcbc.get(BUCKET_TARGET_WEIGHT_NOT_BUILT, 0) == 0
        assert rcbc.get(BUCKET_THESIS_NOT_BUILT, 0) == 0

    def test_blocking_gap_bucket_counts_accumulates_across_holdings(self):
        """Two ETF holdings without target/thesis → secondary gaps appear twice."""
        from app.services.intelligence.v3.intel_data_foundation_forensics_v1 import _build_aggregates
        row1 = _build_holding_row(
            ticker="VTI", asset_type=INSTRUMENT_CATEGORY_ETF,
            lanes={}, supplemental=_empty_supplemental(),
        )
        row2 = _build_holding_row(
            ticker="SCHD", asset_type=INSTRUMENT_CATEGORY_ETF,
            lanes={}, supplemental=_empty_supplemental(),
        )
        mock_coverage = MagicMock()
        mock_coverage.ticker_coverage = {}
        aggs = _build_aggregates([row1, row2], mock_coverage)
        bgbc = aggs["blocking_gap_bucket_counts"]
        assert bgbc.get(BUCKET_ETF_NOT_BUILT, 0) == 2
        assert bgbc.get(BUCKET_TARGET_WEIGHT_NOT_BUILT, 0) == 2
        assert bgbc.get(BUCKET_THESIS_NOT_BUILT, 0) == 2


class TestMultiGapContractShape:
    """Contract tests for the new multi-gap fields in dict output."""

    def test_holding_dict_has_all_multi_gap_fields(self):
        row = _build_holding_row(
            ticker="TEST",
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            lanes={},
            supplemental=_empty_supplemental(),
        )
        d = row.to_dict()
        assert "blocking_gap_buckets" in d
        assert "blocking_gap_count" in d
        assert "next_required_fixes" in d
        assert isinstance(d["blocking_gap_buckets"], list)
        assert isinstance(d["blocking_gap_count"], int)
        assert isinstance(d["next_required_fixes"], list)

    def test_result_dict_has_blocking_gap_bucket_counts(self):
        row = _build_holding_row(
            ticker="TEST",
            asset_type=INSTRUMENT_CATEGORY_ETF,
            lanes={},
            supplemental=_empty_supplemental(),
        )
        result = DataFoundationForensicsResult(
            schema_version=FORENSICS_VERSION,
            user_id="u-test",
            generated_at="2026-05-24T00:00:00+00:00",
            holdings=[row],
        )
        d = result.to_dict()
        assert "blocking_gap_bucket_counts" in d
        assert isinstance(d["blocking_gap_bucket_counts"], dict)

    def test_all_blocking_gaps_are_valid_bucket_strings(self):
        """Every bucket in blocking_gap_buckets must be in ALL_BUCKETS."""
        for asset_type in [
            INSTRUMENT_CATEGORY_EQUITY,
            INSTRUMENT_CATEGORY_ETF,
            INSTRUMENT_CATEGORY_CRYPTO,
            INSTRUMENT_CATEGORY_UNKNOWN,
        ]:
            row = _build_holding_row(
                ticker="X", asset_type=asset_type,
                lanes={}, supplemental=_empty_supplemental(),
            )
            for bucket in row.blocking_gap_buckets:
                assert bucket in ALL_BUCKETS, (
                    f"Unknown bucket {bucket!r} for asset_type={asset_type}"
                )

    def test_blocking_gap_count_equals_list_lengths(self):
        for asset_type in [
            INSTRUMENT_CATEGORY_EQUITY, INSTRUMENT_CATEGORY_ETF, INSTRUMENT_CATEGORY_CRYPTO,
        ]:
            row = _build_holding_row(
                ticker="X", asset_type=asset_type,
                lanes={}, supplemental=_empty_supplemental(),
            )
            assert row.blocking_gap_count == len(row.blocking_gap_buckets)
            assert len(row.next_required_fixes) == row.blocking_gap_count

    def test_next_required_fixes_contain_no_forbidden_patterns(self):
        """next_required_fixes must not leak raw data, URLs bearing secrets, or credentials."""
        FORBIDDEN = ["raw_payload", "api_key", "password", "secret", "private"]
        for asset_type in [INSTRUMENT_CATEGORY_EQUITY, INSTRUMENT_CATEGORY_ETF, INSTRUMENT_CATEGORY_CRYPTO]:
            row = _build_holding_row(
                ticker="X", asset_type=asset_type,
                lanes={}, supplemental=_empty_supplemental(),
            )
            for fix in row.next_required_fixes:
                for pattern in FORBIDDEN:
                    assert pattern.lower() not in fix.lower(), (
                        f"next_required_fixes contains {pattern!r}: {fix!r}"
                    )
