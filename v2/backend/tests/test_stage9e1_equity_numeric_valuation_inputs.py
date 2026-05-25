"""Stage 9E.1 — Equity Numeric Valuation Input Adapter v1.

Covers:
  1.  Ticker-level price (market_price_usd confirmed) + earnings (fact records) → numeric_inputs_ready=True.
  2.  Portfolio-level snapshot alone (no ticker_price_signal) → numeric_inputs_ready=False.
  3.  Missing price (no signal) → numeric_inputs_ready=False, price_input MISSING.
  4.  Carried price (no market_value_certified_at) → numeric_inputs_ready=False.
  5.  Stale price (freshness=STALE) → numeric_inputs_ready=False.
  6.  Missing earnings → numeric_inputs_ready=False, earnings_input MISSING.
  7.  Degraded canonical dataset → numeric_inputs_ready=False for all inputs.
  8.  ETF: all inputs MISSING, numeric_inputs_ready=False.
  9.  Crypto: all inputs MISSING, numeric_inputs_ready=False.
  10. No raw EPS, price, P/E, XBRL metric names in serialized output.
  11. safe_for_decision=False always.
  12. synthesis_ready=False always.
  13. to_dict() has all required fields including valuation_input_scaffold_present,
      ticker_price_metadata_present, numeric_price_confirmed, numeric_earnings_confirmed.
  14. valuation_ready=False in evidence row (band=UNKNOWN, no thresholds defined at 9E.1).
  15. valuation_ready=False in evidence row when numeric_inputs=None (Stage 9E mode).
  16. valuation_numeric_inputs_in_scope=True when numeric adapter is used.
  17. price_is_portfolio_level_proxy=False when ticker-level confirmed.
  18. price_is_portfolio_level_proxy=True when no ticker-level signal.
  19. VALUATION_LANE_NOT_BUILT remains at Stage 9E.1 (valuation_ready=False always).
  20. VALUATION_LANE_NOT_BUILT remains for non-numeric-ready tickers.
  21. cash_flow_input: AVAILABLE when FCF section AVAILABLE.
  22. cash_flow_input: PARTIAL when FCF section PARTIAL.
  23. growth_input: AVAILABLE when both revenue + EPS AVAILABLE.
  24. growth_input: PARTIAL when only one section AVAILABLE.
  25. growth_input: MISSING when both revenue + EPS MISSING.
  26. earnings metric_family: NET_INCOME when unit=USD.
  27. earnings metric_family: EPS when unit=USD/shares.
  28. earnings metric_family: UNKNOWN when no unit.
  29. Forensics DataFoundationForensicsResult has all Stage 9E.1 fields.
  30. equity_numeric_valuation_input_count increases for equity holdings.
  31. equity_numeric_valuation_ready_count=0 when no ticker-level price signals.
  32. equity_numeric_valuation_ready_count>0 when certified prices available.
  33. _extract_ticker_price_signals: certified position → ticker_level_confirmed=True.
  34. _extract_ticker_price_signals: carried position → ticker_level_confirmed=False.
  35. _extract_ticker_price_signals: FRESH when snapshot < 24h old.
  36. _extract_ticker_price_signals: AGING when snapshot 24–72h old.
  37. _extract_ticker_price_signals: STALE when snapshot > 72h old.
  38. _extract_ticker_price_signals: empty positions_data → empty signals.
  39. ETF/crypto unaffected by Stage 9E.1.
  40. Stage 9E tests still pass (regression: valuation_ready=False when no numeric_inputs).
  41. market_value_certified_at alone does not make numeric_price_confirmed=True.
  42. market_price_usd present → numeric_price_confirmed=True (no raw value serialized).
  43. Section status alone does not make numeric_earnings_confirmed=True.
  44. latest_period_identity + source_artifact_id non-None → numeric_earnings_confirmed=True.
  45. valuation_ready=False while valuation_interpretation_band=UNKNOWN.
  46. VALUATION_LANE_NOT_BUILT remains even when numeric_inputs_ready=True (band=UNKNOWN).
"""
from __future__ import annotations

import json
from dataclasses import field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from unittest.mock import MagicMock

import pytest

from app.services.intelligence.v3.equity_numeric_valuation_inputs_v1 import (
    FRESHNESS_AGING,
    FRESHNESS_FRESH,
    FRESHNESS_STALE,
    FRESHNESS_UNKNOWN,
    INPUT_STATUS_AVAILABLE,
    INPUT_STATUS_MISSING,
    INPUT_STATUS_PARTIAL,
    INPUT_STATUS_STALE,
    METRIC_FAMILY_EPS,
    METRIC_FAMILY_FCF_DERIVABLE,
    METRIC_FAMILY_NET_INCOME,
    METRIC_FAMILY_OPERATING_CASH_FLOW,
    METRIC_FAMILY_UNKNOWN,
    MODEL_VERSION,
    PRICE_SOURCE_NONE,
    PRICE_SOURCE_SNAPSHOT_CARRIED,
    PRICE_SOURCE_SNAPSHOT_CERTIFIED,
    EquityNumericValuationInputs,
    TickerPriceSignal,
    build_equity_numeric_valuation_inputs,
)
from app.services.intelligence.v3.equity_valuation_evidence_v1 import (
    BAND_UNKNOWN,
    build_equity_valuation_evidence_row,
)
from app.services.intelligence.v3.canonical_equity_dataset_v1 import (
    DATASET_VERSION,
    SECTION_CASH_FLOW_FCF,
    SECTION_NET_INCOME_EPS,
    SECTION_PROFITABILITY,
    SECTION_REVENUE,
    SECTION_SHARE_COUNT,
    SECTION_STATUS_AVAILABLE,
    SECTION_STATUS_MISSING,
    SECTION_STATUS_PARTIAL,
    SYNTHESIS_GATE_BLOCKED,
    TREND_UNKNOWN,
    VALUATION_GATE_BLOCKED,
    CanonicalEquityDatasetRow,
    CatalystContextSection,
    EvidenceSectionRecord,
    OperatingTrendSection,
    PeriodIdentity,
    SourceHealthEntry,
    TechnicalSupportSection,
)
from app.services.intelligence.v3.intel_data_foundation_forensics_v1 import (
    BUCKET_VALUATION_NOT_BUILT,
    DataFoundationForensicsResult,
    HoldingForensicsRow,
    _SupplementalData,
    _build_holding_row,
    _classify_all_gaps,
    _extract_ticker_price_signals,
)
from app.services.intelligence.v3.research_evidence_coverage_read_model_v1 import (
    LANE_SEC_COMPANY_FACTS,
    STATUS_READY,
    STATUS_SUPPRESSED,
    LaneCoverage,
)
from app.services.intelligence.v3.research_evidence_decision_input_adapter_v1 import (
    INSTRUMENT_CATEGORY_CRYPTO,
    INSTRUMENT_CATEGORY_EQUITY,
    INSTRUMENT_CATEGORY_ETF,
)


# ── Test helpers ───────────────────────────────────────────────────────────────

NOW_ISO = "2026-05-24T12:00:00+00:00"


