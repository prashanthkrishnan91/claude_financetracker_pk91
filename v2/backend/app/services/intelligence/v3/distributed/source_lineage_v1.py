"""Distributed Run Intel — versioned source-reference lineage (PR 2, patched).

Pure module (no IO, no LLM, no providers) owning everything about WHAT counts
as a validated external source reference and HOW per-axis/per-review lineage
is computed from them. Every other PR-2 module builds/consumes references
through this module only — nobody hand-rolls a reference dict or a
truthy-count lineage check elsewhere.

Two supported reference types:

  * ``provider_observation`` — a direct durable collector task's own output
    (yfinance/CoinGecko lanes). Carries only metadata the task/output itself
    established: lane, provider, ticker, an observed/as-of timestamp when the
    provider supplied one, the task id as a replay locator, and a
    deterministic digest of the substantive output. Never a fabricated URL,
    publication date, or document id.
  * ``research_artifact_source`` — one canonical ``research_artifact_sources``
    row belonging to an artifact-backed lane (SEC/ETF). An artifact id alone
    is internal storage provenance, not proof of an external source — a
    valid reference requires an actual, readable, OWNED source row.

Structural, DERIVED validation (never trust-the-persisted-status): every
manifest read back through ``parse_axis_manifest``/``output_lineage_status``
is independently re-derived from its own ``refs``/lane structure. A
manifest whose self-reported ``status`` disagrees with the structurally
derived status is malformed and reads as missing lineage — this is the only
way a downstream reader can trust ``status=="full"`` without re-deriving it
itself every time.

Legacy opaque strings (the pre-PR-2 ``intel_run_tasks.output_ref`` values)
and malformed objects never satisfy ``is_valid_reference``/
``parse_axis_manifest`` — they read as missing lineage, never as truthy.
"""
from __future__ import annotations

from typing import Any, Optional
from urllib.parse import urlsplit

from .task_contracts_v1 import (
    AXIS_CRYPTO_MARKET,
    AXIS_ETF_EXPOSURE,
    AXIS_FUNDAMENTAL,
    AXIS_REVIEW,
    AXIS_RISK_FILING,
    AXIS_SENTIMENT,
    AXIS_TECHNICAL,
    LANE_CRYPTO_MARKET,
    LANE_ETF_FUND_DATA,
    LANE_FUNDAMENTALS,
    LANE_NEWS_SENTIMENT,
    LANE_PRICE,
    LANE_SEC_CATALYST,
    LANE_SEC_COMPANY_FACTS,
    LANE_TECHNICALS,
    stable_fingerprint,
)

SCHEMA_VERSION = "source_lineage_v1"

REF_TYPE_PROVIDER_OBSERVATION = "provider_observation"
REF_TYPE_RESEARCH_ARTIFACT_SOURCE = "research_artifact_source"
ALL_REF_TYPES = frozenset(
    {REF_TYPE_PROVIDER_OBSERVATION, REF_TYPE_RESEARCH_ARTIFACT_SOURCE}
)

LINEAGE_FULL = "full"
LINEAGE_PARTIAL = "partial"
LINEAGE_MISSING = "missing"
ALL_LINEAGE_STATUSES = frozenset({LINEAGE_FULL, LINEAGE_PARTIAL, LINEAGE_MISSING})

# Non-review specialist axes / lanes a manifest may legitimately reference.
# AXIS_REVIEW is validated through a separate schema (derived_from_axes /
# missing_ref_axes), never through the lane-based axis schema.
SUPPORTED_AXES = frozenset({
    AXIS_FUNDAMENTAL, AXIS_TECHNICAL, AXIS_SENTIMENT, AXIS_RISK_FILING,
    AXIS_ETF_EXPOSURE, AXIS_CRYPTO_MARKET,
})
SUPPORTED_LANES = frozenset({
    LANE_PRICE, LANE_TECHNICALS, LANE_FUNDAMENTALS, LANE_NEWS_SENTIMENT,
    LANE_SEC_COMPANY_FACTS, LANE_SEC_CATALYST, LANE_ETF_FUND_DATA,
    LANE_CRYPTO_MARKET,
})

# Volatile/locator-only keys stripped before hashing a lane task's output —
# a fresh fetch/cache-hit timestamp must never change the substantive digest.
_VOLATILE_OUTPUT_KEYS = frozenset({"as_of", "cache_hit"})

