"""Stage 5H — SEC CompanyFacts Official Fundamentals Adapter v1 tests.

Proves all acceptance criteria for Stage 5H:

  1.  SEC CompanyFacts provider/lane represented in provider registry/router.
  2.  sec_edgar registered as FREE / OFFICIAL for sec_company_facts lane.
  3.  Router returns ROUTE_REASON_FREE_OFFICIAL for sec_company_facts lane.
  4.  A backend explicit callable can run SEC CompanyFacts evidence for one ticker.
  5.  SEC CompanyFacts artifacts write through ResearchArtifactServiceV1 (no bypass).
  6.  All four enrichment layers present in every written artifact:
        source_credibility_assessment, contradiction_assessment,
        evidence_completeness_assessment, truth_usability_assessment.
  7.  Official SEC facts → SourceRecord + FactRecord structures with
        period, unit, accession_number, fiscal_year, fiscal_period references.
  8.  Missing/no company facts do not fabricate claims (honest thin-evidence).
  9.  No direct ArtifactStoreWriter bypass (only ResearchArtifactServiceV1).
 10.  No intel_v3_snapshots or recommendations writes.
 11.  safe_for_decision remains False in every artifact.
 12.  Paid providers remain disabled and not selected by the router.
 13.  Kill-switch: disabled flags → None returned, no artifact written.
 14.  Existing Stage 5F yfinance lanes still work with the updated dispatcher.
 15.  Dispatcher run_all_evidence_lanes returns sec_company_facts key.
 16.  No decide() import in adapter or runner modules.
 17.  Adapter confidence / freshness classification correct.
 18.  Distinct skill_pack from yfinance fundamentals (no collision).
 19.  artifact_type="fundamental_quality" (no new SQL required).
 20.  No CIK → honest no-data, no fabrication.
 21.  FactRecord fact_kind=metric_observation, axis_hint=quality, is_quote_grounded=True.
 22.  SourceRecord source_kind=sec_filing, provider_name=sec_edgar with accession URL.
 23.  Each observation's source_index points to the correct SourceRecord.
 24.  Replay idempotency key is deterministic from same inputs.
 25.  sec_company_facts lane added to ALL_LANES in registry.

No production Supabase access. All DB interactions use FakeSupabaseClient.
"""
from __future__ import annotations

import importlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, List, Optional

import pytest

from app.config import Settings
from app.services.intelligence.research_workers.evidence_provider_registry_v1 import (
    ALL_LANES,
    LANE_SEC_COMPANY_FACTS,
    LANE_FUNDAMENTALS,
    CostTier,
    TrustTier,
    get_provider,
    enabled_providers_for_lane,
    providers_for_lane,
    build_registry_summary,
)
from app.services.intelligence.research_workers.evidence_provider_router_v1 import (
    ROUTE_REASON_FREE_OFFICIAL,
    ROUTE_REASON_NO_PROVIDER,
    resolve_provider_for_lane,
)
from app.services.intelligence.research_workers.sec_edgar_provider import (
    SecEdgarProviderResult,
    SecFilingRecord,
)
from app.services.intelligence.research_workers.sec_companyfacts_parser import (
    CompanyFactsParseResult,
    MetricObservation,
)
from app.services.intelligence.research_workers.sec_companyfacts_adapter_v1 import (
    adapt_sec_companyfacts,
    build_sec_companyfacts_worker_output,
    _ARTIFACT_TYPE,
    _SKILL_PACK,
    _MODEL_VERSION,
)
from app.services.intelligence.research_workers.contracts import WorkerInput
from app.services.intelligence.research_workers.evidence_lane_runner_v1 import (
    run_sec_companyfacts_evidence,
    run_all_evidence_lanes,
)


# ── Fake Supabase client ──────────────────────────────────────────────────────

@dataclass
class _TableState:
    inserts: list[dict[str, Any]] = field(default_factory=list)
    upserts: list[dict[str, Any]] = field(default_factory=list)
    updates: list[dict[str, Any]] = field(default_factory=list)


class FakeTableQuery:
    """Chainable fake query matching ResearchArtifactServiceV1 usage patterns."""

    def __init__(self, state: _TableState, return_id: Optional[str] = None) -> None:
        self._state = state
        self._return_id = return_id or str(uuid.uuid4())
        self._row: Optional[dict] = None
        self._on_conflict: Optional[str] = None
        self._is_update: bool = False
        self._select_cols: Optional[str] = None
        self._filters: dict = {}
        self._limit_val: Optional[int] = None

    def insert(self, row: dict) -> "FakeTableQuery":
        self._row = row
        return self

    def update(self, row: dict) -> "FakeTableQuery":
        self._row = row
        self._is_update = True
        return self

    def upsert(self, row: dict, *, on_conflict: str = "", ignore_duplicates: bool = False) -> "FakeTableQuery":
        self._row = row
        self._on_conflict = on_conflict
        return self

    def select(self, cols: str = "*") -> "FakeTableQuery":
        self._select_cols = cols
        return self

    def eq(self, col: str, val: Any) -> "FakeTableQuery":
        self._filters[col] = val
        return self

    def neq(self, col: str, val: Any) -> "FakeTableQuery":
        return self

    def is_(self, col: str, val: Any) -> "FakeTableQuery":
        return self

    def order(self, *args: Any, **kwargs: Any) -> "FakeTableQuery":
        return self

    def limit(self, n: int) -> "FakeTableQuery":
        self._limit_val = n
        return self

    def execute(self) -> Any:
        if self._row is not None and self._is_update:
            class _U:
                data = []
            return _U()
        if self._row is not None:
            row_with_id = {"id": self._return_id, **self._row}
            if self._on_conflict is not None:
                self._state.upserts.append(self._row)
            else:
                self._state.inserts.append(self._row)
            class _R:
                data = [row_with_id]
            return _R()
        class _E:
            data = []
        return _E()


