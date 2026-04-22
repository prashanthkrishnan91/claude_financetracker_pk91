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
from ..market_data.request_coalescer import (
    RequestCoalescer,
    get_request_coalescer,
    make_key,
)

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


# ── Individual fetchers (all cache-backed + coalesced) ─────────────────────
# Each per-ticker fetcher goes through:
#   1. ``MarketCache.get_or_fetch`` — coarse dedup + TTL window cache
#   2. ``RequestCoalescer.coalesce`` — fine-grained in-flight dedup
#      on the EXACT ``(provider, endpoint, ticker, params)`` tuple
#
# The coalescer adds a belt-and-suspenders guarantee that duplicate HTTP
# calls never escape the same process even when the cache is cold.


async def _coalesced(
    coalescer: RequestCoalescer,
    provider: str,
    endpoint: str,
    ticker: str,
    factory: Callable[[], Awaitable[Any]],
    *,
    params: Optional[dict[str, Any]] = None,
) -> Any:
    """Run ``factory`` through the shared request coalescer.

    Concurrent callers for the same ``(provider, endpoint, ticker, params)``
    tuple await a single in-flight Future, so the upstream API sees exactly
    one request regardless of concurrency.
    """
    key = make_key(provider, endpoint, ticker, params)
    return await coalescer.coalesce(key, factory, provider=provider, ticker=ticker)


async def _fetch_news(
    client: httpx.AsyncClient,
    ticker: str,
    finnhub_key: str,
    cache: MarketCache,
    coalescer: RequestCoalescer,
) -> list[dict[str, Any]]:
    async def _factory():
        return await _coalesced(
            coalescer, "finnhub", "company-news", ticker,
            lambda: ds.fetch_news_for_ticker(client, ticker, finnhub_key),
        )
    return await _with_retry_and_cache_fallback(
        key=f"news:{ticker.upper()}",
        fetch=_factory,
        cache=cache,
        neutral=[],
    )


async def _fetch_fundamentals(
    ticker: str,
    cache: MarketCache,
    coalescer: RequestCoalescer,
) -> dict[str, Any]:
    async def _factory():
        return await _coalesced(
            coalescer, "yfinance", "fundamentals", ticker,
            lambda: ds.fetch_fundamentals(ticker),
        )
    return await _with_retry_and_cache_fallback(
        key=f"fundamentals:{ticker.upper()}",
        fetch=_factory,
        cache=cache,
        neutral={},
    )


async def _fetch_price_action(
    ticker: str,
    cache: MarketCache,
    coalescer: RequestCoalescer,
) -> dict[str, Any]:
    async def _factory():
        return await _coalesced(
            coalescer, "yfinance", "history", ticker,
            lambda: ds.fetch_price_action(ticker),
        )
    return await _with_retry_and_cache_fallback(
        key=f"price_action:{ticker.upper()}",
        fetch=_factory,
        cache=cache,
        neutral={},
    )