# Storage/prompt bounds (contract §4). Deterministic sort (dedupe_references)
# is always applied before any truncation.
MAX_REFS_PER_LANE = 8
MAX_REFS_PER_MANIFEST = 24
MAX_FREE_TEXT_CHARS = 200
# Sanity ceiling for a persisted truncated_ref_count — real values are always
# small (bounded by MAX_REFS_PER_LANE * lane count); anything absurdly large
# is forged/corrupted, not a legitimate truncation count.
_MAX_REASONABLE_TRUNCATED_REF_COUNT = 10_000
# Same per-list cap the review prompt/system contract enforces
# (specialist_agents_v1.SPECIALIST_SYSTEM_PROMPT) — the review reconciles
# ALREADY-bounded specialist findings/risks, so the prompt/fingerprint view
# applies the identical cap rather than a second, driftable one.
_MAX_PROMPT_LIST_ITEMS = 2

# Axis -> lanes that axis's compact prompt bundle actually reads (mirrors
# specialist_agents_v1._compact_bundle_for_axis). Every axis's compact bundle
# also carries the unconditional ``market`` section, hence LANE_PRICE
# everywhere. This is intentionally separate from task_contracts_v1's
# AXIS_BACKING_LANES (a scheduling-gate concept owned by run_scheduler_v1) —
# PR 2 must not change scheduling, only lineage. ``supplied_lanes`` (passed
# into build_axis_lineage_manifest by specialist_agents_v1.axis_evidence_context)
# is intersected with this candidate set — it is NEVER derived from this set
# alone, and never from the bundle-wide usable_lanes list.
AXIS_CANDIDATE_LANES: dict[str, tuple[str, ...]] = {
    AXIS_FUNDAMENTAL: (
        LANE_PRICE, LANE_FUNDAMENTALS, LANE_SEC_COMPANY_FACTS, LANE_SEC_CATALYST,
    ),
    AXIS_TECHNICAL: (LANE_PRICE, LANE_TECHNICALS),
    AXIS_SENTIMENT: (LANE_PRICE, LANE_NEWS_SENTIMENT, LANE_SEC_CATALYST),
    AXIS_RISK_FILING: (
        LANE_PRICE, LANE_FUNDAMENTALS, LANE_SEC_COMPANY_FACTS, LANE_SEC_CATALYST,
    ),
    AXIS_ETF_EXPOSURE: (LANE_PRICE, LANE_TECHNICALS, LANE_ETF_FUND_DATA, LANE_FUNDAMENTALS),
    AXIS_CRYPTO_MARKET: (LANE_PRICE, LANE_CRYPTO_MARKET, LANE_TECHNICALS),
}


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _clean(item)
            for key, item in value.items()
            if key not in _VOLATILE_OUTPUT_KEYS and item not in (None, "")
        }
    if isinstance(value, list):
        return [_clean(item) for item in value]
    return value


def _cap(value: Any, limit: int = MAX_FREE_TEXT_CHARS) -> Optional[str]:
    """Bound one free-text identifier field to a fixed length (contract §4)."""
    if value in (None, ""):
        return None
    text = str(value)
    return text[:limit] if len(text) > limit else text


def output_digest(output: dict[str, Any]) -> str:
    """Deterministic digest of a lane task's substantive output.

    Excludes volatile timestamps/cache markers so the digest is stable across
    a cache-hit or a re-fetch of identical evidence. Never hashes a reference
    structure (lane task outputs never contain one) — this digest IS the
    thing a ``provider_observation`` reference identifies, so it must not
    recursively include the reference itself.
    """
    payload = {k: v for k, v in (output or {}).items() if k not in _VOLATILE_OUTPUT_KEYS}
    return stable_fingerprint(_clean(payload))


def make_provider_observation_ref(
    *, lane: str, ticker: str, task_id: Optional[str], output: dict[str, Any],
) -> Optional[dict[str, Any]]:
    """Build a ``provider_observation`` reference from one durable direct-lane
    task output. Returns None for degraded/no-data/unattributed evidence —
    never fabricates a reference."""
    if not isinstance(output, dict) or not output:
        return None
    provider = str(output.get("source") or "").strip()
    if not provider or not ticker or not task_id:
        return None
    substantive = {
        key: value
        for key, value in output.items()
        if key not in _VOLATILE_OUTPUT_KEYS and key != "source"
        and value not in (None, "", [], {})
    }
    if not substantive:
        return None
    ref: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "ref_type": REF_TYPE_PROVIDER_OBSERVATION,
        "lane": lane,
        "provider": _cap(provider),
        "ticker": ticker,
        "task_id": _cap(task_id),
        "output_digest": output_digest(output),
    }
    observed_at = output.get("as_of")
    if observed_at:
        ref["observed_at"] = _cap(observed_at)
    return ref