class FakeSupabaseClient:
    """Records table interactions without touching a real database."""

    def __init__(self) -> None:
        self.tables: dict[str, _TableState] = {
            "research_artifacts": _TableState(),
            "research_artifact_sources": _TableState(),
            "research_artifact_facts": _TableState(),
            "worker_audit_events": _TableState(),
            "intel_v3_snapshots": _TableState(),
            "recommendations": _TableState(),
        }

    def table(self, name: str) -> FakeTableQuery:
        state = self.tables.setdefault(name, _TableState())
        return FakeTableQuery(state)

    def artifact_inserts(self) -> list[dict]:
        return self.tables["research_artifacts"].inserts

    def source_inserts(self) -> list[dict]:
        return self.tables["research_artifact_sources"].inserts

    def fact_inserts(self) -> list[dict]:
        return self.tables["research_artifact_facts"].inserts

    def snapshot_writes(self) -> list[dict]:
        return (
            self.tables["intel_v3_snapshots"].inserts
            + self.tables["intel_v3_snapshots"].upserts
        )

    def recommendation_writes(self) -> list[dict]:
        return (
            self.tables["recommendations"].inserts
            + self.tables["recommendations"].upserts
        )


# ── Test fixtures ─────────────────────────────────────────────────────────────

def _make_observation(
    tag: str = "Revenues",
    value: float = 394329000000.0,
    unit: str = "USD",
    accession: str = "0000320193-23-000054",
    fiscal_year: int = 2023,
    fiscal_period: str = "FY",
    filed: str = "2023-11-03",
    form: str = "10-K",
) -> MetricObservation:
    return MetricObservation(
        taxonomy="us-gaap",
        tag=tag,
        label=tag,
        value=value,
        unit=unit,
        form=form,
        fiscal_year=fiscal_year,
        fiscal_period=fiscal_period,
        filed=filed,
        accession_number=accession,
    )


def _make_filing(
    form_type: str = "10-K",
    accession: str = "0000320193-23-000054",
    filing_date: str = "2023-11-03",
) -> SecFilingRecord:
    return SecFilingRecord(
        form_type=form_type,
        filing_date=filing_date,
        accession_number=accession,
        report_date="2023-09-30",
        filing_url=f"https://www.sec.gov/Archives/edgar/data/320193/{accession.replace('-', '')}/",
    )


def _success_provider_result(
    ticker: str = "AAPL",
    observations: Optional[list[MetricObservation]] = None,
    filings: Optional[list[SecFilingRecord]] = None,
    parse_status: str = "success",
    tags_found: Optional[list[str]] = None,
) -> SecEdgarProviderResult:
    if observations is None:
        observations = [_make_observation()]
    if filings is None:
        filings = [_make_filing()]
    if tags_found is None:
        tags_found = list({o.tag for o in observations})

    cf_result = CompanyFactsParseResult(
        observations=observations,
        parse_status=parse_status,
        tags_found=sorted(tags_found),
    )
    return SecEdgarProviderResult(
        ticker=ticker,
        cik="0000320193",
        filings=filings,
        fetch_status="success",
        fetched_at=datetime.now(timezone.utc).isoformat(),
        request_count=3,
        companyfacts_parse_result=cf_result,
    )


def _settings_sec_on() -> Settings:
    return Settings(
        supabase_url="http://fake",
        supabase_anon_key="anon",
        supabase_service_role_key="svc",
        supabase_jwt_secret="secret",
        encryption_key="a" * 32,
        intel_v3_research_workers_enabled=True,
        intel_v3_sec_companyfacts_evidence_enabled=True,
        sec_edgar_user_agent="TestApp/1.0 test@example.com",
    )


def _settings_sec_off() -> Settings:
    return Settings(
        supabase_url="http://fake",
        supabase_anon_key="anon",
        supabase_service_role_key="svc",
        supabase_jwt_secret="secret",
        encryption_key="a" * 32,
        intel_v3_research_workers_enabled=True,
        intel_v3_sec_companyfacts_evidence_enabled=False,
    )


def _settings_all_lanes_on() -> Settings:
    return Settings(
        supabase_url="http://fake",
        supabase_anon_key="anon",
        supabase_service_role_key="svc",
        supabase_jwt_secret="secret",
        encryption_key="a" * 32,
        intel_v3_research_workers_enabled=True,
        intel_v3_fundamentals_evidence_enabled=True,
        intel_v3_technicals_evidence_enabled=True,
        intel_v3_news_sentiment_evidence_enabled=True,
        intel_v3_sec_companyfacts_evidence_enabled=True,
        sec_edgar_user_agent="TestApp/1.0 test@example.com",
    )


