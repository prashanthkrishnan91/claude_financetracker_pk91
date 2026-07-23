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
existed) own fetching the rows and passing them in. The SAME projection runs
in both places so a pre-existing snapshot displays truthful trust information
without rerunning collectors, providers or LLMs.

Trust is never inferred from display text, and "all decisions persisted" is
never treated as synonymous with "analysis trusted": a session can have full
session coverage (every ticker decided) and still be ``blocked`` overall
because required conflict reviews failed or no output carries a source
reference.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

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

# Readiness vocabulary consumed by the legacy evidence_explanation drawer
# (READY | LIMITED | SUPPRESSED | MISSING | INSUFFICIENT | NOT_APPLICABLE).
READINESS_READY = "READY"
READINESS_LIMITED = "LIMITED"
READINESS_MISSING = "MISSING"
READINESS_NOT_APPLICABLE = "NOT_APPLICABLE"

# Axes tracked for coverage/readiness (review is handled separately — it is
# advisory reconciliation, not a specialist evidence axis).
TRACKED_AXES: tuple[str, ...] = (
    AXIS_FUNDAMENTAL,
    AXIS_TECHNICAL,
    AXIS_SENTIMENT,
    AXIS_RISK_FILING,
    AXIS_ETF_EXPOSURE,
    AXIS_CRYPTO_MARKET,
)

# A card is not "source validated" unless at least this many distinct source
# references are present. PR 2 owns generating real references; this PR only
# refuses to fabricate a validated label from evidence_band alone.
MIN_SOURCE_REF_COUNT = 1

# ── Decision-constraint categories (deterministic, non-exclusive) ────────────
CONSTRAINT_EVIDENCE_QUALITY = "evidence_quality"
CONSTRAINT_SOURCE_LINEAGE = "source_lineage"
CONSTRAINT_PRICE_CONTEXT = "price_context"
CONSTRAINT_PORTFOLIO_POLICY = "portfolio_policy"
CONSTRAINT_RISK = "risk"
CONSTRAINT_CONFLICT_REVIEW = "conflict_review"
CONSTRAINT_OTHER = "other"

_SOURCE_VALIDATED_EVIDENCE_BANDS = frozenset({"OK", "STRONG"})
_PORTFOLIO_POLICY_FIT_BANDS = frozenset(
    {"UNDERWEIGHT", "OVERWEIGHT", "BREACH", "BLOCKED"}
)
_PRICE_CONTEXT_LIMITED_BANDS = frozenset({"SUPPRESSED", "FULL", "EXPENSIVE"})
_RISK_ELEVATED_BANDS = frozenset({"HIGH", "CRITICAL"})
_THIN_EVIDENCE_BANDS = frozenset({"THIN", "SUPPRESSED"})


def _has_min_source_lineage(refs: Any) -> bool:
    if not refs:
        return False
    try:
        return len([r for r in refs if r]) >= MIN_SOURCE_REF_COUNT
    except TypeError:
        return False


