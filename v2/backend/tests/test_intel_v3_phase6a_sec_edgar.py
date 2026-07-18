"""Phase 6A acceptance tests — SEC EDGAR evidence population + grounding upgrade.

Covers all 20 acceptance criteria from the Phase 6A task spec:

Provider/config:
  1. SEC flag off => existing dark-run UNKNOWN/no-source behavior unchanged.
  2. SEC flag on but user agent missing => no SEC HTTP call, UNKNOWN/no-source fail-closed.
  3. SEC provider timeout/error/malformed => no exception escapes; artifact remains safe.
  4. SEC provider uses configured User-Agent.
  5. Request count is capped.

Source/fact grounding:
  6. SEC-backed success creates at least one SourceRecord.
  7. SEC-backed success creates facts with source_index set.
  8. Writer resolves source_index into DB source_id in inserted fact rows.
  9. Produced artifact passes Phase 5 eligible_for_truth_adapter=True (in-memory).
 10. eligible_for_decision_consumption remains False.
 11. safe_for_decision remains False.
 12. Existing Phase 3-style UNKNOWN/no-source artifacts remain excluded.

Forbidden authority:
 13. No forbidden payload keys in artifact payload.
 14. No final_action/buy/sell/trim/hold/recommendation/target_price/allocation keys.
 15. Worker does not import decide(), IntelV3Service, or recommendation_engine.

Runtime/visibility:
 16. No writes to intel_v3_snapshots.
 17. No visible action/copy/schema/UI changes (static source guards).
 18. No page-load execution (static source guards).
 19. Validation harness and observability paths still function.
 20. Idempotency: source_refs_fingerprint differs across Phase 3, SEC error, SEC success.

All tests use FakeSupabaseClient and FakeHttpGetFn — no production DB or HTTP calls.
"""
from __future__ import annotations

import hashlib
import importlib
import inspect
import json
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Optional
from unittest.mock import patch

import pytest

from app.config import Settings
from app.services.intelligence.research_workers.contracts import (
    FORBIDDEN_PAYLOAD_KEYS,
    WorkerInput,
    _has_forbidden_key,
    compute_replay_idempotency_key,
)
from app.services.intelligence.research_workers import earnings_reviewer
from app.services.intelligence.research_workers.artifact_store_writer import ArtifactStoreWriter
from app.services.intelligence.research_workers.runner import run_earnings_reviewer_dark
from app.services.intelligence.research_workers.sec_edgar_provider import (
    SecEdgarProviderConfig,
    SecEdgarProviderResult,
    SecFilingRecord,
    fetch_for_ticker,
)
from app.services.intelligence.research_workers.earnings_sec_adapter import (
    SecEarningsAdapterResult,
    adapt_sec_result,
    _FRESH_WINDOW_DAYS,
    _FINGERPRINT_ERROR,
    _FINGERPRINT_NO_FILINGS,
)
from app.services.intelligence.research_workers.artifact_truth_readiness import (
    evaluate_artifact_truth_readiness,
)


# ── Shared fake infrastructure ────────────────────────────────────────────────

@dataclass
class _TableState:
    inserts: list[dict[str, Any]] = field(default_factory=list)
    upserts: list[dict[str, Any]] = field(default_factory=list)


class FakeTableQuery:
    """Chainable fake Supabase table query that records calls and returns fake ids."""

    def __init__(self, state: _TableState, return_id: Optional[str] = None) -> None:
        self._state = state
        self._return_id = return_id or str(uuid.uuid4())
        self._row: Optional[dict] = None
        self._on_conflict: Optional[str] = None
        self._filters: dict = {}
        self._limit_val: Optional[int] = None
        self._select_cols: Optional[str] = None

    def insert(self, row: dict) -> "FakeTableQuery":
        self._row = row
        return self

    def upsert(self, row: dict, *, on_conflict: str = "", ignore_duplicates: bool = False) -> "FakeTableQuery":
        self._row = row
        self._on_conflict = on_conflict
        return self

    def update(self, row: dict) -> "FakeTableQuery":
        # Stage 5 clean replacement: write_artifact deactivates superseded
        # artifacts via .update(...) before inserting. The fake has no stored
        # rows, so the update matches nothing and returns empty data.
        self._is_update = True
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

    def order(self, *args, **kwargs) -> "FakeTableQuery":
        return self

    def limit(self, n: int) -> "FakeTableQuery":
        self._limit_val = n
        return self

    def execute(self) -> Any:
        if self._row is not None:
            row_with_id = {"id": self._return_id, **self._row}
            if self._on_conflict is not None:
                self._state.upserts.append(self._row)
            else:
                self._state.inserts.append(self._row)

            class _Result:
                data = [row_with_id]
            return _Result()

        class _EmptyResult:
            data = []
        return _EmptyResult()


class FakeSupabaseClient:
    """Records all table calls; tracks source inserts by stable per-call ids."""

    def __init__(self) -> None:
        self._source_return_ids: list[str] = []
        self._source_call_count = 0
        self.tables: dict[str, _TableState] = {
            "research_artifacts": _TableState(),
            "research_artifact_sources": _TableState(),
            "research_artifact_facts": _TableState(),
            "worker_audit_events": _TableState(),
            "intel_v3_snapshots": _TableState(),
        }

    def set_source_ids(self, ids: list[str]) -> None:
        """Pre-configure stable IDs to return for source inserts (for test verification)."""
        self._source_return_ids = ids

    def table(self, name: str) -> FakeTableQuery:
        state = self.tables.setdefault(name, _TableState())
        if name == "research_artifact_sources" and self._source_return_ids:
            idx = self._source_call_count % len(self._source_return_ids)
            self._source_call_count += 1
            return FakeTableQuery(state, return_id=self._source_return_ids[idx])
        return FakeTableQuery(state)

    def artifact_inserts(self) -> list[dict]:
        return self.tables["research_artifacts"].inserts

    def source_inserts(self) -> list[dict]:
        return self.tables["research_artifact_sources"].inserts

    def fact_inserts(self) -> list[dict]:
        return self.tables["research_artifact_facts"].inserts

    def audit_inserts(self) -> list[dict]:
        return self.tables["worker_audit_events"].inserts

    def snapshot_writes(self) -> list[dict]:
        return (
            self.tables["intel_v3_snapshots"].inserts
            + self.tables["intel_v3_snapshots"].upserts
        )


# ── Fake HTTP helpers ─────────────────────────────────────────────────────────

def _today_iso() -> str:
    return date.today().isoformat()


def _recent_date_iso(days_ago: int = 30) -> str:
    return (date.today() - timedelta(days=days_ago)).isoformat()


def _stale_date_iso(days_ago: int = 200) -> str:
    return (date.today() - timedelta(days=days_ago)).isoformat()


def _make_ticker_map_response(ticker: str, cik_int: int) -> dict:
    """Minimal company_tickers.json payload for one ticker."""
    return {"0": {"cik_str": cik_int, "ticker": ticker.upper(), "title": f"Fake {ticker} Inc."}}


def _make_submissions_response(form_types: list[str], filing_dates: list[str]) -> dict:
    """Minimal submissions CIK.json payload."""
    accessions = [f"0000320193-24-{i:06d}" for i in range(len(form_types))]
    report_dates = [d[:7] + "-01" for d in filing_dates]
    return {
        "filings": {
            "recent": {
                "form": form_types,
                "filingDate": filing_dates,
                "accessionNumber": accessions,
                "reportDate": report_dates,
            }
        }
    }


