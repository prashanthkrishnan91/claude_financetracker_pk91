"""Tests — Deploy Stage 2.3A: exact-dollar math v1.

Covers:
  1.  readiness=False leaves recommended_dollar_amount and estimated_share_quantity null.
  2.  HOLD item remains non-actionable even when readiness=True.
  3.  Suppressed item receives no dollar amounts.
  4.  BUY computes deterministic rounded dollar amount (WHOLE_DOLLAR policy).
  5.  BUY with NO_ROUNDING policy passes through unrounded amount.
  6.  BUY with NEAREST_DOLLAR policy rounds to nearest integer.
  7.  TRIM computes deterministic action dollars without changing Intel action.
  8.  SELL computes deterministic action dollars without changing Intel action.
  9.  Minimum-trade threshold suppresses output (amount < minimum_trade_usd → null).
 10.  Amount equal to minimum_trade_usd is accepted.
 11.  Amount above minimum_trade_usd is accepted.
 12.  minimum_trade_usd=0 (zero floor) does not suppress any positive amount.
 13.  Non-positive delta suppresses output (BUY: already at or above target).
 14.  Non-positive delta suppresses output (TRIM: already at or below target).
 15.  share quantity is populated when certified price_per_share_usd is provided.
 16.  share quantity is null when price_per_share_usd is None.
 17.  share quantity is null when price_per_share_usd <= 0.
 18.  Intel action is preserved verbatim (BUY unchanged after math).
 19.  Intel action is preserved verbatim (TRIM unchanged after math).
 20.  Intel action is preserved verbatim (SELL unchanged after math).
 21.  rounding_policy field updated in returned item.
 22.  Original DeployPlanItem is not mutated (immutability).
 23.  apply_dollar_math_to_plan_items handles mixed list (BUY+HOLD+SUPPRESSED).
 24.  apply_dollar_math_to_plan_items with price_per_share_map populates share qty for known tickers.
 25.  apply_dollar_math_to_plan_items with missing ticker in price map leaves share qty null.
 26.  Rounding to zero suppresses output.
 27.  WHOLE_DOLLAR and NEAREST_DOLLAR behave identically.
 28.  Position absent from bundle → treated as zero current value (BUY from scratch).
"""
from __future__ import annotations

import pytest

from app.services.deploy.deploy_contracts import (
    DeployActionabilityStatus,
    DeployActionSource,
    DeployPlanItem,
    DeployPlanStatus,
)
from app.services.deploy.deploy_dollar_math_v1 import (
    apply_dollar_math_to_plan_items,
    compute_dollar_amount_for_item,
)
from app.services.deploy.deploy_policy_bridge import certify_sizing_policy
from app.services.deploy.deploy_sizing_contracts import (
    DeployCashInput,
    DeployPortfolioSizingInput,
    DeployPositionSizingInput,
    DeploySizingInputBundle,
    DeploySizingPolicyPlaceholder,
    DeploySizingTrustStatus,
    DeployTargetAllocationInput,
)
from app.services.deploy.deploy_target_allocation_bridge import certify_target_allocation


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_bundle(
    *,
    portfolio_value: float = 100_000.0,
    cash: float = 10_000.0,
    ticker: str = "AAPL",
    current_market_value: float = 10_000.0,
    current_weight: float = 0.10,
    target_weight: float = 0.15,
    minimum_trade_usd: float = 1.0,
    rounding_policy: str = "WHOLE_DOLLAR",
    exact_dollar_ready: bool = True,
) -> DeploySizingInputBundle:
    """Build a synthetic certified DeploySizingInputBundle for testing."""
    cash_input = DeployCashInput(
        available_cash_usd=cash,
        trust_status=DeploySizingTrustStatus.CERTIFIED,
        source_label="test_brokerage",
    )
    portfolio_input = DeployPortfolioSizingInput(
        total_portfolio_value_usd=portfolio_value,
        trust_status=DeploySizingTrustStatus.CERTIFIED,
        source_label="test_brokerage",
    )
    position_input = DeployPositionSizingInput(
        ticker=ticker,
        current_market_value_usd=current_market_value,
        current_weight=current_weight,
        trust_status=DeploySizingTrustStatus.CERTIFIED,
        source_label="test_brokerage",
    )
    ta = certify_target_allocation(ticker, target_weight, source_label="optimizer")
    policy = certify_sizing_policy(minimum_trade_usd, rounding_policy)

    if not exact_dollar_ready:
        # Make policy UNSUPPORTED to force exact_dollar_ready=False.
        policy = DeploySizingPolicyPlaceholder(
            trust_status=DeploySizingTrustStatus.UNSUPPORTED,
        )

    return DeploySizingInputBundle(
        cash=cash_input,
        portfolio=portfolio_input,
        positions={ticker: position_input},
        target_allocations={ticker: ta},
        policy=policy,
    )


