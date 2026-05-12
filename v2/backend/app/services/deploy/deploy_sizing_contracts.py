"""Deploy Stage 2.1 — sizing input contracts.

Pure data types only. No IO, no LLM, no DB, no broker.

Defines the authoritative typed input seam for future exact-dollar planning math.
In Stage 2.1, exact-dollar math is NOT implemented. These contracts exist to:
  1. Prove the trust / suppression model before any dollar math is written.
  2. Prevent premature dollar amounts from being computed without certified inputs.
  3. Give future stages a stable, typed contract to build against.

Intel v3 remains the only Buy/Hold/Trim/Sell authority.
Sizing inputs annotate trust state only — they cannot override Intel decisions,
create BUY/TRIM/SELL candidates, or make HOLD positions actionable.

Readiness model (three gates, all required for exact_dollar_ready):
  - sizing_values_ready: cash, portfolio, positions all CERTIFIED with valid numeric values.
  - target_allocation_ready: every position ticker has a CERTIFIED, valid target allocation.
  - policy_ready: sizing policy (min-trade, rounding) is CERTIFIED.
  In Stage 2.1, production-like bundles cannot be exact_dollar_ready because
  target allocations are NOT_EVALUATED and policy is UNSUPPORTED.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


# Portfolio-level target allocation total bounds.
# Applied to the sum of target_weight across all position tickers in a bundle.
#
# Deploy v3 does not yet have an explicit cash/residual target contract.
# Until one exists, target allocations must be near-fully specified so that
# exact-dollar math is never run against an incomplete allocation model.
# A future explicit residual/cash target can safely widen TARGET_ALLOCATION_TOTAL_MIN.
TARGET_ALLOCATION_TOTAL_MAX: float = 1.02   # > 102 %: overallocated
TARGET_ALLOCATION_TOTAL_MIN: float = 0.98   # < 98 %: unmodeled residual too large


class DeploySizingTrustStatus(str, Enum):
    """Trust classification for a single sizing input value."""
    CERTIFIED = "CERTIFIED"         # Source verified, fresh, non-conflicting — ready for math.
    MISSING = "MISSING"             # Value is absent; suppresses exact-dollar readiness.
    STALE = "STALE"                 # Value exists but is too old to trust; suppresses.
    WEAK = "WEAK"                   # Value exists but from a low-confidence source; suppresses.
    CONFLICTING = "CONFLICTING"     # Multiple sources disagree; suppresses.
    NOT_EVALUATED = "NOT_EVALUATED" # Not yet checked; placeholder state — suppresses.
    UNSUPPORTED = "UNSUPPORTED"     # Feature not yet wired in this stage — suppresses.


class DeploySizingSuppressionReason(str, Enum):
    """Why portfolio-level exact-dollar readiness is suppressed."""
    # Cash reasons
    MISSING_CASH = "MISSING_CASH"
    STALE_CASH = "STALE_CASH"
    WEAK_CASH = "WEAK_CASH"
    CONFLICTING_CASH = "CONFLICTING_CASH"
    INVALID_CASH_VALUE = "INVALID_CASH_VALUE"           # CERTIFIED trust but None or negative value.
    # Portfolio reasons
    MISSING_PORTFOLIO_VALUE = "MISSING_PORTFOLIO_VALUE"
    STALE_PORTFOLIO_VALUE = "STALE_PORTFOLIO_VALUE"
    WEAK_PORTFOLIO_VALUE = "WEAK_PORTFOLIO_VALUE"
    CONFLICTING_PORTFOLIO_VALUE = "CONFLICTING_PORTFOLIO_VALUE"
    INVALID_PORTFOLIO_VALUE = "INVALID_PORTFOLIO_VALUE" # CERTIFIED trust but None/zero/negative value.
    # Position reasons
    MISSING_POSITION_VALUE = "MISSING_POSITION_VALUE"
    STALE_POSITION_VALUE = "STALE_POSITION_VALUE"
    WEAK_POSITION_VALUE = "WEAK_POSITION_VALUE"
    CONFLICTING_POSITION_VALUE = "CONFLICTING_POSITION_VALUE"
    INVALID_POSITION_VALUE = "INVALID_POSITION_VALUE"   # CERTIFIED trust but None/invalid value or weight.
    # Target allocation reasons
    TARGET_ALLOCATION_NOT_EVALUATED = "TARGET_ALLOCATION_NOT_EVALUATED"
    TARGET_ALLOCATION_MISSING = "TARGET_ALLOCATION_MISSING"         # Position ticker has no allocation entry.
    TARGET_ALLOCATION_INVALID = "TARGET_ALLOCATION_INVALID"         # CERTIFIED but weight None or out of [0,1].
    TARGET_ALLOCATION_CONFLICTING = "TARGET_ALLOCATION_CONFLICTING" # Duplicate/conflicting inputs for ticker.
    TARGET_ALLOCATION_TOTAL_OVERALLOCATED = "TARGET_ALLOCATION_TOTAL_OVERALLOCATED"  # Sum > TARGET_ALLOCATION_TOTAL_MAX.
    TARGET_ALLOCATION_TOTAL_UNDERALLOCATED = "TARGET_ALLOCATION_TOTAL_UNDERALLOCATED"  # Sum < TARGET_ALLOCATION_TOTAL_MIN.
    # Policy reasons
    MINIMUM_TRADE_UNSUPPORTED = "MINIMUM_TRADE_UNSUPPORTED"
    ROUNDING_POLICY_UNSUPPORTED = "ROUNDING_POLICY_UNSUPPORTED"
    # General
    CONFLICTING_SIZING_DATA = "CONFLICTING_SIZING_DATA"
    SIZING_INPUT_NOT_CERTIFIED = "SIZING_INPUT_NOT_CERTIFIED"


# Trust statuses that suppress exact-dollar readiness at the input level.
_SUPPRESSING_STATUSES = frozenset({
    DeploySizingTrustStatus.MISSING,
    DeploySizingTrustStatus.STALE,
    DeploySizingTrustStatus.WEAK,
    DeploySizingTrustStatus.CONFLICTING,
    DeploySizingTrustStatus.NOT_EVALUATED,
    DeploySizingTrustStatus.UNSUPPORTED,
})


@dataclass
class DeployCashInput:
    """Available cash for deployment.

    available_cash_usd: Dollar amount available for new buy orders. None if unknown.
    trust_status: Trust classification for this value.
    source_label: Human-readable source identifier. Not validated by Deploy.

    Value-level guardrails (enforced even when trust_status == CERTIFIED):
      - available_cash_usd must not be None
      - available_cash_usd must be >= 0
    """
    available_cash_usd: Optional[float]
    trust_status: DeploySizingTrustStatus
    source_label: str = "not_provided"

    @property
    def suppresses_exact_dollar_readiness(self) -> bool:
        if self.trust_status in _SUPPRESSING_STATUSES:
            return True
        # Value-level check: CERTIFIED trust but missing or invalid value still suppresses.
        if self.available_cash_usd is None or self.available_cash_usd < 0:
            return True
        return False


@dataclass
class DeployPositionSizingInput:
    """Per-ticker position sizing data for one holding.

    ticker: Ticker symbol this input applies to.
    current_market_value_usd: Market value of the existing position. None if unknown.
    current_weight: Current portfolio weight as a fraction (0.0–1.0). None if unknown.
    trust_status: Trust classification for this ticker's sizing data.

    Value-level guardrails (enforced even when trust_status == CERTIFIED):
      - current_market_value_usd must not be None and must be >= 0
      - current_weight must not be None and must be in [0.0, 1.0]
    """
    ticker: str
    current_market_value_usd: Optional[float]
    current_weight: Optional[float]
    trust_status: DeploySizingTrustStatus
    source_label: str = "not_provided"

    @property
    def suppresses_exact_dollar_readiness(self) -> bool:
        if self.trust_status in _SUPPRESSING_STATUSES:
            return True
        # Value-level checks: CERTIFIED trust but missing or invalid values still suppress.
        if self.current_market_value_usd is None or self.current_market_value_usd < 0:
            return True
        if self.current_weight is None or self.current_weight < 0 or self.current_weight > 1:
            return True
        return False


@dataclass
class DeployPortfolioSizingInput:
    """Total portfolio market value — required for weight-based dollar math.

    total_portfolio_value_usd: Sum of all position market values plus cash. None if unknown.
    trust_status: Trust classification.

    Value-level guardrails (enforced even when trust_status == CERTIFIED):
      - total_portfolio_value_usd must not be None
      - total_portfolio_value_usd must be > 0
    """
    total_portfolio_value_usd: Optional[float]
    trust_status: DeploySizingTrustStatus
    source_label: str = "not_provided"

    @property
    def suppresses_exact_dollar_readiness(self) -> bool:
        if self.trust_status in _SUPPRESSING_STATUSES:
            return True
        # Value-level check: CERTIFIED trust but missing or non-positive value still suppresses.
        if self.total_portfolio_value_usd is None or self.total_portfolio_value_usd <= 0:
            return True
        return False


@dataclass
class DeployTargetAllocationInput:
    """Target weight / allocation placeholder for one ticker.

    Stage 2.1 placeholder only — target allocation logic is not yet implemented.
    target_weight: Target portfolio weight (0.0–1.0). None if not defined.
    trust_status: Must be NOT_EVALUATED or UNSUPPORTED in Stage 2.1.

    Fabrication guardrail: target_weight must not be set with non-CERTIFIED trust.
    Math-readiness guardrail: requires CERTIFIED trust, non-None weight, weight in [0,1].
    """
    ticker: str
    target_weight: Optional[float] = None
    trust_status: DeploySizingTrustStatus = DeploySizingTrustStatus.NOT_EVALUATED
    source_label: str = "not_evaluated_yet"

    @property
    def is_fabricated(self) -> bool:
        """True if a target_weight value was set without CERTIFIED trust — a guardrail violation."""
        return (
            self.target_weight is not None
            and self.trust_status != DeploySizingTrustStatus.CERTIFIED
        )

    @property
    def is_ready_for_math(self) -> bool:
        """True if trust is CERTIFIED, weight is set, and weight is in [0.0, 1.0].

        In Stage 2.1, production-like target allocations are NOT_EVALUATED so this
        is always False unless explicitly constructed with synthetic CERTIFIED data.
        """
        return (
            self.trust_status == DeploySizingTrustStatus.CERTIFIED
            and self.target_weight is not None
            and 0.0 <= self.target_weight <= 1.0
        )


@dataclass
class DeploySizingPolicyPlaceholder:
    """Minimum trade amount and rounding policy — placeholders only in Stage 2.1.

    minimum_trade_usd: Minimum dollar amount for a trade. None = not configured.
    rounding_policy: Policy name; "not_implemented_yet" until a future stage implements it.
    trust_status: UNSUPPORTED in Stage 2.1; will transition to CERTIFIED when wired.
    """
    minimum_trade_usd: Optional[float] = None
    rounding_policy: str = "not_implemented_yet"
    trust_status: DeploySizingTrustStatus = DeploySizingTrustStatus.UNSUPPORTED

    @property
    def suppresses_exact_dollar_readiness(self) -> bool:
        return self.trust_status in _SUPPRESSING_STATUSES


@dataclass
class DeploySizingInputBundle:
    """Complete sizing input bundle for one Deploy planning run.

    Aggregates all sizing inputs and computes portfolio-level exact-dollar readiness
    via three explicit readiness gates:

      sizing_values_ready:      cash, portfolio, positions all CERTIFIED with valid values.
      target_allocation_ready:  every position ticker has a CERTIFIED valid target allocation.
      policy_ready:             policy (min-trade, rounding) is CERTIFIED.
      exact_dollar_ready:       all three gates above are True simultaneously.

    In Stage 2.1, production-like bundles are never exact_dollar_ready because:
      - target allocations are NOT_EVALUATED (placeholder)
      - policy is UNSUPPORTED (placeholder)

    Invariants (enforced here and in tests):
    - Sizing inputs cannot override Intel action or actionability.
    - Missing/stale/weak/conflicting/not-evaluated/unsupported inputs suppress readiness.
    - CERTIFIED trust with missing or invalid numeric values also suppresses readiness.
    - recommended_dollar_amount and estimated_share_quantity remain None until
      exact-dollar math is implemented in a future stage.
    - Target allocations with non-None values require CERTIFIED trust; otherwise
      is_fabricated is True (a guardrail violation).
    - PriceBand is not a sizing authority and is not referenced here.
    """
    cash: Optional[DeployCashInput]
    portfolio: Optional[DeployPortfolioSizingInput]
    positions: Dict[str, DeployPositionSizingInput] = field(default_factory=dict)
    target_allocations: Dict[str, DeployTargetAllocationInput] = field(default_factory=dict)
    policy: Optional[DeploySizingPolicyPlaceholder] = None
    schema_version: str = "deploy_sizing_v1_contract"

    @property
    def sizing_values_ready(self) -> bool:
        """True when cash, portfolio, and all known positions have CERTIFIED trust AND valid values.

        Does NOT check target allocations or policy — those are separate gates.
        This may be True for a production-like Stage 2.1 bundle when all numeric inputs
        are verified, even though exact_dollar_ready remains False.
        """
        if self.cash is None or self.cash.suppresses_exact_dollar_readiness:
            return False
        if self.portfolio is None or self.portfolio.suppresses_exact_dollar_readiness:
            return False
        for pos in self.positions.values():
            if pos.suppresses_exact_dollar_readiness:
                return False
        return True

    @property
    def target_allocation_ready(self) -> bool:
        """True when every position ticker has a CERTIFIED, valid target allocation AND
        the sum of all target weights falls within safe portfolio-level bounds.

        Vacuously True if there are no positions (nothing to allocate).
        Always False in production-like Stage 2.1 bundles because target allocations
        are NOT_EVALUATED placeholders.

        Portfolio-level bounds (TARGET_ALLOCATION_TOTAL_MIN to TARGET_ALLOCATION_TOTAL_MAX):
          - Sum > MAX → overallocated (more than 100 % + tolerance assigned to positions).
          - Sum < MIN → significant unmodeled cash/residual without explicit contract.
        """
        if not self.positions:
            return True  # vacuously true — no positions, nothing to target

        weights: List[float] = []
        for ticker in self.positions:
            ta = self.target_allocations.get(ticker)
            if ta is None or not ta.is_ready_for_math:
                return False
            weights.append(ta.target_weight)  # type: ignore[arg-type]

        total = sum(weights)
        return TARGET_ALLOCATION_TOTAL_MIN <= total <= TARGET_ALLOCATION_TOTAL_MAX

    @property
    def policy_ready(self) -> bool:
        """True when the sizing policy (min-trade, rounding) is CERTIFIED.

        Always False in Stage 2.1 because policy is UNSUPPORTED by default.
        """
        if self.policy is None:
            return False
        return self.policy.trust_status == DeploySizingTrustStatus.CERTIFIED

    @property
    def exact_dollar_ready(self) -> bool:
        """True only when sizing_values_ready, target_allocation_ready, and policy_ready are all True.

        This is the final gate before exact-dollar math may be computed in a future stage.
        In Stage 2.1, production-like bundles always return False because target allocations
        and policy are placeholders. This property is a readiness gate — it never computes
        any dollar amounts.
        """
        return self.sizing_values_ready and self.target_allocation_ready and self.policy_ready

    def get_suppression_reasons(self) -> List[DeploySizingSuppressionReason]:
        """Return all active suppression reasons for this bundle.

        Covers trust-level suppression, value-level suppression, target allocation
        readiness, and policy readiness. Reasons are deduplicated.
        """
        seen: set = set()
        reasons: List[DeploySizingSuppressionReason] = []

        def _add(r: DeploySizingSuppressionReason) -> None:
            if r not in seen:
                seen.add(r)
                reasons.append(r)

        # --- Cash ---
        if self.cash is None or self.cash.trust_status == DeploySizingTrustStatus.MISSING:
            _add(DeploySizingSuppressionReason.MISSING_CASH)
        elif self.cash.trust_status == DeploySizingTrustStatus.STALE:
            _add(DeploySizingSuppressionReason.STALE_CASH)
        elif self.cash.trust_status == DeploySizingTrustStatus.WEAK:
            _add(DeploySizingSuppressionReason.WEAK_CASH)
        elif self.cash.trust_status == DeploySizingTrustStatus.CONFLICTING:
            _add(DeploySizingSuppressionReason.CONFLICTING_CASH)
        elif self.cash is not None and self.cash.trust_status == DeploySizingTrustStatus.CERTIFIED:
            if self.cash.available_cash_usd is None or self.cash.available_cash_usd < 0:
                _add(DeploySizingSuppressionReason.INVALID_CASH_VALUE)

        # --- Portfolio ---
        if self.portfolio is None or self.portfolio.trust_status == DeploySizingTrustStatus.MISSING:
            _add(DeploySizingSuppressionReason.MISSING_PORTFOLIO_VALUE)
        elif self.portfolio.trust_status == DeploySizingTrustStatus.STALE:
            _add(DeploySizingSuppressionReason.STALE_PORTFOLIO_VALUE)
        elif self.portfolio.trust_status == DeploySizingTrustStatus.WEAK:
            _add(DeploySizingSuppressionReason.WEAK_PORTFOLIO_VALUE)
        elif self.portfolio.trust_status == DeploySizingTrustStatus.CONFLICTING:
            _add(DeploySizingSuppressionReason.CONFLICTING_PORTFOLIO_VALUE)
        elif self.portfolio is not None and self.portfolio.trust_status == DeploySizingTrustStatus.CERTIFIED:
            v = self.portfolio.total_portfolio_value_usd
            if v is None or v <= 0:
                _add(DeploySizingSuppressionReason.INVALID_PORTFOLIO_VALUE)

        # --- Positions ---
        for pos in self.positions.values():
            if pos.trust_status == DeploySizingTrustStatus.MISSING:
                _add(DeploySizingSuppressionReason.MISSING_POSITION_VALUE)
            elif pos.trust_status == DeploySizingTrustStatus.STALE:
                _add(DeploySizingSuppressionReason.STALE_POSITION_VALUE)
            elif pos.trust_status == DeploySizingTrustStatus.WEAK:
                _add(DeploySizingSuppressionReason.WEAK_POSITION_VALUE)
            elif pos.trust_status == DeploySizingTrustStatus.CONFLICTING:
                _add(DeploySizingSuppressionReason.CONFLICTING_POSITION_VALUE)
            elif pos.trust_status == DeploySizingTrustStatus.CERTIFIED:
                mkt = pos.current_market_value_usd
                w = pos.current_weight
                if (mkt is None or mkt < 0 or w is None or w < 0 or w > 1):
                    _add(DeploySizingSuppressionReason.INVALID_POSITION_VALUE)

        # --- Target allocations (per position ticker) ---
        certified_weights: List[float] = []
        all_individual_certified = True
        for ticker in self.positions:
            ta = self.target_allocations.get(ticker)
            if ta is None:
                _add(DeploySizingSuppressionReason.TARGET_ALLOCATION_MISSING)
                all_individual_certified = False
            elif ta.trust_status == DeploySizingTrustStatus.NOT_EVALUATED:
                _add(DeploySizingSuppressionReason.TARGET_ALLOCATION_NOT_EVALUATED)
                all_individual_certified = False
            elif ta.trust_status == DeploySizingTrustStatus.CONFLICTING:
                _add(DeploySizingSuppressionReason.TARGET_ALLOCATION_CONFLICTING)
                all_individual_certified = False
            elif ta.trust_status == DeploySizingTrustStatus.CERTIFIED:
                if ta.target_weight is None or not (0.0 <= ta.target_weight <= 1.0):
                    _add(DeploySizingSuppressionReason.TARGET_ALLOCATION_INVALID)
                    all_individual_certified = False
                else:
                    certified_weights.append(ta.target_weight)
            elif ta.trust_status in _SUPPRESSING_STATUSES:
                _add(DeploySizingSuppressionReason.TARGET_ALLOCATION_NOT_EVALUATED)
                all_individual_certified = False

        # --- Target allocation portfolio-level total ---
        # Only evaluate total when all individual per-ticker checks passed.
        if self.positions and all_individual_certified and certified_weights:
            total = sum(certified_weights)
            if total > TARGET_ALLOCATION_TOTAL_MAX:
                _add(DeploySizingSuppressionReason.TARGET_ALLOCATION_TOTAL_OVERALLOCATED)
            elif total < TARGET_ALLOCATION_TOTAL_MIN:
                _add(DeploySizingSuppressionReason.TARGET_ALLOCATION_TOTAL_UNDERALLOCATED)

        # --- Policy ---
        if not self.policy_ready:
            if self.policy is None or self.policy.trust_status == DeploySizingTrustStatus.UNSUPPORTED:
                _add(DeploySizingSuppressionReason.MINIMUM_TRADE_UNSUPPORTED)
                _add(DeploySizingSuppressionReason.ROUNDING_POLICY_UNSUPPORTED)

        return reasons

    def position_suppresses_dollar_readiness(self, ticker: str) -> bool:
        """True if the named ticker's position data suppresses exact-dollar readiness.

        An unknown ticker (not in positions dict) is treated as MISSING — suppressed.
        """
        pos = self.positions.get(ticker)
        if pos is None:
            return True
        return pos.suppresses_exact_dollar_readiness

    def target_allocation_suppresses_exact_dollar_readiness(self, ticker: str) -> bool:
        """True if the named ticker's target allocation suppresses exact-dollar readiness.

        No entry → suppressed. NOT_EVALUATED → suppressed. Invalid weight → suppressed.
        CERTIFIED with valid weight → not suppressed.
        """
        ta = self.target_allocations.get(ticker)
        if ta is None:
            return True
        return not ta.is_ready_for_math

    def target_allocation_for(self, ticker: str) -> Optional[DeployTargetAllocationInput]:
        """Return target allocation for ticker, or None if not defined."""
        return self.target_allocations.get(ticker)

    def has_fabricated_target_allocation(self) -> bool:
        """True if any target allocation has a non-None weight without CERTIFIED trust."""
        return any(ta.is_fabricated for ta in self.target_allocations.values())
