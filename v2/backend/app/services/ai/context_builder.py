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

Data completeness layer:
  Every ticker exits the builder with a guaranteed minimum payload — price,
  trend, sentiment, fundamental score — with deterministic fallbacks for
  any missing field. A per-ticker ``confidence_score`` (0..1) and a
  portfolio-level ``data_quality`` block tell the LLM exactly how much to
  trust each input. The builder NEVER produces an "insufficient data"
  shape; callers downstream can degrade to a "watchlist only" card but
  never to an empty one.

Consumed by the agent orchestrator. See ``agents/orchestrator.py``.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from ...database import get_supabase_client

logger = logging.getLogger(__name__)


# Per-ticker confidence scoring — every missing field reduces score.
# Weights chosen so that a fully-fallback ticker lands in "watchlist only"
# territory (< 0.25) while a ticker with price + sentiment + one richer
# signal stays in the "partial signal" band (>= 0.5).
_CONFIDENCE_WEIGHTS: dict[str, float] = {
    "price": 0.30,
    "sentiment": 0.20,
    "technical": 0.15,
    "fundamental": 0.20,
    "trend": 0.15,
}


def _confidence_label(score: float) -> str:
    """Map a confidence_score to the UI-facing label family.

    Never returns "insufficient data" — the worst case is "watchlist only".
    """
    if score >= 0.75:
        return "high confidence"
    if score >= 0.50:
        return "partial signal"
    if score >= 0.25:
        return "low confidence signal"
    return "watchlist only"


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
    portfolio_missing: list[str] = []  # unique-set of fallback fields used
    any_fallback = False
    confidences: list[float] = []

    for p in positions:
        ticker = (p.get("ticker") or "").strip()
        if not ticker:
            continue
        shares = _to_float(p.get("shares"))
        avg_cost = _to_float(p.get("avg_cost"))
        current_price = prices_map.get(ticker)
        prior = latest_insights_by_ticker.get(ticker) or {}
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

        # ── Data completeness layer ────────────────────────────────────────
        # Every ticker leaves the builder with a guaranteed payload. Missing
        # fields are filled with safe, deterministic fallbacks; the ticker's
        # confidence_score records how much of the signal is real vs inferred.
        _apply_ticker_fallbacks(entry, prior_insight=prior, avg_cost=avg_cost)
        if entry["data_quality"]["fallbacks_used"]:
            any_fallback = True
            for f in entry["data_quality"]["missing_fields"]:
                if f not in portfolio_missing:
                    portfolio_missing.append(f)
        confidences.append(entry["confidence_score"])
        portfolio.append(entry)

    insights: list[dict[str, Any]] = []
    for ticker, row in latest_insights_by_ticker.items():
        insights.append({
            "ticker": ticker,
            "sentiment": row.get("sentiment_label") or "neutral",
            "technical": row.get("technical_signal") or "NEUTRAL",
            "fundamental": _score_to_label(row.get("fundamental_score")),
            "prior_action": row.get("suggested_action") or "",
            "prior_conviction": row.get("conviction_score"),
        })

    macro = {"summary": (macro_summary or _default_macro()).strip()}

    # Portfolio-level completeness_score — the LLM's trust dial for the
    # whole context. Uses the mean ticker confidence so a single missing
    # field doesn't zero the whole portfolio.
    if confidences:
        completeness_score = round(sum(confidences) / len(confidences), 3)
    else:
        completeness_score = 0.0

    data_quality = {
        "completeness_score": completeness_score,
        "missing_fields": portfolio_missing,
        "fallbacks_used": any_fallback,
    }

    sentiment_block = _aggregate_sentiment(portfolio)

    return {
        "portfolio": portfolio,
        "data_quality": data_quality,
        "macro": macro,
        "sentiment": sentiment_block,
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


def _apply_ticker_fallbacks(
    entry: dict[str, Any],
    *,
    prior_insight: dict[str, Any],
    avg_cost: float,
) -> None:
    """Fill every ticker with a guaranteed signal; record what was faked.

    Mutates ``entry`` in place. Guarantees that after this call the ticker
    has a usable ``current_price``, ``sentiment_label``, ``technical_signal``,
    ``fundamental_score``, and ``trend`` — either real or a safe fallback.
    Attaches a ``confidence_score`` (0..1), a ``confidence_label`` (UI text),
    and a ``data_quality`` sub-object listing the fallbacks applied.
    """
    missing: list[str] = []
    confidence = 1.0

    # 1. Price — real > cached fallback > avg_cost > zero sentinel.
    price = entry.get("current_price")
    price_source = "live"
    if price is None or not isinstance(price, (int, float)) or price <= 0:
        if avg_cost > 0:
            price = avg_cost
            price_source = "avg_cost_fallback"
        else:
            price = 0.0
            price_source = "unavailable"
        missing.append("price")
        confidence -= _CONFIDENCE_WEIGHTS["price"]
    entry["current_price"] = price
    entry["price_source"] = price_source

    # 2. Trend — derived from ``price_action`` if present, otherwise neutral.
    pa = entry.get("price_action") or {}
    trend = _derive_trend(pa)
    if trend is None:
        trend = "flat"
        missing.append("trend")
        confidence -= _CONFIDENCE_WEIGHTS["trend"]
    entry["trend"] = trend

    # 3. Sentiment — from prior insights, else neutral.
    sentiment_label = prior_insight.get("sentiment_label")
    sentiment_score = prior_insight.get("sentiment_score")
    if not sentiment_label:
        sentiment_label = "neutral"
        missing.append("sentiment")
        confidence -= _CONFIDENCE_WEIGHTS["sentiment"]
    entry["sentiment_label"] = sentiment_label
    entry["sentiment_score"] = _to_float_or_none(sentiment_score)

    # 4. Technical signal — from prior insights, else NEUTRAL.
    technical_signal = prior_insight.get("technical_signal")
    if not technical_signal:
        technical_signal = "NEUTRAL"
        missing.append("technical")
        confidence -= _CONFIDENCE_WEIGHTS["technical"]
    entry["technical_signal"] = technical_signal

    # 5. Fundamental score — from prior insights, else sector baseline (0.0).
    fundamental_score = _to_float_or_none(prior_insight.get("fundamental_score"))
    if fundamental_score is None:
        fundamental_score = 0.0
        missing.append("fundamental")
        confidence -= _CONFIDENCE_WEIGHTS["fundamental"]
    entry["fundamental_score"] = fundamental_score

    confidence = max(0.0, min(1.0, confidence))
    entry["confidence_score"] = round(confidence, 3)
    entry["confidence_label"] = _confidence_label(confidence)
    entry["data_quality"] = {
        "missing_fields": missing,
        "fallbacks_used": bool(missing),
    }


def _derive_trend(price_action: dict[str, Any]) -> Optional[str]:
    """Infer a coarse trend label from yfinance price-action deltas.

    Returns ``None`` when no usable input is present so the caller can
    record the gap in the ticker's ``missing_fields`` list.
    """
    pct_30d = _to_float_or_none(price_action.get("pct_30d"))
    pct_5d = _to_float_or_none(price_action.get("pct_5d"))
    ref = pct_30d if pct_30d is not None else pct_5d
    if ref is None:
        return None
    if ref >= 3.0:
        return "up"
    if ref <= -3.0:
        return "down"
    return "flat"


def _aggregate_sentiment(portfolio: list[dict[str, Any]]) -> dict[str, Any]:
    """Roll per-ticker sentiment into a portfolio-level snapshot for the LLM."""
    counts = {"bullish": 0, "neutral": 0, "bearish": 0}
    scores: list[float] = []
    for entry in portfolio:
        label = (entry.get("sentiment_label") or "neutral").lower()
        if label in counts:
            counts[label] += 1
        else:
            counts["neutral"] += 1
        s = entry.get("sentiment_score")
        if isinstance(s, (int, float)):
            scores.append(float(s))
    avg = round(sum(scores) / len(scores), 3) if scores else 0.0
    return {
        "bullish_count": counts["bullish"],
        "neutral_count": counts["neutral"],
        "bearish_count": counts["bearish"],
        "average_score": avg,
    }


def _to_float_or_none(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN guard
        return None
    return f
