"""Intel v2 PR-2 — deterministic thesis input mapper.

Translates existing collected recommendation/intel data into the
``score_thesis()`` input dict.  Only source-backed fields are included;
unavailable fields are omitted (None), never faked.

Unit normalization contract
---------------------------
return_5d / return_30d
    Source: FeatureSet.return_5d / .return_30d come from
    ``fetch_yfinance_history_sync`` as percentage-point values
    (e.g., 5.0 = +5 %).  thesis_engine expects decimal (0.05 = 5 %).
    → divide by 100.

revenue_yoy (from fundamentals["revenue_growth"])
    Source: yfinance ``revenueGrowth`` is a decimal fraction
    (0.12 = 12 %).  If a value > 5.0 is seen it is treated as
    percentage-point format and converted.
    → pass through when |v| ≤ 5.0; divide by 100 otherwise.

relative_strength_vs_spy (from FeatureSet.relative_strength_30d)
    Source: feature_engine computes return_30d − benchmark_return_30d,
    both in percentage points, so the delta is already in pp.
    thesis_engine also expects percentage-point delta.
    → pass through as-is (no conversion).

trailing_pe / forward_pe / peg / beta
    Raw multiples / raw floats from yfinance — no conversion.

sma_20_50_signal
    Derived from FeatureSet.sma20 / sma50 (absolute price levels).
    +1 when sma20 > sma50, −1 when sma20 < sma50, 0 otherwise.

trend_regime_score
    Proxy derived from FeatureSet.trend_regime categorical:
        "uptrend"   → 70   (strong positive momentum proxy)
        "range"     → 40   (neutral proxy)
        "downtrend" → 20   (weak proxy)
    Documented as a proxy — not a calibrated score.

Pure function — no IO, no LLM, no DB, no network.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from .feature_engine import FeatureSet

# Threshold for defensive percent-point detection on revenue_growth.
# yfinance returns decimals; values outside this band look like pp.
_DECIMAL_ABS_MAX = 5.0

# trend_regime_score proxy values (documented as proxy).
_TREND_REGIME_MAP: dict[str, float] = {
    "uptrend":   70.0,
    "range":     40.0,
    "downtrend": 20.0,
}

# Intentionally deferred thesis inputs (do NOT proxy-map from non-equivalent
# fundamentals): fcf_margin, roic_ttm, net_debt_to_ebitda,
# forward_revenue_growth_est. Missing data must remain missing.


def map_to_thesis_inputs(
    fundamentals: dict[str, Any],
    feature_set: Optional["FeatureSet"] = None,
) -> dict[str, Optional[float]]:
    """Map existing ticker data to ``score_thesis()`` input dict.

    Args:
        fundamentals: Dict from ``fetch_yfinance_fundamentals_sync()``
                      (keys: pe, forward_pe, peg, revenue_growth, beta, …).
        feature_set:  Optional :class:`~.feature_engine.FeatureSet` for the
                      same ticker.  Provides momentum / SMA / relative-strength
                      fields.  May be None when only fundamentals are available.

    Returns:
        Mapping of thesis-engine input name → value.
        Only fields with a source-backed value are present (or are None);
        missing fields are simply absent from the dict.

    Unit conventions match thesis_engine docstring exactly — see module
    docstring for the conversion rules applied here.
    """
    out: dict[str, Optional[float]] = {}

    # ── Valuation multiples (from fundamentals) ──────────────────────────────
    # All are raw multiples — no unit conversion required.

    trailing_pe = _safe_float(fundamentals.get("pe"))
    if trailing_pe is not None:
        out["trailing_pe"] = trailing_pe

    forward_pe = _safe_float(fundamentals.get("forward_pe"))
    if forward_pe is not None:
        out["forward_pe"] = forward_pe

    peg = _safe_float(fundamentals.get("peg"))
    if peg is not None:
        out["peg"] = peg

    # ── Growth (from fundamentals) ───────────────────────────────────────────
    # yfinance revenueGrowth is a decimal fraction (0.12 = 12 %).
    # Defensive: convert to decimal if the value looks like percent-points.

    rev_growth = _safe_float(fundamentals.get("revenue_growth"))
    if rev_growth is not None:
        out["revenue_yoy"] = _normalize_to_decimal(rev_growth)

    # ── Risk (from fundamentals) ─────────────────────────────────────────────
    # beta is a raw float — no conversion.

    beta = _safe_float(fundamentals.get("beta"))
    if beta is not None:
        out["beta"] = beta

    # ── Momentum (from FeatureSet) ───────────────────────────────────────────

    if feature_set is not None:
        # Short-term return: percentage-point → decimal.
        if feature_set.return_5d is not None:
            out["return_5d"] = feature_set.return_5d / 100.0

        # Medium-term return: percentage-point → decimal.
        if feature_set.return_30d is not None:
            out["return_30d"] = feature_set.return_30d / 100.0

        # Relative strength: already in percentage-point delta — no conversion.
        if feature_set.relative_strength_30d is not None:
            out["relative_strength_vs_spy"] = feature_set.relative_strength_30d

        # SMA crossover signal derived from absolute SMA price levels.
        sma_signal = _derive_sma_signal(feature_set.sma20, feature_set.sma50)
        if sma_signal is not None:
            out["sma_20_50_signal"] = float(sma_signal)

        # trend_regime_score proxy from categorical trend_regime.
        trend_score = _trend_to_regime_score(feature_set.trend_regime)
        if trend_score is not None:
            out["trend_regime_score"] = trend_score

    return out


# ── Internal helpers ─────────────────────────────────────────────────────────


def _safe_float(v: Any) -> Optional[float]:
    """Return float(v) or None when v is absent, None, or NaN."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN guard
        return None
    return f


def _normalize_to_decimal(v: float) -> float:
    """Return v as a decimal fraction.

    If ``abs(v) > _DECIMAL_ABS_MAX`` the value looks like a percentage-point
    figure (e.g., 12.0 instead of 0.12) — divide by 100.  Otherwise assume
    it is already a decimal and pass through unchanged.
    """
    if abs(v) > _DECIMAL_ABS_MAX:
        return v / 100.0
    return v


def _derive_sma_signal(
    sma20: Optional[float],
    sma50: Optional[float],
) -> Optional[int]:
    """Derive SMA crossover signal from absolute SMA price levels.

    Returns:
        +1 when sma20 > sma50  (bullish crossover)
        -1 when sma20 < sma50  (bearish crossover)
         0 when sma20 == sma50
        None when either SMA is missing or non-positive.
    """
    if sma20 is None or sma50 is None:
        return None
    if sma20 <= 0 or sma50 <= 0:
        return None
    if sma20 > sma50:
        return 1
    if sma20 < sma50:
        return -1
    return 0


def _trend_to_regime_score(trend_regime: str) -> Optional[float]:
    """Map categorical trend_regime to a numeric proxy score.

    This is a coarse proxy — not a calibrated momentum score.  thesis_engine
    will treat it as ``trend_regime_score`` in the 0–100 range.
    Returns None for unrecognised labels so missing data is never faked.
    """
    return _TREND_REGIME_MAP.get(trend_regime)
