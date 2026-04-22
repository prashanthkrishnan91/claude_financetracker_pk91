"""Parallel IO layer — cache-first external data fetch for the agent pipeline.

This module is the ONLY place that calls Finnhub / Polygon / yfinance /
CoinGecko for the orchestrator path. It collapses all per-ticker fan-out
into a single ``fetch_market_bundle`` coroutine that:

  1. serves every lookup from ``services.cache.market_cache`` first,
  2. coalesces concurrent requests for the same key (no duplicate in-flight),
  3. retries transient failures at the HTTP layer with exponential backoff
     (max 3 attempts per upstream call),
  4. ISOLATES failures — a broken upstream falls back to the cached value
     (even if expired) or a neutral empty value; it NEVER raises into the
     orchestrator and NEVER triggers a pipeline re-run.

No LLM calls. No DB writes. Pure IO + cache.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any, Awaitable, Callable, Optional

import httpx

from ..cache.market_cache import MarketCache, get_market_cache
from ..agents import data_sources as ds

logger = logging.getLogger(__name__)


# Exponential-backoff schedule for HTTP-layer retries. 3 retries max.
_HTTP_BACKOFF_S = (0.25, 0.5, 1.0)


# ── Retry with cache fallback ────────────────────────────────────────────────


async def _with_retry_and_cache_fallback(
    key: str,
    fetch: Callable[[], Awaitable[Any]],
    *,
    cache: MarketCache,
    ttl: Optional[float] = None,
    neutral: Any,
) -> Any:
    """Cache-first fetch with HTTP retries and a safe fallback.

    Flow:
      1. Serve fresh cache via ``cache.get_or_fetch`` (factory = retrying fetch).
      2. If all retries fail, return the most recent cached value (even if
         expired) when present, else the ``neutral`` default.
    """

    # Snapshot any existing entry BEFORE touching the cache — the get path
    # purges expired entries lazily, so we need the stale value for fallback.
    stale_entry = cache._store.get(key)  # noqa: SLF001 — intentional stale peek
    stale_value = stale_entry.value if stale_entry is not None else None

    async def _retrying_fetch() -> Any:
        last_exc: Optional[BaseException] = None
        for attempt in range(len(_HTTP_BACKOFF_S) + 1):
            try:
                return await fetch()
            except Exception as exc:  # noqa: BLE001 — classified below
                last_exc = exc
                if attempt == len(_HTTP_BACKOFF_S):
                    break
                delay = _HTTP_BACKOFF_S[attempt] + random.uniform(0, 0.1)
                logger.debug(
                    "io_layer retry %s attempt=%d delay=%.2fs err=%s",
                    key, attempt + 1, delay, str(exc)[:120],
                )
                await asyncio.sleep(delay)
        logger.warning(
            "io_layer %s exhausted retries: %s", key, str(last_exc)[:200]
        )
        # Re-raise so the outer ``get_or_fetch`` doesn't cache the failure;
        # the caller falls back to a stale entry / neutral value below.
        raise last_exc if last_exc else RuntimeError("unknown fetch error")

    try:
        value = await cache.get_or_fetch(key, _retrying_fetch, ttl=ttl)
        if value is not None:
            return value
        return neutral
    except Exception:
        if stale_value is not None:
            logger.info("io_layer serving stale cache for %s", key)
            return stale_value
        return neutral


# ── Individual fetchers (all cache-backed) ──────────────────────────────────


async def _fetch_news(
    client: httpx.AsyncClient,
    ticker: str,
    finnhub_key: str,
    cache: MarketCache,
) -> list[dict[str, Any]]:
    return await _with_retry_and_cache_fallback(
        key=f"news:{ticker.upper()}",
        fetch=lambda: ds.fetch_news_for_ticker(client, ticker, finnhub_key),
        cache=cache,
        neutral=[],
    )


async def _fetch_fundamentals(
    ticker: str,
    cache: MarketCache,
) -> dict[str, Any]:
    return await _with_retry_and_cache_fallback(
        key=f"fundamentals:{ticker.upper()}",
        fetch=lambda: ds.fetch_fundamentals(ticker),
        cache=cache,
        neutral={},
    )


async def _fetch_price_action(
    ticker: str,
    cache: MarketCache,
) -> dict[str, Any]:
    return await _with_retry_and_cache_fallback(
        key=f"price_action:{ticker.upper()}",
        fetch=lambda: ds.fetch_price_action(ticker),
        cache=cache,
        neutral={},
    )


async def _fetch_live_price(
    ticker: str,
    price_service: Any,
    cache: MarketCache,
) -> Optional[float]:
    """Fetch a single mid-price via the shared PriceService, cache-first."""
    if price_service is None:
        return None

    async def _one() -> Optional[float]:
        results = await price_service.fetch_prices([ticker])
        pr = (results or {}).get(ticker)
        if pr is not None and getattr(pr, "is_valid", False):
            return float(pr.mid_price)
        return None

    return await _with_retry_and_cache_fallback(
        key=f"price:{ticker.upper()}",
        fetch=_one,
        cache=cache,
        neutral=None,
    )


# ── Bundle fetcher (orchestrator entry point) ───────────────────────────────


async def fetch_market_bundle(
    tickers: list[str],
    *,
    price_service: Any = None,
    finnhub_key: str = "",
    polygon_key: str = "",
    cache: Optional[MarketCache] = None,
    include_news: bool = False,
    include_fundamentals: bool = False,
    include_price_action: bool = False,
) -> dict[str, Any]:
    """Return a fully-populated market bundle — never raises.

    Always returns the following structure (values may be empty/neutral, but
    the keys are guaranteed present so downstream consumers can destructure
    without ``KeyError``):

    ``{
        "tickers": [str, ...],
        "prices": {ticker: float},
        "live_prices": {ticker: float},   # legacy alias for ``prices``
        "news": {ticker: [items]},
        "fundamentals": {ticker: {...}},
        "funds": {ticker: {...}},          # legacy alias for ``fundamentals``
        "price_action": {ticker: {...}},
        "macro": {regime, sentiment, fallback, ...},
        "source_status": {coingecko, finnhub, polygon, ...},
        "missing_fields": [str, ...],
        "completeness_score": float [0..1],
        "timings_ms": {"total": float},
    }``

    Defaults to prices-only (the minimum the single-LLM pipeline needs). The
    orchestrator may opt into heavier fetches when the LLM context warrants it.

    All network errors are caught inside the per-ticker fetchers and degrade to
    neutral/empty values so the pipeline never retries orchestration.
    """
    cache = cache or get_market_cache()
    stage_start = time.perf_counter()

    unique_tickers = [t for t in {t.upper() for t in (tickers or []) if t}]
    if not unique_tickers:
        return _empty_bundle()

    # Prices — always fetched when a price_service is provided.
    price_tasks = {
        t: asyncio.create_task(_fetch_live_price(t, price_service, cache))
        for t in unique_tickers
    }

    # Optional heavier fetches — wrapped in a short-lived httpx client.
    news_tasks: dict[str, asyncio.Task] = {}
    fundamentals_tasks: dict[str, asyncio.Task] = {}
    price_action_tasks: dict[str, asyncio.Task] = {}

    if include_news or include_fundamentals or include_price_action:
        async with await ds._get_client() as client:  # noqa: SLF001 — internal helper
            if include_news:
                news_tasks = {
                    t: asyncio.create_task(_fetch_news(client, t, finnhub_key, cache))
                    for t in unique_tickers
                }
            if include_fundamentals:
                fundamentals_tasks = {
                    t: asyncio.create_task(_fetch_fundamentals(t, cache))
                    for t in unique_tickers
                }
            if include_price_action:
                price_action_tasks = {
                    t: asyncio.create_task(_fetch_price_action(t, cache))
                    for t in unique_tickers
                }

            # Gather heavy tasks inside the client scope so httpx isn't closed
            # while requests are still in flight.
            all_heavy = list(news_tasks.values()) + list(fundamentals_tasks.values()) \
                + list(price_action_tasks.values())
            if all_heavy:
                await asyncio.gather(*all_heavy, return_exceptions=True)

    # Prices can finish outside the httpx scope (they use their own client).
    await asyncio.gather(*price_tasks.values(), return_exceptions=True)

    def _collect(tasks: dict[str, asyncio.Task], default: Any) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for t, task in tasks.items():
            try:
                val = task.result()
            except Exception as exc:  # noqa: BLE001
                logger.debug("io_layer task %s failed: %s", t, exc)
                val = default
            if val is None:
                continue
            out[t] = val
        return out

    live_prices = _collect(price_tasks, default=None)
    news = _collect(news_tasks, default=[]) if news_tasks else {}
    fundamentals = _collect(fundamentals_tasks, default={}) if fundamentals_tasks else {}
    price_action = _collect(price_action_tasks, default={}) if price_action_tasks else {}

    # Filter prices to numeric-only before reporting completeness.
    prices = {k: v for k, v in live_prices.items() if isinstance(v, (int, float))}

    # ── Completeness + source-status reporting ──────────────────────────────
    # Completeness reflects how much of the REQUESTED data actually arrived.
    # When heavier fetches aren't requested those buckets don't count against
    # the score — e.g. a prices-only bundle is 100% complete when every
    # ticker has a price, independent of news/funds.
    requested_buckets: list[tuple[str, bool, dict[str, Any]]] = [
        ("prices", True, prices),
        ("news", include_news, news),
        ("fundamentals", include_fundamentals, fundamentals),
        ("price_action", include_price_action, price_action),
    ]
    completeness_score, missing_fields = _compute_completeness(
        unique_tickers, requested_buckets
    )

    source_status = ds.get_provider_status()

    elapsed_ms = round((time.perf_counter() - stage_start) * 1000, 2)
    logger.info(
        "io_layer bundle ready — tickers=%d prices=%d news=%d funds=%d "
        "completeness=%.2f elapsed_ms=%.1f sources=%s",
        len(unique_tickers), len(prices), len(news), len(fundamentals),
        completeness_score, elapsed_ms, source_status,
    )

    return {
        "tickers": unique_tickers,
        "prices": prices,
        "live_prices": prices,  # legacy alias — keep callers that read this key working
        "news": news,
        "fundamentals": fundamentals,
        "funds": fundamentals,  # legacy alias
        "price_action": price_action,
        "macro": _macro_fallback(),
        "source_status": source_status,
        "missing_fields": missing_fields,
        "completeness_score": completeness_score,
        "timings_ms": {"total": elapsed_ms},
    }


def _empty_bundle() -> dict[str, Any]:
    """Canonical empty bundle — returned when no tickers are supplied."""
    return {
        "tickers": [],
        "prices": {},
        "live_prices": {},
        "news": {},
        "fundamentals": {},
        "funds": {},
        "price_action": {},
        "macro": _macro_fallback(),
        "source_status": ds.get_provider_status(),
        "missing_fields": [],
        "completeness_score": 1.0,
        "timings_ms": {"total": 0.0},
    }


def _compute_completeness(
    tickers: list[str],
    requested_buckets: list[tuple[str, bool, dict[str, Any]]],
) -> tuple[float, list[str]]:
    """Return ``(score, missing_fields)`` for the bundle.

    Score is the mean of per-bucket coverage ratios across the buckets the
    caller actually requested. A bucket with zero tickers covered adds its
    field name to ``missing_fields`` so the LLM can see exactly which slice
    of the data is degraded.
    """
    total = len(tickers) or 1
    ratios: list[float] = []
    missing: list[str] = []
    for name, requested, values in requested_buckets:
        if not requested:
            continue
        have = sum(1 for t in tickers if values.get(t))
        ratio = have / total
        ratios.append(ratio)
        if have == 0:
            missing.append(name)
        elif have < total:
            missing.append(f"{name}(partial)")
    score = round(sum(ratios) / len(ratios), 3) if ratios else 1.0
    return score, missing


def _macro_fallback() -> dict[str, Any]:
    """Default macro object — returned when ``macro_cache`` is missing/404.

    Kept as a stable shape so the LLM contract can reference macro fields
    without ever checking for existence. ``fallback=True`` signals degraded
    mode so reasoning can reflect the uncertainty.
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


# ── Macro snapshot (cached portfolio-level) ─────────────────────────────────


async def fetch_macro_snapshot(
    *,
    cache: Optional[MarketCache] = None,
    factory: Optional[Callable[[], Awaitable[str]]] = None,
) -> str:
    """Return a cached macro summary string (neutral placeholder if none).

    The orchestrator currently pulls macro from the ``macro_cache`` Supabase
    table via the context_builder wrapper. This function is a forward-looking
    hook for when a macro source is wired up without requiring another cache
    rewrite. Safe to call with no ``factory`` — returns the cached value or
    a neutral placeholder.
    """
    cache = cache or get_market_cache()

    async def _placeholder() -> str:
        return (
            "Macro context unavailable — evaluate each ticker on its own merits "
            "and existing portfolio concentration."
        )

    return await _with_retry_and_cache_fallback(
        key="macro:snapshot",
        fetch=factory or _placeholder,
        cache=cache,
        neutral=(
            "Macro context unavailable — evaluate each ticker on its own merits "
            "and existing portfolio concentration."
        ),
    )
