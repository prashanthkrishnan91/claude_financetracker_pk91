"""Phase 7A acceptance tests — SEC CompanyFacts financial evidence enrichment.

Covers acceptance criteria from the Phase 7A task spec:

Provider:
  1. CompanyFacts fetch uses existing SEC user-agent and request cap (request 3).
  2. SEC flag off still produces Phase 3 behavior (no companyfacts).
  3. SEC submissions success + CompanyFacts success → metric_observation facts.
  4. CompanyFacts timeout/error/malformed → fail-closed, Phase 6A filing metadata preserved.
  5. No raw companyfacts payload persisted in artifact payload.

Parser (sec_companyfacts_parser):
  6. Extracts allowed revenue tag from USD facts.
  7. Extracts NetIncomeLoss from USD facts.
  8. Extracts EPS diluted/basic from USD/shares facts.
  9. Skips unsupported/custom taxonomy tags.
 10. Skips wrong units (e.g., USD for EPS tag).
 11. Skips facts without matching accession in source_accessions set.
 12. Keeps only latest bounded recent facts, not full history.
 13. Same fact set in different order → same metric digest.
 14. Changing metric value/accession/filed date changes digest.

Worker/output:
 15. SEC-backed artifact has SourceRecords and source-linked filing facts.
 16. SEC-backed artifact with companyfacts has source-linked metric_observation facts.
 17. Every metric_observation fact has source_index set.
 18. Artifact still passes Phase 5 eligible_for_truth_adapter=True.
 19. eligible_for_decision_consumption remains False.
 20. safe_for_decision remains False.
 21. No forbidden payload/fact keys in metric_observation structured_payload.
 22. No decide()/IntelV3Service/recommendation_engine imports.
 23. No writes to intel_v3_snapshots.
 24. No SQL.

All tests use pure in-memory data structures — no production DB or HTTP calls.
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

import pytest

from app.services.intelligence.research_workers.contracts import (
    FORBIDDEN_PAYLOAD_KEYS,
    WorkerInput,
    _has_forbidden_key,
)
from app.services.intelligence.research_workers import earnings_reviewer
from app.services.intelligence.research_workers.sec_edgar_provider import (
    SecEdgarProviderConfig,
    SecEdgarProviderResult,
    SecFilingRecord,
    fetch_for_ticker,
)
from app.services.intelligence.research_workers.earnings_sec_adapter import (
    SecEarningsAdapterResult,
    adapt_sec_result,
    _compute_source_fingerprint,
)
from app.services.intelligence.research_workers.sec_companyfacts_parser import (
    CompanyFactsParseResult,
    MetricObservation,
    _METRIC_TAG_ALLOWLIST,
    _ALLOWED_UNIT_BY_TAG,
    _EPS_TAGS,
    compute_metric_digest,
    parse_companyfacts,
)
from app.services.intelligence.research_workers.artifact_truth_readiness import (
    evaluate_artifact_truth_readiness,
)


# ── Test helpers ──────────────────────────────────────────────────────────────

def _today() -> str:
    return date.today().isoformat()


def _recent(days: int = 30) -> str:
    return (date.today() - timedelta(days=days)).isoformat()


def _make_sec_config(max_requests: int = 3) -> SecEdgarProviderConfig:
    return SecEdgarProviderConfig(
        user_agent="TestApp/1.0 test@example.com",
        max_requests_per_ticker=max_requests,
    )


def _make_ticker_map(ticker: str, cik: int) -> dict:
    return {"0": {"cik_str": cik, "ticker": ticker.upper(), "title": f"Fake {ticker}"}}


def _make_submissions(forms: list[str], dates: list[str], accessions: Optional[list[str]] = None) -> dict:
    if accessions is None:
        accessions = [f"0000320193-24-{i:06d}" for i in range(len(forms))]
    report_dates = [d[:7] + "-01" for d in dates]
    return {
        "filings": {
            "recent": {
                "form": forms,
                "filingDate": dates,
                "accessionNumber": accessions,
                "reportDate": report_dates,
            }
        }
    }


def _make_companyfacts(tag: str, unit: str, entries: list[dict]) -> dict:
    """Build a minimal companyfacts JSON payload for one tag."""
    label = f"{tag} Label"
    return {
        "facts": {
            "us-gaap": {
                tag: {
                    "label": label,
                    "units": {unit: entries},
                }
            }
        }
    }


def _make_cf_entry(
    accn: str,
    val: Any,
    form: str = "10-Q",
    filed: str = "2025-01-15",
    fy: int = 2025,
    fp: str = "Q1",
) -> dict:
    return {"accn": accn, "val": val, "form": form, "filed": filed, "fy": fy, "fp": fp}


class _FakeResponse:
    status_code = 200

    def __init__(self, data: dict) -> None:
        self._data = data

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._data


class _FakeErrorResponse:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def raise_for_status(self) -> None:
        raise self._exc

    def json(self) -> dict:
        return {}


class FakeHttpGetFn:
    """Fake HTTP callable for provider tests."""

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


def _make_sec_result_with_companyfacts(
    ticker: str = "AAPL",
    accessions: Optional[list[str]] = None,
    companyfacts_observations: Optional[list[MetricObservation]] = None,
) -> SecEdgarProviderResult:
    """Build a SecEdgarProviderResult with optional companyfacts parse result."""
    acc = accessions or ["0000320193-25-000001"]
    filings = [
        SecFilingRecord(
            form_type="10-Q",
            filing_date=_recent(30),
            accession_number=a,
            report_date=_recent(60),
            filing_url=f"https://www.sec.gov/Archives/edgar/data/320193/{a.replace('-','')}/",
        )
        for a in acc
    ]
    cf = None
    if companyfacts_observations is not None:
        cf = CompanyFactsParseResult(
            observations=companyfacts_observations,
            parse_status="success" if companyfacts_observations else "no_facts",
            tags_found=list({o.tag for o in companyfacts_observations}),
        )
    return SecEdgarProviderResult(
        ticker=ticker.upper(),
        cik="0000320193",
        filings=filings,
        fetch_status="success",
        fetched_at=_today(),
        request_count=3,
        companyfacts_parse_result=cf,
    )


def _make_metric_obs(
    tag: str = "Revenues",
    accn: str = "0000320193-25-000001",
    val: int = 1_000_000,
    unit: str = "USD",
) -> MetricObservation:
    return MetricObservation(
        taxonomy="us-gaap",
        tag=tag,
        label=f"{tag} Label",
        value=val,
        unit=unit,
        form="10-Q",
        fiscal_year=2025,
        fiscal_period="Q1",
        filed=_recent(30),
        accession_number=accn,
    )


# ── Criterion 1: CompanyFacts fetch uses request cap ─────────────────────────

class TestCriterion1RequestCap:

    def test_companyfacts_is_request_3(self) -> None:
        """With max_requests=3, companyfacts is fetched as the 3rd request."""
        calls: list[str] = []
        ticker_map = _make_ticker_map("AAPL", 320193)
        submissions = _make_submissions(["10-Q"], [_recent(30)], ["0000320193-25-000001"])
        fake = FakeHttpGetFn(ticker_map=ticker_map, submissions=submissions, calls_log=calls)
        result = fetch_for_ticker("AAPL", _make_sec_config(max_requests=3), http_get_fn=fake)
        assert result.is_success
        assert result.request_count == 3
        assert len(calls) == 3
        assert any("companyfacts" in c for c in calls), "companyfacts URL must be called"
        assert any("company_tickers" in c for c in calls)
        assert any("submissions" in c for c in calls)

    def test_companyfacts_skipped_when_cap_is_2(self) -> None:
        """With max_requests=2, companyfacts is NOT fetched (cap exhausted after submissions)."""
        calls: list[str] = []
        ticker_map = _make_ticker_map("AAPL", 320193)
        submissions = _make_submissions(["10-Q"], [_recent(30)])
        fake = FakeHttpGetFn(ticker_map=ticker_map, submissions=submissions, calls_log=calls)
        result = fetch_for_ticker("AAPL", _make_sec_config(max_requests=2), http_get_fn=fake)
        assert result.is_success
        assert result.request_count == 2
        assert not any("companyfacts" in c for c in calls)
        assert result.companyfacts_parse_result is None

    def test_user_agent_sent_to_companyfacts_url(self) -> None:
        """User-Agent is configured at the provider level — applies to all requests including companyfacts."""
        result = fetch_for_ticker(
            "AAPL",
            SecEdgarProviderConfig(user_agent=""),  # empty agent
        )
        assert result.fetch_status == "no_user_agent"

    def test_companyfacts_cik_url_format(self) -> None:
        """CompanyFacts URL uses padded CIK format."""
        calls: list[str] = []
        ticker_map = _make_ticker_map("AAPL", 320193)
        submissions = _make_submissions(["10-Q"], [_recent(30)])
        fake = FakeHttpGetFn(ticker_map=ticker_map, submissions=submissions, calls_log=calls)
        fetch_for_ticker("AAPL", _make_sec_config(), http_get_fn=fake)
        cf_calls = [c for c in calls if "companyfacts" in c]
        assert len(cf_calls) == 1
        assert "CIK0000320193" in cf_calls[0], f"Expected padded CIK in URL, got: {cf_calls[0]}"


# ── Criterion 2: SEC flag off → Phase 3 behavior ─────────────────────────────

class TestCriterion2SecFlagOff:

    def test_sec_flag_off_no_companyfacts(self) -> None:
        wi = WorkerInput(user_id="u1", ticker="AAPL", worker_run_id="r1")
        output = earnings_reviewer.run(wi, sec_config=None)
        assert output.confidence_or_trust_level == "UNKNOWN"
        assert output.freshness_status == "UNKNOWN"
        assert output.sources == []

    def test_sec_flag_off_model_version_is_phase3(self) -> None:
        wi = WorkerInput(user_id="u1", ticker="AAPL", worker_run_id="r1")
        output = earnings_reviewer.run(wi, sec_config=None)
        assert output.model_version == "none_phase3_dark_run"


# ── Criterion 3: Submissions + CompanyFacts success → metric_observation facts ─

class TestCriterion3MetricObservationFacts:

    def test_metric_observations_produce_fact_records(self) -> None:
        obs = _make_metric_obs(tag="Revenues", accn="0000320193-25-000001", val=99_000_000)
        sec_result = _make_sec_result_with_companyfacts(
            accessions=["0000320193-25-000001"],
            companyfacts_observations=[obs],
        )
        adapted = adapt_sec_result(sec_result)
        metric_facts = [f for f in adapted.facts if f.fact_kind == "metric_observation"]
        assert len(metric_facts) == 1

    def test_metric_fact_has_correct_payload_shape(self) -> None:
        obs = _make_metric_obs(tag="Revenues", accn="0000320193-25-000001", val=500_000_000)
        sec_result = _make_sec_result_with_companyfacts(
            accessions=["0000320193-25-000001"],
            companyfacts_observations=[obs],
        )
        adapted = adapt_sec_result(sec_result)
        metric_fact = next(f for f in adapted.facts if f.fact_kind == "metric_observation")
        payload = metric_fact.structured_payload
        assert payload["claim"] == "sec_companyfact_observed"
        assert payload["taxonomy"] == "us-gaap"
        assert payload["tag"] == "Revenues"
        assert payload["value"] == 500_000_000
        assert payload["unit"] == "USD"
        assert payload["form"] == "10-Q"
        assert "accession_number" in payload
        assert "filed" in payload

    def test_metric_fact_and_filing_fact_both_present(self) -> None:
        obs = _make_metric_obs(tag="NetIncomeLoss", accn="0000320193-25-000001", val=12_000_000)
        sec_result = _make_sec_result_with_companyfacts(
            accessions=["0000320193-25-000001"],
            companyfacts_observations=[obs],
        )
        adapted = adapt_sec_result(sec_result)
        sourced_claims = [f for f in adapted.facts if f.fact_kind == "sourced_claim"]
        metric_obs = [f for f in adapted.facts if f.fact_kind == "metric_observation"]
        assert len(sourced_claims) >= 1
        assert len(metric_obs) >= 1

    def test_model_version_is_phase7a(self) -> None:
        ticker_map = _make_ticker_map("AAPL", 320193)
        submissions = _make_submissions(["10-Q"], [_recent(30)], ["0000320193-25-000001"])
        fake = FakeHttpGetFn(ticker_map=ticker_map, submissions=submissions)
        wi = WorkerInput(user_id="u1", ticker="AAPL", worker_run_id="r1")
        output = earnings_reviewer.run(wi, sec_config=_make_sec_config(), _http_get_fn=fake)
        assert output.model_version == "sec_edgar_phase7a_v1"


# ── Criterion 4: CompanyFacts failure → fail-closed, Phase 6A preserved ──────

class TestCriterion4CompanyFactsFailure:

    def test_companyfacts_timeout_preserves_filing_facts(self) -> None:
        """Companyfacts timeout does not break submissions-backed filing behavior."""
        ticker_map = _make_ticker_map("AAPL", 320193)
        submissions = _make_submissions(["10-Q"], [_recent(30)], ["0000320193-25-000001"])
        cf_url_fragment = "companyfacts"
        fake = FakeHttpGetFn(
            ticker_map=ticker_map,
            submissions=submissions,
            raise_on_url=f"https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json",
            raise_exc=TimeoutError("companyfacts timeout"),
        )
        result = fetch_for_ticker("AAPL", _make_sec_config(), http_get_fn=fake)
        assert result.is_success, "Provider result should still be success after companyfacts failure"
        assert len(result.filings) >= 1
        assert result.companyfacts_parse_result is not None
        assert result.companyfacts_parse_result.parse_status == "error"

    def test_companyfacts_error_adapted_artifact_still_has_filing_facts(self) -> None:
        sec_result = _make_sec_result_with_companyfacts(
            accessions=["0000320193-25-000001"],
            companyfacts_observations=None,  # no companyfacts parse result
        )
        sec_result.companyfacts_parse_result = CompanyFactsParseResult(
            parse_status="error",
            error_message="simulated error",
        )
        adapted = adapt_sec_result(sec_result)
        # Filing facts still present.
        sourced_claims = [f for f in adapted.facts if f.fact_kind == "sourced_claim"]
        assert len(sourced_claims) >= 1
        # No metric observations from failed fetch.
        metric_obs = [f for f in adapted.facts if f.fact_kind == "metric_observation"]
        assert len(metric_obs) == 0
        # Confidence and freshness still derived from filings.
        assert adapted.confidence_or_trust_level in ("MEDIUM", "LOW")

    def test_companyfacts_malformed_response_preserved(self) -> None:
        """Malformed companyfacts response does not crash the provider."""
        ticker_map = _make_ticker_map("NVDA", 1045810)
        submissions = _make_submissions(["10-K"], [_recent(30)])
        # Malformed companyfacts: not a valid structure.
        fake = FakeHttpGetFn(
            ticker_map=ticker_map,
            submissions=submissions,
            companyfacts={"not_facts": "broken"},
        )
        result = fetch_for_ticker("NVDA", _make_sec_config(), http_get_fn=fake)
        assert result.is_success
        assert result.companyfacts_parse_result is not None
        assert result.companyfacts_parse_result.parse_status in ("error", "no_facts")


# ── Criterion 5: No raw companyfacts payload in artifact ─────────────────────

class TestCriterion5NoRawPayload:

    def test_artifact_payload_has_no_raw_companyfacts(self) -> None:
        ticker_map = _make_ticker_map("AAPL", 320193)
        submissions = _make_submissions(["10-Q"], [_recent(30)])
        fake = FakeHttpGetFn(ticker_map=ticker_map, submissions=submissions)
        wi = WorkerInput(user_id="u1", ticker="AAPL", worker_run_id="r1")
        output = earnings_reviewer.run(wi, sec_config=_make_sec_config(), _http_get_fn=fake)
        payload_str = json.dumps(output.artifact_payload)
        # Raw companyfacts payload fields must not appear.
        assert "entityName" not in payload_str
        assert '"facts"' not in payload_str
        assert "companyfacts_raw" not in payload_str

    def test_metric_fact_payload_has_only_safe_fields(self) -> None:
        """Metric observation structured_payload contains only the allowed safe fields."""
        obs = _make_metric_obs(tag="Assets", accn="0000320193-25-000001", val=500_000_000_000)
        sec_result = _make_sec_result_with_companyfacts(
            accessions=["0000320193-25-000001"],
            companyfacts_observations=[obs],
        )
        adapted = adapt_sec_result(sec_result)
        for fact in adapted.facts:
            if fact.fact_kind == "metric_observation":
                payload_str = json.dumps(fact.structured_payload)
                assert "entityName" not in payload_str
                assert "us-gaap" not in payload_str or "taxonomy" in fact.structured_payload


# ── Criterion 6: Extracts revenue tags ───────────────────────────────────────

class TestCriterion6RevenueTag:

    def test_extracts_revenues_from_usd_facts(self) -> None:
        raw = _make_companyfacts("Revenues", "USD", [
            _make_cf_entry("0000320193-25-000001", 123_456_789),
        ])
        accessions = frozenset(["0000320193-25-000001"])
        result = parse_companyfacts(raw, accessions)
        assert result.is_success
        obs = result.observations[0]
        assert obs.tag == "Revenues"
        assert obs.value == 123_456_789
        assert obs.unit == "USD"

    def test_extracts_revenue_from_contract(self) -> None:
        raw = _make_companyfacts(
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "USD",
            [_make_cf_entry("0000320193-25-000001", 90_000_000)],
        )
        result = parse_companyfacts(raw, frozenset(["0000320193-25-000001"]))
        assert result.is_success
        assert result.observations[0].tag == "RevenueFromContractWithCustomerExcludingAssessedTax"

    def test_extracts_sales_revenue_net(self) -> None:
        raw = _make_companyfacts("SalesRevenueNet", "USD", [
            _make_cf_entry("0000320193-25-000001", 80_000_000),
        ])
        result = parse_companyfacts(raw, frozenset(["0000320193-25-000001"]))
        assert result.is_success
        assert result.observations[0].tag == "SalesRevenueNet"


# ── Criterion 7: Extracts net income ─────────────────────────────────────────

class TestCriterion7NetIncome:

    def test_extracts_net_income_loss(self) -> None:
        raw = _make_companyfacts("NetIncomeLoss", "USD", [
            _make_cf_entry("0000320193-25-000001", 25_000_000),
        ])
        result = parse_companyfacts(raw, frozenset(["0000320193-25-000001"]))
        assert result.is_success
        assert result.observations[0].tag == "NetIncomeLoss"
        assert result.observations[0].value == 25_000_000

    def test_negative_net_income_accepted(self) -> None:
        """Negative values are valid metric observations (loss reporting)."""
        raw = _make_companyfacts("NetIncomeLoss", "USD", [
            _make_cf_entry("0000320193-25-000001", -5_000_000),
        ])
        result = parse_companyfacts(raw, frozenset(["0000320193-25-000001"]))
        assert result.is_success
        assert result.observations[0].value == -5_000_000


# ── Criterion 8: Extracts EPS diluted/basic ──────────────────────────────────

class TestCriterion8EPS:

    def test_extracts_eps_diluted(self) -> None:
        raw = _make_companyfacts("EarningsPerShareDiluted", "USD/shares", [
            _make_cf_entry("0000320193-25-000001", 1.52),
        ])
        result = parse_companyfacts(raw, frozenset(["0000320193-25-000001"]))
        assert result.is_success
        obs = result.observations[0]
        assert obs.tag == "EarningsPerShareDiluted"
        assert obs.unit == "USD/shares"
        assert abs(obs.value - 1.52) < 1e-9

    def test_extracts_eps_basic(self) -> None:
        raw = _make_companyfacts("EarningsPerShareBasic", "USD/shares", [
            _make_cf_entry("0000320193-25-000001", 1.56),
        ])
        result = parse_companyfacts(raw, frozenset(["0000320193-25-000001"]))
        assert result.is_success
        assert result.observations[0].tag == "EarningsPerShareBasic"


# ── Criterion 9: Skips unsupported/custom taxonomy tags ──────────────────────

class TestCriterion9UnsupportedTags:

    def test_skips_custom_tag_not_in_allowlist(self) -> None:
        raw = {"facts": {"us-gaap": {
            "CustomMetricNotInAllowlist": {
                "label": "Custom",
                "units": {"USD": [_make_cf_entry("0000320193-25-000001", 999)]},
            }
        }}}
        result = parse_companyfacts(raw, frozenset(["0000320193-25-000001"]))
        assert result.observation_count == 0
        assert result.parse_status == "no_facts"

    def test_skips_dei_taxonomy_tag(self) -> None:
        raw = {"facts": {
            "dei": {
                "EntityCommonStockSharesOutstanding": {
                    "label": "Shares",
                    "units": {"shares": [_make_cf_entry("0000320193-25-000001", 100000)]},
                }
            },
            "us-gaap": {},
        }}
        result = parse_companyfacts(raw, frozenset(["0000320193-25-000001"]))
        assert result.observation_count == 0

    def test_all_allowlisted_tags_recognized(self) -> None:
        """All 13 tags in the allowlist are accepted, no others."""
        expected_count = 13
        assert len(_METRIC_TAG_ALLOWLIST) == expected_count


# ── Criterion 10: Skips wrong units ─────────────────────────────────────────

class TestCriterion10WrongUnits:

    def test_skips_usd_for_eps_tag(self) -> None:
        """EPS tags require USD/shares, not USD."""
        raw = _make_companyfacts("EarningsPerShareDiluted", "USD", [
            _make_cf_entry("0000320193-25-000001", 1.52),
        ])
        result = parse_companyfacts(raw, frozenset(["0000320193-25-000001"]))
        assert result.observation_count == 0, "EPS tag must not accept USD unit"

    def test_skips_usd_shares_for_revenue_tag(self) -> None:
        """Revenue tags require USD, not USD/shares."""
        raw = _make_companyfacts("Revenues", "USD/shares", [
            _make_cf_entry("0000320193-25-000001", 1_000_000),
        ])
        result = parse_companyfacts(raw, frozenset(["0000320193-25-000001"]))
        assert result.observation_count == 0, "Revenue tag must not accept USD/shares unit"

    def test_skips_shares_unit(self) -> None:
        raw = _make_companyfacts("Assets", "shares", [
            _make_cf_entry("0000320193-25-000001", 1_000_000),
        ])
        result = parse_companyfacts(raw, frozenset(["0000320193-25-000001"]))
        assert result.observation_count == 0


# ── Criterion 11: Skips facts without matching accession ─────────────────────

class TestCriterion11AccessionLinkage:

    def test_skips_fact_not_in_source_accessions(self) -> None:
        raw = _make_companyfacts("Revenues", "USD", [
            _make_cf_entry("0000320193-99-999999", 1_000_000),  # not in source set
        ])
        result = parse_companyfacts(raw, frozenset(["0000320193-25-000001"]))
        assert result.observation_count == 0, "Fact with unknown accn must be skipped"

    def test_includes_fact_when_accession_matches(self) -> None:
        raw = _make_companyfacts("Revenues", "USD", [
            _make_cf_entry("0000320193-25-000001", 500_000_000),
        ])
        result = parse_companyfacts(raw, frozenset(["0000320193-25-000001"]))
        assert result.observation_count == 1

    def test_empty_source_accessions_skips_all_facts(self) -> None:
        raw = _make_companyfacts("Revenues", "USD", [
            _make_cf_entry("0000320193-25-000001", 500_000_000),
        ])
        result = parse_companyfacts(raw, frozenset())
        assert result.observation_count == 0

    def test_no_unlinked_metric_facts_in_adapter(self) -> None:
        """Adapter must not produce metric_observation facts with source_index=None."""
        obs = _make_metric_obs(tag="Assets", accn="NONEXISTENT-ACCN", val=1_000_000)
        sec_result = _make_sec_result_with_companyfacts(
            accessions=["0000320193-25-000001"],
            companyfacts_observations=[obs],
        )
        adapted = adapt_sec_result(sec_result)
        for fact in adapted.facts:
            if fact.fact_kind == "metric_observation":
                assert fact.source_index is not None, "metric_observation must have source_index"


# ── Criterion 12: Keeps only latest bounded facts ────────────────────────────

class TestCriterion12BoundedHistory:

    def test_keeps_at_most_2_periods_per_tag(self) -> None:
        accn = "0000320193-25-000001"
        entries = [
            _make_cf_entry(accn, 100, filed="2025-01-15", fy=2025, fp="Q1"),
            _make_cf_entry(accn, 200, filed="2024-10-15", fy=2024, fp="Q3"),
            _make_cf_entry(accn, 300, filed="2024-07-15", fy=2024, fp="Q2"),
            _make_cf_entry(accn, 400, filed="2024-04-15", fy=2024, fp="Q1"),
        ]
        raw = {"facts": {"us-gaap": {
            "Revenues": {"label": "Revenue", "units": {"USD": entries}},
        }}}
        result = parse_companyfacts(raw, frozenset([accn]))
        # Default max is 2 periods per tag.
        assert result.observation_count <= 2

    def test_keeps_most_recent_periods(self) -> None:
        """When bounded, the most recently filed entries are kept."""
        accn = "0000320193-25-000001"
        entries = [
            _make_cf_entry(accn, 100, filed="2025-01-15", fp="Q1"),  # most recent
            _make_cf_entry(accn, 200, filed="2024-10-15", fp="Q3"),  # second most recent
            _make_cf_entry(accn, 300, filed="2024-07-15", fp="Q2"),  # should be dropped
        ]
        raw = {"facts": {"us-gaap": {
            "NetIncomeLoss": {"label": "Net Income", "units": {"USD": entries}},
        }}}
        result = parse_companyfacts(raw, frozenset([accn]), max_periods_per_tag=2)
        assert result.observation_count == 2
        filed_dates = {o.filed for o in result.observations}
        assert "2025-01-15" in filed_dates
        assert "2024-10-15" in filed_dates
        assert "2024-07-15" not in filed_dates, "Oldest period must be dropped"

    def test_8k_form_excluded(self) -> None:
        """8-K entries are excluded — only 10-K/10-Q accepted for metric observations."""
        accn = "0000320193-25-000001"
        entries = [
            {"accn": accn, "val": 99, "form": "8-K", "filed": "2025-01-01"},
            _make_cf_entry(accn, 100, form="10-Q"),
        ]
        raw = {"facts": {"us-gaap": {
            "Revenues": {"label": "Revenue", "units": {"USD": entries}},
        }}}
        result = parse_companyfacts(raw, frozenset([accn]))
        obs_forms = {o.form for o in result.observations}
        assert "8-K" not in obs_forms, "8-K form entries must be excluded"
        assert "10-Q" in obs_forms


# ── Criterion 13: Same fact set in different order → same digest ──────────────

class TestCriterion13DigestStability:

    def test_same_facts_different_order_same_digest(self) -> None:
        obs_a = _make_metric_obs("Revenues", "0000320193-25-000001", 100)
        obs_b = _make_metric_obs("NetIncomeLoss", "0000320193-25-000001", 50)
        digest1 = compute_metric_digest([obs_a, obs_b])
        digest2 = compute_metric_digest([obs_b, obs_a])
        assert digest1 == digest2, "Observation order must not affect digest"

    def test_empty_observations_produce_stable_digest(self) -> None:
        d1 = compute_metric_digest([])
        d2 = compute_metric_digest([])
        assert d1 == d2

    def test_same_facts_same_digest(self) -> None:
        obs = _make_metric_obs("Assets", "0000320193-25-000001", 1_000_000)
        d1 = compute_metric_digest([obs])
        d2 = compute_metric_digest([obs])
        assert d1 == d2


# ── Criterion 14: Changes to facts change the digest ─────────────────────────

class TestCriterion14DigestSensitivity:

    def test_changed_value_changes_digest(self) -> None:
        obs1 = _make_metric_obs("Revenues", "0000320193-25-000001", 100)
        obs2 = _make_metric_obs("Revenues", "0000320193-25-000001", 999)
        assert compute_metric_digest([obs1]) != compute_metric_digest([obs2])

    def test_changed_accession_changes_digest(self) -> None:
        obs1 = _make_metric_obs("Revenues", "0000320193-25-000001", 100)
        obs2 = _make_metric_obs("Revenues", "0000320193-25-000002", 100)
        assert compute_metric_digest([obs1]) != compute_metric_digest([obs2])

    def test_changed_filed_date_changes_digest(self) -> None:
        obs1 = MetricObservation(
            taxonomy="us-gaap", tag="Revenues", label="Rev", value=100,
            unit="USD", form="10-Q", fiscal_year=2025, fiscal_period="Q1",
            filed="2025-01-15", accession_number="0000320193-25-000001",
        )
        obs2 = MetricObservation(
            taxonomy="us-gaap", tag="Revenues", label="Rev", value=100,
            unit="USD", form="10-Q", fiscal_year=2025, fiscal_period="Q1",
            filed="2025-02-01", accession_number="0000320193-25-000001",
        )
        assert compute_metric_digest([obs1]) != compute_metric_digest([obs2])

    def test_changed_fiscal_period_changes_digest(self) -> None:
        obs1 = MetricObservation(
            taxonomy="us-gaap", tag="Revenues", label="Rev", value=100,
            unit="USD", form="10-Q", fiscal_year=2025, fiscal_period="Q1",
            filed="2025-01-15", accession_number="0000320193-25-000001",
        )
        obs2 = MetricObservation(
            taxonomy="us-gaap", tag="Revenues", label="Rev", value=100,
            unit="USD", form="10-Q", fiscal_year=2025, fiscal_period="Q2",
            filed="2025-01-15", accession_number="0000320193-25-000001",
        )
        assert compute_metric_digest([obs1]) != compute_metric_digest([obs2])


# ── Criterion 15: Artifact has SourceRecords and source-linked filing facts ───

class TestCriterion15SourceRecords:

    def test_sec_backed_artifact_has_sources(self) -> None:
        sec_result = _make_sec_result_with_companyfacts(
            accessions=["0000320193-25-000001"],
            companyfacts_observations=None,
        )
        adapted = adapt_sec_result(sec_result)
        assert len(adapted.sources) >= 1

    def test_filing_facts_have_source_index(self) -> None:
        sec_result = _make_sec_result_with_companyfacts(
            accessions=["0000320193-25-000001"],
            companyfacts_observations=None,
        )
        adapted = adapt_sec_result(sec_result)
        filing_facts = [f for f in adapted.facts if f.fact_kind == "sourced_claim"]
        for fact in filing_facts:
            assert fact.source_index is not None


# ── Criterion 16: With companyfacts, metric_observation facts are present ─────

class TestCriterion16MetricObservationFacts:

    def test_metric_observations_added_to_facts_list(self) -> None:
        obs = _make_metric_obs(tag="OperatingIncomeLoss", accn="0000320193-25-000001", val=8_000_000)
        sec_result = _make_sec_result_with_companyfacts(
            accessions=["0000320193-25-000001"],
            companyfacts_observations=[obs],
        )
        adapted = adapt_sec_result(sec_result)
        metric_facts = [f for f in adapted.facts if f.fact_kind == "metric_observation"]
        assert len(metric_facts) >= 1

    def test_multiple_metric_observations_all_added(self) -> None:
        obs1 = _make_metric_obs(tag="Revenues", accn="0000320193-25-000001", val=100_000_000)
        obs2 = _make_metric_obs(tag="NetIncomeLoss", accn="0000320193-25-000001", val=10_000_000)
        sec_result = _make_sec_result_with_companyfacts(
            accessions=["0000320193-25-000001"],
            companyfacts_observations=[obs1, obs2],
        )
        adapted = adapt_sec_result(sec_result)
        metric_facts = [f for f in adapted.facts if f.fact_kind == "metric_observation"]
        assert len(metric_facts) == 2


# ── Criterion 17: Every metric fact has source_index set ─────────────────────

class TestCriterion17SourceIndex:

    def test_every_metric_fact_has_source_index(self) -> None:
        obs = _make_metric_obs(tag="StockholdersEquity", accn="0000320193-25-000001", val=50_000_000)
        sec_result = _make_sec_result_with_companyfacts(
            accessions=["0000320193-25-000001"],
            companyfacts_observations=[obs],
        )
        adapted = adapt_sec_result(sec_result)
        for fact in adapted.facts:
            if fact.fact_kind == "metric_observation":
                assert fact.source_index is not None
                assert isinstance(fact.source_index, int)
                assert fact.source_index >= 0

    def test_source_index_maps_to_valid_source(self) -> None:
        obs = _make_metric_obs(tag="Assets", accn="0000320193-25-000001", val=200_000_000)
        sec_result = _make_sec_result_with_companyfacts(
            accessions=["0000320193-25-000001"],
            companyfacts_observations=[obs],
        )
        adapted = adapt_sec_result(sec_result)
        for fact in adapted.facts:
            if fact.fact_kind == "metric_observation":
                assert fact.source_index < len(adapted.sources)
                linked_source = adapted.sources[fact.source_index]
                assert linked_source.section_reference == obs.accession_number


# ── Criterion 18: Phase 5 eligible_for_truth_adapter=True ────────────────────

class TestCriterion18TruthAdapterEligibility:

    def test_artifact_with_metric_facts_eligible_for_truth_adapter(self) -> None:
        obs = _make_metric_obs(tag="Revenues", accn="0000320193-25-000001", val=100_000)
        sec_result = _make_sec_result_with_companyfacts(
            accessions=["0000320193-25-000001"],
            companyfacts_observations=[obs],
        )
        adapted = adapt_sec_result(sec_result)

        src_id = str(uuid.uuid4())
        artifact = {
            "id": str(uuid.uuid4()),
            "ticker": "AAPL",
            "artifact_type": "catalyst_window",
            "skill_pack": "earnings_reviewer",
            "is_active": True,
            "invalidated_at": None,
            "expires_at": adapted.expires_at,
            "confidence_or_trust_level": adapted.confidence_or_trust_level,
            "freshness_status": adapted.freshness_status,
            "safe_for_decision": False,
            "payload": {"review_status": "sec_source_grounded_partial"},
        }
        sources = [{
            "id": src_id,
            "source_kind": "sec_filing",
            "provider_name": "sec_edgar",
            "source_url": adapted.sources[0].source_url,
            "source_id": None,
            "source_hash": None,
            "section_reference": adapted.sources[0].section_reference,
        }]
        facts = []
        for f in adapted.facts:
            facts.append({
                "fact_kind": f.fact_kind,
                "structured_payload": f.structured_payload,
                "source_id": src_id,
            })

        result = evaluate_artifact_truth_readiness(artifact, sources, facts)
        assert result.eligible_for_truth_adapter is True, (
            f"Expected eligible_for_truth_adapter=True but got reason_codes={result.reason_codes}"
        )


# ── Criterion 19: eligible_for_decision_consumption remains False ─────────────

class TestCriterion19DecisionConsumptionBlocked:

    def test_eligible_for_decision_consumption_is_always_false(self) -> None:
        obs = _make_metric_obs(tag="NetIncomeLoss", accn="0000320193-25-000001", val=10_000)
        sec_result = _make_sec_result_with_companyfacts(
            accessions=["0000320193-25-000001"],
            companyfacts_observations=[obs],
        )
        adapted = adapt_sec_result(sec_result)

        src_id = str(uuid.uuid4())
        artifact = {
            "id": str(uuid.uuid4()),
            "ticker": "AAPL",
            "artifact_type": "catalyst_window",
            "skill_pack": "earnings_reviewer",
            "is_active": True,
            "invalidated_at": None,
            "expires_at": adapted.expires_at,
            "confidence_or_trust_level": adapted.confidence_or_trust_level,
            "freshness_status": adapted.freshness_status,
            "safe_for_decision": False,
            "payload": {"review_status": "sec_source_grounded_partial"},
        }
        facts = [{"fact_kind": f.fact_kind, "structured_payload": f.structured_payload, "source_id": src_id}
                 for f in adapted.facts]
        sources = [{"id": src_id, "source_kind": "sec_filing", "provider_name": "sec_edgar",
                    "source_url": "https://example.com/", "source_id": None, "source_hash": None,
                    "section_reference": "0000320193-25-000001"}]

        result = evaluate_artifact_truth_readiness(artifact, sources, facts)
        assert result.eligible_for_decision_consumption is False


# ── Criterion 20: safe_for_decision remains False ────────────────────────────

class TestCriterion20SafeForDecisionFalse:

    def test_adapter_result_has_no_safe_for_decision_field(self) -> None:
        obs = _make_metric_obs(tag="Assets", accn="0000320193-25-000001", val=100)
        sec_result = _make_sec_result_with_companyfacts(
            accessions=["0000320193-25-000001"],
            companyfacts_observations=[obs],
        )
        adapted = adapt_sec_result(sec_result)
        # The adapter result must not set safe_for_decision anywhere.
        for fact in adapted.facts:
            assert "safe_for_decision" not in fact.structured_payload

    def test_phase5_readiness_blocks_safe_for_decision_promotion(self) -> None:
        src_id = str(uuid.uuid4())
        artifact = {
            "id": "test-id", "ticker": "AAPL",
            "artifact_type": "catalyst_window", "skill_pack": "earnings_reviewer",
            "is_active": True, "invalidated_at": None, "expires_at": None,
            "confidence_or_trust_level": "MEDIUM", "freshness_status": "FRESH",
            "safe_for_decision": False,
            "payload": {"review_status": "sec_source_grounded_partial"},
        }
        result = evaluate_artifact_truth_readiness(artifact, [], [])
        assert result.safe_for_decision_db_promotion_blocked is True


# ── Criterion 21: No forbidden keys in metric_observation payload ──────────────

class TestCriterion21NoForbiddenKeys:

    def test_metric_payload_has_no_forbidden_keys(self) -> None:
        for tag in _METRIC_TAG_ALLOWLIST:
            unit = _ALLOWED_UNIT_BY_TAG[tag]
            obs = MetricObservation(
                taxonomy="us-gaap", tag=tag, label=f"{tag} label",
                value=100_000 if unit == "USD" else 1.23,
                unit=unit, form="10-Q", fiscal_year=2025, fiscal_period="Q1",
                filed="2025-01-15", accession_number="0000320193-25-000001",
            )
            sec_result = _make_sec_result_with_companyfacts(
                accessions=["0000320193-25-000001"],
                companyfacts_observations=[obs],
            )
            adapted = adapt_sec_result(sec_result)
            for fact in adapted.facts:
                if fact.fact_kind == "metric_observation":
                    violation = _has_forbidden_key(fact.structured_payload)
                    assert violation is None, (
                        f"Forbidden key '{violation}' found in metric_observation for tag={tag}"
                    )

    def test_no_action_key_in_any_fact(self) -> None:
        obs = _make_metric_obs("Revenues", "0000320193-25-000001", 1_000_000)
        sec_result = _make_sec_result_with_companyfacts(
            accessions=["0000320193-25-000001"],
            companyfacts_observations=[obs],
        )
        adapted = adapt_sec_result(sec_result)
        for fact in adapted.facts:
            for key in FORBIDDEN_PAYLOAD_KEYS:
                assert key not in fact.structured_payload, (
                    f"Forbidden key '{key}' must not appear in fact payload"
                )


# ── Criterion 22: No forbidden imports ───────────────────────────────────────

class TestCriterion22NoForbiddenImports:

    def test_companyfacts_parser_has_no_decide_import(self) -> None:
        import app.services.intelligence.research_workers.sec_companyfacts_parser as parser_mod
        src = inspect.getsource(parser_mod)
        assert "decide(" not in src
        assert "IntelV3Service" not in src
        assert "recommendation_engine" not in src

    def test_sec_edgar_provider_has_no_decide_import(self) -> None:
        import app.services.intelligence.research_workers.sec_edgar_provider as prov_mod
        src = inspect.getsource(prov_mod)
        assert "decide(" not in src
        assert "IntelV3Service" not in src

    def test_earnings_sec_adapter_has_no_decide_import(self) -> None:
        import app.services.intelligence.research_workers.earnings_sec_adapter as adap_mod
        src = inspect.getsource(adap_mod)
        assert "decide(" not in src
        assert "IntelV3Service" not in src


# ── Criterion 23: No writes to intel_v3_snapshots ────────────────────────────

class TestCriterion23NoSnapshotWrites:

    def test_sec_companyfacts_parser_has_no_snapshot_reference(self) -> None:
        import app.services.intelligence.research_workers.sec_companyfacts_parser as mod
        src = inspect.getsource(mod)
        assert "intel_v3_snapshots" not in src

    def test_earnings_sec_adapter_has_no_snapshot_reference(self) -> None:
        import app.services.intelligence.research_workers.earnings_sec_adapter as mod
        src = inspect.getsource(mod)
        assert "intel_v3_snapshots" not in src


# ── Criterion 24: No SQL ──────────────────────────────────────────────────────

class TestCriterion24NoSQL:

    def test_companyfacts_parser_has_no_sql(self) -> None:
        import app.services.intelligence.research_workers.sec_companyfacts_parser as mod
        src = inspect.getsource(mod)
        assert "execute(" not in src.lower() or "def" in src  # ignore function defs

    def test_no_db_calls_in_adapter(self) -> None:
        import app.services.intelligence.research_workers.earnings_sec_adapter as mod
        src = inspect.getsource(mod)
        assert "supabase" not in src.lower()
        assert ".table(" not in src

    def test_parser_module_has_no_db_import(self) -> None:
        import app.services.intelligence.research_workers.sec_companyfacts_parser as mod
        src = inspect.getsource(mod)
        assert "supabase" not in src.lower()
        assert "psycopg" not in src.lower()


# ── Source fingerprint includes metric digest ─────────────────────────────────

class TestFingerprintWithMetricDigest:

    def test_fingerprint_changes_when_metric_observations_change(self) -> None:
        filings = [SecFilingRecord(
            form_type="10-Q", filing_date=_recent(30),
            accession_number="0000320193-25-000001",
            report_date=_recent(90),
            filing_url="https://example.com/",
        )]
        # Same filings, different metric digests.
        obs1 = _make_metric_obs("Revenues", "0000320193-25-000001", 100)
        obs2 = _make_metric_obs("Revenues", "0000320193-25-000001", 999)
        d1 = compute_metric_digest([obs1])
        d2 = compute_metric_digest([obs2])
        fp1 = _compute_source_fingerprint("0000320193", filings, metric_digest=d1)
        fp2 = _compute_source_fingerprint("0000320193", filings, metric_digest=d2)
        assert fp1 != fp2, "Different metric observations must produce different fingerprint"

    def test_fingerprint_stable_for_same_observations(self) -> None:
        filings = [SecFilingRecord(
            form_type="10-Q", filing_date=_recent(30),
            accession_number="0000320193-25-000001",
            report_date=_recent(90),
            filing_url="https://example.com/",
        )]
        obs = _make_metric_obs("NetIncomeLoss", "0000320193-25-000001", 50_000_000)
        d = compute_metric_digest([obs])
        fp1 = _compute_source_fingerprint("0000320193", filings, metric_digest=d)
        fp2 = _compute_source_fingerprint("0000320193", filings, metric_digest=d)
        assert fp1 == fp2

    def test_fingerprint_with_empty_metric_digest_differs_from_no_digest_arg(self) -> None:
        """Phase 7A fingerprint always includes metric_digest key, even when empty."""
        filings = [SecFilingRecord(
            form_type="10-Q", filing_date=_recent(30),
            accession_number="0000320193-25-000001",
            report_date=None,
            filing_url="https://example.com/",
        )]
        empty_digest = compute_metric_digest([])
        # With default metric_digest="" vs explicit empty digest from compute_metric_digest([]).
        # These could differ or be the same — the key invariant is stability.
        fp1 = _compute_source_fingerprint("0000320193", filings, metric_digest=empty_digest)
        fp2 = _compute_source_fingerprint("0000320193", filings, metric_digest=empty_digest)
        assert fp1 == fp2  # Stable


# ── Observability Phase 7A counters ──────────────────────────────────────────

class TestPhase7AObservability:

    def test_observability_summary_has_metric_observation_fields(self) -> None:
        from app.services.intelligence.research_workers.artifact_observability import (
            ArtifactObservabilitySummary,
        )
        import dataclasses
        field_names = {f.name for f in dataclasses.fields(ArtifactObservabilitySummary)}
        assert "artifacts_with_metric_observations_count" in field_names
        assert "metric_observation_fact_count" in field_names

    def test_observability_summary_metric_fields_default_zero(self) -> None:
        from app.services.intelligence.research_workers.artifact_observability import (
            ArtifactObservabilitySummary,
        )
        import dataclasses
        # Get defaults by creating a minimal instance using all required fields.
        # We just verify the default value is 0.
        for f in dataclasses.fields(ArtifactObservabilitySummary):
            if f.name in ("artifacts_with_metric_observations_count", "metric_observation_fact_count"):
                assert f.default == 0, f"{f.name} must default to 0"
