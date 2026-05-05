"""V3 Data Truth Contract v1 — pure signal evaluator.

Classifies existing InsightCard/intel_read field values by truth/freshness/
source/completeness without provider calls, DB access, or LLM involvement.

Each classifier inspects the in-memory data already available to Intel
assembly and returns a DataTruthFinding. Results feed AxisTruthSummary
aggregation in existing_signal_truth_adapter.py.

Design notes:
- PRESENT / WEAK are safe_for_decision=True (WEAK carries LOW trust).
- MISSING, STALE, CONFLICTING, UNAVAILABLE are safe_for_decision=False.
- STALE requires caller-supplied age data (no timestamps in current signals).
- Conflict detection is deterministic: only direct BUY↔SELL opposition.

Pure function — no IO, DB, LLM, or provider calls.
"""
from __future__ import annotations

from typing import Any, Optional

from .data_truth_contracts import (
    AxisTruthSummary,
    DataTruthFinding,
    DataTruthStatus,
    SourceTrustLevel,
)

# Explicit provider-unavailability sentinels recognized in signal strings.
_UNAVAILABLE_SENTINELS: frozenset[str] = frozenset(
    {"UNAVAILABLE", "N/A", "UNAVAIL", "NOT_AVAILABLE", "NA"}
)

# Action pairs that are directly opposing (deterministic conflict).
_OPPOSING_ACTION_PAIRS: frozenset[tuple[str, str]] = frozenset(
    {("BUY", "SELL"), ("SELL", "BUY")}
)


def _is_unavailable_sentinel(value: Any) -> bool:
    """True if value is an explicit provider-unavailable marker."""
    return isinstance(value, str) and value.strip().upper() in _UNAVAILABLE_SENTINELS


def _norm(raw: Optional[str]) -> str:
    return (raw or "").strip().upper()


def _present(
    signal_name: str,
    *,
    trust_level: SourceTrustLevel,
    source_kind: str,
    freshness_label: str = "current",
    reason_code: str = "field_present",
) -> DataTruthFinding:
    return DataTruthFinding(
        signal_name=signal_name,
        status=DataTruthStatus.PRESENT,
        trust_level=trust_level,
        source_kind=source_kind,
        freshness_label=freshness_label,
        reason_code=reason_code,
        safe_for_decision=True,
    )


def _weak(
    signal_name: str,
    *,
    source_kind: str,
    reason_code: str,
    freshness_label: str = "weak_but_present",
) -> DataTruthFinding:
    return DataTruthFinding(
        signal_name=signal_name,
        status=DataTruthStatus.WEAK,
        trust_level=SourceTrustLevel.LOW,
        source_kind=source_kind,
        freshness_label=freshness_label,
        reason_code=reason_code,
        safe_for_decision=True,
    )


def _missing(
    signal_name: str,
    *,
    source_kind: str,
    reason_code: str = "field_absent",
) -> DataTruthFinding:
    return DataTruthFinding(
        signal_name=signal_name,
        status=DataTruthStatus.MISSING,
        trust_level=SourceTrustLevel.UNKNOWN,
        source_kind=source_kind,
        freshness_label="absent",
        reason_code=reason_code,
        safe_for_decision=False,
    )


def _unavailable(
    signal_name: str,
    *,
    source_kind: str,
) -> DataTruthFinding:
    return DataTruthFinding(
        signal_name=signal_name,
        status=DataTruthStatus.UNAVAILABLE,
        trust_level=SourceTrustLevel.UNKNOWN,
        source_kind=source_kind,
        freshness_label="unavailable",
        reason_code="provider_sentinel",
        safe_for_decision=False,
    )


def _conflicting(
    signal_name: str,
    *,
    source_kind: str,
    reason_code: str = "action_conflict",
) -> DataTruthFinding:
    return DataTruthFinding(
        signal_name=signal_name,
        status=DataTruthStatus.CONFLICTING,
        trust_level=SourceTrustLevel.UNKNOWN,
        source_kind=source_kind,
        freshness_label="conflicting",
        reason_code=reason_code,
        safe_for_decision=False,
    )


# ── Public classifiers ────────────────────────────────────────────────────────


def classify_evidence_signals(
    data_quality_label: Optional[str],
    intel_read: Optional[dict],
) -> DataTruthFinding:
    """Classify the evidence quality signal group.

    intel_read takes precedence over data_quality_label when both present.
    """
    if intel_read is None and data_quality_label is None:
        return _missing("evidence_quality", source_kind="intel_read_and_data_quality_label")

    if intel_read is not None:
        insufficient = bool(intel_read.get("insufficient_data"))
        trusted = intel_read.get("trusted_dimensions") or []
        n_trusted = len(trusted) if isinstance(trusted, list) else 0

        if insufficient or n_trusted == 0:
            return _weak(
                "evidence_quality",
                source_kind="intel_read",
                reason_code="intel_insufficient",
            )
        if n_trusted >= 3:
            return _present(
                "evidence_quality",
                trust_level=SourceTrustLevel.HIGH,
                source_kind="intel_read",
            )
        return _present(
            "evidence_quality",
            trust_level=SourceTrustLevel.MEDIUM,
            source_kind="intel_read",
        )

    label = _norm(data_quality_label)

    if _is_unavailable_sentinel(data_quality_label):
        return _unavailable("evidence_quality", source_kind="data_quality_label")
    if label == "HIGH":
        return _present("evidence_quality", trust_level=SourceTrustLevel.HIGH, source_kind="data_quality_label")
    if label == "MEDIUM":
        return _present("evidence_quality", trust_level=SourceTrustLevel.MEDIUM, source_kind="data_quality_label")
    if label == "LOW":
        return _weak("evidence_quality", source_kind="data_quality_label", reason_code="data_quality_low")

    return _missing("evidence_quality", source_kind="data_quality_label")


