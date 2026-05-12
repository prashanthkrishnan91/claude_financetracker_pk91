"""Tests — Deploy Stage 2.3C: per-item final actionability finalization v1.

Covers:
  1.  BUY with dollar amount + cash_passed → actionable_pending_tax.
  2.  BUY with dollar amount + blocked_insufficient_cash → blocked_cash.
  3.  BUY with dollar amount + blocked_uncertified_cash → blocked_cash.
  4.  BUY with dollar amount + not_evaluated_yet cash → blocked_cash.
  5.  BUY without dollar amount + cash_passed → not_ready.
  6.  BUY without dollar amount + blocked cash → blocked_cash.
  7.  TRIM with dollar amount + not_applicable_trim_sell → actionable_pending_tax.
  8.  SELL with dollar amount + not_applicable_trim_sell → actionable_pending_tax.
  9.  TRIM with dollar amount + unexpected cash status → not_ready.
 10.  HOLD (NOT_ACTIONABLE_HOLD) → informational_hold.
 11.  Suppressed item (SUPPRESSED_MISSING_EVIDENCE) → suppressed.
 12.  Suppressed item (SUPPRESSED_STALE) → suppressed.
 13.  Suppressed item (SUPPRESSED_WEAK) → suppressed.
 14.  Suppressed item (SUPPRESSED_BLOCKED) → suppressed.
 15.  Finalization never changes intel_action.
 16.  Finalization never changes recommended_dollar_amount.
 17.  Finalization never changes cash_constraint_status.
 18.  Finalization never changes tax_guardrail_status (honest placeholder).
 19.  Finalization never changes wash_sale_guardrail_status (honest placeholder).
 20.  Original DeployPlanItem is not mutated (immutability).
 21.  apply_finalization_to_plan_items handles a mixed list correctly.
 22.  apply_finalization_to_plan_items returns a new list, not the original.
 23.  Plan-builder: no bundle → final status not_finalized placeholder... wait,
      finalization always runs → HOLD→informational_hold, BUY→not_ready (no dollars).
 24.  Plan-builder: certified bundle, sufficient cash BUY → actionable_pending_tax.
 25.  Plan-builder: certified bundle, insufficient cash BUY → blocked_cash, dollar amount preserved.
 26.  Plan-builder: TRIM with certified bundle → actionable_pending_tax.
 27.  Plan-builder: HOLD with certified bundle → informational_hold.
 28.  Plan-builder: finalization_evaluated=True always set on guardrail summary.
 29.  Tax/wash-sale placeholder statuses remain "not_evaluated_yet" after finalization.
 30.  BUY dollar amount = 0 treated as None (not_ready) — finalization sees None from upstream math.
"""
from __future__ import annotations

import dataclasses

import pytest

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
    FINAL_NOT_FINALIZED,
    FINAL_NOT_READY,
    FINAL_SUPPRESSED,
    apply_finalization_to_plan_items,
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


def _hold_item() -> DeployPlanItem:
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


def _suppressed_item(
    suppression: DeployActionabilityStatus = DeployActionabilityStatus.SUPPRESSED_MISSING_EVIDENCE,
) -> DeployPlanItem:
    return DeployPlanItem(
        ticker="TSLA",
        intel_action="BUY",
        actionability_status=suppression,
        action_source=DeployActionSource.INTEL_V3,
        intel_snapshot_id="snap-001",
        intel_run_id="run-001",
        plan_status=DeployPlanStatus.SUPPRESSED,
        recommended_dollar_amount=None,
        cash_constraint_status="not_applicable_suppressed",
    )


# ---------------------------------------------------------------------------
# Tests: BUY finalization
# ---------------------------------------------------------------------------

def test_buy_dollar_cash_passed_is_actionable_pending_tax():
    item = _item("BUY", recommended_dollar_amount=5_000.0, cash_constraint_status="passed")
    result = finalize_item_actionability(item)
    assert result.final_actionability_status == FINAL_ACTIONABLE_PENDING_TAX


def test_buy_dollar_insufficient_cash_blocked():
    item = _item("BUY", recommended_dollar_amount=5_000.0, cash_constraint_status="blocked_insufficient_cash")
    result = finalize_item_actionability(item)
    assert result.final_actionability_status == FINAL_BLOCKED_CASH


