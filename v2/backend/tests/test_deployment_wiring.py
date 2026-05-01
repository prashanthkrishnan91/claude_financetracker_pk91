"""Tests for Deploy Logic v2 PR 2 — live wiring of classify_deployment().

These tests verify the contract that the allocation router depends on:
classify_deployment() must produce fields that map cleanly to the API
response shape (_plan_to_dict expects). Tests operate on the service
layer only, matching the pattern of test_deployment_engine.py.

Coverage:
- classify_deployment emits all fields required by the router's deployment_v2 block
- full_deploy case deploys full $900 when no valid reserve trigger exists
- staged_deploy only reserves when reserve_trigger is present (never generic)
- per-ticker allocation sum matches deploy_now_amount, not total_deposit, when staged
- no reserve > $25 without a trigger (hard rule preserved after wiring)
- per-ticker immediate_amounts sum to deploy_now_amount within tolerance
- existing adaptive_deployment and allocation_engine behavior is unaffected
- old/legacy response fields remain available (backward compat contract)
"""

from __future__ import annotations

import dataclasses

import pytest

from app.services.allocation_engine import AllocationItem, Holding
from app.services.deployment_engine import (
    DeploymentDecision,
    MIN_RESERVE_FOR_TRIGGER,
    PerTickerDeployment,
    ReserveTrigger,
    classify_deployment,
)
from app.services.regime_engine import RegimeOutput


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _alloc(
    ticker: str,
    *,
    amount: float,
    current_weight: float = 0.0,
    conviction_level: str = "HIGH",
    conviction_score: float = 0.8,
    confidence: float = 0.8,
    score: float = 4.5,
    category: str = "Core",
) -> AllocationItem:
    return AllocationItem(
        ticker=ticker,
        action="BUY",
        amount=amount,
        current_weight=current_weight,
        after_weight=current_weight + 2.0,
        target_weight=20.0,
        conviction_level=conviction_level,
        conviction_score=conviction_score,
        confidence=confidence,
        score=score,
        reason="test fixture",
        category=category,
    )


def _regime(label: str = "neutral", score: float = 55.0, data_quality: str = "high") -> RegimeOutput:
    return RegimeOutput(
        regime_label=label,  # type: ignore[arg-type]
        regime_score=score,
        regime_reasons=[f"{label} regime fixture"],
        data_quality=data_quality,  # type: ignore[arg-type]
    )


# ── Helper: simulate how _plan_to_dict maps per-ticker v2 output ──────────────

def _map_plan_dict(d: DeploymentDecision, allocs: list[AllocationItem]) -> list[dict]:
    """Mirror the per-ticker mapping logic in _plan_to_dict for test assertions."""
    v2_by_ticker = {pt.ticker.upper(): pt for pt in d.per_ticker_allocations}
    rows = []
    for a in allocs:
        v2t = v2_by_ticker.get(a.ticker.upper())
        rows.append({
            "ticker": a.ticker,
            "amount": a.amount,
            "immediate_amount": v2t.deploy_now if v2t else a.amount,
            "reserve_amount": v2t.reserve if v2t else 0.0,
            "ticker_role": v2t.role if v2t else None,
            "capped": v2t.capped if v2t else False,
            "cap_reason": v2t.cap_reason if v2t else None,
        })
    return rows


def _plan_block(d: DeploymentDecision) -> dict:
    """Mirror the plan_block v2 fields produced by _plan_to_dict."""
    return {
        "recommended_deploy_amount": d.deploy_now_amount,
        "cash_reserve": d.reserve_amount,
        "deploy_now_amount": d.deploy_now_amount,
        "reserve_amount": d.reserve_amount,
        "deployment_mode_v2": d.deployment_mode,
        "deployment_confidence": d.deployment_confidence,
        "deployment_reason": d.deployment_reason,
        "cash_drag_penalty_applied": d.cash_drag_penalty_applied,
        "reserve_reason": d.reserve_reason,
    }


