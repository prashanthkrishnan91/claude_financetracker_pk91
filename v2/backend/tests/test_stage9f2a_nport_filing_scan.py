"""Stage 9F.2a — ETF NPORT multi-filing scan tests.

Coverage (all fixture-based; zero live SEC calls):

  Multi-filing scan — identity match on second filing:
 S1. Filing #1 has wrong series; filing #2 matches → success, identity_verified=True,
     matching_filing_rank=2, filings_scanned_count=2.
 S2. Filing #1 matches immediately → success, matching_filing_rank=1,
     filings_scanned_count=1 (baseline, single-filing behavior preserved).
 S3. All scanned filings have wrong series → series_identity_not_proven, no holdings,
     candidate_identity_failures contains entries for each scanned filing.
 S4. candidate_identity_failures includes filing_rank for each mismatch entry.

  Scan budget exhaustion:
 S5. Budget exhausted before completing scan → series_identity_scan_budget_exhausted,
     no holdings, filings_scanned_count reflects how many were tried.
 S6. scan_limit_reached=True when budget exhausted, False when match found.
 S7. series_identity_scan_budget_exhausted result never contains holdings.
 S8. Budget exhausted on inner-loop check (after submissions fetch) → precise status.

  XLE-style scan (SPDR Series Trust with wrong latest filing):
 S9. XLE: latest filing is SPDR China ETF (wrong) → scan continues to filing #2
     which has Energy Select Sector → success with identity_verified=True.
S10. XLE: all scanned filings are wrong series → series_identity_not_proven,
     diagnostics show candidate_identity_failures with detected_series_names.

  VGT/VHT/VIS-style scan (Vanguard World Fund with wrong latest filing):
S11. VGT: latest filing is Vanguard Value Index Fund (wrong) → scan continues to
     filing #2 with Vanguard Information Technology → success, identity_verified=True.
S12. VHT: latest filing wrong → scan finds health care series → success.
S13. VIS: latest filing wrong → scan finds industrials series → success.
S14. VGT: all scanned filings wrong → series_identity_not_proven.

  Standalone trusts (unchanged behavior):
S15. SPY: filing scan immediately succeeds on first filing (standalone_trust).
S16. QQQ: same standalone trust behavior.

  Commodity trust (unchanged behavior):
S17. GLD: no NPORT filings → commodity_trust_or_no_nport_data (unchanged).

  VYM sec_error path preserves resolver diagnostics:
S18. VYM sec_error on submissions → sec_error status with resolver_source preserved.

  Scan field propagation:
S19. filings_scanned_count=0 when submissions fetch fails (no filing processed).
S20. Diagnostic runner _build_ticker_entry surfaces filings_scanned_count,
     matching_filing_rank, scan_limit_reached.
S21. No live SEC calls in any test.

  max_filings_to_scan cap:
S22. Submissions has 5 NPORT filings; max_filings_to_scan=2 → only 2 scanned.
S23. _collect_recent_nport_filings returns filings in order, newest first.
"""
from __future__ import annotations

from typing import Any, Optional
from unittest.mock import MagicMock

import pytest


# ── XML fixtures ─────────────────────────────────────────────────────────────

_SPDR_CHINA_ETF_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<edgarSubmission xmlns="http://www.sec.gov/edgar/nport">
  <formData>
    <genInfo>
      <regName>SPDR SERIES TRUST</regName>
      <seriesName>SPDR(R) S&amp;P(R) CHINA ETF</seriesName>
      <seriesId>S000009001</seriesId>
      <repPdDate>2026-01-31</repPdDate>
    </genInfo>
    <fundInfo>
      <totAssets>500000000.00</totAssets>
      <netAssets>498000000.00</netAssets>
    </fundInfo>
    <invstOrSecs>
      <invstOrSec>
        <name>Alibaba Group Holding Ltd</name>
        <cusip>01609W102</cusip>
        <valUSD>50000000.00</valUSD>
        <pctVal>10.000000</pctVal>
        <curCd>USD</curCd>
        <assetCat>EC</assetCat>
      </invstOrSec>
    </invstOrSecs>
  </formData>