def test_buy_dollar_uncertified_cash_blocked():
    item = _item("BUY", recommended_dollar_amount=5_000.0, cash_constraint_status="blocked_uncertified_cash")
    result = finalize_item_actionability(item)
    assert result.final_actionability_status == FINAL_BLOCKED_CASH


def test_buy_dollar_not_evaluated_cash_blocked():
    """BUY with dollars but cash still 'not_evaluated_yet' → blocked_cash (not safe)."""
    item = _item("BUY", recommended_dollar_amount=5_000.0, cash_constraint_status="not_evaluated_yet")
    result = finalize_item_actionability(item)
    assert result.final_actionability_status == FINAL_BLOCKED_CASH


def test_buy_no_dollar_certified_cash_not_ready():
    """BUY without a dollar amount but cash is certified → not_ready (math wasn't run)."""
    item = _item("BUY", recommended_dollar_amount=None, cash_constraint_status="passed")
    result = finalize_item_actionability(item)
    assert result.final_actionability_status == FINAL_NOT_READY


def test_buy_no_dollar_blocked_cash_is_blocked():
    """BUY without dollar amount and blocked cash → blocked_cash (primary reason)."""
    item = _item("BUY", recommended_dollar_amount=None, cash_constraint_status="blocked_insufficient_cash")
    result = finalize_item_actionability(item)
    assert result.final_actionability_status == FINAL_BLOCKED_CASH


def test_buy_no_dollar_uncertified_cash_is_blocked():
    item = _item("BUY", recommended_dollar_amount=None, cash_constraint_status="blocked_uncertified_cash")
    result = finalize_item_actionability(item)
    assert result.final_actionability_status == FINAL_BLOCKED_CASH


# ---------------------------------------------------------------------------
# Tests: TRIM/SELL finalization
# ---------------------------------------------------------------------------

def test_trim_dollar_non_blocking_cash_actionable():
    item = _item("TRIM", recommended_dollar_amount=3_000.0, cash_constraint_status="not_applicable_trim_sell")
    result = finalize_item_actionability(item)
    assert result.final_actionability_status == FINAL_ACTIONABLE_PENDING_TAX


def test_sell_dollar_non_blocking_cash_actionable():
    item = _item("SELL", recommended_dollar_amount=4_000.0, cash_constraint_status="not_applicable_trim_sell")
    result = finalize_item_actionability(item)
    assert result.final_actionability_status == FINAL_ACTIONABLE_PENDING_TAX


def test_trim_dollar_unexpected_cash_status_not_ready():
    """TRIM with dollar amount but unexpected cash status → not_ready."""
    item = _item("TRIM", recommended_dollar_amount=3_000.0, cash_constraint_status="not_evaluated_yet")
    result = finalize_item_actionability(item)
    assert result.final_actionability_status == FINAL_NOT_READY


# ---------------------------------------------------------------------------
# Tests: HOLD
# ---------------------------------------------------------------------------

def test_hold_is_informational():
    result = finalize_item_actionability(_hold_item())
    assert result.final_actionability_status == FINAL_INFORMATIONAL_HOLD


# ---------------------------------------------------------------------------
# Tests: Suppressed items
# ---------------------------------------------------------------------------

def test_suppressed_missing_evidence():
    result = finalize_item_actionability(_suppressed_item(DeployActionabilityStatus.SUPPRESSED_MISSING_EVIDENCE))
    assert result.final_actionability_status == FINAL_SUPPRESSED


def test_suppressed_stale():
    result = finalize_item_actionability(_suppressed_item(DeployActionabilityStatus.SUPPRESSED_STALE))
    assert result.final_actionability_status == FINAL_SUPPRESSED


def test_suppressed_weak():
    result = finalize_item_actionability(_suppressed_item(DeployActionabilityStatus.SUPPRESSED_WEAK))
    assert result.final_actionability_status == FINAL_SUPPRESSED


def test_suppressed_blocked():
    result = finalize_item_actionability(_suppressed_item(DeployActionabilityStatus.SUPPRESSED_BLOCKED))
    assert result.final_actionability_status == FINAL_SUPPRESSED


# ---------------------------------------------------------------------------
# Tests: Invariants — no prior fields mutated
# ---------------------------------------------------------------------------

