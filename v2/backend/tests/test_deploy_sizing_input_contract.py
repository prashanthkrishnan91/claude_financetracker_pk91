"""Tests — Deploy Stage 2.1 sizing input contract (hardened).

Proves the following invariants:
  1.  Certified cash/position/portfolio inputs (with valid values) produce sizing_values_ready.
  2.  Missing cash suppresses exact-dollar readiness.
  3.  Stale cash suppresses exact-dollar readiness.
  4.  Weak cash suppresses exact-dollar readiness.
  5.  Conflicting cash suppresses exact-dollar readiness.
  6.  CERTIFIED cash with None or negative value suppresses (value-level guardrail).
  7.  Missing position market value suppresses affected-ticker sizing readiness.
  8.  Stale position market value suppresses affected-ticker sizing readiness.
  9.  CERTIFIED position with None market value suppresses (value-level guardrail).
 10.  CERTIFIED position with None weight suppresses (value-level guardrail).
 11.  CERTIFIED position with negative market value suppresses.
 12.  CERTIFIED position with weight > 1 suppresses.
 13.  Missing total portfolio value suppresses sizing readiness.
 14.  Stale total portfolio value suppresses sizing readiness.
 15.  CERTIFIED portfolio with None or non-positive value suppresses (value-level guardrail).
 16.  Missing target allocation suppresses exact-dollar readiness.
 17.  NOT_EVALUATED target allocation suppresses exact-dollar readiness.
 18.  UNSUPPORTED policy suppresses exact-dollar readiness (policy_ready=False).
 19.  sizing_values_ready can be True while exact_dollar_ready is False (different gates).
 20.  exact_dollar_ready requires all three gates: sizing_values, target_allocation, policy.
 21.  Missing target allocation is not fabricated.
 22.  Target allocation with non-None weight and non-CERTIFIED trust is flagged as fabricated.
 23.  Conflicting sizing inputs suppress readiness.
 24.  Minimum-trade and rounding policy remain placeholders (UNSUPPORTED).
 25.  recommended_dollar_amount and estimated_share_quantity remain None.
 26.  Sizing inputs cannot change Intel action or actionability.
 27.  HOLD remains non-actionable even with certified sizing inputs.
 28.  BUY/TRIM/SELL remain Intel-derived candidates only.
 29.  No SQL/UI/route/provider/LLM/broker files are touched.
 30.  Builder produces correct bundle from portfolio snapshot dict.
 31.  Unknown ticker in positions dict is treated as MISSING (suppressed).
 32.  has_fabricated_target_allocation guardrail works correctly.
 33.  Suppression reasons correctly enumerate active suppressions including value-level.
 34.  target_allocation_suppresses_exact_dollar_readiness method works correctly.
 35.  is_ready_for_math on DeployTargetAllocationInput enforces CERTIFIED+valid weight.
 36.  Schema version is deploy_sizing_v1_contract throughout.
"""
import pytest

from app.services.deploy.deploy_sizing_contracts import (
    DeployCashInput,
    DeployPortfolioSizingInput,
    DeployPositionSizingInput,
    DeploySizingInputBundle,
    DeploySizingPolicyPlaceholder,
    DeploySizingSuppressionReason,
    DeploySizingTrustStatus,
    DeployTargetAllocationInput,
)
from app.services.deploy.deploy_sizing_builder import (
    build_sizing_context_from_portfolio_snapshot,
)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _certified_cash(amount: float = 10_000.0) -> DeployCashInput:
    return DeployCashInput(
        available_cash_usd=amount,
        trust_status=DeploySizingTrustStatus.CERTIFIED,
        source_label="test_source",
    )


def _certified_portfolio(total: float = 100_000.0) -> DeployPortfolioSizingInput:
    return DeployPortfolioSizingInput(
        total_portfolio_value_usd=total,
        trust_status=DeploySizingTrustStatus.CERTIFIED,
        source_label="test_source",
    )


def _certified_position(ticker: str = "AAPL", value: float = 5_000.0, weight: float = 0.05) -> DeployPositionSizingInput:
    return DeployPositionSizingInput(
        ticker=ticker,
        current_market_value_usd=value,
        current_weight=weight,
        trust_status=DeploySizingTrustStatus.CERTIFIED,
        source_label="test_source",
    )


def _certified_target_allocation(ticker: str = "AAPL", weight: float = 0.05) -> DeployTargetAllocationInput:
    return DeployTargetAllocationInput(
        ticker=ticker,
        target_weight=weight,
        trust_status=DeploySizingTrustStatus.CERTIFIED,
    )


def _certified_policy() -> DeploySizingPolicyPlaceholder:
    return DeploySizingPolicyPlaceholder(
        minimum_trade_usd=100.0,
        rounding_policy="round_to_dollar",
        trust_status=DeploySizingTrustStatus.CERTIFIED,
    )


def _fully_certified_bundle(tickers: list | None = None) -> DeploySizingInputBundle:
    """Bundle with certified cash/portfolio/positions — proves sizing_values_ready=True.

    Note: does NOT include target allocations or policy, so exact_dollar_ready=False.
    """
    tickers = tickers or ["AAPL"]
    return DeploySizingInputBundle(
        cash=_certified_cash(),
        portfolio=_certified_portfolio(),
        positions={t: _certified_position(t) for t in tickers},
    )


def _fully_ready_bundle(tickers: list | None = None) -> DeploySizingInputBundle:
    """Bundle with all three readiness gates satisfied — proves exact_dollar_ready=True.

    For testing the gate only; no dollar amounts are computed.
    """
    tickers = tickers or ["AAPL"]
    return DeploySizingInputBundle(
        cash=_certified_cash(),
        portfolio=_certified_portfolio(),
        positions={t: _certified_position(t) for t in tickers},
        target_allocations={t: _certified_target_allocation(t) for t in tickers},
        policy=_certified_policy(),
    )


# ──────────────────────────────────────────────────────────────────────────────
# 1. Certified inputs with valid values → sizing_values_ready
# ──────────────────────────────────────────────────────────────────────────────

def test_certified_bundle_sizing_values_ready():
    """Certified inputs with valid numeric values produce sizing_values_ready=True."""
    bundle = _fully_certified_bundle()
    assert bundle.sizing_values_ready is True


def test_certified_bundle_has_no_dollar_amounts():
    """No dollar amounts live in DeploySizingInputBundle — it is a readiness gate only."""
    bundle = _fully_certified_bundle()
    assert not hasattr(bundle, "recommended_dollar_amount")
    assert not hasattr(bundle, "estimated_share_quantity")


def test_certified_bundle_schema_version():
    bundle = _fully_certified_bundle()
    assert bundle.schema_version == "deploy_sizing_v1_contract"


# ──────────────────────────────────────────────────────────────────────────────
# 2. Missing cash suppresses
# ──────────────────────────────────────────────────────────────────────────────

def test_missing_cash_suppresses_sizing_values_ready():
    bundle = DeploySizingInputBundle(
        cash=DeployCashInput(available_cash_usd=None, trust_status=DeploySizingTrustStatus.MISSING),
        portfolio=_certified_portfolio(),
        positions={"AAPL": _certified_position()},
    )
    assert bundle.sizing_values_ready is False


def test_none_cash_suppresses_sizing_values_ready():
    bundle = DeploySizingInputBundle(
        cash=None,
        portfolio=_certified_portfolio(),
        positions={"AAPL": _certified_position()},
    )
    assert bundle.sizing_values_ready is False


def test_missing_cash_suppression_reason():
    bundle = DeploySizingInputBundle(
        cash=DeployCashInput(available_cash_usd=None, trust_status=DeploySizingTrustStatus.MISSING),
        portfolio=_certified_portfolio(),
    )
    reasons = bundle.get_suppression_reasons()
    assert DeploySizingSuppressionReason.MISSING_CASH in reasons


