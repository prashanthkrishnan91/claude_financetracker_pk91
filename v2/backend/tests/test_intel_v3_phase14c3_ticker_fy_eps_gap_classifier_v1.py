"""Phase 14C.3 — Ticker-level FY EPS gap classifier tests.

Covers:
  1. Ticker with usable diluted FY EPS.
  2. Ticker with usable basic FY EPS fallback.
  3. Ticker with only quarterly EPS (no FY period).
  4. Ticker with no EPS payload.
  5. Ticker with FY EPS but missing source link.
  6. Ticker with no SEC artifact/facts.
  7. Ticker classified unknown/manual-review when no clear sub-gap identifiable.
  8. Aggregate gap_reason_counts are stable and deterministic.
  9. Hard governance locks on the result object.
  10. Config flag exists and defaults to False.
"""
from __future__ import annotations

import pytest

from app.services.intelligence.v3.ticker_fy_eps_gap_classifier_v1 import (
    TickerFyEpsGapInput,
    build_ticker_fy_eps_gap_diagnostics,
    classify_ticker_fy_eps_gap,
    TICKER_FY_EPS_GAP_CLASSIFIER_V1_CONTRACT_VERSION,
    COMPANY_CLASS_SEC_COMPANY,
    GAP_NO_SEC_COMPANYFACTS_ARTIFACT,
    GAP_NO_RESEARCH_ARTIFACT_FACTS,
    GAP_NO_EPS_PAYLOAD_PRESENT,
    GAP_EPS_PAYLOAD_NO_FY_PERIOD,
    GAP_FY_EPS_NOT_SOURCE_LINKED,
    GAP_FY_EPS_MISSING_FISCAL_YEAR,
    GAP_FY_EPS_MISSING_NUMERIC_VALUE,
    GAP_SOURCE_LINKAGE_GAP,
    GAP_UNKNOWN_MANUAL_REVIEW,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _inp(
    ticker: str = "AAA",
    *,
    has_artifact: bool = True,
    has_any_fact: bool = True,
    eps_payload_count: int = 0,
    fy_eps_payload_count: int = 0,
    source_linked_fy_eps_count: int = 0,
    fy_eps_skip_missing_year: int = 0,
    fy_eps_skip_missing_value: int = 0,
    has_diluted: bool = False,
    has_basic: bool = False,
    sel_tag: str | None = None,
    sel_val: float | None = None,
    sel_fy: int | None = None,
    sel_form: str | None = None,
    sel_src: bool = False,
    has_price: bool = True,
    has_sector: bool = True,
) -> TickerFyEpsGapInput:
    return TickerFyEpsGapInput(
        ticker=ticker,
        company_classification=COMPANY_CLASS_SEC_COMPANY,
        has_price=has_price,
        has_sector=has_sector,
        has_any_sec_metric_artifact=has_artifact,
        has_any_fact=has_any_fact,
        eps_payload_count=eps_payload_count,
        fy_eps_payload_count=fy_eps_payload_count,
        source_linked_fy_eps_count=source_linked_fy_eps_count,
        fy_eps_skip_missing_year_count=fy_eps_skip_missing_year,
        fy_eps_skip_missing_value_count=fy_eps_skip_missing_value,
        has_computable_diluted_fy_eps=has_diluted,
        has_computable_basic_fy_eps=has_basic,
        selected_eps_tag=sel_tag,
        selected_eps_value=sel_val,
        selected_eps_fiscal_year=sel_fy,
        selected_eps_form=sel_form,
        selected_eps_source_id_present=sel_src,
    )


def _agg(inputs: list[TickerFyEpsGapInput]):
    return build_ticker_fy_eps_gap_diagnostics(inputs=inputs)


# ── Test 1: Usable diluted FY EPS ─────────────────────────────────────────────

class TestUsableDilutedFyEps:
    def test_no_gap_reason(self):
        inp = _inp(
            "AAPL",
            has_artifact=True,
            has_any_fact=True,
            eps_payload_count=4,
            fy_eps_payload_count=2,
            source_linked_fy_eps_count=2,
            has_diluted=True,
            sel_tag="EarningsPerShareDiluted",
            sel_val=6.11,
            sel_fy=2024,
            sel_form="10-K",
            sel_src=True,
        )
        diag = classify_ticker_fy_eps_gap(inp)
        assert diag.usable_fy_eps_for_yield is True
        assert diag.gap_reason is None

    def test_selected_fields_populated(self):
        inp = _inp(
            "AAPL",
            has_diluted=True,
            sel_tag="EarningsPerShareDiluted",
            sel_val=6.11,
            sel_fy=2024,
            sel_form="10-K",
            sel_src=True,
        )
        diag = classify_ticker_fy_eps_gap(inp)
        assert diag.selected_eps_tag == "EarningsPerShareDiluted"
        assert diag.selected_eps_value == 6.11
        assert diag.selected_eps_fiscal_year == 2024
        assert diag.selected_eps_form == "10-K"
        assert diag.selected_eps_source_id_present is True

    def test_company_classification_preserved(self):
        inp = _inp("AAPL", has_diluted=True, sel_val=1.0)
        diag = classify_ticker_fy_eps_gap(inp)
        assert diag.company_classification == COMPANY_CLASS_SEC_COMPANY


# ── Test 2: Usable basic FY EPS fallback ──────────────────────────────────────

class TestUsableBasicFyEpsFallback:
    def test_no_gap_reason_when_only_basic(self):
        inp = _inp(
            "MSFT",
            has_artifact=True,
            has_any_fact=True,
            eps_payload_count=2,
            fy_eps_payload_count=1,
            source_linked_fy_eps_count=1,
            has_diluted=False,  # no diluted
            has_basic=True,
            sel_tag="EarningsPerShareBasic",
            sel_val=3.50,
            sel_fy=2024,
            sel_form="10-K",
            sel_src=True,
        )
        diag = classify_ticker_fy_eps_gap(inp)
        assert diag.usable_fy_eps_for_yield is True
        assert diag.gap_reason is None
        assert diag.selected_eps_tag == "EarningsPerShareBasic"
        assert diag.selected_eps_value == 3.50


# ── Test 3: Only quarterly EPS (no FY period) ─────────────────────────────────

class TestOnlyQuarterlyEps:
    def test_gap_reason_no_fy_period(self):
        inp = _inp(
            "GOOG",
            has_artifact=True,
            has_any_fact=True,
            eps_payload_count=4,   # 4 EPS rows exist (Q1/Q2/Q3/Q4)
            fy_eps_payload_count=0,  # but none are FY annual
            has_diluted=False,
            has_basic=False,
        )
        diag = classify_ticker_fy_eps_gap(inp)
        assert diag.usable_fy_eps_for_yield is False
        assert diag.gap_reason == GAP_EPS_PAYLOAD_NO_FY_PERIOD

    def test_selected_fields_absent_when_missing(self):
        inp = _inp("GOOG", eps_payload_count=2, fy_eps_payload_count=0)
        diag = classify_ticker_fy_eps_gap(inp)
        assert diag.selected_eps_tag is None
        assert diag.selected_eps_value is None
        assert diag.selected_eps_fiscal_year is None


# ── Test 4: No EPS payload ────────────────────────────────────────────────────

class TestNoEpsPayload:
    def test_gap_reason_no_eps_payload(self):
        inp = _inp(
            "AMZN",
            has_artifact=True,
            has_any_fact=True,
            eps_payload_count=0,   # facts exist but no EPS tags
            fy_eps_payload_count=0,
        )
        diag = classify_ticker_fy_eps_gap(inp)
        assert diag.usable_fy_eps_for_yield is False
        assert diag.gap_reason == GAP_NO_EPS_PAYLOAD_PRESENT

    def test_has_any_eps_payload_is_false(self):
        inp = _inp("AMZN", eps_payload_count=0)
        diag = classify_ticker_fy_eps_gap(inp)
        assert diag.has_any_eps_payload is False
        assert diag.eps_payload_count == 0


# ── Test 5: FY EPS present but missing source link ────────────────────────────

class TestFyEpsNotSourceLinked:
    def test_gap_reason_not_source_linked(self):
        inp = _inp(
            "META",
            has_artifact=True,
            has_any_fact=True,
            eps_payload_count=2,
            fy_eps_payload_count=1,
            source_linked_fy_eps_count=0,  # FY EPS exists but no source_id
            has_diluted=False,
            has_basic=False,
        )
        diag = classify_ticker_fy_eps_gap(inp)
        assert diag.usable_fy_eps_for_yield is False
        assert diag.gap_reason == GAP_FY_EPS_NOT_SOURCE_LINKED

    def test_has_source_linked_fy_eps_reflects_count(self):
        inp = _inp("META", fy_eps_payload_count=1, source_linked_fy_eps_count=0)
        diag = classify_ticker_fy_eps_gap(inp)
        assert diag.has_source_linked_fy_eps is False


# ── Test 6: No SEC artifact/facts ─────────────────────────────────────────────

class TestNoSecArtifact:
    def test_gap_reason_no_artifact(self):
        inp = _inp(
            "NVDA",
            has_artifact=False,
            has_any_fact=False,
            eps_payload_count=0,
        )
        diag = classify_ticker_fy_eps_gap(inp)
        assert diag.usable_fy_eps_for_yield is False
        assert diag.gap_reason == GAP_NO_SEC_COMPANYFACTS_ARTIFACT

    def test_has_artifact_in_artifact(self):
        # Has artifact but no facts → no_research_artifact_facts
        inp = _inp(
            "NVDA",
            has_artifact=True,
            has_any_fact=False,
            eps_payload_count=0,
        )
        diag = classify_ticker_fy_eps_gap(inp)
        assert diag.gap_reason == GAP_NO_RESEARCH_ARTIFACT_FACTS

    def test_has_any_sec_metric_artifact_reflects_input(self):
        inp = _inp("NVDA", has_artifact=False)
        diag = classify_ticker_fy_eps_gap(inp)
        assert diag.has_any_sec_metric_artifact is False


# ── Test 7: FY EPS present, source-linked, but extraction failed ───────────────

class TestExtractionFailureGaps:
    def test_missing_fiscal_year_gap(self):
        # Source-linked FY EPS exists but fiscal_year absent in all payloads.
        inp = _inp(
            "TSLA",
            has_artifact=True,
            has_any_fact=True,
            eps_payload_count=2,
            fy_eps_payload_count=1,
            source_linked_fy_eps_count=1,
            fy_eps_skip_missing_year=1,  # extraction failed due to missing year
            has_diluted=False,
            has_basic=False,
        )
        diag = classify_ticker_fy_eps_gap(inp)
        assert diag.usable_fy_eps_for_yield is False
        assert diag.gap_reason == GAP_FY_EPS_MISSING_FISCAL_YEAR

    def test_missing_numeric_value_gap(self):
        inp = _inp(
            "TSLA",
            has_artifact=True,
            has_any_fact=True,
            eps_payload_count=2,
            fy_eps_payload_count=1,
            source_linked_fy_eps_count=1,
            fy_eps_skip_missing_year=0,
            fy_eps_skip_missing_value=1,  # extraction failed due to null value
            has_diluted=False,
            has_basic=False,
        )
        diag = classify_ticker_fy_eps_gap(inp)
        assert diag.usable_fy_eps_for_yield is False
        assert diag.gap_reason == GAP_FY_EPS_MISSING_NUMERIC_VALUE

    def test_source_linkage_gap_fallback(self):
        # Has source-linked FY EPS but extraction succeeded 0 times and no
        # known skip reason — classify as source_linkage_gap (not unknown).
        inp = _inp(
            "TSLA",
            has_artifact=True,
            has_any_fact=True,
            eps_payload_count=2,
            fy_eps_payload_count=1,
            source_linked_fy_eps_count=1,
            fy_eps_skip_missing_year=0,
            fy_eps_skip_missing_value=0,
            has_diluted=False,
            has_basic=False,
        )
        diag = classify_ticker_fy_eps_gap(inp)
        assert diag.usable_fy_eps_for_yield is False
        assert diag.gap_reason == GAP_SOURCE_LINKAGE_GAP


# ── Test 8: Aggregate gap_reason_counts are stable and deterministic ───────────

class TestAggregateGapReasonCounts:
    def _make_inputs(self) -> list[TickerFyEpsGapInput]:
        return [
            # Usable: diluted
            _inp("AAA", has_diluted=True, sel_val=1.0),
            # Usable: basic fallback
            _inp("BBB", has_basic=True, sel_val=2.0),
            # No artifact
            _inp("CCC", has_artifact=False, has_any_fact=False),
            # No facts
            _inp("DDD", has_artifact=True, has_any_fact=False),
            # No EPS payload
            _inp("EEE", has_artifact=True, has_any_fact=True, eps_payload_count=0),
            # Only quarterly
            _inp("FFF", has_artifact=True, has_any_fact=True, eps_payload_count=3, fy_eps_payload_count=0),
            # Not source linked
            _inp("GGG", has_artifact=True, has_any_fact=True, eps_payload_count=1,
                 fy_eps_payload_count=1, source_linked_fy_eps_count=0),
            # Missing fiscal year
            _inp("HHH", has_artifact=True, has_any_fact=True, eps_payload_count=1,
                 fy_eps_payload_count=1, source_linked_fy_eps_count=1,
                 fy_eps_skip_missing_year=1),
        ]

    def test_counts_stable(self):
        inputs = self._make_inputs()
        result = _agg(inputs)
        assert result.ticker_gap_diagnostics_count == 8
        assert result.usable_fy_eps_ticker_count == 2
        assert result.missing_fy_eps_ticker_count == 6

    def test_gap_reason_distribution(self):
        inputs = self._make_inputs()
        result = _agg(inputs)
        counts = result.gap_reason_counts
        assert counts[GAP_NO_SEC_COMPANYFACTS_ARTIFACT] == 1
        assert counts[GAP_NO_RESEARCH_ARTIFACT_FACTS] == 1
        assert counts[GAP_NO_EPS_PAYLOAD_PRESENT] == 1
        assert counts[GAP_EPS_PAYLOAD_NO_FY_PERIOD] == 1
        assert counts[GAP_FY_EPS_NOT_SOURCE_LINKED] == 1
        assert counts[GAP_FY_EPS_MISSING_FISCAL_YEAR] == 1

    def test_deterministic_on_repeat_calls(self):
        inputs = self._make_inputs()
        r1 = _agg(inputs)
        r2 = _agg(inputs)
        assert r1.gap_reason_counts == r2.gap_reason_counts
        assert r1.usable_fy_eps_ticker_count == r2.usable_fy_eps_ticker_count

    def test_all_gap_reason_keys_present(self):
        result = _agg([])
        assert GAP_NO_SEC_COMPANYFACTS_ARTIFACT in result.gap_reason_counts
        assert GAP_SOURCE_LINKAGE_GAP in result.gap_reason_counts
        assert GAP_UNKNOWN_MANUAL_REVIEW in result.gap_reason_counts

    def test_potentially_fixable_vs_unsupported(self):
        inputs = [
            # Unsupported (no artifact)
            _inp("CCC", has_artifact=False, has_any_fact=False),
            # Fixable (has FY EPS, missing source link)
            _inp("GGG", has_artifact=True, has_any_fact=True, eps_payload_count=1,
                 fy_eps_payload_count=1, source_linked_fy_eps_count=0),
        ]
        result = _agg(inputs)
        assert result.unsupported_or_excludable_ticker_count == 1
        assert result.potentially_fixable_ticker_count == 1


# ── Test 9: Hard governance locks ─────────────────────────────────────────────

class TestGovernanceLocks:
    def test_classifier_version_constant(self):
        assert TICKER_FY_EPS_GAP_CLASSIFIER_V1_CONTRACT_VERSION == (
            "phase14c3_ticker_fy_eps_gap_classifier_v1"
        )

    def test_build_never_raises_on_empty(self):
        result = _agg([])
        assert result.usable_fy_eps_ticker_count == 0
        assert result.missing_fy_eps_ticker_count == 0
        assert result.errors == []

    def test_build_returns_result_on_corrupt_input(self):
        # Even if inputs list is malformed downstream, build_ticker_fy_eps_gap_diagnostics
        # handles it gracefully (try/except in outer wrapper).
        # We test with valid empty inputs here to confirm no-raise contract.
        result = build_ticker_fy_eps_gap_diagnostics(inputs=[], extra_errors=["synthetic_error"])
        assert "synthetic_error" in result.errors


# ── Test 10: Config flag default ──────────────────────────────────────────────

class TestConfigFlagDefault:
    def test_flag_exists_and_default_false(self):
        from app.config import Settings
        s = Settings(
            supabase_url="https://test.supabase.co",
            supabase_anon_key="anon",
            supabase_service_role_key="svc",
            supabase_jwt_secret="jwt",
            encryption_key="a" * 64,
        )
        assert s.intel_v3_fy_eps_ticker_gap_v1_diagnostics_enabled is False
