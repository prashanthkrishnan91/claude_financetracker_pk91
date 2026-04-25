"""Tests for the adaptive deployment / staging engine."""

from __future__ import annotations

import pytest

from app.services.adaptive_deployment import adapt_allocation_plan
from app.services.allocation_engine import AllocationItem, Holding
from app.services.regime_engine import RegimeOutput


def _alloc(
    ticker: str,
    *,
    amount: float,
    current_weight: float = 0.0,
    after_weight: float | None = None,
    category: str = "Core",
    score: float = 4.0,
) -> AllocationItem:
    return AllocationItem(
        ticker=ticker,
        action="BUY",
        amount=amount,
        current_weight=current_weight,
        after_weight=after_weight if after_weight is not None else current_weight + 2.0,
        target_weight=20.0,
        conviction_level="HIGH",
        conviction_score=0.8,
        confidence=0.8,
        score=score,
        reason="strong setup",
        category=category,
    )


def _regime(label: str = "neutral", score: float = 55.0,
            data_quality: str = "high", reasons: list[str] | None = None) -> RegimeOutput:
    return RegimeOutput(
        regime_label=label,  # type: ignore[arg-type]
        regime_score=score,
        regime_reasons=reasons or [f"{label} regime test fixture"],
        data_quality=data_quality,  # type: ignore[arg-type]
    )


class TestRegimeBands:
    def test_bull_regime_deploys_80_to_100(self):
        allocs = [
            _alloc("MSFT", amount=300, category="Core"),
            _alloc("TSM", amount=300, category="Core"),
            _alloc("VOO", amount=300, category="ETF"),
        ]
        out = adapt_allocation_plan(
            cash_to_deploy=900,
            allocations=allocs,
            regime=_regime("bull", 80),
        )
        assert out.deploy_percentage >= 80.0
        assert out.deploy_percentage <= 100.0
        assert out.deployment_mode in {"full", "partial"}
        assert out.cash_reserve_amount <= 900 * 0.20 + 5  # ≤20% + rounding

    def test_neutral_regime_deploys_60_to_80(self):
        allocs = [
            _alloc("MSFT", amount=300),
            _alloc("TSM", amount=300),
            _alloc("VOO", amount=300, category="ETF"),
        ]
        out = adapt_allocation_plan(
            cash_to_deploy=900,
            allocations=allocs,
            regime=_regime("neutral", 55),
        )
        assert 60.0 <= out.deploy_percentage <= 80.0
        assert out.cash_reserve_amount > 0

    def test_risk_off_never_deploys_100(self):
        allocs = [
            _alloc("MSFT", amount=300),
            _alloc("VOO", amount=300, category="ETF"),
            _alloc("BRK-B", amount=300),
        ]
        out = adapt_allocation_plan(
            cash_to_deploy=900,
            allocations=allocs,
            regime=_regime("risk_off", 25),
        )
        assert 35.0 <= out.deploy_percentage <= 60.0
        assert out.deploy_percentage < 100.0
        assert out.deployment_mode in {"defensive", "partial", "wait"}


