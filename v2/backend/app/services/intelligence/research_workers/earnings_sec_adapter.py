"""Phase 6A / Phase 7A — Adapter: converts SecEdgarProviderResult into WorkerOutput components.

This module is a pure function with no DB calls, no external calls, and no side effects.
It bridges the SEC EDGAR provider result into the source/fact/confidence/freshness
fields required by the research artifact contracts.

Freshness window (documented constant):
  FRESH  — most recent source-backed filing date within _FRESH_WINDOW_DAYS (180) days.
           Includes 8-K event notices (material events are a form of recency evidence).
  STALE  — source-backed but most recent filing date older than _FRESH_WINDOW_DAYS.
  UNKNOWN — no parseable filing date across any source-backed filings, or fetch failed.

Confidence classification (documented constant):
  MEDIUM — at least one 10-K or 10-Q filing present (official periodic financial report).
  LOW    — only 8-K filings (material events only, no periodic financial report).
  UNKNOWN — no source-backed evidence (fetch failed, no filings, or no user agent).

Phase 7A additions:
  - CompanyFacts metric_observation FactRecords sourced from the companyfacts parse result.
  - Every metric_observation FactRecord has source_index set (linked to a filing SourceRecord).
  - Facts without a matching source accession are skipped (no unlinked facts).
  - Source fingerprint includes a deterministic digest of metric observations.
  - Confidence/freshness remain MEDIUM/LOW/UNKNOWN (no HIGH) and FRESH/STALE/UNKNOWN.
  - If companyfacts failed or yielded no observations, Phase 6A filing metadata behavior
    is preserved with a limitation recorded.
  - safe_for_decision remains False in all cases (enforced by writer + DB constraint).
  - eligible_for_decision_consumption remains always False.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

from .contracts import FactRecord, SourceRecord
from .sec_edgar_provider import SecEdgarProviderResult
from .sec_companyfacts_parser import compute_metric_digest

# ── Classification constants (documented) ────────────────────────────────────

# Filing published within this many days → FRESH freshness classification.
_FRESH_WINDOW_DAYS = 180

# Provider / source metadata constants.
_SEC_PROVIDER_NAME = "sec_edgar"
_SEC_PROVIDER_VERSION = "sec_edgar_submissions_v1"
_SEC_SOURCE_KIND = "sec_filing"

# Filings that carry substantive periodic financial data (not just event notices).
_PERIODIC_FILING_FORMS: frozenset[str] = frozenset({"10-K", "10-Q"})

# Source fingerprint prefix for fail-closed cases (SEC attempted but no grounding).
_FINGERPRINT_ERROR = "sec_edgar_error_fail_closed"
_FINGERPRINT_NO_FILINGS = "sec_edgar_no_filings"


@dataclass
class SecEarningsAdapterResult:
    """Complete adapter output — fields plug directly into WorkerOutput.

    All fields have safe defaults (UNKNOWN confidence/freshness, empty sources/facts).
    Only populated when SEC fetch succeeded with at least one relevant filing.
    """

    sources: list[SourceRecord] = field(default_factory=list)
    facts: list[FactRecord] = field(default_factory=list)
    confidence_or_trust_level: str = "UNKNOWN"   # HIGH | MEDIUM | LOW | UNKNOWN
    freshness_status: str = "UNKNOWN"             # FRESH | STALE | UNKNOWN
    source_refs_fingerprint: str = _FINGERPRINT_ERROR
    review_status: str = "sec_source_error_fail_closed"
    source_window_start: Optional[str] = None    # ISO date
    source_window_end: Optional[str] = None      # ISO date
    expires_at: Optional[str] = None             # ISO date
    limitations: list[str] = field(default_factory=list)


def _parse_date(date_str: Optional[str]) -> Optional[date]:
    """Parse YYYY-MM-DD string to date. Returns None on any error."""
    if not date_str:
        return None
    try:
        return date.fromisoformat(str(date_str)[:10])
    except (ValueError, TypeError):
        return None


def _compute_source_fingerprint(cik: str, filings: list, metric_digest: str = "") -> str:
    """Deterministic SHA-256 fingerprint of CIK + source-backed filings + metric digest.

    Includes every filing used to create a SourceRecord (10-K, 10-Q, 8-K).
    Phase 7A: also includes metric_digest so fingerprint changes when
    companyfacts observations change materially.

    Any change to filings or metric observations → new fingerprint →
    new replay_idempotency_key → new artifact row.
    """
    filing_entries = sorted(
        [
            {
                "form_type": f.form_type,
                "accession_number": f.accession_number,
                "filing_date": f.filing_date,
            }
            for f in filings
        ],
        key=lambda x: (x["accession_number"], x["form_type"], x["filing_date"]),
    )
    raw = json.dumps(
        {"cik": cik, "filings": filing_entries, "metric_digest": metric_digest},
        sort_keys=True,
    )
    digest = hashlib.sha256(raw.encode()).hexdigest()[:24]
    return f"sec_edgar_{digest}"


def adapt_sec_result(
    sec_result: SecEdgarProviderResult,
    reference_date: Optional[date] = None,
) -> SecEarningsAdapterResult:
    """Convert SecEdgarProviderResult to WorkerOutput source/fact/confidence/freshness.

    Args:
        sec_result:     Result from sec_edgar_provider.fetch_for_ticker().
        reference_date: Date to use for freshness calculation (default: today UTC).
                        Inject in tests to make comparisons deterministic.

    Returns:
        SecEarningsAdapterResult — always. Never raises. Fail-closed on any error.
    """
    ref_date = reference_date or datetime.now(timezone.utc).date()

    # ── Fail path: no user agent (gate failed before any HTTP call) ───────────
    if sec_result.fetch_status == "no_user_agent":
        return SecEarningsAdapterResult(
            source_refs_fingerprint=_FINGERPRINT_ERROR,
            review_status="sec_source_error_fail_closed",
            limitations=[
                "SEC EDGAR fetch not attempted: SEC_EDGAR_USER_AGENT not configured.",
                "No transcript provider configured.",
                "Missing earnings calendar, EPS estimates, and analyst guidance.",
            ],
        )

    # ── Fail path: any non-success fetch ─────────────────────────────────────
    if not sec_result.is_success:
        status_msg = sec_result.error_message or sec_result.fetch_status
        return SecEarningsAdapterResult(
            source_refs_fingerprint=_FINGERPRINT_ERROR,
            review_status="sec_source_error_fail_closed",
            limitations=[
                f"SEC EDGAR fetch failed ({sec_result.fetch_status}): {status_msg}",
                "No transcript provider configured.",
                "Missing earnings calendar, EPS estimates, and analyst guidance.",
            ],
        )

    # ── Success but no relevant filings returned ──────────────────────────────
    if not sec_result.filings:
        return SecEarningsAdapterResult(
            source_refs_fingerprint=_FINGERPRINT_NO_FILINGS,
            review_status="sec_source_unavailable",
            limitations=[
                f"SEC EDGAR returned no recent 10-K/10-Q/8-K filings for {sec_result.ticker}.",
                "No transcript provider configured.",
                "Missing earnings calendar, EPS estimates, and analyst guidance.",
            ],
        )

    # ── Success with filings — build source/fact records ─────────────────────
    sources: list[SourceRecord] = []
    facts: list[FactRecord] = []

    for filing in sec_result.filings:
        src_idx = len(sources)
        src = SourceRecord(
            source_kind=_SEC_SOURCE_KIND,
            provider_name=_SEC_PROVIDER_NAME,
            provider_version=_SEC_PROVIDER_VERSION,
            source_url=filing.filing_url,
            source_published_at=filing.filing_date,
            section_reference=filing.accession_number,
        )
        sources.append(src)

        fact_payload: dict = {
            "claim": "sec_filing_found",
            "form_type": filing.form_type,
            "filing_date": filing.filing_date,
            "accession_number": filing.accession_number,
        }
        if filing.report_date:
            fact_payload["period_of_report"] = filing.report_date

        facts.append(FactRecord(
            fact_kind="sourced_claim",
            structured_payload=fact_payload,
            axis_hint="catalyst",
            period=filing.report_date,
            as_of=filing.filing_date,
            source_index=src_idx,
        ))

    # ── Phase 7A: metric_observation facts from CompanyFacts XBRL ────────────
    # Build accession_number → source_index mapping from the sources list above.
    # Only metric observations whose accn matches a SourceRecord are included.
    # Observations without a source link are silently skipped per spec.
    metric_observation_count = 0
    cf_parse_result = getattr(sec_result, "companyfacts_parse_result", None)
    if cf_parse_result is not None and cf_parse_result.is_success:
        accn_to_src_idx: dict[str, int] = {
            src.section_reference: idx
            for idx, src in enumerate(sources)
            if src.section_reference
        }
        for obs in cf_parse_result.observations:
            src_idx = accn_to_src_idx.get(obs.accession_number)
            if src_idx is None:
                continue  # no matching source — skip per spec
            metric_payload: dict = {
                "claim": "sec_companyfact_observed",
                "taxonomy": obs.taxonomy,
                "tag": obs.tag,
                "label": obs.label,
                "value": obs.value,
                "unit": obs.unit,
                "form": obs.form,
                "filed": obs.filed,
                "accession_number": obs.accession_number,
            }
            if obs.fiscal_year is not None:
                metric_payload["fiscal_year"] = obs.fiscal_year
            if obs.fiscal_period is not None:
                metric_payload["fiscal_period"] = obs.fiscal_period
            facts.append(FactRecord(
                fact_kind="metric_observation",
                structured_payload=metric_payload,
                axis_hint="evidence",
                period=obs.fiscal_period,
                as_of=obs.filed,
                source_index=src_idx,
            ))
            metric_observation_count += 1

    # ── Confidence classification ─────────────────────────────────────────────
    # MEDIUM: has at least one 10-K or 10-Q (official periodic financial report).
    # LOW:    only 8-K filings (material event notices, not periodic financials).
    has_periodic = any(f.form_type in _PERIODIC_FILING_FORMS for f in sec_result.filings)
    confidence = "MEDIUM" if has_periodic else "LOW"

    # ── Freshness classification ──────────────────────────────────────────────
    # Use the most recent filing_date across ALL source-backed filings.
    # 8-K event notices are included — if the most recent SEC activity is an 8-K,
    # it still anchors freshness (material events are a form of recency evidence).
    # UNKNOWN only if no parseable filing date exists across any source-backed filing.
    most_recent_source_date: Optional[date] = None
    for f in sec_result.filings:
        d = _parse_date(f.filing_date)
        if d is not None and (
            most_recent_source_date is None or d > most_recent_source_date
        ):
            most_recent_source_date = d

    source_window_start: Optional[str] = None
    source_window_end: Optional[str] = None
    expires_at: Optional[str] = None
    freshness: str

    if most_recent_source_date is not None:
        days_old = (ref_date - most_recent_source_date).days
        if days_old <= _FRESH_WINDOW_DAYS:
            freshness = "FRESH"
            # Artifact expires when the most recent filing would become stale.
            expires_at = (
                most_recent_source_date + timedelta(days=_FRESH_WINDOW_DAYS)
            ).isoformat()
        else:
            freshness = "STALE"
            # No explicit expiry for STALE; freshness status already signals outdated.
            expires_at = None
        source_window_end = most_recent_source_date.isoformat()
        # Window start: 1 fiscal year before the most recent source-backed filing.
        source_window_start = (
            most_recent_source_date - timedelta(days=365)
        ).isoformat()
    else:
        # No parseable filing date across any source-backed filing.
        freshness = "UNKNOWN"

    # ── Source fingerprint for idempotency ────────────────────────────────────
    # Includes metric digest so fingerprint changes when observations change.
    # Phase 7A: metric_digest="" when no observations — still differs from Phase 6A
    # because metric_digest key is now always present in the hash input.
    metric_obs_list = (
        cf_parse_result.observations
        if cf_parse_result is not None and cf_parse_result.is_success
        else []
    )
    metric_digest = compute_metric_digest(metric_obs_list)
    source_refs_fingerprint = _compute_source_fingerprint(
        cik=sec_result.cik or "",
        filings=sec_result.filings,
        metric_digest=metric_digest,
    )

    # ── Limitations — always honest ───────────────────────────────────────────
    limitations = [
        "No earnings transcript provider configured.",
        "No earnings calendar or analyst EPS estimate provider configured.",
        "No analyst guidance data available. SEC filing metadata and XBRL metrics only.",
        "SEC filing data does not imply analyst expectations or earnings surprises.",
        (
            f"SEC EDGAR evidence: {len(sources)} filing(s) retrieved for "
            f"{sec_result.ticker} (CIK {sec_result.cik})."
        ),
    ]
    if metric_observation_count > 0:
        limitations.append(
            f"Phase 7A: {metric_observation_count} XBRL metric observation(s) extracted "
            f"(filing-source-linked, backend-only, not recommendation authority)."
        )
    else:
        cf_status = cf_parse_result.parse_status if cf_parse_result is not None else "not_fetched"
        limitations.append(
            f"Phase 7A: no XBRL metric observations extracted "
            f"(companyfacts_status={cf_status}). Filing metadata evidence only."
        )

    return SecEarningsAdapterResult(
        sources=sources,
        facts=facts,
        confidence_or_trust_level=confidence,
        freshness_status=freshness,
        source_refs_fingerprint=source_refs_fingerprint,
        review_status="sec_source_grounded_partial",
        source_window_start=source_window_start,
        source_window_end=source_window_end,
        expires_at=expires_at,
        limitations=limitations,
    )
