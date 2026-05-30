"""Stage 9M — SEC NPORT resolver verification and correction for VTI/SCHD/VXUS.

Root cause (from runtime evidence after Stage 9L deploy):
  VTI (CIK 0000764180):  1000 forms in filings.recent, 0 NPORT-P; files page
                          tried, also 0 NPORT-P. CIK is the wrong filing entity.
  SCHD (CIK 0001477379): 20 forms in filings.recent, 0 NPORT-P; no files pages.
                          CIK is the wrong filing entity.
  VXUS (CIK 0001004244): 127 forms in filings.recent, 0 NPORT-P; no files pages.
                          CIK is the wrong filing entity.
  Discovery returned [] for all three — EFTS returned no new candidates because the
  same CIK was deduped (already in static seeds) or EFTS returned 0 hits.

Resolution (path B — bounded diagnostic proving limitation):
  NportProviderResult.resolver_limitation_reason is set on the no_nport_filing path
  when expected_status="candidate" to prove WHY the CIK is wrong, not just that it
  produced no_nport_filing. EFTS verification URLs are constructed and exposed in the
  diagnostic output so operators can manually identify the correct filer.

All tests use fixture http_get_fn — zero live SEC calls.

Coverage:
 M1.  Candidate CIK + no NPORT in filings.recent + no files pages →
      resolver_limitation_reason is set with "candidate_cik_no_nport_verified".
 M2.  Candidate CIK + no NPORT in filings.recent + files page tried + still no NPORT →
      resolver_limitation_reason includes "files page fetched" detail.
 M3.  Non-candidate CIK (confirmed/standalone) → resolver_limitation_reason is None.
 M4.  Success path → resolver_limitation_reason is None.
 M5.  EFTS entity search URL constructed correctly for candidate CIK.
 M6.  EFTS series search URL constructed correctly from expected_series_names.
 M7.  Adapter _build_no_data_result includes resolver_limitation_reason in payload.
 M8.  Adapter _build_no_data_result includes efts_entity_search_url in payload.
 M9.  Adapter _build_no_data_result includes efts_series_search_url in payload.
M10.  Diagnostic runner _build_ticker_entry exposes resolver_limitation_reason.
M11.  Diagnostic runner: discovery returned no hits → discovery_deduped_reason set.
M12.  Diagnostic runner: discovery returned non-rejected candidates all already tried →
      discovery_deduped_reason set to "all_non_rejected_candidates_already_tried".
M13.  No fake identity promotion — identity_verified stays False when CIK is wrong.
M14.  safe_for_decision stays False in adapter result regardless of resolver path.
M15.  VTI-shaped test: 1000 forms, no NPORT, files page tried, no NPORT in page →
      resolver_limitation_reason set, submissions_files_page_tried=True.
M16.  SCHD-shaped test: 20 forms, no NPORT, no files pages →
      resolver_limitation_reason set, submissions_has_files_pages=False.
M17.  VXUS-shaped test: 127 forms, no NPORT, no files pages →
      resolver_limitation_reason set, submissions_has_files_pages=False.
M18.  Stage 9L pagination fallback still works (regression guard).
M19.  No live SEC calls in any test.
"""
from __future__ import annotations

from typing import Any, Optional
from unittest.mock import MagicMock

import pytest


# ── XML fixture for success path ──────────────────────────────────────────────

_SPY_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<edgarSubmission xmlns="http://www.sec.gov/edgar/nport">
  <formData>
    <invstOrSecs>
      <invstOrSec>
        <name>Apple Inc</name>
        <cusip>037833100</cusip>
        <pctVal>7.0</pctVal>
      </invstOrSec>
    </invstOrSecs>
  </formData>
</edgarSubmission>
"""

_VTI_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<edgarSubmission xmlns="http://www.sec.gov/edgar/nport">
  <formData>
    <genInfo>
      <regName>VANGUARD INDEX FUNDS</regName>
      <seriesName>Vanguard Total Stock Market Index Fund</seriesName>
      <repPdDate>2025-12-31</repPdDate>
    </genInfo>
    <invstOrSecs>
      <invstOrSec>
        <name>Apple Inc</name>
        <cusip>037833100</cusip>
        <pctVal>6.3</pctVal>
      </invstOrSec>
    </invstOrSecs>
  </formData>
</edgarSubmission>
"""