</edgarSubmission>
"""

_ENERGY_SELECT_SECTOR_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<edgarSubmission xmlns="http://www.sec.gov/edgar/nport">
  <formData>
    <genInfo>
      <regName>SPDR SERIES TRUST</regName>
      <seriesName>Energy Select Sector SPDR Fund</seriesName>
      <seriesId>S000009002</seriesId>
      <repPdDate>2025-12-31</repPdDate>
    </genInfo>
    <fundInfo>
      <totAssets>40000000000.00</totAssets>
      <netAssets>39900000000.00</netAssets>
    </fundInfo>
    <invstOrSecs>
      <invstOrSec>
        <name>Exxon Mobil Corp</name>
        <cusip>30231G102</cusip>
        <valUSD>5000000000.00</valUSD>
        <pctVal>12.500000</pctVal>
        <curCd>USD</curCd>
        <assetCat>EC</assetCat>
      </invstOrSec>
      <invstOrSec>
        <name>Chevron Corp</name>
        <cusip>166764100</cusip>
        <valUSD>4000000000.00</valUSD>
        <pctVal>10.000000</pctVal>
        <curCd>USD</curCd>
        <assetCat>EC</assetCat>
      </invstOrSec>
    </invstOrSecs>
  </formData>
</edgarSubmission>
"""

_VANGUARD_VALUE_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<edgarSubmission xmlns="http://www.sec.gov/edgar/nport">
  <formData>
    <genInfo>
      <regName>VANGUARD WORLD FUND</regName>
      <seriesName>Vanguard Value Index Fund</seriesName>
      <seriesId>S000009905</seriesId>
      <repPdDate>2026-01-31</repPdDate>
    </genInfo>
    <fundInfo>
      <totAssets>120000000000.00</totAssets>
      <netAssets>119000000000.00</netAssets>
    </fundInfo>
    <invstOrSecs>
      <invstOrSec>
        <name>Berkshire Hathaway Inc</name>
        <cusip>084670702</cusip>
        <valUSD>12000000000.00</valUSD>
        <pctVal>10.000000</pctVal>
        <curCd>USD</curCd>
        <assetCat>EC</assetCat>
      </invstOrSec>
    </invstOrSecs>
  </formData>
</edgarSubmission>
"""

_VGT_INFO_TECH_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<edgarSubmission xmlns="http://www.sec.gov/edgar/nport">
  <formData>
    <genInfo>
      <regName>VANGUARD WORLD FUND</regName>
      <seriesName>Vanguard Information Technology Index Fund</seriesName>
      <seriesId>S000009902</seriesId>
      <repPdDate>2025-12-31</repPdDate>
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

_VHT_HEALTH_CARE_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<edgarSubmission xmlns="http://www.sec.gov/edgar/nport">
  <formData>
    <genInfo>
      <regName>VANGUARD WORLD FUND</regName>
      <seriesName>Vanguard Health Care Index Fund</seriesName>
      <seriesId>S000009904</seriesId>
      <repPdDate>2025-12-31</repPdDate>
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

_VIS_INDUSTRIALS_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<edgarSubmission xmlns="http://www.sec.gov/edgar/nport">
  <formData>
    <genInfo>
      <regName>VANGUARD WORLD FUND</regName>
      <seriesName>Vanguard Industrials Index Fund</seriesName>
      <seriesId>S000009903</seriesId>
      <repPdDate>2025-12-31</repPdDate>
    </genInfo>
    <fundInfo>
      <totAssets>8000000000.00</totAssets>
      <netAssets>7980000000.00</netAssets>
    </fundInfo>
    <invstOrSecs>
      <invstOrSec>
        <name>United Parcel Service Inc</name>
        <cusip>911312106</cusip>
        <valUSD>800000000.00</valUSD>
        <pctVal>10.000000</pctVal>
        <curCd>USD</curCd>
        <assetCat>EC</assetCat>
      </invstOrSec>
    </invstOrSecs>
  </formData>
</edgarSubmission>
"""