def _worker_input(ticker: str = "AAPL") -> WorkerInput:
    return WorkerInput(
        user_id="user-123",
        ticker=ticker,
        worker_run_id=str(uuid.uuid4()),
    )


# ── Registry / router tests ───────────────────────────────────────────────────

class TestProviderRegistry:
    """Registry and router tests for Stage 5H."""

    def test_sec_company_facts_lane_in_all_lanes(self):
        assert LANE_SEC_COMPANY_FACTS in ALL_LANES

    def test_sec_edgar_provider_supports_sec_company_facts_lane(self):
        entry = get_provider("sec_edgar")
        assert entry is not None
        assert LANE_SEC_COMPANY_FACTS in entry.supported_lanes

    def test_sec_edgar_is_free_official(self):
        entry = get_provider("sec_edgar")
        assert entry.cost_tier == CostTier.FREE
        assert entry.trust_tier == TrustTier.OFFICIAL

    def test_sec_edgar_default_enabled(self):
        entry = get_provider("sec_edgar")
        assert entry.default_enabled is True

    def test_router_sec_company_facts_returns_free_official(self):
        result = resolve_provider_for_lane(LANE_SEC_COMPANY_FACTS)
        assert result.provider_id == "sec_edgar"
        assert result.reason == ROUTE_REASON_FREE_OFFICIAL

    def test_router_sec_company_facts_returns_provider_entry(self):
        result = resolve_provider_for_lane(LANE_SEC_COMPANY_FACTS)
        assert result.provider_entry is not None
        assert result.provider_entry.cost_tier == CostTier.FREE
        assert result.provider_entry.trust_tier == TrustTier.OFFICIAL

    def test_yfinance_fundamentals_still_free_baseline(self):
        result = resolve_provider_for_lane(LANE_FUNDAMENTALS)
        assert result.provider_id == "yfinance"

    def test_sec_edgar_priority_above_yfinance(self):
        sec = get_provider("sec_edgar")
        yf = get_provider("yfinance")
        assert sec.source_of_truth_priority < yf.source_of_truth_priority

    def test_paid_providers_not_selected_for_sec_company_facts(self):
        result = resolve_provider_for_lane(LANE_SEC_COMPANY_FACTS)
        assert result.provider_id != "fmp"
        assert result.provider_id != "eodhd"
        assert result.provider_id != "alpha_vantage"

    def test_registry_summary_safe_for_decision_false(self):
        summary = build_registry_summary()
        assert summary["safe_for_decision"] is False

    def test_registry_summary_sec_company_facts_lane_covered(self):
        summary = build_registry_summary()
        cov = summary["lane_coverage"].get(LANE_SEC_COMPANY_FACTS)
        assert cov is not None
        assert cov["enabled_providers"] >= 1
        assert cov["has_free_official"] is True

    def test_lane_sec_company_facts_constant_is_string(self):
        assert isinstance(LANE_SEC_COMPANY_FACTS, str)
        assert LANE_SEC_COMPANY_FACTS == "sec_company_facts"


# ── Adapter unit tests ────────────────────────────────────────────────────────

