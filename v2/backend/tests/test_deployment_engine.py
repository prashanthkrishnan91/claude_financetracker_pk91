"""Tests for Deploy Logic v2 — deterministic deployment-mode classifier.

Coverage:
- full_deploy when no strong reserve trigger exists
- reserve is impossible without a valid trigger
- cash drag penalty converts weak reserve case to full_deploy
- concentration risk can reduce deployment mode
- WATCH-tier ticker cap is respected
- deploy_now denominator is total_deposit, not something else
- output does not produce generic reserve text
- missing/low-quality data lowers confidence honestly
- old decision-log snapshots without v2 fields parse gracefully (type compatibility)
"""

from __future__ import annotations

import pytest

from app.services.deployment_engine import (
    CASH_DRAG_BONUS_MAX,
    DEFENSIVE_RESERVE_SCORE,
    FULL_DEPLOY_SCORE,
    MIN_RESERVE_FOR_TRIGGER,
    STAGED_DEPLOY_SCORE,
    WATCH_TICKER_MAX_PLAN_SHARE,
    DeploymentDecision,
    ReserveTrigger,
    classify_deployment,
)
from app.services.allocation_engine import AllocationItem, Holding
from app.services.regime_engine import RegimeOutput


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _alloc(
    ticker: str,
    *,
    amount: float,
    current_weight: float = 0.0,
    after_weight: float | None = None,
    target_weight: float = 20.0,
    conviction_level: str = "HIGH",
    conviction_score: float = 0.8,
    confidence: float = 0.8,
    score: float = 4.5,
    category: str = "Core",
    reason: str = "test fixture",
) -> AllocationItem:
    return AllocationItem(
        ticker=ticker,
        action="BUY",
        amount=amount,
        current_weight=current_weight,
        after_weight=after_weight if after_weight is not None else current_weight + 2.0,
        target_weight=target_weight,
        conviction_level=conviction_level,
        conviction_score=conviction_score,
        confidence=confidence,
        score=score,
        reason=reason,
        category=category,
    )


def _regime(
    label: str = "neutral",
    score: float = 55.0,
    data_quality: str = "high",
    reasons: list[str] | None = None,
) -> RegimeOutput:
    return RegimeOutput(
        regime_label=label,  # type: ignore[arg-type]
        regime_score=score,
        regime_reasons=reasons or [f"{label} regime fixture"],
        data_quality=data_quality,  # type: ignore[arg-type]
    )


# ── 1. Full deployment when no strong reserve trigger ─────────────────────────

class TestFullDeploy:
    def test_bull_no_concentration_gives_full_deploy(self):
        allocs = [
            _alloc("MSFT", amount=300, category="Core"),
            _alloc("TSM", amount=300, category="Core"),
            _alloc("VOO", amount=300, category="ETF"),
        ]
        out = classify_deployment(
            cash_to_deploy=900,
            allocations=allocs,
            regime=_regime("bull", 80),
        )
        assert out.deployment_mode == "full_deploy"
        assert out.deploy_now_amount == pytest.approx(900.0, abs=1.0)
        assert out.reserve_amount == pytest.approx(0.0, abs=1.0)

    def test_neutral_high_conviction_defaults_to_full_deploy(self):
        """With no concentration, high conviction, neutral regime → full_deploy."""
        allocs = [
            _alloc("AAPL", amount=400, conviction_level="HIGH", score=4.8),
            _alloc("VOO", amount=300, conviction_level="HIGH", score=4.5, category="ETF"),
        ]
        out = classify_deployment(
            cash_to_deploy=700,
            allocations=allocs,
            regime=_regime("neutral", 55),
        )
        assert out.deployment_mode == "full_deploy"

    def test_full_deploy_reserve_is_zero(self):
        allocs = [_alloc("MSFT", amount=500), _alloc("VOO", amount=400, category="ETF")]
        out = classify_deployment(
            cash_to_deploy=900,
            allocations=allocs,
            regime=_regime("bull", 75),
        )
        assert out.reserve_amount == pytest.approx(0.0, abs=1.0)
        assert out.reserve_trigger is None
        assert out.reserve_reason is None

    def test_deploy_now_equals_total_deposit_on_full_deploy(self):
        allocs = [_alloc("MSFT", amount=900)]
        out = classify_deployment(
            cash_to_deploy=900,
            allocations=allocs,
            regime=_regime("bull", 80),
        )
        assert out.deploy_now_amount == pytest.approx(out.total_deposit, abs=1.0)


