"""AI explanation layer for portfolio deposit decisions.

Explains existing structured decision outputs — never generates decisions.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You are a financial assistant explaining portfolio decisions. "
    "Be concise, specific, and data-driven."
)


async def explain_decision(
    snapshot: dict[str, Any],
    decision_plan: dict[str, Any],
    api_key: str = "",
) -> dict[str, Any]:
    """Explain a deposit decision_plan using portfolio snapshot context.

    Returns {"summary": str, "actions": [{"symbol": str, "explanation": str}]}.
    Falls back to static descriptions when api_key is absent or LLM fails.
    """
    actions = decision_plan.get("actions", [])
    if not api_key or not actions:
        return _fallback(actions)

    total_value = snapshot.get("total_value", 0)
    top_positions = snapshot.get("top_positions", [])

    positions_text = ", ".join(
        f"{p['symbol']} {round(p['weight'] * 100, 1)}%"
        for p in top_positions[:5]
    ) or "none"

    actions_text = "\n".join(
        f"- {a['symbol']}: ${a['amount']:.0f} (+{round(a.get('delta_weight', 0) * 100, 1)}% deposit weight)"
        for a in actions
    )

    user_msg = (
        f"Portfolio: ${total_value:,.0f} total. Top holdings: {positions_text}.\n\n"
        f"Deposit actions:\n{actions_text}\n\n"
        'Return JSON only: {"summary": "...", "actions": [{"symbol": "...", "explanation": "..."}]}'
    )

    from .agents.llm import LLMClient

    client = LLMClient(api_key=api_key)
    result = await client.ask_json(system=_SYSTEM, user=user_msg, max_tokens=512)

    if not result or "actions" not in result:
        return _fallback(actions)

    return result


def _fallback(actions: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "summary": "Deploying capital per fixed allocation formula.",
        "actions": [
            {"symbol": a.get("symbol", ""), "explanation": "Allocated per target weight."}
            for a in actions
        ],
    }
