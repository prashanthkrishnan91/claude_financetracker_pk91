"""Async data-source helpers for the multi-agent pipeline.

All network calls are sandboxed here so the agents themselves stay pure.
Every helper degrades gracefully — the agent can still reason on partial
data rather than crash the pipeline.

Resilience layer (shared across helpers):
  * Per-provider circuit breakers — 429/403/5xx streaks open the breaker
    for a cool-off window; subsequent calls short-circuit to the neutral
    fallback instead of hammering the upstream.
  * Optional distributed breaker state (``ProviderHealthStore``) mirrors
    the per-process breaker counters to a shared backend (Supabase table
    ``provider_health``) so all Railway workers agree on provider health.
    Falls back to pure in-memory state when no backend is configured.
  * Per-provider concurrency semaphores — cap in-flight requests per
    provider so a burst of tickers doesn't trip free-tier rate limits.
  * Exponential backoff + jitter for transient upstream errors (429/5xx)
    — 403 is treated as a hard block and is NEVER retried.
  * Every helper catches network exceptions and returns an empty value
    so the orchestrator never sees a raised 429/403.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Optional

import httpx

logger = logging.getLogger(__name__)

_FINNHUB_BASE = "https://finnhub.io/api/v1"
_POLYGON_BASE = "https://api.polygon.io"
_COINGECKO_BASE = "https://api.coingecko.com/api/v3"

# Small, unbounded client reused per-orchestrator-run.
_HTTP_TIMEOUT = 8.0


# ── Circuit breaker + concurrency guard ─────────────────────────────────────
# These limit upstream load across the whole process. The breaker opens on
# repeated 429/403/5xx signals and auto-resets after the cool-off window so
# a transient rate-limit burst doesn't permanently disable the source.

_BREAKER_THRESHOLD = 3
_BREAKER_COOLDOWN_S = 90.0  # 60-120s per spec; 90s middle-of-the-road


@dataclass
class _ProviderBreaker:
    name: str
    failures: int = 0
    opened_at: float = 0.0
    last_reason: str = ""

    def is_open(self) -> bool:
        # Hydrate from the distributed store so cross-worker state is
        # visible here. If no store is configured this is a no-op.
        _STORE.hydrate(self)
        if self.failures < _BREAKER_THRESHOLD:
            return False
        if time.time() - self.opened_at >= _BREAKER_COOLDOWN_S:
            # Cool-off expired — give the provider another chance. The
            # next success resets; another failure re-opens.
            self.failures = 0
            self.opened_at = 0.0
            _STORE.publish(self)
            return False
        return True

    def record_failure(self, reason: str) -> None:
        self.failures += 1
        self.last_reason = reason[:120]
        if self.failures >= _BREAKER_THRESHOLD:
            self.opened_at = time.time()
        _STORE.publish(self)

    def record_success(self) -> None:
        self.failures = 0
        self.opened_at = 0.0
        self.last_reason = ""
        _STORE.publish(self)

    def status(self) -> str:
        if self.is_open():
            reason = (self.last_reason or "").lower()
            if "429" in reason or "rate" in reason:
                return "rate_limited"
            if "403" in reason or "forbidden" in reason or "blocked" in reason:
                return "blocked"
            return "failed"
        return "ok"


# ── Distributed breaker state ────────────────────────────────────────────────
# Optional cross-worker breaker state. When no backend is configured the
# store is a no-op and breakers behave as per-process (legacy) state.
# Supabase is the fallback backend (Redis preferred in prod when wired up).
#
# The ``provider_health`` Supabase table (when present) has one row per
# provider with columns: provider TEXT PK, failures INT, opened_at FLOAT,
# last_reason TEXT, updated_at TIMESTAMP.


class _ProviderHealthStore:
    """Distributed breaker-state sink — Supabase-backed with in-memory fallback.

    Graceful-degradation contract:
      * When ``SUPABASE_*`` env is missing, this is a pure no-op.
      * When the Supabase table doesn't exist (first deploy / untouched
        environment), we flip a local flag and stop writing — the process
        reverts to purely local breakers. No exception surfaces to the
        orchestrator.
      * Hydrate + publish are both synchronous / non-blocking — we swallow
        every error so a flaky ``provider_health`` table can't degrade the
        agent pipeline.
    """

    TABLE = "provider_health"

    # Coalesce publishes to at most one write per provider per N seconds so
    # a burst of record_failure() calls doesn't hammer Supabase.
    _PUBLISH_MIN_INTERVAL_S = 5.0
    # Re-read the shared store at most once per N seconds (the in-process
    # state is the source of truth between hydrations).
    _HYDRATE_MIN_INTERVAL_S = 10.0

    def __init__(self) -> None:
        # Guard so Supabase import failures never crash the module.
        self._available = self._detect_availability()
        self._last_publish: dict[str, float] = {}
        self._last_hydrate: dict[str, float] = {}

    @staticmethod
    def _detect_availability() -> bool:
        # Allow ops to force-disable the distributed store via env.
        if os.getenv("PROVIDER_HEALTH_STORE", "auto").lower() in {"off", "disabled"}:
            return False
        if not (os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_SERVICE_ROLE_KEY")):
            return False
        return True

    def _client(self):
        """Return a supabase client or None — never raises."""
        if not self._available:
            return None
        try:
            from ...database import get_supabase_client
            return get_supabase_client()
        except Exception as exc:  # noqa: BLE001
            logger.debug("provider_health store unavailable: %s", exc)
            self._available = False
            return None

    def hydrate(self, breaker: _ProviderBreaker) -> None:
        """Merge shared-store state into the in-process breaker (rate-limited)."""
        now = time.monotonic()
        if now - self._last_hydrate.get(breaker.name, 0.0) < self._HYDRATE_MIN_INTERVAL_S:
            return
        self._last_hydrate[breaker.name] = now

        client = self._client()
        if client is None:
            return
        try:
            res = (
                client.table(self.TABLE)
                .select("failures, opened_at, last_reason")
                .eq("provider", breaker.name)
                .limit(1)
                .execute()
            )
            rows = res.data or []
        except Exception as exc:  # noqa: BLE001
            logger.debug("provider_health hydrate skipped for %s: %s", breaker.name, exc)
            # First failure disables subsequent reads this process-lifetime.
            self._available = False
            return
        if not rows:
            return
        row = rows[0]
        # Adopt remote state when it's MORE conservative than local state —
        # prevents a flappy local breaker from blowing past a globally-open
        # one. We only downgrade (re-open) locally, never upgrade (force
        # close) based on remote data.
        remote_failures = int(row.get("failures") or 0)
        if remote_failures > breaker.failures:
            breaker.failures = remote_failures
            breaker.opened_at = float(row.get("opened_at") or breaker.opened_at)
            breaker.last_reason = (row.get("last_reason") or breaker.last_reason)[:120]

    def publish(self, breaker: _ProviderBreaker) -> None:
        """Push in-process breaker state to the shared store (rate-limited)."""
        now = time.monotonic()
        if now - self._last_publish.get(breaker.name, 0.0) < self._PUBLISH_MIN_INTERVAL_S:
            return
        self._last_publish[breaker.name] = now

        client = self._client()
        if client is None:
            return
        try:
            client.table(self.TABLE).upsert({
                "provider": breaker.name,
                "failures": breaker.failures,
                "opened_at": breaker.opened_at,
                "last_reason": breaker.last_reason,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }, on_conflict="provider").execute()
        except Exception as exc:  # noqa: BLE001
            logger.debug("provider_health publish skipped for %s: %s", breaker.name, exc)
            self._available = False


# Module-level singleton. Replace via ``_set_store_for_testing`` in tests.
_STORE = _ProviderHealthStore()


def _set_store_for_testing(store: _ProviderHealthStore) -> None:
    """Test hook — swap the distributed store (use ``_ProviderHealthStore()`` subclass)."""
    global _STORE
    _STORE = store


# Per-provider breakers — shared across all helpers in this module.
_BREAKERS: dict[str, _ProviderBreaker] = {
    "finnhub": _ProviderBreaker("finnhub"),
    "polygon": _ProviderBreaker("polygon"),
    "coingecko": _ProviderBreaker("coingecko"),
    "yfinance": _ProviderBreaker("yfinance"),
}

# Per-provider concurrency caps. Values tuned to free-tier limits so a
# burst of tickers (e.g. 40+ positions) never exceeds provider quotas.
_SEMAPHORE_LIMITS: dict[str, int] = {
    "finnhub": 3,
    "polygon": 2,
    "coingecko": 2,
    "yfinance": 6,
}
_SEMAPHORES: dict[str, asyncio.Semaphore] = {
    name: asyncio.Semaphore(limit) for name, limit in _SEMAPHORE_LIMITS.items()
}


def _classify_http_error(status_code: int) -> str:
    if status_code == 429:
        return "429 rate_limited"
    if status_code == 403:
        return "403 forbidden"
    if status_code >= 500:
        return f"{status_code} server_error"
    return f"{status_code} error"


# ── Exponential backoff with jitter ────────────────────────────────────────
# Retry schedule: 1s → 2s → 4s (max 8s ceiling as per stability spec).
# Jitter ±30% keeps concurrent callers from synchronising retries and
# re-hitting the same rate-limit window.
_BACKOFF_BASE_DELAYS_S = (1.0, 2.0, 4.0)
_BACKOFF_MAX_DELAY_S = 8.0
_BACKOFF_JITTER_FRACTION = 0.3  # ±30%


def _is_transient_status(status_code: int) -> bool:
    """Should we retry this HTTP status code?

    429 = rate limited (transient) → yes, back off.
    5xx = server error (transient) → yes, back off.
    403 = forbidden / blocked / key revoked → NO, hard fail, don't retry.
    Everything else (4xx client errors) → NO, won't improve with retry.
    """
    return status_code == 429 or 500 <= status_code < 600


def _jittered_delay(base_s: float) -> float:
    """Apply ±30% jitter to ``base_s`` and clamp to the max ceiling."""
    frac = 1.0 + random.uniform(-_BACKOFF_JITTER_FRACTION, _BACKOFF_JITTER_FRACTION)
    return min(base_s * frac, _BACKOFF_MAX_DELAY_S)


async def _retry_http(
    provider: str,
    ticker: str,
    perform: Callable[[], Awaitable["httpx.Response"]],
) -> Optional["httpx.Response"]:
    """Execute an HTTP call with exponential-backoff retries for transient errors.

    Returns the final ``httpx.Response`` (success or non-retryable error) or
    ``None`` when an exception escaped the last attempt (already logged +
    breaker-recorded by the caller).

    Retry policy:
      * 429 / 5xx → retry up to 3 times with 1s → 2s → 4s base delays + jitter.
      * 403 → NEVER retry (hard block, usually credentials / plan issue).
      * Network exceptions → retry (treated as transient by default).
    """
    last_resp: Optional[httpx.Response] = None
    for attempt, base in enumerate((0.0, *_BACKOFF_BASE_DELAYS_S)):
        if base > 0:
            delay = _jittered_delay(base)
            logger.debug(
                "%s retry %s attempt=%d delay=%.2fs",
                provider, ticker, attempt, delay,
            )
            await asyncio.sleep(delay)
        try:
            resp = await perform()
        except Exception as exc:  # noqa: BLE001 — network error is transient
            logger.debug(
                "%s network error on %s attempt=%d: %s",
                provider, ticker, attempt, exc,
            )
            if attempt == len(_BACKOFF_BASE_DELAYS_S):
                raise
            continue

        last_resp = resp
        status = resp.status_code
        if status == 200:
            return resp
        if not _is_transient_status(status):
            # Hard fail (e.g. 403) — don't retry.
            return resp
        if attempt == len(_BACKOFF_BASE_DELAYS_S):
            # Exhausted retries.
            return resp
    return last_resp


def get_provider_status() -> dict[str, str]:
    """Return the current provider health — ok / rate_limited / blocked / failed.

    Callers (io_layer, resilient_provider) embed this in the bundle so the
    orchestrator and UI can reason about degraded sources without having to
    touch the upstream itself.
    """
    return {name: breaker.status() for name, breaker in _BREAKERS.items()}


def reset_breakers() -> None:
    """Test hook — clear every breaker back to healthy state."""
    for breaker in _BREAKERS.values():
        breaker.record_success()


async def _get_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(_HTTP_TIMEOUT),
        limits=httpx.Limits(max_connections=30),
        headers={"User-Agent": "PortfolioIntelligence/2.0 (multi-agent)"},
    )


# ── News (Sentiment agent) ───────────────────────────────────────────────────

async def fetch_finnhub_news(
    client: httpx.AsyncClient,
    ticker: str,
    api_key: str,
    lookback_days: int = 7,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Return a list of {headline, summary, datetime} dicts.

    Gated by the shared Finnhub circuit breaker + semaphore — when the breaker
    is open (e.g. sustained 429s from the free tier) this is a no-op that
    returns an empty list instead of firing another request.

    Retries 429/5xx with exponential backoff + jitter; 403 fails fast.
    """
    if not api_key:
        return []
    breaker = _BREAKERS["finnhub"]
    if breaker.is_open():
        logger.debug("Finnhub breaker open — skipping news fetch for %s", ticker)
        return []
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=lookback_days)
    params = {
        "symbol": ticker,
        "from": start.isoformat(),
        "to": today.isoformat(),
        "token": api_key,
    }

    async def _perform():
        async with _SEMAPHORES["finnhub"]:
            return await client.get(f"{_FINNHUB_BASE}/company-news", params=params)

    try:
        resp = await _retry_http("finnhub", ticker, _perform)
        if resp is None or resp.status_code != 200:
            status = resp.status_code if resp is not None else 0
            breaker.record_failure(_classify_http_error(status))
            logger.debug("Finnhub news %s → %s", ticker, status)
            return []
        breaker.record_success()
        data = resp.json() or []
        return [
            {
                "headline": item.get("headline", ""),
                "summary": item.get("summary", "")[:300],
                "datetime": item.get("datetime", 0),
                "source": item.get("source", ""),
            }
            for item in data[:limit]
            if item.get("headline")
        ]
    except Exception as exc:
        breaker.record_failure(str(exc))
        logger.debug("Finnhub news failed for %s: %s", ticker, exc)
        return []


