"""Distributed Run Intel — session-native snapshot build, certification and
persistence.

THE single decision authority contract (completion item 1+2): the visible
snapshot for a distributed session is built DIRECTLY from the deterministic
decisions persisted on that session's frozen ``intel_run_tickers`` rows.
Publication:

  * reads ONLY this session's rows (session, frozen tickers, evidence
    bundles, specialist outputs, persisted decisions, session-level context);
  * NEVER loads global active recommendations to determine actions;
  * NEVER runs ``decide()`` again — ``DecisionOutputV3`` is rebuilt verbatim
    from the persisted decision record;
  * accounts for EVERY frozen ticker as exactly one of decided / NO CALL /
    failed, with explicit gap entries for the latter two — an older session's
    action can never surface for them;
  * certifies the assembled snapshot against the frozen scope before persist.

Shared pure formatting is reused (``snapshot_builder.build_snapshot``); the
global evidence-adapter / prewarm publication path is NOT used anywhere in
this module (enforced by the architecture-boundary test).

Snapshot-source vocabulary (backward compatible):
  * ``worker_certified``            — every frozen ticker decided + certified
                                      (green, unchanged meaning);
  * ``worker_certified_with_gaps``  — certified over the decided subset with
                                      every gap explicitly accounted; visibly
                                      non-green (frontend renders an honest
                                      completed-with-gaps state).
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from ..decision_contracts import (
    ActionV3,
    AxisBand,
    ConvictionV3,
    DecisionOutputV3,
    FitBand,
    PriceBand,
    RiskBand,
)
from ..snapshot_builder import build_snapshot
from .task_contracts_v1 import (
    SESSION_COMPLETED,
    SESSION_COMPLETED_WITH_GAPS,
    TICKER_DECIDED,
    TICKER_FAILED,
    TICKER_NO_CALL,
    WORKFLOW_VERSION_DISTRIBUTED,
)

logger = logging.getLogger(__name__)

SOURCE_CERTIFIED = "worker_certified"
SOURCE_CERTIFIED_WITH_GAPS = "worker_certified_with_gaps"

_GAP_REASONS = {
    TICKER_NO_CALL: (
        "Not enough trustworthy evidence this run — no call made rather than "
        "guessing."
    ),
    TICKER_FAILED: (
        "Analysis could not finish for this holding in this run."
    ),
}

_ASSET_TYPE_TO_CATEGORY = {
    "equity": "stock",
    "etf": "ETF",
    "crypto": "Crypto",
}


class SessionPublicationError(Exception):
    """Publication cannot produce a truthful session snapshot."""


def rebuild_decision_output(
    ticker: str, record: dict[str, Any]
) -> DecisionOutputV3:
    """Reconstruct the persisted deterministic decision VERBATIM.

    No policy call, no reinterpretation — every field comes from the record
    the decision task persisted at decide() time. Raises when the record is
    not a complete DECIDED record (publication must fail closed, not guess).
    """
    if str(record.get("outcome")) != "DECIDED" or not record.get("action"):
        raise SessionPublicationError(
            f"incomplete_decision_record:{ticker}"
        )
    try:
        return DecisionOutputV3(
            ticker=ticker,
            action=ActionV3(str(record["action"])),
            conviction=ConvictionV3(str(record.get("conviction") or "LOW")),
            evidence_quality=AxisBand(
                str(record.get("evidence_quality") or "SUPPRESSED")
            ),
            attractiveness=AxisBand(
                str(record.get("attractiveness") or "SUPPRESSED")
            ),
            price_context=PriceBand(
                str(record.get("price_context") or "SUPPRESSED")
            ),
            portfolio_fit=FitBand(
                str(record.get("portfolio_fit") or "UNKNOWN")
            ),
            risk_band=RiskBand(str(record.get("risk_band") or "UNKNOWN")),
            blockers=list(record.get("blockers") or []),
            suppression_reasons=dict(record.get("suppression_reasons") or {}),
            rationale_plain_english=str(
                record.get("rationale_plain_english") or ""
            ),
            why_now=str(record.get("why_now") or ""),
            why_not_now=str(record.get("why_not_now") or ""),
            source_signal_summary=dict(
                record.get("source_signal_summary") or {}
            ),
            schema_version=str(record.get("policy_schema_version") or "v3.1"),
        )
    except (ValueError, KeyError) as exc:
        raise SessionPublicationError(
            f"unreplayable_decision_record:{ticker}:{exc}"
        ) from exc


def _coverage(ticker_rows: list[dict[str, Any]]) -> dict[str, Any]:
    decided, no_call, failed, other = [], [], [], []
    for row in ticker_rows:
        state = str(row.get("state") or "")
        ticker = str(row.get("ticker") or "")
        if state == TICKER_DECIDED:
            decided.append(ticker)
        elif state == TICKER_NO_CALL:
            no_call.append(ticker)
        elif state == TICKER_FAILED:
            failed.append(ticker)
        else:
            other.append(ticker)
    gaps = [
        {
            "ticker": t,
            "state": TICKER_NO_CALL,
            "reason": _GAP_REASONS[TICKER_NO_CALL],
        }
        for t in sorted(no_call)
    ] + [
        {
            "ticker": t,
            "state": TICKER_FAILED,
            "reason": _GAP_REASONS[TICKER_FAILED],
        }
        for t in sorted(failed)
    ]
    return {
        "frozen_holding_count": len(ticker_rows),
        "decided_count": len(decided),
        "no_call_count": len(no_call),
        "failed_count": len(failed),
        "unaccounted_tickers": sorted(other),
        "decided_tickers": sorted(decided),
        "no_call_tickers": sorted(no_call),
        "failed_tickers": sorted(failed),
        "gaps": gaps,
    }


def build_session_snapshot_payload(
    *,
    session: dict[str, Any],
    ticker_rows: list[dict[str, Any]],
    specialist_outputs: list[dict[str, Any]],
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Assemble the complete session-native snapshot payload.

    Decided cards come exclusively from persisted deterministic decisions;
    NO CALL / failed tickers appear ONLY in the coverage-gap accounting —
    never as action cards, never backfilled from an older session.
    """
    now = now or datetime.now(timezone.utc)
    session_id = str(session.get("id"))
    coverage = _coverage(ticker_rows)
    if coverage["unaccounted_tickers"]:
        raise SessionPublicationError(
            "frozen_scope_not_terminal:"
            + ",".join(coverage["unaccounted_tickers"])
        )

    decisions: list[DecisionOutputV3] = []
    card_metas: list[dict[str, Any]] = []
    outputs_by_ticker: dict[str, list[dict[str, Any]]] = {}
    for output in specialist_outputs:
        outputs_by_ticker.setdefault(
            str(output.get("ticker") or ""), []
        ).append(output)

    for row in sorted(
        ticker_rows, key=lambda r: str(r.get("ticker") or "")
    ):
        if str(row.get("state") or "") != TICKER_DECIDED:
            continue
        ticker = str(row.get("ticker") or "")
        record = row.get("decision") or {}
        decisions.append(rebuild_decision_output(ticker, record))
        bundle = row.get("evidence_bundle") or {}
        card_metas.append({
            "ticker": ticker,
            "name": ticker,
            "category": _ASSET_TYPE_TO_CATEGORY.get(
                str(row.get("asset_type") or "equity"), "stock"
            ),
            "thesis_state": "intact",
            # Session provenance for the drawer: explanations come from THIS
            # session's specialist outputs and evidence bundle only.
            "research_axis_readiness": {},
            "session_specialist_axes": sorted({
                str(o.get("axis") or "")
                for o in outputs_by_ticker.get(ticker, [])
            }),
            "session_evidence_refs": list(bundle.get("source_refs") or []),
        })

    payload = build_snapshot(
        run_id=session_id,
        decisions=decisions,
        card_metas=card_metas,
        warnings=(
            [
                f"{len(coverage['gaps'])} holding(s) could not be analyzed "
                "this run — see coverage gaps."
            ]
            if coverage["gaps"] else []
        ),
    )

    has_gaps = bool(coverage["no_call_count"] or coverage["failed_count"])
    snapshot_source = (
        SOURCE_CERTIFIED_WITH_GAPS if has_gaps else SOURCE_CERTIFIED
    )
    session_status = (
        SESSION_COMPLETED_WITH_GAPS if has_gaps else SESSION_COMPLETED
    )

    payload["run_session_id"] = session_id
    payload["workflow_version"] = int(
        session.get("workflow_version") or WORKFLOW_VERSION_DISTRIBUTED
    )
    payload["session_status"] = session_status
    payload["snapshot_source"] = snapshot_source
    payload["evidence_freshness_state"] = "certified_current"
    payload["agents_ran_via_worker"] = True
    payload["this_click_used_llm"] = False
    payload["agents_ran_for_this_click"] = (
        "No — background worker handles analysis"
    )
    # Frontend certification contract fields: certified == decided; total ==
    # the FULL frozen scope (never shrunk to hide gaps).
    payload["certified_holding_count"] = coverage["decided_count"]
    payload["total_holding_count"] = coverage["frozen_holding_count"]
    payload["failed_tickers_in_certification"] = sorted(
        coverage["no_call_tickers"] + coverage["failed_tickers"]
    )
    payload["session_coverage"] = coverage
    payload["session_provenance"] = {
        "decision_authority": "decision_policy_v1.decide@ticker_decision_task",
        "publication_rebuilds_decisions": True,
        "specialist_output_count": len(specialist_outputs),
        "specialist_models": sorted({
            str(o.get("model") or "") for o in specialist_outputs
            if o.get("model")
        }),
        "specialist_prompt_versions": sorted({
            str(o.get("prompt_version") or "") for o in specialist_outputs
            if o.get("prompt_version")
        }),
    }
    payload["certification_summary"] = {
        "certified": True,
        "mode": "distributed_session_native",
        "certified_holding_count": coverage["decided_count"],
        "total_holding_count": coverage["frozen_holding_count"],
        "failed_holding_count": (
            coverage["no_call_count"] + coverage["failed_count"]
        ),
        "gap_tickers": sorted(
            coverage["no_call_tickers"] + coverage["failed_tickers"]
        ),
        "certification_errors": [],
    }
    return payload