_SPY_XML = """\
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

_QQQ_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<edgarSubmission xmlns="http://www.sec.gov/edgar/nport">
  <formData>
    <genInfo>
      <repPdDate>2026-01-31</repPdDate>
    </genInfo>
    <fundInfo>
      <totAssets>300000000000.00</totAssets>
      <netAssets>299000000000.00</netAssets>
    </fundInfo>
    <invstOrSecs>
      <invstOrSec>
        <name>Apple Inc</name>
        <cusip>037833100</cusip>
        <valUSD>30000000000.00</valUSD>
        <pctVal>10.000000</pctVal>
        <curCd>USD</curCd>
        <assetCat>EC</assetCat>
      </invstOrSec>
    </invstOrSecs>
  </formData>
</edgarSubmission>
"""

_WRONG_VANGUARD_XML = """\
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


# ── Submissions fixtures ──────────────────────────────────────────────────────

def _make_submissions_with_n_nport(n: int, cik: str = "0001168164") -> dict:
    """Build a submissions body with n NPORT-P filings, newest first."""
    forms, dates, accs, rpts, pdocs = [], [], [], [], []
    for i in range(n):
        forms.append("NPORT-P")
        dates.append(f"2026-{3 - i:02d}-15")
        accs.append(f"{cik}-26-{100 + i:06d}")
        rpts.append(f"2026-{2 - i:02d}-28")
        pdocs.append(f"primary_doc_{i}.xml")
    # Add a non-NPORT filing at the end
    forms.append("10-K")
    dates.append("2025-12-01")
    accs.append(f"{cik}-25-000001")
    rpts.append("2025-11-30")
    pdocs.append("annual.htm")
    return {
        "filings": {
            "recent": {
                "form": forms,
                "filingDate": dates,
                "accessionNumber": accs,
                "reportDate": rpts,
                "primaryDocument": pdocs,
            }
        }
    }


_SUBMISSIONS_NO_NPORT = {
    "filings": {
        "recent": {
            "form": ["10-K", "8-K"],
            "filingDate": ["2025-12-01", "2025-11-01"],
            "accessionNumber": ["0001222333-25-000001", "0001222333-25-000002"],
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


def _provider_call(
    ticker: str,
    http_responses: list,
    user_agent: str = "test@example.com",
    max_filings_to_scan: int = 12,
    max_requests_per_ticker: int = 20,
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


# ── S1-S4: Multi-filing scan — identity match on second filing ────────────────


class TestMultiFilingScanMatchOnSecondFiling:
    """Provider scans past first wrong-series filing and succeeds on second."""

    def test_s1_xle_wrong_first_filing_right_second_filing(self):
        """XLE filing #1 = SPDR China ETF, filing #2 = Energy Select Sector → success."""
        sub = _make_submissions_with_n_nport(2, cik="0001168164")
        result = _provider_call(
            "XLE",
            http_responses=[
                _mock_resp(json_body=sub),
                _mock_resp(text_body=_SPDR_CHINA_ETF_XML),    # filing #1 wrong
                _mock_resp(text_body=_ENERGY_SELECT_SECTOR_XML),  # filing #2 right
            ],
        )
        assert result.fetch_status == "success", result.error_message
        assert result.identity_verified is True
        assert result.identity_status == "success_identity_verified"
        assert result.matching_filing_rank == 2
        assert result.filings_scanned_count == 2
        assert result.scan_limit_reached is False
        assert len(result.holdings) > 0
        assert result.detected_series_name == "Energy Select Sector SPDR Fund"

    def test_s1_candidate_identity_failures_populated_for_first_wrong_filing(self):
        """candidate_identity_failures contains the first wrong filing's entry."""
        sub = _make_submissions_with_n_nport(2, cik="0001168164")
        result = _provider_call(
            "XLE",
            http_responses=[
                _mock_resp(json_body=sub),
                _mock_resp(text_body=_SPDR_CHINA_ETF_XML),
                _mock_resp(text_body=_ENERGY_SELECT_SECTOR_XML),
            ],
        )
        assert len(result.candidate_identity_failures) == 1
        fail = result.candidate_identity_failures[0]
        assert fail["filing_rank"] == 1
        assert "SPDR" in fail["detected_series_name"] or "China" in fail["detected_series_name"]

    def test_s2_match_on_first_filing_rank_one(self):
        """When first filing matches, matching_filing_rank=1 (baseline behavior)."""
        sub = _make_submissions_with_n_nport(1, cik="0001168164")
        result = _provider_call(
            "XLE",
            http_responses=[
                _mock_resp(json_body=sub),
                _mock_resp(text_body=_ENERGY_SELECT_SECTOR_XML),
            ],
        )
        assert result.fetch_status == "success"
        assert result.identity_verified is True
        assert result.matching_filing_rank == 1
        assert result.filings_scanned_count == 1

    def test_s3_all_wrong_filings_returns_not_proven(self):
        """All scanned filings have wrong series → series_identity_not_proven."""
        sub = _make_submissions_with_n_nport(3, cik="0001168164")
        result = _provider_call(
            "XLE",
            http_responses=[
                _mock_resp(json_body=sub),
                _mock_resp(text_body=_SPDR_CHINA_ETF_XML),   # #1 wrong
                _mock_resp(text_body=_SPDR_CHINA_ETF_XML),   # #2 wrong
                _mock_resp(text_body=_SPDR_CHINA_ETF_XML),   # #3 wrong
            ],
        )
        assert result.fetch_status == "series_identity_not_proven"
        assert result.identity_verified is False
        assert result.holdings == []
        assert result.filings_scanned_count == 3
        assert len(result.candidate_identity_failures) == 3

    def test_s4_candidate_identity_failures_include_filing_rank(self):
        """Each entry in candidate_identity_failures includes filing_rank."""
        sub = _make_submissions_with_n_nport(2, cik="0001168164")
        result = _provider_call(
            "XLE",
            http_responses=[
                _mock_resp(json_body=sub),
                _mock_resp(text_body=_SPDR_CHINA_ETF_XML),
                _mock_resp(text_body=_SPDR_CHINA_ETF_XML),
            ],
        )
        assert result.fetch_status == "series_identity_not_proven"
        ranks = [f["filing_rank"] for f in result.candidate_identity_failures]
        assert ranks == [1, 2]


