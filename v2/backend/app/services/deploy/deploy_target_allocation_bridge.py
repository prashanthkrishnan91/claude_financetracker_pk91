"""Deploy Stage 2.2 — target allocation readiness bridge.

Provides explicit-input certification of target allocations for the
DeploySizingInputBundle. No default weights may be invented; every
certified allocation must come from an explicit, meaningful source.

Source wiring for a canonical portfolio allocation optimizer is deferred
to a future stage. For now, callers must pass explicit allocation configs.

Intel v3 remains the only Buy/Hold/Trim/Sell authority. Certified target
allocations annotate the sizing input contract only — they cannot override
Intel decisions or make HOLD positions actionable.

Certification contract:
  - target_weight must not be None
  - target_weight must be in [0.0, 1.0]
  - source_label must be a non-empty, non-placeholder string
  - trust_status must be CERTIFIED (no fabrication allowed)

Any violation raises ValueError — callers are responsible for valid input.
"""
from __future__ import annotations

from typing import Any, Dict, List

from .deploy_sizing_contracts import (
    DeploySizingTrustStatus,
    DeployTargetAllocationInput,
)

# Labels that indicate placeholder / fabricated sources and are not accepted
# as meaningful source identifiers for certified allocations.
_PLACEHOLDER_SOURCE_LABELS = frozenset({
    "",
    "not_evaluated_yet",
    "not_provided",
    "fabricated",
    "invented",
    "default",
    "placeholder",
    "unknown",
})


def certify_target_allocation(
    ticker: str,
    target_weight: float,
    source_label: str,
) -> DeployTargetAllocationInput:
    """Return a CERTIFIED DeployTargetAllocationInput from explicit input.

    Raises ValueError if any invariant is violated:
      - ticker must be a non-empty string
      - target_weight must not be None and must be in [0.0, 1.0]
      - source_label must be non-empty and not a known placeholder

    No default weights are invented. This function never returns a
    NOT_EVALUATED or UNSUPPORTED allocation — call it only when all
    inputs are known and valid.
    """
    if not ticker or not isinstance(ticker, str):
        raise ValueError(f"ticker must be a non-empty string, got: {ticker!r}")

    if target_weight is None:
        raise ValueError(f"target_weight must not be None for ticker {ticker!r}")

    if not isinstance(target_weight, (int, float)):
        raise ValueError(
            f"target_weight must be a numeric value in [0.0, 1.0] for ticker {ticker!r}, "
            f"got: {target_weight!r}"
        )

    if not (0.0 <= target_weight <= 1.0):
        raise ValueError(
            f"target_weight must be in [0.0, 1.0] for ticker {ticker!r}, "
            f"got: {target_weight!r}"
        )

    if not source_label or not isinstance(source_label, str):
        raise ValueError(
            f"source_label must be a non-empty string for ticker {ticker!r}"
        )

    normalized_label = source_label.strip().lower()
    if normalized_label in _PLACEHOLDER_SOURCE_LABELS:
        raise ValueError(
            f"source_label {source_label!r} is a reserved placeholder for ticker {ticker!r}. "
            "Provide a meaningful source identifier (e.g. 'explicit_user_config', "
            "'model_portfolio_v1')."
        )

    return DeployTargetAllocationInput(
        ticker=ticker,
        target_weight=float(target_weight),
        trust_status=DeploySizingTrustStatus.CERTIFIED,
        source_label=source_label,
    )


def build_certified_target_allocations(
    alloc_list: List[Dict[str, Any]],
) -> Dict[str, DeployTargetAllocationInput]:
    """Build a certified target-allocation dict from an explicit allocation list.

    Each entry must have:
      - ticker (str): Ticker symbol.
      - target_weight (float): Target portfolio weight in [0.0, 1.0].
      - source_label (str): Meaningful source identifier.

    Returns a dict keyed by ticker. Raises ValueError if any entry is invalid.
    Duplicate tickers use the last entry.
    """
    result: Dict[str, DeployTargetAllocationInput] = {}
    for entry in (alloc_list or []):
        ticker = str(entry.get("ticker") or "")
        target_weight = entry.get("target_weight")
        source_label = str(entry.get("source_label") or "")
        result[ticker] = certify_target_allocation(ticker, target_weight, source_label)
    return result
