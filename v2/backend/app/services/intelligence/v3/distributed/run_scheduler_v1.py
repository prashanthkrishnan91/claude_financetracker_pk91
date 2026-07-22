"""Distributed Run Intel — dependency-wave scheduler.

``run_scheduler_pass`` inspects one active session's durable state and creates
the downstream tasks whose prerequisites just became terminal. It is pure
orchestration bookkeeping: ZERO provider calls, ZERO LLM calls, ZERO decision
policy — those live in task executors. It is idempotent and safe to run
repeatedly (logical task identity is a unique index; duplicate creates are
absorbed).

Waves:
  1. lane collectors terminal (per ticker)      → build_evidence_bundle
  2. bundle terminal (ticker evidence_ready)    → specialist batches per axis
  3. required axes terminal (analysis_complete) → review_conflict (conditional)
  4. review terminal / not needed               → ticker_decision
  5. all tickers terminal                       → portfolio_join_publish
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from . import run_task_store_v1 as store
from .task_contracts_v1 import (
    AXIS_BACKING_LANES,
    AXIS_REVIEW,
    SESSION_RUNNING,
    STAGE_ANALYSIS,
    STAGE_COLLECTING,
    STAGE_DECIDING,
    STAGE_PUBLISHING,
    TASK_BUILD_EVIDENCE_BUNDLE,
    TASK_COLLECT_EVIDENCE_LANE,
    TASK_PORTFOLIO_JOIN_PUBLISH,
    TASK_REVIEW_CONFLICT,
    TASK_SPECIALIST_ANALYSIS,
    TASK_TERMINAL_STATES,
    TASK_TICKER_DECISION,
    TICKER_ANALYSIS_COMPLETE,
    TICKER_DECISION_READY,
    TICKER_EVIDENCE_READY,
    TICKER_TERMINAL_STATES,
    axes_for_asset,
    batch_key_for,
    required_axes_for_asset,
)

logger = logging.getLogger(__name__)

# Review-trigger thresholds (deterministic; see contract §8).
REVIEW_SCORE_SPREAD = 1.0
REVIEW_MIN_CONFIDENCE = 0.6
REVIEW_STRONG_NEGATIVE = -0.5
REVIEW_STRONG_POSITIVE = 0.5
REVIEW_MAJOR_WEIGHT_PCT = 5.0
REVIEW_LOW_CONFIDENCE = 0.3

BATCH_TICKER_SEPARATOR = "+"


def parse_batch_tickers(batch_key: str) -> list[str]:
    """Batch keys are self-describing: 'equity:fundamental:b000:AAPL+MSFT'."""
    parts = str(batch_key or "").split(":")
    if len(parts) < 4:
        return []
    return [t for t in parts[3].split(BATCH_TICKER_SEPARATOR) if t]


def make_batch_key(asset_type: str, axis: str, index: int, tickers: list[str]) -> str:
    base = batch_key_for(asset_type, axis, index)
    return f"{base}:{BATCH_TICKER_SEPARATOR.join(sorted(tickers))}"


def should_review(
    outputs: list[dict[str, Any]], weight_pct: Optional[float]
) -> bool:
    """Deterministic conflict rules — the ONLY thing that creates a review."""
    scored = [
        o for o in outputs
        if o.get("axis") != AXIS_REVIEW and o.get("score") is not None
    ]
    if len(scored) < 2:
        # Low confidence on a major holding still warrants review.
        if (
            weight_pct is not None
            and float(weight_pct) >= REVIEW_MAJOR_WEIGHT_PCT
        ):
            for o in scored:
                conf = o.get("confidence")
                if conf is not None and float(conf) < REVIEW_LOW_CONFIDENCE:
                    return True
        return False

    scores = [(float(o["score"]), float(o.get("confidence") or 0.0)) for o in scored]
    max_s = max(s for s, _ in scores)
    min_s = min(s for s, _ in scores)
    max_conf = max(c for s, c in scores if s == max_s)
    min_conf = max(c for s, c in scores if s == min_s)

    if (
        (max_s - min_s) > REVIEW_SCORE_SPREAD
        and max_conf >= REVIEW_MIN_CONFIDENCE
        and min_conf >= REVIEW_MIN_CONFIDENCE
    ):
        return True
    if (
        weight_pct is not None
        and float(weight_pct) >= REVIEW_MAJOR_WEIGHT_PCT
        and min_s <= REVIEW_STRONG_NEGATIVE
        and max_s >= REVIEW_STRONG_POSITIVE
    ):
        return True
    if weight_pct is not None and float(weight_pct) >= REVIEW_MAJOR_WEIGHT_PCT:
        for _, conf in scores:
            if conf < REVIEW_LOW_CONFIDENCE:
                return True
    return False


def run_scheduler_pass(
    client: Any,
    *,
    session: dict[str, Any],
    max_specialist_batch: int = 5,
    now: Optional[datetime] = None,
) -> dict[str, int]:
    """One idempotent scheduling pass for one active session.

    Returns counters of tasks created (observability only).
    """
    session_id = str(session.get("id"))
    user_id = str(session.get("user_id"))
    created = {"bundles": 0, "specialists": 0, "reviews": 0, "decisions": 0, "publish": 0}

    ticker_rows = store.list_ticker_rows(client, run_session_id=session_id)
    if not ticker_rows:
        # A session without frozen scope is a crashed create. The supervisor
        # repairs it via session_control.repair_session_graph (every partial
        # shape, verified, no browser traffic required) — the scheduler never
        # invents scope and never terminalizes a repairable session.
        return created
    tasks = store.list_tasks(client, run_session_id=session_id)

    by_ticker_lane: dict[tuple[str, str], dict[str, Any]] = {}
    bundle_tasks: dict[str, dict[str, Any]] = {}
    specialist_tasks: list[dict[str, Any]] = []
    review_tasks: dict[str, dict[str, Any]] = {}
    decision_tasks: dict[str, dict[str, Any]] = {}
    publish_task: Optional[dict[str, Any]] = None
    for task in tasks:
        task_type = str(task.get("task_type") or "")
        ticker = str(task.get("ticker") or "")
        if task_type == TASK_COLLECT_EVIDENCE_LANE:
            by_ticker_lane[(ticker, str(task.get("lane") or ""))] = task
        elif task_type == TASK_BUILD_EVIDENCE_BUNDLE:
            bundle_tasks[ticker] = task
        elif task_type == TASK_SPECIALIST_ANALYSIS:
            specialist_tasks.append(task)
        elif task_type == TASK_REVIEW_CONFLICT:
            review_tasks[ticker] = task
        elif task_type == TASK_TICKER_DECISION:
            decision_tasks[ticker] = task
        elif task_type == TASK_PORTFOLIO_JOIN_PUBLISH:
            publish_task = task

    specialist_outputs = store.list_specialist_outputs(
        client, run_session_id=session_id
    )
    outputs_by_ticker: dict[str, list[dict[str, Any]]] = {}
    for output in specialist_outputs:
        outputs_by_ticker.setdefault(str(output.get("ticker") or ""), []).append(output)

    # Which (ticker, axis) pairs are already covered by a specialist task?
    covered_axis_tickers: dict[str, set[str]] = {}
    axis_task_terminal: dict[tuple[str, str], bool] = {}
    for task in specialist_tasks:
        axis = str(task.get("lane") or "")
        terminal = str(task.get("state") or "") in TASK_TERMINAL_STATES
        for ticker in parse_batch_tickers(str(task.get("batch_key") or "")):
            covered_axis_tickers.setdefault(axis, set()).add(ticker)
            key = (ticker, axis)
            axis_task_terminal[key] = axis_task_terminal.get(key, True) and terminal

    # ── Wave 1: bundles ──────────────────────────────────────────────────────
    for row in ticker_rows:
        ticker = str(row.get("ticker") or "")
        if row.get("state") != "pending" or ticker in bundle_tasks:
            continue
        lane_tasks = [
            t for (t_ticker, _lane), t in by_ticker_lane.items()
            if t_ticker == ticker
        ]
        if not lane_tasks:
            continue
        if all(
            str(t.get("state") or "") in TASK_TERMINAL_STATES for t in lane_tasks
        ):
            if store.create_task(
                client,
                run_session_id=session_id,
                user_id=user_id,
                task_type=TASK_BUILD_EVIDENCE_BUNDLE,
                ticker=ticker,
                asset_type=str(row.get("asset_type") or ""),
                priority=int(row.get("priority") or 100),
                now=now,
            ):
                created["bundles"] += 1

    # ── Wave 2: specialist batches ───────────────────────────────────────────
    ready_rows = [
        r for r in ticker_rows if str(r.get("state") or "") == TICKER_EVIDENCE_READY
    ]
    pending_by_axis_asset: dict[tuple[str, str], list[str]] = {}
    for row in ready_rows:
        ticker = str(row.get("ticker") or "")
        asset_type = str(row.get("asset_type") or "")
        bundle = row.get("evidence_bundle") or {}
        usable_lanes = set(bundle.get("usable_lanes") or [])
        existing_axes = {
            str(o.get("axis") or "") for o in outputs_by_ticker.get(ticker, [])
        }
        for axis in axes_for_asset(asset_type):
            if axis in existing_axes:
                continue
            if ticker in covered_axis_tickers.get(axis, set()):
                continue
            backing = AXIS_BACKING_LANES.get(axis, ())
            if backing and not (usable_lanes & set(backing)):
                continue  # axis not runnable — recorded at decision time
            pending_by_axis_asset.setdefault((asset_type, axis), []).append(ticker)

    for (asset_type, axis), tickers in sorted(pending_by_axis_asset.items()):
        tickers = sorted(set(tickers))
        existing_batches = sum(
            1 for t in specialist_tasks
            if str(t.get("lane") or "") == axis
            and str(t.get("asset_type") or "") == asset_type
        )
        for offset in range(0, len(tickers), max(1, max_specialist_batch)):
            chunk = tickers[offset : offset + max_specialist_batch]
            index = existing_batches + (offset // max(1, max_specialist_batch))
            if store.create_task(
                client,
                run_session_id=session_id,
                user_id=user_id,
                task_type=TASK_SPECIALIST_ANALYSIS,
                batch_key=make_batch_key(asset_type, axis, index, chunk),
                lane=axis,
                asset_type=asset_type,
                priority=50,
                now=now,
            ):
                created["specialists"] += 1

    # ── Wave 3+4: analysis-complete → review / decision ──────────────────────
    for row in ticker_rows:
        ticker = str(row.get("ticker") or "")
        state = str(row.get("state") or "")
        if state not in (TICKER_EVIDENCE_READY, TICKER_ANALYSIS_COMPLETE):
            continue
        asset_type = str(row.get("asset_type") or "")
        bundle = row.get("evidence_bundle") or {}
        usable_lanes = set(bundle.get("usable_lanes") or [])
        outputs = outputs_by_ticker.get(ticker, [])
        existing_axes = {str(o.get("axis") or "") for o in outputs}

        # An axis is settled when it has an output, is not runnable, or its
        # batch task(s) are terminal without producing an output (exhausted).
        def _axis_settled(axis: str) -> bool:
            if axis in existing_axes:
                return True
            backing = AXIS_BACKING_LANES.get(axis, ())
            if backing and not (usable_lanes & set(backing)):
                return True
            if ticker in covered_axis_tickers.get(axis, set()):
                return axis_task_terminal.get((ticker, axis), False)
            return False

        all_axes = axes_for_asset(asset_type)
        if not all(_axis_settled(axis) for axis in all_axes):
            continue

        if state == TICKER_EVIDENCE_READY:
            # CAS-fenced forward-only transition (adversarial audit D2): a
            # concurrent decision/terminal transition always wins.
            store.update_ticker_row(
                client,
                run_session_id=session_id,
                ticker=ticker,
                patch={"state": TICKER_ANALYSIS_COMPLETE},
                expected_states=[TICKER_EVIDENCE_READY],
                now=now,
            )

        review_task = review_tasks.get(ticker)
        needs_review = should_review(
            outputs, row.get("portfolio_weight_pct")
        ) and any(
            str(o.get("axis") or "") in required_axes_for_asset(asset_type)
            for o in outputs
        )
        if needs_review and review_task is None:
            if store.create_task(
                client,
                run_session_id=session_id,
                user_id=user_id,
                task_type=TASK_REVIEW_CONFLICT,
                ticker=ticker,
                asset_type=asset_type,
                priority=40,
                now=now,
            ):
                created["reviews"] += 1
            continue
        if review_task is not None and str(
            review_task.get("state") or ""
        ) not in TASK_TERMINAL_STATES:
            continue

        if ticker not in decision_tasks:
            if store.create_task(
                client,
                run_session_id=session_id,
                user_id=user_id,
                task_type=TASK_TICKER_DECISION,
                ticker=ticker,
                asset_type=asset_type,
                priority=30,
                now=now,
            ):
                created["decisions"] += 1
                # Deterministic decision input is now assembled/assembling —
                # surface the decision_ready stage (CAS-fenced, forward-only).
                store.update_ticker_row(
                    client,
                    run_session_id=session_id,
                    ticker=ticker,
                    patch={"state": TICKER_DECISION_READY},
                    expected_states=[TICKER_ANALYSIS_COMPLETE],
                    now=now,
                )

    # ── Dead-end guards: a terminally-failed pipeline task must terminalize
    # its ticker (honest failure) instead of leaving the session unfinishable.
    for row in ticker_rows:
        ticker = str(row.get("ticker") or "")
        state = str(row.get("state") or "")
        if state in TICKER_TERMINAL_STATES:
            continue
        stuck_reason = None
        bundle_task = bundle_tasks.get(ticker)
        if (
            bundle_task is not None
            and str(bundle_task.get("state") or "") == "failed"
        ):
            stuck_reason = "evidence_bundle_failed_terminally"
        decision_task = decision_tasks.get(ticker)
        if (
            decision_task is not None
            and str(decision_task.get("state") or "") == "failed"
        ):
            stuck_reason = "ticker_decision_failed_terminally"
        if stuck_reason:
            # CAS-fenced (adversarial audit D2): can never clobber a ticker
            # that reached a terminal state concurrently.
            moved = store.update_ticker_row(
                client,
                run_session_id=session_id,
                ticker=ticker,
                patch={
                    "state": "failed",
                    "degradation_reasons": [stuck_reason],
                },
                expected_states=[
                    "pending", TICKER_EVIDENCE_READY,
                    TICKER_ANALYSIS_COMPLETE, TICKER_DECISION_READY,
                ],
                now=now,
            )
            if moved:
                row["state"] = "failed"

    # ── Wave 5: portfolio join + publish ─────────────────────────────────────
    all_terminal = all(
        str(r.get("state") or "") in TICKER_TERMINAL_STATES for r in ticker_rows
    )
    if (
        publish_task is not None
        and str(publish_task.get("state") or "") == "failed"
        and str(session.get("status") or "") == SESSION_RUNNING
    ):
        # Publication budget exhausted but the session was left active (e.g.
        # crash between task completion and session update) — honest terminal.
        try:
            client.table("intel_run_sessions").update({
                "status": "failed",
                "last_error": "publication_task_failed_terminally",
            }).eq("id", session_id).eq("status", SESSION_RUNNING).execute()
            session["status"] = "failed"
        except Exception as exc:
            logger.warning(
                "scheduler.session_fail_mark_failed session=%s err=%s",
                session_id, exc,
            )
    if all_terminal and publish_task is None:
        if store.create_task(
            client,
            run_session_id=session_id,
            user_id=user_id,
            task_type=TASK_PORTFOLIO_JOIN_PUBLISH,
            priority=10,
            now=now,
        ):
            created["publish"] += 1

    # ── Stage bookkeeping (presentation only) ────────────────────────────────
    _update_stage(client, session, ticker_rows, all_terminal)
    return created


def _update_stage(
    client: Any,
    session: dict[str, Any],
    ticker_rows: list[dict[str, Any]],
    all_terminal: bool,
) -> None:
    if str(session.get("status") or "") != SESSION_RUNNING:
        return
    states = [str(r.get("state") or "") for r in ticker_rows]
    if all_terminal:
        stage = STAGE_PUBLISHING
    elif any(s in ("decision_ready", "analysis_complete") for s in states):
        stage = STAGE_DECIDING if all(
            s not in ("pending", "evidence_ready") for s in states
        ) else STAGE_ANALYSIS
    elif any(s == TICKER_EVIDENCE_READY for s in states):
        stage = STAGE_ANALYSIS
    else:
        stage = STAGE_COLLECTING
    if stage != session.get("current_stage"):
        try:
            client.table("intel_run_sessions").update(
                {"current_stage": stage}
            ).eq("id", str(session.get("id"))).execute()
            session["current_stage"] = stage
        except Exception as exc:
            logger.debug("scheduler.stage_update_failed err=%s", exc)
