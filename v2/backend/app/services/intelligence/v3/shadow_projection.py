"""V3 shadow projection — pure diagnostic helper for dark-launch observability.

Takes existing InsightCard signal fields and returns a diagnostic dict
with stable keys for log parsing and test assertions.

PR 7: uses build_truth_aware_decision_input() so v3 shadow decisions are
informed by the PR 6 Data Truth Contract before axes are derived. Unsafe
axes (MISSING/UNAVAILABLE/CONFLICTING/STALE) suppress only their own signals.
New truth-aware keys are added to the truth_diagnostics sub-dict.

Pure function — no IO, DB, LLM, or supabase dependency.
Callable from tests and from the recommendation_engine wrapper.
"""
from __future__ import annotations

from collections import Counter
from typing import Optional, Any

from .decision_policy_v1 import decide
from .existing_signal_adapter import build_truth_aware_decision_input
from .existing_signal_truth_adapter import build_truth_diagnostic_summary

_VALID_V2_ACTIONS: frozenset[str] = frozenset({"BUY", "HOLD", "TRIM", "SELL"})


def project_shadow_from_card_signals(
    *,
    ticker: str,
    v2_visible_action: Optional[str],
    analyst_action: Optional[str],
    conviction_level: Optional[str],
    technical_signal: Optional[str],
    risk_flag: Optional[str],
    analyst_risks: Optional[list],
    category: Optional[str],
    data_quality_label: Optional[str],
    intel_read: Optional[dict],
    thesis_v2: Optional[dict],
) -> Optional[dict]:
    """Shadow-project a v3 decision from card signal fields.

    v2_visible_action is the post-gate visible card action (may be HOLD even
    when the original agent signal was BUY, due to the insufficient-data gate).
    The v3 shadow derives its own decision from all available signals — the
    divergence between v2 and v3 is the key dark-launch diagnostic.

    Stable diagnostic keys (PR 2/3 — unchanged):
      ticker             — ticker symbol
      v2_visible_action  — normalized visible v2 action (BUY/HOLD/TRIM/SELL)
      v3_shadow_action   — v3 policy output action
      v3_shadow_conviction — v3 policy output conviction
      hold_collapse_risk — True when v2==HOLD but v3 says BUY/TRIM/SELL
      v3_honest_hold     — True when v3==HOLD due to thin/suppressed evidence
      suppressed_axes    — axes with suppression reasons in v3 input
      v3_schema_version  — schema version from DecisionOutputV3

    truth_diagnostics sub-dict (PR 6 — extended in PR 7):
      schema_version             — truth schema version
      safe_axes / unsafe_axes    — PR 6 axis safety counts
      axes                       — per-axis truth detail
      truth_aware_adapter_enabled — always True when truth eval succeeds
      safe_axis_count            — count of safe axes (alias for safe_axes)
      unsafe_axis_count          — count of unsafe axes (alias for unsafe_axes)
      suppressed_axis_reasons    — axis_name → reason_code for each suppressed axis
      dominant_truth_reason      — most common suppression reason code, or "none"

    Returns None on any failure. Never raises.
    """
    try:
        v2_norm = ((v2_visible_action or "HOLD").strip().upper())
        if v2_norm not in _VALID_V2_ACTIONS:
            v2_norm = "HOLD"

        inp, truth_summaries, suppressed_by_truth = build_truth_aware_decision_input(
            ticker=ticker,
            action=v2_visible_action,
            analyst_action=analyst_action,
            conviction_level=conviction_level,
            technical_signal=technical_signal,
            risk_flag=risk_flag,
            analyst_risks=analyst_risks,
            category=category,
            data_quality_label=data_quality_label,
            intel_read=intel_read,
            thesis_v2=thesis_v2,
        )
        v3_out = decide(inp)
        suppressed_axes = list(v3_out.suppression_reasons.keys())
        v3_action = v3_out.action.value

        truth_diag: Optional[dict] = None
        try:
            truth_diag = build_truth_diagnostic_summary(truth_summaries)
            safe_count = sum(1 for s in truth_summaries if s.safe_for_decision)
            unsafe_count = len(truth_summaries) - safe_count
            reason_vals = list(suppressed_by_truth.values())
            dominant = Counter(reason_vals).most_common(1)[0][0] if reason_vals else "none"
            truth_diag["truth_aware_adapter_enabled"] = True
            truth_diag["safe_axis_count"] = safe_count
            truth_diag["unsafe_axis_count"] = unsafe_count
            truth_diag["suppressed_axis_reasons"] = dict(suppressed_by_truth)
            truth_diag["dominant_truth_reason"] = dominant
        except Exception:  # noqa: BLE001
            pass

        return {
            "ticker": ticker,
            "v2_visible_action": v2_norm,
            "v3_shadow_action": v3_action,
            "v3_shadow_conviction": v3_out.conviction.value,
            "hold_collapse_risk": v2_norm == "HOLD" and v3_action != "HOLD",
            "v3_honest_hold": v3_action == "HOLD" and bool(suppressed_axes),
            "suppressed_axes": suppressed_axes,
            "v3_schema_version": v3_out.schema_version,
            "truth_diagnostics": truth_diag,
        }
    except Exception:  # noqa: BLE001
        return None


def summarize_shadow_diagnostics(
    diagnostics: list[Optional[dict[str, Any]]],
    *,
    total_cards: int,
    schema_version: str = "v3.shadow.summary.v1",
) -> dict[str, Any]:
    """Build a portfolio-level shadow summary from per-card diagnostics.

    Deterministic aggregation only; never raises. Failed/None entries are
    counted as projection_failures.
    """
    safe_total = max(int(total_cards or 0), 0)
    valid = [d for d in diagnostics if isinstance(d, dict)]

    v2_counts = {"BUY": 0, "HOLD": 0, "TRIM": 0, "SELL": 0}
    v3_counts = {"BUY": 0, "HOLD": 0, "TRIM": 0, "SELL": 0}
    hold_collapse_risk_count = 0
    honest_hold_count = 0
    non_hold_shadow_from_v2_hold_count = 0

    for diag in valid:
        v2_action = str(diag.get("v2_visible_action") or "HOLD").upper()
        v3_action = str(diag.get("v3_shadow_action") or "HOLD").upper()
        if v2_action in v2_counts:
            v2_counts[v2_action] += 1
        if v3_action in v3_counts:
            v3_counts[v3_action] += 1
        if bool(diag.get("hold_collapse_risk")):
            hold_collapse_risk_count += 1
        if bool(diag.get("v3_honest_hold")):
            honest_hold_count += 1
        if v2_action == "HOLD" and v3_action in {"BUY", "TRIM", "SELL"}:
            non_hold_shadow_from_v2_hold_count += 1

    projected_cards = len(valid)
    projection_failures = max(safe_total - projected_cards, 0)

    return {
        "schema_version": schema_version,
        "total_cards": safe_total,
        "projected_cards": projected_cards,
        "projection_failures": projection_failures,
        "v2_visible_action_counts": v2_counts,
        "v3_shadow_action_counts": v3_counts,
        "hold_collapse_risk_count": hold_collapse_risk_count,
        "honest_hold_count": honest_hold_count,
        "non_hold_shadow_from_v2_hold_count": non_hold_shadow_from_v2_hold_count,
    }
