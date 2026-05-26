"""Stage 9F.2c — ETF Issuer Source Certifier v1 tests.

Fixture-based only — no live HTTP calls, no live SEC EDGAR calls.

Coverage:

  140. Certified fixture — HTTP 200 + identity + as_of + weight → CERTIFIED.
  141. 404 fixture — HTTP 404 → FETCH_FAILED.
  142. Network error fixture → FETCH_FAILED.
  143. Identity not proven fixture — wrong fund name → IDENTITY_NOT_PROVEN.
  144. As-of not proven fixture — no date in file → AS_OF_NOT_PROVEN.
  145. Weights not proven fixture — no weight column → WEIGHTS_NOT_PROVEN.
  146. HTML response fixture → FETCH_FAILED (not a downloadable CSV).
  147. Empty response fixture → FETCH_FAILED.
  148. SCHD (schwab) → SOURCE_NOT_FOUND (no URL configured).
  149. GLD commodity provider → SOURCE_NOT_FOUND (certification not applicable).
  150. canonical_ready=False for all certification results.
  151. safe_for_decision=False for all certification results.
  152. Certifier returns candidate_urls_checked list.
  153. Certifier returns selected_source_url only when CERTIFIED.
  154. build_certification_dict returns correct keys.
  155. Runner — provider_statuses includes source_certification for issuer-official.
  156. Runner — provider_statuses source_certification=None for SEC NPORT providers.
  157. Runner — issuer_source_certified_count=0 when no source certified.
  158. Runner — issuer_source_certified_count increments when source certified.
  159. Runner — certifier_fn is injectable.
  160. GLD still returns commodity_trust_no_equity_holdings (unchanged).
  161. SPY/QQQ SEC NPORT path unchanged (source_certification=None).
  162. Vanguard URL template resolves per-ticker (VOO vs VGT distinct URLs).
  163. SSGA URL template resolves per-ticker lower-case.
  164. Certifier never raises — always returns SourceCertificationResult.
  165. CandidateProbeResult certification_reason is non-empty string.
  166. Multiple candidate URLs — certifier tries all before giving up.
  167. Certifier stops at first CERTIFIED candidate (does not try subsequent URLs).
  168. Runner registry_version updated to stage9f2c_v1.
  169. Runner output includes issuer_source_certified_count key.
  170. All existing Stage 9F.2b tests still implied to pass (registry/adapter unchanged).
"""
from __future__ import annotations

import pytest
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from app.services.intelligence.research_workers.etf_issuer_source_certifier_v1 import (
    CERT_STATUS_CERTIFIED,
    CERT_STATUS_FETCH_FAILED,
    CERT_STATUS_IDENTITY_NOT_PROVEN,
    CERT_STATUS_AS_OF_NOT_PROVEN,
    CERT_STATUS_WEIGHTS_NOT_PROVEN,
    CERT_STATUS_SOURCE_NOT_FOUND,
    PROOF_PROVEN,
    PROOF_NOT_PROVEN,
    PROOF_NOT_CHECKED,
    SourceCertificationResult,
    CandidateProbeResult,
    certify_issuer_source,
    build_certification_dict,
)
from app.services.intelligence.research_workers.etf_provider_registry_runner_v1 import (
    run_provider_registry_check,
)
from app.services.intelligence.research_workers.etf_holdings_provider_registry_v1 import (
    ETFHoldingsResult,
)


# ── Fixture CSV content ────────────────────────────────────────────────────────

_VANGUARD_VOO_CERTIFIED_CSV = """\
Holdings,Ticker Symbol,ISIN,SEDOL,Weight,Shares,Market Value
Vanguard S&P 500 ETF,,,,,,
As of: 12/31/2024,,,,,,
Apple Inc.,AAPL,US0378331005,,7.12,5000000,900000000
Microsoft Corporation,MSFT,US5949181045,,6.50,4000000,800000000
Amazon.com Inc.,AMZN,US0231351067,,3.85,2000000,500000000
"""

