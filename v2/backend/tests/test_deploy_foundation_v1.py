"""Tests — Deploy Foundation v1 (Stage 2.0 backend-only domain seam).

Proves the following invariants:
  1. Adapter reads Intel v3 snapshot read-only and produces DeployPlanInputs.
  2. BUY maps only to buy candidate scaffolding.
  3. TRIM maps only to trim candidate scaffolding.
  4. SELL maps only to sell candidate scaffolding.
  5. HOLD produces no trade/action amounts and cannot become actionable.
  6. Missing / stale / weak / blocked evidence suppresses actionability.
  7. Exact-dollar fields remain null / not-evaluated in v1.
  8. PriceBand/valuation context does not influence Deploy action authority.
  9. Intel action is preserved read-only in every item.
 10. Guardrail summary correctly records boundary enforcement.
 11. Deploy cannot emit BUY/TRIM/SELL unless Intel action matches.
 12. Schema version is deploy_v1_scaffold throughout.
"""
import pytest

from app.services.deploy.deploy_contracts import (
    DeployActionabilityStatus,
    DeployActionSource,
    DeployPlanStatus,
)
from app.services.deploy.deploy_intel_adapter import build_deploy_inputs_from_snapshot
from app.services.deploy.deploy_translation_v1 import (
    _classify_actionability,
    _translate_item,
    build_deploy_plan,
)


# ──────────────────────────────────────────────────────────────────────────────
# Test helpers
# ──────────────────────────────────────────────────────────────────────────────

def _card(
    ticker: str = "AAPL",
    action: str = "BUY",
    conviction: str = "MEDIUM",
    evidence_band: str = "PARTIAL",
    flags: list | None = None,
    price_context: str | None = "FAIR",
) -> dict:
    return {
        "ticker": ticker,
        "action": action,
        "conviction": conviction,
        "evidence_band": evidence_band,
        "flags": flags or [],
        "detail_drawer_payload": {
            "price_context": price_context,
        },
    }


def _snapshot(
    cards: list,
    snapshot_id: str = "snap-001",
    run_id: str = "run-001",
    is_stale: bool = False,
) -> dict:
    return {
        "snapshot_id": snapshot_id,
        "run_id": run_id,
        "is_stale": is_stale,
        "current_holdings": cards,
    }


def _make_input(
    ticker: str = "AAPL",
    intel_action: str = "BUY",
    intel_conviction: str = "MEDIUM",
    intel_evidence_band: str = "PARTIAL",
    snapshot_id: str = "s-1",
    run_id: str = "r-1",
    has_missing_evidence: bool = False,
    has_stale_evidence: bool = False,
    has_weak_evidence: bool = False,
    is_blocked: bool = False,
    price_context_label: str | None = "FAIR",
):
    from app.services.deploy.deploy_contracts import DeployPlanInput
    return DeployPlanInput(
        ticker=ticker,
        intel_action=intel_action,
        intel_conviction=intel_conviction,
        intel_evidence_band=intel_evidence_band,
        intel_snapshot_id=snapshot_id,
        intel_run_id=run_id,
        has_missing_evidence=has_missing_evidence,
        has_stale_evidence=has_stale_evidence,
        has_weak_evidence=has_weak_evidence,
        is_blocked=is_blocked,
        price_context_label=price_context_label,
        action_source=DeployActionSource.INTEL_V3,
    )


# ──────────────────────────────────────────────────────────────────────────────
# 1. Adapter reads snapshot read-only
# ──────────────────────────────────────────────────────────────────────────────

def test_adapter_produces_one_input_per_card():
    snap = _snapshot([_card("AAPL", "BUY"), _card("MSFT", "HOLD"), _card("NVDA", "TRIM")])
    inputs = build_deploy_inputs_from_snapshot(snap)
    assert len(inputs) == 3


def test_adapter_tickers_match_cards():
    snap = _snapshot([_card("AAPL", "BUY"), _card("GOOG", "HOLD")])
    inputs = build_deploy_inputs_from_snapshot(snap)
    tickers = {i.ticker for i in inputs}
    assert tickers == {"AAPL", "GOOG"}


def test_adapter_action_source_is_intel_v3():
    snap = _snapshot([_card("AAPL", "BUY")])
    inputs = build_deploy_inputs_from_snapshot(snap)
    assert inputs[0].action_source == DeployActionSource.INTEL_V3


def test_adapter_snapshot_ids_propagated():
    snap = _snapshot([_card("AAPL", "BUY")], snapshot_id="s-999", run_id="r-888")
    inputs = build_deploy_inputs_from_snapshot(snap)
    assert inputs[0].intel_snapshot_id == "s-999"
    assert inputs[0].intel_run_id == "r-888"


def test_adapter_preserves_intel_action_verbatim():
    snap = _snapshot([
        _card("AAPL", "BUY"),
        _card("NVDA", "TRIM"),
        _card("META", "SELL"),
        _card("GOOG", "HOLD"),
    ])
    inputs = build_deploy_inputs_from_snapshot(snap)
    by_ticker = {i.ticker: i for i in inputs}
    assert by_ticker["AAPL"].intel_action == "BUY"
    assert by_ticker["NVDA"].intel_action == "TRIM"
    assert by_ticker["META"].intel_action == "SELL"
    assert by_ticker["GOOG"].intel_action == "HOLD"


def test_adapter_thin_evidence_sets_missing_flag():
    snap = _snapshot([_card("AAPL", "BUY", evidence_band="THIN")])
    inputs = build_deploy_inputs_from_snapshot(snap)
    assert inputs[0].has_missing_evidence is True


def test_adapter_partial_evidence_does_not_set_missing_flag():
    snap = _snapshot([_card("AAPL", "BUY", evidence_band="PARTIAL")])
    inputs = build_deploy_inputs_from_snapshot(snap)
    assert inputs[0].has_missing_evidence is False


def test_adapter_stale_snapshot_sets_stale_flag_on_all_cards():
    snap = _snapshot([_card("AAPL", "BUY"), _card("MSFT", "HOLD")], is_stale=True)
    inputs = build_deploy_inputs_from_snapshot(snap)
    assert all(i.has_stale_evidence for i in inputs)


