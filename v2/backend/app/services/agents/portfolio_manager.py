"""Portfolio Manager — synthesises analyst outputs into actions + allocation.

This node is the only one that cares about the *portfolio* as a whole.
It takes per-ticker scores + the user's current concentration and produces:

  1. A per-ticker suggested_action (BUY/SELL/TRIM/HOLD/REVIEW)
  2. A per-ticker conviction_score (-1..+1)
  3. A per-ticker investment_thesis (prose)
  4. A per-ticker dollar allocation against (deposit + sale proceeds),
     weighted by conviction and penalised by current concentration.
  5. A portfolio-level summary narrative.
"""

from __future__ import annotations

import logging
from typing import Any

from .llm import LLMClient
from .state import AgentState, TickerInsight

logger = logging.getLogger(__name__)

# Conviction blend weights. Technical + Sentiment are short-term; Fundamentals
# anchor the long-term view and deserve the heaviest weight for a buy-and-hold
# portfolio.
W_FUNDAMENTAL = 0.50
W_TECHNICAL = 0.30
W_SENTIMENT = 0.20

# Concentration penalty: when a position already exceeds this share of the
# portfolio, we multiply its buy conviction by (1 - over_weight_factor).
CONCENTRATION_SOFT_CAP = 0.10  # 10%
CONCENTRATION_HARD_CAP = 0.20  # 20% → hard BUY veto

# Minimum conviction threshold for a BUY to receive any deposit dollars.
BUY_CONVICTION_FLOOR = 0.15

SYSTEM_PROMPT = (
    "You are the Portfolio Manager on a quantitative trading desk. "
    "Three analysts (Sentiment / Technical / Fundamentals) have reported on "
    "each position in the client portfolio. Your job is to synthesise their "
    "views into a concrete action per ticker plus a one-paragraph investment "
    "thesis. Cite at least one analyst's point in each thesis. You are given "
    "the client's current concentration and the cash to deploy. You never "
    "over-concentrate into a single position. Output valid JSON only."
)


def compute_conviction(insight: TickerInsight) -> float:
    """Weighted blend of the three analyst scores, clamped to [-1, 1]."""
    s = insight.sentiment_score if insight.sentiment_score is not None else 0.0
    t = (insight.tech_metrics or {}).get("score")
    if t is None:
        # Fall back on a crude mapping from the categorical signal
        t = {"BUY": 0.6, "HOLD": 0.0, "NEUTRAL": 0.0, "SELL": -0.6}.get(
            insight.technical_signal or "NEUTRAL", 0.0
        )
    f = insight.fundamental_score if insight.fundamental_score is not None else 0.0
    raw = W_SENTIMENT * s + W_TECHNICAL * t + W_FUNDAMENTAL * f
    # Penalise concentrated names from the BUY side.
    if raw > 0:
        weight_frac = max(0.0, insight.current_weight_pct) / 100.0
        if weight_frac >= CONCENTRATION_HARD_CAP:
            raw = 0.0  # veto adding to a hard-capped position
        elif weight_frac >= CONCENTRATION_SOFT_CAP:
            over = (weight_frac - CONCENTRATION_SOFT_CAP) / (
                CONCENTRATION_HARD_CAP - CONCENTRATION_SOFT_CAP
            )
            raw *= max(0.0, 1.0 - over)
    return max(-1.0, min(1.0, raw))


def conviction_to_action(insight: TickerInsight, conviction: float) -> str:
    """Map a (-1..+1) conviction to a BUY/SELL/TRIM/HOLD/REVIEW label."""
    weight_frac = max(0.0, insight.current_weight_pct) / 100.0
    # Strong negative + already held → SELL or TRIM
    if conviction <= -0.50 and insight.shares > 0:
        return "SELL"
    if conviction <= -0.20 and insight.shares > 0:
        return "TRIM"
    if conviction <= -0.20:
        return "REVIEW"
    # Strong positive → BUY (subject to conviction floor + hard cap)
    if conviction >= BUY_CONVICTION_FLOOR and weight_frac < CONCENTRATION_HARD_CAP:
        return "BUY"
    return "HOLD"


def allocate_cash(state: AgentState) -> None:
    """Split `cash_to_deploy` across BUY-rated tickers proportional to conviction.

    Allocation rules:
      1. Only tickers with conviction >= BUY_CONVICTION_FLOOR qualify.
      2. Weight = conviction × (1 - current_weight_fraction / HARD_CAP).
         This naturally tilts dollars toward under-weight high-conviction names.
      3. Normalise weights to the cash envelope and round to 2 dp.
    """
    cash = state.cash_to_deploy
    if cash <= 0:
        return

    candidates: list[tuple[TickerInsight, float]] = []
    for insight in state.insights.values():
        if insight.suggested_action != "BUY":
            continue
        conviction = insight.conviction_score or 0.0
        if conviction < BUY_CONVICTION_FLOOR:
            continue
        weight_frac = max(0.0, insight.current_weight_pct) / 100.0
        under_weight_bonus = max(0.1, 1.0 - weight_frac / CONCENTRATION_HARD_CAP)
        weight = conviction * under_weight_bonus
        if weight > 0:
            candidates.append((insight, weight))

    if not candidates:
        return

    total_weight = sum(w for _, w in candidates)
    if total_weight <= 0:
        return

    # Normalise and apply
    remaining = cash
    last_idx = len(candidates) - 1
    for i, (insight, weight) in enumerate(candidates):
        if i == last_idx:
            dollars = round(remaining, 2)
        else:
            dollars = round(cash * (weight / total_weight), 2)
            remaining -= dollars
        insight.suggested_allocation = max(0.0, dollars)


