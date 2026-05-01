"""Tests for Intel v2 — deterministic thesis score engine.

Coverage:
1. Fully-populated strong company returns READY with HIGH or MEDIUM conviction
2. Missing major fields returns PARTIAL and lists inputs_missing
3. Severe missing data returns INSUFFICIENT_DATA with no published conviction
4. Conviction blend uses the exact specified weights
5. Valuation score higher means cheaper / more attractive
6. Risk score higher means safer, not riskier
7. Missing optional gaap_nongaap_gap does not crash
8. No score exceeds 100 or drops below 0
9. Same inputs always return same outputs (determinism)
10. Momentum accepts precomputed values only and does not fetch data
"""

from __future__ import annotations

import pytest

from app.services.intelligence.thesis_engine import (
    CONVICTION_HIGH_MIN,
    CONVICTION_MEDIUM_MIN,
    MIN_CONVICTION_QUALITY,
    MIN_SUBSCORE_QUALITY,
    WEIGHT_GROWTH,
    WEIGHT_MOMENTUM,
    WEIGHT_QUALITY,
    WEIGHT_RISK,
    WEIGHT_VALUATION,
    score_thesis,
    _score_quality,
    _score_valuation,
    _score_growth,
    _score_risk,
    _score_momentum,
    _blend_conviction,
)
from app.services.intelligence.score_schema import (
    ConvictionBand,
    ScoreStatus,
)


# ── Fixture helpers ───────────────────────────────────────────────────────────

def _strong_quality() -> dict:
    """High-quality company: great ROIC, margins, low leverage."""
    return {
        "roic_ttm":             0.22,    # 22% — excellent
        "gross_margin":         0.72,    # 72%
        "fcf_margin":           0.26,    # 26%
        "fcf_to_net_income":    1.20,
        "net_debt_to_ebitda":   0.5,
        "interest_coverage":    18.0,
        "share_count_delta_3y": -0.06,   # buybacks
    }


def _cheap_valuation() -> dict:
    """Cheap stock: low multiples, high yield, discount to peers."""
    return {
        "ps_ttm":               4.0,
        "ps_forward":           3.5,
        "p_fcf":                15.0,
        "ev_ebitda":            12.0,
        "peg":                  1.0,
        "fcf_yield":            0.06,
        "forward_pe":           18.0,
        "trailing_pe":          22.0,
        "peer_ps_median":       8.0,     # ticker at big discount to peers
        "peer_ev_ebitda_median": 18.0,
        "own_5y_ps_median":     9.0,     # at discount to own history
    }


def _strong_growth() -> dict:
    return {
        "revenue_cagr_3y":            0.25,
        "revenue_yoy":                0.28,
        "fcf_cagr_3y":                0.30,
        "gross_profit_yoy":           0.26,
        "forward_revenue_growth_est": 0.22,
    }


def _low_risk() -> dict:
    """Safe company: no concentration, no guidance cuts, buybacks, low beta."""
    return {
        "customer_concentration_flag": 0,
        "guidance_cut_count_4q":       0,
        "insider_net_selling_6m":      0.005,   # net buying
        "net_debt_to_ebitda":          0.5,
        "beta":                        0.80,
        "max_drawdown_1y":             -0.10,
        "gaap_nongaap_gap":            0.02,
    }


def _positive_momentum() -> dict:
    return {
        "relative_strength_vs_spy": 8.0,
        "trend_regime_score":       75.0,
        "return_5d":                0.03,
        "return_30d":               0.09,
        "sma_20_50_signal":         1,
    }


def _full_inputs() -> dict:
    return {
        **_strong_quality(),
        **_cheap_valuation(),
        **_strong_growth(),
        **_low_risk(),
        **_positive_momentum(),
    }


# ── 1. Fully populated strong company → READY + HIGH/MEDIUM conviction ────────