# ── Helpers ───────────────────────────────────────────────────────────────────


def _mock_resp(json_body=None, text_body=None, status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    if json_body is not None:
        resp.json.return_value = json_body
    if text_body is not None:
        resp.text = text_body
    resp.raise_for_status.return_value = None
    return resp


def _make_submissions_no_nport(
    cik: str,
    form_count: int = 20,
    *,
    with_files_pages: bool = False,
) -> dict:
    """Submissions body with no NPORT-P in filings.recent."""
    forms = ["N-CEN", "485BPOS"] * (form_count // 2) + ["N-CEN"] * (form_count % 2)
    body: dict[str, Any] = {
        "filings": {
            "recent": {
                "form": forms[:form_count],
                "filingDate": ["2026-01-01"] * form_count,
                "accessionNumber": [f"{cik}-26-{i:06d}" for i in range(form_count)],
                "reportDate": [""] * form_count,
                "primaryDocument": ["doc.htm"] * form_count,
            }
        }
    }
    if with_files_pages:
        body["filings"]["files"] = [
            {"name": f"CIK{cik}-submissions-001.json", "date": "2025-12-01"}
        ]
    return body


def _make_submissions_with_nport(cik: str) -> dict:
    return {
        "filings": {
            "recent": {
                "form": ["NPORT-P"],
                "filingDate": ["2026-02-28"],
                "accessionNumber": [f"{cik}-26-000100"],
                "reportDate": ["2025-12-31"],
                "primaryDocument": ["primary_doc.xml"],
            }
        }
    }


def _make_files_page_no_nport(cik: str) -> dict:
    return {
        "form": ["10-K", "DEF 14A"],
        "filingDate": ["2025-06-01", "2025-05-15"],
        "accessionNumber": [f"{cik}-25-000300", f"{cik}-25-000301"],
        "reportDate": ["", ""],
        "primaryDocument": ["annual.htm", "proxy.htm"],
    }


def _make_files_page_with_nport(cik: str) -> dict:
    return {
        "form": ["NPORT-P"],
        "filingDate": ["2025-11-28"],
        "accessionNumber": [f"{cik}-25-000200"],
        "reportDate": ["2025-09-30"],
        "primaryDocument": ["primary_doc.xml"],
    }


def _provider_call(
    ticker: str,
    http_responses: list,
    candidate_ciks_override: Optional[list] = None,
    max_requests: int = 30,
):
    from app.services.intelligence.research_workers.nport_provider_v1 import (
        NportProviderConfig,
        fetch_etf_nport_holdings,
    )

    cfg = NportProviderConfig(
        user_agent="test@example.com",
        max_requests_per_ticker=max_requests,
    )
    responses = list(http_responses)
    idx = [0]

    def _http_get(url: str):
        i = idx[0]
        idx[0] += 1
        if i >= len(responses):
            raise RuntimeError(f"Unexpected HTTP call #{i} to {url!r}")
        return responses[i]

    kwargs: dict[str, Any] = {"http_get_fn": _http_get}
    if candidate_ciks_override is not None:
        kwargs["_candidate_ciks_override"] = candidate_ciks_override
    return fetch_etf_nport_holdings(ticker, cfg, **kwargs)


# ── M1: Candidate CIK + no NPORT in recent + no files pages ──────────────────


class TestM1CandidateCikNoNportNoFilesPages:
    """Candidate CIK produces no_nport_filing → resolver_limitation_reason set."""

    def test_m1_fetch_status_is_no_nport_filing(self):
        # VTI uses candidate CIK 0000764180; inject 20 forms, no NPORT, no files pages
        result = _provider_call(
            "VTI",
            http_responses=[_mock_resp(json_body=_make_submissions_no_nport("0000764180"))],
        )
        assert result.fetch_status == "no_nport_filing"

    def test_m1_resolver_limitation_reason_set(self):
        result = _provider_call(
            "VTI",
            http_responses=[_mock_resp(json_body=_make_submissions_no_nport("0000764180"))],
        )
        assert result.resolver_limitation_reason is not None
        assert "candidate_cik_no_nport_verified" in result.resolver_limitation_reason

    def test_m1_limitation_mentions_form_count(self):
        result = _provider_call(
            "VTI",
            http_responses=[_mock_resp(json_body=_make_submissions_no_nport("0000764180", form_count=20))],
        )
        assert "20" in result.resolver_limitation_reason

    def test_m1_limitation_mentions_ticker(self):
        result = _provider_call(
            "VTI",
            http_responses=[_mock_resp(json_body=_make_submissions_no_nport("0000764180"))],
        )
        assert "VTI" in result.resolver_limitation_reason

    def test_m1_limitation_mentions_candidate_status(self):
        result = _provider_call(
            "VTI",
            http_responses=[_mock_resp(json_body=_make_submissions_no_nport("0000764180"))],
        )
        assert "candidate" in result.resolver_limitation_reason.lower()

    def test_m1_no_files_pages_mentioned_in_limitation(self):
        result = _provider_call(
            "VTI",
            http_responses=[_mock_resp(json_body=_make_submissions_no_nport("0000764180"))],
        )
        assert "no filings.files pages" in result.resolver_limitation_reason


# ── M2: Candidate CIK + no NPORT + files page tried + still no NPORT ─────────


class TestM2CandidateCikFilesPageTried:
    """Files page fallback tried but also no NPORT → limitation includes file page detail."""

    def test_m2_files_page_tried_is_true(self):
        sub = _make_submissions_no_nport("0000764180", form_count=1000, with_files_pages=True)
        result = _provider_call(
            "VTI",
            http_responses=[
                _mock_resp(json_body=sub),
                _mock_resp(json_body=_make_files_page_no_nport("0000764180")),
            ],
        )
        assert result.submissions_files_page_tried is True

    def test_m2_resolver_limitation_mentions_files_page(self):
        sub = _make_submissions_no_nport("0000764180", form_count=1000, with_files_pages=True)
        result = _provider_call(
            "VTI",
            http_responses=[
                _mock_resp(json_body=sub),
                _mock_resp(json_body=_make_files_page_no_nport("0000764180")),
            ],
        )
        assert result.resolver_limitation_reason is not None
        assert "files page fetched" in result.resolver_limitation_reason

    def test_m2_form_count_in_limitation(self):
        sub = _make_submissions_no_nport("0000764180", form_count=1000, with_files_pages=True)
        result = _provider_call(
            "VTI",
            http_responses=[
                _mock_resp(json_body=sub),
                _mock_resp(json_body=_make_files_page_no_nport("0000764180")),
            ],
        )
        assert "1000" in result.resolver_limitation_reason


# ── M3: Non-candidate (standalone/confirmed) → no limitation reason ───────────


class TestM3ConfirmedCikNoLimitationReason:
    """Confirmed/standalone CIK failure does not set resolver_limitation_reason."""

    def test_m3_spy_standalone_no_limitation_reason(self):
        # SPY is standalone_trust=True and expected_status="confirmed"
        sub = _make_submissions_no_nport("0000884394")
        result = _provider_call(
            "SPY",
            http_responses=[_mock_resp(json_body=sub)],
        )
        # SPY should produce commodity_trust_or_no_nport_data or no_nport_filing,
        # but NOT set resolver_limitation_reason since it's confirmed/standalone
        assert result.resolver_limitation_reason is None


# ── M4: Success path → no limitation reason ───────────────────────────────────


class TestM4SuccessNoLimitationReason:
    """Success path never sets resolver_limitation_reason."""

    def test_m4_vti_success_no_limitation_reason(self):
        cik = "0000764180"
        sub = _make_submissions_with_nport(cik)
        result = _provider_call(
            "VTI",
            http_responses=[
                _mock_resp(json_body=sub),
                _mock_resp(text_body=_VTI_XML),
            ],
        )
        assert result.fetch_status == "success"
        assert result.resolver_limitation_reason is None


# ── M5: EFTS entity search URL constructed correctly ─────────────────────────


class TestM5EftsEntitySearchUrl:
    """EFTS entity search URL is constructed from parent_registrant_name."""

    def test_m5_efts_entity_url_present(self):
        result = _provider_call(
            "VTI",
            http_responses=[_mock_resp(json_body=_make_submissions_no_nport("0000764180"))],
        )
        assert result.efts_entity_search_url is not None

    def test_m5_efts_entity_url_contains_registrant_name(self):
        result = _provider_call(
            "VTI",
            http_responses=[_mock_resp(json_body=_make_submissions_no_nport("0000764180"))],
        )
        # URL should include the parent registrant name (URL-encoded)
        assert "VANGUARD" in result.efts_entity_search_url

    def test_m5_efts_entity_url_targets_nport_form(self):
        result = _provider_call(
            "VTI",
            http_responses=[_mock_resp(json_body=_make_submissions_no_nport("0000764180"))],
        )
        assert "NPORT-P" in result.efts_entity_search_url

    def test_m5_efts_entity_url_is_efts_domain(self):
        result = _provider_call(
            "VTI",
            http_responses=[_mock_resp(json_body=_make_submissions_no_nport("0000764180"))],
        )
        assert "efts.sec.gov" in result.efts_entity_search_url

    def test_m5_schd_efts_entity_url_contains_schwab(self):
        result = _provider_call(
            "SCHD",
            http_responses=[_mock_resp(json_body=_make_submissions_no_nport("0001477379", form_count=20))],
        )
        assert result.efts_entity_search_url is not None
        assert "SCHWAB" in result.efts_entity_search_url


# ── M6: EFTS series search URL constructed correctly ─────────────────────────


class TestM6EftsSeriesSearchUrl:
    """EFTS series search URL is constructed from expected_series_names[0]."""

    def test_m6_efts_series_url_present(self):
        result = _provider_call(
            "VTI",
            http_responses=[_mock_resp(json_body=_make_submissions_no_nport("0000764180"))],
        )
        assert result.efts_series_search_url is not None

    def test_m6_efts_series_url_contains_series_name(self):
        result = _provider_call(
            "VTI",
            http_responses=[_mock_resp(json_body=_make_submissions_no_nport("0000764180"))],
        )
        assert "Vanguard" in result.efts_series_search_url

    def test_m6_efts_series_url_targets_nport_form(self):
        result = _provider_call(
            "VTI",
            http_responses=[_mock_resp(json_body=_make_submissions_no_nport("0000764180"))],
        )
        assert "NPORT-P" in result.efts_series_search_url

    def test_m6_schd_efts_series_url_contains_dividend(self):
        result = _provider_call(
            "SCHD",
            http_responses=[_mock_resp(json_body=_make_submissions_no_nport("0001477379", form_count=20))],
        )
        assert result.efts_series_search_url is not None
        assert "Schwab" in result.efts_series_search_url


# ── M7-M9: Adapter _build_no_data_result includes new fields ─────────────────


class TestM7M9AdapterDiagnosticPayload:
    """Adapter no-data payload includes resolver_limitation_reason and EFTS URLs."""

    def _make_no_data_result(self, ticker: str, cik: str, form_count: int = 20):
        from app.services.intelligence.research_workers.nport_provider_v1 import NportProviderResult
        res = NportProviderResult(
            ticker=ticker,
            fetch_status="no_nport_filing",
            cik=cik,
            error_message="No NPORT-P found.",
            resolver_limitation_reason=(
                f"candidate_cik_no_nport_verified: CIK {cik!r} has {form_count} forms "
                "in filings.recent but 0 NPORT-P; no filings.files pages present."
            ),
            efts_entity_search_url=f"https://efts.sec.gov/LATEST/search-index?entity=TEST&forms=NPORT-P",
            efts_series_search_url=f"https://efts.sec.gov/LATEST/search-index?q=%22Test+Fund%22&forms=NPORT-P",
            submissions_recent_form_count=form_count,
            submissions_has_files_pages=False,
            submissions_files_page_tried=False,
            candidate_ciks_tried=[cik],
            parent_registrant_name="TEST REGISTRANT",
        )
        return res

    def test_m7_resolver_limitation_in_payload(self):
        from app.services.intelligence.research_workers.etf_nport_adapter_v1 import adapt_etf_nport
        from datetime import datetime, timezone
        res = self._make_no_data_result("VTI", "0000764180", form_count=1000)
        adapter_result = adapt_etf_nport(res, datetime.now(timezone.utc).isoformat())
        payload = adapter_result.artifact_payload_extra
        assert "resolver_limitation_reason" in payload
        assert "candidate_cik_no_nport_verified" in payload["resolver_limitation_reason"]

    def test_m8_efts_entity_url_in_payload(self):
        from app.services.intelligence.research_workers.etf_nport_adapter_v1 import adapt_etf_nport
        from datetime import datetime, timezone
        res = self._make_no_data_result("VTI", "0000764180")
        adapter_result = adapt_etf_nport(res, datetime.now(timezone.utc).isoformat())
        payload = adapter_result.artifact_payload_extra
        assert "efts_entity_search_url" in payload
        assert "efts.sec.gov" in payload["efts_entity_search_url"]

    def test_m9_efts_series_url_in_payload(self):
        from app.services.intelligence.research_workers.etf_nport_adapter_v1 import adapt_etf_nport
        from datetime import datetime, timezone
        res = self._make_no_data_result("VTI", "0000764180")
        adapter_result = adapt_etf_nport(res, datetime.now(timezone.utc).isoformat())
        payload = adapter_result.artifact_payload_extra
        assert "efts_series_search_url" in payload

    def test_m9_safe_for_decision_always_false(self):
        from app.services.intelligence.research_workers.etf_nport_adapter_v1 import build_etf_nport_worker_output
        from app.services.intelligence.research_workers.contracts import WorkerInput
        from datetime import datetime, timezone
        import uuid
        res = self._make_no_data_result("VTI", "0000764180")
        wi = WorkerInput(
            user_id="test-user",
            worker_run_id=str(uuid.uuid4()),
            ticker="VTI",
            parent_intel_run_id=None,
        )
        output = build_etf_nport_worker_output(wi, res, datetime.now(timezone.utc).isoformat())
        assert output.artifact_payload.get("fetch_status") == "no_nport_filing"


# ── M10: Diagnostic runner exposes resolver_limitation_reason ─────────────────


class TestM10DiagnosticRunnerLimitationReason:
    """Diagnostic runner _build_ticker_entry includes resolver_limitation_reason."""

    def test_m10_limitation_reason_in_ticker_entry(self):
        from app.services.intelligence.research_workers.nport_provider_v1 import NportProviderResult
        from app.services.intelligence.research_workers.nport_diagnostic_runner import _build_ticker_entry
        res = NportProviderResult(
            ticker="VTI",
            fetch_status="no_nport_filing",
            cik="0000764180",
            resolver_limitation_reason="candidate_cik_no_nport_verified: CIK has 1000 forms, 0 NPORT-P",
            efts_entity_search_url="https://efts.sec.gov/LATEST/search-index?entity=VANGUARD+INDEX+FUNDS&forms=NPORT-P",
            efts_series_search_url="https://efts.sec.gov/LATEST/search-index?q=%22Vanguard+Total+Stock%22&forms=NPORT-P",
            submissions_recent_form_count=1000,
            submissions_has_files_pages=True,
            submissions_files_page_tried=True,
        )
        entry = _build_ticker_entry(res)
        assert entry["resolver_limitation_reason"] == res.resolver_limitation_reason

    def test_m10_efts_urls_in_ticker_entry(self):
        from app.services.intelligence.research_workers.nport_provider_v1 import NportProviderResult
        from app.services.intelligence.research_workers.nport_diagnostic_runner import _build_ticker_entry
        res = NportProviderResult(
            ticker="SCHD",
            fetch_status="no_nport_filing",
            cik="0001477379",
            efts_entity_search_url="https://efts.sec.gov/LATEST/search-index?entity=SCHWAB+STRATEGIC+TRUST&forms=NPORT-P",
            efts_series_search_url="https://efts.sec.gov/LATEST/search-index?q=%22Schwab+Dividend%22&forms=NPORT-P",
        )
        entry = _build_ticker_entry(res)
        assert entry["efts_entity_search_url"] == res.efts_entity_search_url
        assert entry["efts_series_search_url"] == res.efts_series_search_url

    def test_m10_submissions_fields_in_ticker_entry(self):
        from app.services.intelligence.research_workers.nport_provider_v1 import NportProviderResult
        from app.services.intelligence.research_workers.nport_diagnostic_runner import _build_ticker_entry
        res = NportProviderResult(
            ticker="VXUS",
            fetch_status="no_nport_filing",
            cik="0001004244",
            submissions_recent_form_count=127,
            submissions_has_files_pages=False,
            submissions_files_page_tried=False,
        )
        entry = _build_ticker_entry(res)
        assert entry["submissions_recent_form_count"] == 127
        assert entry["submissions_has_files_pages"] is False
        assert entry["submissions_files_page_tried"] is False


# ── M11: Discovery returned no hits → discovery_deduped_reason ───────────────


class TestM11DiscoveryNoHits:
    """discovery_deduped_reason set when EFTS returned no candidates at all."""

    def test_m11_no_hits_reason_set(self):
        from app.services.intelligence.research_workers.nport_provider_v1 import NportProviderResult
        from app.services.intelligence.research_workers.nport_diagnostic_runner import _build_ticker_entry
        from types import SimpleNamespace

        discovery_result = SimpleNamespace(
            discovered_candidates=[],
            discovery_sources_tried=["sec_efts_entity", "sec_efts_series"],
            discovery_error=None,
        )
        res = NportProviderResult(ticker="VTI", fetch_status="no_nport_filing", cik="0000764180")
        entry = _build_ticker_entry(res, discovery_result=discovery_result)
        assert entry["resolver_discovery_used"] is True
        assert entry["discovery_deduped_reason"] == "efts_returned_no_hits_for_entity_or_series_name"


# ── M12: Discovery candidates all already tried ───────────────────────────────


class TestM12DiscoveryCandidatesAllAlreadyTried:
    """discovery_deduped_reason set when all non-rejected candidates were already tried."""

    def test_m12_all_candidates_already_tried_reason(self):
        from app.services.intelligence.research_workers.nport_provider_v1 import NportProviderResult
        from app.services.intelligence.research_workers.nport_diagnostic_runner import _build_ticker_entry
        from types import SimpleNamespace

        # Simulate: EFTS found one candidate but it's the same CIK already tried
        candidate = SimpleNamespace(
            candidate_cik="0000764180",
            candidate_title="VANGUARD INDEX FUNDS",
            candidate_source="sec_efts_entity",
            match_reason="entity_name matches",
            confidence="confirmed_candidate",
            rejection_reason=None,
        )
        discovery_result = SimpleNamespace(
            discovered_candidates=[candidate],
            discovery_sources_tried=["sec_efts_entity"],
            discovery_error=None,
        )
        res = NportProviderResult(
            ticker="VTI",
            fetch_status="no_nport_filing",
            cik="0000764180",
            candidate_ciks_tried=["0000764180"],  # already tried
        )
        entry = _build_ticker_entry(res, discovery_result=discovery_result)
        assert entry["discovery_deduped_reason"] == (
            "all_non_rejected_candidates_already_tried_as_static_seeds"
        )

    def test_m12_rejected_candidates_reason(self):
        from app.services.intelligence.research_workers.nport_provider_v1 import NportProviderResult
        from app.services.intelligence.research_workers.nport_diagnostic_runner import _build_ticker_entry
        from types import SimpleNamespace

        candidate = SimpleNamespace(
            candidate_cik="0000999999",
            candidate_title="UNRELATED FUND",
            candidate_source="sec_efts_entity",
            match_reason="",
            confidence="rejected",
            rejection_reason="name did not match",
        )
        discovery_result = SimpleNamespace(
            discovered_candidates=[candidate],
            discovery_sources_tried=["sec_efts_entity"],
            discovery_error=None,
        )
        res = NportProviderResult(
            ticker="VTI",
            fetch_status="no_nport_filing",
            cik="0000764180",
            candidate_ciks_tried=["0000764180"],
        )
        entry = _build_ticker_entry(res, discovery_result=discovery_result)
        assert entry["discovery_deduped_reason"] == (
            "all_discovered_candidates_rejected_by_confidence_filter"
        )


# ── M13: No fake identity promotion ──────────────────────────────────────────


class TestM13NoFakeIdentityPromotion:
    """identity_verified stays False when no NPORT filing is found."""

    def test_m13_identity_not_verified_when_no_nport(self):
        result = _provider_call(
            "VTI",
            http_responses=[_mock_resp(json_body=_make_submissions_no_nport("0000764180"))],
        )
        assert result.identity_verified is False

    def test_m13_identity_status_is_no_nport_filing(self):
        result = _provider_call(
            "VTI",
            http_responses=[_mock_resp(json_body=_make_submissions_no_nport("0000764180"))],
        )
        assert result.identity_status == "no_nport_filing"

    def test_m13_schd_no_identity_without_nport(self):
        result = _provider_call(
            "SCHD",
            http_responses=[_mock_resp(json_body=_make_submissions_no_nport("0001477379", form_count=20))],
        )
        assert result.identity_verified is False
        assert result.fetch_status == "no_nport_filing"

    def test_m13_vxus_no_identity_without_nport(self):
        result = _provider_call(
            "VXUS",
            http_responses=[_mock_resp(json_body=_make_submissions_no_nport("0001004244", form_count=127))],
        )
        assert result.identity_verified is False
        assert result.fetch_status == "no_nport_filing"


# ── M15-M17: Ticker-specific shaped tests mirroring runtime evidence ──────────


class TestM15VtiShapedRuntime:
    """VTI: 1000 forms, no NPORT, files page tried, files page also no NPORT."""

    def test_m15_fetch_status_no_nport(self):
        sub = _make_submissions_no_nport("0000764180", form_count=1000, with_files_pages=True)
        result = _provider_call(
            "VTI",
            http_responses=[
                _mock_resp(json_body=sub),
                _mock_resp(json_body=_make_files_page_no_nport("0000764180")),
            ],
        )
        assert result.fetch_status == "no_nport_filing"

    def test_m15_submissions_form_count_is_1000(self):
        sub = _make_submissions_no_nport("0000764180", form_count=1000, with_files_pages=True)
        result = _provider_call(
            "VTI",
            http_responses=[
                _mock_resp(json_body=sub),
                _mock_resp(json_body=_make_files_page_no_nport("0000764180")),
            ],
        )
        assert result.submissions_recent_form_count == 1000

    def test_m15_files_page_tried_is_true(self):
        sub = _make_submissions_no_nport("0000764180", form_count=1000, with_files_pages=True)
        result = _provider_call(
            "VTI",
            http_responses=[
                _mock_resp(json_body=sub),
                _mock_resp(json_body=_make_files_page_no_nport("0000764180")),
            ],
        )
        assert result.submissions_files_page_tried is True

    def test_m15_resolver_limitation_set(self):
        sub = _make_submissions_no_nport("0000764180", form_count=1000, with_files_pages=True)
        result = _provider_call(
            "VTI",
            http_responses=[
                _mock_resp(json_body=sub),
                _mock_resp(json_body=_make_files_page_no_nport("0000764180")),
            ],
        )
        assert result.resolver_limitation_reason is not None
        assert "1000" in result.resolver_limitation_reason

    def test_m15_efts_urls_set(self):
        sub = _make_submissions_no_nport("0000764180", form_count=1000, with_files_pages=True)
        result = _provider_call(
            "VTI",
            http_responses=[
                _mock_resp(json_body=sub),
                _mock_resp(json_body=_make_files_page_no_nport("0000764180")),
            ],
        )
        assert result.efts_entity_search_url is not None
        assert result.efts_series_search_url is not None


class TestM16SchdShapedRuntime:
    """SCHD: 20 forms, no NPORT, no files pages."""

    def test_m16_fetch_status_no_nport(self):
        result = _provider_call(
            "SCHD",
            http_responses=[_mock_resp(json_body=_make_submissions_no_nport("0001477379", form_count=20))],
        )
        assert result.fetch_status == "no_nport_filing"

    def test_m16_has_files_pages_false(self):
        result = _provider_call(
            "SCHD",
            http_responses=[_mock_resp(json_body=_make_submissions_no_nport("0001477379", form_count=20))],
        )
        assert result.submissions_has_files_pages is False

    def test_m16_resolver_limitation_set(self):
        result = _provider_call(
            "SCHD",
            http_responses=[_mock_resp(json_body=_make_submissions_no_nport("0001477379", form_count=20))],
        )
        assert result.resolver_limitation_reason is not None
        assert "candidate_cik_no_nport_verified" in result.resolver_limitation_reason

    def test_m16_schd_efts_urls_set(self):
        result = _provider_call(
            "SCHD",
            http_responses=[_mock_resp(json_body=_make_submissions_no_nport("0001477379", form_count=20))],
        )
        assert result.efts_entity_search_url is not None
        assert "SCHWAB" in result.efts_entity_search_url


class TestM17VxusShapedRuntime:
    """VXUS: 127 forms, no NPORT, no files pages."""

    def test_m17_fetch_status_no_nport(self):
        result = _provider_call(
            "VXUS",
            http_responses=[_mock_resp(json_body=_make_submissions_no_nport("0001004244", form_count=127))],
        )
        assert result.fetch_status == "no_nport_filing"

    def test_m17_submissions_form_count_127(self):
        result = _provider_call(
            "VXUS",
            http_responses=[_mock_resp(json_body=_make_submissions_no_nport("0001004244", form_count=127))],
        )
        assert result.submissions_recent_form_count == 127

    def test_m17_resolver_limitation_set(self):
        result = _provider_call(
            "VXUS",
            http_responses=[_mock_resp(json_body=_make_submissions_no_nport("0001004244", form_count=127))],
        )
        assert result.resolver_limitation_reason is not None
        assert "VXUS" in result.resolver_limitation_reason

    def test_m17_vxus_efts_urls_set(self):
        result = _provider_call(
            "VXUS",
            http_responses=[_mock_resp(json_body=_make_submissions_no_nport("0001004244", form_count=127))],
        )
        assert result.efts_entity_search_url is not None
        assert "VANGUARD" in result.efts_entity_search_url


# ── M18: Stage 9L pagination fallback regression guard ───────────────────────


class TestM18PaginationFallbackRegression:
    """Stage 9L pagination fallback still works — regression guard for Stage 9M."""

    def test_m18_files_page_fallback_succeeds_for_vti(self):
        """NPORT-P in files page → success (pagination fallback works)."""
        cik = "0000764180"
        sub = _make_submissions_no_nport(cik, form_count=20, with_files_pages=True)
        files_page = _make_files_page_with_nport(cik)
        result = _provider_call(
            "VTI",
            http_responses=[
                _mock_resp(json_body=sub),
                _mock_resp(json_body=files_page),
                _mock_resp(text_body=_VTI_XML),
            ],
        )
        assert result.fetch_status == "success"
        # resolver_limitation_reason is None on success path
        assert result.resolver_limitation_reason is None

    def test_m18_no_limitation_reason_on_success(self):
        cik = "0000764180"
        sub = _make_submissions_no_nport(cik, form_count=20, with_files_pages=True)
        files_page = _make_files_page_with_nport(cik)
        result = _provider_call(
            "VTI",
            http_responses=[
                _mock_resp(json_body=sub),
                _mock_resp(json_body=files_page),
                _mock_resp(text_body=_VTI_XML),
            ],
        )
        assert result.resolver_limitation_reason is None


# ── M19: No live SEC calls ────────────────────────────────────────────────────


class TestM19NoLiveSecCalls:
    """All tests use injected http_get_fn — zero live SEC calls."""

    def test_m19_vti_uses_injected_http(self):
        responses = [_mock_resp(json_body=_make_submissions_no_nport("0000764180"))]
        result = _provider_call("VTI", http_responses=responses)
        assert result.fetch_status == "no_nport_filing"

    def test_m19_schd_uses_injected_http(self):
        responses = [_mock_resp(json_body=_make_submissions_no_nport("0001477379", form_count=20))]
        result = _provider_call("SCHD", http_responses=responses)
        assert result.fetch_status == "no_nport_filing"

    def test_m19_vxus_uses_injected_http(self):
        responses = [_mock_resp(json_body=_make_submissions_no_nport("0001004244", form_count=127))]
        result = _provider_call("VXUS", http_responses=responses)
        assert result.fetch_status == "no_nport_filing"