# ── Distributed-session certification contract ───────────────────────────────

@dataclass
class SessionCertificationResult:
    certified: bool = False
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"certified": self.certified, "errors": list(self.errors)}


def certify_session_snapshot(
    *,
    payload: dict[str, Any],
    session: dict[str, Any],
    ticker_rows: list[dict[str, Any]],
) -> SessionCertificationResult:
    """Certify the assembled snapshot against the frozen session scope.

    Proves (completion item 2):
      * every frozen ticker is accounted for exactly once
        (decided card XOR explicit gap);
      * every decided card belongs to THIS session (run_id == session id)
        and its visible action EXACTLY equals the persisted deterministic
        decision action;
      * NO CALL / failed tickers never appear as ordinary action cards;
      * no card exists for a ticker outside the frozen scope (nothing from an
        older session can leak in);
      * coverage counts are internally consistent with the card list.
    """
    result = SessionCertificationResult()
    session_id = str(session.get("id"))
    rows_by_ticker = {
        str(r.get("ticker") or ""): r for r in ticker_rows
    }
    decided = {
        t for t, r in rows_by_ticker.items()
        if str(r.get("state")) == TICKER_DECIDED
    }
    gap_states = {
        t: str(r.get("state"))
        for t, r in rows_by_ticker.items()
        if str(r.get("state")) in (TICKER_NO_CALL, TICKER_FAILED)
    }

    if str(payload.get("run_session_id") or "") != session_id:
        result.errors.append("payload_session_mismatch")

    cards = payload.get("current_holdings") or []
    card_tickers: list[str] = []
    for card in cards:
        ticker = str(card.get("ticker") or "")
        card_tickers.append(ticker)
        # Cards stamp their producing run as source_run_id; for a session-
        # native snapshot that MUST be this exact session.
        if str(card.get("source_run_id") or "") != session_id:
            result.errors.append(f"card_foreign_session:{ticker}")
        row = rows_by_ticker.get(ticker)
        if row is None:
            result.errors.append(f"card_outside_frozen_scope:{ticker}")
            continue
        if ticker in gap_states:
            result.errors.append(
                f"gap_ticker_rendered_as_action_card:{ticker}"
            )
            continue
        if ticker not in decided:
            result.errors.append(f"card_for_undecided_ticker:{ticker}")
            continue
        persisted_action = str(
            (row.get("decision") or {}).get("action") or ""
        )
        if str(card.get("action") or "") != persisted_action:
            result.errors.append(
                f"card_action_diverges_from_persisted_decision:{ticker}:"
                f"card={card.get('action')} persisted={persisted_action}"
            )

    if len(card_tickers) != len(set(card_tickers)):
        result.errors.append("duplicate_cards")
    missing_cards = decided - set(card_tickers)
    if missing_cards:
        result.errors.append(
            "decided_tickers_missing_cards:" + ",".join(sorted(missing_cards))
        )

    coverage = payload.get("session_coverage") or {}
    accounted = (
        set(coverage.get("decided_tickers") or [])
        | set(coverage.get("no_call_tickers") or [])
        | set(coverage.get("failed_tickers") or [])
    )
    frozen = set(rows_by_ticker)
    # Cross-check the frozen ticker rows against the session's own recorded
    # holdings_scope — a manually deleted/lost ticker row can never silently
    # shrink the certified scope.
    recorded_scope = {
        str(t) for t in (session.get("holdings_scope") or []) if t
    }
    if recorded_scope and recorded_scope != frozen:
        result.errors.append(
            "frozen_rows_diverge_from_session_holdings_scope:"
            + ",".join(sorted(recorded_scope.symmetric_difference(frozen)))
        )
    if accounted != frozen:
        result.errors.append(
            "coverage_does_not_account_full_frozen_scope:"
            + ",".join(sorted(frozen.symmetric_difference(accounted)))
        )
    gap_listed = {
        str(g.get("ticker") or "") for g in (coverage.get("gaps") or [])
    }
    if gap_listed != set(gap_states):
        result.errors.append("gap_list_mismatch")
    if int(coverage.get("frozen_holding_count") or -1) != len(frozen):
        result.errors.append("frozen_count_mismatch")
    if int(payload.get("total_holding_count") or -1) != len(frozen):
        result.errors.append("total_holding_count_mismatch")
    if int(payload.get("certified_holding_count") or -1) != len(decided):
        result.errors.append("certified_holding_count_mismatch")

    expected_source = (
        SOURCE_CERTIFIED_WITH_GAPS if gap_states else SOURCE_CERTIFIED
    )
    if str(payload.get("snapshot_source") or "") != expected_source:
        result.errors.append(
            f"snapshot_source_mismatch:expected={expected_source}"
        )

    result.certified = not result.errors
    return result