def make_research_artifact_source_ref(
    *, lane: str, ticker: str, artifact_id: str, source_row: dict[str, Any],
) -> Optional[dict[str, Any]]:
    """Build a ``research_artifact_source`` reference from one canonical
    ``research_artifact_sources`` row. An artifact id with no readable source
    row never qualifies — returns None (a lineage gap, not a fabrication).
    Callers own verifying the PARENT artifact's ownership/ticker-scope/
    active/substantive status before calling this — this function trusts the
    caller already did that gating."""
    if not artifact_id or not isinstance(source_row, dict):
        return None
    artifact_source_id = source_row.get("id")
    provider_name = str(source_row.get("provider_name") or "").strip()
    if not artifact_source_id or not provider_name:
        return None
    ref: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "ref_type": REF_TYPE_RESEARCH_ARTIFACT_SOURCE,
        "lane": lane,
        "provider": _cap(provider_name),
        "ticker": ticker,
        "artifact_id": _cap(str(artifact_id)),
        "artifact_source_id": _cap(str(artifact_source_id)),
    }
    for key in (
        "provider_version", "source_kind", "source_id", "source_url",
        "source_published_at", "fetched_at", "source_hash",
    ):
        value = source_row.get(key)
        if value not in (None, ""):
            ref[key] = _cap(value)
    return ref


def is_valid_reference(ref: Any) -> bool:
    """Structural validation. Legacy opaque strings and malformed dicts never
    qualify as PR-2 lineage."""
    if not isinstance(ref, dict):
        return False
    if ref.get("schema_version") != SCHEMA_VERSION:
        return False
    ref_type = ref.get("ref_type")
    if ref_type not in ALL_REF_TYPES:
        return False
    if not ref.get("lane") or not ref.get("provider") or not ref.get("ticker"):
        return False
    if ref.get("lane") not in SUPPORTED_LANES:
        return False
    if ref_type == REF_TYPE_PROVIDER_OBSERVATION:
        return bool(ref.get("task_id")) and bool(ref.get("output_digest"))
    return bool(ref.get("artifact_id")) and bool(ref.get("artifact_source_id"))


def _ref_identity(ref: dict[str, Any]) -> tuple:
    ref_type = ref.get("ref_type")
    if ref_type == REF_TYPE_PROVIDER_OBSERVATION:
        return (
            ref_type, ref.get("lane"), ref.get("ticker"), ref.get("provider"),
            ref.get("output_digest"),
        )
    return (
        ref_type, ref.get("lane"), ref.get("ticker"), ref.get("artifact_id"),
        ref.get("artifact_source_id"),
    )


def dedupe_references(refs: Optional[list[Any]]) -> list[dict[str, Any]]:
    """Deterministic de-duplication of valid references, stable-sorted."""
    valid = [r for r in (refs or []) if is_valid_reference(r)]
    by_identity: dict[tuple, dict[str, Any]] = {}
    for ref in valid:
        by_identity[_ref_identity(ref)] = ref
    return sorted(
        by_identity.values(),
        key=lambda r: (
            str(r.get("lane") or ""),
            str(r.get("ref_type") or ""),
            str(r.get("provider") or ""),
            str(r.get("ticker") or ""),
            str(r.get("task_id") or r.get("artifact_source_id") or ""),
        ),
    )


def bound_references(
    refs: Optional[list[Any]], limit: int,
) -> tuple[list[dict[str, Any]], int]:
    """Deterministically dedupe+sort, then bound to ``limit``. Returns
    ``(bounded_refs, truncated_count)`` — truncation is always disclosed,
    never silent."""
    deduped = dedupe_references(refs)
    if len(deduped) <= limit:
        return deduped, 0
    return deduped[:limit], len(deduped) - limit


def _sanitize_source_url(url: Any) -> Optional[str]:
    """Scheme+host+path only — drops query strings/fragments that may carry
    volatile tokens, so a stable document keeps a stable identity."""
    if not url:
        return None
    try:
        parts = urlsplit(str(url))
        if not parts.scheme or not parts.netloc:
            return None
        return _cap(f"{parts.scheme}://{parts.netloc}{parts.path}")
    except Exception:
        return None


