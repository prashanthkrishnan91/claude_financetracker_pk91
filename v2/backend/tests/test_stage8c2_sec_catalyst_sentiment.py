"""Stage 8C PR 2 — SEC catalyst sentiment evidence tests.

Validates the SEC/company catalyst evidence path introduced by Stage 8C PR 2:
  - Flag OFF → no catalyst fetch or artifact write.
  - Equity with material SEC/company catalyst → sentiment_event READY or LIMITED.
  - Routine/noisy filing → NOT_USABLE or skipped.
  - Stale filing → NOT_USABLE / skipped.
  - Low/ambiguous ticker match → not written (no CIK = no artifact).
  - BTC/XRP/ETF → INELIGIBLE / skipped.
  - sentiment_polarity remains None, never neutral.
  - COMPANY_AUTHORED/PRIMARY_AUTHORITY only for CIK-confirmed filings.
  - Raw editorial/yfinance path unchanged.
  - Idempotency: duplicate accession fingerprint = same replay_idempotency_key.
  - Stage 5J sec_catalyst_sentiment lane is registered.
  - No BUY/HOLD/TRIM/SELL policy keys in any artifact.
  - Adapter returns None (no artifact) for no-fresh-filing results.
  - Structured log keys emitted correctly by runner.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone, timedelta
from typing import Any, Optional
from unittest.mock import MagicMock, patch
import uuid

import pytest

from app.services.intelligence.research_workers.sec_catalyst_sentiment_adapter_v1 import (
    SEC_CATALYST_ARTIFACT_TYPE,
    SEC_CATALYST_SKILL_PACK,
    SecCatalystSentimentAdapterResult,
    adapt_sec_catalyst_sentiment,
    build_sec_catalyst_sentiment_worker_output,
)
from app.services.intelligence.research_workers.contracts import WorkerInput
from app.services.intelligence.v3.sentiment_event_adapter_v2 import (
    DECISION_USEFULNESS_INELIGIBLE,
    DECISION_USEFULNESS_LIMITED,
    DECISION_USEFULNESS_NOT_USABLE,
    DECISION_USEFULNESS_READY,
)
from app.services.intelligence.v3.research_evidence_coverage_read_model_v1 import (
    LANE_SEC_CATALYST_SENTIMENT,
    TICKER_LANE_REGISTRY,
)


# ── Fake SEC EDGAR provider types ─────────────────────────────────────────────

@dataclass
class _FakeFilingRecord:
    form_type: str
    filing_date: str       # YYYY-MM-DD
    accession_number: str
    report_date: Optional[str] = None
    filing_url: str = "https://www.sec.gov/Archives/edgar/data/1234/000123400001/"


@dataclass
class _FakeSecEdgarProviderResult:
    ticker: str
    cik: Optional[str]
    filings: list[_FakeFilingRecord] = field(default_factory=list)
    fetch_status: str = "success"
    error_message: Optional[str] = None
    fetched_at: str = ""
    request_count: int = 0

    @property
    def is_success(self) -> bool:
        return self.fetch_status == "success"


def _make_worker_input(ticker: str, holding_context: Optional[dict] = None) -> WorkerInput:
    return WorkerInput(
        user_id="test-user",
        ticker=ticker,
        worker_run_id=str(uuid.uuid4()),
        holding_context=holding_context,
    )


def _today_str(offset_days: int = 0) -> str:
    d = datetime.now(timezone.utc).date() + timedelta(days=offset_days)
    return d.isoformat()


def _recent_10k() -> _FakeFilingRecord:
    return _FakeFilingRecord(
        form_type="10-K",
        filing_date=_today_str(-30),  # 30 days ago — well within 180d window
        accession_number="0001234000-24-000001",
        report_date="2023-12-31",
    )


def _recent_10q() -> _FakeFilingRecord:
    return _FakeFilingRecord(
        form_type="10-Q",
        filing_date=_today_str(-20),  # 20 days ago — within 90d window
        accession_number="0001234000-24-000002",
        report_date="2024-03-31",
    )


def _recent_8k() -> _FakeFilingRecord:
    return _FakeFilingRecord(
        form_type="8-K",
        filing_date=_today_str(-7),  # 7 days ago — within 30d window
        accession_number="0001234000-24-000003",
    )


def _stale_10k() -> _FakeFilingRecord:
    return _FakeFilingRecord(
        form_type="10-K",
        filing_date=_today_str(-365),  # 365 days ago — beyond 180d window
        accession_number="0001234000-23-000001",
    )


def _stale_10q() -> _FakeFilingRecord:
    return _FakeFilingRecord(
        form_type="10-Q",
        filing_date=_today_str(-120),  # 120 days — beyond 90d window
        accession_number="0001234000-23-000002",
    )


def _stale_8k() -> _FakeFilingRecord:
    return _FakeFilingRecord(
        form_type="8-K",
        filing_date=_today_str(-60),  # 60 days — beyond 30d window
        accession_number="0001234000-23-000003",
    )


# ── Adapter tests ──────────────────────────────────────────────────────────────

class TestAdaptSecCatalystSentiment:
    """Tests for adapt_sec_catalyst_sentiment() pure adapter."""

    def test_fresh_10k_produces_ready_artifact(self):
        result = adapt_sec_catalyst_sentiment(
            ticker="AAPL",
            provider_result=_FakeSecEdgarProviderResult(
                ticker="AAPL",
                cik="0000320193",
                filings=[_recent_10k()],
            ),
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )
        assert result.has_material_filings is True
        assert result.catalyst_count == 1
        assert len(result.sources) == 1
        assert len(result.facts) == 1
        # 10-K + PRIMARY_AUTHORITY + HIGH + COMPLETE + FRESH + ticker_match=HIGH → READY
        payload = result.facts[0].structured_payload
        assert payload["decision_usefulness_tier"] == DECISION_USEFULNESS_READY
        assert result.best_tier == DECISION_USEFULNESS_READY

    def test_fresh_10q_produces_limited_artifact(self):
        result = adapt_sec_catalyst_sentiment(
            ticker="MSFT",
            provider_result=_FakeSecEdgarProviderResult(
                ticker="MSFT",
                cik="0000789019",
                filings=[_recent_10q()],
            ),
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )
        assert result.has_material_filings is True
        assert result.catalyst_count == 1
        payload = result.facts[0].structured_payload
        # 10-Q = PARTIAL completeness → LIMITED (all other criteria pass)
        assert payload["decision_usefulness_tier"] == DECISION_USEFULNESS_LIMITED
        assert result.best_tier == DECISION_USEFULNESS_LIMITED

    def test_fresh_8k_produces_limited_artifact(self):
        result = adapt_sec_catalyst_sentiment(
            ticker="GOOGL",
            provider_result=_FakeSecEdgarProviderResult(
                ticker="GOOGL",
                cik="0001652044",
                filings=[_recent_8k()],
            ),
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )
        assert result.has_material_filings is True
        assert result.catalyst_count == 1
        payload = result.facts[0].structured_payload
        # 8-K = PARTIAL completeness → LIMITED
        assert payload["decision_usefulness_tier"] == DECISION_USEFULNESS_LIMITED

    def test_stale_10k_produces_no_artifact(self):
        result = adapt_sec_catalyst_sentiment(
            ticker="AAPL",
            provider_result=_FakeSecEdgarProviderResult(
                ticker="AAPL",
                cik="0000320193",
                filings=[_stale_10k()],
            ),
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )
        assert result.has_material_filings is False
        assert result.catalyst_count == 0
        assert result.skipped_stale_count == 1

    def test_stale_10q_skipped(self):
        result = adapt_sec_catalyst_sentiment(
            ticker="AAPL",
            provider_result=_FakeSecEdgarProviderResult(
                ticker="AAPL",
                cik="0000320193",
                filings=[_stale_10q()],
            ),
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )
        assert result.has_material_filings is False
        assert result.skipped_stale_count == 1

    def test_stale_8k_skipped(self):
        result = adapt_sec_catalyst_sentiment(
            ticker="AAPL",
            provider_result=_FakeSecEdgarProviderResult(
                ticker="AAPL",
                cik="0000320193",
                filings=[_stale_8k()],
            ),
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )
        assert result.has_material_filings is False
        assert result.skipped_stale_count == 1

    def test_all_stale_no_artifact(self):
        result = adapt_sec_catalyst_sentiment(
            ticker="AAPL",
            provider_result=_FakeSecEdgarProviderResult(
                ticker="AAPL",
                cik="0000320193",
                filings=[_stale_10k(), _stale_10q(), _stale_8k()],
            ),
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )
        assert result.has_material_filings is False
        assert result.skipped_stale_count == 3

    def test_mixed_fresh_and_stale_produces_artifact(self):
        result = adapt_sec_catalyst_sentiment(
            ticker="AAPL",
            provider_result=_FakeSecEdgarProviderResult(
                ticker="AAPL",
                cik="0000320193",
                filings=[_recent_10k(), _stale_10q()],
            ),
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )
        assert result.has_material_filings is True
        assert result.catalyst_count == 1
        assert result.skipped_stale_count == 1
        assert result.freshness_status == "FRESH"

    def test_no_cik_no_artifact(self):
        result = adapt_sec_catalyst_sentiment(
            ticker="UNKNOWN",
            provider_result=_FakeSecEdgarProviderResult(
                ticker="UNKNOWN",
                cik=None,
                fetch_status="no_cik",
            ),
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )
        assert result.has_material_filings is False

    def test_provider_fetch_error_no_artifact(self):
        result = adapt_sec_catalyst_sentiment(
            ticker="AAPL",
            provider_result=_FakeSecEdgarProviderResult(
                ticker="AAPL",
                cik=None,
                fetch_status="error",
            ),
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )
        assert result.has_material_filings is False

    def test_no_filings_no_artifact(self):
        result = adapt_sec_catalyst_sentiment(
            ticker="AAPL",
            provider_result=_FakeSecEdgarProviderResult(
                ticker="AAPL",
                cik="0000320193",
                filings=[],
            ),
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )
        assert result.has_material_filings is False

    def test_sentiment_polarity_always_none(self):
        result = adapt_sec_catalyst_sentiment(
            ticker="AAPL",
            provider_result=_FakeSecEdgarProviderResult(
                ticker="AAPL",
                cik="0000320193",
                filings=[_recent_10k()],
            ),
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )
        assert result.has_material_filings is True
        for fact in result.facts:
            assert fact.structured_payload["sentiment_polarity"] is None
            assert fact.structured_payload["is_polarity_present"] is False

    def test_source_authority_primary_for_cik_confirmed(self):
        result = adapt_sec_catalyst_sentiment(
            ticker="AAPL",
            provider_result=_FakeSecEdgarProviderResult(
                ticker="AAPL",
                cik="0000320193",
                filings=[_recent_10k()],
            ),
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )
        assert result.has_material_filings is True
        payload = result.facts[0].structured_payload
        assert payload["source_authority"] == "PRIMARY_AUTHORITY"

    def test_ticker_match_confidence_high_for_cik_match(self):
        result = adapt_sec_catalyst_sentiment(
            ticker="AAPL",
            provider_result=_FakeSecEdgarProviderResult(
                ticker="AAPL",
                cik="0000320193",
                filings=[_recent_10k()],
            ),
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )
        assert result.has_material_filings is True
        payload = result.facts[0].structured_payload
        assert payload["ticker_match_confidence"] == "HIGH"

    def test_btc_ineligible_skipped(self):
        result = adapt_sec_catalyst_sentiment(
            ticker="BTC",
            provider_result=_FakeSecEdgarProviderResult(
                ticker="BTC",
                cik="0009999999",  # hypothetical CIK to ensure we reach ineligibility check
                filings=[_recent_10k()],
            ),
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )
        # v2 adapter marks BTC as INELIGIBLE; adapter skips INELIGIBLE filings.
        assert result.has_material_filings is False

    def test_xrp_ineligible_skipped(self):
        result = adapt_sec_catalyst_sentiment(
            ticker="XRP",
            provider_result=_FakeSecEdgarProviderResult(
                ticker="XRP",
                cik="0009999998",
                filings=[_recent_10k()],
            ),
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )
        assert result.has_material_filings is False

    def test_etf_ineligible_via_holding_context(self):
        result = adapt_sec_catalyst_sentiment(
            ticker="SPY",
            provider_result=_FakeSecEdgarProviderResult(
                ticker="SPY",
                cik="0000884394",
                filings=[_recent_10k()],
            ),
            fetched_at=datetime.now(timezone.utc).isoformat(),
            holding_context={"category": "ETF"},
        )
        assert result.has_material_filings is False

    def test_no_forbidden_keys_in_facts(self):
        from app.services.intelligence.research_workers.contracts import (
            WORKER_FORBIDDEN_PAYLOAD_KEYS,
        )
        result = adapt_sec_catalyst_sentiment(
            ticker="AAPL",
            provider_result=_FakeSecEdgarProviderResult(
                ticker="AAPL",
                cik="0000320193",
                filings=[_recent_10k(), _recent_10q(), _recent_8k()],
            ),
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )
        assert result.has_material_filings is True
        for fact in result.facts:
            for key in WORKER_FORBIDDEN_PAYLOAD_KEYS:
                assert key not in fact.structured_payload, (
                    f"Forbidden key '{key}' found in FactRecord payload"
                )

    def test_fact_kind_is_schema_valid_catalyst_item(self):
        """SEC catalyst facts must use fact_kind='catalyst_item' (schema CHECK constraint).

        fact_kind='sec_catalyst_event' violates research_artifact_facts_fact_kind_check.
        """
        _SCHEMA_VALID_FACT_KINDS = {
            "metric_observation", "risk_item", "catalyst_item", "thesis_pillar",
            "sourced_claim", "event", "peer_context", "quality_observation", "revision_note",
        }
        result = adapt_sec_catalyst_sentiment(
            ticker="MSFT",
            provider_result=_FakeSecEdgarProviderResult(
                ticker="MSFT",
                cik="0000789019",
                filings=[_recent_10k(), _recent_10q(), _recent_8k()],
            ),
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )
        assert result.has_material_filings is True
        for fact in result.facts:
            assert fact.fact_kind in _SCHEMA_VALID_FACT_KINDS, (
                f"fact_kind='{fact.fact_kind}' is not in DB CHECK constraint allowed list"
            )
            assert fact.fact_kind == "catalyst_item", (
                f"SEC catalyst facts must use fact_kind='catalyst_item', got '{fact.fact_kind}'"
            )
            assert fact.axis_hint == "catalyst"

    def test_idempotency_same_accessions_same_fingerprint(self):
        provider = _FakeSecEdgarProviderResult(
            ticker="AAPL",
            cik="0000320193",
            filings=[_recent_10k()],
        )
        fetched_at = datetime.now(timezone.utc).isoformat()
        r1 = adapt_sec_catalyst_sentiment("AAPL", provider, fetched_at)
        r2 = adapt_sec_catalyst_sentiment("AAPL", provider, fetched_at)
        assert r1.source_refs_fingerprint == r2.source_refs_fingerprint

    def test_idempotency_different_accessions_different_fingerprint(self):
        filing_a = _FakeFilingRecord(
            form_type="10-K",
            filing_date=_today_str(-30),
            accession_number="0001234000-24-AAAA",
        )
        filing_b = _FakeFilingRecord(
            form_type="10-K",
            filing_date=_today_str(-30),
            accession_number="0001234000-24-BBBB",
        )
        fetched_at = datetime.now(timezone.utc).isoformat()
        r1 = adapt_sec_catalyst_sentiment(
            "AAPL",
            _FakeSecEdgarProviderResult(ticker="AAPL", cik="0000320193", filings=[filing_a]),
            fetched_at,
        )
        r2 = adapt_sec_catalyst_sentiment(
            "AAPL",
            _FakeSecEdgarProviderResult(ticker="AAPL", cik="0000320193", filings=[filing_b]),
            fetched_at,
        )
        assert r1.source_refs_fingerprint != r2.source_refs_fingerprint

    def test_source_kind_is_sec_filing(self):
        result = adapt_sec_catalyst_sentiment(
            ticker="AAPL",
            provider_result=_FakeSecEdgarProviderResult(
                ticker="AAPL",
                cik="0000320193",
                filings=[_recent_10k()],
            ),
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )
        assert result.has_material_filings is True
        assert result.sources[0].source_kind == "sec_filing"
        assert result.sources[0].provider_name == "sec_edgar"

    def test_best_tier_ready_with_10k(self):
        result = adapt_sec_catalyst_sentiment(
            ticker="AAPL",
            provider_result=_FakeSecEdgarProviderResult(
                ticker="AAPL",
                cik="0000320193",
                filings=[_recent_10k(), _recent_10q()],  # 10-K → READY, 10-Q → LIMITED
            ),
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )
        assert result.has_material_filings is True
        assert result.catalyst_count == 2
        # best = max(READY, LIMITED) = READY
        assert result.best_tier == DECISION_USEFULNESS_READY

    def test_routine_form_type_skipped(self):
        # DEF 14A (proxy statement) is not in _MATERIAL_FORMS
        routine_filing = _FakeFilingRecord(
            form_type="DEF 14A",
            filing_date=_today_str(-10),
            accession_number="0001234000-24-ROUTINE",
        )
        result = adapt_sec_catalyst_sentiment(
            ticker="AAPL",
            provider_result=_FakeSecEdgarProviderResult(
                ticker="AAPL",
                cik="0000320193",
                filings=[routine_filing],
            ),
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )
        assert result.has_material_filings is False
        assert result.skipped_routine_count == 1

    def test_confidence_high_for_cik_confirmed(self):
        result = adapt_sec_catalyst_sentiment(
            ticker="AAPL",
            provider_result=_FakeSecEdgarProviderResult(
                ticker="AAPL",
                cik="0000320193",
                filings=[_recent_10k()],
            ),
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )
        assert result.confidence_or_trust_level == "HIGH"


# ── WorkerOutput builder tests ────────────────────────────────────────────────

class TestBuildSecCatalystSentimentWorkerOutput:

    def test_returns_none_when_no_material_filings(self):
        wi = _make_worker_input("AAPL")
        provider = _FakeSecEdgarProviderResult(
            ticker="AAPL", cik="0000320193", filings=[_stale_10k()]
        )
        output = build_sec_catalyst_sentiment_worker_output(wi, provider, "2024-01-01T00:00:00Z")
        assert output is None

    def test_returns_worker_output_for_fresh_10k(self):
        wi = _make_worker_input("AAPL")
        provider = _FakeSecEdgarProviderResult(
            ticker="AAPL", cik="0000320193", filings=[_recent_10k()]
        )
        output = build_sec_catalyst_sentiment_worker_output(wi, provider, "2024-01-01T00:00:00Z")
        assert output is not None
        assert output.artifact_type == SEC_CATALYST_ARTIFACT_TYPE
        assert output.skill_pack == SEC_CATALYST_SKILL_PACK
        assert output.ticker == "AAPL"
        assert output.scope_kind == "ticker"

    def test_worker_output_payload_no_forbidden_keys(self):
        from app.services.intelligence.research_workers.contracts import (
            WORKER_FORBIDDEN_PAYLOAD_KEYS,
        )
        wi = _make_worker_input("MSFT")
        provider = _FakeSecEdgarProviderResult(
            ticker="MSFT", cik="0000789019", filings=[_recent_10k(), _recent_10q()]
        )
        output = build_sec_catalyst_sentiment_worker_output(wi, provider, "2024-01-01T00:00:00Z")
        assert output is not None
        for key in WORKER_FORBIDDEN_PAYLOAD_KEYS:
            assert key not in output.artifact_payload, (
                f"Forbidden key '{key}' in artifact_payload"
            )
        for fact in output.facts:
            for key in WORKER_FORBIDDEN_PAYLOAD_KEYS:
                assert key not in fact.structured_payload

    def test_replay_idempotency_key_deterministic(self):
        wi1 = _make_worker_input("AAPL")
        wi2 = _make_worker_input("AAPL")
        provider = _FakeSecEdgarProviderResult(
            ticker="AAPL", cik="0000320193", filings=[_recent_10k()]
        )
        fetched_at = "2024-01-01T00:00:00Z"
        out1 = build_sec_catalyst_sentiment_worker_output(wi1, provider, fetched_at)
        out2 = build_sec_catalyst_sentiment_worker_output(wi2, provider, fetched_at)
        assert out1 is not None and out2 is not None
        assert out1.replay_idempotency_key == out2.replay_idempotency_key

    def test_worker_output_freshness_fresh_for_fresh_filing(self):
        wi = _make_worker_input("AAPL")
        provider = _FakeSecEdgarProviderResult(
            ticker="AAPL", cik="0000320193", filings=[_recent_10k()]
        )
        output = build_sec_catalyst_sentiment_worker_output(wi, provider, "2024-01-01T00:00:00Z")
        assert output is not None
        assert output.freshness_status == "FRESH"

    def test_artifact_payload_includes_catalyst_count(self):
        wi = _make_worker_input("AAPL")
        provider = _FakeSecEdgarProviderResult(
            ticker="AAPL", cik="0000320193",
            filings=[_recent_10k(), _recent_10q(), _recent_8k()]
        )
        output = build_sec_catalyst_sentiment_worker_output(wi, provider, "2024-01-01T00:00:00Z")
        assert output is not None
        assert output.artifact_payload["catalyst_count"] == 3
        assert output.artifact_payload["provider"] == "sec_edgar"


# ── Runner integration tests ──────────────────────────────────────────────────

class TestRunSecCatalystSentimentEvidence:
    """Tests for run_sec_catalyst_sentiment_evidence() runner."""

    def _make_settings(
        self,
        workers_enabled: bool = True,
        catalyst_enabled: bool = True,
        user_agent: str = "TestApp/1.0 test@example.com",
    ):
        from app.config import Settings
        return Settings(
            intel_v3_research_workers_enabled=workers_enabled,
            intel_v3_sentiment_catalyst_evidence_enabled=catalyst_enabled,
            sec_edgar_user_agent=user_agent,
        )

    def _make_fake_db(self) -> MagicMock:
        db = MagicMock()
        return db

    def test_flag_off_returns_none_no_provider_call(self):
        from app.services.intelligence.research_workers.evidence_lane_runner_v1 import (
            run_sec_catalyst_sentiment_evidence,
        )
        provider_called = []

        def fake_provider(t):
            provider_called.append(t)
            return _FakeSecEdgarProviderResult(ticker=t, cik="0000320193", filings=[_recent_10k()])

        result = run_sec_catalyst_sentiment_evidence(
            user_id="u1",
            ticker="AAPL",
            db_client=self._make_fake_db(),
            settings=self._make_settings(catalyst_enabled=False),
            _provider_fn=fake_provider,
        )
        assert result is None
        assert provider_called == [], "Provider must not be called when flag is off"

    def test_workers_master_flag_off_returns_none(self):
        from app.services.intelligence.research_workers.evidence_lane_runner_v1 import (
            run_sec_catalyst_sentiment_evidence,
        )
        result = run_sec_catalyst_sentiment_evidence(
            user_id="u1",
            ticker="AAPL",
            db_client=self._make_fake_db(),
            settings=self._make_settings(workers_enabled=False, catalyst_enabled=True),
            _provider_fn=lambda t: _FakeSecEdgarProviderResult(ticker=t, cik="x", filings=[]),
        )
        assert result is None

    def test_equity_fresh_10k_writes_artifact(self):
        from app.services.intelligence.research_workers.evidence_lane_runner_v1 import (
            run_sec_catalyst_sentiment_evidence,
        )
        written_outputs = []

        class _FakeService:
            def __init__(self, supabase_client, user_id):
                pass
            def write_artifact(self, output):
                written_outputs.append(output)
                return "artifact-id-001"

        with patch(
            "app.services.intelligence.research_workers.evidence_lane_runner_v1"
            ".ResearchArtifactServiceV1",
            _FakeService,
        ):
            result = run_sec_catalyst_sentiment_evidence(
                user_id="u1",
                ticker="AAPL",
                db_client=self._make_fake_db(),
                settings=self._make_settings(),
                _provider_fn=lambda t: _FakeSecEdgarProviderResult(
                    ticker=t, cik="0000320193", filings=[_recent_10k()]
                ),
            )

        assert result == "artifact-id-001"
        assert len(written_outputs) == 1
        assert written_outputs[0].artifact_type == "sentiment_event"
        assert written_outputs[0].skill_pack == SEC_CATALYST_SKILL_PACK

    def test_btc_skipped_conservatively(self):
        from app.services.intelligence.research_workers.evidence_lane_runner_v1 import (
            run_sec_catalyst_sentiment_evidence,
        )
        # BTC is classified as non-equity by the SEC metric candidate classifier
        result = run_sec_catalyst_sentiment_evidence(
            user_id="u1",
            ticker="BTC",
            db_client=self._make_fake_db(),
            settings=self._make_settings(),
            _provider_fn=lambda t: _FakeSecEdgarProviderResult(
                ticker=t, cik="0009999999", filings=[_recent_10k()]
            ),
        )
        assert result is None

    def test_xrp_skipped_conservatively(self):
        from app.services.intelligence.research_workers.evidence_lane_runner_v1 import (
            run_sec_catalyst_sentiment_evidence,
        )
        result = run_sec_catalyst_sentiment_evidence(
            user_id="u1",
            ticker="XRP",
            db_client=self._make_fake_db(),
            settings=self._make_settings(),
            _provider_fn=lambda t: _FakeSecEdgarProviderResult(
                ticker=t, cik="0009999998", filings=[_recent_10k()]
            ),
        )
        assert result is None

    def test_etf_holding_context_skipped(self):
        from app.services.intelligence.research_workers.evidence_lane_runner_v1 import (
            run_sec_catalyst_sentiment_evidence,
        )
        result = run_sec_catalyst_sentiment_evidence(
            user_id="u1",
            ticker="SPY",
            db_client=self._make_fake_db(),
            settings=self._make_settings(),
            holding_context={"category": "ETF"},
            _provider_fn=lambda t: _FakeSecEdgarProviderResult(
                ticker=t, cik="0000884394", filings=[_recent_10k()]
            ),
        )
        assert result is None

    def test_no_fresh_filings_no_artifact_written(self):
        from app.services.intelligence.research_workers.evidence_lane_runner_v1 import (
            run_sec_catalyst_sentiment_evidence,
        )
        written = []

        class _FakeService:
            def __init__(self, supabase_client, user_id):
                pass
            def write_artifact(self, output):
                written.append(output)
                return "should-not-happen"

        with patch(
            "app.services.intelligence.research_workers.evidence_lane_runner_v1"
            ".ResearchArtifactServiceV1",
            _FakeService,
        ):
            result = run_sec_catalyst_sentiment_evidence(
                user_id="u1",
                ticker="AAPL",
                db_client=self._make_fake_db(),
                settings=self._make_settings(),
                _provider_fn=lambda t: _FakeSecEdgarProviderResult(
                    ticker=t, cik="0000320193", filings=[_stale_10k()]
                ),
            )

        assert result is None
        assert written == [], "No artifact written when all filings are stale"

    def test_provider_error_handled_gracefully(self):
        from app.services.intelligence.research_workers.evidence_lane_runner_v1 import (
            run_sec_catalyst_sentiment_evidence,
        )

        def failing_provider(t):
            raise RuntimeError("SEC EDGAR unavailable")

        result = run_sec_catalyst_sentiment_evidence(
            user_id="u1",
            ticker="AAPL",
            db_client=self._make_fake_db(),
            settings=self._make_settings(),
            _provider_fn=failing_provider,
        )
        assert result is None

    def test_no_user_agent_skipped(self):
        from app.services.intelligence.research_workers.evidence_lane_runner_v1 import (
            run_sec_catalyst_sentiment_evidence,
        )
        provider_called = []

        def fake_provider(t):
            provider_called.append(t)
            return _FakeSecEdgarProviderResult(ticker=t, cik="x", filings=[])

        result = run_sec_catalyst_sentiment_evidence(
            user_id="u1",
            ticker="AAPL",
            db_client=self._make_fake_db(),
            settings=self._make_settings(user_agent=""),
        )
        assert result is None


# ── Stage 5J registry tests ───────────────────────────────────────────────────

class TestStage5JLaneRegistry:

    def test_sec_catalyst_sentiment_in_ticker_lane_registry(self):
        lane_names = [entry[0] for entry in TICKER_LANE_REGISTRY]
        assert LANE_SEC_CATALYST_SENTIMENT in lane_names

    def test_sec_catalyst_sentiment_maps_to_correct_artifact_type_and_skill_pack(self):
        for lane_name, artifact_type, skill_pack in TICKER_LANE_REGISTRY:
            if lane_name == LANE_SEC_CATALYST_SENTIMENT:
                assert artifact_type == "sentiment_event"
                assert skill_pack == SEC_CATALYST_SKILL_PACK
                return
        pytest.fail("LANE_SEC_CATALYST_SENTIMENT not found in TICKER_LANE_REGISTRY")

    def test_news_sentiment_lane_still_present(self):
        # Editorial yfinance lane must remain intact.
        lane_names = [entry[0] for entry in TICKER_LANE_REGISTRY]
        assert "news_sentiment" in lane_names

    def test_no_policy_keys_in_lane_registry(self):
        for entry in TICKER_LANE_REGISTRY:
            for part in entry:
                assert "buy" not in part.lower()
                assert "sell" not in part.lower()
                assert "trim" not in part.lower()
                assert "hold" not in part.lower()
                assert "recommendation" not in part.lower()


# ── Editorial path unchanged ──────────────────────────────────────────────────

class TestEditorialPathUnchanged:
    """Verify the yfinance editorial sentinel behavior is preserved."""

    def test_news_sentiment_lane_still_in_registry(self):
        lane_names = [entry[0] for entry in TICKER_LANE_REGISTRY]
        assert "news_sentiment" in lane_names

    def test_editorial_source_produces_not_usable(self):
        from app.services.intelligence.v3.sentiment_event_adapter_v2 import (
            SentimentEventV2Input,
            normalize_and_evaluate,
        )
        inp = SentimentEventV2Input(
            ticker="AAPL",
            event_id="news:001",
            source_authority="VENDOR_DERIVED",  # claimed high, but guard will cap it
            source_kind="news",                  # editorial source kind
            provider_name="yfinance",
            freshness_status="FRESH",
            source_count=5,
            fact_count=5,
            is_contradicted=False,
            completeness_band="COMPLETE",
            sentiment_polarity=None,
            catalyst_category_raw="earnings",
            materiality_raw="high",
            ticker_match_confidence_raw="high",
        )
        out = normalize_and_evaluate(inp)
        # Editorial source → capped to EDITORIAL_CONTEXT → NOT_USABLE
        assert out.decision_usefulness_tier == DECISION_USEFULNESS_NOT_USABLE
        assert out.effective_source_authority == "EDITORIAL_CONTEXT"

    def test_sec_catalyst_does_not_use_editorial_source_kind(self):
        result = adapt_sec_catalyst_sentiment(
            ticker="AAPL",
            provider_result=_FakeSecEdgarProviderResult(
                ticker="AAPL",
                cik="0000320193",
                filings=[_recent_10k()],
            ),
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )
        assert result.has_material_filings is True
        for src in result.sources:
            assert src.source_kind != "news"
            assert src.provider_name != "yfinance"


# ── Policy invariant tests ────────────────────────────────────────────────────

class TestNoPolicyChanges:

    def test_no_buy_hold_trim_sell_in_adapter_output(self):
        result = adapt_sec_catalyst_sentiment(
            ticker="AAPL",
            provider_result=_FakeSecEdgarProviderResult(
                ticker="AAPL",
                cik="0000320193",
                filings=[_recent_10k(), _recent_10q(), _recent_8k()],
            ),
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )
        assert result.has_material_filings is True
        for fact in result.facts:
            payload_str = str(fact.structured_payload).lower()
            for forbidden in ("buy", "sell", "trim", "hold", "recommendation", "final_action"):
                assert forbidden not in payload_str or forbidden in (
                    "buy",  # allowed if it's part of a longer word like "buyback"
                ), f"Policy term '{forbidden}' found in fact payload"
            # Be precise: check exact keys
            for key in ("buy", "sell", "trim", "hold", "recommendation", "final_action", "action"):
                assert key not in fact.structured_payload

    def test_decision_usefulness_tier_is_not_action(self):
        result = adapt_sec_catalyst_sentiment(
            ticker="AAPL",
            provider_result=_FakeSecEdgarProviderResult(
                ticker="AAPL",
                cik="0000320193",
                filings=[_recent_10k()],
            ),
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )
        tier = result.facts[0].structured_payload["decision_usefulness_tier"]
        # Tier must be one of the four valid values — never a trade action.
        valid_tiers = {DECISION_USEFULNESS_READY, DECISION_USEFULNESS_LIMITED,
                       DECISION_USEFULNESS_NOT_USABLE, DECISION_USEFULNESS_INELIGIBLE}
        assert tier in valid_tiers

    def test_safe_for_decision_not_in_adapter_result(self):
        result = adapt_sec_catalyst_sentiment(
            ticker="AAPL",
            provider_result=_FakeSecEdgarProviderResult(
                ticker="AAPL",
                cik="0000320193",
                filings=[_recent_10k()],
            ),
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )
        # Adapter result must not have a safe_for_decision field or attribute
        assert not hasattr(result, "safe_for_decision")
        for fact in result.facts:
            assert "safe_for_decision" not in fact.structured_payload


# ── Stage 5D/5E completeness and usability tests ──────────────────────────────

class TestCatalystFactComparabilityAndCompleteness:
    """Stage 8C PR 2.2: catalyst_item facts must produce PARTIAL completeness band.

    Root-cause fix: structured_payload now carries claim_key + text_value so the
    contradiction detector counts them as comparable_fact_count >= 1, allowing
    evidence_completeness_scorer._compute_band() to return PARTIAL instead of THIN.
    """

    def _get_fresh_10k_fact(self):
        result = adapt_sec_catalyst_sentiment(
            ticker="CRM",
            provider_result=_FakeSecEdgarProviderResult(
                ticker="CRM",
                cik="0001108524",
                filings=[_recent_10k()],
            ),
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )
        assert result.has_material_filings is True
        return result.facts[0], result.sources[0]

    def test_catalyst_item_fact_has_claim_key(self):
        fact, _ = self._get_fresh_10k_fact()
        assert "claim_key" in fact.structured_payload
        assert fact.structured_payload["claim_key"] == "catalyst_event_type"

    def test_catalyst_item_fact_has_text_value(self):
        fact, _ = self._get_fresh_10k_fact()
        assert "text_value" in fact.structured_payload
        # 10-K maps to catalyst_category "earnings"
        assert fact.structured_payload["text_value"] == "earnings"

    def test_catalyst_item_8k_has_corporate_action_text_value(self):
        result = adapt_sec_catalyst_sentiment(
            ticker="CRM",
            provider_result=_FakeSecEdgarProviderResult(
                ticker="CRM",
                cik="0001108524",
                filings=[_recent_8k()],
            ),
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )
        assert result.has_material_filings is True
        fact = result.facts[0]
        assert fact.structured_payload["text_value"] == "corporate_action"

    def test_contradiction_detector_counts_catalyst_fact_as_comparable(self):
        from app.services.intelligence.v3.contradiction_detector_v1 import detect_contradictions
        fact, _ = self._get_fresh_10k_fact()
        assessment = detect_contradictions([fact])
        assert assessment.comparable_fact_count >= 1, (
            "catalyst_item fact with claim_key + text_value must be comparable; "
            "comparable_fact_count=0 causes BAND_THIN"
        )

    def test_completeness_scorer_returns_partial_not_thin(self):
        from app.services.intelligence.v3.contradiction_detector_v1 import detect_contradictions
        from app.services.intelligence.v3.source_credibility_registry_v1 import assess_artifact_sources
        from app.services.intelligence.v3.evidence_completeness_scorer_v1 import (
            score_evidence_completeness,
            BAND_THIN,
            BAND_PARTIAL,
            BAND_COMPLETE,
        )
        fact, source = self._get_fresh_10k_fact()
        credibility = assess_artifact_sources([source])
        contradiction = detect_contradictions([fact])
        completeness = score_evidence_completeness(
            sources=[source],
            facts=[fact],
            credibility_assessment=credibility,
            contradiction_assessment=contradiction,
        )
        assert completeness.completeness_band != BAND_THIN, (
            f"Expected PARTIAL or COMPLETE for SEC catalyst with PRIMARY_AUTHORITY, "
            f"got {completeness.completeness_band}. "
            "comparable_fact_count must be >= 1 for catalyst_item facts."
        )
        assert completeness.completeness_band in (BAND_PARTIAL, BAND_COMPLETE)

    def test_truth_adapter_returns_usable_for_sec_catalyst(self):
        from app.services.intelligence.v3.contradiction_detector_v1 import detect_contradictions
        from app.services.intelligence.v3.source_credibility_registry_v1 import assess_artifact_sources
        from app.services.intelligence.v3.evidence_completeness_scorer_v1 import score_evidence_completeness
        from app.services.intelligence.v3.artifact_truth_adapter_v1 import assess_artifact_usability
        fact, source = self._get_fresh_10k_fact()
        credibility = assess_artifact_sources([source])
        contradiction = detect_contradictions([fact])
        completeness = score_evidence_completeness(
            sources=[source],
            facts=[fact],
            credibility_assessment=credibility,
            contradiction_assessment=contradiction,
        )
        usability = assess_artifact_usability(credibility, contradiction, completeness)
        assert usability.is_usable is True, (
            f"SEC catalyst artifact must be usable. Got usability_label={usability.usability_label}. "
            "SUPPRESSED_INCOMPLETE means comparable_fact_count=0 (claim_key/text_value missing)."
        )
        assert usability.usability_label in ("USABLE", "USABLE_WITH_LIMITATIONS")

    def test_missing_polarity_does_not_suppress_sec_catalyst(self):
        """None polarity must not cause SUPPRESSED_INCOMPLETE for SEC catalyst."""
        from app.services.intelligence.v3.contradiction_detector_v1 import detect_contradictions
        from app.services.intelligence.v3.source_credibility_registry_v1 import assess_artifact_sources
        from app.services.intelligence.v3.evidence_completeness_scorer_v1 import score_evidence_completeness
        from app.services.intelligence.v3.artifact_truth_adapter_v1 import assess_artifact_usability
        fact, source = self._get_fresh_10k_fact()
        # Confirm polarity is None (SEC filings never have scored polarity)
        assert fact.structured_payload["sentiment_polarity"] is None
        assert fact.structured_payload["is_polarity_present"] is False
        credibility = assess_artifact_sources([source])
        contradiction = detect_contradictions([fact])
        completeness = score_evidence_completeness(
            sources=[source],
            facts=[fact],
            credibility_assessment=credibility,
            contradiction_assessment=contradiction,
        )
        usability = assess_artifact_usability(credibility, contradiction, completeness)
        assert usability.is_usable is True, (
            "Missing sentiment_polarity (None) must not suppress SEC catalyst artifacts. "
            f"Got usability_label={usability.usability_label}"
        )

    def test_multiple_fresh_filings_all_counted_as_comparable(self):
        from app.services.intelligence.v3.contradiction_detector_v1 import detect_contradictions
        result = adapt_sec_catalyst_sentiment(
            ticker="MSFT",
            provider_result=_FakeSecEdgarProviderResult(
                ticker="MSFT",
                cik="0000789019",
                filings=[_recent_10k(), _recent_10q(), _recent_8k()],
            ),
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )
        assert result.has_material_filings is True
        assert result.catalyst_count == 3
        assessment = detect_contradictions(result.facts)
        assert assessment.comparable_fact_count == 3


# ── Stage 5J coverage read model tests ────────────────────────────────────────

class TestStage5JSECCatalystCoverage:
    """Stage 8C PR 2.2: sec_catalyst_sentiment lane must become LIMITED (not SUPPRESSED)."""

    def _build_fake_artifact_row(
        self,
        *,
        usability_label: str,
        is_usable: bool,
        completeness_band: str,
        source_authority: str = "PRIMARY_AUTHORITY",
        freshness_status: str = "FRESH",
        skill_pack: str = "sec_catalyst_sentiment_evidence_v1",
        ticker: str = "CRM",
    ) -> dict:
        return {
            "id": "artifact-" + ticker,
            "artifact_type": "sentiment_event",
            "skill_pack": skill_pack,
            "scope_kind": "ticker",
            "ticker": ticker,
            "confidence_or_trust_level": "HIGH",
            "freshness_status": freshness_status,
            "generated_at": "2024-01-01T00:00:00Z",
            "expires_at": None,
            "is_active": True,
            "model_version": "sec_catalyst_sentiment_adapter.v1",
            "safe_for_decision": False,
            "payload": {
                "truth_usability_assessment": {
                    "usability_label": usability_label,
                    "is_usable": is_usable,
                    "suppression_reason": None if is_usable else "evidence_completeness_thin:missing_requirements=has_comparable_fact_when_claim_is_metric_like",
                },
                "source_credibility_assessment": {
                    "strongest_authority_level": source_authority,
                    "is_insufficient": False,
                    "source_count": 1,
                },
                "contradiction_assessment": {
                    "is_evaluable": True,
                    "has_contradictions": False,
                    "comparable_fact_count": 1,
                    "non_comparable_fact_count": 0,
                    "contradiction_count": 0,
                    "contradiction_groups": [],
                },
                "evidence_completeness_assessment": {
                    "completeness_band": completeness_band,
                    "is_evaluable": True,
                    "source_count": 1,
                    "fact_count": 1,
                },
            },
        }

    def test_usable_with_limitations_artifact_becomes_limited_in_stage5j(self):
        from app.services.intelligence.v3.research_evidence_coverage_read_model_v1 import (
            _build_lane_coverage,
            LANE_SEC_CATALYST_SENTIMENT,
            STATUS_LIMITED,
        )
        row = self._build_fake_artifact_row(
            usability_label="USABLE_WITH_LIMITATIONS",
            is_usable=True,
            completeness_band="PARTIAL",
        )
        cov = _build_lane_coverage(
            lane=LANE_SEC_CATALYST_SENTIMENT,
            artifact_type="sentiment_event",
            skill_pack="sec_catalyst_sentiment_evidence_v1",
            scope_kind="ticker",
            ticker="CRM",
            row=row,
        )
        assert cov.status == STATUS_LIMITED, (
            f"USABLE_WITH_LIMITATIONS artifact must become STATUS_LIMITED in Stage 5J. "
            f"Got status={cov.status}"
        )
        assert cov.is_usable is True

    def test_suppressed_incomplete_artifact_remains_suppressed(self):
        from app.services.intelligence.v3.research_evidence_coverage_read_model_v1 import (
            _build_lane_coverage,
            LANE_SEC_CATALYST_SENTIMENT,
            STATUS_SUPPRESSED,
        )
        row = self._build_fake_artifact_row(
            usability_label="SUPPRESSED_INCOMPLETE",
            is_usable=False,
            completeness_band="THIN",
        )
        cov = _build_lane_coverage(
            lane=LANE_SEC_CATALYST_SENTIMENT,
            artifact_type="sentiment_event",
            skill_pack="sec_catalyst_sentiment_evidence_v1",
            scope_kind="ticker",
            ticker="CRM",
            row=row,
        )
        assert cov.status == STATUS_SUPPRESSED
        assert cov.is_usable is False

    def test_editorial_news_sentiment_suppressed_incomplete_remains_suppressed(self):
        from app.services.intelligence.v3.research_evidence_coverage_read_model_v1 import (
            _build_lane_coverage,
            LANE_NEWS_SENTIMENT,
            STATUS_SUPPRESSED,
        )
        row = self._build_fake_artifact_row(
            usability_label="SUPPRESSED_INCOMPLETE",
            is_usable=False,
            completeness_band="THIN",
            source_authority="EDITORIAL_CONTEXT",
            skill_pack="news_sentiment_evidence_v1",
        )
        cov = _build_lane_coverage(
            lane=LANE_NEWS_SENTIMENT,
            artifact_type="sentiment_event",
            skill_pack="news_sentiment_evidence_v1",
            scope_kind="ticker",
            ticker="CRM",
            row=row,
        )
        assert cov.status == STATUS_SUPPRESSED
        assert cov.is_usable is False


# ── Stage 5K sentiment axis tests ─────────────────────────────────────────────

class TestStage5KSentimentAxis:
    """Stage 8C PR 2.2: SEC catalyst lane now contributes to the sentiment axis."""

    def _make_lane_coverage(
        self,
        *,
        lane: str,
        status: str,
        is_usable: bool,
    ):
        from app.services.intelligence.v3.research_evidence_coverage_read_model_v1 import LaneCoverage
        return LaneCoverage(
            lane=lane,
            artifact_type="sentiment_event",
            skill_pack="sec_catalyst_sentiment_evidence_v1" if "catalyst" in lane else "news_sentiment_evidence_v1",
            scope_kind="ticker",
            ticker="CRM",
            artifact_id="test-id",
            status=status,
            usability_label="USABLE_WITH_LIMITATIONS" if is_usable else "SUPPRESSED_INCOMPLETE",
            is_usable=is_usable,
            suppression_reason=None if is_usable else "evidence_completeness_thin",
            source_authority="PRIMARY_AUTHORITY" if "catalyst" in lane else "EDITORIAL_CONTEXT",
            completeness_band="PARTIAL" if is_usable else "THIN",
            has_contradictions=False,
            freshness_status="FRESH",
            confidence_or_trust_level="HIGH",
            model_version="v1",
            generated_at="2024-01-01T00:00:00Z",
            expires_at=None,
            missing_reason=None if is_usable else "usability_suppressed",
        )

    def test_sec_catalyst_limited_makes_sentiment_axis_limited(self):
        from app.services.intelligence.v3.research_evidence_decision_input_adapter_v1 import (
            _build_sentiment_axis,
            READINESS_LIMITED,
        )
        from app.services.intelligence.v3.research_evidence_coverage_read_model_v1 import (
            LANE_SEC_CATALYST_SENTIMENT,
            LANE_NEWS_SENTIMENT,
            STATUS_LIMITED,
            STATUS_SUPPRESSED,
        )
        lanes = {
            LANE_SEC_CATALYST_SENTIMENT: self._make_lane_coverage(
                lane=LANE_SEC_CATALYST_SENTIMENT,
                status=STATUS_LIMITED,
                is_usable=True,
            ),
            LANE_NEWS_SENTIMENT: self._make_lane_coverage(
                lane=LANE_NEWS_SENTIMENT,
                status=STATUS_SUPPRESSED,
                is_usable=False,
            ),
        }
        axis = _build_sentiment_axis(lanes=lanes)
        assert axis.is_usable is True, (
            "Sentiment axis must be usable when SEC catalyst lane is LIMITED."
        )
        assert axis.readiness == READINESS_LIMITED
        assert LANE_SEC_CATALYST_SENTIMENT in axis.contributing_lanes
        assert LANE_NEWS_SENTIMENT not in axis.contributing_lanes
        assert LANE_NEWS_SENTIMENT in axis.degraded_lanes

    def test_both_suppressed_news_and_missing_catalyst_yields_insufficient(self):
        from app.services.intelligence.v3.research_evidence_decision_input_adapter_v1 import (
            _build_sentiment_axis,
            READINESS_INSUFFICIENT,
            READINESS_MISSING,
        )
        from app.services.intelligence.v3.research_evidence_coverage_read_model_v1 import (
            LANE_SEC_CATALYST_SENTIMENT,
            LANE_NEWS_SENTIMENT,
            STATUS_SUPPRESSED,
        )
        lanes = {
            LANE_NEWS_SENTIMENT: self._make_lane_coverage(
                lane=LANE_NEWS_SENTIMENT,
                status=STATUS_SUPPRESSED,
                is_usable=False,
            ),
            # LANE_SEC_CATALYST_SENTIMENT intentionally absent (MISSING)
        }
        axis = _build_sentiment_axis(lanes=lanes)
        assert axis.is_usable is False
        assert axis.readiness in (READINESS_INSUFFICIENT, READINESS_MISSING)

    def test_both_lanes_usable_yields_strongest(self):
        from app.services.intelligence.v3.research_evidence_decision_input_adapter_v1 import (
            _build_sentiment_axis,
            READINESS_LIMITED,
        )
        from app.services.intelligence.v3.research_evidence_coverage_read_model_v1 import (
            LANE_SEC_CATALYST_SENTIMENT,
            LANE_NEWS_SENTIMENT,
            STATUS_LIMITED,
        )
        lanes = {
            LANE_SEC_CATALYST_SENTIMENT: self._make_lane_coverage(
                lane=LANE_SEC_CATALYST_SENTIMENT,
                status=STATUS_LIMITED,
                is_usable=True,
            ),
            LANE_NEWS_SENTIMENT: self._make_lane_coverage(
                lane=LANE_NEWS_SENTIMENT,
                status=STATUS_LIMITED,
                is_usable=True,
            ),
        }
        axis = _build_sentiment_axis(lanes=lanes)
        assert axis.is_usable is True
        assert LANE_SEC_CATALYST_SENTIMENT in axis.contributing_lanes
        assert LANE_NEWS_SENTIMENT in axis.contributing_lanes

    def test_etf_btc_xrp_skip_unchanged(self):
        """ETF/BTC/XRP ineligibility is enforced before Stage 5K; INELIGIBLE never reaches axis."""
        from app.services.intelligence.v3.sentiment_event_adapter_v2 import (
            SentimentEventV2Input,
            normalize_and_evaluate,
            DECISION_USEFULNESS_INELIGIBLE,
        )
        for ticker, ctx in [
            ("BTC", None),
            ("XRP", None),
            ("SPY", {"category": "ETF"}),
        ]:
            inp = SentimentEventV2Input(
                ticker=ticker,
                event_id=f"{ticker}:filing:001",
                source_authority="PRIMARY_AUTHORITY",
                source_kind="sec_filing",
                provider_name="sec_edgar",
                freshness_status="FRESH",
                source_count=1,
                fact_count=1,
                is_contradicted=False,
                completeness_band="COMPLETE",
                sentiment_polarity=None,
                catalyst_category_raw="earnings",
                materiality_raw="high",
                ticker_match_confidence_raw="high",
                holding_context=ctx,
            )
            out = normalize_and_evaluate(inp)
            assert out.decision_usefulness_tier == DECISION_USEFULNESS_INELIGIBLE, (
                f"{ticker} must be INELIGIBLE, got {out.decision_usefulness_tier}"
            )
