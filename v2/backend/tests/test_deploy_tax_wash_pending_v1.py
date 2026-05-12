"""Tests — Deploy Stage 2.3D: tax/wash-sale pending contract v1.

Proves that the pending_guardrails_reason field correctly marks items that
are actionable-except-tax/wash-sale-not-evaluated, and that blocked, hold,
suppressed, and not-ready items do not receive that label.

Covers:
  1.  BUY positive-dollar cash-passed → actionable_pending_tax + pending reason set.
  2.  TRIM positive-dollar non-blocking cash → actionable_pending_tax + pending reason set.
  3.  SELL positive-dollar non-blocking cash → actionable_pending_tax + pending reason set.
  4.  BUY with blocked_insufficient_cash → blocked_cash, pending reason = "none".
  5.  BUY with blocked_uncertified_cash → blocked_cash, pending reason = "none".
  6.  BUY zero dollars cash-passed → not_ready, pending reason = "none".
  7.  BUY no dollar amount, cash not_evaluated → not_ready, pending reason = "none".
  8.  HOLD → informational_hold, pending reason = "none".
  9.  Suppressed item → suppressed, pending reason = "none".
 10.  tax_guardrail_status remains "not_evaluated_yet" for pending-tax items (not silently passed).
 11.  wash_sale_guardrail_status remains "not_evaluated_yet" for pending-tax items.
 12.  intel_action not changed for any item.
 13.  recommended_dollar_amount not changed for any item.
 14.  cash_constraint_status not changed for any item.
 15.  plan-builder: BUY with certified bundle + sufficient cash → pending reason exposed.
 16.  plan-builder: TRIM with certified bundle → pending reason exposed.
 17.  plan-builder: blocked-cash BUY → pending reason = "none".
 18.  plan-builder: HOLD → pending reason = "none".
 19.  plan-builder: no bundle → pending reason = "none" (not_ready, no dollars).
 20.  pending_reason is "none" for TRIM with unexpected cash status (not_ready path).
"""
from __future__ import annotations

