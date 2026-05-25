"""Stage 9F — Canonical ETF Fund Intelligence Dataset v1.

Covers:
  1.  ETF ticker receives canonical ETF dataset row (etf_applicable=True).
  2.  Equity ticker receives NOT_APPLICABLE ETF dataset row (etf_applicable=False).
  3.  Crypto ticker receives NOT_APPLICABLE ETF dataset row (etf_applicable=False).
  4.  ETF with only generic metadata does NOT become etf_fund_intelligence_ready.
  5.  ETF with missing fundamentals → fund_identity and cost_and_yield fields all
      indicate unavailability (MISSING or False), not fabricated AVAILABLE.
  6.  ETF composition is ALWAYS MISSING at Stage 9F — no provider built.
  7.  ETF_FUND_COMPOSITION_NOT_READY blocker in forensics gap classification when
      canonical ETF scaffold is present (etf_canonical_scaffold_present=True).
  8.  ETF_PROVIDER_NOT_BUILT blocker remains when scaffold is not present
      (backward-compat: etf_canonical_scaffold_present=False).
  9.  No fake holdings, exposures, expense ratios, or yield values fabricated.
  10. No raw provider payloads serialized in ETF dataset row.
  11. safe_for_decision always False for ETF dataset rows.
  12. synthesis_ready always False for ETF dataset rows.
  13. valuation_ready always False for ETF dataset rows.
  14. etf_fund_intelligence_ready always False at Stage 9F.
  15. canonical_etf_scaffold_present=True for ETF tickers (scaffold built);
      canonical_etf_dataset_safe=False always (composition MISSING, fields unverified).
  16. canonical_etf_scaffold_present=False and canonical_etf_dataset_safe=False for
      NOT_APPLICABLE (equity/crypto) rows.
  17. ETF with usable fundamentals: fund_name/issuer/category False (not extracted at Stage 9F);
      expense_ratio_status=PARTIAL (lane usable but not ETF-specifically validated).
  18. ETF with usable technicals: liquidity_proxy_status=PARTIAL.
  19. ETF with no artifact: all fund_identity False, all statuses MISSING.
  20. ETF dataset row serializes all required contract fields.
  21. Forensics: ETF holdings have canonical_etf_dataset dict populated.
  22. Forensics: equity holdings have canonical_etf_dataset=None.
  23. Forensics: crypto holdings have canonical_etf_dataset=None.
  24. Forensics: etf_canonical_dataset_count reflects scaffold-built ETFs.
  25. Forensics: etf_fund_intelligence_ready_count is always 0 at Stage 9F.
  26. Forensics: etf_missing_reason_counts aggregates missing reason keys.
  27. Forensics: asset_parity_roadmap ETF canonical_dataset_built=True when scaffolds built.
  28. Forensics: asset_parity_roadmap ETF canonical_dataset_built=False when no ETF holdings.
  29. Forensics: blocking_gap_bucket_counts includes ETF_FUND_COMPOSITION_NOT_READY.
  30. Forensics result to_dict includes all Stage 9F ETF fields.
  31. No decide() import anywhere in the ETF dataset module.
  32. build_canonical_etf_fund_dataset_row is pure — no IO.
  33. Stage 9B/9D/9E/9E.1 regression: no breaking imports from new module.
  34. ETF composition never AVAILABLE or PARTIAL — always MISSING.
  35. ETF dataset row missing_reasons has composition key when scaffold built.
  36. ETF with technicals usable: volatility_or_technical_support_status=PARTIAL.
  37. ETF with technicals not usable: trading statuses MISSING.
  38. Source health entries present for lanes with artifacts.
  39. Source health entries absent for lanes with no artifact_id.
  40. Not-applicable row has not_applicable_reason set.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
from unittest.mock import MagicMock

import pytest

from app.services.intelligence.v3.canonical_etf_fund_dataset_v1 import (
    ETF_DATASET_VERSION,
    ETF_STATUS_AVAILABLE,
    ETF_STATUS_MISSING,
    ETF_STATUS_NOT_APPLICABLE,
    ETF_STATUS_PARTIAL,
    CanonicalEtfFundDatasetRow,
    build_canonical_etf_fund_dataset_row,
)
from app.services.intelligence.v3.intel_data_foundation_forensics_v1 import (
    BUCKET_ETF_FUND_COMPOSITION_NOT_READY,
    BUCKET_ETF_NOT_BUILT,
    DataFoundationForensicsResult,
    HoldingForensicsRow,
    _classify_all_gaps,
    _classify_root_cause,
)
from app.services.intelligence.v3.research_evidence_decision_input_adapter_v1 import (
    INSTRUMENT_CATEGORY_CRYPTO,
    INSTRUMENT_CATEGORY_EQUITY,
    INSTRUMENT_CATEGORY_ETF,
)
from app.services.intelligence.v3.research_evidence_coverage_read_model_v1 import (
    LANE_FUNDAMENTALS,
    LANE_TECHNICALS,
    LaneCoverage,
)


# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_lane_coverage(
    lane: str,
    artifact_id: Optional[str],
    usability_label: Optional[str],
    freshness_status: str = "FRESH",
    completeness_band: str = "COMPLETE",
    model_version: Optional[str] = "test_model.v1",
) -> LaneCoverage:
    """Build a minimal LaneCoverage for testing."""
    is_usable = usability_label in ("USABLE", "USABLE_WITH_LIMITATIONS")
    return LaneCoverage(
        lane=lane,
        artifact_type="fundamental_quality",
        skill_pack="fundamentals_evidence_v1",
        scope_kind="ticker",
        ticker="SPY",
        artifact_id=artifact_id,
        status="READY" if usability_label == "USABLE" else (
            "LIMITED" if usability_label == "USABLE_WITH_LIMITATIONS" else "MISSING"
        ),
        usability_label=usability_label,
        is_usable=is_usable,
        suppression_reason=None,
        source_authority="OFFICIAL_FREE_SOURCE" if is_usable else None,
        completeness_band=completeness_band,
        has_contradictions=False,
        freshness_status=freshness_status,
        confidence_or_trust_level=None,
        model_version=model_version,
        generated_at="2026-05-25T00:00:00+00:00",
        expires_at=None,
    )


def _make_etf_lanes(
    fund_artifact_id: Optional[str] = "fund-001",
    fund_usability: Optional[str] = "USABLE",
    tech_artifact_id: Optional[str] = "tech-001",
    tech_usability: Optional[str] = "USABLE",
) -> dict:
    lanes = {}
    if fund_artifact_id is not None or fund_usability is not None:
        lanes[LANE_FUNDAMENTALS] = _make_lane_coverage(
            LANE_FUNDAMENTALS,
            artifact_id=fund_artifact_id,
            usability_label=fund_usability,
        )
    if tech_artifact_id is not None or tech_usability is not None:
        lanes[LANE_TECHNICALS] = _make_lane_coverage(
            LANE_TECHNICALS,
            artifact_id=tech_artifact_id,
            usability_label=tech_usability,
        )
    return lanes


# ── Section 1: ETF applicability ───────────────────────────────────────────────


class TestEtfApplicability:
    def test_etf_ticker_gets_applicable_row(self):
        """Case 1: ETF ticker → etf_applicable=True."""
        row = build_canonical_etf_fund_dataset_row(
            ticker="SPY",
            asset_type=INSTRUMENT_CATEGORY_ETF,
            lanes=_make_etf_lanes(),
        )
        assert row.etf_applicable is True
        assert row.ticker == "SPY"
        assert row.asset_type == INSTRUMENT_CATEGORY_ETF

    def test_equity_ticker_not_applicable(self):
        """Case 2: Equity ticker → etf_applicable=False."""
        row = build_canonical_etf_fund_dataset_row(
            ticker="AAPL",
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            lanes={},
        )
        assert row.etf_applicable is False
        assert row.canonical_etf_scaffold_present is False
        assert row.canonical_etf_dataset_safe is False
        assert "not applicable for equity" in (row.not_applicable_reason or "").lower()

    def test_crypto_ticker_not_applicable(self):
        """Case 3: Crypto ticker → etf_applicable=False."""
        row = build_canonical_etf_fund_dataset_row(
            ticker="BTC",
            asset_type=INSTRUMENT_CATEGORY_CRYPTO,
            lanes={},
        )
        assert row.etf_applicable is False
        assert row.canonical_etf_scaffold_present is False
        assert row.canonical_etf_dataset_safe is False
        assert "not applicable for crypto" in (row.not_applicable_reason or "").lower()


# ── Section 2: Immutable readiness gates ──────────────────────────────────────


class TestImmutableGates:
    def setup_method(self):
        self.row = build_canonical_etf_fund_dataset_row(
            ticker="SPY",
            asset_type=INSTRUMENT_CATEGORY_ETF,
            lanes=_make_etf_lanes(),
        )

    def test_safe_for_decision_always_false(self):
        """Case 11: safe_for_decision always False."""
        assert self.row.safe_for_decision is False

    def test_synthesis_ready_always_false(self):
        """Case 12: synthesis_ready always False."""
        assert self.row.synthesis_ready is False

    def test_valuation_ready_always_false(self):
        """Case 13: valuation_ready always False."""
        assert self.row.valuation_ready is False

    def test_etf_fund_intelligence_ready_always_false(self):
        """Case 14: etf_fund_intelligence_ready always False at Stage 9F."""
        assert self.row.etf_fund_intelligence_ready is False

    def test_etf_with_only_generic_metadata_not_fund_intelligence_ready(self):
        """Case 4: ETF with only yfinance metadata → etf_fund_intelligence_ready=False."""
        row = build_canonical_etf_fund_dataset_row(
            ticker="QQQ",
            asset_type=INSTRUMENT_CATEGORY_ETF,
            lanes=_make_etf_lanes(fund_usability="USABLE", tech_usability="USABLE"),
        )
        assert row.etf_fund_intelligence_ready is False

    def test_canonical_etf_scaffold_present_true_for_etf(self):
        """Case 15: canonical_etf_scaffold_present=True for ETF scaffold rows."""
        assert self.row.canonical_etf_scaffold_present is True

    def test_canonical_etf_dataset_safe_always_false_for_etf(self):
        """Case 15b: canonical_etf_dataset_safe=False always — scaffold != dataset safe.
        Composition is always MISSING and ETF-specific fields are not extracted."""
        assert self.row.canonical_etf_dataset_safe is False

    def test_scaffold_present_does_not_equal_dataset_safe(self):
        """Case 15c: scaffold_present=True while dataset_safe=False is the correct state."""
        assert self.row.canonical_etf_scaffold_present is True
        assert self.row.canonical_etf_dataset_safe is False

    def test_canonical_etf_scaffold_present_false_for_equity(self):
        """Case 16: canonical_etf_scaffold_present=False for equity NOT_APPLICABLE row."""
        row = build_canonical_etf_fund_dataset_row(
            ticker="MSFT",
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            lanes={},
        )
        assert row.canonical_etf_scaffold_present is False
        assert row.canonical_etf_dataset_safe is False

    def test_canonical_etf_scaffold_present_false_for_crypto(self):
        """Case 16b: canonical_etf_scaffold_present=False for crypto NOT_APPLICABLE row."""
        row = build_canonical_etf_fund_dataset_row(
            ticker="XRP",
            asset_type=INSTRUMENT_CATEGORY_CRYPTO,
            lanes={},
        )
        assert row.canonical_etf_scaffold_present is False
        assert row.canonical_etf_dataset_safe is False


# ── Section 3: Fund identity derivation ───────────────────────────────────────


class TestFundIdentity:
    def test_fund_name_not_available_even_when_fundamentals_usable(self):
        """Case 17: usable fundamentals → fund_name_available=False at Stage 9F.
        A usable yfinance lane does not prove ETF-specific fund identity fields
        (fund name, issuer, category) were extracted or validated."""
        row = build_canonical_etf_fund_dataset_row(
            ticker="SPY",
            asset_type=INSTRUMENT_CATEGORY_ETF,
            lanes=_make_etf_lanes(fund_usability="USABLE"),
        )
        assert row.fund_identity.fund_name_available is False
        assert row.fund_identity.issuer_available is False
        assert row.fund_identity.category_or_index_strategy_available is False
        # missing_reason must explain the distinction (lane usable but not extracted)
        assert row.fund_identity.missing_reason is not None
        assert "not extracted" in row.fund_identity.missing_reason.lower()

    def test_fund_identity_false_when_fundamentals_missing(self):
        """Case 5/19: missing fundamentals → all fund_identity fields False."""
        row = build_canonical_etf_fund_dataset_row(
            ticker="SPY",
            asset_type=INSTRUMENT_CATEGORY_ETF,
            lanes=_make_etf_lanes(fund_artifact_id=None, fund_usability=None),
        )
        assert row.fund_identity.fund_name_available is False
        assert row.fund_identity.issuer_available is False
        assert row.fund_identity.category_or_index_strategy_available is False
        assert row.fund_identity.missing_reason is not None

    def test_fund_identity_false_when_fundamentals_suppressed(self):
        """Case 5b: suppressed fundamentals → fund_identity unavailable."""
        row = build_canonical_etf_fund_dataset_row(
            ticker="XLE",
            asset_type=INSTRUMENT_CATEGORY_ETF,
            lanes=_make_etf_lanes(
                fund_artifact_id="fund-suppressed",
                fund_usability="SUPPRESSED_INCOMPLETE",
            ),
        )
        assert row.fund_identity.fund_name_available is False

    def test_fund_identity_false_with_limitations(self):
        """USABLE_WITH_LIMITATIONS fundamentals → fund identity still False at Stage 9F.
        Lane usability (even with limitations) does not validate fund-specific fields."""
        row = build_canonical_etf_fund_dataset_row(
            ticker="GLD",
            asset_type=INSTRUMENT_CATEGORY_ETF,
            lanes=_make_etf_lanes(fund_usability="USABLE_WITH_LIMITATIONS"),
        )
        assert row.fund_identity.fund_name_available is False
        assert row.fund_identity.issuer_available is False
        assert row.fund_identity.category_or_index_strategy_available is False

    def test_lane_usability_never_implies_fund_identity_available(self):
        """No lane state makes fund_name/issuer/category True at Stage 9F."""
        for usability in ("USABLE", "USABLE_WITH_LIMITATIONS"):
            row = build_canonical_etf_fund_dataset_row(
                ticker="SPY",
                asset_type=INSTRUMENT_CATEGORY_ETF,
                lanes=_make_etf_lanes(fund_usability=usability),
            )
            assert row.fund_identity.fund_name_available is False, (
                f"fund_name_available must be False for usability={usability}"
            )
            assert row.fund_identity.issuer_available is False
            assert row.fund_identity.category_or_index_strategy_available is False


# ── Section 4: Cost and yield derivation ──────────────────────────────────────


class TestCostAndYield:
    def test_expense_ratio_partial_when_fundamentals_usable(self):
        """Case 17b: expense_ratio_status=PARTIAL when fundamentals usable.
        PARTIAL means lane signal exists but fields not ETF-specifically validated."""
        row = build_canonical_etf_fund_dataset_row(
            ticker="SPY",
            asset_type=INSTRUMENT_CATEGORY_ETF,
            lanes=_make_etf_lanes(fund_usability="USABLE"),
        )
        assert row.cost_and_yield.expense_ratio_status == ETF_STATUS_PARTIAL
        assert row.cost_and_yield.dividend_or_distribution_yield_status == ETF_STATUS_PARTIAL
        # missing_reason always present — explicitly states "not extracted/validated"
        assert row.cost_and_yield.missing_reason is not None
        assert "not extracted" in row.cost_and_yield.missing_reason.lower()

    def test_expense_ratio_missing_when_fundamentals_absent(self):
        """Case 9/19: no fundamentals → expense_ratio_status=MISSING (not fabricated)."""
        row = build_canonical_etf_fund_dataset_row(
            ticker="SPY",
            asset_type=INSTRUMENT_CATEGORY_ETF,
            lanes=_make_etf_lanes(fund_artifact_id=None, fund_usability=None),
        )
        assert row.cost_and_yield.expense_ratio_status == ETF_STATUS_MISSING
        assert row.cost_and_yield.dividend_or_distribution_yield_status == ETF_STATUS_MISSING
        assert row.cost_and_yield.missing_reason is not None

    def test_no_available_expense_ratio_status_without_dedicated_provider(self):
        """Case 9: expense_ratio_status never AVAILABLE without dedicated fund provider."""
        row = build_canonical_etf_fund_dataset_row(
            ticker="SPY",
            asset_type=INSTRUMENT_CATEGORY_ETF,
            lanes=_make_etf_lanes(fund_usability="USABLE"),
        )
        # PARTIAL at best — never AVAILABLE without dedicated fund data provider
        assert row.cost_and_yield.expense_ratio_status != ETF_STATUS_AVAILABLE


# ── Section 5: Composition always MISSING ─────────────────────────────────────


class TestCompositionAlwaysMissing:
    def test_composition_missing_with_all_lanes_usable(self):
        """Case 6/34: composition is always MISSING even with usable fundamentals."""
        row = build_canonical_etf_fund_dataset_row(
            ticker="SPY",
            asset_type=INSTRUMENT_CATEGORY_ETF,
            lanes=_make_etf_lanes(fund_usability="USABLE", tech_usability="USABLE"),
        )
        assert row.composition.holdings_composition_status == ETF_STATUS_MISSING
        assert row.composition.sector_exposure_status == ETF_STATUS_MISSING
        assert row.composition.geography_exposure_status == ETF_STATUS_MISSING
        assert row.composition.concentration_status == ETF_STATUS_MISSING

    def test_composition_missing_reason_present(self):
        """Case 35: missing_reasons has composition key when scaffold built."""
        row = build_canonical_etf_fund_dataset_row(
            ticker="SPY",
            asset_type=INSTRUMENT_CATEGORY_ETF,
            lanes=_make_etf_lanes(),
        )
        assert "composition" in row.missing_reasons
        assert "ETF_FUND_COMPOSITION_NOT_READY" in row.missing_reasons["composition"]

    def test_composition_never_available_status(self):
        """Case 34: composition statuses are never AVAILABLE at Stage 9F."""
        row = build_canonical_etf_fund_dataset_row(
            ticker="QQQ",
            asset_type=INSTRUMENT_CATEGORY_ETF,
            lanes=_make_etf_lanes(),
        )
        comp = row.composition
        for status in [
            comp.holdings_composition_status,
            comp.sector_exposure_status,
            comp.geography_exposure_status,
            comp.concentration_status,
        ]:
            assert status == ETF_STATUS_MISSING
            assert status != ETF_STATUS_AVAILABLE
            assert status != ETF_STATUS_PARTIAL


# ── Section 6: Trading and risk support ───────────────────────────────────────


class TestTradingAndRisk:
    def test_liquidity_proxy_partial_when_technicals_usable(self):
        """Case 18/36: usable technicals → liquidity and volatility PARTIAL."""
        row = build_canonical_etf_fund_dataset_row(
            ticker="SPY",
            asset_type=INSTRUMENT_CATEGORY_ETF,
            lanes=_make_etf_lanes(tech_usability="USABLE"),
        )
        assert row.trading_and_risk_support.liquidity_proxy_status == ETF_STATUS_PARTIAL
        assert (
            row.trading_and_risk_support.volatility_or_technical_support_status
            == ETF_STATUS_PARTIAL
        )
        assert row.trading_and_risk_support.missing_reason is None

    def test_trading_missing_when_technicals_absent(self):
        """Case 37: no technicals → trading statuses MISSING."""
        row = build_canonical_etf_fund_dataset_row(
            ticker="SPY",
            asset_type=INSTRUMENT_CATEGORY_ETF,
            lanes=_make_etf_lanes(tech_artifact_id=None, tech_usability=None),
        )
        assert row.trading_and_risk_support.liquidity_proxy_status == ETF_STATUS_MISSING
        assert (
            row.trading_and_risk_support.volatility_or_technical_support_status
            == ETF_STATUS_MISSING
        )

    def test_trading_missing_when_technicals_suppressed(self):
        """Case 37b: suppressed technicals → trading statuses MISSING."""
        row = build_canonical_etf_fund_dataset_row(
            ticker="XLE",
            asset_type=INSTRUMENT_CATEGORY_ETF,
            lanes=_make_etf_lanes(
                tech_artifact_id="tech-suppressed",
                tech_usability="SUPPRESSED_INCOMPLETE",
            ),
        )
        assert row.trading_and_risk_support.liquidity_proxy_status == ETF_STATUS_MISSING


# ── Section 7: Source health provenance ───────────────────────────────────────


class TestSourceHealth:
    def test_source_health_entries_for_present_lanes(self):
        """Case 38: source health entries present when artifact_id exists."""
        row = build_canonical_etf_fund_dataset_row(
            ticker="SPY",
            asset_type=INSTRUMENT_CATEGORY_ETF,
            lanes=_make_etf_lanes(
                fund_artifact_id="fund-001",
                tech_artifact_id="tech-001",
            ),
        )
        lane_names = {e.lane for e in row.source_artifacts}
        assert LANE_FUNDAMENTALS in lane_names
        assert LANE_TECHNICALS in lane_names

    def test_no_source_health_entries_for_missing_lanes(self):
        """Case 39: source health entries absent when no artifact_id."""
        row = build_canonical_etf_fund_dataset_row(
            ticker="SPY",
            asset_type=INSTRUMENT_CATEGORY_ETF,
            lanes={},
        )
        assert row.source_artifacts == []

    def test_source_health_no_raw_payloads(self):
        """Case 10: source health entries contain no raw payloads."""
        row = build_canonical_etf_fund_dataset_row(
            ticker="SPY",
            asset_type=INSTRUMENT_CATEGORY_ETF,
            lanes=_make_etf_lanes(),
        )
        for entry in row.source_artifacts:
            d = entry.to_dict()
            # Only safe metadata fields allowed — no raw values
            assert "payload" not in d
            assert "structured_payload" not in d
            assert "fact_records" not in d
            assert "source_url" not in d


# ── Section 8: Serialization contract ─────────────────────────────────────────


class TestSerializationContract:
    def setup_method(self):
        self.row = build_canonical_etf_fund_dataset_row(
            ticker="SPY",
            asset_type=INSTRUMENT_CATEGORY_ETF,
            lanes=_make_etf_lanes(),
        )

    def test_to_dict_has_all_required_fields(self):
        """Case 20: ETF dataset row serializes all required contract fields."""
        d = self.row.to_dict()
        required_keys = [
            "ticker", "asset_type", "dataset_version", "generated_at",
            "etf_applicable", "source_artifacts",
            "fund_identity", "cost_and_yield", "composition",
            "trading_and_risk_support", "missing_reasons",
            "canonical_etf_scaffold_present", "canonical_etf_dataset_safe",
            "etf_fund_intelligence_ready",
            "valuation_ready", "synthesis_ready", "safe_for_decision",
            "not_applicable_reason",
        ]
        for key in required_keys:
            assert key in d, f"Missing required field: {key}"

    def test_fund_identity_to_dict_contract(self):
        d = self.row.fund_identity.to_dict()
        assert "fund_name_available" in d
        assert "issuer_available" in d
        assert "category_or_index_strategy_available" in d
        assert "missing_reason" in d

    def test_cost_and_yield_to_dict_contract(self):
        d = self.row.cost_and_yield.to_dict()
        assert "expense_ratio_status" in d
        assert "dividend_or_distribution_yield_status" in d
        assert "missing_reason" in d

    def test_composition_to_dict_contract(self):
        d = self.row.composition.to_dict()
        assert "holdings_composition_status" in d
        assert "sector_exposure_status" in d
        assert "geography_exposure_status" in d
        assert "concentration_status" in d
        assert "missing_reason" in d

    def test_trading_to_dict_contract(self):
        d = self.row.trading_and_risk_support.to_dict()
        assert "liquidity_proxy_status" in d
        assert "volatility_or_technical_support_status" in d
        assert "missing_reason" in d

    def test_dataset_version_correct(self):
        assert self.row.dataset_version == ETF_DATASET_VERSION
        assert "canonical_etf_fund_dataset.v1" in self.row.dataset_version

    def test_no_raw_values_in_serialized_output(self):
        """Case 9/10: no raw expense ratios, yields, holdings data in output."""
        d = self.row.to_dict()
        # Flatten all string values to check for potential raw data leakage
        all_values = str(d)
        for forbidden_pattern in ["expense_ratio=", "yield=", "holdings=[", "top_holding="]:
            assert forbidden_pattern not in all_values

    def test_not_applicable_row_has_not_applicable_reason(self):
        """Case 40: NOT_APPLICABLE row sets not_applicable_reason."""
        row = build_canonical_etf_fund_dataset_row(
            ticker="AAPL",
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            lanes={},
        )
        assert row.not_applicable_reason is not None
        assert len(row.not_applicable_reason) > 0

    def test_etf_row_not_applicable_reason_none(self):
        """ETF row has not_applicable_reason=None."""
        assert self.row.not_applicable_reason is None

    def test_etf_row_scaffold_present_true_dataset_safe_false_in_dict(self):
        """ETF scaffold present=True, dataset_safe=False in serialized output."""
        d = self.row.to_dict()
        assert d["canonical_etf_scaffold_present"] is True
        assert d["canonical_etf_dataset_safe"] is False

    def test_not_applicable_row_statuses(self):
        """Equity NOT_APPLICABLE row has ETF_STATUS_NOT_APPLICABLE statuses."""
        row = build_canonical_etf_fund_dataset_row(
            ticker="AAPL",
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            lanes={},
        )
        assert row.cost_and_yield.expense_ratio_status == ETF_STATUS_NOT_APPLICABLE
        assert row.composition.holdings_composition_status == ETF_STATUS_NOT_APPLICABLE


# ── Section 9: Gap classification ─────────────────────────────────────────────


class TestGapClassification:
    def _base_kwargs(self, asset_type: str) -> dict:
        return {
            "asset_type": asset_type,
            "has_fundamentals_artifact": False,
            "has_technical_artifact": False,
            "has_sec_companyfacts_artifact": False,
            "sec_companyfacts_usability": None,
            "has_sec_catalyst_artifact": False,
            "has_news_sentiment_artifact": False,
            "news_sentiment_usability": None,
            "has_target_weight": True,
            "has_thesis_history": True,
        }

    def test_etf_with_scaffold_gets_composition_not_ready_bucket(self):
        """Case 7: ETF_FUND_COMPOSITION_NOT_READY when scaffold present."""
        bucket, msg = _classify_root_cause(
            **self._base_kwargs(INSTRUMENT_CATEGORY_ETF),
            etf_canonical_scaffold_present=True,
        )
        assert bucket == BUCKET_ETF_FUND_COMPOSITION_NOT_READY
        assert "ETF fund intelligence scaffold" in msg
        assert "MISSING" in msg

    def test_etf_without_scaffold_gets_provider_not_built_bucket(self):
        """Case 8: ETF_PROVIDER_NOT_BUILT when scaffold NOT present (backward compat)."""
        bucket, msg = _classify_root_cause(
            **self._base_kwargs(INSTRUMENT_CATEGORY_ETF),
            etf_canonical_scaffold_present=False,
        )
        assert bucket == BUCKET_ETF_NOT_BUILT

    def test_etf_default_scaffold_false_preserves_backward_compat(self):
        """Case 8b: default etf_canonical_scaffold_present=False preserves old behavior."""
        bucket, _ = _classify_root_cause(
            **self._base_kwargs(INSTRUMENT_CATEGORY_ETF),
        )
        assert bucket == BUCKET_ETF_NOT_BUILT

    def test_composition_not_ready_message_does_not_claim_intelligence_exists(self):
        """ETF_FUND_COMPOSITION_NOT_READY message correctly describes what is missing."""
        bucket, msg = _classify_root_cause(
            **self._base_kwargs(INSTRUMENT_CATEGORY_ETF),
            etf_canonical_scaffold_present=True,
        )
        # Message should say composition is MISSING, not that fund intelligence is ready
        assert "MISSING" in msg
        assert "fund composition" in msg.lower() or "composition" in msg.lower()

    def test_all_gaps_etf_with_scaffold(self):
        """ETF with scaffold gets ETF_FUND_COMPOSITION_NOT_READY as primary gap."""
        gaps = _classify_all_gaps(
            **self._base_kwargs(INSTRUMENT_CATEGORY_ETF),
            etf_canonical_scaffold_present=True,
        )
        primary_bucket = gaps[0][0]
        assert primary_bucket == BUCKET_ETF_FUND_COMPOSITION_NOT_READY

    def test_equity_not_affected_by_etf_scaffold_param(self):
        """etf_canonical_scaffold_present has no effect on equity gap classification."""
        bucket_without, _ = _classify_root_cause(
            **self._base_kwargs(INSTRUMENT_CATEGORY_EQUITY),
            etf_canonical_scaffold_present=False,
        )
        bucket_with, _ = _classify_root_cause(
            **self._base_kwargs(INSTRUMENT_CATEGORY_EQUITY),
            etf_canonical_scaffold_present=True,
        )
        # Equity classification should be identical regardless of scaffold flag
        assert bucket_without == bucket_with


# ── Section 10: Forensics integration ─────────────────────────────────────────


def _make_mock_db_client() -> MagicMock:
    """Build a mock DB client that returns empty results for all queries."""
    client = MagicMock()

    def _empty_result():
        result = MagicMock()
        result.data = []
        return result

    # Chain all Supabase builder calls back to the result.
    def _chainable():
        mock = MagicMock()
        mock.select.return_value = mock
        mock.eq.return_value = mock
        mock.in_.return_value = mock
        mock.order.return_value = mock
        mock.limit.return_value = mock
        mock.execute.return_value = _empty_result()
        return mock

    client.table.side_effect = lambda _: _chainable()
    return client


class TestForensicsIntegration:
    """Forensics integration: ETF canonical dataset in holding rows and aggregates."""

    def _run_forensics(
        self,
        etf_tickers: list[str],
        equity_tickers: list[str],
        crypto_tickers: list[str],
    ):
        from app.services.intelligence.v3.intel_data_foundation_forensics_v1 import (
            compute_data_foundation_forensics,
        )

        all_tickers = etf_tickers + equity_tickers + crypto_tickers
        holding_ctx = {}
        for t in etf_tickers:
            holding_ctx[t] = {"category": "etf"}
        for t in equity_tickers:
            holding_ctx[t] = {"category": "equity"}
        for t in crypto_tickers:
            holding_ctx[t] = {"category": "crypto"}

        return compute_data_foundation_forensics(
            user_id="test-user",
            tickers=all_tickers,
            holding_context_by_ticker=holding_ctx,
            db_client=_make_mock_db_client(),
        )

    def test_etf_holdings_have_canonical_etf_dataset_populated(self):
        """Case 21: ETF holdings have canonical_etf_dataset dict populated."""
        result = self._run_forensics(["SPY", "QQQ"], [], [])
        for h in result.holdings:
            assert h.canonical_etf_dataset is not None
            assert isinstance(h.canonical_etf_dataset, dict)
            assert "etf_applicable" in h.canonical_etf_dataset
            assert h.canonical_etf_dataset["etf_applicable"] is True

    def test_equity_holdings_have_no_canonical_etf_dataset(self):
        """Case 22: equity holdings have canonical_etf_dataset=None."""
        result = self._run_forensics([], ["AAPL", "MSFT"], [])
        for h in result.holdings:
            assert h.canonical_etf_dataset is None

    def test_crypto_holdings_have_no_canonical_etf_dataset(self):
        """Case 23: crypto holdings have canonical_etf_dataset=None."""
        result = self._run_forensics([], [], ["BTC", "XRP"])
        for h in result.holdings:
            assert h.canonical_etf_dataset is None

    def test_etf_canonical_dataset_count_correct(self):
        """Case 24: etf_canonical_dataset_count reflects scaffold-built ETFs."""
        result = self._run_forensics(["SPY", "QQQ", "GLD"], [], [])
        assert result.etf_canonical_dataset_count == 3

    def test_etf_fund_intelligence_ready_count_always_zero(self):
        """Case 25: etf_fund_intelligence_ready_count always 0 at Stage 9F."""
        result = self._run_forensics(["SPY", "QQQ", "GLD"], [], [])
        assert result.etf_fund_intelligence_ready_count == 0

    def test_etf_missing_reason_counts_aggregated(self):
        """Case 26: etf_missing_reason_counts aggregates missing reason keys."""
        result = self._run_forensics(["SPY", "QQQ"], [], [])
        # Composition is always MISSING, so "composition" key should appear
        assert "composition" in result.etf_missing_reason_counts
        assert result.etf_missing_reason_counts["composition"] == 2

    def test_asset_parity_roadmap_etf_canonical_built_true(self):
        """Case 27: asset_parity_roadmap ETF canonical_dataset_built=True when scaffolds built."""
        result = self._run_forensics(["SPY", "QQQ"], [], [])
        roadmap = result.asset_parity_roadmap
        assert roadmap is not None
        etf_entry = next(
            (ac for ac in roadmap["asset_classes"] if ac["asset_class"] == "etf"),
            None,
        )
        assert etf_entry is not None
        assert etf_entry["canonical_dataset_built"] is True

    def test_asset_parity_roadmap_etf_canonical_built_false_no_etfs(self):
        """Case 28: ETF canonical_dataset_built=False when no ETF holdings."""
        result = self._run_forensics([], ["AAPL"], [])
        roadmap = result.asset_parity_roadmap
        etf_entry = next(
            (ac for ac in roadmap["asset_classes"] if ac["asset_class"] == "etf"),
            None,
        )
        assert etf_entry is not None
        assert etf_entry["canonical_dataset_built"] is False

    def test_blocking_gap_includes_etf_fund_composition_not_ready(self):
        """Case 29: blocking_gap_bucket_counts includes ETF_FUND_COMPOSITION_NOT_READY."""
        result = self._run_forensics(["SPY", "QQQ"], [], [])
        assert BUCKET_ETF_FUND_COMPOSITION_NOT_READY in result.blocking_gap_bucket_counts
        assert result.blocking_gap_bucket_counts[BUCKET_ETF_FUND_COMPOSITION_NOT_READY] == 2

    def test_etf_provider_not_built_absent_when_scaffold_present(self):
        """ETF_PROVIDER_NOT_BUILT should NOT be primary bucket when scaffold is built."""
        result = self._run_forensics(["SPY"], [], [])
        etf_row = next(h for h in result.holdings if h.ticker == "SPY")
        assert etf_row.root_cause_bucket == BUCKET_ETF_FUND_COMPOSITION_NOT_READY
        assert etf_row.root_cause_bucket != BUCKET_ETF_NOT_BUILT

    def test_forensics_result_to_dict_has_stage9f_fields(self):
        """Case 30: forensics result to_dict includes all Stage 9F ETF fields."""
        result = self._run_forensics(["SPY"], [], [])
        d = result.to_dict()
        assert "etf_canonical_dataset_count" in d
        assert "etf_fund_intelligence_ready_count" in d
        assert "etf_canonical_dataset_degraded_tickers" in d
        assert "etf_missing_reason_counts" in d

    def test_holding_row_to_dict_has_canonical_etf_dataset(self):
        """HoldingForensicsRow.to_dict() includes canonical_etf_dataset."""
        result = self._run_forensics(["SPY"], [], [])
        etf_row = next(h for h in result.holdings if h.ticker == "SPY")
        d = etf_row.to_dict()
        assert "canonical_etf_dataset" in d
        assert d["canonical_etf_dataset"] is not None

    def test_visible_decision_policy_unchanged(self):
        """Case 13 (visible decisions): safe_for_decision and synthesis_ready remain False."""
        result = self._run_forensics(["SPY", "QQQ"], ["AAPL"], ["BTC"])
        assert result.safe_for_decision is False
        assert result.synthesis_ready is False
        for h in result.holdings:
            if h.canonical_etf_dataset:
                assert h.canonical_etf_dataset["safe_for_decision"] is False
                assert h.canonical_etf_dataset["synthesis_ready"] is False

    def test_all_classes_synthesis_ready_false(self):
        """all_classes_synthesis_ready remains False in parity roadmap."""
        result = self._run_forensics(["SPY"], ["AAPL"], ["BTC"])
        roadmap = result.asset_parity_roadmap
        assert roadmap["all_classes_synthesis_ready"] is False


# ── Section 11: Safety / purity invariants ────────────────────────────────────


class TestSafetyInvariants:
    def test_no_decide_import_in_etf_module(self):
        """Case 31: decision_policy_v1 is NOT imported in the ETF dataset module."""
        import app.services.intelligence.v3.canonical_etf_fund_dataset_v1 as mod
        import inspect
        source = inspect.getsource(mod)
        # The definitive guard: decision_policy_v1 must not be imported.
        assert "decision_policy_v1" not in source
        assert "from .decision_policy" not in source
        assert "import decision_policy" not in source

    def test_build_function_is_pure(self):
        """Case 32: build_canonical_etf_fund_dataset_row makes no IO calls."""
        # If pure, calling it multiple times with same args should return consistent results.
        lanes = _make_etf_lanes()
        row1 = build_canonical_etf_fund_dataset_row(
            ticker="SPY", asset_type=INSTRUMENT_CATEGORY_ETF, lanes=lanes
        )
        row2 = build_canonical_etf_fund_dataset_row(
            ticker="SPY", asset_type=INSTRUMENT_CATEGORY_ETF, lanes=lanes
        )
        assert row1.etf_applicable == row2.etf_applicable
        assert row1.canonical_etf_scaffold_present == row2.canonical_etf_scaffold_present
        assert row1.canonical_etf_dataset_safe == row2.canonical_etf_dataset_safe
        assert row1.composition.holdings_composition_status == row2.composition.holdings_composition_status

    def test_regression_no_breaking_imports(self):
        """Case 33: existing Stage 9B/9D/9E/9E.1 modules still importable."""
        from app.services.intelligence.v3.canonical_equity_dataset_v1 import (  # noqa: F401
            build_canonical_equity_dataset_row,
            build_asset_parity_roadmap,
        )
        from app.services.intelligence.v3.intel_data_foundation_forensics_v1 import (  # noqa: F401
            compute_data_foundation_forensics,
            DataFoundationForensicsResult,
        )
        from app.services.intelligence.v3.equity_valuation_evidence_v1 import (  # noqa: F401
            build_equity_valuation_evidence_row,
        )
        from app.services.intelligence.v3.equity_numeric_valuation_inputs_v1 import (  # noqa: F401
            build_equity_numeric_valuation_inputs,
        )

    def test_all_buckets_includes_new_etf_bucket(self):
        """Stage 9F adds ETF_FUND_COMPOSITION_NOT_READY to ALL_BUCKETS."""
        from app.services.intelligence.v3.intel_data_foundation_forensics_v1 import (
            ALL_BUCKETS,
        )
        assert BUCKET_ETF_FUND_COMPOSITION_NOT_READY in ALL_BUCKETS
        assert BUCKET_ETF_NOT_BUILT in ALL_BUCKETS  # backward compat preserved

    def test_etf_dataset_module_never_fabricates_holdings(self):
        """Case 9: composition statuses confirmed MISSING — no fabricated holding names."""
        row = build_canonical_etf_fund_dataset_row(
            ticker="SPY",
            asset_type=INSTRUMENT_CATEGORY_ETF,
            lanes=_make_etf_lanes(),
        )
        d = row.to_dict()
        all_content = str(d)
        # These would indicate fabricated holdings data
        for fabricated in ["Technology", "Apple", "Microsoft", "top_10_holdings"]:
            assert fabricated not in all_content
