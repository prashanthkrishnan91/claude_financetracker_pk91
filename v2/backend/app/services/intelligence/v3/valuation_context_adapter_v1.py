"""Phase 13 — Valuation Context Readiness Adapter v1.

Governed backend-only readiness classifier for the valuation context lane.

Purpose:
    Classifies each portfolio ticker into a valuation-readiness status using
    the Phase 10 Evidence Source Registry governance gate for
    valuation_ratio_computed_v1 and the Phase 9 SEC fundamentals readiness.

    This phase is READINESS-ONLY. It does NOT produce PriceBand contributions
    and does NOT change DecisionInputV3.price_context. Its purpose is to
    establish whether a ticker has the prerequisite evidence (SEC fundamentals +
    market price/position availability) for future valuation ratio computation.

    Actual PriceBand contributions require a future phase that computes
    validated valuation ratios from price + fundamentals with sector/context
    guardrails to avoid fake precision.

Governance gate:
    The adapter verifies that valuation_ratio_computed_v1 in the Phase 10 registry:
      - has lane == VALUATION_CONTEXT
      - has trust_tier == SECONDARY_COMPUTED
      - has numeric_authority == True
      - has decision_input_eligible == True
      - has explanation_only == False
      - has lifecycle_status in the Phase 13 approved set (PLANNED or ACTIVE)
    If any check fails, the adapter produces GOVERNANCE_BLOCKED for all tickers.

Readiness statuses (no PriceBand contribution for any status):
    READY_FOR_FUTURE_VALUATION      — SEC READY + price/position available.
                                      Prerequisite evidence is sufficient for
                                      a future valuation ratio computation phase.
    PARTIAL_FOR_FUTURE_VALUATION    — SEC PARTIAL + price/position available.
                                      Limited evidence; future ratio computation
                                      would be degraded.
    SUPPRESSED_MISSING_PRICE_OR_POSITION
                                    — No market price or position data found.
                                      Cannot compute any valuation signal.
    SUPPRESSED_MISSING_FUNDAMENTALS — No SEC fundamentals (BLOCKED / unavailable).
                                      Cannot compute any valuation signal.
    SUPPRESSED_NON_COMPANY          — ETF / fund / crypto ticker.
                                      Company valuation-ratio logic must not apply.
    SUPPRESSED_CONFLICTING_OR_STALE — Reserved: conflicting or stale evidence.
                                      (Currently unused — reserved for future use.)
    GOVERNANCE_BLOCKED              — Registry gate failed. No signal for any ticker.

price_context is NEVER changed by this adapter:
    apply_valuation_context_to_decision_input() only records readiness in
    source_signal_summary. It does NOT modify DecisionInputV3.price_context.
    price_context_contribution is None for all statuses.

Architecture invariants (non-negotiable):
    - NEVER imports or calls decide() from decision_policy_v1.
    - NEVER imports IntelV3Service, recommendation_engine, or any frontend path.
    - NEVER writes to any DB table.
    - NEVER calls any SEC provider, LLM, or external service.
    - NEVER returns raw metric values, metric key names, source URLs, price targets.
    - NEVER sets price_context_contribution to a non-None PriceBand value.
    - NEVER changes DecisionInputV3.price_context.
    - NEVER produces action drift (HOLD→BUY) from Phase 13 signals.
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
_ACCEPTABLE_LIFECYCLE_STATUSES = frozenset({
    LifecycleStatus.PLANNED,
    LifecycleStatus.ACTIVE,
})

# Category keywords that identify non-company / non-equity tickers.
_NON_COMPANY_CATEGORY_KEYWORDS: frozenset[str] = frozenset({
    "etf", "fund", "index", "crypto", "digital asset",
})

# Well-known crypto tickers — always non-company regardless of category label.
_KNOWN_CRYPTO_TICKERS: frozenset[str] = frozenset({
    "BTC", "ETH", "XRP", "SOL", "BNB", "ADA", "DOGE",
})


# ── Readiness status enum ─────────────────────────────────────────────────────


class ValuationSignalStatus(str, Enum):
    """Readiness status for future valuation ratio computation.

    All statuses have price_context_contribution=None.
    None of these statuses change DecisionInputV3.price_context.
    """
    READY_FOR_FUTURE_VALUATION = "READY_FOR_FUTURE_VALUATION"
    PARTIAL_FOR_FUTURE_VALUATION = "PARTIAL_FOR_FUTURE_VALUATION"
    SUPPRESSED_MISSING_PRICE_OR_POSITION = "SUPPRESSED_MISSING_PRICE_OR_POSITION"
    SUPPRESSED_MISSING_FUNDAMENTALS = "SUPPRESSED_MISSING_FUNDAMENTALS"
    SUPPRESSED_NON_COMPANY = "SUPPRESSED_NON_COMPANY"
    SUPPRESSED_CONFLICTING_OR_STALE = "SUPPRESSED_CONFLICTING_OR_STALE"
    GOVERNANCE_BLOCKED = "GOVERNANCE_BLOCKED"


# ── Output contract ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ValuationContextSignal:
    """Typed readiness output contract for Phase 13 Valuation Context Adapter v1.

    Represents the Phase 10-governed readiness classification for a single ticker.
    This phase is readiness-only — no PriceBand contributions are made.

    Invariants:
        price_context_contribution is ALWAYS None for all statuses.
        This signal NEVER changes DecisionInputV3.price_context.
        degraded is True only for PARTIAL_FOR_FUTURE_VALUATION status.
        source_id is always valuation_ratio_computed_v1.
        adapter_version is always phase13_v1.

    Forbidden (never present in any field):
        - raw metric values or metric key names (pe_ratio, pb_ratio, etc.)
        - raw source URLs or excerpts
        - price targets or fair-value estimates
        - Buy / Hold / Trim / Sell signals
        - user-facing UI copy
    """
    ticker: str
    status: ValuationSignalStatus
    governance_gate_passed: bool
    price_context_contribution: None                # Always None — readiness-only phase
    degraded: bool                                  # True only for PARTIAL status
    suppression_reason: Optional[str]
    source_id: str = field(default=_GOVERNED_SOURCE_ID)
    adapter_version: str = field(default=VALUATION_CONTEXT_ADAPTER_V1_CONTRACT_VERSION)


# ── Governance gate ───────────────────────────────────────────────────────────


def check_governance_gate(registry: Optional[dict] = None) -> tuple[bool, str]:
    """Validate Phase 10 registry governance gate for valuation_ratio_computed_v1.

    Returns (passed: bool, reason: str).

    Checks all required governance fields for Phase 13 readiness eligibility:
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
    """Return True for ETF / fund / crypto tickers that must not use company valuation logic."""
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
    """Return the SEC readiness status string for a single ticker."""
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