def test_none_cash_suppression_reason():
    bundle = DeploySizingInputBundle(cash=None, portfolio=_certified_portfolio())
    reasons = bundle.get_suppression_reasons()
    assert DeploySizingSuppressionReason.MISSING_CASH in reasons


# ──────────────────────────────────────────────────────────────────────────────
# 3. Stale cash suppresses
# ──────────────────────────────────────────────────────────────────────────────

def test_stale_cash_suppresses_sizing_values_ready():
    bundle = DeploySizingInputBundle(
        cash=DeployCashInput(available_cash_usd=5000.0, trust_status=DeploySizingTrustStatus.STALE),
        portfolio=_certified_portfolio(),
        positions={"AAPL": _certified_position()},
    )
    assert bundle.sizing_values_ready is False


def test_stale_cash_suppression_reason():
    bundle = DeploySizingInputBundle(
        cash=DeployCashInput(available_cash_usd=5000.0, trust_status=DeploySizingTrustStatus.STALE),
        portfolio=_certified_portfolio(),
    )
    reasons = bundle.get_suppression_reasons()
    assert DeploySizingSuppressionReason.STALE_CASH in reasons


# ──────────────────────────────────────────────────────────────────────────────
# 4. Weak cash suppresses
# ──────────────────────────────────────────────────────────────────────────────

def test_weak_cash_suppresses_sizing_values_ready():
    bundle = DeploySizingInputBundle(
        cash=DeployCashInput(available_cash_usd=5000.0, trust_status=DeploySizingTrustStatus.WEAK),
        portfolio=_certified_portfolio(),
        positions={"AAPL": _certified_position()},
    )
    assert bundle.sizing_values_ready is False


def test_weak_cash_suppression_reason():
    bundle = DeploySizingInputBundle(
        cash=DeployCashInput(available_cash_usd=5000.0, trust_status=DeploySizingTrustStatus.WEAK),
        portfolio=_certified_portfolio(),
    )
    reasons = bundle.get_suppression_reasons()
    assert DeploySizingSuppressionReason.WEAK_CASH in reasons


# ──────────────────────────────────────────────────────────────────────────────
# 5. Conflicting cash suppresses
# ──────────────────────────────────────────────────────────────────────────────

def test_conflicting_cash_suppresses_sizing_values_ready():
    bundle = DeploySizingInputBundle(
        cash=DeployCashInput(available_cash_usd=5000.0, trust_status=DeploySizingTrustStatus.CONFLICTING),
        portfolio=_certified_portfolio(),
        positions={"AAPL": _certified_position()},
    )
    assert bundle.sizing_values_ready is False


def test_conflicting_cash_suppression_reason():
    bundle = DeploySizingInputBundle(
        cash=DeployCashInput(available_cash_usd=5000.0, trust_status=DeploySizingTrustStatus.CONFLICTING),
        portfolio=_certified_portfolio(),
    )
    reasons = bundle.get_suppression_reasons()
    assert DeploySizingSuppressionReason.CONFLICTING_CASH in reasons


# ──────────────────────────────────────────────────────────────────────────────
# 6. CERTIFIED cash with None or negative value suppresses (value-level guardrail)
# ──────────────────────────────────────────────────────────────────────────────

def test_certified_cash_with_none_value_suppresses():
    cash = DeployCashInput(available_cash_usd=None, trust_status=DeploySizingTrustStatus.CERTIFIED)
    assert cash.suppresses_exact_dollar_readiness is True


def test_certified_cash_with_negative_value_suppresses():
    cash = DeployCashInput(available_cash_usd=-100.0, trust_status=DeploySizingTrustStatus.CERTIFIED)
    assert cash.suppresses_exact_dollar_readiness is True


def test_certified_cash_with_none_value_suppresses_bundle():
    bundle = DeploySizingInputBundle(
        cash=DeployCashInput(available_cash_usd=None, trust_status=DeploySizingTrustStatus.CERTIFIED),
        portfolio=_certified_portfolio(),
        positions={"AAPL": _certified_position()},
    )
    assert bundle.sizing_values_ready is False


def test_certified_cash_with_none_value_produces_invalid_cash_reason():
    bundle = DeploySizingInputBundle(
        cash=DeployCashInput(available_cash_usd=None, trust_status=DeploySizingTrustStatus.CERTIFIED),
        portfolio=_certified_portfolio(),
    )
    reasons = bundle.get_suppression_reasons()
    assert DeploySizingSuppressionReason.INVALID_CASH_VALUE in reasons


def test_certified_cash_with_negative_value_produces_invalid_cash_reason():
    bundle = DeploySizingInputBundle(
        cash=DeployCashInput(available_cash_usd=-50.0, trust_status=DeploySizingTrustStatus.CERTIFIED),
        portfolio=_certified_portfolio(),
    )
    reasons = bundle.get_suppression_reasons()
    assert DeploySizingSuppressionReason.INVALID_CASH_VALUE in reasons


def test_certified_cash_zero_does_not_suppress():
    """Zero cash is a valid value (may simply mean no cash available)."""
    cash = DeployCashInput(available_cash_usd=0.0, trust_status=DeploySizingTrustStatus.CERTIFIED)
    assert cash.suppresses_exact_dollar_readiness is False


# ──────────────────────────────────────────────────────────────────────────────
# 7. Missing position market value suppresses
# ──────────────────────────────────────────────────────────────────────────────

def test_missing_position_suppresses_sizing_values_ready():
    bundle = DeploySizingInputBundle(
        cash=_certified_cash(),
        portfolio=_certified_portfolio(),
        positions={
            "AAPL": _certified_position("AAPL"),
            "NVDA": DeployPositionSizingInput(
                ticker="NVDA",
                current_market_value_usd=None,
                current_weight=None,
                trust_status=DeploySizingTrustStatus.MISSING,
            ),
        },
    )
    assert bundle.position_suppresses_dollar_readiness("NVDA") is True
    assert bundle.sizing_values_ready is False


def test_certified_position_does_not_suppress():
    bundle = _fully_certified_bundle(["AAPL"])
    assert bundle.position_suppresses_dollar_readiness("AAPL") is False


def test_unknown_ticker_position_treated_as_missing():
    """A ticker not in the positions dict is treated as MISSING — suppressed."""
    bundle = _fully_certified_bundle(["AAPL"])
    assert bundle.position_suppresses_dollar_readiness("UNKNOWN_TICKER") is True


def test_missing_position_suppression_reason():
    bundle = DeploySizingInputBundle(
        cash=_certified_cash(),
        portfolio=_certified_portfolio(),
        positions={
            "NVDA": DeployPositionSizingInput(
                ticker="NVDA",
                current_market_value_usd=None,
                current_weight=None,
                trust_status=DeploySizingTrustStatus.MISSING,
            ),
        },
    )
    reasons = bundle.get_suppression_reasons()
    assert DeploySizingSuppressionReason.MISSING_POSITION_VALUE in reasons


# ──────────────────────────────────────────────────────────────────────────────
# 8. Stale position suppresses
# ──────────────────────────────────────────────────────────────────────────────

def test_stale_position_suppresses_sizing_values_ready():
    bundle = DeploySizingInputBundle(
        cash=_certified_cash(),
        portfolio=_certified_portfolio(),
        positions={
            "AAPL": DeployPositionSizingInput(
                ticker="AAPL",
                current_market_value_usd=5000.0,
                current_weight=0.05,
                trust_status=DeploySizingTrustStatus.STALE,
            ),
        },
    )
    assert bundle.sizing_values_ready is False


