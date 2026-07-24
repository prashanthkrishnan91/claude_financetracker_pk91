"""Distributed Run Intel — immutable evidence bundle builder.

Runs when a ticker's lane collector tasks are all terminal. Assembles the
normalized evidence bundle (the ONLY input specialists see) from:

  * the frozen ``intel_run_tickers`` scope row;
  * the terminal lane-task outputs (durable ``intel_run_tasks.output``);
  * active ``research_artifacts`` payload summaries for artifact-backed lanes;
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
_ARTIFACT_SOURCE_COLUMNS = (
    "id,artifact_id,provider_name,provider_version,source_kind,source_id,"
    "source_url,source_published_at,fetched_at,source_hash"
)


def _bulk_read_artifact_sources(
    client: Any, artifact_ids: list[str]
) -> dict[str, list[dict[str, Any]]]:
    """One query for every artifact-backed lane's canonical source rows in
    this ticker's bundle. Fails closed to an empty mapping (a lineage gap,
    never a bundle-construction crash) on a read failure."""
    ids = sorted({str(a) for a in artifact_ids if a})
    if not ids:
        return {}
    try:
        res = (
            client.table("research_artifact_sources")
            .select(_ARTIFACT_SOURCE_COLUMNS)
            .in_("artifact_id", ids)
            .execute()
        )
        by_artifact: dict[str, list[dict[str, Any]]] = {}
        for row in _rows(res):
            by_artifact.setdefault(str(row.get("artifact_id") or ""), []).append(row)
        return by_artifact
    except Exception as exc:
        logger.debug(
            "bundle.artifact_sources_read_failed ids=%s err=%s", ids, exc,
        )
        return {}


def _rows(res: Any) -> list[dict[str, Any]]:
    data = getattr(res, "data", None)
    return data if isinstance(data, list) else []


def _artifact_summary(client: Any, artifact_id: Optional[str]) -> Optional[dict[str, Any]]:
    """Compact, decision-safe summary of one research artifact payload."""
    if not artifact_id:
        return None
    try:
        res = (
            client.table("research_artifacts")
            .select("id,artifact_type,skill_pack,generated_at,freshness_status,"
                    "confidence_or_trust_level,payload")
            .eq("id", str(artifact_id))
            .limit(1)
            .execute()
        )
        rows = _rows(res)
        if not rows:
            return None
        row = rows[0]
        payload = row.get("payload") or {}
        return {
            "artifact_id": row.get("id"),
            "artifact_type": row.get("artifact_type"),
            "skill_pack": row.get("skill_pack"),
            "generated_at": row.get("generated_at"),
            "freshness_status": row.get("freshness_status"),
            "trust_level": row.get("confidence_or_trust_level"),
            "payload": payload,
        }
    except Exception as exc:
        logger.debug("bundle.artifact_read_failed id=%s err=%s", artifact_id, exc)
        return None


# Volatile keys stripped recursively from the fingerprint source: timestamps
# and cache markers change on every fetch even when the analytical substance
# is identical, and would make cross-session LLM reuse (contract §14) dead.
# ``task_id`` is stripped for the same reason — it's a fresh replay locator
# minted on every task row (including a TTL cache-hit's own new task), never
# a signal that the underlying evidence or source identity changed.
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
    price signal specialists reason over), and mark-to-market portfolio
    values. Included portfolio context: weight rounded to the whole percent,
    prior action and tax summary — the inputs that actually change analysis.
    """
    source = {
        key: _strip_volatile(value)
        for key, value in bundle.items()
        if key not in (
            "as_of", "run_session_id", "market", "portfolio_context",
            "input_fingerprint",
        )
    }
    context = bundle.get("portfolio_context") or {}
    weight = context.get("portfolio_weight_pct")
    source["portfolio_context"] = {
        "weight_pct_rounded": round(float(weight)) if weight is not None else None,
        "prior_action": context.get("prior_action"),
        "tax_summary": context.get("tax_summary"),
    }
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

    # ── Versioned source-reference lineage (PR 2) ──────────────────────────
    # Direct lanes derive a provider_observation reference straight from
    # their own terminal task output; artifact-backed lanes derive
    # research_artifact_source references from one bulk query over the
    # canonical source rows. A usable lane with no valid reference is
    # recorded as a gap — it never erases the evidence itself.
    source_refs_by_lane: dict[str, list[dict[str, Any]]] = {}
    source_ref_gaps: list[str] = []

    for lane in usable_lanes:
        if lane in _ARTIFACT_LANES:
            continue
        ref = source_lineage_v1.make_provider_observation_ref(
            lane=lane, ticker=ticker, task_id=task_ids.get(lane),
            output=outputs.get(lane) or {},
        )
        if ref is not None:
            source_refs_by_lane[lane] = [ref]
        else:
            source_ref_gaps.append(lane)

    artifact_usable_lanes = [lane for lane in usable_lanes if lane in _ARTIFACT_LANES]
    if artifact_usable_lanes:
        artifact_id_by_lane = {
            lane: str((outputs.get(lane) or {}).get("artifact_id") or "")
            for lane in artifact_usable_lanes
        }
        sources_by_artifact = _bulk_read_artifact_sources(
            client, list(artifact_id_by_lane.values())
        )
        for lane, artifact_id in artifact_id_by_lane.items():
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
            if refs:
                source_refs_by_lane[lane] = refs
            else:
                source_ref_gaps.append(lane)

    source_refs_by_lane = {
        lane: source_lineage_v1.dedupe_references(refs)
        for lane, refs in source_refs_by_lane.items()
    }
    source_refs = source_lineage_v1.dedupe_references(
        [ref for refs in source_refs_by_lane.values() for ref in refs]
    )
    source_ref_gaps = sorted(source_ref_gaps)

    # Session-level context outputs.
    portfolio_context: dict[str, Any] = {}
    macro_summary: Optional[dict[str, Any]] = None
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
            macro_summary = _artifact_summary(client, output.get("artifact_id"))

    sec: dict[str, Any] = {}
    for lane in (LANE_SEC_COMPANY_FACTS, LANE_SEC_CATALYST):
        output = outputs.get(lane) or {}
        summary = _artifact_summary(client, output.get("artifact_id"))
        if summary is not None:
            sec[lane] = summary

    asset_specific: dict[str, Any] = {}
    if asset_type == "etf":
        etf_output = outputs.get(LANE_ETF_FUND_DATA) or {}
        summary = _artifact_summary(client, etf_output.get("artifact_id"))
        if summary is not None:
            asset_specific["etf_fund_data"] = summary
    if asset_type == "crypto":
        asset_specific["crypto_market"] = outputs.get(LANE_CRYPTO_MARKET) or {}

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