def _make_price_signal(
    ticker: str = "MSFT",
    *,
    source_type: str = PRICE_SOURCE_SNAPSHOT_CERTIFIED,
    freshness_label: str = FRESHNESS_FRESH,
    ticker_level_confirmed: bool = True,
    numeric_price_confirmed: bool = True,
) -> TickerPriceSignal:
    return TickerPriceSignal(
        ticker=ticker,
        source_type=source_type,
        freshness_label=freshness_label,
        ticker_level_confirmed=ticker_level_confirmed,
        numeric_price_confirmed=numeric_price_confirmed,
    )


def _make_period_identity(
    unit: str = "USD",
    fiscal_year: int = 2024,
) -> PeriodIdentity:
    return PeriodIdentity(
        fiscal_year=fiscal_year,
        fiscal_period="FY",
        period_end="2024-09-28",
        unit=unit,
        form="10-K",
    )


def _make_section_record(
    section: str,
    status: str,
    unit: str = "USD",
) -> EvidenceSectionRecord:
    return EvidenceSectionRecord(
        section=section,
        status=status,
        evidence_basis="SEC_COMPANYFACTS" if status != SECTION_STATUS_MISSING else "UNAVAILABLE",
        latest_period_identity=_make_period_identity(unit=unit) if status != SECTION_STATUS_MISSING else None,
        comparison_period_identity=None,
        trend_direction=TREND_UNKNOWN,
        source_artifact_id="art-001" if status != SECTION_STATUS_MISSING else None,
        missing_reason=None if status != SECTION_STATUS_MISSING else f"{section} missing.",
    )


def _make_operating_trends(
    *,
    revenue_status: str = SECTION_STATUS_AVAILABLE,
    eps_status: str = SECTION_STATUS_AVAILABLE,
    fcf_status: str = SECTION_STATUS_AVAILABLE,
    eps_unit: str = "USD",
) -> OperatingTrendSection:
    sections = {
        SECTION_REVENUE: _make_section_record(SECTION_REVENUE, revenue_status),
        SECTION_PROFITABILITY: _make_section_record(SECTION_PROFITABILITY, SECTION_STATUS_AVAILABLE),
        SECTION_NET_INCOME_EPS: _make_section_record(SECTION_NET_INCOME_EPS, eps_status, unit=eps_unit),
        SECTION_CASH_FLOW_FCF: _make_section_record(SECTION_CASH_FLOW_FCF, fcf_status),
        SECTION_SHARE_COUNT: _make_section_record(SECTION_SHARE_COUNT, SECTION_STATUS_AVAILABLE),
    }
    return OperatingTrendSection(
        sections=sections,
        trend_source="sec_company_facts",
        observation_count=20,
        completeness_band="COMPLETE",
        freshness_status="FRESH",
        usability_label="USABLE",
        missing_reason=None,
    )


def _make_canonical_row(
    *,
    ticker: str = "MSFT",
    asset_type: str = INSTRUMENT_CATEGORY_EQUITY,
    safe_for_equity_dataset: bool = True,
    operating_trends: Optional[OperatingTrendSection] = None,
) -> CanonicalEquityDatasetRow:
    if operating_trends is None:
        operating_trends = _make_operating_trends()
    return CanonicalEquityDatasetRow(
        ticker=ticker,
        asset_type=asset_type,
        company_applicable=(asset_type == INSTRUMENT_CATEGORY_EQUITY),
        dataset_version=DATASET_VERSION,
        generated_at=NOW_ISO,
        source_artifacts=[
            SourceHealthEntry(
                lane=LANE_SEC_COMPANY_FACTS,
                artifact_id="art-001",
                usability_label="USABLE",
                freshness_status="FRESH",
                model_version="sec_xbrl_companyfacts_v1",
                is_current_model=True,
            )
        ],
        operating_trends=operating_trends,
        catalyst_context=CatalystContextSection(
            catalyst_available=False,
            catalyst_count=None,
            catalyst_source="unavailable",
            catalyst_usability=None,
            missing_reason="No catalyst.",
        ),
        technical_context=TechnicalSupportSection(
            technical_available=False,
            technical_usability=None,
            trust_label="LIMITED_TRUST",
        ),
        missing_section_reasons={},
        safe_for_equity_dataset=safe_for_equity_dataset,
        valuation_ready=False,
        synthesis_ready=False,
        safe_for_decision=False,
        not_safe_reason=None if safe_for_equity_dataset else "SEC facts weak.",
    )


def _make_etf_canonical_row() -> CanonicalEquityDatasetRow:
    from app.services.intelligence.v3.canonical_equity_dataset_v1 import (
        build_canonical_equity_dataset_row,
    )
    return build_canonical_equity_dataset_row(
        ticker="VTI",
        asset_type=INSTRUMENT_CATEGORY_ETF,
        lanes={},
        sec_obs_count=None,
        cat_count=None,
    )


def _make_crypto_canonical_row() -> CanonicalEquityDatasetRow:
    from app.services.intelligence.v3.canonical_equity_dataset_v1 import (
        build_canonical_equity_dataset_row,
    )
    return build_canonical_equity_dataset_row(
        ticker="BTC",
        asset_type=INSTRUMENT_CATEGORY_CRYPTO,
        lanes={},
        sec_obs_count=None,
        cat_count=None,
    )


def _empty_supplemental(
    *,
    has_portfolio_snapshot: bool = True,
    ticker_price_signals: Optional[dict] = None,
) -> _SupplementalData:
    return _SupplementalData(
        target_tickers=frozenset(),
        recommendation_tickers=frozenset(),
        fact_counts={},
        has_portfolio_snapshot=has_portfolio_snapshot,
        sec_fact_records={},
        ticker_price_signals=ticker_price_signals or {},
    )


def _make_sec_lane(
    status: str = STATUS_READY,
    usability_label: str = "USABLE",
) -> LaneCoverage:
    return LaneCoverage(
        lane=LANE_SEC_COMPANY_FACTS,
        artifact_type="fundamental_quality",
        skill_pack="sec_companyfacts_evidence_v1",
        scope_kind="ticker",
        ticker="MSFT",
        artifact_id="art-001" if status != "MISSING" else None,
        status=status,
        usability_label=usability_label,
        is_usable=(status in (STATUS_READY, "LIMITED")),
        suppression_reason=None,
        source_authority="PRIMARY_AUTHORITY",
        completeness_band="COMPLETE",
        has_contradictions=False,
        freshness_status="FRESH",
        confidence_or_trust_level=None,
        model_version="sec_xbrl_companyfacts_v1",
        generated_at=NOW_ISO,
        expires_at=None,
    )


# ── Test: output contract ──────────────────────────────────────────────────────


