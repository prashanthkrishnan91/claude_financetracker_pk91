"""Stage 9F.3b — Alpha Vantage diagnostic result clarity tests.

Fixture-based only — no live HTTP calls in CI.

Coverage:
  9F3b-01. Information daily-limit message → fetch_status=rate_limited.
  9F3b-02. Information premium/entitlement message → fetch_status=entitlement_or_premium_required.
  9F3b-03. Generic Information (no rate-limit or entitlement keywords) → fetch_status=provider_note.
  9F3b-04. Error Message with rate-limit text → fetch_status=rate_limited.
  9F3b-05. Error Message with no special keywords → fetch_status=provider_note.
  9F3b-06. provider_message_snippet is returned in the per-ticker result.
  9F3b-07. provider_message_snippet is capped to 200 characters.
  9F3b-08. provider_message_snippet redacts the API key.
  9F3b-09. API key cannot appear in any field of the full run result JSON.
  9F3b-10. Note message still → fetch_status=rate_limited (regression guard).
  9F3b-11. provider_message_snippet is None when the response is a valid data response.
  9F3b-12. Requests per minute text in Information → rate_limited.
  9F3b-13. Subscription keyword in Information → entitlement_or_premium_required.
  9F3b-14. _classify_information_message unit: rate_limit keywords.
  9F3b-15. _classify_information_message unit: entitlement keywords.
  9F3b-16. _classify_information_message unit: generic → provider_note.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest


_FAKE_API_KEY = "TEST_AV_KEY_NEVER_LOGGED_9F3B"

_VALID_HOLDINGS_PROFILE = {
    "name": "Test ETF",
    "updated_at": "2025-12-31",
    "holdings": [{"name": "Apple Inc", "symbol": "AAPL", "weight": "5.0"}],
}


def _make_http_resp(data, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = data
    return resp


def _make_http_get_fn(responses: dict) -> object:
    def _get(url: str, params: dict, timeout: float) -> MagicMock:
        ticker = params.get("symbol", "UNKNOWN")
        return responses.get(ticker, _make_http_resp({"Note": "API rate limit"}, 200))
    return _get


def _probe(data: dict, ticker: str = "VOO") -> dict:
    from app.services.intelligence.research_workers.alpha_vantage_etf_profile_runner_v1 import _probe_ticker
    fn = _make_http_get_fn({ticker: _make_http_resp(data)})
    return _probe_ticker(ticker, _FAKE_API_KEY, fn)


# ── Tests: Information message sub-classification ─────────────────────────────

class TestInformationMessageClassification:
    def test_information_daily_limit_is_rate_limited(self):
        # 9F3b-01
        result = _probe({"Information": "You have reached the 25 requests per day limit."})
        assert result["fetch_status"] == "rate_limited"
        assert result["provider_message_type"] == "Information"

    def test_information_requests_per_minute_is_rate_limited(self):
        # 9F3b-12
        result = _probe({"Information": "Thank you for using Alpha Vantage! Our standard API call frequency is 5 calls per minute and 100 calls per day."})
        assert result["fetch_status"] == "rate_limited"

    def test_information_premium_endpoint_is_entitlement(self):
        # 9F3b-02
        result = _probe({"Information": "This is a premium endpoint. Please subscribe to a premium plan to access this data."})
        assert result["fetch_status"] == "entitlement_or_premium_required"
        assert result["provider_message_type"] == "Information"

    def test_information_subscription_keyword_is_entitlement(self):
        # 9F3b-13
        result = _probe({"Information": "A subscription is required to access ETF_PROFILE data."})
        assert result["fetch_status"] == "entitlement_or_premium_required"

    def test_information_mixed_standard_api_plus_premium_is_entitlement(self):
        # 9F3b-17: "standard API users" alone is not a rate-limit signal;
        # entitlement keyword ("premium subscription") must win.
        result = _probe({"Information": "This endpoint is not available for standard API users. Please upgrade to a premium subscription."})
        assert result["fetch_status"] == "entitlement_or_premium_required"
        assert result["provider_message_type"] == "Information"

    def test_information_generic_is_provider_note(self):
        # 9F3b-03
        result = _probe({"Information": "This function is unavailable for the requested ticker."})
        assert result["fetch_status"] == "provider_note"
        assert result["provider_message_type"] == "Information"


# ── Tests: Error Message sub-classification ───────────────────────────────────

class TestErrorMessageClassification:
    def test_error_message_with_rate_limit_text_is_rate_limited(self):
        # 9F3b-04
        result = _probe({"Error Message": "API rate limit exceeded. Please wait before retrying."})
        assert result["fetch_status"] == "rate_limited"

    def test_error_message_bad_symbol_is_provider_note(self):
        # 9F3b-05
        result = _probe({"Error Message": "Invalid API call. Invalid symbol."})
        assert result["fetch_status"] == "provider_note"
        assert result["provider_message_type"] == "Error Message"


# ── Tests: provider_message_snippet ──────────────────────────────────────────

class TestProviderMessageSnippet:
    def test_snippet_is_returned_in_result(self):
        # 9F3b-06
        result = _probe({"Information": "You have reached the 25 requests per day limit."})
        assert "provider_message_snippet" in result
        assert result["provider_message_snippet"] is not None
        assert len(result["provider_message_snippet"]) > 0

    def test_snippet_is_capped_to_200_chars(self):
        # 9F3b-07
        long_msg = "X" * 500
        result = _probe({"Information": long_msg})
        assert result["provider_message_snippet"] is not None
        assert len(result["provider_message_snippet"]) <= 200

    def test_snippet_redacts_api_key(self):
        # 9F3b-08: snippet must not contain the API key even if AV echoes it
        msg_with_key = f"Invalid key: {_FAKE_API_KEY} is not valid."
        result = _probe({"Information": msg_with_key})
        snippet = result["provider_message_snippet"]
        assert _FAKE_API_KEY not in snippet
        assert "[REDACTED]" in snippet

    def test_snippet_is_none_for_valid_data_response(self):
        # 9F3b-11
        result = _probe(_VALID_HOLDINGS_PROFILE)
        assert result["provider_message_snippet"] is None


# ── Tests: Note regression guard ─────────────────────────────────────────────

class TestNoteRegressionGuard:
    def test_note_still_rate_limited(self):
        # 9F3b-10
        result = _probe({"Note": "Thank you for using Alpha Vantage! Our standard API call frequency is 5 requests per minute."})
        assert result["fetch_status"] == "rate_limited"
        assert result["provider_message_type"] == "Note"


# ── Tests: API key never in full run result ───────────────────────────────────

class TestApiKeyNeverInRunResult:
    def test_api_key_not_in_full_run_json(self):
        # 9F3b-09
        from app.services.intelligence.research_workers.alpha_vantage_etf_profile_runner_v1 import run_alpha_vantage_etf_profile_check
        # Include a message that echoes something resembling the key pattern
        responses = {
            "VOO": _make_http_resp({"Information": f"key={_FAKE_API_KEY} is not valid."})
        }
        fn = _make_http_get_fn(responses)
        result = run_alpha_vantage_etf_profile_check(
            api_key=_FAKE_API_KEY,
            tickers=["VOO"],
            http_get_fn=fn,
        )
        result_json = json.dumps(result, default=str)
        assert _FAKE_API_KEY not in result_json


# ── Unit tests: _classify_information_message ─────────────────────────────────

class TestClassifyInformationMessage:
    def _classify(self, text: str) -> str:
        from app.services.intelligence.research_workers.alpha_vantage_etf_profile_runner_v1 import _classify_information_message
        return _classify_information_message(text)

    def test_rate_limit_keywords(self):
        # 9F3b-14
        assert self._classify("You have reached the daily limit for requests.") == "rate_limited"
        assert self._classify("API call frequency is 5 calls per minute.") == "rate_limited"
        assert self._classify("standard api usage limit exceeded") == "rate_limited"
        assert self._classify("25 requests per day limit reached") == "rate_limited"

    def test_entitlement_keywords(self):
        # 9F3b-15
        assert self._classify("This is a premium endpoint.") == "entitlement_or_premium_required"
        assert self._classify("Please upgrade your subscription.") == "entitlement_or_premium_required"
        assert self._classify("Entitlement check failed for this endpoint.") == "entitlement_or_premium_required"

    def test_mixed_standard_api_plus_entitlement_returns_entitlement(self):
        # 9F3b-18: "standard API users" is not a rate-limit keyword;
        # entitlement keyword wins when both appear in the same message.
        result = self._classify(
            "This endpoint is not available for standard API users. "
            "Please upgrade to a premium subscription."
        )
        assert result == "entitlement_or_premium_required"

    def test_standard_api_alone_is_provider_note(self):
        # 9F3b-19: "standard API" without quota/frequency wording → provider_note,
        # not rate_limited — avoids misclassifying entitlement messages.
        assert self._classify("Not available for standard API users.") == "provider_note"

    def test_generic_is_provider_note(self):
        # 9F3b-16
        assert self._classify("This function is unavailable for this ticker.") == "provider_note"
        assert self._classify("Unknown error occurred.") == "provider_note"
        assert self._classify("") == "provider_note"
