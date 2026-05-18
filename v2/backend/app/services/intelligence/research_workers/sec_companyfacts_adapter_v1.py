"""Stage 5H — SEC CompanyFacts Official Fundamentals Adapter v1 (pure, no IO).

Converts a SecEdgarProviderResult (with parsed XBRL CompanyFacts metric
observations) into a WorkerOutput suitable for
ResearchArtifactServiceV1.write_artifact().

artifact_type = "fundamental_quality" (existing DB constraint; no new SQL)
skill_pack    = "sec_companyfacts_evidence_v1" (distinct from yfinance)
model_version = "sec_xbrl_companyfacts_v1"

Source linking:
  One SourceRecord per unique filing accession that has at least one
  MetricObservation. Accession→SourceRecord mapping uses SecFilingRecord
  metadata from the provider result (for URLs, dates, form types).
  Defensive fallback when a filing record is absent (should not occur given
  source_accessions filtering in sec_companyfacts_parser, but handled).

Fact mapping:
  One FactRecord per MetricObservation, preserving:
    tag, label, value, unit, fiscal_year, fiscal_period, filed,
    accession_number, form, taxonomy.
  No computed ratios, no annualized values, no directional inferences.

No-data / error path:
  When provider_result has no companyfacts, or parse_status != "success",
  or fetch_status != "success", returns an honest thin-evidence result
  with limitations recorded. Never fabricates observations, periods, or values.

Hard constraints (non-negotiable):
  - Never calls decide() or imports the decision policy.
  - Never writes to intel_v3_snapshots or recommendations.
  - safe_for_decision always False.
  - No new LLM calls.
  - No paid provider activation.
  - No fabrication of missing facts.
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
from .sec_edgar_provider import SecEdgarProviderResult, SecFilingRecord
from .sec_companyfacts_parser import (
    MetricObservation,
    compute_metric_digest,
)

# ── Artifact type and skill pack constants ────────────────────────────────────

_ARTIFACT_TYPE = "fundamental_quality"
_SKILL_PACK = "sec_companyfacts_evidence_v1"
_MODEL_VERSION = "sec_xbrl_companyfacts_v1"
_SCOPE_KIND = "ticker"

# Observations filed within this many days are considered FRESH.
_FRESHNESS_FRESH_DAYS = 180  # 6 months — covers annual 10-K + quarterly 10-Q cadence


# ── Internal adapter result type ──────────────────────────────────────────────

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

def _build_period_str(
    fiscal_year: Optional[int],
    fiscal_period: Optional[str],
    filed: str,
) -> str:
    """Build a compact period string for FactRecord.period."""
    if fiscal_year is not None and fiscal_period:
        return f"{fiscal_year}-{fiscal_period}"
    if fiscal_year is not None:
        return str(fiscal_year)
    # Fallback: YYYY-MM from filed date
    return filed[:7] if filed and len(filed) >= 7 else filed


def _freshness_from_filed_dates(
    observations: list[MetricObservation],
    fetched_at: str,
) -> str:
    """Return FRESH / STALE / UNKNOWN based on the most recently filed observation."""
    if not observations:
        return "UNKNOWN"
    try:
        now = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
    except Exception:
        now = datetime.now(timezone.utc)

    newest_filed: Optional[datetime] = None
    for obs in observations:
        if not obs.filed:
            continue
        try:
            filed_dt = datetime.fromisoformat(obs.filed)
            if filed_dt.tzinfo is None:
                filed_dt = filed_dt.replace(tzinfo=timezone.utc)
            if newest_filed is None or filed_dt > newest_filed:
                newest_filed = filed_dt
        except Exception:
            continue

    if newest_filed is None:
        return "UNKNOWN"

    now_aware = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    age_days = (now_aware - newest_filed).days
    return "FRESH" if age_days <= _FRESHNESS_FRESH_DAYS else "STALE"


def _confidence_from_tag_count(tag_count: int) -> str:
    if tag_count >= 8:
        return "HIGH"
    if tag_count >= 4:
        return "MEDIUM"
    if tag_count >= 1:
        return "LOW"
    return "UNKNOWN"


# ── Public adapter function ───────────────────────────────────────────────────

def adapt_sec_companyfacts(
    provider_result: SecEdgarProviderResult,
    ticker: str,
    fetched_at: str,
) -> _AdapterResult:
    """Convert SecEdgarProviderResult (with companyfacts) → adapter result.

    Honest thin-evidence when no observations are available. Never fabricates.

    Args:
        provider_result: Result from sec_edgar_provider.fetch_for_ticker().
        ticker:          Upper-cased ticker symbol.
        fetched_at:      ISO 8601 timestamp of the fetch call.
    """
    # ── No-CIK / fetch error path ─────────────────────────────────────────────
    if not provider_result.is_success:
        status = provider_result.fetch_status
        msg = provider_result.error_message or f"fetch_status={status}"
        return _AdapterResult(
            source_refs_fingerprint=f"sec_no_fetch_{status}",
            summary=(
                f"SEC CompanyFacts for {ticker}: fetch did not succeed "
                f"(fetch_status={status}). No fundamentals extracted."
            ),
            limitations=[
                f"SEC EDGAR fetch failed: {msg}",
                "No XBRL metric observations available for this ticker.",
            ],
            artifact_payload_extra={
                "fetch_status": status,
                "cik": provider_result.cik,
                "observation_count": 0,
                "tag_count": 0,
            },
        )

    # ── No companyfacts result ────────────────────────────────────────────────
    cf = provider_result.companyfacts_parse_result
    if cf is None:
        return _AdapterResult(
            source_refs_fingerprint="sec_no_companyfacts_attempted",
            summary=(
                f"SEC CompanyFacts for {ticker}: companyfacts not fetched "
                f"(budget or provider config). Filings metadata only."
            ),
            limitations=[
                "CompanyFacts XBRL fetch was not attempted "
                "(request budget or missing SEC user agent).",
                "No XBRL metric observations available.",
            ],
            artifact_payload_extra={
                "fetch_status": "success",
                "cik": provider_result.cik,
                "observation_count": 0,
                "tag_count": 0,
            },
        )

    # ── CompanyFacts parse error or no_facts ──────────────────────────────────
    if not cf.is_success:
        return _AdapterResult(
            source_refs_fingerprint=f"sec_cf_{cf.parse_status}",
            summary=(
                f"SEC CompanyFacts for {ticker}: XBRL parse returned "
                f"parse_status={cf.parse_status}. No metric observations."
            ),
            limitations=[
                f"SEC CompanyFacts parse status: {cf.parse_status}. "
                + (cf.error_message or "No observations extracted."),
                "No metric facts available; artifact records the evidence gap.",
            ],
            artifact_payload_extra={
                "fetch_status": "success",
                "cik": provider_result.cik,
                "observation_count": 0,
                "tag_count": 0,
                "parse_status": cf.parse_status,
            },
        )

    # ── Success path: build SourceRecords and FactRecords ────────────────────
    observations = cf.observations
    if not observations:
        return _AdapterResult(
            source_refs_fingerprint="sec_cf_zero_observations",
            summary=(
                f"SEC CompanyFacts for {ticker}: parse succeeded but "
                "returned zero observations."
            ),
            limitations=["CompanyFacts parse succeeded but extracted no observations."],
            artifact_payload_extra={
                "fetch_status": "success",
                "cik": provider_result.cik,
                "observation_count": 0,
                "tag_count": 0,
                "parse_status": cf.parse_status,
            },
        )

    # Build an accession_number → SecFilingRecord index for fast lookup.
    filing_index: dict[str, SecFilingRecord] = {
        f.accession_number: f for f in provider_result.filings
    }

    # Collect unique accession numbers that appear in observations (ordered for determinism).
    seen_accns: dict[str, int] = {}  # accession_number → source_index
    for obs in observations:
        if obs.accession_number not in seen_accns:
            seen_accns[obs.accession_number] = len(seen_accns)

    sources: list[SourceRecord] = []
    for accn, _ in sorted(seen_accns.items(), key=lambda x: x[1]):
        filing = filing_index.get(accn)
        if filing:
            sources.append(SourceRecord(
                source_kind="sec_filing",
                provider_name="sec_edgar",
                provider_version=_MODEL_VERSION,
                source_url=filing.filing_url,
                source_id=accn,
                source_published_at=filing.filing_date,
                fetched_at=fetched_at,
                section_reference=filing.form_type,
            ))
        else:
            # Defensive fallback: observation's accession was not in the filing list.
            sources.append(SourceRecord(
                source_kind="sec_filing",
                provider_name="sec_edgar",
                provider_version=_MODEL_VERSION,
                source_id=accn,
                fetched_at=fetched_at,
                section_reference="10-K/10-Q",
            ))

    facts: list[FactRecord] = []
    for obs in observations:
        src_idx = seen_accns[obs.accession_number]
        period_str = _build_period_str(obs.fiscal_year, obs.fiscal_period, obs.filed)
        facts.append(FactRecord(
            fact_kind="metric_observation",
            structured_payload={
                "metric_name": obs.tag,
                "metric_label": obs.label,
                "value": obs.value,
                "unit": obs.unit,
                "fiscal_year": obs.fiscal_year,
                "fiscal_period": obs.fiscal_period,
                "filed": obs.filed,
                "accession_number": obs.accession_number,
                "taxonomy": obs.taxonomy,
                "form": obs.form,
                "provider": "sec_edgar",
            },
            period=period_str,
            as_of=obs.filed if obs.filed else fetched_at,
            is_quote_grounded=True,
            axis_hint="quality",
            source_index=src_idx,
        ))

    tag_count = len(cf.tags_found)
    confidence = _confidence_from_tag_count(tag_count)
    freshness = _freshness_from_filed_dates(observations, fetched_at)

    # Deterministic fingerprint from metric observations digest.
    fingerprint_raw = compute_metric_digest(observations)
    source_refs_fingerprint = f"sec_cf_{fingerprint_raw}"

    # Source window: filed dates of observations.
    filed_dates = sorted(
        obs.filed for obs in observations if obs.filed
    )
    source_window_start = filed_dates[0] if filed_dates else None
    source_window_end = filed_dates[-1] if filed_dates else None

    limitations: list[str] = [
        "SEC CompanyFacts XBRL — official but reflects last reported fiscal period.",
        "Coverage limited to us-gaap taxonomy allowlisted concepts only.",
        "Not available for ETF/fund/crypto/non-company tickers.",
    ]
    if cf.fy_eps_added_beyond_generic_limit_count > 0:
        limitations.append(
            f"FY EPS coverage policy added {cf.fy_eps_added_beyond_generic_limit_count} "
            "annual EPS observation(s) beyond the generic limit to preserve 10-K coverage."
        )

    summary = (
        f"SEC CompanyFacts for {ticker}: {len(observations)} XBRL metric observation(s) "
        f"from {tag_count} tag(s) across {len(sources)} filing(s). "
        f"CIK={provider_result.cik}, confidence={confidence}, freshness={freshness}. "
        f"Official us-gaap source. No analyst estimates, no forward guidance."
    )

    return _AdapterResult(
        sources=sources,
        facts=facts,
        confidence_or_trust_level=confidence,
        freshness_status=freshness,
        source_refs_fingerprint=source_refs_fingerprint,
        summary=summary,
        limitations=limitations,
        source_window_start=source_window_start,
        source_window_end=source_window_end,
        artifact_payload_extra={
            "fetch_status": "success",
            "cik": provider_result.cik,
            "observation_count": len(observations),
            "tag_count": tag_count,
            "tags_found": cf.tags_found,
            "parse_status": cf.parse_status,
            "filing_count": len(sources),
            "fy_eps_coverage_additions": cf.fy_eps_added_beyond_generic_limit_count,
        },
    )


# ── WorkerOutput builder ──────────────────────────────────────────────────────

def build_sec_companyfacts_worker_output(
    worker_input: WorkerInput,
    provider_result: SecEdgarProviderResult,
    fetched_at: str,
) -> WorkerOutput:
    """Build WorkerOutput from a SecEdgarProviderResult.

    This is the single callable that the runner uses to create the
    artifact for ResearchArtifactServiceV1.write_artifact().

    Args:
        worker_input:    WorkerInput from the runner (user_id, ticker, run_id).
        provider_result: Output of fetch_for_ticker() — may have companyfacts.
        fetched_at:      ISO 8601 fetch timestamp.
    """
    ticker = (worker_input.ticker or "").upper().strip()
    result = adapt_sec_companyfacts(provider_result, ticker, fetched_at)

    fp_data: dict[str, Any] = {
        "skill_pack": _SKILL_PACK,
        "ticker": ticker,
        "model_version": _MODEL_VERSION,
        "phase": "stage5h_sec_companyfacts",
    }
    if worker_input.holding_context:
        fp_data["context_keys"] = sorted(worker_input.holding_context.keys())
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
        "reviewed_ticker": ticker,
        "worker_phase": "stage5h_sec_companyfacts",
        "provider": "sec_edgar",
    }
    payload.update(result.artifact_payload_extra)

    audit_events: list[AuditEventRecord] = [
        AuditEventRecord(
            tool_call="sec_companyfacts_evidence_run",
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
