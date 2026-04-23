"""Phase 2 — feature-engine acceptance tests.

Gates covered here (see tasks/todo.md → Phase 2 acceptance):
  1. Every snapshot produces a FeatureSet.
  2. At least 3 distinct `trend_regime` values appear across a mixed portfolio.
  3. Features differ across tickers (momentum_score, vol regime, RS differ).
  4. data_quality_score propagates from the snapshot.
  5. Missing benchmark falls back to absolute momentum without crashing.
"""

from __future__ import annotations

import pytest

from app.services.intelligence.feature_engine import (
    FeatureSet,
    build_features,
    _classify_trend,
    _classify_volatility,
    _classify_rs,
    _momentum_score,
)
from app.services.intelligence.market_snapshot import (
    MarketSnapshot,
    build_market_snapshots,
)


# ── Classifier unit tests ───────────────────────────────────────────────────


def test_trend_regime_uptrend():
    assert _classify_trend(price=110, sma20=105, sma50=100) == "uptrend"


def test_trend_regime_downtrend():
    assert _classify_trend(price=90, sma20=95, sma50=100) == "downtrend"


def test_trend_regime_range_on_mixed_ordering():
    assert _classify_trend(price=100, sma20=102, sma50=98) == "range"


def test_trend_regime_range_when_missing():
    assert _classify_trend(price=None, sma20=100, sma50=95) == "range"
    assert _classify_trend(price=100, sma20=None, sma50=95) == "range"


def test_volatility_regime_boundaries():
    assert _classify_volatility(0.10) == "low"
    assert _classify_volatility(0.25) == "low"
    assert _classify_volatility(0.26) == "medium"
    assert _classify_volatility(0.45) == "medium"
    assert _classify_volatility(0.46) == "high"
    assert _classify_volatility(None) == "medium"


def test_relative_strength_bands():
    assert _classify_rs(5.0) == "outperforming"
    assert _classify_rs(-5.0) == "underperforming"
    assert _classify_rs(0.0) == "inline"
    assert _classify_rs(2.9) == "inline"
    assert _classify_rs(-2.9) == "inline"


def test_momentum_score_blends_and_clamps():
    # 5d=+10pp, 30d=+25pp (saturates at cap)
    score = _momentum_score(return_5d=10.0, return_30d=25.0)
    assert 0.0 < score <= 1.0
    # Negative case
    assert _momentum_score(return_5d=-10.0, return_30d=-40.0) < 0
    # Missing inputs → 0
    assert _momentum_score(return_5d=None, return_30d=None) == 0.0


# ── Builder end-to-end ──────────────────────────────────────────────────────


def _snap(ticker, **overrides) -> MarketSnapshot:
    defaults = dict(
        ticker=ticker,
        as_of="2026-04-23T00:00:00+00:00",
        price=100.0,
        price_source="live",
        return_5d=0.0,
        return_30d=0.0,
        volatility_30d=0.25,
        sector="Technology",
        industry="Software",
        category="Tech",
        data_quality_score=0.8,
        missing_fields=[],
    )
    defaults.update(overrides)
    return MarketSnapshot(**defaults)


def _bundle_with_smas(rows: dict[str, dict]) -> dict:
    """Helper — build a bundle-like dict carrying price_action entries."""
    return {"price_action": rows}


def test_every_snapshot_produces_a_feature_set():
    """Gate #1 — every input snapshot must have a FeatureSet in the output."""
    snapshots = {
        "AAPL": _snap("AAPL", price=150, return_5d=2, return_30d=8,
                      volatility_30d=0.22),
        "TSLA": _snap("TSLA", price=240, return_5d=-1, return_30d=-12,
                      volatility_30d=0.55, sector="Auto", category="Auto"),
        "BTC":  _snap("BTC",  price=60000, return_5d=5, return_30d=25,
                      volatility_30d=0.80, sector="", category="Crypto"),
    }
    bundle = _bundle_with_smas({
        "AAPL": {"sma20": 148, "sma50": 140},
        "TSLA": {"sma20": 250, "sma50": 260},
        "BTC":  {"sma20": 58000, "sma50": 55000},
    })
    features = build_features(snapshots, bundle=bundle, benchmark={"pct_30d": 5.0})

    assert set(features.keys()) == {"AAPL", "TSLA", "BTC"}
    assert all(isinstance(f, FeatureSet) for f in features.values())


def test_three_distinct_regimes_across_mixed_portfolio():
    """Gate #2 — at least three different trend_regime values appear."""
    snapshots = {
        "AAPL":  _snap("AAPL",  price=150, return_30d=8),
        "TSLA":  _snap("TSLA",  price=240, return_30d=-12),
        "SLOWCO": _snap("SLOWCO", price=100, return_30d=1),
    }
    bundle = _bundle_with_smas({
        "AAPL":   {"sma20": 148, "sma50": 140},   # uptrend
        "TSLA":   {"sma20": 250, "sma50": 260},   # downtrend
        "SLOWCO": {"sma20": 102, "sma50": 98},    # mixed → range
    })
    features = build_features(snapshots, bundle=bundle, benchmark={"pct_30d": 5.0})
    regimes = {f.trend_regime for f in features.values()}
    assert regimes == {"uptrend", "downtrend", "range"}


def test_features_differ_across_tickers():
    """Gate #3 — momentum, vol regime, and RS label must vary."""
    snapshots = {
        "A": _snap("A", return_5d=3, return_30d=10, volatility_30d=0.15),
        "B": _snap("B", return_5d=-2, return_30d=-8, volatility_30d=0.50),
        "C": _snap("C", return_5d=0, return_30d=1, volatility_30d=0.35),
    }
    bundle = _bundle_with_smas({
        "A": {"sma20": 95, "sma50": 90},
        "B": {"sma20": 105, "sma50": 110},
        "C": {"sma20": 100, "sma50": 100},
    })
    features = build_features(snapshots, bundle=bundle, benchmark={"pct_30d": 5.0})
    assert len({round(f.momentum_score, 3) for f in features.values()}) == 3
    assert len({f.volatility_regime for f in features.values()}) >= 2
    assert len({f.relative_strength_label for f in features.values()}) >= 2


