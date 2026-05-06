"""Intel v2 — deterministic thesis score engine.

Accepts pre-collected numeric inputs for a single ticker and returns a
:class:`ScoreCard` with dimensional subscores, data quality tracking,
and a blended conviction score.

Architecture principle:
  Numbers are deterministic.  Claims require sources.
  LLM explains, challenges, and summarises later.
  LLM must not invent metrics, scores, or allocation amounts.
  Deploy v2 owns allocation math.

Pure function — no IO, no LLM calls, no database, no yfinance or
external service imports.  All input values are expected to be already
collected and passed in.

Input conventions (all rates/margins/yields as decimals unless noted):
  - Margins, ROIC, growth rates, yields: decimal (0.15 = 15 %)
  - Multiples (P/E, EV/EBITDA, P/FCF, P/S, PEG): raw ratio (20.0 = 20×)
  - share_count_delta_3y: decimal fraction (+0.05 = +5 % dilution)
  - beta: raw float (1.20)
  - max_drawdown_1y: negative decimal (−0.30 = −30 % drawdown)
  - insider_net_selling_6m: decimal fraction (−0.01 = 1 % net sold)
  - gaap_nongaap_gap: decimal fraction (0.15 = 15 % gap; 0 = no gap)
  - sma_20_50_signal: integer −1 / 0 / +1 (precomputed crossover signal)
  - trend_regime_score: 0–100 (precomputed; 100 = strongest uptrend)
  - relative_strength_vs_spy: percentage-point delta (5.0 = +5 pp vs SPY)
  - customer_concentration_flag: 0 or 1
  - guidance_cut_count_4q: non-negative integer (0 = no cuts)
"""

from __future__ import annotations

from typing import Callable, Optional

from .score_schema import ConvictionBand, ScoreCard, ScoreStatus, SubScore


# ── Blend weights ─────────────────────────────────────────────────────────────

WEIGHT_QUALITY   = 0.30
WEIGHT_VALUATION = 0.25
WEIGHT_GROWTH    = 0.15
WEIGHT_RISK      = 0.20
WEIGHT_MOMENTUM  = 0.10

_BLEND_WEIGHTS: dict[str, float] = {
    "quality":   WEIGHT_QUALITY,
    "valuation": WEIGHT_VALUATION,
    "growth":    WEIGHT_GROWTH,
    "risk":      WEIGHT_RISK,
    "momentum":  WEIGHT_MOMENTUM,
}


# ── Data quality thresholds ───────────────────────────────────────────────────

# Subscore is not published when data_quality falls below this.
MIN_SUBSCORE_QUALITY = 0.40

# Conviction is not published when blended data quality falls below this.
MIN_CONVICTION_QUALITY = 0.50

# Status is INSUFFICIENT_DATA when this many *major* subscores are weak.
# Stock defaults: require 3-of-4 major scores weak (not 2) AND lower weakness
# threshold to 0.25 so that yfinance-backed tickers with partial coverage
# (e.g. 1-2/7 quality inputs) are PARTIAL rather than INSUFFICIENT_DATA.
_MAJOR_SCORES = {"quality", "valuation", "growth", "risk"}
_INSUFFICIENT_MAJOR_COUNT = 2      # at least 2 of the 4 major scores are weak
_MAJOR_MIN_QUALITY = 0.50          # "weak" = data_quality < this threshold

