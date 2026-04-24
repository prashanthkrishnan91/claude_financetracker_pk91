"""Canonical recommendation-reasoning schema + normalization helpers."""

from __future__ import annotations

from typing import Any


CANONICAL_REASONING_KEYS = (
    "sentiment",
    "summary",
    "reasoning_summary",
    "thesis",
    "why_this_matters",
    "key_drivers",
    "drivers",
    "main_risks",
    "risks",
    "confidence",
    "conviction",
    "supporting_evidence",
    "plain_language_explanation",
    "data_quality",
    "fallback_flags",
    # Hedge-fund memo fields (Phase 7)
    "conviction_level",
    "primary_driver",
    "risk_flag",
    "action_reason",
)


def normalize_reasoning_payload(
    rec: dict[str, Any],
    *,
    analyst_verdict: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return one stable reasoning contract across legacy + mixed rows."""
    rec = rec or {}
    analyst_verdict = analyst_verdict if isinstance(analyst_verdict, dict) else {}
    detail = _s(rec.get("detail"))
    analyst_summary = _s(analyst_verdict.get("summary"))
    analyst_thesis = _s(analyst_verdict.get("thesis"))
    analyst_reasoning = _s(
        analyst_verdict.get("reasoning") or analyst_verdict.get("reasoning_summary")
    )
    thesis = (
        analyst_reasoning
        or analyst_thesis
        or _s(rec.get("thesis"))
        or _s(rec.get("investment_thesis"))
        or detail
    )
    rationale = _s(rec.get("rationale"))
    action = _s(rec.get("action")).upper() or "HOLD"
    ticker = _s(rec.get("ticker")).upper() or "this position"
    sentiment_label = (
        _s(rec.get("sentiment_label"))
        or _s(analyst_verdict.get("sentiment"))
    )

    drivers = _list_of_str(
        analyst_verdict.get("key_drivers")
        or rec.get("key_drivers")
        or rec.get("drivers")
    )
    risks = _list_of_str(
        analyst_verdict.get("risks")
        or rec.get("main_risks")
        or rec.get("risks")
    )
    conviction = _f(rec.get("conviction_score"))
    confidence = _f(analyst_verdict.get("confidence"), fallback=conviction)
    fallback_flags = _list_of_str(rec.get("reason_tags"))
    if analyst_verdict.get("used_fallback") and "analyst_fallback" not in fallback_flags:
        fallback_flags.append("analyst_fallback")
    if not thesis and not detail:
        fallback_flags.append("reasoning_unavailable")

    sentiment_label = _normalize_sentiment(
        sentiment_label,
        action=action,
        sentiment_score=rec.get("sentiment_score"),
        technical_signal=rec.get("technical_signal"),
        conviction=conviction,
    )

    context = _build_reasoning_context(
        rec=rec,
        ticker=ticker,
        action=action,
        sentiment=sentiment_label,
        conviction=conviction,
        confidence=confidence,
        driver=drivers[0] if drivers else "",
        risk=risks[0] if risks else "",
    )
    deterministic_summary = _deterministic_summary(context)
    deterministic_why = _deterministic_why(context)
    deterministic_plain = _deterministic_plain_explanation(context)
    if not drivers:
        drivers = _deterministic_drivers(context)
        if drivers and "deterministic_drivers" not in fallback_flags:
            fallback_flags.append("deterministic_drivers")
    if not risks:
        risks = _deterministic_risks(context)
        if risks and "deterministic_risks" not in fallback_flags:
            fallback_flags.append("deterministic_risks")
    if confidence is None:
        confidence = _infer_confidence(conviction=conviction, sentiment=sentiment_label, action=action)
        fallback_flags.append("confidence_inferred")

    summary = (
        analyst_summary
        or _s(rec.get("summary"))
        or deterministic_summary
        or _human_summary(ticker=ticker, action=action, thesis=thesis, detail=detail)
    )
    why = _s(rec.get("why_this_matters")) or deterministic_why or _human_why(action=action, ticker=ticker, rationale=rationale, driver=drivers[0] if drivers else "")
    explanation = (
        _s(rec.get("plain_language_explanation"))
        or analyst_reasoning
        or deterministic_plain
        or summary
    )

    return {
        "sentiment": sentiment_label,
        "summary": summary,
        "reasoning_summary": _s(rec.get("reasoning_summary")) or summary,
        "thesis": thesis,
        "why_this_matters": why,
        "key_drivers": drivers,
        "drivers": drivers,
        "main_risks": risks,
        "risks": risks,
        "confidence": confidence,
        "conviction": conviction,
        "supporting_evidence": (
            _list_of_str(analyst_verdict.get("citations"))
            or _list_of_str(rec.get("supporting_evidence"))
            or drivers
        ),
        "plain_language_explanation": explanation,
        "data_quality": _s(rec.get("data_quality_label")) or _s(rec.get("data_quality")) or "UNKNOWN",
        "fallback_flags": fallback_flags,
        # Hedge-fund memo fields — pass through from analyst verdict when available
        "conviction_level": _s(analyst_verdict.get("conviction_level")) or None,
        "primary_driver": _s(analyst_verdict.get("primary_driver")) or None,
        "risk_flag": _s(analyst_verdict.get("risk_flag")) or None,
        "action_reason": _s(analyst_verdict.get("action_reason")) or None,
    }


def _human_summary(*, ticker: str, action: str, thesis: str, detail: str) -> str:
    if thesis:
        return thesis[:360]
    if detail:
        return detail[:360]
    return f"Data-backed recommendation available for {ticker}; AI reasoning is unavailable."


def _human_why(*, action: str, ticker: str, rationale: str, driver: str) -> str:
    lead = f"{action} is suggested for {ticker} based on current portfolio evidence."
    if driver:
        return f"{lead} Key driver: {driver.rstrip('.')[:180]}."
    if rationale:
        return rationale[:260]
    return "Data-backed recommendation available; AI reasoning is unavailable."


def _normalize_sentiment(
    sentiment: str,
    *,
    action: str,
    sentiment_score: Any,
    technical_signal: Any,
    conviction: float | None,
) -> str:
    s = _s(sentiment).lower()
    if s in {"positive", "negative", "mixed"}:
        return s.capitalize()
    score = _f(sentiment_score)
    if score is not None:
        if score >= 0.2:
            return "Positive"
        if score <= -0.2:
            return "Negative"
        return "Mixed"
    tech = _s(technical_signal).upper()
    if tech == "BUY":
        return "Positive"
    if tech == "SELL":
        return "Negative"
    if action in {"BUY", "SELL"} and conviction is not None and abs(conviction) >= 0.45:
        return "Positive" if action == "BUY" else "Negative"
    return "Mixed"


def _build_reasoning_context(
    *,
    rec: dict[str, Any],
    ticker: str,
    action: str,
    sentiment: str,
    conviction: float | None,
    confidence: float | None,
    driver: str,
    risk: str,
) -> dict[str, str]:
    technical = _s(rec.get("technical_signal")).upper()
    position = _s(rec.get("position_context"))
    allocation = rec.get("suggested_allocation")
    sector = _s(rec.get("sector") or rec.get("asset_type") or rec.get("category"))
    tax = _s(rec.get("tax_note"))
    rationale = _s(rec.get("rationale"))
    volatility = _s(rec.get("volatility_regime"))
    conviction_band = _conviction_band(conviction, confidence)
    return {
        "ticker": ticker,
        "action": action,
        "sentiment": sentiment,
        "technical": technical or "HOLD",
        "position": position,
        "allocation": f"{float(allocation):.1f}%" if allocation is not None else "",
        "sector": sector,
        "tax": tax,
        "rationale": rationale,
        "volatility": volatility,
        "driver": driver,
        "risk": risk,
        "conviction_band": conviction_band,
    }


def _deterministic_summary(ctx: dict[str, str]) -> str:
    lead = f"{ctx['ticker']} is rated {ctx['action']} with {ctx['sentiment'].lower()} sentiment."
    why = ctx["driver"] or "Position-level evidence supports a cautious, data-backed stance."
    return f"{lead} {why[:220].rstrip('.') }."


def _deterministic_why(ctx: dict[str, str]) -> str:
    upside = ctx["driver"] or "Current business and portfolio context supports staying disciplined with this position."
    downside = ctx["risk"] or "The main risk is concentration drift or a weaker business backdrop than expected."
    return f"{upside[:170].rstrip('.')} while monitoring {downside[:170].rstrip('.')}."


def _deterministic_plain_explanation(ctx: dict[str, str]) -> str:
    action = ctx["action"]
    core = (
        f"{ctx['ticker']} shows {ctx['sentiment'].lower()} evidence across available signals, "
        f"so the current stance is {action}."
    )
    right = ctx["driver"] or "If business demand and execution remain stable, returns can keep compounding."
    wrong = ctx["risk"] or "If volatility rises or fundamentals soften, this thesis weakens quickly."
    sizing = (
        f" Suggested allocation is {ctx['allocation']}."
        if ctx["allocation"]
        else " Position sizing should stay aligned with diversification limits."
    )
    tax = f" {ctx['tax']}" if ctx["tax"] else ""
    return f"{core} What could go right: {right[:170].rstrip('.')}. What could go wrong: {wrong[:170].rstrip('.')}.{sizing}{tax}"


def _deterministic_drivers(ctx: dict[str, str]) -> list[str]:
    out: list[str] = []
    out.append(f"Recommendation confidence is {ctx['conviction_band']} based on available evidence quality.")
    if ctx["sector"]:
        out.append(f"Portfolio context: exposure to {ctx['sector']} remains relevant for this call.")
    if ctx["position"]:
        out.append(ctx["position"][:200])
    return out[:3]


def _deterministic_risks(ctx: dict[str, str]) -> list[str]:
    out: list[str] = []
    out.append("A business-demand slowdown or concentration drift could invalidate this recommendation.")
    if ctx["volatility"]:
        out.append(f"Volatility regime risk: {ctx['volatility'][:180]}.")
    if ctx["allocation"]:
        out.append(f"Allocation risk: keep {ctx['ticker']} near {ctx['allocation']} to avoid concentration drift.")
    return out[:3]


def _conviction_band(conviction: float | None, confidence: float | None) -> str:
    c = confidence if confidence is not None else conviction
    if c is None:
        return "moderate"
    if abs(c) >= 0.7:
        return "high"
    if abs(c) >= 0.4:
        return "medium"
    return "moderate"


def _infer_confidence(*, conviction: float | None, sentiment: str, action: str) -> float:
    base = 0.55
    if conviction is not None:
        base = min(0.92, max(0.35, abs(conviction)))
    if sentiment == "Mixed":
        base = min(base, 0.6)
    if action == "HOLD":
        base = min(base, 0.58)
    return round(base, 2)


def _s(v: Any) -> str:
    return str(v).strip() if isinstance(v, str) else ""


def _list_of_str(v: Any) -> list[str]:
    if not isinstance(v, list):
        return []
    out: list[str] = []
    for item in v:
        if isinstance(item, str) and item.strip():
            out.append(item.strip()[:220])
    return out[:4]


def _f(v: Any, *, fallback: float | None = None) -> float | None:
    try:
        return float(v) if v is not None else fallback
    except (TypeError, ValueError):
        return fallback
