"""Stage 9I — Intel context adapter v1.

Bridges the unified asset intelligence composer (Stage 9G/9H) into the Intel
card data flow. Produces a safe, serializable display-context dict for each
held card without touching the deterministic final action authority.

Architecture contracts:
  - Pure function. No IO, no DB, no LLM, no external calls.
  - Never imports or calls decide().
  - Never sets safe_for_decision=True or synthesis_ready=True.
  - Existing visible action (BUY/HOLD/TRIM/SELL) is never overridden.
    Composer output is explanatory context only.
  - ETFs use role/exposure/cost language — never stock business analysis.
  - Stocks use business/fundamental/valuation language — never ETF role language.
  - GLD/commodity trusts show commodity hedge language only.
  - Missing or weak evidence → explicit caveat, never fake confidence.
  - Returns None if no useful context can be produced.
"""
from __future__ import annotations

from typing import Any, Optional

from .asset_intelligence_composer_v1 import (
    ASSET_CLASS_COMMODITY_TRUST,
    ASSET_CLASS_CRYPTO,
    ASSET_CLASS_ETF,
    ASSET_CLASS_STOCK,
    ASSET_CLASS_UNKNOWN,
    LENS_COMMODITY_HEDGE,
    LENS_CRYPTO,
    LENS_ETF_ROLE,
    LENS_STOCK_FUNDAMENTAL,
    LENS_UNKNOWN,
    AssetIntelligenceResult,
    compose_asset_intelligence,
)

ADAPTER_VERSION = "intel_context_adapter.v1"

# ── Per-lens plain-English trigger lines ──────────────────────────────────────

_ADD_MORE_TRIGGERS: dict[str, str] = {
    LENS_STOCK_FUNDAMENTAL: (
        "Business quality and growth outlook remain intact, position is underweight "
        "its target, and evidence supports adding."
    ),
    LENS_ETF_ROLE: (
        "Target allocation for this portfolio sleeve is not yet reached and "
        "the fund's role is confirmed."
    ),
    LENS_COMMODITY_HEDGE: (
        "Hedge allocation falls below target, especially during periods of "
        "elevated inflation or market volatility."
    ),
    LENS_CRYPTO: (
        "Speculative allocation is below its limit and risk conditions are within plan."
    ),
}

_TRIM_SELL_TRIGGERS: dict[str, str] = {
    LENS_STOCK_FUNDAMENTAL: (
        "Position reaches or exceeds its target, or the business outlook deteriorates "
        "materially (weaker growth, margin pressure, or elevated risk)."
    ),
    LENS_ETF_ROLE: (
        "Position exceeds its target allocation, the fund's role duplicates another "
        "holding, or a structurally superior alternative exists."
    ),
    LENS_COMMODITY_HEDGE: (
        "Hedge position exceeds its target weight, or the inflation/risk hedging "
        "rationale no longer applies."
    ),
    LENS_CRYPTO: (
        "Speculative allocation exceeds its target or risk conditions in the "
        "broader portfolio shift materially."
    ),
}

_ASSET_CLASS_DISPLAY: dict[str, str] = {
    ASSET_CLASS_STOCK:           "Stock",
    ASSET_CLASS_ETF:             "ETF",
    ASSET_CLASS_COMMODITY_TRUST: "Commodity Hedge",
    ASSET_CLASS_CRYPTO:          "Crypto",
    ASSET_CLASS_UNKNOWN:         "Unknown",
}


# ── Public API ────────────────────────────────────────────────────────────────


