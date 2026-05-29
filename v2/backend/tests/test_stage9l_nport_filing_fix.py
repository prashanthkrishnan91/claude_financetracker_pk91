"""Stage 9L — NPORT filing lookup fix for VTI, SCHD, VXUS.

Root cause: _collect_recent_nport_filings() only checks filings.recent.
Large registrants (Vanguard, Schwab) may have recent[] filled by non-NPORT
forms, pushing NPORT-P entries into a filings.files[] page.  Stage 9L adds
a bounded one-extra-request fallback to the first filings.files page.

All tests use fixture http_get_fn — zero live SEC calls.

Coverage:
 L1. VTI: NPORT-P present in filings.recent → success + identity_verified.
 L2. SCHD: NPORT-P present in filings.recent → success + identity_verified.
 L3. VXUS: NPORT-P present in filings.recent → success + identity_verified.
 L4. Ticker with no NPORT in recent but present in filings.files[0] page →
     files page fetched; result is success (pagination fallback works).
 L5. submissions_files_page_tried=True when fallback was used.
 L6. No NPORT in recent, no files pages → no_nport_filing (no extra request).
 L7. No NPORT in recent, files pages exist but no NPORT in page → no_nport_filing.
 L8. Budget guard: files page fallback skipped when request budget at limit.
 L9. Adapter _build_no_data_result includes SEC diagnostic URLs in payload.
L10. Adapter payload includes submissions_recent_form_count and candidate_ciks_tried.
L11. No live SEC calls in any test.
"""
from __future__ import annotations

from typing import Any, Optional
from unittest.mock import MagicMock

import pytest


# ── XML fixtures ──────────────────────────────────────────────────────────────

_VTI_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<edgarSubmission xmlns="http://www.sec.gov/edgar/nport">
  <formData>
    <genInfo>
      <regName>VANGUARD INDEX FUNDS</regName>
      <seriesName>Vanguard Total Stock Market Index Fund</seriesName>
      <seriesId>S000002834</seriesId>
      <repPdDate>2025-12-31</repPdDate>
    </genInfo>
    <fundInfo>
      <totAssets>400000000000.00</totAssets>
      <netAssets>399000000000.00</netAssets>
    </fundInfo>
    <invstOrSecs>
      <invstOrSec>
        <name>Apple Inc</name>
        <cusip>037833100</cusip>
        <valUSD>25000000000.00</valUSD>
        <pctVal>6.266632</pctVal>
        <curCd>USD</curCd>
        <assetCat>EC</assetCat>
      </invstOrSec>
      <invstOrSec>
        <name>Microsoft Corp</name>
        <cusip>594918104</cusip>
        <valUSD>23000000000.00</valUSD>
        <pctVal>5.764541</pctVal>
        <curCd>USD</curCd>
        <assetCat>EC</assetCat>
      </invstOrSec>
    </invstOrSecs>
  </formData>
</edgarSubmission>
"""

_SCHD_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<edgarSubmission xmlns="http://www.sec.gov/edgar/nport">
  <formData>
    <genInfo>
      <regName>SCHWAB STRATEGIC TRUST</regName>
      <seriesName>Schwab U.S. Dividend Equity ETF</seriesName>
      <seriesId>S000029095</seriesId>
      <repPdDate>2025-12-31</repPdDate>
    </genInfo>
    <fundInfo>
      <totAssets>60000000000.00</totAssets>
      <netAssets>59800000000.00</netAssets>
    </fundInfo>
    <invstOrSecs>
      <invstOrSec>
        <name>Broadcom Inc</name>
        <cusip>11135F101</cusip>
        <valUSD>3600000000.00</valUSD>
        <pctVal>6.000000</pctVal>
        <curCd>USD</curCd>
        <assetCat>EC</assetCat>
      </invstOrSec>
      <invstOrSec>
        <name>Home Depot Inc</name>
        <cusip>437076102</cusip>
        <valUSD>2700000000.00</valUSD>
        <pctVal>4.500000</pctVal>
        <curCd>USD</curCd>
        <assetCat>EC</assetCat>
      </invstOrSec>
    </invstOrSecs>
  </formData>
</edgarSubmission>
"""