def _make_item(
    *,
    ticker: str = "AAPL",
    intel_action: str = "BUY",
    actionability: DeployActionabilityStatus = DeployActionabilityStatus.ACTIONABLE_CANDIDATE,
) -> DeployPlanItem:
    return DeployPlanItem(
        ticker=ticker,
        intel_action=intel_action,
        actionability_status=actionability,
        action_source=DeployActionSource.INTEL_V3,
        intel_snapshot_id="snap-001",
        intel_run_id="run-001",
        plan_status=DeployPlanStatus.SCAFFOLD,
    )


# ---------------------------------------------------------------------------
# 1. readiness=False suppresses outputs
# ---------------------------------------------------------------------------

def test_readiness_false_leaves_dollar_and_share_null():
    bundle = _make_bundle(exact_dollar_ready=False)
    assert not bundle.exact_dollar_ready
    item = _make_item(intel_action="BUY")
    result = compute_dollar_amount_for_item(bundle, item)
    assert result.recommended_dollar_amount is None
    assert result.estimated_share_quantity is None


# ---------------------------------------------------------------------------
# 2. HOLD remains non-actionable
# ---------------------------------------------------------------------------

def test_hold_remains_non_actionable_when_readiness_true():
    bundle = _make_bundle()
    assert bundle.exact_dollar_ready
    item = _make_item(
        intel_action="HOLD",
        actionability=DeployActionabilityStatus.NOT_ACTIONABLE_HOLD,
    )
    result = compute_dollar_amount_for_item(bundle, item)
    assert result.recommended_dollar_amount is None
    assert result.estimated_share_quantity is None
    assert result.intel_action == "HOLD"


# ---------------------------------------------------------------------------
# 3. Suppressed item receives no dollar amounts
# ---------------------------------------------------------------------------

def test_suppressed_item_receives_no_dollar_amounts():
    bundle = _make_bundle()
    item = _make_item(
        intel_action="BUY",
        actionability=DeployActionabilityStatus.SUPPRESSED_MISSING_EVIDENCE,
    )
    result = compute_dollar_amount_for_item(bundle, item)
    assert result.recommended_dollar_amount is None
    assert result.estimated_share_quantity is None


# ---------------------------------------------------------------------------
# 4. BUY computes deterministic rounded dollar amount
# ---------------------------------------------------------------------------

def test_buy_computes_deterministic_rounded_dollars_whole_dollar():
    # target=15%, current=10%, portfolio=100k → delta=5000 → rounded=5000
    bundle = _make_bundle(
        portfolio_value=100_000.0,
        current_market_value=10_000.0,
        current_weight=0.10,
        target_weight=0.15,
        rounding_policy="WHOLE_DOLLAR",
        minimum_trade_usd=1.0,
    )
    item = _make_item(intel_action="BUY")
    result = compute_dollar_amount_for_item(bundle, item)
    assert result.recommended_dollar_amount == 5000.0
    assert result.intel_action == "BUY"


def test_buy_with_fractional_delta_rounds_whole_dollar():
    # target=12.5%, current=10%, portfolio=100k → delta=2500 → 2500 (already whole)
    bundle = _make_bundle(
        portfolio_value=100_000.0,
        current_market_value=10_000.0,
        current_weight=0.10,
        target_weight=0.125,
        rounding_policy="WHOLE_DOLLAR",
        minimum_trade_usd=1.0,
    )
    item = _make_item(intel_action="BUY")
    result = compute_dollar_amount_for_item(bundle, item)
    assert result.recommended_dollar_amount == 2500.0