# ── Persistence (session-idempotent; one snapshot per session) ───────────────

def persist_session_snapshot(
    client: Any,
    *,
    settings: Any,
    user_id: str,
    session_id: str,
    payload: dict[str, Any],
) -> Optional[str]:
    """Insert the ONE session-linked snapshot row (deactivate-then-insert).

    Mirrors the repository persistence semantics: honors the
    INTEL_V3_SNAPSHOT_WRITES_ENABLED cost guard (returns None when disabled),
    deactivates the user's previous active snapshots, inserts with the
    ``run_session_id`` scalar column, and on a unique-index conflict adopts +
    re-activates the winning session row instead of duplicating. Returns the
    persisted (or adopted) row id.
    """
    if not getattr(settings, "intel_v3_snapshot_writes_enabled", False):
        logger.info(
            "COST_GUARD distributed_publication.persist_skipped_writes_disabled "
            "session=%s", session_id,
        )
        return None

    def _find_existing() -> Optional[str]:
        try:
            res = (
                client.table("intel_v3_snapshots")
                .select("id")
                .eq("run_session_id", session_id)
                .limit(1)
                .execute()
            )
            rows = getattr(res, "data", None) or []
            if rows and isinstance(rows[0], dict) and rows[0].get("id"):
                return str(rows[0]["id"])
        except Exception:
            pass
        return None

    def _reactivate(row_id: str) -> None:
        client.table("intel_v3_snapshots").update({"is_active": False}).eq(
            "user_id", user_id
        ).eq("is_active", True).neq("id", row_id).execute()
        client.table("intel_v3_snapshots").update({"is_active": True}).eq(
            "id", row_id
        ).execute()

    existing = _find_existing()
    if existing is not None:
        try:
            _reactivate(existing)
        except Exception as exc:
            logger.warning(
                "distributed_publication.reactivate_failed row=%s err=%s",
                existing, exc,
            )
        return existing

    row = {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "run_id": session_id,
        "run_session_id": session_id,
        "payload": payload,
        "snapshot_source": str(payload.get("snapshot_source") or ""),
        "is_active": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        client.table("intel_v3_snapshots").update({"is_active": False}).eq(
            "user_id", user_id
        ).eq("is_active", True).execute()
        client.table("intel_v3_snapshots").insert(row).execute()
        return row["id"]
    except Exception as exc:
        # Unique-index race (uq_intel_v3_snapshots_run_session): adopt the
        # winner instead of failing or duplicating.
        winner = _find_existing()
        if winner is not None:
            try:
                _reactivate(winner)
            except Exception:
                pass
            return winner
        logger.warning(
            "distributed_publication.persist_failed session=%s err=%s",
            session_id, exc,
        )
        raise
