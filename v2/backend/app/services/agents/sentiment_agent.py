"""Sentiment analyst — news-driven mood read per ticker."""

from __future__ import annotations

import logging
from typing import Any

from .data_sources import fetch_coingecko_market, fetch_news_for_ticker, is_crypto
from .llm import LLMClient, clamp
from .state import TickerInsight

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = (
    "You are the Sentiment analyst on a quantitative trading desk. "
    "You read news headlines, short summaries, and social signals, and produce "
    "a single sentiment read per ticker. You are terse, concrete, and never "
    "hedge without a reason. Your output is always valid JSON — no prose "
    "outside the JSON object."
)


async def run_sentiment_agent(
    insight: TickerInsight,
    llm: LLMClient,
    http_client,
    finnhub_key: str,
) -> None:
    """Mutate `insight` in place with sentiment fields."""
    ticker = insight.ticker

    # 1. Fetch raw signal
    if is_crypto(ticker):
        market = await fetch_coingecko_market(http_client, ticker)
        headlines: list[dict[str, Any]] = []
        context_lines = [
            f"Crypto: {ticker}",
            f"24h change: {market.get('pct_24h', 'n/a')}%",
            f"7d change:  {market.get('pct_7d', 'n/a')}%",
            f"30d change: {market.get('pct_30d', 'n/a')}%",
            f"CoinGecko sentiment (votes up%): {market.get('sentiment_up_pct', 'n/a')}",
            f"Market cap rank: {market.get('market_cap_rank', 'n/a')}",
        ]
    else:
        headlines = await fetch_news_for_ticker(http_client, ticker, finnhub_key)
        context_lines = [f"Recent headlines for {ticker}:"]
        if headlines:
            for i, h in enumerate(headlines[:8], 1):
                context_lines.append(f"{i}. {h['headline']} — {h.get('source', '')}")
                if h.get("summary"):
                    context_lines.append(f"   {h['summary'][:180]}")
        else:
            context_lines.append("(no news retrieved in the last 7 days)")

    insight.headlines_used = [h["headline"] for h in headlines[:5]]

    # 2. LLM scoring
    user_prompt = (
        "Score the overall sentiment for this ticker based on the signal below. "
        "Return ONLY this JSON:\n"
        "{\n"
        '  "sentiment_score": <float -1.0 to +1.0>,\n'
        '  "label": "bullish" | "neutral" | "bearish",\n'
        '  "summary": "<one sentence, max 140 chars, cite a headline or data point>"\n'
        "}\n\n"
        + "\n".join(context_lines)
    )

    parsed = await llm.ask_json(SYSTEM_PROMPT, user_prompt, max_tokens=400)

    insight.sentiment_score = clamp(parsed.get("sentiment_score"), -1.0, 1.0, 0.0)
    label = str(parsed.get("label", "neutral")).lower()
    if label not in ("bullish", "neutral", "bearish"):
        label = "neutral"
    insight.sentiment_label = label
    insight.sentiment_summary = (parsed.get("summary") or "No sentiment signal available.")[:240]