# ---------------------------------------------------------------------------
# 5. BUY with NO_ROUNDING passes through unrounded amount
# ---------------------------------------------------------------------------

def test_buy_no_rounding_passes_through_fractional():
    # target=12.3%, current=10%, portfolio=100k → delta=2300.0
    bundle = _make_bundle(
        portfolio_value=100_000.0,
        current_market_value=10_000.0,
        current_weight=0.10,
        target_weight=0.123,
        rounding_policy="NO_ROUNDING",
        minimum_trade_usd=1.0,
    )
    item = _make_item(intel_action="BUY")
    result = compute_dollar_amount_for_item(bundle, item)
    assert result.recommended_dollar_amount == pytest.approx(2300.0)


# ---------------------------------------------------------------------------
# 6. NEAREST_DOLLAR behaves the same as WHOLE_DOLLAR
# ---------------------------------------------------------------------------

def test_nearest_dollar_rounds_to_integer():
    # target=15.5%, current=10%, portfolio=100k → delta=5500 → rounded=5500
    bundle = _make_bundle(
        portfolio_value=100_000.0,
        current_market_value=10_000.0,
        current_weight=0.10,
        target_weight=0.155,
        rounding_policy="NEAREST_DOLLAR",
        minimum_trade_usd=1.0,
    )
    item = _make_item(intel_action="BUY")
    result = compute_dollar_amount_for_item(bundle, item)
    assert result.recommended_dollar_amount == 5500.0


# ---------------------------------------------------------------------------
# 7. TRIM computes deterministic dollars without changing Intel action
# ---------------------------------------------------------------------------

def test_trim_computes_dollars_intel_action_unchanged():
    # current=20%, target=15%, portfolio=100k → delta=5000
    bundle = _make_bundle(
        portfolio_value=100_000.0,
        current_market_value=20_000.0,
        current_weight=0.20,
        target_weight=0.15,
        rounding_policy="WHOLE_DOLLAR",
        minimum_trade_usd=1.0,
    )
    item = _make_item(intel_action="TRIM")
    result = compute_dollar_amount_for_item(bundle, item)
    assert result.recommended_dollar_amount == 5000.0
    assert result.intel_action == "TRIM"


# ---------------------------------------------------------------------------
# 8. SELL computes deterministic dollars without changing Intel action
# ---------------------------------------------------------------------------

def test_sell_computes_dollars_intel_action_unchanged():
    # current=30%, target=10%, portfolio=100k → delta=20000
    bundle = _make_bundle(
        portfolio_value=100_000.0,
        current_market_value=30_000.0,
        current_weight=0.30,
        target_weight=0.10,
        rounding_policy="WHOLE_DOLLAR",
        minimum_trade_usd=1.0,
    )
    item = _make_item(intel_action="SELL")
    result = compute_dollar_amount_for_item(bundle, item)
    assert result.recommended_dollar_amount == 20_000.0
    assert result.intel_action == "SELL"


# ---------------------------------------------------------------------------
# 9. Minimum-trade threshold suppresses output
# ---------------------------------------------------------------------------

def test_minimum_trade_threshold_suppresses_small_amount():
    # delta=500, minimum=1000 → suppressed
    bundle = _make_bundle(
        portfolio_value=100_000.0,
        current_market_value=10_000.0,
        current_weight=0.10,
        target_weight=0.105,   # delta=500
        rounding_policy="WHOLE_DOLLAR",
        minimum_trade_usd=1_000.0,
    )
    item = _make_item(intel_action="BUY")
    result = compute_dollar_amount_for_item(bundle, item)
    assert result.recommended_dollar_amount is None
    assert result.estimated_share_quantity is None


# ---------------------------------------------------------------------------
# 10. Amount equal to minimum_trade_usd is accepted
# ---------------------------------------------------------------------------

def test_amount_equal_to_minimum_trade_is_accepted():
    # delta=1000, minimum=1000 → accepted
    bundle = _make_bundle(
        portfolio_value=100_000.0,
        current_market_value=10_000.0,
        current_weight=0.10,
        target_weight=0.11,    # delta=1000
        rounding_policy="WHOLE_DOLLAR",
        minimum_trade_usd=1_000.0,
    )
    item = _make_item(intel_action="BUY")
    result = compute_dollar_amount_for_item(bundle, item)
    assert result.recommended_dollar_amount == 1000.0