def test_adapter_fresh_snapshot_does_not_set_stale_flag():
    snap = _snapshot([_card("AAPL", "BUY")], is_stale=False)
    inputs = build_deploy_inputs_from_snapshot(snap)
    assert inputs[0].has_stale_evidence is False


def test_adapter_blocked_flag_detected_from_flags_list():
    snap = _snapshot([_card("AAPL", "BUY", flags=["Portfolio fit blocked — speculative or high-risk category."])])
    inputs = build_deploy_inputs_from_snapshot(snap)
    assert inputs[0].is_blocked is True


def test_adapter_no_blocked_flag_when_flags_empty():
    snap = _snapshot([_card("AAPL", "BUY", flags=[])])
    inputs = build_deploy_inputs_from_snapshot(snap)
    assert inputs[0].is_blocked is False


def test_adapter_price_context_passed_through():
    snap = _snapshot([_card("AAPL", "BUY", price_context="EXPENSIVE")])
    inputs = build_deploy_inputs_from_snapshot(snap)
    assert inputs[0].price_context_label == "EXPENSIVE"


def test_adapter_price_context_none_allowed():
    snap = _snapshot([_card("AAPL", "BUY", price_context=None)])
    inputs = build_deploy_inputs_from_snapshot(snap)
    assert inputs[0].price_context_label is None


def test_adapter_empty_snapshot_returns_empty_list():
    snap = _snapshot([])
    inputs = build_deploy_inputs_from_snapshot(snap)
    assert inputs == []


# ──────────────────────────────────────────────────────────────────────────────
# 2. BUY → buy candidate scaffolding
# ──────────────────────────────────────────────────────────────────────────────

def test_buy_with_good_evidence_is_actionable_candidate():
    inp = _make_input("AAPL", "BUY", intel_evidence_band="PARTIAL")
    status, reason = _classify_actionability(inp)
    assert status == DeployActionabilityStatus.ACTIONABLE_CANDIDATE
    assert reason is None


def test_buy_item_plan_status_is_scaffold():
    snap = _snapshot([_card("AAPL", "BUY", evidence_band="PARTIAL")])
    inputs = build_deploy_inputs_from_snapshot(snap)
    plan = build_deploy_plan(inputs)
    assert plan.items[0].plan_status == DeployPlanStatus.SCAFFOLD


def test_buy_item_intel_action_preserved():
    snap = _snapshot([_card("AAPL", "BUY", evidence_band="PARTIAL")])
    inputs = build_deploy_inputs_from_snapshot(snap)
    plan = build_deploy_plan(inputs)
    assert plan.items[0].intel_action == "BUY"


def test_buy_candidates_counted_in_guardrail():
    snap = _snapshot([
        _card("AAPL", "BUY", evidence_band="PARTIAL"),
        _card("MSFT", "BUY", evidence_band="STRONG", conviction="HIGH"),
    ])
    inputs = build_deploy_inputs_from_snapshot(snap)
    plan = build_deploy_plan(inputs)
    assert plan.guardrail_summary.buy_candidates == 2


# ──────────────────────────────────────────────────────────────────────────────
# 3. TRIM → trim candidate scaffolding
# ──────────────────────────────────────────────────────────────────────────────

def test_trim_with_good_evidence_is_actionable_candidate():
    inp = _make_input("NVDA", "TRIM", intel_evidence_band="PARTIAL")
    status, _ = _classify_actionability(inp)
    assert status == DeployActionabilityStatus.ACTIONABLE_CANDIDATE


def test_trim_item_plan_status_is_scaffold():
    snap = _snapshot([_card("NVDA", "TRIM", evidence_band="PARTIAL")])
    inputs = build_deploy_inputs_from_snapshot(snap)
    plan = build_deploy_plan(inputs)
    assert plan.items[0].plan_status == DeployPlanStatus.SCAFFOLD


def test_trim_item_intel_action_preserved():
    snap = _snapshot([_card("NVDA", "TRIM", evidence_band="PARTIAL")])
    inputs = build_deploy_inputs_from_snapshot(snap)
    plan = build_deploy_plan(inputs)
    assert plan.items[0].intel_action == "TRIM"


def test_trim_candidates_counted_in_guardrail():
    snap = _snapshot([_card("NVDA", "TRIM", evidence_band="PARTIAL")])
    inputs = build_deploy_inputs_from_snapshot(snap)
    plan = build_deploy_plan(inputs)
    assert plan.guardrail_summary.trim_candidates == 1
    assert plan.guardrail_summary.buy_candidates == 0


# ──────────────────────────────────────────────────────────────────────────────
# 4. SELL → sell candidate scaffolding
# ──────────────────────────────────────────────────────────────────────────────

def test_sell_with_good_evidence_is_actionable_candidate():
    inp = _make_input("META", "SELL", intel_evidence_band="PARTIAL")
    status, _ = _classify_actionability(inp)
    assert status == DeployActionabilityStatus.ACTIONABLE_CANDIDATE


def test_sell_item_plan_status_is_scaffold():
    snap = _snapshot([_card("META", "SELL", evidence_band="PARTIAL")])
    inputs = build_deploy_inputs_from_snapshot(snap)
    plan = build_deploy_plan(inputs)
    assert plan.items[0].plan_status == DeployPlanStatus.SCAFFOLD


def test_sell_item_intel_action_preserved():
    snap = _snapshot([_card("META", "SELL", evidence_band="PARTIAL")])
    inputs = build_deploy_inputs_from_snapshot(snap)
    plan = build_deploy_plan(inputs)
    assert plan.items[0].intel_action == "SELL"


def test_sell_candidates_counted_in_guardrail():
    snap = _snapshot([_card("META", "SELL", evidence_band="PARTIAL")])
    inputs = build_deploy_inputs_from_snapshot(snap)
    plan = build_deploy_plan(inputs)
    assert plan.guardrail_summary.sell_candidates == 1
    assert plan.guardrail_summary.buy_candidates == 0
    assert plan.guardrail_summary.trim_candidates == 0


# ──────────────────────────────────────────────────────────────────────────────
# 5. HOLD → never actionable, no trade amounts
# ──────────────────────────────────────────────────────────────────────────────

def test_hold_is_not_actionable_hold():
    inp = _make_input("GOOG", "HOLD")
    status, reason = _classify_actionability(inp)
    assert status == DeployActionabilityStatus.NOT_ACTIONABLE_HOLD
    assert reason is None