def test_data_quality_score_propagates():
    """Gate #4 — FeatureSet.data_quality_score == MarketSnapshot.data_quality_score."""
    snapshots = {
        "HIGH": _snap("HIGH", data_quality_score=0.85,
                      missing_fields=[]),
        "LOW":  _snap("LOW",  data_quality_score=0.12,
                      missing_fields=["price", "fundamentals", "sentiment"]),
    }
    bundle = _bundle_with_smas({})
    features = build_features(snapshots, bundle=bundle, benchmark={})
    assert features["HIGH"].data_quality_score == 0.85
    assert features["LOW"].data_quality_score == 0.12
    assert features["LOW"].missing_fields == ["price", "fundamentals", "sentiment"]


def test_missing_benchmark_falls_back_to_absolute():
    """Gate #5 — no benchmark means relative_strength_30d=None, label inline."""
    snapshots = {"AAPL": _snap("AAPL", return_30d=8)}
    bundle = _bundle_with_smas({"AAPL": {"sma20": 148, "sma50": 140}})
    features = build_features(snapshots, bundle=bundle, benchmark=None)
    fs = features["AAPL"]
    assert fs.relative_strength_30d is None
    assert fs.relative_strength_label == "inline"
    assert fs.benchmark_return_30d is None


def test_relative_strength_vs_spy():
    """SPY-relative performance surfaces through the outperforming/underperforming bands."""
    snapshots = {
        "OUT":  _snap("OUT",  return_30d=12),
        "UNDR": _snap("UNDR", return_30d=-2),
        "INL":  _snap("INL",  return_30d=6),
    }
    bundle = _bundle_with_smas({})
    features = build_features(snapshots, bundle=bundle, benchmark={"pct_30d": 5.0})
    assert features["OUT"].relative_strength_label == "outperforming"
    assert features["UNDR"].relative_strength_label == "underperforming"
    assert features["INL"].relative_strength_label == "inline"
    assert features["OUT"].relative_strength_30d == 7.0
    assert features["UNDR"].relative_strength_30d == -7.0


def test_feature_set_row_shape_is_complete():
    """Persisted row includes every column the migration declares."""
    snapshots = {"AAPL": _snap("AAPL", return_30d=8, volatility_30d=0.22)}
    bundle = _bundle_with_smas({"AAPL": {"sma20": 148, "sma50": 140}})
    features = build_features(snapshots, bundle=bundle, benchmark={"pct_30d": 5.0})
    row = features["AAPL"].to_row(run_id="r1", user_id="u1")

    required = {
        "run_id", "user_id", "ticker", "as_of", "trend_regime", "sma20", "sma50",
        "price", "momentum_score", "return_5d", "return_30d",
        "volatility_regime", "volatility_30d", "benchmark_symbol",
        "benchmark_return_30d", "relative_strength_30d", "relative_strength_label",
        "sector", "industry", "category", "data_quality_score", "missing_fields",
    }
    assert required.issubset(row.keys())


# ── End-to-end from MarketSnapshot builder → FeatureSet ────────────────────


def test_features_from_live_snapshots_and_bundle():
    """Smoke: build snapshots from a bundle, then build features from them."""
    bundle = {
        "tickers": ["AAPL", "TSLA"],
        "prices": {"AAPL": 150.0, "TSLA": 240.0},
        "live_prices": {"AAPL": 150.0, "TSLA": 240.0},
        "news": {},
        "fundamentals": {
            "AAPL": {"sector": "Technology", "industry": "Hardware", "pe": 28},
            "TSLA": {"sector": "Consumer Cyclical", "industry": "Auto", "pe": 70},
        },
        "funds": {
            "AAPL": {"sector": "Technology"},
            "TSLA": {"sector": "Consumer Cyclical"},
        },
        "price_action": {
            "AAPL": {
                "pct_5d": 1.5, "pct_30d": 7.0,
                "volatility_30d": 0.22,
                "sma20": 148, "sma50": 140,
            },
            "TSLA": {
                "pct_5d": -3.0, "pct_30d": -10.0,
                "volatility_30d": 0.55,
                "sma20": 250, "sma50": 260,
            },
        },
        "macro": {"fallback": True},
        "source_status": {},
        "missing_fields": [],
        "completeness_score": 0.9,
    }
    positions = [
        {"ticker": "AAPL", "avg_cost": 140.0, "category": "Tech"},
        {"ticker": "TSLA", "avg_cost": 280.0, "category": "Auto"},
    ]
    snapshots = build_market_snapshots(
        bundle, tickers=["AAPL", "TSLA"], positions=positions,
    )
    features = build_features(
        snapshots, bundle=bundle, benchmark={"pct_30d": 4.0},
    )

    aapl = features["AAPL"]
    tsla = features["TSLA"]

    assert aapl.trend_regime == "uptrend"
    assert tsla.trend_regime == "downtrend"
    assert aapl.momentum_score > 0
    assert tsla.momentum_score < 0
    # AAPL 7.0 - SPY 4.0 = +3.0 pp → boundary outperforming per _RS_BAND_PP=3.0
    assert aapl.relative_strength_label == "outperforming"
    assert tsla.relative_strength_label == "underperforming"
    assert aapl.volatility_regime in {"low", "medium"}
    assert tsla.volatility_regime == "high"