# ── S5-S8: Scan budget exhaustion ─────────────────────────────────────────────


class TestScanBudgetExhaustion:
    """Budget exhaustion during multi-filing scan returns precise diagnostic."""

    def test_s5_budget_exhausted_returns_precise_status(self):
        """Budget=2 (1 sub + 1 XML): second filing never fetched → budget exhausted."""
        sub = _make_submissions_with_n_nport(3, cik="0001168164")
        # max_requests=2: submissions(1) + filing#1 XML(1) = budget gone
        result = _provider_call(
            "XLE",
            http_responses=[
                _mock_resp(json_body=sub),
                _mock_resp(text_body=_SPDR_CHINA_ETF_XML),  # filing #1 wrong
            ],
            max_requests_per_ticker=2,
        )
        assert result.fetch_status == "series_identity_scan_budget_exhausted"
        assert result.identity_verified is False
        assert result.holdings == []
        assert result.filings_scanned_count >= 1

    def test_s6_scan_limit_reached_true_when_budget_exhausted(self):
        """scan_limit_reached=True when budget is exhausted."""
        sub = _make_submissions_with_n_nport(3, cik="0001168164")
        result = _provider_call(
            "XLE",
            http_responses=[
                _mock_resp(json_body=sub),
                _mock_resp(text_body=_SPDR_CHINA_ETF_XML),
            ],
            max_requests_per_ticker=2,
        )
        assert result.scan_limit_reached is True

    def test_s6b_scan_limit_reached_false_on_success(self):
        """scan_limit_reached=False when identity match found."""
        sub = _make_submissions_with_n_nport(1, cik="0001168164")
        result = _provider_call(
            "XLE",
            http_responses=[
                _mock_resp(json_body=sub),
                _mock_resp(text_body=_ENERGY_SELECT_SECTOR_XML),
            ],
        )
        assert result.scan_limit_reached is False
        assert result.fetch_status == "success"

    def test_s7_scan_budget_exhausted_never_returns_holdings(self):
        """series_identity_scan_budget_exhausted result has empty holdings."""
        sub = _make_submissions_with_n_nport(3, cik="0001168164")
        result = _provider_call(
            "XLE",
            http_responses=[
                _mock_resp(json_body=sub),
                _mock_resp(text_body=_SPDR_CHINA_ETF_XML),
            ],
            max_requests_per_ticker=2,
        )
        assert result.holdings == []

    def test_s8_budget_check_at_inner_loop_start(self):
        """Budget exhausted before second filing fetch → scan_budget_exhausted."""
        # 2 NPORT filings available; budget=2 allows submissions+XML#1 only
        sub = _make_submissions_with_n_nport(2, cik="0001168164")
        result = _provider_call(
            "XLE",
            http_responses=[
                _mock_resp(json_body=sub),               # submissions (req 1)
                _mock_resp(text_body=_SPDR_CHINA_ETF_XML),  # filing #1 XML (req 2)
                # No more requests available — filing #2 can't be fetched
            ],
            max_requests_per_ticker=2,
        )
        # Filing #1 was processed (mismatch), filing #2 budget check fires → exhausted
        assert result.fetch_status == "series_identity_scan_budget_exhausted"
        assert result.filings_scanned_count == 1  # only filing #1 was completed
        assert result.scan_limit_reached is True


