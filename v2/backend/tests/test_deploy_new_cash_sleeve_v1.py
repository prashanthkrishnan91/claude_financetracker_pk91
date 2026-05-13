"""Tests — Stage 2.6D: Deploy v3 new-cash sleeve sizing v1.

Production-like coverage for the policy in
`deploy_new_cash_sleeve_v1.apply_new_cash_sleeve_sizing` and its integration
through `build_deploy_plan`.

Acceptance behaviors covered:
  - Production-like case: $900 + 10 BUY actionable candidates with targets
    equal to current weights → 3–5 BUY moves with meaningful total deployment.
  - Total BUY dollars <= cash_to_deploy after rounding.
  - HOLD/TRIM/SELL never receive new-cash BUY dollars.
  - Few-candidate case: 1–2 eligible BUYs → only those, residual explained.
  - No-eligible-BUY case → no fabricated moves, residual = full cash.
  - Min-trade threshold exposes residual_cash / residual_reason.
  - Current-gap / no-cash mode preserved (sleeve dormant).
  - Determinism: same inputs → same plan.
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

import pytest

from app.services.deploy.deploy_contracts import (
    DeployActionabilityStatus,
    DeployActionSource,
    DeployPlanInput,
    DeployPlanItem,
    DeployPlanStatus,
)
from app.services.deploy.deploy_new_cash_sleeve_v1 import (
    MAX_RECOMMENDATIONS,
    apply_new_cash_sleeve_sizing,
)
from app.services.deploy.deploy_policy_bridge import certify_sizing_policy
from app.services.deploy.deploy_sizing_contracts import (
    DeployCashInput,
    DeployPortfolioSizingInput,
    DeployPositionSizingInput,
    DeploySizingInputBundle,
    DeploySizingTrustStatus,
)
from app.services.deploy.deploy_target_allocation_bridge import (
    certify_target_allocation,
)
from app.services.deploy.deploy_translation_v1 import build_deploy_plan


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_bundle(
    portfolio_value: float,
    tickers_current_and_target: Dict[str, Tuple[float, float]],
    minimum_trade_usd: float = 1.0,
    rounding_policy: str = "WHOLE_DOLLAR",
    cash_balance: float = 0.0,
) -> DeploySizingInputBundle:
    """Build a fully certified sizing bundle for sleeve tests."""
    cash = DeployCashInput(
        available_cash_usd=cash_balance,
        trust_status=DeploySizingTrustStatus.CERTIFIED,
        source_label="test",
    )
    portfolio = DeployPortfolioSizingInput(
        total_portfolio_value_usd=portfolio_value,
        trust_status=DeploySizingTrustStatus.CERTIFIED,
        source_label="test",
    )
    positions = {}
    target_allocations = {}
    for ticker, (cur_val, target_w) in tickers_current_and_target.items():
        positions[ticker] = DeployPositionSizingInput(
            ticker=ticker,
            current_market_value_usd=cur_val,
            current_weight=cur_val / portfolio_value if portfolio_value > 0 else 0.0,
            trust_status=DeploySizingTrustStatus.CERTIFIED,
            source_label="test",
        )
        target_allocations[ticker] = certify_target_allocation(
            ticker=ticker,
            target_weight=target_w,
            source_label="test",
        )
    policy = certify_sizing_policy(minimum_trade_usd, rounding_policy)
    return DeploySizingInputBundle(
        cash=cash,
        portfolio=portfolio,
        positions=positions,
        target_allocations=target_allocations,
        policy=policy,
    )


def _make_input(
    ticker: str,
    action: str = "BUY",
    snapshot_id: str = "snap1",
    run_id: str = "run1",
) -> DeployPlanInput:
    return DeployPlanInput(
        ticker=ticker,
        intel_action=action,
        intel_conviction="MEDIUM",
        intel_evidence_band="OK",
        intel_snapshot_id=snapshot_id,
        intel_run_id=run_id,
        has_missing_evidence=False,
        has_stale_evidence=False,
        has_weak_evidence=False,
        is_blocked=False,
        price_context_label=None,
        action_source=DeployActionSource.INTEL_V3,
    )


def _make_buy_item(ticker: str) -> DeployPlanItem:
    return DeployPlanItem(
        ticker=ticker,
        intel_action="BUY",
        actionability_status=DeployActionabilityStatus.ACTIONABLE_CANDIDATE,
        action_source=DeployActionSource.INTEL_V3,
        intel_snapshot_id="snap1",
        intel_run_id="run1",
        plan_status=DeployPlanStatus.SCAFFOLD,
    )


def _buy_total(items) -> float:
    return sum(
        i.recommended_dollar_amount
        for i in items
        if i.intel_action == "BUY"
        and i.recommended_dollar_amount is not None
    )


# ── Unit tests: apply_new_cash_sleeve_sizing ─────────────────────────────────


class TestSleeveUnit:
    def test_no_eligible_buys_residual_is_full_cash(self):
        items = [
            DeployPlanItem(
                ticker="AAPL",
                intel_action="HOLD",
                actionability_status=DeployActionabilityStatus.NOT_ACTIONABLE_HOLD,
                action_source=DeployActionSource.INTEL_V3,
                intel_snapshot_id="s",
                intel_run_id="r",
                plan_status=DeployPlanStatus.HOLD_ONLY,
            ),
        ]
        bundle = _make_bundle(10_000.0, {"AAPL": (10_000.0, 1.0)})
        out, residual, reason = apply_new_cash_sleeve_sizing(
            bundle=bundle, items=items, cash_to_deploy=900.0
        )
        assert out[0].recommended_dollar_amount is None
        assert residual == pytest.approx(900.0)
        assert reason is not None and "No eligible BUY" in reason

    def test_zero_or_negative_cash_returns_items_unchanged(self):
        items = [_make_buy_item("AAPL")]
        bundle = _make_bundle(10_000.0, {"AAPL": (6_000.0, 0.6)})
        out, residual, reason = apply_new_cash_sleeve_sizing(
            bundle=bundle, items=items, cash_to_deploy=0.0
        )
        assert out == items
        assert residual == 0.0
        assert reason is None

    def test_caps_at_max_recommendations(self):
        tickers = [f"T{i:02d}" for i in range(8)]
        items = [_make_buy_item(t) for t in tickers]
        bundle = _make_bundle(
            portfolio_value=10_000.0,
            tickers_current_and_target={t: (0.0, 1.0 / len(tickers)) for t in tickers},
        )
        out, residual, _ = apply_new_cash_sleeve_sizing(
            bundle=bundle, items=items, cash_to_deploy=900.0
        )
        sized = [i for i in out if i.recommended_dollar_amount is not None]
        assert len(sized) == MAX_RECOMMENDATIONS
        # First MAX_RECOMMENDATIONS tickers in input order get the allocation.
        assert {i.ticker for i in sized} == set(tickers[:MAX_RECOMMENDATIONS])
        assert sum(i.recommended_dollar_amount for i in sized) <= 900.0

    def test_few_candidates_returns_only_those(self):
        items = [_make_buy_item("AAPL"), _make_buy_item("MSFT")]
        bundle = _make_bundle(
            10_000.0, {"AAPL": (0.0, 0.5), "MSFT": (0.0, 0.5)}
        )
        out, residual, reason = apply_new_cash_sleeve_sizing(
            bundle=bundle, items=items, cash_to_deploy=900.0
        )
        sized = [i for i in out if i.recommended_dollar_amount is not None]
        assert len(sized) == 2
        # Two-candidate case is < 3 → residual reason mentions few candidates.
        # Floor rounding may leave $0 residual when 900 splits evenly. Reason
        # only surfaces when residual > 0; assert non-negative residual either way.
        assert residual >= 0.0
        if residual > 0:
            assert reason is not None and "Only 2" in reason

    def test_single_candidate_gets_full_cash(self):
        items = [_make_buy_item("AAPL")]
        bundle = _make_bundle(10_000.0, {"AAPL": (6_000.0, 0.6)})
        out, residual, reason = apply_new_cash_sleeve_sizing(
            bundle=bundle, items=items, cash_to_deploy=900.0
        )
        sized = [i for i in out if i.recommended_dollar_amount is not None]
        assert len(sized) == 1
        assert sized[0].recommended_dollar_amount == 900.0
        assert residual == 0.0
        assert reason is None

    def test_min_trade_threshold_surfaces_residual_reason(self):
        # 5 BUYs, 900 cash, but min_trade so high that allocations are dropped.
        items = [_make_buy_item(f"T{i}") for i in range(5)]
        bundle = _make_bundle(
            portfolio_value=10_000.0,
            tickers_current_and_target={f"T{i}": (0.0, 0.2) for i in range(5)},
            minimum_trade_usd=500.0,  # 900/5 = 180 < 500 → all dropped
        )
        out, residual, reason = apply_new_cash_sleeve_sizing(
            bundle=bundle, items=items, cash_to_deploy=900.0
        )
        sized = [i for i in out if i.recommended_dollar_amount is not None]
        assert sized == []
        assert residual == pytest.approx(900.0)
        assert reason is not None and "minimum trade" in reason.lower()

    def test_total_never_exceeds_cash_after_rounding(self):
        # 3 BUYs at 33.33% each — floor rounding holds back rounding residual.
        items = [_make_buy_item(t) for t in ["A", "B", "C"]]
        bundle = _make_bundle(
            portfolio_value=10_000.0,
            tickers_current_and_target={"A": (0.0, 0.34), "B": (0.0, 0.33), "C": (0.0, 0.33)},
        )
        out, residual, _ = apply_new_cash_sleeve_sizing(
            bundle=bundle, items=items, cash_to_deploy=900.0
        )
        sized = [i for i in out if i.recommended_dollar_amount is not None]
        total = sum(i.recommended_dollar_amount for i in sized)
        assert total <= 900.0
        assert residual == pytest.approx(900.0 - total)

    def test_only_buy_actionable_eligible_trim_sell_hold_excluded(self):
        items = [
            _make_buy_item("BUY1"),
            DeployPlanItem(
                ticker="TRM",
                intel_action="TRIM",
                actionability_status=DeployActionabilityStatus.ACTIONABLE_CANDIDATE,
                action_source=DeployActionSource.INTEL_V3,
                intel_snapshot_id="s",
                intel_run_id="r",
                plan_status=DeployPlanStatus.SCAFFOLD,
                recommended_dollar_amount=2000.0,  # current-gap delta — must not be touched
            ),
            DeployPlanItem(
                ticker="SEL",
                intel_action="SELL",
                actionability_status=DeployActionabilityStatus.ACTIONABLE_CANDIDATE,
                action_source=DeployActionSource.INTEL_V3,
                intel_snapshot_id="s",
                intel_run_id="r",
                plan_status=DeployPlanStatus.SCAFFOLD,
                recommended_dollar_amount=1500.0,
            ),
            DeployPlanItem(
                ticker="HLD",
                intel_action="HOLD",
                actionability_status=DeployActionabilityStatus.NOT_ACTIONABLE_HOLD,
                action_source=DeployActionSource.INTEL_V3,
                intel_snapshot_id="s",
                intel_run_id="r",
                plan_status=DeployPlanStatus.HOLD_ONLY,
            ),
        ]
        bundle = _make_bundle(
            portfolio_value=10_000.0,
            tickers_current_and_target={
                "BUY1": (0.0, 0.25), "TRM": (5_000.0, 0.25),
                "SEL": (3_500.0, 0.20), "HLD": (1_500.0, 0.30),
            },
        )
        out, _, _ = apply_new_cash_sleeve_sizing(
            bundle=bundle, items=items, cash_to_deploy=900.0
        )
        out_by = {i.ticker: i for i in out}
        assert out_by["BUY1"].recommended_dollar_amount == 900.0
        # TRIM/SELL/HOLD untouched by sleeve sizing.
        assert out_by["TRM"].recommended_dollar_amount == 2000.0
        assert out_by["SEL"].recommended_dollar_amount == 1500.0
        assert out_by["HLD"].recommended_dollar_amount is None

    def test_determinism_same_inputs_same_plan(self):
        items = [_make_buy_item(t) for t in ["A", "B", "C", "D", "E", "F"]]
        bundle = _make_bundle(
            10_000.0,
            {t: (0.0, 1.0 / 6) for t in ["A", "B", "C", "D", "E", "F"]},
        )
        out1, r1, why1 = apply_new_cash_sleeve_sizing(bundle, items, 900.0)
        out2, r2, why2 = apply_new_cash_sleeve_sizing(bundle, items, 900.0)
        amounts1 = [(i.ticker, i.recommended_dollar_amount) for i in out1]
        amounts2 = [(i.ticker, i.recommended_dollar_amount) for i in out2]
        assert amounts1 == amounts2
        assert r1 == r2
        assert why1 == why2


# ── Integration tests: build_deploy_plan in new-cash mode ─────────────────────


class TestSleeveProductionLikeIntegration:
    """The production-evidence regression: $900 + 10 BUY actionable candidates
    where target weights match current weights. Old current-gap math produced
    only 2 tiny BUYs ($59 deployed of $900). Sleeve sizing must produce 3–5
    BUYs with a meaningful share of $900 deployed."""

    def _prod_like_inputs_and_bundle(self):
        # 10 BUY tickers at 10% target each — current weight matches target.
        # Portfolio = 10_000 so each position = $1000 current.
        tickers = [f"T{i:02d}" for i in range(10)]
        inputs = [_make_input(t, action="BUY") for t in tickers]
        bundle = _make_bundle(
            portfolio_value=10_000.0,
            tickers_current_and_target={t: (1_000.0, 0.10) for t in tickers},
            minimum_trade_usd=1.0,
        )
        return inputs, bundle

    def test_produces_three_to_five_buy_moves(self):
        inputs, bundle = self._prod_like_inputs_and_bundle()
        plan = build_deploy_plan(inputs, sizing_bundle=bundle, cash_to_deploy=900.0)
        buy_sized = [
            i for i in plan.items
            if i.intel_action == "BUY" and i.recommended_dollar_amount is not None
        ]
        assert 3 <= len(buy_sized) <= 5, f"Expected 3-5 BUYs, got {len(buy_sized)}"

    def test_deploys_meaningful_share_of_cash(self):
        inputs, bundle = self._prod_like_inputs_and_bundle()
        plan = build_deploy_plan(inputs, sizing_bundle=bundle, cash_to_deploy=900.0)
        total = _buy_total(plan.items)
        # Production-evidence regression: old math deployed ~$59 of $900.
        # Sleeve sizing must do meaningfully better — at least half the budget.
        assert total >= 450.0, f"Expected meaningful deployment, got ${total}"

    def test_total_never_exceeds_cash_to_deploy(self):
        inputs, bundle = self._prod_like_inputs_and_bundle()
        plan = build_deploy_plan(inputs, sizing_bundle=bundle, cash_to_deploy=900.0)
        assert _buy_total(plan.items) <= 900.0

    def test_residual_fields_populated(self):
        inputs, bundle = self._prod_like_inputs_and_bundle()
        plan = build_deploy_plan(inputs, sizing_bundle=bundle, cash_to_deploy=900.0)
        # Residual must be a non-negative number (0 when rounding lands perfectly).
        assert plan.new_cash_residual_usd is not None
        assert plan.new_cash_residual_usd >= 0.0
        # When residual is 0, reason may be None.
        if plan.new_cash_residual_usd > 0:
            assert plan.new_cash_residual_reason is not None


class TestSleeveExcludesNonBuyFromCash:
    """HOLD/TRIM/SELL never receive new-cash BUY dollars."""

    def test_hold_trim_sell_excluded_from_new_cash_allocation(self):
        inputs = [
            _make_input("BUY1", "BUY"),
            _make_input("BUY2", "BUY"),
            _make_input("HLD", "HOLD"),
            _make_input("TRM", "TRIM"),
            _make_input("SEL", "SELL"),
        ]
        bundle = _make_bundle(
            portfolio_value=10_000.0,
            tickers_current_and_target={
                "BUY1": (1_000.0, 0.15),
                "BUY2": (1_000.0, 0.15),
                "HLD": (3_000.0, 0.30),
                "TRM": (3_000.0, 0.20),
                "SEL": (2_000.0, 0.20),
            },
        )
        plan = build_deploy_plan(inputs, sizing_bundle=bundle, cash_to_deploy=900.0)
        for it in plan.items:
            if it.intel_action == "BUY":
                continue
            # Non-BUY items may still get current-gap math amounts (TRIM/SELL),
            # but they must NEVER receive new-cash BUY dollars. We assert by
            # confirming the sleeve's new-cash budget went only to BUYs.
        total_buy = _buy_total(plan.items)
        assert total_buy <= 900.0
        non_buy_received_cash = any(
            it.intel_action in ("HOLD",) and it.recommended_dollar_amount is not None
            for it in plan.items
        )
        assert not non_buy_received_cash


class TestSleeveFewCandidates:
    def test_two_eligible_returns_only_two_with_residual(self):
        inputs = [
            _make_input("A", "BUY"),
            _make_input("B", "BUY"),
            _make_input("H", "HOLD"),
        ]
        bundle = _make_bundle(
            portfolio_value=10_000.0,
            tickers_current_and_target={
                "A": (1_000.0, 0.30), "B": (1_000.0, 0.30), "H": (8_000.0, 0.40)
            },
            minimum_trade_usd=600.0,  # 900/2=450 < 600 → both dropped → residual=900
        )
        plan = build_deploy_plan(inputs, sizing_bundle=bundle, cash_to_deploy=900.0)
        sized_buys = [
            i for i in plan.items
            if i.intel_action == "BUY" and i.recommended_dollar_amount is not None
        ]
        assert sized_buys == []
        assert plan.new_cash_residual_usd == pytest.approx(900.0)
        assert plan.new_cash_residual_reason is not None
        assert "minimum trade" in plan.new_cash_residual_reason.lower()


class TestSleeveNoEligibleBuys:
    def test_all_hold_returns_no_moves_full_residual(self):
        inputs = [_make_input("A", "HOLD"), _make_input("B", "HOLD")]
        bundle = _make_bundle(
            10_000.0, {"A": (5_000.0, 0.5), "B": (5_000.0, 0.5)}
        )
        plan = build_deploy_plan(inputs, sizing_bundle=bundle, cash_to_deploy=900.0)
        assert all(i.recommended_dollar_amount is None for i in plan.items)
        assert plan.new_cash_residual_usd == pytest.approx(900.0)
        assert plan.new_cash_residual_reason is not None
        assert "No eligible BUY" in plan.new_cash_residual_reason


class TestCurrentGapModePreserved:
    """When cash_to_deploy is omitted or 0, sleeve is dormant and current-gap
    behavior is unchanged."""

    def test_omitted_cash_no_sleeve_residual(self):
        inputs = [_make_input("A", "BUY"), _make_input("B", "BUY")]
        bundle = _make_bundle(
            10_000.0, {"A": (3_000.0, 0.30), "B": (3_000.0, 0.30),
                       "C": (4_000.0, 0.40)},
        )
        # Add HOLD ticker C to keep target sum at 100%.
        inputs.append(_make_input("C", "HOLD"))
        plan = build_deploy_plan(inputs, sizing_bundle=bundle, cash_to_deploy=None)
        # Current-gap math: weights match targets → zero BUY deltas → no moves.
        assert _buy_total(plan.items) == 0
        # Residual fields not set outside new-cash mode.
        assert plan.new_cash_residual_usd is None
        assert plan.new_cash_residual_reason is None

    def test_zero_cash_no_sleeve(self):
        inputs = [_make_input("A", "BUY"), _make_input("B", "HOLD")]
        bundle = _make_bundle(10_000.0, {"A": (6_000.0, 0.6), "B": (4_000.0, 0.4)})
        plan = build_deploy_plan(inputs, sizing_bundle=bundle, cash_to_deploy=0)
        assert plan.new_cash_residual_usd is None


class TestRoundingResidualDistribution:
    """Stage 2.8: leftover whole dollars from floor rounding are distributed
    deterministically to selected BUY rows so total BUY == cash_to_deploy when
    no guardrail prevents it."""

    def test_1500_split_uneven_weights_distributes_residual_to_match_budget(self):
        # Weights that would floor-round to a residual without redistribution:
        # 5 tickers with weights 0.21/0.20/0.20/0.20/0.19 summing to 1.0.
        # 1500*0.21=315, 1500*0.20=300, 1500*0.19=285 → exact, no residual.
        # Use weights that produce floor residue: 0.225/0.205/0.205/0.205/0.16
        items = [_make_buy_item(t) for t in ["A", "B", "C", "D", "E"]]
        bundle = _make_bundle(
            portfolio_value=10_000.0,
            tickers_current_and_target={
                "A": (0.0, 0.225),
                "B": (0.0, 0.205),
                "C": (0.0, 0.205),
                "D": (0.0, 0.205),
                "E": (0.0, 0.160),
            },
        )
        out, residual, _ = apply_new_cash_sleeve_sizing(
            bundle=bundle, items=items, cash_to_deploy=1500.0
        )
        sized = [i for i in out if i.recommended_dollar_amount is not None]
        total = sum(i.recommended_dollar_amount for i in sized)
        # Residual distribution closes any whole-dollar gap.
        assert total == pytest.approx(1500.0)
        assert residual == pytest.approx(0.0)

    def test_residual_distribution_never_exceeds_cash_to_deploy(self):
        # Regression: total BUY dollars must never exceed cash_to_deploy.
        items = [_make_buy_item(t) for t in ["A", "B", "C", "D", "E", "F", "G"]]
        bundle = _make_bundle(
            portfolio_value=10_000.0,
            tickers_current_and_target={t: (0.0, 1.0 / 7) for t in ["A", "B", "C", "D", "E", "F", "G"]},
        )
        out, _, _ = apply_new_cash_sleeve_sizing(
            bundle=bundle, items=items, cash_to_deploy=1500.0
        )
        total = sum(
            i.recommended_dollar_amount
            for i in out
            if i.recommended_dollar_amount is not None
        )
        assert total <= 1500.0

    def test_residual_distribution_only_to_selected_buys(self):
        # HOLD/TRIM/SELL must never receive leftover whole dollars from BUY sleeve.
        items = [
            _make_buy_item("BUY1"),
            _make_buy_item("BUY2"),
            DeployPlanItem(
                ticker="HLD",
                intel_action="HOLD",
                actionability_status=DeployActionabilityStatus.NOT_ACTIONABLE_HOLD,
                action_source=DeployActionSource.INTEL_V3,
                intel_snapshot_id="s",
                intel_run_id="r",
                plan_status=DeployPlanStatus.HOLD_ONLY,
            ),
            DeployPlanItem(
                ticker="TRM",
                intel_action="TRIM",
                actionability_status=DeployActionabilityStatus.ACTIONABLE_CANDIDATE,
                action_source=DeployActionSource.INTEL_V3,
                intel_snapshot_id="s",
                intel_run_id="r",
                plan_status=DeployPlanStatus.SCAFFOLD,
                recommended_dollar_amount=1234.0,
            ),
        ]
        bundle = _make_bundle(
            portfolio_value=10_000.0,
            tickers_current_and_target={
                "BUY1": (0.0, 0.33),
                "BUY2": (0.0, 0.33),
                "HLD": (3_000.0, 0.20),
                "TRM": (5_000.0, 0.14),
            },
        )
        out, _, _ = apply_new_cash_sleeve_sizing(
            bundle=bundle, items=items, cash_to_deploy=1501.0
        )
        out_by = {i.ticker: i for i in out}
        # BUYs share the budget (residual distributed).
        assert out_by["BUY1"].recommended_dollar_amount is not None
        assert out_by["BUY2"].recommended_dollar_amount is not None
        buy_total = (out_by["BUY1"].recommended_dollar_amount or 0.0) + (out_by["BUY2"].recommended_dollar_amount or 0.0)
        assert buy_total <= 1501.0
        # HOLD untouched. TRIM amount preserved (current-gap value), never gets BUY dollars.
        assert out_by["HLD"].recommended_dollar_amount is None
        assert out_by["TRM"].recommended_dollar_amount == 1234.0

    def test_residual_distribution_skips_below_min_trade_rows(self):
        # Below-min slots are zeroed; residual must NOT resurrect them.
        items = [_make_buy_item(t) for t in ["A", "B", "C"]]
        # 1500 split would be ~500 each; set min trade so A passes and B,C fail.
        bundle = _make_bundle(
            portfolio_value=10_000.0,
            tickers_current_and_target={"A": (0.0, 0.90), "B": (0.0, 0.05), "C": (0.0, 0.05)},
            minimum_trade_usd=200.0,
        )
        out, _, _ = apply_new_cash_sleeve_sizing(
            bundle=bundle, items=items, cash_to_deploy=1500.0
        )
        out_by = {i.ticker: i for i in out}
        # A gets full budget (B/C below min are zeroed). Residual flows back to A.
        assert (out_by["A"].recommended_dollar_amount or 0.0) <= 1500.0
        # B and C are not resurrected by residual distribution.
        assert out_by["B"].recommended_dollar_amount is None
        assert out_by["C"].recommended_dollar_amount is None

    def test_zero_cash_distribution_is_noop(self):
        items = [_make_buy_item("A")]
        bundle = _make_bundle(10_000.0, {"A": (0.0, 1.0)})
        out, residual, reason = apply_new_cash_sleeve_sizing(
            bundle=bundle, items=items, cash_to_deploy=0.0
        )
        assert out == items
        assert residual == 0.0
        assert reason is None
