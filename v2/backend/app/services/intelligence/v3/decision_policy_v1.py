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

import re
from typing import Optional

from ...policy_tickers import kernel_crypto_tickers as _load_kernel_crypto_tickers

from .buy_conviction_guardrail import apply_buy_conviction_guardrail_by_band
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

# Crypto tickers recognized by the kernel's plain-English reason builder.
# Membership is configuration (app/policy_tickers.json), not code; the config
# load happens in the loader module, keeping this kernel free of its own IO.
_KERNEL_CRYPTO_TICKERS: frozenset[str] = _load_kernel_crypto_tickers()

# Raw metric key names that must never appear in visible rationale text.
_FORBIDDEN_KEYS: frozenset[str] = frozenset({
    "fcf_margin", "roic_ttm", "ev_ebitda", "gross_margin_ttm", "revenue_growth_yoy",
    "peg_ratio", "p_fcf", "ebit_margin", "net_margin_ttm", "debt_to_equity",
    "current_ratio", "quick_ratio", "free_cash_flow_yield", "altman_z",
    "earnings_growth_fwd", "book_value_per_share", "enterprise_value",
})

_PRICE_TARGET_PAT = re.compile(
    r"\$\s*\d+(?:\.\d+)?|price\s+target\s+of\s*\$?\d+", re.IGNORECASE
)