def classify_decision_constraints(
    *,
    decision_record: dict[str, Any],
    has_source_lineage: bool,
    review_status: str,
) -> list[str]:
    """Deterministic, non-exclusive classifier over PERSISTED decision fields.

    Replaces "any nonempty blocker == evidence blocked": each independent
    decision axis maps to its own category, so a suppressed price context
    (universal today) or a portfolio-policy constraint never gets relabeled
    as an evidence-quality failure. A ticker can carry more than one category
    at once (e.g. a speculative holding with a failed required review is both
    ``portfolio_policy`` AND ``conflict_review`` — never conflated into one).
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
    if not has_source_lineage:
        categories.add(CONSTRAINT_SOURCE_LINEAGE)
    if review_status == REVIEW_FAILED:
        categories.add(CONSTRAINT_CONFLICT_REVIEW)

    if not categories:
        categories.add(CONSTRAINT_OTHER)
    return sorted(categories)


def is_source_validated(
    *,
    evidence_quality: str,
    has_source_lineage: bool,
    review_status: str,
) -> bool:
    """A ticker is source-validated ONLY with real lineage, a real evidence
    band AND no failed required conflict review — never from evidence_band
    alone."""
    return (
        has_source_lineage
        and str(evidence_quality or "").upper() in _SOURCE_VALIDATED_EVIDENCE_BANDS
        and review_status != REVIEW_FAILED
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


def _review_status_for_ticker(tasks: list[dict[str, Any]], ticker: str) -> str:
    review_states = {
        str(task.get("state") or "")
        for task in tasks
        if str(task.get("task_type") or "") == TASK_REVIEW_CONFLICT
        and str(task.get("ticker") or "") == ticker
    }
    if not review_states:
        return REVIEW_NOT_REQUIRED
    if TASK_SUCCEEDED in review_states or TASK_DEGRADED in review_states:
        return REVIEW_SUCCEEDED
    if TASK_FAILED in review_states or TASK_CANCELLED in review_states:
        return REVIEW_FAILED
    return REVIEW_PENDING


def _axis_status(
    *,
    axis: str,
    ticker: str,
    asset_type: str,
    outputs_by_ticker_axis: dict[tuple[str, str], dict[str, Any]],
    task_states: dict[str, str],
) -> str:
    if axis not in axes_for_asset(asset_type):
        return AXIS_STATUS_NOT_APPLICABLE
    if (ticker, axis) in outputs_by_ticker_axis:
        return AXIS_STATUS_SUCCEEDED
    if task_states.get(axis) == TASK_FAILED:
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
    overlay only).
    """
    now = now or datetime.now(timezone.utc)
    session_id = str(session.get("id") or "")
    tasks = tasks or []

    non_review_outputs = [
        o for o in specialist_outputs if str(o.get("axis") or "") != AXIS_REVIEW
    ]
    outputs_by_ticker_axis: dict[tuple[str, str], dict[str, Any]] = {
        (str(o.get("ticker") or ""), str(o.get("axis") or "")): o
        for o in non_review_outputs
    }
    outputs_by_ticker: dict[str, list[dict[str, Any]]] = {}
    for output in non_review_outputs:
        outputs_by_ticker.setdefault(str(output.get("ticker") or ""), []).append(output)

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

    # ── Per-ticker axis status + conflict-review status ──────────────────
    ticker_axis_status: dict[str, dict[str, str]] = {}
    review_status_by_ticker: dict[str, str] = {}
    for row in ticker_rows:
        ticker = str(row.get("ticker") or "")
        asset_type = str(row.get("asset_type") or "equity")
        task_states = _axis_task_states_for_ticker(tasks, ticker)
        ticker_axis_status[ticker] = {
            axis: _axis_status(
                axis=axis,
                ticker=ticker,
                asset_type=asset_type,
                outputs_by_ticker_axis=outputs_by_ticker_axis,
                task_states=task_states,
            )
            for axis in TRACKED_AXES
        }
        review_status_by_ticker[ticker] = _review_status_for_ticker(tasks, ticker)

    # ── Axis (specialist) coverage, session-wide ──────────────────────────
    axis_coverage: dict[str, Any] = {}
    for axis in TRACKED_AXES:
        succeeded = missing = failed = not_applicable = 0
        for per_axis in ticker_axis_status.values():
            status = per_axis[axis]
            if status == AXIS_STATUS_SUCCEEDED:
                succeeded += 1
            elif status == AXIS_STATUS_MISSING:
                missing += 1
            elif status == AXIS_STATUS_FAILED:
                failed += 1
            else:
                not_applicable += 1
        axis_coverage[axis] = {
            "expected_count": succeeded + missing + failed,
            "succeeded_count": succeeded,
            "missing_count": missing,
            "failed_count": failed,
            "not_applicable_count": not_applicable,
        }

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

    # ── Source lineage ──────────────────────────────────────────────────
    outputs_with_refs = sum(
        1 for o in non_review_outputs if _has_min_source_lineage(o.get("evidence_refs"))
    )
    outputs_missing_refs = len(non_review_outputs) - outputs_with_refs

    tickers_with_lineage: list[str] = []
    tickers_missing_lineage: list[str] = []
    ticker_has_lineage: dict[str, bool] = {}
    for row in ticker_rows:
        ticker = str(row.get("ticker") or "")
        bundle = row.get("evidence_bundle") or {}
        bundle_has_refs = _has_min_source_lineage(bundle.get("source_refs"))
        output_has_refs = any(
            _has_min_source_lineage(o.get("evidence_refs"))
            for o in outputs_by_ticker.get(ticker, [])
        )
        has_lineage = bundle_has_refs or output_has_refs
        ticker_has_lineage[ticker] = has_lineage
        (tickers_with_lineage if has_lineage else tickers_missing_lineage).append(ticker)

    total_outputs = len(non_review_outputs)
    if total_outputs == 0:
        source_health_status = STATUS_UNKNOWN
    elif outputs_with_refs == 0:
        source_health_status = STATUS_BLOCKED
    elif outputs_missing_refs > 0:
        source_health_status = STATUS_LIMITED
    else:
        source_health_status = STATUS_HEALTHY

    source_lineage = {
        "outputs_with_source_refs": outputs_with_refs,
        "outputs_missing_source_refs": outputs_missing_refs,
        "tickers_with_lineage": sorted(tickers_with_lineage),
        "tickers_missing_lineage": sorted(tickers_missing_lineage),
    }
    source_health = {"status": source_health_status}

    # ── Per-ticker trust entries ────────────────────────────────────────
    ticker_trust: list[dict[str, Any]] = []
    blocking_reasons: list[str] = []
    warnings: list[str] = []

    for row in sorted(ticker_rows, key=lambda r: str(r.get("ticker") or "")):
        ticker = str(row.get("ticker") or "")
        state = str(row.get("state") or "")
        review_status = review_status_by_ticker.get(ticker, REVIEW_NOT_REQUIRED)
        has_lineage = ticker_has_lineage.get(ticker, False)
        decision_record = row.get("decision") or {}
        evidence_quality = str(decision_record.get("evidence_quality") or "")

        per_axis_status = ticker_axis_status.get(ticker, {})
        axis_readiness = {
            axis: _readiness_label(
                per_axis_status.get(axis, AXIS_STATUS_MISSING),
                (
                    (outputs_by_ticker_axis.get((ticker, axis)) or {}).get("confidence")
                ),
            )
            for axis in TRACKED_AXES
        }

        if state == TICKER_DECIDED:
            constraints = classify_decision_constraints(
                decision_record=decision_record,
                has_source_lineage=has_lineage,
                review_status=review_status,
            )
            source_validated = is_source_validated(
                evidence_quality=evidence_quality,
                has_source_lineage=has_lineage,
                review_status=review_status,
            )
        else:
            constraints = []
            source_validated = False

        ticker_trust.append({
            "ticker": ticker,
            "state": state,
            "axis_status": dict(per_axis_status),
            "axis_readiness": axis_readiness,
            "conflict_review_status": review_status,
            "has_source_lineage": has_lineage,
            "source_validated": source_validated,
            "decision_constraints": constraints,
        })

        if review_status == REVIEW_FAILED:
            blocking_reasons.append(
                f"{ticker}: required conflict review failed — action shown "
                "without successful conflict reconciliation."
            )
        if state == TICKER_FAILED:
            blocking_reasons.append(f"{ticker}: analysis could not finish this run.")

    if total_outputs > 0 and outputs_with_refs == 0:
        warnings.append(
            "No specialist outputs in this session carry source references — "
            "source lineage is not established for any holding."
        )
    elif outputs_missing_refs > 0:
        warnings.append(
            f"{outputs_missing_refs} of {total_outputs} specialist outputs are "
            "missing source references."
        )
    if failed_review_tickers:
        warnings.append(
            f"{len(failed_review_tickers)} required conflict review(s) failed — "
            "affected holdings are shown without successful conflict reconciliation."
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
    elif failed_rows or failed_review_tickers or source_health_status == STATUS_BLOCKED:
        overall_status = STATUS_BLOCKED
    elif no_call_rows or source_health_status == STATUS_LIMITED:
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