def test_finalization_does_not_change_intel_action():
    item = _item("BUY", recommended_dollar_amount=5_000.0, cash_constraint_status="passed")
    result = finalize_item_actionability(item)
    assert result.intel_action == "BUY"
    assert result.intel_action == item.intel_action


def test_finalization_does_not_change_recommended_dollar_amount():
    item = _item("BUY", recommended_dollar_amount=5_000.0, cash_constraint_status="passed")
    result = finalize_item_actionability(item)
    assert result.recommended_dollar_amount == 5_000.0


def test_finalization_does_not_change_cash_constraint_status():
    item = _item("BUY", recommended_dollar_amount=5_000.0, cash_constraint_status="passed")
    result = finalize_item_actionability(item)
    assert result.cash_constraint_status == "passed"


def test_finalization_tax_guardrail_placeholder_unchanged():
    """tax_guardrail_status remains 'not_evaluated_yet' — honest placeholder."""
    item = _item("BUY", recommended_dollar_amount=5_000.0, cash_constraint_status="passed")
    result = finalize_item_actionability(item)
    assert result.tax_guardrail_status == "not_evaluated_yet"


def test_finalization_wash_sale_placeholder_unchanged():
    """wash_sale_guardrail_status remains 'not_evaluated_yet' — honest placeholder."""
    item = _item("BUY", recommended_dollar_amount=5_000.0, cash_constraint_status="passed")
    result = finalize_item_actionability(item)
    assert result.wash_sale_guardrail_status == "not_evaluated_yet"


def test_original_item_not_mutated():
    item = _item("BUY", recommended_dollar_amount=5_000.0, cash_constraint_status="passed")
    original_status = item.final_actionability_status
    _ = finalize_item_actionability(item)
    assert item.final_actionability_status == original_status


# ---------------------------------------------------------------------------
# Tests: apply_finalization_to_plan_items
# ---------------------------------------------------------------------------

def test_apply_finalization_mixed_list():
    items = [
        _item("BUY", recommended_dollar_amount=5_000.0, cash_constraint_status="passed"),
        _item("TRIM", recommended_dollar_amount=3_000.0, cash_constraint_status="not_applicable_trim_sell"),
        _hold_item(),
        _suppressed_item(),
        _item("BUY", recommended_dollar_amount=None, cash_constraint_status="not_evaluated_yet"),
    ]
    results = apply_finalization_to_plan_items(items)
    assert results[0].final_actionability_status == FINAL_ACTIONABLE_PENDING_TAX
    assert results[1].final_actionability_status == FINAL_ACTIONABLE_PENDING_TAX
    assert results[2].final_actionability_status == FINAL_INFORMATIONAL_HOLD
    assert results[3].final_actionability_status == FINAL_SUPPRESSED
    assert results[4].final_actionability_status == FINAL_NOT_READY


def test_apply_finalization_returns_new_list():
    items = [_item("BUY", recommended_dollar_amount=5_000.0, cash_constraint_status="passed")]
    results = apply_finalization_to_plan_items(items)
    assert results is not items
    assert results[0] is not items[0]


# ---------------------------------------------------------------------------
# Tests: plan-builder integration (build_deploy_plan path)
# ---------------------------------------------------------------------------

def _make_certified_bundle(
    ticker: str,
    portfolio_value: float,
    current_market_value: float,
    current_weight: float,
    target_weight: float,
    cash_usd: float | None = None,
):
    from app.services.deploy.deploy_policy_bridge import certify_sizing_policy
    from app.services.deploy.deploy_sizing_contracts import (
        DeployCashInput,
        DeployPortfolioSizingInput,
        DeployPositionSizingInput,
        DeploySizingInputBundle,
        DeploySizingTrustStatus,
    )
    from app.services.deploy.deploy_target_allocation_bridge import certify_target_allocation

    effective_cash = cash_usd if cash_usd is not None else portfolio_value * 0.1
    return DeploySizingInputBundle(
        cash=DeployCashInput(
            available_cash_usd=effective_cash,
            trust_status=DeploySizingTrustStatus.CERTIFIED,
            source_label="test",
        ),
        portfolio=DeployPortfolioSizingInput(
            total_portfolio_value_usd=portfolio_value,
            trust_status=DeploySizingTrustStatus.CERTIFIED,
            source_label="test",
        ),
        positions={
            ticker: DeployPositionSizingInput(
                ticker=ticker,
                current_market_value_usd=current_market_value,
                current_weight=current_weight,
                trust_status=DeploySizingTrustStatus.CERTIFIED,
                source_label="test",
            )
        },
        target_allocations={
            ticker: certify_target_allocation(ticker, target_weight, source_label="optimizer")
        },
        policy=certify_sizing_policy(1.0, "WHOLE_DOLLAR"),
    )