def test_hold_item_plan_status_is_hold_only():
    snap = _snapshot([_card("GOOG", "HOLD")])
    inputs = build_deploy_inputs_from_snapshot(snap)
    plan = build_deploy_plan(inputs)
    assert plan.items[0].plan_status == DeployPlanStatus.HOLD_ONLY


def test_hold_cannot_become_actionable_candidate():
    snap = _snapshot([_card("GOOG", "HOLD", evidence_band="STRONG", conviction="HIGH")])
    inputs = build_deploy_inputs_from_snapshot(snap)
    plan = build_deploy_plan(inputs)
    item = plan.items[0]
    assert item.actionability_status == DeployActionabilityStatus.NOT_ACTIONABLE_HOLD
    assert item.actionability_status != DeployActionabilityStatus.ACTIONABLE_CANDIDATE


def test_hold_has_null_dollar_amount():
    snap = _snapshot([_card("GOOG", "HOLD")])
    inputs = build_deploy_inputs_from_snapshot(snap)
    plan = build_deploy_plan(inputs)
    assert plan.items[0].recommended_dollar_amount is None


def test_hold_has_null_share_quantity():
    snap = _snapshot([_card("GOOG", "HOLD")])
    inputs = build_deploy_inputs_from_snapshot(snap)
    plan = build_deploy_plan(inputs)
    assert plan.items[0].estimated_share_quantity is None


def test_hold_has_no_suppression_reason():
    snap = _snapshot([_card("GOOG", "HOLD")])
    inputs = build_deploy_inputs_from_snapshot(snap)
    plan = build_deploy_plan(inputs)
    assert plan.items[0].suppression_reason is None


def test_all_hold_portfolio_plan_status_is_hold_only():
    snap = _snapshot([_card("GOOG", "HOLD"), _card("AAPL", "HOLD")])
    inputs = build_deploy_inputs_from_snapshot(snap)
    plan = build_deploy_plan(inputs)
    assert plan.plan_status == DeployPlanStatus.HOLD_ONLY


def test_guardrail_hold_never_actionable_true():
    snap = _snapshot([_card("GOOG", "HOLD"), _card("AAPL", "BUY", evidence_band="PARTIAL")])
    inputs = build_deploy_inputs_from_snapshot(snap)
    plan = build_deploy_plan(inputs)
    assert plan.guardrail_summary.hold_never_actionable is True


def test_hold_counted_in_guardrail_hold_items():
    snap = _snapshot([_card("GOOG", "HOLD"), _card("AAPL", "HOLD")])
    inputs = build_deploy_inputs_from_snapshot(snap)
    plan = build_deploy_plan(inputs)
    assert plan.guardrail_summary.hold_items == 2


# ──────────────────────────────────────────────────────────────────────────────
# 6. Evidence suppression
# ──────────────────────────────────────────────────────────────────────────────

def test_thin_evidence_suppresses_buy_actionability():
    snap = _snapshot([_card("AAPL", "BUY", evidence_band="THIN", conviction="LOW")])
    inputs = build_deploy_inputs_from_snapshot(snap)
    plan = build_deploy_plan(inputs)
    item = plan.items[0]
    assert item.actionability_status == DeployActionabilityStatus.SUPPRESSED_MISSING_EVIDENCE
    assert item.plan_status == DeployPlanStatus.SUPPRESSED


def test_thin_evidence_suppresses_trim_actionability():
    snap = _snapshot([_card("NVDA", "TRIM", evidence_band="THIN", conviction="LOW")])
    inputs = build_deploy_inputs_from_snapshot(snap)
    plan = build_deploy_plan(inputs)
    assert plan.items[0].actionability_status == DeployActionabilityStatus.SUPPRESSED_MISSING_EVIDENCE


def test_thin_evidence_suppresses_sell_actionability():
    snap = _snapshot([_card("META", "SELL", evidence_band="THIN", conviction="LOW")])
    inputs = build_deploy_inputs_from_snapshot(snap)
    plan = build_deploy_plan(inputs)
    assert plan.items[0].actionability_status == DeployActionabilityStatus.SUPPRESSED_MISSING_EVIDENCE


def test_stale_snapshot_suppresses_buy_actionability():
    snap = _snapshot([_card("AAPL", "BUY", evidence_band="PARTIAL")], is_stale=True)
    inputs = build_deploy_inputs_from_snapshot(snap)
    plan = build_deploy_plan(inputs)
    assert plan.items[0].actionability_status == DeployActionabilityStatus.SUPPRESSED_STALE
    assert plan.items[0].plan_status == DeployPlanStatus.SUPPRESSED


def test_stale_snapshot_suppresses_trim_actionability():
    snap = _snapshot([_card("NVDA", "TRIM", evidence_band="PARTIAL")], is_stale=True)
    inputs = build_deploy_inputs_from_snapshot(snap)
    plan = build_deploy_plan(inputs)
    assert plan.items[0].actionability_status == DeployActionabilityStatus.SUPPRESSED_STALE


def test_blocked_flag_suppresses_buy_actionability():
    snap = _snapshot([
        _card("AAPL", "BUY", evidence_band="PARTIAL",
              flags=["Portfolio fit blocked — speculative or high-risk category."])
    ])
    inputs = build_deploy_inputs_from_snapshot(snap)
    plan = build_deploy_plan(inputs)
    assert plan.items[0].actionability_status == DeployActionabilityStatus.SUPPRESSED_BLOCKED
    assert plan.items[0].plan_status == DeployPlanStatus.SUPPRESSED


def test_suppression_reason_populated_for_missing_evidence():
    snap = _snapshot([_card("AAPL", "BUY", evidence_band="THIN", conviction="LOW")])
    inputs = build_deploy_inputs_from_snapshot(snap)
    plan = build_deploy_plan(inputs)
    assert plan.items[0].suppression_reason is not None
    assert "THIN" in plan.items[0].suppression_reason


def test_suppression_reason_populated_for_stale():
    snap = _snapshot([_card("AAPL", "BUY", evidence_band="PARTIAL")], is_stale=True)
    inputs = build_deploy_inputs_from_snapshot(snap)
    plan = build_deploy_plan(inputs)
    assert plan.items[0].suppression_reason is not None
    assert "stale" in plan.items[0].suppression_reason.lower()


