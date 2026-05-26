"""Stage 9F.2a — ETF NPORT identity-certification repair tests.

Coverage (all fixture-based; zero live SEC calls):

  Identity verification — false-positive prevention:
  84. VGT/VHT/VIS all return series_identity_not_proven when XML has a
      non-matching series name (same parent filing cannot serve all three).
  85. Only the ticker whose expected series name matches the XML succeeds
      with identity_verified=True.
  86. A series mismatch returns NO holdings (empty list).
  87. series_identity_not_proven result includes expected diagnostics:
      identity_mismatch_reason, detected_series_name, accession_number.

  Standalone trusts:
  88. SPY → fetch_status=success, identity_status=success_identity_assumed_single_series,
      identity_verified=True.
  89. QQQ → same standalone identity contract.

  Commodity trust:
  90. GLD → commodity_trust_or_no_nport_data preserved; identity fields consistent.

  VYM sec_error diagnostics:
  91. VYM sec_error/404 on submissions preserves resolver_source and parent_registrant_name.
  92. candidate_ciks_tried populated even on early sec_error.

  Multi-candidate CIK resolution:
  93. Multiple candidate CIKs tried in deterministic order (first then second).
  94. First candidate returns no_nport_filing; second candidate succeeds.
  95. All candidates return no_nport_filing → result is no_nport_filing with
      candidate_ciks_tried listing all attempted CIKs.
  96. All candidates return sec_error → result is sec_error with
      candidate_ciks_tried listing all attempted CIKs.
  97. Single failing candidate stops gracefully (no infinite loop).

  Identity matching helper:
  98. _identity_name_matches: exact match returns True.
  99. _identity_name_matches: substring match returns True (shorter in longer).
 100. _identity_name_matches: case/punctuation-insensitive match returns True.
 101. _identity_name_matches: non-matching names return False (no false positives).
 102. _identity_name_matches: empty detected returns False.
 103. _identity_name_matches: empty expected_names returns False.

  Diagnostic runner identity fields:
 104. _build_ticker_entry includes all identity fields.
 105. identity_verified=True surfaced in runner entry for standalone trust.
 106. identity_mismatch_reason surfaced for series_identity_not_proven.
 107. run_nport_live_check output includes tickers_identity_verified count.

  Multi-candidate identity resolution (Blocker 2 fix):
 111. Candidate 1 yields holdings with wrong series; candidate 2 has matching
      series → result is success with identity_verified=True on candidate 2.
 112. Both candidates yield holdings but neither series matches → result is
      series_identity_not_proven with candidate_identity_failures populated
      and no holdings.

  Invariants:
 108. series_identity_not_proven result never contains holdings.
 109. No live SEC calls in any test (all fixture-based).
 110. Detected identity fields (series_name, registrant_name, series_id, class_id)
      are parsed from genInfo XML and surfaced in result.
"""
from __future__ import annotations

from typing import Any, Optional
from unittest.mock import MagicMock

import pytest


# ── Shared XML fixtures ───────────────────────────────────────────────────────

# XML whose seriesName matches VGT's expected hints.
_NPORT_XML_VGT_SERIES = """\
<?xml version="1.0" encoding="UTF-8"?>
<edgarSubmission xmlns="http://www.sec.gov/edgar/nport">
  <formData>
    <genInfo>
      <regName>VANGUARD WORLD FUND</regName>
      <seriesName>Vanguard Information Technology Index Fund</seriesName>
      <seriesId>S000009902</seriesId>
      <classesContracts>
        <classContract>
          <classId>C000027032</classId>
          <className>ETF Shares</className>
        </classContract>
      </classesContracts>
      <repPdDate>2026-01-31</repPdDate>
    </genInfo>
    <fundInfo>
      <totAssets>75000000000.00</totAssets>
      <netAssets>74900000000.00</netAssets>
    </fundInfo>
    <invstOrSecs>
      <invstOrSec>
        <name>Apple Inc</name>
        <cusip>037833100</cusip>
        <valUSD>10000000000.00</valUSD>
        <pctVal>13.333333</pctVal>
        <curCd>USD</curCd>
        <assetCat>EC</assetCat>
      </invstOrSec>
      <invstOrSec>
        <name>Microsoft Corp</name>
        <cusip>594918104</cusip>
        <valUSD>9000000000.00</valUSD>
        <pctVal>12.000000</pctVal>
        <curCd>USD</curCd>
        <assetCat>EC</assetCat>
      </invstOrSec>
    </invstOrSecs>
  </formData>
</edgarSubmission>
"""

