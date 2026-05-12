"""Tests — Deploy Stage 2.3E: plan-level readiness rollup v1.

Backend-only contract. Proves that build_plan_rollup deterministically
aggregates a list of finalized DeployPlanItems into counts and a plan-level
readiness label, and that build_deploy_plan exposes the rollup on the returned
DeployPlan without mutating items or changing existing item-level behavior.

Covers:
  Rollup pure-function:
    1.  Empty list → no_items, all counts zero.
    2.  All HOLD/informational → all_informational.
    3.  All suppressed → all_suppressed.
    4.  All pending-tax (BUY) → ready_pending_guardrails.
    5.  All pending-tax (TRIM/SELL) → ready_pending_guardrails.
    6.  Pending + informational only → ready_pending_guardrails.
    7.  Pending + suppressed only → ready_pending_guardrails.
    8.  Pending + blocked → partially_ready.
    9.  Pending + not_ready → partially_ready.
   10.  Blocked-only (no pending) → blocked.
   11.  not_ready-only → not_ready.
   12.  Blocked + not_ready (no pending) → blocked (blocked dominates).
   13.  Items with unrecognized final_actionability_status → unknown bucket
        and not_ready fail-safe.
   14.  Items with unrecognized pending_guardrails_reason → unknown reason
        bucket; status bucket unaffected.
   15.  counts_by_final_actionability_status sums to total_items.
   16.  counts_by_pending_guardrails_reason sums to total_items.
   17.  Convenience totals match dict counts.
   18.  Rollup never mutates input items.
   19.  actionable_count is always 0 today (no fully-actionable final status).
   20.  Mixed plan: 1 BUY pending + 1 BUY blocked + 1 HOLD + 1 SUPPRESSED →
        partially_ready, all counts correct.

  build_deploy_plan integration:
   21.  No bundle → rollup present, every BUY → not_ready, status not_ready.
   22.  Certified bundle, sufficient cash BUY → ready_pending_guardrails.
   23.  Certified bundle, insufficient cash BUY → blocked.
   24.  Certified bundle, TRIM → ready_pending_guardrails.
   25.  HOLD-only plan → all_informational.
   26.  Suppressed-only plan (stale snapshot) → all_suppressed.
   27.  Empty input → no_items, total_items=0.
   28.  Rollup does not mutate items returned in plan (per-item fields preserved).
   29.  No bundle: schema_version on rollup is deploy_v1_scaffold.
   30.  Mixed plan via builder: 1 BUY pending + 1 BUY blocked → partially_ready.
"""
from __future__ import annotations

