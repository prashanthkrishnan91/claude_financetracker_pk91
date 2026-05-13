"""Deploy Stage 2.6D — New-cash sleeve sizing v1.

Pure function. No IO, no LLM, no DB, no broker.

Answers the user's question "I have $X of new cash — which 3-5 Intel v3 BUY
opportunities should I buy today?" Replaces current-gap math
(target_weight * portfolio - current_value) for BUY items when
cash_to_deploy > 0. Current-gap math leaves cash idle when current weights
already match target weights, producing tiny BUY deltas that are then
suppressed by the minimum trade threshold.

Policy:
  - Eligible universe: ACTIONABLE_CANDIDATE BUY items only. HOLD/TRIM/SELL
    never receive new-cash BUY dollars. Weak/missing/stale/blocked candidates
    are already suppressed upstream by the translation layer and therefore
    never reach this ranker.
  - Selection: deterministic ranking on Intel conviction (HIGH > MEDIUM > LOW)
    then evidence band (STRONG > OK/PARTIAL > THIN), with ticker A→Z as a
    stable tie-breaker. Capped at MAX_RECOMMENDATIONS. When fewer than
    MAX_RECOMMENDATIONS eligible BUYs exist, return only those — no
    fabrication. Each selected item gets a plain-English selection_reason
    derived from its existing Intel labels (no invented confidence).
  - Allocation: distribute cash_to_deploy across selected items using saved
    target_weight as a relative-weight guardrail. Fall back to equal weight
    when target_weight is missing or sums to zero. Saved target allocation is
    a guardrail/context, not the sole source of new-cash dollars.
  - Budget safety: floor-round so total deployed never exceeds cash_to_deploy.
  - Threshold safety: drop selections below minimum_trade_usd. Surface
    residual_cash and plain-English residual_reason so the UI/API can explain
    any idle planning capital.

Determinism: same inputs produce the same plan. No randomization, no LLM.
"""
from __future__ import annotations

import math
from dataclasses import replace
from typing import Dict, List, Optional, Tuple

from .deploy_contracts import DeployActionabilityStatus, DeployPlanItem
from .deploy_sizing_contracts import DeploySizingInputBundle

# Maximum new-cash BUY recommendations surfaced in one plan. Mirrors the
# frontend Step 2 cap (MAX_AMOUNT_AWARE_BUY_ITEMS) so the backend never
# produces more than the UI can display.
MAX_RECOMMENDATIONS = 5

_BUY_ACTION = "BUY"

# Deterministic ranking keys for eligible BUY candidates. Higher number = stronger
# signal. Unknown labels collapse to the weakest bucket so missing data never
# wins over present data. These read Intel's existing conviction and evidence
# labels — Deploy never invents new evidence.
_CONVICTION_RANK = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}
_EVIDENCE_RANK = {"STRONG": 3, "OK": 2, "PARTIAL": 2, "THIN": 1}


def _rank_key(item: DeployPlanItem) -> tuple:
    """Sort key for eligible BUY candidates.

    Order: higher conviction first, then stronger evidence, then ticker A→Z as a
    stable deterministic tie-breaker. Ticker tie-break guarantees identical
    inputs in any order produce the same top-N.
    """
    conviction = _CONVICTION_RANK.get(item.intel_conviction.upper(), 0)
    evidence = _EVIDENCE_RANK.get(item.intel_evidence_band.upper(), 0)
    return (-conviction, -evidence, item.ticker)


def _selection_reason(item: DeployPlanItem) -> str:
    """Plain-English explanation of why this BUY made the top-N cut.

    Uses Intel's conviction and evidence labels verbatim, translated to plain
    English. No invented confidence. When both labels are missing or unknown,
    returns a candid "no evidence detail available" string.
    """
    conviction = item.intel_conviction.upper()
    evidence = item.intel_evidence_band.upper()
    conv_phrase = {
        "HIGH": "high-conviction BUY",
        "MEDIUM": "medium-conviction BUY",
        "LOW": "low-conviction BUY",
    }.get(conviction)
    ev_phrase = {
        "STRONG": "strong evidence",
        "OK": "supported evidence",
        "PARTIAL": "partial evidence",
        "THIN": "thin evidence",
    }.get(evidence)
    if conv_phrase and ev_phrase:
        return f"{conv_phrase} with {ev_phrase} from Intel v3."
    if conv_phrase:
        return f"{conv_phrase} from Intel v3."
    if ev_phrase:
        return f"BUY with {ev_phrase} from Intel v3."
    return "Selected from Intel v3 BUY candidates (no evidence detail available)."


