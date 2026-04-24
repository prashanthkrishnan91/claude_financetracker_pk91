"""Adaptive Deployment — decides how much cash to deploy *now* vs. hold back.

Pure deterministic module. Consumes:
  * the existing ``allocation_engine`` output (ranked AllocationItems)
  * a ``RegimeOutput`` from ``regime_engine``
  * current Holdings + portfolio total

Emits:
  * an ``AdaptiveDecision`` with deploy_percentage, deployment_mode,
    recommended_deploy_amount, cash_reserve_amount, adaptive_reasons.
  * per-row ``StagedAllocation``s (immediate / reserve / staging instruction).

Staging respects the invariant: ``immediate + reserve == original_amount``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal, Optional

from .allocation_engine import (
    MAX_ETF_WEIGHT,
    MAX_SAME_THEME_WEIGHT,
    MAX_SINGLE_STOCK_WEIGHT,
    MAX_SPECULATIVE_WEIGHT,
    MIN_TICKER_ALLOCATION,
    ROUNDING_STEP,
    AllocationItem,
    Holding,
    _DEFAULT_THEME_MAP,
)
from .regime_engine import RegimeOutput

logger = logging.getLogger(__name__)


DeploymentMode = Literal["full", "partial", "defensive", "wait"]


@dataclass
class StagedAllocation:
    ticker: str
    original_amount: float
    immediate_amount: float
    reserve_amount: float
    staging_instruction: str
    execution_timing: str


@dataclass
class AdaptiveDecision:
    deploy_percentage: float                    # 0..100
    deployment_mode: DeploymentMode
    recommended_deploy_amount: float
    cash_reserve_amount: float
    adaptive_reasons: list[str]
    adjustments_applied: list[str]
    staged_allocations: list[StagedAllocation] = field(default_factory=list)


# Base deploy % by regime
_BASE_DEPLOY = {"bull": 100.0, "neutral": 70.0, "risk_off": 50.0}
# Risk-off cap — never above this even after upward adjustments
_RISK_OFF_CAP = 60.0
# Minimum deploy floor (unless mode = wait)
_DEPLOY_FLOOR = 25.0


def _category_cap(category: str) -> float:
    c = (category or "").lower()
    if c == "etf":
        return MAX_ETF_WEIGHT
    if c in {"crypto", "ipo"}:
        return MAX_SPECULATIVE_WEIGHT
    return MAX_SINGLE_STOCK_WEIGHT


def _theme_for(ticker: str, *, holding: Optional[Holding]) -> Optional[str]:
    if holding is not None and holding.theme:
        return holding.theme
    return _DEFAULT_THEME_MAP.get(ticker.upper())


def _round_5(amount: float) -> float:
    if amount <= 0:
        return 0.0
    return round(amount / ROUNDING_STEP) * ROUNDING_STEP


def _floor_5(amount: float) -> float:
    if amount <= 0:
        return 0.0
    import math
    return math.floor(amount / ROUNDING_STEP) * ROUNDING_STEP


def _theme_concentration(
    allocations: list[AllocationItem],
    *,
    holdings_by_ticker: dict[str, Holding],
) -> tuple[Optional[str], float, int]:
    """Largest *same-theme* cluster of the selected allocations.

    Returns (top_theme, share_of_plan_$, member_count). A single-member top
    theme is not a real concentration signal — caller should gate on count >= 2.
    """
    if not allocations:
        return None, 0.0, 0
    total = sum(max(0.0, a.amount) for a in allocations) or 1.0
    by_theme: dict[str, float] = {}
    counts: dict[str, int] = {}
    for a in allocations:
        theme = _theme_for(a.ticker, holding=holdings_by_ticker.get(a.ticker.upper()))
        if not theme:
            continue
        by_theme[theme] = by_theme.get(theme, 0.0) + max(0.0, a.amount)
        counts[theme] = counts.get(theme, 0) + 1
    if not by_theme:
        return None, 0.0, 0
    top = max(by_theme.items(), key=lambda kv: kv[1])
    return top[0], top[1] / total, counts.get(top[0], 0)


def _top_two_share(allocations: list[AllocationItem]) -> float:
    if len(allocations) < 2:
        return 0.0
    total = sum(max(0.0, a.amount) for a in allocations) or 1.0
    sorted_amts = sorted((a.amount for a in allocations), reverse=True)
    return (sorted_amts[0] + sorted_amts[1]) / total


def _classify_mode(
    deploy_pct: float,
    regime: RegimeOutput,
    *,
    cash_to_deploy: float,
) -> DeploymentMode:
    if cash_to_deploy <= 0:
        return "wait"
    if deploy_pct <= 0.5:
        return "wait"
    if deploy_pct >= 90.0:
        return "full"
    if regime.regime_label == "risk_off" or deploy_pct < 55.0:
        return "defensive"
    return "partial"


def _deferral_candidates(
    allocations: list[AllocationItem],
    *,
    holdings_by_ticker: dict[str, Holding],
) -> set[str]:
    """Tickers that are already at/near their cap and should be deferred."""
    deferred: set[str] = set()
    if not allocations:
        return deferred
    plan_total = sum(max(0.0, a.amount) for a in allocations) or 1.0
    for a in allocations:
        cat = a.category or "Core"
        cap = _category_cap(cat)
        # If current weight is already ≥80% of cap AND this row is a
        # meaningful share of the plan, defer adding more right now.
        if a.current_weight >= cap * 0.8 and (a.amount / plan_total) >= 0.20:
            deferred.add(a.ticker.upper())
    return deferred


def _stage_row(
    a: AllocationItem,
    *,
    deploy_pct: float,
    regime_label: str,
    deferred: bool,
) -> StagedAllocation:
    """Split a row into immediate vs. reserve and pick a staging instruction.

    The split obeys the invariant ``immediate + reserve == original_amount``
    so plan-level recommended/reserve sums cleanly equal the row sums.
    """
    original = max(0.0, float(a.amount or 0.0))
    if original <= 0:
        return StagedAllocation(
            ticker=a.ticker,
            original_amount=0.0,
            immediate_amount=0.0,
            reserve_amount=0.0,
            staging_instruction="No allocation this run.",
            execution_timing="skip",
        )

    if deferred:
        return StagedAllocation(
            ticker=a.ticker,
            original_amount=round(original, 2),
            immediate_amount=0.0,
            reserve_amount=round(original, 2),
            staging_instruction="Defer — already heavy; add only on pullback.",
            execution_timing="wait_for_pullback",
        )

    cat = (a.category or "").lower()
    is_etf = cat == "etf"
    is_speculative = cat in {"crypto", "ipo"}

    # Plan-level deploy_pct is the canonical immediate share. Category nudges
    # tilt within ±15pts: ETFs stay closer to fully deployed; speculative names
    # are always staged. Deploy_pct already encodes regime + concentration.
    share = max(0.0, min(1.0, deploy_pct / 100.0))
    if is_etf and regime_label != "risk_off":
        share = max(share, 0.85)
    if is_speculative:
        share = min(share, 0.50)

    immediate = _round_5(original * share)
    if immediate >= original:
        immediate = round(original, 2)
    reserve = max(0.0, round(original - immediate, 2))

    # Squelch dust: if immediate is positive but below the engine's $25 floor,
    # drop the tranche entirely so the user doesn't trade $5–$20 lots.
    if 0 < immediate < MIN_TICKER_ALLOCATION:
        immediate = 0.0
        reserve = round(original, 2)

    if immediate <= 0:
        instruction = "Defer first tranche; wait for pullback."
        timing = "wait_for_pullback"
    elif reserve <= 0:
        if regime_label == "bull":
            instruction = "Deploy now as primary core allocation."
        else:
            instruction = "Deploy now."
        timing = "now"
    else:
        if is_speculative:
            instruction = "Stage entry over 2–3 buys."
            timing = "stage_2_3"
        elif regime_label == "risk_off":
            instruction = "Buy half now; reserve rest for further weakness."
            timing = "stage_2_entries"
        else:
            instruction = "Buy first tranche now; reserve rest for pullback."
            timing = "two_tranche"

    return StagedAllocation(
        ticker=a.ticker,
        original_amount=round(original, 2),
        immediate_amount=round(immediate, 2),
        reserve_amount=round(reserve, 2),
        staging_instruction=instruction,
        execution_timing=timing,
    )


def adapt_allocation_plan(
    *,
    cash_to_deploy: float,
    allocations: list[AllocationItem],
    regime: RegimeOutput,
    holdings: Optional[list[Holding]] = None,
    portfolio_total: float = 0.0,
    deployment_risks: Optional[list[str]] = None,
) -> AdaptiveDecision:
    """Compute deploy %, mode, per-row staging, and human reasons.

    Pure function — no IO. Safe to call with empty allocations (returns a
    ``wait`` decision with zero deploy).
    """
    cash = max(0.0, float(cash_to_deploy or 0.0))
    holdings = holdings or []
    holdings_by_ticker = {h.ticker.upper(): h for h in holdings if h.ticker}

    label = regime.regime_label if regime else "neutral"
    base = _BASE_DEPLOY.get(label, 70.0)
    deploy_pct = base
    adjustments: list[str] = []

    # Concentration adjustments use only the *selected* allocations (post-engine).
    top_theme, top_theme_share, top_theme_count = _theme_concentration(
        allocations, holdings_by_ticker=holdings_by_ticker,
    )
    # Only flag same-theme concentration when 2+ rows share that theme — a
    # single-row top theme just reflects normal portfolio diversity.
    if top_theme and top_theme_count >= 2 and top_theme_share > 0.40:
        deploy_pct -= 15.0
        adjustments.append(
            f"-15pts: {top_theme} concentration {top_theme_share*100:.0f}% of plan (>40%)"
        )

    # Top-2 dominance is only meaningful with 3+ rows; with 2 rows top-2 is
    # always 100% by definition. Threshold at 65% to avoid false positives
    # from small but evenly distributed plans (e.g. 3 × $300 → top-2 ≈ 67%).
    top_two = _top_two_share(allocations)
    if len(allocations) >= 3 and top_two > 0.70:
        deploy_pct -= 10.0
        adjustments.append(f"-10pts: top-2 dominance {top_two*100:.0f}%")

    deferred_set = _deferral_candidates(
        allocations, holdings_by_ticker=holdings_by_ticker,
    )
    if deferred_set:
        deploy_pct -= 5.0 * len(deferred_set)
        adjustments.append(
            f"-{5*len(deferred_set)}pts: deferred {', '.join(sorted(deferred_set))} "
            f"(already at/near cap)"
        )

    # Data-quality nudge: low quality → trim 5pts and keep mode at most partial.
    if regime and regime.data_quality == "low":
        deploy_pct -= 5.0
        adjustments.append("-5pts: low market-data quality")

    # Caps + floors
    if label == "risk_off":
        deploy_pct = min(deploy_pct, _RISK_OFF_CAP)
    deploy_pct = max(0.0, min(100.0, deploy_pct))

    mode = _classify_mode(deploy_pct, regime, cash_to_deploy=cash)
    if mode != "wait":
        deploy_pct = max(_DEPLOY_FLOOR, deploy_pct)
        if label == "risk_off":
            deploy_pct = min(deploy_pct, _RISK_OFF_CAP)
        # Reclassify after floor/cap clamp.
        mode = _classify_mode(deploy_pct, regime, cash_to_deploy=cash)

    recommended = _round_5(cash * deploy_pct / 100.0) if cash > 0 else 0.0
    if recommended > cash:
        recommended = _floor_5(cash)
    reserve = max(0.0, round(cash - recommended, 2))

    # Stage each row at its ORIGINAL amount. immediate is a fraction of the
    # original row $ driven by plan-level deploy_pct + per-ticker overrides.
    # Sum invariant: per-row immediate + reserve == original.
    staged: list[StagedAllocation] = []
    for a in allocations:
        deferred = a.ticker.upper() in deferred_set
        staged.append(_stage_row(
            a, deploy_pct=deploy_pct, regime_label=label, deferred=deferred,
        ))

    # Plan-level totals are the sums of per-row immediates / reserves.
    total_immediate = round(sum(s.immediate_amount for s in staged), 2)
    total_reserve = round(sum(s.reserve_amount for s in staged), 2)
    plan_total = sum(s.original_amount for s in staged)
    # cash_to_deploy may exceed plan_total when the engine couldn't allocate
    # all cash (insufficient candidates). Anything above plan_total is also reserve.
    extra_reserve = max(0.0, round(cash - plan_total, 2))
    recommended = total_immediate
    reserve = round(total_reserve + extra_reserve, 2)
    if cash > 0:
        deploy_pct = round((recommended / cash) * 100.0, 1)
    else:
        deploy_pct = 0.0

    # Build adaptive_reasons (≤3 short sentences).
    reasons: list[str] = []
    if regime and regime.regime_reasons:
        reasons.append(
            f"Market regime: {label} ({regime.regime_score:.0f}/100). "
            f"{regime.regime_reasons[0]}."
        )
    else:
        reasons.append(f"Market regime: {label}.")
    if cash > 0:
        reasons.append(
            f"Deploying {deploy_pct:.0f}% (${recommended:,.0f}) now and "
            f"reserving {100-deploy_pct:.0f}% (${reserve:,.0f}) for pullbacks."
        )
    if deferred_set:
        reasons.append(
            f"{', '.join(sorted(deferred_set))} deferred — current portfolio "
            f"weight is already heavy."
        )
    elif top_theme and top_theme_count >= 2 and top_theme_share > 0.40:
        reasons.append(
            f"{top_theme} concentration is {top_theme_share*100:.0f}% of the "
            f"plan; staging to manage risk."
        )

    return AdaptiveDecision(
        deploy_percentage=round(deploy_pct, 1),
        deployment_mode=mode,
        recommended_deploy_amount=round(recommended, 2),
        cash_reserve_amount=round(reserve, 2),
        adaptive_reasons=reasons[:3],
        adjustments_applied=adjustments,
        staged_allocations=staged,
    )
