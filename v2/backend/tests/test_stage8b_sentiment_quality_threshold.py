"""Stage 8B — Sentiment Evidence Quality Threshold tests.

Verifies the deterministic quality gate that distinguishes:
  - EDITORIAL_CONTEXT + THIN (always suppressed — correct by design)
  - Fresh, vendor-scored, sufficiently complete sentiment (can be LIMITED/READY)
  - Present-but-not-usable sentinel (INSUFFICIENT) vs genuinely absent (MISSING)
  - Stage 5J sub-reason for editorial-context suppression
  - Stage 5K propagation: SUPPRESSED → INSUFFICIENT, MISSING → MISSING,
    LIMITED → LIMITED, READY → READY
  - Snapshot evidence_explanation distinction: missing vs present-but-not-useful vs useful
  - Stage 6 conviction cap when sentiment is weak
  - BTC/XRP guardrails unchanged
  - No raw internal codes rendered

No Supabase dependency — all fakes defined locally.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import pytest

from app.services.intelligence.v3.sentiment_quality_threshold_v1 import (
    SENTINEL_EDITORIAL_CONTEXT_REASON,
    SENTIMENT_QUALITY_THRESHOLD_VERSION,
    SUFFICIENT_AUTHORITY_LEVELS,
    DISQUALIFYING_COMPLETENESS_BANDS,
    WEAK_AUTHORITY_LEVELS,
    evaluate_sentiment_quality,
)
from app.services.intelligence.v3.research_evidence_coverage_read_model_v1 import (
    LANE_NEWS_SENTIMENT,
    LANE_TECHNICALS,
    STATUS_LIMITED,
    STATUS_MISSING,
    STATUS_READY,
    STATUS_SUPPRESSED,
    compute_research_evidence_coverage,
)
from app.services.intelligence.v3.research_evidence_decision_input_adapter_v1 import (
    AXIS_SENTIMENT,
    READINESS_INSUFFICIENT,
    READINESS_LIMITED,
    READINESS_MISSING,
    READINESS_READY,
    compute_decision_input_readiness,
)

_USER_ID = "user-stage8b-test"
_TICKER = "MSFT"
_TICKER_BTC = "BTC"
_TICKER_XRP = "XRP"


# ── Fakes ─────────────────────────────────────────────────────────────────────


@dataclass
class _FakeDB:
    rows: list[dict[str, Any]] = field(default_factory=list)
    write_attempts: int = 0


class _FakeQuery:
    def __init__(self, db: _FakeDB, table_name: str) -> None:
        self._db = db
        self._table = table_name
        self._filters: dict[str, Any] = {}
        self._op: Optional[str] = None

    def select(self, cols: str = "*") -> "_FakeQuery":
        self._op = "select"
        return self

    def insert(self, *a, **kw):
        self._db.write_attempts += 1
        raise AssertionError("Must never insert")

    def update(self, *a, **kw):
        self._db.write_attempts += 1
        raise AssertionError("Must never update")

    def delete(self, *a, **kw):
        self._db.write_attempts += 1
        raise AssertionError("Must never delete")

    def eq(self, col: str, val: Any) -> "_FakeQuery":
        self._filters[col] = val
        return self

    def execute(self) -> Any:
        if self._table != "research_artifacts" or self._op != "select":
            class _Empty:
                data: list = []
            return _Empty()
        matched = [r for r in self._db.rows if all(r.get(k) == v for k, v in self._filters.items())]

        class _Res:
            data = matched
        return _Res()


class _FakeClient:
    def __init__(self, db: _FakeDB) -> None:
        self._db = db

    def table(self, name: str) -> _FakeQuery:
        return _FakeQuery(self._db, name)


def _now_iso(offset_seconds: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)).isoformat()


def _make_sentiment_row(
    *,
    ticker: str = _TICKER,
    usability_label: str,
    is_usable: Optional[bool] = None,
    suppression_reason: Optional[str] = None,
    freshness_status: str = "FRESH",
    source_authority: str = "EDITORIAL_CONTEXT",
    completeness_band: str = "THIN",
    has_contradictions: bool = False,
) -> dict[str, Any]:
    if is_usable is None:
        is_usable = usability_label in {"USABLE", "USABLE_WITH_LIMITATIONS"}
    return {
        "id": str(uuid.uuid4()),
        "user_id": _USER_ID,
        "artifact_type": "sentiment_event",
        "skill_pack": "news_sentiment_evidence_v1",
        "scope_kind": "ticker",
        "ticker": ticker,
        "is_active": True,
        "safe_for_decision": False,
        "freshness_status": freshness_status,
        "confidence_or_trust_level": "LOW",
        "generated_at": _now_iso(),
        "expires_at": None,
        "model_version": "news_sentiment_evidence.v1",
        "payload": {
            "truth_usability_assessment": {
                "usability_label": usability_label,
                "is_usable": is_usable,
                "suppression_reason": suppression_reason,
                "no_guessing": True,
            },
            "source_credibility_assessment": {
                "strongest_authority_level": source_authority,
            },
            "contradiction_assessment": {
                "is_evaluable": True,
                "has_contradictions": has_contradictions,
            },
            "evidence_completeness_assessment": {
                "completeness_band": completeness_band,
            },
        },
    }


# ── 1. Quality threshold: explicit criteria ────────────────────────────────────


class TestSentimentQualityThresholdCriteria:
    """Verify the explicit quality criteria documented in Stage 8B."""

    def test_editorial_context_thin_is_not_usable(self):
        result = evaluate_sentiment_quality(
            freshness_status="FRESH",
            source_authority="EDITORIAL_CONTEXT",
            completeness_band="THIN",
            is_contradicted=False,
            source_count=5,
            fact_count=5,
        )
        assert result.quality_tier == "NOT_USABLE"
        assert result.is_decision_useful is False
        assert any("source_quality_too_weak" in r for r in result.failure_reasons)
        assert any("completeness_too_weak" in r for r in result.failure_reasons)

    def test_unknown_authority_thin_is_not_usable(self):
        result = evaluate_sentiment_quality(
            freshness_status="FRESH",
            source_authority="UNKNOWN",
            completeness_band="THIN",
            is_contradicted=False,
            source_count=3,
            fact_count=3,
        )
        assert result.quality_tier == "NOT_USABLE"
        assert any("source_quality_too_weak" in r for r in result.failure_reasons)

    def test_editorial_context_many_sources_still_not_usable(self):
        """Volume of editorial items cannot compensate for weak authority."""
        result = evaluate_sentiment_quality(
            freshness_status="FRESH",
            source_authority="EDITORIAL_CONTEXT",
            completeness_band="THIN",
            is_contradicted=False,
            source_count=50,
            fact_count=50,
        )
        assert result.quality_tier == "NOT_USABLE"
        assert result.is_decision_useful is False

    def test_vendor_derived_partial_fresh_is_limited(self):
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
        assert len(result.failure_reasons) == 0

    def test_vendor_derived_complete_fresh_is_ready(self):
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
        assert len(result.failure_reasons) == 0

    def test_primary_authority_complete_fresh_is_ready(self):
        result = evaluate_sentiment_quality(
            freshness_status="FRESH",
            source_authority="PRIMARY_AUTHORITY",
            completeness_band="COMPLETE",
            is_contradicted=False,
            source_count=1,
            fact_count=2,
        )
        assert result.quality_tier == "READY"
        assert result.is_decision_useful is True

    def test_vendor_derived_stale_is_not_usable(self):
        result = evaluate_sentiment_quality(
            freshness_status="STALE",
            source_authority="VENDOR_DERIVED",
            completeness_band="PARTIAL",
            is_contradicted=False,
            source_count=2,
            fact_count=2,
        )
        assert result.quality_tier == "NOT_USABLE"
        assert any("freshness_not_acceptable" in r for r in result.failure_reasons)

    def test_vendor_derived_unknown_freshness_is_not_usable(self):
        result = evaluate_sentiment_quality(
            freshness_status="UNKNOWN",
            source_authority="VENDOR_DERIVED",
            completeness_band="PARTIAL",
            is_contradicted=False,
            source_count=2,
            fact_count=2,
        )
        assert result.quality_tier == "NOT_USABLE"
        assert any("freshness_not_acceptable" in r for r in result.failure_reasons)

    def test_vendor_derived_none_freshness_is_not_usable(self):
        result = evaluate_sentiment_quality(
            freshness_status=None,
            source_authority="VENDOR_DERIVED",
            completeness_band="PARTIAL",
            is_contradicted=False,
            source_count=2,
            fact_count=2,
        )
        assert result.quality_tier == "NOT_USABLE"

    def test_contradicted_vendor_derived_is_not_usable(self):
        result = evaluate_sentiment_quality(
            freshness_status="FRESH",
            source_authority="VENDOR_DERIVED",
            completeness_band="PARTIAL",
            is_contradicted=True,
            source_count=2,
            fact_count=2,
        )
        assert result.quality_tier == "NOT_USABLE"
        assert "evidence_contradicted" in result.failure_reasons

    def test_no_sources_is_not_usable(self):
        result = evaluate_sentiment_quality(
            freshness_status="FRESH",
            source_authority="VENDOR_DERIVED",
            completeness_band="PARTIAL",
            is_contradicted=False,
            source_count=0,
            fact_count=2,
        )
        assert result.quality_tier == "NOT_USABLE"
        assert "no_sources" in result.failure_reasons

    def test_no_facts_is_not_usable(self):
        result = evaluate_sentiment_quality(
            freshness_status="FRESH",
            source_authority="VENDOR_DERIVED",
            completeness_band="PARTIAL",
            is_contradicted=False,
            source_count=2,
            fact_count=0,
        )
        assert result.quality_tier == "NOT_USABLE"
        assert "no_facts" in result.failure_reasons

    def test_none_authority_is_not_usable(self):
        result = evaluate_sentiment_quality(
            freshness_status="FRESH",
            source_authority=None,
            completeness_band="PARTIAL",
            is_contradicted=False,
            source_count=2,
            fact_count=2,
        )
        assert result.quality_tier == "NOT_USABLE"
        assert "source_authority_unknown" in result.failure_reasons

    def test_none_completeness_band_is_not_usable(self):
        result = evaluate_sentiment_quality(
            freshness_status="FRESH",
            source_authority="VENDOR_DERIVED",
            completeness_band=None,
            is_contradicted=False,
            source_count=2,
            fact_count=2,
        )
        assert result.quality_tier == "NOT_USABLE"
        assert "completeness_band_missing" in result.failure_reasons

    def test_version_is_set(self):
        result = evaluate_sentiment_quality(
            freshness_status="FRESH",
            source_authority="VENDOR_DERIVED",
            completeness_band="PARTIAL",
            is_contradicted=False,
            source_count=1,
            fact_count=1,
        )
        assert result.version == SENTIMENT_QUALITY_THRESHOLD_VERSION

    def test_weak_authority_levels_include_editorial_and_unknown(self):
        assert "EDITORIAL_CONTEXT" in WEAK_AUTHORITY_LEVELS
        assert "UNKNOWN" in WEAK_AUTHORITY_LEVELS

    def test_sufficient_authority_levels_include_vendor_derived(self):
        assert "VENDOR_DERIVED" in SUFFICIENT_AUTHORITY_LEVELS
        assert "PRIMARY_AUTHORITY" in SUFFICIENT_AUTHORITY_LEVELS

    def test_disqualifying_bands_include_thin(self):
        assert "THIN" in DISQUALIFYING_COMPLETENESS_BANDS
        assert "NOT_EVALUABLE" in DISQUALIFYING_COMPLETENESS_BANDS


# ── 2. Stage 5J: suppressed vs missing distinction ────────────────────────────


class TestStage5JSentimentCoverage:
    """Verify Stage 5J (research_evidence_coverage_read_model) classifies
    news_sentiment correctly and applies sentiment-specific missing_reason."""

    def _make_client(self, rows: list[dict]) -> _FakeClient:
        db = _FakeDB(rows=rows)
        return _FakeClient(db)

    def test_suppressed_editorial_not_missing(self):
        """SUPPRESSED_INCOMPLETE editorial sentiment → STATUS_SUPPRESSED, not MISSING."""
        client = self._make_client([
            _make_sentiment_row(
                usability_label="SUPPRESSED_INCOMPLETE",
                suppression_reason="evidence_completeness_thin:missing_requirements=has_structured_claim_key_or_metric_name",
                source_authority="EDITORIAL_CONTEXT",
                completeness_band="THIN",
            )
        ])
        summary = compute_research_evidence_coverage(
            user_id=_USER_ID, tickers=[_TICKER], db_client=client
        )
        cov = summary.ticker_coverage[_TICKER].lanes[LANE_NEWS_SENTIMENT]
        assert cov.status == STATUS_SUPPRESSED
        assert cov.missing_reason == SENTINEL_EDITORIAL_CONTEXT_REASON

    def test_suppressed_editorial_reason_is_specific(self):
        """editorial_context_present_not_decision_useful distinguishes editorial
        suppression from a data-quality suppression."""
        client = self._make_client([
            _make_sentiment_row(
                usability_label="SUPPRESSED_INCOMPLETE",
                source_authority="EDITORIAL_CONTEXT",
                completeness_band="THIN",
            )
        ])
        summary = compute_research_evidence_coverage(
            user_id=_USER_ID, tickers=[_TICKER], db_client=client
        )
        cov = summary.ticker_coverage[_TICKER].lanes[LANE_NEWS_SENTIMENT]
        assert cov.missing_reason == SENTINEL_EDITORIAL_CONTEXT_REASON

    def test_missing_sentiment_is_missing(self):
        """No artifact at all → STATUS_MISSING."""
        client = self._make_client([])  # No rows
        summary = compute_research_evidence_coverage(
            user_id=_USER_ID, tickers=[_TICKER], db_client=client
        )
        cov = summary.ticker_coverage[_TICKER].lanes[LANE_NEWS_SENTIMENT]
        assert cov.status == STATUS_MISSING
        assert cov.missing_reason == "no_active_artifact"

    def test_limited_sentiment_is_limited(self):
        """USABLE_WITH_LIMITATIONS → STATUS_LIMITED."""
        client = self._make_client([
            _make_sentiment_row(
                usability_label="USABLE_WITH_LIMITATIONS",
                is_usable=True,
                source_authority="VENDOR_DERIVED",
                completeness_band="PARTIAL",
                freshness_status="FRESH",
            )
        ])
        summary = compute_research_evidence_coverage(
            user_id=_USER_ID, tickers=[_TICKER], db_client=client
        )
        cov = summary.ticker_coverage[_TICKER].lanes[LANE_NEWS_SENTIMENT]
        assert cov.status == STATUS_LIMITED
        assert cov.is_usable is True
        assert cov.missing_reason is None

    def test_ready_sentiment_is_ready(self):
        """USABLE → STATUS_READY."""
        client = self._make_client([
            _make_sentiment_row(
                usability_label="USABLE",
                is_usable=True,
                source_authority="VENDOR_DERIVED",
                completeness_band="COMPLETE",
                freshness_status="FRESH",
            )
        ])
        summary = compute_research_evidence_coverage(
            user_id=_USER_ID, tickers=[_TICKER], db_client=client
        )
        cov = summary.ticker_coverage[_TICKER].lanes[LANE_NEWS_SENTIMENT]
        assert cov.status == STATUS_READY
        assert cov.is_usable is True

    def test_other_suppression_reason_is_generic(self):
        """SUPPRESSED_CONTRADICTED gets generic suppressed_data_quality_issue reason."""
        client = self._make_client([
            _make_sentiment_row(
                usability_label="SUPPRESSED_CONTRADICTED",
                suppression_reason="contradiction_detected",
                source_authority="VENDOR_DERIVED",
                completeness_band="PARTIAL",
                has_contradictions=True,
            )
        ])
        summary = compute_research_evidence_coverage(
            user_id=_USER_ID, tickers=[_TICKER], db_client=client
        )
        cov = summary.ticker_coverage[_TICKER].lanes[LANE_NEWS_SENTIMENT]
        assert cov.status == STATUS_SUPPRESSED
        assert cov.missing_reason == "suppressed_data_quality_issue"

    def test_no_writes_made(self):
        db = _FakeDB(rows=[
            _make_sentiment_row(
                usability_label="SUPPRESSED_INCOMPLETE",
                source_authority="EDITORIAL_CONTEXT",
                completeness_band="THIN",
            )
        ])
        client = _FakeClient(db)
        compute_research_evidence_coverage(
            user_id=_USER_ID, tickers=[_TICKER], db_client=client
        )
        assert db.write_attempts == 0


# ── 3. Stage 5K: readiness propagation ───────────────────────────────────────


class TestStage5KSentimentReadiness:
    """Verify Stage 5K maps all four sentiment states correctly."""

    def _make_client(self, rows: list[dict]) -> _FakeClient:
        db = _FakeDB(rows=rows)
        return _FakeClient(db)

    def test_suppressed_sentiment_maps_to_insufficient(self):
        """SUPPRESSED (editorial context) → READINESS_INSUFFICIENT, not MISSING."""
        client = self._make_client([
            _make_sentiment_row(
                usability_label="SUPPRESSED_INCOMPLETE",
                source_authority="EDITORIAL_CONTEXT",
                completeness_band="THIN",
            )
        ])
        coverage = compute_research_evidence_coverage(
            user_id=_USER_ID, tickers=[_TICKER], db_client=client
        )
        shadow = compute_decision_input_readiness(coverage=coverage)
        sent_axis = shadow.ticker_readiness[_TICKER].axes[AXIS_SENTIMENT]
        assert sent_axis.readiness == READINESS_INSUFFICIENT
        assert sent_axis.is_usable is False

    def test_missing_sentiment_maps_to_missing(self):
        """No artifact → READINESS_MISSING (distinct from INSUFFICIENT)."""
        client = self._make_client([])
        coverage = compute_research_evidence_coverage(
            user_id=_USER_ID, tickers=[_TICKER], db_client=client
        )
        shadow = compute_decision_input_readiness(coverage=coverage)
        sent_axis = shadow.ticker_readiness[_TICKER].axes[AXIS_SENTIMENT]
        assert sent_axis.readiness == READINESS_MISSING
        assert sent_axis.is_usable is False

    def test_limited_sentiment_maps_to_limited(self):
        """USABLE_WITH_LIMITATIONS → READINESS_LIMITED."""
        client = self._make_client([
            _make_sentiment_row(
                usability_label="USABLE_WITH_LIMITATIONS",
                is_usable=True,
                source_authority="VENDOR_DERIVED",
                completeness_band="PARTIAL",
                freshness_status="FRESH",
            )
        ])
        coverage = compute_research_evidence_coverage(
            user_id=_USER_ID, tickers=[_TICKER], db_client=client
        )
        shadow = compute_decision_input_readiness(coverage=coverage)
        sent_axis = shadow.ticker_readiness[_TICKER].axes[AXIS_SENTIMENT]
        assert sent_axis.readiness == READINESS_LIMITED
        assert sent_axis.is_usable is True

    def test_ready_sentiment_maps_to_ready(self):
        """USABLE → READINESS_READY."""
        client = self._make_client([
            _make_sentiment_row(
                usability_label="USABLE",
                is_usable=True,
                source_authority="VENDOR_DERIVED",
                completeness_band="COMPLETE",
                freshness_status="FRESH",
            )
        ])
        coverage = compute_research_evidence_coverage(
            user_id=_USER_ID, tickers=[_TICKER], db_client=client
        )
        shadow = compute_decision_input_readiness(coverage=coverage)
        sent_axis = shadow.ticker_readiness[_TICKER].axes[AXIS_SENTIMENT]
        assert sent_axis.readiness == READINESS_READY
        assert sent_axis.is_usable is True

    def test_suppressed_sentiment_in_degraded_lanes(self):
        """SUPPRESSED sentiment appears in degraded_lanes, not contributing or missing."""
        client = self._make_client([
            _make_sentiment_row(
                usability_label="SUPPRESSED_INCOMPLETE",
                source_authority="EDITORIAL_CONTEXT",
                completeness_band="THIN",
            )
        ])
        coverage = compute_research_evidence_coverage(
            user_id=_USER_ID, tickers=[_TICKER], db_client=client
        )
        shadow = compute_decision_input_readiness(coverage=coverage)
        sent_axis = shadow.ticker_readiness[_TICKER].axes[AXIS_SENTIMENT]
        assert LANE_NEWS_SENTIMENT in sent_axis.degraded_lanes
        assert LANE_NEWS_SENTIMENT not in sent_axis.contributing_lanes
        assert LANE_NEWS_SENTIMENT not in sent_axis.missing_lanes

    def test_missing_sentiment_in_missing_lanes(self):
        """MISSING sentiment appears in missing_lanes."""
        client = self._make_client([])
        coverage = compute_research_evidence_coverage(
            user_id=_USER_ID, tickers=[_TICKER], db_client=client
        )
        shadow = compute_decision_input_readiness(coverage=coverage)
        sent_axis = shadow.ticker_readiness[_TICKER].axes[AXIS_SENTIMENT]
        assert LANE_NEWS_SENTIMENT in sent_axis.missing_lanes
        assert LANE_NEWS_SENTIMENT not in sent_axis.degraded_lanes

    def test_safe_for_decision_always_false(self):
        client = self._make_client([
            _make_sentiment_row(
                usability_label="USABLE",
                is_usable=True,
                source_authority="VENDOR_DERIVED",
                completeness_band="COMPLETE",
                freshness_status="FRESH",
            )
        ])
        coverage = compute_research_evidence_coverage(
            user_id=_USER_ID, tickers=[_TICKER], db_client=client
        )
        shadow = compute_decision_input_readiness(coverage=coverage)
        assert shadow.safe_for_decision is False


# ── 4. BTC/XRP guardrails unchanged ──────────────────────────────────────────


class TestCryptoGuardrails:
    """Verify BTC/XRP sentiment handling is unchanged by Stage 8B."""

    def _make_client(self, rows: list[dict]) -> _FakeClient:
        db = _FakeDB(rows=rows)
        return _FakeClient(db)

    def test_btc_missing_sentiment_is_missing(self):
        """BTC with no sentiment artifact → READINESS_MISSING (no penalty)."""
        client = self._make_client([])
        coverage = compute_research_evidence_coverage(
            user_id=_USER_ID, tickers=[_TICKER_BTC], db_client=client
        )
        shadow = compute_decision_input_readiness(
            coverage=coverage,
            holding_context_by_ticker={_TICKER_BTC: {"category": "Crypto"}},
        )
        sent_axis = shadow.ticker_readiness[_TICKER_BTC].axes[AXIS_SENTIMENT]
        assert sent_axis.readiness == READINESS_MISSING

    def test_xrp_suppressed_sentiment_is_insufficient(self):
        """XRP with editorial-context sentiment → READINESS_INSUFFICIENT."""
        client = self._make_client([
            _make_sentiment_row(
                ticker=_TICKER_XRP,
                usability_label="SUPPRESSED_INCOMPLETE",
                source_authority="EDITORIAL_CONTEXT",
                completeness_band="THIN",
            )
        ])
        coverage = compute_research_evidence_coverage(
            user_id=_USER_ID, tickers=[_TICKER_XRP], db_client=client
        )
        shadow = compute_decision_input_readiness(
            coverage=coverage,
            holding_context_by_ticker={_TICKER_XRP: {"category": "Crypto"}},
        )
        sent_axis = shadow.ticker_readiness[_TICKER_XRP].axes[AXIS_SENTIMENT]
        assert sent_axis.readiness == READINESS_INSUFFICIENT


# ── 5. Non-sentiment lanes unaffected ────────────────────────────────────────


class TestNonSentimentLanesUnchanged:
    """Stage 8B must not change behaviour of technicals or fundamentals lanes."""

    def _make_client(self, rows: list[dict]) -> _FakeClient:
        db = _FakeDB(rows=rows)
        return _FakeClient(db)

    def _make_technicals_row(self, *, usability_label: str = "USABLE") -> dict:
        is_usable = usability_label in {"USABLE", "USABLE_WITH_LIMITATIONS"}
        return {
            "id": str(uuid.uuid4()),
            "user_id": _USER_ID,
            "artifact_type": "technical_signal",
            "skill_pack": "technicals_evidence_v1",
            "scope_kind": "ticker",
            "ticker": _TICKER,
            "is_active": True,
            "safe_for_decision": False,
            "freshness_status": "FRESH",
            "confidence_or_trust_level": "MEDIUM",
            "generated_at": _now_iso(),
            "expires_at": None,
            "model_version": "technicals.v2",
            "payload": {
                "truth_usability_assessment": {
                    "usability_label": usability_label,
                    "is_usable": is_usable,
                    "suppression_reason": None,
                    "no_guessing": True,
                },
                "source_credibility_assessment": {
                    "strongest_authority_level": "VENDOR_DERIVED",
                },
                "contradiction_assessment": {
                    "is_evaluable": True,
                    "has_contradictions": False,
                },
                "evidence_completeness_assessment": {
                    "completeness_band": "COMPLETE",
                },
            },
        }

    def test_technical_limited_still_uses_generic_suppression_reason(self):
        """SUPPRESSED technicals still use the generic 'usability_suppressed' reason."""
        tech_row = self._make_technicals_row(usability_label="SUPPRESSED_INCOMPLETE")
        client = self._make_client([tech_row])
        coverage = compute_research_evidence_coverage(
            user_id=_USER_ID, tickers=[_TICKER], db_client=client
        )
        cov = coverage.ticker_coverage[_TICKER].lanes[LANE_TECHNICALS]
        assert cov.status == STATUS_SUPPRESSED
        assert cov.missing_reason == "usability_suppressed"

    def test_technical_ready_is_unchanged(self):
        """USABLE technicals still map to STATUS_READY."""
        tech_row = self._make_technicals_row(usability_label="USABLE")
        client = self._make_client([tech_row])
        coverage = compute_research_evidence_coverage(
            user_id=_USER_ID, tickers=[_TICKER], db_client=client
        )
        cov = coverage.ticker_coverage[_TICKER].lanes[LANE_TECHNICALS]
        assert cov.status == STATUS_READY