# ── S9-S10: XLE-style scan ────────────────────────────────────────────────────


class TestXLEStyleScan:
    """XLE scanning past wrong SPDR China ETF filing to find Energy Select Sector."""

    def test_s9_xle_scans_past_wrong_filing_to_energy_sector(self):
        """XLE: skip SPDR China ETF filing, find Energy Select Sector on filing #2."""
        sub = _make_submissions_with_n_nport(2, cik="0001168164")
        result = _provider_call(
            "XLE",
            http_responses=[
                _mock_resp(json_body=sub),
                _mock_resp(text_body=_SPDR_CHINA_ETF_XML),
                _mock_resp(text_body=_ENERGY_SELECT_SECTOR_XML),
            ],
        )
        assert result.fetch_status == "success"
        assert result.identity_verified is True
        assert result.identity_status == "success_identity_verified"
        assert result.matching_filing_rank == 2
        assert result.detected_series_name == "Energy Select Sector SPDR Fund"
        assert result.holdings  # non-empty
        # Verify holdings are from the matched Energy filing
        holding_names = [h.name for h in result.holdings]
        assert any("Exxon" in n or "Chevron" in n for n in holding_names)

    def test_s10_xle_all_wrong_returns_not_proven_with_diagnostics(self):
        """XLE: all scanned filings wrong → series_identity_not_proven with diagnostics."""
        sub = _make_submissions_with_n_nport(2, cik="0001168164")
        result = _provider_call(
            "XLE",
            http_responses=[
                _mock_resp(json_body=sub),
                _mock_resp(text_body=_SPDR_CHINA_ETF_XML),
                _mock_resp(text_body=_SPDR_CHINA_ETF_XML),
            ],
        )
        assert result.fetch_status == "series_identity_not_proven"
        assert result.identity_verified is False
        assert result.holdings == []
        # Diagnostics: candidate_identity_failures explains what was found
        assert len(result.candidate_identity_failures) == 2
        for fail in result.candidate_identity_failures:
            assert "detected_series_name" in fail
            assert "identity_mismatch_reason" in fail
            assert "filing_rank" in fail


# ── S11-S14: VGT/VHT/VIS-style scan ─────────────────────────────────────────