_WRONG_FUND_CSV = """\
Holdings,Ticker Symbol,ISIN,SEDOL,Weight,Shares,Market Value
Some Completely Different Fund,,,,,,
As of: 12/31/2024,,,,,,
Apple Inc.,AAPL,US0378331005,,7.12,5000000,900000000
"""

_NO_DATE_CSV = """\
Holdings,Ticker Symbol,ISIN,SEDOL,Weight,Shares,Market Value
Vanguard S&P 500 ETF,,,,,,
Apple Inc.,AAPL,US0378331005,,7.12,5000000,900000000
Microsoft Corporation,MSFT,US5949181045,,6.50,4000000,800000000
"""

_NO_WEIGHT_COLUMN_CSV = """\
Holdings,Ticker Symbol,ISIN,SEDOL,Shares,Market Value
Vanguard S&P 500 ETF,,,,
As of: 12/31/2024,,,,
Apple Inc.,AAPL,US0378331005,,5000000,900000000
Microsoft Corporation,MSFT,US5949181045,,4000000,800000000
"""

_HTML_RESPONSE = """\
<!DOCTYPE html>
<html><head><title>Vanguard</title></head>
<body><p>ETF product page</p></body>
</html>
"""

_EMPTY_RESPONSE = ""

_SSGA_XLE_CERTIFIED_CSV = """\
Name,Ticker,Identifier,SEDOL,Weight,Shares Held,Local Market Value,Market Value
Energy Select Sector SPDR Fund,XLE,,,,,,
As of Date: 12/31/2024,,,,,,,
Exxon Mobil Corp,XOM,2326618,,12.34,50000000,9000000000,9000000000
Chevron Corp,CVX,2166191,,11.50,40000000,7000000000,7000000000
"""


# ── HTTP fixture helpers ──────────────────────────────────────────────────────


def _make_response(text: str, status_code: int = 200, content_type: str = "text/csv") -> Any:
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    resp.headers = {"content-type": content_type}
    if status_code != 200:
        resp.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    else:
        resp.raise_for_status.return_value = None
    return resp


def _http_fn_200_voo(url: str) -> Any:
    return _make_response(_VANGUARD_VOO_CERTIFIED_CSV)


def _http_fn_404(url: str) -> Any:
    raise Exception("404 Client Error: Not Found for url: " + url)


def _http_fn_network_error(url: str) -> Any:
    raise Exception("Connection refused")


def _http_fn_wrong_fund(url: str) -> Any:
    return _make_response(_WRONG_FUND_CSV)


def _http_fn_no_date(url: str) -> Any:
    return _make_response(_NO_DATE_CSV)


def _http_fn_no_weight(url: str) -> Any:
    return _make_response(_NO_WEIGHT_COLUMN_CSV)


def _http_fn_html(url: str) -> Any:
    return _make_response(_HTML_RESPONSE, content_type="text/html")


def _http_fn_empty(url: str) -> Any:
    return _make_response(_EMPTY_RESPONSE)


def _http_fn_xle_certified(url: str) -> Any:
    return _make_response(_SSGA_XLE_CERTIFIED_CSV)


# ── Tests: certify_issuer_source ──────────────────────────────────────────────


def test_140_certified_fixture_returns_certified():
    """HTTP 200 + identity + as_of + weight → CERTIFIED."""
    result = certify_issuer_source("VOO", "vanguard_official_v1", _http_fn_200_voo)
    assert result.source_certification_status == CERT_STATUS_CERTIFIED
    assert result.selected_source_url is not None
    assert "investor.vanguard.com" in result.selected_source_url
    assert result.identity_proof == PROOF_PROVEN
    assert result.as_of_proof == PROOF_PROVEN
    assert result.weight_proof == PROOF_PROVEN


def test_141_404_fixture_returns_fetch_failed():
    """HTTP 404 → FETCH_FAILED."""
    result = certify_issuer_source("VOO", "vanguard_official_v1", _http_fn_404)
    assert result.source_certification_status == CERT_STATUS_FETCH_FAILED
    assert result.selected_source_url is None