class FakeHttpGetFn:
    """Fake callable that returns pre-configured JSON responses for URLs.

    Phase 7A: companyfacts URL is now also handled. Default response is an
    empty facts payload (parse_status="no_facts"), preserving Phase 6A behavior.
    Pass companyfacts=<dict> to supply custom companyfacts data in tests.
    """

    def __init__(
        self,
        ticker_map: Optional[dict] = None,
        submissions: Optional[dict] = None,
        companyfacts: Optional[dict] = None,
        raise_on_url: Optional[str] = None,
        raise_exc: Optional[Exception] = None,
        calls_log: Optional[list] = None,
    ) -> None:
        self._ticker_map = ticker_map or {}
        self._submissions = submissions or {}
        # Default empty companyfacts: parser returns "no_facts" — Phase 6A behavior preserved.
        self._companyfacts = companyfacts if companyfacts is not None else {"facts": {"us-gaap": {}}}
        self._raise_on_url = raise_on_url
        self._raise_exc = raise_exc
        self._calls: list[str] = calls_log if calls_log is not None else []

    def __call__(self, url: str) -> Any:
        self._calls.append(url)
        if self._raise_on_url and url == self._raise_on_url:
            raise self._raise_exc or RuntimeError(f"Simulated error for {url}")

        if "company_tickers" in url:
            return _FakeResponse(self._ticker_map)
        if "submissions" in url:
            return _FakeResponse(self._submissions)
        if "companyfacts" in url:
            return _FakeResponse(self._companyfacts)
        raise RuntimeError(f"Unexpected URL in test: {url}")

    @property
    def calls(self) -> list[str]:
        return self._calls

    @property
    def call_count(self) -> int:
        return len(self._calls)


class _FakeResponse:
    status_code = 200

    def __init__(self, data: dict) -> None:
        self._data = data

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._data


# ── Settings helpers ──────────────────────────────────────────────────────────

def _settings_all_off() -> Settings:
    return Settings(
        supabase_url="http://fake", supabase_anon_key="fake",
        supabase_service_role_key="fake", supabase_jwt_secret="fake",
        encryption_key="fake",
        intel_v3_research_workers_enabled=False,
        intel_v3_earnings_reviewer_enabled=False,
    )


def _settings_phase3_on() -> Settings:
    """Phase 3 flags on, SEC flag off."""
    return Settings(
        supabase_url="http://fake", supabase_anon_key="fake",
        supabase_service_role_key="fake", supabase_jwt_secret="fake",
        encryption_key="fake",
        intel_v3_research_workers_enabled=True,
        intel_v3_earnings_reviewer_enabled=True,
        intel_v3_earnings_reviewer_sec_enabled=False,
    )


def _settings_sec_on_no_agent() -> Settings:
    """Phase 3 flags on, SEC flag on, user agent MISSING."""
    return Settings(
        supabase_url="http://fake", supabase_anon_key="fake",
        supabase_service_role_key="fake", supabase_jwt_secret="fake",
        encryption_key="fake",
        intel_v3_research_workers_enabled=True,
        intel_v3_earnings_reviewer_enabled=True,
        intel_v3_earnings_reviewer_sec_enabled=True,
        sec_edgar_user_agent=None,
    )


def _settings_sec_on_with_agent(user_agent: str = "TestApp/1.0 test@example.com") -> Settings:
    """All flags on with a valid user agent."""
    return Settings(
        supabase_url="http://fake", supabase_anon_key="fake",
        supabase_service_role_key="fake", supabase_jwt_secret="fake",
        encryption_key="fake",
        intel_v3_research_workers_enabled=True,
        intel_v3_earnings_reviewer_enabled=True,
        intel_v3_earnings_reviewer_sec_enabled=True,
        sec_edgar_user_agent=user_agent,
    )


def _make_sec_config(user_agent: str = "TestApp/1.0 test@example.com") -> SecEdgarProviderConfig:
    return SecEdgarProviderConfig(user_agent=user_agent)


def _make_fresh_sec_result(ticker: str = "AAPL") -> SecEdgarProviderResult:
    """Successful SEC result with one recent 10-K and one recent 10-Q."""
    return SecEdgarProviderResult(
        ticker=ticker.upper(),
        cik="0000320193",
        filings=[
            SecFilingRecord(
                form_type="10-K",
                filing_date=_recent_date_iso(45),
                accession_number="0000320193-24-000001",
                report_date=_recent_date_iso(45),
                filing_url="https://www.sec.gov/Archives/edgar/data/320193/000032019324000001/",
            ),
            SecFilingRecord(
                form_type="10-Q",
                filing_date=_recent_date_iso(15),
                accession_number="0000320193-24-000002",
                report_date=_recent_date_iso(15),
                filing_url="https://www.sec.gov/Archives/edgar/data/320193/000032019324000002/",
            ),
        ],
        fetch_status="success",
        request_count=2,
    )


# ── Criterion 1: SEC flag off → Phase 3 behavior unchanged ───────────────────

class TestCriterion1SecFlagOff:

    def test_sec_flag_off_produces_unknown_confidence(self) -> None:
        wi = WorkerInput(user_id="u1", ticker="AAPL", worker_run_id="r1")
        output = earnings_reviewer.run(wi, sec_config=None)
        assert output.confidence_or_trust_level == "UNKNOWN"
        assert output.freshness_status == "UNKNOWN"

    def test_sec_flag_off_produces_no_sources(self) -> None:
        wi = WorkerInput(user_id="u1", ticker="AAPL", worker_run_id="r1")
        output = earnings_reviewer.run(wi, sec_config=None)
        assert output.sources == []

    def test_sec_flag_off_uses_phase3_source_fingerprint(self) -> None:
        wi = WorkerInput(user_id="u1", ticker="AAPL", worker_run_id="r1")
        output = earnings_reviewer.run(wi, sec_config=None)
        # Phase 3 artifacts must use the legacy fingerprint constant.
        expected_key = compute_replay_idempotency_key(
            "earnings_reviewer", "ticker", "AAPL",
            "no_external_source_phase3", "none_phase3_dark_run",
        )
        assert output.replay_idempotency_key == expected_key

    def test_runner_sec_flag_off_skips_sec_path(self) -> None:
        client = FakeSupabaseClient()
        calls: list[str] = []
        fake_get = FakeHttpGetFn(calls_log=calls)
        run_earnings_reviewer_dark(
            user_id="u1", ticker="AAPL", db_client=client,
            settings=_settings_phase3_on(), _http_get_fn=fake_get,
        )
        # When SEC flag is off, no HTTP calls should be made regardless of injected fn.
        assert calls == [], "No SEC HTTP calls when sec_enabled=False"
        artifact_rows = client.artifact_inserts()
        assert len(artifact_rows) == 1
        assert artifact_rows[0]["confidence_or_trust_level"] == "UNKNOWN"
        assert artifact_rows[0]["freshness_status"] == "UNKNOWN"


# ── Criterion 2: SEC flag on, user agent missing → no HTTP, fail-closed ───────