def test_suppression_reason_populated_for_blocked():
    snap = _snapshot([
        _card("AAPL", "BUY", evidence_band="PARTIAL",
              flags=["Portfolio fit blocked — speculative or high-risk category."])
    ])
    inputs = build_deploy_inputs_from_snapshot(snap)
    plan = build_deploy_plan(inputs)
    assert plan.items[0].suppression_reason is not None
    assert "blocked" in plan.items[0].suppression_reason.lower()


def test_missing_evidence_takes_priority_over_stale():
    snap = _snapshot([_card("AAPL", "BUY", evidence_band="THIN", conviction="LOW")], is_stale=True)
    inputs = build_deploy_inputs_from_snapshot(snap)
    plan = build_deploy_plan(inputs)
    # Missing evidence has higher priority than stale.
    assert plan.items[0].actionability_status == DeployActionabilityStatus.SUPPRESSED_MISSING_EVIDENCE


def test_mixed_portfolio_plan_status_suppressed_when_any_item_suppressed():
    snap = _snapshot([
        _card("AAPL", "BUY", evidence_band="PARTIAL"),           # actionable
        _card("NVDA", "BUY", evidence_band="THIN", conviction="LOW"),  # suppressed
        _card("GOOG", "HOLD"),                                    # hold
    ])
    inputs = build_deploy_inputs_from_snapshot(snap)
    plan = build_deploy_plan(inputs)
    assert plan.plan_status == DeployPlanStatus.SUPPRESSED


def test_suppressed_items_counted_in_guardrail():
    snap = _snapshot([
        _card("AAPL", "BUY", evidence_band="THIN", conviction="LOW"),
        _card("MSFT", "BUY", evidence_band="PARTIAL"),
    ])
    inputs = build_deploy_inputs_from_snapshot(snap)
    plan = build_deploy_plan(inputs)
    assert plan.guardrail_summary.suppressed_items == 1


# ──────────────────────────────────────────────────────────────────────────────
# 7. Exact-dollar fields are null / not-evaluated in v1
# ──────────────────────────────────────────────────────────────────────────────

def test_buy_candidate_has_null_dollar_amount():
    snap = _snapshot([_card("AAPL", "BUY", evidence_band="PARTIAL")])
    inputs = build_deploy_inputs_from_snapshot(snap)
    plan = build_deploy_plan(inputs)
    assert plan.items[0].recommended_dollar_amount is None


def test_buy_candidate_has_null_share_quantity():
    snap = _snapshot([_card("AAPL", "BUY", evidence_band="PARTIAL")])
    inputs = build_deploy_inputs_from_snapshot(snap)
    plan = build_deploy_plan(inputs)
    assert plan.items[0].estimated_share_quantity is None


def test_trim_candidate_has_null_dollar_amount():
    snap = _snapshot([_card("NVDA", "TRIM", evidence_band="PARTIAL")])
    inputs = build_deploy_inputs_from_snapshot(snap)
    plan = build_deploy_plan(inputs)
    assert plan.items[0].recommended_dollar_amount is None


def test_sell_candidate_has_null_dollar_amount():
    snap = _snapshot([_card("META", "SELL", evidence_band="PARTIAL")])
    inputs = build_deploy_inputs_from_snapshot(snap)
    plan = build_deploy_plan(inputs)
    assert plan.items[0].recommended_dollar_amount is None


def test_guardrail_dollar_fields_null():
    snap = _snapshot([
        _card("AAPL", "BUY", evidence_band="PARTIAL"),
        _card("NVDA", "TRIM", evidence_band="PARTIAL"),
        _card("GOOG", "HOLD"),
    ])
    inputs = build_deploy_inputs_from_snapshot(snap)
    plan = build_deploy_plan(inputs)
    assert plan.guardrail_summary.dollar_fields_null is True


def test_placeholder_fields_not_evaluated():
    snap = _snapshot([_card("AAPL", "BUY", evidence_band="PARTIAL")])
    inputs = build_deploy_inputs_from_snapshot(snap)
    plan = build_deploy_plan(inputs)
    item = plan.items[0]
    assert item.rounding_policy == "not_applied_yet"
    assert item.cash_constraint_status == "not_evaluated_yet"
    assert item.target_allocation_status == "not_evaluated_yet"
    assert item.tax_guardrail_status == "not_evaluated_yet"
    assert item.wash_sale_guardrail_status == "not_evaluated_yet"


# ──────────────────────────────────────────────────────────────────────────────
# 8. PriceBand does not influence Deploy action authority
# ──────────────────────────────────────────────────────────────────────────────

def test_priceband_expensive_does_not_suppress_buy_candidate():
    """PriceBand EXPENSIVE is supporting context only — BUY candidate still produced."""
    snap = _snapshot([_card("AAPL", "BUY", evidence_band="PARTIAL", price_context="EXPENSIVE")])
    inputs = build_deploy_inputs_from_snapshot(snap)
    plan = build_deploy_plan(inputs)
    assert plan.items[0].actionability_status == DeployActionabilityStatus.ACTIONABLE_CANDIDATE


def test_priceband_cheap_does_not_override_hold():
    """PriceBand CHEAP does not make a HOLD position actionable."""
    snap = _snapshot([_card("GOOG", "HOLD", evidence_band="STRONG", conviction="HIGH", price_context="CHEAP")])
    inputs = build_deploy_inputs_from_snapshot(snap)
    plan = build_deploy_plan(inputs)
    assert plan.items[0].actionability_status == DeployActionabilityStatus.NOT_ACTIONABLE_HOLD


def test_priceband_suppressed_does_not_suppress_deploy_candidate():
    """PriceBand SUPPRESSED does not suppress Deploy actionability."""
    snap = _snapshot([_card("AAPL", "BUY", evidence_band="PARTIAL", price_context="SUPPRESSED")])
    inputs = build_deploy_inputs_from_snapshot(snap)
    plan = build_deploy_plan(inputs)
    assert plan.items[0].actionability_status == DeployActionabilityStatus.ACTIONABLE_CANDIDATE