def test_stale_position_suppression_reason():
    bundle = DeploySizingInputBundle(
        cash=_certified_cash(),
        portfolio=_certified_portfolio(),
        positions={
            "AAPL": DeployPositionSizingInput(
                ticker="AAPL",
                current_market_value_usd=5000.0,
                current_weight=0.05,
                trust_status=DeploySizingTrustStatus.STALE,
            ),
        },
    )
    reasons = bundle.get_suppression_reasons()
    assert DeploySizingSuppressionReason.STALE_POSITION_VALUE in reasons


# ──────────────────────────────────────────────────────────────────────────────
# 9. CERTIFIED position with None market value suppresses (value-level guardrail)
# ──────────────────────────────────────────────────────────────────────────────

def test_certified_position_with_none_market_value_suppresses():
    pos = DeployPositionSizingInput(
        ticker="AAPL",
        current_market_value_usd=None,
        current_weight=0.05,
        trust_status=DeploySizingTrustStatus.CERTIFIED,
    )
    assert pos.suppresses_exact_dollar_readiness is True


def test_certified_position_with_none_market_value_suppresses_bundle():
    bundle = DeploySizingInputBundle(
        cash=_certified_cash(),
        portfolio=_certified_portfolio(),
        positions={
            "AAPL": DeployPositionSizingInput(
                ticker="AAPL",
                current_market_value_usd=None,
                current_weight=0.05,
                trust_status=DeploySizingTrustStatus.CERTIFIED,
            ),
        },
    )
    assert bundle.sizing_values_ready is False


def test_certified_position_none_market_value_produces_invalid_position_reason():
    bundle = DeploySizingInputBundle(
        cash=_certified_cash(),
        portfolio=_certified_portfolio(),
        positions={
            "AAPL": DeployPositionSizingInput(
                ticker="AAPL",
                current_market_value_usd=None,
                current_weight=0.05,
                trust_status=DeploySizingTrustStatus.CERTIFIED,
            ),
        },
    )
    reasons = bundle.get_suppression_reasons()
    assert DeploySizingSuppressionReason.INVALID_POSITION_VALUE in reasons


# ──────────────────────────────────────────────────────────────────────────────
# 10. CERTIFIED position with None weight suppresses (value-level guardrail)
# ──────────────────────────────────────────────────────────────────────────────

def test_certified_position_with_none_weight_suppresses():
    pos = DeployPositionSizingInput(
        ticker="AAPL",
        current_market_value_usd=5000.0,
        current_weight=None,
        trust_status=DeploySizingTrustStatus.CERTIFIED,
    )
    assert pos.suppresses_exact_dollar_readiness is True


def test_certified_position_with_none_weight_suppresses_bundle():
    bundle = DeploySizingInputBundle(
        cash=_certified_cash(),
        portfolio=_certified_portfolio(),
        positions={
            "AAPL": DeployPositionSizingInput(
                ticker="AAPL",
                current_market_value_usd=5000.0,
                current_weight=None,
                trust_status=DeploySizingTrustStatus.CERTIFIED,
            ),
        },
    )
    assert bundle.sizing_values_ready is False


# ──────────────────────────────────────────────────────────────────────────────
# 11. CERTIFIED position with negative market value suppresses
# ──────────────────────────────────────────────────────────────────────────────

def test_certified_position_negative_market_value_suppresses():
    pos = DeployPositionSizingInput(
        ticker="AAPL",
        current_market_value_usd=-100.0,
        current_weight=0.05,
        trust_status=DeploySizingTrustStatus.CERTIFIED,
    )
    assert pos.suppresses_exact_dollar_readiness is True


def test_certified_position_zero_market_value_does_not_suppress():
    """Zero market value is valid (closed-out position or new entry)."""
    pos = DeployPositionSizingInput(
        ticker="AAPL",
        current_market_value_usd=0.0,
        current_weight=0.0,
        trust_status=DeploySizingTrustStatus.CERTIFIED,
    )
    assert pos.suppresses_exact_dollar_readiness is False


# ──────────────────────────────────────────────────────────────────────────────
# 12. CERTIFIED position with weight > 1 suppresses
# ──────────────────────────────────────────────────────────────────────────────

def test_certified_position_weight_greater_than_one_suppresses():
    pos = DeployPositionSizingInput(
        ticker="AAPL",
        current_market_value_usd=5000.0,
        current_weight=1.5,
        trust_status=DeploySizingTrustStatus.CERTIFIED,
    )
    assert pos.suppresses_exact_dollar_readiness is True


def test_certified_position_weight_exactly_one_does_not_suppress():
    """Weight of exactly 1.0 (100% of portfolio) is a valid edge case."""
    pos = DeployPositionSizingInput(
        ticker="AAPL",
        current_market_value_usd=100_000.0,
        current_weight=1.0,
        trust_status=DeploySizingTrustStatus.CERTIFIED,
    )
    assert pos.suppresses_exact_dollar_readiness is False


def test_certified_position_negative_weight_suppresses():
    pos = DeployPositionSizingInput(
        ticker="AAPL",
        current_market_value_usd=5000.0,
        current_weight=-0.01,
        trust_status=DeploySizingTrustStatus.CERTIFIED,
    )
    assert pos.suppresses_exact_dollar_readiness is True


# ──────────────────────────────────────────────────────────────────────────────
# 13. Missing total portfolio value suppresses
# ──────────────────────────────────────────────────────────────────────────────

def test_missing_portfolio_value_suppresses_sizing_values_ready():
    bundle = DeploySizingInputBundle(
        cash=_certified_cash(),
        portfolio=DeployPortfolioSizingInput(
            total_portfolio_value_usd=None,
            trust_status=DeploySizingTrustStatus.MISSING,
        ),
        positions={"AAPL": _certified_position()},
    )
    assert bundle.sizing_values_ready is False


def test_none_portfolio_suppresses_sizing_values_ready():
    bundle = DeploySizingInputBundle(
        cash=_certified_cash(),
        portfolio=None,
        positions={"AAPL": _certified_position()},
    )
    assert bundle.sizing_values_ready is False


def test_missing_portfolio_suppression_reason():
    bundle = DeploySizingInputBundle(
        cash=_certified_cash(),
        portfolio=DeployPortfolioSizingInput(
            total_portfolio_value_usd=None,
            trust_status=DeploySizingTrustStatus.MISSING,
        ),
    )
    reasons = bundle.get_suppression_reasons()
    assert DeploySizingSuppressionReason.MISSING_PORTFOLIO_VALUE in reasons


# ──────────────────────────────────────────────────────────────────────────────
# 14. Stale total portfolio value suppresses
# ──────────────────────────────────────────────────────────────────────────────

def test_stale_portfolio_suppresses_sizing_values_ready():
    bundle = DeploySizingInputBundle(
        cash=_certified_cash(),
        portfolio=DeployPortfolioSizingInput(
            total_portfolio_value_usd=100_000.0,
            trust_status=DeploySizingTrustStatus.STALE,
        ),
        positions={"AAPL": _certified_position()},
    )
    assert bundle.sizing_values_ready is False


def test_stale_portfolio_suppression_reason():
    bundle = DeploySizingInputBundle(
        cash=_certified_cash(),
        portfolio=DeployPortfolioSizingInput(
            total_portfolio_value_usd=100_000.0,
            trust_status=DeploySizingTrustStatus.STALE,
        ),
    )
    reasons = bundle.get_suppression_reasons()
    assert DeploySizingSuppressionReason.STALE_PORTFOLIO_VALUE in reasons


# ──────────────────────────────────────────────────────────────────────────────
# 15. CERTIFIED portfolio with None or non-positive value suppresses
# ──────────────────────────────────────────────────────────────────────────────

