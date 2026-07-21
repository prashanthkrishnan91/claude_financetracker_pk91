"""Distributed Run Intel — deterministic per-ticker decision plane.

When a ticker's required specialist axes are terminal (or explicitly
exhausted), this executor:

  1. deterministically composes the durable analyst-evidence rows the
     canonical certification contract requires (``agent_runs`` +
     ``agent_insights`` with ``analyst_verdict`` + ``recommendations``) from
     the persisted specialist outputs — LLM text is quoted as evidence, the
     advisory ``suggested_action`` is a deterministic score aggregation and is
     itself only ONE advisory input to policy;
  2. runs the canonical deterministic policy ``decision_policy_v1.decide()``
     and records its output on ``intel_run_tickers.decision`` (audit record);
  3. moves the ticker to ``decided`` — or ``no_call`` (EVIDENCE INCOMPLETE)
     when required evidence/axes are unavailable, never fabricating freshness.

No LLM calls, no provider calls, anywhere in this module.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Optional

from ..decision_contracts import (
    AxisBand,
    DecisionInputV3,
    FitBand,
    PriceBand,
    RiskBand,
)
from ..decision_policy_v1 import decide
from ..portfolio_governor_lite import compute_portfolio_fit
from . import run_task_store_v1 as store
from .task_contracts_v1 import (
    AXIS_REVIEW,
    AXIS_RISK_FILING,
    TICKER_DECIDED,
    TICKER_NO_CALL,
    required_axes_for_asset,
)

logger = logging.getLogger(__name__)

# Deterministic advisory-action thresholds (score aggregation → advisory
# signal consumed by decide(); decide() remains the only action authority).
_ADVISORY_BUY_THRESHOLD = 0.35
_ADVISORY_REDUCE_THRESHOLD = -0.35


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TickerDecisionOutcome:
    def __init__(self):
        self.ticker: str = ""
        self.final_ticker_state: str = TICKER_NO_CALL
        self.decision: Optional[dict[str, Any]] = None
        self.evidence_written: bool = False
        self.error: Optional[str] = None


def aggregate_advisory_signal(
    outputs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Deterministic confidence-weighted aggregation of specialist scores.

    Pure math over persisted rows — reproducible from the database alone.
    The review axis, when present, replaces nothing: it is one more weighted
    voice (its whole purpose is to carry reconciliation weight).
    """
    weighted_sum = 0.0
    weight_total = 0.0
    confidences: list[float] = []
    for output in outputs:
        score = output.get("score")
        confidence = output.get("confidence")
        if score is None or confidence is None:
            continue
        weight = max(0.05, float(confidence))
        weighted_sum += float(score) * weight
        weight_total += weight
        confidences.append(float(confidence))

    if weight_total <= 0:
        return {"advisory_action": None, "aggregate_score": None,
                "mean_confidence": None}
    aggregate = weighted_sum / weight_total
    mean_confidence = sum(confidences) / len(confidences)
    if aggregate >= _ADVISORY_BUY_THRESHOLD:
        advisory = "BUY"
    elif aggregate <= _ADVISORY_REDUCE_THRESHOLD:
        advisory = "REDUCE"
    else:
        advisory = "HOLD"
    return {
        "advisory_action": advisory,
        "aggregate_score": round(aggregate, 4),
        "mean_confidence": round(mean_confidence, 4),
    }


def _conviction_level(mean_confidence: Optional[float]) -> str:
    if mean_confidence is None:
        return "LOW"
    if mean_confidence >= 0.75:
        return "HIGH"
    if mean_confidence >= 0.5:
        return "MEDIUM"
    return "LOW"


def _evidence_quality_band(
    outputs: list[dict[str, Any]],
    required_axes: list[str],
    required_lanes_missing: list[str],
) -> AxisBand:
    axes_present = {str(o.get("axis")) for o in outputs}
    required_present = [a for a in required_axes if a in axes_present]
    if not outputs:
        return AxisBand.SUPPRESSED
    if len(required_present) < len(required_axes) or required_lanes_missing:
        return AxisBand.THIN
    high_confidence = sum(
        1 for o in outputs
        if o.get("confidence") is not None and float(o["confidence"]) >= 0.6
    )
    if high_confidence >= max(2, len(required_axes)):
        return AxisBand.STRONG
    return AxisBand.OK