def fetch_yfinance_news_sync(ticker: str, limit: int = 6) -> list[dict[str, Any]]:
    """Synchronous yfinance news fetch — run in an executor."""
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        items = t.news or []
        out: list[dict[str, Any]] = []
        for it in items[:limit]:
            title = it.get("title") or it.get("content", {}).get("title", "")
            if not title:
                continue
            out.append({
                "headline": title,
                "summary": (it.get("summary") or "")[:300],
                "datetime": it.get("providerPublishTime", 0),
                "source": it.get("publisher") or "yfinance",
            })
        return out
    except Exception as exc:
        logger.debug("yfinance news failed for %s: %s", ticker, exc)
        return []


async def fetch_yfinance_news(ticker: str, limit: int = 6) -> list[dict[str, Any]]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, fetch_yfinance_news_sync, ticker, limit)


async def fetch_news_for_ticker(
    client: httpx.AsyncClient,
    ticker: str,
    finnhub_key: str,
) -> list[dict[str, Any]]:
    """Union of Finnhub + yfinance news, de-duped on headline."""
    fh, yf = await asyncio.gather(
        fetch_finnhub_news(client, ticker, finnhub_key),
        fetch_yfinance_news(ticker),
        return_exceptions=True,
    )
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for batch in (fh, yf):
        if isinstance(batch, Exception) or not batch:
            continue
        for item in batch:
            key = item["headline"].strip().lower()
            if key and key not in seen:
                seen.add(key)
                merged.append(item)
    # Newest first
    merged.sort(key=lambda x: x.get("datetime", 0), reverse=True)
    return merged[:10]