class TestStaging:
    def test_sum_invariant_per_row(self):
        allocs = [
            _alloc("MSFT", amount=400),
            _alloc("TSM", amount=300),
            _alloc("VOO", amount=200, category="ETF"),
        ]
        out = adapt_allocation_plan(
            cash_to_deploy=900,
            allocations=allocs,
            regime=_regime("neutral", 55),
        )
        for s in out.staged_allocations:
            assert s.immediate_amount + s.reserve_amount == pytest.approx(s.original_amount, abs=0.01), \
                f"{s.ticker}: {s.immediate_amount} + {s.reserve_amount} != {s.original_amount}"

    def test_high_current_weight_ticker_is_deferred(self):
        # NVDA already at 16% (>= 80% of 20% single-stock cap) and >= 25% of plan.
        allocs = [
            _alloc("NVDA", amount=400, current_weight=17.0, category="Core"),
            _alloc("TSM", amount=300, category="Core"),
            _alloc("VOO", amount=200, category="ETF"),
        ]
        out = adapt_allocation_plan(
            cash_to_deploy=900,
            allocations=allocs,
            regime=_regime("neutral", 55),
        )
        nvda = next(s for s in out.staged_allocations if s.ticker == "NVDA")
        assert nvda.immediate_amount == 0.0
        assert nvda.reserve_amount > 0
        assert "pullback" in nvda.staging_instruction.lower() or "defer" in nvda.staging_instruction.lower()

    def test_bull_full_allocation_no_reserve(self):
        allocs = [
            _alloc("MSFT", amount=300, category="Core"),
            _alloc("VOO", amount=300, category="ETF"),
        ]
        out = adapt_allocation_plan(
            cash_to_deploy=600,
            allocations=allocs,
            regime=_regime("bull", 80),
        )
        # In bull, share=1.0 → most rows have reserve == 0
        non_zero_reserves = [s for s in out.staged_allocations if s.reserve_amount > 0]
        assert len(non_zero_reserves) == 0


class TestConcentration:
    def test_same_theme_concentration_reduces_deploy(self):
        # All three big_tech → top theme = 100%.
        allocs = [
            _alloc("MSFT", amount=300),
            _alloc("GOOGL", amount=300),
            _alloc("META", amount=300),
        ]
        out_concentrated = adapt_allocation_plan(
            cash_to_deploy=900,
            allocations=allocs,
            regime=_regime("neutral", 55),
        )
        # Compare against a diversified counterfactual.
        diversified = [
            _alloc("MSFT", amount=300),                  # big_tech
            _alloc("VOO", amount=300, category="ETF"),   # broad_etf
            _alloc("BRK-B", amount=300),                 # untyped
        ]
        out_diversified = adapt_allocation_plan(
            cash_to_deploy=900,
            allocations=diversified,
            regime=_regime("neutral", 55),
        )
        assert out_concentrated.deploy_percentage < out_diversified.deploy_percentage
        assert any("concentration" in r.lower() for r in out_concentrated.adjustments_applied)


class TestGuardrails:
    def test_deploy_floor_25_unless_wait(self):
        # Force heavy concentration penalty + risk_off to push deploy down.
        allocs = [
            _alloc("MSFT", amount=500),
            _alloc("GOOGL", amount=400),  # both big_tech → 100% theme
        ]
        out = adapt_allocation_plan(
            cash_to_deploy=900,
            allocations=allocs,
            regime=_regime("risk_off", 20),
        )
        if out.deployment_mode != "wait":
            assert out.deploy_percentage >= 25.0

    def test_wait_mode_when_no_cash(self):
        out = adapt_allocation_plan(
            cash_to_deploy=0,
            allocations=[_alloc("MSFT", amount=0)],
            regime=_regime("neutral", 55),
        )
        assert out.deployment_mode == "wait"
        assert out.recommended_deploy_amount == 0.0
        assert out.cash_reserve_amount == 0.0


class TestExplainability:
    def test_adaptive_reasons_mention_regime_and_reserve(self):
        allocs = [
            _alloc("MSFT", amount=300),
            _alloc("TSM", amount=300),
            _alloc("VOO", amount=300, category="ETF"),
        ]
        out = adapt_allocation_plan(
            cash_to_deploy=900,
            allocations=allocs,
            regime=_regime("neutral", 55),
        )
        joined = " ".join(out.adaptive_reasons).lower()
        assert "regime" in joined or "neutral" in joined
        assert "deploy" in joined or "reserve" in joined
        assert len(out.adaptive_reasons) <= 3

    def test_adaptive_reasons_mention_deferred_ticker(self):
        allocs = [
            _alloc("NVDA", amount=400, current_weight=18.0),
            _alloc("VOO", amount=300, category="ETF"),
            _alloc("TSM", amount=200),
        ]
        out = adapt_allocation_plan(
            cash_to_deploy=900,
            allocations=allocs,
            regime=_regime("neutral", 55),
        )
        joined = " ".join(out.adaptive_reasons)
        assert "NVDA" in joined or "deferred" in joined.lower()


