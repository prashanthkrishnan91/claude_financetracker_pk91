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
        or "Unavailable"
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

    summary = (
        analyst_summary
        or _s(rec.get("summary"))
        or _human_summary(ticker=ticker, action=action, thesis=thesis, detail=detail)
    )
    why = _s(rec.get("why_this_matters")) or _human_why(action=action, ticker=ticker, rationale=rationale, driver=drivers[0] if drivers else "")
    explanation = (
        analyst_reasoning
        or _s(rec.get("plain_language_explanation"))
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