def test_certified_portfolio_with_none_value_suppresses():
    port = DeployPortfolioSizingInput(
        total_portfolio_value_usd=None,
        trust_status=DeploySizingTrustStatus.CERTIFIED,
    )
    assert port.suppresses_exact_dollar_readiness is True


def test_certified_portfolio_with_zero_value_suppresses():
    port = DeployPortfolioSizingInput(
        total_portfolio_value_usd=0.0,
        trust_status=DeploySizingTrustStatus.CERTIFIED,
    )
    assert port.suppresses_exact_dollar_readiness is True


def test_certified_portfolio_with_negative_value_suppresses():
    port = DeployPortfolioSizingInput(
        total_portfolio_value_usd=-1.0,
        trust_status=DeploySizingTrustStatus.CERTIFIED,
    )
    assert port.suppresses_exact_dollar_readiness is True


def test_certified_portfolio_none_value_suppresses_bundle():
    bundle = DeploySizingInputBundle(
        cash=_certified_cash(),
        portfolio=DeployPortfolioSizingInput(
            total_portfolio_value_usd=None,
            trust_status=DeploySizingTrustStatus.CERTIFIED,
        ),
        positions={"AAPL": _certified_position()},
    )
    assert bundle.sizing_values_ready is False


def test_certified_portfolio_none_value_produces_invalid_portfolio_reason():
    bundle = DeploySizingInputBundle(
        cash=_certified_cash(),
        portfolio=DeployPortfolioSizingInput(
            total_portfolio_value_usd=None,
            trust_status=DeploySizingTrustStatus.CERTIFIED,
        ),
    )
    reasons = bundle.get_suppression_reasons()
    assert DeploySizingSuppressionReason.INVALID_PORTFOLIO_VALUE in reasons


def test_certified_portfolio_zero_value_produces_invalid_portfolio_reason():
    bundle = DeploySizingInputBundle(
        cash=_certified_cash(),
        portfolio=DeployPortfolioSizingInput(
            total_portfolio_value_usd=0.0,
            trust_status=DeploySizingTrustStatus.CERTIFIED,
        ),
    )
    reasons = bundle.get_suppression_reasons()
    assert DeploySizingSuppressionReason.INVALID_PORTFOLIO_VALUE in reasons


# ──────────────────────────────────────────────────────────────────────────────
# 16. Missing target allocation suppresses exact-dollar readiness
# ──────────────────────────────────────────────────────────────────────────────

def test_missing_target_allocation_suppresses_target_allocation_ready():
    """A position ticker with no target allocation entry makes target_allocation_ready=False."""
    bundle = _fully_certified_bundle(["AAPL"])  # No target allocations defined.
    assert bundle.target_allocation_ready is False


def test_missing_target_allocation_suppresses_exact_dollar_ready():
    bundle = _fully_certified_bundle(["AAPL"])
    assert bundle.exact_dollar_ready is False


def test_target_allocation_suppresses_method_for_missing_ticker():
    bundle = _fully_certified_bundle(["AAPL"])  # No target allocs.
    assert bundle.target_allocation_suppresses_exact_dollar_readiness("AAPL") is True


def test_target_allocation_missing_suppression_reason():
    bundle = _fully_certified_bundle(["AAPL"])
    reasons = bundle.get_suppression_reasons()
    assert DeploySizingSuppressionReason.TARGET_ALLOCATION_MISSING in reasons


# ──────────────────────────────────────────────────────────────────────────────
# 17. NOT_EVALUATED target allocation suppresses exact-dollar readiness
# ──────────────────────────────────────────────────────────────────────────────

def test_not_evaluated_target_allocation_suppresses_target_allocation_ready():
    bundle = DeploySizingInputBundle(
        cash=_certified_cash(),
        portfolio=_certified_portfolio(),
        positions={"AAPL": _certified_position()},
        target_allocations={
            "AAPL": DeployTargetAllocationInput(
                ticker="AAPL",
                target_weight=None,
                trust_status=DeploySizingTrustStatus.NOT_EVALUATED,
            ),
        },
    )
    assert bundle.target_allocation_ready is False
    assert bundle.exact_dollar_ready is False


def test_not_evaluated_target_allocation_suppresses_method():
    bundle = DeploySizingInputBundle(
        cash=_certified_cash(),
        portfolio=_certified_portfolio(),
        positions={"AAPL": _certified_position()},
        target_allocations={
            "AAPL": DeployTargetAllocationInput(
                ticker="AAPL",
                trust_status=DeploySizingTrustStatus.NOT_EVALUATED,
            ),
        },
    )
    assert bundle.target_allocation_suppresses_exact_dollar_readiness("AAPL") is True


def test_not_evaluated_target_allocation_suppression_reason():
    bundle = DeploySizingInputBundle(
        cash=_certified_cash(),
        portfolio=_certified_portfolio(),
        positions={"AAPL": _certified_position()},
        target_allocations={
            "AAPL": DeployTargetAllocationInput(ticker="AAPL", trust_status=DeploySizingTrustStatus.NOT_EVALUATED),
        },
    )
    reasons = bundle.get_suppression_reasons()
    assert DeploySizingSuppressionReason.TARGET_ALLOCATION_NOT_EVALUATED in reasons


# ──────────────────────────────────────────────────────────────────────────────
# 18. UNSUPPORTED policy suppresses exact-dollar readiness (policy_ready=False)
# ──────────────────────────────────────────────────────────────────────────────

def test_default_policy_is_unsupported_and_not_policy_ready():
    bundle = DeploySizingInputBundle(
        cash=_certified_cash(),
        portfolio=_certified_portfolio(),
        policy=DeploySizingPolicyPlaceholder(),  # Default = UNSUPPORTED.
    )
    assert bundle.policy_ready is False


def test_none_policy_is_not_policy_ready():
    bundle = DeploySizingInputBundle(
        cash=_certified_cash(),
        portfolio=_certified_portfolio(),
        policy=None,
    )
    assert bundle.policy_ready is False


def test_unsupported_policy_suppresses_exact_dollar_ready():
    """sizing_values_ready=True but policy UNSUPPORTED → exact_dollar_ready=False."""
    bundle = DeploySizingInputBundle(
        cash=_certified_cash(),
        portfolio=_certified_portfolio(),
        positions={"AAPL": _certified_position()},
        target_allocations={"AAPL": _certified_target_allocation("AAPL")},
        policy=DeploySizingPolicyPlaceholder(),  # UNSUPPORTED.
    )
    assert bundle.sizing_values_ready is True
    assert bundle.target_allocation_ready is True
    assert bundle.policy_ready is False
    assert bundle.exact_dollar_ready is False


def test_unsupported_policy_suppression_reasons():
    bundle = DeploySizingInputBundle(
        cash=_certified_cash(),
        portfolio=_certified_portfolio(),
        policy=DeploySizingPolicyPlaceholder(),
    )
    reasons = bundle.get_suppression_reasons()
    assert DeploySizingSuppressionReason.MINIMUM_TRADE_UNSUPPORTED in reasons
    assert DeploySizingSuppressionReason.ROUNDING_POLICY_UNSUPPORTED in reasons


# ──────────────────────────────────────────────────────────────────────────────
# 19. sizing_values_ready can be True while exact_dollar_ready is False
# ──────────────────────────────────────────────────────────────────────────────

def test_sizing_values_ready_true_while_exact_dollar_ready_false():
    """Certified cash/portfolio/positions → sizing_values_ready=True.
    But missing target allocs + UNSUPPORTED policy → exact_dollar_ready=False.
    This is the expected production-like Stage 2.1 state.
    """
    bundle = _fully_certified_bundle(["AAPL"])
    assert bundle.sizing_values_ready is True
    assert bundle.target_allocation_ready is False
    assert bundle.policy_ready is False
    assert bundle.exact_dollar_ready is False