def _clean_evidence_text(raw: Optional[str], max_chars: int = 115) -> Optional[str]:
    """Sanitize and truncate LLM-generated evidence text for safe visible use.

    Returns None if the text is empty, not a string, contains raw metric keys,
    or price targets. Truncates at a sentence boundary when text exceeds max_chars.
    """
    if not raw or not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    text_lower = text.lower()

    # Safety net: discard entirely if raw metric keys or price targets slip through.
    for key in _FORBIDDEN_KEYS:
        if key in text_lower:
            return None
    if _PRICE_TARGET_PAT.search(text):
        return None

    if len(text) > max_chars:
        truncated = text[:max_chars]
        last_stop = max(truncated.rfind("."), truncated.rfind("!"), truncated.rfind("?"))
        if last_stop > max_chars // 2:
            text = truncated[: last_stop + 1]
        else:
            text = truncated.rstrip() + "…"

    return text or None

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

    # Cap 5: BUY + HIGH conviction requires STRONG evidence.
    # OK evidence (1–2 trusted signals) caps HIGH to MEDIUM for BUY.
    # Promotes the evidence-quality guardrail from shadow-only to the visible policy.
    base = apply_buy_conviction_guardrail_by_band(
        action=action,
        conviction=base,
        evidence_quality=evidence_quality,
    )

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
    primary_driver: Optional[str] = None,
    risk_flag_text: Optional[str] = None,
    action_reason: Optional[str] = None,
    analyst_drivers: Optional[list] = None,
    asset_type_hint: Optional[str] = None,
) -> tuple[str, str, str]:
    """Build (rationale, why_now, why_not_now) plain-English strings.

    Evidence-aware: uses per-ticker analyst evidence when available.
    Falls back to expressive axis-band language when evidence is absent.
    Must not contain raw metric key names or posture labels.
    """
    blocker_text = "; ".join(blockers) if blockers else ""
    suppressed_axes = [k for k in suppression_reasons if not k.startswith("truth_")]

    hint = (asset_type_hint or "stock").lower()
    is_etf = "etf" in hint
    is_crypto = "crypto" in hint or ticker.upper() in _KERNEL_CRYPTO_TICKERS

    # Best available driver text (cleaned, safe for display).
    driver = _clean_evidence_text(primary_driver)
    if not driver and analyst_drivers:
        for d in analyst_drivers:
            driver = _clean_evidence_text(d if isinstance(d, str) else None)
            if driver:
                break
    if not driver:
        driver = _clean_evidence_text(action_reason)

    risk_note = _clean_evidence_text(risk_flag_text, max_chars=90)

    # ── SELL ──────────────────────────────────────────────────────────────────
    if action == ActionV3.SELL:
        if risk_note:
            rationale = (
                f"{ticker}: risk signals are elevated — {risk_note} "
                "Reducing aligns with risk management."
            )
        else:
            rationale = (
                f"{ticker}: risk signals indicate a materially weakened investment case. "
                "Reducing or exiting this position aligns with risk management."
            )
        why_now = f"{ticker} risk signals warrant reduction; existing signal confirms exit direction."
        why_not_now = (
            f"Reassess {ticker} if risk signals materially improve."
            if not blocker_text
            else f"Reassess {ticker} if risk resolves. {blocker_text}"
        )

    # ── TRIM ──────────────────────────────────────────────────────────────────
    elif action == ActionV3.TRIM:
        rationale = (
            f"{ticker}: portfolio exposure has grown above target weight. "
            "Trim to rebalance toward the plan allocation."
        )
        why_now = f"{ticker} position fit signals overexposure; trimming maintains portfolio discipline."
        why_not_now = f"Hold full {ticker} position if fit rebalances or risk conditions improve."

    # ── BUY ───────────────────────────────────────────────────────────────────
    elif action == ActionV3.BUY:
        if is_etf:
            fit_note = {
                FitBand.UNDERWEIGHT: " Position has room to grow toward target.",
                FitBand.ON_TARGET: " Contribution pace is appropriate.",
                FitBand.UNKNOWN: "",
            }.get(portfolio_fit, "")
            rationale = (
                f"{ticker}: adds to core diversified exposure."
                f"{fit_note}"
            )
            why_now = f"Adding to {ticker} extends core portfolio coverage with disciplined allocation."
            why_not_now = f"Pause {ticker} contributions if portfolio weight reaches target or risk rises."

        elif is_crypto:
            if driver:
                rationale = f"{ticker}: {driver}"
                if not rationale.endswith("."):
                    rationale += "."
                rationale += " Speculative category — size position with care."
            else:
                rationale = (
                    f"{ticker}: price and momentum signals support adding, "
                    "but speculative category limits conviction."
                )
            why_now = f"Signal supports adding {ticker}; maintain speculative position limits."
            why_not_now = f"Reduce {ticker} if risk escalates or momentum reverses sharply."

        else:
            # Stock: use evidence when available, otherwise expressive axis-band fallback.
            if driver:
                risk_cav = ""
                if risk_note and risk_band in {RiskBand.MEDIUM, RiskBand.HIGH}:
                    risk_cav = f" Risk: {risk_note}"
                    if not risk_cav.endswith("."):
                        risk_cav += "."
                rationale = f"{ticker}: {driver}"
                if not rationale.endswith("."):
                    rationale += "."
                rationale += risk_cav
            else:
                # Fallback: describe the specific combination of axis bands.
                ev_desc = {
                    AxisBand.STRONG: "strong, multi-signal evidence",
                    AxisBand.OK: "adequate evidence",
                }.get(evidence_quality, "available evidence signals")

                price_desc = {
                    PriceBand.CHEAP: "an attractive entry point",
                    PriceBand.FAIR: "a fair current valuation",
                    PriceBand.SUPPRESSED: "a price context still resolving",
                }.get(price_context, "a price within range")

                fit_note = {
                    FitBand.UNDERWEIGHT: " Position has capacity to grow.",
                    FitBand.ON_TARGET: " Position weight is already near target.",
                    FitBand.UNKNOWN: "",
                }.get(portfolio_fit, "")

                risk_note_inline = (
                    " Risk signals are present but manageable."
                    if risk_band == RiskBand.MEDIUM
                    else ""
                )

                asset_ctx = "equity position"
                if "fund" in hint or "etf" in hint:
                    asset_ctx = "fund exposure"
                rationale = (
                    f"{ticker}: {asset_ctx} has {ev_desc} with {price_desc}."
                    f"{fit_note}{risk_note_inline}"
                )

            why_now = (
                f"{ticker} clears the evidence, attractiveness, and risk bar for adding to this position."
            )
            why_not_now = (
                f"Watch {ticker} for deterioration in: {', '.join(suppressed_axes)}."
                if suppressed_axes
                else (
                    f"Watch {ticker} for evidence weakening or risk escalation before adding further."
                    if not risk_note
                    else f"{ticker} watch: {risk_note}"
                )
            )

    # ── HOLD ──────────────────────────────────────────────────────────────────
    else:
        # Determine primary hold reason from blockers and suppressed axes.
        has_thin_evidence = (
            "Insufficient evidence to act." in blockers
            or evidence_quality in {AxisBand.THIN, AxisBand.SUPPRESSED}
        )
        has_elevated_risk = "Risk too elevated for a BUY recommendation." in blockers
        has_blocked_fit = "Portfolio fit blocked — speculative or high-risk category." in blockers
        has_weak_attractiveness = "Attractiveness signal absent or weak." in blockers
        price_is_stretched = price_context in {PriceBand.FULL, PriceBand.EXPENSIVE}
        price_is_unknown = price_context == PriceBand.SUPPRESSED

        # Use action_reason if it clearly explains the hold without boilerplate.
        hold_reason = _clean_evidence_text(action_reason, max_chars=110)

        if hold_reason and not has_blocked_fit:
            rationale = f"{ticker}: {hold_reason}"
            if not rationale.endswith("."):
                rationale += "."
            why_now = f"No action trigger for {ticker} at this time."
            why_not_now = (
                f"{ticker}: watch for evidence improvement or risk change before acting."
            )

        elif has_blocked_fit:
            # Differentiate HOLD fallback language by asset class and context so
            # rationale remains plain-English but not a repeated skeleton.
            asset_kind = (asset_type_hint or "").strip().lower()
            name_text = ticker.strip()
            driver_text = _clean_evidence_text(primary_driver or action_reason, max_chars=90)

            if "crypto" in asset_kind:
                rationale = (
                    f"{ticker}: {name_text} remains a higher-volatility holding in the portfolio mix. "
                    "We are holding the current size and not adding until risk and entry conditions improve."
                )
            elif "etf" in asset_kind or "fund" in asset_kind:
                rationale = (
                    f"{ticker}: this fund exposure already provides the intended portfolio sleeve. "
                    "Given the risk profile, keep weight steady rather than allocating additional capital now."
                )
            else:
                uncertainty = (
                    f" Current inputs are led by {driver_text.lower()}, but evidence breadth is still limited."
                    if driver_text
                    else " Evidence depth is still limited for increasing the position."
                )
                rationale = (
                    f"{ticker}: {name_text} sits in a higher-risk bucket, so we are maintaining exposure instead of buying more."
                    f"{uncertainty}"
                )
            why_now = f"{ticker} category limits adding beyond current position."
            why_not_now = f"Add {ticker} only if category risk profile materially improves."

        elif has_thin_evidence:
            rationale = (
                f"{ticker}: holding until evidence improves — "
                "data coverage is currently insufficient to act."
            )
            why_now = f"Signal for {ticker} is too thin to add or reduce with confidence."
            why_not_now = (
                f"Await more complete {ticker} signal before committing capital."
            )

        elif has_elevated_risk:
            if risk_note:
                risk_detail = risk_note if risk_note.endswith(".") else risk_note + "."
                rationale = (
                    f"{ticker}: not adding due to elevated risk — {risk_detail} "
                    "Holding current exposure."
                )
            else:
                rationale = (
                    f"{ticker}: not adding due to elevated risk signals. "
                    "Holding current exposure."
                )
            why_now = f"Risk levels for {ticker} are too high to add at this time."
            why_not_now = f"Add {ticker} when risk signals ease or price improves materially."

        elif price_is_stretched:
            rationale = (
                f"{ticker}: not adding at current valuation — "
                "price is extended relative to the evidence base."
            )
            why_now = f"Valuation for {ticker} is stretched; no new capital priority now."
            why_not_now = f"Add {ticker} if price pulls back to a better entry range."

        elif price_is_unknown:
            rationale = (
                f"{ticker}: holding until a clearer entry price develops — "
                "price context is unconfirmed."
            )
            why_now = f"Price signal for {ticker} is unresolved; no add until context clarifies."
            why_not_now = f"Watch {ticker} for price confirmation before committing further capital."

        elif has_weak_attractiveness:
            rationale = (
                f"{ticker}: no clear catalyst to add right now — "
                "attractiveness signals are neutral."
            )
            why_now = f"No compelling signal to act on {ticker} in either direction."
            why_not_now = (
                f"Watch {ticker} for a positive catalyst or improved attractiveness signal."
            )

        elif is_etf and portfolio_fit == FitBand.ON_TARGET:
            rationale = (
                f"{ticker}: position is at target weight — "
                "maintain regular contribution pace without new urgency."
            )
            why_now = f"{ticker} is on plan. Regular contribution schedule applies."
            why_not_now = f"Increase {ticker} if portfolio rebalances below target weight."

        elif portfolio_fit == FitBand.ON_TARGET:
            rationale = (
                f"{ticker}: position is appropriately sized — "
                "no additional capital priority at this time."
            )
            why_now = f"{ticker} position size is on target. No new capital required."
            why_not_now = (
                f"Add {ticker} if position drifts below target weight or evidence strengthens."
            )

        else:
            rationale = (
                f"{ticker}: signals do not yet clear the threshold to add or reduce."
            )
            why_now = f"No clear trigger to add or reduce {ticker} at this time."
            why_not_now = f"Watch {ticker} for evidence or risk changes before acting."

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
        primary_driver=inp.primary_driver,
        risk_flag_text=inp.risk_flag_text,
        action_reason=inp.action_reason,
        analyst_drivers=inp.analyst_drivers,
        asset_type_hint=inp.asset_type_hint,
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