# ---------------------------------------------------------------------------
# 11. Amount above minimum_trade_usd is accepted
# ---------------------------------------------------------------------------

def test_amount_above_minimum_trade_is_accepted():
    bundle = _make_bundle(
        portfolio_value=100_000.0,
        current_market_value=10_000.0,
        current_weight=0.10,
        target_weight=0.15,   # delta=5000
        rounding_policy="WHOLE_DOLLAR",
        minimum_trade_usd=1_000.0,
    )
    item = _make_item(intel_action="BUY")
    result = compute_dollar_amount_for_item(bundle, item)
    assert result.recommended_dollar_amount == 5000.0


# ---------------------------------------------------------------------------
# 12. minimum_trade_usd=0 does not suppress any positive amount
# ---------------------------------------------------------------------------

def test_zero_minimum_trade_does_not_suppress_positive_amount():
    bundle = _make_bundle(
        portfolio_value=100_000.0,
        current_market_value=10_000.0,
        current_weight=0.10,
        target_weight=0.101,   # delta=100
        rounding_policy="WHOLE_DOLLAR",
        minimum_trade_usd=0.0,
    )
    item = _make_item(intel_action="BUY")
    result = compute_dollar_amount_for_item(bundle, item)
    assert result.recommended_dollar_amount == 100.0


# ---------------------------------------------------------------------------
# 13. Non-positive delta suppresses BUY (already at or above target)
# ---------------------------------------------------------------------------

def test_buy_already_at_or_above_target_suppresses_output():
    # current=20%, target=15% → delta for BUY is negative → suppressed
    bundle = _make_bundle(
        portfolio_value=100_000.0,
        current_market_value=20_000.0,
        current_weight=0.20,
        target_weight=0.15,
        rounding_policy="WHOLE_DOLLAR",
        minimum_trade_usd=1.0,
    )
    item = _make_item(intel_action="BUY")
    result = compute_dollar_amount_for_item(bundle, item)
    assert result.recommended_dollar_amount is None


# ---------------------------------------------------------------------------
# 14. Non-positive delta suppresses TRIM (already at or below target)
# ---------------------------------------------------------------------------

def test_trim_already_at_or_below_target_suppresses_output():
    # current=10%, target=15% → delta for TRIM is negative → suppressed
    bundle = _make_bundle(
        portfolio_value=100_000.0,
        current_market_value=10_000.0,
        current_weight=0.10,
        target_weight=0.15,
        rounding_policy="WHOLE_DOLLAR",
        minimum_trade_usd=1.0,
    )
    item = _make_item(intel_action="TRIM")
    result = compute_dollar_amount_for_item(bundle, item)
    assert result.recommended_dollar_amount is None


# ---------------------------------------------------------------------------
# 15–17. Share quantity
# ---------------------------------------------------------------------------

def test_share_quantity_populated_when_certified_price_provided():
    bundle = _make_bundle(
        portfolio_value=100_000.0,
        current_market_value=10_000.0,
        current_weight=0.10,
        target_weight=0.15,   # delta=5000
        rounding_policy="WHOLE_DOLLAR",
        minimum_trade_usd=1.0,
    )
    item = _make_item(intel_action="BUY")
    result = compute_dollar_amount_for_item(bundle, item, price_per_share_usd=100.0)
    assert result.recommended_dollar_amount == 5000.0
    assert result.estimated_share_quantity == pytest.approx(50.0)


def test_share_quantity_null_when_price_not_provided():
    bundle = _make_bundle(
        portfolio_value=100_000.0,
        current_market_value=10_000.0,
        current_weight=0.10,
        target_weight=0.15,
        rounding_policy="WHOLE_DOLLAR",
        minimum_trade_usd=1.0,
    )
    item = _make_item(intel_action="BUY")
    result = compute_dollar_amount_for_item(bundle, item, price_per_share_usd=None)
    assert result.estimated_share_quantity is None
    assert result.recommended_dollar_amount == 5000.0


