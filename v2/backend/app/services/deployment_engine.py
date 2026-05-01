"""Deploy Logic v2 — deterministic deployment-mode classifier.

Emits a ``DeploymentDecision`` with:
  * deployment_mode ∈ {full_deploy, staged_deploy, defensive_reserve, skip_or_wait}
  * deploy_now_amount, reserve_amount, deployment_confidence
  * reserve_trigger (required and validated when reserve_amount > 0)
  * per_ticker_allocations, risks, data_quality

Pure deterministic function — no IO, no LLM calls.
Backward-compatible: old decision-log snapshots without these fields degrade gracefully.

Scoring formula (all values in points):
  deployment_score = BASE (70)
    + structural_bonus      (0..15)   plan addresses real allocation gaps
    + quality_bonus         (0..10)   conviction/confidence of allocations
    + cash_drag_bonus       (0..20)   penalises idle reserve without trigger
    - concentration_penalty (0..20)   theme/sector concentration risk
    - regime_penalty        (0..25)   market regime risk
    - data_quality_penalty  (0..15)   poor data quality

Mode thresholds:
  full_deploy       >= 70
  staged_deploy     >= 50
  defensive_reserve >= 30
  skip_or_wait      <  30
"""

from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass, field
from typing import Literal, Optional

from .allocation_engine import (
    AllocationItem,
    Holding,
    MAX_SINGLE_STOCK_WEIGHT,
    _DEFAULT_THEME_MAP,
)
from .adaptive_deployment import _theme_concentration
from .regime_engine import RegimeOutput

logger = logging.getLogger(__name__)


# ── Type aliases ──────────────────────────────────────────────────────────────

DeploymentModeV2 = Literal[
    "full_deploy",
    "staged_deploy",
    "defensive_reserve",
    "skip_or_wait",
]

TickerRole = Literal["Primary", "Supporting", "Watch"]


# ── Scoring constants ─────────────────────────────────────────────────────────

BASE_DEPLOYMENT_SCORE = 70.0

STRUCTURAL_BONUS_MAX = 15.0
QUALITY_BONUS_MAX = 10.0
CASH_DRAG_BONUS_MAX = 20.0

CONCENTRATION_PENALTY_MAX = 20.0
REGIME_PENALTY_MAX = 25.0
DATA_QUALITY_PENALTY_MAX = 15.0

FULL_DEPLOY_SCORE = 70.0
STAGED_DEPLOY_SCORE = 50.0
DEFENSIVE_RESERVE_SCORE = 30.0

# Reserve below this amount needs no trigger
MIN_RESERVE_FOR_TRIGGER = 25.0

# Watch-tier tickers cannot absorb more than this share of total plan
WATCH_TICKER_MAX_PLAN_SHARE = 0.25

# Near-cap threshold: >= this % current weight triggers pullback trigger
NEAR_CAP_WEIGHT_THRESHOLD = MAX_SINGLE_STOCK_WEIGHT * 0.70  # 14% default

_REGIME_PENALTY: dict[str, float] = {
    "bull": 0.0,
    "neutral": 10.0,
    "risk_off": 25.0,
}

_DATA_QUALITY_CONFIDENCE: dict[str, float] = {
    "high": 1.0,
    "medium": 0.80,
    "low": 0.55,
}

_TRIGGER_TECHNICAL = "technical_pullback"
_TRIGGER_WATCH = "watch_tier_breakout"
_TRIGGER_EVENT = "event_driven"
_TRIGGER_CONCENTRATION = "concentration_reduction"


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class ReserveTrigger:
    """Specific, actionable trigger required when reserve_amount > 0."""
    reserve_reason: str
    reserve_target_tickers: list[str]
    reserve_purpose: str
    trigger_type: str
    trigger_condition: str
    suggested_review_event: Optional[str]
    suggested_review_date: Optional[str]
    when_to_deploy_reserve: str          # user-facing sentence


@dataclass
class PerTickerDeployment:
    ticker: str
    role: TickerRole
    amount: float
    deploy_now: float
    reserve: float
    conviction_level: str
    rationale: str
    capped: bool = False
    cap_reason: Optional[str] = None


