"""Stage 8D — SEC/company catalyst evidence safe display adapter.

Pure deterministic adapter with no IO, no DB calls, no LLM calls, no providers.
Translates Stage 5K TickerDecisionReadiness into three boolean flags that
the snapshot builder embeds in evidence_explanation.sec_catalyst_evidence.

Output contract:
  - sec_catalyst_found: True when sec_catalyst_sentiment was a usable contributing lane
  - editorial_suppressed: True when news_sentiment was in degraded lanes (SUPPRESSED)
  - sec_lane_applicable: False for ETF/crypto/non-equity instruments

No raw backend codes appear in the output dict values. No decision authority is
assigned — these are display-only flags for the Intel drawer UI.
"""
from __future__ import annotations

from typing import Any


_LANE_SEC_CATALYST = "sec_catalyst_sentiment"
_LANE_NEWS_SENTIMENT = "news_sentiment"


def build_catalyst_display_fields(s6_readiness: Any) -> dict:
    """Extract safe display-only catalyst fields from a TickerDecisionReadiness object.

    Args:
        s6_readiness: Stage 5K TickerDecisionReadiness, or None.

    Returns:
        Dict with three boolean fields only — no raw codes, no decision authority.
    """
    if s6_readiness is None:
        return {
            "sec_catalyst_found": False,
            "editorial_suppressed": False,
            "sec_lane_applicable": True,
        }

    axes = getattr(s6_readiness, "axes", {}) or {}
    sent_axis = axes.get("sentiment")
    contributing: list[str] = list(getattr(sent_axis, "contributing_lanes", None) or [])
    degraded: list[str] = list(getattr(sent_axis, "degraded_lanes", None) or [])

    return {
        "sec_catalyst_found": _LANE_SEC_CATALYST in contributing,
        "editorial_suppressed": _LANE_NEWS_SENTIMENT in degraded,
        "sec_lane_applicable": bool(getattr(s6_readiness, "sec_lane_applicable", True)),
    }
