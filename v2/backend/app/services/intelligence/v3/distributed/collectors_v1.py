"""Distributed Run Intel — pure data collectors.

A collector receives ONE explicit task, reads only that task's ticker (or the
portfolio scope for session-level tasks), checks reusable evidence + TTL,
fetches missing/stale data through the EXISTING provider machinery, normalizes
it, and persists its own output. Collectors never call an LLM, never produce
actions, and never fetch unrelated tickers.

Provider reuse (no second provider framework):
  * yfinance / CoinGecko fetchers + circuit breakers + per-provider semaphores:
    ``app.services.agents.data_sources``
  * SEC / ETF / macro lanes: existing flag-gated research-worker lane runners
    (they write ``research_artifacts`` rows themselves and return artifact ids)

TTL reuse: the most recent succeeded task output for the same
(user, ticker, lane) younger than the lane TTL is copied instead of re-fetched
(cache hit); the SEC/ETF/macro runners additionally reuse active
``research_artifacts`` via their own idempotency keys.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from ....agents.data_sources import (
    _get_client as _get_http_client,
    fetch_coingecko_market,
    fetch_fundamentals,
    fetch_price_action,
    fetch_yfinance_news,
)
from .evidence_normalization_v1 import (
    NEWS_NORMALIZATION_VERSION,
    NORMALIZATION_VERSION,
    filter_news_items,
    normalize_fundamentals,
)
from .task_contracts_v1 import (
    ASSET_CRYPTO,
    LANE_CRYPTO_MARKET,
    LANE_ETF_FUND_DATA,
    LANE_FUNDAMENTALS,
    LANE_MACRO,
    LANE_NEWS_SENTIMENT,
    LANE_PRICE,
    LANE_SEC_CATALYST,
    LANE_SEC_COMPANY_FACTS,
    LANE_TECHNICALS,
    LANE_TTL_HOURS,
    TASK_COLLECT_MACRO_CONTEXT,
    TASK_DEGRADED,
    TASK_SUCCEEDED,
)
from .run_task_store_v1 import TASKS_TABLE, TASK_FAILED_RETRYABLE

logger = logging.getLogger(__name__)


class CollectorResult:
    """Outcome of one collector task execution."""

    def __init__(
        self,
        final_state: str,
        *,
        output: Optional[dict[str, Any]] = None,
        output_ref: Optional[str] = None,
        error_code: Optional[str] = None,
        error_detail: Optional[str] = None,
        cache_hit: bool = False,
        provider_calls: int = 0,
    ):
        self.final_state = final_state
        self.output = output
        self.output_ref = output_ref
        self.error_code = error_code
        self.error_detail = error_detail
        self.cache_hit = cache_hit
        self.provider_calls = provider_calls


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _rows(res: Any) -> list[dict[str, Any]]:
    data = getattr(res, "data", None)
    return data if isinstance(data, list) else []


# ── TTL reuse ────────────────────────────────────────────────────────────────

class CacheReadError(Exception):
    """The cache lookup query itself failed — distinguishable from a
    legitimate miss. Callers must fail closed (TASK_FAILED_RETRYABLE, zero
    provider calls), never reinterpret an outage as expired evidence."""


def _reuse_contract_compatible(lane: str, output: dict[str, Any]) -> bool:
    """A pre-normalization fundamentals/news output is never a compatible
    cache entry once the normalization contract version bumps."""
    if lane == LANE_FUNDAMENTALS:
        return (output.get("normalized") or {}).get("schema_version") == NORMALIZATION_VERSION
    if lane == LANE_NEWS_SENTIMENT:
        return output.get("schema_version") == NEWS_NORMALIZATION_VERSION
    return True


def _usable_recent_output(row: dict[str, Any], lane: Optional[str], now: datetime) -> Optional[dict[str, Any]]:
    completed_at = row.get("completed_at")
    if not isinstance(completed_at, str):
        return None  # missing completed_at — age cannot be established
    try:
        if datetime.fromisoformat(completed_at.replace("Z", "+00:00")) > now:
            return None  # future-dated — age cannot be trusted
    except ValueError:
        return None  # unparsable
    output = row.get("output")
    if not isinstance(output, dict):
        return None  # structurally unusable
    if lane is not None and not _reuse_contract_compatible(lane, output):
        return None
    return output


def find_recent_lane_output(
    client: Any,
    *,
    user_id: str,
    ticker: str,
    lane: str,
    asset_type: str,
    ttl_hours: float,
    now: Optional[datetime] = None,
) -> Optional[dict[str, Any]]:
    """Most recent succeeded output for (user, ticker, asset_type, lane)
    within TTL — across sessions. None = legitimate miss/ineligible; raises
    ``CacheReadError`` on an actual query failure (never silently a miss)."""
    if ttl_hours <= 0:
        return None
    now = now or _now()
    cutoff = (now - timedelta(hours=ttl_hours)).isoformat()
    try:
        res = (
            client.table(TASKS_TABLE)
            .select("output,completed_at,state")
            .eq("user_id", user_id)
            .eq("ticker", ticker)
            .eq("asset_type", asset_type)
            .eq("lane", lane)
            .eq("state", TASK_SUCCEEDED)
            .gte("completed_at", cutoff)
            .order("completed_at", desc=True)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        raise CacheReadError(f"lane:{lane}") from exc
    rows = _rows(res)
    return _usable_recent_output(rows[0], lane, now) if rows else None


def find_recent_macro_output(
    client: Any, *, user_id: str, ttl_hours: float, now: Optional[datetime] = None,
) -> Optional[dict[str, Any]]:
    """24h macro reuse via the SAME durable task table — user + session/
    portfolio-scoped identity (task_type, no fabricated ticker), never a
    second cache/table. Same fail-closed contract as lane reuse."""
    if ttl_hours <= 0:
        return None
    now = now or _now()
    cutoff = (now - timedelta(hours=ttl_hours)).isoformat()
    try:
        res = (
            client.table(TASKS_TABLE)
            .select("output,completed_at,state")
            .eq("user_id", user_id)
            .eq("task_type", TASK_COLLECT_MACRO_CONTEXT)
            .eq("state", TASK_SUCCEEDED)
            .gte("completed_at", cutoff)
            .order("completed_at", desc=True)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        raise CacheReadError("macro") from exc
    rows = _rows(res)
    if not rows:
        return None
    output = _usable_recent_output(rows[0], None, now)
    return output if output and output.get("artifact_id") else None


# ── Lane collectors ──────────────────────────────────────────────────────────

async def _collect_price(ticker: str, asset_type: str) -> CollectorResult:
    if asset_type == ASSET_CRYPTO:
        http = await _get_http_client()
        market = await fetch_coingecko_market(http, ticker)
        if not market or market.get("price_usd") is None:
            return CollectorResult(
                TASK_FAILED_RETRYABLE,
                error_code="crypto_price_unavailable",
                provider_calls=1,
            )
        return CollectorResult(
            TASK_SUCCEEDED,
            output={
                "price": market.get("price_usd"),
                "pct_1d": market.get("pct_24h"),
                "source": "coingecko",
                "as_of": _now().isoformat(),
            },
            provider_calls=1,
        )
    history = await fetch_price_action(ticker)
    if not history or history.get("last") is None:
        return CollectorResult(
            TASK_FAILED_RETRYABLE,
            error_code="price_unavailable",
            provider_calls=1,
        )
    return CollectorResult(
        TASK_SUCCEEDED,
        output={
            "price": history.get("last"),
            "pct_1d": history.get("pct_1d"),
            "source": "yfinance",
            "as_of": _now().isoformat(),
        },
        provider_calls=1,
    )


async def _collect_technicals(ticker: str) -> CollectorResult:
    history = await fetch_price_action(ticker)
    if not history or history.get("last") is None:
        return CollectorResult(
            TASK_FAILED_RETRYABLE,
            error_code="history_unavailable",
            provider_calls=1,
        )
    return CollectorResult(
        TASK_SUCCEEDED,
        output={**history, "source": "yfinance", "as_of": _now().isoformat()},
        provider_calls=1,
    )


async def _collect_fundamentals(ticker: str) -> CollectorResult:
    fundamentals = await fetch_fundamentals(ticker)
    usable = {k: v for k, v in (fundamentals or {}).items() if v not in (None, "")}
    if not usable:
        return CollectorResult(
            TASK_FAILED_RETRYABLE,
            error_code="fundamentals_unavailable",
            provider_calls=1,
        )
    return CollectorResult(
        TASK_SUCCEEDED,
        output={
            **fundamentals,
            "normalized": normalize_fundamentals(fundamentals),
            "source": "yfinance", "as_of": _now().isoformat(),
        },
        provider_calls=1,
    )


async def _collect_news(ticker: str) -> CollectorResult:
    news = await fetch_yfinance_news(ticker)
    filtered = filter_news_items(news or [], ticker)
    if not filtered["items"]:
        # Honest degradation — no accepted news is not a failure worth
        # retrying forever, and never becomes fabricated neutral sentiment.
        return CollectorResult(
            TASK_DEGRADED,
            output={**filtered, "as_of": _now().isoformat()},
            error_code="no_news_items",
            provider_calls=1,
        )
    return CollectorResult(
        TASK_SUCCEEDED,
        output={**filtered, "source": "yfinance", "as_of": _now().isoformat()},
        provider_calls=1,
    )


async def _collect_crypto_market(ticker: str) -> CollectorResult:
    http = await _get_http_client()
    market = await fetch_coingecko_market(http, ticker)
    if not market:
        return CollectorResult(
            TASK_FAILED_RETRYABLE,
            error_code="coingecko_unavailable",
            provider_calls=1,
        )
    return CollectorResult(
        TASK_SUCCEEDED,
        output={**market, "source": "coingecko", "as_of": _now().isoformat()},
        provider_calls=1,
    )


def _run_artifact_lane_sync(
    lane: str,
    *,
    user_id: str,
    ticker: str,
    client: Any,
    session_id: str,
    holding_context: dict[str, Any],
    settings: Any,
) -> Optional[str]:
    """Dispatch to the existing flag-gated research-worker lane runners."""
    from ...research_workers.evidence_lane_runner_v1 import (
        run_etf_nport_holdings_evidence,
        run_sec_catalyst_sentiment_evidence,
        run_sec_companyfacts_evidence,
    )

    if lane == LANE_SEC_COMPANY_FACTS:
        return run_sec_companyfacts_evidence(
            user_id, ticker, client, session_id, holding_context, settings,
        )
    if lane == LANE_SEC_CATALYST:
        return run_sec_catalyst_sentiment_evidence(
            user_id, ticker, client, session_id, holding_context, settings,
        )
    if lane == LANE_ETF_FUND_DATA:
        return run_etf_nport_holdings_evidence(
            user_id, ticker, client, session_id, holding_context, settings,
        )
    return None


async def _collect_artifact_lane(
    lane: str,
    *,
    client: Any,
    user_id: str,
    ticker: str,
    session_id: str,
    asset_type: str,
    settings: Any,
) -> CollectorResult:
    holding_context = {"category": "ETF" if asset_type == "etf" else asset_type}
    try:
        artifact_id = await asyncio.to_thread(
            _run_artifact_lane_sync,
            lane,
            user_id=user_id,
            ticker=ticker,
            client=client,
            session_id=session_id,
            holding_context=holding_context,
            settings=settings,
        )
    except Exception as exc:
        return CollectorResult(
            TASK_FAILED_RETRYABLE,
            error_code=f"{lane}_error",
            error_detail=str(exc),
            provider_calls=1,
        )
    if artifact_id:
        return CollectorResult(
            TASK_SUCCEEDED,
            output={"artifact_id": artifact_id, "as_of": _now().isoformat()},
            output_ref=str(artifact_id),
            provider_calls=1,
        )
    # Disabled lane / ineligible ticker / nothing fresh — honest degradation,
    # never a retry storm and never a run blocker (optional lanes only).
    return CollectorResult(
        TASK_DEGRADED,
        output={"artifact_id": None, "as_of": _now().isoformat()},
        error_code=f"{lane}_no_artifact",
    )


async def _collect_macro_with_reuse(
    *, client: Any, user_id: str, session_id: str, settings: Any
) -> CollectorResult:
    """24h macro reuse before any provider call — same fail-closed contract
    as lane reuse (a cache read failure never falls through to a fetch)."""
    ttl_hours = LANE_TTL_HOURS.get(LANE_MACRO, 24.0)
    try:
        cached = await asyncio.to_thread(
            lambda: find_recent_macro_output(client, user_id=user_id, ttl_hours=ttl_hours)
        )
    except CacheReadError:
        return CollectorResult(TASK_FAILED_RETRYABLE, error_code="macro_cache_read_failed")
    if cached is not None:
        return CollectorResult(TASK_SUCCEEDED, output={**cached, "cache_hit": True}, cache_hit=True)
    return await _collect_macro(
        client=client, user_id=user_id, session_id=session_id, settings=settings,
    )


async def _collect_macro(
    *, client: Any, user_id: str, session_id: str, settings: Any
) -> CollectorResult:
    from ...research_workers.evidence_lane_runner_v1 import run_fred_macro_evidence

    try:
        artifact_id = await asyncio.to_thread(
            run_fred_macro_evidence, user_id, client, session_id, settings,
        )
    except Exception as exc:
        return CollectorResult(
            TASK_DEGRADED,
            error_code="macro_error",
            error_detail=str(exc),
        )
    if artifact_id:
        return CollectorResult(
            TASK_SUCCEEDED,
            output={"artifact_id": artifact_id, "as_of": _now().isoformat()},
            output_ref=str(artifact_id),
            provider_calls=1,
        )
    return CollectorResult(
        TASK_DEGRADED,
        output={"artifact_id": None, "as_of": _now().isoformat()},
        error_code="macro_no_artifact",
    )


async def _collect_portfolio_context(
    *, client: Any, user_id: str, session_id: str
) -> CollectorResult:
    """Session-level frozen portfolio context — DB reads only."""
    def _read() -> dict[str, Any]:
        from .run_task_store_v1 import list_ticker_rows

        rows = list_ticker_rows(client, run_session_id=session_id)
        total_value = sum(
            float(r.get("market_value") or 0.0) for r in rows
        )
        by_asset: dict[str, float] = {}
        top: list[dict[str, Any]] = []
        for r in rows:
            asset = str(r.get("asset_type") or "equity")
            by_asset[asset] = by_asset.get(asset, 0.0) + float(
                r.get("market_value") or 0.0
            )
            weight = r.get("portfolio_weight_pct")
            top.append({"ticker": r.get("ticker"), "weight_pct": weight})
        top.sort(key=lambda x: float(x.get("weight_pct") or 0.0), reverse=True)
        cash_available = None
        try:
            res = (
                client.table("portfolios")
                .select("cash_balance")
                .eq("user_id", user_id)
                .limit(1)
                .execute()
            )
            rows_p = _rows(res)
            if rows_p:
                cash_available = rows_p[0].get("cash_balance")
        except Exception:
            cash_available = None
        return {
            "holding_count": len(rows),
            "total_market_value": total_value or None,
            "asset_allocation": by_asset,
            "top_holdings": top[:5],
            "cash_available": cash_available,
            "as_of": _now().isoformat(),
        }

    try:
        context = await asyncio.to_thread(_read)
        return CollectorResult(TASK_SUCCEEDED, output=context)
    except Exception as exc:
        return CollectorResult(
            TASK_FAILED_RETRYABLE,
            error_code="portfolio_context_error",
            error_detail=str(exc),
        )


# ── Dispatch ─────────────────────────────────────────────────────────────────

async def execute_collector_task(
    client: Any,
    *,
    task: dict[str, Any],
    settings: Any,
) -> CollectorResult:
    """Execute one collector task (lane / macro / portfolio context).

    Scope discipline: everything below derives from the task row itself — the
    task's ticker is the only ticker whose data is ever requested.
    """
    task_type = str(task.get("task_type") or "")
    user_id = str(task.get("user_id") or "")
    session_id = str(task.get("run_session_id") or "")
    ticker = str(task.get("ticker") or "")
    lane = str(task.get("lane") or "")
    asset_type = str(task.get("asset_type") or "equity")

    if task_type == "collect_portfolio_context":
        return await _collect_portfolio_context(
            client=client, user_id=user_id, session_id=session_id
        )
    if task_type == "collect_macro_context":
        return await _collect_macro_with_reuse(
            client=client, user_id=user_id, session_id=session_id, settings=settings,
        )

    # TTL reuse before any provider call — a cache READ failure is honestly
    # retryable, never reinterpreted as "no cache, go fetch".
    ttl = LANE_TTL_HOURS.get(lane, 0.0)
    try:
        cached = await asyncio.to_thread(
            lambda: find_recent_lane_output(
                client, user_id=user_id, ticker=ticker, lane=lane,
                asset_type=asset_type, ttl_hours=ttl,
            )
        )
    except CacheReadError:
        return CollectorResult(TASK_FAILED_RETRYABLE, error_code="cache_read_failed")
    if cached is not None:
        return CollectorResult(
            TASK_SUCCEEDED,
            output={**cached, "cache_hit": True},
            cache_hit=True,
        )

    if lane == LANE_PRICE:
        return await _collect_price(ticker, asset_type)
    if lane == LANE_TECHNICALS:
        return await _collect_technicals(ticker)
    if lane == LANE_FUNDAMENTALS:
        return await _collect_fundamentals(ticker)
    if lane == LANE_NEWS_SENTIMENT:
        return await _collect_news(ticker)
    if lane == LANE_CRYPTO_MARKET:
        return await _collect_crypto_market(ticker)
    if lane in (LANE_SEC_COMPANY_FACTS, LANE_SEC_CATALYST, LANE_ETF_FUND_DATA):
        return await _collect_artifact_lane(
            lane,
            client=client,
            user_id=user_id,
            ticker=ticker,
            session_id=session_id,
            asset_type=asset_type,
            settings=settings,
        )
    if lane == LANE_MACRO:
        return await _collect_macro_with_reuse(
            client=client, user_id=user_id, session_id=session_id, settings=settings,
        )
    return CollectorResult(
        "failed", error_code="unknown_lane", error_detail=lane,
    )
