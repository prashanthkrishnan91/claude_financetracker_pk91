"""Intel v2 PR-7 — deterministic plain-English thesis translation.

Backend-only additive contract that translates thesis_v2 scorecard output
into cautious plain-English labels suitable for future UI use.

Rules:
- Deterministic only (no IO, no LLM).
- Do not expose raw finance metric keys in user-facing labels.
- Preserve missing-data honesty for PARTIAL / INSUFFICIENT_DATA statuses.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .score_schema import ScoreCard, ScoreStatus


@dataclass(frozen=True)
class ThesisPlainEnglishSummary:
    headline: str
    quality_label: str
    valuation_label: str
    risk_label: str
    momentum_label: str
    data_label: str
    caveats: list[str]


def build_thesis_plain_english(scorecard: ScoreCard | dict[str, Any]) -> dict[str, Any]:
    """Translate a thesis scorecard into deterministic plain-English labels."""
    summary = _build_summary(_as_dict(scorecard))
    return {
        "headline": summary.headline,
        "quality_label": summary.quality_label,
        "valuation_label": summary.valuation_label,
        "risk_label": summary.risk_label,
        "momentum_label": summary.momentum_label,
        "data_label": summary.data_label,
        "caveats": summary.caveats,
    }


def _build_summary(card: dict[str, Any]) -> ThesisPlainEnglishSummary:
    status = str(card.get("status") or "").upper()
    quality = _dim_label("quality", card.get("quality"), positive="Business quality looks strong", neutral="Business quality looks mixed", cautious="Business quality signal is limited")
    valuation = _dim_label("valuation", card.get("valuation"), positive="Valuation looks reasonable", neutral="Valuation looks balanced", cautious="Valuation signal is limited")
    risk = _dim_label("risk", card.get("risk"), positive="Balance sheet risk looks manageable", neutral="Risk profile looks mixed", cautious="Risk signal is limited")
    momentum = _dim_label("momentum", card.get("momentum"), positive="Momentum is improving", neutral="Momentum looks mixed", cautious="Momentum signal is limited")

    if status == ScoreStatus.INSUFFICIENT_DATA.value:
        return ThesisPlainEnglishSummary(
            headline="Not enough data for a reliable investment-case read",
            quality_label="Business quality data is incomplete",
            valuation_label="Valuation data is incomplete",
            risk_label="Risk data is incomplete",
            momentum_label="Momentum data is incomplete",
            data_label="Data is still incomplete",
            caveats=[
                "Use this as a directional read, not a final answer",
                "Wait for more complete data before making a high-conviction decision",
            ],
        )

    if status == ScoreStatus.PARTIAL.value:
        return ThesisPlainEnglishSummary(
            headline="Signal is mixed with partial data coverage",
            quality_label=quality,
            valuation_label=valuation,
            risk_label=risk,
            momentum_label=momentum,
            data_label="Data is still incomplete",
            caveats=[
                "Some score dimensions are based on limited coverage",
                "Use this as a directional read, not a final answer",
            ],
        )

    return ThesisPlainEnglishSummary(
        headline="Overall investment case looks constructive",
        quality_label=quality,
        valuation_label=valuation,
        risk_label=risk,
        momentum_label=momentum,
        data_label="Data coverage looks usable",
        caveats=["Use this as a directional read, not a final answer"],
    )


def _dim_label(
    _name: str,
    dim: Any,
    *,
    positive: str,
    neutral: str,
    cautious: str,
) -> str:
    if not isinstance(dim, dict):
        return cautious
    if not bool(dim.get("published", False)):
        return cautious
    score = _safe_float(dim.get("score"))
    if score is None:
        return cautious
    if score >= 70.0:
        return positive
    if score >= 50.0:
        return neutral
    return cautious


def _as_dict(scorecard: ScoreCard | dict[str, Any]) -> dict[str, Any]:
    if isinstance(scorecard, dict):
        return scorecard
    return {
        "status": scorecard.status.value,
        "quality": _subscore_to_dict(scorecard.quality),
        "valuation": _subscore_to_dict(scorecard.valuation),
        "risk": _subscore_to_dict(scorecard.risk),
        "momentum": _subscore_to_dict(scorecard.momentum),
    }


def _subscore_to_dict(subscore: Any) -> dict[str, Any]:
    return {
        "score": getattr(subscore, "score", None),
        "published": getattr(subscore, "published", False),
    }


def _safe_float(v: Any) -> float | None:
    try:
        if v is None:
            return None
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:
        return None
    return f
