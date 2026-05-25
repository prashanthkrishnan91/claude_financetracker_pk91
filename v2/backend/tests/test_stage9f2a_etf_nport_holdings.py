"""Stage 9F.2a — SEC NPORT-P ETF Holdings Evidence Lane tests.

Coverage (all fixture-based; zero live SEC calls):

  Provider tests (nport_provider_v1):
   1. Successful NPORT fixture → multiple holdings parsed.
   2. Missing CIK fails closed (missing_cik status).
   3. No NPORT-P filing in submissions → no_nport_filing status.
   4. Malformed XML body → filing_not_parseable status.
   5. Empty XML body → filing_not_parseable status.
   6. No holding elements in parsed XML → no_holdings_found status.
   7. GLD special-case → commodity_trust_or_no_nport_data (at no-filing step).
   8. GLD special-case → commodity_trust_or_no_nport_data (at zero-holdings step).
   9. HTTP timeout → timeout status; never raises.
  10. Unexpected exception → error status; never raises.
  11. Empty user_agent → sec_error; never raises.
  12. Weight derivation: weights absent + totAssets present → derived weights.
  13. Weight derivation: weights absent + totAssets absent → no derivation.
  14. Weight derivation: pctVal present → direct (no derivation).
  15. ISIN and ticker identifiers parsed from <identifiers> block.
  16. countryOfRisk parsed from holding element.
  17. CIK injected via cik_lookup_fn (no seed-map branch).
  18. Request count cap respected when budget exhausted.

  Adapter tests (etf_nport_adapter_v1):
  19. Success result → WorkerOutput shape valid (all required fields set).
  20. Artifact uses correct artifact_type, skill_pack, model_version, scope_kind.
  21. SourceRecord: source_kind=sec_filing, provider_name=sec_edgar.
  22. FactRecords: fact_kind=metric_observation, axis_hint=exposure per holding.
  23. FactRecord payload contains holding_name, provider, lane; no forbidden keys.
  24. Sector status always MISSING in every FactRecord payload.
  25. Geography status MISSING when countryOfRisk absent; AVAILABLE when present.
  26. No raw XML or provider payload in artifact_payload.
  27. safe_for_decision not present in payload (not emitted by adapter).
  28. synthesis_ready not present in payload (not emitted by adapter).
  29. Non-success provider result → thin honest adapter result; sources/facts empty.
  30. holdings_count in artifact_payload matches number of holdings.
  31. weights_available / weights_derived faithfully reflected.
  32. freshness=FRESH when report_period_date within 120 days.
  33. freshness=STALE when report_period_date older than 120 days.
  34. Replay idempotency key is deterministic (same inputs → same key).
  35. Source fingerprint changes when holdings change.
  36. Limitations list non-empty for every result.
  37. Evidence summary non-empty for success path.
  38. GLD commodity trust → no-data adapter result.
  39. build_etf_nport_worker_output passes WorkerOutput validation.

  Runner tests (evidence_lane_runner_v1):
  40. Flag default OFF → run_etf_nport_holdings_evidence returns None immediately.
  41. Non-ETF ticker (equity) skipped honestly (not written as failure).
  42. ETF ticker in known-symbol list → classified as ETF.
  43. Unknown ticker with no holding_context → skipped (not in known list).
  44. holding_context category=etf overrides symbol fallback.
  45. holding_context category=equity returns False even for known ETF symbol.
  46. No holdings returned by provider → skipped write (returns None).
  47. Success path: provider returns holdings → build_etf_nport_worker_output called.
  48. Idempotency key is deterministic from identical holdings fixture.

  Regression:
  49. Importing runner/adapter/provider does not break existing Stage 9F imports.
  50. LANE_ETF_FUND_DATA present in ALL_LANES in provider registry.
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from unittest.mock import MagicMock, patch

import pytest

# ── Helpers ───────────────────────────────────────────────────────────────────

# Minimal valid NPORT-P XML with two equity holdings (pctVal present).
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
        <identifiers>
          <isin value="US0378331005"/>
          <ticker value="AAPL"/>
        </identifiers>
        <valUSD>25000000.00</valUSD>
        <pctVal>5.000000</pctVal>
        <curCd>USD</curCd>
        <assetCat>EC</assetCat>
        <issuerCat>CORP</issuerCat>
        <countryOfRisk>US</countryOfRisk>
      </invstOrSec>
      <invstOrSec>
        <name>Microsoft Corp</name>
        <cusip>594918104</cusip>
        <identifiers>
          <isin value="US5949181045"/>
        </identifiers>
        <valUSD>20000000.00</valUSD>
        <pctVal>4.000000</pctVal>
        <curCd>USD</curCd>
        <assetCat>EC</assetCat>
        <countryOfRisk>US</countryOfRisk>
      </invstOrSec>
    </invstOrSecs>
  </formData>
</edgarSubmission>
"""

