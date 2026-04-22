"""Portfolio context builder — aggregates DB state into a single LLM input.

Invariant: this module performs ZERO LLM calls. Its job is to shape the
Supabase state into the exact JSON structure the single Claude call expects.

Consumed by the agent orchestrator. See `agents/orchestrator.py`.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from ...database import get_supabase_client

logger = logging.getLogger(__name__)


def build_portfolio_context(
    user_id: str,
    *,
    live_prices: Optional[dict[str, float]] = None,
    macro_summary: Optional[str] = None,
) -> dict[str, Any]:
    """Aggregate positions + latest insights + macro into one structured dict.

    Returns a dict with:
      - portfolio : list of {ticker, shares, avg_cost, what_changed}
      - macro     : {summary: str}
      - insights  : list of {ticker, sentiment, technical, fundamental}

    Safe to call with any user_id; empty portfolio returns empty lists so the
    orchestrator can short-circuit before any LLM call.
    """
    db = get_supabase_client()

    positions = _fetch_positions(db, user_id)
    latest_insights_by_ticker = _fetch_latest_insights(db, user_id)
    prior_actions = {t: row.get("suggested_action") for t, row in latest_insights_by_ticker.items()}

    portfolio: list[dict[str, Any]] = []
    for p in positions:
        ticker = p.get("ticker") or ""
        if not ticker:
            continue
        shares = _to_float(p.get("shares"))
        avg_cost = _to_float(p.get("avg_cost"))
        current_price = (live_prices or {}).get(ticker)
        what_changed = _infer_what_changed(
            ticker=ticker,
            prior_action=prior_actions.get(ticker),
            current_price=current_price,
            avg_cost=avg_cost,
        )
        portfolio.append({
            "ticker": ticker,
            "shares": shares,
            "avg_cost": avg_cost,
            "current_price": current_price,
            "category": p.get("category") or "Other",
            "lt_eligible": bool(p.get("lt_eligible", False)),
            "target_price": _to_float(p.get("target_price")) if p.get("target_price") is not None else None,
            "what_changed": what_changed,
        })

    insights: list[dict[str, Any]] = []
    for ticker, row in latest_insights_by_ticker.items():
        insights.append({
            "ticker": ticker,
            "sentiment": row.get("sentiment_label") or "",
            "technical": row.get("technical_signal") or "",
            "fundamental": _score_to_label(row.get("fundamental_score")),
            "prior_action": row.get("suggested_action") or "",
            "prior_conviction": row.get("conviction_score"),
        })

    macro = {"summary": (macro_summary or _fetch_macro_summary(db, user_id)).strip()}

    return {
        "portfolio": portfolio,
        "macro": macro,
        "insights": insights,
    }


# ── Internal helpers ────────────────────────────────────────────────────────


def _fetch_positions(db, user_id: str) -> list[dict[str, Any]]:
    try:
        result = (
            db.table("positions")
            .select("*")
            .eq("user_id", str(user_id))
            .execute()
        )
        return result.data or []
    except Exception as exc:  # noqa: BLE001 — propagate as empty, log context
        logger.warning("context_builder: positions fetch failed for %s: %s", user_id, exc)
        return []


def _fetch_latest_insights(db, user_id: str) -> dict[str, dict[str, Any]]:
    """Return the most recent agent_insights row per ticker for this user."""
    try:
        rows = (
            db.table("agent_insights")
            .select(
                "ticker, sentiment_label, sentiment_score, technical_signal, "
                "fundamental_score, conviction_score, suggested_action, created_at"
            )
            .eq("user_id", str(user_id))
            .order("created_at", desc=True)
            .limit(500)
            .execute()
        ).data or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("context_builder: agent_insights fetch failed for %s: %s", user_id, exc)
        return {}

    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        ticker = row.get("ticker")
        if ticker and ticker not in latest:
            latest[ticker] = row
    return latest


def _fetch_macro_summary(db, user_id: str) -> str:
    """Best-effort lookup of a cached macro summary.

    If no `macro_cache` table exists, or the lookup fails for any reason,
    returns a neutral placeholder so the single LLM call still has context.
    """
    try:
        row = (
            db.table("macro_cache")
            .select("summary, created_at")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        ).data
        if row and row[0].get("summary"):
            return str(row[0]["summary"])
    except Exception:  # noqa: BLE001 — table may not exist; placeholder is fine
        pass
    return (
        "Macro context unavailable — evaluate each ticker on its own merits "
        "and existing portfolio concentration."
    )


def _infer_what_changed(
    *,
    ticker: str,
    prior_action: Optional[str],
    current_price: Optional[float],
    avg_cost: float,
) -> str:
    bits: list[str] = []
    if prior_action:
        bits.append(f"prior action {prior_action}")
    if current_price is not None and avg_cost > 0:
        pnl = (current_price - avg_cost) / avg_cost * 100
        bits.append(f"P&L {pnl:+.1f}%")
    return "; ".join(bits)


def _score_to_label(score: Any) -> str:
    try:
        f = float(score)
    except (TypeError, ValueError):
        return ""
    if f >= 0.25:
        return "bullish"
    if f <= -0.25:
        return "bearish"
    return "neutral"


def _to_float(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0