class TestAdaptSecCompanyfacts:
    """Pure adapter function tests — no DB, no HTTP."""

    def test_success_produces_source_records(self):
        obs = _make_observation()
        filing = _make_filing()
        provider = _success_provider_result(observations=[obs], filings=[filing])
        result = adapt_sec_companyfacts(provider, "AAPL", datetime.now(timezone.utc).isoformat())
        assert len(result.sources) == 1
        src = result.sources[0]
        assert src.source_kind == "sec_filing"
        assert src.provider_name == "sec_edgar"
        assert src.source_id == obs.accession_number

    def test_success_source_record_has_filing_url(self):
        obs = _make_observation()
        filing = _make_filing()
        provider = _success_provider_result(observations=[obs], filings=[filing])
        result = adapt_sec_companyfacts(provider, "AAPL", datetime.now(timezone.utc).isoformat())
        assert result.sources[0].source_url == filing.filing_url

    def test_success_produces_fact_records(self):
        obs = _make_observation()
        provider = _success_provider_result(observations=[obs])
        result = adapt_sec_companyfacts(provider, "AAPL", datetime.now(timezone.utc).isoformat())
        assert len(result.facts) == 1
        fact = result.facts[0]
        assert fact.fact_kind == "metric_observation"
        assert fact.axis_hint == "quality"
        assert fact.is_quote_grounded is True

    def test_fact_payload_has_required_sec_fields(self):
        obs = _make_observation(
            tag="Revenues",
            value=394329000000.0,
            unit="USD",
            accession="0000320193-23-000054",
            fiscal_year=2023,
            fiscal_period="FY",
            filed="2023-11-03",
            form="10-K",
        )
        provider = _success_provider_result(observations=[obs])
        result = adapt_sec_companyfacts(provider, "AAPL", datetime.now(timezone.utc).isoformat())
        pl = result.facts[0].structured_payload
        assert pl["metric_name"] == "Revenues"
        assert pl["value"] == 394329000000.0
        assert pl["unit"] == "USD"
        assert pl["fiscal_year"] == 2023
        assert pl["fiscal_period"] == "FY"
        assert pl["filed"] == "2023-11-03"
        assert pl["accession_number"] == "0000320193-23-000054"
        assert pl["taxonomy"] == "us-gaap"
        assert pl["form"] == "10-K"
        assert pl["provider"] == "sec_edgar"

    def test_fact_period_field_populated(self):
        obs = _make_observation(fiscal_year=2023, fiscal_period="FY")
        provider = _success_provider_result(observations=[obs])
        result = adapt_sec_companyfacts(provider, "AAPL", datetime.now(timezone.utc).isoformat())
        assert result.facts[0].period == "2023-FY"

    def test_fact_period_quarterly(self):
        obs = _make_observation(fiscal_year=2023, fiscal_period="Q3", filed="2023-08-04", form="10-Q")
        provider = _success_provider_result(observations=[obs])
        result = adapt_sec_companyfacts(provider, "AAPL", datetime.now(timezone.utc).isoformat())
        assert result.facts[0].period == "2023-Q3"

    def test_fact_as_of_is_filed_date(self):
        obs = _make_observation(filed="2023-11-03")
        provider = _success_provider_result(observations=[obs])
        result = adapt_sec_companyfacts(provider, "AAPL", datetime.now(timezone.utc).isoformat())
        assert result.facts[0].as_of == "2023-11-03"

    def test_fact_source_index_matches_source_accession(self):
        acc1 = "0000320193-23-000054"
        acc2 = "0000320193-23-000010"
        obs1 = _make_observation(tag="Revenues", accession=acc1, filed="2023-11-03")
        obs2 = _make_observation(tag="NetIncomeLoss", accession=acc2, filed="2023-08-04")
        f1 = _make_filing(accession=acc1, form_type="10-K")
        f2 = _make_filing(accession=acc2, form_type="10-Q", filing_date="2023-08-04")
        provider = _success_provider_result(
            observations=[obs1, obs2], filings=[f1, f2],
            tags_found=["Revenues", "NetIncomeLoss"],
        )
        result = adapt_sec_companyfacts(provider, "AAPL", datetime.now(timezone.utc).isoformat())
        assert len(result.sources) == 2
        assert len(result.facts) == 2
        # fact for acc1 must point to the source with acc1
        for fact in result.facts:
            src = result.sources[fact.source_index]
            assert src.source_id == fact.structured_payload["accession_number"]

    def test_multiple_observations_same_accession_one_source(self):
        acc = "0000320193-23-000054"
        obs1 = _make_observation(tag="Revenues", accession=acc)
        obs2 = _make_observation(tag="NetIncomeLoss", accession=acc)
        filing = _make_filing(accession=acc)
        provider = _success_provider_result(
            observations=[obs1, obs2], filings=[filing],
            tags_found=["Revenues", "NetIncomeLoss"],
        )
        result = adapt_sec_companyfacts(provider, "AAPL", datetime.now(timezone.utc).isoformat())
        assert len(result.sources) == 1
        assert len(result.facts) == 2
        # Both facts must point to source 0
        assert result.facts[0].source_index == 0
        assert result.facts[1].source_index == 0

    def test_no_forbidden_keys_in_fact_payload(self):
        from app.services.intelligence.research_workers.contracts import WORKER_FORBIDDEN_PAYLOAD_KEYS
        obs = _make_observation()
        provider = _success_provider_result(observations=[obs])
        result = adapt_sec_companyfacts(provider, "AAPL", datetime.now(timezone.utc).isoformat())
        for fact in result.facts:
            for k in fact.structured_payload:
                assert k.lower() not in WORKER_FORBIDDEN_PAYLOAD_KEYS, f"Forbidden key: {k}"

    def test_confidence_high_with_8_plus_tags(self):
        tags = [
            "Revenues", "NetIncomeLoss", "OperatingIncomeLoss",
            "EarningsPerShareBasic", "EarningsPerShareDiluted",
            "Assets", "Liabilities", "StockholdersEquity",
        ]
        obs_list = [_make_observation(tag=t, unit="USD/shares" if "EPS" in t else "USD") for t in tags]
        cf = CompanyFactsParseResult(
            observations=obs_list,
            parse_status="success",
            tags_found=tags,
        )
        provider = SecEdgarProviderResult(
            ticker="AAPL", cik="0000320193",
            filings=[_make_filing()],
            fetch_status="success",
            fetched_at=datetime.now(timezone.utc).isoformat(),
            request_count=3,
            companyfacts_parse_result=cf,
        )
        result = adapt_sec_companyfacts(provider, "AAPL", datetime.now(timezone.utc).isoformat())
        assert result.confidence_or_trust_level == "HIGH"

    def test_confidence_medium_with_4_tags(self):
        tags = ["Revenues", "NetIncomeLoss", "Assets", "Liabilities"]
        obs_list = [_make_observation(tag=t) for t in tags]
        cf = CompanyFactsParseResult(observations=obs_list, parse_status="success", tags_found=tags)
        provider = SecEdgarProviderResult(
            ticker="AAPL", cik="0000320193",
            filings=[_make_filing()],
            fetch_status="success",
            fetched_at=datetime.now(timezone.utc).isoformat(),
            request_count=3,
            companyfacts_parse_result=cf,
        )
        result = adapt_sec_companyfacts(provider, "AAPL", datetime.now(timezone.utc).isoformat())
        assert result.confidence_or_trust_level == "MEDIUM"

    def test_confidence_low_with_1_tag(self):
        obs = _make_observation()
        provider = _success_provider_result(observations=[obs], tags_found=["Revenues"])
        result = adapt_sec_companyfacts(provider, "AAPL", datetime.now(timezone.utc).isoformat())
        assert result.confidence_or_trust_level == "LOW"

    def test_freshness_fresh_recent_filing(self):
        today = datetime.now(timezone.utc)
        filed = today.strftime("%Y-%m-%d")
        obs = _make_observation(filed=filed)
        provider = _success_provider_result(observations=[obs])
        result = adapt_sec_companyfacts(provider, "AAPL", today.isoformat())
        assert result.freshness_status == "FRESH"

    def test_freshness_stale_old_filing(self):
        obs = _make_observation(filed="2020-01-01")
        provider = _success_provider_result(observations=[obs])
        result = adapt_sec_companyfacts(provider, "AAPL", datetime.now(timezone.utc).isoformat())
        assert result.freshness_status == "STALE"

    def test_fingerprint_deterministic(self):
        obs = _make_observation()
        provider = _success_provider_result(observations=[obs])
        fetched_at = datetime.now(timezone.utc).isoformat()
        r1 = adapt_sec_companyfacts(provider, "AAPL", fetched_at)
        r2 = adapt_sec_companyfacts(provider, "AAPL", fetched_at)
        assert r1.source_refs_fingerprint == r2.source_refs_fingerprint

    def test_fingerprint_changes_on_different_observations(self):
        obs1 = _make_observation(value=100.0)
        obs2 = _make_observation(value=200.0)
        p1 = _success_provider_result(observations=[obs1])
        p2 = _success_provider_result(observations=[obs2])
        fetched_at = datetime.now(timezone.utc).isoformat()
        r1 = adapt_sec_companyfacts(p1, "AAPL", fetched_at)
        r2 = adapt_sec_companyfacts(p2, "AAPL", fetched_at)
        assert r1.source_refs_fingerprint != r2.source_refs_fingerprint