def test_share_quantity_null_when_price_is_zero():
    bundle = _make_bundle(
        portfolio_value=100_000.0,
        current_market_value=10_000.0,
        current_weight=0.10,
        target_weight=0.15,
        rounding_policy="WHOLE_DOLLAR",
        minimum_trade_usd=1.0,
    )
    item = _make_item(intel_action="BUY")
    result = compute_dollar_amount_for_item(bundle, item, price_per_share_usd=0.0)
    assert result.estimated_share_quantity is None


def test_share_quantity_null_when_price_is_negative():
    bundle = _make_bundle(
        portfolio_value=100_000.0,
        current_market_value=10_000.0,
        current_weight=0.10,
        target_weight=0.15,
        rounding_policy="WHOLE_DOLLAR",
        minimum_trade_usd=1.0,
    )
    item = _make_item(intel_action="BUY")
    result = compute_dollar_amount_for_item(bundle, item, price_per_share_usd=-50.0)
    assert result.estimated_share_quantity is None


# ---------------------------------------------------------------------------
# 18–20. Intel action preserved verbatim
# ---------------------------------------------------------------------------

def test_intel_action_preserved_buy():
    bundle = _make_bundle(target_weight=0.15, current_market_value=10_000.0)
    item = _make_item(intel_action="BUY")
    result = compute_dollar_amount_for_item(bundle, item)
    assert result.intel_action == "BUY"


def test_intel_action_preserved_trim():
    bundle = _make_bundle(
        current_market_value=20_000.0, current_weight=0.20, target_weight=0.15
    )
    item = _make_item(intel_action="TRIM")
    result = compute_dollar_amount_for_item(bundle, item)
    assert result.intel_action == "TRIM"


def test_intel_action_preserved_sell():
    bundle = _make_bundle(
        current_market_value=30_000.0, current_weight=0.30, target_weight=0.10
    )
    item = _make_item(intel_action="SELL")
    result = compute_dollar_amount_for_item(bundle, item)
    assert result.intel_action == "SELL"


# ---------------------------------------------------------------------------
# 21. rounding_policy field updated in returned item
# ---------------------------------------------------------------------------

def test_rounding_policy_field_updated_in_returned_item():
    bundle = _make_bundle(rounding_policy="NO_ROUNDING")
    item = _make_item(intel_action="BUY")
    result = compute_dollar_amount_for_item(bundle, item)
    assert result.rounding_policy == "NO_ROUNDING"


# ---------------------------------------------------------------------------
# 22. Original item is not mutated
# ---------------------------------------------------------------------------

def test_original_item_not_mutated():
    bundle = _make_bundle()
    item = _make_item(intel_action="BUY")
    original_dollar = item.recommended_dollar_amount
    original_share = item.estimated_share_quantity
    compute_dollar_amount_for_item(bundle, item)
    assert item.recommended_dollar_amount == original_dollar
    assert item.estimated_share_quantity == original_share


# ---------------------------------------------------------------------------
# 23. apply_dollar_math_to_plan_items — mixed list
# ---------------------------------------------------------------------------

def test_apply_dollar_math_to_plan_items_mixed_list():
    bundle = _make_bundle(
        portfolio_value=100_000.0,
        current_market_value=10_000.0,
        current_weight=0.10,
        target_weight=0.15,
        minimum_trade_usd=1.0,
    )
    buy_item = _make_item(intel_action="BUY", actionability=DeployActionabilityStatus.ACTIONABLE_CANDIDATE)
    hold_item = _make_item(intel_action="HOLD", actionability=DeployActionabilityStatus.NOT_ACTIONABLE_HOLD)
    suppressed_item = _make_item(intel_action="BUY", actionability=DeployActionabilityStatus.SUPPRESSED_MISSING_EVIDENCE)

    results = apply_dollar_math_to_plan_items(bundle, [buy_item, hold_item, suppressed_item])
    assert results[0].recommended_dollar_amount == 5000.0   # BUY → dollars computed
    assert results[1].recommended_dollar_amount is None     # HOLD → null
    assert results[2].recommended_dollar_amount is None     # suppressed → null


# ---------------------------------------------------------------------------
# 24–25. apply_dollar_math_to_plan_items with price_per_share_map
# ---------------------------------------------------------------------------

