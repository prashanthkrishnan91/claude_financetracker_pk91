"""Stage 5C — Contradiction Detector v1.

Deterministic, typed contradiction detector for research artifact facts.
No LLM calls, no external API calls, no IO, no semantic inference.

Architecture contracts (non-negotiable):
  - Pure function: no IO, no LLM, no external calls.
  - Replayable: same facts always produce the same output.
  - Detects ONLY explicit, structured, comparable contradictions.
  - Does NOT infer semantic contradictions from prose or unstructured text.
  - Does NOT decide truth or name a winner between conflicting sources.
  - Does NOT emit or imply Buy/Hold/Trim/Sell, price target, conviction,
    allocation, deploy amount, or broker action.
  - No-fact and non-comparable-fact artifacts are marked NOT EVALUABLE.
    "Not evaluable" is honest. It is not "no contradictions found."
  - safe_for_decision is never touched.
  - Contradiction resolution is deferred to Stage 5E truth adapter.

Comparable fact criteria:
  A fact is comparable if its structured_payload contains at least:
    - A claim key: 'claim_key' or 'metric_name' (string, non-empty)
    - A value field: 'value', 'value_normalized', 'boolean_value', or 'text_value'

Grouping key for contradiction detection:
  (claim_key_or_metric_name, fact_kind, period, as_of)
  - period: FactRecord.period or structured_payload['period'] (fallback)
  - as_of: FactRecord.as_of or structured_payload['as_of'] (fallback)
  Facts with different periods or as_of do NOT conflict (different time points).
  Facts with different claim_key / metric_name do NOT conflict.

Contradiction criteria (all deterministic):
  - Numeric (value / value_normalized): relative difference > 1% tolerance.
  - Boolean (boolean_value): True vs False.
  - Text (text_value): case-insensitive exact mismatch. No NLP.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

CONTRADICTION_DETECTOR_VERSION = "contradiction_detector.v1"

# Relative tolerance for numeric comparisons.
# |a - b| / max(|a|, |b|, _FLOOR) > NUMERIC_RELATIVE_TOLERANCE → contradiction.
NUMERIC_RELATIVE_TOLERANCE: float = 0.01  # 1%
_NUMERIC_ABS_FLOOR: float = 1e-9


# ── Assessment dataclasses ────────────────────────────────────────────────────


@dataclass
class ContradictionAssessment:
    """Deterministic contradiction assessment for one artifact's facts.

    Replayable: same facts always produce the same assessment.
    Never contains Buy/Hold/Trim/Sell, price target, conviction, or allocation.
    """
    detector_version: str
    is_evaluable: bool
    not_evaluable_reason: Optional[str]
    comparable_fact_count: int
    non_comparable_fact_count: int
    has_contradictions: bool
    contradiction_count: int
    contradiction_groups: List[Dict[str, Any]]
    limitations: List[str]
    no_guessing: bool = True  # Always True — deterministic-safety indicator

    def to_dict(self) -> Dict[str, Any]:
        return {
            "detector_version": self.detector_version,
            "is_evaluable": self.is_evaluable,
            "not_evaluable_reason": self.not_evaluable_reason,
            "comparable_fact_count": self.comparable_fact_count,
            "non_comparable_fact_count": self.non_comparable_fact_count,
            "has_contradictions": self.has_contradictions,
            "contradiction_count": self.contradiction_count,
            "contradiction_groups": self.contradiction_groups,
            "limitations": self.limitations,
            "no_guessing": self.no_guessing,
        }


# ── Internal helpers ──────────────────────────────────────────────────────────


def _extract_claim_key(sp: Dict[str, Any]) -> Optional[str]:
    """Return claim_key or metric_name from structured_payload, or None."""
    ck = sp.get("claim_key")
    if isinstance(ck, str) and ck.strip():
        return ck.strip()
    mn = sp.get("metric_name")
    if isinstance(mn, str) and mn.strip():
        return mn.strip()
    return None


def _extract_value_fields(sp: Dict[str, Any]) -> Dict[str, Any]:
    """Extract recognized value fields from structured_payload."""
    result: Dict[str, Any] = {}
    for key in ("value", "value_normalized", "boolean_value", "text_value"):
        if key in sp:
            result[key] = sp[key]
    return result


def _make_group_key(
    claim_key: str,
    fact_kind: str,
    period: Optional[str],
    as_of: Optional[str],
) -> str:
    parts = [claim_key, fact_kind]
    if period:
        parts.append(f"period:{period}")
    if as_of:
        parts.append(f"as_of:{as_of}")
    return "|".join(parts)


def _make_sec_metric_observation_group_key(
    claim_key: str,
    sp: Dict[str, Any],
    as_of: Optional[str],
) -> str:
    """SEC CompanyFacts metric_observation group key.

    Stage 5H.3: SEC XBRL observations carry duration/period identity beyond
    the generic (claim_key, period, as_of) tuple. Two observations are
    genuinely the same fact only when metric_name, unit, fiscal_year,
    fiscal_period, period_start, period_end, frame, and filed all match.
    Different units, different fiscal periods, different XBRL durations,
    or different filings → different group → no false contradiction.

    Accession is intentionally excluded so two filings asserting different
    values for the same identity (e.g., a restatement) still group together
    and are flagged as a true contradiction.
    """
    unit = sp.get("unit") or ""
    fiscal_year = sp.get("fiscal_year")
    fiscal_period = sp.get("fiscal_period") or ""
    period_start = sp.get("period_start") or ""
    period_end = sp.get("period_end") or ""
    frame = sp.get("frame") or ""
    parts = [
        "provider:sec_edgar",
        f"metric:{claim_key}",
        "fact_kind:metric_observation",
        f"unit:{unit}",
        f"fy:{fiscal_year if fiscal_year is not None else ''}",
        f"fp:{fiscal_period}",
        f"start:{period_start}",
        f"end:{period_end}",
        f"frame:{frame}",
        f"filed:{as_of or ''}",
    ]
    return "|".join(parts)


def _is_sec_metric_observation(fact_kind: str, sp: Dict[str, Any]) -> bool:
    """Return True if this fact is a SEC CompanyFacts metric_observation."""
    if fact_kind != "metric_observation":
        return False
    provider = sp.get("provider")
    return isinstance(provider, str) and provider.strip().lower() == "sec_edgar"


def _numeric_contradicts(a: float, b: float) -> bool:
    denominator = max(abs(a), abs(b), _NUMERIC_ABS_FLOOR)
    return abs(a - b) / denominator > NUMERIC_RELATIVE_TOLERANCE


def _values_contradict(va: Dict[str, Any], vb: Dict[str, Any]) -> bool:
    """Return True if two value-field dicts represent an explicit contradiction.

    Returns False when there are no shared comparable fields — not evaluable,
    not "no contradiction."
    """
    if "value" in va and "value" in vb:
        try:
            if _numeric_contradicts(float(va["value"]), float(vb["value"])):
                return True
        except (TypeError, ValueError):
            pass

    if "value_normalized" in va and "value_normalized" in vb:
        try:
            if _numeric_contradicts(
                float(va["value_normalized"]), float(vb["value_normalized"])
            ):
                return True
        except (TypeError, ValueError):
            pass

    if "boolean_value" in va and "boolean_value" in vb:
        if bool(va["boolean_value"]) != bool(vb["boolean_value"]):
            return True

    if "text_value" in va and "text_value" in vb:
        if str(va["text_value"]).strip().lower() != str(vb["text_value"]).strip().lower():
            return True

    return False


# ── Public API ────────────────────────────────────────────────────────────────

_EVALUABLE_LIMITATIONS = [
    "Contradiction detection is limited to explicitly structured, comparable fact fields. "
    "Prose text, narrative summaries, and unstructured evidence are not evaluated.",
    "Numeric comparisons use a relative tolerance of 1%. Values within tolerance are not flagged.",
    "Source credibility does not suppress or resolve detected contradictions. "
    "Contradiction resolution is deferred to Stage 5E truth adapter.",
]


def detect_contradictions(facts: List[Any]) -> ContradictionAssessment:
    """Detect deterministic contradictions among a list of FactRecord-compatible objects.

    Args:
        facts: List of FactRecord objects (or compatible objects with
               .structured_payload, .fact_kind, .period, .as_of, .source_index,
               .axis_hint attributes).
               Empty list → not evaluable.

    Returns:
        ContradictionAssessment — always non-None, fully replayable.
        Same inputs always produce the same output.
    """
    if not facts:
        return ContradictionAssessment(
            detector_version=CONTRADICTION_DETECTOR_VERSION,
            is_evaluable=False,
            not_evaluable_reason="no_facts_provided",
            comparable_fact_count=0,
            non_comparable_fact_count=0,
            has_contradictions=False,
            contradiction_count=0,
            contradiction_groups=[],
            limitations=["No facts provided — contradiction detection not evaluable."],
            no_guessing=True,
        )

    # ── Pass 1: classify facts as comparable or non-comparable ───────────────
    # Entry: (group_key, claim_key, period, as_of, value_fields, fact)
    comparable: List[Tuple[str, str, Optional[str], Optional[str], Dict[str, Any], Any]] = []
    non_comparable_count = 0

    for fact in facts:
        sp: Dict[str, Any] = getattr(fact, "structured_payload", {}) or {}
        fact_kind: str = getattr(fact, "fact_kind", "") or ""
        period: Optional[str] = getattr(fact, "period", None) or sp.get("period")
        as_of: Optional[str] = getattr(fact, "as_of", None) or sp.get("as_of")

        claim_key = _extract_claim_key(sp)
        values = _extract_value_fields(sp)

        if claim_key and values:
            if _is_sec_metric_observation(fact_kind, sp):
                group_key = _make_sec_metric_observation_group_key(
                    claim_key, sp, as_of,
                )
            else:
                group_key = _make_group_key(claim_key, fact_kind, period, as_of)
            comparable.append((group_key, claim_key, period, as_of, values, fact))
        else:
            non_comparable_count += 1

    if not comparable:
        return ContradictionAssessment(
            detector_version=CONTRADICTION_DETECTOR_VERSION,
            is_evaluable=False,
            not_evaluable_reason="insufficient_comparable_facts",
            comparable_fact_count=0,
            non_comparable_fact_count=non_comparable_count,
            has_contradictions=False,
            contradiction_count=0,
            contradiction_groups=[],
            limitations=[
                "Facts do not expose comparable structured fields "
                "(claim_key/metric_name + value field). "
                "Contradiction detection requires structured, explicitly-comparable evidence. "
                "Prose or unstructured text is never inferred as a contradiction."
            ],
            no_guessing=True,
        )

    # ── Pass 2: group by group_key and detect contradictions ─────────────────
    groups: Dict[str, List[Tuple]] = {}
    for entry in comparable:
        groups.setdefault(entry[0], []).append(entry)

    contradiction_groups: List[Dict[str, Any]] = []

    for group_key in sorted(groups.keys()):
        entries = groups[group_key]
        if len(entries) < 2:
            continue

        # Check all pairs
        contradicting_indices: set[int] = set()
        for i in range(len(entries)):
            for j in range(i + 1, len(entries)):
                if _values_contradict(entries[i][4], entries[j][4]):
                    contradicting_indices.add(i)
                    contradicting_indices.add(j)

        if not contradicting_indices:
            continue

        conflicting_facts = []
        for idx in sorted(contradicting_indices):
            _, ck, period, as_of, values, fact = entries[idx]
            conflicting_facts.append({
                "claim_key": ck,
                "fact_kind": getattr(fact, "fact_kind", ""),
                "period": period,
                "as_of": as_of,
                "value_fields": values,
                "source_index": getattr(fact, "source_index", None),
                "axis_hint": getattr(fact, "axis_hint", None),
            })

        _, claim_key, period, as_of, _, first_fact = entries[0]
        contradiction_groups.append({
            "group_key": group_key,
            "claim_key": claim_key,
            "fact_kind": getattr(first_fact, "fact_kind", ""),
            "period": period,
            "as_of": as_of,
            "conflicting_fact_count": len(contradicting_indices),
            "conflicting_facts": conflicting_facts,
        })

    return ContradictionAssessment(
        detector_version=CONTRADICTION_DETECTOR_VERSION,
        is_evaluable=True,
        not_evaluable_reason=None,
        comparable_fact_count=len(comparable),
        non_comparable_fact_count=non_comparable_count,
        has_contradictions=bool(contradiction_groups),
        contradiction_count=len(contradiction_groups),
        contradiction_groups=contradiction_groups,
        limitations=_EVALUABLE_LIMITATIONS,
        no_guessing=True,
    )