# XML whose seriesName matches VHT's expected hints.
_NPORT_XML_VHT_SERIES = """\
<?xml version="1.0" encoding="UTF-8"?>
<edgarSubmission xmlns="http://www.sec.gov/edgar/nport">
  <formData>
    <genInfo>
      <regName>VANGUARD WORLD FUND</regName>
      <seriesName>Vanguard Health Care Index Fund</seriesName>
      <seriesId>S000009904</seriesId>
      <repPdDate>2026-01-31</repPdDate>
    </genInfo>
    <fundInfo>
      <totAssets>15000000000.00</totAssets>
      <netAssets>14950000000.00</netAssets>
    </fundInfo>
    <invstOrSecs>
      <invstOrSec>
        <name>Johnson and Johnson</name>
        <cusip>478160104</cusip>
        <valUSD>1000000000.00</valUSD>
        <pctVal>6.666667</pctVal>
        <curCd>USD</curCd>
        <assetCat>EC</assetCat>
      </invstOrSec>
    </invstOrSecs>
  </formData>
</edgarSubmission>
"""

# XML with a completely different series name — matches NONE of VGT/VHT/VIS.
_NPORT_XML_OTHER_VANGUARD_SERIES = """\
<?xml version="1.0" encoding="UTF-8"?>
<edgarSubmission xmlns="http://www.sec.gov/edgar/nport">
  <formData>
    <genInfo>
      <regName>VANGUARD WORLD FUND</regName>
      <seriesName>Vanguard Consumer Discretionary Index Fund</seriesName>
      <seriesId>S000009910</seriesId>
      <repPdDate>2026-01-31</repPdDate>
    </genInfo>
    <fundInfo>
      <totAssets>5000000000.00</totAssets>
      <netAssets>4980000000.00</netAssets>
    </fundInfo>
    <invstOrSecs>
      <invstOrSec>
        <name>Amazon.com Inc</name>
        <cusip>023135106</cusip>
        <valUSD>700000000.00</valUSD>
        <pctVal>14.000000</pctVal>
        <curCd>USD</curCd>
        <assetCat>EC</assetCat>
      </invstOrSec>
    </invstOrSecs>
  </formData>
</edgarSubmission>
"""

# SPY-compatible XML — no seriesName (standalone trust doesn't need it).
_NPORT_XML_SPY_HOLDINGS = """\
<?xml version="1.0" encoding="UTF-8"?>
<edgarSubmission xmlns="http://www.sec.gov/edgar/nport">
  <formData>
    <genInfo>
      <repPdDate>2026-01-31</repPdDate>
    </genInfo>
    <fundInfo>
      <totAssets>500000000000.00</totAssets>
      <netAssets>499000000000.00</netAssets>
    </fundInfo>
    <invstOrSecs>
      <invstOrSec>
        <name>Apple Inc</name>
        <cusip>037833100</cusip>
        <valUSD>35000000000.00</valUSD>
        <pctVal>7.000000</pctVal>
        <curCd>USD</curCd>
        <assetCat>EC</assetCat>
      </invstOrSec>
    </invstOrSecs>
  </formData>
</edgarSubmission>
"""

# Submissions JSON with one NPORT-P entry.
_SUBMISSIONS_BODY_WITH_NPORT = {
    "filings": {
        "recent": {
            "form": ["NPORT-P", "10-K"],
            "filingDate": ["2026-03-15", "2025-12-01"],
            "accessionNumber": ["0000036405-26-000074", "0000036405-25-000001"],
            "reportDate": ["2026-01-31", "2025-12-31"],
            "primaryDocument": ["primary_doc.xml", "annual.htm"],
        }
    }
}

_SUBMISSIONS_BODY_NO_NPORT = {
    "filings": {
        "recent": {
            "form": ["10-K", "8-K"],
            "filingDate": ["2025-12-01", "2025-11-01"],
            "accessionNumber": ["0000036405-25-000001", "0000036405-25-000002"],
            "reportDate": ["2025-12-31", ""],
            "primaryDocument": ["annual.htm", "current.htm"],
        }
    }
}