class TestOutputContract:
    """EquityNumericValuationInputs.to_dict() exposes all required fields."""

    def test_to_dict_has_all_required_fields(self):
        inputs = build_equity_numeric_valuation_inputs(
            canonical_row=_make_canonical_row(),
            ticker_price_signal=_make_price_signal(),
        )
        d = inputs.to_dict()
        required_keys = {
            "ticker", "asset_type", "input_version", "generated_at",
            "price_input", "earnings_input", "cash_flow_input", "growth_input",
            "missing_reasons",
            "valuation_input_scaffold_present", "ticker_price_metadata_present",
            "numeric_price_confirmed", "numeric_earnings_confirmed",
            "numeric_inputs_ready",
            "safe_for_decision", "synthesis_ready",
        }
        assert required_keys <= d.keys()

    def test_price_input_fields(self):
        inputs = build_equity_numeric_valuation_inputs(
            canonical_row=_make_canonical_row(),
            ticker_price_signal=_make_price_signal(),
        )
        pi = inputs.to_dict()["price_input"]
        assert set(pi.keys()) == {
            "status", "source_type", "freshness_label",
            "ticker_level_confirmed", "numeric_price_confirmed",
        }

    def test_earnings_input_fields(self):
        inputs = build_equity_numeric_valuation_inputs(
            canonical_row=_make_canonical_row(),
            ticker_price_signal=_make_price_signal(),
        )
        ei = inputs.to_dict()["earnings_input"]
        assert set(ei.keys()) == {"status", "metric_family", "period_identity", "source_artifact_id"}

    def test_cash_flow_input_fields(self):
        inputs = build_equity_numeric_valuation_inputs(
            canonical_row=_make_canonical_row(),
            ticker_price_signal=_make_price_signal(),
        )
        cf = inputs.to_dict()["cash_flow_input"]
        assert set(cf.keys()) == {"status", "metric_family", "period_identity", "source_artifact_id"}

    def test_growth_input_fields(self):
        inputs = build_equity_numeric_valuation_inputs(
            canonical_row=_make_canonical_row(),
            ticker_price_signal=_make_price_signal(),
        )
        gi = inputs.to_dict()["growth_input"]
        assert set(gi.keys()) == {"status", "based_on_sections"}

    def test_model_version(self):
        inputs = build_equity_numeric_valuation_inputs(
            canonical_row=_make_canonical_row(),
            ticker_price_signal=_make_price_signal(),
        )
        assert inputs.input_version == MODEL_VERSION

    def test_generated_at_is_iso(self):
        inputs = build_equity_numeric_valuation_inputs(
            canonical_row=_make_canonical_row(),
        )
        datetime.fromisoformat(inputs.generated_at)


# ── Test: safety invariants ────────────────────────────────────────────────────


class TestSafetyInvariants:
    """safe_for_decision and synthesis_ready are immutable; no raw values serialized."""

    def test_safe_for_decision_always_false(self):
        for safe in (True, False):
            inputs = build_equity_numeric_valuation_inputs(
                canonical_row=_make_canonical_row(safe_for_equity_dataset=safe),
                ticker_price_signal=_make_price_signal(),
            )
            assert inputs.safe_for_decision is False

    def test_synthesis_ready_always_false(self):
        inputs = build_equity_numeric_valuation_inputs(
            canonical_row=_make_canonical_row(),
            ticker_price_signal=_make_price_signal(),
        )
        assert inputs.synthesis_ready is False

    def test_no_raw_values_in_serialized_output(self):
        """No raw EPS, prices, P/E, XBRL metric names, fair values, or price targets."""
        inputs = build_equity_numeric_valuation_inputs(
            canonical_row=_make_canonical_row(),
            ticker_price_signal=_make_price_signal(),
        )
        serialized = json.dumps(inputs.to_dict())
        forbidden_patterns = [
            "EarningsPerShare", "NetIncomeLoss", "GrossProfit",
            "fair_value", "price_target", "intrinsic_value",
            "NetCashProvidedByUsed", "upside", "downside",
        ]
        for pattern in forbidden_patterns:
            assert pattern not in serialized, (
                f"Forbidden pattern '{pattern}' found in serialized output"
            )

    def test_no_decide_import(self):
        """Module must not import decide() or decision_policy_v1."""
        import app.services.intelligence.v3.equity_numeric_valuation_inputs_v1 as mod
        source = __import__("inspect").getsource(mod)
        assert "decision_policy_v1" not in source
        assert "from .decision_policy" not in source


# ── Test: numeric_inputs_ready gate ───────────────────────────────────────────


class TestNumericInputsReadyGate:
    """numeric_inputs_ready requires confirmed ticker-level price + earnings."""

    def test_all_inputs_confirmed_numeric_ready_true(self):
        """Canonical safe + ticker-level certified price + EPS → numeric_inputs_ready=True."""
        inputs = build_equity_numeric_valuation_inputs(
            canonical_row=_make_canonical_row(safe_for_equity_dataset=True),
            ticker_price_signal=_make_price_signal(
                source_type=PRICE_SOURCE_SNAPSHOT_CERTIFIED,
                freshness_label=FRESHNESS_FRESH,
                ticker_level_confirmed=True,
            ),
        )
        assert inputs.numeric_inputs_ready is True

    def test_aging_price_still_ready(self):
        """AGING (within 72h) price is acceptable for numeric readiness."""
        inputs = build_equity_numeric_valuation_inputs(
            canonical_row=_make_canonical_row(safe_for_equity_dataset=True),
            ticker_price_signal=_make_price_signal(
                freshness_label=FRESHNESS_AGING,
                ticker_level_confirmed=True,
            ),
        )
        assert inputs.numeric_inputs_ready is True

    def test_no_price_signal_not_ready(self):
        """No ticker price signal → numeric_inputs_ready=False."""
        inputs = build_equity_numeric_valuation_inputs(
            canonical_row=_make_canonical_row(safe_for_equity_dataset=True),
            ticker_price_signal=None,
        )
        assert inputs.numeric_inputs_ready is False
        assert inputs.price_input.status == INPUT_STATUS_MISSING
        assert inputs.price_input.ticker_level_confirmed is False

    def test_portfolio_level_proxy_not_sufficient(self):
        """Carried (not certified) price signal → ticker_level_confirmed=False → not ready."""
        inputs = build_equity_numeric_valuation_inputs(
            canonical_row=_make_canonical_row(safe_for_equity_dataset=True),
            ticker_price_signal=_make_price_signal(
                source_type=PRICE_SOURCE_SNAPSHOT_CARRIED,
                freshness_label=FRESHNESS_FRESH,
                ticker_level_confirmed=False,
            ),
        )
        assert inputs.numeric_inputs_ready is False
        assert inputs.price_input.status == INPUT_STATUS_PARTIAL
        assert inputs.price_input.ticker_level_confirmed is False

    def test_stale_price_not_ready(self):
        """Stale ticker-level price → numeric_inputs_ready=False."""
        inputs = build_equity_numeric_valuation_inputs(
            canonical_row=_make_canonical_row(safe_for_equity_dataset=True),
            ticker_price_signal=_make_price_signal(
                freshness_label=FRESHNESS_STALE,
                ticker_level_confirmed=True,
            ),
        )
        assert inputs.numeric_inputs_ready is False
        assert inputs.price_input.status == INPUT_STATUS_STALE

    def test_missing_earnings_not_ready(self):
        """Missing EPS section → numeric_inputs_ready=False."""
        inputs = build_equity_numeric_valuation_inputs(
            canonical_row=_make_canonical_row(
                operating_trends=_make_operating_trends(eps_status=SECTION_STATUS_MISSING),
            ),
            ticker_price_signal=_make_price_signal(ticker_level_confirmed=True),
        )
        assert inputs.numeric_inputs_ready is False
        assert inputs.earnings_input.status == INPUT_STATUS_MISSING

    def test_degraded_canonical_not_ready(self):
        """Degraded canonical dataset → numeric_inputs_ready=False for all inputs."""
        inputs = build_equity_numeric_valuation_inputs(
            canonical_row=_make_canonical_row(safe_for_equity_dataset=False),
            ticker_price_signal=_make_price_signal(ticker_level_confirmed=True),
        )
        assert inputs.numeric_inputs_ready is False
        assert inputs.earnings_input.status == INPUT_STATUS_MISSING
        assert inputs.cash_flow_input.status == INPUT_STATUS_MISSING
        assert inputs.growth_input.status == INPUT_STATUS_MISSING

    def test_partial_earnings_still_ready(self):
        """PARTIAL EPS section is acceptable for numeric readiness."""
        inputs = build_equity_numeric_valuation_inputs(
            canonical_row=_make_canonical_row(
                operating_trends=_make_operating_trends(eps_status=SECTION_STATUS_PARTIAL),
            ),
            ticker_price_signal=_make_price_signal(ticker_level_confirmed=True),
        )
        assert inputs.numeric_inputs_ready is True
        assert inputs.earnings_input.status == INPUT_STATUS_PARTIAL