# ── Price action (Technical agent) ───────────────────────────────────────────

def fetch_yfinance_history_sync(ticker: str, period: str = "3mo") -> dict[str, Any]:
    """Return {closes, volumes, high, low, last, pct_5d, pct_30d, vol_avg}."""
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        hist = t.history(period=period, auto_adjust=True)
        if hist is None or hist.empty:
            return {}
        closes = [float(x) for x in hist["Close"].tolist() if x == x]
        volumes = [float(x) for x in hist["Volume"].tolist() if x == x]
        if not closes:
            return {}
        last = closes[-1]
        first = closes[0]
        pct_full = (last / first - 1) * 100 if first else 0.0
        pct_5d = ((last / closes[-6] - 1) * 100) if len(closes) >= 6 else 0.0
        pct_30d = ((last / closes[-22] - 1) * 100) if len(closes) >= 22 else pct_full
        # Simple trend: 20-day SMA slope
        sma20 = sum(closes[-20:]) / min(len(closes), 20)
        sma50 = sum(closes[-50:]) / min(len(closes), 50)
        vol_avg = sum(volumes[-20:]) / max(1, min(len(volumes), 20))
        vol_last = volumes[-1] if volumes else 0
        return {
            "last": last,
            "high_3mo": max(closes),
            "low_3mo": min(closes),
            "pct_5d": round(pct_5d, 2),
            "pct_30d": round(pct_30d, 2),
            "pct_3mo": round(pct_full, 2),
            "sma20": round(sma20, 2),
            "sma50": round(sma50, 2),
            "vol_last": vol_last,
            "vol_avg_20d": vol_avg,
            "n_bars": len(closes),
        }
    except Exception as exc:
        logger.debug("yfinance history failed for %s: %s", ticker, exc)
        return {}


