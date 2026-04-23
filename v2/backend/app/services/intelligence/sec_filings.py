"""Lightweight SEC filing intelligence (10-K/10-Q/8-K) with TTL caching."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

_TICKER_MAP_TTL_S = 24 * 60 * 60
_FILING_TTL_S = 30 * 60
_ticker_map_cache: tuple[float, dict[str, str]] | None = None
_filing_cache: dict[str, tuple[float, "SecFilingSignals"]] = {}
_inflight: dict[str, asyncio.Future] = {}
_lock = asyncio.Lock()


@dataclass
class SecFilingSignals:
    ticker: str
    available: bool
    filing_summary: str
    sentiment_label: str
    revenue_trend: str
    earnings_trend: str
    operating_cash_flow_trend: str
    leverage_liquidity_risk: str
    guidance_or_risk_change: str


def _trend(curr: Optional[float], prev: Optional[float]) -> str:
    if curr is None or prev is None:
        return "unknown"
    if prev == 0:
        return "improving" if curr > 0 else "deteriorating"
    delta = (curr - prev) / abs(prev)
    if delta >= 0.05:
        return "improving"
    if delta <= -0.05:
        return "deteriorating"
    return "stable"


def _pick_latest_two(facts: dict[str, Any], concept: str) -> tuple[Optional[float], Optional[float]]:
    node = (((facts.get("facts") or {}).get("us-gaap") or {}).get(concept) or {}).get("units") or {}
    rows = node.get("USD") or []
    parsed: list[tuple[str, float]] = []
    for r in rows:
        val = r.get("val")
        end = r.get("end")
        form = (r.get("form") or "").upper()
        if end and isinstance(val, (int, float)) and form in {"10-K", "10-Q"}:
            parsed.append((end, float(val)))
    parsed.sort(key=lambda x: x[0], reverse=True)
    if len(parsed) < 2:
        return None, None
    return parsed[0][1], parsed[1][1]


async def _get_ticker_map(client: httpx.AsyncClient) -> dict[str, str]:
    global _ticker_map_cache
    now = time.time()
    if _ticker_map_cache and now - _ticker_map_cache[0] <= _TICKER_MAP_TTL_S:
        return _ticker_map_cache[1]
    resp = await client.get("https://www.sec.gov/files/company_tickers.json")
    resp.raise_for_status()
    raw = resp.json() or {}
    out: dict[str, str] = {}
    for row in raw.values():
        ticker = (row.get("ticker") or "").upper()
        cik = str(row.get("cik_str") or "").strip()
        if ticker and cik:
            out[ticker] = cik.zfill(10)
    _ticker_map_cache = (now, out)
    return out


async def get_sec_filing_signals(ticker: str) -> SecFilingSignals:
    t = ticker.upper()
    cached = _filing_cache.get(t)
    if cached and time.time() - cached[0] <= _FILING_TTL_S:
        return cached[1]

    async with _lock:
        existing = _inflight.get(t)
        if existing and not existing.done():
            return await existing
        fut = asyncio.get_event_loop().create_future()
        _inflight[t] = fut

    headers = {"User-Agent": "financetracker/2.0 support@example.com"}
    try:
        async with httpx.AsyncClient(timeout=8.0, headers=headers) as client:
            ticker_map = await _get_ticker_map(client)
            cik = ticker_map.get(t)
            if not cik:
                out = SecFilingSignals(t, False, "", "Unavailable", "unknown", "unknown", "unknown", "unknown", "unknown")
            else:
                sub = await client.get(f"https://data.sec.gov/submissions/CIK{cik}.json")
                sub.raise_for_status()
                facts = await client.get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json")
                facts.raise_for_status()

                filings = ((sub.json() or {}).get("filings") or {}).get("recent") or {}
                forms = filings.get("form") or []
                recent_forms = {str(f).upper() for f in forms[:20]}

                rev_now, rev_prev = _pick_latest_two(facts.json() or {}, "Revenues")
                earn_now, earn_prev = _pick_latest_two(facts.json() or {}, "NetIncomeLoss")
                cf_now, cf_prev = _pick_latest_two(facts.json() or {}, "NetCashProvidedByUsedInOperatingActivities")

                rev_trend = _trend(rev_now, rev_prev)
                earn_trend = _trend(earn_now, earn_prev)
                cf_trend = _trend(cf_now, cf_prev)
                risk = "elevated" if "8-K" in recent_forms else "normal"
                guidance = "material 8-K filed recently" if "8-K" in recent_forms else "no material update detected"

                trend_votes = [rev_trend, earn_trend, cf_trend]
                pos = sum(1 for x in trend_votes if x == "improving")
                neg = sum(1 for x in trend_votes if x == "deteriorating")
                if pos > neg:
                    sentiment = "Positive"
                elif neg > pos:
                    sentiment = "Negative"
                elif pos == 0 and neg == 0:
                    sentiment = "Unavailable"
                else:
                    sentiment = "Mixed"

                summary = (
                    f"SEC filings suggest revenue={rev_trend}, earnings={earn_trend}, "
                    f"operating cash flow={cf_trend}; liquidity/leverage risk {risk}; {guidance}."
                )
                out = SecFilingSignals(
                    ticker=t,
                    available=True,
                    filing_summary=summary,
                    sentiment_label=sentiment,
                    revenue_trend=rev_trend,
                    earnings_trend=earn_trend,
                    operating_cash_flow_trend=cf_trend,
                    leverage_liquidity_risk=risk,
                    guidance_or_risk_change=guidance,
                )

        _filing_cache[t] = (time.time(), out)
        if not fut.done():
            fut.set_result(out)
        return out
    except Exception as exc:  # noqa: BLE001
        logger.debug("sec filing fetch failed ticker=%s: %s", t, exc)
        out = SecFilingSignals(t, False, "", "Unavailable", "unknown", "unknown", "unknown", "unknown", "unknown")
        if not fut.done():
            fut.set_result(out)
        return out
    finally:
        async with _lock:
            cur = _inflight.get(t)
            if cur is fut:
                _inflight.pop(t, None)