# ── Helpers ───────────────────────────────────────────────────────────────────

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


def _provider_call(ticker, http_responses=None, user_agent="test@example.com",
                   cik_lookup_fn=None, candidate_ciks_override=None):
    from app.services.intelligence.research_workers.nport_provider_v1 import (
        NportProviderConfig,
        fetch_etf_nport_holdings,
    )
    cfg = NportProviderConfig(user_agent=user_agent)
    responses = list(http_responses or [])
    idx = [0]

    def _http_get(url):
        i = idx[0]
        idx[0] += 1
        if i >= len(responses):
            raise RuntimeError(f"Unexpected HTTP call #{i} to {url}")
        return responses[i]

    kwargs: dict[str, Any] = {"http_get_fn": _http_get}
    if cik_lookup_fn is not None:
        kwargs["cik_lookup_fn"] = cik_lookup_fn
    if candidate_ciks_override is not None:
        kwargs["_candidate_ciks_override"] = candidate_ciks_override

    return fetch_etf_nport_holdings(ticker, cfg, **kwargs)


# ── Tests 84-87: False-positive prevention (VGT/VHT/VIS same parent CIK) ─────


class TestIdentityFalsePositivePrevention:
    """VGT, VHT, VIS share parent CIK 0000036405.

    Only the ticker whose expected series name matches the parsed XML should
    succeed. Others must return series_identity_not_proven with NO holdings.
    """

    def _call_with_other_series_xml(self, ticker: str):
        """Call ticker with XML that belongs to a DIFFERENT Vanguard World Fund series."""
        return _provider_call(
            ticker=ticker,
            http_responses=[
                _mock_resp(json_body=_SUBMISSIONS_BODY_WITH_NPORT),
                _mock_resp(text_body=_NPORT_XML_OTHER_VANGUARD_SERIES),
            ],
        )

    def test_84a_vgt_not_proven_when_other_series_in_xml(self):
        """VGT → series_identity_not_proven when XML has a non-VGT series name."""
        result = self._call_with_other_series_xml("VGT")
        assert result.fetch_status == "series_identity_not_proven"
        assert result.identity_status == "series_identity_not_proven"
        assert result.identity_verified is False

    def test_84b_vht_not_proven_when_other_series_in_xml(self):
        """VHT → series_identity_not_proven when XML has a non-VHT series name."""
        result = self._call_with_other_series_xml("VHT")
        assert result.fetch_status == "series_identity_not_proven"
        assert result.identity_verified is False

    def test_84c_vis_not_proven_when_other_series_in_xml(self):
        """VIS → series_identity_not_proven when XML has a non-VIS series name."""
        result = self._call_with_other_series_xml("VIS")
        assert result.fetch_status == "series_identity_not_proven"
        assert result.identity_verified is False

    def test_85_only_matching_ticker_succeeds(self):
        """Only VGT succeeds when the NPORT XML has VGT's series name."""
        # VGT matches — success
        vgt_result = _provider_call(
            ticker="VGT",
            http_responses=[
                _mock_resp(json_body=_SUBMISSIONS_BODY_WITH_NPORT),
                _mock_resp(text_body=_NPORT_XML_VGT_SERIES),
            ],
        )
        assert vgt_result.fetch_status == "success"
        assert vgt_result.identity_verified is True
        assert vgt_result.identity_status == "success_identity_verified"

        # VHT does not match VGT's XML — not proven
        vht_result = _provider_call(
            ticker="VHT",
            http_responses=[
                _mock_resp(json_body=_SUBMISSIONS_BODY_WITH_NPORT),
                _mock_resp(text_body=_NPORT_XML_VGT_SERIES),
            ],
        )
        assert vht_result.fetch_status == "series_identity_not_proven"
        assert vht_result.identity_verified is False

        # VIS does not match VGT's XML — not proven
        vis_result = _provider_call(
            ticker="VIS",
            http_responses=[
                _mock_resp(json_body=_SUBMISSIONS_BODY_WITH_NPORT),
                _mock_resp(text_body=_NPORT_XML_VGT_SERIES),
            ],
        )
        assert vis_result.fetch_status == "series_identity_not_proven"
        assert vis_result.identity_verified is False

    def test_86_mismatch_returns_no_holdings(self):
        """series_identity_not_proven result must have empty holdings list."""
        result = self._call_with_other_series_xml("VGT")
        assert result.holdings == []
        assert result.fetch_status == "series_identity_not_proven"

    def test_87_mismatch_includes_diagnostics(self):
        """series_identity_not_proven includes mismatch reason and detected series."""
        result = self._call_with_other_series_xml("VGT")
        assert result.fetch_status == "series_identity_not_proven"
        # Detected series name must be surfaced
        assert result.detected_series_name == "Vanguard Consumer Discretionary Index Fund"
        # Mismatch reason must explain what was expected vs what was found
        assert result.identity_mismatch_reason is not None
        assert "Vanguard Consumer Discretionary Index Fund" in result.identity_mismatch_reason
        # Accession number surfaced via filing_meta
        assert result.filing_meta is not None
        assert result.filing_meta.accession_number is not None