class TestFullyPopulated:
    def test_status_is_ready(self):
        result = score_thesis("ACME", _full_inputs())
        assert result.status == ScoreStatus.READY

    def test_conviction_is_high_or_medium(self):
        result = score_thesis("ACME", _full_inputs())
        assert result.conviction_band in {ConvictionBand.HIGH, ConvictionBand.MEDIUM}

    def test_conviction_score_is_published(self):
        result = score_thesis("ACME", _full_inputs())
        assert result.conviction_score is not None

    def test_no_inputs_missing_when_fully_populated(self):
        result = score_thesis("ACME", _full_inputs())
        assert result.inputs_missing == []

    def test_all_subscores_published(self):
        result = score_thesis("ACME", _full_inputs())
        for name in ("quality", "valuation", "growth", "risk", "momentum"):
            ss = getattr(result, name)
            assert ss.published, f"{name} should be published"


# ── 2. Missing major fields → PARTIAL + inputs_missing populated ──────────────

class TestPartialData:
    def test_missing_valuation_marks_partial(self):
        inputs = {**_strong_quality(), **_strong_growth(), **_low_risk(), **_positive_momentum()}
        # valuation entirely absent
        result = score_thesis("ACME", inputs)
        assert result.status in {ScoreStatus.PARTIAL, ScoreStatus.INSUFFICIENT_DATA}

    def test_inputs_missing_lists_absent_fields(self):
        inputs = {**_strong_quality(), **_strong_growth()}
        result = score_thesis("ACME", inputs)
        # Valuation, risk, momentum inputs should appear in missing
        assert len(result.inputs_missing) > 0

    def test_missing_fields_appear_in_correct_subscore(self):
        inputs = _full_inputs()
        del inputs["roic_ttm"]
        del inputs["gross_margin"]
        result = score_thesis("ACME", inputs)
        assert "roic_ttm" in result.quality.inputs_missing
        assert "gross_margin" in result.quality.inputs_missing

    def test_present_fields_appear_in_inputs_used(self):
        inputs = _full_inputs()
        result = score_thesis("ACME", inputs)
        assert "roic_ttm" in result.inputs_used
        assert "fcf_yield" in result.inputs_used

    def test_partial_valuation_still_scores_standalone_metrics(self):
        """Standalone valuation inputs score even when peer/history data missing."""
        inputs = {
            "ps_ttm": 5.0,
            "p_fcf": 18.0,
            "ev_ebitda": 14.0,
            **_strong_quality(),
            **_strong_growth(),
            **_low_risk(),
            **_positive_momentum(),
        }
        result = score_thesis("ACME", inputs)
        assert result.valuation.score > 0


# ── 3. Severe missing data → INSUFFICIENT_DATA + no conviction ────────────────

class TestInsufficientData:
    def test_empty_inputs_gives_insufficient_data(self):
        result = score_thesis("ACME", {})
        assert result.status == ScoreStatus.INSUFFICIENT_DATA

    def test_empty_inputs_conviction_is_none(self):
        result = score_thesis("ACME", {})
        assert result.conviction_score is None

    def test_empty_inputs_conviction_band_is_insufficient(self):
        result = score_thesis("ACME", {})
        assert result.conviction_band == ConvictionBand.INSUFFICIENT_DATA

    def test_two_major_scores_below_threshold_gives_insufficient(self):
        # Only momentum and quality present (partial); valuation + growth + risk empty
        inputs = {
            "roic_ttm": 0.15,          # quality only partial
            **_positive_momentum(),
        }
        result = score_thesis("ACME", inputs)
        assert result.status == ScoreStatus.INSUFFICIENT_DATA

    def test_insufficient_data_all_inputs_listed_in_missing(self):
        result = score_thesis("ACME", {})
        # Every defined input should appear in inputs_missing
        assert "roic_ttm" in result.inputs_missing
        assert "ps_ttm" in result.inputs_missing
        assert "revenue_cagr_3y" in result.inputs_missing
        assert "beta" in result.inputs_missing
        assert "trend_regime_score" in result.inputs_missing


