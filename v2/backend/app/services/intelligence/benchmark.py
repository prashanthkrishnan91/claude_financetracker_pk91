"""Benchmark price-action fetcher (SPY by default).

Used by the feature engine for relative-strength computation. Deliberately
tiny — it leans on the existing io_layer cache + coalescer so a multi-user
burst still collapses to a single yfinance request per TTL window.

Absolute failure isolation: a broken benchmark NEVER raises. The feature
engine sees ``{}`` and degrades relative_strength to absolute momentum.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:  # pragma: no cover — type-check only
    from ..cache.market_cache import MarketCache
    from ..market_data.request_coalescer import RequestCoalescer

logger = logging.getLogger(__name__)

_BENCHMARK_TTL_S = 300.0  # 5 minutes — matches price_action cache TTL defaults


async def fetch_benchmark_price_action(
    symbol: str = "SPY",
    *,
    cache: Optional["MarketCache"] = None,
    coalescer: Optional["RequestCoalescer"] = None,
) -> dict[str, Any]:
    """Return a price_action dict for ``symbol`` (default SPY).

    Same shape as ``data_sources.fetch_price_action`` — the feature
    engine reads ``pct_30d`` / ``pct_5d`` / ``volatility_30d`` for the
    relative-strength calculation. Returns ``{}`` when unavailable so
    the caller can degrade silently.
    """
    # Deferred imports break the intelligence ↔ agents/market_data
    # circular at module load time. The io_layer → agents chain pulls
    # ``request_coalescer`` back through ``app.services.ai``, so binding
    # these at the top of the module would bootstrap agents before
    # intelligence finishes its own __init__.
    from ..agents import data_sources as ds
    from ..cache.market_cache import get_market_cache
    from ..market_data.request_coalescer import (
        get_request_coalescer,
        make_key,
    )

    cache = cache or get_market_cache()
    coalescer = coalescer or get_request_coalescer()
    cache_key = f"benchmark_price_action:{symbol.upper()}"

    stale_entry = cache._store.get(cache_key)  # noqa: SLF001 — intentional stale peek
    stale_value = stale_entry.value if stale_entry is not None else None

    async def _fetch_once() -> dict[str, Any]:
        key = make_key("yfinance", "history", symbol.upper(), None)
        return await coalescer.coalesce(
            key,
            lambda: ds.fetch_price_action(symbol),
            provider="yfinance",
            ticker=symbol.upper(),
            distributed=False,  # benchmark is cheap; skip the shared lock
        )

    try:
        value = await cache.get_or_fetch(cache_key, _fetch_once, ttl=_BENCHMARK_TTL_S)
        if isinstance(value, dict):
            return value
        return {}
    except Exception as exc:  # noqa: BLE001 — absolute failure isolation
        logger.warning("benchmark fetch failed for %s: %s", symbol, exc)
        if isinstance(stale_value, dict) and stale_value:
            logger.info("benchmark serving stale cache for %s", symbol)
            return stale_value
        return {}
