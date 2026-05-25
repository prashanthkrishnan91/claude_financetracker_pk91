"""Stage 9F.2a — ETF NPORT-P Holdings Evidence Adapter v1 (pure, no IO).

Converts a NportProviderResult from nport_provider_v1 into a ticker-scope
WorkerOutput suitable for ResearchArtifactServiceV1.write_artifact().

Artifact contract:
  artifact_type = "etf_fund_note"             (existing DB enum — no SQL needed)
  skill_pack    = "etf_sec_nport_holdings_evidence_v1"
  model_version = "sec_nport_etf_holdings_v1"
  scope_kind    = "ticker"
  source_kind   = "sec_filing"                (valid DB enum)
  provider_name = "sec_edgar"

SourceRecord:
  One record per successful NPORT-P filing (the filing itself as the source).
  source_kind="sec_filing"; source_id=accession_number; filing_url as provenance.
  source_published_at = filing_date from the NPORT-P submission.

FactRecord (one per holding):
  fact_kind    = "metric_observation"         (valid DB enum)
  axis_hint    = "exposure"                   (valid DB enum)
  structured_payload contains ONLY safe metadata:
    holding_name, cusip, isin, lei, ticker (when present in filing),
    weight_pct (directly from filing or mathematically derived from same-filing values),
    value_usd (when available), currency, asset_category, country_of_risk,
    report_period_date (from filing), provider, lane.
  No raw XML, no raw filing content, no fabricated sector/geography labels.
  sector_status = MISSING (NPORT does not provide sector labels; derived in 9F.3).
  geography_status = MISSING unless countryOfRisk is directly present in the filing.

Payload:
  summary counts, report/as-of dates, holdings_count,
  total_reported_value_present, weights_available, weights_derived,
  geography_status, sector_status, freshness note, limitations.

Honesty invariants (non-negotiable):
  - safe_for_decision is always False.
  - synthesis_ready is never asserted.
  - No fabricated holdings, weights, geographies, or sector labels.
  - No raw XML or filing dumps.
  - sector_status = MISSING.
  - geography_status = MISSING unless filing directly provides countryOfRisk.
  - NPORT-P is official but periodic/lagged — freshness recorded honestly.
  - GLD and commodity trusts: honest no-holdings artifact if no equity holdings.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

from .contracts import (
    AuditEventRecord,
    FactRecord,
    SourceRecord,
    WorkerInput,
    WorkerOutput,
    compute_input_fingerprint,
    compute_replay_idempotency_key,
)
from .nport_provider_v1 import NportHolding, NportProviderResult

# ── Artifact contract constants ───────────────────────────────────────────────

_ARTIFACT_TYPE = "etf_fund_note"
_SKILL_PACK = "etf_sec_nport_holdings_evidence_v1"
_MODEL_VERSION = "sec_nport_etf_holdings_v1"
_SCOPE_KIND = "ticker"
_PROVIDER_NAME = "sec_edgar"
_SOURCE_KIND = "sec_filing"                # valid DB source_kind enum

# NPORT-P is a quarterly filing with ~60d filing lag → freshness window is 120d.
# FRESH if report_period_date is within this many days of fetched_at.
_FRESHNESS_FRESH_DAYS: int = 120
_FRESHNESS_STALE_DAYS: int = 365           # STALE beyond one year (superceded filing)


# ── Internal result type ──────────────────────────────────────────────────────


@dataclass
class _AdapterResult:
    sources: list[SourceRecord] = field(default_factory=list)
    facts: list[FactRecord] = field(default_factory=list)
    confidence_or_trust_level: str = "UNKNOWN"
    freshness_status: str = "UNKNOWN"
    source_refs_fingerprint: str = "no_data"
    summary: str = ""
    limitations: list[str] = field(default_factory=list)
    source_window_start: Optional[str] = None
    source_window_end: Optional[str] = None
    artifact_payload_extra: dict[str, Any] = field(default_factory=dict)


# ── Internal helpers ──────────────────────────────────────────────────────────


def _freshness_from_period(
    report_period_date: Optional[str],
    fetched_at: str,
) -> str:
    """Determine freshness label from NPORT report period date.

    NPORT-P is official but lagged/periodic:
      - FRESH: report period within _FRESHNESS_FRESH_DAYS of fetched_at (quarterly lag OK).
      - STALE: report period older than _FRESHNESS_STALE_DAYS.
      - STALE_OR_UNKNOWN: report period not available or cannot be parsed.
    """
    if not report_period_date:
        return "STALE_OR_UNKNOWN"
    try:
        period_dt = datetime.strptime(report_period_date[:10], "%Y-%m-%d").replace(
            tzinfo=timezone.utc
        )
        now = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
        now = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
        age_days = (now - period_dt).days
        if age_days <= _FRESHNESS_FRESH_DAYS:
            return "FRESH"
        if age_days <= _FRESHNESS_STALE_DAYS:
            return "STALE"
        return "STALE"
    except Exception:  # noqa: BLE001
        return "STALE_OR_UNKNOWN"


def _confidence_from_holding_count(count: int) -> str:
    """Holdings count → confidence band.

    HIGH for large diversified funds (>100 holdings fully parsed);
    MEDIUM for moderate-size funds; LOW for tiny/commodity-trust artifacts.
    """
    if count >= 100:
        return "HIGH"
    if count >= 20:
        return "MEDIUM"
    if count >= 1:
        return "LOW"
    return "UNKNOWN"


def _compute_holdings_fingerprint(
    holdings: list[NportHolding],
    report_period_date: Optional[str],
) -> str:
    """Deterministic SHA-256 fingerprint over holdings for idempotency key."""
    entries = []
    for h in holdings:
        entries.append({
            "name": h.name,
            "cusip": h.cusip or "",
            "isin": h.isin or "",
            "weight_pct": h.weight_pct,
            "value_usd": h.value_usd,
        })
    entries.sort(key=lambda x: (x["cusip"] or x["isin"] or x["name"]))
    raw = json.dumps(
        {"holdings": entries, "period": report_period_date or ""},
        sort_keys=True, default=str,
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def _has_geography(holdings: list[NportHolding]) -> bool:
    """True when at least one holding has a countryOfRisk from the filing."""
    return any(h.country_of_risk for h in holdings)


def _build_no_data_result(
    ticker: str,
    fetch_status: str,
    error_message: Optional[str],
) -> _AdapterResult:
    """Return an honest thin-evidence adapter result for non-success provider states."""
    return _AdapterResult(
        source_refs_fingerprint=f"nport_no_data_{ticker}_{fetch_status}",
        summary=(
            f"SEC NPORT-P ETF holdings: no usable data for {ticker} "
            f"(fetch_status={fetch_status})."
        ),
        limitations=[
            f"SEC NPORT-P fetch did not produce usable holdings: {error_message or fetch_status}.",
            "No holdings recorded. This may be because:",
            "  - Ticker has no NPORT-P/NPORT-EX filing (e.g. commodity trust like GLD).",
            "  - CIK mapping is missing for this ticker.",
            "  - SEC EDGAR returned an error or the filing could not be parsed.",
            "  - The filing has no structured holding elements.",
        ],
        artifact_payload_extra={
            "fetch_status": fetch_status,
            "holdings_count": 0,
            "total_reported_value_present": False,
            "weights_available": False,
            "weights_derived": False,
            "geography_status": "MISSING",
            "sector_status": "MISSING",
            "report_period_date": None,
            "filing_date": None,
        },
    )


# ── Public adapter function ───────────────────────────────────────────────────


def adapt_etf_nport(
    provider_result: NportProviderResult,
    fetched_at: str,
) -> _AdapterResult:
    """Convert NportProviderResult → adapter result.

    Honest thin-evidence when no holdings were parsed.
    Never fabricates holding data, sectors, or geography.
    """
    ticker = provider_result.ticker or "UNKNOWN"

    if not provider_result.is_success:
        return _build_no_data_result(
            ticker,
            provider_result.fetch_status,
            provider_result.error_message,
        )

    holdings = provider_result.holdings
    filing_meta = provider_result.filing_meta
    report_period_date = filing_meta.report_period_date if filing_meta else None
    filing_date = filing_meta.filing_date if filing_meta else None
    accession_number = filing_meta.accession_number if filing_meta else None
    form_type = filing_meta.form_type if filing_meta else "NPORT-P"
    filing_url = filing_meta.filing_url if filing_meta else None

    freshness = _freshness_from_period(report_period_date, fetched_at)
    confidence = _confidence_from_holding_count(len(holdings))
    geography_present = _has_geography(holdings)
    geography_status = "AVAILABLE" if geography_present else "MISSING"

    # ── SourceRecord ──────────────────────────────────────────────────────────
    # One source record for the NPORT-P filing itself.
    # source_url is internal provenance only — never exposed in diagnostics.
    sources: list[SourceRecord] = [
        SourceRecord(
            source_kind=_SOURCE_KIND,
            provider_name=_PROVIDER_NAME,
            provider_version=_MODEL_VERSION,
            source_url=filing_url,
            source_id=accession_number,
            source_published_at=filing_date or fetched_at,
            fetched_at=fetched_at,
            section_reference=f"nport_filing:{form_type}:{report_period_date or 'unknown'}",
        )
    ]

    # ── FactRecords — one per holding ─────────────────────────────────────────
    facts: list[FactRecord] = []
    for idx, h in enumerate(holdings):
        # Structured payload: safe metadata only.
        # No raw XML, no forbidden keys, no inferred sectors/geography.
        payload: dict[str, Any] = {
            "holding_name": h.name,
            "provider": _PROVIDER_NAME,
            "lane": "etf_nport_holdings",
            "skill_pack": _SKILL_PACK,
        }
        if h.cusip:
            payload["cusip"] = h.cusip
        if h.isin:
            payload["isin"] = h.isin
        if h.lei:
            payload["lei"] = h.lei
        if h.ticker:
            payload["holding_ticker"] = h.ticker
        if h.weight_pct is not None:
            payload["weight_pct"] = h.weight_pct
            payload["weight_source"] = (
                "derived_from_filing_values"
                if provider_result.weights_derived
                else "direct_from_filing"
            )
        if h.value_usd is not None:
            payload["value_usd"] = h.value_usd
        if h.currency:
            payload["currency"] = h.currency
        if h.asset_category:
            payload["asset_category"] = h.asset_category
        if h.country_of_risk:
            payload["country_of_risk"] = h.country_of_risk
        if h.issuer_category:
            payload["issuer_category"] = h.issuer_category
        if report_period_date:
            payload["report_period_date"] = report_period_date
        # Sector is MISSING at Stage 9F.2a — not derivable from NPORT without
        # additional classification that is deferred to 9F.3.
        payload["sector_status"] = "MISSING"

        facts.append(FactRecord(
            fact_kind="metric_observation",
            axis_hint="exposure",
            structured_payload=payload,
            period=report_period_date,
            as_of=report_period_date,
            is_quote_grounded=True,
            source_index=0,             # all facts share the single source record
        ))

    fingerprint = f"nport_{ticker}_{_compute_holdings_fingerprint(holdings, report_period_date)}"

    limitations: list[str] = [
        "Official SEC NPORT-P regulatory filing — PRIMARY_AUTHORITY source.",
        (
            "Periodic/lagged: NPORT-P discloses the 3rd month of each quarter with "
            "approximately 60-day lag. Holdings reflect the report period date, "
            "not the current date."
        ),
        "Full reported holdings from the filing (not a top-N subset).",
        (
            "Sector classification: MISSING at Stage 9F.2a — NPORT does not provide "
            "issuer-level sector labels; derivation deferred to Stage 9F.3."
        ),
        (
            f"Geography: {'country_of_risk available from filing for some holdings'  if geography_present else 'MISSING — NPORT countryOfRisk not present in this filing'  }. "
            "Full geographic exposure derivation deferred to Stage 9F.3."
        ),
        "safe_for_decision=False — ETF holdings evidence feeds data layer only.",
    ]

    if provider_result.weights_derived:
        limitations.append(
            "Holdings weights: derived from (valUSD / totAssets) using values from "
            "the same NPORT-P filing. Direct pctVal was not present for all holdings."
        )
    elif provider_result.weights_available:
        limitations.append("Holdings weights: directly available as pctVal from filing.")
    else:
        limitations.append(
            "Holdings weights: not available (pctVal absent, totAssets insufficient for derivation)."
        )

    summary = (
        f"SEC NPORT-P ETF holdings: {len(holdings)} holdings parsed for {ticker} "
        f"(form={form_type}, report_period={report_period_date or 'unknown'}, "
        f"filing_date={filing_date or 'unknown'}). "
        f"confidence={confidence}, freshness={freshness}. "
        f"weights_available={provider_result.weights_available}, "
        f"weights_derived={provider_result.weights_derived}. "
        f"Official SEC source; periodic/lagged, not daily."
    )

    return _AdapterResult(
        sources=sources,
        facts=facts,
        confidence_or_trust_level=confidence,
        freshness_status=freshness,
        source_refs_fingerprint=fingerprint,
        summary=summary,
        limitations=limitations,
        source_window_start=report_period_date,
        source_window_end=report_period_date,
        artifact_payload_extra={
            "fetch_status": provider_result.fetch_status,
            "holdings_count": len(holdings),
            "total_reported_value_present": provider_result.total_reported_value_present,
            "weights_available": provider_result.weights_available,
            "weights_derived": provider_result.weights_derived,
            "geography_status": geography_status,
            "sector_status": "MISSING",
            "report_period_date": report_period_date,
            "filing_date": filing_date,
            "form_type": form_type,
            "cik": provider_result.cik,
        },
    )


# ── WorkerOutput builder ──────────────────────────────────────────────────────


def build_etf_nport_worker_output(
    worker_input: WorkerInput,
    provider_result: NportProviderResult,
    fetched_at: str,
) -> WorkerOutput:
    """Build a ticker-scope WorkerOutput from an NportProviderResult.

    This is the single callable the runner uses to create the ETF NPORT-P
    holdings evidence artifact for ResearchArtifactServiceV1.write_artifact().
    """
    result = adapt_etf_nport(provider_result, fetched_at)
    ticker = (provider_result.ticker or worker_input.ticker or "UNKNOWN").upper().strip()

    fp_data: dict[str, Any] = {
        "skill_pack": _SKILL_PACK,
        "scope": _SCOPE_KIND,
        "model_version": _MODEL_VERSION,
        "ticker": ticker,
        "phase": "stage9f2a_etf_nport_holdings",
        "holdings_count": result.artifact_payload_extra.get("holdings_count", 0),
    }
    input_fingerprint = compute_input_fingerprint(fp_data)

    replay_key = compute_replay_idempotency_key(
        skill_pack=_SKILL_PACK,
        scope_kind=_SCOPE_KIND,
        ticker=ticker,
        source_refs_fingerprint=result.source_refs_fingerprint,
        model_version=_MODEL_VERSION,
    )

    payload: dict[str, Any] = {
        "lane": _SKILL_PACK,
        "scope": _SCOPE_KIND,
        "worker_phase": "stage9f2a_etf_nport_holdings_evidence",
        "provider": _PROVIDER_NAME,
        "ticker": ticker,
    }
    payload.update(result.artifact_payload_extra)

    audit_events: list[AuditEventRecord] = [
        AuditEventRecord(
            tool_call="etf_nport_evidence_run",
            status="completed",
            model_id=None,
            model_version=_MODEL_VERSION,
            cost_estimate_usd=0.0,
            latency_ms=0,
        )
    ]

    return WorkerOutput(
        worker_run_id=worker_input.worker_run_id,
        ticker=ticker,
        artifact_type=_ARTIFACT_TYPE,
        skill_pack=_SKILL_PACK,
        scope_kind=_SCOPE_KIND,
        artifact_payload=payload,
        sources=result.sources,
        facts=result.facts,
        audit_events=audit_events,
        evidence_summary_plain_english=result.summary,
        limitations_or_missing_evidence=result.limitations,
        confidence_or_trust_level=result.confidence_or_trust_level,
        freshness_status=result.freshness_status,
        input_fingerprint=input_fingerprint,
        replay_idempotency_key=replay_key,
        source_window_start=result.source_window_start,
        source_window_end=result.source_window_end,
        expires_at=None,
        parent_intel_run_id=worker_input.parent_intel_run_id,
        generated_by_model=None,
        model_version=_MODEL_VERSION,
    )