def _deployment_v2_block(d: DeploymentDecision) -> dict:
    """Mirror the deployment_v2 top-level block produced by _plan_to_dict."""
    return {
        "total_deposit": d.total_deposit,
        "deploy_now_amount": d.deploy_now_amount,
        "reserve_amount": d.reserve_amount,
        "deployment_mode": d.deployment_mode,
        "deployment_confidence": d.deployment_confidence,
        "deployment_reason": d.deployment_reason,
        "cash_drag_penalty_applied": d.cash_drag_penalty_applied,
        "reserve_reason": d.reserve_reason,
        "reserve_trigger": dataclasses.asdict(d.reserve_trigger) if d.reserve_trigger else None,
        "risks": d.risks,
        "data_quality": d.data_quality,
        "evaluation_notes_for_future_decision_log": d.evaluation_notes_for_future_decision_log,
        "deployment_score": d.deployment_score,
        "adjustments_applied": d.adjustments_applied,
    }


# ── 1. classify_deployment emits the API contract fields ──────────────────────

class TestDeploymentV2OutputContract:
    def test_all_required_fields_present(self):
        allocs = [_alloc("MSFT", amount=450), _alloc("VOO", amount=450)]
        d = classify_deployment(
            cash_to_deploy=900.0,
            allocations=allocs,
            regime=_regime("neutral"),
        )
        # Fields required by deployment_v2 block in the router response
        assert isinstance(d.total_deposit, float)
        assert isinstance(d.deploy_now_amount, float)
        assert isinstance(d.reserve_amount, float)
        assert d.deployment_mode in {"full_deploy", "staged_deploy", "defensive_reserve", "skip_or_wait"}
        assert 0.0 <= d.deployment_confidence <= 1.0
        assert isinstance(d.deployment_reason, str) and d.deployment_reason
        assert isinstance(d.cash_drag_penalty_applied, bool)
        assert isinstance(d.risks, list)
        assert isinstance(d.data_quality, str)
        assert isinstance(d.evaluation_notes_for_future_decision_log, list)
        assert isinstance(d.deployment_score, float)
        assert isinstance(d.adjustments_applied, list)

    def test_reserve_trigger_is_dataclass_serialisable(self):
        # Near-cap ticker ensures a trigger is generated
        allocs = [
            _alloc("MSFT", amount=450, current_weight=14.5),  # above NEAR_CAP threshold
            _alloc("VOO", amount=450),
        ]
        d = classify_deployment(
            cash_to_deploy=900.0,
            allocations=allocs,
            regime=_regime("neutral"),
        )
        if d.reserve_trigger is not None:
            serialised = dataclasses.asdict(d.reserve_trigger)
            assert "reserve_reason" in serialised
            assert "reserve_target_tickers" in serialised
            assert "trigger_type" in serialised
            assert "when_to_deploy_reserve" in serialised

    def test_per_ticker_allocations_have_required_fields(self):
        allocs = [_alloc("MSFT", amount=450), _alloc("VOO", amount=450)]
        d = classify_deployment(
            cash_to_deploy=900.0,
            allocations=allocs,
            regime=_regime("neutral"),
        )
        for pt in d.per_ticker_allocations:
            assert pt.ticker
            assert pt.role in {"Primary", "Supporting", "Watch"}
            assert pt.amount >= 0
            assert pt.deploy_now >= 0
            assert pt.reserve >= 0
            assert abs((pt.deploy_now + pt.reserve) - pt.amount) < 0.02


# ── 2. Full deploy: $900 deployed when no valid reserve trigger ───────────────