def _identity_token_for_ref(ref: dict[str, Any]) -> Optional[str]:
    """A bounded, stable per-reference identity token for the prompt-safe
    projection — never a raw URL/excerpt/secret. For an artifact source,
    derived from whichever real external identity is available
    (source_id > source_hash > sanitized source_url); two DIFFERENT external
    filings under the same provider/lane always produce different tokens.
    For a non-price provider observation, derived from ``output_digest`` (a
    genuine evidence change changes the token). For the PRICE lane, no token
    is produced at all — provider/lane identification is enough, so an
    ordinary intraday tick never defeats reuse."""
    if ref.get("ref_type") == REF_TYPE_RESEARCH_ARTIFACT_SOURCE:
        raw = ref.get("source_id") or ref.get("source_hash") or _sanitize_source_url(
            ref.get("source_url")
        )
        if not raw:
            return None
        return stable_fingerprint(str(raw))[:24]
    if ref.get("ref_type") == REF_TYPE_PROVIDER_OBSERVATION:
        if ref.get("lane") == LANE_PRICE:
            return None
        digest = ref.get("output_digest")
        return stable_fingerprint(str(digest))[:24] if digest else None
    return None


def compact_projection(
    refs: Optional[list[dict[str, Any]]], *, limit: int = 8,
) -> list[dict[str, Any]]:
    """Bounded, prompt-safe projection: identity fields only, no payload —
    never asks the LLM to invent or select a citation, only to see what
    already backs its evidence. Never includes a source URL/excerpt/secret;
    ``identity_token`` (when derivable) lets a reader recognize the SAME
    underlying source across sessions without seeing its raw identity."""
    out = []
    for ref in dedupe_references(refs)[:limit]:
        entry: dict[str, Any] = {
            "lane": ref.get("lane"),
            "ref_type": ref.get("ref_type"),
            "provider": ref.get("provider"),
        }
        token = _identity_token_for_ref(ref)
        if token:
            entry["identity_token"] = token
        out.append(entry)
    return out


def source_identity_projection(ref: dict[str, Any]) -> dict[str, Any]:
    """Stable identity-only projection of ONE reference for the ANALYTICAL
    fingerprint.

    Excludes internal replay locators (task_id, artifact_id,
    artifact_source_id) and every timestamp — none of those may defeat
    cross-session specialist reuse. For a ``provider_observation`` reference
    on the PRICE lane specifically, also excludes ``output_digest`` — an
    intraday price/pct_1d tick must not invalidate every specialist output's
    fingerprint. Every OTHER lane's ``output_digest`` (technicals,
    fundamentals, news, crypto_market) is retained, so genuine evidence
    changes on those lanes still alter the fingerprint. For a
    ``research_artifact_source`` reference, retains real external source
    identity when present (source_id / source_hash / a sanitized source_url)
    instead of the internal artifact/source-row ids, so swapping which
    internal artifact row backs the SAME external filing never changes the
    fingerprint, while a genuinely different filing does.
    """
    if not isinstance(ref, dict):
        return {}
    projection: dict[str, Any] = {
        "ref_type": ref.get("ref_type"),
        "lane": ref.get("lane"),
        "provider": ref.get("provider"),
    }
    if ref.get("ref_type") == REF_TYPE_PROVIDER_OBSERVATION:
        if ref.get("lane") != LANE_PRICE:
            projection["output_digest"] = ref.get("output_digest")
    elif ref.get("ref_type") == REF_TYPE_RESEARCH_ARTIFACT_SOURCE:
        if ref.get("source_id"):
            projection["source_id"] = ref.get("source_id")
        if ref.get("source_hash"):
            projection["source_hash"] = ref.get("source_hash")
        sanitized_url = _sanitize_source_url(ref.get("source_url"))
        if sanitized_url:
            projection["source_url"] = sanitized_url
    return projection


def fingerprint_source_refs(
    source_refs_by_lane: Optional[dict[str, list[dict[str, Any]]]],
    source_ref_gaps: Optional[list[str]],
) -> dict[str, Any]:
    """Canonical, session/task-independent projection of a bundle's source
    lineage for the analytical ``input_fingerprint`` — NEVER the raw
    reference objects (which carry replay locators/timestamps/the volatile
    price digest). Retains source-reference gaps so sourced vs unsourced
    evidence is never fingerprint-equivalent."""
    return {
        "by_lane": {
            lane: [source_identity_projection(r) for r in (refs or [])]
            for lane, refs in sorted((source_refs_by_lane or {}).items())
        },
        "gaps": sorted(source_ref_gaps or []),
    }


def _is_unique_list_of(values: Any, allowed: frozenset) -> bool:
    """Fail closed for ANY malformed shape — never raises. A dict/list/None
    element (unhashable, or simply not a valid member) makes the whole list
    invalid rather than crashing the caller."""
    if not isinstance(values, list):
        return False
    if not all(isinstance(v, str) for v in values):
        return False
    if len(values) != len(set(values)):
        return False
    return all(v in allowed for v in values)


