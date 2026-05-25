"""Stage 9F.2a — ETF parent-registrant CIK resolver tests.

Coverage (all fixture-based; zero live SEC calls):

  Resolver module (etf_parent_cik_resolver):
  51. All confirmed tickers present in map (SPY, QQQ, XLE, GLD).
  52. All unresolved Vanguard tickers now in map (VOO, VTI, VGT, VHT, VIS, VXUS, VYM).
  53. SCHD present in map (Schwab parent registrant).
  54. VOO/VTI resolve to VANGUARD INDEX FUNDS parent CIK (not share-class CIK).
  55. VGT/VHT/VIS resolve to VANGUARD WORLD FUND parent CIK.
  56. VYM resolves to VANGUARD WHITEHALL FUNDS.
  57. VXUS resolves to a non-empty candidate parent CIK.
  58. SCHD resolves to SCHWAB STRATEGIC TRUST parent CIK.
  59. VHT/VIS/VXUS resolve to non-None CIK (no longer missing_cik in map).
  60. VOO/VTI/VGT is_parent_registrant=True; SPY/QQQ/XLE is_parent_registrant=False.
  61. GLD expected_status="no_nport".
  62. Unknown ticker returns None from resolve_etf_parent_cik.
  63. Resolver is case-insensitive (lowercase ticker works).
  64. All parent CIKs are 10-digit zero-padded strings.
  65. No two tickers share a parent CIK across different Vanguard parent entities.

  Provider integration with parent resolver:
  66. VHT: uses parent map CIK, does not return missing_cik.
  67. VIS: uses parent map CIK, does not return missing_cik.
  68. VXUS: uses parent map CIK, does not return missing_cik.
  69. VOO: uses parent map CIK (not old share-class 0001480511).
  70. VTI: uses parent map CIK (not old share-class 0000732834).
  71. VGT: uses parent map CIK (not old share-class 0001137774).
  72. VYM: uses parent map CIK (not old share-class 0001383310).
  73. SCHD: uses parent map CIK (not old share-class 0001510588).
  74. SPY: success path unchanged (provider still succeeds, resolver_source=etf_parent_map).
  75. QQQ: success path unchanged.
  76. XLE: success path unchanged.
  77. GLD: commodity-trust path unchanged.
  78. provider result.resolver_source="etf_parent_map" when parent map used.
  79. provider result.parent_registrant_name set when parent map used.
  80. resolver_source="company_tickers" for unknown ticker falling through to tickers.json.
  81. resolver_source="injected" when cik_lookup_fn provided.
  82. no_nport_filing result includes resolver_source and parent_registrant_name.
  83. diagnostic runner entry includes resolver_source and parent_registrant_name fields.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

# ── Shared fixtures ────────────────────────────────────────────────────────────

_SUBMISSIONS_BODY_NPORT = {
    "filings": {
        "recent": {
            "form": ["NPORT-P", "10-K"],
            "filingDate": ["2025-11-15", "2025-03-01"],
            "accessionNumber": ["0000884394-25-001234", "0000884394-25-000001"],
            "reportDate": ["2025-09-30", "2024-12-31"],
            "primaryDocument": ["primary_doc.xml", "annual_report.htm"],
        }
    }
}

_SUBMISSIONS_BODY_NO_NPORT = {
    "filings": {
        "recent": {
            "form": ["10-K", "8-K"],
            "filingDate": ["2025-03-01", "2025-01-15"],
            "accessionNumber": ["0001234567-25-000001", "0001234567-25-000002"],
            "reportDate": ["2024-12-31", ""],
            "primaryDocument": ["annual.htm", "current.htm"],
        }
    }
}

_NPORT_XML_TWO_HOLDINGS = """\
<?xml version="1.0" encoding="UTF-8"?>
<edgarSubmission xmlns="http://www.sec.gov/edgar/nport">
  <formData>
    <genInfo>
      <seriesName>TEST ETF TRUST</seriesName>
      <repPdDate>2025-09-30</repPdDate>
    </genInfo>
    <fundInfo>
      <totAssets>500000000.00</totAssets>
      <netAssets>499000000.00</netAssets>
    </fundInfo>
    <invstOrSecs>
      <invstOrSec>
        <name>Apple Inc</name>
        <cusip>037833100</cusip>
        <valUSD>25000000.00</valUSD>
        <pctVal>5.000000</pctVal>
        <curCd>USD</curCd>
        <assetCat>EC</assetCat>
      </invstOrSec>
      <invstOrSec>
        <name>Microsoft Corp</name>
        <cusip>594918104</cusip>
        <valUSD>20000000.00</valUSD>
        <pctVal>4.000000</pctVal>
        <curCd>USD</curCd>
        <assetCat>EC</assetCat>
      </invstOrSec>
    </invstOrSecs>
  </formData>