def test_sizing_values_ready_true_policy_ready_false_still_blocks_exact_dollar():
    """Even with sizing and target allocation ready, missing policy keeps exact_dollar_ready=False."""
    bundle = DeploySizingInputBundle(
        cash=_certified_cash(),
        portfolio=_certified_portfolio(),
        positions={"AAPL": _certified_position()},
        target_allocations={"AAPL": _certified_target_allocation("AAPL")},
        # No policy provided.
    )
    assert bundle.sizing_values_ready is True
    assert bundle.target_allocation_ready is True
    assert bundle.policy_ready is False
    assert bundle.exact_dollar_ready is False


# ──────────────────────────────────────────────────────────────────────────────
# 20. exact_dollar_ready requires all three gates
# ──────────────────────────────────────────────────────────────────────────────

def test_fully_ready_bundle_is_exact_dollar_ready():
    """All three gates certified → exact_dollar_ready=True (readiness gate only, no math)."""
    bundle = _fully_ready_bundle(["AAPL"])
    assert bundle.sizing_values_ready is True
    assert bundle.target_allocation_ready is True
    assert bundle.policy_ready is True
    assert bundle.exact_dollar_ready is True


def test_exact_dollar_ready_true_does_not_mean_dollars_computed():
    """exact_dollar_ready=True signals readiness — no dollar amounts are computed anywhere."""
    bundle = _fully_ready_bundle()
    assert bundle.exact_dollar_ready is True
    # No dollar amounts live in the bundle.
    assert not hasattr(bundle, "recommended_dollar_amount")
    assert not hasattr(bundle, "estimated_share_quantity")


def test_missing_one_gate_blocks_exact_dollar_ready():
    """Verify each gate independently blocks exact_dollar_ready when absent."""
    # Gate 1 missing: no certified cash.
    b1 = DeploySizingInputBundle(
        cash=DeployCashInput(available_cash_usd=None, trust_status=DeploySizingTrustStatus.MISSING),
        portfolio=_certified_portfolio(),
        positions={"AAPL": _certified_position()},
        target_allocations={"AAPL": _certified_target_allocation()},
        policy=_certified_policy(),
    )
    assert b1.exact_dollar_ready is False

    # Gate 2 missing: no target allocation.
    b2 = DeploySizingInputBundle(
        cash=_certified_cash(),
        portfolio=_certified_portfolio(),
        positions={"AAPL": _certified_position()},
        # No target allocations.
        policy=_certified_policy(),
    )
    assert b2.exact_dollar_ready is False

    # Gate 3 missing: policy UNSUPPORTED.
    b3 = DeploySizingInputBundle(
        cash=_certified_cash(),
        portfolio=_certified_portfolio(),
        positions={"AAPL": _certified_position()},
        target_allocations={"AAPL": _certified_target_allocation()},
        policy=DeploySizingPolicyPlaceholder(),  # UNSUPPORTED.
    )
    assert b3.exact_dollar_ready is False


# ──────────────────────────────────────────────────────────────────────────────
# 21. Missing target allocation is not fabricated
# ──────────────────────────────────────────────────────────────────────────────

def test_target_allocation_absent_is_not_fabricated():
    """A ticker with no target allocation entry is absent — not fabricated."""
    bundle = _fully_certified_bundle(["AAPL"])
    ta = bundle.target_allocation_for("AAPL")
    assert ta is None  # Absent, not fabricated.


def test_target_allocation_not_evaluated_with_no_weight_is_not_fabricated():
    ta = DeployTargetAllocationInput(
        ticker="AAPL",
        target_weight=None,
        trust_status=DeploySizingTrustStatus.NOT_EVALUATED,
    )
    assert ta.is_fabricated is False


# ──────────────────────────────────────────────────────────────────────────────
# 22. Target allocation with non-None weight and non-CERTIFIED trust is fabricated
# ──────────────────────────────────────────────────────────────────────────────

def test_target_allocation_with_weight_and_not_evaluated_trust_is_fabricated():
    ta = DeployTargetAllocationInput(
        ticker="AAPL",
        target_weight=0.05,
        trust_status=DeploySizingTrustStatus.NOT_EVALUATED,
    )
    assert ta.is_fabricated is True


def test_target_allocation_with_weight_and_certified_trust_is_not_fabricated():
    ta = DeployTargetAllocationInput(
        ticker="AAPL",
        target_weight=0.05,
        trust_status=DeploySizingTrustStatus.CERTIFIED,
    )
    assert ta.is_fabricated is False


def test_has_fabricated_target_allocation_guardrail():
    bundle = DeploySizingInputBundle(
        cash=_certified_cash(),
        portfolio=_certified_portfolio(),
        target_allocations={
            "AAPL": DeployTargetAllocationInput(
                ticker="AAPL",
                target_weight=0.05,
                trust_status=DeploySizingTrustStatus.NOT_EVALUATED,
            ),
        },
    )
    assert bundle.has_fabricated_target_allocation() is True


def test_no_fabricated_target_allocations_when_all_are_absent():
    bundle = _fully_certified_bundle()
    assert bundle.has_fabricated_target_allocation() is False


# ──────────────────────────────────────────────────────────────────────────────
# 23. Conflicting sizing inputs suppress readiness
# ──────────────────────────────────────────────────────────────────────────────

def test_conflicting_position_suppresses_sizing_values_ready():
    bundle = DeploySizingInputBundle(
        cash=_certified_cash(),
        portfolio=_certified_portfolio(),
        positions={
            "AAPL": DeployPositionSizingInput(
                ticker="AAPL",
                current_market_value_usd=5000.0,
                current_weight=0.05,
                trust_status=DeploySizingTrustStatus.CONFLICTING,
            ),
        },
    )
    assert bundle.sizing_values_ready is False


def test_conflicting_portfolio_suppresses_sizing_values_ready():
    bundle = DeploySizingInputBundle(
        cash=_certified_cash(),
        portfolio=DeployPortfolioSizingInput(
            total_portfolio_value_usd=100_000.0,
            trust_status=DeploySizingTrustStatus.CONFLICTING,
        ),
    )
    assert bundle.sizing_values_ready is False


def test_weak_portfolio_suppression_reason():
    bundle = DeploySizingInputBundle(
        cash=_certified_cash(),
        portfolio=DeployPortfolioSizingInput(
            total_portfolio_value_usd=100_000.0,
            trust_status=DeploySizingTrustStatus.WEAK,
        ),
    )
    reasons = bundle.get_suppression_reasons()
    assert DeploySizingSuppressionReason.WEAK_PORTFOLIO_VALUE in reasons


def test_conflicting_portfolio_suppression_reason():
    bundle = DeploySizingInputBundle(
        cash=_certified_cash(),
        portfolio=DeployPortfolioSizingInput(
            total_portfolio_value_usd=100_000.0,
            trust_status=DeploySizingTrustStatus.CONFLICTING,
        ),
    )
    reasons = bundle.get_suppression_reasons()
    assert DeploySizingSuppressionReason.CONFLICTING_PORTFOLIO_VALUE in reasons


def test_weak_position_suppression_reason():
    bundle = DeploySizingInputBundle(
        cash=_certified_cash(),
        portfolio=_certified_portfolio(),
        positions={
            "AAPL": DeployPositionSizingInput(
                ticker="AAPL",
                current_market_value_usd=5000.0,
                current_weight=0.05,
                trust_status=DeploySizingTrustStatus.WEAK,
            ),
        },
    )
    reasons = bundle.get_suppression_reasons()
    assert DeploySizingSuppressionReason.WEAK_POSITION_VALUE in reasons