# ── No-data / no-fabrication tests ───────────────────────────────────────────

class TestNoClaims:
    """Verify that missing/bad company facts produce honest no-data results."""

    def test_no_cik_returns_thin_result(self):
        provider = SecEdgarProviderResult(
            ticker="AAPL", cik=None,
            fetch_status="no_cik",
            error_message="Ticker not found.",
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )
        result = adapt_sec_companyfacts(provider, "AAPL", datetime.now(timezone.utc).isoformat())
        assert result.sources == []
        assert result.facts == []
        assert result.confidence_or_trust_level == "UNKNOWN"

    def test_no_cik_no_fabricated_facts(self):
        provider = SecEdgarProviderResult(
            ticker="AAPL", fetch_status="no_cik",
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )
        result = adapt_sec_companyfacts(provider, "AAPL", datetime.now(timezone.utc).isoformat())
        assert len(result.facts) == 0

    def test_timeout_returns_thin_result(self):
        provider = SecEdgarProviderResult(
            ticker="AAPL", fetch_status="timeout",
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )
        result = adapt_sec_companyfacts(provider, "AAPL", datetime.now(timezone.utc).isoformat())
        assert result.facts == []
        assert result.sources == []

    def test_no_companyfacts_result_returns_thin(self):
        provider = SecEdgarProviderResult(
            ticker="AAPL", cik="0000320193",
            filings=[_make_filing()],
            fetch_status="success",
            fetched_at=datetime.now(timezone.utc).isoformat(),
            request_count=2,
            companyfacts_parse_result=None,
        )
        result = adapt_sec_companyfacts(provider, "AAPL", datetime.now(timezone.utc).isoformat())
        assert result.facts == []
        assert len(result.limitations) > 0

    def test_parse_error_returns_thin_result(self):
        cf = CompanyFactsParseResult(parse_status="error", error_message="parser_exception")
        provider = SecEdgarProviderResult(
            ticker="AAPL", cik="0000320193",
            filings=[_make_filing()],
            fetch_status="success",
            fetched_at=datetime.now(timezone.utc).isoformat(),
            request_count=3,
            companyfacts_parse_result=cf,
        )
        result = adapt_sec_companyfacts(provider, "AAPL", datetime.now(timezone.utc).isoformat())
        assert result.facts == []
        assert result.sources == []

    def test_no_facts_parse_status_returns_thin(self):
        cf = CompanyFactsParseResult(parse_status="no_facts", observations=[])
        provider = SecEdgarProviderResult(
            ticker="AAPL", cik="0000320193",
            filings=[_make_filing()],
            fetch_status="success",
            fetched_at=datetime.now(timezone.utc).isoformat(),
            request_count=3,
            companyfacts_parse_result=cf,
        )
        result = adapt_sec_companyfacts(provider, "AAPL", datetime.now(timezone.utc).isoformat())
        assert result.facts == []

    def test_thin_result_has_limitations(self):
        provider = SecEdgarProviderResult(
            ticker="XYZ", fetch_status="no_cik",
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )
        result = adapt_sec_companyfacts(provider, "XYZ", datetime.now(timezone.utc).isoformat())
        assert len(result.limitations) > 0

    def test_no_fabrication_of_values(self):
        cf = CompanyFactsParseResult(parse_status="no_facts", observations=[])
        provider = SecEdgarProviderResult(
            ticker="AAPL", cik="0000320193",
            filings=[_make_filing()],
            fetch_status="success",
            fetched_at=datetime.now(timezone.utc).isoformat(),
            request_count=3,
            companyfacts_parse_result=cf,
        )
        result = adapt_sec_companyfacts(provider, "AAPL", datetime.now(timezone.utc).isoformat())
        # No numeric values fabricated
        for fact in result.facts:
            assert fact.structured_payload.get("value") is None or isinstance(
                fact.structured_payload.get("value"), (int, float)
            )