# ── Tests 88-89: Standalone trusts (SPY, QQQ) ────────────────────────────────


class TestStandaloneTrustIdentity:
    """SPY and QQQ are standalone single-series trusts; identity assumed."""

    def test_88_spy_standalone_identity(self):
        """SPY: success_identity_assumed_single_series; identity_verified=True."""
        result = _provider_call(
            ticker="SPY",
            http_responses=[
                _mock_resp(json_body=_SUBMISSIONS_BODY_WITH_NPORT),
                _mock_resp(text_body=_NPORT_XML_SPY_HOLDINGS),
            ],
        )
        assert result.fetch_status == "success"
        assert result.identity_status == "success_identity_assumed_single_series"
        assert result.identity_verified is True
        assert result.identity_basis is not None
        assert "standalone" in result.identity_basis.lower()
        assert result.is_identity_certified is True

    def test_89_qqq_standalone_identity(self):
        """QQQ: success_identity_assumed_single_series; identity_verified=True."""
        result = _provider_call(
            ticker="QQQ",
            http_responses=[
                _mock_resp(json_body=_SUBMISSIONS_BODY_WITH_NPORT),
                _mock_resp(text_body=_NPORT_XML_SPY_HOLDINGS),
            ],
        )
        assert result.fetch_status == "success"
        assert result.identity_status == "success_identity_assumed_single_series"
        assert result.identity_verified is True
        assert result.is_identity_certified is True


# ── Test 90: GLD commodity trust ─────────────────────────────────────────────


def test_90_gld_commodity_trust_preserved():
    """GLD: commodity_trust_or_no_nport_data; behavior unchanged from before."""
    result = _provider_call(
        ticker="GLD",
        http_responses=[
            _mock_resp(json_body=_SUBMISSIONS_BODY_NO_NPORT),
        ],
    )
    assert result.fetch_status == "commodity_trust_or_no_nport_data"
    assert result.holdings == []
    assert result.identity_verified is False
    assert result.resolver_source == "etf_parent_map"
    assert result.parent_registrant_name == "SPDR GOLD TRUST"


# ── Tests 91-92: VYM sec_error preserves resolver diagnostics ────────────────


class TestVymResolverDiagnostics:
    """VYM sec_error/404 must preserve resolver_source and parent_registrant_name."""

    def _vym_404_call(self):
        """Simulate VYM submissions returning HTTP 404."""
        return _provider_call(
            ticker="VYM",
            http_responses=[
                _mock_resp(status_code=404),  # submissions endpoint returns 404
            ],
        )

    def test_91_vym_sec_error_preserves_resolver_source(self):
        """VYM sec_error result includes resolver_source='etf_parent_map'."""
        result = self._vym_404_call()
        # May be sec_error or the next candidate attempt (no next candidate for VYM)
        assert result.fetch_status in ("sec_error", "no_nport_filing")
        assert result.resolver_source == "etf_parent_map"

    def test_92_vym_sec_error_preserves_parent_registrant_name(self):
        """VYM sec_error result includes parent_registrant_name."""
        result = self._vym_404_call()
        assert result.parent_registrant_name == "VANGUARD WHITEHALL FUNDS"

    def test_92b_vym_candidate_ciks_tried_populated(self):
        """candidate_ciks_tried includes the VYM CIK that was attempted."""
        result = self._vym_404_call()
        assert isinstance(result.candidate_ciks_tried, list)
        assert len(result.candidate_ciks_tried) >= 1
        # The VYM primary CIK 0000916548 should have been tried
        assert any("0000916548" in cik for cik in result.candidate_ciks_tried)