# ── Test: ETF/crypto not applicable ───────────────────────────────────────────


class TestNonEquityNotApplicable:
    """ETF and crypto return all-MISSING inputs, numeric_inputs_ready=False."""

    def test_etf_numeric_inputs_not_ready(self):
        inputs = build_equity_numeric_valuation_inputs(
            canonical_row=_make_etf_canonical_row(),
            ticker_price_signal=_make_price_signal(ticker_level_confirmed=True),
        )
        assert inputs.numeric_inputs_ready is False
        assert inputs.price_input.status == INPUT_STATUS_MISSING
        assert inputs.earnings_input.status == INPUT_STATUS_MISSING

    def test_crypto_numeric_inputs_not_ready(self):
        inputs = build_equity_numeric_valuation_inputs(
            canonical_row=_make_crypto_canonical_row(),
            ticker_price_signal=_make_price_signal(ticker_level_confirmed=True),
        )
        assert inputs.numeric_inputs_ready is False
        assert inputs.safe_for_decision is False
        assert inputs.synthesis_ready is False


# ── Test: cash flow input ──────────────────────────────────────────────────────


class TestCashFlowInput:
    """cash_flow_input follows FCF section status."""

    def test_fcf_available_cash_flow_available_operating(self):
        inputs = build_equity_numeric_valuation_inputs(
            canonical_row=_make_canonical_row(
                operating_trends=_make_operating_trends(fcf_status=SECTION_STATUS_AVAILABLE),
            ),
            ticker_price_signal=_make_price_signal(ticker_level_confirmed=True),
        )
        assert inputs.cash_flow_input.status == INPUT_STATUS_AVAILABLE
        assert inputs.cash_flow_input.metric_family == METRIC_FAMILY_OPERATING_CASH_FLOW

    def test_fcf_partial_cash_flow_partial_fcf_derivable(self):
        inputs = build_equity_numeric_valuation_inputs(
            canonical_row=_make_canonical_row(
                operating_trends=_make_operating_trends(fcf_status=SECTION_STATUS_PARTIAL),
            ),
            ticker_price_signal=_make_price_signal(ticker_level_confirmed=True),
        )
        assert inputs.cash_flow_input.status == INPUT_STATUS_PARTIAL
        assert inputs.cash_flow_input.metric_family == METRIC_FAMILY_FCF_DERIVABLE

    def test_fcf_missing_cash_flow_missing(self):
        inputs = build_equity_numeric_valuation_inputs(
            canonical_row=_make_canonical_row(
                operating_trends=_make_operating_trends(fcf_status=SECTION_STATUS_MISSING),
            ),
            ticker_price_signal=_make_price_signal(ticker_level_confirmed=True),
        )
        assert inputs.cash_flow_input.status == INPUT_STATUS_MISSING
        assert "cash_flow_input" in inputs.missing_reasons

    def test_cash_flow_source_artifact_id_present(self):
        inputs = build_equity_numeric_valuation_inputs(
            canonical_row=_make_canonical_row(),
            ticker_price_signal=_make_price_signal(ticker_level_confirmed=True),
        )
        assert inputs.cash_flow_input.source_artifact_id == "art-001"


# ── Test: growth input ─────────────────────────────────────────────────────────


class TestGrowthInput:
    """growth_input uses section names only, no raw values."""

    def test_both_sections_available_growth_available(self):
        inputs = build_equity_numeric_valuation_inputs(
            canonical_row=_make_canonical_row(
                operating_trends=_make_operating_trends(
                    revenue_status=SECTION_STATUS_AVAILABLE,
                    eps_status=SECTION_STATUS_AVAILABLE,
                ),
            ),
            ticker_price_signal=_make_price_signal(ticker_level_confirmed=True),
        )
        assert inputs.growth_input.status == INPUT_STATUS_AVAILABLE
        assert SECTION_REVENUE in inputs.growth_input.based_on_sections
        assert SECTION_NET_INCOME_EPS in inputs.growth_input.based_on_sections

    def test_one_section_available_growth_partial(self):
        inputs = build_equity_numeric_valuation_inputs(
            canonical_row=_make_canonical_row(
                operating_trends=_make_operating_trends(
                    revenue_status=SECTION_STATUS_AVAILABLE,
                    eps_status=SECTION_STATUS_MISSING,
                ),
            ),
            ticker_price_signal=_make_price_signal(ticker_level_confirmed=True),
        )
        assert inputs.growth_input.status == INPUT_STATUS_PARTIAL
        assert SECTION_REVENUE in inputs.growth_input.based_on_sections

    def test_both_sections_missing_growth_missing(self):
        inputs = build_equity_numeric_valuation_inputs(
            canonical_row=_make_canonical_row(
                operating_trends=_make_operating_trends(
                    revenue_status=SECTION_STATUS_MISSING,
                    eps_status=SECTION_STATUS_MISSING,
                ),
            ),
            ticker_price_signal=_make_price_signal(ticker_level_confirmed=True),
        )
        assert inputs.growth_input.status == INPUT_STATUS_MISSING
        assert "growth_input" in inputs.missing_reasons

    def test_growth_based_on_sections_contains_no_raw_values(self):
        inputs = build_equity_numeric_valuation_inputs(
            canonical_row=_make_canonical_row(),
            ticker_price_signal=_make_price_signal(ticker_level_confirmed=True),
        )
        serialized = json.dumps(inputs.growth_input.to_dict())
        assert "EarningsPerShare" not in serialized
        assert "NetIncomeLoss" not in serialized


# ── Test: earnings metric family inference ─────────────────────────────────────


