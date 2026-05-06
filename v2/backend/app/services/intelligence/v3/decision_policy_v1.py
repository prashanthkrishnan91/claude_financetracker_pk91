"""V3 decision policy v1 — deterministic, axis-based, no composite score.

Rules apply independent axes (evidence quality, attractiveness, price context,
portfolio fit, risk) in priority order. No weighted composite score.
LLMs must not produce action labels — they are derived here deterministically.

Priority order:
  1. SELL  — critical risk + (sell signal OR portfolio breach)
  2. TRIM  — portfolio fit OVERWEIGHT/BREACH (SELL bar not met)
             OR high risk with reduce signal
  3. BUY   — evidence OK+, attractiveness OK+, risk ≤ MEDIUM,
             fit not BLOCKED/BREACH/OVERWEIGHT, price FAIR/CHEAP
             (or price SUPPRESSED + evidence STRONG + attractiveness STRONG,
             capped at MEDIUM conviction)
  4. HOLD  — everything else

Conviction capping:
  - THIN evidence → conviction capped at LOW
  - Price SUPPRESSED → conviction capped at MEDIUM (never HIGH)
  - Risk HIGH/CRITICAL on BUY → conviction forced to LOW
  - SELL/TRIM actions → conviction capped at MEDIUM

Pure function — no IO, LLM, DB.
"""
from __future__ import annotations

from .decision_contracts import (
    ActionV3,
    AxisBand,
    ConvictionV3,
    DecisionInputV3,
    DecisionOutputV3,
    FitBand,
    PriceBand,
    RiskBand,
)

_SCHEMA_VERSION = "v3.1"


def _derive_attractiveness(inp: DecisionInputV3) -> AxisBand:
    """Derive attractiveness band from available action + conviction signals."""
    action = (inp.raw_action or "").upper()
    analyst = (inp.raw_analyst_action or "").upper()
    conv = (inp.upstream_conviction or "").upper()

    # SELL/TRIM → not attractive.
    if action in {"SELL", "TRIM"} or analyst in {"SELL", "TRIM", "REDUCE"}:
        return AxisBand.THIN

    # BUY signals → attractive; refine by conviction.
    if action == "BUY" or analyst == "BUY":
        if conv == "HIGH":
            return AxisBand.STRONG
        return AxisBand.OK  # BUY with MEDIUM or missing conviction → OK

    # HOLD: depends on conviction.
    if conv in {"HIGH", "MEDIUM"}:
        return AxisBand.OK
    if conv == "LOW":
        return AxisBand.THIN

    return AxisBand.SUPPRESSED


def _compute_conviction(
    *,
    action: ActionV3,
    evidence_quality: AxisBand,
    price_context: PriceBand,
    risk_band: RiskBand,
    upstream_conviction: str | None,
) -> ConvictionV3:
    """Derive final conviction from policy axes with priority-order caps."""
    upstream = (upstream_conviction or "").upper()

    if upstream == "HIGH":
        base = ConvictionV3.HIGH
    elif upstream == "MEDIUM":
        base = ConvictionV3.MEDIUM
    else:
        base = ConvictionV3.LOW

    # Cap 1: THIN evidence → LOW always.
    if evidence_quality == AxisBand.THIN:
        return ConvictionV3.LOW

    # Cap 2: Price SUPPRESSED → never HIGH.
    if price_context == PriceBand.SUPPRESSED and base == ConvictionV3.HIGH:
        base = ConvictionV3.MEDIUM

    # Cap 3: SELL/TRIM → never HIGH conviction.
    if action in {ActionV3.SELL, ActionV3.TRIM} and base == ConvictionV3.HIGH:
        base = ConvictionV3.MEDIUM

    # Cap 4: HIGH/CRITICAL risk on BUY → LOW conviction.
    if risk_band in {RiskBand.HIGH, RiskBand.CRITICAL} and action == ActionV3.BUY:
        return ConvictionV3.LOW

    return base