def _risk_band(outputs: list[dict[str, Any]]) -> RiskBand:
    risk_outputs = [
        o for o in outputs if str(o.get("axis")) == AXIS_RISK_FILING
    ]
    if not risk_outputs:
        return RiskBand.UNKNOWN
    output = risk_outputs[0]
    score = output.get("score")
    confidence = output.get("confidence")
    if score is None or confidence is None or float(confidence) < 0.3:
        return RiskBand.UNKNOWN
    score = float(score)
    if score <= -0.7:
        return RiskBand.CRITICAL
    if score <= -0.4:
        return RiskBand.HIGH
    if score <= 0.0:
        return RiskBand.MEDIUM
    return RiskBand.LOW


def compose_analyst_verdict(
    ticker: str,
    outputs: list[dict[str, Any]],
    aggregate: dict[str, Any],
) -> dict[str, Any]:
    """Deterministic composition of the certification-required verdict shape
    from specialist findings. Text is quoted specialist evidence — advisory
    only, never action authority."""
    findings: list[str] = []
    risks: list[str] = []
    for output in sorted(outputs, key=lambda o: str(o.get("axis") or "")):
        for finding in (output.get("key_findings") or [])[:2]:
            findings.append(str(finding))
        for risk in (output.get("risks") or [])[:1]:
            risks.append(str(risk))
    primary_driver = findings[0] if findings else (
        f"{ticker} specialist evidence recorded for this run"
    )
    action_reason = (
        findings[1] if len(findings) > 1 else primary_driver
    )
    risk_flag = risks[0] if risks else "No specialist-flagged risk this run"
    return {
        "primary_driver": primary_driver[:280],
        "action_reason": action_reason[:280],
        "risk_flag": risk_flag[:280],
        "conviction_level": _conviction_level(aggregate.get("mean_confidence")),
        "analysis_source": "distributed_specialists_v1",
        "specialist_axes": sorted(
            {str(o.get("axis")) for o in outputs if o.get("axis")}
        ),
    }