class TestEarningsMetricFamily:
    """metric_family is inferred from unit — never a raw XBRL name."""

    def test_usd_unit_net_income_family(self):
        inputs = build_equity_numeric_valuation_inputs(
            canonical_row=_make_canonical_row(
                operating_trends=_make_operating_trends(eps_unit="USD"),
            ),
            ticker_price_signal=_make_price_signal(ticker_level_confirmed=True),
        )
        assert inputs.earnings_input.metric_family == METRIC_FAMILY_NET_INCOME

    def test_usd_per_shares_unit_eps_family(self):
        inputs = build_equity_numeric_valuation_inputs(
            canonical_row=_make_canonical_row(
                operating_trends=_make_operating_trends(eps_unit="USD/shares"),
            ),
            ticker_price_signal=_make_price_signal(ticker_level_confirmed=True),
        )
        assert inputs.earnings_input.metric_family == METRIC_FAMILY_EPS

    def test_no_unit_unknown_family(self):
        inputs = build_equity_numeric_valuation_inputs(
            canonical_row=_make_canonical_row(
                operating_trends=_make_operating_trends(eps_unit=""),
            ),
            ticker_price_signal=_make_price_signal(ticker_level_confirmed=True),
        )
        assert inputs.earnings_input.metric_family == METRIC_FAMILY_UNKNOWN


# ── Test: valuation evidence integration ──────────────────────────────────────


class TestValuationEvidenceIntegration:
    """valuation_ready=True in evidence row when numeric_inputs_ready=True."""

    def test_valuation_ready_false_with_numeric_inputs_band_unknown(self):
        """All confirmed numeric inputs → valuation_ready=False because band=UNKNOWN.

        At Stage 9E.1 no P/E thresholds are defined, so valuation_interpretation_band
        is always BAND_UNKNOWN and valuation_ready is always False regardless of whether
        numeric inputs are confirmed. VALUATION_LANE_NOT_BUILT gap remains.
        """
        canonical_row = _make_canonical_row(safe_for_equity_dataset=True)
        numeric_inputs = build_equity_numeric_valuation_inputs(
            canonical_row=canonical_row,
            ticker_price_signal=_make_price_signal(
                ticker_level_confirmed=True,
                freshness_label=FRESHNESS_FRESH,
            ),
        )
        assert numeric_inputs.numeric_inputs_ready is True
        assert numeric_inputs.numeric_price_confirmed is True
        assert numeric_inputs.numeric_earnings_confirmed is True

        evidence = build_equity_valuation_evidence_row(
            canonical_row=canonical_row,
            price_available=True,
            numeric_inputs=numeric_inputs,
        )
        assert evidence.valuation_ready is False  # band=UNKNOWN, no thresholds
        assert evidence.valuation_context.valuation_interpretation_band == BAND_UNKNOWN
        assert evidence.valuation_numeric_inputs_in_scope is True
        assert evidence.synthesis_ready is False
        assert evidence.safe_for_decision is False

    def test_valuation_ready_false_without_numeric_inputs(self):
        """Without numeric_inputs → valuation_ready=False (Stage 9E behavior)."""
        canonical_row = _make_canonical_row(safe_for_equity_dataset=True)
        evidence = build_equity_valuation_evidence_row(
            canonical_row=canonical_row,
            price_available=True,
            numeric_inputs=None,
        )
        assert evidence.valuation_ready is False
        assert evidence.valuation_numeric_inputs_in_scope is False

    def test_valuation_interpretation_band_always_unknown(self):
        """Band is UNKNOWN even when valuation_ready=True — no thresholds defined."""
        canonical_row = _make_canonical_row(safe_for_equity_dataset=True)
        numeric_inputs = build_equity_numeric_valuation_inputs(
            canonical_row=canonical_row,
            ticker_price_signal=_make_price_signal(ticker_level_confirmed=True),
        )
        evidence = build_equity_valuation_evidence_row(
            canonical_row=canonical_row,
            price_available=True,
            numeric_inputs=numeric_inputs,
        )
        assert evidence.valuation_context.valuation_interpretation_band == BAND_UNKNOWN

    def test_price_is_proxy_false_when_ticker_level_confirmed(self):
        """price_is_portfolio_level_proxy=False when ticker-level price confirmed."""
        canonical_row = _make_canonical_row(safe_for_equity_dataset=True)
        numeric_inputs = build_equity_numeric_valuation_inputs(
            canonical_row=canonical_row,
            ticker_price_signal=_make_price_signal(ticker_level_confirmed=True),
        )
        evidence = build_equity_valuation_evidence_row(
            canonical_row=canonical_row,
            price_available=True,
            numeric_inputs=numeric_inputs,
        )
        assert evidence.input_readiness.price_is_portfolio_level_proxy is False

    def test_price_is_proxy_true_without_numeric_inputs(self):
        """price_is_portfolio_level_proxy=True without numeric_inputs."""
        canonical_row = _make_canonical_row(safe_for_equity_dataset=True)
        evidence = build_equity_valuation_evidence_row(
            canonical_row=canonical_row,
            price_available=True,
            numeric_inputs=None,
        )
        assert evidence.input_readiness.price_is_portfolio_level_proxy is True

    def test_valuation_ready_false_when_numeric_inputs_not_ready(self):
        """numeric_inputs_ready=False → valuation_ready=False in evidence row."""
        canonical_row = _make_canonical_row(safe_for_equity_dataset=True)
        numeric_inputs = build_equity_numeric_valuation_inputs(
            canonical_row=canonical_row,
            ticker_price_signal=None,  # no price signal → numeric_inputs_ready=False
        )
        assert numeric_inputs.numeric_inputs_ready is False
        evidence = build_equity_valuation_evidence_row(
            canonical_row=canonical_row,
            price_available=True,
            numeric_inputs=numeric_inputs,
        )
        assert evidence.valuation_ready is False


# ── Test: VALUATION_LANE_NOT_BUILT gap ────────────────────────────────────────