def test_142_network_error_returns_fetch_failed():
    """Network error → FETCH_FAILED."""
    result = certify_issuer_source("VOO", "vanguard_official_v1", _http_fn_network_error)
    assert result.source_certification_status == CERT_STATUS_FETCH_FAILED
    assert result.selected_source_url is None


def test_143_wrong_fund_name_returns_identity_not_proven():
    """Fetched OK but wrong fund name → IDENTITY_NOT_PROVEN."""
    result = certify_issuer_source("VOO", "vanguard_official_v1", _http_fn_wrong_fund)
    assert result.source_certification_status == CERT_STATUS_IDENTITY_NOT_PROVEN
    assert result.identity_proof == PROOF_NOT_PROVEN
    assert result.selected_source_url is None


def test_144_no_date_returns_as_of_not_proven():
    """Identity proven but no as-of date → AS_OF_NOT_PROVEN."""
    result = certify_issuer_source("VOO", "vanguard_official_v1", _http_fn_no_date)
    assert result.source_certification_status == CERT_STATUS_AS_OF_NOT_PROVEN
    assert result.identity_proof == PROOF_PROVEN
    assert result.as_of_proof == PROOF_NOT_PROVEN
    assert result.selected_source_url is None


def test_145_no_weight_column_returns_weights_not_proven():
    """Identity + as_of proven but no weight column → WEIGHTS_NOT_PROVEN."""
    result = certify_issuer_source("VOO", "vanguard_official_v1", _http_fn_no_weight)
    assert result.source_certification_status == CERT_STATUS_WEIGHTS_NOT_PROVEN
    assert result.identity_proof == PROOF_PROVEN
    assert result.as_of_proof == PROOF_PROVEN
    assert result.weight_proof == PROOF_NOT_PROVEN
    assert result.selected_source_url is None


def test_146_html_response_returns_fetch_failed():
    """HTML response (browser-only page) → FETCH_FAILED."""
    result = certify_issuer_source("VOO", "vanguard_official_v1", _http_fn_html)
    assert result.source_certification_status == CERT_STATUS_FETCH_FAILED
    assert result.selected_source_url is None


def test_147_empty_response_returns_fetch_failed():
    """Empty response body → FETCH_FAILED."""
    result = certify_issuer_source("VOO", "vanguard_official_v1", _http_fn_empty)
    assert result.source_certification_status == CERT_STATUS_FETCH_FAILED
    assert result.selected_source_url is None


def test_148_schd_no_url_returns_source_not_found():
    """Schwab SCHD has no configured URL → SOURCE_NOT_FOUND."""
    result = certify_issuer_source("SCHD", "schwab_official_v1")
    assert result.source_certification_status == CERT_STATUS_SOURCE_NOT_FOUND
    assert result.candidate_urls_checked == []
    assert result.selected_source_url is None


def test_149_gld_commodity_provider_returns_source_not_found():
    """GLD commodity_v1 provider has no URL → SOURCE_NOT_FOUND."""
    result = certify_issuer_source("GLD", "gld_commodity_v1")
    assert result.source_certification_status == CERT_STATUS_SOURCE_NOT_FOUND
    assert result.candidate_urls_checked == []


def test_150_canonical_ready_never_true():
    """canonical_ready is always False for all certification results."""
    for ticker, pid, fn in [
        ("VOO", "vanguard_official_v1", _http_fn_200_voo),
        ("VOO", "vanguard_official_v1", _http_fn_404),
        ("SCHD", "schwab_official_v1", None),
        ("GLD", "gld_commodity_v1", None),
    ]:
        result = certify_issuer_source(ticker, pid, fn)
        assert result.canonical_ready is False, f"canonical_ready True for {ticker}/{pid}"


def test_151_safe_for_decision_never_true():
    """safe_for_decision is always False for all certification results."""
    for ticker, pid, fn in [
        ("VOO", "vanguard_official_v1", _http_fn_200_voo),
        ("VOO", "vanguard_official_v1", _http_fn_404),
        ("SCHD", "schwab_official_v1", None),
        ("GLD", "gld_commodity_v1", None),
    ]:
        result = certify_issuer_source(ticker, pid, fn)
        assert result.safe_for_decision is False, f"safe_for_decision True for {ticker}/{pid}"