@dataclass
class DeploymentDecision:
    total_deposit: float
    deploy_now_amount: float
    reserve_amount: float
    deployment_mode: DeploymentModeV2
    deployment_confidence: float          # 0..1
    deployment_reason: str
    cash_drag_penalty_applied: bool
    reserve_reason: Optional[str]
    reserve_trigger: Optional[ReserveTrigger]
    per_ticker_allocations: list[PerTickerDeployment]
    risks: list[str]
    data_quality: str
    evaluation_notes_for_future_decision_log: list[str]
    deployment_score: float               # raw score for transparency
    adjustments_applied: list[str]        # audit trail


# ── Role classifier ───────────────────────────────────────────────────────────

def _ticker_role(conviction_level: Optional[str], score: float) -> TickerRole:
    """Derive ticker role from conviction level and composite score."""
    conv = (conviction_level or "").upper()
    if conv == "HIGH" and score >= 4.0:
        return "Primary"
    if conv in {"HIGH", "MEDIUM"}:
        return "Supporting"
    return "Watch"


# ── Score components ──────────────────────────────────────────────────────────

def _structural_bonus(allocations: list[AllocationItem]) -> tuple[float, str]:
    """Bonus when plan targets real allocation gaps (current < 80% of target)."""
    if not allocations:
        return 0.0, ""
    below = sum(
        1 for a in allocations
        if a.target_weight > 0 and a.current_weight < a.target_weight * 0.80
    )
    ratio = below / len(allocations)
    bonus = round(STRUCTURAL_BONUS_MAX * ratio, 2)
    note = f"+{bonus:.1f} structural: {below}/{len(allocations)} tickers below target"
    return bonus, note


def _quality_bonus(allocations: list[AllocationItem]) -> tuple[float, str]:
    """Bonus weighted by dollar amount × mean(conviction_score, confidence)."""
    if not allocations:
        return 0.0, ""
    total_amt = sum(max(0.0, a.amount or 0.0) for a in allocations) or 1.0
    weighted_q = 0.0
    for a in allocations:
        amt = max(0.0, a.amount or 0.0)
        q = ((a.conviction_score or 0.0) + (a.confidence or 0.0)) / 2.0
        weighted_q += q * amt
    avg_q = weighted_q / total_amt
    bonus = round(QUALITY_BONUS_MAX * avg_q, 2)
    return bonus, f"+{bonus:.1f} quality: avg signal quality {avg_q:.2f}"


def _cash_drag_bonus(
    *,
    prelim_reserve: float,
    total_cash: float,
    has_strong_trigger: bool,
) -> tuple[float, bool]:
    """Cash drag bonus: idle reserve without a trigger is low-value; promotes deployment.

    Returns (bonus_points, was_applied).
    """
    if total_cash <= 0 or prelim_reserve <= MIN_RESERVE_FOR_TRIGGER:
        return 0.0, False  # no unallocated idle cash — no drag concern
    if has_strong_trigger:
        return 0.0, False  # legitimate reserve, no pressure to force full deployment
    reserve_ratio = prelim_reserve / total_cash
    if reserve_ratio >= 0.50:
        return CASH_DRAG_BONUS_MAX, True
    elif reserve_ratio >= 0.30:
        return 15.0, True
    elif reserve_ratio >= 0.15:
        return 8.0, True
    else:
        return 3.0, True


def _concentration_penalty(
    allocations: list[AllocationItem],
    *,
    holdings_by_ticker: dict[str, Holding],
) -> tuple[float, Optional[str]]:
    """Penalty when same-theme tickers dominate the plan."""
    top_theme, top_share, top_count = _theme_concentration(
        allocations, holdings_by_ticker=holdings_by_ticker
    )
    if not top_theme or top_count < 2:
        return 0.0, None
    if top_share > 0.60:
        p = CONCENTRATION_PENALTY_MAX
        return p, f"-{p:.0f} concentration: {top_theme} {top_share*100:.0f}% of plan (>60%)"
    if top_share > 0.40:
        p = round(CONCENTRATION_PENALTY_MAX * 0.55, 1)
        return p, f"-{p:.0f} concentration: {top_theme} {top_share*100:.0f}% of plan (>40%)"
    return 0.0, None