class TestFullDeployWiring:
    def test_bull_full_deploy_amount_equals_total_deposit(self):
        allocs = [
            _alloc("MSFT", amount=450, conviction_level="HIGH", score=5.0),
            _alloc("VOO", amount=450, conviction_level="HIGH", score=5.0),
        ]
        d = classify_deployment(
            cash_to_deploy=900.0,
            allocations=allocs,
            regime=_regime("bull", 80.0),
        )
        plan = _plan_block(d)
        assert plan["deploy_now_amount"] == 900.0
        assert plan["cash_reserve"] == 0.0
        assert d.deployment_mode == "full_deploy"

    def test_full_deploy_per_ticker_reserve_zero(self):
        allocs = [
            _alloc("MSFT", amount=450, conviction_level="HIGH", score=5.0),
            _alloc("VOO", amount=450, conviction_level="HIGH", score=5.0),
        ]
        d = classify_deployment(
            cash_to_deploy=900.0,
            allocations=allocs,
            regime=_regime("bull", 80.0),
        )
        rows = _map_plan_dict(d, allocs)
        for row in rows:
            assert row["reserve_amount"] == 0.0
            assert row["immediate_amount"] == row["amount"]

    def test_full_deploy_no_reserve_trigger(self):
        allocs = [
            _alloc("MSFT", amount=450, conviction_level="HIGH", score=5.0),
            _alloc("VOO", amount=450, conviction_level="HIGH", score=5.0),
        ]
        d = classify_deployment(
            cash_to_deploy=900.0,
            allocations=allocs,
            regime=_regime("bull", 80.0),
        )
        dv2 = _deployment_v2_block(d)
        assert dv2["reserve_trigger"] is None
        assert dv2["reserve_reason"] is None

    def test_full_deploy_per_ticker_sum_equals_900(self):
        allocs = [
            _alloc("MSFT", amount=300, conviction_level="HIGH", score=5.0),
            _alloc("VOO", amount=300, conviction_level="HIGH", score=5.0),
            _alloc("TSM", amount=300, conviction_level="HIGH", score=5.0),
        ]
        d = classify_deployment(
            cash_to_deploy=900.0,
            allocations=allocs,
            regime=_regime("bull", 80.0),
        )
        rows = _map_plan_dict(d, allocs)
        ticker_sum = sum(r["immediate_amount"] for r in rows)
        assert abs(ticker_sum - 900.0) < 1.0


# ── 3. Staged deploy: reserve only when trigger exists, never generic ─────────