import dataclasses

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
)
from app.services.deploy.deploy_plan_rollup_v1 import (
    ROLLUP_ALL_INFORMATIONAL,
    ROLLUP_ALL_SUPPRESSED,
    ROLLUP_BLOCKED,
    ROLLUP_NO_ITEMS,
    ROLLUP_NOT_READY,
    ROLLUP_PARTIALLY_READY,
    ROLLUP_READY_PENDING_GUARDRAILS,
    ROLLUP_UNKNOWN_BUCKET,
    DeployPlanRollup,
    build_plan_rollup,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _finalized(
    *,
    ticker: str = "AAPL",
    intel_action: str = "BUY",
    actionability: DeployActionabilityStatus = DeployActionabilityStatus.ACTIONABLE_CANDIDATE,
    plan_status: DeployPlanStatus = DeployPlanStatus.SCAFFOLD,
    final_actionability_status: str = FINAL_ACTIONABLE_PENDING_TAX,
    pending_guardrails_reason: str = PENDING_REASON_TAX_WASH_NOT_EVALUATED,
    recommended_dollar_amount: float | None = 5_000.0,
    cash_constraint_status: str = "passed",
) -> DeployPlanItem:
    """Build a finalized DeployPlanItem directly (no plan-builder needed)."""
    return DeployPlanItem(
        ticker=ticker,
        intel_action=intel_action,
        actionability_status=actionability,
        action_source=DeployActionSource.INTEL_V3,
        intel_snapshot_id="snap-001",
        intel_run_id="run-001",
        plan_status=plan_status,
        recommended_dollar_amount=recommended_dollar_amount,
        cash_constraint_status=cash_constraint_status,
        tax_guardrail_status="not_evaluated_yet",
        wash_sale_guardrail_status="not_evaluated_yet",
        final_actionability_status=final_actionability_status,
        pending_guardrails_reason=pending_guardrails_reason,
    )


def _hold_finalized() -> DeployPlanItem:
    return _finalized(
        ticker="GOOG",
        intel_action="HOLD",
        actionability=DeployActionabilityStatus.NOT_ACTIONABLE_HOLD,
        plan_status=DeployPlanStatus.HOLD_ONLY,
        final_actionability_status=FINAL_INFORMATIONAL_HOLD,
        pending_guardrails_reason=PENDING_REASON_NONE,
        recommended_dollar_amount=None,
        cash_constraint_status="not_applicable_hold",
    )


def _suppressed_finalized() -> DeployPlanItem:
    return _finalized(
        ticker="TSLA",
        intel_action="BUY",
        actionability=DeployActionabilityStatus.SUPPRESSED_STALE,
        plan_status=DeployPlanStatus.SUPPRESSED,
        final_actionability_status=FINAL_SUPPRESSED,
        pending_guardrails_reason=PENDING_REASON_NONE,
        recommended_dollar_amount=None,
        cash_constraint_status="not_applicable_suppressed",
    )


def _blocked_buy() -> DeployPlanItem:
    return _finalized(
        ticker="MSFT",
        intel_action="BUY",
        final_actionability_status=FINAL_BLOCKED_CASH,
        pending_guardrails_reason=PENDING_REASON_NONE,
        recommended_dollar_amount=5_000.0,
        cash_constraint_status="blocked_insufficient_cash",
    )


def _not_ready_buy() -> DeployPlanItem:
    return _finalized(
        ticker="NVDA",
        intel_action="BUY",
        final_actionability_status=FINAL_NOT_READY,
        pending_guardrails_reason=PENDING_REASON_NONE,
        recommended_dollar_amount=None,
        cash_constraint_status="not_evaluated_yet",
    )


# ---------------------------------------------------------------------------
# Pure-function rollup tests
# ---------------------------------------------------------------------------

def test_rollup_empty_list_no_items():
    rollup = build_plan_rollup([])
    assert rollup.total_items == 0
    assert rollup.plan_readiness_status == ROLLUP_NO_ITEMS
    assert rollup.actionable_count == 0
    assert rollup.pending_count == 0
    assert rollup.blocked_count == 0
    assert rollup.informational_count == 0
    assert rollup.suppressed_count == 0
    assert rollup.not_ready_count == 0
    assert rollup.unknown_count == 0


def test_rollup_all_informational():
    rollup = build_plan_rollup([_hold_finalized(), _hold_finalized(), _hold_finalized()])
    assert rollup.plan_readiness_status == ROLLUP_ALL_INFORMATIONAL
    assert rollup.informational_count == 3
    assert rollup.counts_by_final_actionability_status[FINAL_INFORMATIONAL_HOLD] == 3
    assert rollup.counts_by_pending_guardrails_reason[PENDING_REASON_NONE] == 3


def test_rollup_all_suppressed():
    rollup = build_plan_rollup([_suppressed_finalized(), _suppressed_finalized()])
    assert rollup.plan_readiness_status == ROLLUP_ALL_SUPPRESSED
    assert rollup.suppressed_count == 2


def test_rollup_all_pending_buy_ready_pending_guardrails():
    items = [
        _finalized(ticker="AAPL", intel_action="BUY"),
        _finalized(ticker="MSFT", intel_action="BUY"),
    ]
    rollup = build_plan_rollup(items)
    assert rollup.plan_readiness_status == ROLLUP_READY_PENDING_GUARDRAILS
    assert rollup.pending_count == 2
    assert rollup.counts_by_pending_guardrails_reason[PENDING_REASON_TAX_WASH_NOT_EVALUATED] == 2


def test_rollup_all_pending_trim_sell_ready_pending_guardrails():
    items = [
        _finalized(
            ticker="AAPL",
            intel_action="TRIM",
            recommended_dollar_amount=3_000.0,
            cash_constraint_status="not_applicable_trim_sell",
        ),
        _finalized(
            ticker="MSFT",
            intel_action="SELL",
            recommended_dollar_amount=4_000.0,
            cash_constraint_status="not_applicable_trim_sell",
        ),
    ]
    rollup = build_plan_rollup(items)
    assert rollup.plan_readiness_status == ROLLUP_READY_PENDING_GUARDRAILS
    assert rollup.pending_count == 2


def test_rollup_pending_with_informational_still_ready():
    items = [_finalized(intel_action="BUY"), _hold_finalized()]
    rollup = build_plan_rollup(items)
    assert rollup.plan_readiness_status == ROLLUP_READY_PENDING_GUARDRAILS
    assert rollup.pending_count == 1
    assert rollup.informational_count == 1


def test_rollup_pending_with_suppressed_still_ready():
    items = [_finalized(intel_action="BUY"), _suppressed_finalized()]
    rollup = build_plan_rollup(items)
    assert rollup.plan_readiness_status == ROLLUP_READY_PENDING_GUARDRAILS
    assert rollup.pending_count == 1
    assert rollup.suppressed_count == 1


def test_rollup_pending_plus_blocked_partially_ready():
    items = [_finalized(intel_action="BUY"), _blocked_buy()]
    rollup = build_plan_rollup(items)
    assert rollup.plan_readiness_status == ROLLUP_PARTIALLY_READY
    assert rollup.pending_count == 1
    assert rollup.blocked_count == 1


def test_rollup_pending_plus_not_ready_partially_ready():
    items = [_finalized(intel_action="BUY"), _not_ready_buy()]
    rollup = build_plan_rollup(items)
    assert rollup.plan_readiness_status == ROLLUP_PARTIALLY_READY
    assert rollup.pending_count == 1
    assert rollup.not_ready_count == 1


def test_rollup_blocked_only_blocked():
    items = [_blocked_buy(), _blocked_buy()]
    rollup = build_plan_rollup(items)
    assert rollup.plan_readiness_status == ROLLUP_BLOCKED
    assert rollup.blocked_count == 2


def test_rollup_not_ready_only_not_ready():
    items = [_not_ready_buy(), _not_ready_buy()]
    rollup = build_plan_rollup(items)
    assert rollup.plan_readiness_status == ROLLUP_NOT_READY
    assert rollup.not_ready_count == 2


def test_rollup_blocked_plus_not_ready_no_pending_blocked_dominates():
    items = [_blocked_buy(), _not_ready_buy()]
    rollup = build_plan_rollup(items)
    assert rollup.plan_readiness_status == ROLLUP_BLOCKED
    assert rollup.blocked_count == 1
    assert rollup.not_ready_count == 1


def test_rollup_unknown_final_status_falls_into_unknown_bucket():
    """Item with unrecognized final_actionability_status → unknown bucket; readiness fails safe."""
    item = _finalized(
        intel_action="BUY",
        final_actionability_status="some_future_status_we_do_not_know",
        pending_guardrails_reason=PENDING_REASON_NONE,
    )
    rollup = build_plan_rollup([item])
    assert rollup.unknown_count == 1
    assert rollup.counts_by_final_actionability_status[ROLLUP_UNKNOWN_BUCKET] == 1
    # No pending, no blocked, no informational, no suppressed → fail-safe to NOT_READY.
    assert rollup.plan_readiness_status == ROLLUP_NOT_READY


def test_rollup_unknown_pending_reason_falls_into_unknown_bucket():
    """Item with unrecognized pending_guardrails_reason → unknown reason bucket only."""
    item = _finalized(
        intel_action="BUY",
        final_actionability_status=FINAL_ACTIONABLE_PENDING_TAX,
        pending_guardrails_reason="some_future_reason",
    )
    rollup = build_plan_rollup([item])
    assert rollup.pending_count == 1
    assert rollup.counts_by_pending_guardrails_reason[ROLLUP_UNKNOWN_BUCKET] == 1
    # Status bucket unaffected — still pending tax.
    assert rollup.counts_by_final_actionability_status[FINAL_ACTIONABLE_PENDING_TAX] == 1


def test_rollup_status_counts_sum_to_total():
    items = [
        _finalized(intel_action="BUY"),
        _finalized(intel_action="BUY"),
        _hold_finalized(),
        _suppressed_finalized(),
        _blocked_buy(),
        _not_ready_buy(),
    ]
    rollup = build_plan_rollup(items)
    assert sum(rollup.counts_by_final_actionability_status.values()) == len(items)
    assert rollup.total_items == len(items)


def test_rollup_reason_counts_sum_to_total():
    items = [
        _finalized(intel_action="BUY"),
        _hold_finalized(),
        _suppressed_finalized(),
        _blocked_buy(),
    ]
    rollup = build_plan_rollup(items)
    assert sum(rollup.counts_by_pending_guardrails_reason.values()) == len(items)


def test_rollup_convenience_totals_match_dict_counts():
    items = [
        _finalized(intel_action="BUY"),
        _hold_finalized(),
        _suppressed_finalized(),
        _blocked_buy(),
        _not_ready_buy(),
    ]
    rollup = build_plan_rollup(items)
    counts = rollup.counts_by_final_actionability_status
    assert rollup.pending_count == counts[FINAL_ACTIONABLE_PENDING_TAX]
    assert rollup.blocked_count == counts[FINAL_BLOCKED_CASH]
    assert rollup.informational_count == counts[FINAL_INFORMATIONAL_HOLD]
    assert rollup.suppressed_count == counts[FINAL_SUPPRESSED]
    assert rollup.not_ready_count == counts[FINAL_NOT_READY]
    assert rollup.unknown_count == counts[ROLLUP_UNKNOWN_BUCKET]


def test_rollup_does_not_mutate_input_items():
    items = [
        _finalized(intel_action="BUY"),
        _hold_finalized(),
        _suppressed_finalized(),
    ]
    snapshots = [dataclasses.replace(it) for it in items]
    _ = build_plan_rollup(items)
    for before, after in zip(snapshots, items):
        assert before == after


def test_rollup_actionable_count_always_zero_today():
    """No fully-actionable final status exists yet — actionable_count is reserved."""
    items = [
        _finalized(intel_action="BUY"),
        _finalized(intel_action="BUY"),
        _finalized(intel_action="TRIM", recommended_dollar_amount=3_000.0,
                   cash_constraint_status="not_applicable_trim_sell"),
    ]
    rollup = build_plan_rollup(items)
    assert rollup.actionable_count == 0
    # All pending → ready_pending_guardrails (not "fully_actionable").
    assert rollup.plan_readiness_status == ROLLUP_READY_PENDING_GUARDRAILS


def test_rollup_mixed_plan_full_breakdown():
    items = [
        _finalized(intel_action="BUY"),  # pending
        _blocked_buy(),                  # blocked
        _hold_finalized(),               # informational
        _suppressed_finalized(),         # suppressed
    ]
    rollup = build_plan_rollup(items)
    assert rollup.total_items == 4
    assert rollup.pending_count == 1
    assert rollup.blocked_count == 1
    assert rollup.informational_count == 1
    assert rollup.suppressed_count == 1
    assert rollup.not_ready_count == 0
    assert rollup.unknown_count == 0
    assert rollup.plan_readiness_status == ROLLUP_PARTIALLY_READY


# ---------------------------------------------------------------------------
# build_deploy_plan integration tests
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
        portfolio=DeployPortfolioSizingInput(
            total_portfolio_value_usd=portfolio_value,
            trust_status=DeploySizingTrustStatus.CERTIFIED,
        ),
        positions={ticker: DeployPositionSizingInput(
            ticker=ticker,
            current_market_value_usd=current_mkt,
            current_weight=current_weight,
            trust_status=DeploySizingTrustStatus.CERTIFIED,
        )},
        target_allocations={ticker: certify_target_allocation(ticker, target_weight, source_label="optimizer")},
        policy=certify_sizing_policy(1.0, "WHOLE_DOLLAR"),
    )