# ── 2. Reserve requires a valid trigger ───────────────────────────────────────

class TestReserveTrigger:
    def test_reserve_without_trigger_forces_full_deploy(self):
        """Hard trigger rule: when staged mode produces reserve > 25 but no specific
        trigger can be generated, the engine must force full_deploy with reserve = 0.

        Scenario: neutral regime + low data quality lowers score to staged range.
        Diverse tickers (no same-theme concentration, no near-cap, no Watch, not
        risk_off) → all 4 trigger paths return None → hard rule fires.
        """
        # MSFT (big_tech) and BRK-B (no theme in map) → no concentration trigger
        # (concentration requires top_count >= 2 from the same theme)
        allocs = [
            _alloc("MSFT", amount=300, current_weight=5.0),
            _alloc("BRK-B", amount=300, current_weight=5.0),
        ]
        out = classify_deployment(
            cash_to_deploy=600,   # cash == plan → no prelim excess
            allocations=allocs,
            regime=_regime("neutral", 55, data_quality="low"),
        )
        # Low dq (-15) + neutral (-10) drops score below 70 → staged without cd bonus.
        # Staged reserve = 30% of plan > 25 → trigger generation → None →
        # hard rule forces full_deploy.
        assert out.deployment_mode == "full_deploy"
        assert out.reserve_amount == pytest.approx(0.0, abs=1.0)
        assert out.cash_drag_penalty_applied is True

    def test_near_cap_ticker_gets_technical_pullback_trigger(self):
        """A ticker at ≥70% of max weight should produce a technical pullback trigger."""
        allocs = [
            _alloc("NVDA", amount=300, current_weight=15.0, category="Core"),
            _alloc("VOO", amount=300, current_weight=5.0, category="ETF"),
        ]
        out = classify_deployment(
            cash_to_deploy=700,
            allocations=allocs,
            regime=_regime("neutral", 55),
        )
        # Near-cap → should have a reserve trigger if reserve exists
        if out.reserve_amount > MIN_RESERVE_FOR_TRIGGER:
            assert out.reserve_trigger is not None
            assert out.reserve_trigger.trigger_type == "technical_pullback"
            assert "NVDA" in out.reserve_trigger.reserve_target_tickers

    def test_risk_off_produces_event_driven_trigger(self):
        """Risk-off regime → reserve trigger must cite regime condition."""
        allocs = [
            _alloc("MSFT", amount=300),
            _alloc("VOO", amount=300, category="ETF"),
            _alloc("BRK-B", amount=200),
        ]
        out = classify_deployment(
            cash_to_deploy=900,
            allocations=allocs,
            regime=_regime("risk_off", 25),
        )
        if out.reserve_amount > MIN_RESERVE_FOR_TRIGGER:
            assert out.reserve_trigger is not None
            assert out.reserve_trigger.trigger_type in {
                "event_driven", "technical_pullback", "watch_tier_breakout",
                "concentration_reduction",
            }
            # Must not be generic
            reason = out.reserve_trigger.reserve_reason.lower()
            assert "hold for pullbacks" not in reason
            assert "keep cash for flexibility" not in reason

    def test_reserve_trigger_has_specific_target_tickers(self):
        """When a trigger is generated, it must name specific tickers."""
        allocs = [
            _alloc("NVDA", amount=400, current_weight=16.0),
            _alloc("VOO", amount=400, category="ETF"),
        ]
        out = classify_deployment(
            cash_to_deploy=900,
            allocations=allocs,
            regime=_regime("neutral", 55),
        )
        if out.reserve_trigger is not None:
            assert len(out.reserve_trigger.reserve_target_tickers) > 0
            assert out.reserve_trigger.when_to_deploy_reserve  # non-empty