def _snapshot_and_inputs(cards):
    from app.services.deploy.deploy_intel_adapter import build_deploy_inputs_from_snapshot
    from tests.test_deploy_foundation_v1 import _snapshot
    snap = _snapshot(cards)
    return build_deploy_inputs_from_snapshot(snap)


def _card(ticker, action, evidence_band="PARTIAL"):
    from tests.test_deploy_foundation_v1 import _card as _c
    return _c(ticker, action, evidence_band=evidence_band)


def test_plan_builder_no_bundle_buy_not_ready():
    """No bundle → BUY gets not_ready (no dollars, finalization always runs)."""
    from app.services.deploy.deploy_translation_v1 import build_deploy_plan
    inputs = _snapshot_and_inputs([_card("AAPL", "BUY")])
    plan = build_deploy_plan(inputs)
    assert plan.items[0].final_actionability_status == FINAL_NOT_READY
    assert plan.guardrail_summary.finalization_evaluated is True


def test_plan_builder_certified_bundle_buy_sufficient_cash_actionable():
    """Certified bundle, sufficient cash → BUY final status actionable_pending_tax."""
    from app.services.deploy.deploy_translation_v1 import build_deploy_plan
    # current=90%, target=95%, delta=5000, cash=10000 > delta → passes
    bundle = _make_certified_bundle(
        "AAPL", 100_000.0, 90_000.0, 0.90, 0.95, cash_usd=10_000.0
    )
    inputs = _snapshot_and_inputs([_card("AAPL", "BUY")])
    plan = build_deploy_plan(inputs, sizing_bundle=bundle)
    item = plan.items[0]
    assert item.recommended_dollar_amount == 5_000.0
    assert item.final_actionability_status == FINAL_ACTIONABLE_PENDING_TAX
    assert item.intel_action == "BUY"


def test_plan_builder_certified_bundle_buy_insufficient_cash_blocked():
    """Certified bundle, cash < amount → blocked_cash; dollar amount unchanged."""
    from app.services.deploy.deploy_translation_v1 import build_deploy_plan
    # current=90%, target=95%, delta=5000, cash=1000 < delta → blocked
    bundle = _make_certified_bundle(
        "AAPL", 100_000.0, 90_000.0, 0.90, 0.95, cash_usd=1_000.0
    )
    inputs = _snapshot_and_inputs([_card("AAPL", "BUY")])
    plan = build_deploy_plan(inputs, sizing_bundle=bundle)
    item = plan.items[0]
    assert item.recommended_dollar_amount == 5_000.0  # unchanged
    assert item.final_actionability_status == FINAL_BLOCKED_CASH
    assert item.intel_action == "BUY"


def test_plan_builder_trim_actionable_pending_tax():
    """TRIM through plan builder with certified bundle → actionable_pending_tax."""
    from app.services.deploy.deploy_translation_v1 import build_deploy_plan
    # current=95%, target=90%, TRIM delta = $5,000
    bundle = _make_certified_bundle(
        "AAPL", 100_000.0, 95_000.0, 0.95, 0.90
    )
    inputs = _snapshot_and_inputs([_card("AAPL", "TRIM")])
    plan = build_deploy_plan(inputs, sizing_bundle=bundle)
    item = plan.items[0]
    assert item.recommended_dollar_amount == 5_000.0
    assert item.final_actionability_status == FINAL_ACTIONABLE_PENDING_TAX


def test_plan_builder_hold_informational():
    """HOLD through plan builder → informational_hold."""
    from app.services.deploy.deploy_translation_v1 import build_deploy_plan
    bundle = _make_certified_bundle("AAPL", 100_000.0, 90_000.0, 0.90, 0.90)
    inputs = _snapshot_and_inputs([_card("AAPL", "HOLD")])
    plan = build_deploy_plan(inputs, sizing_bundle=bundle)
    assert plan.items[0].final_actionability_status == FINAL_INFORMATIONAL_HOLD


