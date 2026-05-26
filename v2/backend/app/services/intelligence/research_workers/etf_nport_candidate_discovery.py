"""Stage 9F.2a — ETF NPORT parent-registrant candidate discovery.

Discovers candidate parent-registrant CIKs from official SEC sources when
static seeds fail identity validation in the NPORT-P diagnostic lane.

This module is diagnostic-only:
  - Returns candidate CIKs and confidence metadata. Makes no holdings decisions.
  - Writes no artifacts. Calls no LLMs. Calls no paid APIs.
  - All HTTP calls are through injectable http_get_fn for test isolation.
  - No live SEC calls are made in CI (all tests use fixture http_get_fn).

Discovery sources (queried in order):
  sec_efts_entity   — SEC EDGAR EFTS search by parent registrant entity name.
  sec_efts_series   — SEC EDGAR EFTS search by expected fund/series name.

SEC EFTS base URL: https://efts.sec.gov/LATEST/search-index
Response format: JSON {"hits": {"hits": [{"_source": {"entity_id": "...",
                                                       "entity_name": "..."}}]}}

Confidence levels:
  confirmed_candidate  — entity name normalized-matches the registrant name hint.
  plausible_candidate  — entity name normalized-matches a series name hint.
  rejected             — entity found but name does not match any configured hint.
"""
from __future__ import annotations

import logging
import re
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# SEC EDGAR EFTS full-text search endpoint (no API key, public).
_EFTS_BASE_URL = "https://efts.sec.gov/LATEST/search-index"

# Only scan recent filings to avoid delisted or renamed entities.
_EFTS_START_DATE = "2023-01-01"

# Cap on hits returned per EFTS query (keep discovery lightweight).
_EFTS_DEFAULT_MAX_HITS = 10


@dataclass
class NportCandidateEntry:
    """One discovered candidate CIK for an ETF's parent registrant."""

    ticker: str
    candidate_cik: str          # 10-digit zero-padded CIK
    candidate_title: str        # SEC registrant/entity name from source
    candidate_source: str       # "sec_efts_entity" | "sec_efts_series"
    match_reason: str           # human-readable match explanation
    confidence: str             # "confirmed_candidate" | "plausible_candidate" | "rejected"
    rejection_reason: Optional[str] = None  # set when confidence == "rejected"


@dataclass
class NportDiscoveryResult:
    """Discovery output for one ETF ticker."""

    ticker: str
    expected_series_names: tuple[str, ...]
    existing_static_candidates: list[str]       # CIKs already in the static resolver map
    discovered_candidates: list[NportCandidateEntry]  # newly found via SEC EFTS
    discovery_sources_tried: list[str]          # source labels that were queried
    discovery_error: Optional[str] = None       # non-None if a source query failed


# ── Internal helpers ──────────────────────────────────────────────────────────


def _normalize(s: str) -> str:
    """Normalize a fund/entity name: lowercase, strip punctuation, collapse whitespace."""
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9\s]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _padded_cik(raw: Any) -> str:
    """Return CIK as 10-digit zero-padded string."""
    try:
        return str(int(str(raw))).zfill(10)
    except (ValueError, TypeError):
        return str(raw).zfill(10)


def _assess_candidate(
    entity_name: str,
    parent_registrant_hint: Optional[str],
    expected_series_names: tuple[str, ...],
    source: str,
) -> tuple[str, str, Optional[str]]:
    """Return (confidence, match_reason, rejection_reason) for one entity.

    Matching rules (conservative — false negatives acceptable, false positives not):
      1. Normalized entity name matches parent_registrant_hint → confirmed_candidate.
      2. Normalized entity name matches any expected_series_name → plausible_candidate.
      3. No match → rejected.
    """
    ent_norm = _normalize(entity_name)

    if parent_registrant_hint:
        hint_norm = _normalize(parent_registrant_hint)
        if hint_norm and (
            hint_norm == ent_norm
            or hint_norm in ent_norm
            or ent_norm in hint_norm
        ):
            return (
                "confirmed_candidate",
                (
                    f"entity_name {entity_name!r} normalized-matches "
                    f"registrant hint {parent_registrant_hint!r} (source: {source})"
                ),
                None,
            )

    for sname in expected_series_names:
        sname_norm = _normalize(sname)
        if sname_norm and (sname_norm in ent_norm or ent_norm in sname_norm):
            return (
                "plausible_candidate",
                (
                    f"entity_name {entity_name!r} substring-matches "
                    f"series hint {sname!r} (source: {source})"
                ),
                None,
            )

    rejection = (
        f"entity_name {entity_name!r} did not match registrant hint "
        f"{parent_registrant_hint!r} or series names {expected_series_names!r}"
    )
    return "rejected", rejection, rejection


def _parse_efts_hits(
    body: dict,
    source: str,
    ticker: str,
    parent_registrant_hint: Optional[str],
    expected_series_names: tuple[str, ...],
    seen_ciks: set[str],
    max_candidates: int,
) -> list[NportCandidateEntry]:
    """Parse EDGAR EFTS response JSON into NportCandidateEntry list.

    Defensive against malformed or unexpected response shapes.
    Skips CIKs already in seen_ciks (deduplication across sources).
    """
    candidates: list[NportCandidateEntry] = []
    try:
        hits = (body.get("hits") or {}).get("hits") or []
    except (AttributeError, TypeError):
        return candidates

    for hit in hits:
        if len(candidates) >= max_candidates:
            break
        if not isinstance(hit, dict):
            continue
        src = hit.get("_source") or {}
        if not isinstance(src, dict):
            continue

        raw_cik = src.get("entity_id")
        entity_name = str(src.get("entity_name") or "").strip()

        if not raw_cik or not entity_name:
            continue

        cik = _padded_cik(raw_cik)
        if cik in seen_ciks:
            continue
        seen_ciks.add(cik)

        confidence, match_reason, rejection_reason = _assess_candidate(
            entity_name, parent_registrant_hint, expected_series_names, source
        )
        candidates.append(
            NportCandidateEntry(
                ticker=ticker,
                candidate_cik=cik,
                candidate_title=entity_name,
                candidate_source=source,
                match_reason=match_reason,
                confidence=confidence,
                rejection_reason=rejection_reason,
            )
        )

    return candidates