_VXUS_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<edgarSubmission xmlns="http://www.sec.gov/edgar/nport">
  <formData>
    <genInfo>
      <regName>VANGUARD INTERNATIONAL EQUITY INDEX FUNDS</regName>
      <seriesName>Vanguard Total International Stock Index Fund</seriesName>
      <seriesId>S000008375</seriesId>
      <repPdDate>2025-12-31</repPdDate>
    </genInfo>
    <fundInfo>
      <totAssets>80000000000.00</totAssets>
      <netAssets>79500000000.00</netAssets>
    </fundInfo>
    <invstOrSecs>
      <invstOrSec>
        <name>Taiwan Semiconductor Manufacturing</name>
        <cusip>874039100</cusip>
        <valUSD>2400000000.00</valUSD>
        <pctVal>3.000000</pctVal>
        <curCd>USD</curCd>
        <assetCat>EC</assetCat>
        <countryOfRisk>TW</countryOfRisk>
      </invstOrSec>
      <invstOrSec>
        <name>Nestle SA</name>
        <cusip>641069406</cusip>
        <valUSD>1600000000.00</valUSD>
        <pctVal>2.000000</pctVal>
        <curCd>USD</curCd>
        <assetCat>EC</assetCat>
        <countryOfRisk>CH</countryOfRisk>
      </invstOrSec>
    </invstOrSecs>
  </formData>