def test_conflicting_position_suppression_reason():
    bundle = DeploySizingInputBundle(
        cash=_certified_cash(),
        portfolio=_certified_portfolio(),
        positions={
            "AAPL": DeployPositionSizingInput(
                ticker="AAPL",
                current_market_value_usd=5000.0,
                current_weight=0.05,
                trust_status=DeploySizingTrustStatus.CONFLICTING,
            ),
        },
    )
    reasons = bundle.get_suppression_reasons()
    assert DeploySizingSuppressionReason.CONFLICTING_POSITION_VALUE in reasons


# ──────────────────────────────────────────────────────────────────────────────
# 24. Minimum-trade and rounding policy remain placeholders (UNSUPPORTED)
# ──────────────────────────────────────────────────────────────────────────────

def test_policy_placeholder_is_unsupported():
    policy = DeploySizingPolicyPlaceholder()
    assert policy.trust_status == DeploySizingTrustStatus.UNSUPPORTED
    assert policy.suppresses_exact_dollar_readiness is True


def test_policy_rounding_policy_is_placeholder():
    policy = DeploySizingPolicyPlaceholder()
    assert policy.rounding_policy == "not_implemented_yet"


def test_policy_minimum_trade_is_none_by_default():
    policy = DeploySizingPolicyPlaceholder()
    assert policy.minimum_trade_usd is None


def test_builder_produces_unsupported_policy():
    bundle = build_sizing_context_from_portfolio_snapshot({
        "available_cash_usd": 10_000.0,
        "cash_trust_status": "CERTIFIED",
        "total_portfolio_value_usd": 100_000.0,
        "portfolio_trust_status": "CERTIFIED",
    })
    assert bundle.policy is not None
    assert bundle.policy.trust_status == DeploySizingTrustStatus.UNSUPPORTED
    assert bundle.policy.suppresses_exact_dollar_readiness is True


# ──────────────────────────────────────────────────────────────────────────────
# 25. recommended_dollar_amount and estimated_share_quantity remain None
# ──────────────────────────────────────────────────────────────────────────────

def test_sizing_bundle_has_no_recommended_dollar_amount_field():
    """DeploySizingInputBundle does not expose dollar amounts — those live in DeployPlanItem."""
    bundle = _fully_ready_bundle()
    assert not hasattr(bundle, "recommended_dollar_amount")
    assert not hasattr(bundle, "estimated_share_quantity")


def test_deploy_plan_items_dollar_fields_remain_null_with_certified_sizing():
    """Even with a fully-ready sizing bundle, DeployPlanItem dollar fields remain None."""
    from app.services.deploy.deploy_contracts import DeployPlanInput, DeployActionSource
    from app.services.deploy.deploy_translation_v1 import build_deploy_plan

    plan_input = DeployPlanInput(
        ticker="AAPL",
        intel_action="BUY",
        intel_conviction="HIGH",
        intel_evidence_band="STRONG",
        intel_snapshot_id="snap-001",
        intel_run_id="run-001",
        has_missing_evidence=False,
        has_stale_evidence=False,
        has_weak_evidence=False,
        is_blocked=False,
        action_source=DeployActionSource.INTEL_V3,
    )
    plan = build_deploy_plan([plan_input])
    assert plan.items[0].recommended_dollar_amount is None
    assert plan.items[0].estimated_share_quantity is None


# ──────────────────────────────────────────────────────────────────────────────
# 26. Sizing inputs cannot change Intel action or actionability
# ──────────────────────────────────────────────────────────────────────────────

def test_certified_sizing_bundle_does_not_change_intel_action():
    from app.services.deploy.deploy_contracts import DeployPlanInput, DeployActionSource
    from app.services.deploy.deploy_translation_v1 import build_deploy_plan

    plan_input = DeployPlanInput(
        ticker="AAPL", intel_action="BUY", intel_conviction="HIGH",
        intel_evidence_band="STRONG", intel_snapshot_id="snap-001", intel_run_id="run-001",
        action_source=DeployActionSource.INTEL_V3,
    )
    plan = build_deploy_plan([plan_input])
    _fully_ready_bundle(["AAPL"])  # Construct — does not interact with plan.
    assert plan.items[0].intel_action == "BUY"


def test_certified_sizing_bundle_does_not_change_actionability_status():
    from app.services.deploy.deploy_contracts import DeployPlanInput, DeployActionSource, DeployActionabilityStatus
    from app.services.deploy.deploy_translation_v1 import build_deploy_plan

    plan_input = DeployPlanInput(
        ticker="AAPL", intel_action="BUY", intel_conviction="HIGH",
        intel_evidence_band="STRONG", intel_snapshot_id="snap-001", intel_run_id="run-001",
        has_missing_evidence=False, has_stale_evidence=False, has_weak_evidence=False, is_blocked=False,
        action_source=DeployActionSource.INTEL_V3,
    )
    plan = build_deploy_plan([plan_input])
    _fully_ready_bundle(["AAPL"])
    assert plan.items[0].actionability_status == DeployActionabilityStatus.ACTIONABLE_CANDIDATE


def test_sizing_inputs_cannot_create_buy_candidate_for_hold():
    from app.services.deploy.deploy_contracts import DeployPlanInput, DeployActionSource, DeployActionabilityStatus
    from app.services.deploy.deploy_translation_v1 import build_deploy_plan

    plan_input = DeployPlanInput(
        ticker="GOOG", intel_action="HOLD", intel_conviction="HIGH",
        intel_evidence_band="STRONG", intel_snapshot_id="snap-001", intel_run_id="run-001",
        action_source=DeployActionSource.INTEL_V3,
    )
    plan = build_deploy_plan([plan_input])
    _fully_ready_bundle(["GOOG"])  # Certified sizing — cannot override HOLD.
    assert plan.items[0].actionability_status == DeployActionabilityStatus.NOT_ACTIONABLE_HOLD


# ──────────────────────────────────────────────────────────────────────────────
# 27. HOLD remains non-actionable even with all sizing gates certified
# ──────────────────────────────────────────────────────────────────────────────

def test_hold_non_actionable_with_fully_ready_sizing():
    from app.services.deploy.deploy_contracts import DeployPlanInput, DeployActionSource, DeployActionabilityStatus
    from app.services.deploy.deploy_translation_v1 import build_deploy_plan

    plan_input = DeployPlanInput(
        ticker="GOOG", intel_action="HOLD", intel_conviction="HIGH",
        intel_evidence_band="STRONG", intel_snapshot_id="snap-001", intel_run_id="run-001",
        action_source=DeployActionSource.INTEL_V3,
    )
    plan = build_deploy_plan([plan_input])
    bundle = _fully_ready_bundle(["GOOG"])
    assert bundle.exact_dollar_ready is True  # All gates certified.
    # But HOLD remains non-actionable regardless.
    assert plan.items[0].actionability_status == DeployActionabilityStatus.NOT_ACTIONABLE_HOLD
    assert plan.guardrail_summary.hold_never_actionable is True


# ──────────────────────────────────────────────────────────────────────────────
# 28. BUY/TRIM/SELL remain Intel-derived candidates only
# ──────────────────────────────────────────────────────────────────────────────

def test_buy_candidate_is_intel_derived_not_sizing_derived():
    from app.services.deploy.deploy_contracts import DeployPlanInput, DeployActionSource, DeployActionabilityStatus
    from app.services.deploy.deploy_translation_v1 import build_deploy_plan

    plan_input = DeployPlanInput(
        ticker="AAPL", intel_action="BUY", intel_conviction="MEDIUM",
        intel_evidence_band="PARTIAL", intel_snapshot_id="snap-001", intel_run_id="run-001",
        action_source=DeployActionSource.INTEL_V3,
    )
    plan = build_deploy_plan([plan_input])
    assert plan.items[0].actionability_status == DeployActionabilityStatus.ACTIONABLE_CANDIDATE
    assert plan.items[0].intel_action == "BUY"
    assert plan.guardrail_summary.buy_candidates == 1