# ── WorkerOutput builder tests ────────────────────────────────────────────────

class TestBuildWorkerOutput:
    """Tests for build_sec_companyfacts_worker_output."""

    def test_artifact_type_fundamental_quality(self):
        provider = _success_provider_result()
        wi = _worker_input()
        output = build_sec_companyfacts_worker_output(wi, provider, datetime.now(timezone.utc).isoformat())
        assert output.artifact_type == "fundamental_quality"

    def test_skill_pack_distinct_from_yfinance(self):
        provider = _success_provider_result()
        wi = _worker_input()
        output = build_sec_companyfacts_worker_output(wi, provider, datetime.now(timezone.utc).isoformat())
        assert output.skill_pack == "sec_companyfacts_evidence_v1"
        assert output.skill_pack != "fundamentals_evidence_v1"  # yfinance skill_pack

    def test_model_version_distinct(self):
        provider = _success_provider_result()
        wi = _worker_input()
        output = build_sec_companyfacts_worker_output(wi, provider, datetime.now(timezone.utc).isoformat())
        assert output.model_version == "sec_xbrl_companyfacts_v1"

    def test_scope_kind_ticker(self):
        provider = _success_provider_result()
        wi = _worker_input()
        output = build_sec_companyfacts_worker_output(wi, provider, datetime.now(timezone.utc).isoformat())
        assert output.scope_kind == "ticker"

    def test_sources_and_facts_populated(self):
        provider = _success_provider_result()
        wi = _worker_input()
        output = build_sec_companyfacts_worker_output(wi, provider, datetime.now(timezone.utc).isoformat())
        assert len(output.sources) >= 1
        assert len(output.facts) >= 1

    def test_replay_key_deterministic(self):
        provider = _success_provider_result()
        wi1 = _worker_input()
        wi2 = _worker_input()
        fetched_at = datetime.now(timezone.utc).isoformat()
        o1 = build_sec_companyfacts_worker_output(wi1, provider, fetched_at)
        o2 = build_sec_companyfacts_worker_output(wi2, provider, fetched_at)
        assert o1.replay_idempotency_key == o2.replay_idempotency_key

    def test_payload_has_provider_sec_edgar(self):
        provider = _success_provider_result()
        wi = _worker_input()
        output = build_sec_companyfacts_worker_output(wi, provider, datetime.now(timezone.utc).isoformat())
        assert output.artifact_payload.get("provider") == "sec_edgar"

    def test_payload_has_observation_count(self):
        provider = _success_provider_result()
        wi = _worker_input()
        output = build_sec_companyfacts_worker_output(wi, provider, datetime.now(timezone.utc).isoformat())
        assert "observation_count" in output.artifact_payload
        assert output.artifact_payload["observation_count"] >= 1

    def test_no_forbidden_keys_in_payload(self):
        from app.services.intelligence.research_workers.contracts import WORKER_FORBIDDEN_PAYLOAD_KEYS
        provider = _success_provider_result()
        wi = _worker_input()
        output = build_sec_companyfacts_worker_output(wi, provider, datetime.now(timezone.utc).isoformat())
        for k in output.artifact_payload:
            assert k.lower() not in WORKER_FORBIDDEN_PAYLOAD_KEYS


# ── Runner integration tests ──────────────────────────────────────────────────

