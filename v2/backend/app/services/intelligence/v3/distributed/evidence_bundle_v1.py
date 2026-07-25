"""Distributed Run Intel — immutable evidence bundle builder.

Runs when a ticker's lane collector tasks are all terminal. Assembles the
normalized evidence bundle (the ONLY input specialists see) from:

  * the frozen ``intel_run_tickers`` scope row;
  * the terminal lane-task outputs (durable ``intel_run_tasks.output``);
  * owner-scoped, active, non-invalidated, contract-matched, substantive
    ``research_artifacts`` payload summaries for artifact-backed lanes;
  * the session's portfolio/macro context task outputs.

Zero provider calls, zero LLM calls — pure DB reads + normalization.

Two explicit concepts (never conflated):

  * TERMINAL lane outcome — what the collector task itself reported
    (``states``/``outputs`` below): succeeded/degraded/missing.
  * EFFECTIVE evidence lanes (``usable_lanes`` on the returned bundle) —
    lanes whose evidence was actually ESTABLISHED: a terminal success, AND,
    for artifact-backed lanes, a parent ``research_artifacts`` row that is
    owned by this bundle's user, correctly scoped, active, not invalidated,
    matches the lane's own adapter contract (artifact_type/skill_pack/
    scope_kind), and carries real lane-specific extracted evidence (not
    merely governance metadata or a zero-observation/zero-catalyst/
    zero-holdings payload). A terminal success whose artifact fails any of
    these checks is DEGRADED, not usable — it is never described as
    evidence supplied to the LLM, and never sources a reference either.
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
    LANE_MACRO,
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
    "id,user_id,ticker,artifact_type,skill_pack,scope_kind,generated_at,"
    "freshness_status,confidence_or_trust_level,payload,is_active,invalidated_at"
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


def _artifact_contract_for_lane(lane: str) -> Optional[dict[str, Optional[str]]]:
    """Expected ``{artifact_type, skill_pack, scope_kind}`` for one
    artifact-backed lane, derived from that lane's OWN existing adapter
    constants — never a parallel/guessed contract. A None value for one key
    means that particular check does not apply to this lane."""
    if lane == LANE_SEC_COMPANY_FACTS:
        from ...research_workers.sec_companyfacts_adapter_v1 import (
            _ARTIFACT_TYPE as artifact_type,
            _SCOPE_KIND as scope_kind,
            _SKILL_PACK as skill_pack,
        )
        return {"artifact_type": artifact_type, "skill_pack": skill_pack, "scope_kind": scope_kind}
    if lane == LANE_SEC_CATALYST:
        from ...research_workers.sec_catalyst_sentiment_adapter_v1 import (
            SEC_CATALYST_ARTIFACT_TYPE as artifact_type,
            SEC_CATALYST_SKILL_PACK as skill_pack,
            _SCOPE_KIND as scope_kind,
        )
        return {"artifact_type": artifact_type, "skill_pack": skill_pack, "scope_kind": scope_kind}
    if lane == LANE_ETF_FUND_DATA:
        from ...research_workers.etf_nport_adapter_v1 import (
            _ARTIFACT_TYPE as artifact_type,
            _SCOPE_KIND as scope_kind,
            _SKILL_PACK as skill_pack,
        )
        return {"artifact_type": artifact_type, "skill_pack": skill_pack, "scope_kind": scope_kind}
    if lane == LANE_MACRO:
        from ...research_workers.fred_macro_adapter_v1 import _SCOPE_KIND as scope_kind
        return {"artifact_type": None, "skill_pack": None, "scope_kind": scope_kind}
    return None


def _is_substantive_artifact_payload(lane: str, payload: dict[str, Any]) -> bool:
    """Lane-specific substantive-evidence predicate over the artifact's OWN
    existing adapter payload shape. Governance-only fields
    (``source_credibility_assessment``/``contradiction_assessment``/
    ``evidence_completeness_assessment``/``truth_usability_assessment``) are
    injected unconditionally onto EVERY artifact by
    ``ResearchArtifactServiceV1`` regardless of lane, so their presence never
    counts as substantive lane evidence — only real extracted data does."""
    if not isinstance(payload, dict) or not payload:
        return False
    try:
        if lane == LANE_SEC_COMPANY_FACTS:
            return int(payload.get("observation_count") or 0) > 0
        if lane == LANE_SEC_CATALYST:
            return int(payload.get("catalyst_count") or 0) > 0
        if lane == LANE_ETF_FUND_DATA:
            return int(payload.get("holdings_count") or 0) > 0
    except (TypeError, ValueError):
        return False
    return True


def _validate_artifact_row(
    row: Optional[dict[str, Any]], *, user_id: str, ticker: Optional[str] = None,
    lane: Optional[str] = None,
) -> tuple[bool, Optional[str]]:
    """Ownership + ticker-scope + active + not-invalidated + adapter-contract
    + substantive-payload gate. Returns ``(is_effective, degraded_reason)`` —
    reason is a bounded, deterministic string identifying WHY validation
    failed (None when it passed). A task with an artifact_id but a failing
    parent must never be described as evidence supplied to the LLM, and
    never source either a display summary or a source reference.
    ``ticker=None`` means the artifact is portfolio-scope (macro) — no
    per-ticker check applies.
    """
    if not row:
        return False, "artifact_missing"
    if str(row.get("user_id") or "") != str(user_id or ""):
        return False, "artifact_wrong_user"
    if row.get("is_active") is False:
        return False, "artifact_inactive"
    if row.get("invalidated_at"):
        return False, "artifact_invalidated"
    if ticker is not None and str(row.get("ticker") or "").upper() != str(ticker).upper():
        return False, "artifact_wrong_ticker"
    contract = _artifact_contract_for_lane(lane) if lane else None
    if contract:
        if contract.get("scope_kind") and row.get("scope_kind") != contract["scope_kind"]:
            return False, "artifact_wrong_scope"
        if contract.get("artifact_type") and row.get("artifact_type") != contract["artifact_type"]:
            return False, "artifact_wrong_type"
        if contract.get("skill_pack") and row.get("skill_pack") != contract["skill_pack"]:
            return False, "artifact_wrong_skill_pack"
    payload = row.get("payload")
    if not isinstance(payload, dict) or not payload:
        return False, "artifact_empty_payload"
    if lane and not _is_substantive_artifact_payload(lane, payload):
        return False, "artifact_not_substantive"
    return True, None


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
# ``artifact_id`` is an internal storage locator (see ``_artifact_summary_from_row``)
# — replacing one internal artifact row with another that carries the SAME
# external evidence must never change the fingerprint.
_VOLATILE_FINGERPRINT_KEYS = frozenset(
    {"as_of", "cache_hit", "generated_at", "fetched_at", "task_id", "observed_at", "artifact_id"}
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

    Excluded: session identity, timestamps/cache markers AND internal
    storage identifiers (recursively — see ``_VOLATILE_FINGERPRINT_KEYS``),
    the intraday `market` price section (its 15-minute TTL would invalidate
    every fingerprint immediately — the technical lane's daily history
    carries the price signal specialists reason over), mark-to-market
    portfolio values, and the RAW source-reference structures (they carry
    internal replay locators — task_id/artifact_id/artifact_source_id — and,
    for the price lane, a digest of the volatile intraday price itself; see
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

    # TERMINAL lane outcome: task succeeded, and (for artifact lanes) an
    # artifact_id was produced at all. This is NOT yet "effective evidence" —
    # see the module docstring.
    raw_usable_lanes = [
        lane for lane, state in states.items()
        if state == TASK_SUCCEEDED
        and isinstance(outputs.get(lane), dict)
        and (outputs[lane].get("artifact_id") is not None
             if "artifact_id" in outputs.get(lane, {}) else True)
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
    # needs — macro (portfolio-scope) + SEC/ETF lanes with a terminal-success
    # artifact_id — scoped to the owning user. Reused for BOTH the display
    # summaries below AND the source-reference gating (never a second/N+1
    # query per lane). ──────────────────────────────────────────────────────
    macro_artifact_id = str((macro_task_output or {}).get("artifact_id") or "") or None
    sec_artifact_id_by_lane = {
        lane: str((outputs.get(lane) or {}).get("artifact_id") or "") or None
        for lane in (LANE_SEC_COMPANY_FACTS, LANE_SEC_CATALYST)
        if lane in raw_usable_lanes
    }
    etf_artifact_id = (
        str((outputs.get(LANE_ETF_FUND_DATA) or {}).get("artifact_id") or "") or None
        if asset_type == "etf" and LANE_ETF_FUND_DATA in raw_usable_lanes
        else None
    )
    artifact_rows = _bulk_read_artifacts(
        client,
        [macro_artifact_id, etf_artifact_id, *sec_artifact_id_by_lane.values()],
        user_id=user_id,
    )

    macro_row = artifact_rows.get(macro_artifact_id) if macro_artifact_id else None
    macro_is_valid, _macro_reason = _validate_artifact_row(
        macro_row, user_id=user_id, lane=LANE_MACRO,
    )
    macro_summary = _artifact_summary_from_row(macro_row) if macro_is_valid else None

    # lane -> artifact_id, ONLY for artifacts that passed the FULL effective-
    # evidence gate (ownership/ticker/active/not-invalidated/contract/
    # substantive). Everything downstream (bundle summaries, source-reference
    # generation, usable_lanes) is built exclusively from this validated set
    # — a wrong-user/wrong-ticker/invalidated/wrong-contract/non-substantive
    # artifact never leaks provenance into the bundle, the prompt, or a
    # persisted reference; it becomes a DEGRADED lane with a bounded reason
    # instead (never a source_ref_gap — there is no usable evidence to have
    # a citation gap over).
    validated_artifact_lane_ids: dict[str, str] = {}
    artifact_degraded_reasons: dict[str, str] = {}

    sec: dict[str, Any] = {}
    for lane, artifact_id in sec_artifact_id_by_lane.items():
        if not artifact_id:
            continue
        row = artifact_rows.get(artifact_id)
        is_valid, reason = _validate_artifact_row(
            row, user_id=user_id, ticker=ticker, lane=lane,
        )
        if is_valid:
            sec[lane] = _artifact_summary_from_row(row)
            validated_artifact_lane_ids[lane] = artifact_id
        else:
            artifact_degraded_reasons[lane] = reason or "artifact_invalid"

    asset_specific: dict[str, Any] = {}
    if etf_artifact_id:
        row = artifact_rows.get(etf_artifact_id)
        is_valid, reason = _validate_artifact_row(
            row, user_id=user_id, ticker=ticker, lane=LANE_ETF_FUND_DATA,
        )
        if is_valid:
            asset_specific["etf_fund_data"] = _artifact_summary_from_row(row)
            validated_artifact_lane_ids[LANE_ETF_FUND_DATA] = etf_artifact_id
        else:
            artifact_degraded_reasons[LANE_ETF_FUND_DATA] = reason or "artifact_invalid"
    if asset_type == "crypto":
        asset_specific["crypto_market"] = outputs.get(LANE_CRYPTO_MARKET) or {}

    # EFFECTIVE evidence lanes: direct lanes pass through terminal success
    # unchanged; artifact-backed lanes require the full validation above.
    # This IS bundle.usable_lanes — the scheduler/specialist evidence
    # authority — never the raw terminal list.
    usable_lanes = [
        lane for lane in raw_usable_lanes
        if lane not in _ARTIFACT_LANES or lane in validated_artifact_lane_ids
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
    # research_artifact_source references ONLY from an EFFECTIVE (validated)
    # parent artifact's canonical source rows (one bulk query). An effective
    # lane with no valid reference is recorded as a gap — it never erases the
    # evidence itself. Every lane's reference list is bounded+deduped
    # deterministically (contract §4); truncation is disclosed, never silent.
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
    if artifact_usable_lanes:
        sources_by_artifact = _bulk_read_artifact_sources(
            client,
            [validated_artifact_lane_ids[lane] for lane in artifact_usable_lanes],
            user_id=user_id,
        )
    else:
        sources_by_artifact = {}
    for lane in artifact_usable_lanes:
        artifact_id = validated_artifact_lane_ids[lane]
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
                + [
                    f"artifact_invalid:{lane}:{reason}"
                    for lane, reason in sorted(artifact_degraded_reasons.items())
                ]
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