# ── 4. Conviction blend uses exact specified weights ──────────────────────────

class TestConvictionWeights:
    def test_blend_uses_specified_weights_when_all_published(self):
        """When all subscores are published, blend = weighted average with
        the exact specified weight constants."""
        full = _full_inputs()
        result = score_thesis("ACME", full)

        q  = result.quality.score
        v  = result.valuation.score
        g  = result.growth.score
        r  = result.risk.score
        m  = result.momentum.score

        expected = (
            q * WEIGHT_QUALITY +
            v * WEIGHT_VALUATION +
            g * WEIGHT_GROWTH +
            r * WEIGHT_RISK +
            m * WEIGHT_MOMENTUM
        )
        # Weights sum to 1.0, so normaliser = 1 → blend = expected directly
        assert result.conviction_score == pytest.approx(expected, abs=0.5)

    def test_weights_sum_to_one(self):
        total = WEIGHT_QUALITY + WEIGHT_VALUATION + WEIGHT_GROWTH + WEIGHT_RISK + WEIGHT_MOMENTUM
        assert total == pytest.approx(1.0, abs=1e-9)

    def test_quality_weight_is_0_30(self):
        assert WEIGHT_QUALITY == pytest.approx(0.30)

    def test_valuation_weight_is_0_25(self):
        assert WEIGHT_VALUATION == pytest.approx(0.25)

    def test_risk_weight_is_0_20(self):
        assert WEIGHT_RISK == pytest.approx(0.20)


# ── 5. Valuation score: higher = cheaper / more attractive ───────────────────

class TestValuationDirection:
    def test_low_multiples_score_higher_than_high_multiples(self):
        cheap = _score_valuation(lambda k: {
            "ps_ttm": 3.0, "p_fcf": 12.0, "ev_ebitda": 9.0, "fcf_yield": 0.07,
            "forward_pe": 14.0,
        }.get(k))
        expensive = _score_valuation(lambda k: {
            "ps_ttm": 18.0, "p_fcf": 48.0, "ev_ebitda": 28.0, "fcf_yield": 0.01,
            "forward_pe": 38.0,
        }.get(k))
        assert cheap.score > expensive.score

    def test_discount_to_peers_boosts_score(self):
        at_premium = _score_valuation(lambda k: {
            "ps_ttm": 15.0, "peer_ps_median": 8.0,
        }.get(k))
        at_discount = _score_valuation(lambda k: {
            "ps_ttm": 4.0, "peer_ps_median": 8.0,
        }.get(k))
        assert at_discount.score > at_premium.score

    def test_high_fcf_yield_boosts_score(self):
        high_yield = _score_valuation(lambda k: {"fcf_yield": 0.07}.get(k))
        low_yield  = _score_valuation(lambda k: {"fcf_yield": 0.01}.get(k))
        assert high_yield.score > low_yield.score


# ── 6. Risk score: higher = safer ────────────────────────────────────────────

class TestRiskDirection:
    def test_safe_company_scores_higher_than_risky(self):
        safe = _score_risk(lambda k: {
            "customer_concentration_flag": 0,
            "guidance_cut_count_4q":       0,
            "beta":                        0.7,
            "max_drawdown_1y":            -0.08,
            "net_debt_to_ebitda":          0.5,
            "insider_net_selling_6m":      0.005,
        }.get(k))
        risky = _score_risk(lambda k: {
            "customer_concentration_flag": 1,
            "guidance_cut_count_4q":       3,
            "beta":                        1.8,
            "max_drawdown_1y":            -0.42,
            "net_debt_to_ebitda":          3.5,
            "insider_net_selling_6m":     -0.015,
        }.get(k))
        assert safe.score > risky.score

    def test_no_guidance_cuts_safer_than_many(self):
        no_cuts   = _score_risk(lambda k: {"guidance_cut_count_4q": 0}.get(k))
        many_cuts = _score_risk(lambda k: {"guidance_cut_count_4q": 4}.get(k))
        assert no_cuts.score > many_cuts.score

    def test_low_beta_safer_than_high_beta(self):
        low_b  = _score_risk(lambda k: {"beta": 0.6}.get(k))
        high_b = _score_risk(lambda k: {"beta": 1.9}.get(k))
        assert low_b.score > high_b.score

    def test_net_cash_safer_than_leveraged(self):
        net_cash   = _score_risk(lambda k: {"net_debt_to_ebitda": -0.5}.get(k))
        leveraged  = _score_risk(lambda k: {"net_debt_to_ebitda":  3.8}.get(k))
        assert net_cash.score > leveraged.score