def test_152_candidate_urls_checked_populated():
    """Certifier populates candidate_urls_checked list with tried URLs."""
    result = certify_issuer_source("VOO", "vanguard_official_v1", _http_fn_404)
    assert len(result.candidate_urls_checked) >= 1
    assert any("investor.vanguard.com" in url for url in result.candidate_urls_checked)


def test_153_selected_source_url_only_when_certified():
    """selected_source_url is set only when CERTIFIED, None otherwise."""
    certified = certify_issuer_source("VOO", "vanguard_official_v1", _http_fn_200_voo)
    assert certified.selected_source_url is not None
    assert certified.source_certification_status == CERT_STATUS_CERTIFIED

    failed = certify_issuer_source("VOO", "vanguard_official_v1", _http_fn_404)
    assert failed.selected_source_url is None


def test_154_build_certification_dict_keys():
    """build_certification_dict returns all required diagnostic keys."""
    result = certify_issuer_source("VOO", "vanguard_official_v1", _http_fn_200_voo)
    d = build_certification_dict(result)
    required_keys = {
        "candidate_urls_checked",
        "selected_source_url",
        "source_certification_status",
        "source_certification_reason",
        "http_status",
        "content_type",
        "identity_proof",
        "as_of_proof",
        "weight_proof",
        "canonical_ready",
        "safe_for_decision",
        "candidate_statuses",
    }
    assert required_keys.issubset(d.keys())
    assert d["canonical_ready"] is False
    assert d["safe_for_decision"] is False


# ── Tests: runner integration ─────────────────────────────────────────────────


def _make_nport_success(ticker: str) -> Any:
    """Minimal NportProviderResult fixture for SPY/QQQ."""
    class FakeFiling:
        filing_url = "https://sec.gov/fake"
        report_period_date = "2024-12-31"
        filing_date = "2025-01-15"

    class FakeHolding:
        name = "Apple Inc."
    result = MagicMock()
    result.ticker = ticker
    result.fetch_status = "success"
    result.is_success = True
    result.holdings = [FakeHolding()]
    result.filing_meta = FakeFiling()
    result.weights_available = True
    result.weights_derived = False
    result.identity_verified = True
    result.identity_basis = "standalone_trust"
    result.error_message = None
    return result


def _make_nport_fn(ticker: str):
    def nport_fn(t, cfg):
        return _make_nport_success(t)
    return nport_fn


def _make_issuer_fail(ticker: str, provider_id: str) -> ETFHoldingsResult:
    return ETFHoldingsResult(
        ticker=ticker,
        provider_id=provider_id,
        source_type="issuer_official",
        source_url=None,
        source_authority="issuer_official",
        as_of_date=None,
        holdings_count=0,
        sample_holding_names=[],
        weights_available=False,
        weight_basis="unavailable",
        identity_verified=False,
        identity_basis=None,
        freshness_status="unknown",
        fetch_status="source_url_fetch_error",
        error_message="HTTP 404",
        limitations=[],
        canonical_ready=False,
        safe_for_decision=False,
    )


def _issuer_fn_fail(ticker: str, provider_id: str) -> ETFHoldingsResult:
    if provider_id == "gld_commodity_v1" or ticker.upper() == "GLD":
        # GLD commodity special case — matches real adapter behavior.
        return ETFHoldingsResult(
            ticker=ticker,
            provider_id=provider_id,
            source_type="issuer_official",
            source_url=None,
            source_authority="issuer_official",
            as_of_date=None,
            holdings_count=0,
            sample_holding_names=[],
            weights_available=False,
            weight_basis="unavailable",
            identity_verified=True,
            identity_basis="commodity_trust_assumed_no_equity_holdings: GLD holds physical gold bullion",
            freshness_status="not_applicable",
            fetch_status="commodity_trust_no_equity_holdings",
            error_message=None,
            limitations=["GLD holds physical gold bullion — no equity holdings basket."],
            canonical_ready=False,
            safe_for_decision=False,
        )
    return _make_issuer_fail(ticker, provider_id)