def _multi_certified_bundle(specs, portfolio_value, cash_usd):
    """Build a bundle covering multiple tickers.

    specs: list of (ticker, current_mkt, current_weight, target_weight).
    """
    from app.services.deploy.deploy_policy_bridge import certify_sizing_policy
    from app.services.deploy.deploy_sizing_contracts import (
        DeployCashInput, DeployPortfolioSizingInput, DeployPositionSizingInput,
        DeploySizingInputBundle, DeploySizingTrustStatus,
    )
    from app.services.deploy.deploy_target_allocation_bridge import certify_target_allocation
    return DeploySizingInputBundle(
        cash=DeployCashInput(
            available_cash_usd=cash_usd,
            trust_status=DeploySizingTrustStatus.CERTIFIED,
        ),
        portfolio=DeployPortfolioSizingInput(
            total_portfolio_value_usd=portfolio_value,
            trust_status=DeploySizingTrustStatus.CERTIFIED,
        ),
        positions={
            t: DeployPositionSizingInput(
                ticker=t,
                current_market_value_usd=mkt,
                current_weight=cw,
                trust_status=DeploySizingTrustStatus.CERTIFIED,
            )
            for (t, mkt, cw, _tw) in specs
        },
        target_allocations={
            t: certify_target_allocation(t, tw, source_label="optimizer")
            for (t, _mkt, _cw, tw) in specs
        },
        policy=certify_sizing_policy(1.0, "WHOLE_DOLLAR"),
    )


