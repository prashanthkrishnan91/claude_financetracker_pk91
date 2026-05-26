"""Stage 9F.2a — NPORT-P live diagnostic runner.

Pure, injectable business logic for the /diagnostics/finance-intel/etf-nport-live-check
endpoint. No FastAPI, Supabase, or asyncio dependencies — callable from async endpoints
via asyncio.to_thread, or directly from sync contexts and tests.

Accepts an injectable provider_fn and sleep_fn so the logic is fully testable
without live SEC EDGAR calls or import-time side effects.

Stage 9F.2a identity-certification additions: per-ticker output now includes
identity_status, identity_verified, identity_basis, candidate_ciks_tried,
selected_candidate_cik, detected_registrant_name, detected_series_name,
detected_class_name, detected_series_id, detected_class_id, and
identity_mismatch_reason.  These fields allow post-deploy validation to confirm
that each ticker's holdings are identity-certified to the correct ETF/fund/series
rather than blindly accepted from a parent registrant's filing.

Stage 9F.2a resolver discovery additions: optional discovery_fn and
discovered_provider_fn parameters enable the runner to query SEC EFTS for
candidate CIKs when the static parent map fails identity validation.
Per-ticker output gains resolver_discovery_used, resolver_discovery_candidates,
resolver_discovery_sources_tried, and resolver_discovery_error fields.
Discovery is diagnostic-lane only — no production evidence changes.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# ── Constants (re-exported for use in diagnostics.py and tests) ───────────────

_NPORT_DIAG_DEFAULT_TICKERS: list[str] = [
    "SPY", "QQQ", "XLE", "VOO", "VTI", "VGT", "VHT", "VIS", "VXUS", "VYM", "SCHD", "GLD",
]
_NPORT_DIAG_MAX_TICKERS: int = 20
_NPORT_DIAG_DELAY_SECONDS: float = 1.5   # SEC EDGAR rate-limit courtesy delay
_NPORT_DIAG_ERROR_MSG_MAX_LEN: int = 200


def _build_ticker_entry(
    result: Any,
    error_msg_max_len: int = _NPORT_DIAG_ERROR_MSG_MAX_LEN,
    discovery_result: Any = None,
) -> dict:
    """Build a compact, safe per-ticker dict from one NportProviderResult.

    Never includes raw XML, raw filing body, or the full holdings payload.
    Sample holding names are capped at 5. Error messages are truncated.
    Identity certification fields (Stage 9F.2a) are included to allow post-deploy
    validation that holdings are correctly attributed to the requested ETF.
    Resolver discovery fields (Stage 9F.2a discovery) surface candidate discovery
    metadata when discovery_fn is used in the diagnostic lane.
    """
    fm = result.filing_meta
    sample_names = [h.name for h in result.holdings[:5]]
    error_msg = result.error_message
    if error_msg and len(error_msg) > error_msg_max_len:
        error_msg = error_msg[:error_msg_max_len] + "…"  # ellipsis

    entry = {
        "ticker": result.ticker,
        "fetch_status": result.fetch_status,
        "resolver_source": result.resolver_source,
        "parent_registrant_name": result.parent_registrant_name,
        "resolved_cik": result.cik,
        "holdings_count": len(result.holdings),
        "form_type": fm.form_type if fm else None,
        "accession_number": fm.accession_number if fm else None,
        "filing_date": fm.filing_date if fm else None,
        "report_period_date": fm.report_period_date if fm else None,
        "primary_doc_from_submissions": result.primary_doc_from_submissions,
        "primary_doc_attempted": result.primary_doc_attempted,
        "selected_doc_source": result.selected_doc_source,
        "candidate_doc_count": result.candidate_doc_count,
        "index_urls_attempted_count": result.index_urls_attempted_count,
        "parse_failure_stage": result.parse_failure_stage,
        "xml_extracted_from_sgml": fm.xml_extracted_from_sgml if fm else None,
        "weights_available": result.weights_available,
        "weights_derived": result.weights_derived,
        "sample_holdings_count": len(sample_names),
        "sample_holding_names": sample_names,
        "error_message": error_msg,
        # Identity certification fields (Stage 9F.2a identity-certification repair)
        "identity_status": getattr(result, "identity_status", None),
        "identity_verified": getattr(result, "identity_verified", False),
        "identity_basis": getattr(result, "identity_basis", None),
        "candidate_ciks_tried": getattr(result, "candidate_ciks_tried", []),
        "selected_candidate_cik": getattr(result, "selected_candidate_cik", None),
        "detected_registrant_name": getattr(result, "detected_registrant_name", None),
        "detected_series_name": getattr(result, "detected_series_name", None),
        "detected_class_name": getattr(result, "detected_class_name", None),
        "detected_series_id": getattr(result, "detected_series_id", None),
        "detected_class_id": getattr(result, "detected_class_id", None),
        "identity_mismatch_reason": getattr(result, "identity_mismatch_reason", None),
        "candidate_identity_failures": getattr(result, "candidate_identity_failures", []),
        # Scan diagnostic fields (Stage 9F.2a multi-filing scan)
        "filings_scanned_count": getattr(result, "filings_scanned_count", 0),
        "matching_filing_rank": getattr(result, "matching_filing_rank", None),
        "scan_limit_reached": getattr(result, "scan_limit_reached", False),
        # Resolver discovery fields (Stage 9F.2a resolver discovery)
        "resolver_discovery_used": False,
        "resolver_discovery_candidates": [],
        "resolver_discovery_sources_tried": [],
        "resolver_discovery_error": None,
    }

    if discovery_result is not None:
        entry["resolver_discovery_used"] = True
        entry["resolver_discovery_candidates"] = [
            {
                "candidate_cik": c.candidate_cik,
                "candidate_title": c.candidate_title,
                "candidate_source": c.candidate_source,
                "match_reason": c.match_reason,
                "confidence": c.confidence,
                "rejection_reason": c.rejection_reason,
            }
            for c in discovery_result.discovered_candidates
        ]
        entry["resolver_discovery_sources_tried"] = list(
            discovery_result.discovery_sources_tried
        )
        entry["resolver_discovery_error"] = discovery_result.discovery_error

    return entry


def run_nport_live_check(
    tickers: list[str],
    user_agent: str,
    *,
    provider_fn: Optional[Callable] = None,
    sleep_fn: Optional[Callable] = None,
    discovery_fn: Optional[Callable] = None,
    discovered_provider_fn: Optional[Callable] = None,
) -> dict:
    """Fetch NPORT-P holdings for each ticker and return a compact diagnostic dict.

    Args:
        tickers:               Non-empty list of uppercase ticker symbols (already validated).
        user_agent:            SEC_EDGAR_USER_AGENT string (already validated as non-empty).
        provider_fn:           Override for fetch_etf_nport_holdings (test injection).
        sleep_fn:              Override for inter-request delay (pass ``lambda s: None`` in tests).
        discovery_fn:          Optional callable(ticker, entry) -> NportDiscoveryResult.
                               When provided, runs SEC candidate discovery after any identity
                               failure and retries with newly discovered CIKs.
                               Signature: discovery_fn(ticker, entry) → NportDiscoveryResult.
        discovered_provider_fn: Optional callable(ticker, cfg, candidate_ciks) -> NportProviderResult.
                               Called with discovered CIKs when discovery finds new candidates.
                               Defaults to fetch_etf_nport_holdings with _candidate_ciks_override.

    Returns:
        Compact dict with ``per_ticker`` list, aggregate counts, and governance
        invariants. Never includes raw XML, full holdings payload, or secrets.
        Identity certification and resolver discovery fields appear per-ticker
        for post-deploy validation. Discovery is diagnostic-lane only.
    """
    from .nport_provider_v1 import NportProviderConfig, fetch_etf_nport_holdings

    _provider = provider_fn or fetch_etf_nport_holdings
    _sleep = sleep_fn if sleep_fn is not None else time.sleep

    # Default discovered_provider_fn: use fetch_etf_nport_holdings with CIK override.
    def _default_discovered_provider(t: str, c: Any, ciks: list[str]) -> Any:
        return fetch_etf_nport_holdings(t, c, _candidate_ciks_override=ciks)

    _discovered_provider = discovered_provider_fn or _default_discovered_provider

    # Diagnostic endpoint uses explicit scan cap (12 filings per candidate).
    cfg = NportProviderConfig(user_agent=user_agent, timeout_seconds=20.0, max_filings_to_scan=12)

    started_at = datetime.now(timezone.utc).isoformat()
    per_ticker: list[dict] = []
    success_count = 0
    no_data_count = 0
    error_count = 0
    identity_verified_count = 0

    for i, ticker in enumerate(tickers):
        if i > 0:
            _sleep(_NPORT_DIAG_DELAY_SECONDS)

        result = _provider(ticker, cfg)
        discovery_result = None

        # ── Resolver discovery (diagnostic lane only) ─────────────────────────
        # Run discovery when discovery_fn is configured AND the static candidate
        # path did not yield an identity-verified result.
        if discovery_fn is not None and not getattr(result, "identity_verified", False):
            try:
                from .etf_parent_cik_resolver import get_parent_registrant_entry
                _entry = get_parent_registrant_entry(ticker)
                discovery_result = discovery_fn(ticker, _entry)

                # Filter to non-rejected candidates not already tried by static provider.
                already_tried = set(getattr(result, "candidate_ciks_tried", []))
                new_ciks = [
                    c.candidate_cik
                    for c in discovery_result.discovered_candidates
                    if c.confidence != "rejected"
                    and c.candidate_cik not in already_tried
                ]

                if new_ciks:
                    result = _discovered_provider(ticker, cfg, new_ciks)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "nport_resolver_discovery_error ticker=%s error=%s",
                    ticker, exc,
                )

        entry = _build_ticker_entry(result, discovery_result=discovery_result)
        per_ticker.append(entry)

        if result.is_success:
            success_count += 1
            if getattr(result, "identity_verified", False):
                identity_verified_count += 1
        elif result.fetch_status in ("sec_error", "timeout", "error", "missing_cik"):
            error_count += 1
        else:
            no_data_count += 1

    logger.info(
        "nport_live_diagnostic_complete total=%d success=%d identity_verified=%d "
        "no_data=%d error=%d discovery_enabled=%s",
        len(tickers),
        success_count,
        identity_verified_count,
        no_data_count,
        error_count,
        discovery_fn is not None,
    )

    return {
        "started_at": started_at,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "tickers_requested": len(tickers),
        "tickers_succeeded": success_count,
        "tickers_identity_verified": identity_verified_count,
        "tickers_no_data": no_data_count,
        "tickers_error": error_count,
        "per_ticker": per_ticker,
        "safe_for_decision": False,
        "visible_snapshot_unchanged": True,
        "diagnostics_only": True,
        "artifact_writes": 0,
        "decision_policy_changed": False,
        "synthesis_ready_changed": False,
        "resolver_discovery_enabled": discovery_fn is not None,
    }