from app.services.deploy.deploy_contracts import (
    DeployActionabilityStatus,
    DeployActionSource,
    DeployPlanItem,
    DeployPlanStatus,
)
from app.services.deploy.deploy_finalization_v1 import (
    FINAL_ACTIONABLE_PENDING_TAX,
    FINAL_BLOCKED_CASH,
    FINAL_INFORMATIONAL_HOLD,
    FINAL_NOT_READY,
    FINAL_SUPPRESSED,
    PENDING_REASON_NONE,
    PENDING_REASON_TAX_WASH_NOT_EVALUATED,
    finalize_item_actionability,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _item(
    intel_action: str = "BUY",
    *,
    actionability: DeployActionabilityStatus = DeployActionabilityStatus.ACTIONABLE_CANDIDATE,
    recommended_dollar_amount: float | None = 5_000.0,
    cash_constraint_status: str = "not_evaluated_yet",
) -> DeployPlanItem:
    return DeployPlanItem(
        ticker="AAPL",
        intel_action=intel_action,
        actionability_status=actionability,
        action_source=DeployActionSource.INTEL_V3,
        intel_snapshot_id="snap-001",
        intel_run_id="run-001",
        plan_status=DeployPlanStatus.SCAFFOLD,
        recommended_dollar_amount=recommended_dollar_amount,
        cash_constraint_status=cash_constraint_status,
        tax_guardrail_status="not_evaluated_yet",
        wash_sale_guardrail_status="not_evaluated_yet",
    )


def _hold() -> DeployPlanItem:
    return DeployPlanItem(
        ticker="GOOG",
        intel_action="HOLD",
        actionability_status=DeployActionabilityStatus.NOT_ACTIONABLE_HOLD,
        action_source=DeployActionSource.INTEL_V3,
        intel_snapshot_id="snap-001",
        intel_run_id="run-001",
        plan_status=DeployPlanStatus.HOLD_ONLY,
        recommended_dollar_amount=None,
        cash_constraint_status="not_applicable_hold",
    )


def _suppressed() -> DeployPlanItem:
    return DeployPlanItem(
        ticker="TSLA",
        intel_action="BUY",
        actionability_status=DeployActionabilityStatus.SUPPRESSED_STALE,
        action_source=DeployActionSource.INTEL_V3,
        intel_snapshot_id="snap-001",
        intel_run_id="run-001",
        plan_status=DeployPlanStatus.SUPPRESSED,
        recommended_dollar_amount=None,
        cash_constraint_status="not_applicable_suppressed",
    )


# ---------------------------------------------------------------------------
# Tests: pending_guardrails_reason set only for actionable_pending_tax
# ---------------------------------------------------------------------------

def test_buy_cash_passed_sets_pending_reason():
    result = finalize_item_actionability(_item("BUY", recommended_dollar_amount=5_000.0, cash_constraint_status="passed"))
    assert result.final_actionability_status == FINAL_ACTIONABLE_PENDING_TAX
    assert result.pending_guardrails_reason == PENDING_REASON_TAX_WASH_NOT_EVALUATED


def test_trim_non_blocking_cash_sets_pending_reason():
    result = finalize_item_actionability(_item("TRIM", recommended_dollar_amount=3_000.0, cash_constraint_status="not_applicable_trim_sell"))
    assert result.final_actionability_status == FINAL_ACTIONABLE_PENDING_TAX
    assert result.pending_guardrails_reason == PENDING_REASON_TAX_WASH_NOT_EVALUATED


def test_sell_non_blocking_cash_sets_pending_reason():
    result = finalize_item_actionability(_item("SELL", recommended_dollar_amount=4_000.0, cash_constraint_status="not_applicable_trim_sell"))
    assert result.final_actionability_status == FINAL_ACTIONABLE_PENDING_TAX
    assert result.pending_guardrails_reason == PENDING_REASON_TAX_WASH_NOT_EVALUATED


# ---------------------------------------------------------------------------
# Tests: blocked/not-ready/hold/suppressed get "none" reason
# ---------------------------------------------------------------------------

def test_buy_insufficient_cash_pending_reason_none():
    result = finalize_item_actionability(_item("BUY", recommended_dollar_amount=5_000.0, cash_constraint_status="blocked_insufficient_cash"))
    assert result.final_actionability_status == FINAL_BLOCKED_CASH
    assert result.pending_guardrails_reason == PENDING_REASON_NONE


def test_buy_uncertified_cash_pending_reason_none():
    result = finalize_item_actionability(_item("BUY", recommended_dollar_amount=5_000.0, cash_constraint_status="blocked_uncertified_cash"))
    assert result.final_actionability_status == FINAL_BLOCKED_CASH
    assert result.pending_guardrails_reason == PENDING_REASON_NONE


def test_buy_zero_dollars_pending_reason_none():
    result = finalize_item_actionability(_item("BUY", recommended_dollar_amount=0.0, cash_constraint_status="passed"))
    assert result.final_actionability_status == FINAL_NOT_READY
    assert result.pending_guardrails_reason == PENDING_REASON_NONE


def test_buy_no_dollars_not_evaluated_cash_pending_reason_none():
    result = finalize_item_actionability(_item("BUY", recommended_dollar_amount=None, cash_constraint_status="not_evaluated_yet"))
    assert result.final_actionability_status == FINAL_NOT_READY
    assert result.pending_guardrails_reason == PENDING_REASON_NONE


def test_hold_pending_reason_none():
    result = finalize_item_actionability(_hold())
    assert result.final_actionability_status == FINAL_INFORMATIONAL_HOLD
    assert result.pending_guardrails_reason == PENDING_REASON_NONE


def test_suppressed_pending_reason_none():
    result = finalize_item_actionability(_suppressed())
    assert result.final_actionability_status == FINAL_SUPPRESSED
    assert result.pending_guardrails_reason == PENDING_REASON_NONE


def test_trim_unexpected_cash_status_pending_reason_none():
    """TRIM with unexpected cash status → not_ready, pending reason = none."""
    result = finalize_item_actionability(_item("TRIM", recommended_dollar_amount=3_000.0, cash_constraint_status="not_evaluated_yet"))
    assert result.final_actionability_status == FINAL_NOT_READY
    assert result.pending_guardrails_reason == PENDING_REASON_NONE


# ---------------------------------------------------------------------------
# Tests: tax/wash placeholders not silently passed for pending-tax items
# ---------------------------------------------------------------------------

def test_tax_guardrail_placeholder_unchanged_for_pending_tax_item():
    result = finalize_item_actionability(_item("BUY", recommended_dollar_amount=5_000.0, cash_constraint_status="passed"))
    assert result.tax_guardrail_status == "not_evaluated_yet"


def test_wash_sale_placeholder_unchanged_for_pending_tax_item():
    result = finalize_item_actionability(_item("BUY", recommended_dollar_amount=5_000.0, cash_constraint_status="passed"))
    assert result.wash_sale_guardrail_status == "not_evaluated_yet"


# ---------------------------------------------------------------------------
# Tests: no prior fields mutated
# ---------------------------------------------------------------------------

def test_intel_action_unchanged():
    result = finalize_item_actionability(_item("BUY", recommended_dollar_amount=5_000.0, cash_constraint_status="passed"))
    assert result.intel_action == "BUY"


def test_recommended_dollar_amount_unchanged():
    result = finalize_item_actionability(_item("BUY", recommended_dollar_amount=5_000.0, cash_constraint_status="blocked_insufficient_cash"))
    assert result.recommended_dollar_amount == 5_000.0


def test_cash_constraint_status_unchanged():
    result = finalize_item_actionability(_item("BUY", recommended_dollar_amount=5_000.0, cash_constraint_status="passed"))
    assert result.cash_constraint_status == "passed"


# ---------------------------------------------------------------------------
# Tests: plan-builder integration
# ---------------------------------------------------------------------------

def _certified_bundle(ticker, portfolio_value, current_mkt, current_weight, target_weight, cash_usd=None):
    from app.services.deploy.deploy_policy_bridge import certify_sizing_policy
    from app.services.deploy.deploy_sizing_contracts import (
        DeployCashInput, DeployPortfolioSizingInput, DeployPositionSizingInput,
        DeploySizingInputBundle, DeploySizingTrustStatus,
    )
    from app.services.deploy.deploy_target_allocation_bridge import certify_target_allocation
    return DeploySizingInputBundle(
        cash=DeployCashInput(
            available_cash_usd=cash_usd if cash_usd is not None else portfolio_value * 0.1,
            trust_status=DeploySizingTrustStatus.CERTIFIED,
        ),
        portfolio=DeployPortfolioSizingInput(total_portfolio_value_usd=portfolio_value, trust_status=DeploySizingTrustStatus.CERTIFIED),
        positions={ticker: DeployPositionSizingInput(ticker=ticker, current_market_value_usd=current_mkt, current_weight=current_weight, trust_status=DeploySizingTrustStatus.CERTIFIED)},
        target_allocations={ticker: certify_target_allocation(ticker, target_weight, source_label="optimizer")},
        policy=certify_sizing_policy(1.0, "WHOLE_DOLLAR"),
    )


def _snap_inputs(cards):
    from app.services.deploy.deploy_intel_adapter import build_deploy_inputs_from_snapshot
    from tests.test_deploy_foundation_v1 import _snapshot
    return build_deploy_inputs_from_snapshot(_snapshot(cards))


def _card(ticker, action, evidence_band="PARTIAL"):
    from tests.test_deploy_foundation_v1 import _card as _c
    return _c(ticker, action, evidence_band=evidence_band)


def test_plan_builder_buy_sufficient_cash_pending_reason_exposed():
    from app.services.deploy.deploy_translation_v1 import build_deploy_plan
    # current=95%, target=100%, delta=5000, cash=10000 → passes
    bundle = _certified_bundle("AAPL", 100_000.0, 95_000.0, 0.95, 1.00, cash_usd=10_000.0)
    plan = build_deploy_plan(_snap_inputs([_card("AAPL", "BUY")]), sizing_bundle=bundle)
    item = plan.items[0]
    assert item.final_actionability_status == FINAL_ACTIONABLE_PENDING_TAX
    assert item.pending_guardrails_reason == PENDING_REASON_TAX_WASH_NOT_EVALUATED


def test_plan_builder_trim_pending_reason_exposed():
    from app.services.deploy.deploy_translation_v1 import build_deploy_plan
    # current=100%, target=98%, TRIM delta=2000
    bundle = _certified_bundle("AAPL", 100_000.0, 100_000.0, 1.00, 0.98)
    plan = build_deploy_plan(_snap_inputs([_card("AAPL", "TRIM")]), sizing_bundle=bundle)
    item = plan.items[0]
    assert item.final_actionability_status == FINAL_ACTIONABLE_PENDING_TAX
    assert item.pending_guardrails_reason == PENDING_REASON_TAX_WASH_NOT_EVALUATED


def test_plan_builder_blocked_cash_buy_pending_reason_none():
    from app.services.deploy.deploy_translation_v1 import build_deploy_plan
    # current=95%, target=100%, delta=5000, cash=1000 < 5000 → blocked
    bundle = _certified_bundle("AAPL", 100_000.0, 95_000.0, 0.95, 1.00, cash_usd=1_000.0)
    plan = build_deploy_plan(_snap_inputs([_card("AAPL", "BUY")]), sizing_bundle=bundle)
    item = plan.items[0]
    assert item.final_actionability_status == FINAL_BLOCKED_CASH
    assert item.pending_guardrails_reason == PENDING_REASON_NONE


def test_plan_builder_hold_pending_reason_none():
    from app.services.deploy.deploy_translation_v1 import build_deploy_plan
    bundle = _certified_bundle("AAPL", 100_000.0, 100_000.0, 1.00, 1.00)
    plan = build_deploy_plan(_snap_inputs([_card("AAPL", "HOLD")]), sizing_bundle=bundle)
    assert plan.items[0].pending_guardrails_reason == PENDING_REASON_NONE


def test_plan_builder_no_bundle_pending_reason_none():
    from app.services.deploy.deploy_translation_v1 import build_deploy_plan
    plan = build_deploy_plan(_snap_inputs([_card("AAPL", "BUY")]))
    assert plan.items[0].final_actionability_status == FINAL_NOT_READY
    assert plan.items[0].pending_guardrails_reason == PENDING_REASON_NONE


# ---------------------------------------------------------------------------
# Tests: stale pending_guardrails_reason is always cleared (determinism)
# ---------------------------------------------------------------------------

def _stale_item(intel_action: str = "BUY", **kwargs) -> DeployPlanItem:
    """Item pre-set with a stale pending_guardrails_reason to prove finalization clears it."""
    base = _item(intel_action, **kwargs)
    import dataclasses
    return dataclasses.replace(base, pending_guardrails_reason="tax_and_wash_sale_not_evaluated")


def test_stale_reason_cleared_for_blocked_cash_buy():
    item = _stale_item("BUY", recommended_dollar_amount=5_000.0, cash_constraint_status="blocked_insufficient_cash")
    result = finalize_item_actionability(item)
    assert result.final_actionability_status == FINAL_BLOCKED_CASH
    assert result.pending_guardrails_reason == PENDING_REASON_NONE


def test_stale_reason_cleared_for_not_ready_buy():
    item = _stale_item("BUY", recommended_dollar_amount=None, cash_constraint_status="not_evaluated_yet")
    result = finalize_item_actionability(item)
    assert result.final_actionability_status == FINAL_NOT_READY
    assert result.pending_guardrails_reason == PENDING_REASON_NONE


def test_stale_reason_cleared_for_hold():
    import dataclasses
    base = _hold()
    item = dataclasses.replace(base, pending_guardrails_reason="tax_and_wash_sale_not_evaluated")
    result = finalize_item_actionability(item)
    assert result.final_actionability_status == FINAL_INFORMATIONAL_HOLD
    assert result.pending_guardrails_reason == PENDING_REASON_NONE


def test_stale_reason_cleared_for_suppressed():
    import dataclasses
    base = _suppressed()
    item = dataclasses.replace(base, pending_guardrails_reason="tax_and_wash_sale_not_evaluated")
    result = finalize_item_actionability(item)
    assert result.final_actionability_status == FINAL_SUPPRESSED
    assert result.pending_guardrails_reason == PENDING_REASON_NONE


def test_stale_reason_cleared_for_trim_unexpected_cash():
    item = _stale_item("TRIM", recommended_dollar_amount=3_000.0, cash_constraint_status="not_evaluated_yet")
    result = finalize_item_actionability(item)
    assert result.final_actionability_status == FINAL_NOT_READY
    assert result.pending_guardrails_reason == PENDING_REASON_NONE


def test_valid_pending_tax_buy_reason_still_set_after_determinism_fix():
    """Regression: valid BUY pending-tax path still produces correct reason."""
    result = finalize_item_actionability(_item("BUY", recommended_dollar_amount=5_000.0, cash_constraint_status="passed"))
    assert result.final_actionability_status == FINAL_ACTIONABLE_PENDING_TAX
    assert result.pending_guardrails_reason == PENDING_REASON_TAX_WASH_NOT_EVALUATED


def test_valid_pending_tax_trim_reason_still_set_after_determinism_fix():
    """Regression: valid TRIM pending-tax path still produces correct reason."""
    result = finalize_item_actionability(_item("TRIM", recommended_dollar_amount=3_000.0, cash_constraint_status="not_applicable_trim_sell"))
    assert result.final_actionability_status == FINAL_ACTIONABLE_PENDING_TAX
    assert result.pending_guardrails_reason == PENDING_REASON_TAX_WASH_NOT_EVALUATED
