"""Distributed Run Intel — immutable evidence bundle builder.

Runs when a ticker's lane collector tasks are all terminal. Assembles the
normalized evidence bundle (the ONLY input specialists see) from:

  * the frozen ``intel_run_tickers`` scope row;
  * the terminal lane-task outputs (durable ``intel_run_tasks.output``);
  * owner-scoped, active ``research_artifacts`` payload summaries for
    artifact-backed lanes;
  * the session's portfolio/macro context task outputs.

Zero provider calls, zero LLM calls — pure DB reads + normalization.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from . import run_task_store_v1 as store
from . import source_lineage_v1
from .task_contracts_v1 import (
    LANE_CRYPTO_MARKET,
    LANE_ETF_FUND_DATA,
    LANE_FUNDAMENTALS,
    LANE_NEWS_SENTIMENT,
    LANE_PRICE,
    LANE_SEC_CATALYST,
    LANE_SEC_COMPANY_FACTS,
    LANE_TECHNICALS,
    TASK_COLLECT_EVIDENCE_LANE,
    TASK_COLLECT_MACRO_CONTEXT,
    TASK_COLLECT_PORTFOLIO_CONTEXT,
    TASK_DEGRADED,
    TASK_SUCCEEDED,
    TICKER_EVIDENCE_READY,
    required_lanes_for_asset,
    stable_fingerprint,
)

logger = logging.getLogger(__name__)

# Artifact-backed lanes derive lineage from research_artifact_sources rows;
# every other lane derives a provider_observation reference directly from its
# own durable task output.
_ARTIFACT_LANES = (LANE_SEC_COMPANY_FACTS, LANE_SEC_CATALYST, LANE_ETF_FUND_DATA)
_ARTIFACT_COLUMNS = (
    "id,user_id,ticker,artifact_type,skill_pack,generated_at,freshness_status,"
    "confidence_or_trust_level,payload,is_active"
)
_ARTIFACT_SOURCE_COLUMNS = (
    "id,artifact_id,user_id,provider_name,provider_version,source_kind,source_id,"
    "source_url,source_published_at,fetched_at,source_hash"
)


def _rows(res: Any) -> list[dict[str, Any]]:
    data = getattr(res, "data", None)
    return data if isinstance(data, list) else []


def _bulk_read_artifacts(
    client: Any, artifact_ids: list[Optional[str]], *, user_id: str,
) -> dict[str, dict[str, Any]]:
    """ONE query for every parent ``research_artifacts`` row this ticker
    bundle needs (macro + SEC/ETF lanes), scoped to the OWNING user and
    active rows only — never a cross-user leak. Fails closed to an empty
    mapping (a lineage/summary gap, never a bundle-construction crash) on a
    read failure."""
    ids = sorted({str(a) for a in artifact_ids if a})
    if not ids or not user_id:
        return {}
    try:
        res = (
            client.table("research_artifacts")
            .select(_ARTIFACT_COLUMNS)
            .in_("id", ids)
            .eq("user_id", str(user_id))
            .eq("is_active", True)
            .execute()
        )
        return {str(row.get("id")): row for row in _rows(res)}
    except Exception as exc:
        logger.debug("bundle.artifacts_read_failed count=%d err=%s", len(ids), exc)
        return {}


def _validate_artifact_row(
    row: Optional[dict[str, Any]], *, user_id: str, ticker: Optional[str] = None,
) -> bool:
    """Ownership + ticker-scope + substantive-payload gate.

    A task with an artifact_id but no owned/readable/substantive artifact
    parent must never be described as evidence supplied to the LLM, and
    never source either a display summary or a source reference.
    ``ticker=None`` means the artifact is portfolio-scope (macro) — no
    per-ticker check applies.
    """
    if not row:
        return False
    if str(row.get("user_id") or "") != str(user_id or ""):
        return False
    if row.get("is_active") is False:
        return False
    if ticker is not None and str(row.get("ticker") or "").upper() != str(ticker).upper():
        return False
    payload = row.get("payload")
    return isinstance(payload, dict) and bool(payload)


def _artifact_summary_from_row(row: dict[str, Any]) -> dict[str, Any]:
    """Compact, decision-safe summary of one ALREADY-VALIDATED artifact row."""
    return {
        "artifact_id": row.get("id"),
        "artifact_type": row.get("artifact_type"),
        "skill_pack": row.get("skill_pack"),
        "generated_at": row.get("generated_at"),
        "freshness_status": row.get("freshness_status"),
        "trust_level": row.get("confidence_or_trust_level"),
        "payload": row.get("payload") or {},
    }


def _bulk_read_artifact_sources(
    client: Any, artifact_ids: list[str], *, user_id: str,
) -> dict[str, list[dict[str, Any]]]:
    """ONE query for every VALIDATED artifact-backed lane's canonical source
    rows in this ticker's bundle, scoped to the owning user. Fails closed to
    an empty mapping (a lineage gap, never a bundle-construction crash) on a
    read failure. Callers pass ONLY artifact ids whose parent already passed
    ``_validate_artifact_row`` — ownership is verified on the PARENT before
    its source rows are ever accepted."""
    ids = sorted({str(a) for a in artifact_ids if a})
    if not ids or not user_id:
        return {}
    try:
        res = (
            client.table("research_artifact_sources")
            .select(_ARTIFACT_SOURCE_COLUMNS)
            .in_("artifact_id", ids)
            .eq("user_id", str(user_id))
            .execute()
        )
        by_artifact: dict[str, list[dict[str, Any]]] = {}
        for row in _rows(res):
            by_artifact.setdefault(str(row.get("artifact_id") or ""), []).append(row)
        return by_artifact
    except Exception as exc:
        logger.debug(
            "bundle.artifact_sources_read_failed count=%d err=%s", len(ids), exc,
        )
        return {}


# Volatile keys stripped recursively from the fingerprint source: timestamps
# and cache markers change on every fetch even when the analytical substance
# is identical, and would make cross-session LLM reuse (contract §14) dead.
_VOLATILE_FINGERPRINT_KEYS = frozenset(
    {"as_of", "cache_hit", "generated_at", "fetched_at", "task_id", "observed_at"}
)


def _strip_volatile(value):
    if isinstance(value, dict):
        return {
            key: _strip_volatile(item)
            for key, item in value.items()
            if key not in _VOLATILE_FINGERPRINT_KEYS
        }
    if isinstance(value, list):
        return [_strip_volatile(item) for item in value]
    return value


def _fingerprint_source(bundle: dict[str, Any]) -> dict[str, Any]:
    """The analytically-significant subset of the bundle.

    Excluded: session identity, timestamps/cache markers (recursively), the
    intraday `market` price section (its 15-minute TTL would invalidate every
    fingerprint immediately — the technical lane's daily history carries the
    price signal specialists reason over), mark-to-market portfolio values,
    and the RAW source-reference structures (they carry internal replay
    locators — task_id/artifact_id/artifact_source_id — and, for the price
    lane, a digest of the volatile intraday price itself; see
    ``source_lineage_v1.fingerprint_source_refs``). Included portfolio
    context: weight rounded to the whole percent, prior action and tax
    summary — the inputs that actually change analysis. Included source
    identity: a canonical, session/task-independent projection so a genuine
    provider/source change (or a sourced-vs-gap transition) still alters the
    fingerprint while replay locators and an ordinary price tick never do.
    """
    source = {
        key: _strip_volatile(value)
        for key, value in bundle.items()
        if key not in (
            "as_of", "run_session_id", "market", "portfolio_context",
            "input_fingerprint", "source_refs", "source_refs_by_lane",
            "source_ref_gaps",
        )
    }
    context = bundle.get("portfolio_context") or {}
    weight = context.get("portfolio_weight_pct")
    source["portfolio_context"] = {
        "weight_pct_rounded": round(float(weight)) if weight is not None else None,
        "prior_action": context.get("prior_action"),
        "tax_summary": context.get("tax_summary"),
    }
    source["source_identity"] = source_lineage_v1.fingerprint_source_refs(
        bundle.get("source_refs_by_lane"), bundle.get("source_ref_gaps"),
    )
    return source


def build_evidence_bundle(
    client: Any,
    *,
    session: dict[str, Any],
    ticker_row: dict[str, Any],
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Assemble the bundle for one ticker and persist it on the ticker row.

    Returns the bundle. Raises only on DB write failure (task retries).
    """
    now = now or datetime.now(timezone.utc)
    session_id = str(session.get("id"))
    ticker = str(ticker_row.get("ticker") or "")
    asset_type = str(ticker_row.get("asset_type") or "equity")
    user_id = str(ticker_row.get("user_id") or session.get("user_id") or "")

    lane_tasks = store.list_tasks(
        client,
        run_session_id=session_id,
        task_type=TASK_COLLECT_EVIDENCE_LANE,
        ticker=ticker,
    )
    outputs: dict[str, dict[str, Any]] = {}
    states: dict[str, str] = {}
    task_ids: dict[str, str] = {}
    for task in lane_tasks:
        lane = str(task.get("lane") or "")
        states[lane] = str(task.get("state") or "")
        task_ids[lane] = str(task.get("id") or "")
        output = task.get("output")
        if isinstance(output, dict):
            outputs[lane] = output

    usable_lanes = [
        lane for lane, state in states.items()
        if state == TASK_SUCCEEDED
        and isinstance(outputs.get(lane), dict)
        # An artifact lane that succeeded but produced no artifact is not usable.
        and (outputs[lane].get("artifact_id") is not None
             if "artifact_id" in outputs.get(lane, {}) else True)
    ]
    degraded_lanes = sorted(
        lane for lane, state in states.items()
        if state == TASK_DEGRADED or (
            state == TASK_SUCCEEDED and lane not in usable_lanes
        )
    )
    missing_lanes = sorted(
        lane for lane, state in states.items()
        if state not in (TASK_SUCCEEDED, TASK_DEGRADED)
    )
    required_missing = [
        lane for lane in required_lanes_for_asset(asset_type)
        if lane not in usable_lanes
    ]

    # Session-level context outputs.
    portfolio_context: dict[str, Any] = {}
    macro_task_output: Optional[dict[str, Any]] = None
    for task in store.list_tasks(
        client, run_session_id=session_id, task_type=TASK_COLLECT_PORTFOLIO_CONTEXT,
    ):
        if isinstance(task.get("output"), dict):
            portfolio_context = task["output"]
    for task in store.list_tasks(
        client, run_session_id=session_id, task_type=TASK_COLLECT_MACRO_CONTEXT,
    ):
        output = task.get("output")
        if isinstance(output, dict) and output.get("artifact_id"):
            macro_task_output = output

    # ── ONE bulk parent-artifact read for every artifact this ticker bundle
    # needs — macro (portfolio-scope) + SEC/ETF lanes usable this run —
    # scoped to the owning user. Reused for BOTH the display summaries below
    # AND the source-reference gating (never a second/N+1 query per lane). ──
    macro_artifact_id = str((macro_task_output or {}).get("artifact_id") or "") or None
    sec_artifact_id_by_lane = {
        lane: str((outputs.get(lane) or {}).get("artifact_id") or "") or None
        for lane in (LANE_SEC_COMPANY_FACTS, LANE_SEC_CATALYST)
        if lane in usable_lanes
    }
    etf_artifact_id = (
        str((outputs.get(LANE_ETF_FUND_DATA) or {}).get("artifact_id") or "") or None
        if asset_type == "etf" and LANE_ETF_FUND_DATA in usable_lanes
        else None
    )
    artifact_rows = _bulk_read_artifacts(
        client,
        [macro_artifact_id, etf_artifact_id, *sec_artifact_id_by_lane.values()],
        user_id=user_id,
    )

    macro_row = artifact_rows.get(macro_artifact_id) if macro_artifact_id else None
    macro_summary = (
        _artifact_summary_from_row(macro_row)
        if _validate_artifact_row(macro_row, user_id=user_id)
        else None
    )

    # lane -> artifact_id, ONLY for artifacts that passed ownership/ticker/
    # active/substantive-payload validation. Everything downstream (bundle
    # summaries, source-reference generation) is built exclusively from this
    # validated set — a wrong-user/wrong-ticker/empty artifact never leaks
    # provenance into the bundle, the prompt, or a persisted reference.
    validated_artifact_lane_ids: dict[str, str] = {}

    sec: dict[str, Any] = {}
    for lane, artifact_id in sec_artifact_id_by_lane.items():
        row = artifact_rows.get(artifact_id) if artifact_id else None
        if artifact_id and _validate_artifact_row(row, user_id=user_id, ticker=ticker):
            sec[lane] = _artifact_summary_from_row(row)
            validated_artifact_lane_ids[lane] = artifact_id

    asset_specific: dict[str, Any] = {}
    if etf_artifact_id:
        row = artifact_rows.get(etf_artifact_id)
        if _validate_artifact_row(row, user_id=user_id, ticker=ticker):
            asset_specific["etf_fund_data"] = _artifact_summary_from_row(row)
            validated_artifact_lane_ids[LANE_ETF_FUND_DATA] = etf_artifact_id
    if asset_type == "crypto":
        asset_specific["crypto_market"] = outputs.get(LANE_CRYPTO_MARKET) or {}

    # ── Versioned source-reference lineage (PR 2) ──────────────────────────
    # Direct lanes derive a provider_observation reference straight from
    # their own terminal task output; artifact-backed lanes derive
    # research_artifact_source references ONLY from a validated parent
    # artifact's canonical source rows (one bulk query). A usable lane with
    # no valid reference is recorded as a gap — it never erases the evidence
    # itself. Every lane's reference list is bounded+deduped deterministically
    # (contract §4); truncation is disclosed, never silent.
    source_refs_by_lane: dict[str, list[dict[str, Any]]] = {}
    source_ref_gaps: list[str] = []
    truncated_reference_count = 0

    for lane in usable_lanes:
        if lane in _ARTIFACT_LANES:
            continue
        ref = source_lineage_v1.make_provider_observation_ref(
            lane=lane, ticker=ticker, task_id=task_ids.get(lane),
            output=outputs.get(lane) or {},
        )
        bounded, truncated = source_lineage_v1.bound_references(
            [ref] if ref is not None else [], source_lineage_v1.MAX_REFS_PER_LANE,
        )
        truncated_reference_count += truncated
        if bounded:
            source_refs_by_lane[lane] = bounded
        else:
            source_ref_gaps.append(lane)

    artifact_usable_lanes = [lane for lane in usable_lanes if lane in _ARTIFACT_LANES]
    if validated_artifact_lane_ids:
        sources_by_artifact = _bulk_read_artifact_sources(
            client, list(validated_artifact_lane_ids.values()), user_id=user_id,
        )
    else:
        sources_by_artifact = {}
    for lane in artifact_usable_lanes:
        artifact_id = validated_artifact_lane_ids.get(lane)
        if artifact_id is None:
            # Task succeeded with an artifact_id, but the parent artifact
            # failed ownership/ticker/active/substantive-payload validation
            # — a genuine lineage gap, never a fabricated reference.
            source_ref_gaps.append(lane)
            continue
        rows = sources_by_artifact.get(artifact_id) or []
        refs = [
            r for r in (
                source_lineage_v1.make_research_artifact_source_ref(
                    lane=lane, ticker=ticker, artifact_id=artifact_id,
                    source_row=row,
                )
                for row in rows
            ) if r is not None
        ]
        bounded, truncated = source_lineage_v1.bound_references(
            refs, source_lineage_v1.MAX_REFS_PER_LANE,
        )
        truncated_reference_count += truncated
        if bounded:
            source_refs_by_lane[lane] = bounded
        else:
            source_ref_gaps.append(lane)

    source_refs_by_lane = {
        lane: source_lineage_v1.dedupe_references(refs)
        for lane, refs in source_refs_by_lane.items()
    }
    source_refs = source_lineage_v1.dedupe_references(
        [ref for refs in source_refs_by_lane.values() for ref in refs]
    )
    source_ref_gaps = sorted(set(source_ref_gaps))

    fundamentals = outputs.get(LANE_FUNDAMENTALS) or {}
    valuation = {
        key: fundamentals.get(key)
        for key in ("pe", "forward_pe", "peg", "ps_ttm", "ev_ebitda",
                    "dividend_yield", "market_cap")
        if fundamentals.get(key) is not None
    }

    frozen_context = {
        "quantity": ticker_row.get("quantity"),
        "market_value": ticker_row.get("market_value"),
        "portfolio_weight_pct": ticker_row.get("portfolio_weight_pct"),
        "cost_basis": ticker_row.get("cost_basis"),
        "unrealized_gain_pct": ticker_row.get("unrealized_gain_pct"),
        "tax_summary": ticker_row.get("tax_summary") or {},
        "prior_action": ticker_row.get("prior_action"),
        "portfolio": portfolio_context,
    }

    bundle: dict[str, Any] = {
        "run_session_id": session_id,
        "ticker": ticker,
        "asset_type": asset_type,
        "as_of": now.isoformat(),
        "portfolio_context": frozen_context,
        "market": outputs.get(LANE_PRICE) or {},
        "technical": outputs.get(LANE_TECHNICALS) or {},
        "fundamental": fundamentals,
        "valuation": valuation,
        "sentiment": outputs.get(LANE_NEWS_SENTIMENT) or {},
        "sec": sec,
        "catalysts": (
            [sec[LANE_SEC_CATALYST]] if LANE_SEC_CATALYST in sec else []
        ),
        "macro": macro_summary,
        "asset_specific": asset_specific,
        "source_refs_by_lane": source_refs_by_lane,
        "source_refs": source_refs,
        "source_ref_gaps": source_ref_gaps,
        "usable_lanes": sorted(usable_lanes),
        "missing_lanes": missing_lanes,
        "degraded_lanes": degraded_lanes,
        "required_lanes_missing": required_missing,
        "quality": {
            "usable_lane_count": len(usable_lanes),
            "total_lane_count": len(states),
            "source_linked_lane_count": len(source_refs_by_lane),
            "source_ref_count": len(source_refs),
            "truncated_reference_count": truncated_reference_count,
        },
    }
    bundle["input_fingerprint"] = stable_fingerprint(
        _fingerprint_source(bundle)
    )

    updated = store.update_ticker_row(
        client,
        run_session_id=session_id,
        ticker=ticker,
        patch={
            "evidence_bundle": bundle,
            "state": TICKER_EVIDENCE_READY,
            "missing_lanes": missing_lanes,
            "degraded_lanes": degraded_lanes,
            "degradation_reasons": (
                [f"required_lane_missing:{lane}" for lane in required_missing]
            ),
        },
        # Claim fence: a bundle write may (re)build only a ticker that has not
        # advanced past evidence_ready — a stale bundle task can never regress
        # a ticker that specialists/decisions already moved forward.
        expected_states=["pending", TICKER_EVIDENCE_READY],
        now=now,
    )
    if not updated:
        raise RuntimeError(f"bundle_persist_failed:{ticker}")
    return bundle