def _snap_inputs(cards):
    from app.services.deploy.deploy_intel_adapter import build_deploy_inputs_from_snapshot
    from tests.test_deploy_foundation_v1 import _snapshot
    return build_deploy_inputs_from_snapshot(_snapshot(cards))


def _stale_snap_inputs(cards):
    from app.services.deploy.deploy_intel_adapter import build_deploy_inputs_from_snapshot
    from tests.test_deploy_foundation_v1 import _snapshot
    return build_deploy_inputs_from_snapshot(_snapshot(cards, is_stale=True))


def _card(ticker, action, evidence_band="PARTIAL"):
    from tests.test_deploy_foundation_v1 import _card as _c
    return _c(ticker, action, evidence_band=evidence_band)


def test_builder_no_bundle_buy_rollup_not_ready():
    from app.services.deploy.deploy_translation_v1 import build_deploy_plan
    plan = build_deploy_plan(_snap_inputs([_card("AAPL", "BUY")]))
    assert plan.rollup is not None
    assert plan.rollup.plan_readiness_status == ROLLUP_NOT_READY
    assert plan.rollup.not_ready_count == 1
    assert plan.rollup.pending_count == 0
    assert plan.rollup.blocked_count == 0


def test_builder_certified_bundle_buy_sufficient_cash_ready_pending():
    from app.services.deploy.deploy_translation_v1 import build_deploy_plan
    # current=90%, target=95%, delta=5000, cash=10000 > delta → passes
    bundle = _certified_bundle("AAPL", 100_000.0, 90_000.0, 0.90, 0.95, cash_usd=10_000.0)
    plan = build_deploy_plan(_snap_inputs([_card("AAPL", "BUY")]), sizing_bundle=bundle)
    assert plan.rollup is not None
    assert plan.rollup.plan_readiness_status == ROLLUP_READY_PENDING_GUARDRAILS
    assert plan.rollup.pending_count == 1
    assert plan.rollup.counts_by_pending_guardrails_reason[
        PENDING_REASON_TAX_WASH_NOT_EVALUATED
    ] == 1


