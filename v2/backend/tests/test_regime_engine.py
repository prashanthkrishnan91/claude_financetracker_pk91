"""Tests for the market regime detection engine."""

from __future__ import annotations

import pytest

from app.services.regime_engine import (
    detect_market_regime_from_bundle,
    RegimeOutput,
)


def _bundle(**overrides):
    base = {
        "last": 500.0,
        "sma20": 495.0,
        "sma50": 485.0,
        "pct_5d": 1.0,
        "pct_30d": 4.0,
        "volatility_30d": 0.15,
        "high_3mo": 510.0,
        "n_bars": 60,
    }
    base.update(overrides)
    return base


class TestRegimeFromBundle:
    def test_bull_regime(self):
        out = detect_market_regime_from_bundle(_bundle(
            last=520.0, sma20=505.0, sma50=490.0,
            pct_5d=2.0, pct_30d=8.0,
            volatility_30d=0.13, high_3mo=525.0,
        ))
        assert out.regime_label == "bull"
        assert out.regime_score >= 65
        assert out.data_quality == "high"
        assert any("50D MA" in r or "30-day" in r for r in out.regime_reasons)

    def test_neutral_regime_mixed_signals(self):
        out = detect_market_regime_from_bundle(_bundle(
            last=498.0, sma20=499.0, sma50=495.0,
            pct_5d=0.2, pct_30d=1.0,
            volatility_30d=0.18, high_3mo=505.0,
        ))
        assert out.regime_label == "neutral"
        assert 35 < out.regime_score < 65

    def test_risk_off_drawdown_and_volatility(self):
        out = detect_market_regime_from_bundle(_bundle(
            last=430.0, sma20=470.0, sma50=485.0,
            pct_5d=-3.0, pct_30d=-9.0,
            volatility_30d=0.40, high_3mo=510.0,
        ))
        assert out.regime_label == "risk_off"
        assert out.regime_score <= 35
        # drawdown ≈ -15.7% should be reported
        assert any("drawdown" in r.lower() for r in out.regime_reasons)

    def test_risk_off_below_long_term_trend(self):
        out = detect_market_regime_from_bundle(_bundle(
            last=470.0, sma20=480.0, sma50=495.0,
            pct_5d=-2.5, pct_30d=-6.0,
            volatility_30d=0.32, high_3mo=505.0,
        ))
        assert out.regime_label == "risk_off"

    def test_empty_bundle_falls_back_to_neutral(self):
        out = detect_market_regime_from_bundle({})
        assert out.regime_label == "neutral"
        assert out.regime_score == 50.0
        assert out.data_quality == "low"
        assert "unavailable" in out.regime_reasons[0].lower()

    def test_none_bundle_does_not_raise(self):
        out = detect_market_regime_from_bundle(None)
        assert out.regime_label == "neutral"
        assert out.data_quality == "low"

    def test_partial_bundle_yields_low_or_medium_quality(self):
        out = detect_market_regime_from_bundle({
            "last": 500.0, "sma50": 480.0,  # only 1 derived signal
        })
        assert out.data_quality in {"low", "medium"}
        # never raises, label still well-defined
        assert out.regime_label in {"bull", "neutral", "risk_off"}

    def test_score_clamped_to_0_100(self):
        # Wildly negative inputs should still produce a valid score.
        out = detect_market_regime_from_bundle(_bundle(
            last=200.0, sma20=400.0, sma50=480.0,
            pct_5d=-50.0, pct_30d=-50.0,
            volatility_30d=0.80, high_3mo=520.0,
        ))
        assert 0.0 <= out.regime_score <= 100.0
        assert out.regime_label == "risk_off"

    def test_signals_passed_through(self):
        out = detect_market_regime_from_bundle(_bundle(
            last=500.0, sma50=495.0, pct_30d=2.0, volatility_30d=0.18, high_3mo=505.0,
        ))
        assert out.spy_pct_30d == 2.0
        assert out.spy_vs_sma50 is not None
        assert out.drawdown_pct is not None
        assert out.realized_vol_30d == 0.18
