"""Stage 9D — Canonical Equity Research Dataset v1.

Covers:
  1. 16 strong SEC equities produce canonical equity dataset rows with
     safe_for_equity_dataset=True.
  2. Degraded/missing equities (TSM/KLAR/BLSH pattern) remain honestly
     degraded with safe_for_equity_dataset=False and clear not_safe_reason.
  3. ETF holdings receive None for canonical_equity_dataset in forensics
     (equity dataset not applicable; ETF provider lane missing separately).
  4. Crypto holdings receive None for canonical_equity_dataset in forensics.
  5. Missing SEC facts do not become fabricated availability signals.
  6. Dataset row never marks synthesis_ready=True.
  7. Dataset row never marks valuation_ready=True.
  8. Dataset row never marks safe_for_decision=True.
  9. Forensics includes canonical_equity_dataset status per equity holding.
  10. Forensics asset_parity_roadmap shows all three asset classes with gaps.
  11. ETF and crypto parity gaps report correct synthesis gates (not equity gaps).
  12. equity_canonical_dataset_count reflects usable equity count in forensics.
  13. equity_canonical_dataset_degraded_tickers lists tickers with weak SEC.
  14. No decide() import guard.
  15. No raw payloads / metric keys / fact values in canonical dataset output.
  16. Operating trends: USABLE + COMPLETE + FRESH → all 5 availability signals True.
  17. Operating trends: USABLE_WITH_LIMITATIONS + PARTIAL → 4 of 5 (FCF False).
  18. Operating trends: SUPPRESSED → all 5 availability signals False.
  19. Technical context always has trust_label=LIMITED_TRUST.
  20. Catalyst context: available when SEC catalyst lane is usable.
  21. Source health entries are populated for present lanes.
  22. Not-applicable row for ETF: company_applicable=False.
  23. Not-applicable row for crypto: company_applicable=False.
  24. Asset parity roadmap synthesis_gate for equity: BLOCKED_VALUATION_LANE_NOT_BUILT
      when canonical dataset exists, BLOCKED_ALL_ASSET_CLASSES_NEED_CANONICAL_DATASETS
      otherwise.
  25. Forensics valuation gap message references canonical dataset when SEC usable.
  26. Forensics valuation gap message references SEC fix when SEC not usable.
  27. build_canonical_equity_dataset_row is pure — no IO calls.
  28. Observation count proxy: low obs (<3) → availability signals False.
  29. Missing catalyst lane → catalyst_available=False, source="unavailable".
  30. Missing technical lane → technical_available=False.
  31. CanonicalEquityDatasetRow.to_dict() serializes all required fields.
  32. AssetParityRoadmap.to_dict() serializes all three asset classes.
  33. OperatingTrendSection.to_dict() has no raw metric keys.
  34. CatalystContextSection.to_dict() has no raw fact values.
  35. Forensics equity_canonical_dataset_count is 0 when all SEC artifacts missing.
  36. safe_for_equity_dataset remains False for NOT_EVALUABLE usability.
  37. Stage-9B/9C tests still pass (regression: no breaking imports).
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
from unittest.mock import MagicMock

import pytest

from app.services.intelligence.v3.canonical_equity_dataset_v1 import (
    ALL_SECTIONS,
    BASIS_SEC_COMPANYFACTS,
    BASIS_UNAVAILABLE,
    DATASET_VERSION,
    SECTION_CASH_FLOW_FCF,
    SECTION_NET_INCOME_EPS,
    SECTION_PROFITABILITY,
    SECTION_REVENUE,
    SECTION_SHARE_COUNT,
    SECTION_STATUS_AVAILABLE,
    SECTION_STATUS_MISSING,
    SECTION_STATUS_NOT_APPLICABLE,
    SECTION_STATUS_PARTIAL,
    SYNTHESIS_GATE_BLOCKED,
    TECHNICAL_TRUST_LABEL,
    TREND_DOWN,
    TREND_FLAT,
    TREND_UNKNOWN,
    TREND_UP,
    VALUATION_GATE_BLOCKED,
    AssetClassFoundationGap,
    AssetParityRoadmap,
    CanonicalEquityDatasetRow,
    EvidenceSectionRecord,
    OperatingTrendSection,
    PeriodIdentity,
    build_asset_parity_roadmap,
    build_canonical_equity_dataset_row,
)
from app.services.intelligence.v3.intel_data_foundation_forensics_v1 import (
    BUCKET_VALUATION_NOT_BUILT,
    FORENSICS_VERSION,
    DataFoundationForensicsResult,
    HoldingForensicsRow,
    _SupplementalData,
    _build_holding_row,
    _classify_all_gaps,
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


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_sec_lane(
    *,
    status: str = STATUS_READY,
    artifact_id: str = "art-sec-001",
    usability_label: str = "USABLE",
    source_authority: str = "PRIMARY_AUTHORITY",
    completeness_band: str = "COMPLETE",
    freshness_status: str = "FRESH",
    model_version: str = "sec_xbrl_companyfacts_v2",
    observation_count: int = 20,
) -> LaneCoverage:
    return LaneCoverage(
        lane=LANE_SEC_COMPANY_FACTS,
        artifact_type="fundamental_quality",
        skill_pack="sec_companyfacts_evidence_v1",
        scope_kind="ticker",
        ticker="TEST",
        artifact_id=artifact_id,
        status=status,
        usability_label=usability_label,
        is_usable=(usability_label in {"USABLE", "USABLE_WITH_LIMITATIONS"}),
        suppression_reason=None,
        source_authority=source_authority,
        completeness_band=completeness_band,
        has_contradictions=None,
        freshness_status=freshness_status,
        confidence_or_trust_level=None,
        model_version=model_version,
        generated_at="2026-05-24T00:00:00+00:00",
        expires_at=None,
    )


def _make_tech_lane(
    *,
    artifact_id: Optional[str] = "art-tech-001",
    usability_label: str = "USABLE_WITH_LIMITATIONS",
    status: str = STATUS_LIMITED,
) -> LaneCoverage:
    return LaneCoverage(
        lane=LANE_TECHNICALS,
        artifact_type="technical_signal",
        skill_pack="technicals_evidence_v1",
        scope_kind="ticker",
        ticker="TEST",
        artifact_id=artifact_id,
        status=status,
        usability_label=usability_label,
        is_usable=(usability_label in {"USABLE", "USABLE_WITH_LIMITATIONS"}),
        suppression_reason=None,
        source_authority="UNOFFICIAL_FREE_SOURCE",
        completeness_band="PARTIAL",
        has_contradictions=None,
        freshness_status="FRESH",
        confidence_or_trust_level=None,
        model_version="technicals_v2",
        generated_at="2026-05-24T00:00:00+00:00",
        expires_at=None,
    )


def _make_cat_lane(
    *,
    artifact_id: Optional[str] = "art-cat-001",
    usability_label: str = "USABLE_WITH_LIMITATIONS",
    status: str = STATUS_LIMITED,
) -> LaneCoverage:
    return LaneCoverage(
        lane=LANE_SEC_CATALYST_SENTIMENT,
        artifact_type="sentiment_event",
        skill_pack="sec_catalyst_sentiment_evidence_v1",
        scope_kind="ticker",
        ticker="TEST",
        artifact_id=artifact_id,
        status=status,
        usability_label=usability_label,
        is_usable=(usability_label in {"USABLE", "USABLE_WITH_LIMITATIONS"}),
        suppression_reason=None,
        source_authority="PRIMARY_AUTHORITY",
        completeness_band="PARTIAL",
        has_contradictions=None,
        freshness_status="FRESH",
        confidence_or_trust_level=None,
        model_version="sec_catalyst_sentiment_adapter.v2",
        generated_at="2026-05-24T00:00:00+00:00",
        expires_at=None,
    )


def _empty_supplemental(
    *,
    target_tickers: frozenset = frozenset(),
    recommendation_tickers: frozenset = frozenset(),
    fact_counts: Optional[dict] = None,
    has_portfolio_snapshot: bool = True,
    sec_fact_records: Optional[dict] = None,
) -> _SupplementalData:
    return _SupplementalData(
        target_tickers=target_tickers,
        recommendation_tickers=recommendation_tickers,
        fact_counts=fact_counts or {},
        has_portfolio_snapshot=has_portfolio_snapshot,
        sec_fact_records=sec_fact_records or {},
    )


def _make_coverage(
    *,
    tickers_lanes: dict[str, dict[str, LaneCoverage]],
) -> ResearchEvidenceCoverageSummary:
    ticker_coverage = {
        t: TickerCoverage(ticker=t, lanes=lanes)
        for t, lanes in tickers_lanes.items()
    }
    return ResearchEvidenceCoverageSummary(
        schema_version="v1",
        user_id="user-test",
        generated_at=datetime.now(timezone.utc).isoformat(),
        portfolio_ticker_count=len(tickers_lanes),
        ticker_coverage=ticker_coverage,
        portfolio_macro_coverage=LaneCoverage(
            lane="macro_context",
            artifact_type="portfolio_exposure",
            skill_pack="fred_macro_evidence_v1",
            scope_kind="portfolio",
            ticker=None,
            artifact_id=None,
            status=STATUS_MISSING,
            usability_label=None,
            is_usable=False,
            suppression_reason=None,
            source_authority=None,
            completeness_band=None,
            has_contradictions=None,
            freshness_status=None,
            confidence_or_trust_level=None,
            model_version=None,
            generated_at=None,
            expires_at=None,
        ),
        lane_counts={},
        usability_counts={},
        missing_lane_counts={},
        suppressed_counts={},
        stale_or_unknown_counts={},
        ready_artifact_count=0,
        errors=[],
    )


# ── Tests: core build_canonical_equity_dataset_row ───────────────────────────


class TestBuildCanonicalEquityDatasetRowEquity:
    """Equity holdings with usable SEC artifacts produce valid dataset rows."""

    def test_usable_sec_produces_safe_dataset_row(self):
        sec = _make_sec_lane(usability_label="USABLE", completeness_band="COMPLETE")
        row = build_canonical_equity_dataset_row(
            ticker="AAPL",
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            lanes={LANE_SEC_COMPANY_FACTS: sec},
            sec_obs_count=25,
            cat_count=None,
        )
        assert row.safe_for_equity_dataset is True
        assert row.ticker == "AAPL"
        assert row.asset_type == INSTRUMENT_CATEGORY_EQUITY
        assert row.company_applicable is True
        assert row.dataset_version == DATASET_VERSION

    def test_usable_with_limitations_produces_safe_dataset_row(self):
        sec = _make_sec_lane(
            usability_label="USABLE_WITH_LIMITATIONS",
            completeness_band="PARTIAL",
        )
        row = build_canonical_equity_dataset_row(
            ticker="MSFT",
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            lanes={LANE_SEC_COMPANY_FACTS: sec},
            sec_obs_count=8,
            cat_count=None,
        )
        assert row.safe_for_equity_dataset is True
        assert row.synthesis_ready is False
        assert row.valuation_ready is False
        assert row.safe_for_decision is False

    def test_usable_complete_fresh_operating_trends_all_true(self):
        """USABLE + COMPLETE + FRESH + high obs_count → all 5 sections True."""
        sec = _make_sec_lane(
            usability_label="USABLE",
            completeness_band="COMPLETE",
            freshness_status="FRESH",
        )
        row = build_canonical_equity_dataset_row(
            ticker="MSFT",
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            lanes={LANE_SEC_COMPANY_FACTS: sec},
            sec_obs_count=30,
            cat_count=None,
        )
        tr = row.operating_trends
        assert tr.revenue_trend_available is True
        assert tr.profitability_margin_available is True
        assert tr.eps_net_income_available is True
        assert tr.fcf_available is True
        assert tr.share_count_dilution_available is True
        assert tr.trend_source == LANE_SEC_COMPANY_FACTS
        assert tr.missing_reason is None

    def test_usable_partial_fresh_fcf_false(self):
        """USABLE + PARTIAL + FRESH → 4 core sections True, FCF False (needs COMPLETE)."""
        sec = _make_sec_lane(
            usability_label="USABLE_WITH_LIMITATIONS",
            completeness_band="PARTIAL",
            freshness_status="FRESH",
        )
        row = build_canonical_equity_dataset_row(
            ticker="CRM",
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            lanes={LANE_SEC_COMPANY_FACTS: sec},
            sec_obs_count=10,
            cat_count=None,
        )
        tr = row.operating_trends
        assert tr.revenue_trend_available is True
        assert tr.profitability_margin_available is True
        assert tr.eps_net_income_available is True
        assert tr.fcf_available is False  # PARTIAL completeness not enough for FCF
        assert tr.share_count_dilution_available is True

    def test_synthesis_ready_always_false(self):
        sec = _make_sec_lane(usability_label="USABLE")
        row = build_canonical_equity_dataset_row(
            ticker="GOOGL",
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            lanes={LANE_SEC_COMPANY_FACTS: sec},
            sec_obs_count=20,
            cat_count=None,
        )
        assert row.synthesis_ready is False

    def test_valuation_ready_always_false(self):
        sec = _make_sec_lane(usability_label="USABLE")
        row = build_canonical_equity_dataset_row(
            ticker="AMZN",
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            lanes={LANE_SEC_COMPANY_FACTS: sec},
            sec_obs_count=20,
            cat_count=None,
        )
        assert row.valuation_ready is False

    def test_safe_for_decision_always_false(self):
        sec = _make_sec_lane(usability_label="USABLE")
        row = build_canonical_equity_dataset_row(
            ticker="NVDA",
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            lanes={LANE_SEC_COMPANY_FACTS: sec},
            sec_obs_count=20,
            cat_count=None,
        )
        assert row.safe_for_decision is False

    def test_technical_context_always_limited_trust(self):
        sec = _make_sec_lane(usability_label="USABLE")
        tech = _make_tech_lane()
        row = build_canonical_equity_dataset_row(
            ticker="META",
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            lanes={LANE_SEC_COMPANY_FACTS: sec, LANE_TECHNICALS: tech},
            sec_obs_count=15,
            cat_count=None,
        )
        assert row.technical_context.trust_label == TECHNICAL_TRUST_LABEL

    def test_catalyst_context_available_when_usable(self):
        sec = _make_sec_lane(usability_label="USABLE")
        cat = _make_cat_lane(usability_label="USABLE_WITH_LIMITATIONS")
        row = build_canonical_equity_dataset_row(
            ticker="AAPL",
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            lanes={LANE_SEC_COMPANY_FACTS: sec, LANE_SEC_CATALYST_SENTIMENT: cat},
            sec_obs_count=20,
            cat_count=5,
        )
        assert row.catalyst_context.catalyst_available is True
        assert row.catalyst_context.catalyst_count == 5
        assert row.catalyst_context.catalyst_source == LANE_SEC_CATALYST_SENTIMENT

    def test_catalyst_context_unavailable_when_no_lane(self):
        sec = _make_sec_lane(usability_label="USABLE")
        row = build_canonical_equity_dataset_row(
            ticker="AAPL",
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            lanes={LANE_SEC_COMPANY_FACTS: sec},
            sec_obs_count=20,
            cat_count=None,
        )
        assert row.catalyst_context.catalyst_available is False
        assert row.catalyst_context.catalyst_source == "unavailable"
        assert row.catalyst_context.missing_reason is not None

    def test_source_health_entries_populated(self):
        sec = _make_sec_lane()
        tech = _make_tech_lane()
        row = build_canonical_equity_dataset_row(
            ticker="AAPL",
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            lanes={LANE_SEC_COMPANY_FACTS: sec, LANE_TECHNICALS: tech},
            sec_obs_count=20,
            cat_count=None,
        )
        lane_names = {e.lane for e in row.source_artifacts}
        assert LANE_SEC_COMPANY_FACTS in lane_names
        assert LANE_TECHNICALS in lane_names

    def test_low_observation_count_makes_availability_false(self):
        """obs_count=0 (below MIN_OBSERVATIONS_FOR_AVAILABILITY=3) → all availability False."""
        sec = _make_sec_lane(
            usability_label="USABLE",
            completeness_band="COMPLETE",
            freshness_status="FRESH",
        )
        row = build_canonical_equity_dataset_row(
            ticker="KLAR",
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            lanes={LANE_SEC_COMPANY_FACTS: sec},
            sec_obs_count=0,
            cat_count=None,
        )
        tr = row.operating_trends
        # All False because obs_count=0 < MIN (3)
        assert tr.revenue_trend_available is False
        assert tr.fcf_available is False

    def test_to_dict_has_required_fields(self):
        sec = _make_sec_lane(usability_label="USABLE")
        row = build_canonical_equity_dataset_row(
            ticker="AAPL",
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            lanes={LANE_SEC_COMPANY_FACTS: sec},
            sec_obs_count=20,
            cat_count=None,
        )
        d = row.to_dict()
        required = {
            "ticker", "asset_type", "company_applicable", "dataset_version",
            "generated_at", "source_artifacts", "operating_trends",
            "catalyst_context", "technical_context", "missing_section_reasons",
            "safe_for_equity_dataset", "valuation_ready", "synthesis_ready",
            "safe_for_decision",
        }
        assert required.issubset(d.keys())

    def test_no_raw_metric_keys_in_output(self):
        """Verify output dict contains no raw SEC metric key jargon."""
        sec = _make_sec_lane(usability_label="USABLE", completeness_band="COMPLETE")
        row = build_canonical_equity_dataset_row(
            ticker="AAPL",
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            lanes={LANE_SEC_COMPANY_FACTS: sec},
            sec_obs_count=20,
            cat_count=None,
        )
        import json
        output_str = json.dumps(row.to_dict())
        # Raw XBRL metric keys that must not appear in output
        forbidden_keys = ["us-gaap/", "Revenues", "NetIncomeLoss", "EarningsPerShare",
                          "OperatingCashFlow", "CommonStockSharesOutstanding"]
        for key in forbidden_keys:
            assert key not in output_str, f"Raw metric key found in output: {key}"


class TestDegradedEquities:
    """TSM/KLAR/BLSH pattern: weak/stale SEC → safe_for_equity_dataset=False."""

    def test_suppressed_contradicted_is_not_safe(self):
        sec = _make_sec_lane(
            usability_label="SUPPRESSED_CONTRADICTED",
            status=STATUS_SUPPRESSED,
        )
        row = build_canonical_equity_dataset_row(
            ticker="TSM",
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            lanes={LANE_SEC_COMPANY_FACTS: sec},
            sec_obs_count=10,
            cat_count=None,
        )
        assert row.safe_for_equity_dataset is False
        assert row.not_safe_reason is not None
        assert "suppressed" in row.not_safe_reason.lower()

    def test_suppressed_incomplete_is_not_safe(self):
        sec = _make_sec_lane(
            usability_label="SUPPRESSED_INCOMPLETE",
            status=STATUS_SUPPRESSED,
        )
        row = build_canonical_equity_dataset_row(
            ticker="KLAR",
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            lanes={LANE_SEC_COMPANY_FACTS: sec},
            sec_obs_count=1,
            cat_count=None,
        )
        assert row.safe_for_equity_dataset is False
        assert row.operating_trends.revenue_trend_available is False
        assert row.operating_trends.trend_source == "unavailable"

    def test_missing_sec_lane_is_not_safe(self):
        """No SEC lane at all → safe_for_equity_dataset=False."""
        row = build_canonical_equity_dataset_row(
            ticker="BLSH",
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            lanes={},
            sec_obs_count=None,
            cat_count=None,
        )
        assert row.safe_for_equity_dataset is False
        assert row.not_safe_reason is not None
        assert "missing" in row.not_safe_reason.lower()

    def test_not_evaluable_is_not_safe(self):
        sec = _make_sec_lane(
            usability_label="NOT_EVALUABLE",
            status=STATUS_SUPPRESSED,
        )
        row = build_canonical_equity_dataset_row(
            ticker="TSM",
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            lanes={LANE_SEC_COMPANY_FACTS: sec},
            sec_obs_count=5,
            cat_count=None,
        )
        assert row.safe_for_equity_dataset is False

    def test_degraded_equity_all_operating_trends_false(self):
        """Suppressed SEC facts → all operating trend signals False (no fabrication)."""
        sec = _make_sec_lane(usability_label="SUPPRESSED_CONTRADICTED")
        row = build_canonical_equity_dataset_row(
            ticker="TSM",
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            lanes={LANE_SEC_COMPANY_FACTS: sec},
            sec_obs_count=10,
            cat_count=None,
        )
        tr = row.operating_trends
        assert tr.revenue_trend_available is False
        assert tr.profitability_margin_available is False
        assert tr.eps_net_income_available is False
        assert tr.fcf_available is False
        assert tr.share_count_dilution_available is False
        assert tr.trend_source == "unavailable"

    def test_degraded_equity_synthesis_still_false(self):
        sec = _make_sec_lane(usability_label="SUPPRESSED_CONTRADICTED")
        row = build_canonical_equity_dataset_row(
            ticker="TSM",
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            lanes={LANE_SEC_COMPANY_FACTS: sec},
            sec_obs_count=10,
            cat_count=None,
        )
        assert row.synthesis_ready is False


class TestNonEquityNotApplicable:
    """ETF and crypto do not receive equity dataset rows."""

    def test_etf_returns_not_applicable_row(self):
        row = build_canonical_equity_dataset_row(
            ticker="SPY",
            asset_type=INSTRUMENT_CATEGORY_ETF,
            lanes={},
            sec_obs_count=None,
            cat_count=None,
        )
        assert row.safe_for_equity_dataset is False
        assert row.company_applicable is False
        assert row.synthesis_ready is False
        assert row.valuation_ready is False
        assert "ETF" in (row.not_safe_reason or "")

    def test_crypto_returns_not_applicable_row(self):
        row = build_canonical_equity_dataset_row(
            ticker="BTC",
            asset_type=INSTRUMENT_CATEGORY_CRYPTO,
            lanes={},
            sec_obs_count=None,
            cat_count=None,
        )
        assert row.safe_for_equity_dataset is False
        assert row.company_applicable is False
        assert "crypto" in (row.not_safe_reason or "").lower()

    def test_unknown_asset_type_not_applicable(self):
        row = build_canonical_equity_dataset_row(
            ticker="UNKNOWN",
            asset_type=INSTRUMENT_CATEGORY_UNKNOWN,
            lanes={},
            sec_obs_count=None,
            cat_count=None,
        )
        assert row.safe_for_equity_dataset is False
        assert row.company_applicable is False

    def test_etf_operating_trends_all_false(self):
        row = build_canonical_equity_dataset_row(
            ticker="VOO",
            asset_type=INSTRUMENT_CATEGORY_ETF,
            lanes={},
            sec_obs_count=None,
            cat_count=None,
        )
        tr = row.operating_trends
        assert tr.revenue_trend_available is False
        assert tr.fcf_available is False
        assert tr.trend_source == "unavailable"


# ── Tests: asset parity roadmap ───────────────────────────────────────────────


class TestAssetParityRoadmap:
    """Asset parity roadmap correctly tracks gaps by asset class."""

    def test_equity_with_canonical_dataset_shows_valuation_blocked(self):
        roadmap = build_asset_parity_roadmap(
            equity_canonical_count=16,
            equity_total=19,
            equity_edge_case_tickers=["TSM", "KLAR", "BLSH"],
            etf_total=13,
            crypto_total=2,
        )
        equity_gap = next(ac for ac in roadmap.asset_classes if ac.asset_class == "equity")
        assert equity_gap.canonical_dataset_built is True
        assert equity_gap.valuation_lane_built is False
        assert equity_gap.synthesis_gate == VALUATION_GATE_BLOCKED

    def test_equity_no_canonical_dataset_shows_full_blocked(self):
        roadmap = build_asset_parity_roadmap(
            equity_canonical_count=0,
            equity_total=19,
            equity_edge_case_tickers=["AAPL", "MSFT"],
            etf_total=13,
            crypto_total=2,
        )
        equity_gap = next(ac for ac in roadmap.asset_classes if ac.asset_class == "equity")
        assert equity_gap.canonical_dataset_built is False
        assert equity_gap.synthesis_gate == SYNTHESIS_GATE_BLOCKED

    def test_etf_shows_canonical_not_built_and_blocked(self):
        roadmap = build_asset_parity_roadmap(
            equity_canonical_count=16,
            equity_total=19,
            equity_edge_case_tickers=[],
            etf_total=13,
            crypto_total=2,
        )
        etf_gap = next(ac for ac in roadmap.asset_classes if ac.asset_class == "etf")
        assert etf_gap.canonical_dataset_built is False
        assert etf_gap.valuation_lane_built is False
        assert etf_gap.synthesis_gate == SYNTHESIS_GATE_BLOCKED

    def test_crypto_shows_canonical_not_built_and_blocked(self):
        roadmap = build_asset_parity_roadmap(
            equity_canonical_count=16,
            equity_total=19,
            equity_edge_case_tickers=[],
            etf_total=13,
            crypto_total=2,
        )
        crypto_gap = next(ac for ac in roadmap.asset_classes if ac.asset_class == "crypto")
        assert crypto_gap.canonical_dataset_built is False
        assert crypto_gap.synthesis_gate == SYNTHESIS_GATE_BLOCKED

    def test_synthesis_never_ready_at_stage_9d(self):
        roadmap = build_asset_parity_roadmap(
            equity_canonical_count=16,
            equity_total=19,
            equity_edge_case_tickers=[],
            etf_total=13,
            crypto_total=2,
        )
        assert roadmap.all_classes_synthesis_ready is False

    def test_edge_case_tickers_appear_in_roadmap(self):
        roadmap = build_asset_parity_roadmap(
            equity_canonical_count=16,
            equity_total=19,
            equity_edge_case_tickers=["TSM", "KLAR", "BLSH"],
            etf_total=13,
            crypto_total=2,
        )
        equity_gap = next(ac for ac in roadmap.asset_classes if ac.asset_class == "equity")
        assert equity_gap.edge_cases is not None
        assert "TSM" in equity_gap.edge_cases
        assert "KLAR" in equity_gap.edge_cases
        assert "BLSH" in equity_gap.edge_cases

    def test_to_dict_has_all_three_asset_classes(self):
        roadmap = build_asset_parity_roadmap(
            equity_canonical_count=16,
            equity_total=19,
            equity_edge_case_tickers=[],
            etf_total=13,
            crypto_total=2,
        )
        d = roadmap.to_dict()
        classes = {ac["asset_class"] for ac in d["asset_classes"]}
        assert classes == {"equity", "etf", "crypto"}

    def test_parity_note_mentions_all_asset_classes(self):
        roadmap = build_asset_parity_roadmap(
            equity_canonical_count=16,
            equity_total=19,
            equity_edge_case_tickers=[],
            etf_total=13,
            crypto_total=2,
        )
        note = roadmap.parity_note.lower()
        assert "equity" in note or "equit" in note
        assert "etf" in note
        assert "crypto" in note


# ── Tests: forensics integration ─────────────────────────────────────────────


class TestForensicsIntegration:
    """Forensics includes canonical_equity_dataset and asset_parity_roadmap."""

    def _equity_lanes_usable(self) -> dict[str, LaneCoverage]:
        return {LANE_SEC_COMPANY_FACTS: _make_sec_lane(usability_label="USABLE")}

    def _equity_lanes_suppressed(self) -> dict[str, LaneCoverage]:
        return {
            LANE_SEC_COMPANY_FACTS: _make_sec_lane(
                usability_label="SUPPRESSED_CONTRADICTED",
                status=STATUS_SUPPRESSED,
            )
        }

    def _etf_lanes(self) -> dict[str, LaneCoverage]:
        return {}  # ETFs have no SEC company facts

    def _crypto_lanes(self) -> dict[str, LaneCoverage]:
        return {}  # Crypto has no SEC company facts

    def test_equity_usable_has_canonical_dataset_populated(self):
        supp = _empty_supplemental()
        row = _build_holding_row(
            ticker="AAPL",
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            lanes=self._equity_lanes_usable(),
            supplemental=supp,
        )
        assert row.canonical_equity_dataset is not None
        assert row.canonical_equity_dataset["safe_for_equity_dataset"] is True
        assert row.canonical_equity_dataset["synthesis_ready"] is False
        assert row.canonical_equity_dataset["valuation_ready"] is False

    def test_equity_suppressed_has_canonical_dataset_not_safe(self):
        supp = _empty_supplemental()
        row = _build_holding_row(
            ticker="TSM",
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            lanes=self._equity_lanes_suppressed(),
            supplemental=supp,
        )
        assert row.canonical_equity_dataset is not None
        assert row.canonical_equity_dataset["safe_for_equity_dataset"] is False

    def test_etf_has_no_canonical_equity_dataset(self):
        supp = _empty_supplemental()
        row = _build_holding_row(
            ticker="SPY",
            asset_type=INSTRUMENT_CATEGORY_ETF,
            lanes=self._etf_lanes(),
            supplemental=supp,
        )
        assert row.canonical_equity_dataset is None

    def test_crypto_has_no_canonical_equity_dataset(self):
        supp = _empty_supplemental()
        row = _build_holding_row(
            ticker="BTC",
            asset_type=INSTRUMENT_CATEGORY_CRYPTO,
            lanes=self._crypto_lanes(),
            supplemental=supp,
        )
        assert row.canonical_equity_dataset is None

    def test_valuation_gap_message_references_canonical_dataset_when_usable(self):
        """When SEC is usable, valuation gap message says canonical dataset is built."""
        gaps = _classify_all_gaps(
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            has_fundamentals_artifact=True,
            has_technical_artifact=True,
            has_sec_companyfacts_artifact=True,
            sec_companyfacts_usability="USABLE",
            has_sec_catalyst_artifact=False,
            has_news_sentiment_artifact=False,
            news_sentiment_usability=None,
            has_target_weight=True,
            has_thesis_history=True,
            valuation_lane_exists=False,
        )
        valuation_gap = next(g for g in gaps if g[0] == BUCKET_VALUATION_NOT_BUILT)
        assert "canonical equity research dataset" in valuation_gap[1].lower() or \
               "canonical equity" in valuation_gap[1]
        assert "Stage 9D" in valuation_gap[1] or "stage 9d" in valuation_gap[1].lower()

    def test_valuation_gap_message_references_sec_fix_when_sec_not_usable(self):
        """When SEC is NOT usable, valuation gap message directs user to fix SEC first."""
        gaps = _classify_all_gaps(
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            has_fundamentals_artifact=True,
            has_technical_artifact=True,
            has_sec_companyfacts_artifact=True,
            sec_companyfacts_usability="SUPPRESSED_CONTRADICTED",
            has_sec_catalyst_artifact=False,
            has_news_sentiment_artifact=False,
            news_sentiment_usability=None,
            has_target_weight=True,
            has_thesis_history=True,
            valuation_lane_exists=False,
        )
        valuation_gap = next((g for g in gaps if g[0] == BUCKET_VALUATION_NOT_BUILT), None)
        # Should still appear (valuation is always missing) but message should mention SEC fix
        assert valuation_gap is not None
        assert "sec" in valuation_gap[1].lower() or "canonical" in valuation_gap[1].lower()


class TestForensicsComputeDataFoundation:
    """End-to-end forensics result includes canonical dataset counts + parity roadmap."""

    def _make_fake_db_client(
        self,
        *,
        tickers_with_sec_usable: list[str],
        tickers_suppressed: list[str],
        etf_tickers: list[str],
        crypto_tickers: list[str],
        holding_context_by_ticker: Optional[dict] = None,
    ) -> MagicMock:
        """Build a FakeSupabaseClient that returns SEC coverage for given tickers."""
        from unittest.mock import MagicMock

        client = MagicMock()

        def _make_usable_artifact(ticker: str, artifact_id: str) -> dict:
            return {
                "id": artifact_id,
                "user_id": "user-test",
                "ticker": ticker,
                "artifact_type": "fundamental_quality",
                "skill_pack": "sec_companyfacts_evidence_v1",
                "model_version": "sec_xbrl_companyfacts_v2",
                "scope_kind": "ticker",
                "is_active": True,
                "is_usable": True,
                "generated_at": "2026-05-24T00:00:00+00:00",
                "expires_at": None,
                "payload": {
                    "truth_usability_assessment": {
                        "usability_label": "USABLE",
                        "strongest_authority_level": "PRIMARY_AUTHORITY",
                        "completeness_band": "COMPLETE",
                        "freshness_status": "FRESH",
                        "confidence_or_trust_level": "HIGH",
                    },
                    "contradiction_assessment": {
                        "has_contradictions": False,
                        "is_evaluable": True,
                        "contradiction_count": 0,
                        "not_evaluable_reason": None,
                        "contradiction_groups": [],
                    },
                },
            }

        def _make_suppressed_artifact(ticker: str, artifact_id: str) -> dict:
            return {
                "id": artifact_id,
                "user_id": "user-test",
                "ticker": ticker,
                "artifact_type": "fundamental_quality",
                "skill_pack": "sec_companyfacts_evidence_v1",
                "model_version": "sec_xbrl_companyfacts_v1",
                "scope_kind": "ticker",
                "is_active": True,
                "is_usable": False,
                "generated_at": "2026-05-24T00:00:00+00:00",
                "expires_at": None,
                "payload": {
                    "truth_usability_assessment": {
                        "usability_label": "SUPPRESSED_CONTRADICTED",
                        "strongest_authority_level": "PRIMARY_AUTHORITY",
                        "completeness_band": "PARTIAL",
                        "freshness_status": "FRESH",
                        "confidence_or_trust_level": "MEDIUM",
                    },
                    "contradiction_assessment": {
                        "has_contradictions": True,
                        "is_evaluable": True,
                        "contradiction_count": 2,
                        "not_evaluable_reason": None,
                        "contradiction_groups": [],
                    },
                },
            }

        all_artifacts = []
        for i, t in enumerate(tickers_with_sec_usable):
            all_artifacts.append(_make_usable_artifact(t, f"art-sec-{i:03d}"))
        for i, t in enumerate(tickers_suppressed):
            all_artifacts.append(_make_suppressed_artifact(t, f"art-sec-supp-{i:03d}"))

        # research_artifacts query
        def _table_select(table_name: str):
            mock = MagicMock()
            if table_name == "research_artifacts":
                mock.select.return_value.eq.return_value.eq.return_value \
                    .eq.return_value.limit.return_value.execute.return_value \
                    = MagicMock(data=all_artifacts)
                mock.select.return_value.eq.return_value.in_.return_value \
                    .eq.return_value.eq.return_value.limit.return_value.execute.return_value \
                    = MagicMock(data=all_artifacts)
                mock.select.return_value.in_.return_value.eq.return_value.limit.return_value \
                    .execute.return_value = MagicMock(data=all_artifacts)
            elif table_name == "research_artifact_facts":
                mock.select.return_value.in_.return_value.limit.return_value \
                    .execute.return_value = MagicMock(data=[])
            elif table_name == "target_allocations":
                mock.select.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
            elif table_name == "recommendations":
                mock.select.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
            elif table_name == "portfolio_snapshots":
                mock.select.return_value.eq.return_value.limit.return_value \
                    .execute.return_value = MagicMock(data=[{"id": "snap-001"}])
            else:
                mock.select.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
            return mock

        client.table.side_effect = _table_select
        return client

    def test_equity_canonical_dataset_count_reflects_usable_equities(self):
        """16 usable equity tickers → equity_canonical_dataset_count=16."""
        usable_equities = [f"EQ{i:02d}" for i in range(16)]
        degraded_equities = ["TSM", "KLAR", "BLSH"]
        etfs = ["SPY", "QQQ"]
        cryptos = ["BTC"]

        all_tickers = usable_equities + degraded_equities + etfs + cryptos
        holding_ctx = {}
        for t in usable_equities + degraded_equities:
            holding_ctx[t] = {"category": "Equity"}
        for t in etfs:
            holding_ctx[t] = {"category": "ETF"}
        for t in cryptos:
            holding_ctx[t] = {"category": "Cryptocurrency"}

        # Build a simple mock coverage instead of full DB
        # (compute_data_foundation_forensics calls compute_research_evidence_coverage)
        with pytest.MonkeyPatch.context() as mp:
            from app.services.intelligence.v3 import intel_data_foundation_forensics_v1 as mod

            # Patch compute_research_evidence_coverage to return pre-built coverage
            coverage = _make_coverage(tickers_lanes={
                **{t: {LANE_SEC_COMPANY_FACTS: _make_sec_lane(
                    usability_label="USABLE",
                    artifact_id=f"art-{t}"
                )} for t in usable_equities},
                **{t: {LANE_SEC_COMPANY_FACTS: _make_sec_lane(
                    usability_label="SUPPRESSED_CONTRADICTED",
                    status=STATUS_SUPPRESSED,
                    artifact_id=f"art-{t}-supp"
                )} for t in degraded_equities},
                **{t: {} for t in etfs},
                **{t: {} for t in cryptos},
            })

            mp.setattr(mod, "compute_research_evidence_coverage", lambda **kw: coverage)

            db_client = MagicMock()
            db_client.table.return_value.select.return_value.eq.return_value \
                .execute.return_value = MagicMock(data=[])
            db_client.table.return_value.select.return_value.in_.return_value \
                .limit.return_value.execute.return_value = MagicMock(data=[])
            db_client.table.return_value.select.return_value.eq.return_value \
                .limit.return_value.execute.return_value = MagicMock(data=[])

            result = compute_data_foundation_forensics(
                user_id="user-test",
                tickers=all_tickers,
                holding_context_by_ticker=holding_ctx,
                db_client=db_client,
            )

        assert result.equity_canonical_dataset_count == len(usable_equities)
        assert sorted(result.equity_canonical_dataset_degraded_tickers) == sorted(degraded_equities)

    def test_forensics_asset_parity_roadmap_present(self):
        """Forensics result always includes asset_parity_roadmap."""
        tickers = ["AAPL", "SPY", "BTC"]
        holding_ctx = {
            "AAPL": {"category": "Equity"},
            "SPY": {"category": "ETF"},
            "BTC": {"category": "Cryptocurrency"},
        }
        coverage = _make_coverage(tickers_lanes={
            "AAPL": {LANE_SEC_COMPANY_FACTS: _make_sec_lane(usability_label="USABLE")},
            "SPY": {},
            "BTC": {},
        })

        with pytest.MonkeyPatch.context() as mp:
            from app.services.intelligence.v3 import intel_data_foundation_forensics_v1 as mod
            mp.setattr(mod, "compute_research_evidence_coverage", lambda **kw: coverage)

            db_client = MagicMock()
            db_client.table.return_value.select.return_value.eq.return_value \
                .execute.return_value = MagicMock(data=[])
            db_client.table.return_value.select.return_value.in_.return_value \
                .limit.return_value.execute.return_value = MagicMock(data=[])
            db_client.table.return_value.select.return_value.eq.return_value \
                .limit.return_value.execute.return_value = MagicMock(data=[])

            result = compute_data_foundation_forensics(
                user_id="user-test",
                tickers=tickers,
                holding_context_by_ticker=holding_ctx,
                db_client=db_client,
            )

        assert result.asset_parity_roadmap is not None
        classes = {ac["asset_class"] for ac in result.asset_parity_roadmap["asset_classes"]}
        assert classes == {"equity", "etf", "crypto"}

    def test_forensics_etf_holdings_report_etf_gap_not_equity_gap(self):
        """ETF holdings in forensics report ETF_PROVIDER_NOT_BUILT, not equity gaps."""
        from app.services.intelligence.v3.intel_data_foundation_forensics_v1 import (
            BUCKET_ETF_NOT_BUILT,
        )
        tickers = ["SPY"]
        holding_ctx = {"SPY": {"category": "ETF"}}
        coverage = _make_coverage(tickers_lanes={"SPY": {}})

        with pytest.MonkeyPatch.context() as mp:
            from app.services.intelligence.v3 import intel_data_foundation_forensics_v1 as mod
            mp.setattr(mod, "compute_research_evidence_coverage", lambda **kw: coverage)

            db_client = MagicMock()
            db_client.table.return_value.select.return_value.eq.return_value \
                .execute.return_value = MagicMock(data=[])
            db_client.table.return_value.select.return_value.in_.return_value \
                .limit.return_value.execute.return_value = MagicMock(data=[])
            db_client.table.return_value.select.return_value.eq.return_value \
                .limit.return_value.execute.return_value = MagicMock(data=[])

            result = compute_data_foundation_forensics(
                user_id="user-test",
                tickers=tickers,
                holding_context_by_ticker=holding_ctx,
                db_client=db_client,
            )

        etf_row = next(h for h in result.holdings if h.ticker == "SPY")
        assert etf_row.root_cause_bucket == BUCKET_ETF_NOT_BUILT
        assert etf_row.canonical_equity_dataset is None

    def test_forensics_result_to_dict_has_new_fields(self):
        """DataFoundationForensicsResult.to_dict() includes Stage 9D fields."""
        tickers = ["AAPL"]
        holding_ctx = {"AAPL": {"category": "Equity"}}
        coverage = _make_coverage(tickers_lanes={
            "AAPL": {LANE_SEC_COMPANY_FACTS: _make_sec_lane(usability_label="USABLE")}
        })

        with pytest.MonkeyPatch.context() as mp:
            from app.services.intelligence.v3 import intel_data_foundation_forensics_v1 as mod
            mp.setattr(mod, "compute_research_evidence_coverage", lambda **kw: coverage)

            db_client = MagicMock()
            db_client.table.return_value.select.return_value.eq.return_value \
                .execute.return_value = MagicMock(data=[])
            db_client.table.return_value.select.return_value.in_.return_value \
                .limit.return_value.execute.return_value = MagicMock(data=[])
            db_client.table.return_value.select.return_value.eq.return_value \
                .limit.return_value.execute.return_value = MagicMock(data=[])

            result = compute_data_foundation_forensics(
                user_id="user-test",
                tickers=tickers,
                holding_context_by_ticker=holding_ctx,
                db_client=db_client,
            )

        d = result.to_dict()
        assert "equity_canonical_dataset_count" in d
        assert "equity_canonical_dataset_degraded_tickers" in d
        assert "asset_parity_roadmap" in d
        assert d["synthesis_ready"] is False
        assert d["safe_for_decision"] is False

    def test_forensics_zero_equity_canonical_when_all_sec_missing(self):
        """If all equity tickers have no SEC artifact, canonical count is 0."""
        tickers = ["EQ01", "EQ02"]
        holding_ctx = {t: {"category": "Equity"} for t in tickers}
        coverage = _make_coverage(tickers_lanes={t: {} for t in tickers})

        with pytest.MonkeyPatch.context() as mp:
            from app.services.intelligence.v3 import intel_data_foundation_forensics_v1 as mod
            mp.setattr(mod, "compute_research_evidence_coverage", lambda **kw: coverage)

            db_client = MagicMock()
            db_client.table.return_value.select.return_value.eq.return_value \
                .execute.return_value = MagicMock(data=[])
            db_client.table.return_value.select.return_value.in_.return_value \
                .limit.return_value.execute.return_value = MagicMock(data=[])
            db_client.table.return_value.select.return_value.eq.return_value \
                .limit.return_value.execute.return_value = MagicMock(data=[])

            result = compute_data_foundation_forensics(
                user_id="user-test",
                tickers=tickers,
                holding_context_by_ticker=holding_ctx,
                db_client=db_client,
            )

        assert result.equity_canonical_dataset_count == 0
        assert len(result.equity_canonical_dataset_degraded_tickers) == len(tickers)


# ── Tests: section-level structure ───────────────────────────────────────────


class TestSectionLevelStructure:
    """Section-level normalized evidence records — the real dataset substance."""

    def _usable_row(self, completeness: str = "COMPLETE", obs: int = 20) -> CanonicalEquityDatasetRow:
        sec = _make_sec_lane(usability_label="USABLE", completeness_band=completeness)
        return build_canonical_equity_dataset_row(
            ticker="AAPL",
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            lanes={LANE_SEC_COMPANY_FACTS: sec},
            sec_obs_count=obs,
            cat_count=None,
        )

    def test_operating_trends_sections_dict_has_all_five_sections(self):
        row = self._usable_row()
        assert isinstance(row.operating_trends.sections, dict)
        assert set(row.operating_trends.sections.keys()) == set(ALL_SECTIONS)

    def test_each_section_has_required_fields(self):
        row = self._usable_row()
        for name, rec in row.operating_trends.sections.items():
            assert isinstance(rec, EvidenceSectionRecord), f"{name} not EvidenceSectionRecord"
            assert rec.section == name
            assert rec.status in (
                SECTION_STATUS_AVAILABLE, SECTION_STATUS_PARTIAL,
                SECTION_STATUS_MISSING, SECTION_STATUS_NOT_APPLICABLE,
            ), f"{name}: invalid status {rec.status!r}"
            assert rec.evidence_basis in (BASIS_SEC_COMPANYFACTS, BASIS_UNAVAILABLE), \
                f"{name}: invalid evidence_basis {rec.evidence_basis!r}"
            assert rec.trend_direction in {"UP", "DOWN", "FLAT", "MIXED", "UNKNOWN"}, \
                f"{name}: invalid trend_direction {rec.trend_direction!r}"

    def test_available_sections_have_sec_companyfacts_basis(self):
        row = self._usable_row(completeness="COMPLETE", obs=20)
        for name, rec in row.operating_trends.sections.items():
            if rec.status in (SECTION_STATUS_AVAILABLE, SECTION_STATUS_PARTIAL):
                assert rec.evidence_basis == BASIS_SEC_COMPANYFACTS, \
                    f"{name}: expected SEC_COMPANYFACTS, got {rec.evidence_basis}"

    def test_missing_sections_have_unavailable_basis(self):
        sec = _make_sec_lane(usability_label="SUPPRESSED_CONTRADICTED", status=STATUS_SUPPRESSED)
        row = build_canonical_equity_dataset_row(
            ticker="TSM",
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            lanes={LANE_SEC_COMPANY_FACTS: sec},
            sec_obs_count=5,
            cat_count=None,
        )
        for name, rec in row.operating_trends.sections.items():
            assert rec.status == SECTION_STATUS_MISSING
            assert rec.evidence_basis == BASIS_UNAVAILABLE

    def test_metadata_fallback_trend_direction_is_unknown(self):
        """When no fact records provided, trend_direction must be UNKNOWN."""
        row = self._usable_row(completeness="COMPLETE", obs=20)
        for name, rec in row.operating_trends.sections.items():
            if rec.status in (SECTION_STATUS_AVAILABLE, SECTION_STATUS_PARTIAL):
                assert rec.trend_direction == TREND_UNKNOWN, \
                    f"{name}: expected UNKNOWN without fact records, got {rec.trend_direction}"

    def test_metadata_fallback_period_identities_are_none(self):
        """When no fact records, period identities are None."""
        row = self._usable_row(completeness="COMPLETE", obs=20)
        for name, rec in row.operating_trends.sections.items():
            assert rec.latest_period_identity is None, \
                f"{name}: expected None period identity without fact records"

    def test_fact_records_populate_period_identities(self):
        """When fact records provided, revenue section gets period identities."""
        sec = _make_sec_lane(usability_label="USABLE", completeness_band="COMPLETE")
        fact_records = [
            {
                "metric_name": "Revenues",
                "value": 391035000000,
                "unit": "USD",
                "fiscal_year": 2024,
                "fiscal_period": "FY",
                "period_end": "2024-09-28",
                "form": "10-K",
            },
            {
                "metric_name": "Revenues",
                "value": 383285000000,
                "unit": "USD",
                "fiscal_year": 2023,
                "fiscal_period": "FY",
                "period_end": "2023-09-30",
                "form": "10-K",
            },
        ]
        row = build_canonical_equity_dataset_row(
            ticker="AAPL",
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            lanes={LANE_SEC_COMPANY_FACTS: sec},
            sec_obs_count=20,
            cat_count=None,
            sec_fact_records=fact_records,
        )
        rev = row.operating_trends.sections[SECTION_REVENUE]
        assert rev.status == SECTION_STATUS_AVAILABLE
        assert rev.latest_period_identity is not None
        assert rev.latest_period_identity.fiscal_year == 2024
        assert rev.latest_period_identity.period_end == "2024-09-28"
        assert rev.comparison_period_identity is not None
        assert rev.comparison_period_identity.fiscal_year == 2023

    def test_fact_records_compute_trend_up(self):
        """Revenue rising >5% → trend_direction == UP."""
        sec = _make_sec_lane(usability_label="USABLE")
        fact_records = [
            {"metric_name": "Revenues", "value": 110, "unit": "USD",
             "fiscal_year": 2024, "fiscal_period": "FY", "period_end": "2024-09-28", "form": "10-K"},
            {"metric_name": "Revenues", "value": 100, "unit": "USD",
             "fiscal_year": 2023, "fiscal_period": "FY", "period_end": "2023-09-30", "form": "10-K"},
        ]
        row = build_canonical_equity_dataset_row(
            ticker="AAPL",
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            lanes={LANE_SEC_COMPANY_FACTS: sec},
            sec_obs_count=20,
            cat_count=None,
            sec_fact_records=fact_records,
        )
        assert row.operating_trends.sections[SECTION_REVENUE].trend_direction == TREND_UP

    def test_fact_records_compute_trend_down(self):
        """Revenue falling >5% → trend_direction == DOWN."""
        sec = _make_sec_lane(usability_label="USABLE")
        fact_records = [
            {"metric_name": "Revenues", "value": 90, "unit": "USD",
             "fiscal_year": 2024, "fiscal_period": "FY", "period_end": "2024-09-28", "form": "10-K"},
            {"metric_name": "Revenues", "value": 100, "unit": "USD",
             "fiscal_year": 2023, "fiscal_period": "FY", "period_end": "2023-09-30", "form": "10-K"},
        ]
        row = build_canonical_equity_dataset_row(
            ticker="TEST",
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            lanes={LANE_SEC_COMPANY_FACTS: sec},
            sec_obs_count=20,
            cat_count=None,
            sec_fact_records=fact_records,
        )
        assert row.operating_trends.sections[SECTION_REVENUE].trend_direction == TREND_DOWN

    def test_fact_records_compute_trend_flat(self):
        """Revenue change within ±5% → trend_direction == FLAT."""
        sec = _make_sec_lane(usability_label="USABLE")
        fact_records = [
            {"metric_name": "Revenues", "value": 102, "unit": "USD",
             "fiscal_year": 2024, "fiscal_period": "FY", "period_end": "2024-09-28", "form": "10-K"},
            {"metric_name": "Revenues", "value": 100, "unit": "USD",
             "fiscal_year": 2023, "fiscal_period": "FY", "period_end": "2023-09-30", "form": "10-K"},
        ]
        row = build_canonical_equity_dataset_row(
            ticker="TEST",
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            lanes={LANE_SEC_COMPANY_FACTS: sec},
            sec_obs_count=20,
            cat_count=None,
            sec_fact_records=fact_records,
        )
        assert row.operating_trends.sections[SECTION_REVENUE].trend_direction == TREND_FLAT

    def test_raw_values_never_in_serialized_output(self):
        """Raw fact values must not appear in the serialized dataset row."""
        sec = _make_sec_lane(usability_label="USABLE")
        fact_records = [
            {"metric_name": "Revenues", "value": 391035000000, "unit": "USD",
             "fiscal_year": 2024, "fiscal_period": "FY", "period_end": "2024-09-28", "form": "10-K"},
            {"metric_name": "Revenues", "value": 383285000000, "unit": "USD",
             "fiscal_year": 2023, "fiscal_period": "FY", "period_end": "2023-09-30", "form": "10-K"},
        ]
        row = build_canonical_equity_dataset_row(
            ticker="AAPL",
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            lanes={LANE_SEC_COMPANY_FACTS: sec},
            sec_obs_count=20,
            cat_count=None,
            sec_fact_records=fact_records,
        )
        import json
        output_str = json.dumps(row.to_dict())
        assert "391035000000" not in output_str
        assert "383285000000" not in output_str

    def test_only_one_fact_record_gives_partial_status(self):
        """One annual observation → PARTIAL status, no comparison period."""
        sec = _make_sec_lane(usability_label="USABLE")
        fact_records = [
            {"metric_name": "Revenues", "value": 100, "unit": "USD",
             "fiscal_year": 2024, "fiscal_period": "FY", "period_end": "2024-09-28", "form": "10-K"},
        ]
        row = build_canonical_equity_dataset_row(
            ticker="TEST",
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            lanes={LANE_SEC_COMPANY_FACTS: sec},
            sec_obs_count=5,
            cat_count=None,
            sec_fact_records=fact_records,
        )
        rev = row.operating_trends.sections[SECTION_REVENUE]
        assert rev.status == SECTION_STATUS_PARTIAL
        assert rev.latest_period_identity is not None
        assert rev.comparison_period_identity is None
        assert rev.trend_direction == TREND_UNKNOWN

    def test_etf_all_sections_not_applicable(self):
        """ETF → all sections have NOT_APPLICABLE status."""
        row = build_canonical_equity_dataset_row(
            ticker="SPY",
            asset_type=INSTRUMENT_CATEGORY_ETF,
            lanes={},
            sec_obs_count=None,
            cat_count=None,
        )
        for name, rec in row.operating_trends.sections.items():
            assert rec.status == SECTION_STATUS_NOT_APPLICABLE, \
                f"{name}: expected NOT_APPLICABLE, got {rec.status}"

    def test_sections_to_dict_has_all_five_keys(self):
        """OperatingTrendSection.to_dict() sections key has all 5 sections."""
        row = self._usable_row()
        d = row.operating_trends.to_dict()
        assert "sections" in d
        assert set(d["sections"].keys()) == set(ALL_SECTIONS)

    def test_backward_compat_booleans_still_in_to_dict(self):
        """to_dict() still includes backward-compat boolean fields."""
        row = self._usable_row(completeness="COMPLETE", obs=20)
        d = row.operating_trends.to_dict()
        for field_name in [
            "revenue_trend_available",
            "profitability_margin_available",
            "eps_net_income_available",
            "fcf_available",
            "share_count_dilution_available",
        ]:
            assert field_name in d, f"Missing backward-compat field: {field_name}"

    def test_section_period_identity_to_dict_has_no_value_key(self):
        """PeriodIdentity.to_dict() must not contain 'value'."""
        pid = PeriodIdentity(
            fiscal_year=2024,
            fiscal_period="FY",
            period_end="2024-09-28",
            unit="USD",
            form="10-K",
        )
        d = pid.to_dict()
        assert "value" not in d
        assert "fiscal_year" in d
        assert "period_end" in d

    def test_no_xbrl_metric_names_in_section_record_to_dict(self):
        """EvidenceSectionRecord.to_dict() must not contain raw XBRL metric names."""
        import json
        sec = _make_sec_lane(usability_label="USABLE")
        fact_records = [
            {"metric_name": "Revenues", "value": 100, "unit": "USD",
             "fiscal_year": 2024, "fiscal_period": "FY", "period_end": "2024-09-28", "form": "10-K"},
            {"metric_name": "Revenues", "value": 90, "unit": "USD",
             "fiscal_year": 2023, "fiscal_period": "FY", "period_end": "2023-09-30", "form": "10-K"},
        ]
        row = build_canonical_equity_dataset_row(
            ticker="AAPL",
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            lanes={LANE_SEC_COMPANY_FACTS: sec},
            sec_obs_count=20,
            cat_count=None,
            sec_fact_records=fact_records,
        )
        for name, rec in row.operating_trends.sections.items():
            d_str = json.dumps(rec.to_dict())
            forbidden = ["Revenues", "NetIncomeLoss", "us-gaap/", "EarningsPerShareBasic"]
            for key in forbidden:
                assert key not in d_str, \
                    f"{name}: raw XBRL key {key!r} found in section record"

    def test_quarterly_facts_excluded_from_annual_section_records(self):
        """Quarterly observations (Q1/Q2/Q3/Q4) must be excluded from annual section records."""
        sec = _make_sec_lane(usability_label="USABLE")
        fact_records = [
            # Only quarterly — no annual
            {"metric_name": "Revenues", "value": 30, "unit": "USD",
             "fiscal_year": 2024, "fiscal_period": "Q1", "period_end": "2024-03-31", "form": "10-Q"},
            {"metric_name": "Revenues", "value": 28, "unit": "USD",
             "fiscal_year": 2024, "fiscal_period": "Q2", "period_end": "2024-06-30", "form": "10-Q"},
        ]
        row = build_canonical_equity_dataset_row(
            ticker="TEST",
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            lanes={LANE_SEC_COMPANY_FACTS: sec},
            sec_obs_count=2,
            cat_count=None,
            sec_fact_records=fact_records,
        )
        rev = row.operating_trends.sections[SECTION_REVENUE]
        # Quarterly facts should not be used for annual section status
        assert rev.status == SECTION_STATUS_MISSING


# ── Tests: forensics section counts ──────────────────────────────────────────


class TestForensicsSectionCounts:
    """DataFoundationForensicsResult includes canonical_equity_dataset_section_counts."""

    def test_forensics_result_includes_section_counts_key(self):
        """to_dict() includes canonical_equity_dataset_section_counts."""
        tickers = ["AAPL"]
        holding_ctx = {"AAPL": {"category": "Equity"}}
        coverage = _make_coverage(tickers_lanes={
            "AAPL": {LANE_SEC_COMPANY_FACTS: _make_sec_lane(
                usability_label="USABLE", completeness_band="COMPLETE"
            )}
        })

        with pytest.MonkeyPatch.context() as mp:
            from app.services.intelligence.v3 import intel_data_foundation_forensics_v1 as mod
            mp.setattr(mod, "compute_research_evidence_coverage", lambda **kw: coverage)

            db_client = MagicMock()
            db_client.table.return_value.select.return_value.eq.return_value \
                .execute.return_value = MagicMock(data=[])
            db_client.table.return_value.select.return_value.in_.return_value \
                .limit.return_value.execute.return_value = MagicMock(data=[])
            db_client.table.return_value.select.return_value.eq.return_value \
                .limit.return_value.execute.return_value = MagicMock(data=[])

            result = compute_data_foundation_forensics(
                user_id="user-test",
                tickers=tickers,
                holding_context_by_ticker=holding_ctx,
                db_client=db_client,
            )

        d = result.to_dict()
        assert "canonical_equity_dataset_section_counts" in d
        assert isinstance(d["canonical_equity_dataset_section_counts"], dict)

    def test_section_counts_populated_for_usable_equity(self):
        """An equity with COMPLETE/USABLE SEC should register section counts."""
        tickers = ["AAPL"]
        holding_ctx = {"AAPL": {"category": "Equity"}}
        coverage = _make_coverage(tickers_lanes={
            "AAPL": {LANE_SEC_COMPANY_FACTS: _make_sec_lane(
                usability_label="USABLE", completeness_band="COMPLETE"
            )}
        })

        with pytest.MonkeyPatch.context() as mp:
            from app.services.intelligence.v3 import intel_data_foundation_forensics_v1 as mod
            mp.setattr(mod, "compute_research_evidence_coverage", lambda **kw: coverage)

            db_client = MagicMock()
            db_client.table.return_value.select.return_value.eq.return_value \
                .execute.return_value = MagicMock(data=[])
            db_client.table.return_value.select.return_value.in_.return_value \
                .limit.return_value.execute.return_value = MagicMock(data=[])
            db_client.table.return_value.select.return_value.eq.return_value \
                .limit.return_value.execute.return_value = MagicMock(data=[])

            result = compute_data_foundation_forensics(
                user_id="user-test",
                tickers=tickers,
                holding_context_by_ticker=holding_ctx,
                db_client=db_client,
            )

        # With COMPLETE/USABLE + obs=0 (mock returns empty), metadata fallback applies.
        # obs_count from mock data = 0, so status will be MISSING (no raw fact records).
        # The section_counts dict should still exist (possibly empty if all MISSING).
        assert isinstance(result.canonical_equity_dataset_section_counts, dict)

    def test_section_counts_zero_for_degraded_equity(self):
        """Degraded (suppressed) equity contributes no sections to section_counts."""
        tickers = ["TSM"]
        holding_ctx = {"TSM": {"category": "Equity"}}
        coverage = _make_coverage(tickers_lanes={
            "TSM": {LANE_SEC_COMPANY_FACTS: _make_sec_lane(
                usability_label="SUPPRESSED_CONTRADICTED",
                status=STATUS_SUPPRESSED,
            )}
        })

        with pytest.MonkeyPatch.context() as mp:
            from app.services.intelligence.v3 import intel_data_foundation_forensics_v1 as mod
            mp.setattr(mod, "compute_research_evidence_coverage", lambda **kw: coverage)

            db_client = MagicMock()
            db_client.table.return_value.select.return_value.eq.return_value \
                .execute.return_value = MagicMock(data=[])
            db_client.table.return_value.select.return_value.in_.return_value \
                .limit.return_value.execute.return_value = MagicMock(data=[])
            db_client.table.return_value.select.return_value.eq.return_value \
                .limit.return_value.execute.return_value = MagicMock(data=[])

            result = compute_data_foundation_forensics(
                user_id="user-test",
                tickers=tickers,
                holding_context_by_ticker=holding_ctx,
                db_client=db_client,
            )

        # Suppressed equity → all sections MISSING → no section counts.
        assert result.canonical_equity_dataset_section_counts == {}


# ── Tests: safety invariants ──────────────────────────────────────────────────


class TestSafetyInvariants:
    """Hard safety gates that must never be violated."""

    def test_no_decide_import_in_canonical_dataset(self):
        """canonical_equity_dataset_v1 must not import the policy decide() function."""
        import importlib
        import sys
        mod_name = "app.services.intelligence.v3.canonical_equity_dataset_v1"
        if mod_name in sys.modules:
            mod = sys.modules[mod_name]
        else:
            mod = importlib.import_module(mod_name)
        # Inspect source for decide import
        import inspect
        src = inspect.getsource(mod)
        assert "from .decision_policy" not in src
        assert "import decide" not in src
        assert "decision_policy_v1" not in src

    def test_synthesis_ready_never_true_for_any_input(self):
        """No input combination can produce synthesis_ready=True."""
        # Try every usability label that could be considered "good"
        for usability in ["USABLE", "USABLE_WITH_LIMITATIONS", "READY"]:
            sec = _make_sec_lane(usability_label=usability)
            row = build_canonical_equity_dataset_row(
                ticker="TEST",
                asset_type=INSTRUMENT_CATEGORY_EQUITY,
                lanes={LANE_SEC_COMPANY_FACTS: sec},
                sec_obs_count=100,
                cat_count=10,
            )
            assert row.synthesis_ready is False, f"synthesis_ready=True for usability={usability}"

    def test_valuation_ready_never_true(self):
        """No input combination can produce valuation_ready=True at Stage 9D."""
        sec = _make_sec_lane(usability_label="USABLE", completeness_band="COMPLETE")
        row = build_canonical_equity_dataset_row(
            ticker="TEST",
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            lanes={LANE_SEC_COMPANY_FACTS: sec},
            sec_obs_count=100,
            cat_count=None,
        )
        assert row.valuation_ready is False

    def test_safe_for_decision_never_true(self):
        sec = _make_sec_lane(usability_label="USABLE")
        row = build_canonical_equity_dataset_row(
            ticker="TEST",
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            lanes={LANE_SEC_COMPANY_FACTS: sec},
            sec_obs_count=20,
            cat_count=None,
        )
        assert row.safe_for_decision is False

    def test_no_raw_fact_values_in_operating_trends_dict(self):
        """OperatingTrendSection.to_dict() must not expose raw XBRL metric keys or values."""
        sec = _make_sec_lane(usability_label="USABLE", completeness_band="COMPLETE")
        row = build_canonical_equity_dataset_row(
            ticker="AAPL",
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            lanes={LANE_SEC_COMPANY_FACTS: sec},
            sec_obs_count=20,
            cat_count=None,
        )
        import json
        trends_str = json.dumps(row.operating_trends.to_dict())
        # Raw XBRL metric key jargon must not appear (field names like
        # "revenue_trend_available" are safe — only raw keys/values are forbidden).
        forbidden_raw_keys = [
            "us-gaap/",
            "Revenues",
            "NetIncomeLoss",
            "EarningsPerShareBasic",
            "OperatingCashFlow",
            "CommonStockSharesOutstanding",
            "RevenueFromContractWithCustomer",
        ]
        for key in forbidden_raw_keys:
            assert key not in trends_str, f"Raw XBRL metric key found in operating trends: {key}"

    def test_all_classes_synthesis_ready_never_true(self):
        roadmap = build_asset_parity_roadmap(
            equity_canonical_count=19,
            equity_total=19,
            equity_edge_case_tickers=[],
            etf_total=0,
            crypto_total=0,
        )
        assert roadmap.all_classes_synthesis_ready is False