class TestVanguardSectorScan:
    """Vanguard World Fund sector ETFs scanning past Vanguard Value latest filing."""

    def test_s11_vgt_scans_past_vanguard_value_to_info_tech(self):
        """VGT: skip Vanguard Value filing, find VGT Info Tech series on filing #2."""
        sub = _make_submissions_with_n_nport(2, cik="0000036405")
        result = _provider_call(
            "VGT",
            http_responses=[
                _mock_resp(json_body=sub),
                _mock_resp(text_body=_VANGUARD_VALUE_XML),       # filing #1 wrong
                _mock_resp(text_body=_VGT_INFO_TECH_XML),        # filing #2 right
            ],
        )
        assert result.fetch_status == "success"
        assert result.identity_verified is True
        assert result.matching_filing_rank == 2
        assert result.filings_scanned_count == 2
        assert result.detected_series_name == "Vanguard Information Technology Index Fund"
        holding_names = [h.name for h in result.holdings]
        assert any("Apple" in n or "Microsoft" in n for n in holding_names)

    def test_s12_vht_scans_past_vanguard_value_to_health_care(self):
        """VHT: skip Vanguard Value filing, find VHT Health Care series on filing #2."""
        sub = _make_submissions_with_n_nport(2, cik="0000036405")
        result = _provider_call(
            "VHT",
            http_responses=[
                _mock_resp(json_body=sub),
                _mock_resp(text_body=_VANGUARD_VALUE_XML),
                _mock_resp(text_body=_VHT_HEALTH_CARE_XML),
            ],
        )
        assert result.fetch_status == "success"
        assert result.identity_verified is True
        assert result.matching_filing_rank == 2
        assert result.detected_series_name == "Vanguard Health Care Index Fund"

    def test_s13_vis_scans_past_vanguard_value_to_industrials(self):
        """VIS: skip Vanguard Value filing, find VIS Industrials series on filing #2."""
        sub = _make_submissions_with_n_nport(2, cik="0000036405")
        result = _provider_call(
            "VIS",
            http_responses=[
                _mock_resp(json_body=sub),
                _mock_resp(text_body=_VANGUARD_VALUE_XML),
                _mock_resp(text_body=_VIS_INDUSTRIALS_XML),
            ],
        )
        assert result.fetch_status == "success"
        assert result.identity_verified is True
        assert result.matching_filing_rank == 2
        assert result.detected_series_name == "Vanguard Industrials Index Fund"

    def test_s14_vgt_all_wrong_returns_not_proven(self):
        """VGT: all scanned Vanguard World Fund filings are wrong → not_proven."""
        sub = _make_submissions_with_n_nport(3, cik="0000036405")
        result = _provider_call(
            "VGT",
            http_responses=[
                _mock_resp(json_body=sub),
                _mock_resp(text_body=_WRONG_VANGUARD_XML),
                _mock_resp(text_body=_WRONG_VANGUARD_XML),
                _mock_resp(text_body=_WRONG_VANGUARD_XML),
            ],
        )
        assert result.fetch_status == "series_identity_not_proven"
        assert result.identity_verified is False
        assert result.holdings == []
        assert result.filings_scanned_count == 3
        assert len(result.candidate_identity_failures) == 3


# ── S15-S16: Standalone trusts unchanged ─────────────────────────────────────


class TestStandaloneTrustUnchanged:
    """SPY/QQQ standalone trust behavior preserved — identity assumed immediately."""

    def test_s15_spy_succeeds_on_first_filing(self):
        """SPY: identity assumed on first filing, matching_filing_rank=1."""
        sub = _make_submissions_with_n_nport(1, cik="0000884394")
        result = _provider_call(
            "SPY",
            http_responses=[
                _mock_resp(json_body=sub),
                _mock_resp(text_body=_SPY_XML),
            ],
        )
        assert result.fetch_status == "success"
        assert result.identity_status == "success_identity_assumed_single_series"
        assert result.identity_verified is True
        assert result.matching_filing_rank == 1
        assert result.filings_scanned_count == 1
        assert len(result.holdings) > 0

    def test_s16_qqq_succeeds_on_first_filing(self):
        """QQQ: identity assumed on first filing (standalone trust)."""
        sub = _make_submissions_with_n_nport(1, cik="0001067839")
        result = _provider_call(
            "QQQ",
            http_responses=[
                _mock_resp(json_body=sub),
                _mock_resp(text_body=_QQQ_XML),
            ],
        )
        assert result.fetch_status == "success"
        assert result.identity_status == "success_identity_assumed_single_series"
        assert result.identity_verified is True
        assert result.matching_filing_rank == 1


