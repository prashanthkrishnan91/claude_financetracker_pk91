"""Market Regime Detection — bull / neutral / risk_off classifier.

Pure deterministic heuristic over the existing SPY benchmark dict produced
by ``services/intelligence/benchmark.fetch_benchmark_price_action()``. No
new external API calls — the benchmark fetcher is already cached + coalesced
+ stale-fallback safe.

Used by the Adaptive Allocation Engine (services/adaptive_deployment.py)
to decide how much cash to deploy now vs. hold back. Failure-isolated:
empty / missing data → neutral with data_quality=low. Never raises.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

logger = logging.getLogger(__name__)


RegimeLabel = Literal["bull", "neutral", "risk_off"]
DataQuality = Literal["high", "medium", "low"]


@dataclass
class RegimeOutput:
    regime_label: RegimeLabel
    regime_score: float                      # 0..100
    regime_reasons: list[str]
    data_quality: DataQuality
    spy_pct_5d: Optional[float] = None
    spy_pct_30d: Optional[float] = None
    spy_vs_sma20: Optional[float] = None     # % above/below
    spy_vs_sma50: Optional[float] = None     # % above/below
    drawdown_pct: Optional[float] = None     # negative number
    realized_vol_30d: Optional[float] = None # annualised


# Score thresholds
_BULL_THRESHOLD = 65.0
_RISK_OFF_THRESHOLD = 35.0
_BASELINE = 50.0


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return f


def _vs_sma(last: Optional[float], sma: Optional[float]) -> Optional[float]:
    if last is None or sma is None or sma <= 0:
        return None
    return (last / sma - 1.0) * 100.0


def _drawdown(last: Optional[float], high: Optional[float]) -> Optional[float]:
    if last is None or high is None or high <= 0:
        return None
    return (last / high - 1.0) * 100.0


def detect_market_regime_from_bundle(bundle: Optional[dict]) -> RegimeOutput:
    """Pure scorer over a benchmark price-action dict.

    Expected keys (all optional): ``last``, ``sma20``, ``sma50``, ``pct_5d``,
    ``pct_30d``, ``volatility_30d``, ``high_3mo``, ``n_bars``.
    """
    bundle = bundle if isinstance(bundle, dict) else {}

    last = _safe_float(bundle.get("last"))
    sma20 = _safe_float(bundle.get("sma20"))
    sma50 = _safe_float(bundle.get("sma50"))
    pct_5d = _safe_float(bundle.get("pct_5d"))
    pct_30d = _safe_float(bundle.get("pct_30d"))
    vol_30d = _safe_float(bundle.get("volatility_30d"))
    high_3mo = _safe_float(bundle.get("high_3mo"))
    n_bars = _safe_float(bundle.get("n_bars")) or 0.0

    vs_sma20 = _vs_sma(last, sma20)
    vs_sma50 = _vs_sma(last, sma50)
    drawdown = _drawdown(last, high_3mo)

    # Data-quality heuristic — count how many of the 5 core signals we have.
    have = sum(
        1 for v in (vs_sma50, vs_sma20, pct_30d, vol_30d, drawdown) if v is not None
    )
    if have == 0 or not bundle:
        return RegimeOutput(
            regime_label="neutral",
            regime_score=_BASELINE,
            regime_reasons=["market data unavailable — defaulting to neutral"],
            data_quality="low",
        )
    if have <= 2 or n_bars and n_bars < 22:
        data_quality: DataQuality = "low"
    elif have <= 3:
        data_quality = "medium"
    else:
        data_quality = "high"

    score = _BASELINE
    reasons: list[str] = []

    # Trend vs moving averages
    if vs_sma50 is not None:
        if vs_sma50 > 0:
            score += 20
            reasons.append(f"SPY {vs_sma50:+.1f}% vs 50D MA (constructive trend)")
        else:
            score -= 20
            reasons.append(f"SPY {vs_sma50:+.1f}% vs 50D MA (below long-term trend)")
    if vs_sma20 is not None:
        if vs_sma20 > 0:
            score += 10
        else:
            score -= 10

    # Recent return (use pct_30d as the medium-term anchor, pct_5d as the short)
    if pct_30d is not None:
        clamped_30 = max(-15.0, min(15.0, pct_30d))
        score += clamped_30
        if pct_30d <= -5.0:
            reasons.append(f"30-day SPY return {pct_30d:+.1f}% (weakness)")
        elif pct_30d >= 5.0:
            reasons.append(f"30-day SPY return {pct_30d:+.1f}% (strength)")
    if pct_5d is not None:
        score += max(-5.0, min(5.0, pct_5d / 2.0))

    # Drawdown — negative numbers (e.g. -7.5 means 7.5% off recent high)
    if drawdown is not None:
        if drawdown <= -15.0:
            score -= 20
            reasons.append(f"SPY drawdown {drawdown:.1f}% from 3M high (severe)")
        elif drawdown <= -10.0:
            score -= 10
            reasons.append(f"SPY drawdown {drawdown:.1f}% from 3M high (elevated)")
        elif drawdown <= -5.0:
            score -= 3

    # Volatility — annualised stddev of daily log-returns
    if vol_30d is not None:
        if vol_30d >= 0.35:
            score -= 15
            reasons.append(f"realized vol {vol_30d:.2f} (elevated)")
        elif vol_30d >= 0.25:
            score -= 10
            reasons.append(f"realized vol {vol_30d:.2f} (above-average)")
        elif vol_30d <= 0.12:
            score += 3

    # Clamp + classify
    score = max(0.0, min(100.0, score))
    if score >= _BULL_THRESHOLD:
        label: RegimeLabel = "bull"
    elif score <= _RISK_OFF_THRESHOLD:
        label = "risk_off"
    else:
        label = "neutral"

    if not reasons:
        reasons.append(f"mixed signals — score {score:.0f}/100")

    return RegimeOutput(
        regime_label=label,
        regime_score=round(score, 1),
        regime_reasons=reasons[:4],
        data_quality=data_quality,
        spy_pct_5d=pct_5d,
        spy_pct_30d=pct_30d,
        spy_vs_sma20=round(vs_sma20, 2) if vs_sma20 is not None else None,
        spy_vs_sma50=round(vs_sma50, 2) if vs_sma50 is not None else None,
        drawdown_pct=round(drawdown, 2) if drawdown is not None else None,
        realized_vol_30d=vol_30d,
    )


async def detect_market_regime(*, benchmark_symbol: str = "SPY") -> RegimeOutput:
    """Async wrapper — pulls the cached SPY benchmark and scores it.

    Always returns a RegimeOutput. Network/cache failures degrade to a
    neutral/low-quality verdict rather than raising.
    """
    try:
        from .intelligence.benchmark import fetch_benchmark_price_action
        bundle = await fetch_benchmark_price_action(benchmark_symbol)
    except Exception as exc:  # noqa: BLE001 — absolute failure isolation
        logger.warning("regime_engine: benchmark fetch failed (%s)", exc)
        bundle = {}

    return detect_market_regime_from_bundle(bundle)
