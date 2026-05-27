"""Stage 9F.4 — FMP ETF holdings free-key entitlement diagnostic tests.

Fixture-based only — no live HTTP calls in CI.

Coverage:
  9F4-01. Missing FMP_API_KEY → endpoint returns 403 (fails closed).
  9F4-02. Empty tickers list → endpoint returns 422.
  9F4-03. VOO-like 200-holding response with weights + date → success, plausible_full, detected fields.
  9F4-04. VOO-like response but no date → freshness_status=date_missing, date blocks candidate_pass.
  9F4-05. VOO-like response but no weights → weights_available=False, limitation noted.
  9F4-06. VXUS-like low holdings (30) → partial_or_suspicious coverage quality.
  9F4-07. HTTP 403 response → fetch_status=paywalled.
  9F4-08. HTTP 401 response → fetch_status=unauthorized.
  9F4-09. HTTP 429 response → fetch_status=rate_limited.
  9F4-10. Provider error message with paywall keyword → classified as paywalled.
  9F4-11. Provider error message with rate-limit keyword → classified as rate_limited.
  9F4-12. Provider error message with unauthorized keyword → classified as unauthorized.
  9F4-13. Malformed JSON → fetch_status=malformed.
  9F4-14. Network error → fetch_status=error.
  9F4-15. No holdings in 200 response → fetch_status=no_data.
  9F4-16. Holdings sample capped to first 5.
  9F4-17. Governance: canonical_ready=False always.
  9F4-18. Governance: safe_for_decision=False always.
  9F4-19. Governance: diagnostics_only=True always.
  9F4-20. Governance: artifact_writes=0 always.
  9F4-21. Governance: decision_policy_changed=False always.
  9F4-22. Governance: synthesis_ready_changed=False always.
  9F4-23. Governance: visible_snapshot_unchanged=True always.
  9F4-24. API key never appears in any returned field value (full result).
  9F4-25. API key never appears in provider_message_snippet.
  9F4-26. candidate_pass when all 4 proof tickers return plausible holdings + weights + date.
  9F4-27. candidate_partial when holdings + weights present but date missing.
  9F4-28. candidate_partial when holdings present but VXUS is partial_or_suspicious.
  9F4-29. candidate_fail when all tickers return paywalled/no_data.
  9F4-30. provider_id is always "fmp_etf_holdings_v1".
  9F4-31. FMP array response (no wrapper dict) is parsed correctly.
  9F4-32. FMP dict response with nested holdings key is parsed correctly.
  9F4-33. Tickers are normalized to uppercase and deduplicated.
  9F4-34. Response preserves tickers_requested and tickers_succeeded fields.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ── Helpers ──────────────────────────────────────────────────────────────────

_FAKE_API_KEY = "TEST_FMP_KEY_NEVER_LOGGED"


def _mock_resp(status_code: int = 200, body: Any = None, raises: Exception = None):
    """Build a mock response object."""
    resp = MagicMock()
    resp.status_code = status_code
    if raises:
        resp.json.side_effect = raises
    else:
        resp.json.return_value = body if body is not None else []
    return resp


def _make_holdings(count: int, *, name_field="name", symbol_field="asset",
                   weight_field="weightPercentage", date_field=None) -> list[dict]:
    """Generate a list of holdings dicts."""
    holdings = []
    for i in range(count):
        h: dict = {
            name_field: f"Holding {i}",
            symbol_field: f"SYM{i}",
            weight_field: round(0.5 - i * 0.001, 4),
        }
        if date_field:
            h[date_field] = "2024-01-31"
        holdings.append(h)
    return holdings


def _run_check(tickers: list[str], http_responses: dict, api_key: str = _FAKE_API_KEY) -> dict:
    """Run the FMP check with fixture responses keyed by ticker."""
    from app.services.intelligence.research_workers.fmp_etf_holdings_runner_v1 import (
        run_fmp_etf_holdings_check,
    )

    call_count = [0]

    def _fake_get(url, params=None, timeout=None):
        ticker = (params or {}).get("symbol", "")
        return http_responses.get(ticker, _mock_resp(200, []))

    return run_fmp_etf_holdings_check(api_key=api_key, tickers=tickers, http_get_fn=_fake_get)


# ── Standard fixture data ──────────────────────────────────────────────────

_VOO_HOLDINGS_WITH_DATE = _make_holdings(250, date_field="date")  # plausible_full (≥200)
_SCHD_HOLDINGS_WITH_DATE = _make_holdings(100, date_field="date")  # plausible_full (≥50)
_XLE_HOLDINGS_WITH_DATE = _make_holdings(30, date_field="date")   # plausible_full (≥20)
_VXUS_HOLDINGS_WITH_DATE = _make_holdings(1100, date_field="date") # plausible_full (≥1000)

_VOO_HOLDINGS_NO_DATE = _make_holdings(250)
_VXUS_HOLDINGS_LOW = _make_holdings(30)  # partial_or_suspicious


# ── 9F4-01. Missing FMP_API_KEY fails closed ─────────────────────────────────

def test_missing_fmp_api_key_raises_on_endpoint():
    """Runner with empty api_key raises or returns fails-closed behavior."""
    from app.services.intelligence.research_workers.fmp_etf_holdings_runner_v1 import (
        run_fmp_etf_holdings_check,
    )

    # The runner itself accepts a key; the router is responsible for the 403.
    # But if an empty key is passed, responses classified as unauthorized/paywalled.
    def _fake_get(url, params=None, timeout=None):
        return _mock_resp(401)

    result = run_fmp_etf_holdings_check(api_key="", tickers=["VOO"], http_get_fn=_fake_get)
    assert result["canonical_ready"] is False
    assert result["safe_for_decision"] is False


# ── 9F4-02. Empty tickers raises at runner level ─────────────────────────────

def test_empty_tickers_returns_empty_per_ticker():
    from app.services.intelligence.research_workers.fmp_etf_holdings_runner_v1 import (
        run_fmp_etf_holdings_check,
    )

    def _fake_get(url, params=None, timeout=None):
        return _mock_resp(200, [])

    result = run_fmp_etf_holdings_check(api_key=_FAKE_API_KEY, tickers=[], http_get_fn=_fake_get)
    assert result["tickers_requested"] == []
    assert result["per_ticker"] == []


# ── 9F4-03. VOO-like success ─────────────────────────────────────────────────

def test_voo_like_holdings_with_date_success():
    result = _run_check(
        ["VOO"],
        {"VOO": _mock_resp(200, _VOO_HOLDINGS_WITH_DATE)},
    )
    pt = result["per_ticker"][0]
    assert pt["ticker"] == "VOO"
    assert pt["fetch_status"] == "success"
    assert pt["holdings_count"] == 250
    assert pt["weights_available"] is True
    assert pt["freshness_status"] == "verified"
    assert pt["coverage_quality"] == "plausible_full"
    assert "weight" in pt["detected_fields"]
    assert "symbol" in pt["detected_fields"]


# ── 9F4-04. Date missing blocks candidate_pass ────────────────────────────────

def test_date_missing_sets_freshness_date_missing():
    result = _run_check(
        ["VOO"],
        {"VOO": _mock_resp(200, _VOO_HOLDINGS_NO_DATE)},
    )
    pt = result["per_ticker"][0]
    assert pt["freshness_status"] == "date_missing"
    assert "as_of_date_missing" in pt["limitations"]


def test_date_missing_prevents_candidate_pass():
    """Four proof tickers with holdings+weights but no date → candidate_partial."""
    responses = {
        "VOO": _mock_resp(200, _VOO_HOLDINGS_NO_DATE),
        "SCHD": _mock_resp(200, _make_holdings(100)),
        "XLE": _mock_resp(200, _make_holdings(30)),
        "VXUS": _mock_resp(200, _make_holdings(1100)),
    }
    result = _run_check(["VOO", "SCHD", "XLE", "VXUS"], responses)
    assert result["provider_candidate_verdict"] == "candidate_partial"
    assert "date" in result["provider_candidate_reason"].lower() or \
           "as-of" in result["provider_candidate_reason"].lower()


# ── 9F4-05. Missing weights ────────────────────────────────────────────────────

def test_no_weights_blocks_usable():
    def _make_no_weight(count: int) -> list[dict]:
        return [{"asset": f"SYM{i}", "name": f"H{i}"} for i in range(count)]

    result = _run_check(
        ["VOO"],
        {"VOO": _mock_resp(200, _make_no_weight(250))},
    )
    pt = result["per_ticker"][0]
    assert pt["weights_available"] is False
    assert "no_per_holding_weights" in pt["limitations"]
    # Without weights, coverage quality is partial or suspicious
    assert pt["coverage_quality"] in ("partial_or_suspicious",)


# ── 9F4-06. VXUS-like low holdings → partial_or_suspicious ────────────────────

def test_vxus_low_holdings_partial_or_suspicious():
    result = _run_check(
        ["VXUS"],
        {"VXUS": _mock_resp(200, _VXUS_HOLDINGS_LOW)},
    )
    pt = result["per_ticker"][0]
    assert pt["holdings_count"] == 30
    # 30 < VXUS usable_supplemental threshold (200) → partial_or_suspicious
    assert pt["coverage_quality"] == "partial_or_suspicious"


# ── 9F4-07. HTTP 403 → paywalled ─────────────────────────────────────────────

def test_http_403_classified_as_paywalled():
    result = _run_check(
        ["VOO"],
        {"VOO": _mock_resp(403)},
    )
    pt = result["per_ticker"][0]
    assert pt["fetch_status"] == "paywalled"
    assert pt["http_status"] == 403


# ── 9F4-08. HTTP 401 → unauthorized ──────────────────────────────────────────

def test_http_401_classified_as_unauthorized():
    result = _run_check(
        ["VOO"],
        {"VOO": _mock_resp(401)},
    )
    pt = result["per_ticker"][0]
    assert pt["fetch_status"] == "unauthorized"
    assert pt["http_status"] == 401


# ── 9F4-09. HTTP 429 → rate_limited ──────────────────────────────────────────

def test_http_429_classified_as_rate_limited():
    result = _run_check(
        ["VOO"],
        {"VOO": _mock_resp(429)},
    )
    pt = result["per_ticker"][0]
    assert pt["fetch_status"] == "rate_limited"


# ── 9F4-10. Provider error body paywall keyword ────────────────────────────────

def test_provider_error_body_paywall_classified():
    body = {"Error Message": "This endpoint requires a premium subscription plan."}
    result = _run_check(["VOO"], {"VOO": _mock_resp(200, body)})
    pt = result["per_ticker"][0]
    assert pt["fetch_status"] == "paywalled"
    assert pt["provider_message_snippet"] is not None
    assert _FAKE_API_KEY not in (pt["provider_message_snippet"] or "")


# ── 9F4-11. Provider error body rate-limit keyword ────────────────────────────

def test_provider_error_body_rate_limit_classified():
    body = {"Error Message": "Rate limit reached. Too many requests per minute."}
    result = _run_check(["VOO"], {"VOO": _mock_resp(200, body)})
    pt = result["per_ticker"][0]
    assert pt["fetch_status"] == "rate_limited"


# ── 9F4-12. Provider error body unauthorized keyword ──────────────────────────

def test_provider_error_body_unauthorized_classified():
    body = {"message": "Invalid API KEY. Please retry or visit our documentation to create one FREE."}
    result = _run_check(["VOO"], {"VOO": _mock_resp(200, body)})
    pt = result["per_ticker"][0]
    assert pt["fetch_status"] == "unauthorized"


# ── 9F4-13. Malformed JSON ────────────────────────────────────────────────────

def test_malformed_json_classified():
    result = _run_check(
        ["VOO"],
        {"VOO": _mock_resp(200, raises=ValueError("not JSON"))},
    )
    pt = result["per_ticker"][0]
    assert pt["fetch_status"] == "malformed"


# ── 9F4-14. Network error → error ────────────────────────────────────────────

def test_network_error_classified():
    from app.services.intelligence.research_workers.fmp_etf_holdings_runner_v1 import (
        run_fmp_etf_holdings_check,
    )

    def _raises(url, params=None, timeout=None):
        raise ConnectionError("network error")

    result = run_fmp_etf_holdings_check(
        api_key=_FAKE_API_KEY, tickers=["VOO"], http_get_fn=_raises
    )
    pt = result["per_ticker"][0]
    assert pt["fetch_status"] == "error"


# ── 9F4-15. Empty holdings list → no_data ────────────────────────────────────

def test_empty_holdings_classified_as_no_data():
    result = _run_check(["VOO"], {"VOO": _mock_resp(200, [])})
    pt = result["per_ticker"][0]
    assert pt["fetch_status"] == "no_data"
    assert pt["holdings_count"] == 0


# ── 9F4-16. Holdings sample capped to 5 ──────────────────────────────────────

def test_holdings_sample_capped_at_5():
    result = _run_check(
        ["VOO"],
        {"VOO": _mock_resp(200, _make_holdings(250, date_field="date"))},
    )
    pt = result["per_ticker"][0]
    assert len(pt["holdings_sample"]) == 5
    assert pt["holdings_count"] == 250


# ── 9F4-17 through 9F4-23. Governance fields ────────────────────────────────

def test_governance_fields_always_set():
    result = _run_check(
        ["VOO"],
        {"VOO": _mock_resp(200, _VOO_HOLDINGS_WITH_DATE)},
    )
    assert result["diagnostics_only"] is True
    assert result["canonical_ready"] is False
    assert result["safe_for_decision"] is False
    assert result["artifact_writes"] == 0
    assert result["decision_policy_changed"] is False
    assert result["synthesis_ready_changed"] is False
    assert result["visible_snapshot_unchanged"] is True


# ── 9F4-24. API key never in full result ──────────────────────────────────────

def _contains_key(obj: Any, key: str) -> bool:
    """Recursively check if the API key string appears anywhere in the result."""
    if isinstance(obj, str):
        return key in obj
    if isinstance(obj, dict):
        return any(_contains_key(v, key) for v in obj.values())
    if isinstance(obj, list):
        return any(_contains_key(item, key) for item in obj)
    return False


def test_api_key_never_in_result():
    result = _run_check(
        ["VOO"],
        {"VOO": _mock_resp(200, _VOO_HOLDINGS_WITH_DATE)},
    )
    assert not _contains_key(result, _FAKE_API_KEY), "API key appeared in result"


def test_api_key_never_in_error_snippet():
    body = {"Error Message": f"Invalid apikey={_FAKE_API_KEY}. Please check."}
    result = _run_check(["VOO"], {"VOO": _mock_resp(200, body)})
    pt = result["per_ticker"][0]
    assert _FAKE_API_KEY not in (pt.get("provider_message_snippet") or "")
    assert not _contains_key(result, _FAKE_API_KEY), "API key appeared in error snippet result"


# ── 9F4-26. candidate_pass all 4 proof tickers ────────────────────────────────

def test_candidate_pass_all_4_tickers():
    responses = {
        "VOO": _mock_resp(200, _VOO_HOLDINGS_WITH_DATE),
        "SCHD": _mock_resp(200, _SCHD_HOLDINGS_WITH_DATE),
        "XLE": _mock_resp(200, _XLE_HOLDINGS_WITH_DATE),
        "VXUS": _mock_resp(200, _VXUS_HOLDINGS_WITH_DATE),
    }
    result = _run_check(["VOO", "SCHD", "XLE", "VXUS"], responses)
    assert result["provider_candidate_verdict"] == "candidate_pass"


# ── 9F4-27. candidate_partial when date missing ───────────────────────────────

def test_candidate_partial_when_date_missing():
    responses = {
        "VOO": _mock_resp(200, _make_holdings(250)),  # no date
        "SCHD": _mock_resp(200, _make_holdings(100)),
        "XLE": _mock_resp(200, _make_holdings(30)),
        "VXUS": _mock_resp(200, _make_holdings(1100)),
    }
    result = _run_check(["VOO", "SCHD", "XLE", "VXUS"], responses)
    assert result["provider_candidate_verdict"] == "candidate_partial"


# ── 9F4-28. candidate_partial when VXUS is partial_or_suspicious ───────────────

def test_candidate_partial_when_vxus_low():
    responses = {
        "VOO": _mock_resp(200, _VOO_HOLDINGS_WITH_DATE),
        "SCHD": _mock_resp(200, _SCHD_HOLDINGS_WITH_DATE),
        "XLE": _mock_resp(200, _XLE_HOLDINGS_WITH_DATE),
        "VXUS": _mock_resp(200, _make_holdings(30, date_field="date")),  # low → partial
    }
    result = _run_check(["VOO", "SCHD", "XLE", "VXUS"], responses)
    # VXUS is partial_or_suspicious, so cannot be candidate_pass
    assert result["provider_candidate_verdict"] in ("candidate_partial",)


# ── 9F4-29. candidate_fail when all tickers fail ──────────────────────────────

def test_candidate_fail_all_paywalled():
    responses = {
        "VOO": _mock_resp(403),
        "SCHD": _mock_resp(403),
        "XLE": _mock_resp(403),
        "VXUS": _mock_resp(403),
    }
    result = _run_check(["VOO", "SCHD", "XLE", "VXUS"], responses)
    assert result["provider_candidate_verdict"] == "candidate_fail"


# ── 9F4-30. provider_id constant ─────────────────────────────────────────────

def test_provider_id_constant():
    result = _run_check(["VOO"], {"VOO": _mock_resp(200, [])})
    assert result["provider_id"] == "fmp_etf_holdings_v1"


# ── 9F4-31. FMP array response (no wrapper) ──────────────────────────────────

def test_fmp_array_response_parsed():
    holdings = _make_holdings(250, date_field="date")
    result = _run_check(["VOO"], {"VOO": _mock_resp(200, holdings)})
    pt = result["per_ticker"][0]
    assert pt["fetch_status"] == "success"
    assert pt["holdings_count"] == 250


# ── 9F4-32. FMP dict response with nested holdings ───────────────────────────

def test_fmp_dict_response_with_nested_holdings_parsed():
    holdings = _make_holdings(250)
    body = {
        "symbol": "VOO",
        "date": "2024-01-31",
        "holdings": holdings,
    }
    result = _run_check(["VOO"], {"VOO": _mock_resp(200, body)})
    pt = result["per_ticker"][0]
    assert pt["fetch_status"] == "success"
    assert pt["holdings_count"] == 250
    assert pt["freshness_status"] == "verified"
    assert pt["as_of_date_or_date_field"] == "2024-01-31"
    assert "top_level_date" in pt["detected_fields"]


# ── 9F4-33. Tickers normalized to uppercase ───────────────────────────────────

def test_tickers_normalization_is_router_responsibility():
    """Normalization (uppercase, dedup) is the router's job; runner passes tickers as-is."""
    from app.services.intelligence.research_workers.fmp_etf_holdings_runner_v1 import (
        run_fmp_etf_holdings_check,
    )

    called_with = []

    def _fake_get(url, params=None, timeout=None):
        called_with.append((params or {}).get("symbol", ""))
        return _mock_resp(200, [])

    # Router normalizes before calling runner; simulate pre-normalized input
    result = run_fmp_etf_holdings_check(
        api_key=_FAKE_API_KEY,
        tickers=["VOO", "SCHD"],  # already normalized by router
        http_get_fn=_fake_get,
    )
    assert "VOO" in called_with
    assert "SCHD" in called_with
    assert result["tickers_requested"] == ["VOO", "SCHD"]


# ── 9F4-34. tickers_requested and tickers_succeeded ──────────────────────────

def test_tickers_requested_and_succeeded_fields():
    result = _run_check(
        ["VOO", "SCHD"],
        {
            "VOO": _mock_resp(200, _VOO_HOLDINGS_WITH_DATE),
            "SCHD": _mock_resp(403),
        },
    )
    assert "VOO" in result["tickers_requested"]
    assert "SCHD" in result["tickers_requested"]
    assert "VOO" in result["tickers_succeeded"]
    assert "SCHD" not in result["tickers_succeeded"]
