"""Stage 9F.2a — ETF NPORT-P diagnostic runner and endpoint tests.

Tests the nport_diagnostic_runner module (pure business logic, no FastAPI/Supabase
dependency) plus structural checks for the endpoint wiring.

Coverage:

  64. Endpoint flag OFF → runner not called; result shape safe when disabled.
  65. Mocked success provider → per-ticker JSON correct shape and values.
  66. Response never contains raw XML or raw filing body.
  67. Error message truncated to 200 chars + ellipsis.
  68. Default tickers include Vanguard (VOO, VGT, VHT, VIS, VXUS, VYM), SCHD, GLD.
  69. No artifact write function called during run.
  70. Non-success result → holdings_count=0 and sample_holding_names=[].
  71. Aggregate counts (succeeded/no_data/error) correct in response.
  72. Missing SEC_EDGAR_USER_AGENT guard: safe to test at runner level (caller checked first).
  73. Existing Stage 9F.2a provider/adapter imports unaffected by new runner module.
  74. safe_for_decision is always False in runner output.
  75. artifact_writes is always 0 in runner output.
  76. visible_snapshot_unchanged is always True in runner output.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_success_result(ticker: str = "SPY") -> object:
    from app.services.intelligence.research_workers.nport_provider_v1 import (
        NportFilingMeta,
        NportHolding,
        NportProviderResult,
    )
    return NportProviderResult(
        ticker=ticker,
        fetch_status="success",
        cik="0000884394",
        holdings=[
            NportHolding(name="Apple Inc", cusip="037833100", weight_pct=7.0),
            NportHolding(name="Microsoft Corp", cusip="594918104", weight_pct=6.5),
        ],
        filing_meta=NportFilingMeta(
            accession_number="0000884394-25-000001",
            form_type="NPORT-P",
            filing_date="2025-01-15",
            report_period_date="2024-12-31",
            primary_doc="primary_doc.xml",
            filing_url="https://www.sec.gov/Archives/edgar/data/884394/000088439425000001/",
        ),
        weights_available=True,
        weights_derived=False,
    )


def _make_error_result(ticker: str, fetch_status: str, message: str = "error") -> object:
    from app.services.intelligence.research_workers.nport_provider_v1 import NportProviderResult
    return NportProviderResult(
        ticker=ticker,
        fetch_status=fetch_status,
        error_message=message,
        cik="0000074260",
    )


def _no_sleep(seconds: float) -> None:  # noqa: ARG001
    """Test sleep_fn that skips all delays."""


# ── Test 64 — when endpoint flag is off, runner is never reached ──────────────
# The endpoint raises HTTPException(404) before calling run_nport_live_check.
# Verified structurally: the endpoint body guards on settings.intel_v3_nport_diagnostic_endpoint_enabled.

def test_64_runner_not_invoked_when_flag_off():
    """Confirm run_nport_live_check is never called when the flag is off.

    The endpoint in diagnostics.py raises HTTPException(404) before touching
    the runner. This test validates the guard is present in the endpoint source.
    """
    import inspect
    import ast

    # Read the endpoint source without importing the router (avoids supabase/jwt)
    import pathlib
    src = pathlib.Path(
        __file__
    ).parent.parent / "app" / "routers" / "diagnostics.py"
    source = src.read_text()

    # Verify the flag check and 404 are both present in the endpoint
    assert "intel_v3_nport_diagnostic_endpoint_enabled" in source
    assert "HTTP_404_NOT_FOUND" in source
    # Verify the flag check appears before the runner import so the module is never
    # loaded (and the runner never called) when the flag is off.
    fn_start = source.index("async def etf_nport_live_check")
    fn_source = source[fn_start:]
    flag_pos = fn_source.index("intel_v3_nport_diagnostic_endpoint_enabled")
    import_pos = fn_source.index("run_nport_live_check")  # first occurrence = lazy import
    assert flag_pos < import_pos, "Flag check must appear before runner import"


# ── Test 65 — mocked success provider → correct per-ticker JSON ───────────────

def test_65_success_provider_returns_compact_json():
    from app.services.intelligence.research_workers.nport_diagnostic_runner import (
        run_nport_live_check,
    )

    mock_result = _make_success_result("SPY")

    def _mock_provider(ticker, cfg):
        return mock_result

    out = run_nport_live_check(["SPY"], "TestApp/1.0 test@example.com",
                               provider_fn=_mock_provider, sleep_fn=_no_sleep)

    assert out["tickers_requested"] == 1
    assert out["tickers_succeeded"] == 1
    row = out["per_ticker"][0]
    assert row["ticker"] == "SPY"
    assert row["holdings_count"] == 2
    assert row["fetch_status"] == "success"
    assert row["resolved_cik"] == "0000884394"
    assert row["form_type"] == "NPORT-P"
    assert row["filing_date"] == "2025-01-15"
    assert row["report_period_date"] == "2024-12-31"
    assert row["weights_available"] is True
    assert row["weights_derived"] is False
    assert row["sample_holdings_count"] == 2
    assert "Apple Inc" in row["sample_holding_names"]


# ── Test 66 — response excludes raw XML, filing body, full holdings ───────────

def test_66_response_excludes_raw_xml():
    from app.services.intelligence.research_workers.nport_diagnostic_runner import (
        run_nport_live_check,
    )

    def _mock_provider(ticker, cfg):
        return _make_success_result(ticker)

    out = run_nport_live_check(["SPY"], "TestApp/1.0 test@example.com",
                               provider_fn=_mock_provider, sleep_fn=_no_sleep)

    serialized = json.dumps(out)
    assert "<?xml" not in serialized
    assert "<edgarSubmission" not in serialized
    assert "<SEC-DOCUMENT" not in serialized
    assert "<invstOrSecs" not in serialized

    row = out["per_ticker"][0]
    assert "raw_xml" not in row
    assert "filing_body" not in row
    # Full holdings list must NOT be in the per-ticker entry
    assert "holdings" not in row


# ── Test 67 — error message truncated at 200 chars ───────────────────────────

def test_67_error_message_truncated():
    from app.services.intelligence.research_workers.nport_diagnostic_runner import (
        run_nport_live_check,
    )

    long_msg = "X" * 500
    error_result = _make_error_result("VHT", "sec_error", long_msg)

    def _mock_provider(ticker, cfg):
        return error_result

    out = run_nport_live_check(["VHT"], "TestApp/1.0 test@example.com",
                               provider_fn=_mock_provider, sleep_fn=_no_sleep)

    row = out["per_ticker"][0]
    assert row["error_message"] is not None
    assert len(row["error_message"]) <= 201   # 200 chars + ellipsis char
    assert row["error_message"].endswith("…")


# ── Test 68 — default tickers include Vanguard, SCHD, GLD ────────────────────

def test_68_default_tickers_include_vanguard_schd_gld():
    from app.services.intelligence.research_workers.nport_diagnostic_runner import (
        _NPORT_DIAG_DEFAULT_TICKERS,
    )

    required = {"VOO", "VGT", "VHT", "VIS", "VXUS", "VYM", "SCHD", "GLD"}
    missing = required - set(_NPORT_DIAG_DEFAULT_TICKERS)
    assert not missing, f"Default tickers missing: {missing}"
    assert len(_NPORT_DIAG_DEFAULT_TICKERS) >= 12


# ── Test 69 — provider called; no artifact write occurs ──────────────────────

def test_69_no_artifact_writes():
    from app.services.intelligence.research_workers.nport_diagnostic_runner import (
        run_nport_live_check,
    )

    call_log = []

    def _mock_provider(ticker, cfg):
        call_log.append(ticker)
        return _make_success_result(ticker)

    write_spy = MagicMock()

    with patch(
        "app.services.intelligence.research_workers.etf_nport_adapter_v1.build_etf_nport_worker_output",
        write_spy,
    ):
        out = run_nport_live_check(["SPY"], "TestApp/1.0 test@example.com",
                                   provider_fn=_mock_provider, sleep_fn=_no_sleep)

    # Provider was called once
    assert call_log == ["SPY"]
    # Adapter (artifact writer) was never called
    write_spy.assert_not_called()
    assert out["artifact_writes"] == 0


# ── Test 70 — non-success result → holdings_count=0 and empty names ──────────

def test_70_non_success_holdings_count_zero():
    from app.services.intelligence.research_workers.nport_diagnostic_runner import (
        run_nport_live_check,
    )

    error_result = _make_error_result("VHT", "no_nport_filing", "no filing found")

    def _mock_provider(ticker, cfg):
        return error_result

    out = run_nport_live_check(["VHT"], "TestApp/1.0 test@example.com",
                               provider_fn=_mock_provider, sleep_fn=_no_sleep)

    row = out["per_ticker"][0]
    assert row["holdings_count"] == 0
    assert row["sample_holding_names"] == []
    assert row["sample_holdings_count"] == 0
    assert row["fetch_status"] == "no_nport_filing"


# ── Test 71 — aggregate counts correct ───────────────────────────────────────

def test_71_aggregate_counts_correct():
    from app.services.intelligence.research_workers.nport_diagnostic_runner import (
        run_nport_live_check,
    )

    results = {
        "SPY": _make_success_result("SPY"),
        "VOO": _make_error_result("VOO", "no_nport_filing", "no filing"),
        "VHT": _make_error_result("VHT", "sec_error", "403"),
    }

    def _mock_provider(ticker, cfg):
        return results[ticker]

    out = run_nport_live_check(
        ["SPY", "VOO", "VHT"],
        "TestApp/1.0 test@example.com",
        provider_fn=_mock_provider,
        sleep_fn=_no_sleep,
    )

    assert out["tickers_requested"] == 3
    assert out["tickers_succeeded"] == 1    # SPY
    assert out["tickers_no_data"] == 1      # VOO (no_nport_filing)
    assert out["tickers_error"] == 1        # VHT (sec_error)
    assert len(out["per_ticker"]) == 3


# ── Test 72 — commodity_trust classified as no_data (not error) ──────────────

def test_72_commodity_trust_counts_as_no_data():
    from app.services.intelligence.research_workers.nport_diagnostic_runner import (
        run_nport_live_check,
    )

    gld_result = _make_error_result("GLD", "commodity_trust_or_no_nport_data", "")

    def _mock_provider(ticker, cfg):
        return gld_result

    out = run_nport_live_check(["GLD"], "TestApp/1.0 test@example.com",
                               provider_fn=_mock_provider, sleep_fn=_no_sleep)

    assert out["tickers_no_data"] == 1
    assert out["tickers_error"] == 0


# ── Test 73 — existing Stage 9F.2a imports unaffected ────────────────────────

def test_73_existing_stage9f2a_imports_unaffected():
    from app.services.intelligence.research_workers.nport_provider_v1 import (
        fetch_etf_nport_holdings,
        NportProviderConfig,
        NportProviderResult,
    )
    from app.services.intelligence.research_workers.etf_nport_adapter_v1 import (
        build_etf_nport_worker_output,
    )
    from app.services.intelligence.research_workers.nport_diagnostic_runner import (
        run_nport_live_check,
        _NPORT_DIAG_DEFAULT_TICKERS,
        _NPORT_DIAG_MAX_TICKERS,
        _NPORT_DIAG_ERROR_MSG_MAX_LEN,
    )

    assert callable(fetch_etf_nport_holdings)
    assert callable(build_etf_nport_worker_output)
    assert callable(run_nport_live_check)
    assert isinstance(_NPORT_DIAG_DEFAULT_TICKERS, list)
    assert _NPORT_DIAG_MAX_TICKERS >= 12
    assert _NPORT_DIAG_ERROR_MSG_MAX_LEN == 200


# ── Test 74 — safe_for_decision always False ──────────────────────────────────

def test_74_safe_for_decision_always_false():
    from app.services.intelligence.research_workers.nport_diagnostic_runner import (
        run_nport_live_check,
    )

    def _mock_provider(ticker, cfg):
        return _make_success_result(ticker)

    out = run_nport_live_check(["SPY"], "TestApp/1.0 test@example.com",
                               provider_fn=_mock_provider, sleep_fn=_no_sleep)

    assert out["safe_for_decision"] is False


# ── Test 75 — artifact_writes always 0 ───────────────────────────────────────

def test_75_artifact_writes_always_zero():
    from app.services.intelligence.research_workers.nport_diagnostic_runner import (
        run_nport_live_check,
    )

    def _mock_provider(ticker, cfg):
        return _make_success_result(ticker)

    out = run_nport_live_check(["SPY"], "TestApp/1.0 test@example.com",
                               provider_fn=_mock_provider, sleep_fn=_no_sleep)

    assert out["artifact_writes"] == 0


# ── Test 76 — visible_snapshot_unchanged always True ─────────────────────────

def test_76_visible_snapshot_unchanged_always_true():
    from app.services.intelligence.research_workers.nport_diagnostic_runner import (
        run_nport_live_check,
    )

    def _mock_provider(ticker, cfg):
        return _make_error_result(ticker, "sec_error")

    out = run_nport_live_check(["VHT"], "TestApp/1.0 test@example.com",
                               provider_fn=_mock_provider, sleep_fn=_no_sleep)

    assert out["visible_snapshot_unchanged"] is True
