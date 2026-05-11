"""Tests — Deploy Stage 2.3B: cash constraint guardrail v1.

Covers:
  1.  BUY with certified sufficient cash → cash_constraint_status = "passed".
  2.  BUY with certified exact-match cash (amount == available) → "passed".
  3.  BUY with certified insufficient cash → "blocked_insufficient_cash".
  4.  BUY with MISSING cash trust → "blocked_uncertified_cash".
  5.  BUY with STALE cash trust → "blocked_uncertified_cash".
  6.  BUY with WEAK cash trust → "blocked_uncertified_cash".
  7.  BUY with CONFLICTING cash trust → "blocked_uncertified_cash".
  8.  BUY with bundle.cash=None → "blocked_uncertified_cash".
  9.  BUY with CERTIFIED trust but None available_cash_usd → "blocked_uncertified_cash".
 10.  BUY with CERTIFIED trust but negative available_cash_usd → "blocked_uncertified_cash".
 11.  BUY with no recommended_dollar_amount and certified cash → "not_evaluated_no_dollar_amount".
 12.  BUY with no recommended_dollar_amount and uncertified cash → "blocked_uncertified_cash".
 13.  TRIM (ACTIONABLE_CANDIDATE) → "not_applicable_trim_sell" (never blocked).
 14.  SELL (ACTIONABLE_CANDIDATE) → "not_applicable_trim_sell" (never blocked).
 15.  HOLD (NOT_ACTIONABLE_HOLD) → "not_applicable_hold".
 16.  Suppressed item (SUPPRESSED_MISSING_EVIDENCE) → "not_applicable_suppressed".
 17.  Suppressed item (SUPPRESSED_STALE) → "not_applicable_suppressed".
 18.  Intel action is never changed by the guardrail (BUY remains BUY).
 19.  recommended_dollar_amount is never changed by the guardrail.
 20.  Original DeployPlanItem is not mutated (immutability).
 21.  apply_cash_guardrail_to_plan_items handles mixed list correctly.
 22.  TRIM with uncertified cash is still not_applicable_trim_sell (not blocked).
 23.  Guardrail runs independently of exact_dollar_ready (can evaluate partial bundles).
 24.  Zero available cash and positive BUY amount → "blocked_insufficient_cash".
 25.  BUY passes when available cash exactly equals recommended amount (boundary).
"""
from __future__ import annotations

import pytest

from app.services.deploy.deploy_cash_guardrail_v1 import (
    CASH_BLOCKED_INSUFFICIENT,
    CASH_BLOCKED_UNCERTIFIED,
    CASH_NOT_APPLICABLE_HOLD,
    CASH_NOT_APPLICABLE_SUPPRESSED,
    CASH_NOT_APPLICABLE_TRIM_SELL,
    CASH_NOT_EVALUATED_NO_DOLLAR_AMOUNT,
    CASH_PASSED,
    apply_cash_guardrail_to_plan_items,
    evaluate_cash_constraint_for_item,
)
from app.services.deploy.deploy_contracts import (
    DeployActionabilityStatus,
    DeployActionSource,
    DeployPlanItem,
    DeployPlanStatus,
)
from app.services.deploy.deploy_policy_bridge import certify_sizing_policy
from app.services.deploy.deploy_sizing_contracts import (
    DeployCashInput,
    DeployPortfolioSizingInput,
    DeployPositionSizingInput,
    DeploySizingInputBundle,
    DeploySizingTrustStatus,
)
from app.services.deploy.deploy_target_allocation_bridge import certify_target_allocation


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_bundle(
    *,
    cash_usd: float = 10_000.0,
    cash_trust: DeploySizingTrustStatus = DeploySizingTrustStatus.CERTIFIED,
    cash_none: bool = False,
    bundle_cash_none: bool = False,
) -> DeploySizingInputBundle:
    """Build a minimal DeploySizingInputBundle for cash guardrail testing."""
    if bundle_cash_none:
        cash_input = None
    elif cash_none:
        cash_input = DeployCashInput(
            available_cash_usd=None,
            trust_status=DeploySizingTrustStatus.CERTIFIED,
        )
    else:
        cash_input = DeployCashInput(
            available_cash_usd=cash_usd,
            trust_status=cash_trust,
        )

    portfolio = DeployPortfolioSizingInput(
        total_portfolio_value_usd=100_000.0,
        trust_status=DeploySizingTrustStatus.CERTIFIED,
    )
    ticker = "AAPL"
    pos = DeployPositionSizingInput(
        ticker=ticker,
        current_market_value_usd=10_000.0,
        current_weight=0.10,
        trust_status=DeploySizingTrustStatus.CERTIFIED,
    )
    ta = certify_target_allocation(ticker, 0.15, source_label="optimizer")
    policy = certify_sizing_policy(1.0, "WHOLE_DOLLAR")

    return DeploySizingInputBundle(
        cash=cash_input,
        portfolio=portfolio,
        positions={ticker: pos},
        target_allocations={ticker: ta},
        policy=policy,
    )


