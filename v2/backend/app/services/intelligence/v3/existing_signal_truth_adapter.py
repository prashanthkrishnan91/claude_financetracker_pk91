"""Bridge: classify existing InsightCard/intel_read signals into AxisTruthSummaries.

Wraps the data_truth_v1 classifiers and aggregates findings per axis.
Produces the AxisTruthSummary list and a compact diagnostic dict for shadow logging.

Pure function — no IO, DB, LLM, or provider calls.
Does not change any v3 decision output or visible action.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Optional

from .data_truth_contracts import (
    AxisTruthSummary,
    DataTruthFinding,
    DataTruthStatus,
)
from .data_truth_v1 import (
    classify_action_signals,
    classify_conviction_signal,
    classify_evidence_signals,
    classify_risk_signals,
    classify_technical_signal,
)

_TRUTH_SCHEMA_VERSION = "v3.truth.v1"


def _build_axis_summary(axis_name: str, findings: list[DataTruthFinding]) -> AxisTruthSummary:
    """Aggregate a list of findings into an AxisTruthSummary."""
    present = sum(1 for f in findings if f.status == DataTruthStatus.PRESENT)
    missing = sum(1 for f in findings if f.status == DataTruthStatus.MISSING)
    stale = sum(1 for f in findings if f.status == DataTruthStatus.STALE)
    weak = sum(1 for f in findings if f.status == DataTruthStatus.WEAK)

    has_conflicting = any(f.status == DataTruthStatus.CONFLICTING for f in findings)
    has_unavailable = any(f.status == DataTruthStatus.UNAVAILABLE for f in findings)
    has_safe = any(f.safe_for_decision for f in findings)

    safe_for_decision = has_safe and not has_conflicting and not has_unavailable

    unsafe_reasons = [f.reason_code for f in findings if not f.safe_for_decision]
    dominant_reason_code = (
        "all_present"
        if not unsafe_reasons
        else Counter(unsafe_reasons).most_common(1)[0][0]
    )

    return AxisTruthSummary(
        axis_name=axis_name,
        findings=findings,
        present_count=present,
        missing_count=missing,
        stale_count=stale,
        weak_count=weak,
        safe_for_decision=safe_for_decision,
        dominant_reason_code=dominant_reason_code,
    )


def evaluate_card_signals_truth(
    *,
    action: Optional[str],
    analyst_action: Optional[str],
    conviction_level: Optional[str],
    technical_signal: Optional[str],
    risk_flag: Optional[str],
    analyst_risks: Optional[list],
    data_quality_label: Optional[str],
    intel_read: Optional[dict],
    analyst_used_fallback: Optional[bool] = None,
) -> list[AxisTruthSummary]:
    """Build axis truth summaries from existing InsightCard signal fields.

    Returns one AxisTruthSummary per signal group axis:
      - evidence_quality: data_quality_label + intel_read
      - action_signal:    card action + analyst_action (with conflict detection)
      - conviction:       conviction_level
      - technical_signal: technical_signal
      - risk_signal:      risk_flag + analyst_risks

    analyst_used_fallback=True conservatively caps evidence quality trust at
    MEDIUM even when signal count would qualify as HIGH.

    Parameters match existing InsightCard field names.
    No new providers, no external calls, no inferred metrics.
    """
    return [
        _build_axis_summary(
            "evidence_quality",
            [classify_evidence_signals(data_quality_label, intel_read, analyst_used_fallback=analyst_used_fallback)],
        ),
        _build_axis_summary(
            "action_signal",
            [classify_action_signals(action, analyst_action)],
        ),
        _build_axis_summary(
            "conviction",
            [classify_conviction_signal(conviction_level)],
        ),
        _build_axis_summary(
            "technical_signal",
            [classify_technical_signal(technical_signal)],
        ),
        _build_axis_summary(
            "risk_signal",
            [classify_risk_signals(risk_flag, analyst_risks)],
        ),
    ]


def build_truth_diagnostic_summary(
    summaries: list[AxisTruthSummary],
    schema_version: str = _TRUTH_SCHEMA_VERSION,
) -> dict[str, Any]:
    """Build a compact diagnostic dict from axis truth summaries.

    Suitable for shadow-only diagnostic logging. Keys are stable.
    Does not expose raw metric names or user data.
    """
    return {
        "schema_version": schema_version,
        "safe_axes": sum(1 for s in summaries if s.safe_for_decision),
        "unsafe_axes": sum(1 for s in summaries if not s.safe_for_decision),
        "axes": {
            s.axis_name: {
                "safe_for_decision": s.safe_for_decision,
                "present": s.present_count,
                "missing": s.missing_count,
                "stale": s.stale_count,
                "weak": s.weak_count,
                "dominant_reason": s.dominant_reason_code,
            }
            for s in summaries
        },
    }
