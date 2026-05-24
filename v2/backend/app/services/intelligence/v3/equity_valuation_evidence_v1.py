"""Stage 9E — Equity Valuation Evidence Lane v1.

Pure, no-IO read model. Converts a Stage 9D canonical equity dataset row +
trusted price/fundamental availability signals into a conservative, versioned
valuation evidence artifact for each equity holding.

Stage 9E.1 adds optional numeric valuation input support via
EquityNumericValuationInputs from equity_numeric_valuation_inputs_v1.
When numeric_inputs are provided and numeric_inputs_ready=True:
  - valuation_numeric_inputs_in_scope=True
  - price_is_portfolio_level_proxy=False (when ticker-level confirmed)
  - valuation_ready=True

Architecture contracts (non-negotiable):
  - Pure function. No IO, no DB, no LLM, no external calls.
  - Never imports or calls decide().
  - Never produces Buy/Hold/Trim/Sell authority.
  - safe_for_decision is always False.
  - synthesis_ready is always False.
  - No raw EPS values, no raw price values, no raw SEC metric names serialized.
  - No generated fair values, no price targets, no fake precision.
  - ETF/crypto: valuation_applicable=False, valuation_ready=False always.
  - If valuation inputs are missing/weak, returns LIMITED/MISSING with reasons.
  - Does NOT infer unavailable metrics.
  - Does NOT mark safe_for_decision=True.
  - valuation_interpretation_band is UNKNOWN unless defensible from trusted
    deterministic thresholds. No thresholds defined at Stage 9E/9E.1 —
    band remains UNKNOWN.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from .canonical_equity_dataset_v1 import (
    SECTION_CASH_FLOW_FCF,
    SECTION_NET_INCOME_EPS,
    SECTION_PROFITABILITY,
    SECTION_REVENUE,
    SECTION_STATUS_AVAILABLE,
    SECTION_STATUS_PARTIAL,
    CanonicalEquityDatasetRow,
)
from .equity_numeric_valuation_inputs_v1 import (
    EquityNumericValuationInputs,
)
from .research_evidence_decision_input_adapter_v1 import (
    INSTRUMENT_CATEGORY_EQUITY,
)

VALUATION_EVIDENCE_VERSION = "equity_valuation_evidence.v1"
SKILL_PACK = "equity_valuation_evidence_v1"

# Context status values.
CONTEXT_STATUS_AVAILABLE = "AVAILABLE"
CONTEXT_STATUS_PARTIAL = "PARTIAL"
CONTEXT_STATUS_MISSING = "MISSING"

# Interpretation band values.
BAND_CHEAP = "CHEAP"
BAND_REASONABLE = "REASONABLE"
BAND_EXPENSIVE = "EXPENSIVE"
BAND_UNKNOWN = "UNKNOWN"

# Sector context: not built at Stage 9E.
_SECTOR_CONTEXT_NOT_BUILT_REASON = (
    "Sector peer context not built at Stage 9E. "
    "Sector-relative valuation requires a sector benchmark lane (future stage)."
)

# Interpretation band: no numeric EPS/price values in scope at Stage 9E.
_BAND_NOT_DERIVABLE_REASON = (
    "Valuation interpretation band cannot be derived without trusted numeric "
    "EPS and price values. Raw values are intentionally excluded from this "
    "read model to prevent fake precision. Band will be computed in a future "
    "stage when defensible deterministic thresholds are confirmed."
)

_USABLE_SECTION_STATUSES = frozenset({SECTION_STATUS_AVAILABLE, SECTION_STATUS_PARTIAL})


# ── Input readiness ────────────────────────────────────────────────────────────


@dataclass
class InputReadiness:
    """Readiness of each category of valuation input for one equity ticker.

    At Stage 9E, price_is_portfolio_level_proxy=True always (price_available
    is derived from portfolio snapshot existence, not per-ticker lookup).

    At Stage 9E.1, when EquityNumericValuationInputs is provided and
    ticker_level_confirmed=True, price_is_portfolio_level_proxy=False.
    """

    canonical_equity_dataset_safe: bool
    price_available: bool
    # False only when ticker-level price confirmation is provided via
    # EquityNumericValuationInputs with ticker_level_confirmed=True.
    # True (portfolio-level proxy) when no ticker-level signal exists.
    price_is_portfolio_level_proxy: bool
    eps_or_earnings_available: bool
    cash_flow_available: bool
    sector_context_available: bool   # always False at Stage 9E/9E.1

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical_equity_dataset_safe": self.canonical_equity_dataset_safe,
            "price_available": self.price_available,
            "price_is_portfolio_level_proxy": self.price_is_portfolio_level_proxy,
            "eps_or_earnings_available": self.eps_or_earnings_available,
            "cash_flow_available": self.cash_flow_available,
            "sector_context_available": self.sector_context_available,
        }


# ── Valuation context ──────────────────────────────────────────────────────────


@dataclass
class ValuationContext:
    """Conservative valuation context per equity ticker.

    All context statuses are derived from Stage 9D canonical dataset section
    statuses and trusted price availability. No raw financial values are stored.

    valuation_interpretation_band is always UNKNOWN at Stage 9E because actual
    EPS and price numeric values are intentionally excluded from this read model.
    """

    earnings_yield_status: str    # AVAILABLE | PARTIAL | MISSING
    pe_context_status: str        # AVAILABLE | PARTIAL | MISSING
    cash_flow_context_status: str # AVAILABLE | PARTIAL | MISSING
    growth_context_status: str    # AVAILABLE | PARTIAL | MISSING
    valuation_interpretation_band: str  # always UNKNOWN at Stage 9E

    def to_dict(self) -> dict[str, Any]:
        return {
            "earnings_yield_status": self.earnings_yield_status,
            "pe_context_status": self.pe_context_status,
            "cash_flow_context_status": self.cash_flow_context_status,
            "growth_context_status": self.growth_context_status,
            "valuation_interpretation_band": self.valuation_interpretation_band,
        }


# ── Valuation evidence row ─────────────────────────────────────────────────────


@dataclass
class EquityValuationEvidenceRow:
    """Per-ticker equity valuation evidence artifact.

    Safe for diagnostics and downstream adapters. No raw payloads, no raw
    metric keys, no fact values, no price targets, no fair values.

    synthesis_ready and safe_for_decision are always False.
    valuation_interpretation_band is always UNKNOWN at Stage 9E.
    """

    ticker: str
    asset_type: str
    valuation_applicable: bool     # True only for equity asset type
    valuation_evidence_version: str
    skill_pack: str
    generated_at: str

    source_health: list    # list[dict] — from canonical dataset source_artifacts

    input_readiness: InputReadiness
    valuation_context: ValuationContext

    # Missing/degraded reasons per valuation subsection.
    missing_reasons: dict[str, str]

    # Forward readiness gates.
    # Whether the minimum valuation inputs (canonical + ticker price + EPS) are all present.
    # Named "valuation_build" not "policy" to avoid implying decision authority.
    usable_for_future_valuation_build: bool
    valuation_ready: bool
    # Whether numeric EPS/price values are actually in scope for this read model.
    # Always False at Stage 9E — raw values are intentionally excluded to prevent
    # fake precision. valuation_interpretation_band is always UNKNOWN as a result.
    valuation_numeric_inputs_in_scope: bool = False

    # Immutable safety gates.
    synthesis_ready: bool = False    # always False
    safe_for_decision: bool = False  # always False

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "asset_type": self.asset_type,
            "valuation_applicable": self.valuation_applicable,
            "valuation_evidence_version": self.valuation_evidence_version,
            "skill_pack": self.skill_pack,
            "generated_at": self.generated_at,
            "source_health": list(self.source_health),
            "input_readiness": self.input_readiness.to_dict(),
            "valuation_context": self.valuation_context.to_dict(),
            "missing_reasons": dict(self.missing_reasons),
            "usable_for_future_valuation_build": self.usable_for_future_valuation_build,
            "valuation_ready": self.valuation_ready,
            "valuation_numeric_inputs_in_scope": self.valuation_numeric_inputs_in_scope,
            "synthesis_ready": self.synthesis_ready,
            "safe_for_decision": self.safe_for_decision,
        }


# ── Public API ─────────────────────────────────────────────────────────────────


def build_equity_valuation_evidence_row(
    *,
    canonical_row: CanonicalEquityDatasetRow,
    price_available: bool,
    numeric_inputs: Optional[EquityNumericValuationInputs] = None,
) -> EquityValuationEvidenceRow:
    """Build a valuation evidence row for one ticker from Stage 9D canonical dataset.

    Args:
        canonical_row: Stage 9D CanonicalEquityDatasetRow for this ticker.
        price_available: True when a fresh price exists for this ticker in the
            portfolio (derived from portfolio_snapshots existence in forensics).
            Used as fallback when numeric_inputs is None.
        numeric_inputs: Optional Stage 9E.1 numeric valuation input adapter result.
            When provided and numeric_inputs.numeric_inputs_ready=True:
              - valuation_numeric_inputs_in_scope=True
              - price_is_portfolio_level_proxy=False (when ticker-level confirmed)
              - valuation_ready=True
            When None, behavior is identical to Stage 9E (valuation_ready=False).

    Returns:
        EquityValuationEvidenceRow — always non-None, never raises.
        synthesis_ready=False always. safe_for_decision=False always.
        valuation_interpretation_band=UNKNOWN always (no thresholds defined).
        ETF/crypto rows: valuation_applicable=False, valuation_ready=False.
    """
    now_iso = datetime.now(timezone.utc).isoformat()

    if canonical_row.asset_type != INSTRUMENT_CATEGORY_EQUITY:
        return _build_not_applicable_row(canonical_row, now_iso)

    sections = canonical_row.operating_trends.sections

    eps_section = sections.get(SECTION_NET_INCOME_EPS)
    eps_status = eps_section.status if eps_section else None
    eps_available = eps_status in _USABLE_SECTION_STATUSES

    fcf_section = sections.get(SECTION_CASH_FLOW_FCF)
    fcf_status = fcf_section.status if fcf_section else None
    cash_flow_available = fcf_status in _USABLE_SECTION_STATUSES

    revenue_section = sections.get(SECTION_REVENUE)
    revenue_status = revenue_section.status if revenue_section else None
    revenue_available = revenue_status in _USABLE_SECTION_STATUSES

    # Determine price readiness and proxy status from numeric_inputs when available.
    if numeric_inputs is not None and numeric_inputs.asset_type == INSTRUMENT_CATEGORY_EQUITY:
        effective_price_available = (
            numeric_inputs.price_input.status in ("AVAILABLE", "PARTIAL")
        )
        price_is_proxy = not numeric_inputs.price_input.ticker_level_confirmed
        numeric_inputs_in_scope = True
    else:
        effective_price_available = price_available
        price_is_proxy = True
        numeric_inputs_in_scope = False

    input_readiness = InputReadiness(
        canonical_equity_dataset_safe=canonical_row.safe_for_equity_dataset,
        price_available=effective_price_available,
        price_is_portfolio_level_proxy=price_is_proxy,
        eps_or_earnings_available=eps_available,
        cash_flow_available=cash_flow_available,
        sector_context_available=False,
    )

    valuation_context, missing_reasons = _derive_valuation_context(
        canonical_safe=canonical_row.safe_for_equity_dataset,
        price_available=effective_price_available,
        eps_status=eps_status,
        eps_available=eps_available,
        fcf_status=fcf_status,
        cash_flow_available=cash_flow_available,
        revenue_status=revenue_status,
        revenue_available=revenue_available,
    )

    valuation_ready = _compute_valuation_ready(
        canonical_safe=canonical_row.safe_for_equity_dataset,
        price_available=effective_price_available,
        eps_available=eps_available,
        valuation_context=valuation_context,
        numeric_inputs=numeric_inputs,
    )

    # Require all three minimum inputs: canonical safe + price + EPS.
    usable_for_future_valuation_build = (
        canonical_row.safe_for_equity_dataset
        and effective_price_available
        and eps_available
    )

    source_health = [s.to_dict() for s in canonical_row.source_artifacts]

    return EquityValuationEvidenceRow(
        ticker=canonical_row.ticker,
        asset_type=canonical_row.asset_type,
        valuation_applicable=True,
        valuation_evidence_version=VALUATION_EVIDENCE_VERSION,
        skill_pack=SKILL_PACK,
        generated_at=now_iso,
        source_health=source_health,
        input_readiness=input_readiness,
        valuation_context=valuation_context,
        missing_reasons=missing_reasons,
        usable_for_future_valuation_build=usable_for_future_valuation_build,
        valuation_ready=valuation_ready,
        valuation_numeric_inputs_in_scope=numeric_inputs_in_scope,
        synthesis_ready=False,
        safe_for_decision=False,
    )


# ── Internal helpers ───────────────────────────────────────────────────────────


def _derive_valuation_context(
    *,
    canonical_safe: bool,
    price_available: bool,
    eps_status: Optional[str],
    eps_available: bool,
    fcf_status: Optional[str],
    cash_flow_available: bool,
    revenue_status: Optional[str],
    revenue_available: bool,
) -> tuple:  # (ValuationContext, dict[str, str])
    """Derive conservative valuation context from section statuses and readiness flags."""
    missing_reasons: dict[str, str] = {}

    # Earnings yield status: requires EPS section + price.
    earnings_yield_status, ey_reason = _earnings_yield_status(
        canonical_safe=canonical_safe,
        price_available=price_available,
        eps_status=eps_status,
        eps_available=eps_available,
    )
    if ey_reason:
        missing_reasons["earnings_yield"] = ey_reason

    # P/E context status: same as earnings yield (price/EPS = 1/earnings_yield).
    pe_context_status, pe_reason = _pe_context_status(
        canonical_safe=canonical_safe,
        price_available=price_available,
        eps_status=eps_status,
        eps_available=eps_available,
    )
    if pe_reason:
        missing_reasons["pe_context"] = pe_reason

    # Cash flow context status: from FCF section.
    cf_context_status, cf_reason = _cash_flow_context_status(
        canonical_safe=canonical_safe,
        fcf_status=fcf_status,
        cash_flow_available=cash_flow_available,
    )
    if cf_reason:
        missing_reasons["cash_flow_context"] = cf_reason

    # Growth context status: from revenue + net income trends.
    growth_context_status, growth_reason = _growth_context_status(
        canonical_safe=canonical_safe,
        revenue_status=revenue_status,
        revenue_available=revenue_available,
        eps_status=eps_status,
        eps_available=eps_available,
    )
    if growth_reason:
        missing_reasons["growth_context"] = growth_reason

    missing_reasons["valuation_interpretation_band"] = _BAND_NOT_DERIVABLE_REASON
    missing_reasons["sector_context"] = _SECTOR_CONTEXT_NOT_BUILT_REASON

    return (
        ValuationContext(
            earnings_yield_status=earnings_yield_status,
            pe_context_status=pe_context_status,
            cash_flow_context_status=cf_context_status,
            growth_context_status=growth_context_status,
            valuation_interpretation_band=BAND_UNKNOWN,
        ),
        missing_reasons,
    )


def _earnings_yield_status(
    *,
    canonical_safe: bool,
    price_available: bool,
    eps_status: Optional[str],
    eps_available: bool,
) -> tuple:  # (status, optional_reason)
    if not canonical_safe:
        return (
            CONTEXT_STATUS_MISSING,
            "Canonical equity dataset is not safe — SEC company facts are weak or missing.",
        )
    if not price_available:
        return (
            CONTEXT_STATUS_MISSING,
            "Price not available. Portfolio snapshot is missing for this user.",
        )
    if not eps_available:
        reason = (
            f"Earnings/EPS section is {eps_status or 'missing'} in the canonical equity dataset. "
            "A minimum of one AVAILABLE or PARTIAL net income/EPS period is required."
        )
        return CONTEXT_STATUS_MISSING, reason
    if eps_status == SECTION_STATUS_AVAILABLE:
        return CONTEXT_STATUS_AVAILABLE, None
    return CONTEXT_STATUS_PARTIAL, None


def _pe_context_status(
    *,
    canonical_safe: bool,
    price_available: bool,
    eps_status: Optional[str],
    eps_available: bool,
) -> tuple:
    return _earnings_yield_status(
        canonical_safe=canonical_safe,
        price_available=price_available,
        eps_status=eps_status,
        eps_available=eps_available,
    )


def _cash_flow_context_status(
    *,
    canonical_safe: bool,
    fcf_status: Optional[str],
    cash_flow_available: bool,
) -> tuple:
    if not canonical_safe:
        return (
            CONTEXT_STATUS_MISSING,
            "Canonical equity dataset is not safe — cash flow context cannot be derived.",
        )
    if not cash_flow_available:
        reason = (
            f"Cash flow/FCF section is {fcf_status or 'missing'} in canonical equity dataset. "
            "Operating cash flow requires COMPLETE SEC XBRL data with ≥3 annual observations."
        )
        return CONTEXT_STATUS_MISSING, reason
    if fcf_status == SECTION_STATUS_AVAILABLE:
        return CONTEXT_STATUS_AVAILABLE, None
    return CONTEXT_STATUS_PARTIAL, None


def _growth_context_status(
    *,
    canonical_safe: bool,
    revenue_status: Optional[str],
    revenue_available: bool,
    eps_status: Optional[str],
    eps_available: bool,
) -> tuple:
    if not canonical_safe:
        return (
            CONTEXT_STATUS_MISSING,
            "Canonical equity dataset is not safe — growth context cannot be derived.",
        )
    if revenue_available and eps_available:
        if revenue_status == SECTION_STATUS_AVAILABLE and eps_status == SECTION_STATUS_AVAILABLE:
            return CONTEXT_STATUS_AVAILABLE, None
        return CONTEXT_STATUS_PARTIAL, None
    if revenue_available or eps_available:
        return (
            CONTEXT_STATUS_PARTIAL,
            (
                f"Growth context is partial: revenue={revenue_status or 'missing'}, "
                f"earnings={eps_status or 'missing'}. Both sections are needed for "
                "full revenue + earnings growth context."
            ),
        )
    reason = (
        f"Both revenue ({revenue_status or 'missing'}) and earnings "
        f"({eps_status or 'missing'}) sections are unavailable. "
        "Cannot derive growth context without at least one AVAILABLE or PARTIAL section."
    )
    return CONTEXT_STATUS_MISSING, reason


def _compute_valuation_ready(
    *,
    canonical_safe: bool,
    price_available: bool,
    eps_available: bool,
    valuation_context: ValuationContext,
    numeric_inputs: Optional[EquityNumericValuationInputs] = None,
) -> bool:
    """True only when numeric inputs are confirmed ready via the Stage 9E.1 adapter.

    Without numeric_inputs (Stage 9E mode): always returns False.
    With numeric_inputs: returns numeric_inputs.numeric_inputs_ready, which requires:
      - canonical_equity_dataset_safe=True
      - ticker-level price confirmed (market_value_certified_at set, FRESH or AGING)
      - earnings/EPS input AVAILABLE or PARTIAL

    valuation_interpretation_band remains UNKNOWN regardless of valuation_ready status
    because no deterministic P/E thresholds are defined at Stage 9E.1.
    """
    if numeric_inputs is None:
        return False
    return numeric_inputs.numeric_inputs_ready


def _build_not_applicable_row(
    canonical_row: CanonicalEquityDatasetRow,
    now_iso: str,
) -> EquityValuationEvidenceRow:
    """Return a NOT_APPLICABLE evidence row for non-equity tickers."""
    reason = (
        f"Valuation evidence is not applicable for asset type '{canonical_row.asset_type}'. "
        "ETF and crypto holdings require dedicated provider lanes, not equity valuation logic."
    )
    not_applicable_context = ValuationContext(
        earnings_yield_status=CONTEXT_STATUS_MISSING,
        pe_context_status=CONTEXT_STATUS_MISSING,
        cash_flow_context_status=CONTEXT_STATUS_MISSING,
        growth_context_status=CONTEXT_STATUS_MISSING,
        valuation_interpretation_band=BAND_UNKNOWN,
    )
    not_applicable_readiness = InputReadiness(
        canonical_equity_dataset_safe=False,
        price_available=False,
        price_is_portfolio_level_proxy=False,
        eps_or_earnings_available=False,
        cash_flow_available=False,
        sector_context_available=False,
    )
    return EquityValuationEvidenceRow(
        ticker=canonical_row.ticker,
        asset_type=canonical_row.asset_type,
        valuation_applicable=False,
        valuation_evidence_version=VALUATION_EVIDENCE_VERSION,
        skill_pack=SKILL_PACK,
        generated_at=now_iso,
        source_health=[],
        input_readiness=not_applicable_readiness,
        valuation_context=not_applicable_context,
        missing_reasons={"all": reason},
        usable_for_future_valuation_build=False,
        valuation_ready=False,
        synthesis_ready=False,
        safe_for_decision=False,
    )