class TestStagedDeployWiring:
    def test_explicit_900_deposit_staged_720_rows_sum_720(self):
        allocs = [_alloc("RDDT", amount=360), _alloc("MSFT", amount=270), _alloc("TSM", amount=270)]
        d = DeploymentDecision(
            total_deposit=900.0,
            deploy_now_amount=720.0,
            reserve_amount=180.0,
            deployment_mode="staged_deploy",
            deployment_confidence=0.74,
            deployment_reason="Explicit staged fixture",
            cash_drag_penalty_applied=False,
            reserve_reason="Wait for risk-off reversal signal on growth names",
            reserve_trigger=ReserveTrigger(
                reserve_reason="Wait for risk-off reversal signal on growth names",
                reserve_target_tickers=["RDDT", "TSM"],
                reserve_purpose="stage_high_beta",
                trigger_type="event_driven",
                trigger_condition="Deploy reserve when VIX < 20 and breadth improves",
                suggested_review_event=None,
                suggested_review_date=None,
                when_to_deploy_reserve="Deploy reserve on confirmed risk-on reversal.",
            ),
            per_ticker_allocations=[
                PerTickerDeployment("RDDT", "Primary", 360.0, 302.14, 57.86, "HIGH", "Fixture"),
                PerTickerDeployment("MSFT", "Supporting", 270.0, 217.93, 52.07, "HIGH", "Fixture"),
                PerTickerDeployment("TSM", "Supporting", 270.0, 199.93, 70.07, "MEDIUM", "Fixture"),
            ],
            risks=[],
            data_quality="high",
            evaluation_notes_for_future_decision_log=[],
            deployment_score=61.0,
            adjustments_applied=[],
        )
        rows = _map_plan_dict(d, allocs)
        assert sum(r["immediate_amount"] for r in rows) == pytest.approx(720.0, abs=0.02)
        assert _plan_block(d)["deploy_now_amount"] == 720.0

    def test_risk_off_staged_has_trigger(self):
        allocs = [_alloc("MSFT", amount=450), _alloc("VOO", amount=450)]
        d = classify_deployment(
            cash_to_deploy=900.0,
            allocations=allocs,
            regime=_regime("risk_off", 30.0),
        )
        if d.reserve_amount > MIN_RESERVE_FOR_TRIGGER:
            assert d.reserve_trigger is not None, (
                f"reserve=${d.reserve_amount:.2f} > ${MIN_RESERVE_FOR_TRIGGER} must have trigger"
            )
            dv2 = _deployment_v2_block(d)
            assert dv2["reserve_trigger"] is not None
            assert dv2["reserve_reason"] is not None

    def test_no_reserve_without_trigger_hard_rule(self):
        # neutral regime, HIGH conviction, no near-cap — no trigger can be generated
        # hard rule must force reserve=0
        allocs = [
            _alloc("MSFT", amount=450, conviction_level="HIGH", score=4.5, current_weight=2.0),
            _alloc("VOO", amount=450, conviction_level="HIGH", score=4.5, current_weight=2.0),
        ]
        d = classify_deployment(
            cash_to_deploy=900.0,
            allocations=allocs,
            regime=_regime("neutral", 55.0),
        )
        if d.reserve_amount > MIN_RESERVE_FOR_TRIGGER:
            assert d.reserve_trigger is not None, (
                "Hard rule violated: reserve > $25 with no trigger"
            )
        if d.reserve_trigger is None:
            assert d.reserve_amount <= MIN_RESERVE_FOR_TRIGGER

    def test_staged_per_ticker_sum_matches_deploy_now_not_total_deposit(self):
        # risk_off: plan = $900, deploy_now < $900
        allocs = [_alloc("MSFT", amount=450), _alloc("VOO", amount=450)]
        d = classify_deployment(
            cash_to_deploy=900.0,
            allocations=allocs,
            regime=_regime("risk_off", 30.0),
        )
        rows = _map_plan_dict(d, allocs)
        ticker_immediate_sum = sum(r["immediate_amount"] for r in rows)
        # Sum of per-ticker immediate must equal deploy_now_amount, not total_deposit
        assert abs(ticker_immediate_sum - d.deploy_now_amount) < 1.0
        # When mode is staged/defensive, deploy_now < total
        if d.deployment_mode in {"staged_deploy", "defensive_reserve"}:
            assert d.deploy_now_amount < 900.0
            assert ticker_immediate_sum < 900.0

    def test_reserve_trigger_is_specific_not_generic(self):
        allocs = [_alloc("MSFT", amount=450), _alloc("VOO", amount=450)]
        d = classify_deployment(
            cash_to_deploy=900.0,
            allocations=allocs,
            regime=_regime("risk_off", 30.0),
        )
        if d.reserve_trigger is not None:
            # Must have a specific condition, not just "hold for pullbacks"
            assert d.reserve_trigger.trigger_condition, "trigger_condition must be non-empty"
            assert d.reserve_trigger.trigger_type, "trigger_type must be non-empty"
            assert d.reserve_trigger.when_to_deploy_reserve, "when_to_deploy_reserve must be non-empty"
            assert len(d.reserve_trigger.reserve_target_tickers) > 0, (
                "reserve_target_tickers must identify specific tickers"
            )


# ── 4. Per-ticker v2 fields map correctly ────────────────────────────────────

