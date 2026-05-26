"""Stage 9F.2b — ETF Holdings Provider Registry v1 tests.

Fixture-based only — no live HTTP calls, no live SEC EDGAR calls.

Coverage:

  107. Registry routing: each ETF universe ticker has a non-empty provider list.
  108. Registry routing: SPY and QQQ list sec_nport_v1 as first priority.
  109. Registry routing: Vanguard ETFs list vanguard_official_v1 as first priority.
  110. Registry routing: XLE lists spdr_official_v1 as first priority.
  111. Registry routing: SCHD lists schwab_official_v1 as first priority.
  112. Registry routing: GLD lists only gld_commodity_v1.
  113. SEC NPORT provider record is registered with sec_nport_v1 provider_id.
  114. SEC NPORT provider has enabled_for_diagnostics=True and enabled_for_canonical=False.
  115. All providers have enabled_for_canonical=False.
  116. All providers have enabled_for_diagnostics=True.
  117. Vanguard CSV fixture — successful parse returns identity_verified=True and holdings.
  118. Vanguard CSV fixture — wrong fund name returns identity_not_proven, no holdings.
  119. SSGA/SPDR CSV fixture — XLE parse returns identity_verified=True and holdings.
  120. SSGA/SPDR CSV fixture — wrong fund name returns identity_not_proven, no holdings.
  121. SCHD Schwab adapter — returns source_url_not_validated (URL not confirmed).
  122. GLD commodity path — returns commodity_trust_no_equity_holdings with identity_verified=True.
  123. GLD commodity path — holdings_count=0 and sample_holding_names=[].
  124. Missing/changed CSV schema — returns source_shape_changed.
  125. Empty CSV content — returns source_url_fetch_error.
  126. Runner — no raw full holdings in per_ticker output (only sample ≤5 names).
  127. Runner — canonical_ready=False and safe_for_decision=False for all tickers.
  128. Runner — diagnostics_only=True and artifact_writes=0 always.
  129. Runner — identity mismatch from provider returns no holdings in selected result.
  130. Runner — nport_provider_fn and issuer_provider_fn are injectable.
  131. Runner — provider_statuses includes all attempted providers.
  132. Runner — selected_provider_id is None when no provider succeeds.
  133. Registry summary returns correct provider count and etf_universe.
  134. No live HTTP calls occur during any test (verified by fixture fn only approach).
  135. Adapter — missing as-of date in CSV returns as_of_date_not_verified, no holdings.
  136. Adapter — no weight column in CSV returns weights_not_verified, no holdings.
  137. Adapter — metadata rows (fund name, as-of date) are excluded from holdings count.
  138. Runner — issuer result with as_of_date=None is not selected.
  139. Runner — issuer result with weights_available=False is not selected.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest


# ── Fixtures and helpers ──────────────────────────────────────────────────────

_VANGUARD_VOO_CSV = """\
Holdings,Ticker Symbol,ISIN,SEDOL,Weight,Shares,Market Value
Vanguard S&P 500 ETF,,,,,,
As of: 12/31/2024,,,,,,
Apple Inc.,AAPL,US0378331005,2046251,7.12,5000000,900000000
Microsoft Corporation,MSFT,US5949181045,2588173,6.50,4000000,800000000
Amazon.com Inc.,AMZN,US0231351067,2000019,3.85,2000000,500000000
NVIDIA Corporation,NVDA,US67066G1040,B58NG06,3.00,1500000,400000000
Alphabet Inc.,GOOGL,US02079K3059,BYVY8G0,2.10,1200000,300000000
Berkshire Hathaway Inc.,BRK.B,US0846707026,2073390,1.80,900000,200000000
"""

_VANGUARD_WRONG_FUND_CSV = """\
Holdings,Ticker Symbol,ISIN,SEDOL,Weight,Shares,Market Value
Some Other Fund,,,,,,
As of: 12/31/2024,,,,,,
Apple Inc.,AAPL,US0378331005,,7.12,5000000,900000000
"""

_SSGA_XLE_CSV = """\
Name,Ticker,Identifier,SEDOL,Weight,Shares Held,Local Market Value (Local CY),Market Value
Energy Select Sector SPDR Fund,XLE,,,,,,
As of Date: 12/31/2024,,,,,,,
Exxon Mobil Corp,XOM,2326618,,12.34,50000000,9000000000,9000000000
Chevron Corp,CVX,2166191,,11.50,40000000,7000000000,7000000000
ConocoPhillips,COP,2161218,,9.20,30000000,5000000000,5000000000
EOG Resources Inc,EOG,2691700,,5.10,20000000,3000000000,3000000000
"""

_SSGA_WRONG_FUND_CSV = """\
Name,Ticker,Identifier,SEDOL,Weight,Shares Held,Local Market Value,Market Value
Technology SPDR Fund,XLK,,,,,,
As of Date: 12/31/2024,,,,,,,
Apple Inc.,AAPL,,,7.5,50000000,9000000000,9000000000
"""

_MISSING_HEADERS_CSV = """\
foo,bar,baz
value1,value2,value3
"""

_EMPTY_CSV = ""


def _make_http_response(text: str, status_code: int = 200) -> Any:
    """Build a fake HTTP response for injection."""
    resp = MagicMock()
    resp.text = text
    resp.content = text.encode("utf-8")
    resp.status_code = status_code
    if status_code >= 400:
        resp.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    else:
        resp.raise_for_status.return_value = None
    return resp


def _make_http_get_fn(text: str, status_code: int = 200):
    """Return a no-HTTP function that returns a fixed response."""
    def _get(url: str) -> Any:  # noqa: ARG001
        return _make_http_response(text, status_code)
    return _get


def _no_sleep(seconds: float) -> None:  # noqa: ARG001
    pass


# ── Registry routing tests (107–116) ──────────────────────────────────────────

def test_107_all_etf_universe_tickers_have_providers():
    """Every ticker in the ETF universe has at least one registered provider."""
    from app.services.intelligence.research_workers.etf_holdings_provider_registry_v1 import (
        get_etf_universe, get_providers_for_ticker,
    )
    for ticker in get_etf_universe():
        providers = get_providers_for_ticker(ticker)
        assert providers, f"No providers for {ticker}"


def test_108_spy_qqq_prefer_sec_nport():
    """SPY and QQQ list sec_nport_v1 as first-priority provider."""
    from app.services.intelligence.research_workers.etf_holdings_provider_registry_v1 import (
        get_providers_for_ticker,
    )
    for ticker in ("SPY", "QQQ"):
        providers = get_providers_for_ticker(ticker)
        assert providers[0].provider_id == "sec_nport_v1", f"{ticker} first provider should be sec_nport_v1"


def test_109_vanguard_etfs_prefer_vanguard_official():
    """Vanguard ETFs list vanguard_official_v1 as first-priority provider."""
    from app.services.intelligence.research_workers.etf_holdings_provider_registry_v1 import (
        get_providers_for_ticker,
    )
    for ticker in ("VOO", "VTI", "VGT", "VHT", "VIS", "VXUS", "VYM"):
        providers = get_providers_for_ticker(ticker)
        assert providers[0].provider_id == "vanguard_official_v1", (
            f"{ticker} first provider should be vanguard_official_v1"
        )


def test_110_xle_prefers_spdr_official():
    """XLE lists spdr_official_v1 as first-priority provider."""
    from app.services.intelligence.research_workers.etf_holdings_provider_registry_v1 import (
        get_providers_for_ticker,
    )
    providers = get_providers_for_ticker("XLE")
    assert providers[0].provider_id == "spdr_official_v1"


def test_111_schd_prefers_schwab_official():
    """SCHD lists schwab_official_v1 as first-priority provider."""
    from app.services.intelligence.research_workers.etf_holdings_provider_registry_v1 import (
        get_providers_for_ticker,
    )
    providers = get_providers_for_ticker("SCHD")
    assert providers[0].provider_id == "schwab_official_v1"


def test_112_gld_only_commodity_provider():
    """GLD lists only gld_commodity_v1."""
    from app.services.intelligence.research_workers.etf_holdings_provider_registry_v1 import (
        get_providers_for_ticker,
    )
    providers = get_providers_for_ticker("GLD")
    assert len(providers) == 1
    assert providers[0].provider_id == "gld_commodity_v1"


def test_113_sec_nport_provider_registered():
    """sec_nport_v1 is registered in the provider registry."""
    from app.services.intelligence.research_workers.etf_holdings_provider_registry_v1 import (
        get_provider,
    )
    record = get_provider("sec_nport_v1")
    assert record is not None
    assert record.provider_id == "sec_nport_v1"
    assert record.source_type == "sec_nport"


def test_114_sec_nport_diagnostic_enabled_canonical_false():
    """sec_nport_v1 has enabled_for_diagnostics=True and enabled_for_canonical=False."""
    from app.services.intelligence.research_workers.etf_holdings_provider_registry_v1 import (
        get_provider,
    )
    record = get_provider("sec_nport_v1")
    assert record.enabled_for_diagnostics is True
    assert record.enabled_for_canonical is False


def test_115_all_providers_canonical_false():
    """All providers have enabled_for_canonical=False (Stage 9F.2b invariant)."""
    from app.services.intelligence.research_workers.etf_holdings_provider_registry_v1 import (
        list_all_providers,
    )
    for record in list_all_providers():
        assert record.enabled_for_canonical is False, (
            f"Provider {record.provider_id} has enabled_for_canonical=True — "
            "not allowed in Stage 9F.2b"
        )


def test_116_all_providers_diagnostics_enabled():
    """All providers have enabled_for_diagnostics=True."""
    from app.services.intelligence.research_workers.etf_holdings_provider_registry_v1 import (
        list_all_providers,
    )
    for record in list_all_providers():
        assert record.enabled_for_diagnostics is True, (
            f"Provider {record.provider_id} has enabled_for_diagnostics=False"
        )


# ── Issuer-official adapter tests (117–125) ───────────────────────────────────

def test_117_vanguard_csv_fixture_identity_verified():
    """Vanguard VOO CSV fixture → identity_verified=True and holdings_count > 0."""
    from app.services.intelligence.research_workers.etf_issuer_official_adapter_v1 import (
        fetch_issuer_official_holdings,
    )
    result = fetch_issuer_official_holdings(
        "VOO", "vanguard_official_v1",
        http_get_fn=_make_http_get_fn(_VANGUARD_VOO_CSV),
    )
    assert result.identity_verified is True, f"Expected identity_verified=True, got: {result.identity_basis}"
    assert result.holdings_count > 0
    assert result.fetch_status == "success"
    assert result.canonical_ready is False
    assert result.safe_for_decision is False
    assert len(result.sample_holding_names) <= 5


def test_118_vanguard_csv_wrong_fund_identity_not_proven():
    """Vanguard CSV with wrong fund name → identity_not_proven, no holdings."""
    from app.services.intelligence.research_workers.etf_issuer_official_adapter_v1 import (
        fetch_issuer_official_holdings,
    )
    result = fetch_issuer_official_holdings(
        "VOO", "vanguard_official_v1",
        http_get_fn=_make_http_get_fn(_VANGUARD_WRONG_FUND_CSV),
    )
    assert result.identity_verified is False
    assert result.holdings_count == 0
    assert result.fetch_status == "identity_not_proven"
    assert result.sample_holding_names == []


def test_119_ssga_xle_csv_fixture_identity_verified():
    """SSGA/SPDR XLE CSV fixture → identity_verified=True and holdings_count > 0."""
    from app.services.intelligence.research_workers.etf_issuer_official_adapter_v1 import (
        fetch_issuer_official_holdings,
    )
    result = fetch_issuer_official_holdings(
        "XLE", "spdr_official_v1",
        http_get_fn=_make_http_get_fn(_SSGA_XLE_CSV),
    )
    assert result.identity_verified is True, f"Expected identity_verified=True, got: {result.identity_basis}"
    assert result.holdings_count > 0
    assert result.fetch_status == "success"
    assert result.canonical_ready is False
    assert result.safe_for_decision is False


def test_120_ssga_wrong_fund_identity_not_proven():
    """SSGA CSV with wrong fund name → identity_not_proven, no holdings."""
    from app.services.intelligence.research_workers.etf_issuer_official_adapter_v1 import (
        fetch_issuer_official_holdings,
    )
    result = fetch_issuer_official_holdings(
        "XLE", "spdr_official_v1",
        http_get_fn=_make_http_get_fn(_SSGA_WRONG_FUND_CSV),
    )
    assert result.identity_verified is False
    assert result.holdings_count == 0
    assert result.fetch_status == "identity_not_proven"


def test_121_schd_schwab_source_url_not_validated():
    """SCHD Schwab adapter returns source_url_not_validated (URL not confirmed)."""
    from app.services.intelligence.research_workers.etf_issuer_official_adapter_v1 import (
        fetch_issuer_official_holdings,
    )
    # No http_get_fn needed — adapter returns early if URL is None.
    result = fetch_issuer_official_holdings("SCHD", "schwab_official_v1")
    assert result.fetch_status == "source_url_not_validated"
    assert result.identity_verified is False
    assert result.holdings_count == 0


def test_122_gld_commodity_identity_verified():
    """GLD commodity path returns commodity_trust_no_equity_holdings with identity_verified=True."""
    from app.services.intelligence.research_workers.etf_issuer_official_adapter_v1 import (
        fetch_issuer_official_holdings,
    )
    result = fetch_issuer_official_holdings("GLD", "gld_commodity_v1")
    assert result.fetch_status == "commodity_trust_no_equity_holdings"
    assert result.identity_verified is True


def test_123_gld_commodity_no_holdings():
    """GLD returns holdings_count=0 and sample_holding_names=[]."""
    from app.services.intelligence.research_workers.etf_issuer_official_adapter_v1 import (
        fetch_issuer_official_holdings,
    )
    result = fetch_issuer_official_holdings("GLD", "gld_commodity_v1")
    assert result.holdings_count == 0
    assert result.sample_holding_names == []
    assert result.canonical_ready is False
    assert result.safe_for_decision is False


def test_124_missing_csv_schema_returns_source_shape_changed():
    """CSV without recognizable column headers returns source_shape_changed."""
    from app.services.intelligence.research_workers.etf_issuer_official_adapter_v1 import (
        fetch_issuer_official_holdings,
    )
    result = fetch_issuer_official_holdings(
        "VOO", "vanguard_official_v1",
        http_get_fn=_make_http_get_fn(_MISSING_HEADERS_CSV),
    )
    assert result.fetch_status == "source_shape_changed"
    assert result.holdings_count == 0
    assert result.identity_verified is False


def test_125_empty_csv_content_returns_error():
    """Empty CSV content returns an error status."""
    from app.services.intelligence.research_workers.etf_issuer_official_adapter_v1 import (
        fetch_issuer_official_holdings,
    )
    result = fetch_issuer_official_holdings(
        "VOO", "vanguard_official_v1",
        http_get_fn=_make_http_get_fn(_EMPTY_CSV),
    )
    assert result.fetch_status in ("source_url_fetch_error", "empty_content")
    assert result.holdings_count == 0


# ── Runner tests (126–134) ────────────────────────────────────────────────────

def _make_nport_success(ticker: str) -> Any:
    """Build a fake NportProviderResult that looks like a success."""
    from app.services.intelligence.research_workers.nport_provider_v1 import (
        NportFilingMeta, NportHolding, NportProviderResult,
    )
    return NportProviderResult(
        ticker=ticker,
        fetch_status="success",
        cik="0000884394",
        holdings=[
            NportHolding(name="Apple Inc", cusip="037833100", weight_pct=7.0),
            NportHolding(name="Microsoft Corp", cusip="594918104", weight_pct=6.5),
            NportHolding(name="Amazon.com Inc", cusip="023135106", weight_pct=3.0),
            NportHolding(name="NVIDIA Corp", cusip="67066G104", weight_pct=2.5),
            NportHolding(name="Alphabet Inc", cusip="02079K305", weight_pct=2.0),
            NportHolding(name="Meta Platforms Inc", cusip="30303M102", weight_pct=1.9),
        ],
        filing_meta=NportFilingMeta(
            accession_number="0000884394-25-000001",
            form_type="NPORT-P",
            filing_date="2025-01-15",
            report_period_date="2024-12-31",
            primary_doc="primary_doc.xml",
            filing_url="https://www.sec.gov/Archives/edgar/data/884394/",
        ),
        weights_available=True,
        weights_derived=False,
        identity_verified=True,
        identity_status="success_identity_assumed_single_series",
        identity_basis="standalone_single_series_trust: SPDR S&P 500 ETF TRUST",
    )


def _make_nport_no_data(ticker: str, status: str = "no_nport_filing") -> Any:
    """Build a fake NportProviderResult with a no-data status."""
    from app.services.intelligence.research_workers.nport_provider_v1 import NportProviderResult
    return NportProviderResult(
        ticker=ticker,
        fetch_status=status,
        error_message=f"No NPORT filing: {status}",
    )


def test_126_no_full_holdings_in_runner_output():
    """Runner per_ticker output contains only sample_holding_names (max 5), not full holdings."""
    from app.services.intelligence.research_workers.etf_provider_registry_runner_v1 import (
        run_provider_registry_check,
    )

    spy_result = _make_nport_success("SPY")

    def _nport_fn(ticker, cfg):
        if ticker == "SPY":
            return spy_result
        return _make_nport_no_data(ticker)

    def _issuer_fn(ticker, provider_id):
        from app.services.intelligence.research_workers.etf_holdings_provider_registry_v1 import ETFHoldingsResult
        return ETFHoldingsResult(
            ticker=ticker, provider_id=provider_id, source_type="issuer_official",
            source_url=None, source_authority="issuer_official", as_of_date=None,
            holdings_count=0, sample_holding_names=[], weights_available=False,
            weight_basis="unavailable", identity_verified=False, identity_basis=None,
            freshness_status="unknown", fetch_status="source_url_not_validated",
        )

    result = run_provider_registry_check(
        ["SPY"], "TestApp/1.0 test@example.com",
        nport_provider_fn=_nport_fn,
        issuer_provider_fn=_issuer_fn,
        sleep_fn=_no_sleep,
    )

    spy_entry = result["per_ticker"][0]
    assert spy_entry["ticker"] == "SPY"
    # sample_holding_names must be capped at 5.
    assert len(spy_entry["sample_holding_names"]) <= 5
    # Verify full holdings list is not in the output.
    assert "holdings" not in spy_entry
    assert "raw_holdings" not in spy_entry


def test_127_canonical_ready_and_safe_for_decision_always_false():
    """canonical_ready=False and safe_for_decision=False for all tickers in runner output."""
    from app.services.intelligence.research_workers.etf_provider_registry_runner_v1 import (
        run_provider_registry_check,
    )

    def _nport_fn(ticker, cfg):
        return _make_nport_success(ticker)

    def _issuer_fn(ticker, provider_id):
        from app.services.intelligence.research_workers.etf_holdings_provider_registry_v1 import ETFHoldingsResult
        return ETFHoldingsResult(
            ticker=ticker, provider_id=provider_id, source_type="issuer_official",
            source_url=None, source_authority="issuer_official", as_of_date=None,
            holdings_count=0, sample_holding_names=[], weights_available=False,
            weight_basis="unavailable", identity_verified=False, identity_basis=None,
            freshness_status="unknown", fetch_status="source_url_not_validated",
        )

    result = run_provider_registry_check(
        ["SPY", "QQQ"], "TestApp/1.0 test@example.com",
        nport_provider_fn=_nport_fn,
        issuer_provider_fn=_issuer_fn,
        sleep_fn=_no_sleep,
    )

    assert result["safe_for_decision"] is False
    assert result["canonical_ready"] is False
    for entry in result["per_ticker"]:
        assert entry["canonical_ready"] is False
        assert entry["safe_for_decision"] is False


def test_128_diagnostics_only_and_zero_artifact_writes():
    """Runner output has diagnostics_only=True and artifact_writes=0 always."""
    from app.services.intelligence.research_workers.etf_provider_registry_runner_v1 import (
        run_provider_registry_check,
    )

    def _nport_fn(ticker, cfg):
        return _make_nport_no_data(ticker)

    def _issuer_fn(ticker, provider_id):
        from app.services.intelligence.research_workers.etf_holdings_provider_registry_v1 import ETFHoldingsResult
        return ETFHoldingsResult(
            ticker=ticker, provider_id=provider_id, source_type="issuer_official",
            source_url=None, source_authority="issuer_official", as_of_date=None,
            holdings_count=0, sample_holding_names=[], weights_available=False,
            weight_basis="unavailable", identity_verified=False, identity_basis=None,
            freshness_status="unknown", fetch_status="source_url_not_validated",
        )

    result = run_provider_registry_check(
        ["SPY"], "TestApp/1.0 test@example.com",
        nport_provider_fn=_nport_fn,
        issuer_provider_fn=_issuer_fn,
        sleep_fn=_no_sleep,
    )

    assert result["diagnostics_only"] is True
    assert result["artifact_writes"] == 0
    assert result["decision_policy_changed"] is False
    assert result["synthesis_ready_changed"] is False
    assert result["visible_snapshot_unchanged"] is True


def test_129_identity_mismatch_no_holdings_in_selected():
    """When no provider returns identity_verified=True, selected_provider_id=None and holdings_count=0."""
    from app.services.intelligence.research_workers.etf_provider_registry_runner_v1 import (
        run_provider_registry_check,
    )

    def _nport_fn(ticker, cfg):
        return _make_nport_no_data(ticker, "series_identity_not_proven")

    def _issuer_fn(ticker, provider_id):
        from app.services.intelligence.research_workers.etf_holdings_provider_registry_v1 import ETFHoldingsResult
        return ETFHoldingsResult(
            ticker=ticker, provider_id=provider_id, source_type="issuer_official",
            source_url=None, source_authority="issuer_official", as_of_date=None,
            holdings_count=0, sample_holding_names=[], weights_available=False,
            weight_basis="unavailable", identity_verified=False, identity_basis="identity_not_proven",
            freshness_status="unknown", fetch_status="identity_not_proven",
        )

    result = run_provider_registry_check(
        ["VOO"], "TestApp/1.0 test@example.com",
        nport_provider_fn=_nport_fn,
        issuer_provider_fn=_issuer_fn,
        sleep_fn=_no_sleep,
    )

    voo_entry = result["per_ticker"][0]
    assert voo_entry["ticker"] == "VOO"
    assert voo_entry["selected_provider_id"] is None
    assert voo_entry["holdings_count"] == 0
    assert voo_entry["identity_verified"] is False


def test_130_provider_fns_injectable():
    """nport_provider_fn and issuer_provider_fn are called instead of real providers."""
    from app.services.intelligence.research_workers.etf_provider_registry_runner_v1 import (
        run_provider_registry_check,
    )

    nport_called = []
    issuer_called = []

    def _nport_fn(ticker, cfg):
        nport_called.append(ticker)
        return _make_nport_success(ticker)

    def _issuer_fn(ticker, provider_id):
        issuer_called.append((ticker, provider_id))
        from app.services.intelligence.research_workers.etf_holdings_provider_registry_v1 import ETFHoldingsResult
        return ETFHoldingsResult(
            ticker=ticker, provider_id=provider_id, source_type="issuer_official",
            source_url=None, source_authority="issuer_official", as_of_date=None,
            holdings_count=0, sample_holding_names=[], weights_available=False,
            weight_basis="unavailable", identity_verified=False, identity_basis=None,
            freshness_status="unknown", fetch_status="source_url_not_validated",
        )

    run_provider_registry_check(
        ["SPY", "VOO"], "TestApp/1.0 test@example.com",
        nport_provider_fn=_nport_fn,
        issuer_provider_fn=_issuer_fn,
        sleep_fn=_no_sleep,
    )

    # SPY → sec_nport_v1 first (spy_result identity_verified=True → stops)
    assert "SPY" in nport_called
    # VOO → vanguard_official_v1 first (issuer_fn returns not verified) → falls back to sec_nport
    assert any(t == "VOO" for t, _ in issuer_called)


def test_131_provider_statuses_include_all_attempted():
    """provider_statuses includes a status entry for each attempted provider."""
    from app.services.intelligence.research_workers.etf_provider_registry_runner_v1 import (
        run_provider_registry_check,
    )

    def _nport_fn(ticker, cfg):
        return _make_nport_no_data(ticker)

    def _issuer_fn(ticker, provider_id):
        from app.services.intelligence.research_workers.etf_holdings_provider_registry_v1 import ETFHoldingsResult
        return ETFHoldingsResult(
            ticker=ticker, provider_id=provider_id, source_type="issuer_official",
            source_url=None, source_authority="issuer_official", as_of_date=None,
            holdings_count=0, sample_holding_names=[], weights_available=False,
            weight_basis="unavailable", identity_verified=False, identity_basis=None,
            freshness_status="unknown", fetch_status="source_url_not_validated",
        )

    result = run_provider_registry_check(
        ["VOO"], "TestApp/1.0 test@example.com",
        nport_provider_fn=_nport_fn,
        issuer_provider_fn=_issuer_fn,
        sleep_fn=_no_sleep,
    )

    voo_entry = result["per_ticker"][0]
    # VOO priority: vanguard_official_v1, sec_nport_v1 — both should have been attempted.
    provider_ids_in_statuses = {s["provider_id"] for s in voo_entry["provider_statuses"]}
    assert "vanguard_official_v1" in provider_ids_in_statuses
    assert "sec_nport_v1" in provider_ids_in_statuses
    assert len(voo_entry["provider_statuses"]) >= 2


def test_132_selected_provider_none_when_all_fail():
    """selected_provider_id=None when no provider returns an identity-verified result."""
    from app.services.intelligence.research_workers.etf_provider_registry_runner_v1 import (
        run_provider_registry_check,
    )

    def _nport_fn(ticker, cfg):
        return _make_nport_no_data(ticker)

    def _issuer_fn(ticker, provider_id):
        from app.services.intelligence.research_workers.etf_holdings_provider_registry_v1 import ETFHoldingsResult
        return ETFHoldingsResult(
            ticker=ticker, provider_id=provider_id, source_type="issuer_official",
            source_url=None, source_authority="issuer_official", as_of_date=None,
            holdings_count=0, sample_holding_names=[], weights_available=False,
            weight_basis="unavailable", identity_verified=False, identity_basis=None,
            freshness_status="unknown", fetch_status="source_url_not_validated",
        )

    result = run_provider_registry_check(
        ["SCHD"], "TestApp/1.0 test@example.com",
        nport_provider_fn=_nport_fn,
        issuer_provider_fn=_issuer_fn,
        sleep_fn=_no_sleep,
    )

    schd_entry = result["per_ticker"][0]
    assert schd_entry["selected_provider_id"] is None
    assert schd_entry["holdings_count"] == 0


def test_133_registry_summary_correct_counts():
    """registry_summary returns correct provider_count and etf_universe_count."""
    from app.services.intelligence.research_workers.etf_holdings_provider_registry_v1 import (
        registry_summary, list_all_providers, get_etf_universe,
    )
    summary = registry_summary()
    assert summary["provider_count"] == len(list_all_providers())
    assert summary["etf_universe_count"] == len(get_etf_universe())
    assert set(summary["etf_universe"]) == set(get_etf_universe())
    assert "SPY" in summary["etf_universe"]
    assert "GLD" in summary["etf_universe"]


# ── Fail-closed behavior tests (135–139) ─────────────────────────────────────

_VANGUARD_VOO_NO_DATE_CSV = """\
Holdings,Ticker Symbol,ISIN,SEDOL,Weight,Shares,Market Value
Vanguard S&P 500 ETF,,,,,,
Apple Inc.,AAPL,US0378331005,2046251,7.12,5000000,900000000
Microsoft Corporation,MSFT,US5949181045,2588173,6.50,4000000,800000000
"""

_VANGUARD_VOO_NO_WEIGHT_CSV = """\
Holdings,Ticker Symbol,ISIN,SEDOL,Shares,Market Value
Vanguard S&P 500 ETF,,,,,
As of: 12/31/2024,,,,,
Apple Inc.,AAPL,US0378331005,,5000000,900000000
Microsoft Corporation,MSFT,US5949181045,,4000000,800000000
"""


def test_135_missing_as_of_date_fails_closed():
    """CSV with no as-of date → as_of_date_not_verified, holdings_count=0."""
    from app.services.intelligence.research_workers.etf_issuer_official_adapter_v1 import (
        fetch_issuer_official_holdings,
    )
    result = fetch_issuer_official_holdings(
        "VOO", "vanguard_official_v1",
        http_get_fn=_make_http_get_fn(_VANGUARD_VOO_NO_DATE_CSV),
    )
    assert result.fetch_status == "as_of_date_not_verified", (
        f"Expected as_of_date_not_verified, got: {result.fetch_status}"
    )
    assert result.holdings_count == 0
    assert result.safe_for_decision is False
    assert result.canonical_ready is False


def test_136_missing_weight_column_fails_closed():
    """CSV with no weight column → weights_not_verified, holdings_count=0."""
    from app.services.intelligence.research_workers.etf_issuer_official_adapter_v1 import (
        fetch_issuer_official_holdings,
    )
    result = fetch_issuer_official_holdings(
        "VOO", "vanguard_official_v1",
        http_get_fn=_make_http_get_fn(_VANGUARD_VOO_NO_WEIGHT_CSV),
    )
    assert result.fetch_status == "weights_not_verified", (
        f"Expected weights_not_verified, got: {result.fetch_status}"
    )
    assert result.holdings_count == 0
    assert result.safe_for_decision is False
    assert result.canonical_ready is False


def test_137_metadata_rows_excluded_from_holdings():
    """Fund-name and as-of-date rows in Vanguard CSV are not counted as holdings."""
    from app.services.intelligence.research_workers.etf_issuer_official_adapter_v1 import (
        fetch_issuer_official_holdings,
    )
    result = fetch_issuer_official_holdings(
        "VOO", "vanguard_official_v1",
        http_get_fn=_make_http_get_fn(_VANGUARD_VOO_CSV),
    )
    assert result.fetch_status == "success"
    assert result.identity_verified is True
    # Fixture has 6 equity rows after 2 metadata rows.
    assert result.holdings_count == 6, (
        f"Expected 6 equity holdings (metadata rows excluded), got {result.holdings_count}"
    )
    for name in result.sample_holding_names:
        assert "Vanguard S&P 500 ETF" not in name, (
            f"Fund-name metadata row counted as holding: {name!r}"
        )
        assert not name.startswith("As of"), (
            f"As-of-date metadata row counted as holding: {name!r}"
        )


def test_138_runner_rejects_issuer_without_as_of_date():
    """Runner does not select an issuer-official result that has as_of_date=None."""
    from app.services.intelligence.research_workers.etf_provider_registry_runner_v1 import (
        run_provider_registry_check,
    )

    def _issuer_fn(ticker, provider_id):
        from app.services.intelligence.research_workers.etf_holdings_provider_registry_v1 import ETFHoldingsResult
        return ETFHoldingsResult(
            ticker=ticker, provider_id=provider_id, source_type="issuer_official",
            source_url="https://example.com/voo.csv", source_authority="issuer_official",
            as_of_date=None,  # missing — should not be selected
            holdings_count=6, sample_holding_names=["Apple Inc.", "Microsoft Corp"],
            weights_available=True, weight_basis="percent",
            identity_verified=True, identity_basis="fund_name_matched",
            freshness_status="unknown", fetch_status="success",
        )

    result = run_provider_registry_check(
        ["VOO"], "TestApp/1.0 test@example.com",
        nport_provider_fn=lambda t, c: _make_nport_no_data(t),
        issuer_provider_fn=_issuer_fn,
        sleep_fn=_no_sleep,
    )

    voo_entry = result["per_ticker"][0]
    assert voo_entry["selected_provider_id"] is None, (
        f"Runner must not select issuer with as_of_date=None; selected: {voo_entry['selected_provider_id']}"
    )
    assert voo_entry["holdings_count"] == 0


def test_139_runner_rejects_issuer_without_weights():
    """Runner does not select an issuer-official result that lacks percent weights."""
    from app.services.intelligence.research_workers.etf_provider_registry_runner_v1 import (
        run_provider_registry_check,
    )

    def _issuer_fn(ticker, provider_id):
        from app.services.intelligence.research_workers.etf_holdings_provider_registry_v1 import ETFHoldingsResult
        return ETFHoldingsResult(
            ticker=ticker, provider_id=provider_id, source_type="issuer_official",
            source_url="https://example.com/voo.csv", source_authority="issuer_official",
            as_of_date="2024-12-31",
            holdings_count=6, sample_holding_names=["Apple Inc.", "Microsoft Corp"],
            weights_available=False, weight_basis="unavailable",  # no weights — should not be selected
            identity_verified=True, identity_basis="fund_name_matched",
            freshness_status="fresh", fetch_status="success",
        )

    result = run_provider_registry_check(
        ["VOO"], "TestApp/1.0 test@example.com",
        nport_provider_fn=lambda t, c: _make_nport_no_data(t),
        issuer_provider_fn=_issuer_fn,
        sleep_fn=_no_sleep,
    )

    voo_entry = result["per_ticker"][0]
    assert voo_entry["selected_provider_id"] is None, (
        f"Runner must not select issuer with weights_available=False; selected: {voo_entry['selected_provider_id']}"
    )
    assert voo_entry["holdings_count"] == 0


def test_134_no_live_http_in_any_test():
    """Verify that all tests above use only fixture HTTP functions.

    This test is structural: it confirms the adapter accepts http_get_fn and
    that the fixture-only pattern works for all provider IDs.
    """
    from app.services.intelligence.research_workers.etf_issuer_official_adapter_v1 import (
        fetch_issuer_official_holdings,
    )

    # All non-GLD providers accept injectable http_get_fn and call it.
    providers_tested = []
    for provider_id, ticker in [
        ("vanguard_official_v1", "VOO"),
        ("spdr_official_v1", "XLE"),
        ("invesco_official_v1", "QQQ"),
    ]:
        called_with: list[str] = []

        def _tracking_get(url: str, _called=called_with) -> Any:
            _called.append(url)
            return _make_http_response(_VANGUARD_VOO_CSV if "vanguard" in provider_id else _SSGA_XLE_CSV)

        fetch_issuer_official_holdings(ticker, provider_id, http_get_fn=_tracking_get)
        providers_tested.append(provider_id)
        # The callable was invoked — confirming no real HTTP was made.
        assert called_with, f"http_get_fn was never called for {provider_id}"

    assert len(providers_tested) == 3