def test_builder_certified_bundle_buy_insufficient_cash_blocked():
    from app.services.deploy.deploy_translation_v1 import build_deploy_plan
    # current=90%, target=95%, delta=5000, cash=1000 < delta → blocked
    bundle = _certified_bundle("AAPL", 100_000.0, 90_000.0, 0.90, 0.95, cash_usd=1_000.0)
    plan = build_deploy_plan(_snap_inputs([_card("AAPL", "BUY")]), sizing_bundle=bundle)
    assert plan.rollup is not None
    assert plan.rollup.plan_readiness_status == ROLLUP_BLOCKED
    assert plan.rollup.blocked_count == 1
    assert plan.rollup.counts_by_final_actionability_status[FINAL_BLOCKED_CASH] == 1


def test_builder_certified_bundle_trim_ready_pending():
    from app.services.deploy.deploy_translation_v1 import build_deploy_plan
    # current=95%, target=90%, TRIM delta=5000
    bundle = _certified_bundle("AAPL", 100_000.0, 95_000.0, 0.95, 0.90)
    plan = build_deploy_plan(_snap_inputs([_card("AAPL", "TRIM")]), sizing_bundle=bundle)
    assert plan.rollup is not None
    assert plan.rollup.plan_readiness_status == ROLLUP_READY_PENDING_GUARDRAILS
    assert plan.rollup.pending_count == 1