# ── S17: Commodity trust unchanged ───────────────────────────────────────────


class TestCommodityTrustUnchanged:
    """GLD commodity trust path returns commodity_trust_or_no_nport_data."""

    def test_s17_gld_no_nport_filing(self):
        """GLD: no NPORT-P filings → commodity_trust_or_no_nport_data."""
        result = _provider_call(
            "GLD",
            http_responses=[
                _mock_resp(json_body=_SUBMISSIONS_NO_NPORT),
            ],
        )
        assert result.fetch_status == "commodity_trust_or_no_nport_data"
        assert result.identity_verified is False
        assert result.holdings == []
        # Scan fields: no filing was processed
        assert result.filings_scanned_count == 0
        assert result.matching_filing_rank is None


# ── S18: VYM sec_error preserves diagnostics ─────────────────────────────────


class TestVYMSecErrorDiagnostics:
    """VYM sec_error on submissions preserves resolver_source and diagnostics."""

    def test_s18_vym_sec_error_preserves_resolver_diagnostics(self):
        """VYM 404 on submissions → sec_error with parent_registrant_name and CIK."""
        from httpx import HTTPStatusError
        err_resp = MagicMock()
        err_resp.status_code = 404
        err_resp.raise_for_status.side_effect = HTTPStatusError(
            "404", request=MagicMock(), response=err_resp
        )
        result = _provider_call(
            "VYM",
            http_responses=[err_resp],
        )
        # The resolver map entry for VYM sets resolver_source to etf_parent_map
        assert result.resolver_source == "etf_parent_map"
        assert result.parent_registrant_name is not None
        assert "VANGUARD" in result.parent_registrant_name.upper()
        assert result.fetch_status == "sec_error"
        assert result.holdings == []
        # candidate_ciks_tried is populated even on early sec_error
        assert len(result.candidate_ciks_tried) >= 1


# ── S19-S21: Scan field propagation ──────────────────────────────────────────


class TestScanFieldPropagation:
    """Scan diagnostic fields are populated correctly across paths."""

    def test_s19_filings_scanned_count_zero_when_submissions_fails(self):
        """filings_scanned_count=0 when submissions fetch returns error (no filing processed)."""
        from httpx import HTTPStatusError
        err_resp = MagicMock()
        err_resp.status_code = 500
        err_resp.raise_for_status.side_effect = HTTPStatusError(
            "500", request=MagicMock(), response=err_resp
        )
        result = _provider_call(
            "XLE",
            http_responses=[err_resp],
        )
        assert result.filings_scanned_count == 0
        assert result.matching_filing_rank is None

    def test_s20_diagnostic_runner_surfaces_scan_fields(self):
        """_build_ticker_entry includes filings_scanned_count, matching_filing_rank, scan_limit_reached."""
        from app.services.intelligence.research_workers.nport_diagnostic_runner import _build_ticker_entry
        from app.services.intelligence.research_workers.nport_provider_v1 import (
            NportProviderResult,
        )
        result = NportProviderResult(
            ticker="XLE",
            fetch_status="success",
            filings_scanned_count=3,
            matching_filing_rank=2,
            scan_limit_reached=False,
        )
        entry = _build_ticker_entry(result)
        assert entry["filings_scanned_count"] == 3
        assert entry["matching_filing_rank"] == 2
        assert entry["scan_limit_reached"] is False

    def test_s21_no_live_sec_calls(self):
        """No live SEC calls in any test — all use injected http_get_fn."""
        # This test is a documentation invariant — verified by all other tests
        # using http_responses (which raise on unexpected calls).
        pass


# ── S22-S23: max_filings_to_scan cap ─────────────────────────────────────────