def _write_durable_evidence(
    client: Any,
    *,
    user_id: str,
    ticker: str,
    run_id: str,
    verdict: dict[str, Any],
    aggregate: dict[str, Any],
    outputs: list[dict[str, Any]],
    final_action: str,
    now_iso: str,
) -> bool:
    """Idempotently write agent_runs + agent_insights + recommendations rows
    as COMPATIBILITY PROJECTIONS of the final deterministic decision.

    ``final_action`` is the canonical ``decide()`` output — these rows are
    written AFTER policy determined it and carry that exact action. They are
    never an independent advisory action that publication later reinterprets:
    the distributed snapshot is built from the persisted session decision,
    and these rows exist only for legacy surfaces (freshness recomputation,
    evidence adapters) that still read them.

    Uses the caller's client (worker service-role client in production, the
    in-memory fake in tests) — never a second connection path.
    """
    suggested_action = str(final_action or "HOLD").upper()
    confidence = aggregate.get("mean_confidence")
    thesis = str(verdict.get("primary_driver") or "")

    try:
        existing_run = (
            client.table("agent_runs").select("id").eq("id", run_id).execute()
        )
        if not (getattr(existing_run, "data", None) or []):
            client.table("agent_runs").insert({
                "id": run_id,
                "user_id": user_id,
                "status": "completed",
                "tickers": [ticker],
                "started_at": now_iso,
                "finished_at": now_iso,
            }).execute()
    except Exception as exc:
        logger.warning(
            "decision.agent_run_write_failed ticker=%s err=%s", ticker, exc,
        )
        return False

    sentiment_score = None
    technical_signal = None
    fundamental_score = None
    for output in outputs:
        axis = str(output.get("axis") or "")
        if axis == "sentiment":
            sentiment_score = output.get("score")
        elif axis == "technical":
            score_value = output.get("score")
            if score_value is not None:
                technical_signal = (
                    "BUY" if float(score_value) >= 0.35
                    else "SELL" if float(score_value) <= -0.35 else "NEUTRAL"
                )
        elif axis == "fundamental":
            fundamental_score = output.get("score")

    try:
        existing_insight = (
            client.table("agent_insights")
            .select("id")
            .eq("user_id", user_id)
            .eq("run_id", run_id)
            .eq("ticker", ticker)
            .execute()
        )
        if not (getattr(existing_insight, "data", None) or []):
            client.table("agent_insights").insert({
                "run_id": run_id,
                "user_id": user_id,
                "ticker": ticker,
                "investment_thesis": thesis,
                "sentiment_score": sentiment_score,
                "technical_signal": technical_signal,
                "fundamental_score": fundamental_score,
                "conviction_score": confidence,
                "suggested_action": suggested_action,
                "suggested_allocation": 0.0,
                "analyst_verdict": verdict,
                "analyst_confidence": confidence,
                "created_at": now_iso,
            }).execute()
    except Exception as exc:
        logger.warning(
            "decision.insight_write_failed ticker=%s err=%s", ticker, exc,
        )
        return False

    try:
        existing_rec = (
            client.table("recommendations")
            .select("id")
            .eq("user_id", user_id)
            .eq("agent_run_id", run_id)
            .eq("ticker", ticker)
            .execute()
        )
        if not (getattr(existing_rec, "data", None) or []):
            client.table("recommendations").insert({
                "user_id": user_id,
                "ticker": ticker,
                "action": suggested_action,
                "detail": thesis[:600],
                "is_active": True,
                "agent_run_id": run_id,
                "investment_thesis": thesis,
                "sentiment_score": sentiment_score,
                "technical_signal": technical_signal,
                "conviction_score": confidence,
                "suggested_allocation": 0.0,
                "created_at": now_iso,
            }).execute()
            # Expire THIS ticker's older active recs only (scoped, so other
            # tickers' certification evidence survives).
            (
                client.table("recommendations")
                .update({
                    "is_active": False,
                    "resolution": "expired",
                    "resolved_at": now_iso,
                })
                .eq("user_id", user_id)
                .eq("ticker", ticker)
                .eq("is_active", True)
                .neq("agent_run_id", run_id)
                .execute()
            )
    except Exception as exc:
        logger.warning(
            "decision.rec_write_failed ticker=%s err=%s", ticker, exc,
        )
        return False
    return True