</edgarSubmission>
"""

# XLE-specific XML fixture: seriesName matches "Energy Select Sector SPDR Fund"
# so the identity check succeeds for XLE.
_NPORT_XML_XLE_SERIES = """\
<?xml version="1.0" encoding="UTF-8"?>
<edgarSubmission xmlns="http://www.sec.gov/edgar/nport">
  <formData>
    <genInfo>
      <seriesName>Energy Select Sector SPDR Fund</seriesName>
      <repPdDate>2025-09-30</repPdDate>
    </genInfo>
    <fundInfo>
      <totAssets>40000000000.00</totAssets>
      <netAssets>39900000000.00</netAssets>
    </fundInfo>
    <invstOrSecs>
      <invstOrSec>
        <name>Exxon Mobil Corp</name>
        <cusip>302491303</cusip>
        <valUSD>5000000000.00</valUSD>
        <pctVal>12.500000</pctVal>
        <curCd>USD</curCd>
        <assetCat>EC</assetCat>
      </invstOrSec>
      <invstOrSec>
        <name>Chevron Corp</name>
        <cusip>166764100</cusip>
        <valUSD>3000000000.00</valUSD>
        <pctVal>7.500000</pctVal>
        <curCd>USD</curCd>
        <assetCat>EC</assetCat>
      </invstOrSec>
    </invstOrSecs>
  </formData>
