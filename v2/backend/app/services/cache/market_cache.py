"""Shared TTL cache for market data with request coalescing.

Used by the Portfolio Engine v2 IO layer to avoid:
  * duplicate CoinGecko / Polygon / Finnhub / yfinance calls within a TTL window
  * concurrent fetches of the same key (N in-flight requests collapse to 1)

Design:
  * Pure in-memory (per-process). Redis is an optional later upgrade.
  * One ``asyncio.Lock`` per key — coalesces concurrent ``get_or_fetch`` calls
    behind the same key so the upstream API is called once per TTL window.
  * TTLs chosen per key family; caller can override.

Key conventions (documented):
  * ``price:{SYMBOL}``     — last-known quote dict
  * ``news:{SYMBOL}``      — list of normalised news items
  * ``fundamentals:{SYM}`` — fundamentals dict
  * ``macro:snapshot``     — portfolio-level macro summary
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)


# Default TTLs (seconds). Keep conservative — IO layer callers may override.
DEFAULT_TTL_S: dict[str, float] = {
    "price": 30.0,          # prices: sub-minute freshness
    "news": 300.0,          # news: 5 min is plenty for headline-level analysis
    "fundamentals": 3600.0, # fundamentals: daily-ish, but 1h for safety
    "macro": 900.0,         # macro snapshot: 15 min
}


@dataclass
class _Entry:
    value: Any
    expires_at: float  # monotonic


class MarketCache:
    """Async-safe TTL cache with per-key request coalescing.

    Public API:
      * ``await get_or_fetch(key, ttl, factory)``  — primary path
      * ``await get(key)`` / ``await set(key, value, ttl)`` — manual
      * ``await invalidate(key)`` — drop a key on demand
    """

    def __init__(self) -> None:
        self._store: dict[str, _Entry] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._store_lock = asyncio.Lock()
        self.hits = 0
        self.misses = 0
        self.coalesced = 0

    # ── TTL resolution ────────────────────────────────────────────────────

    @staticmethod
    def _family(key: str) -> str:
        return key.split(":", 1)[0] if ":" in key else key

    @classmethod
    def default_ttl_for(cls, key: str) -> float:
        return DEFAULT_TTL_S.get(cls._family(key), 60.0)

    # ── Core operations ───────────────────────────────────────────────────

    async def get(self, key: str) -> Optional[Any]:
        entry = self._store.get(key)
        if entry is None:
            return None
        if entry.expires_at <= time.monotonic():
            # Expired — purge lazily.
            self._store.pop(key, None)
            return None
        return entry.value

    async def set(self, key: str, value: Any, ttl: Optional[float] = None) -> None:
        ttl_s = ttl if ttl is not None else self.default_ttl_for(key)
        self._store[key] = _Entry(value=value, expires_at=time.monotonic() + ttl_s)

    async def invalidate(self, key: str) -> None:
        self._store.pop(key, None)

    async def _lock_for(self, key: str) -> asyncio.Lock:
        # Under a master lock to avoid racing two callers creating two locks.
        async with self._store_lock:
            lock = self._locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[key] = lock
            return lock

    async def get_or_fetch(
        self,
        key: str,
        factory: Callable[[], Awaitable[Any]],
        *,
        ttl: Optional[float] = None,
    ) -> Any:
        """Return cached value, else call ``factory()`` (coalesced) and cache.

        Concurrent calls for the same key wait on a shared lock and all receive
        the single fetched value — the upstream API is called at most once per
        TTL window regardless of concurrency.

        Failure semantics: exceptions from ``factory`` propagate. The caller
        (io_layer) is responsible for catching them and falling back to cached
        data or neutral defaults. This keeps the cache store honest — we never
        cache a failure as if it were a hit.
        """
        # Fast path — no lock needed if fresh.
        cached = await self.get(key)
        if cached is not None:
            self.hits += 1
            return cached

        lock = await self._lock_for(key)
        async with lock:
            # Re-check under lock — another coroutine may have populated.
            cached = await self.get(key)
            if cached is not None:
                self.coalesced += 1
                return cached

            self.misses += 1
            value = await factory()
            # Only cache non-None values so that transient empty fetches
            # don't poison the slot for the full TTL window.
            if value is not None:
                await self.set(key, value, ttl=ttl)
            return value

    # ── Introspection ─────────────────────────────────────────────────────

    def stats(self) -> dict[str, int]:
        return {
            "entries": len(self._store),
            "hits": self.hits,
            "misses": self.misses,
            "coalesced": self.coalesced,
        }

    async def clear(self) -> None:
        """Drop all entries — used in tests."""
        self._store.clear()
        self._locks.clear()
        self.hits = self.misses = self.coalesced = 0


# ── Module-level singleton ───────────────────────────────────────────────────

_SINGLETON: Optional[MarketCache] = None


def get_market_cache() -> MarketCache:
    """Return the process-wide cache instance (lazy)."""
    global _SINGLETON
    if _SINGLETON is None:
        _SINGLETON = MarketCache()
    return _SINGLETON
