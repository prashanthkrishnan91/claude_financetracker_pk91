"""Distributed Run Intel — versioned source-reference lineage (PR 2).

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
    valid reference requires an actual, readable source row.

Legacy opaque strings (the pre-PR-2 ``intel_run_tasks.output_ref`` values)
and malformed objects never satisfy ``is_valid_reference``/
``parse_axis_manifest`` — they read as missing lineage, never as truthy.
"""
from __future__ import annotations

from typing import Any, Optional

from .task_contracts_v1 import (
    AXIS_CRYPTO_MARKET,
    AXIS_ETF_EXPOSURE,
    AXIS_FUNDAMENTAL,
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

# Volatile/locator-only keys stripped before hashing a lane task's output —
# a fresh fetch/cache-hit timestamp must never change the substantive digest.
_VOLATILE_OUTPUT_KEYS = frozenset({"as_of", "cache_hit"})

# Axis -> lanes that axis's compact prompt bundle actually reads (mirrors
# specialist_agents_v1._compact_bundle_for_axis). Every axis's compact bundle
# also carries the unconditional ``market`` section, hence LANE_PRICE
# everywhere. This is intentionally separate from task_contracts_v1's
# AXIS_BACKING_LANES (a scheduling-gate concept owned by run_scheduler_v1) —
# PR 2 must not change scheduling, only lineage.
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
        "provider": provider,
        "ticker": ticker,
        "task_id": str(task_id),
        "output_digest": output_digest(output),
    }
    observed_at = output.get("as_of")
    if observed_at:
        ref["observed_at"] = observed_at
    return ref


def make_research_artifact_source_ref(
    *, lane: str, ticker: str, artifact_id: str, source_row: dict[str, Any],
) -> Optional[dict[str, Any]]:
    """Build a ``research_artifact_source`` reference from one canonical
    ``research_artifact_sources`` row. An artifact id with no readable source
    row never qualifies — returns None (a lineage gap, not a fabrication)."""
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
        "provider": provider_name,
        "ticker": ticker,
        "artifact_id": str(artifact_id),
        "artifact_source_id": str(artifact_source_id),
    }
    for key in (
        "provider_version", "source_kind", "source_id", "source_url",
        "source_published_at", "fetched_at", "source_hash",
    ):
        value = source_row.get(key)
        if value not in (None, ""):
            ref[key] = value
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


def compact_projection(
    refs: Optional[list[dict[str, Any]]], *, limit: int = 8,
) -> list[dict[str, Any]]:
    """Bounded, prompt-safe projection: identity fields only, no payload —
    never asks the LLM to invent or select a citation, only to see what
    already backs its evidence."""
    return [
        {
            "lane": ref.get("lane"),
            "ref_type": ref.get("ref_type"),
            "provider": ref.get("provider"),
        }
        for ref in dedupe_references(refs)[:limit]
    ]


def build_axis_lineage_manifest(
    *,
    axis: str,
    source_refs_by_lane: dict[str, list[Any]],
    usable_lanes: list[str],
) -> dict[str, Any]:
    """Deterministic per-axis lineage manifest.

    Derives the exact evidence lanes actually supplied to this axis (its
    candidate lanes intersected with the bundle's own usable-lane set) — this
    is never the whole-bundle reference list. ``full`` requires at least one
    valid reference AND every expected lane linked; one supplied-but-
    unreferenced lane makes it ``partial``, never ``full``.
    """
    candidate_lanes = AXIS_CANDIDATE_LANES.get(axis, ())
    usable = set(usable_lanes or [])
    expected_lanes = sorted(lane for lane in candidate_lanes if lane in usable)

    linked_lanes: list[str] = []
    all_refs: list[dict[str, Any]] = []
    for lane in expected_lanes:
        lane_refs = dedupe_references((source_refs_by_lane or {}).get(lane))
        if lane_refs:
            linked_lanes.append(lane)
            all_refs.extend(lane_refs)

    refs = dedupe_references(all_refs)
    missing_ref_lanes = sorted(set(expected_lanes) - set(linked_lanes))

    if not expected_lanes or not refs:
        status = LINEAGE_MISSING
    elif not missing_ref_lanes:
        status = LINEAGE_FULL
    else:
        status = LINEAGE_PARTIAL

    return {
        "schema_version": SCHEMA_VERSION,
        "axis": axis,
        "expected_lanes": expected_lanes,
        "linked_lanes": sorted(linked_lanes),
        "missing_ref_lanes": missing_ref_lanes,
        "status": status,
        "refs": refs,
    }