def test_priceband_none_does_not_suppress_actionable_candidate():
    """Missing PriceBand does not suppress an otherwise valid BUY candidate."""
    snap = _snapshot([_card("AAPL", "BUY", evidence_band="PARTIAL", price_context=None)])
    inputs = build_deploy_inputs_from_snapshot(snap)
    plan = build_deploy_plan(inputs)
    assert plan.items[0].actionability_status == DeployActionabilityStatus.ACTIONABLE_CANDIDATE


def test_guardrail_priceband_not_authority():
    snap = _snapshot([_card("AAPL", "BUY", evidence_band="PARTIAL", price_context="EXPENSIVE")])
    inputs = build_deploy_inputs_from_snapshot(snap)
    plan = build_deploy_plan(inputs)
    assert plan.guardrail_summary.priceband_not_authority is True


def test_priceband_context_accessible_in_input_but_not_used_for_classification():
    """PriceBand is stored in DeployPlanInput but _classify_actionability ignores it."""
    inp_expensive = _make_input("AAPL", "BUY", price_context_label="EXPENSIVE")
    inp_cheap = _make_input("AAPL", "BUY", price_context_label="CHEAP")
    inp_none = _make_input("AAPL", "BUY", price_context_label=None)

    status_exp, _ = _classify_actionability(inp_expensive)
    status_cheap, _ = _classify_actionability(inp_cheap)
    status_none, _ = _classify_actionability(inp_none)

    # All produce the same result regardless of PriceBand.
    assert status_exp == status_cheap == status_none == DeployActionabilityStatus.ACTIONABLE_CANDIDATE


# ──────────────────────────────────────────────────────────────────────────────
# 9. Intel action preserved read-only
# ──────────────────────────────────────────────────────────────────────────────

def test_intel_action_preserved_for_all_actions():
    snap = _snapshot([
        _card("AAPL", "BUY", evidence_band="PARTIAL"),
        _card("NVDA", "TRIM", evidence_band="PARTIAL"),
        _card("META", "SELL", evidence_band="PARTIAL"),
        _card("GOOG", "HOLD"),
    ])
    inputs = build_deploy_inputs_from_snapshot(snap)
    plan = build_deploy_plan(inputs)
    by_ticker = {item.ticker: item for item in plan.items}
    assert by_ticker["AAPL"].intel_action == "BUY"
    assert by_ticker["NVDA"].intel_action == "TRIM"
    assert by_ticker["META"].intel_action == "SELL"
    assert by_ticker["GOOG"].intel_action == "HOLD"


def test_guardrail_intel_action_preserved():
    snap = _snapshot([
        _card("AAPL", "BUY", evidence_band="PARTIAL"),
        _card("GOOG", "HOLD"),
    ])
    inputs = build_deploy_inputs_from_snapshot(snap)
    plan = build_deploy_plan(inputs)
    assert plan.guardrail_summary.intel_action_preserved is True


# ──────────────────────────────────────────────────────────────────────────────
# 10. Guardrail summary integrity
# ──────────────────────────────────────────────────────────────────────────────

def test_guardrail_total_items_matches_inputs():
    snap = _snapshot([
        _card("AAPL", "BUY", evidence_band="PARTIAL"),
        _card("NVDA", "TRIM", evidence_band="PARTIAL"),
        _card("GOOG", "HOLD"),
    ])
    inputs = build_deploy_inputs_from_snapshot(snap)
    plan = build_deploy_plan(inputs)
    assert plan.guardrail_summary.total_items == 3


def test_guardrail_counts_sum_to_total():
    snap = _snapshot([
        _card("AAPL", "BUY", evidence_band="PARTIAL"),
        _card("NVDA", "TRIM", evidence_band="PARTIAL"),
        _card("META", "SELL", evidence_band="PARTIAL"),
        _card("GOOG", "HOLD"),
        _card("AMZN", "BUY", evidence_band="THIN", conviction="LOW"),
    ])
    inputs = build_deploy_inputs_from_snapshot(snap)
    plan = build_deploy_plan(inputs)
    g = plan.guardrail_summary
    counted = g.buy_candidates + g.trim_candidates + g.sell_candidates + g.hold_items + g.suppressed_items
    assert counted == g.total_items


def test_guardrail_schema_version():
    snap = _snapshot([_card("AAPL", "BUY", evidence_band="PARTIAL")])
    inputs = build_deploy_inputs_from_snapshot(snap)
    plan = build_deploy_plan(inputs)
    assert plan.guardrail_summary.schema_version == "deploy_v1_scaffold"


def test_plan_has_guardrail_summary():
    snap = _snapshot([_card("AAPL", "BUY", evidence_band="PARTIAL")])
    inputs = build_deploy_inputs_from_snapshot(snap)
    plan = build_deploy_plan(inputs)
    assert plan.guardrail_summary is not None


# ──────────────────────────────────────────────────────────────────────────────
# 11. Deploy cannot emit BUY/TRIM/SELL unless Intel action matches
# ──────────────────────────────────────────────────────────────────────────────

def test_only_buy_intel_produces_buy_candidates():
    """TRIM and SELL do not contribute to buy_candidates."""
    snap = _snapshot([
        _card("NVDA", "TRIM", evidence_band="PARTIAL"),
        _card("META", "SELL", evidence_band="PARTIAL"),
    ])
    inputs = build_deploy_inputs_from_snapshot(snap)
    plan = build_deploy_plan(inputs)
    assert plan.guardrail_summary.buy_candidates == 0


def test_only_trim_intel_produces_trim_candidates():
    """BUY and SELL do not contribute to trim_candidates."""
    snap = _snapshot([
        _card("AAPL", "BUY", evidence_band="PARTIAL"),
        _card("META", "SELL", evidence_band="PARTIAL"),
    ])
    inputs = build_deploy_inputs_from_snapshot(snap)
    plan = build_deploy_plan(inputs)
    assert plan.guardrail_summary.trim_candidates == 0


def test_only_sell_intel_produces_sell_candidates():
    """BUY and TRIM do not contribute to sell_candidates."""
    snap = _snapshot([
        _card("AAPL", "BUY", evidence_band="PARTIAL"),
        _card("NVDA", "TRIM", evidence_band="PARTIAL"),
    ])
    inputs = build_deploy_inputs_from_snapshot(snap)
    plan = build_deploy_plan(inputs)
    assert plan.guardrail_summary.sell_candidates == 0


