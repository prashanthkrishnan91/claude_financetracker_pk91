"""Phase 13 — Valuation Context Adapter v1.

Governed backend-only bridge from Phase 9 SEC metric evidence readiness +
portfolio price availability into Intel v3 DecisionInputV3 price_context axis.

Purpose:
    Translates available evidence (SEC fundamentals readiness + market price
    availability) into a narrow, typed price-context contribution for
    DecisionInputV3, using the Phase 10 Evidence Source Registry as an
    explicit governance gate for valuation_ratio_computed_v1.

Governance gate:
    The adapter verifies that valuation_ratio_computed_v1 in the Phase 10 registry:
      - has lane == VALUATION_CONTEXT
      - has trust_tier == SECONDARY_COMPUTED
      - has numeric_authority == True
      - has decision_input_eligible == True
      - has explanation_only == False
      - has lifecycle_status in the Phase 13 approved set (PLANNED or ACTIVE)
    If any check fails, the adapter produces GOVERNANCE_BLOCKED for all tickers
    and leaves price_context unchanged.

Signal statuses:
    READY                           — SEC READY + market price available
                                      → PriceBand.FAIR contribution.
    PARTIAL                         — SEC PARTIAL + market price available
                                      → PriceBand.FAIR contribution (degraded).
    SUPPRESSED_MISSING_PRICE        — No market price data for ticker.
                                      No contribution.
    SUPPRESSED_MISSING_FUNDAMENTALS — SEC BLOCKED / unavailable for ticker.
                                      No contribution.
    SUPPRESSED_NON_COMPANY          — ETF / fund / crypto ticker.
                                      Company valuation logic must not apply.
    SUPPRESSED_CONFLICTING_OR_STALE — Reserved: conflicting or stale evidence.
                                      No contribution (currently unused).
    GOVERNANCE_BLOCKED              — Registry gate failed.
                                      No contribution for any ticker.

Merge rule (apply_valuation_context_to_decision_input):
    Upgrade-only: SUPPRESSED → FAIR when contribution is available.
        SUPPRESSED + FAIR  → FAIR   (upgrade)
        FAIR       + FAIR  → FAIR   (no change)
        CHEAP      + FAIR  → CHEAP  (no downgrade — keep stronger existing signal)
        FULL       + FAIR  → FULL   (no downgrade)
        EXPENSIVE  + FAIR  → EXPENSIVE (no downgrade)
    PARTIAL contribution: same merge rule, but degraded=True is recorded.

Product rules (non-negotiable):
    - Valuation informs PriceBand but must not become a prediction oracle.
    - No price targets.  No fair-value estimates.
    - Adapter may only contribute PriceBand.FAIR — never CHEAP, FULL, or EXPENSIVE.
    - If evidence is missing/stale/weak/conflicting, suppress or degrade the lane.
    - ETF/fund/crypto tickers are always SUPPRESSED_NON_COMPANY.
    - No single valuation signal drives an aggressive action alone.
    - Deterministic decision_policy_v1 remains the only Buy/Hold/Trim/Sell authority.

Architecture invariants (non-negotiable):
    - NEVER imports or calls decide() from decision_policy_v1.
    - NEVER imports IntelV3Service, recommendation_engine, or any frontend path.
    - NEVER writes to any DB table.
    - NEVER calls any SEC provider, LLM, or external service.
    - NEVER returns raw metric values, metric key names, source URLs, price targets.
    - NEVER emits raw valuation keys (pe_ratio, pb_ratio, ev_ebitda, etc.) in output.
    - NEVER produces PriceBand.CHEAP / FULL / EXPENSIVE on its own.
    - NEVER sets safe_for_decision=True.
    - ETF / fund / crypto → always SUPPRESSED_NON_COMPANY.
    - This module is pure — no IO, no DB, no LLM.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..research_workers.sec_metric_evidence_readiness_adapter import (
        SecMetricEvidenceReadinessResult,
    )
    from .decision_contracts import DecisionInputV3

from .decision_contracts import PriceBand
from .evidence_source_registry import (
    EVIDENCE_SOURCE_REGISTRY,
    EvidenceLane,
    LifecycleStatus,
    TrustTier,
)

VALUATION_CONTEXT_ADAPTER_V1_CONTRACT_VERSION = "phase13_v1"

# The sole source governed by this adapter.
_GOVERNED_SOURCE_ID = "valuation_ratio_computed_v1"

# Phase 13 accepts PLANNED as well as ACTIVE.
# PLANNED is the current registry state; this adapter is the first
# consumption step for valuation_ratio_computed_v1.
_ACCEPTABLE_LIFECYCLE_STATUSES = frozenset({
    LifecycleStatus.PLANNED,
    LifecycleStatus.ACTIVE,
})

# Category keywords that identify non-company / non-equity tickers.
# These tickers must not use company valuation-ratio logic.
_NON_COMPANY_CATEGORY_KEYWORDS: frozenset[str] = frozenset({
    "etf", "fund", "index", "crypto", "digital asset",
})

# Well-known crypto tickers — always non-company regardless of category label.
_KNOWN_CRYPTO_TICKERS: frozenset[str] = frozenset({
    "BTC", "ETH", "XRP", "SOL", "BNB", "ADA", "DOGE",
})


# ── Signal status enum ────────────────────────────────────────────────────────


class ValuationSignalStatus(str, Enum):
    """Status of the valuation context signal for a single ticker.

    READY and PARTIAL produce a PriceBand.FAIR contribution.
    All SUPPRESSED_* and GOVERNANCE_BLOCKED produce no contribution.
    """
    READY = "READY"
    PARTIAL = "PARTIAL"
    SUPPRESSED_MISSING_PRICE = "SUPPRESSED_MISSING_PRICE"
    SUPPRESSED_MISSING_FUNDAMENTALS = "SUPPRESSED_MISSING_FUNDAMENTALS"
    SUPPRESSED_NON_COMPANY = "SUPPRESSED_NON_COMPANY"
    SUPPRESSED_CONFLICTING_OR_STALE = "SUPPRESSED_CONFLICTING_OR_STALE"
    GOVERNANCE_BLOCKED = "GOVERNANCE_BLOCKED"


# ── Output contract ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ValuationContextSignal:
    """Typed output contract for Phase 13 Valuation Context Adapter v1.

    Represents the Phase 10-governed price-context contribution from
    valuation_ratio_computed_v1 for a single ticker.

    Forbidden (never present in any field):
        - raw metric values or metric key names (pe_ratio, pb_ratio, etc.)
        - raw source URLs or excerpts
        - raw DB rows or structured payloads
        - price targets or fair-value estimates
        - Buy / Hold / Trim / Sell signals
        - user-facing UI copy

    Invariants:
        price_context_contribution is None when status is not READY or PARTIAL.
        price_context_contribution is PriceBand.FAIR for READY and PARTIAL.
        price_context_contribution is NEVER CHEAP, FULL, or EXPENSIVE.
        degraded is True only for PARTIAL tickers.
        source_id is always valuation_ratio_computed_v1.
        adapter_version is always phase13_v1.
    """
    ticker: str
    status: ValuationSignalStatus
    governance_gate_passed: bool
    price_context_contribution: Optional[PriceBand]   # None or PriceBand.FAIR only
    degraded: bool                                      # True only for PARTIAL status
    suppression_reason: Optional[str]
    source_id: str = field(default=_GOVERNED_SOURCE_ID)
    adapter_version: str = field(default=VALUATION_CONTEXT_ADAPTER_V1_CONTRACT_VERSION)


# ── Governance gate ───────────────────────────────────────────────────────────


def check_governance_gate(registry: Optional[dict] = None) -> tuple[bool, str]:
    """Validate Phase 10 registry governance gate for valuation_ratio_computed_v1.

    Returns (passed: bool, reason: str).

    Checks all required governance fields for Phase 13 consumption eligibility:
      - source_id found in registry
      - lane == VALUATION_CONTEXT
      - trust_tier == SECONDARY_COMPUTED
      - numeric_authority == True
      - decision_input_eligible == True
      - explanation_only == False
      - lifecycle_status in Phase 13 approved set (PLANNED or ACTIVE)

    Evaluated once per run — not per ticker.
    """
    if registry is None:
        registry = EVIDENCE_SOURCE_REGISTRY

    source = registry.get(_GOVERNED_SOURCE_ID)
    if source is None:
        return False, f"source_not_found:{_GOVERNED_SOURCE_ID}"

    if source.lane != EvidenceLane.VALUATION_CONTEXT:
        return False, f"wrong_lane:{source.lane.value}"

    if source.trust_tier != TrustTier.SECONDARY_COMPUTED:
        return False, f"wrong_trust_tier:{source.trust_tier.value}"

    if not source.numeric_authority:
        return False, "numeric_authority_false"

    if not source.decision_input_eligible:
        return False, "decision_input_eligible_false"

    if source.explanation_only:
        return False, "explanation_only_true:unsupported_source"

    if source.lifecycle_status not in _ACCEPTABLE_LIFECYCLE_STATUSES:
        return False, f"lifecycle_status_not_acceptable:{source.lifecycle_status.value}"

    return True, "governance_gate_passed"


# ── Non-company classification ─────────────────────────────────────────────────


def _is_non_company_ticker(ticker: str, category: Optional[str]) -> bool:
    """Return True for ETF / fund / crypto tickers that must not use company valuation logic.

    Checks both the ticker symbol (for well-known crypto) and the category
    string (for ETFs, funds, indexes).
    """
    ticker_up = ticker.upper()
    if ticker_up in _KNOWN_CRYPTO_TICKERS:
        return True
    cat_low = (category or "").lower()
    return any(kw in cat_low for kw in _NON_COMPANY_CATEGORY_KEYWORDS)


# ── Readiness classification ──────────────────────────────────────────────────


def _classify_readiness_status(
    ticker: str,
    readiness_result: "SecMetricEvidenceReadinessResult",
) -> Optional[str]:
    """Return the SEC readiness status string for a single ticker.

    Returns one of: READY, PARTIAL, BLOCKED, SKIPPED_NON_COMPANY, or None
    (ticker not found in readiness result).
    """
    from ..research_workers.sec_metric_evidence_readiness_adapter import (
        READINESS_STATUS_READY,
        READINESS_STATUS_PARTIAL,
        READINESS_STATUS_BLOCKED,
        READINESS_STATUS_SKIPPED_NON_COMPANY,
    )

    if ticker in readiness_result.ready_tickers:
        return READINESS_STATUS_READY

    if ticker in readiness_result.partial_tickers_with_missing_groups:
        return READINESS_STATUS_PARTIAL

    if ticker in readiness_result.blocked_tickers_with_reason:
        return READINESS_STATUS_BLOCKED

    for tickers in readiness_result.skipped_tickers_by_reason.values():
        if ticker in tickers:
            return READINESS_STATUS_SKIPPED_NON_COMPANY

    return None


# ── Core signal builder ───────────────────────────────────────────────────────


def build_valuation_context_signal(
    ticker: str,
    category: Optional[str],
    sec_readiness: Optional["SecMetricEvidenceReadinessResult"],
    has_market_price: bool,
    registry: Optional[dict] = None,
) -> ValuationContextSignal:
    """Build a ValuationContextSignal for one ticker.

    Pure, deterministic — no IO, no LLM, no DB.

    Steps:
      1. Check governance gate (caller may cache the result).
      2. Check ETF / fund / crypto exclusion.
      3. Check market price availability.
      4. Check SEC fundamentals readiness.
      5. Map combined evidence to signal status + coarse PriceBand contribution.

    Args:
        ticker:           Portfolio ticker (uppercase, e.g. "AAPL").
        category:         Asset category string (e.g. "stock", "etf", "crypto").
        sec_readiness:    Phase 9 SecMetricEvidenceReadinessResult. None = no data.
        has_market_price: True if a current market price is available for this ticker.
        registry:         Phase 10 EVIDENCE_SOURCE_REGISTRY (default: module-level).

    Returns:
        ValuationContextSignal — frozen, aggregate-safe, no raw metric values.
    """
    from ..research_workers.sec_metric_evidence_readiness_adapter import (
        READINESS_STATUS_READY,
        READINESS_STATUS_PARTIAL,
        READINESS_STATUS_BLOCKED,
        READINESS_STATUS_SKIPPED_NON_COMPANY,
    )

    gate_passed, gate_reason = check_governance_gate(registry)
    if not gate_passed:
        return ValuationContextSignal(
            ticker=ticker,
            status=ValuationSignalStatus.GOVERNANCE_BLOCKED,
            governance_gate_passed=False,
            price_context_contribution=None,
            degraded=False,
            suppression_reason=f"governance_gate_failed:{gate_reason}",
        )

    # ETF / fund / crypto tickers must not use company valuation-ratio logic.
    if _is_non_company_ticker(ticker, category):
        return ValuationContextSignal(
            ticker=ticker,
            status=ValuationSignalStatus.SUPPRESSED_NON_COMPANY,
            governance_gate_passed=True,
            price_context_contribution=None,
            degraded=False,
            suppression_reason="non_company_ticker:etf_fund_crypto_excluded",
        )

    # Market price is required for any valuation signal.
    if not has_market_price:
        return ValuationContextSignal(
            ticker=ticker,
            status=ValuationSignalStatus.SUPPRESSED_MISSING_PRICE,
            governance_gate_passed=True,
            price_context_contribution=None,
            degraded=False,
            suppression_reason="no_market_price_available",
        )

    # SEC fundamentals readiness is required.
    if sec_readiness is None:
        return ValuationContextSignal(
            ticker=ticker,
            status=ValuationSignalStatus.SUPPRESSED_MISSING_FUNDAMENTALS,
            governance_gate_passed=True,
            price_context_contribution=None,
            degraded=False,
            suppression_reason="sec_readiness_unavailable",
        )

    # Classify ticker in SEC readiness result.
    readiness_status = _classify_readiness_status(ticker, sec_readiness)

    if readiness_status == READINESS_STATUS_SKIPPED_NON_COMPANY:
        return ValuationContextSignal(
            ticker=ticker,
            status=ValuationSignalStatus.SUPPRESSED_NON_COMPANY,
            governance_gate_passed=True,
            price_context_contribution=None,
            degraded=False,
            suppression_reason="skipped_non_company:sec_readiness_confirms_non_company",
        )

    if readiness_status == READINESS_STATUS_BLOCKED:
        return ValuationContextSignal(
            ticker=ticker,
            status=ValuationSignalStatus.SUPPRESSED_MISSING_FUNDAMENTALS,
            governance_gate_passed=True,
            price_context_contribution=None,
            degraded=False,
            suppression_reason="sec_readiness_blocked:no_source_linked_sec_fundamentals",
        )

    if readiness_status is None:
        return ValuationContextSignal(
            ticker=ticker,
            status=ValuationSignalStatus.SUPPRESSED_MISSING_FUNDAMENTALS,
            governance_gate_passed=True,
            price_context_contribution=None,
            degraded=False,
            suppression_reason="ticker_not_in_sec_readiness:no_fundamentals_data",
        )

    if readiness_status == READINESS_STATUS_READY:
        return ValuationContextSignal(
            ticker=ticker,
            status=ValuationSignalStatus.READY,
            governance_gate_passed=True,
            price_context_contribution=PriceBand.FAIR,
            degraded=False,
            suppression_reason=None,
        )

    if readiness_status == READINESS_STATUS_PARTIAL:
        return ValuationContextSignal(
            ticker=ticker,
            status=ValuationSignalStatus.PARTIAL,
            governance_gate_passed=True,
            price_context_contribution=PriceBand.FAIR,
            degraded=True,
            suppression_reason=None,
        )

    # Unrecognized readiness status — suppress safely.
    return ValuationContextSignal(
        ticker=ticker,
        status=ValuationSignalStatus.SUPPRESSED_MISSING_FUNDAMENTALS,
        governance_gate_passed=True,
        price_context_contribution=None,
        degraded=False,
        suppression_reason=f"unrecognized_readiness_status:{readiness_status}",
    )


# ── Merge / apply ─────────────────────────────────────────────────────────────


def apply_valuation_context_to_decision_input(
    inp: "DecisionInputV3",
    signal: ValuationContextSignal,
) -> None:
    """Apply ValuationContextSignal to DecisionInputV3 price_context in-place.

    Upgrade-only: upgrades price_context from SUPPRESSED to FAIR when the
    signal provides a FAIR contribution. Never downgrades an existing
    non-SUPPRESSED value.

    Records contribution in inp.source_signal_summary under key
    "valuation_context_lane" — aggregate info only, no raw metric keys.

    If the signal has no contribution (governance blocked, SUPPRESSED_*),
    records the suppression reason in source_signal_summary but does not
    change price_context.

    Pure function — no IO, no LLM, no DB.
    """
    contribution = signal.price_context_contribution

    if contribution is None:
        # Record suppression info only.
        inp.source_signal_summary["valuation_context_lane"] = {
            "status": signal.status.value,
            "contribution": None,
            "suppression_reason": signal.suppression_reason,
            "source_id": signal.source_id,
            "adapter_version": signal.adapter_version,
        }
        return

    current = inp.price_context
    upgraded = False

    # Upgrade-only: SUPPRESSED → FAIR only.
    # CHEAP / FULL / EXPENSIVE / FAIR remain unchanged.
    if current == PriceBand.SUPPRESSED and contribution == PriceBand.FAIR:
        inp.price_context = PriceBand.FAIR
        inp.suppression_reasons.pop("price_context", None)
        upgraded = True

    inp.source_signal_summary["valuation_context_lane"] = {
        "status": signal.status.value,
        "contribution": contribution.value,
        "degraded": signal.degraded,
        "price_context_upgraded": upgraded,
        "source_id": signal.source_id,
        "adapter_version": signal.adapter_version,
    }