def test_apply_dollar_math_price_map_populates_known_tickers():
    bundle = _make_bundle(
        portfolio_value=100_000.0,
        current_market_value=10_000.0,
        current_weight=0.10,
        target_weight=0.15,  # delta=5000
        minimum_trade_usd=1.0,
    )
    item = _make_item(intel_action="BUY")
    results = apply_dollar_math_to_plan_items(bundle, [item], price_per_share_map={"AAPL": 250.0})
    assert results[0].estimated_share_quantity == pytest.approx(20.0)


def test_apply_dollar_math_missing_ticker_in_price_map_leaves_share_qty_null():
    bundle = _make_bundle(
        portfolio_value=100_000.0,
        current_market_value=10_000.0,
        current_weight=0.10,
        target_weight=0.15,
        minimum_trade_usd=1.0,
    )
    item = _make_item(intel_action="BUY")
    results = apply_dollar_math_to_plan_items(bundle, [item], price_per_share_map={"MSFT": 300.0})
    assert results[0].estimated_share_quantity is None
    assert results[0].recommended_dollar_amount == 5000.0


# ---------------------------------------------------------------------------
# 26. Rounding to zero suppresses output
# ---------------------------------------------------------------------------

def test_rounding_to_zero_suppresses_output():
    # delta=0.3, rounded to nearest whole dollar = 0 → suppressed
    bundle = _make_bundle(
        portfolio_value=100_000.0,
        current_market_value=10_000.0,
        current_weight=0.10,
        target_weight=0.100003,   # tiny delta → rounds to 0
        rounding_policy="WHOLE_DOLLAR",
        minimum_trade_usd=0.0,
    )
    item = _make_item(intel_action="BUY")
    result = compute_dollar_amount_for_item(bundle, item)
    assert result.recommended_dollar_amount is None


# ---------------------------------------------------------------------------
# 27. WHOLE_DOLLAR and NEAREST_DOLLAR behave identically
# ---------------------------------------------------------------------------

def test_whole_dollar_and_nearest_dollar_same_result():
    common_kwargs = dict(
        portfolio_value=100_000.0,
        current_market_value=10_000.0,
        current_weight=0.10,
        target_weight=0.1557,   # delta=570.0 (rounds to 570 either way)
        minimum_trade_usd=1.0,
    )
    b1 = _make_bundle(**common_kwargs, rounding_policy="WHOLE_DOLLAR")
    b2 = _make_bundle(**common_kwargs, rounding_policy="NEAREST_DOLLAR")
    item = _make_item(intel_action="BUY")
    r1 = compute_dollar_amount_for_item(b1, item)
    r2 = compute_dollar_amount_for_item(b2, item)
    assert r1.recommended_dollar_amount == r2.recommended_dollar_amount


# ---------------------------------------------------------------------------
# 28. Position absent from bundle → treated as zero current value (BUY from scratch)
# ---------------------------------------------------------------------------

def test_buy_from_scratch_position_absent_from_bundle():
    # Build bundle without the ticker in positions dict.
    cash_input = DeployCashInput(
        available_cash_usd=50_000.0,
        trust_status=DeploySizingTrustStatus.CERTIFIED,
        source_label="test",
    )
    portfolio_input = DeployPortfolioSizingInput(
        total_portfolio_value_usd=100_000.0,
        trust_status=DeploySizingTrustStatus.CERTIFIED,
        source_label="test",
    )
    ta = certify_target_allocation("NVDA", 0.10, source_label="optimizer")
    policy = certify_sizing_policy(1.0, "WHOLE_DOLLAR")
    bundle = DeploySizingInputBundle(
        cash=cash_input,
        portfolio=portfolio_input,
        positions={},            # No NVDA position yet
        target_allocations={"NVDA": ta},
        policy=policy,
    )
    assert bundle.exact_dollar_ready  # vacuously true for positions

    item = DeployPlanItem(
        ticker="NVDA",
        intel_action="BUY",
        actionability_status=DeployActionabilityStatus.ACTIONABLE_CANDIDATE,
        action_source=DeployActionSource.INTEL_V3,
        intel_snapshot_id="snap-001",
        intel_run_id="run-001",
        plan_status=DeployPlanStatus.SCAFFOLD,
    )
    result = compute_dollar_amount_for_item(bundle, item)
    # target=10% of 100k = 10000, current=0 → delta=10000
    assert result.recommended_dollar_amount == 10_000.0