# NPORT-P XML without pctVal — forces weight derivation from valUSD/totAssets.
_NPORT_XML_NO_PCT_VAL = """\
<?xml version="1.0" encoding="UTF-8"?>
<edgarSubmission xmlns="http://www.sec.gov/edgar/nport">
  <formData>
    <genInfo>
      <repPdDate>2025-09-30</repPdDate>
    </genInfo>
    <fundInfo>
      <totAssets>1000000.00</totAssets>
      <netAssets>990000.00</netAssets>
    </fundInfo>
    <invstOrSecs>
      <invstOrSec>
        <name>Some Corp</name>
        <cusip>111222333</cusip>
        <valUSD>100000.00</valUSD>
        <curCd>USD</curCd>
        <assetCat>EC</assetCat>
      </invstOrSec>
      <invstOrSec>
        <name>Other Corp</name>
        <cusip>444555666</cusip>
        <valUSD>200000.00</valUSD>
        <curCd>USD</curCd>
        <assetCat>EC</assetCat>
      </invstOrSec>
    </invstOrSecs>
  </formData>
</edgarSubmission>
"""

# NPORT-P XML with no invstOrSec elements.
_NPORT_XML_EMPTY_HOLDINGS = """\
<?xml version="1.0" encoding="UTF-8"?>
<edgarSubmission xmlns="http://www.sec.gov/edgar/nport">
  <formData>
    <genInfo><repPdDate>2025-09-30</repPdDate></genInfo>
    <fundInfo><totAssets>1000000</totAssets></fundInfo>
    <invstOrSecs/>
  </formData>
</edgarSubmission>
"""

# Malformed XML.
_NPORT_XML_MALFORMED = "<edgarSubmission><formData>BROKEN"

