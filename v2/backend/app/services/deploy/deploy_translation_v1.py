"""Deploy Foundation v1 — translation skeleton.

Converts certified Intel v3 visible decisions into scaffolded future
action-plan candidates. No exact-dollar math in v1.

Guardrails enforced in code:
  - Deploy cannot emit a BUY candidate unless Intel visible action is BUY.
  - Deploy cannot emit a TRIM candidate unless Intel visible action is TRIM.
  - Deploy cannot emit a SELL candidate unless Intel visible action is SELL.
  - Deploy cannot convert HOLD into BUY/TRIM/SELL.
  - Deploy cannot populate exact-dollar fields (all null in v1).
  - Deploy cannot use PriceBand/valuation context as decision authority.
  - Deploy cannot fabricate evidence, target allocations, cash, tax, or broker data.
  - Deploy output must clearly show scaffold/planning status only.

Intel v3 remains the only Buy/Hold/Trim/Sell authority.
"""
from __future__ import annotations

from .deploy_contracts import (
    DeployActionabilityStatus,
    DeployActionSource,
    DeployGuardrailSummary,
    DeployPlan,
    DeployPlanInput,
    DeployPlanItem,
    DeployPlanStatus,
)

# Intel actions that may become future trade candidates (not HOLD).
_ACTIONABLE_INTEL_ACTIONS = frozenset({"BUY", "TRIM", "SELL"})


def _classify_actionability(inp: DeployPlanInput) -> tuple[DeployActionabilityStatus, str | None]:
    """Classify a single DeployPlanInput's actionability status.

    Returns (status, suppression_reason | None).

    Priority order:
      1. HOLD → always NOT_ACTIONABLE_HOLD.
      2. Missing evidence → SUPPRESSED_MISSING_EVIDENCE.
      3. Stale snapshot → SUPPRESSED_STALE.
      4. Blocked fit → SUPPRESSED_BLOCKED.
      5. Weak evidence → SUPPRESSED_WEAK.
      6. Unknown action → SUPPRESSED_UNSAFE.
      7. Otherwise → ACTIONABLE_CANDIDATE (action preserved from Intel).

    PriceBand is never read here — it is not a Deploy decision authority.
    """
    intel_action = inp.intel_action.upper()

    # Rule 1: HOLD is never actionable in Deploy.
    if intel_action == "HOLD":
        return DeployActionabilityStatus.NOT_ACTIONABLE_HOLD, None

    # Rule 2: Missing evidence suppresses actionability.
    if inp.has_missing_evidence:
        return (
            DeployActionabilityStatus.SUPPRESSED_MISSING_EVIDENCE,
            "Intel evidence band is THIN — missing evidence suppresses Deploy actionability.",
        )

    # Rule 3: Stale snapshot suppresses actionability.
    if inp.has_stale_evidence:
        return (
            DeployActionabilityStatus.SUPPRESSED_STALE,
            "Intel snapshot is stale — stale evidence suppresses Deploy actionability.",
        )

    # Rule 4: Blocked portfolio fit suppresses actionability.
    if inp.is_blocked:
        return (
            DeployActionabilityStatus.SUPPRESSED_BLOCKED,
            "Portfolio fit blocked — blocked evidence suppresses Deploy actionability.",
        )

    # Rule 5: Weak evidence suppresses actionability.
    if inp.has_weak_evidence:
        return (
            DeployActionabilityStatus.SUPPRESSED_WEAK,
            "Weak evidence suppresses Deploy actionability.",
        )

    # Rule 6: Unrecognized action → unsafe.
    if intel_action not in _ACTIONABLE_INTEL_ACTIONS:
        return (
            DeployActionabilityStatus.SUPPRESSED_UNSAFE,
            f"Intel action '{intel_action}' is not a recognized actionable action.",
        )

    # Rule 7: Actionable candidate — Intel action direction is preserved.
    return DeployActionabilityStatus.ACTIONABLE_CANDIDATE, None