# Asset-type-aware overrides for _overall_status().
# ETFs lack company fundamentals (quality/growth); crypto lacks almost all
# company metrics. These overrides let the status reflect what IS available
# for each asset type rather than penalising missing inapplicable inputs.
_MAJOR_SCORES_BY_TYPE: dict[str, frozenset[str]] = {
    "stock":     frozenset({"quality", "valuation", "growth", "risk"}),
    "etf":       frozenset({"momentum", "valuation"}),
    "crypto":    frozenset({"momentum"}),
    "commodity": frozenset({"momentum"}),
}
# Number of weak major scores required to trigger INSUFFICIENT_DATA.
_INSUFFICIENT_COUNT_BY_TYPE: dict[str, int] = {
    "stock":     3,   # raised from 2 — partial yfinance coverage is normal
    "etf":       2,   # need BOTH momentum AND valuation to be weak
    "crypto":    1,   # single major score
    "commodity": 1,
}
# "Weak" threshold per asset type.
_MAJOR_MIN_QUALITY_BY_TYPE: dict[str, float] = {
    "stock":     0.25,  # lowered from 0.50 — yfinance rarely fills >50 % inputs
    "etf":       0.25,
    "crypto":    0.20,
    "commodity": 0.20,
}


# ── Conviction band thresholds ────────────────────────────────────────────────

CONVICTION_HIGH_MIN   = 70.0
CONVICTION_MEDIUM_MIN = 50.0


# ── Input definitions ─────────────────────────────────────────────────────────

_QUALITY_INPUTS = [
    "roic_ttm",
    "gross_margin",
    "fcf_margin",
    "fcf_to_net_income",
    "net_debt_to_ebitda",
    "interest_coverage",
    "share_count_delta_3y",
]

_VALUATION_INPUTS = [
    "ps_ttm",
    "ps_forward",
    "p_fcf",
    "ev_ebitda",
    "peg",
    "fcf_yield",
    "forward_pe",
    "trailing_pe",
    "peer_ps_median",
    "peer_ev_ebitda_median",
    "own_5y_ps_median",
]

_GROWTH_INPUTS = [
    "revenue_cagr_3y",
    "revenue_yoy",
    "fcf_cagr_3y",
    "gross_profit_yoy",
    "forward_revenue_growth_est",
]

_RISK_INPUTS = [
    "customer_concentration_flag",
    "guidance_cut_count_4q",
    "insider_net_selling_6m",
    "net_debt_to_ebitda",
    "beta",
    "max_drawdown_1y",
    "gaap_nongaap_gap",
]

_MOMENTUM_INPUTS = [
    "relative_strength_vs_spy",
    "trend_regime_score",
    "return_5d",
    "return_30d",
    "sma_20_50_signal",
]


# ── Primitive normalizers ─────────────────────────────────────────────────────

def _norm(value: float, low: float, high: float) -> float:
    """Linear normalise value to [0, 1]; clamp to [0, 1]."""
    if high <= low:
        return 0.0
    return max(0.0, min(1.0, (value - low) / (high - low)))


def _norm_inv(value: float, low: float, high: float) -> float:
    """Normalise inverted — lower value yields higher score."""
    return 1.0 - _norm(value, low, high)


def _score_clamp(x: float) -> float:
    """Clamp a 0–100 score to its valid range."""
    return max(0.0, min(100.0, x))


# ── Subscore builders ─────────────────────────────────────────────────────────

_Get = Callable[[str], Optional[float]]


def _make_subscore(
    all_inputs: list[str],
    raw_scores: list[float],
    used: list[str],
) -> SubScore:
    missing = [k for k in all_inputs if k not in used]
    dq = len(used) / len(all_inputs) if all_inputs else 0.0
    published = dq >= MIN_SUBSCORE_QUALITY
    score = _score_clamp(
        round(sum(raw_scores) / len(raw_scores) * 100.0, 1)
        if raw_scores else 0.0
    )
    return SubScore(
        score=score,
        data_quality=round(dq, 3),
        inputs_used=used,
        inputs_missing=missing,
        published=published,
    )