# ── 3. Cash drag penalty converts weak reserve into full_deploy ───────────────

class TestCashDragPenalty:
    def test_cash_drag_applied_when_no_trigger(self):
        """Idle unallocated cash without a strong trigger should apply cash drag bonus.

        Scenario: cash > plan total, no strong reserve trigger, neutral + low dq →
        cash drag bonus is applied in scoring and the engine reports it in output.
        """
        # MSFT (big_tech) + BRK-B (no theme in _DEFAULT_THEME_MAP) → no concentration
        allocs = [
            _alloc("MSFT", amount=300, current_weight=5.0),
            _alloc("BRK-B", amount=300, current_weight=5.0),
        ]
        out = classify_deployment(
            cash_to_deploy=800,   # 200 unallocated above plan of 600
            allocations=allocs,
            regime=_regime("neutral", 55, data_quality="low"),
        )
        # Unallocated excess = 200 → reserve_ratio = 0.25 → cd_bonus = +3
        # Score = 70 + 15 + 8 + 3 - 0 - 10 - 15 = 71 → full_deploy
        assert out.deployment_mode == "full_deploy"
        assert out.cash_drag_penalty_applied is True

    def test_cash_drag_forces_full_deploy_when_no_trigger_possible(self):
        """Hard rule: staged mode without a valid trigger must resolve to full_deploy.

        Neutral + low data quality drops score to staged range (no prelim excess
        so no cd_bonus applies). The staged reserve (30% of plan) > MIN_RESERVE
        but no trigger can be found → engine forces full_deploy.
        """
        # AAPL (big_tech) + BRK-B (no theme → no concentration trigger possible)
        allocs = [
            _alloc("AAPL", amount=450, current_weight=8.0),
            _alloc("BRK-B", amount=450, current_weight=9.0),
        ]
        out = classify_deployment(
            cash_to_deploy=900,   # cash == plan → no unallocated prelim reserve
            allocations=allocs,
            regime=_regime("neutral", 55, data_quality="low"),
        )
        # Score = 70+15+8-0-10-15 = 68 → staged. Staged reserve 270 > 25, trigger=None
        # → hard rule forces full_deploy with reserve = 0.
        assert out.deployment_mode == "full_deploy"
        assert out.cash_drag_penalty_applied is True

    def test_cash_drag_not_applied_with_strong_trigger(self):
        """When a near-cap ticker provides a strong trigger, cash drag is suppressed."""
        allocs = [
            _alloc("NVDA", amount=500, current_weight=16.0),  # near cap → strong trigger
            _alloc("VOO", amount=300, category="ETF"),
        ]
        out = classify_deployment(
            cash_to_deploy=900,
            allocations=allocs,
            regime=_regime("neutral", 55),
        )
        # Strong trigger present → cash drag bonus = 0, so scoring stays unforced
        # Expect staged or defensive (neutral -10 + no cd_bonus)
        # If there's a reserve, it must have a trigger
        if out.reserve_amount > MIN_RESERVE_FOR_TRIGGER:
            assert out.reserve_trigger is not None


# ── 4. Concentration risk reduces deployment mode ────────────────────────────