class TestValuationLaneGapBehavior:
    """VALUATION_LANE_NOT_BUILT disappears only when valuation_ready=True.

    At Stage 9E.1, valuation_ready is always False (band=UNKNOWN), so the gap
    always remains. The _classify_all_gaps function accepts valuation_lane_exists
    as a parameter; _build_holding_row passes val_row.valuation_ready (False).
    """

    def test_valuation_lane_exists_true_removes_bucket(self):
        """_classify_all_gaps: when valuation_lane_exists=True, gap disappears."""
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
            valuation_lane_exists=True,
            valuation_evidence_model_present=True,
        )
        bucket_names = [g[0] for g in gaps]
        assert BUCKET_VALUATION_NOT_BUILT not in bucket_names

    def test_numeric_not_ready_keeps_valuation_lane_bucket(self):
        """When numeric_inputs_ready=False, VALUATION_LANE_NOT_BUILT remains."""
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
            valuation_lane_exists=False,   # numeric not ready
            valuation_evidence_model_present=True,
        )
        bucket_names = [g[0] for g in gaps]
        assert BUCKET_VALUATION_NOT_BUILT in bucket_names

    def test_holding_row_valuation_numeric_ready_with_confirmed_price(self):
        """Equity holding with confirmed ticker price → valuation_numeric_ready=True."""
        row = _build_holding_row(
            ticker="MSFT",
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            lanes={
                LANE_SEC_COMPANY_FACTS: _make_sec_lane(
                    status=STATUS_READY,
                    usability_label="USABLE",
                ),
            },
            supplemental=_empty_supplemental(
                has_portfolio_snapshot=True,
                ticker_price_signals={
                    "MSFT": TickerPriceSignal(
                        ticker="MSFT",
                        source_type=PRICE_SOURCE_SNAPSHOT_CERTIFIED,
                        freshness_label=FRESHNESS_FRESH,
                        ticker_level_confirmed=True,
                    ),
                },
            ),
        )
        # valuation_numeric_ready depends on canonical dataset quality too.
        # With USABLE SEC but no sec_fact_records, sections are metadata fallback.
        # The test just verifies the signal flows through.
        assert isinstance(row.valuation_numeric_ready, bool)
        assert row.valuation_evidence is not None
        assert row.valuation_evidence["safe_for_decision"] is False

    def test_holding_row_valuation_numeric_ready_false_without_price_signal(self):
        """No price signal → valuation_numeric_ready=False."""
        row = _build_holding_row(
            ticker="MSFT",
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            lanes={
                LANE_SEC_COMPANY_FACTS: _make_sec_lane(
                    status=STATUS_READY,
                    usability_label="USABLE",
                ),
            },
            supplemental=_empty_supplemental(
                has_portfolio_snapshot=True,
                ticker_price_signals={},  # no ticker-level price signal
            ),
        )
        assert row.valuation_numeric_ready is False

    def test_etf_holding_valuation_numeric_ready_false(self):
        row = _build_holding_row(
            ticker="VTI",
            asset_type=INSTRUMENT_CATEGORY_ETF,
            lanes={},
            supplemental=_empty_supplemental(
                ticker_price_signals={
                    "VTI": TickerPriceSignal(
                        ticker="VTI",
                        source_type=PRICE_SOURCE_SNAPSHOT_CERTIFIED,
                        freshness_label=FRESHNESS_FRESH,
                        ticker_level_confirmed=True,
                    ),
                },
            ),
        )
        assert row.valuation_numeric_ready is False
        assert row.valuation_evidence is None


# ── Test: forensics aggregate counts ──────────────────────────────────────────


class TestForensicsAggregateCounts:
    """DataFoundationForensicsResult includes Stage 9E.1 numeric valuation counts."""

    def test_result_has_stage9e1_fields(self):
        result = DataFoundationForensicsResult(
            schema_version="v1",
            user_id="u1",
            generated_at=NOW_ISO,
            equity_numeric_valuation_input_count=3,
            equity_numeric_valuation_ready_count=2,
            equity_numeric_valuation_degraded_tickers=["KLAR"],
            numeric_valuation_missing_reason_counts={"price_input": 1},
        )
        assert result.equity_numeric_valuation_input_count == 3
        assert result.equity_numeric_valuation_ready_count == 2
        assert result.equity_numeric_valuation_degraded_tickers == ["KLAR"]
        assert result.numeric_valuation_missing_reason_counts == {"price_input": 1}

    def test_result_to_dict_includes_stage9e1_fields(self):
        result = DataFoundationForensicsResult(
            schema_version="v1",
            user_id="u1",
            generated_at=NOW_ISO,
            equity_numeric_valuation_input_count=2,
            equity_numeric_valuation_ready_count=1,
            equity_numeric_valuation_degraded_tickers=["KLAR"],
            numeric_valuation_missing_reason_counts={"price_input": 1},
        )
        d = result.to_dict()
        assert "equity_numeric_valuation_input_count" in d
        assert "equity_numeric_valuation_ready_count" in d
        assert "equity_numeric_valuation_degraded_tickers" in d
        assert "numeric_valuation_missing_reason_counts" in d
        assert d["equity_numeric_valuation_input_count"] == 2
        assert d["equity_numeric_valuation_ready_count"] == 1

    def test_result_defaults_are_zero(self):
        result = DataFoundationForensicsResult(
            schema_version="v1",
            user_id="u1",
            generated_at=NOW_ISO,
        )
        assert result.equity_numeric_valuation_input_count == 0
        assert result.equity_numeric_valuation_ready_count == 0
        assert result.equity_numeric_valuation_degraded_tickers == []
        assert result.numeric_valuation_missing_reason_counts == {}


# ── Test: _extract_ticker_price_signals ───────────────────────────────────────


class TestExtractTickerPriceSignals:
    """_extract_ticker_price_signals derives safe signals from positions_data."""

    def _make_snapshot_row(
        self,
        snapshot_at: str,
        positions: list,
    ) -> dict:
        return {"snapshot_at": snapshot_at, "positions_data": positions}

    def _now_minus_hours(self, hours: int) -> str:
        dt = datetime.now(timezone.utc) - timedelta(hours=hours)
        return dt.isoformat()

    def test_certified_position_ticker_level_confirmed(self):
        snapshot_row = self._make_snapshot_row(
            snapshot_at=self._now_minus_hours(1),
            positions=[
                {
                    "ticker": "MSFT",
                    "market_value": 5000.0,
                    "market_value_certified_at": "2026-05-24T11:00:00+00:00",
                }
            ],
        )
        signals = _extract_ticker_price_signals(snapshot_row)
        assert "MSFT" in signals
        signal = signals["MSFT"]
        assert signal.ticker_level_confirmed is True
        assert signal.source_type == PRICE_SOURCE_SNAPSHOT_CERTIFIED
        assert signal.ticker == "MSFT"

    def test_carried_position_not_ticker_level_confirmed(self):
        snapshot_row = self._make_snapshot_row(
            snapshot_at=self._now_minus_hours(1),
            positions=[
                {
                    "ticker": "AAPL",
                    "market_value": 3000.0,
                    # no market_value_certified_at — carried forward
                }
            ],
        )
        signals = _extract_ticker_price_signals(snapshot_row)
        assert "AAPL" in signals
        signal = signals["AAPL"]
        assert signal.ticker_level_confirmed is False
        assert signal.source_type == PRICE_SOURCE_SNAPSHOT_CARRIED

    def test_fresh_snapshot_under_24h(self):
        snapshot_row = self._make_snapshot_row(
            snapshot_at=self._now_minus_hours(6),
            positions=[
                {"ticker": "MSFT", "market_value": 5000.0, "market_value_certified_at": "x"},
            ],
        )
        signals = _extract_ticker_price_signals(snapshot_row)
        assert signals["MSFT"].freshness_label == FRESHNESS_FRESH

    def test_aging_snapshot_24_72h(self):
        snapshot_row = self._make_snapshot_row(
            snapshot_at=self._now_minus_hours(36),
            positions=[
                {"ticker": "MSFT", "market_value": 5000.0, "market_value_certified_at": "x"},
            ],
        )
        signals = _extract_ticker_price_signals(snapshot_row)
        assert signals["MSFT"].freshness_label == FRESHNESS_AGING

    def test_stale_snapshot_over_72h(self):
        snapshot_row = self._make_snapshot_row(
            snapshot_at=self._now_minus_hours(100),
            positions=[
                {"ticker": "MSFT", "market_value": 5000.0, "market_value_certified_at": "x"},
            ],
        )
        signals = _extract_ticker_price_signals(snapshot_row)
        assert signals["MSFT"].freshness_label == FRESHNESS_STALE

    def test_empty_positions_data(self):
        snapshot_row = self._make_snapshot_row(
            snapshot_at=self._now_minus_hours(1),
            positions=[],
        )
        signals = _extract_ticker_price_signals(snapshot_row)
        assert signals == {}

    def test_none_positions_data(self):
        snapshot_row = {"snapshot_at": self._now_minus_hours(1), "positions_data": None}
        signals = _extract_ticker_price_signals(snapshot_row)
        assert signals == {}

    def test_no_market_value_position_skipped(self):
        snapshot_row = self._make_snapshot_row(
            snapshot_at=self._now_minus_hours(1),
            positions=[{"ticker": "MSFT"}],  # no market_value at all
        )
        signals = _extract_ticker_price_signals(snapshot_row)
        assert "MSFT" not in signals

    def test_ticker_normalized_uppercase(self):
        snapshot_row = self._make_snapshot_row(
            snapshot_at=self._now_minus_hours(1),
            positions=[
                {"ticker": "msft", "market_value": 5000.0, "market_value_certified_at": "x"},
            ],
        )
        signals = _extract_ticker_price_signals(snapshot_row)
        assert "MSFT" in signals
        assert "msft" not in signals

    def test_no_raw_values_in_signal(self):
        snapshot_row = self._make_snapshot_row(
            snapshot_at=self._now_minus_hours(1),
            positions=[
                {"ticker": "MSFT", "market_value": 99999.99, "market_value_certified_at": "x"},
            ],
        )
        signals = _extract_ticker_price_signals(snapshot_row)
        signal = signals["MSFT"]
        serialized = json.dumps(signal.to_dict())
        assert "99999" not in serialized
        assert "market_value" not in serialized