# ── 7. Missing gaap_nongaap_gap does not crash ───────────────────────────────

class TestOptionalGaapGap:
    def test_missing_gaap_gap_does_not_crash(self):
        inputs = {k: v for k, v in _full_inputs().items() if k != "gaap_nongaap_gap"}
        result = score_thesis("ACME", inputs)
        assert result is not None

    def test_missing_gaap_gap_appears_in_risk_missing(self):
        inputs = {k: v for k, v in _full_inputs().items() if k != "gaap_nongaap_gap"}
        result = score_thesis("ACME", inputs)
        assert "gaap_nongaap_gap" in result.risk.inputs_missing

    def test_gaap_gap_present_reduces_risk_score(self):
        """Large GAAP/non-GAAP gap is a risk signal; score should be lower."""
        no_gap   = _score_risk(lambda k: {"gaap_nongaap_gap": 0.0}.get(k))
        big_gap  = _score_risk(lambda k: {"gaap_nongaap_gap": 0.28}.get(k))
        assert no_gap.score > big_gap.score

    def test_missing_gaap_gap_risk_subscore_still_published_with_full_data(self):
        """Risk subscore should still publish if other risk inputs are present."""
        inputs = {k: v for k, v in {**_full_inputs()}.items() if k != "gaap_nongaap_gap"}
        result = score_thesis("ACME", inputs)
        assert result.risk.published


# ── 8. No score exceeds 100 or drops below 0 ─────────────────────────────────

class TestBoundsClamping:
    def test_extreme_positive_inputs_do_not_exceed_100(self):
        extreme = {
            "roic_ttm":             1.0,     # 100% ROIC — far beyond range
            "gross_margin":         1.0,
            "fcf_margin":           1.0,
            "fcf_to_net_income":    5.0,
            "net_debt_to_ebitda":  -10.0,    # massively net cash
            "interest_coverage":   200.0,
            "share_count_delta_3y": -0.50,
            "revenue_cagr_3y":      2.0,
            "revenue_yoy":          2.0,
            "fcf_cagr_3y":          2.0,
            "gross_profit_yoy":     2.0,
            "forward_revenue_growth_est": 2.0,
            "fcf_yield":            1.0,
            "beta":                 0.01,
            "max_drawdown_1y":      0.0,
            "guidance_cut_count_4q": 0,
            "customer_concentration_flag": 0,
            "insider_net_selling_6m": 0.10,
            "relative_strength_vs_spy": 100.0,
            "trend_regime_score":   200.0,
            "return_5d":            1.0,
            "return_30d":           1.0,
            "sma_20_50_signal":     1,
        }
        result = score_thesis("BULL", extreme)
        for name in ("quality", "valuation", "growth", "risk", "momentum"):
            ss = getattr(result, name)
            assert ss.score <= 100.0, f"{name}.score > 100"
            assert ss.score >= 0.0,   f"{name}.score < 0"
        if result.conviction_score is not None:
            assert result.conviction_score <= 100.0
            assert result.conviction_score >= 0.0

    def test_extreme_negative_inputs_do_not_go_below_0(self):
        terrible = {
            "roic_ttm":             -2.0,
            "gross_margin":         -1.0,
            "fcf_margin":           -1.0,
            "fcf_to_net_income":   -5.0,
            "net_debt_to_ebitda":   20.0,
            "interest_coverage":    0.0,
            "share_count_delta_3y": 2.0,
            "revenue_cagr_3y":     -1.0,
            "revenue_yoy":         -1.0,
            "beta":                 5.0,
            "max_drawdown_1y":     -2.0,
            "guidance_cut_count_4q": 10,
            "customer_concentration_flag": 1,
            "insider_net_selling_6m": -0.10,
            "ps_ttm":               200.0,
            "p_fcf":                500.0,
            "relative_strength_vs_spy": -100.0,
            "return_30d":          -1.0,
            "sma_20_50_signal":    -1,
        }
        result = score_thesis("BEAR", terrible)
        for name in ("quality", "valuation", "growth", "risk", "momentum"):
            ss = getattr(result, name)
            assert ss.score >= 0.0, f"{name}.score < 0"
            assert ss.score <= 100.0, f"{name}.score > 100"