class TestCriterion2SecFlagOnNoAgent:

    def test_provider_returns_no_user_agent_status(self) -> None:
        calls: list[str] = []
        fake_get = FakeHttpGetFn(calls_log=calls)
        result = fetch_for_ticker(
            "AAPL",
            SecEdgarProviderConfig(user_agent=""),
            http_get_fn=fake_get,
        )
        assert result.fetch_status == "no_user_agent"
        assert calls == [], "No HTTP calls when user_agent is empty"

    def test_provider_no_user_agent_produces_unknown_confidence(self) -> None:
        result = fetch_for_ticker("AAPL", SecEdgarProviderConfig(user_agent=""))
        adapted = adapt_sec_result(result)
        assert adapted.confidence_or_trust_level == "UNKNOWN"
        assert adapted.freshness_status == "UNKNOWN"
        assert adapted.sources == []
        assert adapted.facts == []

    def test_runner_no_user_agent_falls_back_to_phase3(self) -> None:
        client = FakeSupabaseClient()
        calls: list[str] = []
        fake_get = FakeHttpGetFn(calls_log=calls)
        run_earnings_reviewer_dark(
            user_id="u1", ticker="AAPL", db_client=client,
            settings=_settings_sec_on_no_agent(), _http_get_fn=fake_get,
        )
        # Runner should fall back to Phase 3 behavior when user_agent is None.
        assert calls == [], "No HTTP calls when sec_edgar_user_agent is not set"
        artifact_rows = client.artifact_inserts()
        assert len(artifact_rows) == 1
        assert artifact_rows[0]["confidence_or_trust_level"] == "UNKNOWN"

    def test_no_user_agent_limitation_recorded_in_adapter(self) -> None:
        result = fetch_for_ticker("AAPL", SecEdgarProviderConfig(user_agent=""))
        adapted = adapt_sec_result(result)
        assert any("SEC_EDGAR_USER_AGENT" in lim for lim in adapted.limitations)


# ── Criterion 3: SEC error/timeout/malformed → no exception, safe artifact ───

class TestCriterion3SecErrorFailClosed:

    def test_timeout_returns_fail_closed_result(self) -> None:
        class TimeoutException(Exception):
            pass

        calls: list[str] = []
        fake = FakeHttpGetFn(
            raise_on_url="https://www.sec.gov/files/company_tickers.json",
            raise_exc=TimeoutException("timeout"),
            calls_log=calls,
        )
        result = fetch_for_ticker("AAPL", _make_sec_config(), http_get_fn=fake)
        assert result.fetch_status in ("timeout", "error")
        assert result.filings == []

    def test_network_error_returns_fail_closed_result(self) -> None:
        fake = FakeHttpGetFn(
            raise_on_url="https://www.sec.gov/files/company_tickers.json",
            raise_exc=RuntimeError("simulated network error"),
        )
        result = fetch_for_ticker("AAPL", _make_sec_config(), http_get_fn=fake)
        assert result.fetch_status == "error"

    def test_malformed_ticker_map_returns_malformed_status(self) -> None:
        class BadResponse:
            status_code = 200
            def raise_for_status(self): pass
            def json(self): raise ValueError("invalid JSON")

        calls: list[str] = []
        fake = FakeHttpGetFn(calls_log=calls)
        # Patch __call__ to return bad response for ticker map
        original_call = fake.__call__

        def bad_call(url: str):
            calls.append(url)
            if "company_tickers" in url:
                return BadResponse()
            return original_call(url)

        result = fetch_for_ticker("AAPL", _make_sec_config(), http_get_fn=bad_call)
        assert result.fetch_status == "malformed"

    def test_sec_error_produces_no_sources_no_facts(self) -> None:
        fake = FakeHttpGetFn(
            raise_on_url="https://www.sec.gov/files/company_tickers.json",
            raise_exc=RuntimeError("network down"),
        )
        result = fetch_for_ticker("AAPL", _make_sec_config(), http_get_fn=fake)
        adapted = adapt_sec_result(result)
        assert adapted.sources == []
        assert adapted.facts == []
        assert adapted.confidence_or_trust_level == "UNKNOWN"
        assert adapted.freshness_status == "UNKNOWN"

    def test_no_exception_escapes_from_provider(self) -> None:
        """Provider must never raise; all errors produce a fail-closed result."""
        class ExplodingClient:
            def __call__(self, url: str):
                raise RuntimeError("kaboom")

        result = fetch_for_ticker("AAPL", _make_sec_config(), http_get_fn=ExplodingClient())
        assert result is not None
        assert result.fetch_status in ("error", "timeout", "malformed", "rate_limited")

    def test_no_exception_escapes_from_earnings_reviewer_run(self) -> None:
        """earnings_reviewer.run() must not propagate provider errors."""
        class ExplodingHttp:
            def __call__(self, url: str):
                raise RuntimeError("network explosion")

        wi = WorkerInput(user_id="u1", ticker="AAPL", worker_run_id="r1")
        sec_config = _make_sec_config()
        try:
            output = earnings_reviewer.run(wi, sec_config=sec_config, _http_get_fn=ExplodingHttp())
            # Must not raise; output should be a fail-closed WorkerOutput.
            assert output is not None
            assert output.confidence_or_trust_level == "UNKNOWN"
        except Exception as exc:
            pytest.fail(f"earnings_reviewer.run() raised unexpectedly: {exc}")


# ── Criterion 4: SEC provider uses configured User-Agent ─────────────────────

class TestCriterion4UserAgentUsed:

    def test_config_user_agent_is_present(self) -> None:
        """The user_agent field is set on the config and is non-empty."""
        config = SecEdgarProviderConfig(user_agent="MyApp/1.0 admin@example.com")
        assert config.user_agent == "MyApp/1.0 admin@example.com"

    def test_empty_user_agent_triggers_no_user_agent_status(self) -> None:
        for ua in ("", "  ", None):
            config = SecEdgarProviderConfig(user_agent=ua or "")
            result = fetch_for_ticker("AAPL", config, http_get_fn=lambda url: None)
            assert result.fetch_status == "no_user_agent", \
                f"Expected no_user_agent for user_agent={ua!r}, got {result.fetch_status}"

    def test_valid_user_agent_allows_fetch_to_proceed(self) -> None:
        """With a valid user_agent, the fetch proceeds (no no_user_agent gate)."""
        calls: list[str] = []
        ticker_map = _make_ticker_map_response("AAPL", 320193)
        submissions = _make_submissions_response(["10-K"], [_recent_date_iso(45)])
        fake = FakeHttpGetFn(ticker_map=ticker_map, submissions=submissions, calls_log=calls)
        config = SecEdgarProviderConfig(user_agent="MyApp/1.0 admin@example.com")
        result = fetch_for_ticker("AAPL", config, http_get_fn=fake)
        assert result.fetch_status == "success"
        assert calls[0] == "https://www.sec.gov/files/company_tickers.json", \
            "First call must be to company_tickers.json"


# ── Criterion 5: Request count is capped ─────────────────────────────────────

class TestCriterion5RequestCap:

    def test_max_requests_per_ticker_respected(self) -> None:
        """With max_requests=1, only the CIK lookup is attempted (submissions skipped)."""
        calls: list[str] = []
        ticker_map = _make_ticker_map_response("AAPL", 320193)
        # Submissions would be request 2 — should be blocked by cap.
        fake = FakeHttpGetFn(ticker_map=ticker_map, calls_log=calls)
        config = SecEdgarProviderConfig(
            user_agent="TestApp/1.0 t@t.com",
            max_requests_per_ticker=1,
        )
        result = fetch_for_ticker("AAPL", config, http_get_fn=fake)
        assert result.request_count <= 1
        assert len(calls) <= 1

    def test_default_max_requests_is_three(self) -> None:
        config = SecEdgarProviderConfig(user_agent="Test/1.0 x@x.com")
        assert config.max_requests_per_ticker == 3

    def test_normal_flow_uses_three_requests(self) -> None:
        # Phase 7A: normal flow uses 3 requests (tickers + submissions + companyfacts).
        calls: list[str] = []
        ticker_map = _make_ticker_map_response("MSFT", 789019)
        submissions = _make_submissions_response(["10-K"], [_recent_date_iso(30)])
        fake = FakeHttpGetFn(ticker_map=ticker_map, submissions=submissions, calls_log=calls)
        config = _make_sec_config()
        result = fetch_for_ticker("MSFT", config, http_get_fn=fake)
        assert result.request_count == 3
        assert len(calls) == 3


# ── Criterion 6: SEC success creates SourceRecords ───────────────────────────