def _validate_truncated_ref_count(evidence_refs: dict[str, Any]) -> tuple[bool, int]:
    """Strict validation of a persisted ``truncated_ref_count``: absent/None
    defaults to 0; booleans, non-ints, negatives and unreasonably large
    values are all REJECTED (never silently coerced). Returns
    ``(is_valid, value)``."""
    raw = evidence_refs.get("truncated_ref_count")
    if raw is None:
        return True, 0
    if isinstance(raw, bool) or not isinstance(raw, int):
        return False, 0
    if raw < 0 or raw > _MAX_REASONABLE_TRUNCATED_REF_COUNT:
        return False, 0
    return True, raw


def _derive_axis_status(
    expected_lanes: set, linked_lanes: set, missing_ref_lanes: set,
    refs: list[dict[str, Any]],
) -> str:
    if expected_lanes and refs and linked_lanes == expected_lanes and not missing_ref_lanes:
        return LINEAGE_FULL
    if refs and linked_lanes and missing_ref_lanes:
        return LINEAGE_PARTIAL
    return LINEAGE_MISSING


def build_axis_lineage_manifest(
    *,
    axis: str,
    source_refs_by_lane: dict[str, list[Any]],
    supplied_lanes: list[str],
) -> dict[str, Any]:
    """Deterministic per-axis lineage manifest.

    ``supplied_lanes`` MUST be the exact nonempty external-evidence lanes
    actually represented in the axis's OWN compact prompt bundle (see
    ``specialist_agents_v1.axis_evidence_context``) — never the bundle-wide
    ``usable_lanes`` list. ``full`` requires at least one valid reference AND
    every expected lane linked; one supplied-but-unreferenced lane is
    ``partial``, never ``full``. ``linked_lanes``/``missing_ref_lanes`` are
    derived from the FINAL (post-truncation) reference set so a manifest
    round-trips through ``parse_axis_manifest`` self-consistently even when
    bounding drops some references.
    """
    candidate_lanes = AXIS_CANDIDATE_LANES.get(axis, ())
    supplied = set(supplied_lanes or [])
    expected_lanes = sorted(lane for lane in candidate_lanes if lane in supplied)

    all_refs: list[dict[str, Any]] = []
    for lane in expected_lanes:
        all_refs.extend(dedupe_references((source_refs_by_lane or {}).get(lane)))

    refs, truncated = bound_references(all_refs, MAX_REFS_PER_MANIFEST)
    linked_lanes = sorted({r.get("lane") for r in refs} & set(expected_lanes))
    missing_ref_lanes = sorted(set(expected_lanes) - set(linked_lanes))
    status = _derive_axis_status(
        set(expected_lanes), set(linked_lanes), set(missing_ref_lanes), refs,
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "axis": axis,
        "expected_lanes": expected_lanes,
        "linked_lanes": linked_lanes,
        "missing_ref_lanes": missing_ref_lanes,
        "status": status,
        "refs": refs,
        "truncated_ref_count": truncated,
    }


def _validate_non_review_manifest(
    evidence_refs: dict[str, Any], *, expected_axis: Optional[str],
    expected_ticker: Optional[str],
) -> Optional[dict[str, Any]]:
    axis = evidence_refs.get("axis")
    if axis not in SUPPORTED_AXES:
        return None
    if expected_axis is not None and axis != expected_axis:
        return None

    # Axis-specific enforcement: every lane this manifest touches must be one
    # of THIS axis's own candidate lanes — a "technical" manifest can never
    # legitimately carry a fundamentals/news/SEC lane, a "sentiment" manifest
    # can never carry technicals/fundamentals, etc. Never the wider
    # SUPPORTED_LANES set.
    axis_lane_set = frozenset(AXIS_CANDIDATE_LANES.get(axis, ()))

    expected_lanes = evidence_refs.get("expected_lanes")
    linked_lanes = evidence_refs.get("linked_lanes")
    missing_ref_lanes = evidence_refs.get("missing_ref_lanes")
    if not _is_unique_list_of(expected_lanes, axis_lane_set):
        return None
    if not _is_unique_list_of(linked_lanes, axis_lane_set):
        return None
    if not _is_unique_list_of(missing_ref_lanes, axis_lane_set):
        return None
    linked_set, missing_set, expected_set = (
        set(linked_lanes), set(missing_ref_lanes), set(expected_lanes),
    )
    if linked_set & missing_set:
        return None
    if linked_set | missing_set != expected_set:
        return None

    raw_refs = evidence_refs.get("refs")
    if not isinstance(raw_refs, list):
        return None
    if len(raw_refs) > MAX_REFS_PER_MANIFEST:
        # Persisted manifests must already be bounded — a manifest claiming
        # more raw refs than the storage bound allows is malformed.
        return None
    valid_refs: list[dict[str, Any]] = []
    for ref in raw_refs:
        if not is_valid_reference(ref):
            return None
        if ref.get("lane") not in axis_lane_set:
            return None
        if ref.get("lane") not in linked_set:
            return None
        if expected_ticker is not None and ref.get("ticker") != expected_ticker:
            return None
        valid_refs.append(ref)
    lanes_with_ref = {r.get("lane") for r in valid_refs}
    if not linked_set.issubset(lanes_with_ref):
        return None

    derived_status = _derive_axis_status(expected_set, linked_set, missing_set, valid_refs)
    persisted_status = evidence_refs.get("status")
    if persisted_status != derived_status:
        return None

    truncated_ok, truncated = _validate_truncated_ref_count(evidence_refs)
    if not truncated_ok:
        return None

    return {
        "schema_version": SCHEMA_VERSION,
        "axis": axis,
        "expected_lanes": sorted(expected_set),
        "linked_lanes": sorted(linked_set),
        "missing_ref_lanes": sorted(missing_set),
        "status": derived_status,
        "refs": dedupe_references(valid_refs),
        "truncated_ref_count": truncated,
    }