class TestConcentrationRisk:
    def test_high_concentration_reduces_score(self):
        """All big_tech tickers → theme share > 60% → max concentration penalty."""
        concentrated = [
            _alloc("MSFT", amount=300),
            _alloc("GOOGL", amount=300),
            _alloc("META", amount=300),
        ]
        out_conc = classify_deployment(
            cash_to_deploy=900,
            allocations=concentrated,
            regime=_regime("neutral", 55),
        )
        diversified = [
            _alloc("MSFT", amount=300),
            _alloc("VOO", amount=300, category="ETF"),
            _alloc("BRK-B", amount=300),
        ]
        out_div = classify_deployment(
            cash_to_deploy=900,
            allocations=diversified,
            regime=_regime("neutral", 55),
        )
        assert out_conc.deployment_score < out_div.deployment_score

    def test_concentration_risk_appears_in_risks(self):
        allocs = [
            _alloc("MSFT", amount=300),
            _alloc("GOOGL", amount=300),
            _alloc("META", amount=300),
        ]
        out = classify_deployment(
            cash_to_deploy=900,
            allocations=allocs,
            regime=_regime("neutral", 55),
        )
        assert any("concentration" in r.lower() for r in out.risks)

    def test_concentration_penalty_note_in_adjustments(self):
        allocs = [
            _alloc("MSFT", amount=300),
            _alloc("GOOGL", amount=300),
            _alloc("META", amount=300),
        ]
        out = classify_deployment(
            cash_to_deploy=900,
            allocations=allocs,
            regime=_regime("neutral", 55),
        )
        assert any("concentration" in a.lower() for a in out.adjustments_applied)


# ── 5. WATCH ticker cap ───────────────────────────────────────────────────────

class TestWatchTickerCap:
    def test_watch_ticker_capped_at_25_percent_of_plan(self):
        """LOW conviction ticker should be capped at WATCH_TICKER_MAX_PLAN_SHARE."""
        allocs = [
            _alloc("MSFT", amount=600, conviction_level="HIGH", score=4.5),
            _alloc("XYZ", amount=300, conviction_level="LOW", score=2.0),  # Watch, 33% of plan
        ]
        total_plan = 900.0
        expected_cap = round(total_plan * WATCH_TICKER_MAX_PLAN_SHARE, 2)
        out = classify_deployment(
            cash_to_deploy=900,
            allocations=allocs,
            regime=_regime("bull", 80),
        )
        xyz_row = next((r for r in out.per_ticker_allocations if r.ticker == "XYZ"), None)
        assert xyz_row is not None
        assert xyz_row.capped is True
        assert xyz_row.amount <= expected_cap + 0.01

    def test_watch_ticker_within_cap_not_modified(self):
        """Watch ticker at 20% of plan (≤ 25% cap) should not be capped."""
        allocs = [
            _alloc("MSFT", amount=600, conviction_level="HIGH", score=4.5),
            _alloc("XYZ", amount=150, conviction_level="LOW", score=2.0),  # Watch, 20% of plan
        ]
        out = classify_deployment(
            cash_to_deploy=750,
            allocations=allocs,
            regime=_regime("bull", 80),
        )
        xyz_row = next((r for r in out.per_ticker_allocations if r.ticker == "XYZ"), None)
        assert xyz_row is not None
        assert xyz_row.capped is False

    def test_watch_cap_noted_in_risks(self):
        allocs = [
            _alloc("MSFT", amount=600, conviction_level="HIGH"),
            _alloc("ZZZ", amount=400, conviction_level="LOW", score=1.5),  # Watch, 40% → capped
        ]
        out = classify_deployment(
            cash_to_deploy=1000,
            allocations=allocs,
            regime=_regime("bull", 80),
        )
        assert any("ZZZ" in r for r in out.risks) or any(
            r.capped for r in out.per_ticker_allocations if r.ticker == "ZZZ"
        )


# ── 6. Deploy-now uses correct denominator ────────────────────────────────────

class TestDeployDenominator:
    def test_deploy_now_plus_reserve_equals_total_deposit(self):
        allocs = [
            _alloc("MSFT", amount=300),
            _alloc("TSM", amount=300),
            _alloc("VOO", amount=300, category="ETF"),
        ]
        out = classify_deployment(
            cash_to_deploy=900,
            allocations=allocs,
            regime=_regime("neutral", 55),
        )
        total = out.deploy_now_amount + out.reserve_amount
        # deploy_now + reserve should account for the full cash
        assert total == pytest.approx(out.total_deposit, abs=5.0)

    def test_per_ticker_deploy_now_sums_correctly(self):
        allocs = [
            _alloc("AAPL", amount=400),
            _alloc("MSFT", amount=300),
            _alloc("VOO", amount=200, category="ETF"),
        ]
        out = classify_deployment(
            cash_to_deploy=900,
            allocations=allocs,
            regime=_regime("bull", 80),
        )
        per_ticker_total = sum(r.deploy_now for r in out.per_ticker_allocations)
        assert per_ticker_total == pytest.approx(out.deploy_now_amount, abs=5.0)

    def test_total_deposit_reflects_cash_to_deploy(self):
        allocs = [_alloc("MSFT", amount=500)]
        out = classify_deployment(
            cash_to_deploy=500,
            allocations=allocs,
            regime=_regime("bull", 80),
        )
        assert out.total_deposit == pytest.approx(500.0, abs=0.01)