async def run_portfolio_manager(state: AgentState, llm: LLMClient) -> None:
    """Run the PM node — populate conviction, thesis, action, and allocation."""

    # 1. Deterministic blending + action classification
    for insight in state.insights.values():
        insight.conviction_score = compute_conviction(insight)
        insight.suggested_action = conviction_to_action(insight, insight.conviction_score)

    # 2. Allocate cash envelope across BUY rows
    allocate_cash(state)

    # 3. Ask the LLM for investment theses (one call, batched per ticker)
    #    Gives us narrative prose that cites the analyst points.
    batch_context = _build_batch_context(state)
    user_prompt = (
        "For each ticker below, write a concise investment thesis (2-3 sentences, "
        "max 350 chars) that references at least one analyst's point and the "
        "suggested action. Return ONLY this JSON:\n"
        "{\n"
        '  "theses": {\n'
        '    "<TICKER>": "<thesis string>",\n'
        "    ...\n"
        "  },\n"
        '  "portfolio_summary": "<3 sentence rollup citing the biggest conviction buys, any sells, and concentration risks>"\n'
        "}\n\n"
        + batch_context
    )

    parsed = await llm.ask_json(SYSTEM_PROMPT, user_prompt, max_tokens=3500)
    theses = parsed.get("theses") or {}

    for insight in state.insights.values():
        t = theses.get(insight.ticker) or theses.get(insight.ticker.upper())
        if t:
            insight.investment_thesis = str(t).strip()[:500]
        else:
            insight.investment_thesis = _fallback_thesis(insight)

    state.pm_summary = (parsed.get("portfolio_summary") or _fallback_summary(state)).strip()


# ── helpers ──────────────────────────────────────────────────────────────────

def _build_batch_context(state: AgentState) -> str:
    lines = [
        f"Client portfolio value: ${state.total_portfolio_value:,.2f}",
        f"Cash to deploy: ${state.cash_to_deploy:,.2f} "
        f"(deposit ${state.deposit_amount:,.2f} + sale proceeds ${state.sale_proceeds:,.2f})",
        "",
        "Category concentration: "
        + ", ".join(f"{k}:{v:.0f}%" for k, v in state.category_weights.items()),
        "",
        "Tickers with analyst reports:",
    ]
    for insight in state.insights.values():
        lines.append(
            f"- {insight.ticker} [{insight.category}] "
            f"weight={insight.current_weight_pct:.1f}% pnl={insight.pnl_pct}% "
            f"conviction={insight.conviction_score:.2f} "
            f"action={insight.suggested_action} "
            f"allocation=${insight.suggested_allocation:.2f}"
        )
        lines.append(
            f"    sentiment={insight.sentiment_label} ({insight.sentiment_score}): {insight.sentiment_summary}"
        )
        lines.append(
            f"    technical={insight.technical_signal}: {insight.technical_summary}"
        )
        lines.append(
            f"    fundamentals={insight.fundamental_score}: {insight.fundamental_summary}"
        )
    return "\n".join(lines)


def _fallback_thesis(insight: TickerInsight) -> str:
    bits = []
    if insight.sentiment_summary:
        bits.append(f"Sentiment: {insight.sentiment_summary}")
    if insight.technical_summary:
        bits.append(f"Technicals: {insight.technical_summary}")
    if insight.fundamental_summary:
        bits.append(f"Fundamentals: {insight.fundamental_summary}")
    if not bits:
        # UI contract: never surface "insufficient data". A thesis with no
        # analyst support is a watchlist-only card.
        return f"{insight.ticker}: watchlist only — holding position."
    return " · ".join(bits)[:500]


def _fallback_summary(state: AgentState) -> str:
    buys = [i.ticker for i in state.insights.values() if i.suggested_action == "BUY"]
    sells = [i.ticker for i in state.insights.values() if i.suggested_action in ("SELL", "TRIM")]
    parts = [f"Pipeline processed {len(state.insights)} positions."]
    if buys:
        parts.append(f"Top BUY conviction: {', '.join(buys[:5])}.")
    if sells:
        parts.append(f"Flagged for trim/sell: {', '.join(sells[:5])}.")
    return " ".join(parts)