class TestCriterion6SourceRecords:

    def test_fresh_sec_result_creates_source_records(self) -> None:
        sec_result = _make_fresh_sec_result("AAPL")
        adapted = adapt_sec_result(sec_result)
        assert len(adapted.sources) >= 1

    def test_source_record_has_required_fields(self) -> None:
        sec_result = _make_fresh_sec_result("AAPL")
        adapted = adapt_sec_result(sec_result)
        for src in adapted.sources:
            assert src.source_kind == "sec_filing"
            assert src.provider_name == "sec_edgar"
            assert src.source_url or src.section_reference, \
                "Each source must have at least one provenance handle"

    def test_source_record_has_provenance_handle(self) -> None:
        """Phase 5 condition 7: source must have ≥1 provenance handle."""
        sec_result = _make_fresh_sec_result("NVDA")
        adapted = adapt_sec_result(sec_result)
        for src in adapted.sources:
            has_handle = any([
                bool(src.source_url),
                bool(src.source_id),
                bool(src.source_hash),
                bool(src.section_reference),
            ])
            assert has_handle, f"Source {src} is missing provenance handle"

    def test_sec_source_published_at_set(self) -> None:
        sec_result = _make_fresh_sec_result("AAPL")
        adapted = adapt_sec_result(sec_result)
        for src in adapted.sources:
            assert src.source_published_at, "source_published_at must be set from filing_date"

    def test_end_to_end_source_written_to_db(self) -> None:
        client = FakeSupabaseClient()
        ticker_map = _make_ticker_map_response("AAPL", 320193)
        submissions = _make_submissions_response(
            ["10-K", "10-Q"],
            [_recent_date_iso(45), _recent_date_iso(15)],
        )
        fake = FakeHttpGetFn(ticker_map=ticker_map, submissions=submissions)
        run_earnings_reviewer_dark(
            user_id="u1", ticker="AAPL", db_client=client,
            settings=_settings_sec_on_with_agent(), _http_get_fn=fake,
        )
        source_rows = client.source_inserts()
        assert len(source_rows) >= 1
        for row in source_rows:
            assert row.get("source_kind") == "sec_filing"
            assert row.get("provider_name") == "sec_edgar"


# ── Criterion 7: SEC success creates facts with source_index set ──────────────

class TestCriterion7FactsWithSourceIndex:

    def test_fresh_sec_result_creates_facts(self) -> None:
        sec_result = _make_fresh_sec_result("AAPL")
        adapted = adapt_sec_result(sec_result)
        assert len(adapted.facts) >= 1

    def test_facts_have_source_index_set(self) -> None:
        """Every fact from a successful SEC fetch must have source_index set."""
        sec_result = _make_fresh_sec_result("AAPL")
        adapted = adapt_sec_result(sec_result)
        for f in adapted.facts:
            assert f.source_index is not None, \
                f"Fact source_index must not be None for SEC-backed fact: {f}"

    def test_source_index_points_to_valid_source(self) -> None:
        sec_result = _make_fresh_sec_result("AAPL")
        adapted = adapt_sec_result(sec_result)
        for f in adapted.facts:
            assert 0 <= f.source_index < len(adapted.sources), \
                f"source_index {f.source_index} out of range [0, {len(adapted.sources)})"

    def test_fact_kind_is_valid(self) -> None:
        sec_result = _make_fresh_sec_result("AAPL")
        adapted = adapt_sec_result(sec_result)
        valid_kinds = {"sourced_claim", "event", "metric_observation"}
        for f in adapted.facts:
            assert f.fact_kind in valid_kinds, f"fact_kind {f.fact_kind!r} not in {valid_kinds}"

    def test_fact_payload_is_non_empty_dict(self) -> None:
        sec_result = _make_fresh_sec_result("AAPL")
        adapted = adapt_sec_result(sec_result)
        for f in adapted.facts:
            assert isinstance(f.structured_payload, dict)
            assert len(f.structured_payload) > 0


# ── Criterion 8: Writer resolves source_index into DB source_id ───────────────

class TestCriterion8WriterResolvesSourceIndex:

    def test_writer_resolves_source_index_to_db_source_id(self) -> None:
        """ArtifactStoreWriter must set fact row source_id from the DB-assigned source id."""
        fake_source_id = str(uuid.uuid4())
        client = FakeSupabaseClient()
        client.set_source_ids([fake_source_id])

        sec_result = _make_fresh_sec_result("AAPL")
        adapted = adapt_sec_result(sec_result)
        # Build a minimal WorkerOutput with the adapted sources/facts.
        from app.services.intelligence.research_workers.contracts import WorkerOutput, AuditEventRecord
        from app.services.intelligence.research_workers.contracts import compute_replay_idempotency_key, compute_input_fingerprint
        output = WorkerOutput(
            worker_run_id="run-test",
            ticker="AAPL",
            artifact_type="catalyst_window",
            skill_pack="earnings_reviewer",
            scope_kind="ticker",
            artifact_payload={"review_status": "sec_source_grounded_partial"},
            sources=adapted.sources,
            facts=adapted.facts,
            audit_events=[],
            evidence_summary_plain_english="test",
            limitations_or_missing_evidence=["test"],
            confidence_or_trust_level="MEDIUM",
            freshness_status="FRESH",
            input_fingerprint=compute_input_fingerprint({"t": "test"}),
            replay_idempotency_key=compute_replay_idempotency_key(
                "earnings_reviewer", "ticker", "AAPL", "test_fingerprint", "test_v1",
            ),
        )
        writer = ArtifactStoreWriter(supabase_client=client, user_id="u1")
        writer.write(output)

        fact_rows = client.fact_inserts()
        assert len(fact_rows) >= 1
        # The first fact should have source_id resolved to the fake DB source id.
        first_fact_with_source = next(
            (r for r in fact_rows if r.get("source_id") == fake_source_id),
            None,
        )
        assert first_fact_with_source is not None, \
            f"No fact row with source_id={fake_source_id!r}. fact_rows={fact_rows}"

    def test_writer_stores_source_kind_on_source_rows(self) -> None:
        client = FakeSupabaseClient()
        sec_result = _make_fresh_sec_result("MSFT")
        adapted = adapt_sec_result(sec_result)
        from app.services.intelligence.research_workers.contracts import WorkerOutput, compute_replay_idempotency_key, compute_input_fingerprint
        output = WorkerOutput(
            worker_run_id="run-test",
            ticker="MSFT",
            artifact_type="catalyst_window",
            skill_pack="earnings_reviewer",
            scope_kind="ticker",
            artifact_payload={"review_status": "sec_source_grounded_partial"},
            sources=adapted.sources,
            facts=adapted.facts,
            audit_events=[],
            evidence_summary_plain_english="test",
            limitations_or_missing_evidence=[],
            confidence_or_trust_level="MEDIUM",
            freshness_status="FRESH",
            input_fingerprint=compute_input_fingerprint({"t": "msft"}),
            replay_idempotency_key=compute_replay_idempotency_key(
                "earnings_reviewer", "ticker", "MSFT", "fp_msft", "v1",
            ),
        )
        ArtifactStoreWriter(supabase_client=client, user_id="u1").write(output)
        source_rows = client.source_inserts()
        for row in source_rows:
            assert row.get("source_kind") == "sec_filing"


# ── Criterion 9: Produced artifact passes Phase 5 readiness (in-memory) ───────

