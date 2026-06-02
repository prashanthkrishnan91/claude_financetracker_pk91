"""Stage 9O — Issuer-Official ETF CSV Live Proof Diagnostic Runner v1.

Pure, injectable business logic for the
/diagnostics/finance-intel/etf-issuer-csv-live-check endpoint.
No FastAPI, Supabase, or asyncio dependencies — callable from async endpoints
via asyncio.to_thread, or directly from sync contexts and tests.

Purpose:
  Run a bounded live diagnostic against issuer-official holdings CSV URLs,
  starting with Vanguard ETFs (VTI/VXUS/VOO).
  Prove whether the official CSV provides:
    - identity/fund name (verified from CSV metadata)
    - holdings count (>= 1 rows)
    - weights (per-holding percentage weights)
    - as-of/report date (from CSV metadata)
    - stable URL access pattern

  Do NOT promote any provider to canonical_ready in this module.
  If all 6 S-grade criteria pass for a ticker, record gate_passed=True and
  note that a separate promotion decision is required before any artifact write.

Hard constraints:
  - Never raises; always returns a dict.
  - No artifact writes. No decision mutations. No SQL.
  - safe_for_decision=False in every result and in the top-level output.
  - canonical_ready=False in every ticker result; gate_passed is diagnostic-only.
  - promotion_recommended=False always — Stage 9O is proof-of-path only.
  - sample_holding_names capped at 5.
  - error messages truncated at 300 chars.
  - http_get_fn is injectable (tests use fixture fn; None → httpx.Client).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from .etf_issuer_official_adapter_v1 import fetch_issuer_official_holdings
from .etf_provider_decision_matrix_v1 import check_canonical_gate

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

DIAGNOSTIC_VERSION = "stage9o_v1"

_DEFAULT_TICKERS: list[str] = ["VTI", "VXUS", "VOO"]
_DEFAULT_PROVIDER_ID = "vanguard_official_v1"
_MAX_TICKERS = 10
_ERROR_MSG_MAX_LEN = 300
_MAX_SAMPLE_NAMES = 5


# ── Per-ticker entry builder ──────────────────────────────────────────────────


def _truncate(s: Optional[str], max_len: int = _ERROR_MSG_MAX_LEN) -> Optional[str]:
    if s and len(s) > max_len:
        return s[:max_len] + "…"
    return s


def _build_ticker_entry(
    ticker: str,
    provider_id: str,
    http_get_fn: Optional[Callable[[str], Any]],
) -> dict:
    """Fetch and analyse one ticker's issuer-official holdings CSV.

    Returns a diagnostic dict. Never raises.
    """
    try:
        result = fetch_issuer_official_holdings(
            ticker=ticker,
            provider_id=provider_id,
            http_get_fn=http_get_fn,
        )

        # Determine source_authority for gate check.
        # All issuer-official adapters use "issuer_official".
        source_authority = "issuer_official"
        entitlement_status = "free_no_key_required"

        gate_passed, gate_failures = check_canonical_gate(
            identity_verified=result.identity_verified,
            holdings_count=result.holdings_count,
            weights_available=result.weights_available,
            as_of_or_report_date_present=result.as_of_date is not None,
            source_authority=source_authority,
            entitlement_status=entitlement_status,
        )

        sample_names = (result.sample_holding_names or [])[:_MAX_SAMPLE_NAMES]

        return {
            "ticker": ticker,
            "provider_id": provider_id,
            "fetch_status": result.fetch_status,
            "identity_verified": result.identity_verified,
            "identity_basis": _truncate(result.identity_basis),
            "as_of_date": result.as_of_date,
            "freshness_status": result.freshness_status,
            "holdings_count": result.holdings_count,
            "weights_available": result.weights_available,
            "sample_holding_names": sample_names,
            "source_url": result.source_url,
            "error_message": _truncate(result.error_message),
            "limitations": list(result.limitations or []),
            "canonical_gate_passed": gate_passed,
            "gate_failures": gate_failures,
            "canonical_ready": False,
            "safe_for_decision": False,
            "promotion_recommended": False,
        }

    except Exception as exc:  # noqa: BLE001
        logger.exception("etf_csv_diagnostic ticker=%s provider=%s error=%s", ticker, provider_id, exc)
        return {
            "ticker": ticker,
            "provider_id": provider_id,
            "fetch_status": "runner_error",
            "identity_verified": False,
            "identity_basis": None,
            "as_of_date": None,
            "freshness_status": "unknown",
            "holdings_count": 0,
            "weights_available": False,
            "sample_holding_names": [],
            "source_url": None,
            "error_message": _truncate(f"Runner error: {exc}"),
            "limitations": ["Unexpected runner error — see backend logs."],
            "canonical_gate_passed": False,
            "gate_failures": ["runner_error"],
            "canonical_ready": False,
            "safe_for_decision": False,
            "promotion_recommended": False,
        }


# ── Public API ────────────────────────────────────────────────────────────────


def run_issuer_csv_live_check(
    tickers: list[str],
    provider_id: str = _DEFAULT_PROVIDER_ID,
    http_get_fn: Optional[Callable[[str], Any]] = None,
) -> dict:
    """Run issuer-official CSV diagnostic for each ticker.

    Returns a JSON-serializable diagnostic dict.
    Never raises.

    tickers: list of uppercase ticker symbols (already validated by caller).
    provider_id: issuer-official provider to use (default: vanguard_official_v1).
    http_get_fn: injectable HTTP GET callable for testing.
    """
    run_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    results: dict[str, dict] = {}

    for ticker in tickers:
        results[ticker] = _build_ticker_entry(ticker, provider_id, http_get_fn)

    gate_passed_count = sum(1 for r in results.values() if r["canonical_gate_passed"])

    return {
        "diagnostic_version": DIAGNOSTIC_VERSION,
        "provider_id": provider_id,
        "tickers_requested": len(tickers),
        "tickers_completed": len(results),
        "canonical_gate_passed_count": gate_passed_count,
        "run_timestamp_utc": run_ts,
        "results": results,
        "safe_for_decision": False,
        "diagnostics_only": True,
        "artifact_writes": 0,
        "promotion_note": (
            "canonical_gate_passed=True is diagnostic-only. "
            "A separate PR is required to promote a provider to canonical_ready "
            "and wire it into artifact writes or visible decisions."
        ),
    }