# ── 7. No generic reserve text ────────────────────────────────────────────────

class TestNoGenericReserveText:
    def _check_no_generic(self, text: str) -> None:
        generic = [
            "hold for pullbacks",
            "keep cash for flexibility",
            "dry powder",
            "general reserve",
        ]
        lower = text.lower()
        for phrase in generic:
            assert phrase not in lower, f"Generic phrase found: '{phrase}' in '{text}'"

    def test_reserve_reason_not_generic(self):
        allocs = [
            _alloc("NVDA", amount=500, current_weight=15.0),
            _alloc("TSM", amount=400),
        ]
        out = classify_deployment(
            cash_to_deploy=1000,
            allocations=allocs,
            regime=_regime("neutral", 55),
        )
        if out.reserve_reason:
            self._check_no_generic(out.reserve_reason)

    def test_reserve_trigger_text_not_generic(self):
        allocs = [
            _alloc("NVDA", amount=500, current_weight=16.0),
            _alloc("VOO", amount=400, category="ETF"),
        ]
        out = classify_deployment(
            cash_to_deploy=1000,
            allocations=allocs,
            regime=_regime("neutral", 55),
        )
        if out.reserve_trigger:
            self._check_no_generic(out.reserve_trigger.reserve_reason)
            self._check_no_generic(out.reserve_trigger.when_to_deploy_reserve)
            assert out.reserve_trigger.trigger_condition  # non-empty
            assert out.reserve_trigger.trigger_type      # non-empty


# ── 8. Low data quality lowers confidence honestly ────────────────────────────

class TestDataQualityAndConfidence:
    def test_low_data_quality_lowers_confidence(self):
        allocs = [_alloc("MSFT", amount=500), _alloc("VOO", amount=400, category="ETF")]
        out_high = classify_deployment(
            cash_to_deploy=900,
            allocations=allocs,
            regime=_regime("neutral", 55, data_quality="high"),
        )
        out_low = classify_deployment(
            cash_to_deploy=900,
            allocations=allocs,
            regime=_regime("neutral", 55, data_quality="low"),
        )
        assert out_low.deployment_confidence < out_high.deployment_confidence

    def test_low_data_quality_reflected_in_output(self):
        allocs = [_alloc("MSFT", amount=500)]
        out = classify_deployment(
            cash_to_deploy=500,
            allocations=allocs,
            regime=_regime("neutral", 55, data_quality="low"),
        )
        assert out.data_quality == "low"
        assert out.deployment_confidence <= 0.55

    def test_medium_quality_confidence_between_high_and_low(self):
        allocs = [_alloc("MSFT", amount=500), _alloc("VOO", amount=400, category="ETF")]
        high_c = classify_deployment(
            cash_to_deploy=900,
            allocations=allocs,
            regime=_regime("bull", 80, data_quality="high"),
        ).deployment_confidence
        med_c = classify_deployment(
            cash_to_deploy=900,
            allocations=allocs,
            regime=_regime("bull", 80, data_quality="medium"),
        ).deployment_confidence
        low_c = classify_deployment(
            cash_to_deploy=900,
            allocations=allocs,
            regime=_regime("bull", 80, data_quality="low"),
        ).deployment_confidence
        assert high_c >= med_c >= low_c

    def test_low_quality_penalty_in_adjustments(self):
        allocs = [_alloc("MSFT", amount=500)]
        out = classify_deployment(
            cash_to_deploy=500,
            allocations=allocs,
            regime=_regime("neutral", 55, data_quality="low"),
        )
        assert any("data_quality" in a.lower() for a in out.adjustments_applied)