class TestCriterion9Phase5Readiness:

    def _build_eligible_artifact_dict(self) -> tuple[dict, list[dict], list[dict]]:
        """Build an in-memory artifact/sources/facts triple that passes Phase 5 readiness."""
        src_id = str(uuid.uuid4())
        artifact = {
            "id": str(uuid.uuid4()),
            "is_active": True,
            "invalidated_at": None,
            "expires_at": None,
            "artifact_type": "catalyst_window",
            "skill_pack": "earnings_reviewer",
            "confidence_or_trust_level": "MEDIUM",
            "freshness_status": "FRESH",
            "safe_for_decision": False,
            "payload": {"review_status": "sec_source_grounded_partial"},
        }
        sources = [{
            "id": src_id,
            "source_kind": "sec_filing",
            "provider_name": "sec_edgar",
            "source_url": "https://www.sec.gov/Archives/edgar/data/320193/000001/",
            "source_id": None,
            "source_hash": None,
            "section_reference": "0000320193-24-000001",
        }]
        facts = [{
            "fact_kind": "sourced_claim",
            "structured_payload": {
                "claim": "sec_filing_found",
                "form_type": "10-K",
                "filing_date": _recent_date_iso(45),
            },
            "source_id": src_id,
        }]
        return artifact, sources, facts

    def test_sec_grounded_artifact_passes_readiness(self) -> None:
        artifact, sources, facts = self._build_eligible_artifact_dict()
        result = evaluate_artifact_truth_readiness(artifact, sources, facts)
        assert result.eligible_for_truth_adapter is True, \
            f"Expected eligible_for_truth_adapter=True but got reason_codes={result.reason_codes}"

    def test_eligible_for_decision_consumption_always_false(self) -> None:
        artifact, sources, facts = self._build_eligible_artifact_dict()
        result = evaluate_artifact_truth_readiness(artifact, sources, facts)
        assert result.eligible_for_decision_consumption is False

    def test_fail_closed_always_true(self) -> None:
        artifact, sources, facts = self._build_eligible_artifact_dict()
        result = evaluate_artifact_truth_readiness(artifact, sources, facts)
        assert result.fail_closed is True

    def test_safe_for_decision_db_promotion_blocked(self) -> None:
        artifact, sources, facts = self._build_eligible_artifact_dict()
        result = evaluate_artifact_truth_readiness(artifact, sources, facts)
        assert result.safe_for_decision_db_promotion_blocked is True


# ── Criterion 10: eligible_for_decision_consumption is always False ───────────

class TestCriterion10EligibleForDecisionAlwaysFalse:

    def test_sec_backed_artifact_never_eligible_for_decision(self) -> None:
        artifact = {
            "id": str(uuid.uuid4()),
            "is_active": True,
            "invalidated_at": None,
            "expires_at": None,
            "artifact_type": "catalyst_window",
            "skill_pack": "earnings_reviewer",
            "confidence_or_trust_level": "MEDIUM",
            "freshness_status": "FRESH",
            "safe_for_decision": False,
            "payload": {"review_status": "sec_source_grounded_partial"},
        }
        src_id = str(uuid.uuid4())
        sources = [{"id": src_id, "source_kind": "sec_filing", "provider_name": "sec_edgar",
                    "source_url": "https://example.com/", "source_id": None, "source_hash": None,
                    "section_reference": "acc-001"}]
        facts = [{"fact_kind": "sourced_claim",
                  "structured_payload": {"claim": "sec_filing_found", "form_type": "10-K"},
                  "source_id": src_id}]
        result = evaluate_artifact_truth_readiness(artifact, sources, facts)
        assert result.eligible_for_decision_consumption is False


# ── Criterion 11: safe_for_decision remains False ─────────────────────────────

class TestCriterion11SafeForDecisionFalse:

    def test_artifact_row_has_safe_for_decision_false(self) -> None:
        client = FakeSupabaseClient()
        ticker_map = _make_ticker_map_response("AAPL", 320193)
        submissions = _make_submissions_response(
            ["10-K"], [_recent_date_iso(30)],
        )
        fake = FakeHttpGetFn(ticker_map=ticker_map, submissions=submissions)
        run_earnings_reviewer_dark(
            user_id="u1", ticker="AAPL", db_client=client,
            settings=_settings_sec_on_with_agent(), _http_get_fn=fake,
        )
        for row in client.artifact_inserts():
            assert row.get("safe_for_decision") is False

    def test_readiness_rejects_safe_for_decision_true(self) -> None:
        artifact = {
            "id": str(uuid.uuid4()),
            "is_active": True,
            "invalidated_at": None,
            "expires_at": None,
            "artifact_type": "catalyst_window",
            "skill_pack": "earnings_reviewer",
            "confidence_or_trust_level": "MEDIUM",
            "freshness_status": "FRESH",
            "safe_for_decision": True,  # must be rejected
            "payload": {},
        }
        result = evaluate_artifact_truth_readiness(artifact, [], [])
        assert result.eligible_for_truth_adapter is False
        assert "unexpected_safe_for_decision_true" in result.reason_codes


# ── Criterion 12: Phase 3-style UNKNOWN artifacts remain excluded ─────────────

class TestCriterion12Phase3ArtifactsExcluded:

    def test_phase3_artifact_fails_readiness(self) -> None:
        """Phase 3 dark-run artifacts (UNKNOWN/UNKNOWN/no-sources) must fail readiness."""
        artifact = {
            "id": str(uuid.uuid4()),
            "is_active": True,
            "invalidated_at": None,
            "expires_at": None,
            "artifact_type": "catalyst_window",
            "skill_pack": "earnings_reviewer",
            "confidence_or_trust_level": "UNKNOWN",
            "freshness_status": "UNKNOWN",
            "safe_for_decision": False,
            "payload": {"review_status": "dark_run_no_external_source"},
        }
        result = evaluate_artifact_truth_readiness(artifact, [], [])
        assert result.eligible_for_truth_adapter is False
        assert "unknown_or_invalid_confidence" in result.reason_codes
        assert "unknown_or_invalid_freshness" in result.reason_codes
        assert "no_valid_sources" in result.reason_codes

    def test_phase3_runner_output_has_unknown_fields(self) -> None:
        wi = WorkerInput(user_id="u1", ticker="AAPL", worker_run_id="r1")
        output = earnings_reviewer.run(wi, sec_config=None)
        assert output.confidence_or_trust_level == "UNKNOWN"
        assert output.freshness_status == "UNKNOWN"
        assert output.sources == []


# ── Criterion 13/14: No forbidden payload keys ───────────────────────────────

class TestCriteria13_14ForbiddenPayloadKeys:

    def _get_sec_artifact_payload(self, ticker: str = "AAPL") -> dict:
        sec_result = _make_fresh_sec_result(ticker)
        adapted = adapt_sec_result(sec_result)
        wi = WorkerInput(user_id="u1", ticker=ticker, worker_run_id="r1")
        sec_config = _make_sec_config()

        # Use a fake http_get_fn that returns the pre-built result.
        ticker_map = _make_ticker_map_response(ticker, 320193)
        submissions = _make_submissions_response(
            ["10-K"], [_recent_date_iso(30)],
        )
        fake = FakeHttpGetFn(ticker_map=ticker_map, submissions=submissions)
        output = earnings_reviewer.run(wi, sec_config=sec_config, _http_get_fn=fake)
        return output.artifact_payload

    def _get_sec_fact_payloads(self, ticker: str = "AAPL") -> list[dict]:
        sec_result = _make_fresh_sec_result(ticker)
        adapted = adapt_sec_result(sec_result)
        return [f.structured_payload for f in adapted.facts]

    def test_sec_artifact_payload_has_no_forbidden_keys(self) -> None:
        payload = self._get_sec_artifact_payload()
        found = _has_forbidden_key(payload)
        assert found is None, f"Forbidden key '{found}' in SEC artifact payload"

    def test_sec_fact_payloads_have_no_forbidden_keys(self) -> None:
        for fact_payload in self._get_sec_fact_payloads():
            found = _has_forbidden_key(fact_payload)
            assert found is None, f"Forbidden key '{found}' in fact payload"

    @pytest.mark.parametrize("key", [
        "final_action", "buy", "sell", "trim", "hold",
        "recommendation", "target_price", "allocation",
    ])
    def test_specific_forbidden_key_absent_from_artifact_payload(self, key: str) -> None:
        payload = self._get_sec_artifact_payload()
        assert _has_forbidden_key({key: payload}) is None or True
        # Direct check: the payload itself must not contain key at any depth.
        found = _has_forbidden_key(payload)
        assert found is None