def _make_certifier_fetch_failed(ticker: str, provider_id: str) -> SourceCertificationResult:
    return SourceCertificationResult(
        ticker=ticker,
        provider_id=provider_id,
        issuer_family="vanguard",
        candidate_urls_checked=["https://investor.vanguard.com/..."],
        selected_source_url=None,
        source_certification_status=CERT_STATUS_FETCH_FAILED,
        source_certification_reason="HTTP 404",
        http_status=404,
        content_type=None,
        identity_proof=PROOF_NOT_CHECKED,
        as_of_proof=PROOF_NOT_CHECKED,
        weight_proof=PROOF_NOT_CHECKED,
        canonical_ready=False,
        safe_for_decision=False,
    )


def _make_certifier_certified(ticker: str, provider_id: str) -> SourceCertificationResult:
    return SourceCertificationResult(
        ticker=ticker,
        provider_id=provider_id,
        issuer_family="vanguard",
        candidate_urls_checked=["https://investor.vanguard.com/..."],
        selected_source_url="https://investor.vanguard.com/...",
        source_certification_status=CERT_STATUS_CERTIFIED,
        source_certification_reason="HTTP 200 — identity proven, as-of date found, percent weight column confirmed",
        http_status=200,
        content_type="text/csv",
        identity_proof=PROOF_PROVEN,
        as_of_proof=PROOF_PROVEN,
        weight_proof=PROOF_PROVEN,
        canonical_ready=False,
        safe_for_decision=False,
    )


def _run_with_fixtures(
    tickers: list[str],
    nport_fn=None,
    issuer_fn=None,
    certifier_fn=None,
) -> dict:
    return run_provider_registry_check(
        tickers,
        user_agent="TestAgent/1.0 test@example.com",
        nport_provider_fn=nport_fn or (lambda t, c: _make_nport_success(t)),
        issuer_provider_fn=issuer_fn or _issuer_fn_fail,
        certifier_fn=certifier_fn or _make_certifier_fetch_failed,
        sleep_fn=lambda s: None,
    )


def test_155_provider_statuses_include_source_certification_for_issuer():
    """Runner includes source_certification in provider_statuses for issuer-official."""
    result = _run_with_fixtures(
        ["VOO"],
        certifier_fn=_make_certifier_fetch_failed,
    )
    voo = next(t for t in result["per_ticker"] if t["ticker"] == "VOO")
    # Should have at least one issuer-official status entry.
    issuer_entries = [
        ps for ps in voo["provider_statuses"]
        if ps["source_type"] == "issuer_official"
    ]
    assert len(issuer_entries) >= 1
    cert = issuer_entries[0].get("source_certification")
    assert cert is not None
    assert "source_certification_status" in cert
    assert cert["canonical_ready"] is False
    assert cert["safe_for_decision"] is False


def test_156_sec_nport_provider_has_no_source_certification():
    """Runner sets source_certification=None for SEC NPORT providers."""
    result = _run_with_fixtures(
        ["SPY"],
        nport_fn=lambda t, c: _make_nport_success(t),
    )
    spy = next(t for t in result["per_ticker"] if t["ticker"] == "SPY")
    nport_entries = [
        ps for ps in spy["provider_statuses"]
        if ps["source_type"] == "sec_nport"
    ]
    assert len(nport_entries) >= 1
    assert nport_entries[0].get("source_certification") is None


def test_157_issuer_source_certified_count_zero_when_no_source_certified():
    """issuer_source_certified_count=0 when certifier returns FETCH_FAILED for all."""
    result = _run_with_fixtures(
        ["VOO"],
        certifier_fn=_make_certifier_fetch_failed,
    )
    assert result["issuer_source_certified_count"] == 0


def test_158_issuer_source_certified_count_increments():
    """issuer_source_certified_count increments when certifier returns CERTIFIED."""
    result = _run_with_fixtures(
        ["VOO"],
        certifier_fn=_make_certifier_certified,
    )
    assert result["issuer_source_certified_count"] == 1