def classify_action_signals(
    action: Optional[str],
    analyst_action: Optional[str],
) -> DataTruthFinding:
    """Classify the action signal pair with deterministic conflict detection.

    BUY↔SELL is the only recognized direct conflict. HOLD/TRIM vs BUY
    are not flagged as conflicts — they represent disagreement, not contradiction.
    """
    a = _norm(action)
    b = _norm(analyst_action)

    if _is_unavailable_sentinel(action) or _is_unavailable_sentinel(analyst_action):
        return _unavailable("action_signal", source_kind="card_action_and_analyst_action")

    if (a, b) in _OPPOSING_ACTION_PAIRS:
        return _conflicting(
            "action_signal",
            source_kind="card_action_and_analyst_action",
            reason_code="action_conflict",
        )

    valid_actions = {"BUY", "HOLD", "TRIM", "SELL", "REVIEW", "REDUCE", "WATCH"}

    if a in valid_actions or b in valid_actions:
        trust = SourceTrustLevel.HIGH if (a in valid_actions and b in valid_actions) else SourceTrustLevel.MEDIUM
        return _present("action_signal", trust_level=trust, source_kind="card_action_and_analyst_action")

    return _missing("action_signal", source_kind="card_action_and_analyst_action")


def classify_conviction_signal(conviction_level: Optional[str]) -> DataTruthFinding:
    """Classify the conviction signal."""
    if conviction_level is None:
        return _missing("conviction", source_kind="conviction_level")

    if _is_unavailable_sentinel(conviction_level):
        return _unavailable("conviction", source_kind="conviction_level")

    level = _norm(conviction_level)
    if level == "HIGH":
        return _present("conviction", trust_level=SourceTrustLevel.HIGH, source_kind="conviction_level")
    if level == "MEDIUM":
        return _present("conviction", trust_level=SourceTrustLevel.MEDIUM, source_kind="conviction_level")
    if level == "LOW":
        return _weak("conviction", source_kind="conviction_level", reason_code="conviction_low")

    return _missing("conviction", source_kind="conviction_level")


def classify_technical_signal(technical_signal: Optional[str]) -> DataTruthFinding:
    """Classify the technical signal."""
    if technical_signal is None:
        return _missing("technical_signal", source_kind="technical_signal")

    if _is_unavailable_sentinel(technical_signal):
        return _unavailable("technical_signal", source_kind="technical_signal")

    sig = _norm(technical_signal)
    known = {"BUY", "SELL", "BULLISH", "BEARISH", "NEUTRAL", "WEAK", "STRONG", "HOLD"}
    if sig in known:
        return _present("technical_signal", trust_level=SourceTrustLevel.MEDIUM, source_kind="technical_signal")

    if sig:
        return _weak("technical_signal", source_kind="technical_signal", reason_code="unrecognized_technical_value")

    return _missing("technical_signal", source_kind="technical_signal")


def classify_risk_signals(
    risk_flag: Optional[str],
    analyst_risks: Optional[list],
) -> DataTruthFinding:
    """Classify the risk signal group.

    No risk data (both None/empty) → MISSING.
    Any risk text or flag → PRESENT with MEDIUM trust.
    """
    flag_text = (risk_flag or "").strip()
    risks_list = [r for r in (analyst_risks or []) if isinstance(r, str) and r.strip()]

    if _is_unavailable_sentinel(risk_flag):
        return _unavailable("risk_signal", source_kind="risk_flag_and_analyst_risks")

    if not flag_text and not risks_list:
        return _missing("risk_signal", source_kind="risk_flag_and_analyst_risks", reason_code="risk_data_absent")

    return _present("risk_signal", trust_level=SourceTrustLevel.MEDIUM, source_kind="risk_flag_and_analyst_risks")


def classify_with_staleness(
    signal_name: str,
    value: Any,
    *,
    last_updated_hours_ago: Optional[float],
    stale_threshold_hours: float = 48.0,
    source_kind: str = "unknown",
) -> DataTruthFinding:
    """Classify a signal field with optional staleness detection.

    Intended for future use when timestamp data becomes available in
    existing signal shapes. Current InsightCard signals do not carry
    timestamps, so callers must derive age externally.

    If last_updated_hours_ago is None, freshness is treated as unknown
    (classified as PRESENT with MEDIUM trust if value exists).
    If last_updated_hours_ago > stale_threshold_hours, status is STALE.
    """
    if value is None:
        return _missing(signal_name, source_kind=source_kind)

    if _is_unavailable_sentinel(value):
        return _unavailable(signal_name, source_kind=source_kind)

    if last_updated_hours_ago is not None and last_updated_hours_ago > stale_threshold_hours:
        return DataTruthFinding(
            signal_name=signal_name,
            status=DataTruthStatus.STALE,
            trust_level=SourceTrustLevel.LOW,
            source_kind=source_kind,
            freshness_label=f"stale_{int(last_updated_hours_ago)}h_ago",
            reason_code="field_stale",
            safe_for_decision=False,
        )

    freshness = (
        "current"
        if last_updated_hours_ago is None
        else f"fresh_{int(last_updated_hours_ago)}h_ago"
    )
    return _present(
        signal_name,
        trust_level=SourceTrustLevel.MEDIUM,
        source_kind=source_kind,
        freshness_label=freshness,
    )