def test_no_buy_candidate_without_intel_buy_action():
    from app.services.deploy.deploy_contracts import DeployPlanInput, DeployActionSource
    from app.services.deploy.deploy_translation_v1 import build_deploy_plan

    plan_input = DeployPlanInput(
        ticker="AAPL", intel_action="HOLD", intel_conviction="MEDIUM",
        intel_evidence_band="PARTIAL", intel_snapshot_id="snap-001", intel_run_id="run-001",
        action_source=DeployActionSource.INTEL_V3,
    )
    plan = build_deploy_plan([plan_input])
    assert plan.guardrail_summary.buy_candidates == 0


# ──────────────────────────────────────────────────────────────────────────────
# 29. No SQL/UI/route/provider/LLM/broker files touched — module import check
# ──────────────────────────────────────────────────────────────────────────────

def test_sizing_contracts_no_sql_imports():
    import importlib.util
    src = importlib.util.find_spec("app.services.deploy.deploy_sizing_contracts").origin
    with open(src) as f:
        text = f.read()
    for forbidden in ["supabase", "psycopg", "sqlalchemy", "asyncpg", "requests", "httpx", "aiohttp"]:
        assert forbidden not in text, f"deploy_sizing_contracts.py must not import {forbidden}"


def test_sizing_builder_no_sql_imports():
    import importlib.util
    src = importlib.util.find_spec("app.services.deploy.deploy_sizing_builder").origin
    with open(src) as f:
        text = f.read()
    for forbidden in ["supabase", "psycopg", "sqlalchemy", "asyncpg", "requests", "httpx", "aiohttp"]:
        assert forbidden not in text, f"deploy_sizing_builder.py must not import {forbidden}"


def test_sizing_contracts_no_intel_internals_import():
    import importlib.util
    src = importlib.util.find_spec("app.services.deploy.deploy_sizing_contracts").origin
    with open(src) as f:
        text = f.read()
    assert "from ..intelligence" not in text
    assert "from app.services.intelligence" not in text
    assert "intel_v3_service" not in text
    assert "RecommendationService" not in text


# ──────────────────────────────────────────────────────────────────────────────
# 30. Builder produces correct bundle from portfolio snapshot dict
# ──────────────────────────────────────────────────────────────────────────────

def test_builder_produces_certified_cash_when_specified():
    bundle = build_sizing_context_from_portfolio_snapshot({
        "available_cash_usd": 12_000.0,
        "cash_trust_status": "CERTIFIED",
    })
    assert bundle.cash.available_cash_usd == 12_000.0
    assert bundle.cash.trust_status == DeploySizingTrustStatus.CERTIFIED


def test_builder_missing_cash_trust_defaults_to_missing():
    bundle = build_sizing_context_from_portfolio_snapshot({})
    assert bundle.cash.trust_status == DeploySizingTrustStatus.MISSING
    assert bundle.cash.suppresses_exact_dollar_readiness is True


def test_builder_produces_certified_portfolio_when_specified():
    bundle = build_sizing_context_from_portfolio_snapshot({
        "total_portfolio_value_usd": 150_000.0,
        "portfolio_trust_status": "CERTIFIED",
    })
    assert bundle.portfolio.total_portfolio_value_usd == 150_000.0
    assert bundle.portfolio.trust_status == DeploySizingTrustStatus.CERTIFIED


def test_builder_missing_portfolio_trust_defaults_to_missing():
    bundle = build_sizing_context_from_portfolio_snapshot({})
    assert bundle.portfolio.trust_status == DeploySizingTrustStatus.MISSING
    assert bundle.sizing_values_ready is False


def test_builder_produces_certified_positions():
    bundle = build_sizing_context_from_portfolio_snapshot({
        "available_cash_usd": 10_000.0,
        "cash_trust_status": "CERTIFIED",
        "total_portfolio_value_usd": 100_000.0,
        "portfolio_trust_status": "CERTIFIED",
        "positions": [
            {
                "ticker": "AAPL",
                "current_market_value_usd": 5_000.0,
                "current_weight": 0.05,
                "trust_status": "CERTIFIED",
            },
        ],
    })
    assert "AAPL" in bundle.positions
    assert bundle.positions["AAPL"].trust_status == DeploySizingTrustStatus.CERTIFIED
    assert bundle.sizing_values_ready is True
    # exact_dollar_ready remains False — no target allocs or policy.
    assert bundle.exact_dollar_ready is False


def test_builder_missing_position_trust_defaults_to_missing():
    bundle = build_sizing_context_from_portfolio_snapshot({
        "positions": [{"ticker": "AAPL", "current_market_value_usd": 5000.0}],
    })
    assert bundle.positions["AAPL"].trust_status == DeploySizingTrustStatus.MISSING


def test_builder_target_allocation_defaults_to_not_evaluated():
    bundle = build_sizing_context_from_portfolio_snapshot({
        "target_allocations": [{"ticker": "AAPL"}],
    })
    assert bundle.target_allocations["AAPL"].trust_status == DeploySizingTrustStatus.NOT_EVALUATED


def test_builder_with_none_input_returns_suppressed_bundle():
    bundle = build_sizing_context_from_portfolio_snapshot(None)
    assert bundle.sizing_values_ready is False
    assert bundle.exact_dollar_ready is False
    assert bundle.cash.trust_status == DeploySizingTrustStatus.MISSING


def test_builder_unknown_trust_status_string_falls_back_gracefully():
    bundle = build_sizing_context_from_portfolio_snapshot({
        "cash_trust_status": "TOTALLY_MADE_UP_VALUE",
        "available_cash_usd": 5000.0,
    })
    assert bundle.cash.trust_status == DeploySizingTrustStatus.MISSING


# ──────────────────────────────────────────────────────────────────────────────
# 31. Unknown ticker in positions dict treated as MISSING
# ──────────────────────────────────────────────────────────────────────────────

def test_position_suppresses_dollar_readiness_for_unknown_ticker():
    bundle = _fully_certified_bundle(["AAPL"])
    assert bundle.position_suppresses_dollar_readiness("NVDA") is True


# ──────────────────────────────────────────────────────────────────────────────
# 32. has_fabricated_target_allocation guardrail
# ──────────────────────────────────────────────────────────────────────────────

def test_fabricated_target_allocation_detected():
    bundle = DeploySizingInputBundle(
        cash=_certified_cash(),
        portfolio=_certified_portfolio(),
        target_allocations={
            "AAPL": DeployTargetAllocationInput(
                ticker="AAPL",
                target_weight=0.10,
                trust_status=DeploySizingTrustStatus.UNSUPPORTED,
            ),
        },
    )
    assert bundle.has_fabricated_target_allocation() is True


def test_not_fabricated_when_weight_is_none():
    bundle = DeploySizingInputBundle(
        cash=_certified_cash(),
        portfolio=_certified_portfolio(),
        target_allocations={
            "AAPL": DeployTargetAllocationInput(
                ticker="AAPL",
                target_weight=None,
                trust_status=DeploySizingTrustStatus.NOT_EVALUATED,
            ),
        },
    )
    assert bundle.has_fabricated_target_allocation() is False


def test_not_fabricated_when_certified_weight():
    bundle = DeploySizingInputBundle(
        cash=_certified_cash(),
        portfolio=_certified_portfolio(),
        target_allocations={
            "AAPL": DeployTargetAllocationInput(
                ticker="AAPL",
                target_weight=0.05,
                trust_status=DeploySizingTrustStatus.CERTIFIED,
            ),
        },
    )
    assert bundle.has_fabricated_target_allocation() is False


# ──────────────────────────────────────────────────────────────────────────────
# 33. Suppression reasons enumerate active suppressions including value-level
# ──────────────────────────────────────────────────────────────────────────────