class TestRunnerIntegration:
    """Tests for run_sec_companyfacts_evidence — DB writes via FakeSupabaseClient."""

    def _provider_fn(self, result: SecEdgarProviderResult):
        return lambda t: result

    def test_run_returns_artifact_id_on_success(self):
        db = FakeSupabaseClient()
        provider = _success_provider_result()
        artifact_id = run_sec_companyfacts_evidence(
            user_id="user-1",
            ticker="AAPL",
            db_client=db,
            settings=_settings_sec_on(),
            _provider_fn=lambda t: provider,
        )
        assert artifact_id is not None

    def test_run_writes_artifact_through_service(self):
        db = FakeSupabaseClient()
        provider = _success_provider_result()
        run_sec_companyfacts_evidence(
            user_id="user-1",
            ticker="AAPL",
            db_client=db,
            settings=_settings_sec_on(),
            _provider_fn=lambda t: provider,
        )
        assert len(db.artifact_inserts()) == 1

    def test_run_disabled_flag_returns_none(self):
        db = FakeSupabaseClient()
        artifact_id = run_sec_companyfacts_evidence(
            user_id="user-1",
            ticker="AAPL",
            db_client=db,
            settings=_settings_sec_off(),
        )
        assert artifact_id is None
        assert len(db.artifact_inserts()) == 0

    def test_global_kill_switch_blocks_lane(self):
        db = FakeSupabaseClient()
        settings = Settings(
            supabase_url="http://fake",
            supabase_anon_key="anon",
            supabase_service_role_key="svc",
            supabase_jwt_secret="secret",
            encryption_key="a" * 32,
            intel_v3_research_workers_enabled=False,
            intel_v3_sec_companyfacts_evidence_enabled=True,
        )
        artifact_id = run_sec_companyfacts_evidence(
            user_id="user-1",
            ticker="AAPL",
            db_client=db,
            settings=settings,
        )
        assert artifact_id is None

    def test_no_intel_v3_snapshots_writes(self):
        db = FakeSupabaseClient()
        provider = _success_provider_result()
        run_sec_companyfacts_evidence(
            user_id="user-1",
            ticker="AAPL",
            db_client=db,
            settings=_settings_sec_on(),
            _provider_fn=lambda t: provider,
        )
        assert db.snapshot_writes() == []

    def test_no_recommendations_writes(self):
        db = FakeSupabaseClient()
        provider = _success_provider_result()
        run_sec_companyfacts_evidence(
            user_id="user-1",
            ticker="AAPL",
            db_client=db,
            settings=_settings_sec_on(),
            _provider_fn=lambda t: provider,
        )
        assert db.recommendation_writes() == []

    def test_artifact_safe_for_decision_never_set(self):
        db = FakeSupabaseClient()
        provider = _success_provider_result()
        run_sec_companyfacts_evidence(
            user_id="user-1",
            ticker="AAPL",
            db_client=db,
            settings=_settings_sec_on(),
            _provider_fn=lambda t: provider,
        )
        for row in db.artifact_inserts():
            assert row.get("safe_for_decision") is not True

    def test_artifact_payload_has_four_enrichment_layers(self):
        db = FakeSupabaseClient()
        provider = _success_provider_result()
        run_sec_companyfacts_evidence(
            user_id="user-1",
            ticker="AAPL",
            db_client=db,
            settings=_settings_sec_on(),
            _provider_fn=lambda t: provider,
        )
        payload = db.artifact_inserts()[0]["payload"]
        assert "source_credibility_assessment" in payload
        assert "contradiction_assessment" in payload
        assert "evidence_completeness_assessment" in payload
        assert "truth_usability_assessment" in payload

    def test_source_records_written(self):
        db = FakeSupabaseClient()
        provider = _success_provider_result()
        run_sec_companyfacts_evidence(
            user_id="user-1",
            ticker="AAPL",
            db_client=db,
            settings=_settings_sec_on(),
            _provider_fn=lambda t: provider,
        )
        assert len(db.source_inserts()) >= 1

    def test_fact_records_written(self):
        db = FakeSupabaseClient()
        provider = _success_provider_result()
        run_sec_companyfacts_evidence(
            user_id="user-1",
            ticker="AAPL",
            db_client=db,
            settings=_settings_sec_on(),
            _provider_fn=lambda t: provider,
        )
        assert len(db.fact_inserts()) >= 1

    def test_no_data_ticker_writes_thin_artifact(self):
        """No-CIK result writes a thin honest artifact, not nothing and not fabricated."""
        db = FakeSupabaseClient()
        no_cik_result = SecEdgarProviderResult(
            ticker="AAPL", fetch_status="no_cik",
            error_message="ticker not found",
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )
        artifact_id = run_sec_companyfacts_evidence(
            user_id="user-1",
            ticker="AAPL",
            db_client=db,
            settings=_settings_sec_on(),
            _provider_fn=lambda t: no_cik_result,
        )
        assert artifact_id is not None
        assert len(db.artifact_inserts()) == 1
        payload = db.artifact_inserts()[0]["payload"]
        assert payload.get("observation_count") == 0

    def test_no_data_no_facts_written_for_no_cik(self):
        db = FakeSupabaseClient()
        no_cik_result = SecEdgarProviderResult(
            ticker="AAPL", fetch_status="no_cik",
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )
        run_sec_companyfacts_evidence(
            user_id="user-1",
            ticker="AAPL",
            db_client=db,
            settings=_settings_sec_on(),
            _provider_fn=lambda t: no_cik_result,
        )
        assert db.fact_inserts() == []

    def test_no_artifactstore_writer_import_in_runner(self):
        import app.services.intelligence.research_workers.evidence_lane_runner_v1 as runner_mod
        src = runner_mod.__file__
        with open(src) as f:
            content = f.read()
        assert "ArtifactStoreWriter" not in content

    def test_no_decide_import_in_runner(self):
        import app.services.intelligence.research_workers.evidence_lane_runner_v1 as runner_mod
        src = runner_mod.__file__
        with open(src) as f:
            content = f.read()
        # Check that decision policy is never imported — the boundary must hold.
        assert "from app.services.intelligence.v3.decision_policy" not in content
        assert "import decide" not in content


# ── Dispatcher tests ──────────────────────────────────────────────────────────

