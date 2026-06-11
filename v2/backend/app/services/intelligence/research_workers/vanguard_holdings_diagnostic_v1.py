"""Stage 9O — Vanguard Issuer-Official Holdings Diagnostic v1 (proof stage only).

Backend-only diagnostic worker. Evaluates whether Vanguard issuer-official
holdings exports can become a future canonical ETF holdings source.

Covers: VTI, VOO, VXUS (default). Up to _MAX_TICKERS per run.

Proof stage only — no canonical adapter, no artifact writes, no synthesis,
no decision integration. canonical_ready=False and safe_for_decision=False always.

Classification values:
  canonical_candidate      — all S-grade criteria verified in this live run.
  supplemental_only        — useful data present but missing a canonical criterion.
  manual_research_required — source reachable but requires manual resolution
                             (identity mismatch; URL not confirmed stable).
  rejected                 — access failure, empty content, or invalid shape.

Automatic canonical_candidate disqualifiers (S-grade hard rules):
  missing as_of_date   → supplemental_only (data present but undated).
  missing weights      → supplemental_only (data present but unweighted).
  identity_not_proven  → manual_research_required.
  access_failure       → rejected.

Evidence recorded per ticker:
  fund_name_detected, identity_verified, identity_basis, holdings_count,
  holdings_weights_available, as_of_date, parse_status, fetch_status,
  failure_reason, url_used, access_pattern, response_type.

Invariants:
  - Never infers or fabricates dates, holdings counts, identities, or weights.
  - Does not modify Stage 9N matrix rules.
  - Does not promote any provider to canonical.
  - Does not change Buy/Hold/Trim/Sell, synthesis_ready, or safe_for_decision.
  - No SQL, no UI, no paid providers, no LLM calls.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

DIAGNOSTIC_VERSION = "stage9o_v1"

# Vanguard issuer-official provider identifiers.
PROVIDER_ID = "vanguard_official_v1"
PROVIDER_LABEL = "issuer_official_vanguard"

# Default tickers for this proof stage.
_DEFAULT_TICKERS: tuple[str, ...] = ("VTI", "VOO", "VXUS")
_MAX_TICKERS: int = 10

# Tickers known to be issued by Vanguard — the only tickers this provider may attempt.
# Any ticker not in this set is rejected before the network call with
# failure_reason="unsupported_issuer_for_provider".
_VANGUARD_ELIGIBLE_TICKERS: frozenset[str] = frozenset({
    "VTI", "VOO", "VXUS", "VGT", "VHT", "VIS", "VYM",
    "VOOG", "VOOV", "VV", "VB", "VO", "VBR", "VBK",
    "MGK", "MGV", "VUG", "VTV",
})

# Access metadata constants.
_ACCESS_PATTERN = "direct_csv_download"
_RESPONSE_TYPE = "csv"

# URL template — mirrors etf_issuer_official_adapter_v1.
_VANGUARD_URL_TEMPLATE = (
    "https://investor.vanguard.com/content/dam/fas-portspec-images/downloads/"
    "etf-shares/{ticker}_QuantDataFundHoldings.csv"
)

# Classification constants (this diagnostic only; orthogonal to Stage 9N matrix values).
CANONICAL_CANDIDATE = "canonical_candidate"
SUPPLEMENTAL_ONLY = "supplemental_only"
MANUAL_RESEARCH_REQUIRED = "manual_research_required"
REJECTED = "rejected"


def _classify_result(result: Any) -> tuple[str, list[str], str, Optional[str]]:
    """Return (classification, disqualifiers, rationale, failure_reason).

    Applies S-grade hard disqualifier rules explicitly by fetch_status, then
    evaluates canonical criteria on the success path.

    Never loosens the canonical gate. Never infers missing evidence.
    """
    fetch_status = result.fetch_status or ""

    # ── Access failures → rejected ─────────────────────────────────────────────
    if fetch_status in ("source_url_fetch_error", "source_url_fetch_timeout", "error"):
        msg = (result.error_message or fetch_status)[:300]
        return REJECTED, ["access_failure"], "HTTP access failed", msg

    if fetch_status == "no_holdings_found":
        return REJECTED, ["no_holdings_found"], (
            "CSV fetched and parsed but zero holding rows found"
        ), fetch_status

    if fetch_status == "source_shape_changed":
        return REJECTED, ["source_shape_changed"], (
            "CSV column layout not recognised — issuer may have changed file schema"
        ), fetch_status

    if fetch_status == "empty_content":
        return REJECTED, ["empty_response"], "Empty response body from URL", fetch_status

    # ── URL not configured → manual_research_required ─────────────────────────
    if fetch_status == "source_url_not_validated":
        return MANUAL_RESEARCH_REQUIRED, ["url_not_configured"], (
            "No confirmed URL for this ticker — manual URL research required"
        ), fetch_status

    # ── Identity not proven → manual_research_required ────────────────────────
    if fetch_status == "identity_not_proven":
        msg = (result.error_message or "fund identity check failed")[:300]
        return MANUAL_RESEARCH_REQUIRED, ["identity_not_proven"], (
            "Fund identity could not be verified from CSV metadata — "
            "detected fund name does not match expected names for this ticker"
        ), msg

    # ── As-of date absent (automatic canonical_candidate disqualifier) ─────────
    if fetch_status == "as_of_date_not_verified":
        return SUPPLEMENTAL_ONLY, ["as_of_date_missing"], (
            "Data accessible and identity verified but as-of date absent — "
            "missing as_of_date automatically disqualifies canonical_candidate"
        ), "as_of_date not found in CSV metadata rows"

    # ── Weights absent (automatic canonical_candidate disqualifier) ────────────
    if fetch_status == "weights_not_verified":
        return SUPPLEMENTAL_ONLY, ["weights_missing"], (
            "Data accessible, identity verified, date present, but no percentage "
            "weight column found — missing weights automatically disqualifies canonical_candidate"
        ), "weight column absent or unparseable in CSV"

    # ── Success path ───────────────────────────────────────────────────────────
    if fetch_status == "success":
        disqualifiers: list[str] = []
        if not result.identity_verified:
            disqualifiers.append("identity_not_proven")
        if not result.as_of_date:
            disqualifiers.append("as_of_date_missing")
        if not result.weights_available:
            disqualifiers.append("weights_missing")
        if result.holdings_count < 1:
            disqualifiers.append("holdings_count_zero")

        if disqualifiers:
            return SUPPLEMENTAL_ONLY, disqualifiers, (
                f"fetch_status=success but S-grade criteria failed: {', '.join(disqualifiers)}"
            ), None

        return CANONICAL_CANDIDATE, [], (
            "All S-grade criteria verified in this live run: "
            "identity_verified, holdings_count >= 1, weights_available, as_of_date present, "
            "issuer_official source authority, free_no_key_required entitlement"
        ), None

    # ── Unexpected fetch_status ────────────────────────────────────────────────
    short_status = fetch_status[:80]
    return REJECTED, [f"unexpected_fetch_status"], (
        f"Unrecognised fetch_status={short_status!r}"
    ), (result.error_message or short_status)[:300]


def run_vanguard_holdings_diagnostic(
    tickers: list[str],
    *,
    http_get_fn: Optional[Callable[[str], Any]] = None,
) -> dict:
    """Run the Vanguard issuer-official holdings diagnostic for the given tickers.

    Each ticker is evaluated independently. Per-ticker evidence is recorded
    exactly as discovered — nothing is inferred or fabricated.

    Args:
        tickers:     Non-empty list of ticker symbols (already validated, uppercase).
        http_get_fn: Injectable HTTP GET callable for testing.
                     Signature: fn(url: str) -> response_with_raise_for_status_and_text
                     If None, the issuer-official adapter uses httpx.Client.

    Returns:
        Structured diagnostic dict. Never includes raw holdings beyond sample names.
        canonical_ready=False and safe_for_decision=False on every result.
        Never raises — exceptions are captured and recorded as rejected per-ticker.
    """
    from .etf_issuer_official_adapter_v1 import fetch_issuer_official_holdings

    started_at = datetime.now(timezone.utc).isoformat()
    per_ticker: list[dict] = []

    canonical_candidate_count = 0
    supplemental_only_count = 0
    manual_research_required_count = 0
    rejected_count = 0

    for ticker in tickers:
        ticker_upper = ticker.upper().strip()
        url = _VANGUARD_URL_TEMPLATE.format(ticker=ticker_upper)

        # ── Issuer eligibility guard — reject before any network call ──────────
        if ticker_upper not in _VANGUARD_ELIGIBLE_TICKERS:
            logger.info(
                "vanguard_diagnostic_unsupported_issuer ticker=%s provider=%s",
                ticker_upper, PROVIDER_ID,
            )
            per_ticker.append({
                "ticker": ticker_upper,
                "url_used": url,
                "access_pattern": _ACCESS_PATTERN,
                "response_type": _RESPONSE_TYPE,
                "fund_name_detected": None,
                "identity_verified": False,
                "identity_basis": None,
                "holdings_count": 0,
                "holdings_weights_available": False,
                "as_of_date": None,
                "parse_status": "unsupported_issuer_for_provider",
                "fetch_status": "unsupported_issuer_for_provider",
                "failure_reason": "unsupported_issuer_for_provider",
                "classification": REJECTED,
                "disqualifiers": ["unsupported_issuer_for_provider"],
                "classification_rationale": (
                    f"Ticker {ticker_upper} is not a known Vanguard ETF — "
                    "this provider may only attempt Vanguard-issued tickers. "
                    "No network call was made."
                ),
                "canonical_ready": False,
                "safe_for_decision": False,
            })
            rejected_count += 1
            continue

        try:
            result = fetch_issuer_official_holdings(
                ticker_upper,
                PROVIDER_ID,
                http_get_fn=http_get_fn,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "vanguard_diagnostic_unexpected_error ticker=%s error=%s",
                ticker_upper, exc,
            )
            per_ticker.append({
                "ticker": ticker_upper,
                "url_used": url,
                "access_pattern": _ACCESS_PATTERN,
                "response_type": _RESPONSE_TYPE,
                "fund_name_detected": None,
                "identity_verified": False,
                "identity_basis": None,
                "holdings_count": 0,
                "holdings_weights_available": False,
                "as_of_date": None,
                "parse_status": "error",
                "fetch_status": "error",
                "failure_reason": str(exc)[:300],
                "classification": REJECTED,
                "disqualifiers": ["unexpected_error"],
                "classification_rationale": "Unexpected error in adapter — recorded as rejected",
                "canonical_ready": False,
                "safe_for_decision": False,
            })
            rejected_count += 1
            continue

        classification, disqualifiers, rationale, failure_reason = _classify_result(result)

        if classification == CANONICAL_CANDIDATE:
            canonical_candidate_count += 1
        elif classification == SUPPLEMENTAL_ONLY:
            supplemental_only_count += 1
        elif classification == MANUAL_RESEARCH_REQUIRED:
            manual_research_required_count += 1
        else:
            rejected_count += 1

        per_ticker.append({
            "ticker": ticker_upper,
            "url_used": url,
            "access_pattern": _ACCESS_PATTERN,
            "response_type": _RESPONSE_TYPE,
            "fund_name_detected": result.detected_fund_name,
            "identity_verified": result.identity_verified,
            "identity_basis": result.identity_basis,
            "holdings_count": result.holdings_count,
            "holdings_weights_available": result.weights_available,
            "as_of_date": result.as_of_date,
            "parse_status": result.fetch_status,
            "fetch_status": result.fetch_status,
            "failure_reason": failure_reason or result.error_message,
            "classification": classification,
            "disqualifiers": disqualifiers,
            "classification_rationale": rationale,
            "canonical_ready": False,
            "safe_for_decision": False,
        })

    completed_at = datetime.now(timezone.utc).isoformat()

    logger.info(
        "vanguard_holdings_diagnostic_complete tickers=%d canonical_candidate=%d "
        "supplemental=%d manual=%d rejected=%d",
        len(tickers),
        canonical_candidate_count,
        supplemental_only_count,
        manual_research_required_count,
        rejected_count,
    )

    return {
        "diagnostic_version": DIAGNOSTIC_VERSION,
        "provider": PROVIDER_LABEL,
        "provider_id": PROVIDER_ID,
        "started_at": started_at,
        "completed_at": completed_at,
        "tickers_requested": len(tickers),
        "per_ticker": per_ticker,
        "summary": {
            "canonical_candidate_count": canonical_candidate_count,
            "supplemental_only_count": supplemental_only_count,
            "manual_research_required_count": manual_research_required_count,
            "rejected_count": rejected_count,
        },
        "safe_for_decision": False,
        "canonical_ready": False,
        "artifact_writes": 0,
        "diagnostics_only": True,
        "policy_unchanged": True,
        "visible_snapshot_unchanged": True,
    }