def _score_quality(get: _Get) -> SubScore:
    """Business quality subscore.

    Higher score = higher quality (strong ROIC, fat margins, low leverage,
    good FCF conversion, buybacks instead of dilution).
    """
    used: list[str] = []
    raw: list[float] = []

    def _add(key: str, fn: Callable[[float], float]) -> None:
        v = get(key)
        if v is not None:
            used.append(key)
            raw.append(fn(v))

    _add("roic_ttm",             lambda v: _norm(v, 0.0, 0.25))
    _add("gross_margin",         lambda v: _norm(v, 0.20, 0.80))
    _add("fcf_margin",           lambda v: _norm(v, 0.0, 0.30))
    _add("fcf_to_net_income",    lambda v: _norm(v, 0.50, 1.50))
    _add("net_debt_to_ebitda",   lambda v: _norm_inv(v, -1.0, 4.0))
    _add("interest_coverage",    lambda v: _norm(v, 2.0, 20.0))
    _add("share_count_delta_3y", lambda v: _norm_inv(v, -0.10, 0.10))

    return _make_subscore(_QUALITY_INPUTS, raw, used)


def _score_valuation(get: _Get) -> SubScore:
    """Valuation attractiveness subscore.

    Higher score = cheaper / more attractive (low multiples, high yield,
    discount to peers and own history).

    Relative metrics (peer/history comparisons) contribute only when
    both the ticker value and the benchmark are present.
    """
    used: list[str] = []
    raw: list[float] = []

    def _add(key: str, fn: Callable[[float], float]) -> None:
        v = get(key)
        if v is not None:
            used.append(key)
            raw.append(fn(v))

    # Standalone absolute metrics (lower multiple = higher score)
    _add("ps_ttm",      lambda v: _norm_inv(v, 2.0, 20.0))
    _add("ps_forward",  lambda v: _norm_inv(v, 2.0, 20.0))
    _add("p_fcf",       lambda v: _norm_inv(v, 10.0, 50.0))
    _add("ev_ebitda",   lambda v: _norm_inv(v, 8.0, 30.0))
    _add("peg",         lambda v: _norm_inv(v, 0.50, 3.0))
    _add("fcf_yield",   lambda v: _norm(v, 0.0, 0.08))      # higher yield = better
    _add("forward_pe",  lambda v: _norm_inv(v, 10.0, 40.0))
    _add("trailing_pe", lambda v: _norm_inv(v, 10.0, 50.0))

    # Track comparative inputs for data_quality even if unpaired
    ps_ttm = get("ps_ttm")
    ev_ebitda = get("ev_ebitda")
    peer_ps = get("peer_ps_median")
    peer_ev = get("peer_ev_ebitda_median")
    own_5y = get("own_5y_ps_median")

    if peer_ps is not None and "peer_ps_median" not in used:
        used.append("peer_ps_median")
    if peer_ev is not None and "peer_ev_ebitda_median" not in used:
        used.append("peer_ev_ebitda_median")
    if own_5y is not None and "own_5y_ps_median" not in used:
        used.append("own_5y_ps_median")

    # Relative score: ticker vs peers (ratio < 1 = discount = good)
    if ps_ttm is not None and peer_ps is not None and peer_ps > 0:
        raw.append(_norm_inv(ps_ttm / peer_ps, 0.50, 2.0))
    if ev_ebitda is not None and peer_ev is not None and peer_ev > 0:
        raw.append(_norm_inv(ev_ebitda / peer_ev, 0.50, 2.0))

    # Historical score: ticker vs own 5-year P/S (ratio < 1 = discount = good)
    if ps_ttm is not None and own_5y is not None and own_5y > 0:
        raw.append(_norm_inv(ps_ttm / own_5y, 0.50, 2.0))

    return _make_subscore(_VALUATION_INPUTS, raw, used)


def _score_growth(get: _Get) -> SubScore:
    """Growth subscore.

    Higher score = faster, more consistent growth.
    """
    used: list[str] = []
    raw: list[float] = []

    def _add(key: str, fn: Callable[[float], float]) -> None:
        v = get(key)
        if v is not None:
            used.append(key)
            raw.append(fn(v))

    _add("revenue_cagr_3y",           lambda v: _norm(v, 0.0, 0.35))
    _add("revenue_yoy",               lambda v: _norm(v, 0.0, 0.40))
    _add("fcf_cagr_3y",               lambda v: _norm(v, 0.0, 0.40))
    _add("gross_profit_yoy",          lambda v: _norm(v, 0.0, 0.40))
    _add("forward_revenue_growth_est", lambda v: _norm(v, 0.0, 0.30))

    return _make_subscore(_GROWTH_INPUTS, raw, used)