def _regime_penalty(regime: RegimeOutput) -> tuple[float, str]:
    label = regime.regime_label if regime else "neutral"
    p = _REGIME_PENALTY.get(label, 10.0)
    return p, f"-{p:.0f} regime: {label} (score {regime.regime_score:.0f})"


def _data_quality_penalty(regime: RegimeOutput) -> tuple[float, str]:
    quality = (regime.data_quality if regime else "medium") or "medium"
    if quality == "high":
        return 0.0, ""
    if quality == "medium":
        return 3.0, "-3 data_quality: medium"
    return DATA_QUALITY_PENALTY_MAX, f"-{DATA_QUALITY_PENALTY_MAX:.0f} data_quality: low"


# ── Mode classifier ───────────────────────────────────────────────────────────

def _classify_mode(score: float) -> DeploymentModeV2:
    if score >= FULL_DEPLOY_SCORE:
        return "full_deploy"
    if score >= STAGED_DEPLOY_SCORE:
        return "staged_deploy"
    if score >= DEFENSIVE_RESERVE_SCORE:
        return "defensive_reserve"
    return "skip_or_wait"


# ── Reserve trigger generation ────────────────────────────────────────────────

def _generate_reserve_trigger(
    *,
    allocations: list[AllocationItem],
    regime: RegimeOutput,
    holdings_by_ticker: dict[str, Holding],
) -> Optional[ReserveTrigger]:
    """Generate a specific, actionable reserve trigger or return None.

    Priority:
    1. Near-cap tickers → technical pullback trigger
    2. Watch-tier tickers in plan → breakout / conviction trigger
    3. Risk-off regime → specific market condition trigger
    4. Concentration → staged entry trigger

    Returns None when no specific (non-generic) trigger can be constructed.
    """
    # 1. Tickers already near weight cap
    near_cap = [
        a for a in allocations
        if a.current_weight >= NEAR_CAP_WEIGHT_THRESHOLD
    ]
    if near_cap:
        targets = sorted({a.ticker.upper() for a in near_cap})
        target_str = "/".join(targets)
        return ReserveTrigger(
            reserve_reason=(
                f"{target_str} position(s) are near the allocation cap "
                f"({NEAR_CAP_WEIGHT_THRESHOLD:.0f}% weight threshold); "
                "reserve held for pullback add."
            ),
            reserve_target_tickers=targets,
            reserve_purpose="add to position on pullback",
            trigger_type=_TRIGGER_TECHNICAL,
            trigger_condition=f"{target_str} pulls back 5–10% from current price",
            suggested_review_event="5–10% pullback in position price",
            suggested_review_date=None,
            when_to_deploy_reserve=(
                f"Deploy reserve when {target_str} pulls back 5–10% from entry price."
            ),
        )

    # 2. Watch-tier tickers in the allocation plan
    watch = [
        a for a in allocations
        if _ticker_role(a.conviction_level, a.score) == "Watch"
    ]
    if watch:
        targets = sorted({a.ticker.upper() for a in watch})
        target_str = "/".join(targets)
        return ReserveTrigger(
            reserve_reason=(
                f"{target_str} conviction is low (Watch tier); "
                "reserve held pending conviction improvement or breakout."
            ),
            reserve_target_tickers=targets,
            reserve_purpose="add to position on conviction upgrade or price breakout",
            trigger_type=_TRIGGER_WATCH,
            trigger_condition=(
                f"{target_str} receives a HIGH conviction rating or breaks "
                "above key resistance"
            ),
            suggested_review_event="analyst upgrade to HIGH conviction or price breakout",
            suggested_review_date=None,
            when_to_deploy_reserve=(
                f"Deploy reserve when {target_str} achieves HIGH conviction "
                "or confirms a clear breakout."
            ),
        )

    # 3. Risk-off regime with specific re-entry condition
    if regime and regime.regime_label == "risk_off":
        targets = [a.ticker.upper() for a in allocations[:3]]
        return ReserveTrigger(
            reserve_reason=(
                "Market is in risk-off regime; reserve held until regime improves."
            ),
            reserve_target_tickers=targets,
            reserve_purpose="add to plan positions when regime normalises",
            trigger_type=_TRIGGER_EVENT,
            trigger_condition="SPY regime transitions to neutral (regime score > 50)",
            suggested_review_event="regime transition to neutral or bull",
            suggested_review_date=None,
            when_to_deploy_reserve=(
                "Deploy reserve when market regime shifts to neutral or bull "
                "(SPY regime score > 50)."
            ),
        )

    # 4. Theme concentration → staged entry
    top_theme, top_share, top_count = _theme_concentration(
        allocations, holdings_by_ticker=holdings_by_ticker
    )
    if top_theme and top_count >= 2 and top_share > 0.40:
        targets = [
            a.ticker.upper() for a in allocations
            if _DEFAULT_THEME_MAP.get(a.ticker.upper()) == top_theme
        ][:3]
        target_str = "/".join(targets) if targets else top_theme
        return ReserveTrigger(
            reserve_reason=(
                f"{top_theme} concentration at {top_share*100:.0f}% of plan; "
                "staging entry to reduce timing risk."
            ),
            reserve_target_tickers=targets,
            reserve_purpose="2nd-tranche entry into concentrated theme",
            trigger_type=_TRIGGER_CONCENTRATION,
            trigger_condition=(
                f"Positive price action in {target_str} 2–4 weeks after initial entry"
            ),
            suggested_review_event="30-day price confirmation after initial entry",
            suggested_review_date=None,
            when_to_deploy_reserve=(
                f"Deploy reserve in {top_theme} tickers 2–4 weeks after initial "
                "entry if price action is positive."
            ),
        )

    return None


