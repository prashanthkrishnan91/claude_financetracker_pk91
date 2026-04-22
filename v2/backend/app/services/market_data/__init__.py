"""Resilient market data facade — single entry point for upstream quotes.

Callers should import ``ResilientMarketProvider`` / ``get_market_snapshot``
from this package rather than reaching into ``price_engine`` or ``io_layer``
directly. The facade guarantees:

  * cached-first lookups with TTLs per data family (prices/news/fundamentals)
  * per-provider circuit breakers + concurrency semaphores
  * a deterministic return schema — even when every upstream fails
"""

from .resilient_provider import (
    ResilientMarketProvider,
    get_market_snapshot,
)

__all__ = ["ResilientMarketProvider", "get_market_snapshot"]
