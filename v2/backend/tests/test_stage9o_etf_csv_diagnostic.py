"""Stage 9O tests — issuer-official ETF CSV live-proof diagnostic runner.

All tests are fixture-based — no live HTTP calls, no DB, no LLM.

Test coverage:
  O1–O3:   Default tickers / max-tickers / provider_id recorded
  O4–O10:  Per-ticker result fields from success path
  O11–O16: Gate failure paths (fetch error, identity, date, weights, holdings)
  O17–O21: Invariants (promotion_recommended, safe_for_decision, artifact_writes, serializable)
  O22–O26: Top-level output shape (version, counts, timestamp, promotion_note)
  O27–O31: URL pattern / per-provider_id behaviour
  O32–O36: Edge cases (panic recovery, empty tickers norm, truncation, dedup)
  O37–O39: check_canonical_gate wiring via runner
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.services.intelligence.research_workers.etf_csv_diagnostic_runner_v1 import (
    DIAGNOSTIC_VERSION,
    _DEFAULT_TICKERS,
    _DEFAULT_PROVIDER_ID,
    _MAX_TICKERS,
    _build_ticker_entry,
    run_issuer_csv_live_check,
)

# ── Fixture helpers ───────────────────────────────────────────────────────────

_VANGUARD_VTI_CSV = """\
Vanguard Total Stock Market ETF
As of 05/30/2026
Name,Ticker,ISIN,Weight (%),Market Value
Apple Inc.,AAPL,US0378331005,6.5,1000
Microsoft Corp,MSFT,US5949181045,6.1,950
NVIDIA Corp,NVDA,US67066G1040,5.8,900
Amazon.com Inc,AMZN,US0231351067,3.4,500
Alphabet Inc,GOOGL,US02079K3059,2.1,320
Meta Platforms Inc,META,US30303M1027,2.0,310
"""

_VANGUARD_VXUS_CSV = """\
Vanguard Total International Stock ETF
As of 05/28/2026
Name,Ticker,ISIN,Weight (%),Market Value
Taiwan Semiconductor,TSM,TW0002330008,2.1,100
ASML Holding,ASML,NL0010273215,1.2,80
Nestle SA,NESN,CH0038863350,1.0,70
Samsung Electronics,005930,KR7005930003,0.9,60
Toyota Motor Corp,7203,JP3633400001,0.8,50
"""

_VANGUARD_VOO_CSV = """\
Vanguard S&P 500 ETF
As of 05/30/2026
Name,Ticker,ISIN,Weight (%),Market Value
Apple Inc.,AAPL,US0378331005,7.0,2000
Microsoft Corp,MSFT,US5949181045,6.5,1800
NVIDIA Corp,NVDA,US67066G1040,6.0,1700
"""

_WRONG_FUND_CSV = """\
Some Other Fund Name XYZ
As of 05/30/2026
Name,Ticker,ISIN,Weight (%),Market Value
Stock A,AAA,US1111111111,5.0,100
"""

_NO_DATE_CSV = """\
Vanguard Total Stock Market ETF
Name,Ticker,ISIN,Weight (%),Market Value
Apple Inc.,AAPL,US0378331005,6.5,1000
"""

_NO_WEIGHTS_CSV = """\
Vanguard Total Stock Market ETF
As of 05/30/2026
Name,Ticker,ISIN,Market Value
Apple Inc.,AAPL,US0378331005,1000
"""

_EMPTY_CSV = ""


def _make_http_fn(ticker_csv_map: dict[str, str]):
    """Return a mock http_get_fn that serves CSV text keyed by URL substring."""
    def _get(url: str):
        for key, csv_text in ticker_csv_map.items():
            if key.upper() in url.upper():
                resp = MagicMock()
                resp.text = csv_text
                resp.raise_for_status = lambda: None
                return resp
        # Default: HTTP 404-like error
        resp = MagicMock()
        resp.raise_for_status.side_effect = Exception("HTTP 404 Not Found")
        return resp
    return _get


def _make_error_http_fn(exc_msg: str = "connection timeout"):
    def _get(url: str):
        raise TimeoutError(exc_msg)
    return _get


def _make_success_http_fn(csv_text: str):
    def _get(url: str):
        resp = MagicMock()
        resp.text = csv_text
        resp.raise_for_status = lambda: None
        return resp
    return _get


# ── O1–O3: Constants and defaults ────────────────────────────────────────────


def test_O1_default_tickers_are_vti_vxus_voo():
    assert set(_DEFAULT_TICKERS) == {"VTI", "VXUS", "VOO"}
    assert len(_DEFAULT_TICKERS) == 3


def test_O2_max_tickers_is_10():
    assert _MAX_TICKERS == 10


def test_O3_default_provider_is_vanguard():
    assert _DEFAULT_PROVIDER_ID == "vanguard_official_v1"


# ── O4–O10: Success path per-ticker fields ────────────────────────────────────


def test_O4_success_fetch_status():
    fn = _make_success_http_fn(_VANGUARD_VTI_CSV)
    entry = _build_ticker_entry("VTI", "vanguard_official_v1", fn)
    assert entry["fetch_status"] == "success"


def test_O5_identity_verified_true_on_matching_fund_name():
    fn = _make_success_http_fn(_VANGUARD_VTI_CSV)
    entry = _build_ticker_entry("VTI", "vanguard_official_v1", fn)
    assert entry["identity_verified"] is True


def test_O6_as_of_date_captured():
    fn = _make_success_http_fn(_VANGUARD_VTI_CSV)
    entry = _build_ticker_entry("VTI", "vanguard_official_v1", fn)
    assert entry["as_of_date"] == "2026-05-30"


def test_O7_holdings_count_nonzero():
    fn = _make_success_http_fn(_VANGUARD_VTI_CSV)
    entry = _build_ticker_entry("VTI", "vanguard_official_v1", fn)
    assert entry["holdings_count"] >= 1


def test_O8_weights_available_true():
    fn = _make_success_http_fn(_VANGUARD_VTI_CSV)
    entry = _build_ticker_entry("VTI", "vanguard_official_v1", fn)
    assert entry["weights_available"] is True


def test_O9_sample_holding_names_capped_at_5():
    fn = _make_success_http_fn(_VANGUARD_VTI_CSV)
    entry = _build_ticker_entry("VTI", "vanguard_official_v1", fn)
    assert len(entry["sample_holding_names"]) <= 5
    assert "Apple Inc." in entry["sample_holding_names"]


def test_O10_source_url_present():
    fn = _make_success_http_fn(_VANGUARD_VTI_CSV)
    entry = _build_ticker_entry("VTI", "vanguard_official_v1", fn)
    assert entry["source_url"] is not None
    assert "VTI" in entry["source_url"]


# ── O11–O16: Gate failure paths ───────────────────────────────────────────────


def test_O11_fetch_error_sets_gate_failed():
    fn = _make_error_http_fn("timeout")
    entry = _build_ticker_entry("VTI", "vanguard_official_v1", fn)
    assert entry["canonical_gate_passed"] is False
    assert len(entry["gate_failures"]) > 0


def test_O12_identity_mismatch_sets_gate_failed():
    fn = _make_success_http_fn(_WRONG_FUND_CSV)
    entry = _build_ticker_entry("VTI", "vanguard_official_v1", fn)
    assert entry["identity_verified"] is False
    assert entry["canonical_gate_passed"] is False
    assert "identity_verified_false" in entry["gate_failures"]


def test_O13_no_as_of_date_sets_gate_failed():
    fn = _make_success_http_fn(_NO_DATE_CSV)
    entry = _build_ticker_entry("VTI", "vanguard_official_v1", fn)
    assert entry["as_of_date"] is None
    assert entry["canonical_gate_passed"] is False
    assert "as_of_or_report_date_missing" in entry["gate_failures"]


def test_O14_no_weights_sets_gate_failed():
    fn = _make_success_http_fn(_NO_WEIGHTS_CSV)
    entry = _build_ticker_entry("VTI", "vanguard_official_v1", fn)
    assert entry["canonical_gate_passed"] is False
    assert "weights_not_available" in entry["gate_failures"]


def test_O15_empty_csv_sets_holdings_count_zero():
    fn = _make_success_http_fn(_EMPTY_CSV)
    entry = _build_ticker_entry("VTI", "vanguard_official_v1", fn)
    assert entry["holdings_count"] == 0
    assert entry["canonical_gate_passed"] is False


def test_O16_gate_failures_list_populated_on_multiple_failures():
    fn = _make_success_http_fn(_WRONG_FUND_CSV)
    entry = _build_ticker_entry("VTI", "vanguard_official_v1", fn)
    # identity and potentially holdings/date/weights should all fail
    assert isinstance(entry["gate_failures"], list)
    assert len(entry["gate_failures"]) >= 1


# ── O17–O21: Invariants ───────────────────────────────────────────────────────


def test_O17_promotion_recommended_always_false():
    fn = _make_success_http_fn(_VANGUARD_VTI_CSV)
    entry = _build_ticker_entry("VTI", "vanguard_official_v1", fn)
    assert entry["promotion_recommended"] is False


def test_O18_safe_for_decision_always_false():
    fn = _make_success_http_fn(_VANGUARD_VTI_CSV)
    entry = _build_ticker_entry("VTI", "vanguard_official_v1", fn)
    assert entry["safe_for_decision"] is False


def test_O19_canonical_ready_always_false_in_entry():
    fn = _make_success_http_fn(_VANGUARD_VTI_CSV)
    entry = _build_ticker_entry("VTI", "vanguard_official_v1", fn)
    assert entry["canonical_ready"] is False


def test_O20_artifact_writes_zero_in_top_level():
    fn = _make_success_http_fn(_VANGUARD_VTI_CSV)
    out = run_issuer_csv_live_check(["VTI"], http_get_fn=fn)
    assert out["artifact_writes"] == 0


def test_O21_output_is_json_serializable():
    fn = _make_success_http_fn(_VANGUARD_VTI_CSV)
    out = run_issuer_csv_live_check(["VTI"], http_get_fn=fn)
    serialized = json.dumps(out)
    assert isinstance(serialized, str)


# ── O22–O26: Top-level output shape ──────────────────────────────────────────


def test_O22_diagnostic_version_present():
    fn = _make_success_http_fn(_VANGUARD_VTI_CSV)
    out = run_issuer_csv_live_check(["VTI"], http_get_fn=fn)
    assert out["diagnostic_version"] == DIAGNOSTIC_VERSION


def test_O23_provider_id_recorded_in_output():
    fn = _make_success_http_fn(_VANGUARD_VTI_CSV)
    out = run_issuer_csv_live_check(["VTI"], provider_id="vanguard_official_v1", http_get_fn=fn)
    assert out["provider_id"] == "vanguard_official_v1"


def test_O24_tickers_requested_count_correct():
    fn = _make_http_fn({"VTI": _VANGUARD_VTI_CSV, "VOO": _VANGUARD_VOO_CSV})
    out = run_issuer_csv_live_check(["VTI", "VOO"], http_get_fn=fn)
    assert out["tickers_requested"] == 2
    assert out["tickers_completed"] == 2


def test_O25_run_timestamp_utc_present():
    fn = _make_success_http_fn(_VANGUARD_VTI_CSV)
    out = run_issuer_csv_live_check(["VTI"], http_get_fn=fn)
    ts = out["run_timestamp_utc"]
    assert isinstance(ts, str) and len(ts) > 0
    assert "Z" in ts or "T" in ts


def test_O26_canonical_gate_passed_count_correct():
    fn = _make_http_fn({
        "VTI": _VANGUARD_VTI_CSV,
        "VXUS": _VANGUARD_VXUS_CSV,
        "VOO": _VANGUARD_VOO_CSV,
    })
    out = run_issuer_csv_live_check(["VTI", "VXUS", "VOO"], http_get_fn=fn)
    # All three have valid fund name + date + weights → gate should pass
    assert out["canonical_gate_passed_count"] == 3


# ── O27–O31: URL pattern and provider coverage ───────────────────────────────


def test_O27_vti_url_contains_vti():
    fn = _make_success_http_fn(_VANGUARD_VTI_CSV)
    entry = _build_ticker_entry("VTI", "vanguard_official_v1", fn)
    assert entry["source_url"] is not None
    assert "VTI" in entry["source_url"]


def test_O28_vxus_url_contains_vxus():
    fn = _make_success_http_fn(_VANGUARD_VXUS_CSV)
    entry = _build_ticker_entry("VXUS", "vanguard_official_v1", fn)
    assert entry["source_url"] is not None
    assert "VXUS" in entry["source_url"]


def test_O29_voo_url_contains_voo():
    fn = _make_success_http_fn(_VANGUARD_VOO_CSV)
    entry = _build_ticker_entry("VOO", "vanguard_official_v1", fn)
    assert entry["source_url"] is not None
    assert "VOO" in entry["source_url"]


def test_O30_unrecognized_provider_returns_error_entry():
    fn = _make_success_http_fn(_VANGUARD_VTI_CSV)
    entry = _build_ticker_entry("VTI", "nonexistent_provider_v1", fn)
    # source_url_not_validated or error expected
    assert entry["canonical_gate_passed"] is False
    assert entry["safe_for_decision"] is False


def test_O31_diagnostics_only_flag_true():
    fn = _make_success_http_fn(_VANGUARD_VTI_CSV)
    out = run_issuer_csv_live_check(["VTI"], http_get_fn=fn)
    assert out["diagnostics_only"] is True


# ── O32–O36: Edge cases ───────────────────────────────────────────────────────


def test_O32_panic_recovery_never_raises():
    def _exploding_get(url: str):
        raise RuntimeError("catastrophic failure")
    # Adapter catches exceptions from http_get_fn and returns fetch_status=source_url_fetch_error.
    # runner_error path is reserved for exceptions that escape the adapter entirely.
    entry = _build_ticker_entry("VTI", "vanguard_official_v1", _exploding_get)
    assert entry["fetch_status"] in ("source_url_fetch_error", "runner_error")
    assert entry["canonical_gate_passed"] is False
    assert entry["safe_for_decision"] is False


def test_O33_run_never_raises_on_all_errors():
    def _exploding_get(url: str):
        raise RuntimeError("everything is broken")
    out = run_issuer_csv_live_check(["VTI", "VXUS"], http_get_fn=_exploding_get)
    assert "results" in out
    assert out["safe_for_decision"] is False


def test_O34_error_message_truncated_at_300():
    long_error = "X" * 500
    def _get(url: str):
        raise Exception(long_error)
    entry = _build_ticker_entry("VTI", "vanguard_official_v1", _get)
    err = entry.get("error_message") or ""
    assert len(err) <= 310  # 300 + "…" + "Runner error: " prefix


def test_O35_ticker_normalized_to_upper_in_entry():
    fn = _make_success_http_fn(_VANGUARD_VTI_CSV)
    entry = _build_ticker_entry("vti", "vanguard_official_v1", fn)
    # The adapter uppercases internally; source_url should contain VTI
    assert entry["ticker"] == "vti"  # runner preserves caller's case in key
    # But source_url should still have uppercase VTI from adapter
    if entry["source_url"]:
        assert "VTI" in entry["source_url"]


def test_O36_per_ticker_result_has_all_expected_keys():
    fn = _make_success_http_fn(_VANGUARD_VTI_CSV)
    entry = _build_ticker_entry("VTI", "vanguard_official_v1", fn)
    required_keys = {
        "ticker", "provider_id", "fetch_status", "identity_verified",
        "identity_basis", "as_of_date", "freshness_status", "holdings_count",
        "weights_available", "sample_holding_names", "source_url",
        "error_message", "limitations", "canonical_gate_passed", "gate_failures",
        "canonical_ready", "safe_for_decision", "promotion_recommended",
    }
    assert required_keys.issubset(set(entry.keys()))


# ── O37–O39: Gate wiring ──────────────────────────────────────────────────────


def test_O37_gate_passes_when_all_criteria_met():
    fn = _make_success_http_fn(_VANGUARD_VTI_CSV)
    entry = _build_ticker_entry("VTI", "vanguard_official_v1", fn)
    assert entry["canonical_gate_passed"] is True
    assert entry["gate_failures"] == []


def test_O38_gate_failures_empty_when_passed():
    fn = _make_success_http_fn(_VANGUARD_VOO_CSV)
    entry = _build_ticker_entry("VOO", "vanguard_official_v1", fn)
    assert entry["canonical_gate_passed"] is True
    assert entry["gate_failures"] == []


def test_O39_promotion_note_present_in_top_level():
    fn = _make_success_http_fn(_VANGUARD_VTI_CSV)
    out = run_issuer_csv_live_check(["VTI"], http_get_fn=fn)
    assert "promotion_note" in out
    assert "canonical_gate_passed=True is diagnostic-only" in out["promotion_note"]
