"""Distributed Run Intel — deterministic conflict-resolution policy.

Single-source authority replacing the deleted conditional review LLM: the
review-trigger thresholds (moved verbatim from ``run_scheduler_v1``), input
normalization, conflict assessment, and the confidence/action guardrail.
Pure module — no IO, no LLM. ``run_scheduler_v1`` and the conflict task both
call ``assess_conflict`` — one function, no duplicate trigger logic."""
from __future__ import annotations

from typing import Any, Optional

from .task_contracts_v1 import AXIS_REVIEW

SCHEMA_VERSION = "deterministic_conflict_policy_v1"

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
_MAX_SUMMARY_CHARS = 160


def normalize_valid_inputs(outputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Non-review outputs with both score AND confidence present, one entry
    per axis (duplicates fail closed — excluded), sorted by axis."""
    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for output in sorted(outputs, key=lambda o: str(o.get("axis") or "")):
        axis = output.get("axis")
        if not axis or axis == AXIS_REVIEW or axis in seen:
            continue
        score, confidence = output.get("score"), output.get("confidence")
        if score is None or confidence is None:
            continue
        try:
            score, confidence = float(score), float(confidence)
        except (TypeError, ValueError):
            continue
        seen.add(str(axis))
        normalized.append({"axis": str(axis), "score": score, "confidence": confidence})
    return normalized


def assess_conflict(
    outputs: list[dict[str, Any]], weight_pct: Optional[float],
) -> dict[str, Any]:
    """Deterministic — identical inputs always produce an identical result."""
    scored = normalize_valid_inputs(outputs)
    reason_codes: set[str] = set()
    conflicting_axes: set[str] = set()
    low_confidence_axes: set[str] = set()
    score_min = score_max = score_spread = None
    major = weight_pct is not None and float(weight_pct) >= REVIEW_MAJOR_WEIGHT_PCT
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
    """One bounded, deterministic sentence naming the disagreeing axes."""
    axes = assessment.get("conflicting_axes") or assessment.get("low_confidence_axes") or []
    axis_text = ", ".join(axes) if axes else "the reviewed specialist axes"
    sentence = f"Specialist evidence disagreed across {axis_text}."
    return sentence[:_MAX_SUMMARY_CHARS]
