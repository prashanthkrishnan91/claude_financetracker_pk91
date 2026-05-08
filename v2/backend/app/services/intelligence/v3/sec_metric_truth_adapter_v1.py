"""Phase 11 — SEC Metric Truth Adapter v1.

Governed backend-only bridge from Phase 8/9 SEC metric evidence readiness into
Intel v3 DecisionInputV3 evidence_quality axis.

Purpose:
    Translates the Phase 9 SecMetricEvidenceReadinessResult (READY / PARTIAL /
    BLOCKED / SKIPPED_NON_COMPANY) into a narrow, typed evidence-quality
    contribution for DecisionInputV3, using the Phase 10 Evidence Source Registry
    as an explicit governance gate.

Governance gate:
    The adapter verifies that sec_companyfacts_v1 in the Phase 10 registry:
      - has lane == SEC_COMPANY_FUNDAMENTALS
      - has trust_tier == PRIMARY_HARD_DATA
      - has numeric_authority == True
      - has decision_input_eligible == True
      - has explanation_only == False
      - has lifecycle_status in the Phase 11 approved set (PLANNED or ACTIVE)
    If any check fails, the adapter produces no signal — all tickers get
    evidence_quality_contribution = None and the existing axis is unchanged.

Signal rules (per ticker):
    READY               → evidence_quality_contribution = AxisBand.OK
                          Ticker has all expected metric bucket groups covered.
    PARTIAL             → evidence_quality_contribution = AxisBand.THIN (degraded)
                          Some buckets present, some missing. Contributes limited
                          support only; degraded=True is set in the output.
    BLOCKED             → evidence_quality_contribution = None
                          No source-linked SEC metric evidence. No signal.
    SKIPPED_NON_COMPANY → evidence_quality_contribution = None
                          ETF / fund / crypto ticker. SEC company logic must not apply.
    Not found           → evidence_quality_contribution = None
                          Ticker not in readiness result (treated as no data).

Merge rule (apply_sec_fundamentals_to_decision_input):
    Takes the better of current evidence_quality and the SEC contribution:
        max_rank(current, contribution)
    Only upgrades, never downgrades. This means:
        SUPPRESSED + READY(OK)   → OK   (SEC alone enables decisions)
        THIN       + READY(OK)   → OK   (SEC upgrades from thin evidence)
        OK         + READY(OK)   → OK   (no change — SEC confirms)
        STRONG     + READY(OK)   → STRONG (no change — already strong)
        SUPPRESSED + PARTIAL(THIN) → THIN  (slight improvement)
        THIN       + PARTIAL(THIN) → THIN  (no change)
        OK         + PARTIAL(THIN) → OK   (no change)
        STRONG     + PARTIAL(THIN) → STRONG (no change)
    BLOCKED / SKIPPED / governance failure → no change.

Architecture invariants (non-negotiable):
    - NEVER imports or calls decide() from decision_policy_v1.
    - NEVER imports IntelV3Service, recommendation_engine, or any frontend path.
    - NEVER writes to any DB table.
    - NEVER calls any SEC provider, LLM, or external service.
    - NEVER returns raw metric values, structured_payload, source URLs.
    - NEVER exposes raw metric key names (fcf_margin, roic_ttm, etc.).
    - NEVER sets safe_for_decision=True.
    - ETF / fund / crypto tickers are always SKIPPED_NON_COMPANY → no signal.
    - Finance-agent / research-artifact outputs remain non-authoritative.
    - Deterministic decision_policy_v1 remains the only Buy/Hold/Trim/Sell authority.
    - This module is pure — no IO, no DB, no LLM.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..research_workers.sec_metric_evidence_readiness_adapter import (
        SecMetricEvidenceReadinessResult,
    )

from .decision_contracts import AxisBand
from .evidence_source_registry import (
    EVIDENCE_SOURCE_REGISTRY,
    EvidenceLane,
    LifecycleStatus,
    TrustTier,
)

SEC_METRIC_TRUTH_ADAPTER_V1_CONTRACT_VERSION = "phase11_v1"

# The sole source governed by this adapter.
_GOVERNED_SOURCE_ID = "sec_companyfacts_v1"

# Phase 11 explicitly accepts PLANNED as well as ACTIVE.
# PLANNED is the current registry state; Phase 11 is the first consumption step
# (the PLANNED→ACTIVE promotion happens here in practice; the registry entry
# will be updated to ACTIVE once Phase 11 is validated in production).
_ACCEPTABLE_LIFECYCLE_STATUSES = frozenset({
    LifecycleStatus.PLANNED,
    LifecycleStatus.ACTIVE,
})

# Evidence band priority for merge (lower index = weaker).
_BAND_ORDER: list[AxisBand] = [
    AxisBand.SUPPRESSED,
    AxisBand.THIN,
    AxisBand.OK,
    AxisBand.STRONG,
]


@dataclass(frozen=True)
class SecFundamentalsSignal:
    """Typed output contract for Phase 11 SEC Metric Truth Adapter v1.

    Represents the Phase 10-governed evidence contribution from
    sec_companyfacts_v1 for a single ticker.

    Forbidden (never present in any field):
        - raw metric values or metric key names
        - full structured_payload dicts
        - raw source URLs or excerpts
        - raw DB rows
        - Buy / Hold / Trim / Sell signals
        - user-facing UI copy

    Invariants:
        evidence_quality_contribution is None when governance_gate_passed is False.
        evidence_quality_contribution is None for BLOCKED / SKIPPED_NON_COMPANY tickers.
        degraded is True only for PARTIAL tickers.
        source_id is always sec_companyfacts_v1.
        adapter_version is always phase11_v1.
    """
    ticker: str
    governance_gate_passed: bool
    readiness_status: Optional[str]          # READY | PARTIAL | BLOCKED | SKIPPED_NON_COMPANY | None
    evidence_quality_contribution: Optional[AxisBand]   # None = no contribution
    degraded: bool                            # True if readiness_status == PARTIAL
    suppression_reason: Optional[str]         # why no contribution was made
    source_id: str = field(default=_GOVERNED_SOURCE_ID)
    adapter_version: str = field(default=SEC_METRIC_TRUTH_ADAPTER_V1_CONTRACT_VERSION)


def check_governance_gate(registry: Optional[dict] = None) -> tuple[bool, str]:
    """Validate Phase 10 registry governance gate for sec_companyfacts_v1.

    Returns (passed: bool, reason: str).

    Checks all required governance fields for Phase 11 consumption eligibility:
      - source_id found in registry
      - lane == SEC_COMPANY_FUNDAMENTALS
      - trust_tier == PRIMARY_HARD_DATA
      - numeric_authority == True
      - decision_input_eligible == True
      - explanation_only == False
      - lifecycle_status in Phase 11 approved set (PLANNED or ACTIVE)

    This gate is evaluated once per run — not per ticker.
    """
    if registry is None:
        registry = EVIDENCE_SOURCE_REGISTRY

    source = registry.get(_GOVERNED_SOURCE_ID)
    if source is None:
        return False, f"source_not_found:{_GOVERNED_SOURCE_ID}"

    if source.lane != EvidenceLane.SEC_COMPANY_FUNDAMENTALS:
        return False, f"wrong_lane:{source.lane.value}"

    if source.trust_tier != TrustTier.PRIMARY_HARD_DATA:
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


def _classify_ticker_readiness(
    ticker: str,
    readiness_result: "SecMetricEvidenceReadinessResult",
) -> tuple[Optional[str], Optional[str]]:
    """Return (readiness_status, suppression_reason) for a single ticker.

    Looks up the ticker in the Phase 9 readiness result.
    Returns (status_str_or_None, reason_str_or_None).
    """
    from ..research_workers.sec_metric_evidence_readiness_adapter import (
        READINESS_STATUS_READY,
        READINESS_STATUS_PARTIAL,
        READINESS_STATUS_BLOCKED,
        READINESS_STATUS_SKIPPED_NON_COMPANY,
    )

    if ticker in readiness_result.ready_tickers:
        return READINESS_STATUS_READY, None

    if ticker in readiness_result.partial_tickers_with_missing_groups:
        return READINESS_STATUS_PARTIAL, None

    if ticker in readiness_result.blocked_tickers_with_reason:
        return READINESS_STATUS_BLOCKED, "ticker_is_blocked:no_source_linked_sec_metric_evidence"

    # Check skipped (flattened from reason→ticker_list dict).
    for reason, tickers in readiness_result.skipped_tickers_by_reason.items():
        if ticker in tickers:
            return READINESS_STATUS_SKIPPED_NON_COMPANY, f"skipped_non_company:{reason}"

    return None, "ticker_not_in_readiness_result:no_sec_data"


def build_sec_fundamentals_signal(
    ticker: str,
    readiness_result: Optional["SecMetricEvidenceReadinessResult"],
    registry: Optional[dict] = None,
) -> SecFundamentalsSignal:
    """Build a SecFundamentalsSignal for one ticker from Phase 9 readiness data.

    Pure, deterministic — no IO, no LLM, no DB.

    Steps:
      1. Check governance gate (once per call — caller may cache the gate result).
      2. Look up ticker in readiness_result.
      3. Map readiness status → evidence_quality_contribution.

    Args:
        ticker:           Portfolio ticker (uppercase, e.g. "AAPL").
        readiness_result: Phase 9 SecMetricEvidenceReadinessResult. If None or
                          adapter_enabled=False, returns a suppressed signal.
        registry:         Phase 10 EVIDENCE_SOURCE_REGISTRY (default: module-level).

    Returns:
        SecFundamentalsSignal — frozen, aggregate-safe, no raw metric values.
    """
    from ..research_workers.sec_metric_evidence_readiness_adapter import (
        READINESS_STATUS_READY,
        READINESS_STATUS_PARTIAL,
    )

    gate_passed, gate_reason = check_governance_gate(registry)
    if not gate_passed:
        return SecFundamentalsSignal(
            ticker=ticker,
            governance_gate_passed=False,
            readiness_status=None,
            evidence_quality_contribution=None,
            degraded=False,
            suppression_reason=f"governance_gate_failed:{gate_reason}",
        )

    if readiness_result is None:
        return SecFundamentalsSignal(
            ticker=ticker,
            governance_gate_passed=True,
            readiness_status=None,
            evidence_quality_contribution=None,
            degraded=False,
            suppression_reason="readiness_result_unavailable",
        )

    readiness_status, suppression_reason = _classify_ticker_readiness(ticker, readiness_result)

    if readiness_status == READINESS_STATUS_READY:
        return SecFundamentalsSignal(
            ticker=ticker,
            governance_gate_passed=True,
            readiness_status=readiness_status,
            evidence_quality_contribution=AxisBand.OK,
            degraded=False,
            suppression_reason=None,
        )

    if readiness_status == READINESS_STATUS_PARTIAL:
        return SecFundamentalsSignal(
            ticker=ticker,
            governance_gate_passed=True,
            readiness_status=readiness_status,
            evidence_quality_contribution=AxisBand.THIN,
            degraded=True,
            suppression_reason=None,
        )

    # BLOCKED / SKIPPED_NON_COMPANY / not found → no contribution.
    return SecFundamentalsSignal(
        ticker=ticker,
        governance_gate_passed=True,
        readiness_status=readiness_status,
        evidence_quality_contribution=None,
        degraded=False,
        suppression_reason=suppression_reason or "no_sec_fundamentals_contribution",
    )


def apply_sec_fundamentals_to_decision_input(
    inp: "DecisionInputV3",  # noqa: F821 — imported at call sites via TYPE_CHECKING
    signal: SecFundamentalsSignal,
) -> None:
    """Apply SecFundamentalsSignal to DecisionInputV3 evidence_quality in-place.

    Upgrade-only: takes the better of current evidence_quality and the
    SEC fundamentals contribution. Never downgrades.

    Records contribution in inp.source_signal_summary under key
    "sec_fundamentals_lane" — aggregate info only, no raw metric keys.

    If the signal has no contribution (governance gate failed, ticker BLOCKED,
    SKIPPED, or readiness unavailable), this is a no-op.

    Pure function — no IO, no LLM, no DB.
    """
    contribution = signal.evidence_quality_contribution
    if contribution is None:
        return

    current = inp.evidence_quality
    try:
        current_rank = _BAND_ORDER.index(current)
    except ValueError:
        current_rank = 0

    try:
        contrib_rank = _BAND_ORDER.index(contribution)
    except ValueError:
        return

    new_band = _BAND_ORDER[max(current_rank, contrib_rank)]
    if new_band != current:
        inp.evidence_quality = new_band
        inp.suppression_reasons.pop("evidence_quality", None)

    inp.source_signal_summary["sec_fundamentals_lane"] = {
        "readiness_status": signal.readiness_status,
        "contribution": contribution.value,
        "degraded": signal.degraded,
        "evidence_quality_upgraded": new_band != current,
        "source_id": signal.source_id,
        "adapter_version": signal.adapter_version,
    }