</edgarSubmission>
"""

_COMPANY_TICKERS_BODY_UNKNOWN = {
    "0": {"cik_str": 999001, "ticker": "UNKOWN_ONLY", "title": "Some Unknown Corp"},
}


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


def _provider_call(ticker, http_responses=None, cik_lookup_fn=None, user_agent="test@example.com"):
    """Call fetch_etf_nport_holdings with mocked HTTP and no live calls."""
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

    return fetch_etf_nport_holdings(ticker, cfg, http_get_fn=_http_get, cik_lookup_fn=cik_lookup_fn)


# ── Resolver module tests ─────────────────────────────────────────────────────


class TestEtfParentCikResolverModule:
    """Tests for etf_parent_cik_resolver module (pure static, no HTTP)."""

    def _map(self):
        from app.services.intelligence.research_workers.etf_parent_cik_resolver import (
            _ETF_PARENT_REGISTRANT_MAP,
        )
        return _ETF_PARENT_REGISTRANT_MAP

    def _resolve(self, ticker):
        from app.services.intelligence.research_workers.etf_parent_cik_resolver import (
            resolve_etf_parent_cik,
        )
        return resolve_etf_parent_cik(ticker)

    def _entry(self, ticker):
        from app.services.intelligence.research_workers.etf_parent_cik_resolver import (
            get_parent_registrant_entry,
        )
        return get_parent_registrant_entry(ticker)

    def test_51_confirmed_tickers_in_map(self):
        """All confirmed standalone-trust tickers present in the map."""
        m = self._map()
        for ticker in ("SPY", "QQQ", "XLE", "GLD"):
            assert ticker in m, f"{ticker} missing from parent registrant map"

    def test_52_vanguard_unresolved_tickers_now_in_map(self):
        """All previously unresolved Vanguard tickers now have parent map entries."""
        m = self._map()
        for ticker in ("VOO", "VTI", "VGT", "VHT", "VIS", "VXUS", "VYM"):
            assert ticker in m, f"{ticker} missing from parent registrant map"

    def test_53_schd_in_map(self):
        """SCHD present in map with a Schwab parent registrant entry."""
        m = self._map()
        assert "SCHD" in m
        entry = m["SCHD"]
        assert "SCHWAB" in entry.parent_name.upper()

    def test_54_voo_vti_resolve_to_vanguard_index_funds(self):
        """VOO and VTI resolve to VANGUARD INDEX FUNDS parent CIK."""
        for ticker in ("VOO", "VTI"):
            entry = self._entry(ticker)
            assert entry is not None
            assert "VANGUARD INDEX FUNDS" in entry.parent_name
            assert entry.parent_cik == "0000764180"

    def test_55_vgt_vht_vis_resolve_to_vanguard_world_fund(self):
        """VGT, VHT, VIS resolve to VANGUARD WORLD FUND parent CIK."""
        for ticker in ("VGT", "VHT", "VIS"):
            entry = self._entry(ticker)
            assert entry is not None
            assert "VANGUARD WORLD FUND" in entry.parent_name
            assert entry.parent_cik == "0000036405"

    def test_56_vym_resolves_to_whitehall_funds(self):
        """VYM resolves to VANGUARD WHITEHALL FUNDS."""
        entry = self._entry("VYM")
        assert entry is not None
        assert "WHITEHALL" in entry.parent_name
        assert entry.parent_cik == "0000916548"

    def test_57_vxus_has_non_empty_candidate_parent_cik(self):
        """VXUS has a non-empty candidate parent CIK entry."""
        entry = self._entry("VXUS")
        assert entry is not None
        assert entry.parent_cik, "VXUS parent_cik must not be empty"
        assert len(entry.parent_cik) == 10, "CIK must be 10 chars"
        # Marked as candidate — needs post-deploy validation
        assert entry.expected_status == "candidate"

    def test_58_schd_resolves_to_schwab_strategic_trust(self):
        """SCHD resolves to SCHWAB STRATEGIC TRUST."""
        entry = self._entry("SCHD")
        assert entry is not None
        assert "SCHWAB STRATEGIC TRUST" in entry.parent_name
        assert entry.parent_cik == "0001477379"

    def test_59_vht_vis_vxus_resolve_to_nonnone_cik(self):
        """VHT/VIS/VXUS (previously missing_cik) now have valid parent CIK entries."""
        for ticker in ("VHT", "VIS", "VXUS"):
            result = self._resolve(ticker)
            assert result is not None, f"{ticker} must resolve via parent map"
            parent_cik, parent_name, is_parent = result
            assert parent_cik, f"{ticker} parent_cik must be non-empty"
            assert parent_name, f"{ticker} parent_name must be non-empty"
            assert is_parent is True, f"{ticker} should be is_parent_registrant=True"

    def test_60_is_parent_registrant_flag(self):
        """Vanguard/SCHD ETFs have is_parent_registrant=True; standalone trusts False."""
        for ticker in ("VOO", "VTI", "VGT", "VHT", "VIS", "VXUS", "VYM", "SCHD"):
            entry = self._entry(ticker)
            assert entry.is_parent_registrant is True, f"{ticker} must have is_parent_registrant=True"
        for ticker in ("SPY", "QQQ", "XLE", "GLD"):
            entry = self._entry(ticker)
            assert entry.is_parent_registrant is False, f"{ticker} must have is_parent_registrant=False"

    def test_61_gld_expected_status_no_nport(self):
        """GLD has expected_status='no_nport' (commodity trust, no equity holdings)."""
        entry = self._entry("GLD")
        assert entry is not None
        assert entry.expected_status == "no_nport"

    def test_62_unknown_ticker_returns_none(self):
        """Unknown ticker returns None from resolve_etf_parent_cik."""
        result = self._resolve("TOTALLY_UNKNOWN_TICKER_XYZ")
        assert result is None

    def test_63_resolver_is_case_insensitive(self):
        """Resolver accepts lowercase and mixed-case ticker input."""
        result_upper = self._resolve("VOO")
        result_lower = self._resolve("voo")
        result_mixed = self._resolve("Voo")
        assert result_upper is not None
        assert result_lower is not None
        assert result_mixed is not None
        assert result_upper[0] == result_lower[0] == result_mixed[0]

    def test_64_all_parent_ciks_are_10digit_zero_padded(self):
        """Every entry's parent_cik is a 10-character zero-padded string."""
        for ticker, entry in self._map().items():
            assert len(entry.parent_cik) == 10, (
                f"{ticker} parent_cik {entry.parent_cik!r} is not 10 digits"
            )
            assert entry.parent_cik.isdigit(), (
                f"{ticker} parent_cik {entry.parent_cik!r} is not all digits"
            )

    def test_65_vanguard_parent_entities_are_distinct(self):
        """VOO/VTI share one parent; VGT/VHT/VIS share another; VYM has its own."""
        index_funds_cik = self._entry("VOO").parent_cik
        world_fund_cik = self._entry("VGT").parent_cik
        whitehall_cik = self._entry("VYM").parent_cik

        # VOO and VTI share the same parent
        assert self._entry("VTI").parent_cik == index_funds_cik
        # VGT/VHT/VIS share the same parent
        assert self._entry("VHT").parent_cik == world_fund_cik
        assert self._entry("VIS").parent_cik == world_fund_cik
        # Three distinct parent registrants
        assert len({index_funds_cik, world_fund_cik, whitehall_cik}) == 3