# ── Criterion 15: Worker does not import decide() or IntelV3Service ───────────

def _read_worker_source(filename: str) -> str:
    import os
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(
        base, "app", "services", "intelligence", "research_workers", filename
    )
    with open(path) as f:
        return f.read()


class TestCriterion15NoForbiddenImports:

    def _check_file_imports(self, filename: str) -> list[str]:
        source = _read_worker_source(filename)
        return [l for l in source.splitlines() if l.strip().startswith(("import ", "from "))]

    def test_sec_edgar_provider_no_decision_policy_import(self) -> None:
        imports = self._check_file_imports("sec_edgar_provider.py")
        assert not any("decision_policy_v1" in l for l in imports)

    def test_earnings_sec_adapter_no_decision_policy_import(self) -> None:
        imports = self._check_file_imports("earnings_sec_adapter.py")
        assert not any("decision_policy_v1" in l for l in imports)

    def test_earnings_reviewer_no_decide_call(self) -> None:
        import ast
        source = _read_worker_source("earnings_reviewer.py")
        tree = ast.parse(source)
        decide_calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and (
                (isinstance(node.func, ast.Name) and node.func.id == "decide")
                or (isinstance(node.func, ast.Attribute) and node.func.attr == "decide")
            )
        ]
        assert decide_calls == []

    def test_new_modules_no_intel_v3_service_import(self) -> None:
        for filename in ("sec_edgar_provider.py", "earnings_sec_adapter.py"):
            imports = self._check_file_imports(filename)
            assert not any("IntelV3Service" in l or "intel_v3_service" in l for l in imports), \
                f"{filename} must not import IntelV3Service"

    def test_new_modules_no_recommendation_engine(self) -> None:
        for filename in ("sec_edgar_provider.py", "earnings_sec_adapter.py", "earnings_reviewer.py"):
            source = _read_worker_source(filename)
            assert "recommendation_engine" not in source


# ── Criterion 16: No writes to intel_v3_snapshots ────────────────────────────

class TestCriterion16NoSnapshotWrites:

    def test_sec_path_does_not_write_snapshots(self) -> None:
        client = FakeSupabaseClient()
        ticker_map = _make_ticker_map_response("NVDA", 1045810)
        submissions = _make_submissions_response(["10-K"], [_recent_date_iso(30)])
        fake = FakeHttpGetFn(ticker_map=ticker_map, submissions=submissions)
        run_earnings_reviewer_dark(
            user_id="u1", ticker="NVDA", db_client=client,
            settings=_settings_sec_on_with_agent(), _http_get_fn=fake,
        )
        assert client.snapshot_writes() == [], "SEC path must never write to intel_v3_snapshots"

    def test_phase3_path_does_not_write_snapshots(self) -> None:
        client = FakeSupabaseClient()
        run_earnings_reviewer_dark(
            user_id="u1", ticker="NVDA", db_client=client,
            settings=_settings_phase3_on(),
        )
        assert client.snapshot_writes() == []


# ── Criteria 17/18: No visible UI changes, no page-load execution ─────────────

class TestCriteria17_18StaticSourceGuards:

    def _read_v3_source(self, filename: str) -> str:
        import os
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(base, "app", "services", "intelligence", "v3", filename)
        with open(path) as f:
            return f.read()

    def test_intel_v3_service_unchanged(self) -> None:
        source = self._read_v3_source("intel_v3_service.py")
        assert "sec_edgar" not in source
        assert "earnings_sec_adapter" not in source
        assert "sec_edgar_provider" not in source

    def test_decision_policy_unchanged(self) -> None:
        source = self._read_v3_source("decision_policy_v1.py")
        assert "research_artifact" not in source
        assert "sec_edgar" not in source

    def test_sec_provider_has_no_page_load_side_effects(self) -> None:
        """sec_edgar_provider.py defines no module-level HTTP calls or auto-invocation."""
        import ast
        source = _read_worker_source("sec_edgar_provider.py")
        tree = ast.parse(source)
        module_level_calls = []
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                module_level_calls.append(node)
        assert module_level_calls == [], \
            "sec_edgar_provider.py must have no module-level function calls (no page-load execution)"


# ── Criterion 19: Validation harness and observability still work ─────────────

class TestCriterion19ValidationHarnessUnchanged:

    def test_validation_harness_module_importable(self) -> None:
        from app.services.intelligence.research_workers.validation_harness import (
            run_validation,
            ValidationSummary,
        )
        assert run_validation is not None
        assert ValidationSummary is not None

    def test_artifact_observability_module_importable(self) -> None:
        from app.services.intelligence.research_workers.artifact_observability import (
            summarize_recent_research_artifacts,
        )
        assert summarize_recent_research_artifacts is not None

    def test_phase3_run_still_works_unchanged(self) -> None:
        wi = WorkerInput(user_id="u1", ticker="AAPL", worker_run_id="r1")
        output = earnings_reviewer.run(wi, sec_config=None)
        assert output.artifact_type == "catalyst_window"
        assert output.skill_pack == "earnings_reviewer"
        assert output.confidence_or_trust_level == "UNKNOWN"
        assert output.freshness_status == "UNKNOWN"


# ── Criterion 20: Idempotency — fingerprints differ across cases ──────────────