def test_plan_builder_finalization_evaluated_always_true():
    """guardrail_summary.finalization_evaluated is always True after build_deploy_plan."""
    from app.services.deploy.deploy_translation_v1 import build_deploy_plan
    inputs = _snapshot_and_inputs([_card("AAPL", "BUY")])
    plan = build_deploy_plan(inputs)
    assert plan.guardrail_summary.finalization_evaluated is True


def test_plan_builder_tax_wash_sale_placeholders_unchanged():
    """Tax/wash-sale placeholders remain 'not_evaluated_yet' — not silently passed."""
    from app.services.deploy.deploy_translation_v1 import build_deploy_plan
    # current=90%, target=95%, delta=5000, cash=10000 → actionable
    bundle = _make_certified_bundle(
        "AAPL", 100_000.0, 90_000.0, 0.90, 0.95, cash_usd=10_000.0
    )
    inputs = _snapshot_and_inputs([_card("AAPL", "BUY")])
    plan = build_deploy_plan(inputs, sizing_bundle=bundle)
    item = plan.items[0]
    assert item.tax_guardrail_status == "not_evaluated_yet"
    assert item.wash_sale_guardrail_status == "not_evaluated_yet"
    # But final status is pending tax (honest about not evaluated)
    assert item.final_actionability_status == FINAL_ACTIONABLE_PENDING_TAX


# ---------------------------------------------------------------------------
# Tests: non-positive dollar amounts must not produce actionable status
# ---------------------------------------------------------------------------

def test_buy_zero_dollar_amount_cash_passed_not_ready():
    """BUY with recommended_dollar_amount=0 and cash passed → not_ready (not actionable)."""
    item = _item("BUY", recommended_dollar_amount=0.0, cash_constraint_status="passed")
    result = finalize_item_actionability(item)
    assert result.final_actionability_status == FINAL_NOT_READY


def test_buy_negative_dollar_amount_cash_passed_not_ready():
    """BUY with negative recommended_dollar_amount and cash passed → not_ready."""
    item = _item("BUY", recommended_dollar_amount=-100.0, cash_constraint_status="passed")
    result = finalize_item_actionability(item)
    assert result.final_actionability_status == FINAL_NOT_READY


def test_buy_zero_dollar_blocked_cash_still_blocked():
    """BUY with zero dollars and blocked cash → blocked_cash (primary reason preserved)."""
    item = _item("BUY", recommended_dollar_amount=0.0, cash_constraint_status="blocked_insufficient_cash")
    result = finalize_item_actionability(item)
    assert result.final_actionability_status == FINAL_BLOCKED_CASH


def test_trim_zero_dollar_amount_not_ready():
    """TRIM with zero dollar amount and non-blocking cash → not_ready."""
    item = _item("TRIM", recommended_dollar_amount=0.0, cash_constraint_status="not_applicable_trim_sell")
    result = finalize_item_actionability(item)
    assert result.final_actionability_status == FINAL_NOT_READY


def test_trim_negative_dollar_amount_not_ready():
    """TRIM with negative dollar amount and non-blocking cash → not_ready."""
    item = _item("TRIM", recommended_dollar_amount=-50.0, cash_constraint_status="not_applicable_trim_sell")
    result = finalize_item_actionability(item)
    assert result.final_actionability_status == FINAL_NOT_READY


def test_sell_zero_dollar_amount_not_ready():
    """SELL with zero dollar amount and non-blocking cash → not_ready."""
    item = _item("SELL", recommended_dollar_amount=0.0, cash_constraint_status="not_applicable_trim_sell")
    result = finalize_item_actionability(item)
    assert result.final_actionability_status == FINAL_NOT_READY


def test_recommended_dollar_amount_not_mutated_by_zero_check():
    """Finalization never mutates recommended_dollar_amount even when it's zero."""
    item = _item("BUY", recommended_dollar_amount=0.0, cash_constraint_status="passed")
    result = finalize_item_actionability(item)
    assert result.recommended_dollar_amount == 0.0