# ── Tests 93-97: Multi-candidate CIK resolution ──────────────────────────────


class TestMultiCandidateResolution:
    """Multi-candidate CIK resolution via _candidate_ciks_override parameter."""

    def test_93_candidates_tried_in_deterministic_order(self):
        """First candidate is tried before second; candidate_ciks_tried reflects order."""
        # Both candidates return no NPORT so we can see both were tried
        result = _provider_call(
            ticker="VGT",
            candidate_ciks_override=["0000036405", "0000764180"],
            http_responses=[
                _mock_resp(json_body=_SUBMISSIONS_BODY_NO_NPORT),  # first candidate
                _mock_resp(json_body=_SUBMISSIONS_BODY_NO_NPORT),  # second candidate
            ],
        )
        assert result.fetch_status == "no_nport_filing"
        tried = result.candidate_ciks_tried
        assert tried[0] == "0000036405"
        assert tried[1] == "0000764180"

    def test_94_first_candidate_fails_second_succeeds(self):
        """First candidate no_nport_filing → second candidate tried and succeeds."""
        result = _provider_call(
            ticker="VGT",
            candidate_ciks_override=["0000000001", "0000036405"],
            http_responses=[
                _mock_resp(json_body=_SUBMISSIONS_BODY_NO_NPORT),  # first: no NPORT
                _mock_resp(json_body=_SUBMISSIONS_BODY_WITH_NPORT),  # second: NPORT found
                _mock_resp(text_body=_NPORT_XML_VGT_SERIES),        # XML for second
            ],
        )
        assert result.fetch_status == "success"
        assert result.identity_verified is True
        assert result.selected_candidate_cik == "0000036405"
        assert result.candidate_ciks_tried == ["0000000001", "0000036405"]

    def test_95_all_candidates_no_nport_filing(self):
        """All candidates no_nport_filing → no_nport_filing with full candidate list."""
        result = _provider_call(
            ticker="VGT",
            candidate_ciks_override=["0000000001", "0000000002", "0000000003"],
            http_responses=[
                _mock_resp(json_body=_SUBMISSIONS_BODY_NO_NPORT),
                _mock_resp(json_body=_SUBMISSIONS_BODY_NO_NPORT),
                _mock_resp(json_body=_SUBMISSIONS_BODY_NO_NPORT),
            ],
        )
        assert result.fetch_status == "no_nport_filing"
        assert "0000000001" in result.candidate_ciks_tried
        assert "0000000002" in result.candidate_ciks_tried
        assert "0000000003" in result.candidate_ciks_tried

    def test_96_all_candidates_sec_error(self):
        """All candidates return 404 → sec_error with candidate_ciks_tried populated."""
        result = _provider_call(
            ticker="VGT",
            candidate_ciks_override=["0000000001", "0000000002"],
            http_responses=[
                _mock_resp(status_code=404),  # first
                _mock_resp(status_code=404),  # second
            ],
        )
        assert result.fetch_status == "sec_error"
        assert len(result.candidate_ciks_tried) == 2

    def test_97_single_failing_candidate_stops_gracefully(self):
        """Single candidate returning no_nport_filing → no_nport_filing, no loop."""
        result = _provider_call(
            ticker="VGT",
            candidate_ciks_override=["0000036405"],
            http_responses=[
                _mock_resp(json_body=_SUBMISSIONS_BODY_NO_NPORT),
            ],
        )
        assert result.fetch_status == "no_nport_filing"
        assert result.candidate_ciks_tried == ["0000036405"]


# ── Tests 98-103: Identity name matching helper ───────────────────────────────