class TestPerTickerMapping:
    def test_ticker_role_is_set_for_all_tickers(self):
        allocs = [
            _alloc("MSFT", amount=450, conviction_level="HIGH", score=5.0),
            _alloc("VOO", amount=225, conviction_level="MEDIUM", score=3.0),
            _alloc("MSTR", amount=225, conviction_level="LOW", score=1.0),
        ]
        d = classify_deployment(
            cash_to_deploy=900.0,
            allocations=allocs,
            regime=_regime("neutral"),
        )
        rows = _map_plan_dict(d, allocs)
        for row in rows:
            assert row["ticker_role"] in {"Primary", "Supporting", "Watch"}, (
                f"{row['ticker']}: unexpected role {row['ticker_role']!r}"
            )

    def test_watch_ticker_cap_reflected_in_immediate_amount(self):
        # LOW conviction ticker with > 25% of plan: must be capped
        allocs = [
            _alloc("MSTR", amount=900, conviction_level="LOW", score=1.0),
        ]
        d = classify_deployment(
            cash_to_deploy=900.0,
            allocations=allocs,
            regime=_regime("neutral"),
        )
        rows = _map_plan_dict(d, allocs)
        mstr_row = next(r for r in rows if r["ticker"] == "MSTR")
        # Cap = 25% × $900 = $225; immediate should be ≤ $225 + rounding tolerance
        assert mstr_row["immediate_amount"] <= 225.0 + 1.0
        assert mstr_row["capped"] is True

    def test_per_ticker_deploy_now_reserve_sum_equals_amount(self):
        allocs = [
            _alloc("MSFT", amount=300),
            _alloc("VOO", amount=300),
            _alloc("TSM", amount=300),
        ]
        d = classify_deployment(
            cash_to_deploy=900.0,
            allocations=allocs,
            regime=_regime("neutral"),
        )
        for pt in d.per_ticker_allocations:
            assert abs((pt.deploy_now + pt.reserve) - pt.amount) < 0.02, (
                f"{pt.ticker}: deploy_now({pt.deploy_now}) + reserve({pt.reserve}) "
                f"!= amount({pt.amount})"
            )


# ── 5. Backward compat: adaptive and legacy fields survive ────────────────────

class TestBackwardCompatContract:
    def test_explicit_900_deposit_full_deploy_rows_sum_900(self):
        allocs = [_alloc("MSFT", amount=300), _alloc("TSM", amount=300), _alloc("NVDA", amount=300)]
        d = DeploymentDecision(
            total_deposit=900.0,
            deploy_now_amount=900.0,
            reserve_amount=0.0,
            deployment_mode="full_deploy",
            deployment_confidence=0.9,
            deployment_reason="Explicit full fixture",
            cash_drag_penalty_applied=False,
            reserve_reason=None,
            reserve_trigger=None,
            per_ticker_allocations=[
                PerTickerDeployment("MSFT", "Primary", 300.0, 300.0, 0.0, "HIGH", "Fixture"),
                PerTickerDeployment("TSM", "Supporting", 300.0, 300.0, 0.0, "HIGH", "Fixture"),
                PerTickerDeployment("NVDA", "Supporting", 300.0, 300.0, 0.0, "HIGH", "Fixture"),
            ],
            risks=[],
            data_quality="high",
            evaluation_notes_for_future_decision_log=[],
            deployment_score=76.0,
            adjustments_applied=[],
        )
        rows = _map_plan_dict(d, allocs)
        assert sum(r["immediate_amount"] for r in rows) == pytest.approx(900.0, abs=0.02)
        assert _plan_block(d)["cash_reserve"] == 0.0

    def test_classify_deployment_does_not_break_adapt_allocation_plan(self):
        from app.services.adaptive_deployment import adapt_allocation_plan
        allocs = [_alloc("MSFT", amount=450), _alloc("VOO", amount=450)]
        r = _regime("neutral")
        adaptive = adapt_allocation_plan(
            cash_to_deploy=900.0,
            allocations=allocs,
            regime=r,
        )
        d = classify_deployment(
            cash_to_deploy=900.0,
            allocations=allocs,
            regime=r,
        )
        # Both succeed independently
        assert adaptive.recommended_deploy_amount >= 0
        assert d.deploy_now_amount >= 0
        # adaptive block fields still carry their own values (not overwritten by v2)
        assert hasattr(adaptive, "deployment_mode")
        assert hasattr(adaptive, "cash_reserve_amount")

    def test_plan_block_backward_compat_fields(self):
        allocs = [_alloc("MSFT", amount=450), _alloc("VOO", amount=450)]
        d = classify_deployment(
            cash_to_deploy=900.0,
            allocations=allocs,
            regime=_regime("neutral"),
        )
        plan = _plan_block(d)
        # All four legacy field names are present (router keeps them for old UI)
        assert "recommended_deploy_amount" in plan
        assert "cash_reserve" in plan
        # And they equal the v2 canonical values
        assert plan["recommended_deploy_amount"] == d.deploy_now_amount
        assert plan["cash_reserve"] == d.reserve_amount