def test_hold_never_in_any_candidate_bucket():
    snap = _snapshot([_card("GOOG", "HOLD")])
    inputs = build_deploy_inputs_from_snapshot(snap)
    plan = build_deploy_plan(inputs)
    g = plan.guardrail_summary
    assert g.buy_candidates == 0
    assert g.trim_candidates == 0
    assert g.sell_candidates == 0
    assert g.hold_items == 1


# ──────────────────────────────────────────────────────────────────────────────
# 12. Schema version
# ──────────────────────────────────────────────────────────────────────────────

def test_plan_schema_version():
    snap = _snapshot([_card("AAPL", "BUY", evidence_band="PARTIAL")])
    inputs = build_deploy_inputs_from_snapshot(snap)
    plan = build_deploy_plan(inputs)
    assert plan.schema_version == "deploy_v1_scaffold"


def test_item_schema_version():
    snap = _snapshot([_card("AAPL", "BUY", evidence_band="PARTIAL")])
    inputs = build_deploy_inputs_from_snapshot(snap)
    plan = build_deploy_plan(inputs)
    assert plan.items[0].schema_version == "deploy_v1_scaffold"


def test_empty_snapshot_produces_valid_empty_plan():
    snap = _snapshot([])
    inputs = build_deploy_inputs_from_snapshot(snap)
    plan = build_deploy_plan(inputs)
    assert plan.plan_status == DeployPlanStatus.SCAFFOLD
    assert plan.items == []
    assert plan.schema_version == "deploy_v1_scaffold"
    assert plan.guardrail_summary is not None
    assert plan.guardrail_summary.total_items == 0


# ──────────────────────────────────────────────────────────────────────────────
# 13. Contracts import — no cross-domain contamination
# ──────────────────────────────────────────────────────────────────────────────

def test_deploy_contracts_have_no_intel_v3_imports():
    """Deploy contracts must not import Intel v3 internals."""
    import importlib
    import app.services.deploy.deploy_contracts as m
    src = importlib.util.find_spec("app.services.deploy.deploy_contracts").origin
    with open(src) as f:
        src_text = f.read()
    assert "from ..intelligence" not in src_text
    assert "from app.services.intelligence" not in src_text
    assert "decision_policy" not in src_text
    assert "intel_v3_service" not in src_text


def test_deploy_translation_does_not_import_recommendation_service():
    """Translation layer must not use legacy RecommendationService."""
    import importlib
    src = importlib.util.find_spec("app.services.deploy.deploy_translation_v1").origin
    with open(src) as f:
        src_text = f.read()
    assert "RecommendationService" not in src_text
    assert "intel_v2" not in src_text


# ──────────────────────────────────────────────────────────────────────────────
# 14. build_deploy_plan wired to exact-dollar math (Stage 2.3A correction)
# ──────────────────────────────────────────────────────────────────────────────

