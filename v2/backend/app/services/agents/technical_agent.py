"""Technical analyst — price-action / momentum / moving-average read."""

from __future__ import annotations

import logging

from .data_sources import fetch_polygon_aggs, fetch_price_action
from .llm import LLMClient, clamp
from .state import TickerInsight

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = (
    "You are the Technical analyst on a quantitative trading desk. "
    "Given price-action summary statistics, you produce a single technical "
    "signal (BUY/HOLD/SELL/NEUTRAL) and a short justification. "
    "You are terse and concrete. Output valid JSON only."
)


async def run_technical_agent(
    insight: TickerInsight,
    llm: LLMClient,
    http_client,
    polygon_key: str,
) -> None:
    ticker = insight.ticker

    # 1. Fetch price action (yfinance primary, Polygon secondary)
    yf_stats = await fetch_price_action(ticker)
    if polygon_key:
        poly_stats = await fetch_polygon_aggs(http_client, ticker, polygon_key)
        yf_stats.update(poly_stats)

    insight.tech_metrics = yf_stats

    if not yf_stats:
        insight.technical_signal = "NEUTRAL"
        insight.technical_summary = "No price history available."
        return

    # 2. Deterministic pre-score (so the LLM doesn't flip on random noise)
    pct_5d = yf_stats.get("pct_5d", 0) or 0
    pct_30d = yf_stats.get("pct_30d", 0) or 0
    last = yf_stats.get("last", 0) or 0
    sma20 = yf_stats.get("sma20", 0) or 0
    sma50 = yf_stats.get("sma50", 0) or 0
    above_20 = last > sma20 if sma20 else False
    above_50 = last > sma50 if sma50 else False
    golden = sma20 > sma50 if (sma20 and sma50) else False

    context = (
        f"Ticker: {ticker}\n"
        f"Last price: {last}\n"
        f"5d change: {pct_5d}%\n"
        f"30d change: {pct_30d}%\n"
        f"3mo change: {yf_stats.get('pct_3mo', 'n/a')}%\n"
        f"20d SMA: {sma20} (price {'above' if above_20 else 'below'})\n"
        f"50d SMA: {sma50} (price {'above' if above_50 else 'below'})\n"
        f"20d-over-50d crossover: {'bullish (golden)' if golden else 'bearish/flat'}\n"
        f"3mo high: {yf_stats.get('high_3mo')} / 3mo low: {yf_stats.get('low_3mo')}\n"
        f"Volume last vs 20d-avg: {yf_stats.get('vol_last')} vs {yf_stats.get('vol_avg_20d')}\n"
    )

    user_prompt = (
        "Produce a technical read from the metrics below. Return ONLY this JSON:\n"
        "{\n"
        '  "signal": "BUY" | "HOLD" | "SELL" | "NEUTRAL",\n'
        '  "score": <float -1.0 to +1.0>,\n'
        '  "summary": "<one sentence, <=140 chars, cite a specific metric>"\n'
        "}\n\n"
        + context
    )

    parsed = await llm.ask_json(SYSTEM_PROMPT, user_prompt, max_tokens=400)

    signal = str(parsed.get("signal", "NEUTRAL")).upper()
    if signal not in ("BUY", "HOLD", "SELL", "NEUTRAL"):
        signal = "NEUTRAL"
    insight.technical_signal = signal
    insight.technical_summary = (parsed.get("summary") or "").strip()[:240]
    # Stash the numeric score alongside the metrics so the PM can see it.
    insight.tech_metrics["score"] = clamp(parsed.get("score"), -1.0, 1.0, 0.0)