class TestMaxFilingsScanCap:
    """max_filings_to_scan limits how many filings are collected from submissions."""

    def test_s22_max_filings_to_scan_limits_collection(self):
        """With 5 NPORT filings available and max_filings_to_scan=2, only 2 are tried."""
        from app.services.intelligence.research_workers.nport_provider_v1 import (
            _collect_recent_nport_filings,
        )
        sub = _make_submissions_with_n_nport(5)
        filings = _collect_recent_nport_filings(sub, max_filings=2)
        assert len(filings) == 2

    def test_s23_collect_recent_nport_filings_returns_newest_first(self):
        """_collect_recent_nport_filings preserves submissions order (newest first)."""
        from app.services.intelligence.research_workers.nport_provider_v1 import (
            _collect_recent_nport_filings,
        )
        sub = _make_submissions_with_n_nport(3)
        filings = _collect_recent_nport_filings(sub, max_filings=3)
        assert len(filings) == 3
        # First filing should be the newest (index 0 in submissions)
        dates = [f["filing_date"] for f in filings]
        assert dates[0] >= dates[1] >= dates[2]

    def test_s23b_collect_recent_no_nport_returns_empty(self):
        """_collect_recent_nport_filings returns [] when no NPORT forms found."""
        from app.services.intelligence.research_workers.nport_provider_v1 import (
            _collect_recent_nport_filings,
        )
        filings = _collect_recent_nport_filings(_SUBMISSIONS_NO_NPORT, max_filings=12)
        assert filings == []

    def test_s23c_provider_respects_max_filings_to_scan_config(self):
        """Provider uses max_filings_to_scan=1 to scan only first filing."""
        sub = _make_submissions_with_n_nport(3, cik="0001168164")
        result = _provider_call(
            "XLE",
            http_responses=[
                _mock_resp(json_body=sub),
                _mock_resp(text_body=_SPDR_CHINA_ETF_XML),  # only filing #1 scanned
                # filing #2 would be Energy sector but is not fetched
            ],
            max_filings_to_scan=1,
        )
        # With max=1, only filing #1 (wrong series) is tried → not_proven
        assert result.fetch_status == "series_identity_not_proven"
        assert result.filings_scanned_count == 1


# ── Invariant guard ───────────────────────────────────────────────────────────


class TestInvariants:
    """Hard invariants that must hold across all scan scenarios."""

    def test_identity_scan_budget_exhausted_never_has_holdings(self):
        """series_identity_scan_budget_exhausted always returns empty holdings."""
        sub = _make_submissions_with_n_nport(5, cik="0001168164")
        result = _provider_call(
            "XLE",
            http_responses=[
                _mock_resp(json_body=sub),
                _mock_resp(text_body=_SPDR_CHINA_ETF_XML),
            ],
            max_requests_per_ticker=2,
        )
        assert result.fetch_status == "series_identity_scan_budget_exhausted"
        assert result.holdings == []
        assert result.identity_verified is False

    def test_series_identity_not_proven_never_has_holdings(self):
        """series_identity_not_proven always returns empty holdings (unchanged invariant)."""
        sub = _make_submissions_with_n_nport(1, cik="0001168164")
        result = _provider_call(
            "XLE",
            http_responses=[
                _mock_resp(json_body=sub),
                _mock_resp(text_body=_SPDR_CHINA_ETF_XML),
            ],
        )
        assert result.fetch_status == "series_identity_not_proven"
        assert result.holdings == []

    def test_matching_filing_rank_none_when_no_match(self):
        """matching_filing_rank is None when no filing matched."""
        sub = _make_submissions_with_n_nport(1, cik="0001168164")
        result = _provider_call(
            "XLE",
            http_responses=[
                _mock_resp(json_body=sub),
                _mock_resp(text_body=_SPDR_CHINA_ETF_XML),
            ],
        )
        assert result.matching_filing_rank is None

    def test_success_has_nonzero_filings_scanned_count(self):
        """On success, filings_scanned_count >= 1."""
        sub = _make_submissions_with_n_nport(1, cik="0001168164")
        result = _provider_call(
            "XLE",
            http_responses=[
                _mock_resp(json_body=sub),
                _mock_resp(text_body=_ENERGY_SELECT_SECTOR_XML),
            ],
        )
        assert result.fetch_status == "success"
        assert result.filings_scanned_count >= 1
