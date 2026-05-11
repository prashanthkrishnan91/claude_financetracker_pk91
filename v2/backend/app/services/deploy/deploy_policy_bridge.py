"""Deploy Stage 2.2 — execution policy readiness bridge.

Provides explicit-input certification of the sizing policy (minimum trade
amount, rounding policy) for the DeploySizingInputBundle.

Policy can only be CERTIFIED from explicit config/object input. No
hardcoded production policy is invented here. Missing or UNSUPPORTED
policy continues to suppress exact-dollar readiness — the default
DeploySizingPolicyPlaceholder in the existing builder remains UNSUPPORTED.

Intel v3 remains the only Buy/Hold/Trim/Sell authority. Certified policy
annotates the sizing input contract only — it cannot override Intel decisions.

Certification contract:
  - minimum_trade_usd must be a non-negative numeric value (>= 0.0)
  - rounding_policy must be one of the allowed values (see ALLOWED_ROUNDING_POLICIES)

Any violation raises ValueError — callers are responsible for valid input.

Allowed rounding policies:
  WHOLE_DOLLAR   — round to the nearest whole dollar
  NEAREST_DOLLAR — synonym for WHOLE_DOLLAR (kept for naming flexibility)
  NO_ROUNDING    — pass the computed amount through without rounding
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from .deploy_sizing_contracts import (
    DeploySizingPolicyPlaceholder,
    DeploySizingTrustStatus,
)

# Allowed rounding policy identifiers for certified policy objects.
ALLOWED_ROUNDING_POLICIES = frozenset({
    "WHOLE_DOLLAR",
    "NEAREST_DOLLAR",
    "NO_ROUNDING",
})


def certify_sizing_policy(
    minimum_trade_usd: float,
    rounding_policy: str,
) -> DeploySizingPolicyPlaceholder:
    """Return a CERTIFIED DeploySizingPolicyPlaceholder from explicit input.

    Raises ValueError if any invariant is violated:
      - minimum_trade_usd must not be None and must be >= 0.0
      - rounding_policy must be one of ALLOWED_ROUNDING_POLICIES

    This function never invents default policy values. Call it only when
    both inputs are explicitly known and valid.
    """
    if minimum_trade_usd is None:
        raise ValueError(
            "minimum_trade_usd must not be None. "
            "Provide an explicit non-negative numeric value."
        )

    if not isinstance(minimum_trade_usd, (int, float)):
        raise ValueError(
            f"minimum_trade_usd must be a numeric value >= 0, got: {minimum_trade_usd!r}"
        )

    if minimum_trade_usd < 0:
        raise ValueError(
            f"minimum_trade_usd must be >= 0.0, got: {minimum_trade_usd!r}"
        )

    if not rounding_policy or not isinstance(rounding_policy, str):
        raise ValueError(
            "rounding_policy must be a non-empty string. "
            f"Allowed values: {sorted(ALLOWED_ROUNDING_POLICIES)}"
        )

    normalized = rounding_policy.strip().upper()
    if normalized not in ALLOWED_ROUNDING_POLICIES:
        raise ValueError(
            f"rounding_policy {rounding_policy!r} is not an allowed value. "
            f"Allowed values: {sorted(ALLOWED_ROUNDING_POLICIES)}"
        )

    return DeploySizingPolicyPlaceholder(
        minimum_trade_usd=float(minimum_trade_usd),
        rounding_policy=normalized,
        trust_status=DeploySizingTrustStatus.CERTIFIED,
    )


def build_policy_from_config(
    config: Optional[Dict[str, Any]],
) -> DeploySizingPolicyPlaceholder:
    """Build a sizing policy from a config dict, certifying if valid.

    Accepts a dict with:
      - minimum_trade_usd (float): Minimum trade threshold. Required.
      - rounding_policy (str): One of ALLOWED_ROUNDING_POLICIES. Required.

    Returns a CERTIFIED policy if both keys are present and valid.
    Returns an UNSUPPORTED placeholder if config is None or empty.
    Raises ValueError if config is present but values are invalid.
    """
    if not config:
        return DeploySizingPolicyPlaceholder(
            trust_status=DeploySizingTrustStatus.UNSUPPORTED,
        )

    minimum_trade_usd = config.get("minimum_trade_usd")
    rounding_policy = config.get("rounding_policy")

    if minimum_trade_usd is None and rounding_policy is None:
        return DeploySizingPolicyPlaceholder(
            trust_status=DeploySizingTrustStatus.UNSUPPORTED,
        )

    # Both fields must be present for certification.
    if minimum_trade_usd is None:
        raise ValueError(
            "minimum_trade_usd is required in policy config for certification."
        )
    if rounding_policy is None:
        raise ValueError(
            "rounding_policy is required in policy config for certification."
        )

    return certify_sizing_policy(minimum_trade_usd, rounding_policy)
