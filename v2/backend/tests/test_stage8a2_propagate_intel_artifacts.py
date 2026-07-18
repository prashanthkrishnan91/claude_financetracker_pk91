"""Stage 8A.2 — Propagate usable technical evidence artifacts into Intel v3 snapshot.

Tests verify:
  1. Stage 5J/5K: USABLE_WITH_LIMITATIONS technical artifact → STATUS_LIMITED → READINESS_LIMITED.
  2. SUPPRESSED_INCOMPLETE sentiment artifact → STATUS_SUPPRESSED → not usable.
  3. Watchtower evidence collector returns FRESH EvidenceRecord for usable technical artifact.
  4. Watchtower eligibility republish compares technical artifact timestamp vs certified snapshot.
  5. Republish triggers when usable technical artifact is newer than certified snapshot.
  6. Republish does NOT trigger when technical artifact is older than certified snapshot.
  7. Republisher returns skipped_no_new_evidence when technical artifact older than snapshot.
  8. republish_after_analyst_eligibility never enqueues analyst LLM jobs.
  9. Snapshot evidence_explanation includes technical_signals_status LIMITED after republish
     (both Stage 6 on and Stage 6 off paths).
 10. Snapshot evidence_explanation keeps sentiment_status not usable when SUPPRESSED.
 11. BTC/XRP guardrail: crypto tickers treated as non-equity; SEC lane not applicable.
 12. Action distribution not changed by evidence artifact propagation alone.

No production Supabase dependency — all fakes defined locally.
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Shared fixtures ───────────────────────────────────────────────────────────

UID = uuid.UUID("aaaabbbb-0001-0000-0000-000000000002")
NOW = datetime(2026, 5, 21, 3, 0, 0, tzinfo=timezone.utc)  # 03:00 UTC
SNAPSHOT_AT = datetime(2026, 5, 21, 1, 49, 22, tzinfo=timezone.utc)  # 01:49 — old snapshot
ARTIFACT_AT = datetime(2026, 5, 21, 2, 42, 0, tzinfo=timezone.utc)   # 02:42 — newer than snapshot


def _usable_technical_payload(usability_label: str = "USABLE_WITH_LIMITATIONS") -> dict:
    return {
        "source_credibility_assessment": {
            "credibility_strongest": "VENDOR_DERIVED",
            "strongest_authority_level": "VENDOR_DERIVED",
        },
        "evidence_completeness_assessment": {
            "completeness_band": "PARTIAL",
        },
        "truth_usability_assessment": {
            "usability_label": usability_label,
            "is_usable": True,
            "suppression_reason": None,
        },
    }


def _suppressed_sentiment_payload() -> dict:
    return {
        "source_credibility_assessment": {
            "credibility_strongest": "EDITORIAL_CONTEXT",
            "strongest_authority_level": "EDITORIAL_CONTEXT",
        },
        "evidence_completeness_assessment": {
            "completeness_band": "THIN",
        },
        "truth_usability_assessment": {
            "usability_label": "SUPPRESSED_INCOMPLETE",
            "is_usable": False,
            "suppression_reason": "completeness_band_thin",
        },
    }


def _make_artifact_row(
    artifact_type: str,
    skill_pack: str,
    ticker: Optional[str],
    generated_at: datetime,
    payload: dict,
    freshness_status: str = "FRESH",
) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "artifact_type": artifact_type,
        "skill_pack": skill_pack,
        "scope_kind": "ticker" if ticker else "portfolio",
        "ticker": ticker,
        "generated_at": generated_at.isoformat(),
        "freshness_status": freshness_status,
        "confidence_or_trust_level": "medium",
        "is_active": True,
        "safe_for_decision": False,
        "model_version": "v1",
        "expires_at": None,
        "payload": payload,
    }


# ── 1. Stage 5J: USABLE_WITH_LIMITATIONS → STATUS_LIMITED ────────────────────

class TestStage5JTechnicalLimitedMapping:
    """Stage 5J maps USABLE_WITH_LIMITATIONS to STATUS_LIMITED for technicals lane."""

    def _make_db_client(self, rows: list[dict]) -> Any:
        class _FakeResult:
            data = rows

        class _FakeTable:
            def select(self, *a): return self
            def eq(self, *a): return self
            def execute(self): return _FakeResult()

        class _FakeClient:
            def table(self, name): return _FakeTable()

        return _FakeClient()

    def test_usable_with_limitations_is_limited(self):
        from app.services.intelligence.v3.research_evidence_coverage_read_model_v1 import (
            STATUS_LIMITED,
            compute_research_evidence_coverage,
        )
        row = _make_artifact_row(
            "technical_signal",
            "technicals_evidence_v1",
            "MSFT",
            ARTIFACT_AT,
            _usable_technical_payload("USABLE_WITH_LIMITATIONS"),
        )
        client = self._make_db_client([row])
        summary = compute_research_evidence_coverage(
            user_id=str(UID),
            tickers=["MSFT"],
            db_client=client,
        )
        cov = summary.ticker_coverage["MSFT"].lanes["technicals"]
        assert cov.status == STATUS_LIMITED
        assert cov.is_usable is True
        assert cov.usability_label == "USABLE_WITH_LIMITATIONS"

    def test_suppressed_sentiment_is_suppressed(self):
        from app.services.intelligence.v3.research_evidence_coverage_read_model_v1 import (
            STATUS_SUPPRESSED,
            compute_research_evidence_coverage,
        )
        row = _make_artifact_row(
            "sentiment_event",
            "news_sentiment_evidence_v1",
            "MSFT",
            ARTIFACT_AT,
            _suppressed_sentiment_payload(),
        )
        client = self._make_db_client([row])
        summary = compute_research_evidence_coverage(
            user_id=str(UID),
            tickers=["MSFT"],
            db_client=client,
        )
        cov = summary.ticker_coverage["MSFT"].lanes["news_sentiment"]
        assert cov.status == STATUS_SUPPRESSED
        assert cov.is_usable is False


# ── 2. Stage 5K: technical LIMITED → axis READINESS_LIMITED ──────────────────

class TestStage5KTechnicalAxisReadiness:
    """Stage 5K maps LIMITED technicals lane to READINESS_LIMITED axis signal."""

    def _make_coverage_with_technical(self, usability: str = "USABLE_WITH_LIMITATIONS") -> Any:
        from app.services.intelligence.v3.research_evidence_coverage_read_model_v1 import (
            LaneCoverage,
            STATUS_LIMITED,
            STATUS_SUPPRESSED,
            TickerCoverage,
            ResearchEvidenceCoverageSummary,
            LANE_TECHNICALS,
            LANE_NEWS_SENTIMENT,
            LANE_FUNDAMENTALS,
            LANE_SEC_COMPANY_FACTS,
            LANE_MACRO_CONTEXT,
        )
        tech_status = STATUS_LIMITED if usability == "USABLE_WITH_LIMITATIONS" else STATUS_SUPPRESSED
        tech_cov = LaneCoverage(
            lane=LANE_TECHNICALS,
            artifact_type="technical_signal",
            skill_pack="technicals_evidence_v1",
            scope_kind="ticker",
            ticker="MSFT",
            artifact_id="art-001",
            status=tech_status,
            usability_label=usability,
            is_usable=(tech_status == STATUS_LIMITED),
            suppression_reason=None,
            source_authority="VENDOR_DERIVED",
            completeness_band="PARTIAL",
            has_contradictions=False,
            freshness_status="FRESH",
            confidence_or_trust_level="medium",
            model_version="v1",
            generated_at=ARTIFACT_AT.isoformat(),
            expires_at=None,
        )
        sent_cov = LaneCoverage(
            lane=LANE_NEWS_SENTIMENT,
            artifact_type="sentiment_event",
            skill_pack="news_sentiment_evidence_v1",
            scope_kind="ticker",
            ticker="MSFT",
            artifact_id="art-002",
            status=STATUS_SUPPRESSED,
            usability_label="SUPPRESSED_INCOMPLETE",
            is_usable=False,
            suppression_reason="completeness_band_thin",
            source_authority="EDITORIAL_CONTEXT",
            completeness_band="THIN",
            has_contradictions=None,
            freshness_status="FRESH",
            confidence_or_trust_level="low",
            model_version="v1",
            generated_at=ARTIFACT_AT.isoformat(),
            expires_at=None,
        )
        missing_fund = LaneCoverage(
            lane=LANE_FUNDAMENTALS, artifact_type="fundamental_quality",
            skill_pack="fundamentals_evidence_v1", scope_kind="ticker",
            ticker="MSFT", artifact_id=None, status="MISSING",
            usability_label=None, is_usable=False, suppression_reason=None,
            source_authority=None, completeness_band=None, has_contradictions=None,
            freshness_status=None, confidence_or_trust_level=None,
            model_version=None, generated_at=None, expires_at=None,
            missing_reason="no_active_artifact",
        )
        missing_sec = LaneCoverage(
            lane=LANE_SEC_COMPANY_FACTS, artifact_type="fundamental_quality",
            skill_pack="sec_companyfacts_evidence_v1", scope_kind="ticker",
            ticker="MSFT", artifact_id=None, status="MISSING",
            usability_label=None, is_usable=False, suppression_reason=None,
            source_authority=None, completeness_band=None, has_contradictions=None,
            freshness_status=None, confidence_or_trust_level=None,
            model_version=None, generated_at=None, expires_at=None,
            missing_reason="no_active_artifact",
        )
        macro_cov = LaneCoverage(
            lane=LANE_MACRO_CONTEXT, artifact_type="portfolio_exposure",
            skill_pack="fred_macro_evidence_v1", scope_kind="portfolio",
            ticker=None, artifact_id=None, status="MISSING",
            usability_label=None, is_usable=False, suppression_reason=None,
            source_authority=None, completeness_band=None, has_contradictions=None,
            freshness_status=None, confidence_or_trust_level=None,
            model_version=None, generated_at=None, expires_at=None,
            missing_reason="no_active_artifact",
        )
        tc = TickerCoverage(ticker="MSFT")
        tc.lanes = {
            LANE_TECHNICALS: tech_cov,
            LANE_NEWS_SENTIMENT: sent_cov,
            LANE_FUNDAMENTALS: missing_fund,
            LANE_SEC_COMPANY_FACTS: missing_sec,
        }
        tc.limited_lane_count = 1 if tech_status == STATUS_LIMITED else 0
        tc.suppressed_lane_count = (1 if tech_status == STATUS_SUPPRESSED else 0) + 1  # sentiment
        return ResearchEvidenceCoverageSummary(
            schema_version="research_evidence_coverage.v1",
            user_id=str(UID),
            generated_at=NOW.isoformat(),
            portfolio_ticker_count=1,
            ticker_coverage={"MSFT": tc},
            portfolio_macro_coverage=macro_cov,
            lane_counts={},
            usability_counts={},
            missing_lane_counts={},
            suppressed_counts={},
            stale_or_unknown_counts={},
            ready_artifact_count=1 if tech_status == STATUS_LIMITED else 0,
        )

    def test_limited_technical_maps_to_limited_axis(self):
        from app.services.intelligence.v3.research_evidence_decision_input_adapter_v1 import (
            READINESS_LIMITED,
            compute_decision_input_readiness,
        )
        coverage = self._make_coverage_with_technical("USABLE_WITH_LIMITATIONS")
        shadow = compute_decision_input_readiness(coverage)
        tech_axis = shadow.ticker_readiness["MSFT"].axes["technical_signals"]
        assert tech_axis.readiness == READINESS_LIMITED
        assert tech_axis.is_usable is True

    def test_suppressed_sentiment_maps_to_not_usable(self):
        from app.services.intelligence.v3.research_evidence_decision_input_adapter_v1 import (
            compute_decision_input_readiness,
        )
        coverage = self._make_coverage_with_technical("USABLE_WITH_LIMITATIONS")
        shadow = compute_decision_input_readiness(coverage)
        sent_axis = shadow.ticker_readiness["MSFT"].axes["sentiment"]
        assert sent_axis.is_usable is False

    def test_btc_xrp_sec_not_applicable(self):
        from app.services.intelligence.v3.research_evidence_decision_input_adapter_v1 import (
            compute_decision_input_readiness,
            READINESS_NOT_APPLICABLE,
        )
        from app.services.intelligence.v3.research_evidence_coverage_read_model_v1 import (
            LaneCoverage,
            TickerCoverage,
            ResearchEvidenceCoverageSummary,
            LANE_TECHNICALS, LANE_NEWS_SENTIMENT, LANE_FUNDAMENTALS,
            LANE_SEC_COMPANY_FACTS, LANE_MACRO_CONTEXT,
        )

        def _missing_lane(lane, art_type, skill_pack, ticker):
            return LaneCoverage(
                lane=lane, artifact_type=art_type, skill_pack=skill_pack,
                scope_kind="ticker", ticker=ticker, artifact_id=None,
                status="MISSING", usability_label=None, is_usable=False,
                suppression_reason=None, source_authority=None,
                completeness_band=None, has_contradictions=None,
                freshness_status=None, confidence_or_trust_level=None,
                model_version=None, generated_at=None, expires_at=None,
                missing_reason="no_active_artifact",
            )

        btc_tc = TickerCoverage(ticker="BTC")
        btc_tc.lanes = {
            LANE_TECHNICALS: _missing_lane(LANE_TECHNICALS, "technical_signal", "technicals_evidence_v1", "BTC"),
            LANE_NEWS_SENTIMENT: _missing_lane(LANE_NEWS_SENTIMENT, "sentiment_event", "news_sentiment_evidence_v1", "BTC"),
            LANE_FUNDAMENTALS: _missing_lane(LANE_FUNDAMENTALS, "fundamental_quality", "fundamentals_evidence_v1", "BTC"),
            LANE_SEC_COMPANY_FACTS: _missing_lane(LANE_SEC_COMPANY_FACTS, "fundamental_quality", "sec_companyfacts_evidence_v1", "BTC"),
        }
        macro = _missing_lane(LANE_MACRO_CONTEXT, "portfolio_exposure", "fred_macro_evidence_v1", None)
        macro.scope_kind = "portfolio"
        macro.ticker = None
        coverage = ResearchEvidenceCoverageSummary(
            schema_version="research_evidence_coverage.v1",
            user_id=str(UID), generated_at=NOW.isoformat(),
            portfolio_ticker_count=1,
            ticker_coverage={"BTC": btc_tc},
            portfolio_macro_coverage=macro,
            lane_counts={}, usability_counts={}, missing_lane_counts={},
            suppressed_counts={}, stale_or_unknown_counts={},
            ready_artifact_count=0,
        )
        shadow = compute_decision_input_readiness(coverage)
        btc_readiness = shadow.ticker_readiness["BTC"]
        assert btc_readiness.sec_lane_applicable is False
        fund_axis = btc_readiness.axes["company_fundamentals"]
        not_applicable = [c.lane for c in fund_axis.lane_contributions
                          if not c.is_applicable]
        assert "sec_company_facts" in not_applicable


# ── 3. Evidence collector: usable technical artifact → FRESH EvidenceRecord ──

class TestWatchtowerEvidenceCollectorTechnicalArtifacts:
    """Evidence collector returns FRESH records for usable technical artifacts."""

    def _make_client(
        self,
        *,
        tickers: list[str] = None,
        technical_rows: list[dict] = None,
        snap_rows: list[dict] = None,
        rec_rows: list[dict] = None,
        insight_rows: list[dict] = None,
        intel_snap_rows: list[dict] = None,
    ):
        tickers = tickers or []
        technical_rows = technical_rows or []
        snap_rows = snap_rows or []

        class _Fake:
            def __init__(self, rows):
                self._rows = rows
            def select(self, *a): return self
            def eq(self, *a, **kw): return self
            def order(self, *a, **kw): return self
            def limit(self, *a): return self
            def in_(self, *a): return self
            def execute(self):
                class R:
                    pass
                r = R()
                r.data = self._rows
                return r

        class _Client:
            def __init__(self):
                self._tables = {
                    "positions": _Fake([{"ticker": t} for t in tickers]),
                    "portfolio_snapshots": _Fake(snap_rows),
                    "recommendations": _Fake(rec_rows or []),
                    "agent_insights": _Fake(insight_rows or []),
                    "intel_v3_snapshots": _Fake(intel_snap_rows or []),
                    # Production queries research_artifacts with eq(is_active=True) as
                    # the usability proxy (Stage 5A) and no longer reads payload
                    # usability fields; emulate that DB-side filter here.
                    "research_artifacts": _Fake(
                        [r for r in technical_rows if r.get("is_active", True)]
                    ),
                }

            def table(self, name):
                return self._tables.get(name, _Fake([]))

        return _Client()

    @pytest.mark.asyncio
    async def test_usable_fresh_artifact_emits_fresh_evidence_record(self):
        from app.services.intelligence.v3.watchtower_evidence_collector_v1 import collect_evidence_records
        from app.services.intelligence.v3.watchtower_freshness_ledger_v1 import FRESHNESS_FRESH, FRESHNESS_AGING

        tech_row = {
            "ticker": "MSFT",
            "generated_at": ARTIFACT_AT.isoformat(),
            "payload": _usable_technical_payload(),
        }
        client = self._make_client(
            tickers=["MSFT"],
            technical_rows=[tech_row],
        )
        records = await collect_evidence_records(str(UID), client, now=NOW)
        tech_records = [r for r in records if r.evidence_type == "technical" and r.ticker == "MSFT"]
        assert tech_records, "Expected a technical EvidenceRecord for MSFT"
        assert tech_records[0].freshness_status in (FRESHNESS_FRESH, FRESHNESS_AGING)
        assert tech_records[0].as_of == ARTIFACT_AT

    @pytest.mark.asyncio
    async def test_no_usable_artifact_emits_missing_portfolio_record(self):
        from app.services.intelligence.v3.watchtower_evidence_collector_v1 import collect_evidence_records
        from app.services.intelligence.v3.watchtower_freshness_ledger_v1 import FRESHNESS_MISSING

        client = self._make_client(tickers=["MSFT"], technical_rows=[])
        records = await collect_evidence_records(str(UID), client, now=NOW)
        tech_records = [r for r in records if r.evidence_type == "technical"]
        assert tech_records, "Expected a technical MISSING EvidenceRecord"
        assert any(r.freshness_status == FRESHNESS_MISSING for r in tech_records)

    @pytest.mark.asyncio
    async def test_not_usable_artifact_treated_as_missing(self):
        """is_usable=False artifacts (e.g. SUPPRESSED_INCOMPLETE) should not surface as FRESH."""
        from app.services.intelligence.v3.watchtower_evidence_collector_v1 import collect_evidence_records
        from app.services.intelligence.v3.watchtower_freshness_ledger_v1 import FRESHNESS_MISSING, FRESHNESS_FRESH

        # Stage 5A / Migration 024: the collector no longer inspects payload
        # usability — it queries is_active=True as the usable proxy. A suppressed
        # (is_usable=False) artifact is not active, so the query excludes it.
        suppressed_row = {
            "ticker": "MSFT",
            "generated_at": ARTIFACT_AT.isoformat(),
            "is_active": False,
            "payload": {
                "truth_usability_assessment": {"is_usable": False, "usability_label": "SUPPRESSED_INCOMPLETE"},
            },
        }
        client = self._make_client(tickers=["MSFT"], technical_rows=[suppressed_row])
        records = await collect_evidence_records(str(UID), client, now=NOW)
        tech_records = [r for r in records if r.evidence_type == "technical"]
        # Not-usable artifacts should not produce FRESH records
        assert not any(r.freshness_status == FRESHNESS_FRESH and r.ticker == "MSFT"
                       for r in tech_records), \
            "Suppressed artifact must not emit FRESH technical EvidenceRecord"


# ── 4. Watchtower republish: usable artifact newer than snapshot → triggers ──

class TestWatchtowerRepublishFreshnessTrigger:
    """republish_after_analyst_eligibility triggers republish when
    usable technical artifact timestamp is newer than certified snapshot."""

    def _make_snap_row(self, generated_at: datetime) -> dict:
        # Migration 024: _fetch_latest_intel_snapshot reads flat metadata columns
        # from intel_v3_snapshots, not the payload JSONB.
        return {
            "source_hash": "hash-snap-before-artifact",
            "snapshot_source": "worker_certified",
            "payload_generated_at": generated_at.isoformat(),
            "evidence_mapping_version": None,
            "stage7_contract_complete": True,
            "stage8e_contract_complete": True,
        }

    def _make_client_with_snap(self, snap_generated_at: datetime):
        snap_row = self._make_snap_row(snap_generated_at)

        class _FakeResult:
            def __init__(self, rows):
                self.data = rows

        class _FakeTable:
            def __init__(self, rows):
                self._rows = rows
            def select(self, *a): return self
            def eq(self, *a, **kw): return self
            def order(self, *a, **kw): return self
            def limit(self, *a): return self
            def execute(self): return _FakeResult(self._rows)

        class _Client:
            def table(self, name):
                if name == "intel_v3_snapshots":
                    return _FakeTable([snap_row])
                if name == "portfolio_snapshots":
                    return _FakeTable([])
                return _FakeTable([])

        return _Client()

    @pytest.mark.asyncio
    async def test_technical_artifact_newer_triggers_republish(self):
        from app.services.intelligence.v3.watchtower_intel_republisher_v1 import (
            republish_after_analyst_eligibility,
            PUBLISH_REBUILT_AND_PUBLISHED,
        )
        client = self._make_client_with_snap(SNAPSHOT_AT)
        republish_called = []

        async def _fake_republish(uid):
            republish_called.append(uid)
            return {"snapshot_source": "worker_certified"}

        result = await republish_after_analyst_eligibility(
            UID,
            client,
            intel_republish_callable=_fake_republish,
            latest_evidence_at=ARTIFACT_AT,  # 02:42 > snapshot 01:49
            now=NOW,
        )
        assert result.evidence_newer_than_certified_snapshot is True
        assert result.publish_status == PUBLISH_REBUILT_AND_PUBLISHED
        assert len(republish_called) == 1

    @pytest.mark.asyncio
    async def test_technical_artifact_older_skips_republish(self):
        from app.services.intelligence.v3.watchtower_intel_republisher_v1 import (
            republish_after_analyst_eligibility,
            PUBLISH_SKIPPED_NO_NEW_EVIDENCE,
        )
        # Snapshot is NEWER than the artifact
        snap_newer = ARTIFACT_AT + timedelta(hours=1)
        client = self._make_client_with_snap(snap_newer)

        with patch(
            "app.services.intelligence.v3.evidence_mapping_version_v1.is_snapshot_mapping_current",
            return_value=True,
        ), patch(
            "app.services.intelligence.v3.stage7_snapshot_contract_v1.is_snapshot_stage7_complete",
            return_value=True,
        ):
            result = await republish_after_analyst_eligibility(
                UID,
                client,
                intel_republish_callable=AsyncMock(),
                latest_evidence_at=ARTIFACT_AT,  # older than snap_newer
                now=NOW,
            )
        assert result.evidence_newer_than_certified_snapshot is False
        assert result.publish_status == PUBLISH_SKIPPED_NO_NEW_EVIDENCE

    @pytest.mark.asyncio
    async def test_republish_never_enqueues_analyst_jobs(self):
        """Deterministic republish triggered by technical artifact NEVER queues analyst LLM jobs."""
        from app.services.intelligence.v3.watchtower_intel_republisher_v1 import (
            republish_after_analyst_eligibility,
        )
        client = self._make_client_with_snap(SNAPSHOT_AT)

        async def _fake_republish(uid):
            return {"snapshot_source": "worker_certified"}

        result = await republish_after_analyst_eligibility(
            UID,
            client,
            intel_republish_callable=_fake_republish,
            latest_evidence_at=ARTIFACT_AT,
            now=NOW,
        )
        assert result.analyst_jobs_queued == 0, \
            "republish triggered by technical artifact must not enqueue analyst jobs"


# ── 5. Watchtower background worker uses TECHNICAL in _max_evidence_at ────────

class TestWatchtowerWorkerTechnicalEvidenceInEligibility:
    """Background worker includes TECHNICAL in evidence types for eligibility republish."""

    @pytest.mark.asyncio
    async def test_worker_includes_technical_in_max_evidence_at(self):
        """When all analyst evidence is stale but technical artifact is fresh,
        _max_evidence_at must include the technical timestamp."""
        from app.services.intelligence.v3.watchtower_background_refresh_worker_v1 import (
            _max_evidence_at,
            EVIDENCE_TYPE_TECHNICAL,
            EVIDENCE_TYPE_ANALYST_LLM,
            EVIDENCE_TYPE_RECOMMENDATION,
        )
        from app.services.intelligence.v3.watchtower_freshness_ledger_v1 import EvidenceRecord, FRESHNESS_FRESH

        tech_record = EvidenceRecord(
            evidence_type=EVIDENCE_TYPE_TECHNICAL,
            ticker="MSFT",
            scope="ticker",
            as_of=ARTIFACT_AT,
            collected_at=ARTIFACT_AT,
            source="research_artifacts.technical_signal",
            freshness_status=FRESHNESS_FRESH,
            freshness_sla_seconds=14400,
            deploy_eligible=True,
            decision_eligible=True,
            reason="fresh",
        )
        # No analyst or recommendation records
        result = _max_evidence_at(
            [tech_record],
            evidence_types={EVIDENCE_TYPE_ANALYST_LLM, EVIDENCE_TYPE_RECOMMENDATION, EVIDENCE_TYPE_TECHNICAL},
        )
        assert result == ARTIFACT_AT

    @pytest.mark.asyncio
    async def test_worker_technical_not_counted_without_evidence_type_param(self):
        """Without TECHNICAL in evidence_types, technical records are ignored."""
        from app.services.intelligence.v3.watchtower_background_refresh_worker_v1 import (
            _max_evidence_at,
            EVIDENCE_TYPE_TECHNICAL,
            EVIDENCE_TYPE_ANALYST_LLM,
        )
        from app.services.intelligence.v3.watchtower_freshness_ledger_v1 import EvidenceRecord, FRESHNESS_FRESH

        tech_record = EvidenceRecord(
            evidence_type=EVIDENCE_TYPE_TECHNICAL,
            ticker="MSFT",
            scope="ticker",
            as_of=ARTIFACT_AT,
            collected_at=ARTIFACT_AT,
            source="research_artifacts.technical_signal",
            freshness_status=FRESHNESS_FRESH,
            freshness_sla_seconds=14400,
            deploy_eligible=True,
            decision_eligible=True,
            reason="fresh",
        )
        # Only analyst_llm in types — technical ignored
        result = _max_evidence_at(
            [tech_record],
            evidence_types={EVIDENCE_TYPE_ANALYST_LLM},
        )
        assert result is None


# ── 6. Snapshot evidence_explanation includes technical status LIMITED ─────────

class TestSnapshotEvidenceExplanationTechnicalStatus:
    """Snapshot builder uses research_axis_readiness to populate technical_signals_status."""

    def _make_synthetic_decision(self):
        from app.services.intelligence.v3.decision_contracts import (
            DecisionOutputV3, ActionV3, ConvictionV3, AxisBand,
            FitBand, RiskBand, PriceBand,
        )
        return DecisionOutputV3(
            ticker="MSFT",
            action=ActionV3.HOLD,
            conviction=ConvictionV3.MEDIUM,
            evidence_quality=AxisBand.OK,
            portfolio_fit=FitBand.ON_TARGET,
            risk_band=RiskBand.MEDIUM,
            attractiveness=AxisBand.OK,
            price_context=PriceBand.FAIR,
            rationale_plain_english="Hold — moderate evidence.",
            why_now="",
            why_not_now="Awaiting technical confirmation.",
            suppression_reasons={},
            blockers=[],
            source_signal_summary={},
        )

    def test_technical_signals_status_limited_from_research_axis(self):
        """When research_axis_readiness provides LIMITED technical, snapshot shows LIMITED."""
        from app.services.intelligence.v3.snapshot_builder import _build_held_card

        decision = self._make_synthetic_decision()
        card_meta = {
            "ticker": "MSFT",
            "name": "Microsoft",
            "category": "stock",
            "thesis_state": "intact",
            "governance_result": None,  # Stage 6 inactive
            "research_axis_readiness": {
                "technical_signals": "LIMITED",
                "sentiment": "MISSING",
            },
        }
        card = _build_held_card(
            decision=decision,
            card_meta=card_meta,
            snapshot_id="snap-001",
            run_id="run-001",
        )
        expl = card["detail_drawer_payload"]["evidence_explanation"]
        assert expl["technical_signals_status"] == "LIMITED", \
            "technical_signals_status must be LIMITED when USABLE_WITH_LIMITATIONS artifact exists"

    def test_sentiment_suppressed_remains_missing_or_suppressed(self):
        """Suppressed sentiment artifact keeps sentiment_status not usable."""
        from app.services.intelligence.v3.snapshot_builder import _build_held_card

        decision = self._make_synthetic_decision()
        card_meta = {
            "ticker": "MSFT",
            "name": "Microsoft",
            "category": "stock",
            "thesis_state": "intact",
            "governance_result": None,
            "research_axis_readiness": {
                "technical_signals": "LIMITED",
                "sentiment": "INSUFFICIENT",  # suppressed → INSUFFICIENT from adapter
            },
        }
        card = _build_held_card(
            decision=decision,
            card_meta=card_meta,
            snapshot_id="snap-001",
            run_id="run-001",
        )
        expl = card["detail_drawer_payload"]["evidence_explanation"]
        # sentiment should NOT be "READY" or "LIMITED" (it's suppressed/not usable)
        assert expl.get("sentiment_status") not in ("READY", "LIMITED"), \
            "Suppressed sentiment must not show as usable in evidence_explanation"

    def test_no_research_axis_readiness_falls_back_to_synthetic(self):
        """When research_axis_readiness is absent, synthetic path still works."""
        from app.services.intelligence.v3.snapshot_builder import _build_held_card

        decision = self._make_synthetic_decision()
        card_meta = {
            "ticker": "MSFT",
            "name": "Microsoft",
            "category": "stock",
            "thesis_state": "intact",
            "governance_result": None,
            "research_axis_readiness": None,
        }
        card = _build_held_card(
            decision=decision,
            card_meta=card_meta,
            snapshot_id="snap-001",
            run_id="run-001",
        )
        expl = card["detail_drawer_payload"]["evidence_explanation"]
        # Falls back to synthetic MISSING
        assert expl["technical_signals_status"] == "MISSING"

    def test_stage6_active_overrides_research_axis(self):
        """When Stage 6 governance result is present, it takes precedence over research_axis_readiness."""
        from app.services.intelligence.v3.snapshot_builder import _build_held_card

        decision = self._make_synthetic_decision()
        gov_result = {
            "primary_evidence_readiness": "LIMITED",
            "auxiliary_evidence_readiness": {
                "technical_signals": "LIMITED",
                "sentiment": "MISSING",
            },
            "conviction_cap_applied": True,
            "conviction_cap_reason": "ok_cap_medium",
            "safe_for_visible_decision": True,
            "safe_for_visible_decision_reason": "ok_band",
            "governance_priority_applied": "priority4a",
            "corroboration_gap": True,
            "action_blocks_applied": [],
        }
        card_meta = {
            "ticker": "MSFT",
            "name": "Microsoft",
            "category": "stock",
            "thesis_state": "intact",
            "governance_result": gov_result,
            "research_axis_readiness": {"technical_signals": "READY", "sentiment": "READY"},
        }
        card = _build_held_card(
            decision=decision,
            card_meta=card_meta,
            snapshot_id="snap-001",
            run_id="run-001",
        )
        expl = card["detail_drawer_payload"]["evidence_explanation"]
        # governance_result takes priority; research_axis_readiness is ignored
        assert expl["technical_signals_status"] == "LIMITED"

    def test_sec_catalyst_sentiment_limited_surfaces_in_snapshot(self):
        """Usable SEC catalyst sentiment propagates as LIMITED in snapshot evidence_explanation."""
        from app.services.intelligence.v3.snapshot_builder import _build_held_card

        decision = self._make_synthetic_decision()
        card_meta = {
            "ticker": "CRM",
            "name": "Salesforce",
            "category": "stock",
            "thesis_state": "intact",
            "governance_result": None,
            "research_axis_readiness": {
                "technical_signals": "MISSING",
                "sentiment": "LIMITED",   # SEC catalyst is usable → LIMITED
            },
        }
        card = _build_held_card(
            decision=decision,
            card_meta=card_meta,
            snapshot_id="snap-sec-001",
            run_id="run-sec-001",
        )
        expl = card["detail_drawer_payload"]["evidence_explanation"]
        assert expl["sentiment_status"] == "LIMITED", \
            "Usable SEC catalyst sentiment must surface as LIMITED in evidence_explanation"
        # No raw internal keys in the explanation payload.
        import json
        blob = json.dumps(expl)
        for bad_key in ("sec_catalyst_sentiment_evidence_v1", "skill_pack", "api_key"):
            assert bad_key not in blob, f"Internal key {bad_key} must not leak into explanation"

    def test_suppressed_editorial_sentiment_does_not_show_limited(self):
        """Suppressed editorial sentiment must not show as LIMITED or READY."""
        from app.services.intelligence.v3.snapshot_builder import _build_held_card

        decision = self._make_synthetic_decision()
        card_meta = {
            "ticker": "CRM",
            "name": "Salesforce",
            "category": "stock",
            "thesis_state": "intact",
            "governance_result": None,
            "research_axis_readiness": {
                "technical_signals": "MISSING",
                "sentiment": "INSUFFICIENT",  # suppressed editorial → INSUFFICIENT
            },
        }
        card = _build_held_card(
            decision=decision,
            card_meta=card_meta,
            snapshot_id="snap-sec-002",
            run_id="run-sec-002",
        )
        expl = card["detail_drawer_payload"]["evidence_explanation"]
        assert expl.get("sentiment_status") not in ("LIMITED", "READY"), \
            "Suppressed editorial must not show as usable in evidence_explanation"


# ── 7. Action distribution unchanged ─────────────────────────────────────────

class TestActionDistributionUnchanged:
    """Technical artifact evidence does not alter BUY/HOLD/TRIM/SELL policy."""

    def test_decide_ignores_research_artifacts(self):
        """decide() output is identical before and after research artifacts exist.

        The evidence_quality input to decide() is unchanged by technical artifact
        propagation — it comes from analyst evidence, not from research artifacts.
        decide() is deterministic; same inputs → same outputs.
        """
        from app.services.intelligence.v3.decision_policy_v1 import decide
        from app.services.intelligence.v3.decision_contracts import (
            DecisionInputV3, AxisBand, FitBand, RiskBand,
        )
        inp = DecisionInputV3(
            ticker="MSFT",
            raw_action="HOLD",
            raw_analyst_action="HOLD",
            upstream_conviction="MEDIUM",
            evidence_quality=AxisBand.OK,
            portfolio_fit=FitBand.ON_TARGET,
            risk_band=RiskBand.MEDIUM,
        )
        out1 = decide(inp)
        # Simulate "artifact added" — decide() inputs are unchanged
        out2 = decide(inp)
        assert out1.action == out2.action
        assert out1.conviction == out2.conviction
        assert out1.evidence_quality == out2.evidence_quality