async def execute_ticker_decision_task(
    client: Any,
    *,
    task: dict[str, Any],
    now: Optional[datetime] = None,
) -> TickerDecisionOutcome:
    import asyncio

    now = now or _now()
    outcome = TickerDecisionOutcome()
    session_id = str(task.get("run_session_id") or "")
    user_id = str(task.get("user_id") or "")
    ticker = str(task.get("ticker") or "")
    outcome.ticker = ticker

    def _run_sync() -> TickerDecisionOutcome:
        ticker_rows = store.list_ticker_rows(client, run_session_id=session_id)
        row = next(
            (r for r in ticker_rows if str(r.get("ticker")) == ticker), None
        )
        if row is None:
            outcome.error = "ticker_row_missing"
            return outcome
        asset_type = str(row.get("asset_type") or "equity")
        bundle = row.get("evidence_bundle") or {}
        outputs = [
            o for o in store.list_specialist_outputs(
                client, run_session_id=session_id, ticker=ticker,
            )
        ]
        non_review = [o for o in outputs if str(o.get("axis")) != AXIS_REVIEW]
        required_axes = required_axes_for_asset(asset_type)
        axes_present = {str(o.get("axis")) for o in non_review}
        required_missing_axes = [
            a for a in required_axes if a not in axes_present
        ]
        required_lanes_missing = list(bundle.get("required_lanes_missing") or [])

        # Claim fence: refuse every side effect unless this worker still holds
        # the task's CURRENT claim (state/owner/token verified on the row).
        if not store.owns_claim(client, task):
            outcome.error = "claim_lost"
            return outcome

        # Idempotent retry: an already NO CALL ticker needs nothing more.
        existing_decision = row.get("decision") or {}
        if str(row.get("state")) == TICKER_NO_CALL:
            outcome.final_ticker_state = TICKER_NO_CALL
            outcome.decision = existing_decision or {"outcome": "NO_CALL"}
            return outcome

        # Idempotent retry: the deterministic decision already persisted but a
        # previous attempt died writing the compatibility projections. Rewrite
        # ONLY the projections from the persisted final action — decide() is
        # never re-run for an already-decided ticker.
        if (
            str(row.get("state")) == TICKER_DECIDED
            and existing_decision.get("agent_run_id")
            and existing_decision.get("action")
        ):
            aggregate = aggregate_advisory_signal(outputs)
            verdict = compose_analyst_verdict(ticker, non_review, aggregate)
            evidence_ok = _write_durable_evidence(
                client,
                user_id=user_id,
                ticker=ticker,
                run_id=str(existing_decision["agent_run_id"]),
                verdict=verdict,
                aggregate=aggregate,
                outputs=non_review,
                final_action=str(existing_decision["action"]),
                now_iso=now.isoformat(),
            )
            outcome.evidence_written = evidence_ok
            outcome.final_ticker_state = TICKER_DECIDED
            outcome.decision = existing_decision
            if not evidence_ok:
                outcome.error = "durable_evidence_write_failed"
            return outcome

        # NO CALL: required analytical evidence is unavailable — suppress
        # honestly instead of fabricating a HOLD verdict from nothing.
        if not non_review or required_missing_axes == required_axes:
            reasons = (
                [f"required_axis_missing:{a}" for a in required_missing_axes]
                + [f"required_lane_missing:{l}" for l in required_lanes_missing]
            )
            moved = store.update_ticker_row(
                client,
                run_session_id=session_id,
                ticker=ticker,
                patch={
                    "state": TICKER_NO_CALL,
                    "decision": {
                        "outcome": "NO_CALL",
                        "reason": "evidence_incomplete",
                        "detail": reasons,
                        "decided_at": now.isoformat(),
                    },
                    "degradation_reasons": reasons,
                },
                # Fence: only a not-yet-terminal ticker can transition. A
                # rival claim that already decided/terminalized it wins.
                expected_states=[
                    "pending", "evidence_ready", "analysis_complete",
                    "decision_ready",
                ],
                now=now,
            )
            if not moved:
                outcome.error = "ticker_transition_lost"
                return outcome
            outcome.final_ticker_state = TICKER_NO_CALL
            outcome.decision = {"outcome": "NO_CALL", "detail": reasons}
            return outcome

        # ── SINGLE DECISION AUTHORITY ORDERING ──────────────────────────────
        # 1. specialist aggregate → 2. canonical decision input →
        # 3. decide() exactly once → 4. persist the full input+output on the
        # session ticker row → 5. compatibility evidence rows carrying the
        # FINAL deterministic action. No BUY/HOLD/TRIM/SELL row exists before
        # canonical policy determined it.
        aggregate = aggregate_advisory_signal(outputs)
        verdict = compose_analyst_verdict(ticker, non_review, aggregate)
        run_id = str(uuid.uuid4())

        suppression: dict[str, Any] = {}
        fit = compute_portfolio_fit(
            ticker=ticker,
            category=asset_type,
            current_pct=row.get("portfolio_weight_pct"),
            suppression_reasons=suppression,
        )
        decision_input = DecisionInputV3(
            ticker=ticker,
            evidence_quality=_evidence_quality_band(
                non_review, required_axes, required_lanes_missing,
            ),
            price_context=PriceBand.SUPPRESSED,
            portfolio_fit=fit,
            risk_band=_risk_band(outputs),
            raw_action=str(aggregate.get("advisory_action") or "") or None,
            raw_analyst_action=str(aggregate.get("advisory_action") or "") or None,
            upstream_conviction=verdict.get("conviction_level"),
            suppression_reasons={
                **suppression,
                **(
                    {"price_context": "Price/valuation banding evidence is "
                     "not available for this run."}
                ),
            },
            primary_driver=verdict.get("primary_driver"),
            risk_flag_text=verdict.get("risk_flag"),
            action_reason=verdict.get("action_reason"),
            analyst_drivers=list(verdict.get("specialist_axes") or []),
            asset_type_hint=(
                "stock" if asset_type == "equity" else asset_type
            ),
        )
        # 3. Canonical policy runs EXACTLY ONCE per decided ticker — here.
        # Publication rebuilds the visible card from this persisted record and
        # never calls decide() again.
        decision = decide(decision_input)
        decision_record = {
            "outcome": "DECIDED",
            # ── Complete deterministic OUTPUT (visible-card source of truth) ─
            "action": decision.action.value,
            "conviction": decision.conviction.value,
            "evidence_quality": decision.evidence_quality.value,
            "attractiveness": decision.attractiveness.value,
            "price_context": decision.price_context.value,
            "portfolio_fit": decision.portfolio_fit.value,
            "risk_band": decision.risk_band.value,
            "blockers": list(decision.blockers or []),
            "suppression_reasons": dict(decision.suppression_reasons or {}),
            "rationale_plain_english": decision.rationale_plain_english,
            "why_now": decision.why_now,
            "why_not_now": decision.why_not_now,
            "source_signal_summary": dict(decision.source_signal_summary or {}),
            # ── Complete deterministic INPUT (replay/audit record) ──────────
            "decision_input": {
                "ticker": decision_input.ticker,
                "evidence_quality": decision_input.evidence_quality.value,
                "price_context": decision_input.price_context.value,
                "portfolio_fit": decision_input.portfolio_fit.value,
                "risk_band": decision_input.risk_band.value,
                "raw_action": decision_input.raw_action,
                "raw_analyst_action": decision_input.raw_analyst_action,
                "upstream_conviction": decision_input.upstream_conviction,
                "suppression_reasons": dict(
                    decision_input.suppression_reasons or {}
                ),
                "primary_driver": decision_input.primary_driver,
                "risk_flag_text": decision_input.risk_flag_text,
                "action_reason": decision_input.action_reason,
                "analyst_drivers": list(decision_input.analyst_drivers or []),
                "asset_type_hint": decision_input.asset_type_hint,
            },
            "advisory_signal": aggregate,
            "agent_run_id": run_id,
            "policy_schema_version": decision.schema_version,
            "decided_at": now.isoformat(),
        }

        # 4. Persist the final deterministic decision on the session ticker
        # row (claim-fenced state transition — a reclaimed rival's decision
        # can never be overwritten).
        if not store.owns_claim(client, task):
            outcome.error = "claim_lost"
            return outcome
        moved = store.update_ticker_row(
            client,
            run_session_id=session_id,
            ticker=ticker,
            patch={"state": TICKER_DECIDED, "decision": decision_record},
            expected_states=[
                "pending", "evidence_ready", "analysis_complete",
                "decision_ready",
            ],
            now=now,
        )
        if not moved:
            outcome.error = "ticker_transition_lost"
            return outcome

        # 5. Compatibility evidence rows — projections of the FINAL action.
        evidence_ok = _write_durable_evidence(
            client,
            user_id=user_id,
            ticker=ticker,
            run_id=run_id,
            verdict=verdict,
            aggregate=aggregate,
            outputs=non_review,
            final_action=decision.action.value,
            now_iso=now.isoformat(),
        )
        outcome.evidence_written = evidence_ok
        if not evidence_ok:
            # The deterministic decision is durable; only the compatibility
            # projection failed. Retry rewrites projections idempotently.
            outcome.error = "durable_evidence_write_failed"
            outcome.final_ticker_state = TICKER_DECIDED
            outcome.decision = decision_record
            return outcome

        outcome.final_ticker_state = TICKER_DECIDED
        outcome.decision = decision_record
        return outcome

    return await asyncio.to_thread(_run_sync)
