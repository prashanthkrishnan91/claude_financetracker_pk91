"""V3 shadow projection — pure diagnostic helper for dark-launch observability.

Takes existing InsightCard signal fields and returns a diagnostic dict
with stable keys for log parsing and test assertions.

Pure function — no IO, DB, LLM, or supabase dependency.
Callable from tests and from the recommendation_engine wrapper.
"""
from __future__ import annotations

from typing import Optional

from .decision_policy_v1 import decide
from .existing_signal_adapter import build_decision_input_from_card

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

    Stable diagnostic keys:
      ticker             — ticker symbol
      v2_visible_action  — normalized visible v2 action (BUY/HOLD/TRIM/SELL)
      v3_shadow_action   — v3 policy output action
      v3_shadow_conviction — v3 policy output conviction
      hold_collapse_risk — True when v2==HOLD but v3 says BUY/TRIM/SELL
      v3_honest_hold     — True when v3==HOLD due to thin/suppressed evidence
      suppressed_axes    — axes with suppression reasons in v3 input
      v3_schema_version  — schema version from DecisionOutputV3

    Returns None on any failure. Never raises.
    """
    try:
        v2_norm = ((v2_visible_action or "HOLD").strip().upper())
        if v2_norm not in _VALID_V2_ACTIONS:
            v2_norm = "HOLD"

        inp = build_decision_input_from_card(
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

        return {
            "ticker": ticker,
            "v2_visible_action": v2_norm,
            "v3_shadow_action": v3_action,
            "v3_shadow_conviction": v3_out.conviction.value,
            "hold_collapse_risk": v2_norm == "HOLD" and v3_action != "HOLD",
            "v3_honest_hold": v3_action == "HOLD" and bool(suppressed_axes),
            "suppressed_axes": suppressed_axes,
            "v3_schema_version": v3_out.schema_version,
        }
    except Exception:  # noqa: BLE001
        return None
