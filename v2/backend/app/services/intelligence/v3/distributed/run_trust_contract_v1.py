"""Distributed Run Intel — pure run-trust projection (``run_trust_contract_v1``).

Publishes and displays a TRUTHFUL trust contract for a Run Intel session:
what completed, what evidence exists, what failed, and what remains
untrusted. This module owns none of the later trust-recovery work (source
reference generation, currency normalization, conflict-review reliability,
financial-truth refresh, repeat runs, performance, Deploy Cash) — it only
projects the truth that already lives on the session's durable rows.

Pure function, derived ONLY from already-fetched rows:

  * the run session row;
  * the frozen ``intel_run_tickers`` rows (state, asset_type, evidence_bundle,
    decision);
  * the session's ``intel_run_tasks`` rows (specialist_analysis +
    review_conflict scope/state, for axis MISSING/FAILED and conflict-review
    pending/failed distinctions that the ticker/output rows alone cannot
    express);
  * the session's ``intel_run_specialist_outputs`` rows.

No IO, no DB reads, no LLM calls here — callers (session-native publication,
and a read-time enrichment path for snapshots persisted before this contract
existed) own fetching the rows and passing them in. The caller is also
responsible for failing CLOSED (an explicit unknown overlay — see
``unknown_overlay_contract``) instead of calling this function at all when
the underlying reads themselves are unavailable/suspect; this function
always assumes its inputs are a successful, complete read.

Trust is never inferred from display text, and "all decisions persisted" is
never treated as synonymous with "analysis trusted": a session can have full
session coverage (every ticker decided) and still be ``blocked`` overall
because a REQUIRED axis is missing/failed, a required conflict review is
unresolved, or required decision-influencing source lineage is missing.

## Proof of success

A terminal task is not proof of anything by itself:

  * a specialist axis "succeeds" only when a VALID persisted
    ``intel_run_specialist_outputs`` row exists for (ticker, axis) — one
    with both ``score`` and ``confidence`` set, the exact same validity gate
    ``decision_tasks_v1.aggregate_advisory_signal`` itself uses to decide
    whether an output actually influenced the decision. A terminal
    (succeeded/degraded) specialist task with no valid output is FAILED, not
    succeeded and not merely missing — the pipeline claimed to finish but
    produced nothing usable.
  * a conflict review "succeeds" only when its task reached
    ``TASK_SUCCEEDED`` AND a valid persisted ``axis=review`` output exists.
    ``TASK_DEGRADED`` is never review success. A terminal-success task with
    no valid review output is FAILED. A ``deterministic_conflict_policy_v1``
    -tagged row additionally must pass
    ``conflict_policy_v1.validate_current_conflict_row`` — stale/forged/
    mismatched is FAILED. A genuine historical LLM review row keeps its
    original validity gate untouched — never rewritten or reinterpreted.

## Source lineage

Lineage is evaluated over every VALID output recorded for a ticker — every
tracked axis output AND the review output when one exists. Per ticker this
yields one of ``full`` (every decision-influencing output has a source
reference), ``partial`` (some but not all do) or ``missing`` (none do, or
there are no valid decision-influencing outputs at all). ``source_validated``
requires ``full``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from . import conflict_policy_v1
from . import source_lineage_v1
from .run_scheduler_v1 import parse_batch_tickers
from .task_contracts_v1 import (
    AXIS_CRYPTO_MARKET,
    AXIS_ETF_EXPOSURE,
    AXIS_FUNDAMENTAL,
    AXIS_REVIEW,
    AXIS_RISK_FILING,
    AXIS_SENTIMENT,
    AXIS_TECHNICAL,
    TASK_CANCELLED,
    TASK_DEGRADED,
    TASK_FAILED,
    TASK_REVIEW_CONFLICT,
    TASK_SPECIALIST_ANALYSIS,
    TASK_SUCCEEDED,
    TICKER_DECIDED,
    TICKER_FAILED,
    TICKER_NO_CALL,
    axes_for_asset,
    required_axes_for_asset,
)

SCHEMA_VERSION = "run_trust_contract_v1"

# ── Statuses (explicit vocabulary; never inferred from display text) ─────────
STATUS_HEALTHY = "healthy"
STATUS_LIMITED = "limited"
STATUS_BLOCKED = "blocked"
STATUS_NOT_APPLICABLE = "not_applicable"
STATUS_UNKNOWN = "unknown"

AXIS_STATUS_SUCCEEDED = "succeeded"
AXIS_STATUS_MISSING = "missing"
AXIS_STATUS_FAILED = "failed"
AXIS_STATUS_NOT_APPLICABLE = "not_applicable"

REVIEW_NOT_REQUIRED = "not_required"
REVIEW_SUCCEEDED = "succeeded"
REVIEW_FAILED = "failed"
REVIEW_PENDING = "pending"
# Only ever produced by the fail-closed read-time overlay — never by
# build_run_trust_contract itself, which assumes a successful read.
REVIEW_UNKNOWN = "unknown"

LINEAGE_FULL = "full"
LINEAGE_PARTIAL = "partial"
LINEAGE_MISSING = "missing"
LINEAGE_UNKNOWN = "unknown"

# Readiness vocabulary consumed by the legacy evidence_explanation drawer
# (READY | LIMITED | SUPPRESSED | MISSING | INSUFFICIENT | NOT_APPLICABLE).
READINESS_READY = "READY"
READINESS_LIMITED = "LIMITED"
READINESS_MISSING = "MISSING"
READINESS_NOT_APPLICABLE = "NOT_APPLICABLE"

# Axes tracked for coverage/readiness (review is handled separately — it is
# advisory reconciliation, not a specialist evidence axis, and is folded
# into source lineage instead since it feeds the same aggregate signal).
TRACKED_AXES: tuple[str, ...] = (
    AXIS_FUNDAMENTAL,
    AXIS_TECHNICAL,
    AXIS_SENTIMENT,
    AXIS_RISK_FILING,
    AXIS_ETF_EXPOSURE,
    AXIS_CRYPTO_MARKET,
)

# ── Decision-constraint categories (deterministic, non-exclusive) ────────────
CONSTRAINT_EVIDENCE_QUALITY = "evidence_quality"
CONSTRAINT_SOURCE_LINEAGE = "source_lineage"
CONSTRAINT_PRICE_CONTEXT = "price_context"
CONSTRAINT_PORTFOLIO_POLICY = "portfolio_policy"
CONSTRAINT_RISK = "risk"
CONSTRAINT_CONFLICT_REVIEW = "conflict_review"
CONSTRAINT_OTHER = "other"

ALL_CONSTRAINT_CATEGORIES = frozenset({
    CONSTRAINT_EVIDENCE_QUALITY, CONSTRAINT_SOURCE_LINEAGE,
    CONSTRAINT_PRICE_CONTEXT, CONSTRAINT_PORTFOLIO_POLICY,
    CONSTRAINT_RISK, CONSTRAINT_CONFLICT_REVIEW, CONSTRAINT_OTHER,
})

_SOURCE_VALIDATED_EVIDENCE_BANDS = frozenset({"OK", "STRONG"})
# UNDERWEIGHT is room-to-add — a positive/neutral fit, never a limitation.
_PORTFOLIO_POLICY_FIT_BANDS = frozenset({"OVERWEIGHT", "BREACH", "BLOCKED"})
_PRICE_CONTEXT_LIMITED_BANDS = frozenset({"SUPPRESSED", "FULL", "EXPENSIVE"})
_RISK_ELEVATED_BANDS = frozenset({"HIGH", "CRITICAL"})
_THIN_EVIDENCE_BANDS = frozenset({"THIN", "SUPPRESSED"})

# Substrings of KNOWN persisted blocker text (decision_policy_v1.decide()) —
# anything in the persisted ``blockers`` list that matches NONE of these is a
# real constraint the band-based checks don't already represent (e.g. "
# Attractiveness signal absent or weak.") and must surface as CONSTRAINT_OTHER
# rather than silently disappear, even when other categories already apply.
_KNOWN_BLOCKER_SUBSTRINGS: dict[str, tuple[str, ...]] = {
    CONSTRAINT_EVIDENCE_QUALITY: ("insufficient evidence",),
    CONSTRAINT_PRICE_CONTEXT: ("price context",),
    CONSTRAINT_PORTFOLIO_POLICY: ("portfolio fit",),
    CONSTRAINT_RISK: ("risk too elevated", "critical risk", "high risk with"),
}


def _is_valid_output(output: Optional[dict[str, Any]]) -> bool:
    """Same validity gate ``aggregate_advisory_signal`` uses — a terminal
    task claiming success with no usable score/confidence is not proof of
    anything."""
    if not output:
        return False
    return output.get("score") is not None and output.get("confidence") is not None


def _output_lineage_status(
    output: dict[str, Any],
    *,
    ticker_outputs: Optional[list[dict[str, Any]]] = None,
) -> str:
    """Structural, axis/ticker-aware lineage status for one persisted
    specialist/review output — validates against the output's OWN axis and
    ticker (a manifest claiming a different axis, or carrying a reference
    for a different ticker, is rejected, not trusted).

    A review output's lineage claim is additionally cross-validated against
    the CURRENT valid non-review outputs for the same ticker
    (``ticker_outputs``) — a review claiming it reconciled a different axis
    set, or different per-axis lineage statuses, than what those outputs
    presently show is treated as missing lineage, never full."""
    axis = output.get("axis")
    ticker = output.get("ticker")
    if axis == AXIS_REVIEW:
        current_non_review = [
            o
            for o in (ticker_outputs or [])
            if o is not output and o.get("axis") != AXIS_REVIEW
        ]
        manifest = source_lineage_v1.validate_review_against_current_outputs(
            output.get("evidence_refs"),
            ticker=ticker,
            current_non_review_outputs=current_non_review,
        )
        return manifest.get("status") if manifest else source_lineage_v1.LINEAGE_MISSING
    return source_lineage_v1.output_lineage_status(
        output.get("evidence_refs"),
        expected_axis=axis,
        expected_ticker=ticker,
    )


def classify_decision_constraints(
    *,
    decision_record: dict[str, Any],
    lineage_status: str,
    review_status: str,
) -> list[str]:
    """Deterministic, non-exclusive classifier over PERSISTED decision fields.

    Each independent decision axis maps to its own category, so a suppressed
    price context or a portfolio-policy constraint never gets relabeled as
    an evidence-quality failure, and a positive/assessed band (UNDERWEIGHT
    fit) never renders as a limitation. A ticker can carry more than one
    category at once (e.g. a speculative holding with a failed required
    review is both ``portfolio_policy`` AND ``conflict_review`` — never
    conflated into one). The persisted ``blockers`` list is also parsed
    directly: any blocker text not matched by a known category (e.g. a weak
    attractiveness signal) still surfaces as ``other`` — additively, never
    replacing whatever band-based categories already apply.
    """
    categories: set[str] = set()

    evidence_quality = str(decision_record.get("evidence_quality") or "").upper()
    price_context = str(decision_record.get("price_context") or "").upper()
    portfolio_fit = str(decision_record.get("portfolio_fit") or "").upper()
    risk_band = str(decision_record.get("risk_band") or "").upper()

    if evidence_quality in _THIN_EVIDENCE_BANDS:
        categories.add(CONSTRAINT_EVIDENCE_QUALITY)
    if price_context in _PRICE_CONTEXT_LIMITED_BANDS:
        categories.add(CONSTRAINT_PRICE_CONTEXT)
    if portfolio_fit in _PORTFOLIO_POLICY_FIT_BANDS:
        categories.add(CONSTRAINT_PORTFOLIO_POLICY)
    if risk_band in _RISK_ELEVATED_BANDS:
        categories.add(CONSTRAINT_RISK)
    if lineage_status != LINEAGE_FULL:
        categories.add(CONSTRAINT_SOURCE_LINEAGE)
    if review_status in (REVIEW_FAILED, REVIEW_PENDING, REVIEW_UNKNOWN):
        categories.add(CONSTRAINT_CONFLICT_REVIEW)

    for blocker in decision_record.get("blockers") or []:
        text = str(blocker).lower()
        matched = any(
            any(sub in text for sub in subs)
            for subs in _KNOWN_BLOCKER_SUBSTRINGS.values()
        )
        if not matched:
            categories.add(CONSTRAINT_OTHER)

    # CONSTRAINT_OTHER is added ONLY by the blocker-parsing loop above, when
    # a REAL persisted blocker doesn't match a known category. A clean
    # decision with no persisted blockers and no band-based constraint must
    # return an empty list — fabricating "other" from an empty category set
    # would manufacture a limitation that doesn't exist.
    return sorted(categories)


def is_source_validated(
    *,
    evidence_quality: str,
    lineage_status: str,
    review_status: str,
) -> bool:
    """A ticker is source-validated ONLY with FULL lineage across every
    decision-influencing output, a real evidence band, AND a conflict review
    that is exactly not-required or succeeded — never merely "not failed"
    (a still-pending required review is not validated either)."""
    return (
        lineage_status == LINEAGE_FULL
        and str(evidence_quality or "").upper() in _SOURCE_VALIDATED_EVIDENCE_BANDS
        and review_status in (REVIEW_NOT_REQUIRED, REVIEW_SUCCEEDED)
    )


def _axis_task_states_for_ticker(
    tasks: list[dict[str, Any]], ticker: str
) -> dict[str, str]:
    """lane(axis) -> most-advanced known task state for this ticker.

    ``specialist_analysis`` tasks are batch-scoped (``batch_key`` encodes the
    tickers in the batch); a ticker's own row has no direct task FK for this
    task type, so batch membership is parsed the same way the scheduler
    parses it.
    """
    states: dict[str, str] = {}
    for task in tasks:
        if str(task.get("task_type") or "") != TASK_SPECIALIST_ANALYSIS:
            continue
        if ticker not in parse_batch_tickers(str(task.get("batch_key") or "")):
            continue
        lane = str(task.get("lane") or "")
        state = str(task.get("state") or "")
        prior = states.get(lane)
        if prior is None or state in (TASK_SUCCEEDED, TASK_DEGRADED):
            states[lane] = state
    return states


def _review_status_for_ticker(
    tasks: list[dict[str, Any]],
    ticker: str,
    *,
    has_valid_review_output: bool,
) -> str:
    review_states = {
        str(task.get("state") or "")
        for task in tasks
        if str(task.get("task_type") or "") == TASK_REVIEW_CONFLICT
        and str(task.get("ticker") or "") == ticker
    }
    if not review_states:
        return REVIEW_NOT_REQUIRED
    if TASK_SUCCEEDED in review_states:
        # A terminal-success review task with no valid persisted review
        # output is NOT success — the reconciliation claimed to finish but
        # produced nothing usable.
        return REVIEW_SUCCEEDED if has_valid_review_output else REVIEW_FAILED
    if TASK_DEGRADED in review_states or TASK_FAILED in review_states or TASK_CANCELLED in review_states:
        return REVIEW_FAILED
    return REVIEW_PENDING


def _axis_status(
    *,
    axis: str,
    ticker: str,
    asset_type: str,
    valid_outputs_by_ticker_axis: dict[tuple[str, str], dict[str, Any]],
    task_states: dict[str, str],
) -> str:
    if axis not in axes_for_asset(asset_type):
        return AXIS_STATUS_NOT_APPLICABLE
    if (ticker, axis) in valid_outputs_by_ticker_axis:
        return AXIS_STATUS_SUCCEEDED
    state = task_states.get(axis)
    if state == TASK_FAILED:
        return AXIS_STATUS_FAILED
    if state in (TASK_SUCCEEDED, TASK_DEGRADED):
        # Terminal task, no valid output — the pipeline claimed to finish
        # but produced nothing usable. Never counted as merely "missing".
        return AXIS_STATUS_FAILED
    return AXIS_STATUS_MISSING


def _readiness_label(status: str, confidence: Optional[float]) -> str:
    if status == AXIS_STATUS_SUCCEEDED:
        if confidence is not None and confidence >= 0.6:
            return READINESS_READY
        return READINESS_LIMITED
    if status == AXIS_STATUS_NOT_APPLICABLE:
        return READINESS_NOT_APPLICABLE
    return READINESS_MISSING


def _has_valid_review_output(
    review_row: Optional[dict[str, Any]],
    *,
    ticker: str,
    non_review_outputs_valid: list[dict[str, Any]],
    task_succeeded: bool,
    weight_pct: Optional[float],
) -> bool:
    """Legacy (pre-deterministic) rows keep the ORIGINAL validity gate —
    never reinterpreted. A ``deterministic_conflict_policy_v1``-tagged row
    counts as successful ONLY when it is a CURRENT, valid resolution."""
    if review_row is None:
        return False
    if str(review_row.get("model") or "") != conflict_policy_v1.SCHEMA_VERSION:
        return True
    if not task_succeeded:
        return False
    return conflict_policy_v1.validate_current_conflict_row(
        review_row, ticker=ticker, non_review_outputs=non_review_outputs_valid,
        weight_pct=weight_pct,
    ) is not None


def _lineage_status_for_outputs(outputs: list[dict[str, Any]]) -> str:
    """Per-ticker lineage over every decision-influencing output.

    ``full`` only when EVERY output's own structural lineage is full;
    ``partial`` when at least one output carries valid (full or partial)
    lineage but not every output does; ``missing`` when none do. A
    per-output ``full`` status already requires every evidence lane THAT
    axis was actually given to carry a reference — this aggregation never
    downgrades that by re-counting raw ref presence.
    """
    if not outputs:
        return LINEAGE_MISSING
    statuses = [_output_lineage_status(o, ticker_outputs=outputs) for o in outputs]
    if all(s == source_lineage_v1.LINEAGE_FULL for s in statuses):
        return LINEAGE_FULL
    if any(
        s in (source_lineage_v1.LINEAGE_FULL, source_lineage_v1.LINEAGE_PARTIAL)
        for s in statuses
    ):
        return LINEAGE_PARTIAL
    return LINEAGE_MISSING


def build_run_trust_contract(
    *,
    session: dict[str, Any],
    ticker_rows: list[dict[str, Any]],
    tasks: list[dict[str, Any]],
    specialist_outputs: list[dict[str, Any]],
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Build the complete run-trust projection for one session.

    Deterministic over its inputs alone — same rows in, same contract out.
    Integrated identically by session-native publication (persisted) and by
    read-time enrichment of a pre-existing snapshot (not persisted, additive
    overlay only). ASSUMES the inputs are a successful, complete read — the
    caller must fail closed (``unknown_overlay_contract``) instead of
    calling this function when reads are unavailable/suspect.
    """
    now = now or datetime.now(timezone.utc)
    session_id = str(session.get("id") or "")
    tasks = tasks or []

    asset_type_by_ticker = {
        str(r.get("ticker") or ""): str(r.get("asset_type") or "equity")
        for r in ticker_rows
    }

    # Valid (score+confidence present) outputs only — the same set that
    # actually influences aggregate_advisory_signal. Keyed both by
    # (ticker, axis) for lookup and by ticker for lineage aggregation across
    # every decision-influencing output, review included.
    valid_outputs_by_ticker_axis: dict[tuple[str, str], dict[str, Any]] = {}
    valid_outputs_by_ticker_all_axes: dict[str, list[dict[str, Any]]] = {}
    valid_outputs_by_ticker_tracked_axes: dict[str, list[dict[str, Any]]] = {}
    for output in specialist_outputs:
        ticker = str(output.get("ticker") or "")
        axis = str(output.get("axis") or "")
        if not _is_valid_output(output):
            continue
        valid_outputs_by_ticker_axis[(ticker, axis)] = output
        valid_outputs_by_ticker_all_axes.setdefault(ticker, []).append(output)
        if axis != AXIS_REVIEW:
            valid_outputs_by_ticker_tracked_axes.setdefault(ticker, []).append(output)

    # ── Session coverage ──────────────────────────────────────────────────
    decided_rows = [r for r in ticker_rows if str(r.get("state")) == TICKER_DECIDED]
    no_call_rows = [r for r in ticker_rows if str(r.get("state")) == TICKER_NO_CALL]
    failed_rows = [r for r in ticker_rows if str(r.get("state")) == TICKER_FAILED]
    known_states = {TICKER_DECIDED, TICKER_NO_CALL, TICKER_FAILED}
    unaccounted_rows = [
        r for r in ticker_rows if str(r.get("state")) not in known_states
    ]
    frozen_count = len(ticker_rows)
    publication_complete = not unaccounted_rows

    session_coverage = {
        "frozen_holding_count": frozen_count,
        "decided_count": len(decided_rows),
        "no_call_count": len(no_call_rows),
        "failed_count": len(failed_rows),
        "unaccounted_count": len(unaccounted_rows),
        "publication_complete": publication_complete,
    }

    # ── Per-ticker axis status + conflict-review status + lineage ─────────
    ticker_axis_status: dict[str, dict[str, str]] = {}
    review_status_by_ticker: dict[str, str] = {}
    lineage_status_by_ticker: dict[str, str] = {}
    for row in ticker_rows:
        ticker = str(row.get("ticker") or "")
        asset_type = asset_type_by_ticker.get(ticker, "equity")
        task_states = _axis_task_states_for_ticker(tasks, ticker)
        ticker_axis_status[ticker] = {
            axis: _axis_status(
                axis=axis,
                ticker=ticker,
                asset_type=asset_type,
                valid_outputs_by_ticker_axis=valid_outputs_by_ticker_axis,
                task_states=task_states,
            )
            for axis in TRACKED_AXES
        }
        review_row = valid_outputs_by_ticker_axis.get((ticker, AXIS_REVIEW))
        review_task_succeeded = any(
            str(t.get("task_type") or "") == TASK_REVIEW_CONFLICT
            and str(t.get("ticker") or "") == ticker
            and str(t.get("state") or "") == TASK_SUCCEEDED
            for t in tasks
        )
        has_valid_review_output = _has_valid_review_output(
            review_row, ticker=ticker,
            non_review_outputs_valid=valid_outputs_by_ticker_tracked_axes.get(ticker, []),
            task_succeeded=review_task_succeeded,
            weight_pct=row.get("portfolio_weight_pct"),
        )
        review_status_by_ticker[ticker] = _review_status_for_ticker(
            tasks, ticker, has_valid_review_output=has_valid_review_output,
        )
        lineage_status_by_ticker[ticker] = _lineage_status_for_outputs(
            valid_outputs_by_ticker_all_axes.get(ticker, [])
        )

    # ── Axis (specialist) coverage, session-wide — required vs optional ───
    axis_coverage: dict[str, Any] = {}
    for axis in TRACKED_AXES:
        req_succeeded = req_missing = req_failed = 0
        opt_succeeded = opt_missing = opt_failed = 0
        not_applicable = 0
        for ticker, per_axis in ticker_axis_status.items():
            status = per_axis[axis]
            if status == AXIS_STATUS_NOT_APPLICABLE:
                not_applicable += 1
                continue
            asset_type = asset_type_by_ticker.get(ticker, "equity")
            required = axis in required_axes_for_asset(asset_type)
            if status == AXIS_STATUS_SUCCEEDED:
                if required:
                    req_succeeded += 1
                else:
                    opt_succeeded += 1
            elif status == AXIS_STATUS_MISSING:
                if required:
                    req_missing += 1
                else:
                    opt_missing += 1
            elif status == AXIS_STATUS_FAILED:
                if required:
                    req_failed += 1
                else:
                    opt_failed += 1
        axis_coverage[axis] = {
            # Aggregate (required + optional) — kept for existing frontend
            # chips (e.g. "Technical 31/31") that don't need the split.
            "expected_count": req_succeeded + req_missing + req_failed
                + opt_succeeded + opt_missing + opt_failed,
            "succeeded_count": req_succeeded + opt_succeeded,
            "missing_count": req_missing + opt_missing,
            "failed_count": req_failed + opt_failed,
            "not_applicable_count": not_applicable,
            # Required vs optional split — the authority for overall_status.
            "required_expected_count": req_succeeded + req_missing + req_failed,
            "required_succeeded_count": req_succeeded,
            "required_missing_count": req_missing,
            "required_failed_count": req_failed,
            "optional_expected_count": opt_succeeded + opt_missing + opt_failed,
            "optional_succeeded_count": opt_succeeded,
            "optional_missing_count": opt_missing,
            "optional_failed_count": opt_failed,
        }

    any_required_axis_gap = any(
        counts["required_missing_count"] + counts["required_failed_count"] > 0
        for counts in axis_coverage.values()
    )
    any_optional_axis_gap = any(
        counts["optional_missing_count"] + counts["optional_failed_count"] > 0
        for counts in axis_coverage.values()
    )

    # ── Conflict-review coverage, session-wide ─────────────────────────────
    required_tickers = sorted(
        t for t, s in review_status_by_ticker.items() if s != REVIEW_NOT_REQUIRED
    )
    succeeded_review_tickers = sorted(
        t for t, s in review_status_by_ticker.items() if s == REVIEW_SUCCEEDED
    )
    failed_review_tickers = sorted(
        t for t, s in review_status_by_ticker.items() if s == REVIEW_FAILED
    )
    pending_review_tickers = sorted(
        t for t, s in review_status_by_ticker.items() if s == REVIEW_PENDING
    )
    conflict_review_coverage = {
        "required_count": len(required_tickers),
        "succeeded_count": len(succeeded_review_tickers),
        "failed_count": len(failed_review_tickers),
        "pending_count": len(pending_review_tickers),
        "required_tickers": required_tickers,
        "succeeded_tickers": succeeded_review_tickers,
        "failed_tickers": failed_review_tickers,
        "pending_tickers": pending_review_tickers,
    }
    any_required_review_unresolved = bool(failed_review_tickers or pending_review_tickers)

    # ── Source lineage, session-wide (decided tickers only — the only ones
    # with a visible action and a real decision to have lineage over) ─────
    decided_tickers = sorted(str(r.get("ticker") or "") for r in decided_rows)
    lineage_full_tickers = sorted(
        t for t in decided_tickers if lineage_status_by_ticker.get(t) == LINEAGE_FULL
    )
    lineage_partial_tickers = sorted(
        t for t in decided_tickers if lineage_status_by_ticker.get(t) == LINEAGE_PARTIAL
    )
    lineage_missing_tickers = sorted(
        t for t in decided_tickers if lineage_status_by_ticker.get(t) == LINEAGE_MISSING
    )

    # Full/partial/missing tracked SEPARATELY — an all-partial run must never
    # collapse into "outputs_with_refs == total" and read as healthy (the
    # release-blocker defect this patch fixes: partial output lineage is
    # NOT full lineage, even though it is not "missing" either).
    outputs_full_lineage = sum(
        1 for outs in valid_outputs_by_ticker_all_axes.values()
        for o in outs if _output_lineage_status(o, ticker_outputs=outs) == LINEAGE_FULL
    )
    outputs_partial_lineage = sum(
        1 for outs in valid_outputs_by_ticker_all_axes.values()
        for o in outs if _output_lineage_status(o, ticker_outputs=outs) == LINEAGE_PARTIAL
    )
    total_valid_outputs = sum(len(outs) for outs in valid_outputs_by_ticker_all_axes.values())
    outputs_missing_lineage = total_valid_outputs - outputs_full_lineage - outputs_partial_lineage
    # Back-compat aggregate counts (existing response fields) — "with refs"
    # means ANY nonmissing lineage (full or partial), matching the prior
    # public meaning of this field.
    outputs_with_refs = outputs_full_lineage + outputs_partial_lineage
    outputs_missing_refs = outputs_missing_lineage

    if total_valid_outputs == 0:
        source_health_status = STATUS_UNKNOWN
        # Distinguishes a SUCCESSFUL read that genuinely found zero valid
        # specialist outputs from a fail-closed read failure
        # (unknown_overlay_contract below, which carries its own reason) —
        # both produce status="unknown" but mean different things.
        source_health_reason = (
            "No specialist outputs were recorded for this session yet — "
            "source health could not be established from zero outputs."
        )
        source_health = {"status": source_health_status, "reason": source_health_reason}
    elif outputs_full_lineage == total_valid_outputs:
        # EVERY valid decision-influencing output has structurally valid
        # FULL lineage — the only condition that may ever read healthy.
        source_health_status = STATUS_HEALTHY
        source_health = {"status": source_health_status}
    elif outputs_full_lineage == 0 and outputs_partial_lineage == 0:
        # None sourced at all.
        source_health_status = STATUS_BLOCKED
        source_health = {"status": source_health_status}
    else:
        # Any partial output, or a mix of full and missing — never healthy.
        source_health_status = STATUS_LIMITED
        source_health = {"status": source_health_status}

    source_lineage = {
        "outputs_with_source_refs": outputs_with_refs,
        "outputs_missing_source_refs": outputs_missing_refs,
        "outputs_full_lineage": outputs_full_lineage,
        "outputs_partial_lineage": outputs_partial_lineage,
        "outputs_missing_lineage": outputs_missing_lineage,
        # Back-compat ticker lists (nonempty lineage anywhere) — superseded
        # by the explicit full/partial/missing lists below for new readers.
        "tickers_with_lineage": lineage_full_tickers,
        "tickers_missing_lineage": lineage_missing_tickers,
        "tickers_full_lineage": lineage_full_tickers,
        "tickers_partial_lineage": lineage_partial_tickers,
        "tickers_missing_lineage_full": lineage_missing_tickers,
    }
    any_required_lineage_missing = bool(lineage_missing_tickers)
    any_lineage_partial = bool(lineage_partial_tickers)

    # ── Per-ticker trust entries ────────────────────────────────────────
    ticker_trust: list[dict[str, Any]] = []
    blocking_reasons: list[str] = []
    warnings: list[str] = []

    for row in sorted(ticker_rows, key=lambda r: str(r.get("ticker") or "")):
        ticker = str(row.get("ticker") or "")
        state = str(row.get("state") or "")
        asset_type = asset_type_by_ticker.get(ticker, "equity")
        review_status = review_status_by_ticker.get(ticker, REVIEW_NOT_REQUIRED)
        lineage_status = lineage_status_by_ticker.get(ticker, LINEAGE_MISSING)
        decision_record = row.get("decision") or {}
        evidence_quality = str(decision_record.get("evidence_quality") or "")

        per_axis_status = ticker_axis_status.get(ticker, {})
        required_axes = set(required_axes_for_asset(asset_type))
        axis_readiness = {
            axis: _readiness_label(
                per_axis_status.get(axis, AXIS_STATUS_MISSING),
                (
                    (valid_outputs_by_ticker_axis.get((ticker, axis)) or {}).get("confidence")
                ),
            )
            for axis in TRACKED_AXES
        }
        required_axis_gap_ticker = any(
            per_axis_status.get(axis) in (AXIS_STATUS_MISSING, AXIS_STATUS_FAILED)
            for axis in required_axes
        )
        optional_axis_gap_ticker = any(
            per_axis_status.get(axis) in (AXIS_STATUS_MISSING, AXIS_STATUS_FAILED)
            for axis in TRACKED_AXES if axis not in required_axes
        )

        decision_bands = {
            "evidence_quality": decision_record.get("evidence_quality"),
            "price_context": decision_record.get("price_context"),
            "portfolio_fit": decision_record.get("portfolio_fit"),
            "risk_band": decision_record.get("risk_band"),
            "attractiveness": decision_record.get("attractiveness"),
        }

        if state == TICKER_DECIDED:
            constraints = classify_decision_constraints(
                decision_record=decision_record,
                lineage_status=lineage_status,
                review_status=review_status,
            )
            source_validated = is_source_validated(
                evidence_quality=evidence_quality,
                lineage_status=lineage_status,
                review_status=review_status,
            )
            if (
                required_axis_gap_ticker
                or review_status in (REVIEW_FAILED, REVIEW_PENDING, REVIEW_UNKNOWN)
                or lineage_status == LINEAGE_MISSING
            ):
                trust_status = STATUS_BLOCKED
            elif optional_axis_gap_ticker or lineage_status == LINEAGE_PARTIAL:
                trust_status = STATUS_LIMITED
            else:
                trust_status = STATUS_HEALTHY
        elif state == TICKER_NO_CALL:
            constraints = []
            source_validated = False
            trust_status = STATUS_LIMITED
        elif state == TICKER_FAILED:
            constraints = []
            source_validated = False
            trust_status = STATUS_BLOCKED
        else:
            constraints = []
            source_validated = False
            trust_status = STATUS_UNKNOWN

        ticker_trust.append({
            "ticker": ticker,
            "state": state,
            "trust_status": trust_status,
            "axis_status": dict(per_axis_status),
            "axis_readiness": axis_readiness,
            "required_axis_gap": required_axis_gap_ticker,
            "optional_axis_gap": optional_axis_gap_ticker,
            "conflict_review_status": review_status,
            "lineage_status": lineage_status,
            "source_validated": source_validated,
            "decision_constraints": constraints,
            "decision_bands": decision_bands,
        })

        if state == TICKER_DECIDED and required_axis_gap_ticker:
            blocking_reasons.append(
                f"{ticker}: a required specialist axis is missing or failed this run."
            )
        if review_status in (REVIEW_FAILED, REVIEW_PENDING):
            verb = "failed" if review_status == REVIEW_FAILED else "is still pending"
            blocking_reasons.append(
                f"{ticker}: required conflict review {verb} — action shown "
                "without successful conflict reconciliation."
            )
        if state == TICKER_DECIDED and lineage_status == LINEAGE_MISSING:
            blocking_reasons.append(
                f"{ticker}: no decision-influencing output carries a source reference."
            )
        if state == TICKER_FAILED:
            blocking_reasons.append(f"{ticker}: analysis could not finish this run.")

    if total_valid_outputs > 0 and outputs_with_refs == 0:
        warnings.append(
            "No specialist outputs in this session carry source references — "
            "source lineage is not established for any holding."
        )
    elif outputs_missing_refs > 0:
        warnings.append(
            f"{outputs_missing_refs} of {total_valid_outputs} specialist outputs are "
            "missing source references."
        )
    if failed_review_tickers:
        warnings.append(
            f"{len(failed_review_tickers)} required conflict review(s) failed — "
            "affected holdings are shown without successful conflict reconciliation."
        )
    if pending_review_tickers:
        warnings.append(
            f"{len(pending_review_tickers)} required conflict review(s) are still pending."
        )

    # ── Overall status (deterministic; never "decisions persisted" == trust) ─
    if unaccounted_rows:
        overall_status = STATUS_UNKNOWN
        blocking_reasons.insert(
            0,
            "Publication is incomplete — not every frozen holding reached a "
            "terminal state.",
        )
    elif frozen_count == 0:
        overall_status = STATUS_NOT_APPLICABLE
    elif (
        failed_rows
        or any_required_axis_gap
        or any_required_review_unresolved
        or any_required_lineage_missing
    ):
        overall_status = STATUS_BLOCKED
    elif no_call_rows or any_optional_axis_gap or any_lineage_partial:
        overall_status = STATUS_LIMITED
    else:
        overall_status = STATUS_HEALTHY

    return {
        "schema_version": SCHEMA_VERSION,
        "run_session_id": session_id,
        "generated_at": now.isoformat(),
        "overall_status": overall_status,
        "session_coverage": session_coverage,
        "axis_coverage": axis_coverage,
        "conflict_review_coverage": conflict_review_coverage,
        "source_lineage": source_lineage,
        "source_health": source_health,
        "ticker_trust": ticker_trust,
        "blocking_reasons": blocking_reasons,
        "warnings": warnings,
    }