# ── Core readiness signal builder ─────────────────────────────────────────────


def build_valuation_context_signal(
    ticker: str,
    category: Optional[str],
    sec_readiness: Optional["SecMetricEvidenceReadinessResult"],
    has_market_price: bool,
    registry: Optional[dict] = None,
) -> ValuationContextSignal:
    """Build a ValuationContextSignal readiness classification for one ticker.

    Pure, deterministic — no IO, no LLM, no DB.

    This function classifies whether a ticker has the prerequisite evidence
    for future valuation ratio computation. It does NOT produce a PriceBand
    contribution and does NOT affect DecisionInputV3.price_context.

    Args:
        ticker:           Portfolio ticker (uppercase, e.g. "AAPL").
        category:         Asset category string (e.g. "stock", "etf", "crypto").
        sec_readiness:    Phase 9 SecMetricEvidenceReadinessResult. None = no data.
        has_market_price: True if a market price / position is available for ticker.
        registry:         Phase 10 EVIDENCE_SOURCE_REGISTRY (default: module-level).

    Returns:
        ValuationContextSignal — frozen, readiness-only, price_context_contribution=None.
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

    # Market price / position data is required for any future valuation signal.
    if not has_market_price:
        return ValuationContextSignal(
            ticker=ticker,
            status=ValuationSignalStatus.SUPPRESSED_MISSING_PRICE_OR_POSITION,
            governance_gate_passed=True,
            price_context_contribution=None,
            degraded=False,
            suppression_reason="no_market_price_or_position_available",
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
            status=ValuationSignalStatus.READY_FOR_FUTURE_VALUATION,
            governance_gate_passed=True,
            price_context_contribution=None,
            degraded=False,
            suppression_reason=None,
        )

    if readiness_status == READINESS_STATUS_PARTIAL:
        return ValuationContextSignal(
            ticker=ticker,
            status=ValuationSignalStatus.PARTIAL_FOR_FUTURE_VALUATION,
            governance_gate_passed=True,
            price_context_contribution=None,
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


# ── Record-only apply function ────────────────────────────────────────────────


def apply_valuation_context_to_decision_input(
    inp: "DecisionInputV3",
    signal: ValuationContextSignal,
) -> None:
    """Record ValuationContextSignal readiness in DecisionInputV3.source_signal_summary.

    This function is RECORD-ONLY. It does NOT change inp.price_context.
    Phase 13 is readiness/diagnostics-only. PriceBand contributions are
    deferred to a future phase that computes validated valuation ratios.

    Records aggregate readiness info in inp.source_signal_summary under key
    "valuation_context_lane" — no raw metric keys, no price values.

    Pure function — no IO, no LLM, no DB.
    """
    inp.source_signal_summary["valuation_context_lane"] = {
        "status": signal.status.value,
        "readiness_only": True,
        "price_context_contribution": None,
        "price_context_unchanged": True,
        "degraded": signal.degraded,
        "suppression_reason": signal.suppression_reason,
        "source_id": signal.source_id,
        "adapter_version": signal.adapter_version,
    }
    # Explicitly: inp.price_context is NOT modified here.
