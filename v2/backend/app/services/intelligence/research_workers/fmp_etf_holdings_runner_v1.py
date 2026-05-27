"""Stage 9F.4 — FMP ETF holdings free-key entitlement + shape diagnostic.

Diagnostic-only. Tests whether the FMP free API key can return ETF holdings
with per-holding weights and provider as-of/date metadata, before any canonical
adapter is built.

Hard constraints:
  - No artifact writes.
  - No SQL.
  - No decision, synthesis, Deploy, Watchtower, snapshot, or visible Intel change.
  - No LLM calls.
  - API key never logged or returned.
  - Fails closed if FMP_API_KEY is missing.
  - canonical_ready=False always.
  - safe_for_decision=False always.

Injectable:
  http_get_fn — replaces requests.get (tests inject fixture, no live HTTP in CI).
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

PROVIDER_ID = "fmp_etf_holdings_v1"

_FMP_ETF_HOLDINGS_URL = "https://financialmodelingprep.com/stable/etf/holdings"
_REQUEST_TIMEOUT_SECONDS: float = 15.0

# Per-ticker plausible_full thresholds (holdings count).
# Based on known fund composition sizes at proof time.
_PLAUSIBLE_FULL_THRESHOLDS: dict[str, int] = {
    "VOO": 200,    # large-cap domestic broad — S&P 500
    "SCHD": 50,    # dividend screen — ~100 holdings
    "XLE": 20,     # sector concentrated — dozens
    "VXUS": 1000,  # total international — thousands of positions
}
_DEFAULT_PLAUSIBLE_THRESHOLD: int = 10

# Minimum count for usable_supplemental (below plausible_full but non-trivial).
_USABLE_SUPPLEMENTAL_THRESHOLDS: dict[str, int] = {
    "VOO": 50,
    "SCHD": 20,
    "XLE": 10,
    "VXUS": 200,   # even "partial" VXUS must have meaningful depth
}
_DEFAULT_USABLE_SUPPLEMENTAL_THRESHOLD: int = 5

# Keywords used to classify FMP provider-level error responses.
_PAYWALL_KEYWORDS: frozenset = frozenset([
    "subscription", "premium", "plan", "upgrade", "not available",
    "your account", "access", "entitlement",
])
_RATE_LIMIT_KEYWORDS: frozenset = frozenset([
    "rate limit", "too many", "requests per", "limit reached", "daily limit",
])
_UNAUTHORIZED_KEYWORDS: frozenset = frozenset([
    "invalid api", "invalid key", "not authorized", "unauthorized", "apikey",
    "authentication",
])


def _redact_key(text: str, api_key: str) -> str:
    """Remove API key value from a string."""
    if api_key:
        text = text.replace(api_key, "[REDACTED]")
    # Also redact apikey= query param form.
    text = re.sub(r"apikey=[^&\s\"']+", "apikey=[REDACTED]", text)
    return text


def _safe_str(val: Any) -> Optional[str]:
    if val is None:
        return None
    s = str(val).strip()
    return s if s else None


def _classify_error_message(text: str) -> str:
    """Map a provider error message text to a fetch_status sub-class."""
    lower = text.lower()
    if any(k in lower for k in _UNAUTHORIZED_KEYWORDS):
        return "unauthorized"
    if any(k in lower for k in _PAYWALL_KEYWORDS):
        return "paywalled"
    if any(k in lower for k in _RATE_LIMIT_KEYWORDS):
        return "rate_limited"
    return "error"


def _extract_error_from_body(data: Any) -> Optional[str]:
    """Return error message text from an FMP error response body, or None."""
    if isinstance(data, dict):
        for key in ("Error Message", "message", "error", "detail"):
            val = data.get(key)
            if val and isinstance(val, str) and val.strip():
                return val.strip()
    return None


def _detect_holdings_in_response(
    data: Any,
) -> tuple[list[Any], Optional[str]]:
    """
    Extract holdings list and top-level date from FMP response.

    FMP /stable/etf/holdings may return:
      - A JSON array of holding objects directly.
      - A JSON object with a 'holdings' key and optional top-level 'date'.

    Returns (holdings_list, top_level_date_str).
    """
    if isinstance(data, list):
        return data, None
    if isinstance(data, dict):
        # Look for a holdings array nested inside.
        for key in ("holdings", "constituents", "etfHoldings"):
            if isinstance(data.get(key), list):
                top_date: Optional[str] = None
                for dk in ("date", "asOfDate", "reportDate", "updated", "updatedAt"):
                    v = data.get(dk)
                    if v and isinstance(v, str) and v.strip():
                        top_date = v.strip()
                        break
                return data[key], top_date
    return [], None


def _assess_coverage_quality(
    ticker: str,
    holdings_count: int,
    weights_available: bool,
) -> str:
    """Return coverage quality label for a given ticker and holdings result."""
    if holdings_count == 0:
        return "no_holdings"
    if not weights_available:
        return "partial_or_suspicious"
    plausible = _PLAUSIBLE_FULL_THRESHOLDS.get(ticker, _DEFAULT_PLAUSIBLE_THRESHOLD)
    usable_min = _USABLE_SUPPLEMENTAL_THRESHOLDS.get(ticker, _DEFAULT_USABLE_SUPPLEMENTAL_THRESHOLD)
    if holdings_count >= plausible:
        return "plausible_full"
    if holdings_count >= usable_min:
        return "usable_supplemental"
    return "partial_or_suspicious"


def _probe_ticker(
    ticker: str,
    api_key: str,
    http_get_fn: Callable,
) -> dict:
    """Fetch FMP ETF holdings for one ticker and return normalized per-ticker diagnostic."""
    params = {"symbol": ticker, "apikey": api_key}

    http_status: Optional[int] = None
    raw: Any = None
    fetch_status = "error"
    provider_message_snippet: Optional[str] = None

    try:
        resp = http_get_fn(_FMP_ETF_HOLDINGS_URL, params=params, timeout=_REQUEST_TIMEOUT_SECONDS)
        http_status = resp.status_code

        if http_status == 401:
            fetch_status = "unauthorized"
        elif http_status in (402, 403):
            # FMP uses 402 (Payment Required) and 403 for plan-gated endpoints.
            fetch_status = "paywalled"
            try:
                body = resp.json()
                err_text = _extract_error_from_body(body)
                if err_text:
                    provider_message_snippet = _redact_key(err_text[:200], api_key)
            except Exception:  # noqa: BLE001
                pass
        elif http_status == 429:
            fetch_status = "rate_limited"
        elif http_status != 200:
            fetch_status = "error"
        else:
            try:
                raw = resp.json()
            except Exception:  # noqa: BLE001
                fetch_status = "malformed"
                raw = None

    except Exception as exc:  # noqa: BLE001
        logger.warning("fmp_etf_holdings_probe ticker=%s error=%s", ticker, type(exc).__name__)
        fetch_status = "error"

    # Inspect 200 response for provider-level errors embedded in the body.
    if raw is not None and http_status == 200:
        err_text = _extract_error_from_body(raw)
        if err_text:
            redacted = _redact_key(err_text[:200], api_key)
            provider_message_snippet = redacted
            fetch_status = _classify_error_message(err_text)
            raw = None

    # Classify malformed if body is not a list or dict.
    if raw is not None and not isinstance(raw, (dict, list)):
        fetch_status = "malformed"
        raw = None

    # --- Parse shape ---
    response_shape_keys: list[str] = []
    holdings_raw: list[Any] = []
    top_level_date: Optional[str] = None
    holdings_count: int = 0

    holding_name_field: Optional[str] = None
    holding_symbol_field: Optional[str] = None
    holding_weight_field: Optional[str] = None
    holding_shares_field: Optional[str] = None
    holding_market_value_field: Optional[str] = None
    holding_date_field: Optional[str] = None

    weights_available: bool = False
    weight_basis: Optional[str] = None
    as_of_date_or_date_field: Optional[str] = None
    freshness_status: str = "unknown"
    detected_fields: list[str] = []
    limitations: list[str] = []

    if raw is not None:
        if isinstance(raw, dict):
            response_shape_keys = sorted(k for k in raw.keys() if k.lower() != "apikey")
        elif isinstance(raw, list):
            response_shape_keys = ["<array>"]

        holdings_raw, top_level_date = _detect_holdings_in_response(raw)
        holdings_count = len(holdings_raw)

        # Detect field names from first holding.
        if holdings_raw:
            first = holdings_raw[0] if isinstance(holdings_raw[0], dict) else {}

            for candidate in ("name", "security_name", "description", "Name"):
                if candidate in first:
                    holding_name_field = candidate
                    detected_fields.append("name")
                    break

            for candidate in ("asset", "symbol", "ticker", "Symbol"):
                if candidate in first:
                    holding_symbol_field = candidate
                    detected_fields.append("symbol")
                    break

            for candidate in ("weightPercentage", "weight", "percentage", "Weight", "pct", "holdingPercent"):
                if candidate in first:
                    holding_weight_field = candidate
                    detected_fields.append("weight")
                    weights_available = True
                    weight_basis = "percent"
                    break

            for candidate in ("sharesNumber", "shares", "numberOfShares", "sharesHeld"):
                if candidate in first:
                    holding_shares_field = candidate
                    detected_fields.append("shares")
                    break

            for candidate in ("marketValue", "marketVal", "market_value", "MarketValue"):
                if candidate in first:
                    holding_market_value_field = candidate
                    detected_fields.append("market_value")
                    break

            for candidate in ("date", "updated", "asOfDate", "reportDate", "updatedAt"):
                if candidate in first:
                    holding_date_field = candidate
                    detected_fields.append("holding_date")
                    break

        # Resolve as-of date: prefer top-level date, fall back to first holding's date.
        if top_level_date:
            as_of_date_or_date_field = top_level_date
            detected_fields.append("top_level_date")
            freshness_status = "verified"
        elif holding_date_field and holdings_raw:
            first = holdings_raw[0] if isinstance(holdings_raw[0], dict) else {}
            date_val = _safe_str(first.get(holding_date_field))
            if date_val:
                as_of_date_or_date_field = date_val
                freshness_status = "verified"

        if as_of_date_or_date_field is None:
            freshness_status = "date_missing"
            limitations.append("as_of_date_missing")

        if not weights_available:
            limitations.append("no_per_holding_weights")

        if fetch_status == "error" and holdings_count == 0:
            fetch_status = "no_data"
        elif raw is not None:
            # 200 + valid JSON + no provider error = success
            if holdings_count == 0:
                fetch_status = "no_data"
            else:
                fetch_status = "success"

    # Assess coverage quality.
    coverage_quality = _assess_coverage_quality(ticker, holdings_count, weights_available)
    if coverage_quality in ("partial_or_suspicious", "no_holdings") and "coverage_gap" not in limitations:
        if holdings_count > 0:
            plausible = _PLAUSIBLE_FULL_THRESHOLDS.get(ticker, _DEFAULT_PLAUSIBLE_THRESHOLD)
            limitations.append(f"holdings_count_{holdings_count}_below_plausible_threshold_{plausible}")

    # Holdings sample — first 5 only, name/symbol/weight fields only.
    holdings_sample: list[dict] = []
    for h in holdings_raw[:5]:
        if isinstance(h, dict):
            entry: dict = {}
            if holding_name_field and holding_name_field in h:
                entry["name"] = _safe_str(h[holding_name_field])
            if holding_symbol_field and holding_symbol_field in h:
                entry["symbol"] = _safe_str(h[holding_symbol_field])
            if holding_weight_field and holding_weight_field in h:
                entry["weight"] = _safe_str(h[holding_weight_field])
            holdings_sample.append(entry)

    result: dict = {
        "ticker": ticker,
        "fetch_status": fetch_status,
        "http_status": http_status,
        "provider_message_snippet": provider_message_snippet,
        "response_shape_keys": response_shape_keys,
        "holdings_count": holdings_count,
        "holdings_sample": holdings_sample,
        "detected_fields": sorted(set(detected_fields)),
        "weights_available": weights_available,
        "weight_basis": weight_basis,
        "as_of_date_or_date_field": as_of_date_or_date_field,
        "freshness_status": freshness_status,
        "coverage_quality": coverage_quality,
        "limitations": limitations,
    }

    # Governance: api key must never appear in any field value.
    if api_key:
        for field, val in result.items():
            if isinstance(val, str) and api_key in val:
                result[field] = "[REDACTED]"

    logger.info(
        "fmp_etf_holdings_probe ticker=%s fetch_status=%s holdings=%d weights=%s freshness=%s coverage=%s",
        ticker, fetch_status, holdings_count, weights_available, freshness_status, coverage_quality,
    )
    return result


def _compute_verdict(per_ticker: list[dict]) -> tuple[str, str]:
    """Compute candidate_pass / candidate_partial / candidate_fail verdict.

    candidate_pass: ALL FOUR proof tickers (VOO, SCHD, VXUS, XLE) must be probed
    and each must have plausible_full coverage + weights_available + freshness_status=verified.
    A subset that fully passes is candidate_partial (missing proof tickers noted in reason).

    candidate_partial: Some usable data but not all 4 proof tickers probed and passing,
    or date missing, or coverage weak.

    candidate_fail: Paywalled / unauthorized / no usable holdings across tickers.
    """
    _PROOF_TICKERS = {"VOO", "VXUS", "SCHD", "XLE"}
    probed = {e["ticker"] for e in per_ticker}

    plausible_full = {e["ticker"] for e in per_ticker if e.get("coverage_quality") == "plausible_full"}
    with_weights = {e["ticker"] for e in per_ticker if e.get("weights_available")}
    with_date = {e["ticker"] for e in per_ticker if e.get("freshness_status") == "verified"}
    with_holdings = {e["ticker"] for e in per_ticker if e.get("holdings_count", 0) > 0}

    proof_probed = _PROOF_TICKERS & probed
    missing_proof_tickers = _PROOF_TICKERS - probed

    # Dominant failure check.
    failed_statuses = [
        e["fetch_status"] for e in per_ticker
        if e["fetch_status"] in ("paywalled", "unauthorized", "rate_limited", "error", "malformed", "no_data")
    ]
    fail_dominated = len(failed_statuses) >= max(1, len(per_ticker))

    if fail_dominated and not with_holdings:
        dominant = set(e["fetch_status"] for e in per_ticker)
        return (
            "candidate_fail",
            f"No usable holdings returned. Dominant statuses: {', '.join(sorted(dominant))}.",
        )

    # candidate_pass gate: ALL 4 proof tickers must be probed and each must pass fully.
    if proof_probed == _PROOF_TICKERS:
        proof_pass_full = _PROOF_TICKERS & plausible_full & with_weights & with_date
        if proof_pass_full == _PROOF_TICKERS:
            return (
                "candidate_pass",
                f"All proof tickers ({', '.join(sorted(proof_pass_full))}) returned "
                "plausible holdings + weights + as-of date. FMP free key passes entitlement gate.",
            )

    # candidate_partial — some data but not full pass.
    reasons: list[str] = []

    if missing_proof_tickers:
        reasons.append(
            f"incomplete proof set — missing tickers not yet probed: "
            f"{', '.join(sorted(missing_proof_tickers))}"
        )

    failing_probed = proof_probed - (plausible_full & with_weights & with_date)
    for t in sorted(failing_probed):
        entry = next((e for e in per_ticker if e["ticker"] == t), None)
        if entry:
            cq = entry.get("coverage_quality", "unknown")
            cnt = entry.get("holdings_count", 0)
            reasons.append(f"{t}: coverage_quality={cq} holdings_count={cnt}")
    date_missing = (with_holdings & with_weights) - with_date
    if date_missing:
        reasons.append(f"as-of date missing for: {', '.join(sorted(date_missing))}")
    weight_missing = with_holdings - with_weights
    if weight_missing:
        reasons.append(f"weights missing for: {', '.join(sorted(weight_missing))}")

    if with_holdings or missing_proof_tickers:
        reason_str = "; ".join(reasons) if reasons else "not all proof tickers probed and passing"
        return (
            "candidate_partial",
            "Some holdings returned but full pass criteria not met: " + reason_str,
        )

    return (
        "candidate_fail",
        "No usable holdings returned across all probed tickers.",
    )


def run_fmp_etf_holdings_check(
    api_key: str,
    tickers: list[str],
    *,
    http_get_fn: Optional[Callable] = None,
) -> dict:
    """Run the FMP ETF holdings diagnostic for the given tickers.

    Args:
        api_key:     FMP_API_KEY (never logged or returned).
        tickers:     Normalized, deduplicated list (capped by caller).
        http_get_fn: Injectable HTTP GET override (tests inject fixture).

    Returns:
        Compact normalized diagnostic dict. Never contains the api_key.
    """
    import requests as _requests

    _http_get = http_get_fn if http_get_fn is not None else _requests.get

    started_at = datetime.now(timezone.utc).isoformat()
    per_ticker: list[dict] = []

    for ticker in tickers:
        entry = _probe_ticker(ticker, api_key, _http_get)
        per_ticker.append(entry)

    completed_at = datetime.now(timezone.utc).isoformat()

    tickers_succeeded = [e["ticker"] for e in per_ticker if e["fetch_status"] == "success"]
    tickers_with_holdings = [e["ticker"] for e in per_ticker if e.get("holdings_count", 0) > 0]
    tickers_with_weights = [e["ticker"] for e in per_ticker if e.get("weights_available")]
    tickers_with_date = [e["ticker"] for e in per_ticker if e.get("freshness_status") == "verified"]

    verdict, reason = _compute_verdict(per_ticker)

    logger.info(
        "fmp_etf_holdings_check_complete tickers=%d succeeded=%d "
        "with_holdings=%d with_weights=%d with_date=%d verdict=%s",
        len(tickers),
        len(tickers_succeeded),
        len(tickers_with_holdings),
        len(tickers_with_weights),
        len(tickers_with_date),
        verdict,
    )

    return {
        "provider_id": PROVIDER_ID,
        "diagnostics_only": True,
        "canonical_ready": False,
        "safe_for_decision": False,
        "artifact_writes": 0,
        "decision_policy_changed": False,
        "synthesis_ready_changed": False,
        "visible_snapshot_unchanged": True,
        "started_at": started_at,
        "completed_at": completed_at,
        "tickers_requested": list(tickers),
        "tickers_succeeded": tickers_succeeded,
        "provider_candidate_verdict": verdict,
        "provider_candidate_reason": reason,
        "per_ticker": per_ticker,
    }