def _score_risk(get: _Get) -> SubScore:
    """Risk / safety subscore.

    Higher score = safer (low leverage, no guidance cuts, insider buying,
    low beta, shallow drawdown, no GAAP gap).

    gaap_nongaap_gap is optional — missing does not penalise the score.
    """
    used: list[str] = []
    raw: list[float] = []

    def _add(key: str, fn: Callable[[float], float]) -> None:
        v = get(key)
        if v is not None:
            used.append(key)
            raw.append(fn(v))

    # 0 = no concentration risk = safe (1 = concentrated = penalty)
    _add("customer_concentration_flag", lambda v: 1.0 - _norm(float(v), 0.0, 1.0))
    # Fewer guidance cuts = safer
    _add("guidance_cut_count_4q",       lambda v: _norm_inv(v, 0.0, 4.0))
    # Positive = insider buying = safer
    _add("insider_net_selling_6m",      lambda v: _norm(v, -0.02, 0.02))
    # Lower net debt = safer
    _add("net_debt_to_ebitda",          lambda v: _norm_inv(v, -1.0, 4.0))
    # Lower beta = safer
    _add("beta",                        lambda v: _norm_inv(v, 0.50, 2.0))
    # Less negative drawdown = safer (0.0 = no drawdown = best)
    _add("max_drawdown_1y",             lambda v: _norm(v, -0.50, 0.0))
    # Smaller GAAP/non-GAAP gap = more transparent = safer
    _add("gaap_nongaap_gap",            lambda v: _norm_inv(v, 0.0, 0.30))

    return _make_subscore(_RISK_INPUTS, raw, used)


def _score_momentum(get: _Get) -> SubScore:
    """Price/trend momentum subscore.

    Accepts precomputed values only — does not fetch market data.
    Higher score = stronger positive momentum.
    """
    used: list[str] = []
    raw: list[float] = []

    def _add(key: str, fn: Callable[[float], float]) -> None:
        v = get(key)
        if v is not None:
            used.append(key)
            raw.append(fn(v))

    # Percentage-point outperformance vs SPY (−20 to +20 pp)
    _add("relative_strength_vs_spy", lambda v: _norm(v, -20.0, 20.0))
    # Precomputed 0–100 regime score (100 = strongest uptrend)
    _add("trend_regime_score",       lambda v: _norm(v, 0.0, 100.0))
    # Short-term return (decimal; −10 % to +10 %)
    _add("return_5d",                lambda v: _norm(v, -0.10, 0.10))
    # Medium-term return (decimal; −20 % to +20 %)
    _add("return_30d",               lambda v: _norm(v, -0.20, 0.20))
    # Precomputed SMA crossover signal: −1 / 0 / +1 → mapped to 0 / 0.5 / 1.0
    _add("sma_20_50_signal",         lambda v: (float(v) + 1.0) / 2.0)

    return _make_subscore(_MOMENTUM_INPUTS, raw, used)


# ── Conviction blend ──────────────────────────────────────────────────────────

def _blend_conviction(
    subscores: dict[str, SubScore],
) -> tuple[Optional[float], float]:
    """Weighted blend of published subscores.

    Uses the exact weights from _BLEND_WEIGHTS.  If fewer than all
    subscores are published, normalises by the sum of weights that
    contributed so the result remains in [0, 100].

    Returns:
        (conviction_score, blended_data_quality)

    ``conviction_score`` is None when blended_data_quality < MIN_CONVICTION_QUALITY.
    """
    total_weight = 0.0
    weighted_score = 0.0
    weighted_quality = 0.0

    for name, weight in _BLEND_WEIGHTS.items():
        ss = subscores.get(name)
        if ss and ss.published:
            weighted_score += ss.score * weight
            weighted_quality += ss.data_quality * weight
            total_weight += weight

    if total_weight == 0.0:
        return None, 0.0

    blended_quality = round(weighted_quality / total_weight, 3)
    if blended_quality < MIN_CONVICTION_QUALITY:
        return None, blended_quality

    conviction = round(weighted_score / total_weight, 1)
    return _score_clamp(conviction), blended_quality


