"""Distributed Run Intel — deterministic conflict-resolution policy: strict
input validation, conflict assessment, the confidence/action guardrail,
disagreement-vs-low-confidence copy, and the current-row activation contract
(shared fingerprint). Single authority, replacing the deleted review LLM."""
from __future__ import annotations

import math
from typing import Any, Optional
from . import source_lineage_v1
from .task_contracts_v1 import (
    AXIS_CRYPTO_MARKET,
    AXIS_ETF_EXPOSURE,
    AXIS_FUNDAMENTAL,
    AXIS_RISK_FILING,
    AXIS_SENTIMENT,
    AXIS_TECHNICAL,
    stable_fingerprint,
)

SCHEMA_VERSION = "deterministic_conflict_policy_v1"
SUPPORTED_AXES = frozenset({
    AXIS_FUNDAMENTAL, AXIS_TECHNICAL, AXIS_SENTIMENT, AXIS_RISK_FILING,
    AXIS_ETF_EXPOSURE, AXIS_CRYPTO_MARKET,
})
# Bounded display mapping — user-facing text never carries a raw axis id.
AXIS_DISPLAY_NAMES: dict[str, str] = {
    AXIS_FUNDAMENTAL: "Fundamental analysis", AXIS_TECHNICAL: "Technical analysis",
    AXIS_SENTIMENT: "News and sentiment", AXIS_RISK_FILING: "Filing risk",
    AXIS_ETF_EXPOSURE: "ETF exposure", AXIS_CRYPTO_MARKET: "Crypto market",
}
# Review-trigger thresholds — unchanged from the prior LLM-review era.
REVIEW_SCORE_SPREAD = 1.0
REVIEW_MIN_CONFIDENCE = 0.6
REVIEW_STRONG_NEGATIVE = -0.5
REVIEW_STRONG_POSITIVE = 0.5
REVIEW_MAJOR_WEIGHT_PCT = 5.0
REVIEW_LOW_CONFIDENCE = 0.3
# Conflict guardrail — the ONLY output a conflict may produce.
CONFLICT_ACTION = "HOLD"
CONFLICT_CONFIDENCE_CAP = 0.49
REASON_MATERIAL_SCORE_SPREAD = "material_score_spread"
REASON_OPPOSING_STRONG_SIGNALS_MAJOR_POSITION = "opposing_strong_signals_major_position"
REASON_LOW_CONFIDENCE_MAJOR_POSITION = "low_confidence_major_position"
_MAX_SUMMARY_CHARS = 200

def _finite_in_range(value: Any, lo: float, hi: float) -> Optional[float]:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f) or not (lo <= f <= hi):
        return None
    return f

def safe_major_position(weight_pct: Any) -> bool:
    """Never raises; None/malformed/NaN/infinite/negative → not major."""
    w = _finite_in_range(weight_pct, 0.0, math.inf)
    return w is not None and w >= REVIEW_MAJOR_WEIGHT_PCT

