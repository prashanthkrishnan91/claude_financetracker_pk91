"""Phase 14E — PriceBand Visible Language Governance Contract.

GOVERNANCE STATUS: Phase 14E — contract defined, NOT YET WIRED TO ANY VISIBLE PATH.

This module defines the durable governance contract for future visible PriceBand
use. It provides four things:

  1. Tested visible-language translations for each internal shadow label.
  2. Declaration of what must remain backend-only (never exposed to the frontend).
  3. Promotion gates Phase 14F must satisfy before any visible integration.
  4. Permitted and prohibited interaction modes between valuation and decisions.

Phase 14F recommendation: Add backend-only visible-language translator scaffolding
behind an explicit disabled flag — still no action changes, no DecisionInputV3
mutation, no visible snapshot changes.

Hard invariants (non-negotiable):
    - No IO, no DB, no provider, no LLM.
    - NEVER imported by decision_policy_v1, snapshot_builder, intel_v3_service,
      or any serializer that feeds the frontend response.
    - NEVER imports DecisionInputV3, PriceBand (decision_contracts enum), decide(),
      or run_v3.
    - NEVER emits a target price, fair value, intrinsic value, buy_below, or
      sell_above value.
    - NEVER exposes raw EPS, raw price, or raw earnings-yield numbers.
    - negative_eps MUST map to unavailable-style visible language.
    - unusually_cheap MUST carry an explicit quality/risk caution.
    - All visible text MUST be plain-English and beginner-friendly.
"""
from __future__ import annotations

from dataclasses import dataclass

from .priceband_shadow_policy_v1 import (
    VALUATION_SIGNAL_ATTRACTIVE,
    VALUATION_SIGNAL_ELEVATED,
    VALUATION_SIGNAL_EXPENSIVE,
    VALUATION_SIGNAL_NEGATIVE_EPS,
    VALUATION_SIGNAL_REASONABLE,
    VALUATION_SIGNAL_UNAVAILABLE,
    VALUATION_SIGNAL_UNUSUALLY_CHEAP,
    _ALL_VALUATION_SIGNALS,
)

GOVERNANCE_CONTRACT_VERSION: str = "phase14e_visible_language_v1"

# ── Visible-language translation table ─────────────────────────────────────
# Short, plain-English, beginner-friendly. No raw metrics. No action directives.
# No internal signal label leaked. No price targets. No fair values.
#
# APPROVED for Phase 14F integration — MUST NOT be rendered in any visible path
# until all PHASE_14F_PROMOTION_GATES below are independently verified.

_VISIBLE_TRANSLATIONS: dict[str, str] = {
    VALUATION_SIGNAL_EXPENSIVE: "Valuation looks demanding",
    VALUATION_SIGNAL_ELEVATED: "Valuation looks somewhat demanding",
    VALUATION_SIGNAL_REASONABLE: "Valuation looks reasonable",
    VALUATION_SIGNAL_ATTRACTIVE: "Valuation looks favorable",
    VALUATION_SIGNAL_UNUSUALLY_CHEAP: (
        "Valuation looks unusually low — review quality/risk first"
    ),
    VALUATION_SIGNAL_NEGATIVE_EPS: (
        "Earnings are negative; valuation signal unavailable"
    ),
    VALUATION_SIGNAL_UNAVAILABLE: "Valuation signal unavailable",
}

# Compile-time completeness check: every known signal must have a translation.
_MISSING = set(_ALL_VALUATION_SIGNALS) - set(_VISIBLE_TRANSLATIONS)
if _MISSING:  # pragma: no cover
    raise RuntimeError(
        f"priceband_visible_language_v1: missing translations for {_MISSING}"
    )


@dataclass(frozen=True)
class PriceBandVisibleTranslation:
    """Safe visible-language output for one internal valuation signal.

    Forbidden fields (invariants enforced by tests and this type):
        - No raw EPS, raw price, raw earnings-yield value.
        - No target_price, fair_value, intrinsic_value field.
        - No buy_below, sell_above field.
        - No action authority field.
    """
    internal_signal: str
    visible_text: str
    # False when signal is unavailable or negative-EPS (cannot support
    # a valuation claim); True for all classifiable signals.
    has_valuation_context: bool