def apply_new_cash_sleeve_sizing(
    bundle: DeploySizingInputBundle,
    items: List[DeployPlanItem],
    cash_to_deploy: float,
    price_per_share_map: Optional[Dict[str, float]] = None,
    max_recommendations: int = MAX_RECOMMENDATIONS,
) -> Tuple[List[DeployPlanItem], float, Optional[str]]:
    """Allocate cash_to_deploy across eligible BUY ACTIONABLE_CANDIDATE items.

    Returns (new_items, residual_cash, residual_reason).

    BUY ACTIONABLE_CANDIDATE items receive new-cash dollars. Non-BUY items and
    non-ACTIONABLE items are returned unchanged. Total deployed dollars never
    exceed cash_to_deploy after floor rounding.

    All BUY ACTIONABLE_CANDIDATE items are reset before allocation, so any
    upstream current-gap BUY amounts are cleared. Selected items get the
    sleeve amount; non-selected eligible BUYs end with no dollar amount.
    """
    if price_per_share_map is None:
        price_per_share_map = {}

    new_items = list(items)
    if cash_to_deploy <= 0:
        return new_items, 0.0, None

    policy = bundle.policy
    minimum_trade_usd = (
        policy.minimum_trade_usd
        if policy is not None and policy.minimum_trade_usd is not None
        else 0.0
    )
    rounding_policy = (
        policy.rounding_policy if policy is not None else "WHOLE_DOLLAR"
    )

    eligible_indices = [
        i for i, it in enumerate(items)
        if it.intel_action.upper() == _BUY_ACTION
        and it.actionability_status == DeployActionabilityStatus.ACTIONABLE_CANDIDATE
    ]
    eligible_count = len(eligible_indices)

    if eligible_count == 0:
        return new_items, cash_to_deploy, "No eligible BUY candidates from Intel v3."

    # Reset every eligible BUY so any upstream current-gap amounts are cleared.
    # Non-selected eligible BUYs end with no recommended dollar amount.
    for idx in eligible_indices:
        new_items[idx] = replace(
            items[idx],
            recommended_dollar_amount=None,
            estimated_share_quantity=None,
            target_allocation_status="not_evaluated_yet",
            cash_constraint_status="not_evaluated_yet",
        )

    # Rank eligible BUYs by Intel conviction + evidence band so the top-N is
    # explainable rather than dependent on snapshot card order. Python's sort is
    # stable; the ticker tie-breaker inside _rank_key keeps identical-signal ties
    # deterministic across re-orderings of the input list.
    ranked_indices = sorted(eligible_indices, key=lambda i: _rank_key(items[i]))
    selected_indices = ranked_indices[:max_recommendations]
    selected_count = len(selected_indices)

    # Relative weights using saved target allocation as a guardrail. Fall back
    # to equal weight when any selected ticker lacks a usable target weight.
    raw_weights: List[Optional[float]] = []
    for idx in selected_indices:
        ta = bundle.target_allocation_for(items[idx].ticker)
        if (
            ta is not None
            and ta.is_ready_for_math
            and ta.target_weight is not None
            and ta.target_weight > 0
        ):
            raw_weights.append(float(ta.target_weight))
        else:
            raw_weights.append(None)

    total_known = sum(w for w in raw_weights if w is not None)
    if all(w is not None for w in raw_weights) and total_known > 0:
        weights = [w / total_known for w in raw_weights]  # type: ignore[misc]
    else:
        weights = [1.0 / selected_count] * selected_count

    # Floor-round each allocation to guarantee total <= cash_to_deploy.
    allocations = [float(math.floor(cash_to_deploy * w)) for w in weights]

    # Drop allocations below the minimum trade threshold.
    suppressed_below_min = 0
    for k in range(selected_count):
        amount = allocations[k]
        if amount <= 0 or (minimum_trade_usd > 0 and amount < minimum_trade_usd):
            allocations[k] = 0.0
            suppressed_below_min += 1

    # Distribute leftover whole dollars from floor rounding to selected BUY rows
    # in deterministic top-ranked-first order. Skips suppressed/zero slots so a
    # below-min-trade row never gets resurrected by residual distribution.
    # Never adds beyond floor(cash_to_deploy) so total <= cash_to_deploy holds.
    nonzero_slots = [k for k in range(selected_count) if allocations[k] > 0]
    if nonzero_slots:
        current_total = sum(allocations)
        leftover = int(math.floor(max(cash_to_deploy - current_total, 0.0)))
        i = 0
        while leftover > 0:
            allocations[nonzero_slots[i % len(nonzero_slots)]] += 1.0
            leftover -= 1
            i += 1

    total_allocated = 0.0
    for k, idx in enumerate(selected_indices):
        amount = allocations[k]
        if amount <= 0:
            continue
        it = new_items[idx]
        price = price_per_share_map.get(it.ticker)
        shares = (amount / price) if (price is not None and price > 0) else None
        new_items[idx] = replace(
            it,
            recommended_dollar_amount=amount,
            estimated_share_quantity=shares,
            rounding_policy=rounding_policy,
            target_allocation_status="evaluated",
            cash_constraint_status="not_evaluated_yet",
            selection_reason=_selection_reason(it),
        )
        total_allocated += amount

    residual = max(cash_to_deploy - total_allocated, 0.0)
    residual_reason = _residual_reason(
        residual=residual,
        eligible_count=eligible_count,
        selected_count=selected_count,
        suppressed_below_min=suppressed_below_min,
        total_allocated=total_allocated,
        minimum_trade_usd=minimum_trade_usd,
    )
    return new_items, residual, residual_reason


def _residual_reason(
    residual: float,
    eligible_count: int,
    selected_count: int,
    suppressed_below_min: int,
    total_allocated: float,
    minimum_trade_usd: float,
) -> Optional[str]:
    """Plain-English explanation for any idle planning capital."""
    if residual <= 0:
        return None
    if total_allocated == 0:
        if suppressed_below_min > 0:
            return (
                f"All sleeve allocations fell below the ${minimum_trade_usd:.0f} "
                "minimum trade threshold; no BUYs sized."
            )
        return "No BUY recommendations sized; remaining planning capital held back."
    if eligible_count < 3:
        return (
            f"Only {eligible_count} eligible BUY candidate(s) from Intel v3; "
            "remaining planning capital held back."
        )
    if suppressed_below_min > 0:
        return (
            f"{suppressed_below_min} sleeve allocation(s) fell below the "
            f"${minimum_trade_usd:.0f} minimum trade threshold and were dropped; "
            "remaining planning capital held back."
        )
    return "Rounding holdback to keep total within planning capital."
