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
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


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
    MISSING_CASH = "MISSING_CASH"
    STALE_CASH = "STALE_CASH"
    WEAK_CASH = "WEAK_CASH"
    CONFLICTING_CASH = "CONFLICTING_CASH"
    MISSING_POSITION_VALUE = "MISSING_POSITION_VALUE"
    STALE_POSITION_VALUE = "STALE_POSITION_VALUE"
    MISSING_PORTFOLIO_VALUE = "MISSING_PORTFOLIO_VALUE"
    STALE_PORTFOLIO_VALUE = "STALE_PORTFOLIO_VALUE"
    CONFLICTING_SIZING_DATA = "CONFLICTING_SIZING_DATA"
    TARGET_ALLOCATION_NOT_EVALUATED = "TARGET_ALLOCATION_NOT_EVALUATED"
    MINIMUM_TRADE_UNSUPPORTED = "MINIMUM_TRADE_UNSUPPORTED"
    ROUNDING_POLICY_UNSUPPORTED = "ROUNDING_POLICY_UNSUPPORTED"
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
    """
    available_cash_usd: Optional[float]
    trust_status: DeploySizingTrustStatus
    source_label: str = "not_provided"

    @property
    def suppresses_exact_dollar_readiness(self) -> bool:
        return self.trust_status in _SUPPRESSING_STATUSES


@dataclass
class DeployPositionSizingInput:
    """Per-ticker position sizing data for one holding.

    ticker: Ticker symbol this input applies to.
    current_market_value_usd: Market value of the existing position. None if unknown.
    current_weight: Current portfolio weight as a fraction (0.0–1.0). None if unknown.
    trust_status: Trust classification for this ticker's sizing data.
    """
    ticker: str
    current_market_value_usd: Optional[float]
    current_weight: Optional[float]
    trust_status: DeploySizingTrustStatus
    source_label: str = "not_provided"

    @property
    def suppresses_exact_dollar_readiness(self) -> bool:
        return self.trust_status in _SUPPRESSING_STATUSES


@dataclass
class DeployPortfolioSizingInput:
    """Total portfolio market value — required for weight-based dollar math.

    total_portfolio_value_usd: Sum of all position market values plus cash. None if unknown.
    trust_status: Trust classification.
    """
    total_portfolio_value_usd: Optional[float]
    trust_status: DeploySizingTrustStatus
    source_label: str = "not_provided"

    @property
    def suppresses_exact_dollar_readiness(self) -> bool:
        return self.trust_status in _SUPPRESSING_STATUSES


@dataclass
class DeployTargetAllocationInput:
    """Target weight / allocation placeholder for one ticker.

    Stage 2.1 placeholder only — target allocation logic is not yet implemented.
    target_weight: Target portfolio weight (0.0–1.0). None if not defined.
    trust_status: Must be NOT_EVALUATED or UNSUPPORTED in Stage 2.1.
    Fabrication guardrail: target_weight must not be set with non-CERTIFIED trust.
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

    Aggregates all sizing inputs and computes portfolio-level exact-dollar readiness.
    Missing inputs suppress readiness; no fabrication is permitted.

    Invariants (enforced here and in tests):
    - Sizing inputs cannot override Intel action or actionability.
    - Missing/stale/weak/conflicting/not-evaluated/unsupported inputs suppress
      exact_dollar_ready.
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
    def exact_dollar_ready(self) -> bool:
        """True only when cash, portfolio, and all known positions are CERTIFIED.

        Any missing, stale, weak, conflicting, not-evaluated, or unsupported input
        suppresses readiness. In Stage 2.1 this is always False unless fully-certified
        synthetic data is provided in tests (to prove the model works correctly).
        This property is a readiness gate — it does not compute any dollar amounts.
        """
        if self.cash is None or self.cash.suppresses_exact_dollar_readiness:
            return False
        if self.portfolio is None or self.portfolio.suppresses_exact_dollar_readiness:
            return False
        for pos in self.positions.values():
            if pos.suppresses_exact_dollar_readiness:
                return False
        return True

    def get_suppression_reasons(self) -> List[DeploySizingSuppressionReason]:
        """Return all active suppression reasons for this bundle."""
        reasons: List[DeploySizingSuppressionReason] = []

        if self.cash is None or self.cash.trust_status == DeploySizingTrustStatus.MISSING:
            reasons.append(DeploySizingSuppressionReason.MISSING_CASH)
        elif self.cash.trust_status == DeploySizingTrustStatus.STALE:
            reasons.append(DeploySizingSuppressionReason.STALE_CASH)
        elif self.cash.trust_status == DeploySizingTrustStatus.WEAK:
            reasons.append(DeploySizingSuppressionReason.WEAK_CASH)
        elif self.cash.trust_status == DeploySizingTrustStatus.CONFLICTING:
            reasons.append(DeploySizingSuppressionReason.CONFLICTING_CASH)

        if self.portfolio is None or self.portfolio.trust_status == DeploySizingTrustStatus.MISSING:
            reasons.append(DeploySizingSuppressionReason.MISSING_PORTFOLIO_VALUE)
        elif self.portfolio.trust_status == DeploySizingTrustStatus.STALE:
            reasons.append(DeploySizingSuppressionReason.STALE_PORTFOLIO_VALUE)

        for pos in self.positions.values():
            if pos.trust_status == DeploySizingTrustStatus.MISSING:
                reasons.append(DeploySizingSuppressionReason.MISSING_POSITION_VALUE)
            elif pos.trust_status == DeploySizingTrustStatus.STALE:
                reasons.append(DeploySizingSuppressionReason.STALE_POSITION_VALUE)

        return reasons

    def position_suppresses_dollar_readiness(self, ticker: str) -> bool:
        """True if the named ticker's position data suppresses exact-dollar readiness.

        An unknown ticker (not in positions dict) is treated as MISSING — suppressed.
        """
        pos = self.positions.get(ticker)
        if pos is None:
            return True
        return pos.suppresses_exact_dollar_readiness

    def target_allocation_for(self, ticker: str) -> Optional[DeployTargetAllocationInput]:
        """Return target allocation for ticker, or None if not defined."""
        return self.target_allocations.get(ticker)

    def has_fabricated_target_allocation(self) -> bool:
        """True if any target allocation has a non-None weight without CERTIFIED trust."""
        return any(ta.is_fabricated for ta in self.target_allocations.values())