async def fetch_price_action(ticker: str) -> dict[str, Any]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, fetch_yfinance_history_sync, ticker)


async def fetch_polygon_aggs(
    client: httpx.AsyncClient,
    ticker: str,
    api_key: str,
    days: int = 30,
) -> dict[str, Any]:
    """Polygon daily aggregates — used as a secondary source.

    Treated as OPTIONAL enrichment: when the breaker is open (403 Forbidden
    is the dominant failure mode on free/paused plans) this returns an
    empty dict so the orchestrator's IO bundle still completes.
    """
    if not api_key:
        return {}
    breaker = _BREAKERS["polygon"]
    if breaker.is_open():
        logger.debug("Polygon breaker open — skipping aggs for %s", ticker)
        return {}
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days)
    url = (
        f"{_POLYGON_BASE}/v2/aggs/ticker/{ticker}/range/1/day/"
        f"{start.isoformat()}/{end.isoformat()}"
    )
    async def _perform():
        async with _SEMAPHORES["polygon"]:
            return await client.get(
                url, params={"apiKey": api_key, "adjusted": "true"}
            )

    try:
        resp = await _retry_http("polygon", ticker, _perform)
        if resp is None or resp.status_code != 200:
            status = resp.status_code if resp is not None else 0
            breaker.record_failure(_classify_http_error(status))
            return {}
        breaker.record_success()
        data = resp.json()
        results = data.get("results") or []
        if not results:
            return {}
        closes = [r["c"] for r in results]
        last = closes[-1]
        first = closes[0]
        return {
            "polygon_pct": round((last / first - 1) * 100 if first else 0, 2),
            "polygon_last": last,
            "polygon_bars": len(results),
        }
    except Exception as exc:
        breaker.record_failure(str(exc))
        logger.debug("Polygon aggs failed for %s: %s", ticker, exc)
        return {}