def test_builder_hold_only_plan_all_informational():
    from app.services.deploy.deploy_translation_v1 import build_deploy_plan
    plan = build_deploy_plan(_snap_inputs([_card("AAPL", "HOLD"), _card("GOOG", "HOLD")]))
    assert plan.rollup is not None
    assert plan.rollup.plan_readiness_status == ROLLUP_ALL_INFORMATIONAL
    assert plan.rollup.informational_count == 2


def test_builder_suppressed_only_plan_all_suppressed():
    """Stale snapshot suppresses every BUY → all_suppressed."""
    from app.services.deploy.deploy_translation_v1 import build_deploy_plan
    plan = build_deploy_plan(_stale_snap_inputs([_card("AAPL", "BUY"), _card("MSFT", "BUY")]))
    assert plan.rollup is not None
    assert plan.rollup.plan_readiness_status == ROLLUP_ALL_SUPPRESSED
    assert plan.rollup.suppressed_count == 2


def test_builder_empty_inputs_rollup_no_items():
    from app.services.deploy.deploy_translation_v1 import build_deploy_plan
    plan = build_deploy_plan([])
    assert plan.rollup is not None
    assert plan.rollup.plan_readiness_status == ROLLUP_NO_ITEMS
    assert plan.rollup.total_items == 0


def test_builder_rollup_does_not_mutate_items():
    """Per-item fields after rollup must match what finalization produced."""
    from app.services.deploy.deploy_translation_v1 import build_deploy_plan
    # current=90%, target=95%, delta=5000, cash=10000 → passes
    bundle = _certified_bundle("AAPL", 100_000.0, 90_000.0, 0.90, 0.95, cash_usd=10_000.0)
    plan = build_deploy_plan(_snap_inputs([_card("AAPL", "BUY")]), sizing_bundle=bundle)
    item = plan.items[0]
    assert item.intel_action == "BUY"
    assert item.recommended_dollar_amount == 5_000.0
    assert item.cash_constraint_status == "passed"
    assert item.final_actionability_status == FINAL_ACTIONABLE_PENDING_TAX
    assert item.pending_guardrails_reason == PENDING_REASON_TAX_WASH_NOT_EVALUATED
    assert item.tax_guardrail_status == "not_evaluated_yet"
    assert item.wash_sale_guardrail_status == "not_evaluated_yet"


def test_builder_no_bundle_rollup_schema_version():
    from app.services.deploy.deploy_translation_v1 import build_deploy_plan
    plan = build_deploy_plan(_snap_inputs([_card("AAPL", "BUY")]))
    assert plan.rollup is not None
    assert plan.rollup.schema_version == "deploy_v1_scaffold"


def test_builder_mixed_plan_partially_ready():
    """1 BUY pending + 1 BUY blocked → partially_ready."""
    from app.services.deploy.deploy_translation_v1 import build_deploy_plan
    # AAPL: current=50%, target=55%, delta=5000; MSFT: current=5%, target=40%, delta=35000
    # Target total = 0.55+0.40 = 0.95 (valid); cash=6000: AAPL passes, MSFT blocked.
    bundle = _multi_certified_bundle(
        specs=[
            ("AAPL", 50_000.0, 0.50, 0.55),  # BUY delta = 5_000
            ("MSFT", 5_000.0, 0.05, 0.40),   # BUY delta = 35_000 (large)
        ],
        portfolio_value=100_000.0,
        cash_usd=6_000.0,  # Enough for AAPL (5_000), not for MSFT (35_000).
    )
    plan = build_deploy_plan(
        _snap_inputs([_card("AAPL", "BUY"), _card("MSFT", "BUY")]),
        sizing_bundle=bundle,
    )
    assert plan.rollup is not None
    assert plan.rollup.pending_count == 1
    assert plan.rollup.blocked_count == 1
    assert plan.rollup.plan_readiness_status == ROLLUP_PARTIALLY_READY


def test_builder_rollup_isinstance_dataclass():
    from app.services.deploy.deploy_translation_v1 import build_deploy_plan
    plan = build_deploy_plan(_snap_inputs([_card("AAPL", "HOLD")]))
    assert isinstance(plan.rollup, DeployPlanRollup)