def _validate_review_manifest(
    evidence_refs: dict[str, Any], *, expected_ticker: Optional[str],
) -> Optional[dict[str, Any]]:
    if evidence_refs.get("axis") != AXIS_REVIEW:
        return None

    # input_axis_lineage is the ONE source of truth for derived_from_axes/
    # missing_ref_axes/status below — none of those three may be
    # independently hand-authored; every one is re-derived here and any
    # persisted disagreement makes the whole manifest malformed.
    input_axis_lineage = evidence_refs.get("input_axis_lineage")
    if not isinstance(input_axis_lineage, list) or not input_axis_lineage:
        return None
    seen_axes: set[str] = set()
    normalized_lineage: list[dict[str, str]] = []
    for entry in input_axis_lineage:
        if not isinstance(entry, dict):
            return None
        entry_axis = entry.get("axis")
        entry_status = entry.get("status")
        if entry_axis not in SUPPORTED_AXES:
            return None
        if entry_axis in seen_axes:
            return None
        if entry_status not in ALL_LINEAGE_STATUSES:
            return None
        seen_axes.add(entry_axis)
        normalized_lineage.append({"axis": entry_axis, "status": entry_status})
    normalized_lineage.sort(key=lambda e: e["axis"])

    derived_from_axes = evidence_refs.get("derived_from_axes")
    missing_ref_axes = evidence_refs.get("missing_ref_axes")
    if not _is_unique_list_of(derived_from_axes, SUPPORTED_AXES) or not derived_from_axes:
        return None
    if not _is_unique_list_of(missing_ref_axes, SUPPORTED_AXES):
        return None
    if set(derived_from_axes) != seen_axes:
        return None
    expected_missing = {e["axis"] for e in normalized_lineage if e["status"] != LINEAGE_FULL}
    if set(missing_ref_axes) != expected_missing:
        return None

    raw_refs = evidence_refs.get("refs")
    if not isinstance(raw_refs, list):
        return None
    if len(raw_refs) > MAX_REFS_PER_MANIFEST:
        return None
    valid_refs: list[dict[str, Any]] = []
    for ref in raw_refs:
        if not is_valid_reference(ref):
            return None
        if expected_ticker is not None and ref.get("ticker") != expected_ticker:
            return None
        valid_refs.append(ref)

    if valid_refs and not missing_ref_axes:
        derived_status = LINEAGE_FULL
    elif valid_refs and missing_ref_axes:
        derived_status = LINEAGE_PARTIAL
    else:
        derived_status = LINEAGE_MISSING
    persisted_status = evidence_refs.get("status")
    if persisted_status != derived_status:
        return None

    truncated_ok, truncated = _validate_truncated_ref_count(evidence_refs)
    if not truncated_ok:
        return None

    return {
        "schema_version": SCHEMA_VERSION,
        "axis": AXIS_REVIEW,
        "input_axis_lineage": normalized_lineage,
        "derived_from_axes": sorted(set(derived_from_axes)),
        "missing_ref_axes": sorted(set(missing_ref_axes)),
        "status": derived_status,
        "refs": dedupe_references(valid_refs),
        "truncated_ref_count": truncated,
    }