def _make_certified_bundle(
    ticker: str,
    portfolio_value: float,
    current_market_value: float,
    current_weight: float,
    target_weight: float,
    minimum_trade_usd: float = 1.0,
    rounding_policy: str = "WHOLE_DOLLAR",
):
    """Build a synthetic exact_dollar_ready bundle for integration tests."""
    from app.services.deploy.deploy_policy_bridge import certify_sizing_policy
    from app.services.deploy.deploy_sizing_contracts import (
        DeployCashInput,
        DeployPortfolioSizingInput,
        DeployPositionSizingInput,
        DeploySizingInputBundle,
        DeploySizingTrustStatus,
    )
    from app.services.deploy.deploy_target_allocation_bridge import certify_target_allocation

    return DeploySizingInputBundle(
        cash=DeployCashInput(
            available_cash_usd=portfolio_value * 0.1,
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
        policy=certify_sizing_policy(minimum_trade_usd, rounding_policy),
    )


def test_build_deploy_plan_without_bundle_preserves_null_scaffold():
    """No sizing bundle → old scaffold/null behavior unchanged."""
    snap = _snapshot([_card("AAPL", "BUY", evidence_band="PARTIAL")])
    inputs = build_deploy_inputs_from_snapshot(snap)
    plan = build_deploy_plan(inputs)  # no sizing_bundle
    assert plan.items[0].recommended_dollar_amount is None
    assert plan.items[0].estimated_share_quantity is None
    assert plan.guardrail_summary.dollar_fields_null is True
    assert plan.guardrail_summary.exact_dollar_math_evaluated is False


def test_build_deploy_plan_with_certified_bundle_populates_buy_dollars():
    """Certified exact-dollar-ready bundle → BUY item gets dollar amount."""
    # current=85%, target=90%, delta=5000
    bundle = _make_certified_bundle(
        ticker="AAPL",
        portfolio_value=100_000.0,
        current_market_value=85_000.0,
        current_weight=0.85,
        target_weight=0.90,   # delta=5000
    )
    assert bundle.exact_dollar_ready
    snap = _snapshot([_card("AAPL", "BUY", evidence_band="PARTIAL")])
    inputs = build_deploy_inputs_from_snapshot(snap)
    plan = build_deploy_plan(inputs, sizing_bundle=bundle)
    assert plan.items[0].recommended_dollar_amount == 5000.0
    assert plan.guardrail_summary.exact_dollar_math_evaluated is True
    assert plan.guardrail_summary.dollar_fields_null is False


def test_build_deploy_plan_readiness_false_keeps_outputs_null():
    """exact_dollar_ready=False → bundle provided but outputs remain null."""
    from app.services.deploy.deploy_sizing_contracts import (
        DeployCashInput,
        DeploySizingInputBundle,
        DeploySizingPolicyPlaceholder,
        DeploySizingTrustStatus,
        DeployPortfolioSizingInput,
        DeployPositionSizingInput,
    )
    # Make policy UNSUPPORTED so exact_dollar_ready=False.
    from app.services.deploy.deploy_target_allocation_bridge import certify_target_allocation
    bundle = DeploySizingInputBundle(
        cash=DeployCashInput(
            available_cash_usd=10_000.0,
            trust_status=DeploySizingTrustStatus.CERTIFIED,
            source_label="test",
        ),
        portfolio=DeployPortfolioSizingInput(
            total_portfolio_value_usd=100_000.0,
            trust_status=DeploySizingTrustStatus.CERTIFIED,
            source_label="test",
        ),
        positions={
            "AAPL": DeployPositionSizingInput(
                ticker="AAPL",
                current_market_value_usd=10_000.0,
                current_weight=0.10,
                trust_status=DeploySizingTrustStatus.CERTIFIED,
                source_label="test",
            )
        },
        target_allocations={
            "AAPL": certify_target_allocation("AAPL", 0.15, source_label="optimizer")
        },
        policy=DeploySizingPolicyPlaceholder(
            trust_status=DeploySizingTrustStatus.UNSUPPORTED,
        ),
    )
    assert not bundle.exact_dollar_ready
    snap = _snapshot([_card("AAPL", "BUY", evidence_band="PARTIAL")])
    inputs = build_deploy_inputs_from_snapshot(snap)
    plan = build_deploy_plan(inputs, sizing_bundle=bundle)
    assert plan.items[0].recommended_dollar_amount is None
    assert plan.guardrail_summary.dollar_fields_null is True
    assert plan.guardrail_summary.exact_dollar_math_evaluated is False


def test_build_deploy_plan_absent_position_suppresses_output():
    """Item ticker absent from bundle positions → dollar output suppressed."""
    from app.services.deploy.deploy_sizing_contracts import (
        DeployCashInput,
        DeploySizingInputBundle,
        DeploySizingTrustStatus,
        DeployPortfolioSizingInput,
    )
    from app.services.deploy.deploy_policy_bridge import certify_sizing_policy
    from app.services.deploy.deploy_target_allocation_bridge import certify_target_allocation
    bundle = DeploySizingInputBundle(
        cash=DeployCashInput(
            available_cash_usd=10_000.0,
            trust_status=DeploySizingTrustStatus.CERTIFIED,
            source_label="test",
        ),
        portfolio=DeployPortfolioSizingInput(
            total_portfolio_value_usd=100_000.0,
            trust_status=DeploySizingTrustStatus.CERTIFIED,
            source_label="test",
        ),
        positions={},   # AAPL not present
        target_allocations={
            "AAPL": certify_target_allocation("AAPL", 0.15, source_label="optimizer")
        },
        policy=certify_sizing_policy(1.0, "WHOLE_DOLLAR"),
    )
    assert bundle.exact_dollar_ready
    snap = _snapshot([_card("AAPL", "BUY", evidence_band="PARTIAL")])
    inputs = build_deploy_inputs_from_snapshot(snap)
    plan = build_deploy_plan(inputs, sizing_bundle=bundle)
    assert plan.items[0].recommended_dollar_amount is None
    assert plan.guardrail_summary.exact_dollar_math_evaluated is True   # math was attempted
    assert plan.guardrail_summary.dollar_fields_null is True             # but nothing populated


def test_build_deploy_plan_hold_non_actionable_with_certified_bundle():
    """HOLD remains non-actionable even when certified bundle is provided."""
    bundle = _make_certified_bundle(
        ticker="GOOG",
        portfolio_value=100_000.0,
        current_market_value=15_000.0,
        current_weight=0.15,
        target_weight=0.20,
    )
    snap = _snapshot([_card("GOOG", "HOLD", evidence_band="STRONG", conviction="HIGH")])
    inputs = build_deploy_inputs_from_snapshot(snap)
    plan = build_deploy_plan(inputs, sizing_bundle=bundle)
    assert plan.items[0].recommended_dollar_amount is None
    assert plan.items[0].actionability_status == DeployActionabilityStatus.NOT_ACTIONABLE_HOLD
    assert plan.guardrail_summary.hold_never_actionable is True


def test_build_deploy_plan_trim_populated_and_intel_action_preserved():
    """TRIM candidate gets dollar amount; Intel action is not changed."""
    # current=95%, target=90%, TRIM delta=5000
    bundle = _make_certified_bundle(
        ticker="NVDA",
        portfolio_value=100_000.0,
        current_market_value=95_000.0,
        current_weight=0.95,
        target_weight=0.90,   # TRIM delta=5000
    )
    snap = _snapshot([_card("NVDA", "TRIM", evidence_band="PARTIAL")])
    inputs = build_deploy_inputs_from_snapshot(snap)
    plan = build_deploy_plan(inputs, sizing_bundle=bundle)
    assert plan.items[0].recommended_dollar_amount == 5000.0
    assert plan.items[0].intel_action == "TRIM"
    assert plan.guardrail_summary.intel_action_preserved is True


def test_build_deploy_plan_guardrail_dollar_fields_null_still_true_without_bundle():
    """Existing guardrail invariant: dollar_fields_null=True when no bundle provided."""
    snap = _snapshot([
        _card("AAPL", "BUY", evidence_band="PARTIAL"),
        _card("NVDA", "TRIM", evidence_band="PARTIAL"),
        _card("GOOG", "HOLD"),
    ])
    inputs = build_deploy_inputs_from_snapshot(snap)
    plan = build_deploy_plan(inputs)
    assert plan.guardrail_summary.dollar_fields_null is True
    assert plan.guardrail_summary.exact_dollar_math_evaluated is False


# ---------------------------------------------------------------------------
# 15. Cash guardrail wired into build_deploy_plan (Stage 2.3B)
# ---------------------------------------------------------------------------

def test_build_deploy_plan_no_bundle_cash_status_placeholder():
    """No sizing bundle → cash_constraint_status remains placeholder; cash_guardrail_evaluated=False."""
    snap = _snapshot([_card("AAPL", "BUY", evidence_band="PARTIAL")])
    inputs = build_deploy_inputs_from_snapshot(snap)
    plan = build_deploy_plan(inputs)
    assert plan.items[0].cash_constraint_status == "not_evaluated_yet"
    assert plan.guardrail_summary.cash_guardrail_evaluated is False


def test_build_deploy_plan_certified_bundle_buy_sufficient_cash_passed():
    """Certified exact-dollar-ready bundle, sufficient cash → BUY cash_constraint_status=passed."""
    from app.services.deploy.deploy_cash_guardrail_v1 import CASH_PASSED
    # current=85%, target=90%, delta=5000; cash=10000 (portfolio*0.1) → sufficient
    bundle = _make_certified_bundle(
        ticker="AAPL",
        portfolio_value=100_000.0,
        current_market_value=85_000.0,
        current_weight=0.85,
        target_weight=0.90,  # delta = $5,000; cash = $10,000 → sufficient
    )
    snap = _snapshot([_card("AAPL", "BUY", evidence_band="PARTIAL")])
    inputs = build_deploy_inputs_from_snapshot(snap)
    plan = build_deploy_plan(inputs, sizing_bundle=bundle)
    item = plan.items[0]
    assert item.recommended_dollar_amount == 5000.0
    assert item.cash_constraint_status == CASH_PASSED
    assert item.intel_action == "BUY"
    assert plan.guardrail_summary.cash_guardrail_evaluated is True


def test_build_deploy_plan_certified_bundle_buy_insufficient_cash_blocked():
    """Certified bundle, cash less than recommended amount → blocked_insufficient_cash; dollar amount unchanged."""
    from app.services.deploy.deploy_cash_guardrail_v1 import CASH_BLOCKED_INSUFFICIENT
    from app.services.deploy.deploy_sizing_contracts import (
        DeployCashInput,
        DeploySizingTrustStatus,
    )
    # current=85%, target=90%, delta=5000
    bundle = _make_certified_bundle(
        ticker="AAPL",
        portfolio_value=100_000.0,
        current_market_value=85_000.0,
        current_weight=0.85,
        target_weight=0.90,  # delta = $5,000
    )
    # Override cash to $1,000 — insufficient for $5,000 BUY.
    import dataclasses
    bundle = dataclasses.replace(
        bundle,
        cash=DeployCashInput(
            available_cash_usd=1_000.0,
            trust_status=DeploySizingTrustStatus.CERTIFIED,
            source_label="test",
        ),
    )
    snap = _snapshot([_card("AAPL", "BUY", evidence_band="PARTIAL")])
    inputs = build_deploy_inputs_from_snapshot(snap)
    plan = build_deploy_plan(inputs, sizing_bundle=bundle)
    item = plan.items[0]
    assert item.recommended_dollar_amount == 5000.0  # not changed by guardrail
    assert item.cash_constraint_status == CASH_BLOCKED_INSUFFICIENT
    assert item.intel_action == "BUY"  # Intel action unchanged


def test_build_deploy_plan_trim_gets_explicit_non_blocking_cash_status():
    """TRIM through build_deploy_plan gets not_applicable_trim_sell (never blocked)."""
    from app.services.deploy.deploy_cash_guardrail_v1 import CASH_NOT_APPLICABLE_TRIM_SELL
    from app.services.deploy.deploy_sizing_contracts import (
        DeployCashInput,
        DeploySizingTrustStatus,
    )
    bundle = _make_certified_bundle(
        ticker="AAPL",
        portfolio_value=100_000.0,
        current_market_value=20_000.0,
        current_weight=0.20,
        target_weight=0.10,  # TRIM delta = $10,000
    )
    # Set cash to $0 — should not block TRIM.
    import dataclasses
    bundle = dataclasses.replace(
        bundle,
        cash=DeployCashInput(
            available_cash_usd=0.0,
            trust_status=DeploySizingTrustStatus.CERTIFIED,
            source_label="test",
        ),
    )
    snap = _snapshot([_card("AAPL", "TRIM", evidence_band="PARTIAL")])
    inputs = build_deploy_inputs_from_snapshot(snap)
    plan = build_deploy_plan(inputs, sizing_bundle=bundle)
    item = plan.items[0]
    assert item.cash_constraint_status == CASH_NOT_APPLICABLE_TRIM_SELL
    assert item.intel_action == "TRIM"


def test_build_deploy_plan_hold_cash_status_not_applicable():
    """HOLD through build_deploy_plan gets not_applicable_hold."""
    from app.services.deploy.deploy_cash_guardrail_v1 import CASH_NOT_APPLICABLE_HOLD
    bundle = _make_certified_bundle(
        ticker="AAPL",
        portfolio_value=100_000.0,
        current_market_value=10_000.0,
        current_weight=0.10,
        target_weight=0.10,
    )
    snap = _snapshot([_card("AAPL", "HOLD")])
    inputs = build_deploy_inputs_from_snapshot(snap)
    plan = build_deploy_plan(inputs, sizing_bundle=bundle)
    item = plan.items[0]
    assert item.cash_constraint_status == CASH_NOT_APPLICABLE_HOLD
    assert item.recommended_dollar_amount is None


def test_build_deploy_plan_readiness_false_buy_uncertified_cash_not_safe():
    """exact_dollar_ready=False, uncertified cash → BUY not cash-safe."""
    from app.services.deploy.deploy_cash_guardrail_v1 import CASH_BLOCKED_UNCERTIFIED
    from app.services.deploy.deploy_sizing_contracts import (
        DeployCashInput,
        DeployPortfolioSizingInput,
        DeployPositionSizingInput,
        DeploySizingInputBundle,
        DeploySizingPolicyPlaceholder,
        DeploySizingTrustStatus,
    )
    # Build a bundle that is NOT exact_dollar_ready (policy UNSUPPORTED) but has uncertified cash.
    bundle = DeploySizingInputBundle(
        cash=DeployCashInput(
            available_cash_usd=None,
            trust_status=DeploySizingTrustStatus.MISSING,
            source_label="test",
        ),
        portfolio=DeployPortfolioSizingInput(
            total_portfolio_value_usd=100_000.0,
            trust_status=DeploySizingTrustStatus.CERTIFIED,
            source_label="test",
        ),
        positions={},
        policy=DeploySizingPolicyPlaceholder(trust_status=DeploySizingTrustStatus.UNSUPPORTED),
    )
    assert not bundle.exact_dollar_ready
    snap = _snapshot([_card("AAPL", "BUY", evidence_band="PARTIAL")])
    inputs = build_deploy_inputs_from_snapshot(snap)
    plan = build_deploy_plan(inputs, sizing_bundle=bundle)
    item = plan.items[0]
    # Dollar math not run (not exact_dollar_ready), so no dollar amount.
    assert item.recommended_dollar_amount is None
    # Cash guardrail still ran and correctly flagged uncertified cash.
    assert item.cash_constraint_status == CASH_BLOCKED_UNCERTIFIED
    assert plan.guardrail_summary.cash_guardrail_evaluated is True
