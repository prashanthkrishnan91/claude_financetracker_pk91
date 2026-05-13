"""Tests — Stage 2.6C: Deploy v3 amount-aware new-cash planning.

Covers the acceptance criteria from the task brief:
  - cash_to_deploy omitted preserves existing current-gap behavior (amount_aware=False).
  - cash_to_deploy=0 preserves current-gap behavior.
  - cash_to_deploy=900 with current weights equal targets produces BUY recommendations
    (post-cash target dollars increase).
  - Only Intel BUY ACTIONABLE_CANDIDATE items receive new-cash dollars.
  - HOLD items never receive new-cash BUY dollars.
  - Total recommended BUY dollars never exceeds cash_to_deploy after rounding.
  - Below-minimum final recommendations are suppressed.
  - No eligible BUY candidates returns no_moves, not fabricated recommendations.
  - Route response exposes amount_aware, cash_to_deploy, sizing_mode in source metadata.
  - cap_buy_amounts_to_cash correctly caps proportionally.
  - Dollar math: new-cash BUY delta uses portfolio_value + cash_to_deploy reference.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.intelligence.v3.decision_contracts import (
    ActionV3,
    AxisBand,
    ConvictionV3,
    DecisionOutputV3,
    FitBand,
    PriceBand,
    RiskBand,
)
from app.services.intelligence.v3.snapshot_builder import build_snapshot
from app.services.deploy.deploy_sizing_contracts import (
    DeployCashInput,
    DeployPortfolioSizingInput,
    DeployPositionSizingInput,
    DeploySizingInputBundle,
    DeploySizingTrustStatus,
)


# ── Module-level autouse fixture (mirrors existing router test file) ───────────

@pytest.fixture(autouse=True)
def _patch_sizing_adapter():
    """Default: adapter returns None (no portfolio snapshot available)."""
    with patch(
        "app.routers.deploy_v3.build_sizing_bundle_from_persisted_data",
        new_callable=AsyncMock,
        return_value=None,
    ):
        yield


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_output(ticker: str, action: ActionV3 = ActionV3.HOLD) -> DecisionOutputV3:
    return DecisionOutputV3(
        ticker=ticker,
        action=action,
        conviction=ConvictionV3.MEDIUM,
        evidence_quality=AxisBand.OK,
        attractiveness=AxisBand.OK,
        price_context=PriceBand.FAIR,
        portfolio_fit=FitBand.ON_TARGET,
        risk_band=RiskBand.LOW,
        blockers=[],
        suppression_reasons={},
        rationale_plain_english="Signals support this position.",
        why_now="Evidence and fit support acting now.",
        why_not_now="Watch for evidence weakening.",
        source_signal_summary={},
        schema_version="v3.1",
    )


def _make_snapshot(*ticker_actions: tuple[str, ActionV3]) -> dict:
    decisions = [_make_output(t, a) for t, a in ticker_actions]
    metas = [
        {"ticker": t, "name": t, "category": "stock", "thesis_state": "intact"}
        for t, _ in ticker_actions
    ]
    return build_snapshot(
        run_id="test-run-amount-aware",
        decisions=decisions,
        card_metas=metas,
        source_health={"status": "ok"},
        is_stale=False,
    )


def _mock_intel_service(snapshot_or_none):
    mock_svc = MagicMock()
    mock_svc.get_latest_snapshot = AsyncMock(return_value=snapshot_or_none)
    return mock_svc


def _make_exact_dollar_bundle(
    portfolio_value: float = 10_000.0,
    cash_balance: float = 0.0,
    tickers_and_weights: dict | None = None,
    minimum_trade_usd: float = 1.0,
    rounding_policy: str = "WHOLE_DOLLAR",
) -> DeploySizingInputBundle:
    """Build a fully certified sizing bundle for amount-aware tests.

    tickers_and_weights: {ticker: (current_value, target_weight)}
      current_weight is derived from current_value / portfolio_value.
    """
    from app.services.deploy.deploy_policy_bridge import certify_sizing_policy
    from app.services.deploy.deploy_target_allocation_bridge import certify_target_allocation

    if tickers_and_weights is None:
        tickers_and_weights = {"AAPL": (6_000.0, 0.60), "MSFT": (4_000.0, 0.40)}

    cash = DeployCashInput(
        available_cash_usd=cash_balance,
        trust_status=DeploySizingTrustStatus.CERTIFIED,
        source_label="portfolio_snapshots:test",
    )
    portfolio = DeployPortfolioSizingInput(
        total_portfolio_value_usd=portfolio_value,
        trust_status=DeploySizingTrustStatus.CERTIFIED,
        source_label="portfolio_snapshots:test",
    )
    positions = {}
    target_allocations = {}
    for ticker, (cur_val, target_w) in tickers_and_weights.items():
        positions[ticker] = DeployPositionSizingInput(
            ticker=ticker,
            current_market_value_usd=cur_val,
            current_weight=cur_val / portfolio_value if portfolio_value > 0 else 0.0,
            trust_status=DeploySizingTrustStatus.CERTIFIED,
            source_label="portfolio_snapshots:test",
        )
        target_allocations[ticker] = certify_target_allocation(
            ticker=ticker,
            target_weight=target_w,
            source_label="target_allocations_table",
        )

    policy = certify_sizing_policy(minimum_trade_usd, rounding_policy)
    return DeploySizingInputBundle(
        cash=cash,
        portfolio=portfolio,
        positions=positions,
        target_allocations=target_allocations,
        policy=policy,
    )


def _call_plan(snapshot: dict, bundle, cash_to_deploy=None) -> dict:
    """Call get_deploy_v3_plan with a given snapshot, bundle, and optional cash_to_deploy."""
    from app.routers.deploy_v3 import get_deploy_v3_plan

    mock_user = MagicMock()
    mock_user.id = uuid.UUID("00000000-0000-0000-0000-aabbccddeeff")

    # Always pass cash_to_deploy explicitly so FastAPI's Query() object
    # is never used as the default (which breaks direct function calls in tests).
    kwargs = {"user": mock_user, "cash_to_deploy": cash_to_deploy}

    with (
        patch.dict(os.environ, {"INTEL_V3_VISIBLE_SNAPSHOT_ENABLED": "true"}),
        patch(
            "app.routers.deploy_v3.IntelV3Service",
            return_value=_mock_intel_service(snapshot),
        ),
        patch(
            "app.routers.deploy_v3.build_sizing_bundle_from_persisted_data",
            new_callable=AsyncMock,
            return_value=bundle,
        ),
    ):
        return asyncio.run(get_deploy_v3_plan(**kwargs))


# ── Dollar math unit tests (no IO) ───────────────────────────────────────────

class TestNewCashDollarMath:
    """Unit tests for compute_dollar_amount_for_item and cap_buy_amounts_to_cash."""

    def _make_bundle_for_math(self) -> DeploySizingInputBundle:
        # Portfolio: $10k total; AAPL at $6k (60%), MSFT at $4k (40%)
        return _make_exact_dollar_bundle(
            portfolio_value=10_000.0,
            cash_balance=0.0,
            tickers_and_weights={"AAPL": (6_000.0, 0.60), "MSFT": (4_000.0, 0.40)},
        )

    def test_buy_delta_zero_in_current_gap_when_weights_match_targets(self):
        """BUY delta is zero (no action) in current-gap mode when weights already match targets."""
        from app.services.deploy.deploy_dollar_math_v1 import compute_dollar_amount_for_item
        from app.services.deploy.deploy_contracts import DeployActionabilityStatus, DeployPlanItem

        bundle = self._make_bundle_for_math()
        item = DeployPlanItem(
            ticker="AAPL",
            intel_action="BUY",
            actionability_status=DeployActionabilityStatus.ACTIONABLE_CANDIDATE,
            action_source="intel_v3",
            intel_snapshot_id="snap1",
            intel_run_id="run1",
            plan_status="SCAFFOLD",
        )
        # No cash_to_deploy: current-gap math, delta = 0.6*10000 - 6000 = 0 → suppressed
        result = compute_dollar_amount_for_item(bundle=bundle, item=item, cash_to_deploy=None)
        assert result.recommended_dollar_amount is None

    def test_buy_delta_positive_in_new_cash_mode(self):
        """BUY delta is positive in new-cash mode even when weights match targets."""
        from app.services.deploy.deploy_dollar_math_v1 import compute_dollar_amount_for_item
        from app.services.deploy.deploy_contracts import DeployActionabilityStatus, DeployPlanItem

        bundle = self._make_bundle_for_math()
        item = DeployPlanItem(
            ticker="AAPL",
            intel_action="BUY",
            actionability_status=DeployActionabilityStatus.ACTIONABLE_CANDIDATE,
            action_source="intel_v3",
            intel_snapshot_id="snap1",
            intel_run_id="run1",
            plan_status="SCAFFOLD",
        )
        cash = 900.0
        result = compute_dollar_amount_for_item(bundle=bundle, item=item, cash_to_deploy=cash)
        # Expected: 0.6 * (10000 + 900) - 6000 = 6540 - 6000 = 540
        assert result.recommended_dollar_amount is not None
        assert result.recommended_dollar_amount == pytest.approx(540.0, abs=1.0)

    def test_trim_uses_current_gap_in_new_cash_mode(self):
        """TRIM always uses current-gap math even when cash_to_deploy is provided."""
        from app.services.deploy.deploy_dollar_math_v1 import compute_dollar_amount_for_item
        from app.services.deploy.deploy_contracts import DeployActionabilityStatus, DeployPlanItem
        from app.services.deploy.deploy_policy_bridge import certify_sizing_policy
        from app.services.deploy.deploy_target_allocation_bridge import certify_target_allocation

        # Portfolio: AAPL over-target: 70% current vs 50% target
        bundle = _make_exact_dollar_bundle(
            portfolio_value=10_000.0,
            cash_balance=0.0,
            tickers_and_weights={"AAPL": (7_000.0, 0.50), "MSFT": (3_000.0, 0.50)},
        )
        item = DeployPlanItem(
            ticker="AAPL",
            intel_action="TRIM",
            actionability_status=DeployActionabilityStatus.ACTIONABLE_CANDIDATE,
            action_source="intel_v3",
            intel_snapshot_id="snap1",
            intel_run_id="run1",
            plan_status="SCAFFOLD",
        )
        result = compute_dollar_amount_for_item(bundle=bundle, item=item, cash_to_deploy=900.0)
        # TRIM delta: 7000 - (0.5 * 10000) = 7000 - 5000 = 2000 (current-gap, not new-cash)
        assert result.recommended_dollar_amount is not None
        assert result.recommended_dollar_amount == pytest.approx(2000.0, abs=1.0)

    def test_hold_never_gets_dollar_amount_in_new_cash_mode(self):
        """HOLD item never receives dollar amount regardless of cash_to_deploy."""
        from app.services.deploy.deploy_dollar_math_v1 import compute_dollar_amount_for_item
        from app.services.deploy.deploy_contracts import DeployActionabilityStatus, DeployPlanItem

        bundle = self._make_bundle_for_math()
        item = DeployPlanItem(
            ticker="AAPL",
            intel_action="HOLD",
            actionability_status=DeployActionabilityStatus.NOT_ACTIONABLE_HOLD,
            action_source="intel_v3",
            intel_snapshot_id="snap1",
            intel_run_id="run1",
            plan_status="HOLD_ONLY",
        )
        result = compute_dollar_amount_for_item(bundle=bundle, item=item, cash_to_deploy=900.0)
        assert result.recommended_dollar_amount is None


class TestCapBuyAmounts:
    """Unit tests for cap_buy_amounts_to_cash."""

    def _make_buy_item(self, ticker: str, dollar_amount: float):
        from app.services.deploy.deploy_contracts import DeployActionabilityStatus, DeployPlanItem
        return DeployPlanItem(
            ticker=ticker,
            intel_action="BUY",
            actionability_status=DeployActionabilityStatus.ACTIONABLE_CANDIDATE,
            action_source="intel_v3",
            intel_snapshot_id="snap1",
            intel_run_id="run1",
            plan_status="SCAFFOLD",
            recommended_dollar_amount=dollar_amount,
        )

    def test_no_cap_when_total_buy_within_budget(self):
        """Items unchanged when total BUY dollars <= cash_to_deploy."""
        from app.services.deploy.deploy_dollar_math_v1 import cap_buy_amounts_to_cash

        items = [
            self._make_buy_item("AAPL", 200.0),
            self._make_buy_item("MSFT", 300.0),
        ]
        result = cap_buy_amounts_to_cash(items, cash_to_deploy=900.0, minimum_trade_usd=1.0)
        assert result[0].recommended_dollar_amount == 200.0
        assert result[1].recommended_dollar_amount == 300.0

    def test_total_buy_never_exceeds_cash_to_deploy_after_cap(self):
        """Total BUY dollars never exceed cash_to_deploy after cap."""
        from app.services.deploy.deploy_dollar_math_v1 import cap_buy_amounts_to_cash

        items = [
            self._make_buy_item("AAPL", 600.0),
            self._make_buy_item("MSFT", 700.0),
        ]
        result = cap_buy_amounts_to_cash(items, cash_to_deploy=900.0, minimum_trade_usd=1.0)
        total = sum(
            i.recommended_dollar_amount
            for i in result
            if i.recommended_dollar_amount is not None
        )
        assert total <= 900.0

    def test_items_below_minimum_suppressed_after_cap(self):
        """Items below minimum_trade_usd after pro-rating are suppressed (None)."""
        from app.services.deploy.deploy_dollar_math_v1 import cap_buy_amounts_to_cash

        # After cap: 100 * (50 / 10100) ≈ 0.50 < min_trade=1
        items = [
            self._make_buy_item("AAPL", 10_000.0),
            self._make_buy_item("GOOG", 100.0),
        ]
        result = cap_buy_amounts_to_cash(
            items, cash_to_deploy=50.0, minimum_trade_usd=5.0, rounding_policy="WHOLE_DOLLAR"
        )
        # Small item should be suppressed; large item should get most of budget
        small_item = next(i for i in result if i.ticker == "GOOG")
        assert small_item.recommended_dollar_amount is None

    def test_non_buy_items_unchanged_by_cap(self):
        """TRIM/SELL/HOLD items are never modified by cap_buy_amounts_to_cash."""
        from app.services.deploy.deploy_dollar_math_v1 import cap_buy_amounts_to_cash
        from app.services.deploy.deploy_contracts import DeployActionabilityStatus, DeployPlanItem

        trim_item = DeployPlanItem(
            ticker="NVDA",
            intel_action="TRIM",
            actionability_status=DeployActionabilityStatus.ACTIONABLE_CANDIDATE,
            action_source="intel_v3",
            intel_snapshot_id="s",
            intel_run_id="r",
            plan_status="SCAFFOLD",
            recommended_dollar_amount=500.0,
        )
        buy_item = self._make_buy_item("AAPL", 2000.0)
        result = cap_buy_amounts_to_cash(
            [trim_item, buy_item], cash_to_deploy=100.0, minimum_trade_usd=1.0
        )
        trim_result = next(i for i in result if i.ticker == "NVDA")
        assert trim_result.recommended_dollar_amount == 500.0

    def test_adversarial_rounding_total_never_exceeds_budget(self):
        """Adversarial rounding: independent rounding would exceed budget; floor prevents it.

        3 BUY items at $67 each (total $201), budget $200.
        scale = 200/201 ≈ 0.995; scaled = 66.67.
        Independent WHOLE_DOLLAR rounding gives $67 each → $201 > $200.
        Floor gives $66 each → $198 ≤ $200.
        """
        from app.services.deploy.deploy_dollar_math_v1 import cap_buy_amounts_to_cash

        items = [
            self._make_buy_item("AAPL", 67.0),
            self._make_buy_item("MSFT", 67.0),
            self._make_buy_item("GOOG", 67.0),
        ]
        result = cap_buy_amounts_to_cash(
            items, cash_to_deploy=200.0, minimum_trade_usd=1.0, rounding_policy="WHOLE_DOLLAR"
        )
        total = sum(
            i.recommended_dollar_amount
            for i in result
            if i.recommended_dollar_amount is not None
        )
        assert total <= 200.0, f"Total {total} exceeded budget 200"

    def test_original_items_not_mutated(self):
        """cap_buy_amounts_to_cash never mutates the original item list."""
        from app.services.deploy.deploy_dollar_math_v1 import cap_buy_amounts_to_cash

        original_amount = 600.0
        items = [self._make_buy_item("AAPL", original_amount)]
        cap_buy_amounts_to_cash(items, cash_to_deploy=100.0, minimum_trade_usd=1.0)
        assert items[0].recommended_dollar_amount == original_amount


# ── Router integration: amount-aware mode ────────────────────────────────────

class TestAmountAwareCurrentGapPreservation:
    """Omitting or passing 0 for cash_to_deploy preserves existing current-gap behavior."""

    def test_omitting_cash_to_deploy_gives_current_gap_mode(self):
        """No cash_to_deploy → amount_aware=False, sizing_mode=current_gap."""
        snap = _make_snapshot(("AAPL", ActionV3.BUY), ("MSFT", ActionV3.HOLD))
        bundle = _make_exact_dollar_bundle(
            portfolio_value=10_000.0,
            cash_balance=0.0,
            tickers_and_weights={"AAPL": (6_000.0, 0.60), "MSFT": (4_000.0, 0.40)},
        )
        result = _call_plan(snap, bundle)  # no cash_to_deploy
        source = result["source"]
        assert source["amount_aware"] is False
        assert source["sizing_mode"] == "current_gap"
        assert source.get("cash_to_deploy") is None

    def test_zero_cash_to_deploy_gives_current_gap_mode(self):
        """cash_to_deploy=0 → amount_aware=False, sizing_mode=current_gap."""
        snap = _make_snapshot(("AAPL", ActionV3.BUY))
        bundle = _make_exact_dollar_bundle(
            portfolio_value=10_000.0,
            cash_balance=0.0,
            tickers_and_weights={"AAPL": (6_000.0, 1.0)},
        )
        result = _call_plan(snap, bundle, cash_to_deploy=0)
        source = result["source"]
        assert source["amount_aware"] is False
        assert source["sizing_mode"] == "current_gap"

    def test_current_gap_buy_zero_delta_no_moves_when_weights_match_targets(self):
        """When weights match targets and cash_to_deploy omitted, BUY produces no moves."""
        snap = _make_snapshot(("AAPL", ActionV3.BUY))
        bundle = _make_exact_dollar_bundle(
            portfolio_value=10_000.0,
            cash_balance=0.0,
            tickers_and_weights={"AAPL": (10_000.0, 1.0)},
        )
        result = _call_plan(snap, bundle)
        buy_items = [i for i in result["items"] if i["intel_action"] == "BUY"]
        assert all(i["recommended_dollar_amount"] is None for i in buy_items)


class TestAmountAwareBuyRecommendations:
    """cash_to_deploy=900 produces BUY recommendations when weights match targets."""

    def test_amount_aware_source_metadata(self):
        """source.amount_aware=True, cash_to_deploy set, sizing_mode=new_cash."""
        snap = _make_snapshot(("AAPL", ActionV3.BUY), ("MSFT", ActionV3.HOLD))
        bundle = _make_exact_dollar_bundle(
            portfolio_value=10_000.0,
            cash_balance=0.0,
            tickers_and_weights={"AAPL": (6_000.0, 0.60), "MSFT": (4_000.0, 0.40)},
        )
        result = _call_plan(snap, bundle, cash_to_deploy=900.0)
        source = result["source"]
        assert source["amount_aware"] is True
        assert source["cash_to_deploy"] == 900.0
        assert source["sizing_mode"] == "new_cash"
        assert source["cash_source"] == "user_entered_planning_capital"

    def test_buy_item_has_positive_dollar_amount_when_weights_match_targets(self):
        """AAPL BUY item has positive dollar amount in amount-aware mode with matching weights."""
        snap = _make_snapshot(("AAPL", ActionV3.BUY), ("MSFT", ActionV3.HOLD))
        bundle = _make_exact_dollar_bundle(
            portfolio_value=10_000.0,
            cash_balance=0.0,
            tickers_and_weights={"AAPL": (6_000.0, 0.60), "MSFT": (4_000.0, 0.40)},
        )
        result = _call_plan(snap, bundle, cash_to_deploy=900.0)
        buy_items = [i for i in result["items"] if i["intel_action"] == "BUY"]
        assert buy_items, "Expected at least one BUY item"
        for item in buy_items:
            assert item["recommended_dollar_amount"] is not None
            assert item["recommended_dollar_amount"] > 0

    def test_hold_item_has_no_dollar_amount_in_amount_aware_mode(self):
        """HOLD item always has null dollar amount even in amount-aware mode."""
        snap = _make_snapshot(("AAPL", ActionV3.BUY), ("MSFT", ActionV3.HOLD))
        bundle = _make_exact_dollar_bundle(
            portfolio_value=10_000.0,
            cash_balance=0.0,
            tickers_and_weights={"AAPL": (6_000.0, 0.60), "MSFT": (4_000.0, 0.40)},
        )
        result = _call_plan(snap, bundle, cash_to_deploy=900.0)
        hold_items = [i for i in result["items"] if i["intel_action"] == "HOLD"]
        for item in hold_items:
            assert item["recommended_dollar_amount"] is None

    def test_total_buy_dollars_never_exceeds_cash_to_deploy(self):
        """Sum of all BUY recommended_dollar_amount values <= cash_to_deploy."""
        snap = _make_snapshot(
            ("AAPL", ActionV3.BUY),
            ("GOOG", ActionV3.BUY),
            ("MSFT", ActionV3.HOLD),
        )
        bundle = _make_exact_dollar_bundle(
            portfolio_value=10_000.0,
            cash_balance=0.0,
            tickers_and_weights={
                "AAPL": (3_000.0, 0.30),
                "GOOG": (3_000.0, 0.30),
                "MSFT": (4_000.0, 0.40),
            },
        )
        cash_to_deploy = 900.0
        result = _call_plan(snap, bundle, cash_to_deploy=cash_to_deploy)
        total_buy = sum(
            i["recommended_dollar_amount"]
            for i in result["items"]
            if i["intel_action"] == "BUY" and i["recommended_dollar_amount"] is not None
        )
        assert total_buy <= cash_to_deploy + 0.01  # +0.01 for rounding tolerance

    def test_buy_item_final_status_actionable_pending_tax_in_amount_aware_mode(self):
        """BUY item with positive dollar amount and cash passed = actionable_pending_tax."""
        snap = _make_snapshot(("AAPL", ActionV3.BUY), ("MSFT", ActionV3.HOLD))
        bundle = _make_exact_dollar_bundle(
            portfolio_value=10_000.0,
            cash_balance=0.0,
            tickers_and_weights={"AAPL": (6_000.0, 0.60), "MSFT": (4_000.0, 0.40)},
        )
        result = _call_plan(snap, bundle, cash_to_deploy=900.0)
        buy_items = [i for i in result["items"] if i["intel_action"] == "BUY"]
        assert buy_items
        for item in buy_items:
            if item["recommended_dollar_amount"] is not None and item["recommended_dollar_amount"] > 0:
                assert item["final_actionability_status"] == "actionable_pending_tax", (
                    f"Expected actionable_pending_tax for {item['ticker']}, "
                    f"got {item['final_actionability_status']}"
                )

    def test_intel_action_preserved_in_amount_aware_mode(self):
        """Intel v3 actions are never changed by amount-aware mode."""
        snap = _make_snapshot(("AAPL", ActionV3.BUY), ("MSFT", ActionV3.HOLD))
        bundle = _make_exact_dollar_bundle(
            portfolio_value=10_000.0,
            cash_balance=0.0,
            tickers_and_weights={"AAPL": (6_000.0, 0.60), "MSFT": (4_000.0, 0.40)},
        )
        result = _call_plan(snap, bundle, cash_to_deploy=900.0)
        actions = {i["ticker"]: i["intel_action"] for i in result["items"]}
        assert actions.get("AAPL") == "BUY"
        assert actions.get("MSFT") == "HOLD"


class TestAmountAwareNoBuyCandidates:
    """When no eligible BUY candidates exist, amount-aware mode returns no BUY moves."""

    def test_all_hold_snapshot_produces_no_buy_moves_in_amount_aware_mode(self):
        """All-HOLD snapshot produces no BUY dollar amounts even with cash_to_deploy."""
        snap = _make_snapshot(("AAPL", ActionV3.HOLD), ("MSFT", ActionV3.HOLD))
        bundle = _make_exact_dollar_bundle(
            portfolio_value=10_000.0,
            cash_balance=0.0,
            tickers_and_weights={"AAPL": (5_000.0, 0.50), "MSFT": (5_000.0, 0.50)},
        )
        result = _call_plan(snap, bundle, cash_to_deploy=900.0)
        for item in result["items"]:
            assert item["recommended_dollar_amount"] is None

    def test_all_hold_snapshot_rollup_is_all_informational(self):
        """All-HOLD snapshot rollup is all_informational, not fabricated."""
        snap = _make_snapshot(("AAPL", ActionV3.HOLD), ("MSFT", ActionV3.HOLD))
        bundle = _make_exact_dollar_bundle(
            portfolio_value=10_000.0,
            cash_balance=0.0,
            tickers_and_weights={"AAPL": (5_000.0, 0.50), "MSFT": (5_000.0, 0.50)},
        )
        result = _call_plan(snap, bundle, cash_to_deploy=900.0)
        assert result["rollup"]["plan_readiness_status"] == "all_informational"


class TestAmountAwareTrimSellNotChanged:
    """TRIM/SELL handling remains current-gap only in amount-aware mode."""

    def test_trim_uses_current_gap_in_amount_aware_mode(self):
        """TRIM dollar amounts use current-gap math even when cash_to_deploy is passed."""
        # AAPL over-weight: 70% actual vs 50% target — TRIM has positive delta
        snap = _make_snapshot(("AAPL", ActionV3.TRIM), ("MSFT", ActionV3.BUY))
        bundle = _make_exact_dollar_bundle(
            portfolio_value=10_000.0,
            cash_balance=0.0,
            tickers_and_weights={"AAPL": (7_000.0, 0.50), "MSFT": (3_000.0, 0.50)},
        )
        result = _call_plan(snap, bundle, cash_to_deploy=900.0)
        trim_items = [i for i in result["items"] if i["intel_action"] == "TRIM"]
        assert trim_items
        trim_item = trim_items[0]
        # TRIM delta: 7000 - (0.5 * 10000) = 2000 (current-gap, not affected by cash_to_deploy)
        assert trim_item["recommended_dollar_amount"] == pytest.approx(2000.0, abs=1.0)


class TestAmountAwareNoBundlePreservesScaffold:
    """Without a sizing bundle, amount-aware flag is still present and scaffold is honest."""

    def test_no_bundle_gives_amount_aware_false(self):
        """When no sizing bundle exists, amount_aware is False regardless of cash_to_deploy."""
        snap = _make_snapshot(("AAPL", ActionV3.BUY))
        result = _call_plan(snap, None, cash_to_deploy=900.0)
        source = result["source"]
        assert source["amount_aware"] is False
        assert source["sizing_bundle_provided"] is False

    def test_no_bundle_dollar_fields_null_even_with_cash_to_deploy(self):
        """Dollar fields remain null without a bundle, even with cash_to_deploy."""
        snap = _make_snapshot(("AAPL", ActionV3.BUY))
        result = _call_plan(snap, None, cash_to_deploy=900.0)
        for item in result["items"]:
            assert item["recommended_dollar_amount"] is None


class TestAmountAwareSourceMetadataRequiredFields:
    """source block always exposes amount_aware, cash_to_deploy, sizing_mode."""

    def test_source_has_amount_aware_field_with_no_bundle(self):
        snap = _make_snapshot(("AAPL", ActionV3.BUY))
        result = _call_plan(snap, None)
        assert "amount_aware" in result["source"]

    def test_source_has_sizing_mode_field_with_no_bundle(self):
        snap = _make_snapshot(("AAPL", ActionV3.BUY))
        result = _call_plan(snap, None)
        assert "sizing_mode" in result["source"]

    def test_source_has_cash_to_deploy_field_with_bundle(self):
        snap = _make_snapshot(("AAPL", ActionV3.BUY))
        bundle = _make_exact_dollar_bundle(
            portfolio_value=10_000.0,
            cash_balance=0.0,
            tickers_and_weights={"AAPL": (6_000.0, 1.0)},
        )
        result = _call_plan(snap, bundle, cash_to_deploy=500.0)
        assert "cash_to_deploy" in result["source"]
        assert result["source"]["cash_to_deploy"] == 500.0
