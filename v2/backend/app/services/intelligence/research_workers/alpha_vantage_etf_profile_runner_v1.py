"""Stage 9F.3a — Alpha Vantage ETF_PROFILE entitlement + shape diagnostic.

Diagnostic-only. Tests whether Alpha Vantage can provide S-grade ETF holdings
data for the missing ETF set before any canonical adapter is built.

Hard constraints:
  - No artifact writes.
  - No SQL.
  - No decision, synthesis, Deploy, Watchtower, snapshot, or visible Intel change.
  - No LLM calls.
  - API key never logged or returned.
  - Fails closed if ALPHA_VANTAGE_API_KEY is missing.
  - Rate-limited to at most MAX_TICKERS_PER_RUN per call (free quota guard).
  - canonical_ready=False always.
  - safe_for_decision=False always.

Injectable:
  http_get_fn — replaces requests.get (tests inject fixture, no live HTTP in CI).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

PROVIDER_ID = "alpha_vantage_etf_profile_v1"

# Keywords used to classify Alpha Vantage Information / Error Message responses.
# Rate-limit keywords require explicit quota/frequency wording — "standard api" alone
# is intentionally excluded because "standard API users" is an entitlement phrase.
_RATE_LIMIT_KEYWORDS: frozenset = frozenset([
    "request frequency", "daily limit", "rate limit", "requests per day",
    "requests per minute", "api call frequency",
    "usage limit", "calls per day", "calls per minute",
])
# Entitlement keywords — checked after rate-limit so they win on mixed messages
# (e.g. "not available for standard API users, upgrade to premium subscription").
_ENTITLEMENT_KEYWORDS: frozenset = frozenset([
    "premium", "subscription", "entitlement",
])

# Missing ETF set — default tickers to probe.
_DEFAULT_TICKERS: list[str] = [
    "XLE", "VOO", "VTI", "VGT", "VHT", "VIS", "VXUS", "VYM", "SCHD",
]
# Known-good control tickers (only included when include_controls=True).
_CONTROL_TICKERS: list[str] = ["SPY", "QQQ"]

MAX_TICKERS_PER_RUN: int = 11
_DEFAULT_MAX_TICKERS: int = 9

# Candidate pass requires at least this many Vanguard tickers with holdings+weights.
_PASS_VANGUARD_TICKERS: set[str] = {"VOO", "VTI", "VGT", "VHT", "VIS", "VXUS", "VYM"}
_PASS_MIN_VANGUARD: int = 5
_PASS_REQUIRED: set[str] = {"XLE", "SCHD"}

_AV_URL = "https://www.alphavantage.co/query"
_REQUEST_TIMEOUT_SECONDS: float = 15.0


def _safe_str(val: Any) -> Optional[str]:
    if val is None:
        return None
    s = str(val).strip()
    return s if s else None


def _redact_key(url: str) -> str:
    """Strip apikey= parameter from URL strings before logging."""
    import re
    return re.sub(r"apikey=[^&]+", "apikey=[REDACTED]", url)


def _classify_provider_message(data: dict) -> Optional[str]:
    """Return the AV message-type key present in the response, or None."""
    for key in ("Note", "Information", "Error Message"):
        if key in data:
            return key
    return None


def _classify_information_message(text: str) -> str:
    """Map an AV Information/Error Message text to a diagnostic fetch_status sub-class."""
    lower = text.lower()
    if any(k in lower for k in _RATE_LIMIT_KEYWORDS):
        return "rate_limited"
    if any(k in lower for k in _ENTITLEMENT_KEYWORDS):
        return "entitlement_or_premium_required"
    return "provider_note"


def _probe_ticker(
    ticker: str,
    api_key: str,
    http_get_fn: Callable,
) -> dict:
    """Fetch ETF_PROFILE for one ticker and return normalized per-ticker diagnostic."""
    url = _AV_URL
    params = {"function": "ETF_PROFILE", "symbol": ticker, "apikey": api_key}

    http_status: Optional[int] = None
    raw: Optional[dict] = None
    fetch_status = "error"
    provider_message_type: Optional[str] = None
    provider_message_snippet: Optional[str] = None

    try:
        resp = http_get_fn(url, params=params, timeout=_REQUEST_TIMEOUT_SECONDS)
        http_status = resp.status_code

        if http_status == 401:
            fetch_status = "unauthorized"
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
        logger.warning("alpha_vantage_etf_profile_probe ticker=%s error=%s", ticker, type(exc).__name__)
        fetch_status = "error"

    if raw is not None:
        msg_key = _classify_provider_message(raw)
        if msg_key is not None:
            provider_message_type = msg_key
            raw_text = str(raw[msg_key])
            # Redact API key before storing snippet.
            if api_key:
                raw_text = raw_text.replace(api_key, "[REDACTED]")
            provider_message_snippet = raw_text[:200]
            # Note is always a rate-limit signal from AV.
            # Information and Error Message are classified by message content.
            if msg_key == "Note":
                fetch_status = "rate_limited"
            else:
                fetch_status = _classify_information_message(raw_text)
            raw = None

    if raw is not None and not isinstance(raw, dict):
        fetch_status = "malformed"
        raw = None

    if raw is not None and fetch_status == "error":
        # Got 200 + valid JSON with no provider message → success
        fetch_status = "success"

    # --- Parse shape ---
    response_top_level_keys: list[str] = sorted(raw.keys()) if raw else []

    # Governance: never return the api key — it must not appear in any structure.
    response_top_level_keys = [k for k in response_top_level_keys if k.lower() != "apikey"]

    fund_identity_fields_found: list[str] = []
    fund_name: Optional[str] = None
    net_assets: Optional[str] = None
    expense_ratio: Optional[str] = None
    turnover: Optional[str] = None

    holdings_raw: list[Any] = []
    holdings_count: int = 0
    holding_name_field: Optional[str] = None
    holding_symbol_field: Optional[str] = None
    holding_weight_field: Optional[str] = None
    weights_available: bool = False
    weight_basis: Optional[str] = None
    total_weight_sample_sum: Optional[float] = None
    as_of_or_date: Optional[str] = None
    freshness_status: str = "unknown"
    limitations: list[str] = []

    if raw is not None:
        # Fund identity fields
        identity_candidates = {
            "name": "fund_name",
            "description": "description",
            "asset_type": "asset_type",
            "asset class": "asset_class",
            "net_assets": "net_assets",
            "net_expense_ratio": "expense_ratio",
            "portfolio_turnover": "turnover",
        }
        for raw_key, label in identity_candidates.items():
            # AV keys may be mixed-case; check all keys case-insensitively
            for k, v in raw.items():
                if k.lower() == raw_key.lower() and v:
                    fund_identity_fields_found.append(label)
                    val_str = _safe_str(v)
                    if label == "fund_name":
                        fund_name = val_str
                    elif label == "net_assets":
                        net_assets = val_str
                    elif label == "expense_ratio":
                        expense_ratio = val_str
                    elif label == "turnover":
                        turnover = val_str
                    break

        # Holdings
        for k, v in raw.items():
            if k.lower() in ("holdings", "constituents", "portfolio"):
                if isinstance(v, list):
                    holdings_raw = v
                break

        holdings_count = len(holdings_raw)

        if holdings_raw:
            first = holdings_raw[0] if isinstance(holdings_raw[0], dict) else {}
            # Detect name / symbol / weight field names
            for possible_name in ("name", "description", "security_name", "Name"):
                if possible_name in first:
                    holding_name_field = possible_name
                    break
            for possible_sym in ("symbol", "ticker", "Symbol", "Ticker"):
                if possible_sym in first:
                    holding_symbol_field = possible_sym
                    break
            for possible_wt in ("weight", "percentage", "Weight", "Percentage", "pct"):
                if possible_wt in first:
                    holding_weight_field = possible_wt
                    break

            if holding_weight_field:
                weights_available = True
                weight_basis = "percent"
                # Sum sample weights (first 5)
                sample_weights = []
                for h in holdings_raw[:5]:
                    if isinstance(h, dict):
                        wt = h.get(holding_weight_field)
                        try:
                            sample_weights.append(float(wt))
                        except (TypeError, ValueError):
                            pass
                if sample_weights:
                    total_weight_sample_sum = round(sum(sample_weights), 4)

        if not weights_available:
            limitations.append("no_per_holding_weights")

        # as-of / date field
        for k, v in raw.items():
            if k.lower() in ("updated_at", "last_updated", "as_of", "date", "as_of_date"):
                date_str = _safe_str(v)
                if date_str:
                    as_of_or_date = date_str
                    freshness_status = "verified"
                    break

        if as_of_or_date is None:
            freshness_status = "date_missing"
            limitations.append("as_of_date_missing")

        if fetch_status == "success" and holdings_count == 0:
            fetch_status = "no_data"

    # Holdings sample — redacted to first 5 holdings only.
    holdings_sample: list[dict] = []
    for h in holdings_raw[:5]:
        if isinstance(h, dict):
            # Only include name/symbol/weight fields — no raw financial values.
            sample_entry: dict = {}
            if holding_name_field and holding_name_field in h:
                sample_entry["name"] = _safe_str(h[holding_name_field])
            if holding_symbol_field and holding_symbol_field in h:
                sample_entry["symbol"] = _safe_str(h[holding_symbol_field])
            if holding_weight_field and holding_weight_field in h:
                sample_entry["weight"] = _safe_str(h[holding_weight_field])
            holdings_sample.append(sample_entry)

    entry: dict = {
        "ticker": ticker,
        "fetch_status": fetch_status,
        "http_status": http_status,
        "provider_message_type": provider_message_type,
        "provider_message_snippet": provider_message_snippet,
        "response_top_level_keys": response_top_level_keys,
        "fund_identity_fields_found": fund_identity_fields_found,
        "fund_name": fund_name,
        "net_assets": net_assets,
        "expense_ratio": expense_ratio,
        "turnover": turnover,
        "holdings_count": holdings_count,
        "holdings_sample": holdings_sample,
        "holding_name_field": holding_name_field,
        "holding_symbol_field": holding_symbol_field,
        "holding_weight_field": holding_weight_field,
        "weights_available": weights_available,
        "weight_basis": weight_basis,
        "total_weight_sample_or_full_sum": total_weight_sample_sum,
        "as_of_date_or_date_field": as_of_or_date,
        "freshness_status": freshness_status,
        "limitations": limitations,
    }

    # Governance: api key must never appear in any field value.
    if api_key:
        for field, val in entry.items():
            if isinstance(val, str) and api_key in val:
                entry[field] = "[REDACTED]"

    logger.info(
        "alpha_vantage_etf_profile_probe ticker=%s fetch_status=%s holdings=%d weights=%s freshness=%s",
        ticker, fetch_status, holdings_count, weights_available, freshness_status,
    )
    return entry


def _compute_verdict(
    per_ticker: list[dict],
    requested_tickers: list[str],
) -> tuple[str, str]:
    """Compute candidate_pass / candidate_partial / candidate_fail verdict."""
    succeeded = {e["ticker"] for e in per_ticker if e["fetch_status"] == "success"}
    with_holdings = {e["ticker"] for e in per_ticker if e.get("holdings_count", 0) > 0}
    with_weights = {e["ticker"] for e in per_ticker if e.get("weights_available")}
    with_date = {e["ticker"] for e in per_ticker if e.get("freshness_status") == "verified"}

    # Required tickers: XLE, SCHD, and >= 5 Vanguard tickers
    required_ok = _PASS_REQUIRED & with_holdings & with_weights
    vanguard_ok = _PASS_VANGUARD_TICKERS & with_holdings & with_weights
    required_count_ok = len(required_ok) == len(_PASS_REQUIRED)
    vanguard_count_ok = len(vanguard_ok) >= _PASS_MIN_VANGUARD

    date_ok = all(t in with_date for t in (with_holdings & with_weights))
    any_holdings = bool(with_holdings)
    any_weights = bool(with_weights)

    # Dominant failure check
    failed_statuses = [e["fetch_status"] for e in per_ticker if e["fetch_status"] not in ("success",)]
    fail_dominated = len(failed_statuses) > len(per_ticker) / 2

    if required_count_ok and vanguard_count_ok:
        if not date_ok:
            return (
                "candidate_partial",
                f"Holdings+weights found for required tickers ({', '.join(sorted(required_ok))}) "
                f"and {len(vanguard_ok)} Vanguard ETFs, but as-of date missing — "
                "cannot be canonical without date.",
            )
        return (
            "candidate_pass",
            f"Holdings+weights verified for required tickers ({', '.join(sorted(required_ok))}) "
            f"and {len(vanguard_ok)}/{len(_PASS_VANGUARD_TICKERS)} Vanguard ETFs with as-of date.",
        )
    elif any_holdings or any_weights:
        reasons = []
        if not required_count_ok:
            missing_required = _PASS_REQUIRED - required_ok
            reasons.append(f"required tickers missing holdings/weights: {', '.join(sorted(missing_required))}")
        if not vanguard_count_ok:
            reasons.append(f"only {len(vanguard_ok)}/{_PASS_MIN_VANGUARD} required Vanguard ETFs covered")
        if not date_ok:
            reasons.append("as-of date missing for some holdings")
        return (
            "candidate_partial",
            "Some holdings/weights returned but pass criteria not met: " + "; ".join(reasons),
        )
    else:
        error_types = set(e["fetch_status"] for e in per_ticker)
        return (
            "candidate_fail",
            f"No holdings or weights returned. Dominant statuses: {', '.join(sorted(error_types))}.",
        )


def run_alpha_vantage_etf_profile_check(
    api_key: str,
    tickers: list[str],
    *,
    http_get_fn: Optional[Callable] = None,
) -> dict:
    """Run the Alpha Vantage ETF_PROFILE diagnostic for the given tickers.

    Args:
        api_key:     ALPHA_VANTAGE_API_KEY (never logged or returned).
        tickers:     Normalized, deduplicated list (already capped by caller).
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
    tickers_error = [
        e["ticker"] for e in per_ticker
        if e["fetch_status"] not in ("success", "no_data", "rate_limited", "provider_note")
    ]

    verdict, reason = _compute_verdict(per_ticker, tickers)

    logger.info(
        "alpha_vantage_etf_profile_check_complete tickers=%d succeeded=%d "
        "with_holdings=%d with_weights=%d with_date=%d verdict=%s",
        len(tickers),
        len(tickers_succeeded),
        len(tickers_with_holdings),
        len(tickers_with_weights),
        len(tickers_with_date),
        verdict,
    )

    return {
        "started_at": started_at,
        "completed_at": completed_at,
        "provider_id": PROVIDER_ID,
        "diagnostics_only": True,
        "canonical_ready": False,
        "safe_for_decision": False,
        "artifact_writes": 0,
        "decision_policy_changed": False,
        "synthesis_ready_changed": False,
        "visible_snapshot_unchanged": True,
        "tickers_requested": list(tickers),
        "tickers_succeeded": tickers_succeeded,
        "tickers_with_holdings": tickers_with_holdings,
        "tickers_with_weights": tickers_with_weights,
        "tickers_with_as_of_or_date": tickers_with_date,
        "tickers_error": tickers_error,
        "provider_candidate_verdict": verdict,
        "provider_candidate_reason": reason,
        "per_ticker": per_ticker,
    }
