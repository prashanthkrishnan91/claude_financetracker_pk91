"""Portfolio Governor Lite — K2: maps actual portfolio weights to FitBand.

Uses real holdings data (ticker, current_value, portfolio_total) instead of
guessing fit from action signals alone. This prevents the governor from blindly
echoing the upstream action as fit.

Thresholds (configurable at call site, defaults below):
  BLOCKED:    category is speculative/crypto/ipo  — never BUY
  BREACH:     current_pct > cap_pct * 1.5         — urgent reduce
  OVERWEIGHT: current_pct > cap_pct               — trim toward target
  ON_TARGET:  current_pct within ±50% of target   — hold or add small
  UNDERWEIGHT:current_pct < target_pct * 0.5      — room to add
  UNKNOWN:    no weight data available

Pure function — no IO, DB, LLM.
"""
from __future__ import annotations

from typing import Optional

from .decision_contracts import FitBand

# Tickers/categories blocked from BUY by design.
_SPECULATIVE_TICKERS: frozenset[str] = frozenset(
    {"BTC", "XRP", "RIVN", "KLAR", "BLSH"}
)
_BLOCKED_CAT_KEYWORDS: frozenset[str] = frozenset({"crypto", "speculative", "ipo"})

# Default concentration caps by category (pct of total portfolio).
_DEFAULT_CAPS: dict[str, float] = {
    "etf":         30.0,
    "stock":       15.0,
    "crypto":       5.0,
    "commodity":    5.0,
    "ipo":          3.0,
    "other":       10.0,
}

# Breach multiplier: current_pct > cap * BREACH_MULTIPLIER → BREACH.
_BREACH_MULTIPLIER = 1.5


def _resolve_cap(category: str, target_pct: Optional[float]) -> float:
    """Return concentration cap: prefer explicit target, else default by category."""
    if target_pct is not None and target_pct > 0:
        return float(target_pct)
    cat_low = (category or "").lower()
    for key, cap in _DEFAULT_CAPS.items():
        if key in cat_low:
            return cap
    return _DEFAULT_CAPS["other"]


def compute_portfolio_fit(
    *,
    ticker: str,
    category: str,
    current_pct: Optional[float],
    target_pct: Optional[float] = None,
    suppression_reasons: Optional[dict] = None,
) -> FitBand:
    """Compute FitBand from actual portfolio weight data.

    Args:
        ticker:            Ticker symbol.
        category:          Asset category (stock/etf/crypto/commodity/ipo).
        current_pct:       Current portfolio weight (0–100). None → UNKNOWN.
        target_pct:        Target portfolio weight (0–100). None → use cap default.
        suppression_reasons: Optional dict to populate with reasons (mutated in place).

    Returns:
        FitBand enum value.
    """
    if suppression_reasons is None:
        suppression_reasons = {}

    ticker_up = (ticker or "").upper()
    cat_low = (category or "").lower()

    # BLOCKED: speculative tickers and categories.
    if ticker_up in _SPECULATIVE_TICKERS or any(kw in cat_low for kw in _BLOCKED_CAT_KEYWORDS):
        return FitBand.BLOCKED

    # No weight data available.
    if current_pct is None:
        suppression_reasons["portfolio_fit"] = "No current weight data — fit unknown."
        return FitBand.UNKNOWN

    cap = _resolve_cap(category, target_pct)
    breach_threshold = cap * _BREACH_MULTIPLIER

    if current_pct >= breach_threshold:
        return FitBand.BREACH

    if current_pct >= cap:
        return FitBand.OVERWEIGHT

    half_cap = cap * 0.5
    if current_pct >= half_cap:
        return FitBand.ON_TARGET

    return FitBand.UNDERWEIGHT


def build_weight_map(positions: list[dict]) -> dict[str, float]:
    """Build ticker→weight_pct map from a list of position dicts.

    Each position dict should have: ticker, market_value (or current_value).
    Returns a dict of {ticker_upper: pct_of_total}.
    """
    total = sum(
        float(p.get("market_value") or p.get("current_value") or 0.0)
        for p in positions
        if p
    )
    if total <= 0:
        return {}
    return {
        p["ticker"].upper(): (
            float(p.get("market_value") or p.get("current_value") or 0.0) / total * 100.0
        )
        for p in positions
        if p and p.get("ticker")
    }