async def _fetch_live_price(
    ticker: str,
    price_service: Any,
    cache: MarketCache,
    coalescer: RequestCoalescer,
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

    async def _factory():
        return await _coalesced(
            coalescer, "price_service", "quote", ticker, _one,
        )

    return await _with_retry_and_cache_fallback(
        key=f"price:{ticker.upper()}",
        fetch=_factory,
        cache=cache,
        neutral=None,
    )


# ── Provider-batched helpers ──────────────────────────────────────────────
# Replace per-ticker fan-out with grouped execution so upstream load spikes
# are bounded by the per-provider semaphore rather than the ticker count.
# Free-tier providers (Finnhub, CoinGecko) don't expose true batch
# endpoints, so these helpers are *logical* batches: they dispatch all
# ticker fetches concurrently, coalesce duplicates, and return a
# ``{ticker: value}`` dict — the semaphore inside ``data_sources`` keeps
# the actual concurrent HTTP load to 2–3 per provider regardless of the
# batch size.


async def _fetch_news_batch(
    client: httpx.AsyncClient,
    tickers: list[str],
    finnhub_key: str,
    cache: MarketCache,
    coalescer: RequestCoalescer,
) -> dict[str, list[dict[str, Any]]]:
    """Batch news fetch — one logical group for the finnhub provider."""
    tasks = {
        t: asyncio.create_task(_fetch_news(client, t, finnhub_key, cache, coalescer))
        for t in tickers
    }
    await asyncio.gather(*tasks.values(), return_exceptions=True)
    return _collect_tasks(tasks, default=[])


async def _fetch_fundamentals_batch(
    tickers: list[str],
    cache: MarketCache,
    coalescer: RequestCoalescer,
) -> dict[str, dict[str, Any]]:
    """Batch fundamentals fetch — one logical group for the yfinance provider."""
    tasks = {
        t: asyncio.create_task(_fetch_fundamentals(t, cache, coalescer))
        for t in tickers
    }
    await asyncio.gather(*tasks.values(), return_exceptions=True)
    return _collect_tasks(tasks, default={})


async def _fetch_price_action_batch(
    tickers: list[str],
    cache: MarketCache,
    coalescer: RequestCoalescer,
) -> dict[str, dict[str, Any]]:
    """Batch price-action fetch — one logical group for the yfinance provider."""
    tasks = {
        t: asyncio.create_task(_fetch_price_action(t, cache, coalescer))
        for t in tickers
    }
    await asyncio.gather(*tasks.values(), return_exceptions=True)
    return _collect_tasks(tasks, default={})


async def _fetch_prices_batch(
    tickers: list[str],
    price_service: Any,
    cache: MarketCache,
    coalescer: RequestCoalescer,
) -> dict[str, Optional[float]]:
    """Batch price fetch — delegates to the shared PriceService."""
    tasks = {
        t: asyncio.create_task(_fetch_live_price(t, price_service, cache, coalescer))
        for t in tickers
    }
    await asyncio.gather(*tasks.values(), return_exceptions=True)
    return _collect_tasks(tasks, default=None)


def _collect_tasks(
    tasks: dict[str, asyncio.Task],
    *,
    default: Any,
) -> dict[str, Any]:
    """Drain task map into a ``{ticker: value}`` dict, swallowing exceptions."""
    out: dict[str, Any] = {}
    for t, task in tasks.items():
        try:
            val = task.result()
        except Exception as exc:  # noqa: BLE001
            logger.debug("io_layer batch task %s failed: %s", t, exc)
            val = default
        if val is None:
            continue
        out[t] = val
    return out


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
    coalescer = get_request_coalescer()
    stage_start = time.perf_counter()

    unique_tickers = [t for t in {t.upper() for t in (tickers or []) if t}]
    if not unique_tickers:
        return _empty_bundle()

    # ── Provider-batched execution ────────────────────────────────────────
    # Grouping by provider keeps load predictable: one batch per upstream
    # (prices, finnhub-news, yfinance-fundamentals, yfinance-history). The
    # per-provider semaphore inside ``data_sources`` throttles concurrent
    # HTTP calls within each batch; the coalescer + cache collapse
    # duplicates across concurrent callers.
    live_prices: dict[str, Optional[float]] = {}
    news: dict[str, list[dict[str, Any]]] = {}
    fundamentals: dict[str, dict[str, Any]] = {}
    price_action: dict[str, dict[str, Any]] = {}

    price_batch_task = asyncio.create_task(
        _fetch_prices_batch(unique_tickers, price_service, cache, coalescer)
    )

    if include_news or include_fundamentals or include_price_action:
        async with await ds._get_client() as client:  # noqa: SLF001 — internal helper
            provider_batches: list[Awaitable[Any]] = []
            if include_news:
                provider_batches.append(
                    _fetch_news_batch(
                        client, unique_tickers, finnhub_key, cache, coalescer
                    )
                )
            if include_fundamentals:
                provider_batches.append(
                    _fetch_fundamentals_batch(unique_tickers, cache, coalescer)
                )
            if include_price_action:
                provider_batches.append(
                    _fetch_price_action_batch(unique_tickers, cache, coalescer)
                )

            results = await asyncio.gather(*provider_batches, return_exceptions=True)
            idx = 0
            if include_news:
                news = _unwrap_batch(results[idx], default={})
                idx += 1
            if include_fundamentals:
                fundamentals = _unwrap_batch(results[idx], default={})
                idx += 1
            if include_price_action:
                price_action = _unwrap_batch(results[idx], default={})
                idx += 1

    # Prices finish outside the httpx scope — they use the price_service's
    # own HTTP client, so ordering w.r.t. the heavy fetch scope doesn't matter.
    try:
        live_prices = await price_batch_task
    except Exception as exc:  # noqa: BLE001 — absolute failure isolation
        logger.debug("io_layer price batch failed: %s", exc)
        live_prices = {}

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
    # Number of provider batches actually dispatched this run. Prices are
    # always a batch when a price_service is available; news/funds/price_
    # action are opt-in. 3–6 batches is the target per the stability spec.
    batches = 1 + int(include_news) + int(include_fundamentals) + int(include_price_action)
    coalescer_stats = coalescer.stats()
    logger.info(
        "io_layer bundle ready — tickers=%d batches=%d prices=%d news=%d "
        "funds=%d completeness=%.2f elapsed_ms=%.1f coalesced=%d "
        "violations=%d sources=%s",
        len(unique_tickers), batches, len(prices), len(news),
        len(fundamentals), completeness_score, elapsed_ms,
        coalescer_stats.get("coalesced", 0),
        coalescer_stats.get("violations", 0),
        source_status,
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


def _unwrap_batch(result: Any, *, default: dict) -> dict:
    """Turn a batch task result (or exception) into a plain dict."""
    if isinstance(result, BaseException):
        logger.debug("io_layer provider batch raised: %s", result)
        return dict(default)
    if not isinstance(result, dict):
        return dict(default)
    return result


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