# ── Fundamentals (Fundamental agent) ─────────────────────────────────────────

def fetch_yfinance_fundamentals_sync(ticker: str) -> dict[str, Any]:
    """Return a compact fundamentals dict from yfinance."""
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        info: dict[str, Any] = {}
        try:
            info = t.info or {}
        except Exception:
            info = {}
        return {
            "pe": _safe_float(info.get("trailingPE")),
            "forward_pe": _safe_float(info.get("forwardPE")),
            "peg": _safe_float(info.get("pegRatio")),
            "eps": _safe_float(info.get("trailingEps")),
            "profit_margin": _safe_float(info.get("profitMargins")),
            "revenue_growth": _safe_float(info.get("revenueGrowth")),
            "earnings_growth": _safe_float(info.get("earningsGrowth")),
            "debt_to_equity": _safe_float(info.get("debtToEquity")),
            "return_on_equity": _safe_float(info.get("returnOnEquity")),
            "beta": _safe_float(info.get("beta")),
            "market_cap": _safe_float(info.get("marketCap")),
            "sector": info.get("sector") or "",
            "industry": info.get("industry") or "",
            "dividend_yield": _safe_float(info.get("dividendYield")),
            "recommendation_mean": _safe_float(info.get("recommendationMean")),
            "target_mean_price": _safe_float(info.get("targetMeanPrice")),
        }
    except Exception as exc:
        logger.debug("yfinance fundamentals failed for %s: %s", ticker, exc)
        return {}


