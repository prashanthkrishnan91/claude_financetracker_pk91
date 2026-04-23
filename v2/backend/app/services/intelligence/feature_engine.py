"""Phase 2 — deterministic per-ticker feature generation.

Pure, LLM-free feature engine over the Phase 1 :class:`MarketSnapshot`
rows + the raw io_layer bundle. The output is a :class:`FeatureSet` per
ticker, consumed by:
  * the per-ticker LLM analyst stage (Phase 3) as structured input;
  * the portfolio synthesis stage (Phase 4) as cross-asset signal.

Design invariants:
  * Pure — no DB, no network, no LLM. Deterministic for identical inputs.
  * Never raises — missing inputs yield a coarse regime with the
    propagated data_quality_score; nothing is silently faked.
  * Feature values vary strictly with upstream inputs. Two tickers with
    different prices / returns / volatility will produce different
    feature sets (otherwise the LLM collapses back into uniform HOLD).
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional

from .market_snapshot import MarketSnapshot


# ── Regime thresholds ───────────────────────────────────────────────────────
# Annualized volatility bands. Tuned against a long-equities universe so
# a typical large-cap lands in ``medium`` and crypto tickers reliably
# trip ``high``.
_VOL_LOW_MAX = 0.25
_VOL_MEDIUM_MAX = 0.45

# Relative-strength band (30-day return delta vs SPY, in percentage points).
# ±3 pp keeps the ``inline`` bucket from collapsing on noise while still
# distinguishing a 5-pp outperformer.
_RS_BAND_PP = 3.0

# Momentum normalization cap — a 20% 30-day move saturates momentum at 1.0.
_MOMENTUM_CAP_PP = 20.0


@dataclass
class FeatureSet:
    """Deterministic per-ticker feature row for the feature engine.

    Persisted one row per ticker per run into ``agent_features``.
    """

    ticker: str
    as_of: str

    # Trend / regime classification
    trend_regime: str = "range"            # uptrend / range / downtrend
    sma20: Optional[float] = None
    sma50: Optional[float] = None
    price: Optional[float] = None

    # Momentum
    momentum_score: float = 0.0            # blended 5d + 30d, clamped [-1, 1]
    return_5d: Optional[float] = None
    return_30d: Optional[float] = None

    # Volatility
    volatility_regime: str = "medium"      # low / medium / high
    volatility_30d: Optional[float] = None

    # Relative strength vs benchmark (SPY by default)
    benchmark_symbol: str = "SPY"
    benchmark_return_30d: Optional[float] = None
    relative_strength_30d: Optional[float] = None
    relative_strength_label: str = "inline"  # outperforming / inline / underperforming

    # Sector context
    sector: str = ""
    industry: str = ""
    category: str = "Other"

    # Data-quality propagation from MarketSnapshot
    data_quality_score: float = 0.0
    missing_fields: list[str] = field(default_factory=list)

    def to_row(self, *, run_id: str, user_id: str) -> dict[str, Any]:
        """Shape the feature set for an insert into ``agent_features``."""
        return {
            "run_id": run_id,
            "user_id": user_id,
            "ticker": self.ticker,
            "as_of": self.as_of,
            "trend_regime": self.trend_regime,
            "sma20": self.sma20,
            "sma50": self.sma50,
            "price": self.price,
            "momentum_score": round(self.momentum_score, 4),
            "return_5d": self.return_5d,
            "return_30d": self.return_30d,
            "volatility_regime": self.volatility_regime,
            "volatility_30d": self.volatility_30d,
            "benchmark_symbol": self.benchmark_symbol,
            "benchmark_return_30d": self.benchmark_return_30d,
            "relative_strength_30d": self.relative_strength_30d,
            "relative_strength_label": self.relative_strength_label,
            "sector": self.sector,
            "industry": self.industry,
            "category": self.category,
            "data_quality_score": round(self.data_quality_score, 3),
            "missing_fields": self.missing_fields,
        }

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── Pure builder ────────────────────────────────────────────────────────────


def build_features(
    snapshots: dict[str, MarketSnapshot],
    *,
    bundle: dict[str, Any],
    benchmark: Optional[dict[str, Any]] = None,
    benchmark_symbol: str = "SPY",
) -> dict[str, FeatureSet]:
    """Project ``snapshots`` + ``bundle`` into one :class:`FeatureSet` per ticker.

    ``benchmark`` is the price-action dict for the benchmark symbol
    (typically SPY). When missing or empty, relative-strength degrades
    to absolute 30-day return and the label collapses to ``inline``.
    """
    price_action_map = (bundle or {}).get("price_action") or {}
    benchmark = benchmark or {}
    benchmark_return = _to_float_or_none(benchmark.get("pct_30d"))

    features: dict[str, FeatureSet] = {}
    for ticker, snap in snapshots.items():
        pa = price_action_map.get(ticker) or {}
        features[ticker] = _build_one(
            snap=snap,
            price_action=pa,
            benchmark_return=benchmark_return,
            benchmark_symbol=benchmark_symbol,
        )
    return features


def _build_one(
    *,
    snap: MarketSnapshot,
    price_action: dict[str, Any],
    benchmark_return: Optional[float],
    benchmark_symbol: str,
) -> FeatureSet:
    fs = FeatureSet(
        ticker=snap.ticker,
        as_of=snap.as_of,
        sector=snap.sector,
        industry=snap.industry,
        category=snap.category,
        price=snap.price,
        return_5d=snap.return_5d,
        return_30d=snap.return_30d,
        volatility_30d=snap.volatility_30d,
        data_quality_score=snap.data_quality_score,
        missing_fields=list(snap.missing_fields),
        benchmark_symbol=benchmark_symbol,
    )

    fs.sma20 = _to_float_or_none(price_action.get("sma20"))
    fs.sma50 = _to_float_or_none(price_action.get("sma50"))
    fs.trend_regime = _classify_trend(
        price=fs.price, sma20=fs.sma20, sma50=fs.sma50,
    )

    fs.momentum_score = _momentum_score(
        return_5d=fs.return_5d, return_30d=fs.return_30d,
    )
    fs.volatility_regime = _classify_volatility(fs.volatility_30d)

    fs.benchmark_return_30d = benchmark_return
    if fs.return_30d is not None and benchmark_return is not None:
        rs = round(fs.return_30d - benchmark_return, 3)
        fs.relative_strength_30d = rs
        fs.relative_strength_label = _classify_rs(rs)
    elif fs.return_30d is not None:
        # No benchmark — tag the absolute momentum bucket so the downstream
        # LLM still sees a coarse signal instead of a null cell.
        fs.relative_strength_30d = None
        fs.relative_strength_label = "inline"
    else:
        fs.relative_strength_30d = None
        fs.relative_strength_label = "inline"

    return fs


# ── Classifiers ─────────────────────────────────────────────────────────────


def _classify_trend(
    *, price: Optional[float], sma20: Optional[float], sma50: Optional[float],
) -> str:
    """Uptrend / range / downtrend from price vs MA20 vs MA50.

    Rules:
        * uptrend:   price > sma20 > sma50
        * downtrend: price < sma20 < sma50
        * range:     every other ordering, or any field missing
    """
    if price is None or sma20 is None or sma50 is None:
        return "range"
    if price <= 0 or sma20 <= 0 or sma50 <= 0:
        return "range"
    if price > sma20 > sma50:
        return "uptrend"
    if price < sma20 < sma50:
        return "downtrend"
    return "range"


def _momentum_score(
    *, return_5d: Optional[float], return_30d: Optional[float],
) -> float:
    """Normalized blend of 5d and 30d returns, clamped to [-1.0, +1.0].

    The 30-day window carries more weight (0.6) because it's less noisy;
    the 5-day (0.4) keeps recent moves visible.
    """
    parts: list[tuple[float, float]] = []
    if return_5d is not None:
        parts.append((0.4, _normalize_return(return_5d)))
    if return_30d is not None:
        parts.append((0.6, _normalize_return(return_30d)))
    if not parts:
        return 0.0
    total_weight = sum(w for w, _ in parts)
    score = sum(w * v for w, v in parts) / total_weight
    return max(-1.0, min(1.0, score))


def _normalize_return(pct: float) -> float:
    """Map a percentage-point return onto [-1, +1] via ``_MOMENTUM_CAP_PP``."""
    if pct != pct:  # NaN guard
        return 0.0
    scaled = pct / _MOMENTUM_CAP_PP
    return max(-1.0, min(1.0, scaled))


def _classify_volatility(vol: Optional[float]) -> str:
    if vol is None:
        return "medium"
    if vol <= _VOL_LOW_MAX:
        return "low"
    if vol <= _VOL_MEDIUM_MAX:
        return "medium"
    return "high"


def _classify_rs(rs_pp: float) -> str:
    if rs_pp >= _RS_BAND_PP:
        return "outperforming"
    if rs_pp <= -_RS_BAND_PP:
        return "underperforming"
    return "inline"


# ── Small helpers ───────────────────────────────────────────────────────────


def _to_float_or_none(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN guard
        return None
    return f