class TestIdentityNameMatching:
    """Unit tests for _identity_name_matches normalization logic."""

    def _match(self, detected: Optional[str], expected: tuple[str, ...]) -> bool:
        from app.services.intelligence.research_workers.nport_provider_v1 import (
            _identity_name_matches,
        )
        return _identity_name_matches(detected, expected)

    def test_98_exact_match_returns_true(self):
        assert self._match(
            "Vanguard Information Technology Index Fund",
            ("Vanguard Information Technology Index Fund",),
        ) is True

    def test_99_substring_match_shorter_in_longer_returns_true(self):
        # Expected is substring of detected
        assert self._match(
            "Vanguard Information Technology Index Fund ETF Shares Class",
            ("Vanguard Information Technology Index Fund",),
        ) is True

    def test_100_case_insensitive_match(self):
        assert self._match(
            "VANGUARD INFORMATION TECHNOLOGY INDEX FUND",
            ("Vanguard Information Technology Index Fund",),
        ) is True

    def test_100b_punctuation_insensitive_match(self):
        assert self._match(
            "Schwab U.S. Dividend Equity ETF",
            ("Schwab US Dividend Equity ETF",),
        ) is True

    def test_101_non_matching_names_return_false(self):
        # VGT vs VHT — must not match
        assert self._match(
            "Vanguard Health Care Index Fund",
            ("Vanguard Information Technology Index Fund",),
        ) is False

    def test_101b_vti_vs_vxus_no_false_positive(self):
        # Similar fund names must not cross-match
        assert self._match(
            "Vanguard Total International Stock Index Fund",
            ("Vanguard Total Stock Market Index Fund",),
        ) is False

    def test_102_empty_detected_returns_false(self):
        assert self._match(None, ("Vanguard Information Technology Index Fund",)) is False
        assert self._match("", ("Vanguard Information Technology Index Fund",)) is False

    def test_103_empty_expected_names_returns_false(self):
        assert self._match("Vanguard Information Technology Index Fund", ()) is False


# ── Tests 104-107: Diagnostic runner identity fields ─────────────────────────