async def fetch_fundamentals(ticker: str) -> dict[str, Any]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, fetch_yfinance_fundamentals_sync, ticker)


# ── Crypto (CoinGecko for fundamentals/sentiment) ────────────────────────────

_COINGECKO_IDS: dict[str, str] = {
    "BTC": "bitcoin", "ETH": "ethereum", "XRP": "ripple",
    "SOL": "solana", "DOGE": "dogecoin", "ADA": "cardano",
    "AVAX": "avalanche-2", "MATIC": "matic-network",
    "DOT": "polkadot", "LTC": "litecoin",
}


async def fetch_coingecko_market(
    client: httpx.AsyncClient,
    ticker: str,
) -> dict[str, Any]:
    """Rich market data for crypto — fills in for fundamentals + sentiment.

    Gated by the shared CoinGecko breaker + semaphore. Free-tier rate limits
    are the primary failure mode (HTTP 429 on BTC/XRP lookups); an open
    breaker means the next call short-circuits to {} instead of contending
    for the rate-limit window.
    """
    coin_id = _COINGECKO_IDS.get(ticker.upper())
    if not coin_id:
        return {}
    breaker = _BREAKERS["coingecko"]
    if breaker.is_open():
        logger.debug("CoinGecko breaker open — skipping %s", ticker)
        return {}
    async def _perform():
        async with _SEMAPHORES["coingecko"]:
            return await client.get(
                f"{_COINGECKO_BASE}/coins/{coin_id}",
                params={
                    "localization": "false",
                    "tickers": "false",
                    "market_data": "true",
                    "community_data": "true",
                    "developer_data": "false",
                    "sparkline": "false",
                },
            )

    try:
        resp = await _retry_http("coingecko", ticker, _perform)
        if resp is None or resp.status_code != 200:
            status = resp.status_code if resp is not None else 0
            breaker.record_failure(_classify_http_error(status))
            return {}
        breaker.record_success()
        data = resp.json()
        md = data.get("market_data", {}) or {}
        return {
            "price_usd": _safe_float((md.get("current_price") or {}).get("usd")),
            "ath_pct": _safe_float((md.get("ath_change_percentage") or {}).get("usd")),
            "pct_24h": _safe_float(md.get("price_change_percentage_24h")),
            "pct_7d": _safe_float(md.get("price_change_percentage_7d")),
            "pct_30d": _safe_float(md.get("price_change_percentage_30d")),
            "market_cap_rank": md.get("market_cap_rank"),
            "sentiment_up_pct": _safe_float(data.get("sentiment_votes_up_percentage")),
            "sentiment_down_pct": _safe_float(data.get("sentiment_votes_down_percentage")),
        }
    except Exception as exc:
        breaker.record_failure(str(exc))
        logger.debug("CoinGecko fetch failed for %s: %s", ticker, exc)
        return {}


# ── utils ────────────────────────────────────────────────────────────────────

def _safe_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        f = float(v)
        if f != f:  # NaN
            return None
        return f
    except (TypeError, ValueError):
        return None


def is_crypto(ticker: str) -> bool:
    return ticker.upper() in _COINGECKO_IDS