def _make_buy_item(
    *,
    ticker: str = "AAPL",
    recommended_dollar_amount: float | None = 5_000.0,
    actionability: DeployActionabilityStatus = DeployActionabilityStatus.ACTIONABLE_CANDIDATE,
) -> DeployPlanItem:
    return DeployPlanItem(
        ticker=ticker,
        intel_action="BUY",
        actionability_status=actionability,
        action_source=DeployActionSource.INTEL_V3,
        intel_snapshot_id="snap-001",
        intel_run_id="run-001",
        plan_status=DeployPlanStatus.SCAFFOLD,
        recommended_dollar_amount=recommended_dollar_amount,
        cash_constraint_status="not_evaluated_yet",
    )


def _make_item(
    intel_action: str,
    *,
    actionability: DeployActionabilityStatus = DeployActionabilityStatus.ACTIONABLE_CANDIDATE,
    recommended_dollar_amount: float | None = 3_000.0,
) -> DeployPlanItem:
    return DeployPlanItem(
        ticker="XYZ",
        intel_action=intel_action,
        actionability_status=actionability,
        action_source=DeployActionSource.INTEL_V3,
        intel_snapshot_id="snap-001",
        intel_run_id="run-001",
        plan_status=DeployPlanStatus.SCAFFOLD,
        recommended_dollar_amount=recommended_dollar_amount,
        cash_constraint_status="not_evaluated_yet",
    )


# ---------------------------------------------------------------------------
# Tests: BUY — certified cash, sufficient
# ---------------------------------------------------------------------------

def test_buy_certified_sufficient_cash_passes():
    """BUY with recommended_dollar_amount < available_cash → passed."""
    bundle = _make_bundle(cash_usd=10_000.0)
    item = _make_buy_item(recommended_dollar_amount=5_000.0)
    result = evaluate_cash_constraint_for_item(bundle, item)
    assert result.cash_constraint_status == CASH_PASSED


def test_buy_exact_match_cash_passes():
    """BUY where recommended_dollar_amount == available_cash → passed (boundary)."""
    bundle = _make_bundle(cash_usd=5_000.0)
    item = _make_buy_item(recommended_dollar_amount=5_000.0)
    result = evaluate_cash_constraint_for_item(bundle, item)
    assert result.cash_constraint_status == CASH_PASSED


# ---------------------------------------------------------------------------
# Tests: BUY — certified cash, insufficient
# ---------------------------------------------------------------------------

def test_buy_certified_insufficient_cash_blocked():
    """BUY with recommended_dollar_amount > available_cash → blocked_insufficient_cash."""
    bundle = _make_bundle(cash_usd=1_000.0)
    item = _make_buy_item(recommended_dollar_amount=5_000.0)
    result = evaluate_cash_constraint_for_item(bundle, item)
    assert result.cash_constraint_status == CASH_BLOCKED_INSUFFICIENT


def test_buy_zero_available_cash_blocked():
    """BUY with zero available cash and positive amount → blocked_insufficient_cash."""
    bundle = _make_bundle(cash_usd=0.0)
    item = _make_buy_item(recommended_dollar_amount=1.0)
    result = evaluate_cash_constraint_for_item(bundle, item)
    assert result.cash_constraint_status == CASH_BLOCKED_INSUFFICIENT


# ---------------------------------------------------------------------------
# Tests: BUY — uncertified / missing / invalid cash
# ---------------------------------------------------------------------------

def test_buy_missing_cash_trust_blocked():
    bundle = _make_bundle(cash_trust=DeploySizingTrustStatus.MISSING)
    item = _make_buy_item(recommended_dollar_amount=5_000.0)
    result = evaluate_cash_constraint_for_item(bundle, item)
    assert result.cash_constraint_status == CASH_BLOCKED_UNCERTIFIED


def test_buy_stale_cash_trust_blocked():
    bundle = _make_bundle(cash_trust=DeploySizingTrustStatus.STALE)
    item = _make_buy_item(recommended_dollar_amount=5_000.0)
    result = evaluate_cash_constraint_for_item(bundle, item)
    assert result.cash_constraint_status == CASH_BLOCKED_UNCERTIFIED