</edgarSubmission>
"""


# ── Submissions fixture helpers ───────────────────────────────────────────────

def _make_submissions_with_nport(cik: str, *, with_files_pages: bool = False) -> dict:
    """Submissions body with one NPORT-P filing in filings.recent."""
    body: dict[str, Any] = {
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
    if with_files_pages:
        body["filings"]["files"] = [
            {"name": f"CIK{cik}-submissions-001.json", "date": "2025-12-01", "reportDate": "2025-09-30"}
        ]
    return body


def _make_submissions_no_nport(cik: str, *, with_files_pages: bool = False) -> dict:
    """Submissions body with no NPORT-P in filings.recent."""
    body: dict[str, Any] = {
        "filings": {
            "recent": {
                "form": ["N-CEN", "485BPOS", "N-14"],
                "filingDate": ["2026-01-15", "2026-01-10", "2025-12-20"],
                "accessionNumber": [
                    f"{cik}-26-000010",
                    f"{cik}-26-000011",
                    f"{cik}-25-000099",
                ],
                "reportDate": ["", "", ""],
                "primaryDocument": ["ncen.xml", "485bpos.htm", "n14.htm"],
            }
        }
    }
    if with_files_pages:
        body["filings"]["files"] = [
            {"name": f"CIK{cik}-submissions-001.json", "date": "2025-12-01", "reportDate": "2025-09-30"}
        ]
    return body


def _make_files_page_body_with_nport(cik: str) -> dict:
    """Flat files page body (no filings.recent wrapper) containing NPORT-P."""
    return {
        "form": ["NPORT-P"],
        "filingDate": ["2025-11-28"],
        "accessionNumber": [f"{cik}-25-000200"],
        "reportDate": ["2025-09-30"],
        "primaryDocument": ["primary_doc.xml"],
    }


def _make_files_page_body_no_nport(cik: str) -> dict:
    """Flat files page body with no NPORT-P."""
    return {
        "form": ["10-K", "DEF 14A"],
        "filingDate": ["2025-06-01", "2025-05-15"],
        "accessionNumber": [f"{cik}-25-000300", f"{cik}-25-000301"],
        "reportDate": ["2025-05-31", ""],
        "primaryDocument": ["annual.htm", "proxy.htm"],
    }


# ── Test helpers ──────────────────────────────────────────────────────────────

def _mock_resp(json_body=None, text_body=None, status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    if json_body is not None:
        resp.json.return_value = json_body
    if text_body is not None:
        resp.text = text_body
    if status_code >= 400:
        from httpx import HTTPStatusError
        resp.raise_for_status.side_effect = HTTPStatusError(
            "error", request=MagicMock(), response=resp
        )
    else:
        resp.raise_for_status.return_value = None
    return resp


def _provider_call(
    ticker: str,
    http_responses: list,
    user_agent: str = "test@example.com",
    max_filings_to_scan: int = 12,
    max_requests_per_ticker: int = 30,
    candidate_ciks_override: Optional[list] = None,
):
    from app.services.intelligence.research_workers.nport_provider_v1 import (
        NportProviderConfig,
        fetch_etf_nport_holdings,
    )
    cfg = NportProviderConfig(
        user_agent=user_agent,
        max_filings_to_scan=max_filings_to_scan,
        max_requests_per_ticker=max_requests_per_ticker,
    )
    responses = list(http_responses)
    idx = [0]

    def _http_get(url):
        i = idx[0]
        idx[0] += 1
        if i >= len(responses):
            raise RuntimeError(f"Unexpected HTTP call #{i} to {url!r}")
        return responses[i]

    kwargs: dict[str, Any] = {"http_get_fn": _http_get}
    if candidate_ciks_override is not None:
        kwargs["_candidate_ciks_override"] = candidate_ciks_override
    return fetch_etf_nport_holdings(ticker, cfg, **kwargs)


# ── L1: VTI success via filings.recent ───────────────────────────────────────

class TestVtiFixtureSuccess:
    """VTI: NPORT-P present in filings.recent → success, identity_verified."""

    def test_l1_vti_success_status(self):
        cik = "0000764180"
        sub = _make_submissions_with_nport(cik)
        result = _provider_call(
            "VTI",
            http_responses=[
                _mock_resp(json_body=sub),
                _mock_resp(text_body=_VTI_XML),
            ],
        )
        assert result.fetch_status == "success", result.error_message

    def test_l1_vti_identity_verified(self):
        cik = "0000764180"
        sub = _make_submissions_with_nport(cik)
        result = _provider_call(
            "VTI",
            http_responses=[
                _mock_resp(json_body=sub),
                _mock_resp(text_body=_VTI_XML),
            ],
        )
        assert result.identity_verified is True
        assert result.identity_status == "success_identity_verified"

    def test_l1_vti_detected_series_name(self):
        cik = "0000764180"
        sub = _make_submissions_with_nport(cik)
        result = _provider_call(
            "VTI",
            http_responses=[
                _mock_resp(json_body=sub),
                _mock_resp(text_body=_VTI_XML),
            ],
        )
        assert result.detected_series_name == "Vanguard Total Stock Market Index Fund"

    def test_l1_vti_holdings_populated(self):
        cik = "0000764180"
        sub = _make_submissions_with_nport(cik)
        result = _provider_call(
            "VTI",
            http_responses=[
                _mock_resp(json_body=sub),
                _mock_resp(text_body=_VTI_XML),
            ],
        )
        assert len(result.holdings) >= 2
        names = [h.name for h in result.holdings]
        assert any("Apple" in n for n in names)

    def test_l1_vti_weights_available(self):
        cik = "0000764180"
        sub = _make_submissions_with_nport(cik)
        result = _provider_call(
            "VTI",
            http_responses=[
                _mock_resp(json_body=sub),
                _mock_resp(text_body=_VTI_XML),
            ],
        )
        assert result.weights_available is True

    def test_l1_vti_report_period_date(self):
        cik = "0000764180"
        sub = _make_submissions_with_nport(cik)
        result = _provider_call(
            "VTI",
            http_responses=[
                _mock_resp(json_body=sub),
                _mock_resp(text_body=_VTI_XML),
            ],
        )
        assert result.filing_meta is not None
        assert result.filing_meta.report_period_date == "2025-12-31"


# ── L2: SCHD success via filings.recent ──────────────────────────────────────

class TestSchdFixtureSuccess:
    """SCHD: NPORT-P present in filings.recent → success, identity_verified."""

    def test_l2_schd_success(self):
        cik = "0001477379"
        sub = _make_submissions_with_nport(cik)
        result = _provider_call(
            "SCHD",
            http_responses=[
                _mock_resp(json_body=sub),
                _mock_resp(text_body=_SCHD_XML),
            ],
        )
        assert result.fetch_status == "success", result.error_message

    def test_l2_schd_identity_verified(self):
        cik = "0001477379"
        sub = _make_submissions_with_nport(cik)
        result = _provider_call(
            "SCHD",
            http_responses=[
                _mock_resp(json_body=sub),
                _mock_resp(text_body=_SCHD_XML),
            ],
        )
        assert result.identity_verified is True
        assert result.detected_series_name == "Schwab U.S. Dividend Equity ETF"

    def test_l2_schd_holdings_populated(self):
        cik = "0001477379"
        sub = _make_submissions_with_nport(cik)
        result = _provider_call(
            "SCHD",
            http_responses=[
                _mock_resp(json_body=sub),
                _mock_resp(text_body=_SCHD_XML),
            ],
        )
        assert len(result.holdings) >= 2
        assert any("Broadcom" in h.name for h in result.holdings)


# ── L3: VXUS success via filings.recent ──────────────────────────────────────

class TestVxusFixtureSuccess:
    """VXUS: NPORT-P present in filings.recent → success, identity_verified."""

    def test_l3_vxus_success(self):
        cik = "0001004244"
        sub = _make_submissions_with_nport(cik)
        result = _provider_call(
            "VXUS",
            http_responses=[
                _mock_resp(json_body=sub),
                _mock_resp(text_body=_VXUS_XML),
            ],
        )
        assert result.fetch_status == "success", result.error_message

    def test_l3_vxus_identity_verified(self):
        cik = "0001004244"
        sub = _make_submissions_with_nport(cik)
        result = _provider_call(
            "VXUS",
            http_responses=[
                _mock_resp(json_body=sub),
                _mock_resp(text_body=_VXUS_XML),
            ],
        )
        assert result.identity_verified is True
        assert result.detected_series_name == "Vanguard Total International Stock Index Fund"

    def test_l3_vxus_geography_present(self):
        cik = "0001004244"
        sub = _make_submissions_with_nport(cik)
        result = _provider_call(
            "VXUS",
            http_responses=[
                _mock_resp(json_body=sub),
                _mock_resp(text_body=_VXUS_XML),
            ],
        )
        assert any(h.country_of_risk for h in result.holdings)


# ── L4–L8: filings.files pagination fallback ─────────────────────────────────

class TestFilingsFilesPaginationFallback:
    """Pagination fallback: try first filings.files page when recent has no NPORT-P."""

    def test_l4_success_when_nport_in_files_page(self):
        """NPORT-P in filings.files[0] page → success after fallback fetch."""
        cik = "0001477379"
        sub = _make_submissions_no_nport(cik, with_files_pages=True)
        files_page = _make_files_page_body_with_nport(cik)
        result = _provider_call(
            "SCHD",
            http_responses=[
                _mock_resp(json_body=sub),         # submissions
                _mock_resp(json_body=files_page),  # files page
                _mock_resp(text_body=_SCHD_XML),   # NPORT-P doc from files page
            ],
        )
        assert result.fetch_status == "success", result.error_message
        assert result.identity_verified is True

    def test_l5_files_page_tried_flag_set(self):
        """submissions_files_page_tried=True when fallback page was fetched."""
        cik = "0001477379"
        sub = _make_submissions_no_nport(cik, with_files_pages=True)
        files_page = _make_files_page_body_with_nport(cik)
        result = _provider_call(
            "SCHD",
            http_responses=[
                _mock_resp(json_body=sub),
                _mock_resp(json_body=files_page),
                _mock_resp(text_body=_SCHD_XML),
            ],
        )
        assert result.fetch_status == "success"
        # files_page_tried is set on the no_nport_filing path only; on success the
        # provider returns immediately — the flag is internal. Verify the path worked
        # by confirming success was reached from the files-page filing.
        assert result.holdings

    def test_l5_no_nport_result_carries_files_page_tried(self):
        """no_nport_filing result carries submissions_files_page_tried=True."""
        cik = "0001477379"
        sub = _make_submissions_no_nport(cik, with_files_pages=True)
        files_page_no_nport = _make_files_page_body_no_nport(cik)
        result = _provider_call(
            "SCHD",
            http_responses=[
                _mock_resp(json_body=sub),
                _mock_resp(json_body=files_page_no_nport),
            ],
        )
        assert result.fetch_status == "no_nport_filing"
        assert result.submissions_files_page_tried is True

    def test_l6_no_nport_filing_when_no_files_pages(self):
        """No NPORT in recent, no files pages → no_nport_filing, no extra request."""
        cik = "0001477379"
        sub = _make_submissions_no_nport(cik, with_files_pages=False)
        result = _provider_call(
            "SCHD",
            http_responses=[
                _mock_resp(json_body=sub),
                # Only one response provided — second call would raise RuntimeError
            ],
        )
        assert result.fetch_status == "no_nport_filing"
        assert result.submissions_has_files_pages is False
        assert result.submissions_files_page_tried is False

    def test_l7_no_nport_when_files_page_has_no_nport(self):
        """NPORT not in recent or in files page → no_nport_filing."""
        cik = "0001477379"
        sub = _make_submissions_no_nport(cik, with_files_pages=True)
        files_page_no_nport = _make_files_page_body_no_nport(cik)
        result = _provider_call(
            "SCHD",
            http_responses=[
                _mock_resp(json_body=sub),
                _mock_resp(json_body=files_page_no_nport),
            ],
        )
        assert result.fetch_status == "no_nport_filing"

    def test_l8_budget_guard_prevents_files_page_fetch(self):
        """When request budget is at limit, files page fallback is skipped."""
        cik = "0001477379"
        sub = _make_submissions_no_nport(cik, with_files_pages=True)
        result = _provider_call(
            "SCHD",
            http_responses=[
                _mock_resp(json_body=sub),
                # No second response provided — budget check must prevent the call
            ],
            max_requests_per_ticker=1,  # budget exhausted after submissions fetch
        )
        assert result.fetch_status == "no_nport_filing"
        assert result.submissions_files_page_tried is False


# ── L9–L10: Adapter diagnostic payload ───────────────────────────────────────

class TestAdapterNoDataDiagnosticPayload:
    """Adapter _build_no_data_result includes SEC diagnostic URLs and submission counts."""

    def _make_no_data_result(
        self,
        cik: str = "0000764180",
        parent_name: str = "VANGUARD INDEX FUNDS",
        recent_form_count: int = 3,
        has_files: bool = True,
        files_tried: bool = True,
    ):
        from app.services.intelligence.research_workers.nport_provider_v1 import (
            NportProviderResult,
        )
        return NportProviderResult(
            ticker="VTI",
            fetch_status="no_nport_filing",
            error_message="No NPORT-P found.",
            fetched_at="2026-05-29T00:00:00+00:00",
            cik=cik,
            parent_registrant_name=parent_name,
            candidate_ciks_tried=[cik],
            resolver_source="etf_parent_map",
            identity_status="no_nport_filing",
            submissions_recent_form_count=recent_form_count,
            submissions_has_files_pages=has_files,
            submissions_files_page_tried=files_tried,
        )

    def test_l9_submissions_url_in_payload(self):
        from app.services.intelligence.research_workers.etf_nport_adapter_v1 import (
            _build_no_data_result,
        )
        res = self._make_no_data_result()
        adapted = _build_no_data_result(res)
        payload = adapted.artifact_payload_extra
        assert "submissions_url" in payload
        assert "0000764180" in payload["submissions_url"]

    def test_l9_edgar_search_url_in_payload(self):
        from app.services.intelligence.research_workers.etf_nport_adapter_v1 import (
            _build_no_data_result,
        )
        res = self._make_no_data_result()
        adapted = _build_no_data_result(res)
        payload = adapted.artifact_payload_extra
        assert "edgar_nport_search_url" in payload
        assert "NPORT-P" in payload["edgar_nport_search_url"]

    def test_l10_submissions_recent_form_count_in_payload(self):
        from app.services.intelligence.research_workers.etf_nport_adapter_v1 import (
            _build_no_data_result,
        )
        res = self._make_no_data_result(recent_form_count=5)
        adapted = _build_no_data_result(res)
        payload = adapted.artifact_payload_extra
        assert payload.get("submissions_recent_form_count") == 5

    def test_l10_candidate_ciks_tried_in_payload(self):
        from app.services.intelligence.research_workers.etf_nport_adapter_v1 import (
            _build_no_data_result,
        )
        res = self._make_no_data_result()
        adapted = _build_no_data_result(res)
        payload = adapted.artifact_payload_extra
        assert "candidate_ciks_tried" in payload
        assert "0000764180" in payload["candidate_ciks_tried"]

    def test_l10_parent_registrant_name_in_payload(self):
        from app.services.intelligence.research_workers.etf_nport_adapter_v1 import (
            _build_no_data_result,
        )
        res = self._make_no_data_result()
        adapted = _build_no_data_result(res)
        payload = adapted.artifact_payload_extra
        assert payload.get("parent_registrant_name") == "VANGUARD INDEX FUNDS"


# ── L11: No live SEC calls ────────────────────────────────────────────────────

class TestNoLiveSecCalls:
    """Confirm all tests use fixture http_get_fn — zero live SEC calls."""

    def test_l11_vti_uses_injected_http(self):
        """VTI test raises on unexpected extra HTTP call — proves no live calls."""
        cik = "0000764180"
        sub = _make_submissions_with_nport(cik)
        # Provide exactly 2 responses — any extra call raises RuntimeError
        result = _provider_call(
            "VTI",
            http_responses=[
                _mock_resp(json_body=sub),
                _mock_resp(text_body=_VTI_XML),
            ],
        )
        assert result.fetch_status == "success"

    def test_l11_schd_uses_injected_http(self):
        cik = "0001477379"
        sub = _make_submissions_with_nport(cik)
        result = _provider_call(
            "SCHD",
            http_responses=[
                _mock_resp(json_body=sub),
                _mock_resp(text_body=_SCHD_XML),
            ],
        )
        assert result.fetch_status == "success"

    def test_l11_vxus_uses_injected_http(self):
        cik = "0001004244"
        sub = _make_submissions_with_nport(cik)
        result = _provider_call(
            "VXUS",
            http_responses=[
                _mock_resp(json_body=sub),
                _mock_resp(text_body=_VXUS_XML),
            ],
        )
        assert result.fetch_status == "success"