# Submission JSON fixture with one NPORT-P filing.
_SUBMISSIONS_BODY = {
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

# Submission JSON fixture with NO NPORT-P filing.
_SUBMISSIONS_BODY_NO_NPORT = {
    "filings": {
        "recent": {
            "form": ["10-K", "8-K"],
            "filingDate": ["2025-03-01", "2025-01-15"],
            "accessionNumber": ["0000884394-25-000001", "0000884394-25-000002"],
            "reportDate": ["2024-12-31", ""],
            "primaryDocument": ["annual.htm", "current.htm"],
        }
    }
}

# Company tickers JSON fixture.
_COMPANY_TICKERS_BODY = {
    "0": {"cik_str": 884394, "ticker": "SPY", "title": "SPDR S&P 500 ETF Trust"},
    "1": {"cik_str": 789019, "ticker": "MSFT", "title": "Microsoft Corp"},
}


def _make_mock_response(json_body=None, text_body=None, status_code=200):
    """Build a mock HTTP response object."""
    resp = MagicMock()
    resp.status_code = status_code
    if json_body is not None:
        resp.json.return_value = json_body
    if text_body is not None:
        resp.text = text_body
    if status_code >= 400:
        from httpx import HTTPStatusError
        err = HTTPStatusError("error", request=MagicMock(), response=resp)
        resp.raise_for_status.side_effect = err
    else:
        resp.raise_for_status.return_value = None
    return resp


def _make_provider_result_success(
    ticker="SPY",
    holdings=None,
    report_period_date="2025-09-30",
    filing_date="2025-11-15",
    weights_available=True,
    weights_derived=False,
    total_assets_usd=500_000_000.0,
):
    """Build a minimal NportProviderResult with is_success=True."""
    from app.services.intelligence.research_workers.nport_provider_v1 import (
        NportFilingMeta,
        NportHolding,
        NportProviderResult,
    )

    if holdings is None:
        holdings = [
            NportHolding(
                name="Apple Inc",
                cusip="037833100",
                isin="US0378331005",
                weight_pct=5.0,
                value_usd=25_000_000.0,
                currency="USD",
                asset_category="EC",
                country_of_risk="US",
            ),
            NportHolding(
                name="Microsoft Corp",
                cusip="594918104",
                weight_pct=4.0,
                value_usd=20_000_000.0,
                currency="USD",
                asset_category="EC",
            ),
        ]

    meta = NportFilingMeta(
        accession_number="0000884394-25-001234",
        form_type="NPORT-P",
        filing_date=filing_date,
        report_period_date=report_period_date,
        primary_doc="primary_doc.xml",
        filing_url="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=884394&type=NPORT-P",
    )
    return NportProviderResult(
        ticker=ticker,
        fetch_status="success",
        cik="0000884394",
        filing_meta=meta,
        holdings=holdings,
        total_assets_usd=total_assets_usd,
        total_reported_value_present=True,
        weights_available=weights_available,
        weights_derived=weights_derived,
        request_count=2,
    )


def _make_worker_input(ticker="SPY"):
    from app.services.intelligence.research_workers.contracts import WorkerInput

    return WorkerInput(
        user_id="test-user",
        ticker=ticker,
        worker_run_id="test-worker-run-id",
        parent_intel_run_id="test-intel-run-id",
    )


def _make_settings(enabled: bool = False, user_agent: str = "test/1.0"):
    """Return a minimal Settings-like object."""
    s = MagicMock()
    s.intel_v3_research_workers_enabled = enabled
    s.intel_v3_etf_nport_evidence_enabled = enabled
    s.sec_edgar_user_agent = user_agent
    return s


# ── Provider tests ────────────────────────────────────────────────────────────


class TestNportProvider:
    """Tests for nport_provider_v1.fetch_etf_nport_holdings (no live HTTP)."""

    def _call(
        self,
        ticker="SPY",
        http_responses=None,
        cik_lookup_fn=None,
        user_agent="test@example.com",
    ):
        from app.services.intelligence.research_workers.nport_provider_v1 import (
            NportProviderConfig,
            fetch_etf_nport_holdings,
        )

        cfg = NportProviderConfig(user_agent=user_agent)
        responses = list(http_responses or [])
        call_count = [0]

        def _http_get(url):
            idx = call_count[0]
            call_count[0] += 1
            if idx >= len(responses):
                raise RuntimeError(f"Unexpected HTTP call #{idx} to {url}")
            return responses[idx]

        return fetch_etf_nport_holdings(
            ticker,
            cfg,
            http_get_fn=_http_get,
            cik_lookup_fn=cik_lookup_fn,
        )

    def test_01_success_two_holdings(self):
        """Successful NPORT fixture parses multiple holdings."""
        result = self._call(
            ticker="SPY",
            cik_lookup_fn=lambda t: "0000884394",
            http_responses=[
                _make_mock_response(json_body=_SUBMISSIONS_BODY),
                _make_mock_response(text_body=_NPORT_XML_TWO_HOLDINGS),
            ],
        )
        assert result.fetch_status == "success"
        assert result.is_success
        assert len(result.holdings) == 2
        names = {h.name for h in result.holdings}
        assert "Apple Inc" in names
        assert "Microsoft Corp" in names
        assert result.request_count == 2  # submissions + XML doc

    def test_02_missing_cik_fails_closed(self):
        """Unknown ticker not in seed map → missing_cik, no holdings."""
        result = self._call(
            ticker="UNKNOWN_ETF_XYZ",
            cik_lookup_fn=lambda t: None,
        )
        assert result.fetch_status == "missing_cik"
        assert not result.is_success
        assert result.holdings == []
        assert "UNKNOWN_ETF_XYZ" in result.error_message

    def test_03_no_nport_filing_fails_closed(self):
        """No NPORT-P/NPORT-EX in submissions → no_nport_filing, never raises."""
        result = self._call(
            ticker="SOME_ETF",
            cik_lookup_fn=lambda t: "0001234567",
            http_responses=[
                _make_mock_response(json_body=_SUBMISSIONS_BODY_NO_NPORT),
            ],
        )
        assert result.fetch_status == "no_nport_filing"
        assert not result.is_success
        assert result.holdings == []

    def test_04_malformed_xml_fails_closed(self):
        """Malformed XML body → filing_not_parseable, never raises."""
        result = self._call(
            ticker="SPY",
            cik_lookup_fn=lambda t: "0000884394",
            http_responses=[
                _make_mock_response(json_body=_SUBMISSIONS_BODY),
                _make_mock_response(text_body=_NPORT_XML_MALFORMED),
            ],
        )
        assert result.fetch_status in ("filing_not_parseable", "no_holdings_found")
        assert not result.is_success

    def test_05_empty_xml_body_fails_closed(self):
        """Empty XML body → fails closed (filing_not_parseable, no_holdings_found, or error)."""
        result = self._call(
            ticker="SPY",
            cik_lookup_fn=lambda t: "0000884394",
            http_responses=[
                _make_mock_response(json_body=_SUBMISSIONS_BODY),
                _make_mock_response(text_body=""),
            ],
        )
        # Any failure status is acceptable; must not be success and must not raise.
        assert result.fetch_status in (
            "filing_not_parseable", "no_holdings_found", "error"
        ), f"Unexpected status: {result.fetch_status}"
        assert not result.is_success

    def test_06_no_holdings_in_xml_fails_closed(self):
        """XML parsed but zero holding elements → no_holdings_found."""
        result = self._call(
            ticker="SPY",
            cik_lookup_fn=lambda t: "0000884394",
            http_responses=[
                _make_mock_response(json_body=_SUBMISSIONS_BODY),
                _make_mock_response(text_body=_NPORT_XML_EMPTY_HOLDINGS),
            ],
        )
        assert result.fetch_status == "no_holdings_found"
        assert not result.is_success

    def test_07_gld_no_filing_commodity_trust_status(self):
        """GLD with no NPORT-P filing → commodity_trust_or_no_nport_data."""
        result = self._call(
            ticker="GLD",
            cik_lookup_fn=lambda t: "0001222333",
            http_responses=[
                _make_mock_response(json_body=_SUBMISSIONS_BODY_NO_NPORT),
            ],
        )
        assert result.fetch_status == "commodity_trust_or_no_nport_data"
        assert not result.is_success

    def test_08_gld_zero_holdings_commodity_trust_status(self):
        """GLD with NPORT filing but zero holdings → commodity_trust_or_no_nport_data."""
        result = self._call(
            ticker="GLD",
            cik_lookup_fn=lambda t: "0001222333",
            http_responses=[
                _make_mock_response(json_body=_SUBMISSIONS_BODY),
                _make_mock_response(text_body=_NPORT_XML_EMPTY_HOLDINGS),
            ],
        )
        assert result.fetch_status == "commodity_trust_or_no_nport_data"
        assert not result.is_success

    def test_09_timeout_never_raises(self):
        """HTTP timeout → timeout status; function never raises."""
        import httpx

        class _TimeoutError(Exception):
            pass

        def _http_get_timeout(url):
            raise TimeoutError("read timed out")

        from app.services.intelligence.research_workers.nport_provider_v1 import (
            NportProviderConfig,
            fetch_etf_nport_holdings,
        )

        cfg = NportProviderConfig(user_agent="test@example.com")
        result = fetch_etf_nport_holdings(
            "SPY",
            cfg,
            http_get_fn=_http_get_timeout,
            cik_lookup_fn=lambda t: "0000884394",
        )
        assert result.fetch_status == "timeout"
        assert not result.is_success

    def test_10_unexpected_exception_never_raises(self):
        """Unexpected exception → error status; never raises."""

        def _bomb(_url):
            raise RuntimeError("unexpected boom")

        from app.services.intelligence.research_workers.nport_provider_v1 import (
            NportProviderConfig,
            fetch_etf_nport_holdings,
        )

        cfg = NportProviderConfig(user_agent="test@example.com")
        result = fetch_etf_nport_holdings(
            "SPY",
            cfg,
            http_get_fn=_bomb,
            cik_lookup_fn=lambda t: "0000884394",
        )
        assert result.fetch_status in ("error", "sec_error", "timeout")
        assert not result.is_success

    def test_11_empty_user_agent_sec_error(self):
        """Empty user_agent immediately fails closed with sec_error."""
        from app.services.intelligence.research_workers.nport_provider_v1 import (
            NportProviderConfig,
            fetch_etf_nport_holdings,
        )

        cfg = NportProviderConfig(user_agent="")
        result = fetch_etf_nport_holdings("SPY", cfg, http_get_fn=lambda u: None)
        assert result.fetch_status == "sec_error"
        assert "User-Agent" in (result.error_message or "")

    def test_12_weight_derivation_when_pct_absent(self):
        """Weights derived from valUSD/totAssets when pctVal absent."""
        result = self._call(
            ticker="SPY",
            cik_lookup_fn=lambda t: "0000884394",
            http_responses=[
                _make_mock_response(json_body=_SUBMISSIONS_BODY),
                _make_mock_response(text_body=_NPORT_XML_NO_PCT_VAL),
            ],
        )
        assert result.fetch_status == "success"
        assert result.weights_derived is True
        assert result.weights_available is True
        for h in result.holdings:
            assert h.weight_pct is not None
            assert h.weight_pct > 0.0

    def test_13_no_derivation_when_total_assets_absent(self):
        """No weight derivation when totAssets is absent from XML."""
        xml_no_total = """\
<?xml version="1.0"?>
<edgarSubmission xmlns="http://www.sec.gov/edgar/nport">
  <formData>
    <genInfo><repPdDate>2025-09-30</repPdDate></genInfo>
    <invstOrSecs>
      <invstOrSec>
        <name>Some Corp</name><cusip>111222333</cusip>
        <valUSD>100000.00</valUSD><curCd>USD</curCd>
      </invstOrSec>
    </invstOrSecs>
  </formData>
</edgarSubmission>
"""
        result = self._call(
            ticker="SPY",
            cik_lookup_fn=lambda t: "0000884394",
            http_responses=[
                _make_mock_response(json_body=_SUBMISSIONS_BODY),
                _make_mock_response(text_body=xml_no_total),
            ],
        )
        assert result.fetch_status == "success"
        assert result.weights_derived is False
        # weight_pct may be None (no derivation) since totAssets absent.
        for h in result.holdings:
            assert h.weight_pct is None

    def test_14_direct_pct_val_no_derivation(self):
        """Holdings with pctVal → direct weights, weights_derived=False."""
        result = self._call(
            ticker="SPY",
            cik_lookup_fn=lambda t: "0000884394",
            http_responses=[
                _make_mock_response(json_body=_SUBMISSIONS_BODY),
                _make_mock_response(text_body=_NPORT_XML_TWO_HOLDINGS),
            ],
        )
        assert result.weights_available is True
        assert result.weights_derived is False
        assert result.holdings[0].weight_pct == 5.0

    def test_15_isin_and_ticker_from_identifiers_block(self):
        """ISIN and ticker identifiers are parsed from <identifiers> block."""
        result = self._call(
            ticker="SPY",
            cik_lookup_fn=lambda t: "0000884394",
            http_responses=[
                _make_mock_response(json_body=_SUBMISSIONS_BODY),
                _make_mock_response(text_body=_NPORT_XML_TWO_HOLDINGS),
            ],
        )
        apple = next(h for h in result.holdings if h.name == "Apple Inc")
        assert apple.isin == "US0378331005"
        assert apple.ticker == "AAPL"
        msft = next(h for h in result.holdings if h.name == "Microsoft Corp")
        assert msft.isin == "US5949181045"

    def test_16_country_of_risk_parsed(self):
        """countryOfRisk is extracted from the holding element."""
        result = self._call(
            ticker="SPY",
            cik_lookup_fn=lambda t: "0000884394",
            http_responses=[
                _make_mock_response(json_body=_SUBMISSIONS_BODY),
                _make_mock_response(text_body=_NPORT_XML_TWO_HOLDINGS),
            ],
        )
        apple = next(h for h in result.holdings if h.name == "Apple Inc")
        assert apple.country_of_risk == "US"

    def test_17_cik_from_injected_lookup_fn(self):
        """CIK provided via cik_lookup_fn — seed map not consulted."""
        resolved: list[str] = []

        def _lookup(t: str) -> Optional[str]:
            resolved.append(t)
            return "0000884394"

        result = self._call(
            ticker="SPY",
            cik_lookup_fn=_lookup,
            http_responses=[
                _make_mock_response(json_body=_SUBMISSIONS_BODY),
                _make_mock_response(text_body=_NPORT_XML_TWO_HOLDINGS),
            ],
        )
        assert result.is_success
        assert "SPY" in resolved

    def test_18_request_count_on_success(self):
        """Successful path with injected CIK uses exactly 2 HTTP requests."""
        result = self._call(
            ticker="SPY",
            cik_lookup_fn=lambda t: "0000884394",
            http_responses=[
                _make_mock_response(json_body=_SUBMISSIONS_BODY),
                _make_mock_response(text_body=_NPORT_XML_TWO_HOLDINGS),
            ],
        )
        assert result.request_count == 2


# ── Adapter tests ─────────────────────────────────────────────────────────────


class TestEtfNportAdapter:
    """Tests for etf_nport_adapter_v1 (pure, no IO)."""

    _NOW = datetime.now(timezone.utc).isoformat()
    # A period within 120 days = FRESH.
    _FRESH_PERIOD = (datetime.now(timezone.utc) - timedelta(days=60)).strftime("%Y-%m-%d")
    # A period older than 120 days = STALE.
    _STALE_PERIOD = (datetime.now(timezone.utc) - timedelta(days=200)).strftime("%Y-%m-%d")

    def _adapt(self, provider_result, fetched_at=None):
        from app.services.intelligence.research_workers.etf_nport_adapter_v1 import (
            adapt_etf_nport,
        )

        return adapt_etf_nport(provider_result, fetched_at or self._NOW)

    def _build_output(self, provider_result, fetched_at=None):
        from app.services.intelligence.research_workers.etf_nport_adapter_v1 import (
            build_etf_nport_worker_output,
        )

        wi = _make_worker_input(provider_result.ticker)
        return build_etf_nport_worker_output(
            wi, provider_result, fetched_at or self._NOW
        )

    def test_19_worker_output_shape_valid(self):
        """Success result → WorkerOutput passes __post_init__ validation."""
        pr = _make_provider_result_success()
        output = self._build_output(pr)
        # If validation fails, __post_init__ raises; reaching here means it passed.
        assert output.worker_run_id == "test-worker-run-id"
        assert output.ticker == "SPY"
        assert isinstance(output.sources, list)
        assert isinstance(output.facts, list)
        assert isinstance(output.audit_events, list)
        assert output.evidence_summary_plain_english
        assert isinstance(output.limitations_or_missing_evidence, list)

    def test_20_artifact_contract_constants(self):
        """Artifact uses correct artifact_type, skill_pack, model_version, scope_kind."""
        pr = _make_provider_result_success()
        output = self._build_output(pr)
        assert output.artifact_type == "etf_fund_note"
        assert output.skill_pack == "etf_sec_nport_holdings_evidence_v1"
        assert output.model_version == "sec_nport_etf_holdings_v1"
        assert output.scope_kind == "ticker"

    def test_21_source_record_identifies_sec_filing(self):
        """SourceRecord: source_kind=sec_filing, provider_name=sec_edgar."""
        pr = _make_provider_result_success()
        result = self._adapt(pr)
        assert len(result.sources) == 1
        src = result.sources[0]
        assert src.source_kind == "sec_filing"
        assert src.provider_name == "sec_edgar"
        assert src.source_id == "0000884394-25-001234"

    def test_22_fact_records_per_holding(self):
        """FactRecords: one per holding, fact_kind=metric_observation, axis_hint=exposure."""
        pr = _make_provider_result_success()
        result = self._adapt(pr)
        assert len(result.facts) == 2
        for fact in result.facts:
            assert fact.fact_kind == "metric_observation"
            assert fact.axis_hint == "exposure"
            assert fact.source_index == 0

    def test_23_fact_payload_has_required_keys_no_forbidden(self):
        """FactRecord payload has holding_name, provider, lane; no forbidden keys."""
        from app.services.intelligence.research_workers.contracts import (
            WORKER_FORBIDDEN_PAYLOAD_KEYS,
        )

        pr = _make_provider_result_success()
        result = self._adapt(pr)
        for fact in result.facts:
            p = fact.structured_payload
            assert "holding_name" in p
            assert "provider" in p
            assert "lane" in p
            for fk in WORKER_FORBIDDEN_PAYLOAD_KEYS:
                assert fk not in p, f"Forbidden key {fk!r} found in FactRecord payload"

    def test_24_sector_status_always_missing(self):
        """sector_status=MISSING in every FactRecord payload."""
        pr = _make_provider_result_success()
        result = self._adapt(pr)
        for fact in result.facts:
            assert fact.structured_payload.get("sector_status") == "MISSING"

    def test_25_geography_status_available_when_country_present(self):
        """geography_status=AVAILABLE when countryOfRisk present; MISSING otherwise."""
        # Holdings with country_of_risk — AVAILABLE.
        pr_with_geo = _make_provider_result_success()
        result_geo = self._adapt(pr_with_geo)
        assert result_geo.artifact_payload_extra.get("geography_status") == "AVAILABLE"

        # Holdings without country_of_risk — MISSING.
        from app.services.intelligence.research_workers.nport_provider_v1 import (
            NportHolding,
        )

        holdings_no_geo = [
            NportHolding(name="X Corp", cusip="111000000", weight_pct=5.0),
            NportHolding(name="Y Corp", cusip="222000000", weight_pct=3.0),
        ]
        pr_no_geo = _make_provider_result_success(holdings=holdings_no_geo)
        result_no_geo = self._adapt(pr_no_geo)
        assert result_no_geo.artifact_payload_extra.get("geography_status") == "MISSING"

    def test_26_no_raw_xml_in_artifact_payload(self):
        """No raw XML, no raw filing dump in artifact_payload or facts."""
        pr = _make_provider_result_success()
        result = self._adapt(pr)
        payload_str = json.dumps(result.artifact_payload_extra)
        assert "<edgarSubmission" not in payload_str
        assert "<invstOrSec" not in payload_str
        for fact in result.facts:
            fact_str = json.dumps(fact.structured_payload)
            assert "<edgarSubmission" not in fact_str
            assert "<invstOrSec" not in fact_str

    def test_27_safe_for_decision_not_in_payload(self):
        """safe_for_decision not emitted in artifact_payload."""
        pr = _make_provider_result_success()
        output = self._build_output(pr)
        assert "safe_for_decision" not in output.artifact_payload

    def test_28_synthesis_ready_not_in_payload(self):
        """synthesis_ready not emitted in artifact_payload."""
        pr = _make_provider_result_success()
        output = self._build_output(pr)
        assert "synthesis_ready" not in output.artifact_payload

    def test_29_non_success_result_honest_no_data(self):
        """Non-success provider result → sources=[], facts=[] (thin honest result)."""
        from app.services.intelligence.research_workers.nport_provider_v1 import (
            NportProviderResult,
        )

        pr = NportProviderResult(
            ticker="SPY",
            fetch_status="no_nport_filing",
            error_message="No filing found.",
        )
        result = self._adapt(pr)
        assert result.sources == []
        assert result.facts == []
        assert "no_nport_filing" in result.source_refs_fingerprint

    def test_30_holdings_count_in_payload(self):
        """holdings_count in artifact_payload matches number of holdings."""
        pr = _make_provider_result_success()
        output = self._build_output(pr)
        assert output.artifact_payload.get("holdings_count") == 2

    def test_31_weights_flags_in_payload(self):
        """weights_available and weights_derived faithfully in artifact_payload."""
        pr_direct = _make_provider_result_success(weights_available=True, weights_derived=False)
        out_direct = self._build_output(pr_direct)
        assert out_direct.artifact_payload.get("weights_available") is True
        assert out_direct.artifact_payload.get("weights_derived") is False

        pr_derived = _make_provider_result_success(weights_available=True, weights_derived=True)
        out_derived = self._build_output(pr_derived)
        assert out_derived.artifact_payload.get("weights_derived") is True

    def test_32_freshness_fresh_within_120_days(self):
        """FRESH when report_period_date is within 120 days."""
        pr = _make_provider_result_success(report_period_date=self._FRESH_PERIOD)
        result = self._adapt(pr)
        assert result.freshness_status == "FRESH"

    def test_33_freshness_stale_older_than_120_days(self):
        """STALE when report_period_date is older than 120 days."""
        pr = _make_provider_result_success(report_period_date=self._STALE_PERIOD)
        result = self._adapt(pr)
        assert result.freshness_status == "STALE"

    def test_34_replay_key_deterministic(self):
        """Identical inputs → identical replay idempotency key."""
        pr = _make_provider_result_success()
        fetched_at = "2025-11-20T12:00:00+00:00"
        o1 = self._build_output(pr, fetched_at=fetched_at)
        o2 = self._build_output(pr, fetched_at=fetched_at)
        assert o1.replay_idempotency_key == o2.replay_idempotency_key

    def test_35_fingerprint_changes_with_different_holdings(self):
        """Source fingerprint differs when holdings change."""
        from app.services.intelligence.research_workers.nport_provider_v1 import (
            NportHolding,
        )

        pr_a = _make_provider_result_success()
        pr_b = _make_provider_result_success(
            holdings=[NportHolding(name="Totally Different Corp", cusip="999000000", weight_pct=10.0)]
        )
        fetched_at = "2025-11-20T12:00:00+00:00"
        o_a = self._build_output(pr_a, fetched_at=fetched_at)
        o_b = self._build_output(pr_b, fetched_at=fetched_at)
        assert o_a.replay_idempotency_key != o_b.replay_idempotency_key

    def test_36_limitations_non_empty(self):
        """Limitations list is non-empty for both success and failure paths."""
        pr_ok = _make_provider_result_success()
        result_ok = self._adapt(pr_ok)
        assert len(result_ok.limitations) >= 1

        from app.services.intelligence.research_workers.nport_provider_v1 import (
            NportProviderResult,
        )

        pr_fail = NportProviderResult(ticker="SPY", fetch_status="missing_cik")
        result_fail = self._adapt(pr_fail)
        assert len(result_fail.limitations) >= 1

    def test_37_evidence_summary_non_empty_on_success(self):
        """Evidence summary plain-English string is non-empty for success."""
        pr = _make_provider_result_success()
        result = self._adapt(pr)
        assert result.summary
        assert "NPORT" in result.summary or "holdings" in result.summary

    def test_38_gld_no_data_adapter_result(self):
        """GLD commodity_trust_or_no_nport_data → honest no-data adapter result."""
        from app.services.intelligence.research_workers.nport_provider_v1 import (
            NportProviderResult,
        )

        pr = NportProviderResult(
            ticker="GLD",
            fetch_status="commodity_trust_or_no_nport_data",
            error_message="Commodity trust — no equity holdings.",
        )
        result = self._adapt(pr)
        assert result.facts == []
        assert result.sources == []
        assert result.artifact_payload_extra.get("holdings_count") == 0

    def test_39_worker_output_passes_validation(self):
        """build_etf_nport_worker_output passes WorkerOutput __post_init__ validation."""
        from app.services.intelligence.research_workers.contracts import (
            WORKER_FORBIDDEN_PAYLOAD_KEYS,
        )

        pr = _make_provider_result_success()
        output = self._build_output(pr)

        # Confirm no forbidden keys recursively anywhere in payload.
        payload_json = json.dumps(output.artifact_payload)
        for fk in WORKER_FORBIDDEN_PAYLOAD_KEYS:
            assert f'"{fk}"' not in payload_json


# ── Runner tests ──────────────────────────────────────────────────────────────


class TestRunnerEtfNport:
    """Tests for run_etf_nport_holdings_evidence in evidence_lane_runner_v1."""

    def _call_runner(
        self,
        ticker="SPY",
        settings=None,
        provider_result=None,
        db_client=None,
        holding_context=None,
    ):
        from app.services.intelligence.research_workers.evidence_lane_runner_v1 import (
            run_etf_nport_holdings_evidence,
        )

        if db_client is None:
            db_client = MagicMock()
        if settings is None:
            settings = _make_settings(enabled=True)

        _provider_fn = None
        if provider_result is not None:
            _provider_fn = lambda t: provider_result  # noqa: E731

        return run_etf_nport_holdings_evidence(
            user_id="test-user",
            ticker=ticker,
            db_client=db_client,
            parent_intel_run_id="test-intel-run-id",
            holding_context=holding_context,
            settings=settings,
            _provider_fn=_provider_fn,
        )

    def test_40_flag_default_off_returns_none(self):
        """Flag default OFF → runner returns None without touching provider."""
        settings = _make_settings(enabled=False)
        result = self._call_runner(ticker="SPY", settings=settings)
        assert result is None

    def test_41_non_etf_ticker_skipped_honestly(self):
        """Non-ETF ticker (equity) skipped honestly, not written as failure."""
        settings = _make_settings(enabled=True)
        result = self._call_runner(
            ticker="AAPL",
            settings=settings,
            holding_context={"category": "equity"},
        )
        assert result is None

    def test_42_known_etf_symbol_classified_as_etf(self):
        """Known ETF symbol in fallback list → classified as ETF (guard passes)."""
        from app.services.intelligence.research_workers.evidence_lane_runner_v1 import (
            _classify_holding_as_etf,
        )

        for sym in ("SPY", "VOO", "QQQ", "GLD", "VGT"):
            assert _classify_holding_as_etf(sym, None) is True

    def test_43_unknown_symbol_no_context_skipped(self):
        """Unknown ticker with no holding_context → not in known list → skipped."""
        from app.services.intelligence.research_workers.evidence_lane_runner_v1 import (
            _classify_holding_as_etf,
        )

        assert _classify_holding_as_etf("RANDOMCO", None) is False

    def test_44_holding_context_etf_category_overrides_symbol(self):
        """holding_context with category=etf → classified as ETF."""
        from app.services.intelligence.research_workers.evidence_lane_runner_v1 import (
            _classify_holding_as_etf,
        )

        assert _classify_holding_as_etf("RANDOMCO", {"category": "etf"}) is True
        assert _classify_holding_as_etf("RANDOMCO", {"asset_type": "ETF"}) is True
        assert _classify_holding_as_etf("RANDOMCO", {"instrument_type": "exchange traded fund"}) is True

    def test_45_holding_context_equity_returns_false_for_known_etf(self):
        """holding_context category=equity → False even for known ETF symbol."""
        from app.services.intelligence.research_workers.evidence_lane_runner_v1 import (
            _classify_holding_as_etf,
        )

        assert _classify_holding_as_etf("SPY", {"category": "equity"}) is False
        assert _classify_holding_as_etf("QQQ", {"asset_type": "stock"}) is False

    def test_46_zero_holdings_skips_write(self):
        """Provider returns non-success (no holdings) → runner skips write, returns None."""
        from app.services.intelligence.research_workers.nport_provider_v1 import (
            NportProviderResult,
        )

        pr = NportProviderResult(
            ticker="SPY",
            fetch_status="no_nport_filing",
            error_message="No filing found.",
        )
        db_client = MagicMock()
        result = self._call_runner(ticker="SPY", provider_result=pr, db_client=db_client)
        assert result is None
        db_client.table.assert_not_called()

    def test_47_success_path_calls_write_artifact(self):
        """Success path: provider returns holdings → write_artifact called."""
        pr = _make_provider_result_success()
        db_client = MagicMock()

        # Mock the ResearchArtifactServiceV1 write path.
        with patch(
            "app.services.intelligence.research_workers.evidence_lane_runner_v1"
            ".ResearchArtifactServiceV1"
        ) as MockService:
            mock_svc = MockService.return_value
            mock_svc.write_artifact.return_value = "artifact-uuid-001"
            result = self._call_runner(ticker="SPY", provider_result=pr, db_client=db_client)

        assert result == "artifact-uuid-001"
        mock_svc.write_artifact.assert_called_once()

    def test_48_idempotency_key_deterministic_from_holdings(self):
        """Identical holdings produce identical replay_idempotency_key."""
        from app.services.intelligence.research_workers.etf_nport_adapter_v1 import (
            build_etf_nport_worker_output,
        )

        pr = _make_provider_result_success()
        wi = _make_worker_input("SPY")
        fetched_at = "2025-11-20T12:00:00+00:00"

        out1 = build_etf_nport_worker_output(wi, pr, fetched_at)
        out2 = build_etf_nport_worker_output(wi, pr, fetched_at)
        assert out1.replay_idempotency_key == out2.replay_idempotency_key


# ── Regression tests ──────────────────────────────────────────────────────────


class TestRegressionImports:
    """Ensure new modules do not break existing Stage 9F imports."""

    def test_49_no_import_break_from_new_modules(self):
        """Importing new lane modules does not break existing imports."""
        # Re-import to force any import-time errors to surface.
        import importlib

        importlib.import_module(
            "app.services.intelligence.research_workers.nport_provider_v1"
        )
        importlib.import_module(
            "app.services.intelligence.research_workers.etf_nport_adapter_v1"
        )
        importlib.import_module(
            "app.services.intelligence.research_workers.evidence_lane_runner_v1"
        )
        importlib.import_module(
            "app.services.intelligence.v3.canonical_etf_fund_dataset_v1"
        )

    def test_50_lane_etf_fund_data_in_all_lanes(self):
        """LANE_ETF_FUND_DATA is present in the ALL_LANES frozenset."""
        from app.services.intelligence.research_workers.evidence_provider_registry_v1 import (
            ALL_LANES,
            LANE_ETF_FUND_DATA,
        )

        assert LANE_ETF_FUND_DATA in ALL_LANES
        assert LANE_ETF_FUND_DATA == "etf_fund_data"