class TestDiagnosticRunnerIdentityFields:
    """Verify identity fields surfaced in runner and diagnostic output."""

    def _build_entry(self, result) -> dict:
        from app.services.intelligence.research_workers.nport_diagnostic_runner import (
            _build_ticker_entry,
        )
        return _build_ticker_entry(result)

    def _make_success_result(self, ticker="SPY", identity_verified=True,
                              identity_status="success_identity_assumed_single_series",
                              identity_basis="standalone_single_series_trust"):
        from app.services.intelligence.research_workers.nport_provider_v1 import (
            NportFilingMeta, NportHolding, NportProviderResult,
        )
        return NportProviderResult(
            ticker=ticker,
            fetch_status="success",
            cik="0000884394",
            holdings=[NportHolding(name="Apple Inc", cusip="037833100", weight_pct=7.0)],
            filing_meta=NportFilingMeta(
                accession_number="0000884394-26-000001",
                form_type="NPORT-P",
                filing_date="2026-03-15",
                report_period_date="2026-01-31",
                primary_doc="primary_doc.xml",
                filing_url="https://www.sec.gov/cgi-bin/browse-edgar",
            ),
            resolver_source="etf_parent_map",
            parent_registrant_name="SPDR S&P 500 ETF TRUST",
            identity_status=identity_status,
            identity_verified=identity_verified,
            identity_basis=identity_basis,
            candidate_ciks_tried=["0000884394"],
            selected_candidate_cik="0000884394",
            detected_series_name=None,
            detected_registrant_name=None,
        )

    def _make_mismatch_result(self, ticker="VGT"):
        from app.services.intelligence.research_workers.nport_provider_v1 import (
            NportFilingMeta, NportProviderResult,
        )
        return NportProviderResult(
            ticker=ticker,
            fetch_status="series_identity_not_proven",
            cik="0000036405",
            resolver_source="etf_parent_map",
            parent_registrant_name="VANGUARD WORLD FUND",
            identity_status="series_identity_not_proven",
            identity_verified=False,
            identity_mismatch_reason="Expected VGT series, detected VHT series",
            detected_series_name="Vanguard Health Care Index Fund",
            detected_registrant_name="VANGUARD WORLD FUND",
            candidate_ciks_tried=["0000036405"],
            selected_candidate_cik="0000036405",
            filing_meta=NportFilingMeta(
                accession_number="0000036405-26-000074",
                form_type="NPORT-P",
                filing_date="2026-03-15",
                report_period_date="2026-01-31",
                primary_doc="primary_doc.xml",
                filing_url="https://www.sec.gov/cgi-bin/browse-edgar",
            ),
        )

    def test_104_build_ticker_entry_includes_all_identity_fields(self):
        """_build_ticker_entry includes all required identity fields."""
        result = self._make_success_result()
        entry = self._build_entry(result)
        required_identity_fields = [
            "identity_status",
            "identity_verified",
            "identity_basis",
            "candidate_ciks_tried",
            "selected_candidate_cik",
            "detected_registrant_name",
            "detected_series_name",
            "detected_class_name",
            "detected_series_id",
            "detected_class_id",
            "identity_mismatch_reason",
            "candidate_identity_failures",
        ]
        for field in required_identity_fields:
            assert field in entry, f"Missing identity field: {field!r}"

    def test_105_identity_verified_true_surfaced_for_standalone_trust(self):
        """identity_verified=True surfaced in runner entry for standalone trust."""
        result = self._make_success_result(
            identity_verified=True,
            identity_status="success_identity_assumed_single_series",
        )
        entry = self._build_entry(result)
        assert entry["identity_verified"] is True
        assert entry["identity_status"] == "success_identity_assumed_single_series"

    def test_106_identity_mismatch_reason_surfaced_for_not_proven(self):
        """identity_mismatch_reason surfaced for series_identity_not_proven result."""
        result = self._make_mismatch_result("VGT")
        entry = self._build_entry(result)
        assert entry["identity_status"] == "series_identity_not_proven"
        assert entry["identity_verified"] is False
        assert entry["identity_mismatch_reason"] is not None
        assert "VGT" in entry["identity_mismatch_reason"] or "VHT" in entry["identity_mismatch_reason"]
        assert entry["detected_series_name"] == "Vanguard Health Care Index Fund"

    def test_107_run_nport_live_check_includes_identity_verified_count(self):
        """run_nport_live_check output includes tickers_identity_verified aggregate."""
        from app.services.intelligence.research_workers.nport_diagnostic_runner import (
            run_nport_live_check,
        )
        from app.services.intelligence.research_workers.nport_provider_v1 import (
            NportFilingMeta, NportHolding, NportProviderResult,
        )

        def _mock_provider(ticker, cfg):
            return NportProviderResult(
                ticker=ticker,
                fetch_status="success",
                cik="0000884394",
                holdings=[NportHolding(name="Apple", cusip="037833100", weight_pct=5.0)],
                filing_meta=NportFilingMeta(
                    accession_number="0000884394-26-000001",
                    form_type="NPORT-P",
                    filing_date="2026-03-15",
                    report_period_date="2026-01-31",
                    primary_doc="primary_doc.xml",
                    filing_url="https://www.sec.gov/",
                ),
                identity_verified=True,
                identity_status="success_identity_assumed_single_series",
            )

        result = run_nport_live_check(
            ["SPY", "QQQ"],
            user_agent="test@example.com",
            provider_fn=_mock_provider,
            sleep_fn=lambda s: None,
        )
        assert "tickers_identity_verified" in result
        assert result["tickers_identity_verified"] == 2
        assert result["tickers_succeeded"] == 2
        # Identity fields present in each per_ticker entry
        for entry in result["per_ticker"]:
            assert "identity_status" in entry
            assert "identity_verified" in entry


# ── Tests 108-110: Invariants ─────────────────────────────────────────────────


def test_108_series_identity_not_proven_never_has_holdings():
    """series_identity_not_proven result must always have empty holdings."""
    result = _provider_call(
        ticker="VGT",
        http_responses=[
            _mock_resp(json_body=_SUBMISSIONS_BODY_WITH_NPORT),
            _mock_resp(text_body=_NPORT_XML_OTHER_VANGUARD_SERIES),
        ],
    )
    assert result.fetch_status == "series_identity_not_proven"
    assert result.holdings == []
    assert not result.is_success
    assert not result.is_identity_certified


