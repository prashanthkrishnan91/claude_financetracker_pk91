"""Portfolio context builder — aggregates DB state into a single LLM input.

Invariants:
  * This module performs ZERO LLM calls and ZERO external API calls.
  * The core transform (``build_context_from_inputs``) is a PURE function —
    no DB, no network, no async. It accepts already-resolved positions,
    insights, live prices, and macro summary, and returns the compact JSON
    the single Claude call expects.
  * The legacy ``build_portfolio_context(user_id, ...)`` wrapper remains for
    callers that still want the DB fetch to happen inline. New hot-path
    callers (``orchestrator.run``) resolve inputs themselves and call the
    pure transform directly for deterministic execution.

Consumed by the agent orchestrator. See ``agents/orchestrator.py``.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from ...database import get_supabase_client

logger = logging.getLogger(__name__)


# ── Pure transform (NEW) ─────────────────────────────────────────────────────


def build_context_from_inputs(
    *,
    positions: list[dict[str, Any]],
    latest_insights_by_ticker: dict[str, dict[str, Any]],
    live_prices: Optional[dict[str, float]] = None,
    macro_summary: Optional[str] = None,
    market_data: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Pure transform: shape already-fetched state into the LLM context object.

    Guarantees:
      * No DB access.
      * No network / async.
      * Deterministic output for identical inputs.

    ``market_data`` is an opaque dict produced by ``io_layer.fetch_market_bundle``
    — it may carry live prices, news counts, and other signals the LLM can use
    for richer reasoning without any per-ticker fan-out.
    """
    prices_map: dict[str, float] = dict(live_prices or {})
    if market_data and isinstance(market_data.get("live_prices"), dict):
        # io_layer prices take precedence over the legacy ``live_prices`` arg
        # when both are supplied — they're fresher (just fetched).
        for t, v in market_data["live_prices"].items():
            if isinstance(v, (int, float)) and v > 0:
                prices_map[t] = float(v)

    prior_actions = {
        t: row.get("suggested_action")
        for t, row in latest_insights_by_ticker.items()
    }

    news_map = (market_data or {}).get("news") or {}
    fundamentals_map = (market_data or {}).get("fundamentals") or {}
    price_action_map = (market_data or {}).get("price_action") or {}

    portfolio: list[dict[str, Any]] = []
    for p in positions:
        ticker = (p.get("ticker") or "").strip()
        if not ticker:
            continue
        shares = _to_float(p.get("shares"))
        avg_cost = _to_float(p.get("avg_cost"))
        current_price = prices_map.get(ticker)
        what_changed = _infer_what_changed(
            ticker=ticker,
            prior_action=prior_actions.get(ticker),
            current_price=current_price,
            avg_cost=avg_cost,
        )
        entry: dict[str, Any] = {
            "ticker": ticker,
            "shares": shares,
            "avg_cost": avg_cost,
            "current_price": current_price,
            "category": p.get("category") or "Other",
            "lt_eligible": bool(p.get("lt_eligible", False)),
            "target_price": _to_float(p.get("target_price"))
                if p.get("target_price") is not None else None,
            "what_changed": what_changed,
        }
        # Optional IO-layer enrichments — compact, token-cheap.
        news_items = news_map.get(ticker) or []
        if news_items:
            entry["recent_news_count"] = len(news_items)
            entry["recent_headlines"] = [
                (n.get("headline") or "")[:120]
                for n in news_items[:3]
                if n.get("headline")
            ]
        fund = fundamentals_map.get(ticker)
        if fund:
            entry["fundamentals"] = _compact_fundamentals(fund)
        pa = price_action_map.get(ticker)
        if pa:
            entry["price_action"] = _compact_price_action(pa)
        portfolio.append(entry)

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

    macro = {"summary": (macro_summary or _default_macro()).strip()}

    return {
        "portfolio": portfolio,
        "macro": macro,
        "insights": insights,
    }


# ── Legacy wrapper — still fetches state from Supabase ──────────────────────


def build_portfolio_context(
    user_id: str,
    *,
    live_prices: Optional[dict[str, float]] = None,
    macro_summary: Optional[str] = None,
    market_data: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Back-compat entry point: resolve state from DB, then call the pure transform.

    New code should prefer ``build_context_from_inputs`` with pre-resolved data
    so the execution DAG stays deterministic and testable.
    """
    db = get_supabase_client()
    positions = _fetch_positions(db, user_id)
    latest_insights_by_ticker = _fetch_latest_insights(db, user_id)
    if macro_summary is None:
        macro_summary = _fetch_macro_summary(db, user_id)

    return build_context_from_inputs(
        positions=positions,
        latest_insights_by_ticker=latest_insights_by_ticker,
        live_prices=live_prices,
        macro_summary=macro_summary,
        market_data=market_data,
    )


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

    If no ``macro_cache`` table exists, or the lookup fails for any reason,
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
    return _default_macro()


def _default_macro() -> str:
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


def _compact_fundamentals(f: dict[str, Any]) -> dict[str, Any]:
    """Keep only the 5 fundamentals the LLM actually uses — saves tokens."""
    keys = ("pe", "forward_pe", "profit_margin", "revenue_growth", "dividend_yield")
    return {k: f.get(k) for k in keys if f.get(k) is not None}


def _compact_price_action(pa: dict[str, Any]) -> dict[str, Any]:
    keys = ("pct_5d", "pct_30d", "pct_3mo", "sma20", "sma50")
    return {k: pa.get(k) for k in keys if pa.get(k) is not None}
