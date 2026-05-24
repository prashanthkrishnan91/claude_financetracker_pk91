"""Stage 9C — SEC CompanyFacts Readiness Diagnostic + Root-Cause Repair.

Regression tests verifying:

1. Same metric, different fiscal years → no contradiction (group key splits on fy).
2. Same metric, same quarter, quarterly vs YTD durations → no contradiction
   (group key splits on period_start/period_end).
3. Same metric, different units → no contradiction (group key splits on unit).
4. Same metric, same complete identity, conflicting values → contradiction preserved.
5. Missing identity fields → NOT_EVALUABLE or LIMITED honestly (no false clean).
6. Stale model version (sec_xbrl_companyfacts_v1) is flagged as stale by the
   diagnostic; artifact with current version (sec_xbrl_companyfacts_v2) is not.
7. is_false_contradiction_candidate is True for SUPPRESSED_CONTRADICTED + stale model.
8. is_false_contradiction_candidate is False for SUPPRESSED_CONTRADICTED + current model.
9. Forensics HoldingForensicsRow includes sec_companyfacts_diagnostic when artifact
   is weak equity; None when artifact is usable; None for ETF/crypto.
10. diagnose_sec_companyfacts_readiness produces safe output (no raw values, no PII).
11. Decision path is unchanged: no decide() import anywhere in this path.
12. LaneCoverage.contradiction_count and .not_evaluable_reason are populated from
    the stored payload by _build_lane_coverage.
13. Model version sec_xbrl_companyfacts_v2 is used by the adapter (idempotency bump).

No production Supabase access. All DB calls use a FakeSupabaseClient.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

import pytest

from app.services.intelligence.research_workers.contracts import FactRecord
from app.services.intelligence.research_workers.sec_companyfacts_adapter_v1 import (
    _MODEL_VERSION as SEC_ADAPTER_MODEL_VERSION,
    build_sec_companyfacts_worker_output,
)
from app.services.intelligence.v3.contradiction_detector_v1 import (
    detect_contradictions,
)
from app.services.intelligence.v3.sec_companyfacts_readiness_diagnostic_v1 import (
    CATEGORY_STRONG,
    CATEGORY_LIMITED,
    CATEGORY_SUPPRESSED_CONTRADICTED,
    CATEGORY_SUPPRESSED_INCOMPLETE,
    CATEGORY_NOT_EVALUABLE,
    CATEGORY_STALE_OR_UNKNOWN,
    DIAGNOSTIC_VERSION,
    SEC_COMPANYFACTS_CURRENT_MODEL_VERSION,
    SEC_COMPANYFACTS_PREV_MODEL_VERSION,
    diagnose_sec_companyfacts_readiness,
)
from app.services.intelligence.v3.research_evidence_coverage_read_model_v1 import (
    LaneCoverage,
    LANE_SEC_COMPANY_FACTS,
    _build_lane_coverage,
)
from app.services.intelligence.v3.intel_data_foundation_forensics_v1 import (
    BUCKET_SEC_EXISTS_WEAK,
    HoldingForensicsRow,
    _build_holding_row,
    _SupplementalData,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _sec_fact(
    metric: str,
    value: float,
    unit: str = "USD",
    fy: Optional[int] = 2023,
    fp: Optional[str] = "FY",
    start: Optional[str] = "2022-09-25",
    end: Optional[str] = "2023-09-30",
    filed: str = "2023-11-03",
    accn: str = "ACC-K",
    frame: Optional[str] = None,
) -> FactRecord:
    """Build a FactRecord matching what the SEC CompanyFacts adapter emits."""
    period = f"{fy}-{fp}:{start}..{end}" if start and end else f"{fy}-{fp}"
    return FactRecord(
        fact_kind="metric_observation",
        structured_payload={
            "metric_name": metric,
            "value": value,
            "unit": unit,
            "fiscal_year": fy,
            "fiscal_period": fp,
            "period_start": start,
            "period_end": end,
            "frame": frame,
            "filed": filed,
            "accession_number": accn,
            "provider": "sec_edgar",
        },
        period=period,
        as_of=filed,
    )


def _fake_lane_row(
    usability_label: str = "USABLE",
    is_usable: bool = True,
    freshness: str = "FRESH",
    contradiction_evaluable: bool = True,
    has_contradictions: bool = False,
    contradiction_count: int = 0,
    not_evaluable_reason: Optional[str] = None,
    completeness_band: str = "COMPLETE",
    source_authority: str = "PRIMARY_AUTHORITY",
    model_version: str = SEC_COMPANYFACTS_CURRENT_MODEL_VERSION,
    artifact_id: Optional[str] = None,
) -> dict[str, Any]:
    """Build a fake DB row for _build_lane_coverage, mimicking the payload structure."""
    aid = artifact_id or str(uuid.uuid4())
    contradiction = {
        "is_evaluable": contradiction_evaluable,
        "has_contradictions": has_contradictions,
        "contradiction_count": contradiction_count,
        "not_evaluable_reason": not_evaluable_reason,
    }
    return {
        "id": aid,
        "artifact_type": "fundamental_quality",
        "skill_pack": "sec_companyfacts_evidence_v1",
        "scope_kind": "ticker",
        "ticker": "MSFT",
        "confidence_or_trust_level": "HIGH",
        "freshness_status": freshness,
        "generated_at": "2026-05-24T10:00:00+00:00",
        "expires_at": None,
        "is_active": True,
        "model_version": model_version,
        "safe_for_decision": False,
        "payload": {
            "truth_usability_assessment": {
                "usability_label": usability_label,
                "is_usable": is_usable,
                "suppression_reason": (
                    "material_contradiction_detected:contradiction_count=1"
                    if "CONTRADICTED" in usability_label
                    else (
                        "evidence_completeness_thin:missing_requirements=comparable_facts"
                        if "INCOMPLETE" in usability_label
                        else None
                    )
                ),
            },
            "source_credibility_assessment": {
                "strongest_authority_level": source_authority,
                "is_insufficient": False,
            },
            "contradiction_assessment": contradiction,
            "evidence_completeness_assessment": {
                "completeness_band": completeness_band,
            },
        },
    }


def _supplemental(
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
    )


# ── Group 1: Contradiction grouping regression (core logic) ───────────────────


class TestSecContradictionGroupingRegression:
    """Core regression: the SEC-specific group key must NOT produce false
    contradictions from legitimate multi-period or multi-unit XBRL data."""

    def test_different_fiscal_years_no_contradiction(self):
        """Same metric, different fiscal years → separate group keys → no contradiction."""
        facts = [
            _sec_fact("Revenues", 394_328e6, fy=2022, start="2021-09-26", end="2022-09-24",
                      filed="2022-10-28", accn="ACC-22"),
            _sec_fact("Revenues", 383_285e6, fy=2023, start="2022-09-25", end="2023-09-30",
                      filed="2023-11-03", accn="ACC-23"),
        ]
        result = detect_contradictions(facts)
        assert result.has_contradictions is False
        assert result.is_evaluable is True
        assert result.contradiction_count == 0

    def test_different_units_no_contradiction(self):
        """Same metric, different units → separate group keys → no contradiction."""
        facts = [
            _sec_fact("EarningsPerShareBasic", 6.16, unit="USD/shares"),
            _sec_fact("EarningsPerShareBasic", 6_160_000.0, unit="USD"),
        ]
        result = detect_contradictions(facts)
        assert result.has_contradictions is False

    def test_quarterly_vs_ytd_same_fy_fp_no_contradiction(self):
        """Q3 quarterly (3-month) vs Q3 YTD (9-month) — same fy, same fp, same filed,
        but different period_start → different group keys → no contradiction."""
        facts = [
            # 3-month quarterly: Apr–Jul
            _sec_fact("Revenues", 81_797e6, fp="Q3",
                      start="2023-04-02", end="2023-07-01",
                      filed="2023-08-04", accn="ACC-Q3"),
            # 9-month YTD: Sep–Jul
            _sec_fact("Revenues", 244_776e6, fp="Q3",
                      start="2022-09-25", end="2023-07-01",
                      filed="2023-08-04", accn="ACC-Q3"),
        ]
        result = detect_contradictions(facts)
        assert result.has_contradictions is False, (
            "Quarterly vs YTD must not produce a false contradiction — "
            "they share fy/fp/filed but have different period_start."
        )

    def test_different_filing_dates_no_contradiction(self):
        """Same metric, same period, different filed dates → different group keys."""
        facts = [
            _sec_fact("NetIncomeLoss", 96_995e6, filed="2023-11-03", accn="ACC-K-1"),
            _sec_fact("NetIncomeLoss", 96_995e6, filed="2023-11-15", accn="ACC-K-2"),
        ]
        result = detect_contradictions(facts)
        assert result.has_contradictions is False

    def test_true_same_identity_contradiction_preserved(self):
        """Same metric + same complete identity but conflicting values → true contradiction."""
        facts = [
            # Restatement: same filed date, same identity, different value
            _sec_fact("Revenues", 383_285e6,
                      filed="2023-11-03", accn="ACC-K"),
            _sec_fact("Revenues", 400_000e6,
                      filed="2023-11-03", accn="ACC-K-RESTATE"),
        ]
        result = detect_contradictions(facts)
        assert result.has_contradictions is True, (
            "Conflicting values with same identity (metric/unit/fy/fp/start/end/frame/filed) "
            "must remain flagged as a contradiction."
        )
        assert result.contradiction_count >= 1

    def test_no_contradiction_when_values_within_tolerance(self):
        """Values within 1% relative tolerance are NOT contradictions."""
        base = 383_285e6
        within_one_pct = base * 1.005  # 0.5% difference
        facts = [
            _sec_fact("Revenues", base, filed="2023-11-03", accn="ACC-A"),
            _sec_fact("Revenues", within_one_pct, filed="2023-11-03", accn="ACC-A"),
        ]
        result = detect_contradictions(facts)
        assert result.has_contradictions is False

    def test_different_metrics_never_conflict(self):
        """Different metric names never conflict regardless of other fields."""
        facts = [
            _sec_fact("Revenues", 383_285e6),
            _sec_fact("NetIncomeLoss", 96_995e6),
            _sec_fact("OperatingIncomeLoss", 114_301e6),
        ]
        result = detect_contradictions(facts)
        assert result.has_contradictions is False
        assert result.comparable_fact_count == 3

    def test_empty_period_start_end_groups_correctly(self):
        """Balance-sheet (instant) observations with no period_start/end
        use empty strings in the key. Two with same identity but different
        values are a real contradiction."""
        # Same instant balance-sheet metric from two filings, different values
        facts = [
            FactRecord(
                fact_kind="metric_observation",
                structured_payload={
                    "metric_name": "Assets",
                    "value": 352_755e6,
                    "unit": "USD",
                    "fiscal_year": 2023,
                    "fiscal_period": "FY",
                    "period_start": None,
                    "period_end": "2023-09-30",
                    "frame": "CY2023Q4I",
                    "filed": "2023-11-03",
                    "accession_number": "ACC-K-A",
                    "provider": "sec_edgar",
                },
                period="2023-FY:None..2023-09-30",
                as_of="2023-11-03",
            ),
            FactRecord(
                fact_kind="metric_observation",
                structured_payload={
                    "metric_name": "Assets",
                    "value": 400_000e6,  # conflicting value, same identity
                    "unit": "USD",
                    "fiscal_year": 2023,
                    "fiscal_period": "FY",
                    "period_start": None,
                    "period_end": "2023-09-30",
                    "frame": "CY2023Q4I",
                    "filed": "2023-11-03",
                    "accession_number": "ACC-K-B",
                    "provider": "sec_edgar",
                },
                period="2023-FY:None..2023-09-30",
                as_of="2023-11-03",
            ),
        ]
        result = detect_contradictions(facts)
        # Same identity (unit/fy/fp/start=None/end/frame/filed) → true contradiction
        assert result.has_contradictions is True

    def test_missing_identity_fields_not_evaluable_or_limited(self):
        """When structured_payload lacks metric_name or value, the fact is
        non-comparable and the assessment is not_evaluable_reason=
        'insufficient_comparable_facts', not a false clean result."""
        facts = [
            FactRecord(
                fact_kind="metric_observation",
                structured_payload={
                    # no metric_name, no value — non-comparable
                    "provider": "sec_edgar",
                    "fiscal_year": 2023,
                },
                period="2023-FY",
                as_of="2023-11-03",
            ),
        ]
        result = detect_contradictions(facts)
        assert result.is_evaluable is False
        assert result.not_evaluable_reason == "insufficient_comparable_facts"
        assert result.has_contradictions is False

    def test_non_sec_facts_use_generic_group_key(self):
        """Non-SEC facts fall through to the generic group key (no provider=sec_edgar)."""
        facts = [
            FactRecord(
                fact_kind="financial_metric",
                structured_payload={
                    "metric_name": "revenue",
                    "value": 100.0,
                    "provider": "yfinance",
                },
                period="2023-Q1",
                as_of="2023-04-01",
            ),
            FactRecord(
                fact_kind="financial_metric",
                structured_payload={
                    "metric_name": "revenue",
                    "value": 200.0,
                    "provider": "yfinance",
                },
                period="2023-Q1",
                as_of="2023-04-01",
            ),
        ]
        result = detect_contradictions(facts)
        # Generic group key: same claim_key + fact_kind + period + as_of → contradiction
        assert result.has_contradictions is True


# ── Group 2: Diagnostic module ────────────────────────────────────────────────


class TestSecCompanyFactsReadinessDiagnostic:
    """Verify diagnose_sec_companyfacts_readiness produces correct categories
    and safe output for all key scenarios."""

    def test_strong_artifact_current_model(self):
        d = diagnose_sec_companyfacts_readiness(
            artifact_id="abc123",
            generated_at="2026-05-24T10:00:00+00:00",
            model_version=SEC_COMPANYFACTS_CURRENT_MODEL_VERSION,
            observation_count=45,
            freshness_status="FRESH",
            source_authority="PRIMARY_AUTHORITY",
            completeness_band="COMPLETE",
            usability_label="USABLE",
            suppression_reason=None,
            contradiction_evaluable=True,
            contradiction_count=0,
            not_evaluable_reason=None,
        )
        assert d.readiness_category == CATEGORY_STRONG
        assert d.is_stale_model_version is False
        assert d.is_false_contradiction_candidate is False
        assert d.safe_for_decision is False
        assert d.diagnostic_version == DIAGNOSTIC_VERSION

    def test_limited_artifact_current_model(self):
        d = diagnose_sec_companyfacts_readiness(
            artifact_id="abc",
            generated_at=None,
            model_version=SEC_COMPANYFACTS_CURRENT_MODEL_VERSION,
            observation_count=10,
            freshness_status="FRESH",
            source_authority="OFFICIAL_FREE_SOURCE",
            completeness_band="PARTIAL",
            usability_label="USABLE_WITH_LIMITATIONS",
            suppression_reason=None,
            contradiction_evaluable=True,
            contradiction_count=0,
            not_evaluable_reason=None,
        )
        assert d.readiness_category == CATEGORY_LIMITED
        assert d.is_stale_model_version is False
        assert d.is_false_contradiction_candidate is False

    def test_suppressed_contradicted_stale_model_is_false_contradiction_candidate(self):
        """SUPPRESSED_CONTRADICTED + stale model → is_false_contradiction_candidate=True."""
        d = diagnose_sec_companyfacts_readiness(
            artifact_id="old-art",
            generated_at="2026-03-01T10:00:00+00:00",
            model_version=SEC_COMPANYFACTS_PREV_MODEL_VERSION,  # v1 = stale
            observation_count=30,
            freshness_status="FRESH",
            source_authority="PRIMARY_AUTHORITY",
            completeness_band="PARTIAL",
            usability_label="SUPPRESSED_CONTRADICTED",
            suppression_reason="material_contradiction_detected:contradiction_count=3",
            contradiction_evaluable=True,
            contradiction_count=3,
            not_evaluable_reason=None,
        )
        assert d.readiness_category == CATEGORY_SUPPRESSED_CONTRADICTED
        assert d.is_stale_model_version is True
        assert d.is_false_contradiction_candidate is True
        assert SEC_COMPANYFACTS_PREV_MODEL_VERSION in d.diagnosis_note
        assert "5H.3" in d.diagnosis_note or "pre-Stage" in d.diagnosis_note

    def test_suppressed_contradicted_current_model_true_contradiction(self):
        """SUPPRESSED_CONTRADICTED + current model → is_false_contradiction_candidate=False."""
        d = diagnose_sec_companyfacts_readiness(
            artifact_id="new-art",
            generated_at="2026-05-24T10:00:00+00:00",
            model_version=SEC_COMPANYFACTS_CURRENT_MODEL_VERSION,  # v2
            observation_count=30,
            freshness_status="FRESH",
            source_authority="PRIMARY_AUTHORITY",
            completeness_band="PARTIAL",
            usability_label="SUPPRESSED_CONTRADICTED",
            suppression_reason="material_contradiction_detected:contradiction_count=1",
            contradiction_evaluable=True,
            contradiction_count=1,
            not_evaluable_reason=None,
        )
        assert d.readiness_category == CATEGORY_SUPPRESSED_CONTRADICTED
        assert d.is_stale_model_version is False
        assert d.is_false_contradiction_candidate is False
        assert "restatement" in d.diagnosis_note.lower() or "current" in d.diagnosis_note.lower()

    def test_suppressed_incomplete_thin_completeness(self):
        d = diagnose_sec_companyfacts_readiness(
            artifact_id="thin-art",
            generated_at=None,
            model_version=SEC_COMPANYFACTS_CURRENT_MODEL_VERSION,
            observation_count=2,
            freshness_status="FRESH",
            source_authority="PRIMARY_AUTHORITY",
            completeness_band="THIN",
            usability_label="SUPPRESSED_INCOMPLETE",
            suppression_reason="evidence_completeness_thin",
            contradiction_evaluable=False,
            contradiction_count=None,
            not_evaluable_reason="insufficient_comparable_facts",
        )
        assert d.readiness_category == CATEGORY_SUPPRESSED_INCOMPLETE
        assert "THIN" in d.diagnosis_note

    def test_not_evaluable_stale_model_version(self):
        """NOT_EVALUABLE + stale model → enrichment missing before Stage 5B-5E."""
        d = diagnose_sec_companyfacts_readiness(
            artifact_id="very-old",
            generated_at="2025-12-01T00:00:00+00:00",
            model_version=SEC_COMPANYFACTS_PREV_MODEL_VERSION,
            observation_count=None,
            freshness_status="UNKNOWN",
            source_authority=None,
            completeness_band=None,
            usability_label="NOT_EVALUABLE",
            suppression_reason="missing_enrichment_metadata",
            contradiction_evaluable=False,
            contradiction_count=None,
            not_evaluable_reason=None,
        )
        assert d.readiness_category == CATEGORY_NOT_EVALUABLE
        assert d.is_stale_model_version is True
        assert d.is_false_contradiction_candidate is False

    def test_stale_freshness_category(self):
        d = diagnose_sec_companyfacts_readiness(
            artifact_id="stale-art",
            generated_at="2025-10-01T00:00:00+00:00",
            model_version=SEC_COMPANYFACTS_CURRENT_MODEL_VERSION,
            observation_count=20,
            freshness_status="STALE",
            source_authority="PRIMARY_AUTHORITY",
            completeness_band="COMPLETE",
            usability_label="USABLE",
            suppression_reason=None,
            contradiction_evaluable=True,
            contradiction_count=0,
            not_evaluable_reason=None,
        )
        assert d.readiness_category == CATEGORY_STALE_OR_UNKNOWN

    def test_missing_model_version_is_stale(self):
        """None model_version → is_stale_model_version=True."""
        d = diagnose_sec_companyfacts_readiness(
            artifact_id="no-mv",
            generated_at=None,
            model_version=None,
            observation_count=None,
            freshness_status=None,
            source_authority=None,
            completeness_band=None,
            usability_label=None,
            suppression_reason=None,
            contradiction_evaluable=None,
            contradiction_count=None,
            not_evaluable_reason=None,
        )
        assert d.is_stale_model_version is True
        assert d.readiness_category == CATEGORY_NOT_EVALUABLE

    def test_to_dict_has_no_raw_payload(self):
        """to_dict() output must not include raw fact values or source URLs."""
        d = diagnose_sec_companyfacts_readiness(
            artifact_id="abc",
            generated_at="2026-05-24T10:00:00+00:00",
            model_version=SEC_COMPANYFACTS_CURRENT_MODEL_VERSION,
            observation_count=10,
            freshness_status="FRESH",
            source_authority="PRIMARY_AUTHORITY",
            completeness_band="COMPLETE",
            usability_label="USABLE",
            suppression_reason=None,
            contradiction_evaluable=True,
            contradiction_count=0,
            not_evaluable_reason=None,
        )
        result = d.to_dict()
        assert result["safe_for_decision"] is False
        assert "payload" not in result
        assert "source_url" not in result
        assert "value" not in result
        assert "api_key" not in result

    def test_diagnosis_note_is_string(self):
        """diagnosis_note must always be a non-empty string."""
        d = diagnose_sec_companyfacts_readiness(
            artifact_id=None,
            generated_at=None,
            model_version=SEC_COMPANYFACTS_CURRENT_MODEL_VERSION,
            observation_count=None,
            freshness_status="FRESH",
            source_authority="PRIMARY_AUTHORITY",
            completeness_band="COMPLETE",
            usability_label="USABLE",
            suppression_reason=None,
            contradiction_evaluable=True,
            contradiction_count=0,
            not_evaluable_reason=None,
        )
        assert isinstance(d.diagnosis_note, str)
        assert len(d.diagnosis_note) > 0


# ── Group 3: LaneCoverage contradiction metadata ─────────────────────────────


class TestLaneCoverageContradictionMetadata:
    """LaneCoverage.contradiction_count and .not_evaluable_reason are populated
    by _build_lane_coverage from the stored payload."""

    def test_contradiction_count_populated_when_evaluable(self):
        row = _fake_lane_row(
            usability_label="SUPPRESSED_CONTRADICTED",
            is_usable=False,
            contradiction_evaluable=True,
            has_contradictions=True,
            contradiction_count=2,
        )
        cov = _build_lane_coverage(
            lane=LANE_SEC_COMPANY_FACTS,
            artifact_type="fundamental_quality",
            skill_pack="sec_companyfacts_evidence_v1",
            scope_kind="ticker",
            ticker="MSFT",
            row=row,
        )
        assert cov.contradiction_count == 2
        assert cov.not_evaluable_reason is None

    def test_not_evaluable_reason_populated_when_not_evaluable(self):
        row = _fake_lane_row(
            usability_label="NOT_EVALUABLE",
            is_usable=False,
            contradiction_evaluable=False,
            has_contradictions=False,
            contradiction_count=0,
            not_evaluable_reason="insufficient_comparable_facts",
        )
        cov = _build_lane_coverage(
            lane=LANE_SEC_COMPANY_FACTS,
            artifact_type="fundamental_quality",
            skill_pack="sec_companyfacts_evidence_v1",
            scope_kind="ticker",
            ticker="MSFT",
            row=row,
        )
        assert cov.not_evaluable_reason == "insufficient_comparable_facts"
        assert cov.contradiction_count is None

    def test_both_none_when_no_contradiction_assessment(self):
        row = _fake_lane_row()
        # Remove the contradiction_assessment from payload
        row["payload"].pop("contradiction_assessment", None)
        cov = _build_lane_coverage(
            lane=LANE_SEC_COMPANY_FACTS,
            artifact_type="fundamental_quality",
            skill_pack="sec_companyfacts_evidence_v1",
            scope_kind="ticker",
            ticker="CRM",
            row=row,
        )
        assert cov.contradiction_count is None
        assert cov.not_evaluable_reason is None

    def test_both_none_when_artifact_missing(self):
        cov = _build_lane_coverage(
            lane=LANE_SEC_COMPANY_FACTS,
            artifact_type="fundamental_quality",
            skill_pack="sec_companyfacts_evidence_v1",
            scope_kind="ticker",
            ticker="AAPL",
            row=None,
        )
        assert cov.artifact_id is None
        assert cov.contradiction_count is None
        assert cov.not_evaluable_reason is None

    def test_contradiction_count_zero_for_clean_artifact(self):
        row = _fake_lane_row(
            usability_label="USABLE",
            is_usable=True,
            contradiction_evaluable=True,
            has_contradictions=False,
            contradiction_count=0,
        )
        cov = _build_lane_coverage(
            lane=LANE_SEC_COMPANY_FACTS,
            artifact_type="fundamental_quality",
            skill_pack="sec_companyfacts_evidence_v1",
            scope_kind="ticker",
            ticker="NVDA",
            row=row,
        )
        assert cov.contradiction_count == 0
        assert cov.is_usable is True


# ── Group 4: Model version bump ───────────────────────────────────────────────


class TestAdapterModelVersionBump:
    """The adapter must use sec_xbrl_companyfacts_v2 so old v1 artifacts are
    superseded (clean replacement) on the next Intel v3 run."""

    def test_current_model_version_is_v2(self):
        assert SEC_ADAPTER_MODEL_VERSION == "sec_xbrl_companyfacts_v2"

    def test_diagnostic_current_model_matches_adapter(self):
        assert SEC_COMPANYFACTS_CURRENT_MODEL_VERSION == SEC_ADAPTER_MODEL_VERSION

    def test_prev_model_version_is_v1(self):
        assert SEC_COMPANYFACTS_PREV_MODEL_VERSION == "sec_xbrl_companyfacts_v1"

    def test_v1_artifact_detected_as_stale(self):
        from app.services.intelligence.v3.sec_companyfacts_readiness_diagnostic_v1 import (
            _is_stale_model_version,
        )
        assert _is_stale_model_version("sec_xbrl_companyfacts_v1") is True

    def test_v2_artifact_not_stale(self):
        from app.services.intelligence.v3.sec_companyfacts_readiness_diagnostic_v1 import (
            _is_stale_model_version,
        )
        assert _is_stale_model_version("sec_xbrl_companyfacts_v2") is False

    def test_none_model_version_is_stale(self):
        from app.services.intelligence.v3.sec_companyfacts_readiness_diagnostic_v1 import (
            _is_stale_model_version,
        )
        assert _is_stale_model_version(None) is True


# ── Group 5: Forensics integration ───────────────────────────────────────────


class TestForensicsSecDiagnosticIntegration:
    """HoldingForensicsRow includes sec_companyfacts_diagnostic when the artifact
    exists but is weak for an equity holding; None otherwise."""

    def _equity_lanes_suppressed(self, artifact_id: str = "fake-art-id") -> dict:
        row = _fake_lane_row(
            usability_label="SUPPRESSED_CONTRADICTED",
            is_usable=False,
            contradiction_evaluable=True,
            has_contradictions=True,
            contradiction_count=2,
            model_version=SEC_COMPANYFACTS_PREV_MODEL_VERSION,
            artifact_id=artifact_id,
        )
        cov = _build_lane_coverage(
            lane=LANE_SEC_COMPANY_FACTS,
            artifact_type="fundamental_quality",
            skill_pack="sec_companyfacts_evidence_v1",
            scope_kind="ticker",
            ticker="MSFT",
            row=row,
        )
        return {LANE_SEC_COMPANY_FACTS: cov}

    def _equity_lanes_usable(self, artifact_id: str = "usable-art") -> dict:
        row = _fake_lane_row(
            usability_label="USABLE",
            is_usable=True,
            contradiction_evaluable=True,
            has_contradictions=False,
            contradiction_count=0,
            model_version=SEC_COMPANYFACTS_CURRENT_MODEL_VERSION,
            artifact_id=artifact_id,
        )
        cov = _build_lane_coverage(
            lane=LANE_SEC_COMPANY_FACTS,
            artifact_type="fundamental_quality",
            skill_pack="sec_companyfacts_evidence_v1",
            scope_kind="ticker",
            ticker="MSFT",
            row=row,
        )
        return {LANE_SEC_COMPANY_FACTS: cov}

    def test_diagnostic_present_for_weak_equity_artifact(self):
        aid = "stale-suppressed-art"
        lanes = self._equity_lanes_suppressed(artifact_id=aid)
        supp = _supplemental(fact_counts={aid: 30})
        row = _build_holding_row(
            ticker="MSFT",
            asset_type="equity",
            lanes=lanes,
            supplemental=supp,
        )
        assert row.sec_companyfacts_diagnostic is not None
        diag = row.sec_companyfacts_diagnostic
        assert diag["readiness_category"] == CATEGORY_SUPPRESSED_CONTRADICTED
        assert diag["is_stale_model_version"] is True
        assert diag["is_false_contradiction_candidate"] is True
        assert diag["safe_for_decision"] is False
        assert diag["observation_count"] == 30

    def test_diagnostic_none_for_usable_equity_artifact(self):
        lanes = self._equity_lanes_usable()
        supp = _supplemental(fact_counts={"usable-art": 40})
        row = _build_holding_row(
            ticker="MSFT",
            asset_type="equity",
            lanes=lanes,
            supplemental=supp,
        )
        assert row.sec_companyfacts_diagnostic is None

    def test_diagnostic_none_for_etf(self):
        # ETF has no sec_company_facts artifact
        supp = _supplemental()
        row = _build_holding_row(
            ticker="VTI",
            asset_type="etf",
            lanes={},
            supplemental=supp,
        )
        assert row.sec_companyfacts_diagnostic is None

    def test_diagnostic_none_for_crypto(self):
        supp = _supplemental()
        row = _build_holding_row(
            ticker="BTC",
            asset_type="crypto",
            lanes={},
            supplemental=supp,
        )
        assert row.sec_companyfacts_diagnostic is None

    def test_diagnostic_none_when_no_artifact(self):
        """No artifact at all → sec_companyfacts_diagnostic is None."""
        supp = _supplemental()
        row = _build_holding_row(
            ticker="CRM",
            asset_type="equity",
            lanes={},
            supplemental=supp,
        )
        assert row.sec_companyfacts_diagnostic is None

    def test_to_dict_includes_diagnostic_key(self):
        aid = "test-art"
        lanes = self._equity_lanes_suppressed(artifact_id=aid)
        supp = _supplemental(fact_counts={aid: 15})
        row = _build_holding_row(
            ticker="MSFT",
            asset_type="equity",
            lanes=lanes,
            supplemental=supp,
        )
        d = row.to_dict()
        assert "sec_companyfacts_diagnostic" in d
        assert d["sec_companyfacts_diagnostic"] is not None

    def test_to_dict_diagnostic_safe_for_decision_false(self):
        aid = "safe-test"
        lanes = self._equity_lanes_suppressed(artifact_id=aid)
        supp = _supplemental(fact_counts={aid: 20})
        row = _build_holding_row(
            ticker="MSFT",
            asset_type="equity",
            lanes=lanes,
            supplemental=supp,
        )
        d = row.to_dict()
        diag = d["sec_companyfacts_diagnostic"]
        assert diag["safe_for_decision"] is False

    def test_sec_exists_weak_bucket_with_diagnosis_note(self):
        """Forensics root_cause_bucket is SEC_ARTIFACT_EXISTS_BUT_READINESS_WEAK
        and the diagnostic provides the explanation."""
        aid = "weak-art"
        lanes = self._equity_lanes_suppressed(artifact_id=aid)
        supp = _supplemental(fact_counts={aid: 8})
        row = _build_holding_row(
            ticker="WMT",
            asset_type="equity",
            lanes=lanes,
            supplemental=supp,
        )
        assert row.root_cause_bucket == BUCKET_SEC_EXISTS_WEAK
        assert row.sec_companyfacts_diagnostic is not None
        assert isinstance(row.sec_companyfacts_diagnostic["diagnosis_note"], str)


# ── Group 6: Safety and policy invariants ────────────────────────────────────


class TestSafetyAndPolicyInvariants:
    """Stage 9C must not touch the decision path, not import decide(), and must
    keep safe_for_decision=False throughout."""

    def test_diagnostic_module_does_not_import_decide(self):
        """The diagnostic module must never import the decision policy."""
        import importlib
        mod_name = (
            "app.services.intelligence.v3.sec_companyfacts_readiness_diagnostic_v1"
        )
        mod = importlib.import_module(mod_name)
        mod_source = mod.__file__ or ""
        if mod_source.endswith(".py"):
            with open(mod_source) as f:
                content = f.read()
            assert "decision_policy_v1" not in content
            # Check there is no import of the decide function (not just docstring mention)
            assert "import decide" not in content
            assert "from decision_policy_v1" not in content

    def test_forensics_module_safe_for_decision_always_false(self):
        """DataFoundationForensicsResult and HoldingForensicsRow always have
        safe_for_decision=False."""
        from app.services.intelligence.v3.intel_data_foundation_forensics_v1 import (
            DataFoundationForensicsResult,
        )
        result = DataFoundationForensicsResult(
            schema_version="test",
            user_id="user1",
            generated_at="2026-05-24T00:00:00+00:00",
        )
        assert result.safe_for_decision is False
        assert result.synthesis_ready is False

    def test_diagnostic_safe_for_decision_always_false(self):
        d = diagnose_sec_companyfacts_readiness(
            artifact_id=None,
            generated_at=None,
            model_version=SEC_COMPANYFACTS_CURRENT_MODEL_VERSION,
            observation_count=None,
            freshness_status=None,
            source_authority=None,
            completeness_band=None,
            usability_label=None,
            suppression_reason=None,
            contradiction_evaluable=None,
            contradiction_count=None,
            not_evaluable_reason=None,
        )
        assert d.safe_for_decision is False

    def test_suppressed_contradicted_not_relaxed(self):
        """The fix must NOT relax SUPPRESSED_CONTRADICTED for true contradictions.
        Only the grouping identity determines truth — we never suppress the gate."""
        # Two facts with same complete identity and conflicting values
        # → must remain SUPPRESSED_CONTRADICTED
        facts = [
            _sec_fact("Revenues", 383_285e6, filed="2023-11-03", accn="ACC-A"),
            _sec_fact("Revenues", 500_000e6, filed="2023-11-03", accn="ACC-B"),
        ]
        result = detect_contradictions(facts)
        assert result.has_contradictions is True
        assert result.is_evaluable is True
        # The detector result feeds into truth adapter → SUPPRESSED_CONTRADICTED
        # is still set. We do not override it.

    def test_lane_coverage_to_dict_backward_compatible(self):
        """New fields in LaneCoverage.to_dict() don't break existing keys."""
        cov = LaneCoverage(
            lane=LANE_SEC_COMPANY_FACTS,
            artifact_type="fundamental_quality",
            skill_pack="sec_companyfacts_evidence_v1",
            scope_kind="ticker",
            ticker="AAPL",
            artifact_id=None,
            status="MISSING",
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
        )
        d = cov.to_dict()
        # Original required keys must still be present
        for key in (
            "lane", "artifact_type", "skill_pack", "scope_kind", "ticker",
            "artifact_id", "status", "usability_label", "is_usable",
            "suppression_reason", "source_authority", "completeness_band",
            "has_contradictions", "freshness_status", "model_version",
            "generated_at", "missing_reason",
        ):
            assert key in d, f"Expected key {key!r} missing from LaneCoverage.to_dict()"
        # New keys must be present too
        assert "contradiction_count" in d
        assert "not_evaluable_reason" in d
