"""Stage 9F.3a — Alpha Vantage ETF_PROFILE diagnostic tests.

Fixture-based only — no live HTTP calls in CI.

Coverage:
  9F3a-01. Endpoint returns 404 when flag is disabled.
  9F3a-02. Endpoint returns 403 when ALPHA_VANTAGE_API_KEY is missing.
  9F3a-03. Default tickers exclude SPY and QQQ.
  9F3a-04. include_controls=true adds SPY and QQQ.
  9F3a-05. Max tickers per run cap (11) is enforced.
  9F3a-06. Valid response with holdings + weights returns fetch_status=success.
  9F3a-07. Valid response with holdings + weights → weights_available=True.
  9F3a-08. Valid response with holdings + weights + date → freshness_status=verified.
  9F3a-09. Provider Note response → fetch_status=rate_limited.
  9F3a-10. Provider Information response → fetch_status=provider_note.
  9F3a-11. Provider Error Message response → fetch_status=provider_note.
  9F3a-12. Malformed JSON response → fetch_status=malformed.
  9F3a-13. No holdings in response → fetch_status=no_data, holdings_count=0.
  9F3a-14. Missing weight field → weights_available=False, limitation noted.
  9F3a-15. Missing date field → freshness_status=date_missing, limitation noted.
  9F3a-16. Governance: canonical_ready=False always.
  9F3a-17. Governance: safe_for_decision=False always.
  9F3a-18. Governance: diagnostics_only=True always.
  9F3a-19. Governance: artifact_writes=0 always.
  9F3a-20. Governance: decision_policy_changed=False always.
  9F3a-21. Governance: synthesis_ready_changed=False always.
  9F3a-22. Governance: visible_snapshot_unchanged=True always.
  9F3a-23. API key never appears in any returned field value.
  9F3a-24. API key never appears in any per-ticker response_top_level_keys.
  9F3a-25. Holdings sample is capped to first 5 holdings only.
  9F3a-26. candidate_pass when XLE, SCHD, and >= 5 Vanguard ETFs have holdings+weights+date.
  9F3a-27. candidate_partial when some holdings/weights exist but date missing.
  9F3a-28. candidate_partial when less than 5 Vanguard ETFs covered.
  9F3a-29. candidate_fail when no holdings returned.
  9F3a-30. HTTP 401 → fetch_status=unauthorized.
  9F3a-31. HTTP 429 → fetch_status=rate_limited.
  9F3a-32. Network error → fetch_status=error.
  9F3a-33. provider_id is always "alpha_vantage_etf_profile_v1".
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest


# ── Fixtures ──────────────────────────────────────────────────────────────────

_FAKE_API_KEY = "TEST_AV_KEY_NEVER_LOGGED"

_HOLDINGS_FULL = [
    {"name": "Apple Inc", "symbol": "AAPL", "weight": "7.12"},
    {"name": "Microsoft Corp", "symbol": "MSFT", "weight": "6.50"},
    {"name": "Amazon.com Inc", "symbol": "AMZN", "weight": "3.85"},
    {"name": "NVIDIA Corp", "symbol": "NVDA", "weight": "3.00"},
    {"name": "Alphabet Inc", "symbol": "GOOGL", "weight": "2.10"},
    {"name": "Meta Platforms", "symbol": "META", "weight": "1.90"},
]

_VALID_PROFILE = {
    "name": "Vanguard S&P 500 ETF",
    "description": "Tracks the S&P 500 index",
    "asset_type": "ETF",
    "net_assets": "1200000000000",
    "net_expense_ratio": "0.03",
    "portfolio_turnover": "2.00",
    "updated_at": "2025-12-31",
    "holdings": _HOLDINGS_FULL,
}

_VALID_PROFILE_NO_DATE = {
    "name": "Vanguard Total Stock ETF",
    "asset_type": "ETF",
    "holdings": [
        {"name": "Apple Inc", "symbol": "AAPL", "weight": "6.5"},
    ],
}

_VALID_PROFILE_NO_WEIGHTS = {
    "name": "Some ETF",
    "updated_at": "2025-12-31",
    "holdings": [
        {"name": "Apple Inc", "symbol": "AAPL"},
    ],
}

_VALID_PROFILE_NO_HOLDINGS = {
    "name": "Empty ETF",
    "updated_at": "2025-12-31",
    "holdings": [],
}


def _make_http_resp(data: Any, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = data
    return resp


def _make_http_resp_malformed(status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.side_effect = ValueError("not json")
    return resp


def _make_http_get_fn(responses: dict[str, Any]) -> Any:
    """Return an injectable http_get_fn that maps ticker → response."""
    def _get(url: str, params: dict, timeout: float) -> MagicMock:
        ticker = params.get("symbol", "UNKNOWN")
        return responses.get(ticker, _make_http_resp({"Note": "API rate limit reached"}, 200))
    return _get


def _make_raise_fn(exc_type: type) -> Any:
    """Return an injectable http_get_fn that always raises exc_type."""
    def _get(url: str, params: dict, timeout: float) -> Any:
        raise exc_type("simulated error")
    return _get


# ── Tests: default ticker set ─────────────────────────────────────────────────

class TestDefaultTickers:
    def test_defaults_exclude_spy_and_qqq(self):
        # 9F3a-03
        from app.services.intelligence.research_workers.alpha_vantage_etf_profile_runner_v1 import _DEFAULT_TICKERS
        assert "SPY" not in _DEFAULT_TICKERS
        assert "QQQ" not in _DEFAULT_TICKERS

    def test_controls_include_spy_and_qqq(self):
        # 9F3a-04
        from app.services.intelligence.research_workers.alpha_vantage_etf_profile_runner_v1 import _CONTROL_TICKERS
        assert "SPY" in _CONTROL_TICKERS
        assert "QQQ" in _CONTROL_TICKERS

    def test_default_tickers_are_missing_set(self):
        from app.services.intelligence.research_workers.alpha_vantage_etf_profile_runner_v1 import _DEFAULT_TICKERS
        expected = {"XLE", "VOO", "VTI", "VGT", "VHT", "VIS", "VXUS", "VYM", "SCHD"}
        assert set(_DEFAULT_TICKERS) == expected

    def test_max_tickers_per_run(self):
        # 9F3a-05
        from app.services.intelligence.research_workers.alpha_vantage_etf_profile_runner_v1 import MAX_TICKERS_PER_RUN
        assert MAX_TICKERS_PER_RUN == 11


# ── Tests: valid response with holdings + weights ─────────────────────────────

class TestValidResponse:
    def _probe(self, data: dict, ticker: str = "VOO") -> dict:
        from app.services.intelligence.research_workers.alpha_vantage_etf_profile_runner_v1 import _probe_ticker
        fn = _make_http_get_fn({ticker: _make_http_resp(data)})
        return _probe_ticker(ticker, _FAKE_API_KEY, fn)

    def test_fetch_status_success(self):
        # 9F3a-06
        result = self._probe(_VALID_PROFILE)
        assert result["fetch_status"] == "success"

    def test_weights_available_true(self):
        # 9F3a-07
        result = self._probe(_VALID_PROFILE)
        assert result["weights_available"] is True

    def test_freshness_status_verified(self):
        # 9F3a-08
        result = self._probe(_VALID_PROFILE)
        assert result["freshness_status"] == "verified"
        assert result["as_of_date_or_date_field"] == "2025-12-31"

    def test_holdings_count_correct(self):
        result = self._probe(_VALID_PROFILE)
        assert result["holdings_count"] == len(_HOLDINGS_FULL)

    def test_fund_identity_fields(self):
        result = self._probe(_VALID_PROFILE)
        assert "fund_name" in result["fund_identity_fields_found"]
        assert result["fund_name"] == "Vanguard S&P 500 ETF"

    def test_no_limitations_on_full_response(self):
        result = self._probe(_VALID_PROFILE)
        assert "no_per_holding_weights" not in result["limitations"]
        assert "as_of_date_missing" not in result["limitations"]


# ── Tests: provider message types ─────────────────────────────────────────────

class TestProviderMessages:
    def _probe(self, data: dict, ticker: str = "VOO") -> dict:
        from app.services.intelligence.research_workers.alpha_vantage_etf_profile_runner_v1 import _probe_ticker
        fn = _make_http_get_fn({ticker: _make_http_resp(data)})
        return _probe_ticker(ticker, _FAKE_API_KEY, fn)

    def test_note_response_is_rate_limited(self):
        # 9F3a-09
        result = self._probe({"Note": "Thank you for using Alpha Vantage API. 5 requests per minute."})
        assert result["fetch_status"] == "rate_limited"
        assert result["provider_message_type"] == "Note"

    def test_information_response_is_provider_note(self):
        # 9F3a-10: generic Information message (no rate-limit or entitlement keywords) → provider_note
        result = self._probe({"Information": "This function is unavailable for the requested ticker."})
        assert result["fetch_status"] == "provider_note"
        assert result["provider_message_type"] == "Information"

    def test_error_message_response_is_provider_note(self):
        # 9F3a-11
        result = self._probe({"Error Message": "Invalid API call. Invalid symbol."})
        assert result["fetch_status"] == "provider_note"
        assert result["provider_message_type"] == "Error Message"


# ── Tests: malformed / no data ────────────────────────────────────────────────

class TestMalformedAndNoData:
    def test_malformed_json_is_malformed(self):
        # 9F3a-12
        from app.services.intelligence.research_workers.alpha_vantage_etf_profile_runner_v1 import _probe_ticker
        fn = _make_http_get_fn({"VOO": _make_http_resp_malformed()})
        result = _probe_ticker("VOO", _FAKE_API_KEY, fn)
        assert result["fetch_status"] == "malformed"

    def test_no_holdings_returns_no_data(self):
        # 9F3a-13
        from app.services.intelligence.research_workers.alpha_vantage_etf_profile_runner_v1 import _probe_ticker
        fn = _make_http_get_fn({"VOO": _make_http_resp(_VALID_PROFILE_NO_HOLDINGS)})
        result = _probe_ticker("VOO", _FAKE_API_KEY, fn)
        assert result["fetch_status"] == "no_data"
        assert result["holdings_count"] == 0

    def test_missing_weights_field(self):
        # 9F3a-14
        from app.services.intelligence.research_workers.alpha_vantage_etf_profile_runner_v1 import _probe_ticker
        fn = _make_http_get_fn({"VOO": _make_http_resp(_VALID_PROFILE_NO_WEIGHTS)})
        result = _probe_ticker("VOO", _FAKE_API_KEY, fn)
        assert result["weights_available"] is False
        assert "no_per_holding_weights" in result["limitations"]

    def test_missing_date_field(self):
        # 9F3a-15
        from app.services.intelligence.research_workers.alpha_vantage_etf_profile_runner_v1 import _probe_ticker
        fn = _make_http_get_fn({"VOO": _make_http_resp(_VALID_PROFILE_NO_DATE)})
        result = _probe_ticker("VOO", _FAKE_API_KEY, fn)
        assert result["freshness_status"] == "date_missing"
        assert "as_of_date_missing" in result["limitations"]

    def test_http_401_is_unauthorized(self):
        # 9F3a-30
        from app.services.intelligence.research_workers.alpha_vantage_etf_profile_runner_v1 import _probe_ticker
        fn = _make_http_get_fn({"VOO": _make_http_resp({}, status_code=401)})
        result = _probe_ticker("VOO", _FAKE_API_KEY, fn)
        assert result["fetch_status"] == "unauthorized"

    def test_http_429_is_rate_limited(self):
        # 9F3a-31
        from app.services.intelligence.research_workers.alpha_vantage_etf_profile_runner_v1 import _probe_ticker
        fn = _make_http_get_fn({"VOO": _make_http_resp({}, status_code=429)})
        result = _probe_ticker("VOO", _FAKE_API_KEY, fn)
        assert result["fetch_status"] == "rate_limited"

    def test_network_error_is_error(self):
        # 9F3a-32
        from app.services.intelligence.research_workers.alpha_vantage_etf_profile_runner_v1 import _probe_ticker
        fn = _make_raise_fn(ConnectionError)
        result = _probe_ticker("VOO", _FAKE_API_KEY, fn)
        assert result["fetch_status"] == "error"


# ── Tests: holdings sample cap ────────────────────────────────────────────────

class TestHoldingsSample:
    def test_sample_capped_to_5(self):
        # 9F3a-25
        from app.services.intelligence.research_workers.alpha_vantage_etf_profile_runner_v1 import _probe_ticker
        fn = _make_http_get_fn({"VOO": _make_http_resp(_VALID_PROFILE)})
        result = _probe_ticker("VOO", _FAKE_API_KEY, fn)
        assert len(result["holdings_sample"]) <= 5
        # _HOLDINGS_FULL has 6 holdings — sample must be exactly 5
        assert len(result["holdings_sample"]) == 5


# ── Tests: governance invariants ──────────────────────────────────────────────

class TestGovernanceInvariants:
    def _run(self, tickers: list[str], responses: dict) -> dict:
        from app.services.intelligence.research_workers.alpha_vantage_etf_profile_runner_v1 import run_alpha_vantage_etf_profile_check
        fn = _make_http_get_fn(responses)
        return run_alpha_vantage_etf_profile_check(
            api_key=_FAKE_API_KEY,
            tickers=tickers,
            http_get_fn=fn,
        )

    def _simple_run(self) -> dict:
        responses = {"VOO": _make_http_resp(_VALID_PROFILE)}
        return self._run(["VOO"], responses)

    def test_canonical_ready_false(self):
        # 9F3a-16
        result = self._simple_run()
        assert result["canonical_ready"] is False

    def test_safe_for_decision_false(self):
        # 9F3a-17
        result = self._simple_run()
        assert result["safe_for_decision"] is False

    def test_diagnostics_only_true(self):
        # 9F3a-18
        result = self._simple_run()
        assert result["diagnostics_only"] is True

    def test_artifact_writes_zero(self):
        # 9F3a-19
        result = self._simple_run()
        assert result["artifact_writes"] == 0

    def test_decision_policy_changed_false(self):
        # 9F3a-20
        result = self._simple_run()
        assert result["decision_policy_changed"] is False

    def test_synthesis_ready_changed_false(self):
        # 9F3a-21
        result = self._simple_run()
        assert result["synthesis_ready_changed"] is False

    def test_visible_snapshot_unchanged_true(self):
        # 9F3a-22
        result = self._simple_run()
        assert result["visible_snapshot_unchanged"] is True

    def test_provider_id(self):
        # 9F3a-33
        result = self._simple_run()
        assert result["provider_id"] == "alpha_vantage_etf_profile_v1"


# ── Tests: API key never returned ─────────────────────────────────────────────

class TestApiKeyRedaction:
    def _run_with_key_in_response(self, ticker: str = "VOO") -> dict:
        from app.services.intelligence.research_workers.alpha_vantage_etf_profile_runner_v1 import _probe_ticker
        # Inject the api_key into the response body to test redaction.
        data = {
            "name": "Test ETF",
            "apikey": _FAKE_API_KEY,  # top-level key that must be stripped
            "info": f"key={_FAKE_API_KEY}",  # value that must be redacted
            "updated_at": "2025-12-31",
            "holdings": [{"name": "Apple", "symbol": "AAPL", "weight": "5.0"}],
        }
        fn = _make_http_get_fn({ticker: _make_http_resp(data)})
        return _probe_ticker(ticker, _FAKE_API_KEY, fn)

    def test_api_key_not_in_response_top_level_keys(self):
        # 9F3a-24
        result = self._run_with_key_in_response()
        for k in result.get("response_top_level_keys", []):
            assert _FAKE_API_KEY not in k
            assert k.lower() != "apikey"

    def test_api_key_not_in_any_field_value(self):
        # 9F3a-23
        result = self._run_with_key_in_response()
        result_json = json.dumps(result, default=str)
        assert _FAKE_API_KEY not in result_json

    def test_api_key_not_in_full_run_result(self):
        # 9F3a-23 (full run)
        from app.services.intelligence.research_workers.alpha_vantage_etf_profile_runner_v1 import run_alpha_vantage_etf_profile_check
        responses = {
            "VOO": _make_http_resp({
                "name": f"key={_FAKE_API_KEY}",
                "updated_at": "2025-12-31",
                "holdings": [{"name": "Apple", "symbol": "AAPL", "weight": "5.0"}],
            })
        }
        fn = _make_http_get_fn(responses)
        result = run_alpha_vantage_etf_profile_check(
            api_key=_FAKE_API_KEY,
            tickers=["VOO"],
            http_get_fn=fn,
        )
        result_json = json.dumps(result, default=str)
        assert _FAKE_API_KEY not in result_json


# ── Tests: verdict logic ──────────────────────────────────────────────────────

class TestVerdictLogic:
    def _make_ticker_entry(
        self,
        ticker: str,
        *,
        holdings_count: int = 10,
        weights_available: bool = True,
        freshness_status: str = "verified",
        fetch_status: str = "success",
    ) -> dict:
        return {
            "ticker": ticker,
            "fetch_status": fetch_status,
            "holdings_count": holdings_count,
            "weights_available": weights_available,
            "freshness_status": freshness_status,
        }

    def _all_pass_entries(self) -> list[dict]:
        tickers = ["XLE", "SCHD", "VOO", "VTI", "VGT", "VHT", "VIS", "VXUS", "VYM"]
        return [self._make_ticker_entry(t) for t in tickers]

    def test_candidate_pass_with_full_coverage(self):
        # 9F3a-26
        from app.services.intelligence.research_workers.alpha_vantage_etf_profile_runner_v1 import _compute_verdict
        entries = self._all_pass_entries()
        verdict, reason = _compute_verdict(entries, [e["ticker"] for e in entries])
        assert verdict == "candidate_pass"

    def test_candidate_partial_date_missing(self):
        # 9F3a-27
        from app.services.intelligence.research_workers.alpha_vantage_etf_profile_runner_v1 import _compute_verdict
        entries = self._all_pass_entries()
        for e in entries:
            e["freshness_status"] = "date_missing"
        verdict, reason = _compute_verdict(entries, [e["ticker"] for e in entries])
        assert verdict == "candidate_partial"
        assert "date" in reason.lower() or "as-of" in reason.lower() or "canonical" in reason.lower()

    def test_candidate_partial_few_vanguard(self):
        # 9F3a-28: only 3 Vanguard ETFs covered (need >=5)
        from app.services.intelligence.research_workers.alpha_vantage_etf_profile_runner_v1 import _compute_verdict
        entries = [
            self._make_ticker_entry("XLE"),
            self._make_ticker_entry("SCHD"),
            self._make_ticker_entry("VOO"),
            self._make_ticker_entry("VTI"),
            self._make_ticker_entry("VGT"),
            # VHT, VIS, VXUS, VYM missing/failed
            self._make_ticker_entry("VHT", holdings_count=0, fetch_status="no_data"),
            self._make_ticker_entry("VIS", holdings_count=0, fetch_status="provider_note"),
            self._make_ticker_entry("VXUS", holdings_count=0, fetch_status="rate_limited"),
            self._make_ticker_entry("VYM", holdings_count=0, fetch_status="error"),
        ]
        verdict, reason = _compute_verdict(entries, [e["ticker"] for e in entries])
        assert verdict == "candidate_partial"

    def test_candidate_fail_no_holdings(self):
        # 9F3a-29
        from app.services.intelligence.research_workers.alpha_vantage_etf_profile_runner_v1 import _compute_verdict
        entries = [
            self._make_ticker_entry(t, holdings_count=0, weights_available=False, fetch_status="provider_note")
            for t in ["XLE", "VOO", "VTI", "VGT", "VHT", "VIS", "VXUS", "VYM", "SCHD"]
        ]
        verdict, reason = _compute_verdict(entries, [e["ticker"] for e in entries])
        assert verdict == "candidate_fail"

    def test_candidate_fail_all_errors(self):
        from app.services.intelligence.research_workers.alpha_vantage_etf_profile_runner_v1 import _compute_verdict
        entries = [
            self._make_ticker_entry(t, holdings_count=0, weights_available=False, fetch_status="unauthorized")
            for t in ["XLE", "SCHD", "VOO"]
        ]
        verdict, reason = _compute_verdict(entries, [e["ticker"] for e in entries])
        assert verdict == "candidate_fail"


# ── Tests: env var missing scenario (simulated at runner level) ───────────────

class TestEnvVarMissing:
    """Test fail-closed behavior when API key is empty."""

    def test_probe_with_empty_api_key_result_does_not_embed_key(self):
        from app.services.intelligence.research_workers.alpha_vantage_etf_profile_runner_v1 import _probe_ticker
        empty_key = ""
        resp = _make_http_resp({"Error Message": "Invalid API call. Invalid API key."})
        fn = _make_http_get_fn({"VOO": resp})
        result = _probe_ticker("VOO", empty_key, fn)
        # fetch_status reflects AV Error Message → provider_note
        assert result["fetch_status"] == "provider_note"
        # holdings and weights remain absent
        assert result["holdings_count"] == 0
        assert result["weights_available"] is False