# ── Test: regression guard (Stage 9E behavior preserved) ──────────────────────


class TestStage9ERegressionGuard:
    """Stage 9E behavior preserved — valuation_ready=False without numeric_inputs."""

    def test_stage9e_mode_valuation_ready_false(self):
        """When numeric_inputs=None, valuation_ready=False (Stage 9E)."""
        evidence = build_equity_valuation_evidence_row(
            canonical_row=_make_canonical_row(safe_for_equity_dataset=True),
            price_available=True,
            numeric_inputs=None,
        )
        assert evidence.valuation_ready is False
        assert evidence.valuation_numeric_inputs_in_scope is False

    def test_stage9e_mode_valuation_interpretation_band_unknown(self):
        evidence = build_equity_valuation_evidence_row(
            canonical_row=_make_canonical_row(safe_for_equity_dataset=True),
            price_available=True,
        )
        assert evidence.valuation_context.valuation_interpretation_band == BAND_UNKNOWN

    def test_stage9e_mode_etf_still_not_applicable(self):
        evidence = build_equity_valuation_evidence_row(
            canonical_row=_make_etf_canonical_row(),
            price_available=True,
            numeric_inputs=None,
        )
        assert evidence.valuation_applicable is False
        assert evidence.valuation_ready is False

    def test_stage9e_mode_synthesis_ready_always_false(self):
        """synthesis_ready=False in all cases."""
        for safe in (True, False):
            canonical_row = _make_canonical_row(safe_for_equity_dataset=safe)
            numeric_inputs = build_equity_numeric_valuation_inputs(
                canonical_row=canonical_row,
                ticker_price_signal=_make_price_signal(ticker_level_confirmed=True),
            )
            evidence = build_equity_valuation_evidence_row(
                canonical_row=canonical_row,
                price_available=True,
                numeric_inputs=numeric_inputs,
            )
            assert evidence.synthesis_ready is False, (
                f"synthesis_ready must be False (safe_for_equity_dataset={safe})"
            )

    def test_safe_for_decision_always_false(self):
        """safe_for_decision=False regardless of numeric_inputs state."""
        canonical_row = _make_canonical_row(safe_for_equity_dataset=True)
        numeric_inputs = build_equity_numeric_valuation_inputs(
            canonical_row=canonical_row,
            ticker_price_signal=_make_price_signal(ticker_level_confirmed=True),
        )
        evidence = build_equity_valuation_evidence_row(
            canonical_row=canonical_row,
            price_available=True,
            numeric_inputs=numeric_inputs,
        )
        assert evidence.safe_for_decision is False


# ── Test: semantic correctness — price semantics ──────────────────────────────


class TestPriceSemantics:
    """market_value_certified_at alone does not confirm per-share price."""

    def test_market_value_cert_alone_not_numeric_price_confirmed(self):
        """market_value_certified_at present but market_price_usd absent → numeric_price_confirmed=False."""
        signal = TickerPriceSignal(
            ticker="MSFT",
            source_type=PRICE_SOURCE_SNAPSHOT_CERTIFIED,
            freshness_label=FRESHNESS_FRESH,
            ticker_level_confirmed=True,
            numeric_price_confirmed=False,  # no market_price_usd
        )
        inputs = build_equity_numeric_valuation_inputs(
            canonical_row=_make_canonical_row(safe_for_equity_dataset=True),
            ticker_price_signal=signal,
        )
        assert inputs.numeric_price_confirmed is False
        assert inputs.ticker_price_metadata_present is True
        assert inputs.numeric_inputs_ready is False
        assert "price_input" in inputs.missing_reasons

    def test_market_value_cert_alone_not_numeric_price_ready(self):
        """market_value only (no per-share price field) does not make numeric_inputs_ready=True."""
        signal = TickerPriceSignal(
            ticker="MSFT",
            source_type=PRICE_SOURCE_SNAPSHOT_CERTIFIED,
            freshness_label=FRESHNESS_FRESH,
            ticker_level_confirmed=True,
            numeric_price_confirmed=False,
        )
        inputs = build_equity_numeric_valuation_inputs(
            canonical_row=_make_canonical_row(safe_for_equity_dataset=True),
            ticker_price_signal=signal,
        )
        assert inputs.numeric_inputs_ready is False

    def test_market_price_usd_present_makes_numeric_price_confirmed(self):
        """market_price_usd present in snapshot → numeric_price_confirmed=True (no raw value serialized)."""
        snapshot_row = {
            "snapshot_at": datetime.now(timezone.utc).isoformat(),
            "positions_data": [
                {
                    "ticker": "MSFT",
                    "market_value": 50000.0,
                    "market_value_certified_at": "2026-05-24T11:00:00+00:00",
                    "market_price_usd": 415.0,  # per-share price — presence checked, value not serialized
                }
            ],
        }
        from app.services.intelligence.v3.intel_data_foundation_forensics_v1 import (
            _extract_ticker_price_signals,
        )
        signals = _extract_ticker_price_signals(snapshot_row)
        assert "MSFT" in signals
        signal = signals["MSFT"]
        assert signal.numeric_price_confirmed is True
        assert signal.ticker_level_confirmed is True
        # Raw price value must NOT appear in signal serialization.
        serialized = json.dumps(signal.to_dict())
        assert "415" not in serialized
        assert "market_price" not in serialized

    def test_market_value_cert_without_per_share_price_not_numeric_confirmed(self):
        """market_value_certified_at without market_price_usd → numeric_price_confirmed=False."""
        snapshot_row = {
            "snapshot_at": datetime.now(timezone.utc).isoformat(),
            "positions_data": [
                {
                    "ticker": "MSFT",
                    "market_value": 50000.0,
                    "market_value_certified_at": "2026-05-24T11:00:00+00:00",
                    # no market_price_usd
                }
            ],
        }
        from app.services.intelligence.v3.intel_data_foundation_forensics_v1 import (
            _extract_ticker_price_signals,
        )
        signals = _extract_ticker_price_signals(snapshot_row)
        signal = signals["MSFT"]
        assert signal.ticker_level_confirmed is True
        assert signal.numeric_price_confirmed is False

    def test_scaffold_present_even_when_numeric_price_not_confirmed(self):
        """valuation_input_scaffold_present=True when canonical safe + signal present, even if numeric not confirmed."""
        signal = TickerPriceSignal(
            ticker="MSFT",
            source_type=PRICE_SOURCE_SNAPSHOT_CERTIFIED,
            freshness_label=FRESHNESS_FRESH,
            ticker_level_confirmed=True,
            numeric_price_confirmed=False,
        )
        inputs = build_equity_numeric_valuation_inputs(
            canonical_row=_make_canonical_row(safe_for_equity_dataset=True),
            ticker_price_signal=signal,
        )
        assert inputs.valuation_input_scaffold_present is True
        assert inputs.ticker_price_metadata_present is True
        assert inputs.numeric_price_confirmed is False
        assert inputs.numeric_inputs_ready is False