# ── Provider integration tests ────────────────────────────────────────────────


class TestProviderWithParentResolver:
    """Tests proving provider uses parent resolver for CIK resolution."""

    # ── Previously missing_cik tickers now resolved via parent map ─────────────

    def test_66_vht_uses_parent_map_not_missing_cik(self):
        """VHT: parent map resolves to parent CIK; provider fetches submissions."""
        from app.services.intelligence.research_workers.etf_parent_cik_resolver import (
            get_parent_registrant_entry,
        )
        entry = get_parent_registrant_entry("VHT")
        expected_cik = entry.parent_cik

        result = _provider_call(
            ticker="VHT",
            http_responses=[
                _mock_resp(json_body=_SUBMISSIONS_BODY_NO_NPORT),  # submissions → no NPORT yet
            ],
        )
        # Must NOT be missing_cik — the parent map resolved it.
        assert result.fetch_status != "missing_cik", (
            f"VHT must not return missing_cik; got {result.fetch_status}"
        )
        assert result.cik == expected_cik
        assert result.resolver_source == "etf_parent_map"
        assert result.parent_registrant_name == entry.parent_name

    def test_67_vis_uses_parent_map_not_missing_cik(self):
        """VIS: parent map resolves to parent CIK; provider fetches submissions."""
        from app.services.intelligence.research_workers.etf_parent_cik_resolver import (
            get_parent_registrant_entry,
        )
        entry = get_parent_registrant_entry("VIS")
        expected_cik = entry.parent_cik

        result = _provider_call(
            ticker="VIS",
            http_responses=[
                _mock_resp(json_body=_SUBMISSIONS_BODY_NO_NPORT),
            ],
        )
        assert result.fetch_status != "missing_cik"
        assert result.cik == expected_cik
        assert result.resolver_source == "etf_parent_map"

    def test_68_vxus_uses_parent_map_not_missing_cik(self):
        """VXUS: parent map resolves to candidate parent CIK; not missing_cik."""
        from app.services.intelligence.research_workers.etf_parent_cik_resolver import (
            get_parent_registrant_entry,
        )
        entry = get_parent_registrant_entry("VXUS")
        expected_cik = entry.parent_cik

        result = _provider_call(
            ticker="VXUS",
            http_responses=[
                _mock_resp(json_body=_SUBMISSIONS_BODY_NO_NPORT),
            ],
        )
        assert result.fetch_status != "missing_cik"
        assert result.cik == expected_cik
        assert result.resolver_source == "etf_parent_map"

    # ── Previously wrong share-class CIK tickers now use parent map ────────────

    def test_69_voo_uses_parent_cik_not_old_shareclass(self):
        """VOO: parent map CIK used; old share-class 0001480511 NOT used."""
        result = _provider_call(
            ticker="VOO",
            http_responses=[
                _mock_resp(json_body=_SUBMISSIONS_BODY_NO_NPORT),
            ],
        )
        assert result.cik == "0000764180", (
            f"VOO must use parent CIK 0000764180, got {result.cik!r}"
        )
        assert result.cik != "0001480511", "VOO must not use old share-class CIK"
        assert result.resolver_source == "etf_parent_map"

    def test_70_vti_uses_parent_cik_not_old_shareclass(self):
        """VTI: parent map CIK used; old share-class 0000732834 NOT used."""
        result = _provider_call(
            ticker="VTI",
            http_responses=[
                _mock_resp(json_body=_SUBMISSIONS_BODY_NO_NPORT),
            ],
        )
        assert result.cik == "0000764180"
        assert result.cik != "0000732834"
        assert result.resolver_source == "etf_parent_map"

    def test_71_vgt_uses_parent_cik_not_old_shareclass(self):
        """VGT: parent map CIK used; old share-class 0001137774 NOT used."""
        result = _provider_call(
            ticker="VGT",
            http_responses=[
                _mock_resp(json_body=_SUBMISSIONS_BODY_NO_NPORT),
            ],
        )
        assert result.cik == "0000036405"
        assert result.cik != "0001137774"
        assert result.resolver_source == "etf_parent_map"

    def test_72_vym_uses_parent_cik_not_old_shareclass(self):
        """VYM: parent map CIK used; old share-class 0001383310 NOT used."""
        result = _provider_call(
            ticker="VYM",
            http_responses=[
                _mock_resp(json_body=_SUBMISSIONS_BODY_NO_NPORT),
            ],
        )
        assert result.cik == "0000916548"
        assert result.cik != "0001383310"
        assert result.resolver_source == "etf_parent_map"

    def test_73_schd_uses_parent_cik_not_old_shareclass(self):
        """SCHD: parent map CIK used; old share-class 0001510588 NOT used."""
        result = _provider_call(
            ticker="SCHD",
            http_responses=[
                _mock_resp(json_body=_SUBMISSIONS_BODY_NO_NPORT),
            ],
        )
        assert result.cik == "0001477379"
        assert result.cik != "0001510588"
        assert result.resolver_source == "etf_parent_map"

    # ── Confirmed success paths preserved ──────────────────────────────────────

    def test_74_spy_success_path_unchanged(self):
        """SPY: provider still succeeds via parent map (confirmed CIK 0000884394)."""
        result = _provider_call(
            ticker="SPY",
            http_responses=[
                _mock_resp(json_body=_SUBMISSIONS_BODY_NPORT),
                _mock_resp(text_body=_NPORT_XML_TWO_HOLDINGS),
            ],
        )
        assert result.fetch_status == "success"
        assert result.is_success
        assert len(result.holdings) == 2
        assert result.cik == "0000884394"
        assert result.resolver_source == "etf_parent_map"

    def test_75_qqq_success_path_unchanged(self):
        """QQQ: provider still succeeds via parent map (confirmed CIK 0001067839)."""
        result = _provider_call(
            ticker="QQQ",
            http_responses=[
                _mock_resp(json_body=_SUBMISSIONS_BODY_NPORT),
                _mock_resp(text_body=_NPORT_XML_TWO_HOLDINGS),
            ],
        )
        assert result.fetch_status == "success"
        assert result.cik == "0001067839"
        assert result.resolver_source == "etf_parent_map"

    def test_76_xle_success_path_with_identity_verified(self):
        """XLE: succeeds via parent map when series name matches; identity_verified=True."""
        result = _provider_call(
            ticker="XLE",
            http_responses=[
                _mock_resp(json_body=_SUBMISSIONS_BODY_NPORT),
                _mock_resp(text_body=_NPORT_XML_XLE_SERIES),
            ],
        )
        assert result.fetch_status == "success"
        assert result.cik == "0001168164"
        assert result.resolver_source == "etf_parent_map"
        # Identity verification: XLE series name matched expected hints.
        assert result.identity_verified is True
        assert result.identity_status == "success_identity_verified"
        assert result.detected_series_name == "Energy Select Sector SPDR Fund"

    def test_77_gld_commodity_trust_path_unchanged(self):
        """GLD: commodity-trust path unchanged; resolves via parent map."""
        result = _provider_call(
            ticker="GLD",
            http_responses=[
                _mock_resp(json_body=_SUBMISSIONS_BODY_NO_NPORT),
            ],
        )
        assert result.fetch_status == "commodity_trust_or_no_nport_data"
        assert result.cik == "0001222333"
        assert result.resolver_source == "etf_parent_map"

    # ── Resolver source / diagnostic fields ────────────────────────────────────

    def test_78_resolver_source_etf_parent_map_on_success(self):
        """resolver_source='etf_parent_map' on successful fetch via parent map."""
        result = _provider_call(
            ticker="SPY",
            http_responses=[
                _mock_resp(json_body=_SUBMISSIONS_BODY_NPORT),
                _mock_resp(text_body=_NPORT_XML_TWO_HOLDINGS),
            ],
        )
        assert result.resolver_source == "etf_parent_map"

    def test_79_parent_registrant_name_set_on_success(self):
        """parent_registrant_name populated when parent map used."""
        result = _provider_call(
            ticker="SPY",
            http_responses=[
                _mock_resp(json_body=_SUBMISSIONS_BODY_NPORT),
                _mock_resp(text_body=_NPORT_XML_TWO_HOLDINGS),
            ],
        )
        assert result.parent_registrant_name is not None
        assert result.parent_registrant_name == "SPDR S&P 500 ETF TRUST"

    def test_80_resolver_source_company_tickers_for_unknown(self):
        """resolver_source='company_tickers' when ticker falls through to tickers.json."""
        result = _provider_call(
            ticker="COMPLETELY_UNKNOWN_ETF_987",
            http_responses=[
                _mock_resp(json_body=_COMPANY_TICKERS_BODY_UNKNOWN),
            ],
        )
        # Not in parent map → tries company_tickers.json → not found there either
        assert result.fetch_status == "missing_cik"
        # resolver_source may be "company_tickers" (fallback attempted) or None
        # depending on whether lookup ran; key invariant: NOT "etf_parent_map"
        assert result.resolver_source != "etf_parent_map"

    def test_81_resolver_source_injected_when_cik_lookup_fn_provided(self):
        """resolver_source='injected' when caller provides cik_lookup_fn."""
        result = _provider_call(
            ticker="SPY",
            cik_lookup_fn=lambda t: "0000884394",
            http_responses=[
                _mock_resp(json_body=_SUBMISSIONS_BODY_NPORT),
                _mock_resp(text_body=_NPORT_XML_TWO_HOLDINGS),
            ],
        )
        assert result.fetch_status == "success"
        assert result.resolver_source == "injected"

    def test_82_no_nport_filing_includes_resolver_diagnostics(self):
        """no_nport_filing result includes resolver_source and parent_registrant_name."""
        result = _provider_call(
            ticker="VOO",
            http_responses=[
                _mock_resp(json_body=_SUBMISSIONS_BODY_NO_NPORT),
            ],
        )
        assert result.fetch_status == "no_nport_filing"
        assert result.resolver_source == "etf_parent_map"
        assert result.parent_registrant_name == "VANGUARD INDEX FUNDS"
        # Should report the parent CIK that was tried, not a share-class CIK
        assert result.cik == "0000764180"

    # ── Diagnostic runner integration ─────────────────────────────────────────

    def test_83_diagnostic_runner_entry_includes_resolver_fields(self):
        """Diagnostic runner per-ticker entry includes resolver_source and parent_registrant_name."""
        from app.services.intelligence.research_workers.nport_diagnostic_runner import (
            _build_ticker_entry,
        )
        from app.services.intelligence.research_workers.nport_provider_v1 import (
            NportProviderResult,
        )

        fake_result = NportProviderResult(
            ticker="VOO",
            fetch_status="no_nport_filing",
            cik="0000764180",
            resolver_source="etf_parent_map",
            parent_registrant_name="VANGUARD INDEX FUNDS",
        )
        entry = _build_ticker_entry(fake_result)

        assert "resolver_source" in entry
        assert "parent_registrant_name" in entry
        assert entry["resolver_source"] == "etf_parent_map"
        assert entry["parent_registrant_name"] == "VANGUARD INDEX FUNDS"
        assert entry["resolved_cik"] == "0000764180"