def test_fully_ready_bundle_has_no_suppression_reasons():
    """A fully ready bundle (all three gates satisfied) has no suppression reasons."""
    bundle = _fully_ready_bundle()
    reasons = bundle.get_suppression_reasons()
    assert reasons == []


def test_multiple_suppressions_all_enumerated():
    bundle = DeploySizingInputBundle(
        cash=DeployCashInput(available_cash_usd=None, trust_status=DeploySizingTrustStatus.MISSING),
        portfolio=DeployPortfolioSizingInput(
            total_portfolio_value_usd=None,
            trust_status=DeploySizingTrustStatus.STALE,
        ),
        positions={
            "AAPL": DeployPositionSizingInput(
                ticker="AAPL",
                current_market_value_usd=None,
                current_weight=None,
                trust_status=DeploySizingTrustStatus.MISSING,
            ),
        },
    )
    reasons = bundle.get_suppression_reasons()
    assert DeploySizingSuppressionReason.MISSING_CASH in reasons
    assert DeploySizingSuppressionReason.STALE_PORTFOLIO_VALUE in reasons
    assert DeploySizingSuppressionReason.MISSING_POSITION_VALUE in reasons


def test_certified_inputs_with_invalid_values_produce_value_level_reasons():
    bundle = DeploySizingInputBundle(
        cash=DeployCashInput(available_cash_usd=-1.0, trust_status=DeploySizingTrustStatus.CERTIFIED),
        portfolio=DeployPortfolioSizingInput(
            total_portfolio_value_usd=0.0,
            trust_status=DeploySizingTrustStatus.CERTIFIED,
        ),
        positions={
            "AAPL": DeployPositionSizingInput(
                ticker="AAPL",
                current_market_value_usd=None,
                current_weight=1.5,
                trust_status=DeploySizingTrustStatus.CERTIFIED,
            ),
        },
    )
    reasons = bundle.get_suppression_reasons()
    assert DeploySizingSuppressionReason.INVALID_CASH_VALUE in reasons
    assert DeploySizingSuppressionReason.INVALID_PORTFOLIO_VALUE in reasons
    assert DeploySizingSuppressionReason.INVALID_POSITION_VALUE in reasons


# ──────────────────────────────────────────────────────────────────────────────
# 34. target_allocation_suppresses_exact_dollar_readiness method
# ──────────────────────────────────────────────────────────────────────────────

def test_target_allocation_suppresses_method_for_certified_valid_allocation():
    bundle = _fully_ready_bundle(["AAPL"])
    assert bundle.target_allocation_suppresses_exact_dollar_readiness("AAPL") is False


def test_target_allocation_suppresses_method_for_missing_ticker():
    bundle = _fully_certified_bundle(["AAPL"])
    assert bundle.target_allocation_suppresses_exact_dollar_readiness("AAPL") is True  # Not in target_allocations.


def test_target_allocation_suppresses_method_for_certified_invalid_weight():
    bundle = DeploySizingInputBundle(
        cash=_certified_cash(),
        portfolio=_certified_portfolio(),
        positions={"AAPL": _certified_position()},
        target_allocations={
            "AAPL": DeployTargetAllocationInput(
                ticker="AAPL",
                target_weight=1.5,  # Out of range.
                trust_status=DeploySizingTrustStatus.CERTIFIED,
            ),
        },
    )
    assert bundle.target_allocation_suppresses_exact_dollar_readiness("AAPL") is True


def test_target_allocation_invalid_weight_suppression_reason():
    bundle = DeploySizingInputBundle(
        cash=_certified_cash(),
        portfolio=_certified_portfolio(),
        positions={"AAPL": _certified_position()},
        target_allocations={
            "AAPL": DeployTargetAllocationInput(
                ticker="AAPL",
                target_weight=1.5,
                trust_status=DeploySizingTrustStatus.CERTIFIED,
            ),
        },
    )
    reasons = bundle.get_suppression_reasons()
    assert DeploySizingSuppressionReason.TARGET_ALLOCATION_INVALID in reasons


# ──────────────────────────────────────────────────────────────────────────────
# 35. is_ready_for_math on DeployTargetAllocationInput
# ──────────────────────────────────────────────────────────────────────────────

def test_target_allocation_is_ready_for_math_certified_valid():
    ta = DeployTargetAllocationInput(
        ticker="AAPL",
        target_weight=0.05,
        trust_status=DeploySizingTrustStatus.CERTIFIED,
    )
    assert ta.is_ready_for_math is True


def test_target_allocation_is_not_ready_for_math_not_evaluated():
    ta = DeployTargetAllocationInput(ticker="AAPL", trust_status=DeploySizingTrustStatus.NOT_EVALUATED)
    assert ta.is_ready_for_math is False


def test_target_allocation_is_not_ready_for_math_certified_none_weight():
    ta = DeployTargetAllocationInput(
        ticker="AAPL",
        target_weight=None,
        trust_status=DeploySizingTrustStatus.CERTIFIED,
    )
    assert ta.is_ready_for_math is False


def test_target_allocation_is_not_ready_for_math_certified_out_of_range():
    ta = DeployTargetAllocationInput(
        ticker="AAPL",
        target_weight=1.1,
        trust_status=DeploySizingTrustStatus.CERTIFIED,
    )
    assert ta.is_ready_for_math is False


def test_target_allocation_is_ready_for_math_boundary_values():
    """Weights at exactly 0.0 and 1.0 are valid."""
    ta_zero = DeployTargetAllocationInput(
        ticker="AAPL", target_weight=0.0, trust_status=DeploySizingTrustStatus.CERTIFIED
    )
    ta_one = DeployTargetAllocationInput(
        ticker="AAPL", target_weight=1.0, trust_status=DeploySizingTrustStatus.CERTIFIED
    )
    assert ta_zero.is_ready_for_math is True
    assert ta_one.is_ready_for_math is True


# ──────────────────────────────────────────────────────────────────────────────
# 36. Schema version is deploy_sizing_v1_contract
# ──────────────────────────────────────────────────────────────────────────────

def test_bundle_schema_version():
    bundle = _fully_certified_bundle()
    assert bundle.schema_version == "deploy_sizing_v1_contract"


def test_builder_bundle_schema_version():
    bundle = build_sizing_context_from_portfolio_snapshot({})
    assert bundle.schema_version == "deploy_sizing_v1_contract"


# ──────────────────────────────────────────────────────────────────────────────
# DeploySizingTrustStatus completeness
# ──────────────────────────────────────────────────────────────────────────────

def test_all_trust_status_values_defined():
    values = {s.value for s in DeploySizingTrustStatus}
    assert "CERTIFIED" in values
    assert "MISSING" in values
    assert "STALE" in values
    assert "WEAK" in values
    assert "CONFLICTING" in values
    assert "NOT_EVALUATED" in values
    assert "UNSUPPORTED" in values


def test_certified_is_not_suppressing():
    cash = DeployCashInput(
        available_cash_usd=10_000.0,
        trust_status=DeploySizingTrustStatus.CERTIFIED,
    )
    assert cash.suppresses_exact_dollar_readiness is False


def test_all_non_certified_statuses_suppress():
    for status in [
        DeploySizingTrustStatus.MISSING,
        DeploySizingTrustStatus.STALE,
        DeploySizingTrustStatus.WEAK,
        DeploySizingTrustStatus.CONFLICTING,
        DeploySizingTrustStatus.NOT_EVALUATED,
        DeploySizingTrustStatus.UNSUPPORTED,
    ]:
        cash = DeployCashInput(available_cash_usd=5000.0, trust_status=status)
        assert cash.suppresses_exact_dollar_readiness is True, f"{status} should suppress"