def build_intel_context(
    *,
    ticker: str,
    asset_type: str,
    portfolio_fit_raw: str,
    evidence_quality_raw: str,
    existing_action: str,
    provider_outputs: Optional[dict[str, Any]] = None,
    upstream_signals: Optional[dict[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    """Build a safe display-context dict from the asset intelligence composer.

    Calls compose_asset_intelligence() with the available evidence and returns
    a serializable dict for embedding in detail_drawer_payload.

    Args:
        ticker:              Uppercase ticker symbol.
        asset_type:          Raw asset type string ('etf', 'stock', 'crypto', ...).
        portfolio_fit_raw:   FitBand value string (UNDERWEIGHT/ON_TARGET/...).
        evidence_quality_raw: AxisBand value string (THIN/OK/STRONG/SUPPRESSED).
        existing_action:     Deterministic visible action (BUY/HOLD/TRIM/SELL).
                             Preserved as-is; composer output is context only.
        provider_outputs:    Optional Stage 9F provider output dict.
        upstream_signals:    Optional upstream signal hints (is_redundant_etf etc.).

    Returns:
        dict with role_lens, why_this_action, add_more_trigger, trim_sell_trigger,
        evidence_caveat (when useful), lens_applied, asset_class_display,
        adapter_version. Returns None when no useful context can be produced
        (empty ticker, completely unknown asset with no drivers).
    """
    t = (ticker or "").strip().upper()
    if not t:
        return None

    try:
        result = compose_asset_intelligence(
            ticker=t,
            asset_type=asset_type,
            portfolio_fit=portfolio_fit_raw,
            evidence_quality=evidence_quality_raw,
            provider_outputs=provider_outputs,
            upstream_signals=upstream_signals,
        )
    except Exception:
        return None

    lens = result.lens_applied
    asset_class = result.asset_class

    # Unknown lens with no drivers produces no useful context.
    if asset_class == ASSET_CLASS_UNKNOWN and not result.decision_drivers:
        return None

    role_lens = _build_role_lens(t, result)
    why_this_action = _build_why_this_action(result)
    add_more_trigger = _ADD_MORE_TRIGGERS.get(lens, "") if lens != LENS_UNKNOWN else ""
    trim_sell_trigger = _TRIM_SELL_TRIGGERS.get(lens, "") if lens != LENS_UNKNOWN else ""
    evidence_caveat = _build_evidence_caveat(result, evidence_quality_raw)

    return {
        "role_lens":          role_lens,
        "why_this_action":    why_this_action,
        "add_more_trigger":   add_more_trigger,
        "trim_sell_trigger":  trim_sell_trigger,
        "evidence_caveat":    evidence_caveat,
        "lens_applied":       lens,
        "asset_class_display": _ASSET_CLASS_DISPLAY.get(asset_class, ""),
        "adapter_version":    ADAPTER_VERSION,
    }


# ── Internal helpers ──────────────────────────────────────────────────────────


def _build_role_lens(ticker: str, result: AssetIntelligenceResult) -> str:
    """Extract a compact role/lens description for display."""
    lens = result.lens_applied
    cls = result.etf_classification

    if lens in (LENS_ETF_ROLE, LENS_COMMODITY_HEDGE) and cls is not None:
        return cls.role_description

    if lens == LENS_STOCK_FUNDAMENTAL:
        return (
            f"{ticker}: analyzed using stock fundamental lens "
            "(business quality, growth, valuation)."
        )
    if lens == LENS_CRYPTO:
        return f"{ticker}: crypto asset — speculative position sizing applies."

    # Fallback: use first driver when available.
    if result.decision_drivers:
        return result.decision_drivers[0]
    return f"{ticker}: asset intelligence lens could not be applied."


def _build_why_this_action(result: AssetIntelligenceResult) -> str:
    """Build the why-this-action text from the composer's decision drivers.

    For ETF/commodity lenses the first driver is the role description (shown
    separately as role_lens), so action-specific context starts at index 1.
    """
    lens = result.lens_applied
    drivers = result.decision_drivers

    if not drivers:
        if result.blocked_reason:
            return f"Analysis blocked: {result.blocked_reason}"
        return ""

    # ETF/commodity: skip role description (first driver → role_lens).
    if lens in (LENS_ETF_ROLE, LENS_COMMODITY_HEDGE) and len(drivers) > 1:
        action_drivers = drivers[1:]
    else:
        action_drivers = drivers

    # Take up to 3 drivers; join as clean sentences.
    selected = [d.strip().rstrip(".") for d in action_drivers[:3] if d and d.strip()]
    return ". ".join(selected) + "." if selected else ""


def _build_evidence_caveat(
    result: AssetIntelligenceResult,
    evidence_quality_raw: str,
) -> Optional[str]:
    """Return a caveat string when evidence is blocked, weak, or missing.

    Returns None when evidence is adequate (no caveat needed).
    """
    if result.blocked_reason:
        return (
            "Limited data: confidence in this view is lower than usual. "
            "Analysis will improve as more evidence becomes available."
        )
    eq = (evidence_quality_raw or "").upper()
    if eq in ("THIN", "SUPPRESSED"):
        return (
            "Evidence is partial — this view may update as financial data, "
            "filings, and market signals become available."
        )
    return None
