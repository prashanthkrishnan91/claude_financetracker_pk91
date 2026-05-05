"""Adapter: build DecisionInputV3 from existing InsightCard + intel_read signals.

Uses only data already present in the v2 backend today.
Does not add new providers, external calls, or invented metrics.
Missing signals suppress only the relevant axis and record a reason.

PR 7: build_truth_aware_decision_input() wires the PR 6 Data Truth Contract
into this adapter so unsafe axes (MISSING/UNAVAILABLE/CONFLICTING/STALE) null
only their own input signals before DecisionInputV3 is built. WEAK axes remain
safe per the PR 6 contract (safe_for_decision=True with LOW trust).

Pure function — no IO, DB, or LLM calls.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Optional

from .decision_contracts import (
    AxisBand,
    DecisionInputV3,
    FitBand,
    PriceBand,
    RiskBand,
)

# Tickers always treated as high-risk / speculative.
_SPECULATIVE_TICKERS: frozenset[str] = frozenset(
    {"BTC", "XRP", "RIVN", "KLAR", "BLSH", "STUB"}
)

# Category keywords implying BLOCKED fit (not core portfolio material).
_BLOCKED_CAT_KEYWORDS: frozenset[str] = frozenset({"crypto", "speculative", "ipo"})

# Category keywords implying ON_TARGET fit by nature (DCA / income ETFs).
_DCA_CAT_KEYWORDS: frozenset[str] = frozenset({"etf"})


def _norm(raw: Optional[str]) -> str:
    return (raw or "").strip().upper()


def _derive_evidence_quality(
    *,
    data_quality_label: Optional[str],
    intel_read: Optional[dict],
    suppression_reasons: dict,
) -> AxisBand:
    """Map existing data-quality signals to AxisBand."""
    if intel_read is None and data_quality_label is None:
        suppression_reasons["evidence_quality"] = (
            "No intel_read or data_quality_label available."
        )
        return AxisBand.SUPPRESSED

    # intel_read carries trusted-dimension counts — prefer it when present.
    if intel_read is not None:
        insufficient = bool(intel_read.get("insufficient_data"))
        trusted = intel_read.get("trusted_dimensions") or []
        n_trusted = len(trusted) if isinstance(trusted, list) else 0

        if insufficient or n_trusted == 0:
            suppression_reasons["evidence_quality"] = (
                f"Insufficient data — {n_trusted} trusted dimension(s)."
            )
            return AxisBand.THIN
        if n_trusted >= 3:
            return AxisBand.STRONG
        return AxisBand.OK

    # Fallback: data_quality_label.
    label = _norm(data_quality_label)
    if label == "HIGH":
        return AxisBand.STRONG
    if label == "MEDIUM":
        return AxisBand.OK
    if label == "LOW":
        suppression_reasons["evidence_quality"] = (
            "Data quality label is LOW — evidence thin."
        )
        return AxisBand.THIN

    suppression_reasons["evidence_quality"] = (
        f"Unrecognized data quality label: {data_quality_label!r}."
    )
    return AxisBand.SUPPRESSED


def _derive_price_context(
    *,
    action: str,
    analyst_action: str,
    thesis_v2: Optional[dict],
    suppression_reasons: dict,
) -> PriceBand:
    """Map existing action + thesis signals to PriceBand."""
    if action == "SELL" or analyst_action == "SELL":
        return PriceBand.EXPENSIVE
    if action == "TRIM" or analyst_action in {"TRIM", "REDUCE"}:
        return PriceBand.FULL

    if action == "BUY" or analyst_action == "BUY":
        # Try to refine CHEAP vs FAIR from thesis scorecard valuation band.
        if thesis_v2 and isinstance(thesis_v2, dict):
            val = str(
                thesis_v2.get("valuation_band")
                or thesis_v2.get("valuation_signal")
                or ""
            ).upper()
            if any(kw in val for kw in ("CHEAP", "UNDERVAL", "DISCOUNT")):
                return PriceBand.CHEAP
        return PriceBand.FAIR

    # HOLD / REVIEW with no stronger signal — price context unknown.
    suppression_reasons["price_context"] = (
        "No clear price signal from action or analyst action."
    )
    return PriceBand.SUPPRESSED


def _derive_portfolio_fit(
    *,
    action: str,
    analyst_action: str,
    category: str,
    ticker: str,
    suppression_reasons: dict,
) -> FitBand:
    """Map action + category to FitBand."""
    ticker_up = ticker.upper()
    cat_low = (category or "").lower()

    # Speculative tickers / categories → BLOCKED.
    if ticker_up in _SPECULATIVE_TICKERS or any(
        k in cat_low for k in _BLOCKED_CAT_KEYWORDS
    ):
        return FitBand.BLOCKED

    # Explicit SELL → BREACH (plan says exit).
    if action == "SELL" or analyst_action == "SELL":
        return FitBand.BREACH

    # TRIM signal → OVERWEIGHT.
    if action == "TRIM" or analyst_action in {"TRIM", "REDUCE"}:
        return FitBand.OVERWEIGHT

    # Core ETF / income → ON_TARGET by nature.
    if any(k in cat_low for k in _DCA_CAT_KEYWORDS):
        return FitBand.ON_TARGET

    # BUY → UNDERWEIGHT signal.
    if action == "BUY" or analyst_action == "BUY":
        return FitBand.UNDERWEIGHT

    # HOLD / REVIEW → ON_TARGET.
    if action in {"HOLD", "REVIEW"}:
        return FitBand.ON_TARGET

    suppression_reasons["portfolio_fit"] = (
        "Cannot derive portfolio fit from available signals."
    )
    return FitBand.UNKNOWN


def _derive_risk_band(
    *,
    ticker: str,
    category: str,
    technical_signal: Optional[str],
    risk_flag: Optional[str],
    analyst_risks: Optional[list],
    action: str,
    suppression_reasons: dict,
) -> RiskBand:
    """Map risk signals to RiskBand."""
    ticker_up = ticker.upper()
    cat_low = (category or "").lower()
    tech = _norm(technical_signal)

    # Build a risk text blob from available fields (no raw metric keys).
    risk_parts = [risk_flag or ""]
    for r in analyst_risks or []:
        if isinstance(r, str):
            risk_parts.append(r)
    risk_text = " ".join(risk_parts).lower()

    # CRITICAL: crypto/speculative + SELL action.
    if (ticker_up in _SPECULATIVE_TICKERS or "crypto" in cat_low) and action == "SELL":
        return RiskBand.CRITICAL

    # CRITICAL: explicit catastrophic language in risk text.
    _critical_kw = {"critical", "severe", "catastrophic", "insolvency", "default", "fraud"}
    if any(kw in risk_text for kw in _critical_kw):
        return RiskBand.CRITICAL

    # HIGH: bearish technicals + any risk text, or SELL action with risk text.
    if tech in {"SELL", "BEARISH", "WEAK"} and risk_text.strip():
        return RiskBand.HIGH
    if action == "SELL" and risk_text.strip():
        return RiskBand.HIGH

    # MEDIUM: any risk text without bearish technicals.
    if risk_text.strip():
        return RiskBand.MEDIUM

    # LOW: bearish technicals only.
    if tech in {"SELL", "BEARISH", "WEAK"}:
        return RiskBand.LOW

    # NONE: clean BUY/HOLD with no risk text.
    if action in {"BUY", "HOLD"} and not risk_text.strip():
        return RiskBand.NONE

    suppression_reasons["risk_band"] = "Insufficient risk signal data."
    return RiskBand.UNKNOWN


def build_decision_input_from_card(
    *,
    ticker: str,
    action: Optional[str],
    analyst_action: Optional[str],
    conviction_level: Optional[str],
    technical_signal: Optional[str],
    risk_flag: Optional[str],
    analyst_risks: Optional[list],
    category: Optional[str],
    data_quality_label: Optional[str],
    intel_read: Optional[dict],
    thesis_v2: Optional[dict],
) -> DecisionInputV3:
    """Build a DecisionInputV3 from existing InsightCard + intel_read signals.

    All parameters correspond to existing InsightCard or intel_read fields.
    Does not add new providers or invent metrics.
    """
    suppression_reasons: dict = {}
    source_signal_summary: dict = {}

    _action = _norm(action)
    _analyst = _norm(analyst_action)

    # Audit trail — no raw metric keys.
    source_signal_summary["action"] = _action or None
    source_signal_summary["analyst_action"] = _analyst or None
    source_signal_summary["conviction_level"] = conviction_level
    source_signal_summary["technical_signal"] = technical_signal
    source_signal_summary["category"] = category
    source_signal_summary["data_quality_label"] = data_quality_label
    source_signal_summary["has_intel_read"] = intel_read is not None
    source_signal_summary["has_thesis_v2"] = thesis_v2 is not None

    evidence_quality = _derive_evidence_quality(
        data_quality_label=data_quality_label,
        intel_read=intel_read,
        suppression_reasons=suppression_reasons,
    )
    price_context = _derive_price_context(
        action=_action,
        analyst_action=_analyst,
        thesis_v2=thesis_v2,
        suppression_reasons=suppression_reasons,
    )
    portfolio_fit = _derive_portfolio_fit(
        action=_action,
        analyst_action=_analyst,
        category=category or "",
        ticker=ticker,
        suppression_reasons=suppression_reasons,
    )
    risk_band = _derive_risk_band(
        ticker=ticker,
        category=category or "",
        technical_signal=technical_signal,
        risk_flag=risk_flag,
        analyst_risks=analyst_risks,
        action=_action,
        suppression_reasons=suppression_reasons,
    )

    return DecisionInputV3(
        ticker=ticker,
        evidence_quality=evidence_quality,
        price_context=price_context,
        portfolio_fit=portfolio_fit,
        risk_band=risk_band,
        raw_action=_action or None,
        raw_analyst_action=_analyst or None,
        upstream_conviction=conviction_level,
        suppression_reasons=suppression_reasons,
        source_signal_summary=source_signal_summary,
    )


def build_truth_aware_decision_input(
    *,
    ticker: str,
    action: Optional[str],
    analyst_action: Optional[str],
    conviction_level: Optional[str],
    technical_signal: Optional[str],
    risk_flag: Optional[str],
    analyst_risks: Optional[list],
    category: Optional[str],
    data_quality_label: Optional[str],
    intel_read: Optional[dict],
    thesis_v2: Optional[dict],
) -> tuple:
    """Build DecisionInputV3 informed by the PR 6 Data Truth Contract.

    Evaluates each signal axis with evaluate_card_signals_truth() before
    building DecisionInputV3. Axes the truth contract marks unsafe
    (safe_for_decision=False) have their input signals nulled so only the
    affected axis is suppressed. Axes with WEAK findings (safe_for_decision=True,
    LOW trust) are NOT suppressed — they pass through with reduced trust.

    Returns:
        (DecisionInputV3, truth_summaries: list[AxisTruthSummary], suppressed_by_truth: dict[str, str])
        suppressed_by_truth maps axis_name → dominant_reason_code for each
        axis that was suppressed due to truth unsafety.

    Pure function — no IO, DB, LLM, or provider calls. Never raises.
    """
    from .existing_signal_truth_adapter import evaluate_card_signals_truth

    truth_summaries = evaluate_card_signals_truth(
        action=action,
        analyst_action=analyst_action,
        conviction_level=conviction_level,
        technical_signal=technical_signal,
        risk_flag=risk_flag,
        analyst_risks=analyst_risks,
        data_quality_label=data_quality_label,
        intel_read=intel_read,
    )
    truth_by_axis = {s.axis_name: s for s in truth_summaries}
    suppressed_by_truth: dict[str, str] = {}

    # Start with original signal values; null out each axis that is unsafe.
    safe_action = action
    safe_analyst_action = analyst_action
    safe_conviction = conviction_level
    safe_technical = technical_signal
    safe_risk_flag = risk_flag
    safe_analyst_risks = analyst_risks
    safe_data_quality = data_quality_label
    safe_intel_read = intel_read

    ev_axis = truth_by_axis.get("evidence_quality")
    if ev_axis is not None and not ev_axis.safe_for_decision:
        safe_data_quality = None
        safe_intel_read = None
        suppressed_by_truth["evidence_quality"] = ev_axis.dominant_reason_code

    act_axis = truth_by_axis.get("action_signal")
    if act_axis is not None and not act_axis.safe_for_decision:
        safe_action = None
        safe_analyst_action = None
        suppressed_by_truth["action_signal"] = act_axis.dominant_reason_code

    conv_axis = truth_by_axis.get("conviction")
    if conv_axis is not None and not conv_axis.safe_for_decision:
        safe_conviction = None
        suppressed_by_truth["conviction"] = conv_axis.dominant_reason_code

    tech_axis = truth_by_axis.get("technical_signal")
    if tech_axis is not None and not tech_axis.safe_for_decision:
        safe_technical = None
        suppressed_by_truth["technical_signal"] = tech_axis.dominant_reason_code

    risk_axis = truth_by_axis.get("risk_signal")
    if risk_axis is not None and not risk_axis.safe_for_decision:
        safe_risk_flag = None
        safe_analyst_risks = None
        suppressed_by_truth["risk_signal"] = risk_axis.dominant_reason_code

    inp = build_decision_input_from_card(
        ticker=ticker,
        action=safe_action,
        analyst_action=safe_analyst_action,
        conviction_level=safe_conviction,
        technical_signal=safe_technical,
        risk_flag=safe_risk_flag,
        analyst_risks=safe_analyst_risks,
        category=category,
        data_quality_label=safe_data_quality,
        intel_read=safe_intel_read,
        thesis_v2=thesis_v2,
    )

    for axis, reason_code in suppressed_by_truth.items():
        inp.suppression_reasons[f"truth_{axis}"] = f"truth_suppressed:{reason_code}"

    return inp, truth_summaries, suppressed_by_truth