def _build_rationale(
    *,
    ticker: str,
    action: ActionV3,
    evidence_quality: AxisBand,
    attractiveness: AxisBand,
    price_context: PriceBand,
    portfolio_fit: FitBand,
    risk_band: RiskBand,
    blockers: list,
    suppression_reasons: dict,
) -> tuple[str, str, str]:
    """Build (rationale, why_now, why_not_now) plain-English strings.

    Ticker is included in every rationale to ensure card-level uniqueness.
    Must not contain raw metric key names (fcf_margin, roic_ttm, ev_ebitda, etc.).
    """
    blocker_text = "; ".join(blockers) if blockers else ""
    suppressed_axes = [k for k in suppression_reasons if not k.startswith("truth_")]

    if action == ActionV3.SELL:
        rationale = (
            f"{ticker}: evidence points to material risk or a broken investment case. "
            "Reducing or exiting this position aligns with risk management."
        )
        why_now = f"{ticker} risk signals are elevated; existing signal indicates reduction is warranted."
        why_not_now = (
            f"Reassess {ticker} if risk signals materially improve."
            if not blocker_text
            else f"Reassess {ticker} if risk resolves. {blocker_text}"
        )

    elif action == ActionV3.TRIM:
        rationale = (
            f"{ticker}: portfolio exposure appears elevated relative to target weight. "
            "Trim to rebalance toward plan allocation."
        )
        why_now = f"{ticker} position fit indicates overexposure; trimming maintains portfolio discipline."
        why_not_now = f"Hold full {ticker} position if fit rebalances or risk conditions improve."

    elif action == ActionV3.BUY:
        price_phrase = {
            PriceBand.CHEAP: "attractively priced",
            PriceBand.FAIR: "fairly priced",
            PriceBand.SUPPRESSED: "price context not yet confirmed",
        }.get(price_context, "priced within range")
        ev_phrase = {
            AxisBand.STRONG: "strong evidence",
            AxisBand.OK: "adequate evidence",
        }.get(evidence_quality, "available evidence")
        fit_phrase = {
            FitBand.UNDERWEIGHT: "Portfolio has room to add.",
            FitBand.ON_TARGET: "Position weight is on target.",
            FitBand.UNKNOWN: "Portfolio fit not yet assessed.",
        }.get(portfolio_fit, "Portfolio fit allows adding.")
        rationale = (
            f"{ticker}: {ev_phrase} and {price_phrase}. "
            f"{fit_phrase} Manageable risk."
        )
        why_now = (
            f"{ticker} meets the evidence quality and attractiveness bar for adding to this position."
        )
        why_not_now = (
            f"Watch {ticker} for deterioration in: {', '.join(suppressed_axes)}."
            if suppressed_axes
            else f"Watch {ticker} for evidence weakening or risk escalation before adding further."
        )

    else:  # HOLD
        if blockers:
            rationale = f"{ticker}: holding while addressing — {blocker_text}."
            why_now = f"No clear trigger to add or reduce {ticker} at this time."
            why_not_now = f"Address {ticker} blockers before acting: {blocker_text}."
        elif suppressed_axes:
            rationale = (
                f"{ticker}: holding while evidence builds. "
                f"Missing context on: {', '.join(suppressed_axes)}."
            )
            why_now = f"Insufficient signal to act on {ticker} in either direction."
            why_not_now = f"Await improved {ticker} evidence before committing further capital."
        else:
            rationale = f"{ticker}: current signals support maintaining this position."
            why_now = f"No compelling reason to add or reduce {ticker} at this time."
            why_not_now = f"Watch {ticker} for evidence changes or risk escalation."

    return rationale, why_now, why_not_now