# ── WATCH-tier cap ────────────────────────────────────────────────────────────

def _apply_watch_cap(
    allocations: list[AllocationItem],
    total_plan: float,
) -> tuple[list[AllocationItem], list[str]]:
    """Cap Watch-tier tickers at WATCH_TICKER_MAX_PLAN_SHARE of total plan.

    Returns (capped_allocations, cap_notes).
    """
    if total_plan <= 0:
        return list(allocations), []
    notes: list[str] = []
    result: list[AllocationItem] = []
    for a in allocations:
        role = _ticker_role(a.conviction_level, a.score)
        if role != "Watch":
            result.append(a)
            continue
        share = (a.amount or 0.0) / total_plan
        if share > WATCH_TICKER_MAX_PLAN_SHARE:
            cap_amt = round(total_plan * WATCH_TICKER_MAX_PLAN_SHARE, 2)
            notes.append(
                f"{a.ticker}: Watch-tier capped at "
                f"{WATCH_TICKER_MAX_PLAN_SHARE*100:.0f}% of plan "
                f"(${cap_amt:.0f} vs ${a.amount:.0f})"
            )
            result.append(dataclasses.replace(a, amount=cap_amt))
        else:
            result.append(a)
    return result, notes


# ── Main function ─────────────────────────────────────────────────────────────