# ── Public API ────────────────────────────────────────────────────────────────


def discover_nport_candidates(
    ticker: str,
    entry: Any,  # Optional[ETFParentRegistrantEntry] — avoid circular import
    *,
    http_get_fn: Optional[Callable] = None,
    max_candidates_per_source: int = _EFTS_DEFAULT_MAX_HITS,
) -> NportDiscoveryResult:
    """Discover candidate parent-registrant CIKs from official SEC EFTS sources.

    Args:
        ticker:                  Uppercase ETF ticker symbol.
        entry:                   ETFParentRegistrantEntry from static resolver (may be None).
        http_get_fn:             Injectable HTTP callable (signature: get(url) -> response).
                                 When None, uses httpx directly. Always injected in tests.
        max_candidates_per_source: Cap on EFTS hits per source query.

    Returns:
        NportDiscoveryResult with discovered candidates sorted by confidence.
        Never raises — errors are captured in discovery_error field.

    Live SEC calls: made via http_get_fn (injectable). Tests always inject a mock.
    """
    ticker_upper = ticker.upper().strip()

    parent_hint: Optional[str] = None
    expected_series: tuple[str, ...] = ()
    existing_static: list[str] = []

    if entry is not None:
        parent_hint = getattr(entry, "parent_name", None)
        expected_series = getattr(entry, "expected_series_names", ())
        primary_cik = getattr(entry, "parent_cik", None)
        extra_ciks = list(getattr(entry, "candidate_ciks", ()))
        if primary_cik:
            existing_static = [_padded_cik(primary_cik)] + [_padded_cik(c) for c in extra_ciks]

    discovered: list[NportCandidateEntry] = []
    sources_tried: list[str] = []
    discovery_error: Optional[str] = None
    # Exclude static CIKs from "discovered" — they are already seeds.
    seen_ciks: set[str] = set(existing_static)

    _get_fn = http_get_fn
    _client = None

    if _get_fn is None:
        try:
            import httpx  # deferred import; not needed in test paths
            _client = httpx.Client(
                timeout=15.0,
                headers={"User-Agent": "FinanceTracker/1.0 diagnostic@financetracker.app"},
            )
            _get_fn = _client.get
        except ImportError:
            return NportDiscoveryResult(
                ticker=ticker_upper,
                expected_series_names=expected_series,
                existing_static_candidates=list(existing_static),
                discovered_candidates=[],
                discovery_sources_tried=[],
                discovery_error="httpx not available for discovery",
            )

    try:
        # ── Source 1: EFTS entity search by registrant name ──────────────────
        if parent_hint:
            sources_tried.append("sec_efts_entity")
            try:
                params = {
                    "entity": parent_hint,
                    "forms": "NPORT-P",
                    "dateRange": "custom",
                    "startdt": _EFTS_START_DATE,
                }
                url = _EFTS_BASE_URL + "?" + urllib.parse.urlencode(params)
                resp = _get_fn(url)
                resp.raise_for_status()
                body = resp.json() or {}
                new_cands = _parse_efts_hits(
                    body,
                    "sec_efts_entity",
                    ticker_upper,
                    parent_hint,
                    expected_series,
                    seen_ciks,
                    max_candidates_per_source,
                )
                discovered.extend(new_cands)
            except Exception as exc:  # noqa: BLE001
                logger.debug("EFTS entity search failed for %s: %s", ticker_upper, exc)
                discovery_error = f"sec_efts_entity failed: {str(exc)[:200]}"

        # ── Source 2: EFTS full-text search by first expected series name ────
        # Cap at first series name only to limit HTTP budget (diagnostic lane).
        if expected_series:
            series_name = expected_series[0]
            sources_tried.append("sec_efts_series")
            try:
                params = {
                    "q": f'"{series_name}"',
                    "forms": "NPORT-P",
                    "dateRange": "custom",
                    "startdt": _EFTS_START_DATE,
                }
                url = _EFTS_BASE_URL + "?" + urllib.parse.urlencode(params)
                resp = _get_fn(url)
                resp.raise_for_status()
                body = resp.json() or {}
                new_cands = _parse_efts_hits(
                    body,
                    "sec_efts_series",
                    ticker_upper,
                    parent_hint,
                    expected_series,
                    seen_ciks,
                    max_candidates_per_source,
                )
                discovered.extend(new_cands)
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "EFTS series search failed for %s %r: %s",
                    ticker_upper, series_name, exc,
                )
                if discovery_error is None:
                    discovery_error = f"sec_efts_series failed: {str(exc)[:200]}"

    finally:
        if _client is not None:
            try:
                _client.close()
            except Exception:  # noqa: BLE001
                pass

    # Sort: confirmed_candidate first, then plausible_candidate, then rejected.
    _rank = {"confirmed_candidate": 0, "plausible_candidate": 1, "rejected": 2}
    discovered.sort(key=lambda c: _rank.get(c.confidence, 3))

    return NportDiscoveryResult(
        ticker=ticker_upper,
        expected_series_names=expected_series,
        existing_static_candidates=list(existing_static),
        discovered_candidates=discovered,
        discovery_sources_tried=sources_tried,
        discovery_error=discovery_error,
    )