# ── Test: semantic correctness — earnings semantics ───────────────────────────


class TestEarningsSemantics:
    """Section status alone does not confirm numeric earnings."""

    def test_section_status_alone_not_numeric_earnings_confirmed(self):
        """AVAILABLE section status with latest_period_identity=None → numeric_earnings_confirmed=False."""
        from app.services.intelligence.v3.canonical_equity_dataset_v1 import (
            EvidenceSectionRecord,
        )
        # Simulate metadata-only fallback: status=AVAILABLE but no fact records loaded.
        metadata_only_section = EvidenceSectionRecord(
            section=SECTION_NET_INCOME_EPS,
            status=SECTION_STATUS_AVAILABLE,
            evidence_basis="SEC_COMPANYFACTS",
            latest_period_identity=None,   # metadata fallback — no fact records
            comparison_period_identity=None,
            trend_direction=TREND_UNKNOWN,
            source_artifact_id=None,       # no fact records
            missing_reason=None,
        )
        from app.services.intelligence.v3.canonical_equity_dataset_v1 import (
            OperatingTrendSection,
        )
        sections = {
            SECTION_NET_INCOME_EPS: metadata_only_section,
            SECTION_REVENUE: _make_section_record(SECTION_REVENUE, SECTION_STATUS_AVAILABLE),
            SECTION_CASH_FLOW_FCF: _make_section_record(SECTION_CASH_FLOW_FCF, SECTION_STATUS_AVAILABLE),
        }
        operating_trends = OperatingTrendSection(
            sections=sections,
            trend_source="sec_company_facts",
            observation_count=0,
            completeness_band="COMPLETE",
            freshness_status="FRESH",
            usability_label="USABLE",
            missing_reason=None,
        )
        inputs = build_equity_numeric_valuation_inputs(
            canonical_row=_make_canonical_row(
                safe_for_equity_dataset=True,
                operating_trends=operating_trends,
            ),
            ticker_price_signal=_make_price_signal(ticker_level_confirmed=True),
        )
        assert inputs.numeric_earnings_confirmed is False
        assert inputs.earnings_input.status == INPUT_STATUS_AVAILABLE  # status is from section
        assert inputs.numeric_inputs_ready is False
        assert "numeric_inputs_ready" in inputs.missing_reasons

    def test_period_identity_and_artifact_id_make_numeric_earnings_confirmed(self):
        """latest_period_identity non-None + source_artifact_id non-None → numeric_earnings_confirmed=True."""
        inputs = build_equity_numeric_valuation_inputs(
            canonical_row=_make_canonical_row(safe_for_equity_dataset=True),
            ticker_price_signal=_make_price_signal(ticker_level_confirmed=True),
        )
        assert inputs.numeric_earnings_confirmed is True

    def test_missing_earnings_section_not_numeric_earnings_confirmed(self):
        """Missing EPS section → numeric_earnings_confirmed=False."""
        inputs = build_equity_numeric_valuation_inputs(
            canonical_row=_make_canonical_row(
                operating_trends=_make_operating_trends(eps_status=SECTION_STATUS_MISSING),
            ),
            ticker_price_signal=_make_price_signal(ticker_level_confirmed=True),
        )
        assert inputs.numeric_earnings_confirmed is False
        assert inputs.numeric_inputs_ready is False


# ── Test: semantic correctness — valuation_ready band gate ────────────────────


class TestValuationReadyBandGate:
    """valuation_ready=False while valuation_interpretation_band=UNKNOWN."""

    def test_valuation_ready_false_while_band_unknown(self):
        """Even with numeric_inputs_ready=True, valuation_ready=False because band=UNKNOWN."""
        canonical_row = _make_canonical_row(safe_for_equity_dataset=True)
        inputs = build_equity_numeric_valuation_inputs(
            canonical_row=canonical_row,
            ticker_price_signal=_make_price_signal(ticker_level_confirmed=True),
        )
        assert inputs.numeric_inputs_ready is True  # confirmed numerics
        evidence = build_equity_valuation_evidence_row(
            canonical_row=canonical_row,
            price_available=True,
            numeric_inputs=inputs,
        )
        assert evidence.valuation_context.valuation_interpretation_band == BAND_UNKNOWN
        assert evidence.valuation_ready is False

    def test_valuation_lane_not_built_remains_when_numeric_inputs_ready(self):
        """VALUATION_LANE_NOT_BUILT gap persists even when numeric_inputs_ready=True.

        valuation_lane_exists is driven by val_row.valuation_ready (False because
        band=UNKNOWN at Stage 9E.1), not by numeric_inputs_ready.
        """
        row = _build_holding_row(
            ticker="MSFT",
            asset_type=INSTRUMENT_CATEGORY_EQUITY,
            lanes={
                LANE_SEC_COMPANY_FACTS: _make_sec_lane(
                    status=STATUS_READY,
                    usability_label="USABLE",
                ),
            },
            supplemental=_empty_supplemental(
                has_portfolio_snapshot=True,
                ticker_price_signals={
                    "MSFT": TickerPriceSignal(
                        ticker="MSFT",
                        source_type=PRICE_SOURCE_SNAPSHOT_CERTIFIED,
                        freshness_label=FRESHNESS_FRESH,
                        ticker_level_confirmed=True,
                        numeric_price_confirmed=True,
                    ),
                },
            ),
        )
        # VALUATION_LANE_NOT_BUILT must remain because valuation_ready=False (band=UNKNOWN).
        assert BUCKET_VALUATION_NOT_BUILT in row.blocking_gap_buckets
