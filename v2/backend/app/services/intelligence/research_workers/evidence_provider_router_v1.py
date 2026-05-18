"""Stage 5G — Evidence Provider Router v1 (free-first).

Deterministic routing policy: selects the best available enabled provider for
an evidence lane based on cost tier, trust tier, and default-enabled status.

Router policy (applied in strict priority order):
  1. Prefer enabled OFFICIAL + FREE provider for the lane.
     Example: SEC EDGAR (FREE/OFFICIAL) for sec_filing lane.
  2. Else prefer enabled FREE provider (any trust tier).
     Example: yfinance (FREE/UNOFFICIAL_AGGREGATOR) for fundamentals lane.
  3. Enabled LOW_COST provider selected only when explicitly enabled
     (default_enabled=True). All LOW_COST candidates are currently disabled.
  4. Enabled PAID/EXPENSIVE provider selected only when explicitly enabled
     (default_enabled=True). All PAID candidates are currently disabled.
  5. No enabled provider available → ProviderRouteResult with reason=NO_PROVIDER.
     The caller must return honest no-evidence, not fabricated evidence.

Hard constraints:
  - Disabled providers (default_enabled=False) are NEVER selected.
  - No network calls. No DB IO. No env reads.
  - Deterministic: same registry state → same routing result.

Pure module — no IO.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from .evidence_provider_registry_v1 import (
    CostTier,
    EvidenceProviderEntry,
    TrustTier,
    enabled_providers_for_lane,
)

# ── Route reason codes ────────────────────────────────────────────────────────

ROUTE_REASON_FREE_OFFICIAL = "free_official_selected"
ROUTE_REASON_FREE_BASELINE = "free_baseline_selected"
ROUTE_REASON_LOW_COST_ENABLED = "low_cost_enabled_selected"
ROUTE_REASON_PAID_ENABLED = "paid_enabled_selected"
ROUTE_REASON_NO_PROVIDER = "no_provider_available"


@dataclass(frozen=True)
class ProviderRouteResult:
    """Result of a provider routing decision for one evidence lane.

    Fields:
        provider_id:    The selected provider_id, or None if no provider is available.
        reason:         One of the ROUTE_REASON_* constants describing why this
                        provider was chosen (or not).
        provider_entry: The full EvidenceProviderEntry for the selected provider,
                        or None if no provider was available.
    """
    provider_id: Optional[str]
    reason: str
    provider_entry: Optional[EvidenceProviderEntry] = None


def resolve_provider_for_lane(lane: str) -> ProviderRouteResult:
    """Resolve the best available enabled provider for the given evidence lane.

    Applies the free-first routing policy deterministically.

    Args:
        lane: Evidence lane identifier (e.g., "fundamentals", "sec_filing").

    Returns:
        ProviderRouteResult with provider_id and reason.
        If no enabled provider exists, provider_id is None and reason is
        ROUTE_REASON_NO_PROVIDER.

    Hard constraint: disabled providers are never returned.
    """
    candidates: List[EvidenceProviderEntry] = enabled_providers_for_lane(lane)

    if not candidates:
        return ProviderRouteResult(
            provider_id=None,
            reason=ROUTE_REASON_NO_PROVIDER,
            provider_entry=None,
        )

    # Step 1: free + official (e.g., SEC EDGAR).
    for p in candidates:
        if p.cost_tier == CostTier.FREE and p.trust_tier == TrustTier.OFFICIAL:
            return ProviderRouteResult(
                provider_id=p.provider_id,
                reason=ROUTE_REASON_FREE_OFFICIAL,
                provider_entry=p,
            )

    # Step 2: free baseline (any trust tier, e.g., yfinance).
    for p in candidates:
        if p.cost_tier == CostTier.FREE:
            return ProviderRouteResult(
                provider_id=p.provider_id,
                reason=ROUTE_REASON_FREE_BASELINE,
                provider_entry=p,
            )

    # Step 3: low-cost explicitly enabled.
    for p in candidates:
        if p.cost_tier == CostTier.LOW_COST:
            return ProviderRouteResult(
                provider_id=p.provider_id,
                reason=ROUTE_REASON_LOW_COST_ENABLED,
                provider_entry=p,
            )

    # Step 4: paid/expensive explicitly enabled.
    for p in candidates:
        if p.cost_tier in (CostTier.PAID, CostTier.EXPENSIVE):
            return ProviderRouteResult(
                provider_id=p.provider_id,
                reason=ROUTE_REASON_PAID_ENABLED,
                provider_entry=p,
            )

    # Unknown tier — last resort if the registry has an unclassified enabled provider.
    best = candidates[0]
    return ProviderRouteResult(
        provider_id=best.provider_id,
        reason=ROUTE_REASON_FREE_BASELINE,
        provider_entry=best,
    )