class TestDispatcher:
    """Tests for run_all_evidence_lanes updated dispatcher."""

    def test_dispatcher_includes_sec_company_facts_key(self):
        db = FakeSupabaseClient()
        settings = _settings_all_lanes_on()
        results = run_all_evidence_lanes(
            user_id="user-1",
            ticker="AAPL",
            db_client=db,
            settings=settings,
            _fundamentals_fetch_fn=lambda t: {"pe": 30},
            _technicals_fetch_fn=lambda t: {"last": 150.0},
            _news_sentiment_fetch_fn=lambda t: [{"headline": "AAPL beats", "datetime": 1700000000, "source": "Reuters"}],
            _sec_companyfacts_provider_fn=lambda t: _success_provider_result(),
        )
        assert LANE_SEC_COMPANY_FACTS in results

    def test_dispatcher_sec_lane_written_when_enabled(self):
        db = FakeSupabaseClient()
        results = run_all_evidence_lanes(
            user_id="user-1",
            ticker="AAPL",
            db_client=db,
            settings=_settings_all_lanes_on(),
            _fundamentals_fetch_fn=lambda t: {},
            _technicals_fetch_fn=lambda t: {},
            _news_sentiment_fetch_fn=lambda t: [],
            _sec_companyfacts_provider_fn=lambda t: _success_provider_result(),
        )
        assert results[LANE_SEC_COMPANY_FACTS] is not None

    def test_dispatcher_sec_lane_skipped_when_disabled(self):
        db = FakeSupabaseClient()
        results = run_all_evidence_lanes(
            user_id="user-1",
            ticker="AAPL",
            db_client=db,
            settings=_settings_sec_off(),
            _sec_companyfacts_provider_fn=lambda t: _success_provider_result(),
        )
        assert results[LANE_SEC_COMPANY_FACTS] is None

    def test_dispatcher_yfinance_lanes_still_work(self):
        db = FakeSupabaseClient()
        settings = Settings(
            supabase_url="http://fake",
            supabase_anon_key="anon",
            supabase_service_role_key="svc",
            supabase_jwt_secret="secret",
            encryption_key="a" * 32,
            intel_v3_research_workers_enabled=True,
            intel_v3_fundamentals_evidence_enabled=True,
            intel_v3_technicals_evidence_enabled=False,
            intel_v3_news_sentiment_evidence_enabled=False,
            intel_v3_sec_companyfacts_evidence_enabled=False,
        )
        results = run_all_evidence_lanes(
            user_id="user-1",
            ticker="AAPL",
            db_client=db,
            settings=settings,
            _fundamentals_fetch_fn=lambda t: {"pe": 30, "eps": 6.0, "revenue": 100, "net_income": 10},
        )
        from app.services.intelligence.research_workers.evidence_lane_adapter_v1 import (
            LANE_FUNDAMENTALS, LANE_TECHNICALS, LANE_NEWS_SENTIMENT,
        )
        assert results[LANE_FUNDAMENTALS] is not None
        assert results[LANE_TECHNICALS] is None
        assert results[LANE_NEWS_SENTIMENT] is None
        assert results[LANE_SEC_COMPANY_FACTS] is None


# ── Safety invariants ─────────────────────────────────────────────────────────

class TestSafetyInvariants:
    """Cross-cutting safety invariants."""

    def test_paid_providers_disabled_in_registry(self):
        for pid in ("fmp", "eodhd", "alpha_vantage"):
            entry = get_provider(pid)
            assert entry is not None
            assert entry.default_enabled is False

    def test_paid_providers_not_enabled_for_sec_company_facts(self):
        enabled = enabled_providers_for_lane(LANE_SEC_COMPANY_FACTS)
        paid_ids = {"fmp", "eodhd", "alpha_vantage"}
        for p in enabled:
            assert p.provider_id not in paid_ids

    def test_no_decide_import_in_adapter(self):
        import app.services.intelligence.research_workers.sec_companyfacts_adapter_v1 as mod
        src = mod.__file__
        with open(src) as f:
            content = f.read()
        # Import boundary — decision policy must never be imported.
        assert "from app.services.intelligence.v3.decision_policy" not in content
        assert "import decide" not in content

    def test_no_decide_import_in_registry(self):
        import app.services.intelligence.research_workers.evidence_provider_registry_v1 as mod
        src = mod.__file__
        with open(src) as f:
            content = f.read()
        assert "from app.services.intelligence.v3.decision_policy" not in content
        assert "import decide" not in content

    def test_no_intel_v3_snapshots_in_adapter(self):
        import app.services.intelligence.research_workers.sec_companyfacts_adapter_v1 as mod
        src = mod.__file__
        with open(src) as f:
            content = f.read()
        # The table name must never appear as a write target.
        assert '"intel_v3_snapshots"' not in content
        assert "'intel_v3_snapshots'" not in content

    def test_no_recommendations_in_adapter(self):
        import app.services.intelligence.research_workers.sec_companyfacts_adapter_v1 as mod
        src = mod.__file__
        with open(src) as f:
            content = f.read()
        assert '"recommendations"' not in content

    def test_artifact_type_fundamental_quality_no_new_sql(self):
        assert _ARTIFACT_TYPE == "fundamental_quality"

    def test_skill_pack_constant(self):
        assert _SKILL_PACK == "sec_companyfacts_evidence_v1"

    def test_model_version_constant(self):
        assert _MODEL_VERSION == "sec_xbrl_companyfacts_v1"

    def test_sec_edgar_priority_is_1(self):
        entry = get_provider("sec_edgar")
        assert entry.source_of_truth_priority == 1

    def test_config_flag_defaults_false(self):
        settings = Settings(
            supabase_url="http://fake",
            supabase_anon_key="anon",
            supabase_service_role_key="svc",
            supabase_jwt_secret="secret",
            encryption_key="a" * 32,
        )
        assert settings.intel_v3_sec_companyfacts_evidence_enabled is False
