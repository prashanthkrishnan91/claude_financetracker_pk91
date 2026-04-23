"""MarketSnapshot — per-ticker, per-run data envelope (Phase 1).

This is the deterministic data layer for the staged intelligence pipeline.
It aggregates the existing ``io_layer.fetch_market_bundle`` output into a
typed, per-ticker object so downstream stages (feature engine, LLM analyst,
synthesis) can reason on a stable shape instead of pulling from loose dicts.

Design invariants:
    * Pure transform — no DB, no network, no LLM.
    * Never raises — every field either has a real value or a deterministic
      fallback; gaps are recorded in ``missing_fields`` and ``fallback_chain``.
    * ``data_quality_score`` is computed from actual fields present, NOT
      hardcoded to 1.0. Weights are declared below so the score varies
      meaningfully across tickers with different data coverage.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Optional


# Weighted contributions to ``data_quality_score``. Chosen so that a
# fully-fallback ticker (no live price, no returns, no fundamentals, no
# sentiment) scores 0.0, a ticker with live price + trend scores ~0.45,
# and a fully-populated ticker scores 1.0.
_QUALITY_WEIGHTS: dict[str, float] = {
    "price": 0.25,
    "return_5d": 0.10,
    "return_30d": 0.10,
    "volatility": 0.10,
    "sector": 0.10,
    "fundamentals": 0.15,
    "sentiment": 0.10,
    "news": 0.10,
}


@dataclass
class MarketSnapshot:
    """Per-ticker data envelope for a single orchestrator run.

    Stored one row per ticker in the ``market_snapshots`` Supabase table
    and threaded through the downstream stages (feature engine, LLM
    analyst) as the canonical input shape.
    """

    ticker: str
    as_of: str  # ISO-8601 UTC

    # Prices / returns
    price: Optional[float] = None
    price_source: str = "unavailable"   # live / cache / avg_cost_fallback / unavailable
    return_1d: Optional[float] = None
    return_5d: Optional[float] = None
    return_30d: Optional[float] = None
    volatility_30d: Optional[float] = None

    # Classification
    sector: str = ""
    industry: str = ""
    category: str = "Other"

    # Fundamentals (compact subset — tokens matter downstream)
    fundamentals: dict[str, Any] = field(default_factory=dict)

    # Sentiment
    sentiment_label: str = "neutral"
    sentiment_score: Optional[float] = None
    news_count: int = 0
    recent_headlines: list[str] = field(default_factory=list)

    # Data-quality envelope
    data_quality_score: float = 0.0
    missing_fields: list[str] = field(default_factory=list)
    fallback_chain: list[str] = field(default_factory=list)

    def to_row(self, *, run_id: str, user_id: str) -> dict[str, Any]:
        """Shape the snapshot for an insert into ``market_snapshots``.

        Columns not worth a dedicated field (recent_headlines, the raw
        fundamentals dict) ride along in ``raw`` as JSONB so the
        downstream feature engine can pull richer context without
        another fetch.
        """
        return {
            "run_id": run_id,
            "user_id": user_id,
            "ticker": self.ticker,
            "as_of": self.as_of,
            "price": self.price,
            "price_source": self.price_source,
            "return_1d": self.return_1d,
            "return_5d": self.return_5d,
            "return_30d": self.return_30d,
            "volatility_30d": self.volatility_30d,
            "sector": self.sector,
            "industry": self.industry,
            "category": self.category,
            "sentiment_label": self.sentiment_label,
            "sentiment_score": self.sentiment_score,
            "news_count": self.news_count,
            "data_quality_score": round(self.data_quality_score, 3),
            "missing_fields": self.missing_fields,
            "fallback_chain": self.fallback_chain,
            "raw": {
                "fundamentals": self.fundamentals,
                "recent_headlines": self.recent_headlines,
            },
        }

    def to_dict(self) -> dict[str, Any]:
        """Plain dict representation — used by downstream stages + logs."""
        return asdict(self)


# ── Pure builder ────────────────────────────────────────────────────────────


def build_market_snapshots(
    bundle: dict[str, Any],
    *,
    tickers: list[str],
    prior_insights: Optional[dict[str, dict[str, Any]]] = None,
    positions: Optional[list[dict[str, Any]]] = None,
    as_of: Optional[str] = None,
) -> dict[str, "MarketSnapshot"]:
    """Project an io_layer bundle into one :class:`MarketSnapshot` per ticker.

    The function is pure — identical inputs produce identical outputs, no
    side effects. It is the integration point between the existing
    resilient-IO layer and the new staged-intelligence pipeline.

    Fallback chain semantics:
        * ``live`` — live quote present in ``bundle['prices']``.
        * ``price_action`` — price fell back to yfinance last-close.
        * ``avg_cost`` — no quote, used the user's avg_cost as a stand-in.
        * ``cache`` — served from the market_cache stale-peek.
        * ``unavailable`` — no price at all.

    Only the sources actually consulted make it into ``fallback_chain`` so
    log lines are truthful — a ticker that succeeded on the first try
    shows ``['live']``, a ticker that degraded twice shows the full chain.
    """
    prior_insights = prior_insights or {}
    positions_by_ticker = {
        (p.get("ticker") or "").upper(): p for p in (positions or [])
    }
    as_of = as_of or datetime.now(timezone.utc).isoformat()

    prices = bundle.get("prices") or bundle.get("live_prices") or {}
    news_map = bundle.get("news") or {}
    fundamentals_map = bundle.get("fundamentals") or bundle.get("funds") or {}
    price_action_map = bundle.get("price_action") or {}
    source_status = bundle.get("source_status") or {}

    snapshots: dict[str, MarketSnapshot] = {}
    for raw_ticker in tickers:
        ticker = (raw_ticker or "").upper()
        if not ticker:
            continue

        snap = MarketSnapshot(ticker=ticker, as_of=as_of)
        position = positions_by_ticker.get(ticker, {})
        snap.category = position.get("category") or "Other"
        prior = prior_insights.get(ticker) or {}

        _apply_prices_and_returns(
            snap,
            price=prices.get(ticker),
            price_action=price_action_map.get(ticker),
            avg_cost=_to_float(position.get("avg_cost")),
            source_status=source_status,
        )
        _apply_volatility(snap, price_action=price_action_map.get(ticker))
        _apply_sector(snap, fundamentals=fundamentals_map.get(ticker))
        _apply_fundamentals(snap, fundamentals=fundamentals_map.get(ticker))
        _apply_sentiment(
            snap,
            news=news_map.get(ticker),
            prior=prior,
        )

        snap.data_quality_score = _compute_quality_score(snap)
        snapshots[ticker] = snap

    return snapshots


# ── Field appliers ──────────────────────────────────────────────────────────


def _apply_prices_and_returns(
    snap: "MarketSnapshot",
    *,
    price: Any,
    price_action: Optional[dict[str, Any]],
    avg_cost: float,
    source_status: dict[str, str],
) -> None:
    """Resolve ``price`` + all three return horizons through the fallback chain."""
    pa = price_action or {}

    live_price = _to_float_or_none(price)
    if live_price is not None and live_price > 0:
        snap.price = live_price
        snap.price_source = "live"
        snap.fallback_chain.append("live")
    else:
        # Primary live quote failed — try price_action last-close, then
        # avg_cost fallback. Each step is recorded in ``fallback_chain``
        # so the log line shows the exact degradation path per ticker.
        snap.fallback_chain.append("live_failed")
        pa_last = _to_float_or_none(pa.get("last"))
        if pa_last is not None and pa_last > 0:
            snap.price = pa_last
            snap.price_source = "price_action"
            snap.fallback_chain.append("price_action")
        elif avg_cost > 0:
            snap.price = avg_cost
            snap.price_source = "avg_cost_fallback"
            snap.fallback_chain.append("avg_cost")
            snap.missing_fields.append("price")
        else:
            snap.price = None
            snap.price_source = "unavailable"
            snap.fallback_chain.append("unavailable")
            snap.missing_fields.append("price")

    # Record what upstream said about each provider — helpful when triaging
    # which specific source failed (429 vs 403 vs network). We only attach
    # non-ok statuses to avoid noise when everything is healthy.
    for provider, status in source_status.items():
        if status and status != "ok":
            tag = f"{provider}:{status}"
            if tag not in snap.fallback_chain:
                snap.fallback_chain.append(tag)

    snap.return_1d = _to_float_or_none(pa.get("pct_1d"))
    snap.return_5d = _to_float_or_none(pa.get("pct_5d"))
    snap.return_30d = _to_float_or_none(pa.get("pct_30d"))
    if snap.return_5d is None:
        snap.missing_fields.append("return_5d")
    if snap.return_30d is None:
        snap.missing_fields.append("return_30d")


def _apply_volatility(
    snap: "MarketSnapshot",
    *,
    price_action: Optional[dict[str, Any]],
) -> None:
    vol = _to_float_or_none((price_action or {}).get("volatility_30d"))
    snap.volatility_30d = vol
    if vol is None:
        snap.missing_fields.append("volatility_30d")


def _apply_sector(
    snap: "MarketSnapshot",
    *,
    fundamentals: Optional[dict[str, Any]],
) -> None:
    fundamentals = fundamentals or {}
    sector = fundamentals.get("sector") or ""
    industry = fundamentals.get("industry") or ""
    snap.sector = sector
    snap.industry = industry
    if not sector:
        snap.missing_fields.append("sector")


def _apply_fundamentals(
    snap: "MarketSnapshot",
    *,
    fundamentals: Optional[dict[str, Any]],
) -> None:
    if not fundamentals:
        snap.missing_fields.append("fundamentals")
        return
    # Keep a compact subset — the downstream feature engine rebuilds
    # richer derivations from these five keys if needed.
    keep = ("pe", "forward_pe", "profit_margin", "revenue_growth", "dividend_yield")
    compact = {k: fundamentals[k] for k in keep if fundamentals.get(k) is not None}
    snap.fundamentals = compact
    if not compact:
        snap.missing_fields.append("fundamentals")


def _apply_sentiment(
    snap: "MarketSnapshot",
    *,
    news: Optional[list[dict[str, Any]]],
    prior: dict[str, Any],
) -> None:
    headlines: list[str] = []
    for item in news or []:
        headline = (item.get("headline") or "").strip()
        if headline:
            headlines.append(headline[:160])
    snap.recent_headlines = headlines[:5]
    snap.news_count = len(news or [])

    label = (prior.get("sentiment_label") or "").strip().lower()
    score = _to_float_or_none(prior.get("sentiment_score"))
    if label:
        snap.sentiment_label = label
        snap.sentiment_score = score
    else:
        snap.sentiment_label = "neutral"
        snap.sentiment_score = None
        snap.missing_fields.append("sentiment")
    if snap.news_count == 0:
        snap.missing_fields.append("news")


# ── Data-quality score ──────────────────────────────────────────────────────


def _compute_quality_score(snap: "MarketSnapshot") -> float:
    """Weighted sum of signals actually present on ``snap``.

    Deliberately does NOT start at 1.0 and subtract missing fields —
    starting from zero and adding weight for each present signal makes
    the score strictly vary with coverage and prevents a silent "1.0"
    slipping through when the bundle is degraded.
    """
    score = 0.0
    if snap.price is not None and snap.price_source == "live":
        score += _QUALITY_WEIGHTS["price"]
    elif snap.price is not None:
        # Non-live price still counts partially — we have a number to
        # reason on, but it's staler than a live quote.
        score += _QUALITY_WEIGHTS["price"] * 0.5
    if snap.return_5d is not None:
        score += _QUALITY_WEIGHTS["return_5d"]
    if snap.return_30d is not None:
        score += _QUALITY_WEIGHTS["return_30d"]
    if snap.volatility_30d is not None:
        score += _QUALITY_WEIGHTS["volatility"]
    if snap.sector:
        score += _QUALITY_WEIGHTS["sector"]
    if snap.fundamentals:
        score += _QUALITY_WEIGHTS["fundamentals"]
    if snap.sentiment_label and snap.sentiment_label != "neutral":
        score += _QUALITY_WEIGHTS["sentiment"]
    elif snap.sentiment_score is not None:
        score += _QUALITY_WEIGHTS["sentiment"] * 0.5
    if snap.news_count > 0:
        score += _QUALITY_WEIGHTS["news"]
    return max(0.0, min(1.0, round(score, 3)))


# ── Small helpers ───────────────────────────────────────────────────────────


def _to_float(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _to_float_or_none(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN guard
        return None
    return f
