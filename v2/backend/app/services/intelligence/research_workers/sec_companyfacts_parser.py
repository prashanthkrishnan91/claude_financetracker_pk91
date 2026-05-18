"""Phase 7A — SEC CompanyFacts XBRL parser for bounded metric observations.

Parses the SEC EDGAR CompanyFacts API response to extract a bounded set of
safe, source-linked financial metric observations from the us-gaap taxonomy.

Hard constraints (non-negotiable):
  - No forbidden payload keys (action, recommendation, target_price, etc.).
  - Does NOT compute ratios, annualize, normalize, or infer value direction.
  - Does NOT compare year-over-year in Phase 7A (raw observations only).
  - Does NOT infer "good" or "bad" from metric values.
  - Only extracts facts whose accession number matches a known SourceRecord set.
  - Fail-closed on malformed input — always returns CompanyFactsParseResult.
  - Bounded output: _MAX_PERIODS_PER_TAG most recent periods per tag.
  - Source-linked: only includes facts with matching accession in source_accessions.
  - Never fabricates missing facts.
  - Never runs on page load — called only from research worker pipeline.

Stage 5H.2 — XBRL duration identity preservation:
  SEC 10-Q filings report the same metric (e.g., Revenue) for the same
  fy+fp twice: once as the 3-month quarterly figure and once as the 9-month
  YTD figure. Both share the same accn/fy/fp/filed but differ in start date.
  These represent DIFFERENT XBRL duration dimensions and must NOT be treated
  as contradictions. The parser:
    - Preserves period_start, period_end, frame from each XBRL entry.
    - Deduplicates only exact-identity entries: same (accn, fy, fp, start, end).
      Different start/end under the same accn/fy/fp are kept as distinct
      observations (quarterly and YTD are legitimately different measurements).
    - The adapter then includes duration in the FactRecord.period string so
      the contradiction detector sees different group keys for different durations.

Phase 14C.2 — FY EPS coverage selection policy:
  For EPS tags only, after applying the generic latest-N policy, the parser
  also retains the latest annual FY observation if it is not already in the
  selection. This prevents the FY 10-K EPS from being dropped when the two
  most recently filed observations are Q1/Q2/Q3 quarterly 10-Qs.

  Rules:
    - A FY annual entry is one where fp=="FY" OR (fp absent AND form=="10-K").
    - Additive only: the generic latest-N entries are always kept unchanged.
    - Deduplication by accession_number — no duplicate observations.
    - No annualization of quarterly EPS values.
    - Non-EPS tags use the generic latest-N policy without modification.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Metric tag allowlist (us-gaap only) ───────────────────────────────────────
# Only tags in this allowlist will be extracted.
# Custom taxonomy tags and any tag not in this set are silently skipped.
_METRIC_TAG_ALLOWLIST: frozenset[str] = frozenset({
    "Revenues",
    "SalesRevenueNet",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "NetIncomeLoss",
    "OperatingIncomeLoss",
    "EarningsPerShareBasic",
    "EarningsPerShareDiluted",
    "CashAndCashEquivalentsAtCarryingValue",
    "Assets",
    "Liabilities",
    "StockholdersEquity",
    "NetCashProvidedByUsedInOperatingActivities",
    "PaymentsToAcquirePropertyPlantAndEquipment",
})

# EPS tags require USD/shares unit; all other allowlisted tags require USD.
_EPS_TAGS: frozenset[str] = frozenset({"EarningsPerShareBasic", "EarningsPerShareDiluted"})

# Allowed unit per tag — wrong unit is rejected.
_ALLOWED_UNIT_BY_TAG: dict[str, str] = {
    tag: ("USD/shares" if tag in _EPS_TAGS else "USD")
    for tag in _METRIC_TAG_ALLOWLIST
}

# Only periodic financial reports are included (not event-only 8-K filings).
_ALLOWED_FORMS: frozenset[str] = frozenset({"10-K", "10-Q"})

# Max observations to keep per tag (latest fiscal periods first).
_MAX_PERIODS_PER_TAG: int = 2

# SEC CompanyFacts taxonomy key.
_US_GAAP_TAXONOMY = "us-gaap"


@dataclass
class MetricObservation:
    """One bounded XBRL metric observation — safe, source-linked, machine-readable.

    Contains only machine-safe observation fields.
    No decision-authority keys (action, recommendation, target_price, etc.).
    No inferred direction, ratio, or valuation.

    Duration identity fields (period_start, period_end, frame) are preserved
    from the raw XBRL entry so the adapter can build duration-aware period
    strings for contradiction grouping. Different start/end under the same
    fy+fp represent distinct measurement windows (quarterly vs YTD) and must
    not be collapsed.
    """
    taxonomy: str                        # always "us-gaap"
    tag: str                             # e.g. "Revenues"
    label: str                           # human-readable label from taxonomy
    value: Any                           # numeric (int or float)
    unit: str                            # "USD" or "USD/shares"
    form: str                            # "10-K" or "10-Q"
    fiscal_year: Optional[int]           # fy field, or None if absent
    fiscal_period: Optional[str]         # fp field (e.g. "FY", "Q1", "Q2"), or None
    filed: str                           # filing date ISO string (YYYY-MM-DD)
    accession_number: str                # e.g. "0000320193-23-000054"
    period_start: Optional[str] = None  # XBRL "start" date (ISO string), if present
    period_end: Optional[str] = None    # XBRL "end" date (ISO string), if present
    frame: Optional[str] = None         # XBRL "frame" value if present (e.g. "CY2023Q3I")


@dataclass
class CompanyFactsParseResult:
    """Result of parsing one SEC CompanyFacts API response.

    parse_status values:
      success     — at least one observation extracted and source-linked.
      no_facts    — parsed OK but no observations matched all constraints.
      error       — malformed input or exception; observations is empty.
      not_fetched — companyfacts was not attempted (request cap or upstream error).

    Phase 14C.2 counters (EPS FY coverage policy):
      fy_eps_added_beyond_generic_limit_count — number of FY annual EPS
        observations added beyond the generic latest-N limit, to ensure the
        most recent FY EPS is retained when only quarterly observations fill
        the generic slots.
    """
    observations: list[MetricObservation] = field(default_factory=list)
    parse_status: str = "not_fetched"
    error_message: Optional[str] = None
    tags_found: list[str] = field(default_factory=list)
    fy_eps_added_beyond_generic_limit_count: int = 0

    @property
    def observation_count(self) -> int:
        return len(self.observations)

    @property
    def is_success(self) -> bool:
        return self.parse_status == "success"


def parse_companyfacts(
    raw_json: Any,
    source_accessions: frozenset[str],
    max_periods_per_tag: int = _MAX_PERIODS_PER_TAG,
) -> CompanyFactsParseResult:
    """Extract bounded metric observations from a CompanyFacts API JSON payload.

    Args:
        raw_json:            Parsed JSON dict from companyfacts/CIK{cik}.json.
        source_accessions:   Frozenset of accession numbers that have SourceRecords.
                             Only facts whose accn is in this set are included.
        max_periods_per_tag: Max observations per tag (latest filed dates first).

    Returns:
        CompanyFactsParseResult — always. Never raises.
    """
    try:
        return _parse(raw_json, source_accessions, max_periods_per_tag)
    except Exception as exc:  # noqa: BLE001
        logger.error("sec_companyfacts_parser unexpected error=%s", exc)
        return CompanyFactsParseResult(
            parse_status="error",
            error_message=f"parser_exception: {type(exc).__name__}: {exc}",
        )


def compute_metric_digest(observations: list[MetricObservation]) -> str:
    """Deterministic SHA-256 digest of a list of MetricObservations.

    Same observations in any order produce the same digest.
    Any change to value, accession number, fiscal period, duration, or filed date
    produces a different digest.
    """
    entries = sorted(
        [
            {
                "tag": o.tag,
                "accession_number": o.accession_number,
                "value": o.value,
                "unit": o.unit,
                "fiscal_year": o.fiscal_year,
                "fiscal_period": o.fiscal_period if o.fiscal_period is not None else "",
                "filed": o.filed,
                "period_start": o.period_start if o.period_start is not None else "",
                "period_end": o.period_end if o.period_end is not None else "",
            }
            for o in observations
        ],
        key=lambda x: (x["tag"], x["accession_number"], x["fiscal_period"],
                       x["period_start"], x["period_end"]),
    )
    raw = json.dumps(entries, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


# ── Internal parsing logic ────────────────────────────────────────────────────

def _is_fy_annual_entry(entry: dict) -> bool:
    """Return True if entry represents a fiscal-year annual observation.

    FY annual = fiscal_period=="FY"  OR  (fiscal_period absent AND form=="10-K").
    This mirrors the detection logic in eps_payload_extractor_v1.py shapes A/B/C.
    """
    fp_raw = entry.get("fp")
    fp = str(fp_raw).strip().upper() if fp_raw is not None else None
    form = str(entry.get("form") or "").upper().strip()
    return fp == "FY" or (fp is None and form == "10-K")


def _parse(
    raw_json: Any,
    source_accessions: frozenset[str],
    max_periods_per_tag: int,
) -> CompanyFactsParseResult:
    """Core parsing logic — caller wraps in try/except."""
    if not isinstance(raw_json, dict):
        return CompanyFactsParseResult(
            parse_status="error",
            error_message="companyfacts response is not a dict",
        )

    facts_root = raw_json.get("facts")
    if not isinstance(facts_root, dict):
        return CompanyFactsParseResult(
            parse_status="error",
            error_message="companyfacts.facts missing or not a dict",
        )

    us_gaap = facts_root.get(_US_GAAP_TAXONOMY)
    if not isinstance(us_gaap, dict):
        return CompanyFactsParseResult(
            parse_status="no_facts",
            error_message="no us-gaap taxonomy in companyfacts",
        )

    observations: list[MetricObservation] = []
    tags_found: list[str] = []
    fy_eps_added_beyond_generic_limit_count: int = 0

    # Iterate tags in sorted order for determinism.
    for tag in sorted(_METRIC_TAG_ALLOWLIST):
        tag_data = us_gaap.get(tag)
        if not isinstance(tag_data, dict):
            continue  # tag not present — skip silently

        allowed_unit = _ALLOWED_UNIT_BY_TAG.get(tag)
        if not allowed_unit:
            continue  # should not happen given allowlist, but be safe

        label = str(tag_data.get("label") or tag)
        units_data = tag_data.get("units")
        if not isinstance(units_data, dict):
            continue

        unit_entries = units_data.get(allowed_unit)
        if not isinstance(unit_entries, list) or not unit_entries:
            continue  # wrong/missing unit type — skip

        # Filter entries: allowed form, source-linked accession, numeric value.
        candidates = []
        for entry in unit_entries:
            if not isinstance(entry, dict):
                continue
            form = str(entry.get("form") or "").upper().strip()
            if form not in _ALLOWED_FORMS:
                continue  # 8-K and custom forms excluded
            accn = str(entry.get("accn") or "").strip()
            if not accn or accn not in source_accessions:
                continue  # not source-linked — skip per spec
            val = entry.get("val")
            if val is None or not isinstance(val, (int, float)):
                continue  # non-numeric value — skip
            filed = str(entry.get("filed") or "").strip()
            if not filed:
                continue  # no filed date — skip
            candidates.append(entry)

        if not candidates:
            continue

        # Deduplicate exact-identity entries only: same (accn, fy, fp, start, end).
        # Different start/end under the same accn/fy/fp are DISTINCT XBRL duration
        # dimensions (e.g., Q3 quarterly 3-month vs Q3 YTD 9-month) and are both
        # preserved. The adapter encodes duration in the FactRecord.period string so
        # the contradiction detector sees separate group keys for each duration.
        seen_exact: set[tuple] = set()
        deduped: list[dict] = []
        for entry in candidates:
            exact_key = (
                str(entry.get("accn") or ""),
                entry.get("fy"),
                entry.get("fp"),
                str(entry.get("start") or ""),
                str(entry.get("end") or ""),
            )
            if exact_key not in seen_exact:
                seen_exact.add(exact_key)
                deduped.append(entry)
        candidates = deduped

        # Sort by filed date descending (most recent first).
        candidates.sort(key=lambda e: str(e.get("filed") or ""), reverse=True)

        # Generic latest-N selection (unchanged behavior for all tags).
        selected = candidates[:max_periods_per_tag]

        # Phase 14C.2 — FY EPS coverage policy (EPS tags only, additive).
        # If none of the generic-selected observations is a FY annual, find
        # the latest FY annual in the full candidate list and append it.
        # This prevents quarterly Q1/Q2/Q3 observations from crowding out the
        # FY 10-K EPS observation when max_periods_per_tag is small.
        # Rules: no annualization, no fabrication, dedup by accession_number.
        if tag in _EPS_TAGS:
            has_fy_in_selected = any(_is_fy_annual_entry(e) for e in selected)
            if not has_fy_in_selected:
                selected_accns: frozenset[str] = frozenset(
                    str(e.get("accn") or "") for e in selected
                )
                for candidate in candidates:
                    accn_c = str(candidate.get("accn") or "")
                    if accn_c in selected_accns:
                        continue  # already in selection
                    if _is_fy_annual_entry(candidate):
                        # candidates is sorted by filed desc, so first FY hit
                        # is the most recently filed FY annual observation.
                        selected.append(candidate)
                        fy_eps_added_beyond_generic_limit_count += 1
                        break  # one FY annual per tag is sufficient

        tags_found.append(tag)

        for entry in selected:
            fy_raw = entry.get("fy")
            fp_raw = entry.get("fp")
            start_raw = entry.get("start")
            end_raw = entry.get("end")
            frame_raw = entry.get("frame")
            observations.append(MetricObservation(
                taxonomy=_US_GAAP_TAXONOMY,
                tag=tag,
                label=label,
                value=entry["val"],
                unit=allowed_unit,
                form=str(entry.get("form") or "").upper().strip(),
                fiscal_year=int(fy_raw) if fy_raw is not None else None,
                fiscal_period=str(fp_raw) if fp_raw is not None else None,
                filed=str(entry.get("filed") or ""),
                accession_number=str(entry.get("accn") or ""),
                period_start=str(start_raw) if start_raw is not None else None,
                period_end=str(end_raw) if end_raw is not None else None,
                frame=str(frame_raw) if frame_raw is not None else None,
            ))

    parse_status = "success" if observations else "no_facts"
    return CompanyFactsParseResult(
        observations=observations,
        parse_status=parse_status,
        tags_found=sorted(set(tags_found)),  # sorted for determinism
        fy_eps_added_beyond_generic_limit_count=fy_eps_added_beyond_generic_limit_count,
    )
