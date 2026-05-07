"""Phase 6A — Adapter: converts SecEdgarProviderResult into WorkerOutput components.

This module is a pure function with no DB calls, no external calls, and no side effects.
It bridges the SEC EDGAR provider result into the source/fact/confidence/freshness
fields required by the research artifact contracts.

Freshness window (documented constant):
  FRESH  — most recent 10-K or 10-Q filing date within _FRESH_WINDOW_DAYS (180) days.
  STALE  — source-backed but most recent 10-K/10-Q older than _FRESH_WINDOW_DAYS.
  UNKNOWN — no reliable filing date for periodic filings, or fetch failed.

Confidence classification (documented constant):
  MEDIUM — at least one 10-K or 10-Q filing present (official periodic financial report).
  LOW    — only 8-K filings (material events only, no periodic financial report).
  UNKNOWN — no source-backed evidence (fetch failed, no filings, or no user agent).

Phase 6A scope:
  - Filing metadata only. No transcript. No EPS estimates. No analyst guidance.
  - No claim that SEC facts equal analyst expectations.
  - safe_for_decision remains False in all cases (enforced by writer + DB constraint).
  - Artifacts with MEDIUM/FRESH status are structurally eligible for Phase 5 readiness
    but NOT eligible for decision consumption (eligible_for_decision_consumption=False always).
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


def _compute_source_fingerprint(cik: str, filings: list) -> str:
    """Deterministic SHA-256 fingerprint of CIK + sorted periodic accession numbers.

    If the set of 10-K/10-Q accession numbers changes (new filing published),
    this fingerprint changes → new replay_idempotency_key → new artifact row.
    """
    periodic_accessions = sorted(
        f.accession_number
        for f in filings
        if f.form_type in _PERIODIC_FILING_FORMS
    )
    raw = json.dumps({"cik": cik, "accessions": periodic_accessions}, sort_keys=True)
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

    # ── Confidence classification ─────────────────────────────────────────────
    # MEDIUM: has at least one 10-K or 10-Q (official periodic financial report).
    # LOW:    only 8-K filings (material event notices, not periodic financials).
    has_periodic = any(f.form_type in _PERIODIC_FILING_FORMS for f in sec_result.filings)
    confidence = "MEDIUM" if has_periodic else "LOW"

    # ── Freshness classification ──────────────────────────────────────────────
    # Based on the most recent 10-K or 10-Q filing date only.
    # 8-K dates are not used for freshness (they are event notices, not reports).
    most_recent_periodic_date: Optional[date] = None
    for f in sec_result.filings:
        if f.form_type in _PERIODIC_FILING_FORMS:
            d = _parse_date(f.filing_date)
            if d is not None and (
                most_recent_periodic_date is None or d > most_recent_periodic_date
            ):
                most_recent_periodic_date = d

    source_window_start: Optional[str] = None
    source_window_end: Optional[str] = None
    expires_at: Optional[str] = None
    freshness: str

    if most_recent_periodic_date is not None:
        days_old = (ref_date - most_recent_periodic_date).days
        if days_old <= _FRESH_WINDOW_DAYS:
            freshness = "FRESH"
            # Artifact expires when the filing would become stale.
            expires_at = (
                most_recent_periodic_date + timedelta(days=_FRESH_WINDOW_DAYS)
            ).isoformat()
        else:
            freshness = "STALE"
            # No explicit expiry for STALE; STALE freshness status already signals outdated.
            expires_at = None
        source_window_end = most_recent_periodic_date.isoformat()
        # Window start: 1 fiscal year before the most recent periodic filing.
        source_window_start = (
            most_recent_periodic_date - timedelta(days=365)
        ).isoformat()
    else:
        # No parseable periodic filing date — can't classify freshness.
        freshness = "UNKNOWN"

    # ── Source fingerprint for idempotency ────────────────────────────────────
    # Differs when the set of 10-K/10-Q accession numbers changes.
    source_refs_fingerprint = _compute_source_fingerprint(
        cik=sec_result.cik or "",
        filings=sec_result.filings,
    )

    # ── Limitations — always honest ───────────────────────────────────────────
    limitations = [
        "No earnings transcript provider configured.",
        "No earnings calendar or analyst EPS estimate provider configured.",
        "No analyst guidance data available. SEC filing metadata only.",
        "SEC filing metadata does not imply analyst expectations or earnings surprises.",
        (
            f"SEC EDGAR evidence: {len(sources)} filing(s) retrieved for "
            f"{sec_result.ticker} (CIK {sec_result.cik})."
        ),
    ]

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
