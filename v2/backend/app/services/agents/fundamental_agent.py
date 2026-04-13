"""Fundamental analyst — valuation / growth / balance-sheet read."""

from __future__ import annotations

import logging

from .data_sources import fetch_coingecko_market, fetch_fundamentals, is_crypto
from .llm import LLMClient, clamp
from .state import TickerInsight

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = (
    "You are the Fundamentals analyst on a quantitative trading desk. "
    "Given balance-sheet + valuation metrics, you produce a single quality/value "
    "score and a one-sentence justification. For crypto you reason on network "
    "health, market cap rank, and liquidity instead. Output valid JSON only."
)


async def run_fundamental_agent(
    insight: TickerInsight,
    llm: LLMClient,
    http_client,
) -> None:
    ticker = insight.ticker

    if is_crypto(ticker):
        market = await fetch_coingecko_market(http_client, ticker)
        insight.fundamentals = market
        context = (
            f"Crypto: {ticker}\n"
            f"Market cap rank: {market.get('market_cap_rank', 'n/a')}\n"
            f"ATH drawdown: {market.get('ath_pct', 'n/a')}%\n"
            f"30d return: {market.get('pct_30d', 'n/a')}%\n"
            f"Community sentiment up%: {market.get('sentiment_up_pct', 'n/a')}\n"
        )
    else:
        fundamentals = await fetch_fundamentals(ticker)
        insight.fundamentals = fundamentals
        if not fundamentals:
            insight.fundamental_score = 0.0
            insight.fundamental_summary = "No fundamentals available."
            return
        context = "\n".join([
            f"Ticker: {ticker}  ({insight.name})",
            f"Sector: {fundamentals.get('sector')} / {fundamentals.get('industry')}",
            f"Trailing P/E: {fundamentals.get('pe')}",
            f"Forward P/E: {fundamentals.get('forward_pe')}",
            f"PEG: {fundamentals.get('peg')}",
            f"Revenue growth (YoY): {fundamentals.get('revenue_growth')}",
            f"Earnings growth (YoY): {fundamentals.get('earnings_growth')}",
            f"Profit margin: {fundamentals.get('profit_margin')}",
            f"Debt/Equity: {fundamentals.get('debt_to_equity')}",
            f"ROE: {fundamentals.get('return_on_equity')}",
            f"Beta: {fundamentals.get('beta')}",
            f"Dividend yield: {fundamentals.get('dividend_yield')}",
            f"Analyst mean rating (1=Strong Buy, 5=Sell): {fundamentals.get('recommendation_mean')}",
            f"Analyst target mean: {fundamentals.get('target_mean_price')}",
        ])

    user_prompt = (
        "Score the fundamentals of this asset. Return ONLY this JSON:\n"
        "{\n"
        '  "score": <float -1.0 to +1.0>,\n'
        '  "summary": "<one sentence, <=160 chars, cite a specific metric>"\n'
        "}\n\n"
        + context
    )

    parsed = await llm.ask_json(SYSTEM_PROMPT, user_prompt, max_tokens=400)
    insight.fundamental_score = clamp(parsed.get("score"), -1.0, 1.0, 0.0)
    insight.fundamental_summary = (parsed.get("summary") or "").strip()[:240]