def _translate_item(inp: DeployPlanInput) -> DeployPlanItem:
    """Translate one DeployPlanInput into a DeployPlanItem scaffold.

    Guardrail: intel_action is preserved verbatim from the Intel card.
    Guardrail: all dollar/quantity fields remain null.
    Guardrail: price_context_label is not read for actionability decisions.
    """
    actionability, suppression_reason = _classify_actionability(inp)

    if actionability == DeployActionabilityStatus.NOT_ACTIONABLE_HOLD:
        item_plan_status = DeployPlanStatus.HOLD_ONLY
    elif actionability == DeployActionabilityStatus.ACTIONABLE_CANDIDATE:
        item_plan_status = DeployPlanStatus.SCAFFOLD
    else:
        item_plan_status = DeployPlanStatus.SUPPRESSED

    return DeployPlanItem(
        ticker=inp.ticker,
        intel_action=inp.intel_action,          # Read-only from Intel; not overridden.
        actionability_status=actionability,
        action_source=inp.action_source,
        intel_snapshot_id=inp.intel_snapshot_id,
        intel_run_id=inp.intel_run_id,
        plan_status=item_plan_status,
        # Exact-dollar placeholders — null in v1.
        recommended_dollar_amount=None,
        estimated_share_quantity=None,
        rounding_policy="not_applied_yet",
        cash_constraint_status="not_evaluated_yet",
        target_allocation_status="not_evaluated_yet",
        tax_guardrail_status="not_evaluated_yet",
        wash_sale_guardrail_status="not_evaluated_yet",
        suppression_reason=suppression_reason,
        schema_version="deploy_v1_scaffold",
    )


def build_deploy_plan(inputs: list[DeployPlanInput]) -> DeployPlan:
    """Build a complete Deploy plan scaffold from a list of DeployPlanInputs.

    Returns a DeployPlan with all items translated and a guardrail summary
    that records enforcement of the Deploy/Intel boundary invariants.

    This is a scaffold / planning artefact only — not an executable trade instruction.
    """
    if not inputs:
        return DeployPlan(
            snapshot_id="",
            run_id="",
            plan_status=DeployPlanStatus.SCAFFOLD,
            items=[],
            guardrail_summary=DeployGuardrailSummary(
                total_items=0,
                buy_candidates=0,
                trim_candidates=0,
                sell_candidates=0,
                hold_items=0,
                suppressed_items=0,
            ),
            schema_version="deploy_v1_scaffold",
        )

    # All inputs from the same snapshot share snapshot_id and run_id.
    snapshot_id = inputs[0].intel_snapshot_id
    run_id = inputs[0].intel_run_id

    items = [_translate_item(inp) for inp in inputs]

    # Guardrail counters.
    buy_candidates = sum(
        1 for item in items
        if item.actionability_status == DeployActionabilityStatus.ACTIONABLE_CANDIDATE
        and item.intel_action == "BUY"
    )
    trim_candidates = sum(
        1 for item in items
        if item.actionability_status == DeployActionabilityStatus.ACTIONABLE_CANDIDATE
        and item.intel_action == "TRIM"
    )
    sell_candidates = sum(
        1 for item in items
        if item.actionability_status == DeployActionabilityStatus.ACTIONABLE_CANDIDATE
        and item.intel_action == "SELL"
    )
    hold_items = sum(
        1 for item in items
        if item.actionability_status == DeployActionabilityStatus.NOT_ACTIONABLE_HOLD
    )
    suppressed_items = sum(
        1 for item in items
        if item.actionability_status not in {
            DeployActionabilityStatus.ACTIONABLE_CANDIDATE,
            DeployActionabilityStatus.NOT_ACTIONABLE_HOLD,
        }
    )

    # Guardrail invariant checks.
    hold_never_actionable = not any(
        item.intel_action == "HOLD"
        and item.actionability_status == DeployActionabilityStatus.ACTIONABLE_CANDIDATE
        for item in items
    )
    dollar_fields_null = all(
        item.recommended_dollar_amount is None
        and item.estimated_share_quantity is None
        for item in items
    )
    # PriceBand is never read in _classify_actionability — always True in v1.
    priceband_not_authority = True
    intel_action_preserved = all(
        item.intel_action == inp.intel_action
        for item, inp in zip(items, inputs)
    )

    guardrail = DeployGuardrailSummary(
        total_items=len(items),
        buy_candidates=buy_candidates,
        trim_candidates=trim_candidates,
        sell_candidates=sell_candidates,
        hold_items=hold_items,
        suppressed_items=suppressed_items,
        hold_never_actionable=hold_never_actionable,
        dollar_fields_null=dollar_fields_null,
        priceband_not_authority=priceband_not_authority,
        intel_action_preserved=intel_action_preserved,
        schema_version="deploy_v1_scaffold",
    )

    # Aggregate plan status.
    if any(item.plan_status == DeployPlanStatus.SUPPRESSED for item in items):
        plan_status = DeployPlanStatus.SUPPRESSED
    elif all(item.plan_status == DeployPlanStatus.HOLD_ONLY for item in items):
        plan_status = DeployPlanStatus.HOLD_ONLY
    else:
        plan_status = DeployPlanStatus.SCAFFOLD

    return DeployPlan(
        snapshot_id=snapshot_id,
        run_id=run_id,
        plan_status=plan_status,
        items=items,
        guardrail_summary=guardrail,
        schema_version="deploy_v1_scaffold",
    )