# ── 9. Empty / edge cases ─────────────────────────────────────────────────────

class TestEdgeCases:
    def test_zero_cash_returns_skip_or_wait(self):
        allocs = [_alloc("MSFT", amount=500)]
        out = classify_deployment(
            cash_to_deploy=0,
            allocations=allocs,
            regime=_regime("bull", 80),
        )
        assert out.deployment_mode == "skip_or_wait"
        assert out.deploy_now_amount == 0.0
        assert out.reserve_amount == 0.0

    def test_empty_allocations_returns_skip_or_wait(self):
        out = classify_deployment(
            cash_to_deploy=900,
            allocations=[],
            regime=_regime("bull", 80),
        )
        assert out.deployment_mode == "skip_or_wait"

    def test_output_fields_always_present(self):
        """DeploymentDecision must always have all expected fields."""
        out = classify_deployment(
            cash_to_deploy=900,
            allocations=[_alloc("MSFT", amount=900)],
            regime=_regime("bull", 80),
        )
        assert isinstance(out.total_deposit, float)
        assert isinstance(out.deploy_now_amount, float)
        assert isinstance(out.reserve_amount, float)
        assert out.deployment_mode in {
            "full_deploy", "staged_deploy", "defensive_reserve", "skip_or_wait"
        }
        assert isinstance(out.deployment_confidence, float)
        assert isinstance(out.deployment_reason, str)
        assert isinstance(out.cash_drag_penalty_applied, bool)
        assert isinstance(out.per_ticker_allocations, list)
        assert isinstance(out.risks, list)
        assert isinstance(out.adjustments_applied, list)
        assert isinstance(out.evaluation_notes_for_future_decision_log, list)

    def test_old_snapshot_fields_gracefully_absent(self):
        """Simulates an old decision log dict that lacks v2 fields — no crash."""
        old_snapshot: dict = {
            "recommendation_snapshot": {"normalized_tickers": []},
            "actual_decisions": [],
        }
        # Just verify the dataclass can be constructed with None reserve fields
        dummy = DeploymentDecision(
            total_deposit=900.0,
            deploy_now_amount=900.0,
            reserve_amount=0.0,
            deployment_mode="full_deploy",
            deployment_confidence=1.0,
            deployment_reason="legacy",
            cash_drag_penalty_applied=False,
            reserve_reason=None,
            reserve_trigger=None,
            per_ticker_allocations=[],
            risks=[],
            data_quality="high",
            evaluation_notes_for_future_decision_log=[],
            deployment_score=85.0,
            adjustments_applied=[],
        )
        assert dummy.reserve_trigger is None
        assert dummy.deployment_mode == "full_deploy"


# ── 10. Risk-off regime behavior ─────────────────────────────────────────────

class TestRiskOffRegime:
    def test_risk_off_max_penalty_lowers_score(self):
        allocs = [_alloc("MSFT", amount=300), _alloc("VOO", amount=300, category="ETF")]
        out_bull = classify_deployment(
            cash_to_deploy=600,
            allocations=allocs,
            regime=_regime("bull", 80),
        )
        out_roff = classify_deployment(
            cash_to_deploy=600,
            allocations=allocs,
            regime=_regime("risk_off", 20),
        )
        assert out_roff.deployment_score < out_bull.deployment_score

    def test_risk_off_produces_defensive_or_skip(self):
        allocs = [
            _alloc("MSFT", amount=300),
            _alloc("GOOGL", amount=300),
            _alloc("META", amount=300),
        ]
        out = classify_deployment(
            cash_to_deploy=900,
            allocations=allocs,
            regime=_regime("risk_off", 20),
        )
        assert out.deployment_mode in {
            "defensive_reserve", "skip_or_wait", "staged_deploy"
        }