def unknown_overlay_contract(
    *, run_session_id: str, reason: str, now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Fail-closed trust contract for when durable state cannot be read at
    all (missing session row, missing ticker rows, a suspiciously empty task
    read, or a raised read failure). NEVER preserves an old snapshot's
    optimistic ``source_validated``/committee status — this is an explicit,
    honest "we don't know" projection, not a guess. ``ticker_trust`` is
    intentionally empty: the caller applies a matching neutral overlay to
    every existing card (see ``intel_v3_service._enrich_snapshot_with_run_trust_contract``)
    rather than fabricating per-ticker detail this function has no rows for.
    """
    now = now or datetime.now(timezone.utc)
    return {
        "schema_version": SCHEMA_VERSION,
        "run_session_id": run_session_id,
        "generated_at": now.isoformat(),
        "overall_status": STATUS_UNKNOWN,
        "session_coverage": {
            "frozen_holding_count": 0,
            "decided_count": 0,
            "no_call_count": 0,
            "failed_count": 0,
            "unaccounted_count": 0,
            "publication_complete": False,
        },
        "axis_coverage": {},
        "conflict_review_coverage": {
            "required_count": 0,
            "succeeded_count": 0,
            "failed_count": 0,
            "pending_count": 0,
            "required_tickers": [],
            "succeeded_tickers": [],
            "failed_tickers": [],
            "pending_tickers": [],
        },
        "source_lineage": {
            "outputs_with_source_refs": 0,
            "outputs_missing_source_refs": 0,
            "outputs_full_lineage": 0,
            "outputs_partial_lineage": 0,
            "outputs_missing_lineage": 0,
            "tickers_with_lineage": [],
            "tickers_missing_lineage": [],
            "tickers_full_lineage": [],
            "tickers_partial_lineage": [],
            "tickers_missing_lineage_full": [],
        },
        "source_health": {"status": STATUS_UNKNOWN, "reason": reason},
        "ticker_trust": [],
        "blocking_reasons": [reason],
        "warnings": [reason],
    }
