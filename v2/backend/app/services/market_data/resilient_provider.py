"""Resilient market-data provider — unified, cache-first, circuit-broken.

Composes the existing ``io_layer`` (cache + coalescing) and ``data_sources``
(per-provider circuit breakers + semaphores) into a single facade that
returns a stable, fully-populated schema regardless of upstream health.

Design intent (see tasks/todo.md — task #1):
  * Centralized market-data layer: one call site for orchestrator / routers
  * TTL-based caching: prices 30s, news 5m, fundamentals 1h, macro 15m
  * Request deduplication (single-flight per ticker) via MarketCache locks
  * Batched requests via ``io_layer.fetch_market_bundle``
  * Per-provider circuit breaker + concurrency cap in ``data_sources``

Return schema (always present, values may be empty/neutral):
  ``{
      "prices": {ticker: float | None},
      "source_status": {
          "coingecko": "ok" | "rate_limited" | "failed",
          "finnhub":   "ok" | "rate_limited" | "failed",
          "polygon":   "ok" | "blocked" | "disabled",
      },
      "missing_fields": [str, ...],
      "completeness_score": float [0..1],
      "news": {ticker: [...]},
      "fundamentals": {ticker: {...}},
      "macro": {regime, inflation, rates, sentiment, fallback, ...},
      "tickers": [str, ...],
  }``
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from ..ai import io_layer
from ..cache.market_cache import MarketCache, get_market_cache

logger = logging.getLogger(__name__)


class ResilientMarketProvider:
    """Facade around ``io_layer`` with a simplified, stable public API.

    Instances are cheap — construct per request (or reuse the module-level
    ``get_market_snapshot`` helper). The underlying cache is a process-wide
    singleton, so TTL windows are shared across instances.
    """

    def __init__(
        self,
        *,
        price_service: Any = None,
        finnhub_key: str = "",
        polygon_key: str = "",
        cache: Optional[MarketCache] = None,
    ) -> None:
        self._price_service = price_service
        self._finnhub_key = finnhub_key
        self._polygon_key = polygon_key
        self._cache = cache or get_market_cache()

    async def fetch_snapshot(
        self,
        tickers: list[str],
        *,
        include_news: bool = False,
        include_fundamentals: bool = False,
        include_price_action: bool = False,
    ) -> dict[str, Any]:
        """Return the unified snapshot schema.

        Never raises: every upstream failure maps to an empty bucket + a
        ``source_status`` entry that reflects the degraded state.
        """
        bundle = await io_layer.fetch_market_bundle(
            tickers,
            price_service=self._price_service,
            finnhub_key=self._finnhub_key,
            polygon_key=self._polygon_key,
            cache=self._cache,
            include_news=include_news,
            include_fundamentals=include_fundamentals,
            include_price_action=include_price_action,
        )
        # Normalise prices: every requested ticker is represented (value may
        # be ``None`` when no upstream returned a quote and no stale cache
        # exists). This stops consumers from accidentally treating a missing
        # key as a crash condition.
        tickers_upper = [t.upper() for t in (tickers or []) if t]
        prices: dict[str, Optional[float]] = {
            t: bundle["prices"].get(t) for t in tickers_upper
        }
        return {
            "tickers": tickers_upper,
            "prices": prices,
            "source_status": bundle.get("source_status") or {},
            "missing_fields": bundle.get("missing_fields") or [],
            "completeness_score": float(bundle.get("completeness_score") or 0.0),
            "news": bundle.get("news") or {},
            "fundamentals": bundle.get("fundamentals") or {},
            "price_action": bundle.get("price_action") or {},
            "macro": bundle.get("macro") or _default_macro(),
            "timings_ms": bundle.get("timings_ms") or {"total": 0.0},
        }


async def get_market_snapshot(
    tickers: list[str],
    *,
    price_service: Any = None,
    finnhub_key: str = "",
    polygon_key: str = "",
    include_news: bool = False,
    include_fundamentals: bool = False,
    include_price_action: bool = False,
    cache: Optional[MarketCache] = None,
) -> dict[str, Any]:
    """Module-level helper — one-shot snapshot without constructing a class."""
    provider = ResilientMarketProvider(
        price_service=price_service,
        finnhub_key=finnhub_key,
        polygon_key=polygon_key,
        cache=cache,
    )
    return await provider.fetch_snapshot(
        tickers,
        include_news=include_news,
        include_fundamentals=include_fundamentals,
        include_price_action=include_price_action,
    )


def _default_macro() -> dict[str, Any]:
    """Fallback macro shape — mirrors ``io_layer._macro_fallback`` but safe
    to import independently (no circular-import risk for external callers).
    """
    return {
        "regime": "unknown",
        "inflation": None,
        "rates": None,
        "sentiment": "neutral",
        "fallback": True,
        "summary": (
            "Macro context unavailable — evaluate each ticker on its own merits "
            "and existing portfolio concentration."
        ),
    }