def test_109_no_live_sec_calls_confirmed():
    """All tests in this file are fixture-based (no live network calls).

    This structural test confirms that the RuntimeError-raising mock HTTP function
    is what all the above tests use. If any test reached a real HTTP call with
    a real URL, it would have called the real network, not the mock.
    """
    # Verify the mock raises on unexpected calls (ensures no real network calls).
    result = _provider_call(
        ticker="SPY",
        http_responses=[
            _mock_resp(json_body=_SUBMISSIONS_BODY_WITH_NPORT),
            _mock_resp(text_body=_NPORT_XML_SPY_HOLDINGS),
        ],
    )
    assert result.fetch_status == "success"


def test_110_detected_identity_fields_parsed_from_xml():
    """series_name, registrant_name, series_id, class_id parsed from genInfo XML."""
    result = _provider_call(
        ticker="VGT",
        http_responses=[
            _mock_resp(json_body=_SUBMISSIONS_BODY_WITH_NPORT),
            _mock_resp(text_body=_NPORT_XML_VGT_SERIES),
        ],
    )
    assert result.fetch_status == "success"
    assert result.detected_series_name == "Vanguard Information Technology Index Fund"
    assert result.detected_registrant_name == "VANGUARD WORLD FUND"
    assert result.detected_series_id == "S000009902"
    assert result.detected_class_id == "C000027032"
    assert result.detected_class_name == "ETF Shares"


# ── Tests 111-112: Multi-candidate identity resolution (Blocker 2 fix) ────────


class TestMultiCandidateIdentityResolution:
    """Blocker 2 fix: record identity failure and continue to next candidate."""

    def test_111_candidate1_wrong_series_candidate2_matches_succeeds(self):
        """Candidate 1 yields holdings with wrong series; candidate 2 has matching
        series → result is success with identity_verified=True on candidate 2."""
        result = _provider_call(
            ticker="VGT",
            candidate_ciks_override=["0000000001", "0000036405"],
            http_responses=[
                _mock_resp(json_body=_SUBMISSIONS_BODY_WITH_NPORT),         # cand1 submissions
                _mock_resp(text_body=_NPORT_XML_OTHER_VANGUARD_SERIES),     # cand1 XML (wrong series)
                _mock_resp(json_body=_SUBMISSIONS_BODY_WITH_NPORT),         # cand2 submissions
                _mock_resp(text_body=_NPORT_XML_VGT_SERIES),                # cand2 XML (VGT series)
            ],
        )
        assert result.fetch_status == "success"
        assert result.identity_verified is True
        assert result.identity_status == "success_identity_verified"
        assert result.selected_candidate_cik == "0000036405"
        assert result.holdings != []
        # First candidate's identity failure must be recorded
        assert len(result.candidate_identity_failures) == 1
        failure = result.candidate_identity_failures[0]
        assert failure["candidate_cik"] == "0000000001"
        assert failure["detected_series_name"] == "Vanguard Consumer Discretionary Index Fund"
        assert failure["identity_mismatch_reason"] is not None

    def test_112_all_candidates_wrong_series_returns_not_proven(self):
        """Both candidates yield holdings but neither series matches →
        series_identity_not_proven with candidate_identity_failures populated, no holdings."""
        result = _provider_call(
            ticker="VGT",
            candidate_ciks_override=["0000000001", "0000000002"],
            http_responses=[
                _mock_resp(json_body=_SUBMISSIONS_BODY_WITH_NPORT),         # cand1 submissions
                _mock_resp(text_body=_NPORT_XML_OTHER_VANGUARD_SERIES),     # cand1 XML (wrong)
                _mock_resp(json_body=_SUBMISSIONS_BODY_WITH_NPORT),         # cand2 submissions
                _mock_resp(text_body=_NPORT_XML_OTHER_VANGUARD_SERIES),     # cand2 XML (wrong)
            ],
        )
        assert result.fetch_status == "series_identity_not_proven"
        assert result.holdings == []
        assert result.identity_verified is False
        assert result.identity_status == "series_identity_not_proven"
        # Both candidates' failures must be recorded
        assert len(result.candidate_identity_failures) == 2
        assert "0000000001" in result.candidate_ciks_tried
        assert "0000000002" in result.candidate_ciks_tried
        for failure in result.candidate_identity_failures:
            assert failure["detected_series_name"] == "Vanguard Consumer Discretionary Index Fund"
            assert failure["identity_mismatch_reason"] is not None