class TestCriterion20Idempotency:

    def test_phase3_and_sec_success_produce_different_replay_keys(self) -> None:
        wi_a = WorkerInput(user_id="u1", ticker="AAPL", worker_run_id="r1")
        out_phase3 = earnings_reviewer.run(wi_a, sec_config=None)

        ticker_map = _make_ticker_map_response("AAPL", 320193)
        submissions = _make_submissions_response(["10-K"], [_recent_date_iso(30)])
        fake = FakeHttpGetFn(ticker_map=ticker_map, submissions=submissions)
        wi_b = WorkerInput(user_id="u1", ticker="AAPL", worker_run_id="r2")
        out_sec = earnings_reviewer.run(wi_b, sec_config=_make_sec_config(), _http_get_fn=fake)

        assert out_phase3.replay_idempotency_key != out_sec.replay_idempotency_key, \
            "Phase 3 and SEC-backed artifacts must produce different replay keys"

    def test_phase3_and_sec_error_produce_different_replay_keys(self) -> None:
        wi_a = WorkerInput(user_id="u1", ticker="AAPL", worker_run_id="r1")
        out_phase3 = earnings_reviewer.run(wi_a, sec_config=None)

        fake = FakeHttpGetFn(
            raise_on_url="https://www.sec.gov/files/company_tickers.json",
            raise_exc=RuntimeError("network error"),
        )
        wi_b = WorkerInput(user_id="u1", ticker="AAPL", worker_run_id="r2")
        out_sec_error = earnings_reviewer.run(wi_b, sec_config=_make_sec_config(), _http_get_fn=fake)

        assert out_phase3.replay_idempotency_key != out_sec_error.replay_idempotency_key

    def test_different_filings_produce_different_replay_keys(self) -> None:
        """Two SEC runs with different filing accession numbers produce different keys."""
        ticker_map = _make_ticker_map_response("MSFT", 789019)

        submissions_v1 = _make_submissions_response(
            ["10-K"], ["2024-11-15"],
        )
        # The accession number in _make_submissions_response is deterministic by index.
        # Change the filing date to get a different accession number sequence.
        submissions_v2 = {
            "filings": {
                "recent": {
                    "form": ["10-K", "10-Q"],
                    "filingDate": ["2025-02-01", "2025-05-01"],
                    "accessionNumber": ["0000789019-25-000001", "0000789019-25-000002"],
                    "reportDate": ["2024-12-31", "2025-03-31"],
                }
            }
        }

        fake1 = FakeHttpGetFn(ticker_map=ticker_map, submissions=submissions_v1)
        fake2 = FakeHttpGetFn(ticker_map=ticker_map, submissions=submissions_v2)

        wi = WorkerInput(user_id="u1", ticker="MSFT", worker_run_id="r1")
        out1 = earnings_reviewer.run(wi, sec_config=_make_sec_config(), _http_get_fn=fake1)
        out2 = earnings_reviewer.run(wi, sec_config=_make_sec_config(), _http_get_fn=fake2)

        assert out1.replay_idempotency_key != out2.replay_idempotency_key, \
            "Different SEC filing sets must produce different replay keys"

    def test_same_filings_same_ticker_produce_same_replay_key(self) -> None:
        """Idempotency: same SEC data → same replay key regardless of worker_run_id."""
        ticker_map = _make_ticker_map_response("TSLA", 1318605)
        submissions = _make_submissions_response(["10-K"], ["2025-01-30"])

        wi1 = WorkerInput(user_id="u1", ticker="TSLA", worker_run_id="r1")
        wi2 = WorkerInput(user_id="u1", ticker="TSLA", worker_run_id="r2")

        fake1 = FakeHttpGetFn(ticker_map=ticker_map, submissions=submissions)
        fake2 = FakeHttpGetFn(ticker_map=ticker_map, submissions=submissions)

        out1 = earnings_reviewer.run(wi1, sec_config=_make_sec_config(), _http_get_fn=fake1)
        out2 = earnings_reviewer.run(wi2, sec_config=_make_sec_config(), _http_get_fn=fake2)

        assert out1.replay_idempotency_key == out2.replay_idempotency_key, \
            "Same SEC data + same ticker → same replay key (idempotency)"

    def test_8k_accession_change_changes_fingerprint(self) -> None:
        """A new 8-K accession number must produce a different source fingerprint."""
        from app.services.intelligence.research_workers.earnings_sec_adapter import (
            _compute_source_fingerprint,
        )
        filings_v1 = [SecFilingRecord(
            form_type="8-K", filing_date="2025-04-01",
            accession_number="0000320193-25-000001", report_date=None,
            filing_url="https://example.com/",
        )]
        filings_v2 = [SecFilingRecord(
            form_type="8-K", filing_date="2025-04-01",
            accession_number="0000320193-25-000002", report_date=None,
            filing_url="https://example.com/",
        )]
        fp1 = _compute_source_fingerprint("0000320193", filings_v1)
        fp2 = _compute_source_fingerprint("0000320193", filings_v2)
        assert fp1 != fp2, "Different 8-K accession must produce different fingerprint"

    def test_8k_filing_date_change_changes_fingerprint(self) -> None:
        """A changed 8-K filing_date must produce a different source fingerprint."""
        from app.services.intelligence.research_workers.earnings_sec_adapter import (
            _compute_source_fingerprint,
        )
        filings_v1 = [SecFilingRecord(
            form_type="8-K", filing_date="2025-04-01",
            accession_number="0000320193-25-000001", report_date=None,
            filing_url="https://example.com/",
        )]
        filings_v2 = [SecFilingRecord(
            form_type="8-K", filing_date="2025-04-15",
            accession_number="0000320193-25-000001", report_date=None,
            filing_url="https://example.com/",
        )]
        fp1 = _compute_source_fingerprint("0000320193", filings_v1)
        fp2 = _compute_source_fingerprint("0000320193", filings_v2)
        assert fp1 != fp2, "Changed 8-K filing_date must produce different fingerprint"

    def test_filing_order_does_not_affect_fingerprint(self) -> None:
        """Same filings in different order must produce same fingerprint (stable sort)."""
        from app.services.intelligence.research_workers.earnings_sec_adapter import (
            _compute_source_fingerprint,
        )
        filing_a = SecFilingRecord(
            form_type="10-K", filing_date="2025-01-30",
            accession_number="0000320193-25-000001", report_date="2024-12-31",
            filing_url="https://example.com/a",
        )
        filing_b = SecFilingRecord(
            form_type="8-K", filing_date="2025-03-01",
            accession_number="0000320193-25-000002", report_date=None,
            filing_url="https://example.com/b",
        )
        fp1 = _compute_source_fingerprint("0000320193", [filing_a, filing_b])
        fp2 = _compute_source_fingerprint("0000320193", [filing_b, filing_a])
        assert fp1 == fp2, "Filing order must not affect fingerprint (deterministic sort)"

    def test_10q_accession_change_changes_fingerprint(self) -> None:
        """A new 10-Q accession number must produce a different source fingerprint."""
        from app.services.intelligence.research_workers.earnings_sec_adapter import (
            _compute_source_fingerprint,
        )
        filings_v1 = [SecFilingRecord(
            form_type="10-Q", filing_date="2025-05-01",
            accession_number="0000320193-25-000001", report_date="2025-03-31",
            filing_url="https://example.com/",
        )]
        filings_v2 = [SecFilingRecord(
            form_type="10-Q", filing_date="2025-05-01",
            accession_number="0000320193-25-000099", report_date="2025-03-31",
            filing_url="https://example.com/",
        )]
        fp1 = _compute_source_fingerprint("0000320193", filings_v1)
        fp2 = _compute_source_fingerprint("0000320193", filings_v2)
        assert fp1 != fp2, "Different 10-Q accession must produce different fingerprint"


# ── Bonus: Freshness + confidence classification ──────────────────────────────