def parse_axis_manifest(evidence_refs: Any) -> Optional[dict[str, Any]]:
    """Parse a persisted ``evidence_refs`` JSONB value into a validated
    manifest, or None. Legacy opaque-string lists, empty lists, malformed
    dicts, or anything missing the PR-2 schema version all return None —
    callers treat None as missing lineage, never as truthy-nonempty."""
    if not isinstance(evidence_refs, dict):
        return None
    if evidence_refs.get("schema_version") != SCHEMA_VERSION:
        return None
    status = evidence_refs.get("status")
    if status not in ALL_LINEAGE_STATUSES:
        return None
    refs = evidence_refs.get("refs")
    if not isinstance(refs, list):
        return None
    return {
        "schema_version": SCHEMA_VERSION,
        "axis": evidence_refs.get("axis"),
        "expected_lanes": list(evidence_refs.get("expected_lanes") or []),
        "linked_lanes": list(evidence_refs.get("linked_lanes") or []),
        "missing_ref_lanes": list(evidence_refs.get("missing_ref_lanes") or []),
        "status": status,
        "refs": [r for r in refs if is_valid_reference(r)],
        "derived_from_axes": list(evidence_refs.get("derived_from_axes") or []),
        "missing_ref_axes": list(evidence_refs.get("missing_ref_axes") or []),
    }


def output_lineage_status(evidence_refs: Any) -> str:
    """Structural lineage status of one persisted output's ``evidence_refs``.
    Malformed objects and legacy opaque strings are ``missing`` — never
    truthy merely for being nonempty."""
    manifest = parse_axis_manifest(evidence_refs)
    if manifest is None:
        return LINEAGE_MISSING
    return manifest.get("status") or LINEAGE_MISSING


def build_review_lineage_manifest(
    reviewed_inputs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Derived-lineage manifest for a conflict review.

    A review never fabricates a new source — it unions and deduplicates the
    valid external references of every non-review specialist input it
    reconciled. ``full`` only when every reviewed input's own lineage was
    ``full`` AND at least one valid reference exists overall.
    """
    derived_from_axes = sorted({str(i.get("axis")) for i in reviewed_inputs if i.get("axis")})
    missing_ref_axes: set[str] = set()
    all_refs: list[dict[str, Any]] = []
    every_input_full = bool(reviewed_inputs)
    for item in reviewed_inputs:
        axis = str(item.get("axis") or "")
        manifest = parse_axis_manifest(item.get("evidence_refs"))
        status = manifest.get("status") if manifest else LINEAGE_MISSING
        refs = manifest.get("refs") if manifest else []
        if refs:
            all_refs.extend(refs)
        if status != LINEAGE_FULL:
            every_input_full = False
            if axis:
                missing_ref_axes.add(axis)

    refs = dedupe_references(all_refs)
    if not reviewed_inputs or not refs:
        status = LINEAGE_MISSING
    elif every_input_full:
        status = LINEAGE_FULL
    else:
        status = LINEAGE_PARTIAL

    return {
        "schema_version": SCHEMA_VERSION,
        "axis": "review",
        "derived_from_axes": derived_from_axes,
        "missing_ref_axes": sorted(missing_ref_axes),
        "status": status,
        "refs": refs,
    }


def review_input_fingerprint(reviewed_inputs: list[dict[str, Any]]) -> str:
    """Deterministic fingerprint of the exact reconciled inputs a review saw
    — replaces the previous always-empty ``input_fingerprint`` string."""
    normalized = sorted(
        (
            str(item.get("axis") or ""),
            item.get("stance"),
            item.get("score"),
            item.get("confidence"),
            sorted(
                (
                    ref.get("lane"), ref.get("ref_type"), ref.get("provider"),
                    ref.get("task_id") or ref.get("artifact_source_id"),
                )
                for ref in (parse_axis_manifest(item.get("evidence_refs")) or {}).get("refs", [])
            ),
        )
        for item in reviewed_inputs
    )
    return stable_fingerprint(normalized)