def test_buy_weak_cash_trust_blocked():
    bundle = _make_bundle(cash_trust=DeploySizingTrustStatus.WEAK)
    item = _make_buy_item(recommended_dollar_amount=5_000.0)
    result = evaluate_cash_constraint_for_item(bundle, item)
    assert result.cash_constraint_status == CASH_BLOCKED_UNCERTIFIED


def test_buy_conflicting_cash_trust_blocked():
    bundle = _make_bundle(cash_trust=DeploySizingTrustStatus.CONFLICTING)
    item = _make_buy_item(recommended_dollar_amount=5_000.0)
    result = evaluate_cash_constraint_for_item(bundle, item)
    assert result.cash_constraint_status == CASH_BLOCKED_UNCERTIFIED


def test_buy_bundle_cash_none_blocked():
    """bundle.cash is None → blocked_uncertified_cash."""
    bundle = _make_bundle(bundle_cash_none=True)
    item = _make_buy_item(recommended_dollar_amount=5_000.0)
    result = evaluate_cash_constraint_for_item(bundle, item)
    assert result.cash_constraint_status == CASH_BLOCKED_UNCERTIFIED


def test_buy_certified_trust_but_none_value_blocked():
    """CERTIFIED trust but available_cash_usd=None → blocked_uncertified_cash."""
    bundle = _make_bundle(cash_none=True)
    item = _make_buy_item(recommended_dollar_amount=5_000.0)
    result = evaluate_cash_constraint_for_item(bundle, item)
    assert result.cash_constraint_status == CASH_BLOCKED_UNCERTIFIED


def test_buy_certified_trust_but_negative_value_blocked():
    """CERTIFIED trust but available_cash_usd < 0 → blocked_uncertified_cash."""
    bundle = _make_bundle(cash_usd=-100.0)
    item = _make_buy_item(recommended_dollar_amount=5_000.0)
    result = evaluate_cash_constraint_for_item(bundle, item)
    assert result.cash_constraint_status == CASH_BLOCKED_UNCERTIFIED


# ---------------------------------------------------------------------------
# Tests: BUY — no dollar amount (dollar math suppressed upstream)
# ---------------------------------------------------------------------------

def test_buy_no_dollar_amount_certified_cash():
    """BUY with no recommended_dollar_amount and certified cash → not_evaluated_no_dollar_amount."""
    bundle = _make_bundle(cash_usd=10_000.0)
    item = _make_buy_item(recommended_dollar_amount=None)
    result = evaluate_cash_constraint_for_item(bundle, item)
    assert result.cash_constraint_status == CASH_NOT_EVALUATED_NO_DOLLAR_AMOUNT


def test_buy_no_dollar_amount_uncertified_cash():
    """BUY with no recommended_dollar_amount and uncertified cash → blocked_uncertified_cash."""
    bundle = _make_bundle(cash_trust=DeploySizingTrustStatus.MISSING)
    item = _make_buy_item(recommended_dollar_amount=None)
    result = evaluate_cash_constraint_for_item(bundle, item)
    assert result.cash_constraint_status == CASH_BLOCKED_UNCERTIFIED


# ---------------------------------------------------------------------------
# Tests: TRIM / SELL — never blocked
# ---------------------------------------------------------------------------

def test_trim_not_blocked_by_cash():
    """TRIM is not applicable for cash constraint regardless of cash state."""
    bundle = _make_bundle(cash_usd=0.0)  # zero cash — should not block TRIM
    item = _make_item("TRIM")
    result = evaluate_cash_constraint_for_item(bundle, item)
    assert result.cash_constraint_status == CASH_NOT_APPLICABLE_TRIM_SELL


def test_sell_not_blocked_by_cash():
    """SELL is not applicable for cash constraint."""
    bundle = _make_bundle(cash_trust=DeploySizingTrustStatus.MISSING)
    item = _make_item("SELL")
    result = evaluate_cash_constraint_for_item(bundle, item)
    assert result.cash_constraint_status == CASH_NOT_APPLICABLE_TRIM_SELL


def test_trim_uncertified_cash_still_not_applicable():
    """TRIM with uncertified cash still gets not_applicable_trim_sell (not blocked)."""
    bundle = _make_bundle(cash_trust=DeploySizingTrustStatus.STALE)
    item = _make_item("TRIM")
    result = evaluate_cash_constraint_for_item(bundle, item)
    assert result.cash_constraint_status == CASH_NOT_APPLICABLE_TRIM_SELL


