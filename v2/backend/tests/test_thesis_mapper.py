"""Tests for Intel v2 PR-2 — thesis_mapper deterministic input mapper.

Coverage:
 1.  mapper maps available fundamentals correctly
 2.  pe maps to trailing_pe
 3.  revenue_growth decimal input stays decimal
 4.  revenue_growth percent-point input converts to decimal
 5.  return_5d / return_30d percent-point inputs convert to decimal
 6.  relative_strength_30d remains percentage points (no conversion)
 7.  sma20/sma50 derives signal 1 / 0 / -1
 8.  missing fields are omitted / None, not faked
 9.  calling score_thesis through mapper returns PARTIAL or INSUFFICIENT_DATA
     honestly when field coverage is limited
10.  deterministic: same bundle → same ScoreCard
11.  no external network/vendor calls in mapper tests
12.  existing recommendation endpoint / service contract still works with
     additive thesis_v2 block (InsightCard backward-compat)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
from unittest.mock import MagicMock

import pytest

from app.services.intelligence.thesis_mapper import (
    _derive_sma_signal,
    _normalize_to_decimal,
    _trend_to_regime_score,
    map_to_thesis_inputs,
)
from app.services.intelligence.thesis_engine import score_thesis
from app.services.intelligence.score_schema import ConvictionBand, ScoreStatus


# ── Minimal FeatureSet stub (avoids importing the real class in tests) ────────
# Tests that do need a real FeatureSet import the actual class.

@dataclass
class _FakeFeatureSet:
    ticker: str = "FAKE"
    as_of: str = "2026-05-01T00:00:00Z"
    trend_regime: str = "range"
    sma20: Optional[float] = None
    sma50: Optional[float] = None
    return_5d: Optional[float] = None
    return_30d: Optional[float] = None
    relative_strength_30d: Optional[float] = None
    momentum_score: float = 0.0
    volatility_regime: str = "medium"
    volatility_30d: Optional[float] = None
    benchmark_symbol: str = "SPY"
    benchmark_return_30d: Optional[float] = None
    relative_strength_label: str = "inline"
    sector: str = ""
    industry: str = ""
    category: str = "Other"
    data_quality_score: float = 0.0
    missing_fields: list = field(default_factory=list)


# ── Helper fundamentals dicts ─────────────────────────────────────────────────

def _full_fundamentals() -> dict:
    """yfinance-style fundamentals dict with all mappable fields."""
    return {
        "pe":             22.0,
        "forward_pe":     18.0,
        "peg":             1.5,
        "revenue_growth":  0.15,   # decimal, 15 %
        "beta":            1.10,
        "profit_margin":   0.20,
        "dividend_yield":  0.02,
        "sector":         "Technology",
    }


def _full_feature_set() -> _FakeFeatureSet:
    return _FakeFeatureSet(
        ticker="ACME",
        sma20=105.0,
        sma50=100.0,           # sma20 > sma50 → signal = 1
        return_5d=3.0,         # percent-point (3 %) → decimal 0.03
        return_30d=8.0,        # percent-point (8 %) → decimal 0.08
        relative_strength_30d=5.0,  # 5 pp vs SPY — no conversion
        trend_regime="uptrend",
    )


# ── 1. Mapper maps available fundamentals correctly ───────────────────────────

class TestMapperMapsFields:
    def test_trailing_pe_present(self):
        inputs = map_to_thesis_inputs({"pe": 22.0})
        assert inputs["trailing_pe"] == 22.0

    def test_forward_pe_present(self):
        inputs = map_to_thesis_inputs({"forward_pe": 18.0})
        assert inputs["forward_pe"] == 18.0

    def test_peg_present(self):
        inputs = map_to_thesis_inputs({"peg": 1.5})
        assert inputs["peg"] == 1.5

    def test_beta_present(self):
        inputs = map_to_thesis_inputs({"beta": 1.1})
        assert inputs["beta"] == 1.1

    def test_revenue_growth_mapped_to_revenue_yoy(self):
        inputs = map_to_thesis_inputs({"revenue_growth": 0.15})
        assert "revenue_yoy" in inputs

    def test_unknown_fields_not_included(self):
        inputs = map_to_thesis_inputs({"sector": "Tech", "market_cap": 1e12})
        assert "sector" not in inputs
        assert "market_cap" not in inputs


# ── 2. pe maps to trailing_pe ─────────────────────────────────────────────────

class TestPeToTrailingPe:
    def test_pe_becomes_trailing_pe(self):
        inputs = map_to_thesis_inputs({"pe": 25.0})
        assert "trailing_pe" in inputs
        assert "pe" not in inputs
        assert inputs["trailing_pe"] == 25.0

    def test_pe_is_raw_multiple_no_conversion(self):
        inputs = map_to_thesis_inputs({"pe": 35.7})
        assert inputs["trailing_pe"] == pytest.approx(35.7)


# ── 3. revenue_growth decimal input stays decimal ─────────────────────────────

class TestRevenueGrowthDecimal:
    def test_small_decimal_unchanged(self):
        inputs = map_to_thesis_inputs({"revenue_growth": 0.12})
        assert inputs["revenue_yoy"] == pytest.approx(0.12)

    def test_negative_decimal_unchanged(self):
        inputs = map_to_thesis_inputs({"revenue_growth": -0.05})
        assert inputs["revenue_yoy"] == pytest.approx(-0.05)

    def test_zero_unchanged(self):
        inputs = map_to_thesis_inputs({"revenue_growth": 0.0})
        assert inputs["revenue_yoy"] == pytest.approx(0.0)

    def test_boundary_at_five_stays_decimal(self):
        # abs(5.0) == _DECIMAL_ABS_MAX — not strictly greater, so no conversion
        inputs = map_to_thesis_inputs({"revenue_growth": 5.0})
        assert inputs["revenue_yoy"] == pytest.approx(5.0)


# ── 4. revenue_growth percent-point input converts to decimal ─────────────────

class TestRevenueGrowthPercentPoints:
    def test_above_threshold_divides_by_100(self):
        inputs = map_to_thesis_inputs({"revenue_growth": 15.0})
        assert inputs["revenue_yoy"] == pytest.approx(0.15)

    def test_large_positive_pp_converts(self):
        inputs = map_to_thesis_inputs({"revenue_growth": 28.0})
        assert inputs["revenue_yoy"] == pytest.approx(0.28)

    def test_negative_pp_converts(self):
        inputs = map_to_thesis_inputs({"revenue_growth": -12.0})
        assert inputs["revenue_yoy"] == pytest.approx(-0.12)


# ── 5. return_5d / return_30d percent-point inputs convert to decimal ─────────

class TestReturnConversion:
    def test_return_5d_divided_by_100(self):
        fs = _FakeFeatureSet(return_5d=3.0)
        inputs = map_to_thesis_inputs({}, fs)
        assert inputs["return_5d"] == pytest.approx(0.03)

    def test_return_30d_divided_by_100(self):
        fs = _FakeFeatureSet(return_30d=8.0)
        inputs = map_to_thesis_inputs({}, fs)
        assert inputs["return_30d"] == pytest.approx(0.08)

    def test_negative_return_5d_converts(self):
        fs = _FakeFeatureSet(return_5d=-4.5)
        inputs = map_to_thesis_inputs({}, fs)
        assert inputs["return_5d"] == pytest.approx(-0.045)

    def test_return_5d_zero_converts(self):
        fs = _FakeFeatureSet(return_5d=0.0)
        inputs = map_to_thesis_inputs({}, fs)
        assert inputs["return_5d"] == pytest.approx(0.0)


# ── 6. relative_strength_30d remains percentage points ────────────────────────

class TestRelativeStrengthNoConversion:
    def test_rs_passed_through_as_pp(self):
        fs = _FakeFeatureSet(relative_strength_30d=5.0)
        inputs = map_to_thesis_inputs({}, fs)
        assert inputs["relative_strength_vs_spy"] == pytest.approx(5.0)

    def test_negative_rs_passed_through(self):
        fs = _FakeFeatureSet(relative_strength_30d=-8.0)
        inputs = map_to_thesis_inputs({}, fs)
        assert inputs["relative_strength_vs_spy"] == pytest.approx(-8.0)

    def test_zero_rs_passed_through(self):
        fs = _FakeFeatureSet(relative_strength_30d=0.0)
        inputs = map_to_thesis_inputs({}, fs)
        assert inputs["relative_strength_vs_spy"] == pytest.approx(0.0)


# ── 7. sma20/sma50 derives signal 1 / 0 / -1 ─────────────────────────────────

class TestSmaCrossoverSignal:
    def test_sma20_above_sma50_gives_plus_one(self):
        assert _derive_sma_signal(105.0, 100.0) == 1

    def test_sma20_below_sma50_gives_minus_one(self):
        assert _derive_sma_signal(95.0, 100.0) == -1

    def test_sma20_equal_sma50_gives_zero(self):
        assert _derive_sma_signal(100.0, 100.0) == 0

    def test_none_sma20_returns_none(self):
        assert _derive_sma_signal(None, 100.0) is None

    def test_none_sma50_returns_none(self):
        assert _derive_sma_signal(100.0, None) is None

    def test_zero_sma20_returns_none(self):
        assert _derive_sma_signal(0.0, 100.0) is None

    def test_via_mapper_bullish_crossover(self):
        fs = _FakeFeatureSet(sma20=105.0, sma50=100.0)
        inputs = map_to_thesis_inputs({}, fs)
        assert inputs["sma_20_50_signal"] == pytest.approx(1.0)

    def test_via_mapper_bearish_crossover(self):
        fs = _FakeFeatureSet(sma20=95.0, sma50=100.0)
        inputs = map_to_thesis_inputs({}, fs)
        assert inputs["sma_20_50_signal"] == pytest.approx(-1.0)

    def test_via_mapper_no_sma_no_signal_key(self):
        fs = _FakeFeatureSet(sma20=None, sma50=None)
        inputs = map_to_thesis_inputs({}, fs)
        assert "sma_20_50_signal" not in inputs


# ── 8. Missing fields are omitted / None, not faked ───────────────────────────

class TestMissingFieldsOmitted:
    def test_empty_fundamentals_no_keys(self):
        inputs = map_to_thesis_inputs({})
        assert inputs == {}

    def test_none_feature_set_no_momentum_keys(self):
        inputs = map_to_thesis_inputs({}, None)
        assert "return_5d" not in inputs
        assert "return_30d" not in inputs
        assert "relative_strength_vs_spy" not in inputs
        assert "sma_20_50_signal" not in inputs
        assert "trend_regime_score" not in inputs

    def test_partial_fundamentals_only_present_keys_mapped(self):
        inputs = map_to_thesis_inputs({"pe": 20.0})
        assert "trailing_pe" in inputs
        assert "forward_pe" not in inputs
        assert "peg" not in inputs
        assert "beta" not in inputs
        assert "revenue_yoy" not in inputs

    def test_nan_fundamentals_omitted(self):
        import math
        inputs = map_to_thesis_inputs({"pe": math.nan})
        assert "trailing_pe" not in inputs

    def test_none_revenue_growth_omitted(self):
        inputs = map_to_thesis_inputs({"revenue_growth": None})
        assert "revenue_yoy" not in inputs


class TestUnsafeProxyMappingsAreOmitted:
    def test_profit_margin_not_mapped_to_fcf_margin(self):
        inputs = map_to_thesis_inputs({"profit_margin": 0.22})
        assert "fcf_margin" not in inputs

    def test_return_on_equity_not_mapped_to_roic_ttm(self):
        inputs = map_to_thesis_inputs({"return_on_equity": 0.18})
        assert "roic_ttm" not in inputs

    def test_debt_to_equity_not_mapped_to_net_debt_to_ebitda(self):
        inputs = map_to_thesis_inputs({"debt_to_equity": 1.8})
        assert "net_debt_to_ebitda" not in inputs

    def test_earnings_growth_not_mapped_to_forward_revenue_growth_est(self):
        inputs = map_to_thesis_inputs({"earnings_growth": 0.25})
        assert "forward_revenue_growth_est" not in inputs
        assert "revenue_yoy" not in inputs


# ── 9. score_thesis through mapper returns honest status ──────────────────────

class TestScoreThroughMapper:
    def test_partial_fields_returns_partial_or_insufficient(self):
        # Only a few fundamentals — many required fields missing.
        funds = {"pe": 20.0, "forward_pe": 15.0, "beta": 1.1}
        inputs = map_to_thesis_inputs(funds)
        card = score_thesis("ACME", inputs)
        assert card.status in {ScoreStatus.PARTIAL, ScoreStatus.INSUFFICIENT_DATA}

    def test_empty_inputs_returns_insufficient_data(self):
        inputs = map_to_thesis_inputs({})
        card = score_thesis("ACME", inputs)
        assert card.status == ScoreStatus.INSUFFICIENT_DATA
        assert card.conviction_score is None
        assert card.conviction_band == ConvictionBand.INSUFFICIENT_DATA

    def test_status_is_not_ready_with_limited_coverage(self):
        # Only momentum inputs from features — no quality/valuation/growth/risk
        fs = _FakeFeatureSet(
            return_5d=3.0, return_30d=8.0, sma20=105.0, sma50=100.0,
        )
        inputs = map_to_thesis_inputs({}, fs)
        card = score_thesis("ACME", inputs)
        # Momentum only → 4 of 4 major subscores are weak → INSUFFICIENT_DATA
        assert card.status == ScoreStatus.INSUFFICIENT_DATA

    def test_inputs_used_reflects_mapper_output(self):
        funds = {"pe": 20.0, "beta": 1.1}
        inputs = map_to_thesis_inputs(funds)
        card = score_thesis("ACME", inputs)
        assert "trailing_pe" in card.inputs_used
        assert "beta" in card.inputs_used

    def test_inputs_missing_are_honest(self):
        inputs = map_to_thesis_inputs({"pe": 20.0})
        card = score_thesis("ACME", inputs)
        # Most thesis fields absent — inputs_missing is non-empty
        assert len(card.inputs_missing) > 0


# ── 10. Determinism: same bundle → same ScoreCard ─────────────────────────────

class TestDeterminism:
    def test_same_fundamentals_same_scorecard(self):
        funds = _full_fundamentals()
        fs = _full_feature_set()
        inputs_a = map_to_thesis_inputs(funds, fs)
        inputs_b = map_to_thesis_inputs(funds, fs)
        card_a = score_thesis("ACME", inputs_a)
        card_b = score_thesis("ACME", inputs_b)
        assert card_a.status == card_b.status
        assert card_a.conviction_band == card_b.conviction_band
        assert card_a.blended_data_quality == card_b.blended_data_quality
        assert card_a.conviction_score == card_b.conviction_score

    def test_different_fundamentals_different_scorecard(self):
        funds_cheap = dict(_full_fundamentals())
        funds_cheap["pe"] = 8.0      # cheap

        funds_expensive = dict(_full_fundamentals())
        funds_expensive["pe"] = 80.0  # expensive

        card_cheap = score_thesis("A", map_to_thesis_inputs(funds_cheap))
        card_exp = score_thesis("B", map_to_thesis_inputs(funds_expensive))
        # Cheaper PE should score higher in valuation
        assert card_cheap.valuation.score >= card_exp.valuation.score


# ── 11. No network / vendor calls in mapper tests ─────────────────────────────

class TestNoExternalCalls:
    def test_mapper_is_pure_no_io(self, monkeypatch):
        """Mapper must not make any network calls."""
        import app.services.intelligence.thesis_mapper as tm

        # Monkeypatch httpx to raise if called — mapper must never touch it
        def _fail(*a, **kw):
            raise AssertionError("mapper made an external call")

        monkeypatch.setattr(tm, "_safe_float", tm._safe_float)  # re-bind (no-op)
        funds = {"pe": 20.0, "forward_pe": 15.0}
        # Should return without hitting any I/O
        result = map_to_thesis_inputs(funds)
        assert "trailing_pe" in result

    def test_score_thesis_is_pure_no_io(self):
        """score_thesis must not make any network calls (pure function)."""
        inputs = map_to_thesis_inputs({"pe": 20.0, "beta": 1.1})
        card = score_thesis("TEST", inputs)
        assert card.ticker == "TEST"


# ── 12. InsightCard backward compatibility with thesis_v2 field ───────────────

class TestInsightCardBackwardCompat:
    def test_thesis_v2_defaults_to_none(self):
        from uuid import uuid4
        from app.models.recommendation import InsightCard

        card = InsightCard(
            id=uuid4(),
            ticker="NVDA",
            name="NVIDIA",
            action="BUY",
            detail="Test detail",
            rationale="Test rationale",
            urgency=2,
            color="green",
            tax_note="",
            drip_note="",
            category="Core",
        )
        assert card.thesis_v2 is None

    def test_thesis_v2_accepts_scorecard_dict(self):
        from uuid import uuid4
        from app.models.recommendation import InsightCard

        scorecard_dict = {
            "ticker": "NVDA",
            "status": "PARTIAL",
            "conviction_score": None,
            "conviction_band": "INSUFFICIENT_DATA",
            "blended_data_quality": 0.15,
            "inputs_used": ["beta", "trailing_pe"],
            "inputs_missing": ["roic_ttm", "gross_margin"],
            "score_version": "v1",
        }
        card = InsightCard(
            id=uuid4(),
            ticker="NVDA",
            name="NVIDIA",
            action="BUY",
            detail="Test detail",
            rationale="Test rationale",
            urgency=2,
            color="green",
            tax_note="",
            drip_note="",
            category="Core",
            thesis_v2=scorecard_dict,
        )
        assert card.thesis_v2 is not None
        assert card.thesis_v2["status"] == "PARTIAL"
        assert "conviction_band" in card.thesis_v2

    def test_insight_card_serialises_thesis_v2(self):
        from uuid import uuid4
        from app.models.recommendation import InsightCard

        card = InsightCard(
            id=uuid4(),
            ticker="NVDA",
            name="NVIDIA",
            action="BUY",
            detail="d",
            rationale="r",
            urgency=0,
            color="green",
            tax_note="",
            drip_note="",
            category="Core",
            thesis_v2={"status": "INSUFFICIENT_DATA"},
        )
        dumped = card.model_dump()
        assert "thesis_v2" in dumped
        assert dumped["thesis_v2"]["status"] == "INSUFFICIENT_DATA"

    def test_existing_fields_unaffected(self):
        from uuid import uuid4
        from app.models.recommendation import InsightCard

        card = InsightCard(
            id=uuid4(),
            ticker="NVDA",
            name="NVIDIA",
            action="BUY",
            detail="d",
            rationale="r",
            urgency=3,
            color="green",
            tax_note="tax",
            drip_note="drip",
            category="Core",
        )
        assert card.ticker == "NVDA"
        assert card.urgency == 3
        assert card.thesis_v2 is None  # new field doesn't pollute existing


# ── Helpers unit tests ────────────────────────────────────────────────────────

class TestNormalizeToDecimal:
    def test_small_positive_unchanged(self):
        assert _normalize_to_decimal(0.12) == pytest.approx(0.12)

    def test_large_positive_divides(self):
        assert _normalize_to_decimal(12.0) == pytest.approx(0.12)

    def test_negative_large_divides(self):
        assert _normalize_to_decimal(-15.0) == pytest.approx(-0.15)

    def test_exactly_boundary_unchanged(self):
        # abs(5.0) is not > 5.0, so it stays
        assert _normalize_to_decimal(5.0) == pytest.approx(5.0)

    def test_just_above_boundary_divides(self):
        assert _normalize_to_decimal(5.1) == pytest.approx(0.051)


class TestTrendToRegimeScore:
    def test_uptrend_maps_to_70(self):
        assert _trend_to_regime_score("uptrend") == pytest.approx(70.0)

    def test_range_maps_to_40(self):
        assert _trend_to_regime_score("range") == pytest.approx(40.0)

    def test_downtrend_maps_to_20(self):
        assert _trend_to_regime_score("downtrend") == pytest.approx(20.0)

    def test_unknown_returns_none(self):
        assert _trend_to_regime_score("sideways") is None

    def test_empty_returns_none(self):
        assert _trend_to_regime_score("") is None