def validate_review_against_current_outputs(
    review_evidence_refs: Any, *, ticker: str,
    current_non_review_outputs: list[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    """Cross-validates a persisted review manifest against the CURRENT valid
    non-review specialist outputs for the same ticker (not merely the
    review's own self-consistent structure). A review claiming it reconciled
    a different axis set, or a different per-axis lineage status, than what
    is CURRENTLY true for this ticker is stale/forged — read as missing
    lineage, never full. ``current_non_review_outputs`` should already be the
    caller's valid (score+confidence present) output rows for this ticker.
    """
    manifest = parse_axis_manifest(
        review_evidence_refs, expected_axis=AXIS_REVIEW, expected_ticker=ticker,
    )
    if manifest is None:
        return None
    current_by_axis: dict[str, str] = {}
    for output in current_non_review_outputs:
        axis = output.get("axis")
        if axis not in SUPPORTED_AXES:
            continue
        current_by_axis[axis] = output_lineage_status(
            output.get("evidence_refs"), expected_axis=axis, expected_ticker=ticker,
        )
    if set(manifest.get("derived_from_axes") or []) != set(current_by_axis):
        return None
    claimed_by_axis = {
        entry.get("axis"): entry.get("status")
        for entry in (manifest.get("input_axis_lineage") or [])
    }
    for axis, current_status in current_by_axis.items():
        if claimed_by_axis.get(axis) != current_status:
            return None
    return manifest


def parse_axis_manifest(
    evidence_refs: Any, *, expected_axis: Optional[str] = None,
    expected_ticker: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Parse+STRUCTURALLY VALIDATE a persisted ``evidence_refs`` JSONB value.

    Never trusts the persisted ``status`` field — it is independently
    re-derived from the manifest's own lane/reference structure, and a
    disagreement between the persisted and derived status makes the whole
    manifest malformed (returns None, read as missing lineage by every
    caller). Legacy opaque-string lists, empty lists, malformed dicts, or
    anything missing the PR-2 schema version also return None.

    ``expected_axis``/``expected_ticker``, when given, must match the
    manifest's own axis and every one of its references' ticker — a
    manifest that claims a different axis, or carries a reference for a
    DIFFERENT ticker, is rejected rather than silently trusted.
    """
    if not isinstance(evidence_refs, dict):
        return None
    if evidence_refs.get("schema_version") != SCHEMA_VERSION:
        return None
    axis = evidence_refs.get("axis")
    if axis == AXIS_REVIEW:
        if expected_axis is not None and expected_axis != AXIS_REVIEW:
            return None
        return _validate_review_manifest(evidence_refs, expected_ticker=expected_ticker)
    return _validate_non_review_manifest(
        evidence_refs, expected_axis=expected_axis, expected_ticker=expected_ticker,
    )


def output_lineage_status(
    evidence_refs: Any, *, expected_axis: Optional[str] = None,
    expected_ticker: Optional[str] = None,
) -> str:
    """Structural lineage status of one persisted output's ``evidence_refs``.
    Malformed objects, legacy opaque strings, and a persisted status that
    disagrees with the structurally derived status are all ``missing`` —
    never truthy merely for being nonempty."""
    manifest = parse_axis_manifest(
        evidence_refs, expected_axis=expected_axis, expected_ticker=expected_ticker,
    )
    if manifest is None:
        return LINEAGE_MISSING
    return manifest.get("status") or LINEAGE_MISSING


def build_review_lineage_manifest(
    reviewed_inputs: list[dict[str, Any]], *, ticker: str,
) -> dict[str, Any]:
    """Derived-lineage manifest for a conflict review.

    A review never fabricates a new source — it unions and deduplicates the
    valid external references of every non-review specialist input it
    reconciled (each input's OWN lineage independently re-validated, never
    trusted from its persisted status). ``input_axis_lineage`` is the ONE
    source of truth for ``derived_from_axes``/``missing_ref_axes``/
    ``status`` — none of those three is independently hand-authored.
    ``full`` only when every reviewed input's own lineage was structurally
    ``full`` AND at least one valid reference survives bounding.
    """
    input_axis_lineage: list[dict[str, str]] = []
    seen_axes: set[str] = set()
    all_refs: list[dict[str, Any]] = []
    for item in reviewed_inputs:
        axis = str(item.get("axis") or "")
        if not axis or axis in seen_axes:
            continue
        seen_axes.add(axis)
        manifest = parse_axis_manifest(
            item.get("evidence_refs"),
            expected_axis=axis if axis in SUPPORTED_AXES else None,
            expected_ticker=ticker,
        )
        status = manifest.get("status") if manifest else LINEAGE_MISSING
        refs = manifest.get("refs") if manifest else []
        if refs:
            all_refs.extend(refs)
        input_axis_lineage.append({"axis": axis, "status": status})

    input_axis_lineage.sort(key=lambda e: e["axis"])
    derived_from_axes = sorted(e["axis"] for e in input_axis_lineage)
    missing_ref_axes = sorted(
        e["axis"] for e in input_axis_lineage if e["status"] != LINEAGE_FULL
    )

    refs, truncated = bound_references(all_refs, MAX_REFS_PER_MANIFEST)
    if refs and not missing_ref_axes:
        status = LINEAGE_FULL
    elif refs and missing_ref_axes:
        status = LINEAGE_PARTIAL
    else:
        status = LINEAGE_MISSING

    return {
        "schema_version": SCHEMA_VERSION,
        "axis": AXIS_REVIEW,
        "input_axis_lineage": input_axis_lineage,
        "derived_from_axes": derived_from_axes,
        "missing_ref_axes": missing_ref_axes,
        "status": status,
        "refs": refs,
        "truncated_ref_count": truncated,
    }


def build_review_prompt_context(
    reviewed_inputs: list[dict[str, Any]], *, ticker: str,
) -> list[dict[str, Any]]:
    """The ONE normalized, bounded list used for BOTH the review LLM prompt
    (``json.dumps``) and ``review_input_fingerprint`` — no independent
    re-sorting or alternate representation anywhere else in the codebase.

    Filters to valid (score+confidence present) non-review inputs, dedupes to
    one entry per axis, sorted deterministically BY AXIS (so the database's
    row-return order never affects the prompt or its fingerprint). Each
    axis's own manifest is independently re-validated here (never trusted
    from its persisted status). Findings/risks are bounded to the same
    per-list cap the actual specialist prompt enforces, with their original
    (semantic) order preserved — never re-sorted.
    """
    seen_axes: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for item in reviewed_inputs:
        axis = str(item.get("axis") or "")
        if (
            not axis or axis in seen_axes
            or item.get("score") is None or item.get("confidence") is None
        ):
            continue
        seen_axes.add(axis)
        deduped.append(item)

    context: list[dict[str, Any]] = []
    for item in sorted(deduped, key=lambda i: str(i.get("axis") or "")):
        axis = str(item.get("axis") or "")
        manifest = parse_axis_manifest(
            item.get("evidence_refs"),
            expected_axis=axis if axis in SUPPORTED_AXES else None,
            expected_ticker=ticker,
        )
        status = manifest.get("status") if manifest else LINEAGE_MISSING
        linked_lanes = list(manifest.get("linked_lanes") or []) if manifest else []
        missing_ref_lanes = list(manifest.get("missing_ref_lanes") or []) if manifest else []
        refs = manifest.get("refs") if manifest else []
        findings = [str(f) for f in (item.get("key_findings") or [])][:_MAX_PROMPT_LIST_ITEMS]
        risks = [str(r) for r in (item.get("risks") or [])][:_MAX_PROMPT_LIST_ITEMS]
        context.append({
            "axis": axis,
            "stance": item.get("stance"),
            "score": item.get("score"),
            "confidence": item.get("confidence"),
            "key_findings": findings,
            "risks": risks,
            "lineage_status": status,
            "linked_lanes": linked_lanes,
            "missing_ref_lanes": missing_ref_lanes,
            "evidence_sources": compact_projection(refs),
        })
    return context


def review_input_fingerprint(
    prompt_inputs: list[dict[str, Any]], *, ticker: str, prompt_version: str,
) -> str:
    """Deterministic fingerprint of the EXACT bounded prompt input a review
    saw (see ``build_review_prompt_context``) — replaces the previous
    always-empty ``input_fingerprint`` string. Changes when any reviewed
    finding, risk, score, confidence, lineage status, missing lane or source
    identity visible to the review changes; stable to input ORDER (the
    entries are independently re-sorted here too) and to the ticker/
    prompt_version staying the same. Finding/risk order WITHIN one axis is
    preserved (never re-sorted) — only DB row order across axes is
    order-independent.
    """
    normalized = sorted(
        (
            str(item.get("axis") or ""),
            item.get("stance"),
            item.get("score"),
            item.get("confidence"),
            tuple(str(f) for f in (item.get("key_findings") or [])),
            tuple(str(r) for r in (item.get("risks") or [])),
            str(item.get("lineage_status") or ""),
            tuple(sorted(item.get("linked_lanes") or [])),
            tuple(sorted(item.get("missing_ref_lanes") or [])),
            tuple(sorted(
                (s.get("lane"), s.get("ref_type"), s.get("provider"), s.get("identity_token"))
                for s in (item.get("evidence_sources") or [])
            )),
        )
        for item in prompt_inputs
    )
    return stable_fingerprint({
        "ticker": ticker, "prompt_version": prompt_version, "inputs": normalized,
    })
