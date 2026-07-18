"""Recommendation panel rationale composer — pure presentation over Intel v3.

Takes the latest certified Intel v3 snapshot (the ONE deterministic decision
authority — actions are NEVER recomputed or overridden here) and decorates
each held-card action with a one-line rationale that shows its work:

  - profit threshold: unrealized gain vs the configured profit-taking threshold
  - estimated tax impact of selling at the configured tax rates (from tax lots)
  - allocation drift vs the user's target allocation (when a target exists)
  - the engine's own plain-English reason (why_text) as the evidence component

HARD RULE: a recommendation with no composable rationale is NOT returned —
it is excluded and reported in the diagnostics, never rendered without
showing its work.

Pure functions — no IO, DB, LLM, or provider calls.
"""

from __future__ import annotations

from typing import Any, Optional

_VALID_ACTIONS = {"BUY", "HOLD", "TRIM", "SELL"}


def _profit_part(summary: Optional[dict], threshold_pct: float) -> Optional[str]:
    if not summary:
        return None
    gain_total = summary.get("unrealized_gain_total")
    cost = summary.get("total_cost_basis") or 0
    if gain_total is None or cost <= 0:
        return None
    gain_pct = gain_total / cost * 100
    if gain_pct >= threshold_pct:
        return f"up {gain_pct:.1f}% — above the {threshold_pct:.0f}% profit threshold"
    return f"up {gain_pct:.1f}% vs the {threshold_pct:.0f}% profit threshold" if gain_pct >= 0 \
        else f"down {abs(gain_pct):.1f}% (profit threshold {threshold_pct:.0f}% not in play)"


def _tax_part(lots: Optional[list[dict]], action: str) -> Optional[str]:
    """Estimated tax impact of selling, from per-lot estimates.

    Only sell-side actions (TRIM/SELL) carry a tax-impact clause; buys/holds
    trigger no sale, so no estimate is fabricated for them.
    """
    if action not in {"TRIM", "SELL"} or not lots:
        return None
    estimates = [l.get("estimated_tax_if_sold") for l in lots]
    if any(e is None for e in estimates) or not estimates:
        return None  # missing price/cost data → no fabricated estimate
    total = sum(estimates)
    st = sum(1 for l in lots if not l.get("is_long_term"))
    mix = "all long-term" if st == 0 else (
        "all short-term" if st == len(lots) else f"{st} of {len(lots)} lots short-term"
    )
    if total > 0:
        return f"selling all lots ≈ ${total:,.0f} tax ({mix})"
    return f"selling realizes a loss (≈ ${abs(total):,.0f} potential offset, {mix})"


def _drift_part(current_pct: Optional[float], target_pct: Optional[float]) -> Optional[str]:
    if current_pct is None or target_pct is None:
        return None
    drift = current_pct - target_pct
    direction = "above" if drift >= 0 else "below"
    return f"{abs(drift):.1f}pp {direction} the {target_pct:.1f}% target"


def build_recommendation_panel(
    *,
    snapshot_payload: Optional[dict[str, Any]],
    lots_by_ticker: dict[str, list[dict[str, Any]]],
    lot_summaries: dict[str, dict[str, Any]],
    target_weights_pct: dict[str, float],
    profit_threshold_pct: float,
) -> dict[str, Any]:
    """Compose the recommendations panel from the latest Intel v3 snapshot.

    Returns {items, excluded, snapshot_meta}. Items keep the engine's action,
    conviction and evidence band verbatim; only presentation is added.
    """
    if not snapshot_payload:
        return {
            "items": [],
            "excluded": [],
            "snapshot_meta": {"status": "no_snapshot"},
        }

    cards = snapshot_payload.get("current_holdings") or snapshot_payload.get("cards") or []
    items: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []

    for card in cards:
        ticker = str(card.get("ticker") or "").upper()
        action = str(card.get("action") or "").upper()
        if not ticker or action not in _VALID_ACTIONS:
            excluded.append({"ticker": ticker or "?", "reason": "no_action"})
            continue

        summary = lot_summaries.get(ticker)
        lots = lots_by_ticker.get(ticker)
        current_pct = card.get("portfolio_current_pct")
        target_pct = target_weights_pct.get(ticker)
        why_text = (card.get("why_text") or "").strip()

        parts = [
            p for p in (
                _profit_part(summary, profit_threshold_pct),
                _tax_part(lots, action),
                _drift_part(
                    float(current_pct) if current_pct is not None else None,
                    target_pct,
                ),
            )
            if p
        ]

        # HARD RULE: no rationale → no render. The engine's own plain-English
        # reason counts as rationale (it is the evidence-side of the work);
        # portfolio math parts count as the numbers-side. Neither → excluded.
        if not parts and not why_text:
            excluded.append({"ticker": ticker, "reason": "no_rationale_available"})
            continue

        rationale_line = "; ".join(parts) if parts else why_text

        items.append({
            "ticker": ticker,
            "name": card.get("name"),
            "action": action,
            "conviction": card.get("conviction"),
            "evidence_band": card.get("evidence_band"),
            "rationale": rationale_line,
            "engine_reason": why_text or None,
            "components": {
                "profit_threshold": parts and _profit_part(summary, profit_threshold_pct) or None,
                "tax_impact": _tax_part(lots, action),
                "allocation_drift": _drift_part(
                    float(current_pct) if current_pct is not None else None,
                    target_pct,
                ),
            },
        })

    return {
        "items": items,
        "excluded": excluded,
        "snapshot_meta": {
            "status": "ok",
            "snapshot_id": snapshot_payload.get("snapshot_id"),
            "generated_at": snapshot_payload.get("generated_at"),
            "evidence_freshness_state": snapshot_payload.get("evidence_freshness_state"),
        },
    }