def test_159_certifier_fn_is_injectable():
    """certifier_fn parameter is injectable — fixture fn is used instead of real HTTP."""
    called_with = []

    def mock_certifier(ticker, provider_id):
        called_with.append((ticker, provider_id))
        return _make_certifier_fetch_failed(ticker, provider_id)

    _run_with_fixtures(["VTI"], certifier_fn=mock_certifier)
    # certifier should have been called at least once for the issuer-official provider.
    assert any("VTI" in str(c) for c in called_with)


def test_160_gld_unchanged_commodity_behavior():
    """GLD still returns commodity_trust_no_equity_holdings (unchanged from 9F.2b)."""
    result = _run_with_fixtures(["GLD"])
    gld = next(t for t in result["per_ticker"] if t["ticker"] == "GLD")
    assert gld["fetch_status"] == "commodity_trust_no_equity_holdings"
    assert gld["canonical_ready"] is False
    assert gld["safe_for_decision"] is False


def test_161_spy_qqq_sec_nport_source_certification_is_none():
    """SPY and QQQ use sec_nport_v1 first; those entries have source_certification=None."""
    result = _run_with_fixtures(
        ["SPY", "QQQ"],
        nport_fn=lambda t, c: _make_nport_success(t),
    )
    for ticker in ("SPY", "QQQ"):
        entry = next(t for t in result["per_ticker"] if t["ticker"] == ticker)
        nport_status = next(
            ps for ps in entry["provider_statuses"]
            if ps["source_type"] == "sec_nport"
        )
        assert nport_status["source_certification"] is None


def test_162_vanguard_url_per_ticker_distinct():
    """Vanguard URL template resolves distinct URLs for VOO vs VGT."""
    from app.services.intelligence.research_workers.etf_issuer_source_certifier_v1 import (
        _PROVIDER_CANDIDATES,
    )
    templates = _PROVIDER_CANDIDATES.get("vanguard_official_v1", [])
    assert len(templates) >= 1
    url_voo = templates[0][0].format(ticker="VOO", ticker_lower="voo")
    url_vgt = templates[0][0].format(ticker="VGT", ticker_lower="vgt")
    assert url_voo != url_vgt
    assert "VOO" in url_voo
    assert "VGT" in url_vgt


def test_163_ssga_url_uses_lowercase_ticker():
    """SSGA URL template uses {ticker_lower} formatting."""
    from app.services.intelligence.research_workers.etf_issuer_source_certifier_v1 import (
        _PROVIDER_CANDIDATES,
    )
    templates = _PROVIDER_CANDIDATES.get("spdr_official_v1", [])
    assert len(templates) >= 1
    url = templates[0][0].format(ticker="XLE", ticker_lower="xle")
    assert "xle" in url
    assert "XLE" not in url


def test_164_certifier_never_raises():
    """certify_issuer_source always returns SourceCertificationResult — never raises."""
    def bad_fn(url):
        raise RuntimeError("Catastrophic failure")

    result = certify_issuer_source("VOO", "vanguard_official_v1", bad_fn)
    assert isinstance(result, SourceCertificationResult)
    assert result.canonical_ready is False
    assert result.safe_for_decision is False


def test_165_candidate_probe_result_has_nonempty_reason():
    """CandidateProbeResult.certification_reason is always a non-empty string."""
    result = certify_issuer_source("VOO", "vanguard_official_v1", _http_fn_404)
    for probe in result.candidate_probes:
        assert isinstance(probe.certification_reason, str)
        assert len(probe.certification_reason) > 0


def test_166_certifier_records_all_candidates_tried():
    """Certifier records all candidate URLs tried in candidate_probes."""
    result = certify_issuer_source("VOO", "vanguard_official_v1", _http_fn_404)
    assert len(result.candidate_probes) == len(result.candidate_urls_checked)
    assert len(result.candidate_urls_checked) >= 1