def translate_signal_to_visible(internal_signal: str) -> PriceBandVisibleTranslation:
    """Translate one internal shadow label to its approved visible-language text.

    Args:
        internal_signal: One of the VALUATION_SIGNAL_* constants from
                         priceband_shadow_policy_v1. Unknown values raise.

    Returns:
        PriceBandVisibleTranslation — deterministic, no IO, no side effects.

    Raises:
        ValueError: If internal_signal is not a known valuation signal.

    GOVERNANCE: Only call from Phase 14F+ scaffolding behind an explicit
    disabled flag. Never call from snapshot_builder, decision_policy_v1,
    intel_v3_service, or any visible path.
    """
    if internal_signal not in _VISIBLE_TRANSLATIONS:
        raise ValueError(
            f"translate_signal_to_visible: unknown signal {internal_signal!r}. "
            f"Must be one of {_ALL_VALUATION_SIGNALS}."
        )
    visible_text = _VISIBLE_TRANSLATIONS[internal_signal]
    has_valuation_context = internal_signal not in (
        VALUATION_SIGNAL_UNAVAILABLE,
        VALUATION_SIGNAL_NEGATIVE_EPS,
    )
    return PriceBandVisibleTranslation(
        internal_signal=internal_signal,
        visible_text=visible_text,
        has_valuation_context=has_valuation_context,
    )


# ── What must remain backend-only ──────────────────────────────────────────
# Fields that MUST NEVER be exposed to the frontend in any Phase 14F+ work.

BACKEND_ONLY_FIELDS: tuple[str, ...] = (
    "raw valuation_signal enum value (e.g., 'unusually_cheap')",
    "raw earnings_yield_bucket enum value",
    "EPS numeric value",
    "price numeric value",
    "computed earnings yield percentage",
    "policy threshold boundaries (e.g., 2.0%, 4.0%, 6.0%, 9.0%)",
    "unavailable_reason technical codes (e.g., 'stale_price', 'zero_eps_invalid_for_valuation')",
    "per-ticker diagnostics outside cert-gated operator endpoint",
)


# ── Phase 14F promotion gates ───────────────────────────────────────────────
# ALL gates must be independently verified before Phase 14F adds any visible
# PriceBand scaffolding. No partial promotion.

PHASE_14F_PROMOTION_GATES: tuple[str, ...] = (
    "Phase 14D endpoint production validation passed (validated 2026-05-09: "
    "evaluated_company_ticker_count=19, priceband_computed_count=14, errors=[])",
    "No target_price or fair_value key anywhere in the response chain",
    "visible_language_translator tested — all 7 labels covered, no forbidden-term leakage",
    "Valuation context cannot override Buy/Hold/Trim/Sell "
    "(policy_authority_immutable: deterministic backend policy remains final)",
    "negative_eps cannot map to any cheap or favorable signal",
    "missing EPS cannot be inferred, approximated, or back-filled",
    "low confidence cannot produce strong or unqualified visible language",
    "static broad-market limitation must attach to every visible translation display",
    "snapshot contract remains backward-compatible "
    "(no existing field removed, renamed, or type-changed)",
)


# ── Permitted / prohibited interaction modes ────────────────────────────────
# Defines how PriceBand valuation context may (and may not) interact with
# visible decisions in Phase 14F and beyond.

INTERACTION_MODES_ALLOWED: tuple[str, ...] = (
    "Supporting rationale: context note displayed alongside deterministic Buy/Hold/Trim/Sell",
    "Confidence modifier: reduce displayed conviction when valuation is expensive "
    "and evidence is thin (must not change the action itself)",
    "Red-flag context: flag unusually_cheap signal when input quality is low",
    "Context note in Intel detail view (must not appear in card or list view)",
)

INTERACTION_MODES_PROHIBITED: tuple[str, ...] = (
    "Standalone action authority: valuation alone cannot determine or change "
    "Buy/Hold/Trim/Sell",
    "Price target: no numeric price value emitted to frontend",
    "Exact trade threshold: no buy_below or sell_above value",
    "Hidden override: valuation cannot silently override deterministic policy output",
    "Fair value or intrinsic value label",
    "Strong language on low-confidence or unavailable signals",
)