# ---------------------------------------------------------------------------
# Tests: HOLD
# ---------------------------------------------------------------------------

def test_hold_is_not_applicable():
    """HOLD → not_applicable_hold."""
    bundle = _make_bundle(cash_usd=10_000.0)
    item = _make_item(
        "HOLD",
        actionability=DeployActionabilityStatus.NOT_ACTIONABLE_HOLD,
        recommended_dollar_amount=None,
    )
    result = evaluate_cash_constraint_for_item(bundle, item)
    assert result.cash_constraint_status == CASH_NOT_APPLICABLE_HOLD


# ---------------------------------------------------------------------------
# Tests: Suppressed items
# ---------------------------------------------------------------------------

def test_suppressed_missing_evidence_not_applicable():
    bundle = _make_bundle()
    item = _make_item(
        "BUY",
        actionability=DeployActionabilityStatus.SUPPRESSED_MISSING_EVIDENCE,
        recommended_dollar_amount=None,
    )
    result = evaluate_cash_constraint_for_item(bundle, item)
    assert result.cash_constraint_status == CASH_NOT_APPLICABLE_SUPPRESSED


def test_suppressed_stale_not_applicable():
    bundle = _make_bundle()
    item = _make_item(
        "BUY",
        actionability=DeployActionabilityStatus.SUPPRESSED_STALE,
        recommended_dollar_amount=None,
    )
    result = evaluate_cash_constraint_for_item(bundle, item)
    assert result.cash_constraint_status == CASH_NOT_APPLICABLE_SUPPRESSED


# ---------------------------------------------------------------------------
# Tests: Invariants
# ---------------------------------------------------------------------------

def test_intel_action_not_changed():
    """Guardrail must not modify intel_action."""
    bundle = _make_bundle(cash_usd=10_000.0)
    item = _make_buy_item(recommended_dollar_amount=5_000.0)
    result = evaluate_cash_constraint_for_item(bundle, item)
    assert result.intel_action == "BUY"
    assert result.intel_action == item.intel_action


def test_recommended_dollar_amount_not_changed():
    """Guardrail must not modify recommended_dollar_amount."""
    bundle = _make_bundle(cash_usd=1_000.0)  # insufficient
    item = _make_buy_item(recommended_dollar_amount=5_000.0)
    result = evaluate_cash_constraint_for_item(bundle, item)
    assert result.recommended_dollar_amount == 5_000.0  # unchanged despite block


def test_original_item_not_mutated():
    """Original DeployPlanItem must not be mutated."""
    bundle = _make_bundle(cash_usd=10_000.0)
    item = _make_buy_item(recommended_dollar_amount=5_000.0)
    original_status = item.cash_constraint_status
    _ = evaluate_cash_constraint_for_item(bundle, item)
    assert item.cash_constraint_status == original_status


# ---------------------------------------------------------------------------
# Tests: apply_cash_guardrail_to_plan_items — mixed list
# ---------------------------------------------------------------------------

def test_apply_guardrail_mixed_list():
    """Mixed list: BUY-passed, TRIM-not_applicable, HOLD-not_applicable, suppressed."""
    bundle = _make_bundle(cash_usd=10_000.0)

    buy_item = _make_buy_item(recommended_dollar_amount=5_000.0)
    trim_item = _make_item("TRIM")
    hold_item = _make_item(
        "HOLD",
        actionability=DeployActionabilityStatus.NOT_ACTIONABLE_HOLD,
        recommended_dollar_amount=None,
    )
    suppressed_item = _make_item(
        "BUY",
        actionability=DeployActionabilityStatus.SUPPRESSED_STALE,
        recommended_dollar_amount=None,
    )

    results = apply_cash_guardrail_to_plan_items(bundle, [buy_item, trim_item, hold_item, suppressed_item])

    assert results[0].cash_constraint_status == CASH_PASSED
    assert results[1].cash_constraint_status == CASH_NOT_APPLICABLE_TRIM_SELL
    assert results[2].cash_constraint_status == CASH_NOT_APPLICABLE_HOLD
    assert results[3].cash_constraint_status == CASH_NOT_APPLICABLE_SUPPRESSED


def test_apply_guardrail_returns_new_list():
    """apply_cash_guardrail_to_plan_items returns a new list, not the original."""
    bundle = _make_bundle(cash_usd=10_000.0)
    items = [_make_buy_item(recommended_dollar_amount=5_000.0)]
    results = apply_cash_guardrail_to_plan_items(bundle, items)
    assert results is not items
    assert results[0] is not items[0]