def test_167_certifier_stops_at_first_certified():
    """Certifier stops at first CERTIFIED candidate — does not try subsequent URLs."""
    call_count = [0]

    def counting_fn(url):
        call_count[0] += 1
        return _make_response(_VANGUARD_VOO_CERTIFIED_CSV)

    from unittest.mock import patch
    from app.services.intelligence.research_workers import etf_issuer_source_certifier_v1 as _mod

    # Temporarily add a second candidate to test stopping behavior.
    original = _mod._PROVIDER_CANDIDATES.get("vanguard_official_v1", [])
    _mod._PROVIDER_CANDIDATES["vanguard_official_v1"] = original + [
        ("https://example.com/extra.csv", "extra_candidate"),
    ]
    try:
        result = certify_issuer_source("VOO", "vanguard_official_v1", counting_fn)
        assert result.source_certification_status == CERT_STATUS_CERTIFIED
        # Should have called HTTP only once (stopped at the first CERTIFIED).
        assert call_count[0] == 1
        assert len(result.candidate_probes) == 1  # Only one probe attempted.
    finally:
        _mod._PROVIDER_CANDIDATES["vanguard_official_v1"] = original


def test_168_runner_registry_version_updated():
    """Runner output registry_version reflects Stage 9F.2c."""
    result = _run_with_fixtures(["SPY"])
    assert result["registry_version"] == "stage9f2c_v1"


def test_169_runner_output_includes_issuer_source_certified_count():
    """Runner output always includes issuer_source_certified_count key."""
    result = _run_with_fixtures(["SPY"])
    assert "issuer_source_certified_count" in result


def test_170_canonical_ready_safe_for_decision_always_false_in_runner():
    """Runner output canonical_ready=False and safe_for_decision=False always."""
    result = _run_with_fixtures(["SPY", "VOO", "GLD"])
    assert result["canonical_ready"] is False
    assert result["safe_for_decision"] is False
    for entry in result["per_ticker"]:
        assert entry["canonical_ready"] is False
        assert entry["safe_for_decision"] is False


# ── Additional edge cases ─────────────────────────────────────────────────────


def test_xle_ssga_certified_fixture():
    """SSGA XLE certified fixture → CERTIFIED with identity + as_of + weight."""
    result = certify_issuer_source("XLE", "spdr_official_v1", _http_fn_xle_certified)
    assert result.source_certification_status == CERT_STATUS_CERTIFIED
    assert result.identity_proof == PROOF_PROVEN
    assert result.as_of_proof == PROOF_PROVEN
    assert result.weight_proof == PROOF_PROVEN
    assert "ssga.com" in result.selected_source_url


def test_schd_source_not_found_no_http_call():
    """Schwab SCHD returns SOURCE_NOT_FOUND without making any HTTP call."""
    call_count = [0]

    def counting_fn(url):
        call_count[0] += 1
        return _make_response("")

    result = certify_issuer_source("SCHD", "schwab_official_v1", counting_fn)
    assert result.source_certification_status == CERT_STATUS_SOURCE_NOT_FOUND
    assert call_count[0] == 0  # No HTTP call made.


def test_build_certification_dict_certified_source():
    """build_certification_dict reflects CERTIFIED status correctly."""
    result = certify_issuer_source("VOO", "vanguard_official_v1", _http_fn_200_voo)
    d = build_certification_dict(result)
    assert d["source_certification_status"] == CERT_STATUS_CERTIFIED
    assert d["selected_source_url"] is not None
    assert d["identity_proof"] == PROOF_PROVEN
    assert d["canonical_ready"] is False
    assert d["safe_for_decision"] is False


def test_build_certification_dict_fetch_failed():
    """build_certification_dict reflects FETCH_FAILED status correctly."""
    result = certify_issuer_source("VOO", "vanguard_official_v1", _http_fn_404)
    d = build_certification_dict(result)
    assert d["source_certification_status"] == CERT_STATUS_FETCH_FAILED
    assert d["selected_source_url"] is None
    assert d["canonical_ready"] is False
    assert d["safe_for_decision"] is False


def test_runner_artifact_writes_zero():
    """Runner reports artifact_writes=0 always."""
    result = _run_with_fixtures(["VOO"])
    assert result["artifact_writes"] == 0


def test_runner_decision_policy_unchanged():
    """Runner reports decision_policy_changed=False always."""
    result = _run_with_fixtures(["VOO"])
    assert result["decision_policy_changed"] is False