def decide(inp: DecisionInputV3) -> DecisionOutputV3:
    """Apply v3 policy rules to produce a deterministic DecisionOutputV3.

    Uses independent axes in priority order — no composite score.
    """
    blockers: list[str] = []
    suppression_reasons: dict = dict(inp.suppression_reasons)

    attractiveness = _derive_attractiveness(inp)

    action_raw = (inp.raw_action or "").upper()
    analyst_raw = (inp.raw_analyst_action or "").upper()
    sell_signal = action_raw == "SELL" or analyst_raw == "SELL"

    # ── Rule 1: SELL ─────────────────────────────────────────────────────────
    # Critical risk + (explicit sell signal OR portfolio breach).
    if inp.risk_band == RiskBand.CRITICAL and sell_signal:
        action = ActionV3.SELL
        blockers.append("Critical risk with sell signal.")

    elif inp.risk_band == RiskBand.CRITICAL and inp.portfolio_fit == FitBand.BREACH:
        action = ActionV3.SELL
        blockers.append("Critical risk with portfolio breach.")

    # ── Rule 2: TRIM ─────────────────────────────────────────────────────────
    # Portfolio overweight/breach (SELL bar not met) OR high risk + reduce signal.
    elif inp.portfolio_fit in {FitBand.OVERWEIGHT, FitBand.BREACH}:
        action = ActionV3.TRIM
        blockers.append(f"Portfolio fit is {inp.portfolio_fit.value}.")

    elif inp.risk_band == RiskBand.HIGH and action_raw in {"TRIM", "SELL"}:
        action = ActionV3.TRIM
        blockers.append("High risk with reduce signal.")

    # ── Rule 3: BUY ──────────────────────────────────────────────────────────
    # evidence OK+, attractiveness OK+, risk ≤ MEDIUM,
    # fit not BLOCKED/BREACH/OVERWEIGHT, price FAIR/CHEAP
    # OR (price SUPPRESSED + evidence STRONG + attractiveness STRONG).
    elif (
        inp.evidence_quality not in {AxisBand.THIN, AxisBand.SUPPRESSED}
        and attractiveness not in {AxisBand.THIN, AxisBand.SUPPRESSED}
        and inp.risk_band not in {RiskBand.HIGH, RiskBand.CRITICAL}
        and inp.portfolio_fit not in {FitBand.BLOCKED, FitBand.BREACH, FitBand.OVERWEIGHT}
        and (
            inp.price_context in {PriceBand.CHEAP, PriceBand.FAIR}
            or (
                inp.price_context == PriceBand.SUPPRESSED
                and inp.evidence_quality == AxisBand.STRONG
                and attractiveness == AxisBand.STRONG
            )
        )
    ):
        action = ActionV3.BUY
        if inp.price_context == PriceBand.SUPPRESSED:
            blockers.append("Price context unconfirmed — conviction capped at MEDIUM.")

    # ── Rule 4: HOLD ─────────────────────────────────────────────────────────
    else:
        action = ActionV3.HOLD
        if inp.evidence_quality in {AxisBand.THIN, AxisBand.SUPPRESSED}:
            blockers.append("Insufficient evidence to act.")
        if inp.risk_band in {RiskBand.HIGH, RiskBand.CRITICAL}:
            blockers.append("Risk too elevated for a BUY recommendation.")
        if inp.portfolio_fit == FitBand.BLOCKED:
            blockers.append("Portfolio fit blocked — speculative or high-risk category.")
        if attractiveness in {AxisBand.THIN, AxisBand.SUPPRESSED}:
            blockers.append("Attractiveness signal absent or weak.")

    conviction = _compute_conviction(
        action=action,
        evidence_quality=inp.evidence_quality,
        price_context=inp.price_context,
        risk_band=inp.risk_band,
        upstream_conviction=inp.upstream_conviction,
    )

    rationale, why_now, why_not_now = _build_rationale(
        ticker=inp.ticker,
        action=action,
        evidence_quality=inp.evidence_quality,
        attractiveness=attractiveness,
        price_context=inp.price_context,
        portfolio_fit=inp.portfolio_fit,
        risk_band=inp.risk_band,
        blockers=blockers,
        suppression_reasons=suppression_reasons,
    )

    return DecisionOutputV3(
        ticker=inp.ticker,
        action=action,
        conviction=conviction,
        evidence_quality=inp.evidence_quality,
        attractiveness=attractiveness,
        price_context=inp.price_context,
        portfolio_fit=inp.portfolio_fit,
        risk_band=inp.risk_band,
        blockers=blockers,
        suppression_reasons=suppression_reasons,
        rationale_plain_english=rationale,
        why_now=why_now,
        why_not_now=why_not_now,
        source_signal_summary=inp.source_signal_summary,
        schema_version=_SCHEMA_VERSION,
    )
