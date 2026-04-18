"""Strategy mode configuration — defines multipliers for growth, income, conservative, and balanced strategies."""

from __future__ import annotations

STRATEGY_MODES: dict[str, dict[str, float]] = {
    "growth": {
        "risk_multiplier": 1.2,
        "growth_bias": 1.3,
        "dividend_bias": 0.8,
    },
    "income": {
        "risk_multiplier": 0.8,
        "growth_bias": 0.7,
        "dividend_bias": 1.4,
    },
    "conservative": {
        "risk_multiplier": 0.6,
        "growth_bias": 0.6,
        "dividend_bias": 1.2,
    },
    "balanced": {
        "risk_multiplier": 1.0,
        "growth_bias": 1.0,
        "dividend_bias": 1.0,
    },
}

# Ticker classification — maintained here so no ticker is hardcoded in engine logic
GROWTH_ASSETS: frozenset[str] = frozenset({
    "NVDA", "QQQ", "AMD", "GOOGL", "META", "AAPL", "MSFT", "NFLX", "CRM", "VGT",
})

DIVIDEND_ASSETS: frozenset[str] = frozenset({
    "VYM", "SCHD", "VIG", "HDV", "DGRO",
})

DEFAULT_STRATEGY: str = "balanced"