def _conviction_band(
    conviction_score: Optional[float],
    blended_quality: float,
) -> ConvictionBand:
    if conviction_score is None or blended_quality < MIN_CONVICTION_QUALITY:
        return ConvictionBand.INSUFFICIENT_DATA
    if conviction_score >= CONVICTION_HIGH_MIN:
        return ConvictionBand.HIGH
    if conviction_score >= CONVICTION_MEDIUM_MIN:
        return ConvictionBand.MEDIUM
    return ConvictionBand.LOW


def _overall_status(
    subscores: dict[str, SubScore],
    blended_quality: float,
    asset_type: str = "stock",
) -> ScoreStatus:
    """Derive overall scorecard status from subscore coverage.

    Asset-type-aware: ETFs and crypto/commodities do not have company
    fundamentals, so quality/growth are not required major axes for them.
    """
    effective_major = _MAJOR_SCORES_BY_TYPE.get(asset_type, _MAJOR_SCORES_BY_TYPE["stock"])
    insufficient_count = _INSUFFICIENT_COUNT_BY_TYPE.get(asset_type, _INSUFFICIENT_COUNT_BY_TYPE["stock"])
    min_quality = _MAJOR_MIN_QUALITY_BY_TYPE.get(asset_type, _MAJOR_MIN_QUALITY_BY_TYPE["stock"])

    major_insufficient = sum(
        1
        for name in effective_major
        if subscores.get(name) is not None and subscores[name].data_quality < min_quality
    )
    if (
        major_insufficient >= insufficient_count
        or blended_quality < MIN_CONVICTION_QUALITY
    ):
        return ScoreStatus.INSUFFICIENT_DATA

    if all(ss.published for ss in subscores.values()):
        return ScoreStatus.READY
    return ScoreStatus.PARTIAL


# ── Public entry point ────────────────────────────────────────────────────────

def score_thesis(
    ticker: str,
    inputs: dict[str, Optional[float]],
    *,
    asset_type: str = "stock",
) -> ScoreCard:
    """Deterministic thesis score engine — Intel v2 foundation.

    Args:
        ticker: Ticker symbol (used for provenance only; no data lookup).
        inputs: Mapping of input field name → numeric value.
                Pass ``None`` or omit a key for unavailable fields.
                See module docstring for expected units.

    Returns:
        A :class:`ScoreCard` with five subscores, blended conviction,
        data quality, and status.  Identical inputs always produce
        identical outputs.
    """

    def get(key: str) -> Optional[float]:
        return inputs.get(key)

    quality   = _score_quality(get)
    valuation = _score_valuation(get)
    growth    = _score_growth(get)
    risk      = _score_risk(get)
    momentum  = _score_momentum(get)

    subscores: dict[str, SubScore] = {
        "quality":   quality,
        "valuation": valuation,
        "growth":    growth,
        "risk":      risk,
        "momentum":  momentum,
    }

    conviction_score, blended_quality = _blend_conviction(subscores)
    band   = _conviction_band(conviction_score, blended_quality)
    status = _overall_status(subscores, blended_quality, asset_type=asset_type)

    all_used    = sorted({u for ss in subscores.values() for u in ss.inputs_used})
    all_missing = sorted({m for ss in subscores.values() for m in ss.inputs_missing})

    return ScoreCard(
        ticker=ticker,
        status=status,
        quality=quality,
        valuation=valuation,
        growth=growth,
        risk=risk,
        momentum=momentum,
        conviction_score=conviction_score,
        conviction_band=band,
        blended_data_quality=blended_quality,
        inputs_used=all_used,
        inputs_missing=all_missing,
    )
