"""Phase 14F — PriceBand Visible Context Scaffold v1.

GOVERNANCE STATUS: Phase 14F — hidden scaffold only. Disabled by default.
NOT wired to any route, snapshot, frontend, or visible path.

This module composes Phase 14D shadow diagnostic output with the Phase 14E
visible-language translator to produce a future-renderable but currently
hidden internal context object.

Hard invariants (non-negotiable):
    - No IO, no DB, no provider, no LLM.
    - NEVER imported by decision_policy_v1, snapshot_builder, intel_v3_service,
      intel_v3_snapshots, or any serializer that feeds the frontend response.
    - NEVER imports DecisionInputV3, PriceBand (decision_contracts enum), decide(),
      or run_v3.
    - NEVER emits a target price, fair value, intrinsic value, buy_below, or
      sell_above value.
    - NEVER exposes raw EPS, raw price, raw earnings-yield numbers, bucket enum
      values, threshold values, or unavailable_reason technical codes.
    - negative_eps is never display-eligible; must not map to favorable/cheap.
    - unavailable signals are never display-eligible.
    - low confidence cannot produce renderable visible context.
    - unusually_cheap must include an explicit quality/risk caution.
    - All visible text must be plain-English, beginner-friendly, and digit-free.
    - decision_authority is always False.
    - decision_impact is always "none".
    - supporting_context_only is always True.
    - no_target_price_emitted is always True.
    - no_fair_value_emitted is always True.
    - enabled=True is only for tests/scaffold validation, never runtime wiring.
"""
from __future__ import annotations

from dataclasses import dataclass

from .priceband_shadow_policy_v1 import (
    CONFIDENCE_LOW,
    VALUATION_SIGNAL_NEGATIVE_EPS,
    VALUATION_SIGNAL_UNAVAILABLE,
    PriceBandShadowDiagnostic,
)
from .priceband_visible_language_v1 import translate_signal_to_visible

VISIBLE_CONTEXT_CONTRACT_VERSION: str = "phase14f_priceband_visible_context_v1"

_CONTEXT_KIND: str = "valuation_context"

_SOURCE_BASIS: str = (
    "Latest annual earnings compared to current price "
    "under a static broad-market policy table"
)

_LIMITATION_TEXT: str = (
    "FY-only annual earnings (no trailing twelve months); "
    "static broad-market policy (no sector-specific bands); "
    "supporting context only — does not determine Buy, Hold, Trim, or Sell; "
    "not a fair-value estimate; "
    "not a price target"
)

_CONFIDENCE_NOTES: dict[str, str] = {
    "high": (
        "Based on source-linked diluted annual earnings "
        "with sector data available"
    ),
    "medium": (
        "Based on source-linked annual earnings; "
        "some inputs used a broad-market fallback"
    ),
}

_BLOCKED_REASON_DISABLED: str = (
    "Valuation context is disabled by configuration"
)
_BLOCKED_REASON_UNAVAILABLE: str = (
    "Valuation inputs are missing or stale; context cannot be displayed"
)
_BLOCKED_REASON_NEGATIVE_EPS: str = (
    "Company has negative earnings; valuation context is not applicable"
)
_BLOCKED_REASON_LOW_CONFIDENCE: str = (
    "Valuation confidence is insufficient for display"
)


@dataclass(frozen=True)
class PriceBandVisibleContext:
    """Hidden internal valuation context for future rendering.

    Forbidden fields (enforced by invariants and tests):
        - No raw valuation_signal enum value.
        - No earnings_yield_bucket enum value.
        - No raw EPS, price, or earnings-yield number.
        - No target_price, fair_value, intrinsic_value field.
        - No buy_below, sell_above field.
        - No action authority field.
    """
    contract_version: str
    context_kind: str
    enabled: bool
    should_render: bool
    supporting_context_only: bool
    decision_authority: bool
    decision_impact: str
    visible_text: str | None
    confidence_note: str | None
    limitation_text: str
    blocked_reason: str | None
    source_basis: str
    no_target_price_emitted: bool
    no_fair_value_emitted: bool


def build_visible_context(
    *,
    enabled: bool,
    diagnostic: PriceBandShadowDiagnostic,
) -> PriceBandVisibleContext:
    """Build a hidden valuation context object from a Phase 14D shadow diagnostic.

    Args:
        enabled:    Must be False in production. Set True only in tests or
                    scaffold validation.
        diagnostic: One ticker's Phase 14D PriceBandShadowDiagnostic.

    Returns:
        PriceBandVisibleContext — deterministic, no IO, no side effects.
        should_render is always False when enabled is False.
        should_render is False for unavailable, negative_eps, or low-confidence
        signals even when enabled is True.
    """
    if not enabled:
        return _not_rendered(enabled=False, blocked_reason=_BLOCKED_REASON_DISABLED)

    signal = diagnostic.valuation_signal
    confidence = diagnostic.valuation_confidence

    if signal == VALUATION_SIGNAL_UNAVAILABLE:
        return _not_rendered(enabled=True, blocked_reason=_BLOCKED_REASON_UNAVAILABLE)

    if signal == VALUATION_SIGNAL_NEGATIVE_EPS:
        return _not_rendered(enabled=True, blocked_reason=_BLOCKED_REASON_NEGATIVE_EPS)

    if confidence == CONFIDENCE_LOW:
        return _not_rendered(enabled=True, blocked_reason=_BLOCKED_REASON_LOW_CONFIDENCE)

    translation = translate_signal_to_visible(signal)
    confidence_note = _CONFIDENCE_NOTES.get(confidence)

    return PriceBandVisibleContext(
        contract_version=VISIBLE_CONTEXT_CONTRACT_VERSION,
        context_kind=_CONTEXT_KIND,
        enabled=True,
        should_render=True,
        supporting_context_only=True,
        decision_authority=False,
        decision_impact="none",
        visible_text=translation.visible_text,
        confidence_note=confidence_note,
        limitation_text=_LIMITATION_TEXT,
        blocked_reason=None,
        source_basis=_SOURCE_BASIS,
        no_target_price_emitted=True,
        no_fair_value_emitted=True,
    )


def _not_rendered(*, enabled: bool, blocked_reason: str) -> PriceBandVisibleContext:
    return PriceBandVisibleContext(
        contract_version=VISIBLE_CONTEXT_CONTRACT_VERSION,
        context_kind=_CONTEXT_KIND,
        enabled=enabled,
        should_render=False,
        supporting_context_only=True,
        decision_authority=False,
        decision_impact="none",
        visible_text=None,
        confidence_note=None,
        limitation_text=_LIMITATION_TEXT,
        blocked_reason=blocked_reason,
        source_basis=_SOURCE_BASIS,
        no_target_price_emitted=True,
        no_fair_value_emitted=True,
    )