class TestBehaviorAdaptiveLayer:
    def test_under_deployer_reduces_deploy_percentage(self):
        allocs = [
            _alloc("MSFT", amount=300),
            _alloc("TSM", amount=300),
            _alloc("VOO", amount=300, category="ETF"),
        ]
        baseline = adapt_allocation_plan(
            cash_to_deploy=900,
            allocations=allocs,
            regime=_regime("neutral", 55),
        )
        behavior_adjusted = adapt_allocation_plan(
            cash_to_deploy=900,
            allocations=allocs,
            regime=_regime("neutral", 55),
            user_behavior={
                "avg_deploy_ratio": 0.78,
                "stable_deploy_ratio": 0.78,
                "sample_size": 10,
                "personalization_confidence": "High",
                "adjustment_strength": 1.0,
                "under_deployer": True,
            },
        )
        assert behavior_adjusted.deploy_percentage < baseline.deploy_percentage
        assert any("execution ratio" in item.lower() for item in behavior_adjusted.adjustments_applied)

    def test_prefers_etf_biases_staging_toward_etf(self):
        allocs = [
            _alloc("MSFT", amount=300, category="Core"),
            _alloc("VOO", amount=300, category="ETF"),
        ]
        base = adapt_allocation_plan(
            cash_to_deploy=600,
            allocations=allocs,
            regime=_regime("neutral", 55),
        )
        etf_biased = adapt_allocation_plan(
            cash_to_deploy=600,
            allocations=allocs,
            regime=_regime("neutral", 55),
            user_behavior={"prefers_etf": True},
        )
        base_etf = next(s for s in base.staged_allocations if s.ticker == "VOO")
        tilted_etf = next(s for s in etf_biased.staged_allocations if s.ticker == "VOO")
        assert tilted_etf.immediate_amount >= base_etf.immediate_amount

    def test_low_history_disables_personalized_deploy_adjustment(self):
        allocs = [
            _alloc("MSFT", amount=300),
            _alloc("TSM", amount=300),
            _alloc("VOO", amount=300, category="ETF"),
        ]
        baseline = adapt_allocation_plan(
            cash_to_deploy=900,
            allocations=allocs,
            regime=_regime("neutral", 55),
        )
        low_history = adapt_allocation_plan(
            cash_to_deploy=900,
            allocations=allocs,
            regime=_regime("neutral", 55),
            user_behavior={
                "stable_deploy_ratio": 0.6,
                "sample_size": 2,
                "personalization_confidence": "Low",
                "adjustment_strength": 0.0,
            },
        )
        assert low_history.deploy_percentage == baseline.deploy_percentage
        assert any("not enough history yet to personalize deployment" in m.lower() for m in low_history.style_messages)

    def test_medium_confidence_uses_half_strength_penalty(self):
        allocs = [
            _alloc("MSFT", amount=300),
            _alloc("TSM", amount=300),
            _alloc("VOO", amount=300, category="ETF"),
        ]
        baseline = adapt_allocation_plan(
            cash_to_deploy=900,
            allocations=allocs,
            regime=_regime("neutral", 55),
        )
        medium = adapt_allocation_plan(
            cash_to_deploy=900,
            allocations=allocs,
            regime=_regime("neutral", 55),
            user_behavior={
                "stable_deploy_ratio": 0.6,
                "sample_size": 4,
                "personalization_confidence": "Medium",
                "adjustment_strength": 0.5,
            },
        )
        assert medium.deploy_percentage < baseline.deploy_percentage
        assert any("-5pts" in item.lower() for item in medium.adjustments_applied)