def normalize_valid_inputs(outputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The ONE strict input authority: dict, ``SUPPORTED_AXES`` axis, finite
    score in [-1,1]/confidence in [0,1]. A repeated axis excludes EVERY
    occurrence (order-independent). Returns full rows for every consumer."""
    by_axis: dict[str, list[dict[str, Any]]] = {}
    for output in outputs:
        if not isinstance(output, dict):
            continue
        axis = output.get("axis")
        if axis not in SUPPORTED_AXES:
            continue
        score = _finite_in_range(output.get("score"), -1.0, 1.0)
        confidence = _finite_in_range(output.get("confidence"), 0.0, 1.0)
        if score is None or confidence is None:
            continue
        by_axis.setdefault(str(axis), []).append(
            {**output, "axis": str(axis), "score": score, "confidence": confidence}
        )
    return [rows[0] for axis, rows in sorted(by_axis.items()) if len(rows) == 1]

def assess_conflict(outputs: list[dict[str, Any]], weight_pct: Optional[float]) -> dict[str, Any]:
    """Deterministic — identical inputs always produce an identical result."""
    scored = normalize_valid_inputs(outputs)
    reason_codes: set[str] = set()
    conflicting_axes: set[str] = set()
    low_confidence_axes: set[str] = set()
    score_min = score_max = score_spread = None
    major = safe_major_position(weight_pct)
    if len(scored) >= 2:
        scores = [o["score"] for o in scored]
        score_max, score_min = max(scores), min(scores)
        score_spread = round(score_max - score_min, 4)
        max_conf = max(o["confidence"] for o in scored if o["score"] == score_max)
        min_conf = max(o["confidence"] for o in scored if o["score"] == score_min)
        if (
            score_spread > REVIEW_SCORE_SPREAD
            and max_conf >= REVIEW_MIN_CONFIDENCE
            and min_conf >= REVIEW_MIN_CONFIDENCE
        ):
            reason_codes.add(REASON_MATERIAL_SCORE_SPREAD)
            conflicting_axes.update(
                o["axis"] for o in scored if o["score"] in (score_max, score_min)
            )
        if major and score_min <= REVIEW_STRONG_NEGATIVE and score_max >= REVIEW_STRONG_POSITIVE:
            reason_codes.add(REASON_OPPOSING_STRONG_SIGNALS_MAJOR_POSITION)
            conflicting_axes.update(
                o["axis"] for o in scored
                if o["score"] <= REVIEW_STRONG_NEGATIVE or o["score"] >= REVIEW_STRONG_POSITIVE
            )
    if major:
        for o in scored:
            if o["confidence"] < REVIEW_LOW_CONFIDENCE:
                reason_codes.add(REASON_LOW_CONFIDENCE_MAJOR_POSITION)
                low_confidence_axes.add(o["axis"])
    conflict_detected = bool(reason_codes)
    return {
        "schema_version": SCHEMA_VERSION,
        "conflict_detected": conflict_detected,
        "reason_codes": sorted(reason_codes),
        "conflicting_axes": sorted(conflicting_axes),
        "low_confidence_axes": sorted(low_confidence_axes),
        "score_min": score_min,
        "score_max": score_max,
        "score_spread": score_spread,
        "confidence_cap": CONFLICT_CONFIDENCE_CAP if conflict_detected else None,
    }

def conflict_summary_sentence(assessment: dict[str, Any]) -> str:
    """The one explanation-copy authority — never conflates disagreement with low confidence."""
    disagreement = [AXIS_DISPLAY_NAMES.get(a, a) for a in assessment.get("conflicting_axes") or []]
    low_confidence = [AXIS_DISPLAY_NAMES.get(a, a) for a in assessment.get("low_confidence_axes") or []]
    parts = []
    if disagreement:
        parts.append(f"Specialist evidence disagreed across {', '.join(disagreement)}")
    if low_confidence:
        parts.append(f"confidence was low for {', '.join(low_confidence)}")
    sentence = "; ".join(parts) if parts else "Specialist evidence could not be reconciled"
    return (sentence + ".")[:_MAX_SUMMARY_CHARS]

def conflict_fingerprint(
    *, ticker: str, prompt_context: list[dict[str, Any]], assessment: dict[str, Any], major: bool,
) -> str:
    """Executor and decision reader call this SAME function — one authority."""
    return stable_fingerprint({
        "ticker": ticker, "schema_version": SCHEMA_VERSION,
        "prompt_context": prompt_context, "assessment": assessment, "major": bool(major),
    })

def validate_current_conflict_row(
    review_row: dict[str, Any], *, ticker: str,
    non_review_outputs: list[dict[str, Any]], weight_pct: Optional[float],
) -> Optional[dict[str, Any]]:
    """Recomputed assessment iff ``review_row`` is a CURRENT valid resolution
    — else None. Caller owns the task-state/exactly-one-row checks."""
    required = {"model": SCHEMA_VERSION, "prompt_version": SCHEMA_VERSION, "stance": "neutral"}
    if any(str(review_row.get(k) or "") != v for k, v in required.items()):
        return None
    try:
        if float(review_row.get("score")) != 0.0:
            return None
        if float(review_row.get("confidence")) != CONFLICT_CONFIDENCE_CAP:
            return None
    except (TypeError, ValueError):
        return None
    normalized = normalize_valid_inputs(non_review_outputs)
    if source_lineage_v1.validate_review_against_current_outputs(
        review_row.get("evidence_refs"), ticker=ticker,
        current_non_review_outputs=normalized,
    ) is None:
        return None
    assessment = assess_conflict(normalized, weight_pct)
    if not assessment["conflict_detected"]:
        return None
    prompt_context = source_lineage_v1.build_review_prompt_context(normalized, ticker=ticker)
    expected = conflict_fingerprint(
        ticker=ticker, prompt_context=prompt_context, assessment=assessment,
        major=safe_major_position(weight_pct),
    )
    if str(review_row.get("input_fingerprint") or "") != expected:
        return None
    return assessment