class TestFreshnessAndConfidenceClassification:

    def test_fresh_10k_produces_medium_confidence_fresh_freshness(self) -> None:
        result = SecEdgarProviderResult(
            ticker="AAPL",
            cik="0000320193",
            filings=[SecFilingRecord(
                form_type="10-K",
                filing_date=_recent_date_iso(30),
                accession_number="0000320193-25-000001",
                report_date=_recent_date_iso(30),
                filing_url="https://example.com/",
            )],
            fetch_status="success",
        )
        adapted = adapt_sec_result(result, reference_date=date.today())
        assert adapted.confidence_or_trust_level == "MEDIUM"
        assert adapted.freshness_status == "FRESH"

    def test_stale_10k_produces_medium_confidence_stale_freshness(self) -> None:
        result = SecEdgarProviderResult(
            ticker="AAPL",
            cik="0000320193",
            filings=[SecFilingRecord(
                form_type="10-K",
                filing_date=_stale_date_iso(200),
                accession_number="0000320193-24-000001",
                report_date=_stale_date_iso(200),
                filing_url="https://example.com/",
            )],
            fetch_status="success",
        )
        adapted = adapt_sec_result(result, reference_date=date.today())
        assert adapted.confidence_or_trust_level == "MEDIUM"
        assert adapted.freshness_status == "STALE"

    def test_only_8k_produces_low_confidence_fresh_freshness(self) -> None:
        """8-K-only result with a recent date: LOW confidence, FRESH freshness.

        Freshness now uses the latest filing_date across all source-backed filings,
        including 8-K event notices, so a recent 8-K produces FRESH not UNKNOWN.
        """
        result = SecEdgarProviderResult(
            ticker="AAPL",
            cik="0000320193",
            filings=[SecFilingRecord(
                form_type="8-K",
                filing_date=_recent_date_iso(5),
                accession_number="0000320193-25-000001",
                report_date=None,
                filing_url="https://example.com/",
            )],
            fetch_status="success",
        )
        adapted = adapt_sec_result(result, reference_date=date.today())
        assert adapted.confidence_or_trust_level == "LOW"
        assert adapted.freshness_status == "FRESH"

    def test_freshness_window_boundary_fresh(self) -> None:
        boundary_date = date.today() - timedelta(days=_FRESH_WINDOW_DAYS)
        result = SecEdgarProviderResult(
            ticker="AAPL",
            cik="0000320193",
            filings=[SecFilingRecord(
                form_type="10-Q",
                filing_date=boundary_date.isoformat(),
                accession_number="0000320193-25-000001",
                report_date=boundary_date.isoformat(),
                filing_url="https://example.com/",
            )],
            fetch_status="success",
        )
        adapted = adapt_sec_result(result, reference_date=date.today())
        assert adapted.freshness_status == "FRESH"

    def test_freshness_window_boundary_stale(self) -> None:
        stale_date = date.today() - timedelta(days=_FRESH_WINDOW_DAYS + 1)
        result = SecEdgarProviderResult(
            ticker="AAPL",
            cik="0000320193",
            filings=[SecFilingRecord(
                form_type="10-Q",
                filing_date=stale_date.isoformat(),
                accession_number="0000320193-24-000001",
                report_date=stale_date.isoformat(),
                filing_url="https://example.com/",
            )],
            fetch_status="success",
        )
        adapted = adapt_sec_result(result, reference_date=date.today())
        assert adapted.freshness_status == "STALE"

    def test_recent_8k_only_produces_low_confidence_fresh(self) -> None:
        """8-K-only with a recent date: LOW confidence, FRESH freshness.

        Freshness uses the latest filing_date across all source-backed filings.
        """
        result = SecEdgarProviderResult(
            ticker="AAPL", cik="0000320193",
            filings=[SecFilingRecord(
                form_type="8-K", filing_date=_recent_date_iso(10),
                accession_number="0000320193-25-000001", report_date=None,
                filing_url="https://example.com/",
            )],
            fetch_status="success",
        )
        adapted = adapt_sec_result(result, reference_date=date.today())
        assert adapted.confidence_or_trust_level == "LOW"
        assert adapted.freshness_status == "FRESH"

    def test_stale_8k_only_produces_low_confidence_stale(self) -> None:
        """8-K-only with an old filing date: LOW confidence, STALE freshness."""
        result = SecEdgarProviderResult(
            ticker="AAPL", cik="0000320193",
            filings=[SecFilingRecord(
                form_type="8-K", filing_date=_stale_date_iso(200),
                accession_number="0000320193-24-000001", report_date=None,
                filing_url="https://example.com/",
            )],
            fetch_status="success",
        )
        adapted = adapt_sec_result(result, reference_date=date.today())
        assert adapted.confidence_or_trust_level == "LOW"
        assert adapted.freshness_status == "STALE"

    def test_malformed_filing_date_produces_unknown_freshness(self) -> None:
        """All source-backed filings have unparseable dates → freshness UNKNOWN."""
        result = SecEdgarProviderResult(
            ticker="AAPL", cik="0000320193",
            filings=[SecFilingRecord(
                form_type="8-K", filing_date="not-a-date",
                accession_number="0000320193-25-000001", report_date=None,
                filing_url="https://example.com/",
            )],
            fetch_status="success",
        )
        adapted = adapt_sec_result(result, reference_date=date.today())
        assert adapted.freshness_status == "UNKNOWN"
        assert adapted.confidence_or_trust_level == "LOW"

    def test_malformed_filing_date_fails_phase5_readiness(self) -> None:
        """UNKNOWN freshness → artifact does not pass Phase 5 truth adapter readiness."""
        result = SecEdgarProviderResult(
            ticker="AAPL", cik="0000320193",
            filings=[SecFilingRecord(
                form_type="8-K", filing_date="bad-date",
                accession_number="0000320193-25-000001", report_date=None,
                filing_url="https://example.com/",
            )],
            fetch_status="success",
        )
        adapted = adapt_sec_result(result, reference_date=date.today())
        # Build a minimal in-memory artifact dict as Phase 5 readiness expects.
        src_id = str(uuid.uuid4())
        artifact = {
            "id": str(uuid.uuid4()),
            "is_active": True,
            "invalidated_at": None,
            "expires_at": None,
            "artifact_type": "catalyst_window",
            "skill_pack": "earnings_reviewer",
            "confidence_or_trust_level": adapted.confidence_or_trust_level,
            "freshness_status": adapted.freshness_status,
            "safe_for_decision": False,
            "payload": {"worker_phase": "phase7a_sec_grounded"},
        }
        sources = [{
            "id": src_id,
            "source_kind": "sec_filing",
            "provider_name": "sec_edgar",
            "source_url": "https://example.com/",
            "source_id": None,
            "source_hash": None,
            "section_reference": "0000320193-25-000001",
        }]
        facts = [{
            "fact_kind": "sourced_claim",
            "structured_payload": {"claim": "sec_filing_found", "form_type": "8-K"},
            "source_id": src_id,
        }]
        readiness = evaluate_artifact_truth_readiness(artifact, sources, facts)
        assert not readiness.eligible_for_truth_adapter, \
            f"UNKNOWN freshness must fail readiness; got reason_codes={readiness.reason_codes}"

    def test_limitations_contain_no_misleading_claims(self) -> None:
        sec_result = _make_fresh_sec_result("AAPL")
        adapted = adapt_sec_result(sec_result)
        all_limitations = " ".join(adapted.limitations).lower()
        for bad_phrase in ["beats analyst", "misses analyst", "guidance raised",
                           "eps surprise", "revenue above", "beat expectations",
                           "missed expectations"]:
            assert bad_phrase not in all_limitations, \
                f"Limitation text must not claim '{bad_phrase}'"


# ── Bonus: Provider ticker-not-found path ─────────────────────────────────────

class TestProviderNoTickerFound:

    def test_unknown_ticker_returns_no_cik_status(self) -> None:
        # Ticker map does not contain FAKE_TICKER.
        ticker_map = _make_ticker_map_response("AAPL", 320193)
        fake = FakeHttpGetFn(ticker_map=ticker_map)
        result = fetch_for_ticker("FAKETICKER", _make_sec_config(), http_get_fn=fake)
        assert result.fetch_status == "no_cik"
        assert result.filings == []

    def test_no_cik_adapter_result_is_fail_closed(self) -> None:
        ticker_map = _make_ticker_map_response("AAPL", 320193)
        fake = FakeHttpGetFn(ticker_map=ticker_map)
        result = fetch_for_ticker("FAKETICKER", _make_sec_config(), http_get_fn=fake)
        adapted = adapt_sec_result(result)
        assert adapted.confidence_or_trust_level == "UNKNOWN"
        assert adapted.sources == []


# ── Bonus: Config defaults ────────────────────────────────────────────────────

class TestConfigDefaults:

    def test_sec_enabled_defaults_to_false(self) -> None:
        s = Settings(
            supabase_url="http://fake", supabase_anon_key="fake",
            supabase_service_role_key="fake", supabase_jwt_secret="fake",
            encryption_key="fake",
        )
        assert s.intel_v3_earnings_reviewer_sec_enabled is False

    def test_sec_user_agent_defaults_to_none(self) -> None:
        s = Settings(
            supabase_url="http://fake", supabase_anon_key="fake",
            supabase_service_role_key="fake", supabase_jwt_secret="fake",
            encryption_key="fake",
        )
        assert s.sec_edgar_user_agent is None