# ── 9. Determinism: same inputs → same outputs ───────────────────────────────

class TestDeterminism:
    def test_same_inputs_same_outputs(self):
        inputs = _full_inputs()
        result_a = score_thesis("ACME", inputs)
        result_b = score_thesis("ACME", inputs)

        assert result_a.conviction_score == result_b.conviction_score
        assert result_a.status == result_b.status
        assert result_a.quality.score == result_b.quality.score
        assert result_a.valuation.score == result_b.valuation.score
        assert result_a.risk.score == result_b.risk.score

    def test_different_ticker_same_inputs_same_scores(self):
        """Ticker symbol is provenance only and must not affect scores."""
        inputs = _full_inputs()
        r1 = score_thesis("NVDA", inputs)
        r2 = score_thesis("MSFT", inputs)
        assert r1.conviction_score == r2.conviction_score
        assert r1.quality.score == r2.quality.score


# ── 10. Momentum: precomputed values only, no data fetching ──────────────────

class TestMomentumPrecomputed:
    def test_momentum_scores_from_precomputed_values(self):
        result = _score_momentum(lambda k: {
            "relative_strength_vs_spy": 5.0,
            "trend_regime_score":       60.0,
            "return_5d":                0.02,
            "return_30d":               0.07,
            "sma_20_50_signal":         1,
        }.get(k))
        assert result.published
        assert 0.0 <= result.score <= 100.0

    def test_sma_signal_minus1_gives_lowest_momentum_contribution(self):
        bearish = _score_momentum(lambda k: {"sma_20_50_signal": -1}.get(k))
        bullish = _score_momentum(lambda k: {"sma_20_50_signal":  1}.get(k))
        assert bullish.score > bearish.score

    def test_negative_momentum_scores_lower_than_positive(self):
        negative = _score_momentum(lambda k: {
            "relative_strength_vs_spy": -15.0,
            "return_30d":               -0.18,
            "sma_20_50_signal":         -1,
        }.get(k))
        positive = _score_momentum(lambda k: {
            "relative_strength_vs_spy": 15.0,
            "return_30d":               0.18,
            "sma_20_50_signal":         1,
        }.get(k))
        assert positive.score > negative.score

    def test_all_momentum_inputs_tracked(self):
        result = _score_momentum(lambda k: {
            "relative_strength_vs_spy": 3.0,
            "trend_regime_score":       55.0,
        }.get(k))
        assert "return_5d" in result.inputs_missing
        assert "return_30d" in result.inputs_missing
        assert "sma_20_50_signal" in result.inputs_missing

    def test_momentum_subscore_data_quality_tracks_presence(self):
        full = _score_momentum(lambda k: _positive_momentum().get(k))
        partial = _score_momentum(lambda k: {"relative_strength_vs_spy": 5.0}.get(k))
        assert full.data_quality > partial.data_quality