def classify_deployment(
    *,
    cash_to_deploy: float,
    allocations: list[AllocationItem],
    regime: RegimeOutput,
    holdings: Optional[list[Holding]] = None,
    portfolio_total: float = 0.0,
) -> DeploymentDecision:
    """Deterministic deployment mode classifier for Deploy Logic v2.

    Pure function — no IO, no LLM calls. Safe to call with empty plans.
    All old decision-log fields that lack deployment_mode/reserve_trigger
    simply won't have these fields and remain valid.
    """
    cash = max(0.0, float(cash_to_deploy or 0.0))
    holdings_list = holdings or []
    holdings_by_ticker = {h.ticker.upper(): h for h in holdings_list if h.ticker}
    data_quality = (getattr(regime, "data_quality", "medium") or "medium") if regime else "medium"
    base_confidence = _DATA_QUALITY_CONFIDENCE.get(data_quality, 0.80)

    if not allocations or cash <= 0:
        return DeploymentDecision(
            total_deposit=cash,
            deploy_now_amount=0.0,
            reserve_amount=0.0,
            deployment_mode="skip_or_wait",
            deployment_confidence=0.0,
            deployment_reason="No cash or allocations to deploy.",
            cash_drag_penalty_applied=False,
            reserve_reason=None,
            reserve_trigger=None,
            per_ticker_allocations=[],
            risks=["No allocations or zero cash."],
            data_quality=data_quality,
            evaluation_notes_for_future_decision_log=["Zero cash or empty allocation plan."],
            deployment_score=0.0,
            adjustments_applied=[],
        )

    # Apply WATCH-tier cap before scoring
    total_plan_raw = sum(max(0.0, a.amount or 0.0) for a in allocations)
    capped_allocs, cap_notes = _apply_watch_cap(allocations, total_plan_raw)
    total_plan = sum(max(0.0, a.amount or 0.0) for a in capped_allocs)

    # Preliminary unallocated reserve (cash beyond plan total)
    prelim_reserve = max(0.0, cash - total_plan)

    # Strong-trigger signals: near-cap tickers or risk-off regime → legitimate reserve
    has_strong_signal = any(
        a.current_weight >= NEAR_CAP_WEIGHT_THRESHOLD for a in capped_allocs
    ) or (regime and regime.regime_label == "risk_off")

    # ── Score computation ─────────────────────────────────────────────────────
    score = BASE_DEPLOYMENT_SCORE
    adjustments: list[str] = []

    s_bonus, s_note = _structural_bonus(capped_allocs)
    if s_bonus > 0:
        score += s_bonus
        adjustments.append(s_note)

    q_bonus, q_note = _quality_bonus(capped_allocs)
    if q_bonus > 0:
        score += q_bonus
        adjustments.append(q_note)

    cd_bonus, cd_applied = _cash_drag_bonus(
        prelim_reserve=prelim_reserve,
        total_cash=cash,
        has_strong_trigger=has_strong_signal,
    )
    if cd_bonus > 0:
        score += cd_bonus
        if cd_applied:
            adjustments.append(
                f"+{cd_bonus:.0f} cash_drag: idle reserve without strong trigger"
            )

    conc_penalty, conc_note = _concentration_penalty(
        capped_allocs, holdings_by_ticker=holdings_by_ticker
    )
    if conc_penalty > 0:
        score -= conc_penalty
        if conc_note:
            adjustments.append(conc_note)

    reg_penalty, reg_note = _regime_penalty(regime)
    score -= reg_penalty
    adjustments.append(reg_note)

    dq_penalty, dq_note = _data_quality_penalty(regime)
    if dq_penalty > 0:
        score -= dq_penalty
        adjustments.append(dq_note)

    # ── Mode classification ───────────────────────────────────────────────────
    mode = _classify_mode(score)

    # Compute deploy split based on mode
    mode_fractions: dict[str, float] = {
        "full_deploy": 1.0,
        "staged_deploy": 0.70,
        "defensive_reserve": 0.50,
        "skip_or_wait": 0.0,
    }
    imm_frac = mode_fractions.get(mode, 1.0)
    deploy_now = round(total_plan * imm_frac, 2)
    reserve = max(0.0, round(cash - deploy_now, 2))

    # ── Hard reserve trigger rule ─────────────────────────────────────────────
    reserve_trigger: Optional[ReserveTrigger] = None
    reserve_reason: Optional[str] = None
    forced_full_deploy = False

    if reserve > MIN_RESERVE_FOR_TRIGGER:
        reserve_trigger = _generate_reserve_trigger(
            allocations=capped_allocs,
            regime=regime,
            holdings_by_ticker=holdings_by_ticker,
        )
        if reserve_trigger is None:
            # No valid specific trigger → force reserve = 0 and full_deploy
            deploy_now = round(total_plan, 2)
            reserve = max(0.0, round(cash - deploy_now, 2))
            mode = "full_deploy"
            forced_full_deploy = True
            cd_applied = True
            adjustments.append(
                "No valid reserve trigger found; forced reserve=0 and mode=full_deploy"
            )
        else:
            reserve_reason = reserve_trigger.reserve_reason

    # ── Confidence ────────────────────────────────────────────────────────────
    confidence = round(base_confidence, 2)
    if data_quality == "low":
        # Low quality reduces confidence further below the multiplier
        confidence = round(base_confidence * 0.85, 2)

    # ── Per-ticker output ─────────────────────────────────────────────────────
    original_amt_by_ticker = {a.ticker: a.amount for a in allocations}
    per_ticker: list[PerTickerDeployment] = []
    for a in capped_allocs:
        role = _ticker_role(a.conviction_level, a.score)
        was_capped = abs((a.amount or 0.0) - (original_amt_by_ticker.get(a.ticker) or a.amount)) > 0.01
        cap_reason = next(
            (n for n in cap_notes if a.ticker in n),
            None,
        )
        t_imm = round((a.amount or 0.0) * imm_frac, 2)
        t_res = max(0.0, round((a.amount or 0.0) - t_imm, 2))
        per_ticker.append(
            PerTickerDeployment(
                ticker=a.ticker,
                role=role,
                amount=round(a.amount or 0.0, 2),
                deploy_now=t_imm,
                reserve=t_res,
                conviction_level=a.conviction_level or "LOW",
                rationale=a.reason or "",
                capped=was_capped,
                cap_reason=cap_reason,
            )
        )

    # ── Risks ─────────────────────────────────────────────────────────────────
    risks: list[str] = []
    if conc_note:
        risks.append(conc_note.lstrip("- 0123456789").strip())
    if reg_penalty > 0:
        risks.append(
            f"Regime: {regime.regime_label} (score {regime.regime_score:.0f})"
        )
    if dq_penalty > 0:
        risks.append(f"Data quality: {data_quality}")
    risks.extend(cap_notes)

    # ── Evaluation notes ──────────────────────────────────────────────────────
    eval_notes: list[str] = [
        f"deployment_score={score:.1f}, mode={mode}",
    ]
    if cd_applied:
        eval_notes.append(
            "cash_drag_penalty applied: weak/no reserve trigger promoted deployment"
        )
    if forced_full_deploy:
        eval_notes.append(
            "forced full_deploy: no specific reserve trigger could be generated"
        )
    if reserve_trigger:
        eval_notes.append(
            f"reserve_trigger: {reserve_trigger.trigger_type} → "
            f"{reserve_trigger.trigger_condition}"
        )

    # ── Deployment reason (user-facing) ───────────────────────────────────────
    label = regime.regime_label if regime else "neutral"
    if mode == "full_deploy":
        if forced_full_deploy or cd_applied:
            reason = (
                f"Full deployment: no specific reserve trigger found; "
                f"cash drag penalty applied. Regime: {label}."
            )
        else:
            reason = (
                f"Full deployment: strong plan in {label} market."
            )
    elif mode == "staged_deploy":
        reason = (
            f"Staged deployment: 70% now, 30% reserved. Regime: {label}."
            + (" Concentration risk present." if conc_note else "")
        )
    elif mode == "defensive_reserve":
        reason = (
            f"Defensive: 50% deployed now, 50% reserved. "
            f"{label.replace('_', '-')} regime with elevated risk signals."
        )
    else:
        reason = (
            f"Skip or wait: deployment quality insufficient. Regime: {label}."
        )

    return DeploymentDecision(
        total_deposit=round(cash, 2),
        deploy_now_amount=round(deploy_now, 2),
        reserve_amount=round(reserve, 2),
        deployment_mode=mode,
        deployment_confidence=confidence,
        deployment_reason=reason,
        cash_drag_penalty_applied=cd_applied,
        reserve_reason=reserve_reason,
        reserve_trigger=reserve_trigger,
        per_ticker_allocations=per_ticker,
        risks=risks,
        data_quality=data_quality,
        evaluation_notes_for_future_decision_log=eval_notes,
        deployment_score=round(score, 2),
        adjustments_applied=adjustments,
    )
