"""Stage 9F.2b — ETF Holdings Provider Registry Runner v1.

Diagnostic runner that orchestrates the ETF holdings provider registry.
For each requested ticker, tries providers in priority order (registry-defined)
and returns the first identity-verified result. All provider attempts are
recorded for diagnostic visibility.

This runner is diagnostic-only:
  - No artifact writes.
  - No decision mutations.
  - canonical_ready=False for all tickers.
  - safe_for_decision=False for all tickers.

Provider try order (per-ticker):
  1. Fetch from provider (SEC NPORT or issuer-official).
  2. If identity_verified → use this result as selected, stop trying.
  3. If not → record attempt, try next provider.
  4. If no provider succeeds → report all failure statuses.

SEC NPORT calls use the existing fetch_etf_nport_holdings() function (identity-
certified, rate-limit delayed). Issuer-official calls use fetch_issuer_official_holdings().

Injectable:
  nport_provider_fn  — replaces fetch_etf_nport_holdings (tests inject fixture)
  issuer_provider_fn — replaces fetch_issuer_official_holdings (tests inject fixture)
  sleep_fn           — replaces time.sleep (tests inject no-op)
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from .etf_holdings_provider_registry_v1 import (
    ETFHoldingsResult,
    get_providers_for_ticker,
    registry_summary,
)

logger = logging.getLogger(__name__)

# Default tickers for the registry diagnostic run.
_REGISTRY_DEFAULT_TICKERS: list[str] = [
    "SPY", "QQQ", "XLE", "GLD",
    "VOO", "VTI", "VGT", "VHT", "VIS", "VXUS", "VYM", "SCHD",
]
_REGISTRY_MAX_TICKERS: int = 20

# Rate-limit delay between SEC NPORT calls (SEC courtesy).
_SEC_DELAY_SECONDS: float = 1.5
# Minimal delay between issuer-official calls (courtesy; not rate-limited by SEC).
_ISSUER_DELAY_SECONDS: float = 0.5


def _normalize_nport_result(
    result: Any,
    provider_id: str,
    source_authority: str = "sec_primary_authority",
) -> ETFHoldingsResult:
    """Wrap a NportProviderResult into the normalized ETFHoldingsResult shape."""
    fm = result.filing_meta
    sample_names = [h.name for h in result.holdings[:5]]
    holdings_count = len(result.holdings)

    as_of_date: Optional[str] = None
    if fm is not None:
        as_of_date = fm.report_period_date or fm.filing_date

    # Weight basis
    weight_basis = "unavailable"
    if getattr(result, "weights_available", False):
        weight_basis = "percent"
        if getattr(result, "weights_derived", False):
            weight_basis = "market_value_derived"

    # Freshness
    freshness = "unknown"
    if as_of_date:
        try:
            from datetime import datetime, timezone
            dt = datetime.strptime(as_of_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            age_days = (datetime.now(timezone.utc) - dt).days
            freshness = "stale" if age_days > 90 else "fresh"
        except (ValueError, TypeError):
            pass

    fetch_status = result.fetch_status
    if fetch_status == "success" and not result.is_success:
        fetch_status = "no_holdings_found"
    # Prefix non-success statuses to distinguish from issuer-official statuses.
    if fetch_status != "success":
        fetch_status = f"sec_nport_{fetch_status}"

    identity_verified = getattr(result, "identity_verified", False)
    # For commodity trust: identity_verified=True but holdings_count=0
    if result.fetch_status == "commodity_trust_or_no_nport_data":
        fetch_status = "commodity_trust_no_equity_holdings"
        identity_verified = True

    return ETFHoldingsResult(
        ticker=result.ticker,
        provider_id=provider_id,
        source_type="sec_nport",
        source_url=fm.filing_url if fm else None,
        source_authority=source_authority,
        as_of_date=as_of_date,
        holdings_count=holdings_count,
        sample_holding_names=sample_names,
        weights_available=getattr(result, "weights_available", False),
        weight_basis=weight_basis,
        identity_verified=identity_verified,
        identity_basis=getattr(result, "identity_basis", None),
        freshness_status=freshness,
        fetch_status=fetch_status,
        error_message=result.error_message,
        limitations=[],
        canonical_ready=False,
        safe_for_decision=False,
    )


def _build_per_ticker_entry(
    ticker: str,
    selected: Optional[ETFHoldingsResult],
    providers_attempted: list[str],
    provider_statuses: list[dict],
) -> dict:
    """Build the compact per-ticker diagnostic dict."""
    if selected is not None:
        return {
            "ticker": ticker,
            "selected_provider_id": selected.provider_id,
            "providers_attempted": providers_attempted,
            "provider_statuses": provider_statuses,
            "identity_verified": selected.identity_verified,
            "identity_basis": selected.identity_basis,
            "as_of_date": selected.as_of_date,
            "holdings_count": selected.holdings_count,
            "weights_available": selected.weights_available,
            "weight_basis": selected.weight_basis,
            "freshness_status": selected.freshness_status,
            "sample_holding_names": selected.sample_holding_names,
            "source_type": selected.source_type,
            "source_authority": selected.source_authority,
            "source_url": selected.source_url,
            "fetch_status": selected.fetch_status,
            "failure_reason": None,
            "canonical_ready": False,
            "safe_for_decision": False,
        }
    # No provider succeeded.
    failure_reasons = [
        f"{s['provider_id']}: {s['fetch_status']}"
        for s in provider_statuses
    ]
    return {
        "ticker": ticker,
        "selected_provider_id": None,
        "providers_attempted": providers_attempted,
        "provider_statuses": provider_statuses,
        "identity_verified": False,
        "identity_basis": None,
        "as_of_date": None,
        "holdings_count": 0,
        "weights_available": False,
        "weight_basis": "unavailable",
        "freshness_status": "unknown",
        "sample_holding_names": [],
        "source_type": None,
        "source_authority": None,
        "source_url": None,
        "fetch_status": "no_provider_succeeded",
        "failure_reason": "; ".join(failure_reasons) if failure_reasons else "no_providers_registered",
        "canonical_ready": False,
        "safe_for_decision": False,
    }


def run_provider_registry_check(
    tickers: list[str],
    user_agent: str,
    *,
    nport_provider_fn: Optional[Callable] = None,
    issuer_provider_fn: Optional[Callable] = None,
    sleep_fn: Optional[Callable] = None,
) -> dict:
    """Run the provider registry diagnostic for the given tickers.

    For each ticker:
      1. Looks up providers in priority order from the registry.
      2. Calls SEC NPORT or issuer-official provider.
      3. Returns the first identity-verified result.
      4. Records all provider attempts in provider_statuses.

    Args:
        tickers:          List of uppercase ticker symbols.
        user_agent:       SEC EDGAR User-Agent string (for NPORT calls).
        nport_provider_fn: Injectable override for fetch_etf_nport_holdings.
        issuer_provider_fn: Injectable override for fetch_issuer_official_holdings.
        sleep_fn:         Injectable sleep override (use lambda s: None in tests).

    Returns:
        Compact dict with per_ticker list, aggregate counts, and governance
        invariants. Never contains raw holdings payload or raw XML.
    """
    from .nport_provider_v1 import NportProviderConfig, fetch_etf_nport_holdings
    from .etf_issuer_official_adapter_v1 import fetch_issuer_official_holdings

    _nport = nport_provider_fn or fetch_etf_nport_holdings
    _issuer = issuer_provider_fn or fetch_issuer_official_holdings
    _sleep = sleep_fn if sleep_fn is not None else time.sleep

    nport_cfg = NportProviderConfig(
        user_agent=user_agent,
        timeout_seconds=20.0,
        max_filings_to_scan=12,
    )

    started_at = datetime.now(timezone.utc).isoformat()
    per_ticker: list[dict] = []

    success_count = 0
    no_data_count = 0
    error_count = 0
    identity_verified_count = 0
    issuer_official_selected_count = 0
    sec_nport_selected_count = 0
    _last_was_nport = False

    for ticker in tickers:
        providers = get_providers_for_ticker(ticker)
        providers_attempted: list[str] = []
        provider_statuses: list[dict] = []
        selected: Optional[ETFHoldingsResult] = None
        _this_had_nport = False

        for provider_record in providers:
            if not provider_record.enabled_for_diagnostics:
                continue

            pid = provider_record.provider_id

            # Rate-limit delay: SEC NPORT before this call, or issuer after prior NPORT.
            if provider_record.source_type == "sec_nport":
                if _last_was_nport or _this_had_nport:
                    _sleep(_SEC_DELAY_SECONDS)
                _this_had_nport = True
            elif _this_had_nport:
                # Small courtesy delay after a NPORT call.
                _sleep(_ISSUER_DELAY_SECONDS)

            providers_attempted.append(pid)

            result: ETFHoldingsResult
            if provider_record.source_type == "sec_nport":
                raw = _nport(ticker, nport_cfg)
                result = _normalize_nport_result(raw, pid)
            else:
                result = _issuer(ticker, pid)

            status_entry = {
                "provider_id": pid,
                "source_type": result.source_type,
                "source_authority": result.source_authority,
                "fetch_status": result.fetch_status,
                "identity_verified": result.identity_verified,
                "holdings_count": result.holdings_count,
                "as_of_date": result.as_of_date,
                "error_message": (
                    result.error_message[:200] + "…"
                    if result.error_message and len(result.error_message) > 200
                    else result.error_message
                ),
                "limitations": list(result.limitations),
            }
            provider_statuses.append(status_entry)

            # Accept the first result that passes the source-type-specific gate.
            is_commodity = result.fetch_status == "commodity_trust_no_equity_holdings"
            if is_commodity:
                # GLD / commodity trust — identity assumed, no holdings to validate.
                selected = result
                break
            elif provider_record.source_type == "sec_nport":
                # SEC NPORT: identity + at least one holding is sufficient.
                if result.identity_verified and result.holdings_count > 0:
                    selected = result
                    break
            else:
                # Issuer-official: all four attributes must be present and verified.
                if (result.identity_verified
                        and result.holdings_count > 0
                        and result.as_of_date
                        and result.weights_available
                        and result.weight_basis == "percent"
                        and result.source_authority == "issuer_official"):
                    selected = result
                    break

        _last_was_nport = _this_had_nport

        entry = _build_per_ticker_entry(ticker, selected, providers_attempted, provider_statuses)
        per_ticker.append(entry)

        if selected is not None:
            if selected.fetch_status == "commodity_trust_no_equity_holdings":
                no_data_count += 1
            else:
                success_count += 1
                identity_verified_count += 1
                if selected.source_type == "issuer_official":
                    issuer_official_selected_count += 1
                else:
                    sec_nport_selected_count += 1
        else:
            # Check if any provider returned a hard error.
            any_error = any(
                s["fetch_status"] in (
                    "sec_nport_sec_error", "sec_nport_timeout", "sec_nport_error",
                    "source_url_fetch_error", "error",
                )
                for s in provider_statuses
            )
            if any_error:
                error_count += 1
            else:
                no_data_count += 1

    logger.info(
        "etf_provider_registry_check_complete tickers=%d success=%d "
        "identity_verified=%d no_data=%d error=%d "
        "issuer_official_selected=%d sec_nport_selected=%d",
        len(tickers), success_count, identity_verified_count,
        no_data_count, error_count,
        issuer_official_selected_count, sec_nport_selected_count,
    )

    return {
        "started_at": started_at,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "registry_version": "stage9f2b_v1",
        "tickers_requested": len(tickers),
        "tickers_succeeded": success_count,
        "tickers_identity_verified": identity_verified_count,
        "tickers_no_data": no_data_count,
        "tickers_error": error_count,
        "issuer_official_selected_count": issuer_official_selected_count,
        "sec_nport_selected_count": sec_nport_selected_count,
        "per_ticker": per_ticker,
        "registry_summary": registry_summary(),
        # Governance invariants — never mutated.
        "safe_for_decision": False,
        "canonical_ready": False,
        "diagnostics_only": True,
        "artifact_writes": 0,
        "decision_policy_changed": False,
        "synthesis_ready_changed": False,
        "visible_snapshot_unchanged": True,
    }
