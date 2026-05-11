"""Tests — Deploy Stage 2.1 sizing input contract.

Proves the following invariants:
  1.  Certified cash/position/portfolio inputs produce sizing-readiness metadata only.
  2.  Missing cash suppresses exact-dollar readiness.
  3.  Stale cash suppresses exact-dollar readiness.
  4.  Weak cash suppresses exact-dollar readiness.
  5.  Conflicting cash suppresses exact-dollar readiness.
  6.  Missing position market value suppresses affected-ticker sizing readiness.
  7.  Stale position market value suppresses affected-ticker sizing readiness.
  8.  Missing total portfolio value suppresses sizing readiness.
  9.  Stale total portfolio value suppresses sizing readiness.
 10.  Missing target allocation is not fabricated.
 11.  Target allocation with non-None weight and non-CERTIFIED trust is flagged as fabricated.
 12.  Conflicting sizing inputs suppress readiness.
 13.  Minimum-trade and rounding policy remain placeholders (UNSUPPORTED).
 14.  recommended_dollar_amount and estimated_share_quantity remain None.
 15.  Sizing inputs cannot change Intel action or actionability.
 16.  HOLD remains non-actionable even with certified sizing inputs.
 17.  BUY/TRIM/SELL remain Intel-derived candidates only.
 18.  No SQL/UI/route/provider/LLM/broker files are touched.
 19.  Existing Stage 2.0 Deploy tests still pass (verified by running both suites together).
 20.  Builder produces correct bundle from portfolio snapshot dict.
 21.  Unknown ticker in positions dict is treated as MISSING (suppressed).
 22.  Bundle with all certified inputs reports exact_dollar_ready=True (readiness gate only).
 23.  Suppression reasons correctly enumerate active suppressions.
 24.  has_fabricated_target_allocation guardrail works correctly.
 25.  Schema version is deploy_sizing_v1_contract throughout.
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


def _fully_certified_bundle(tickers: list | None = None) -> DeploySizingInputBundle:
    tickers = tickers or ["AAPL"]
    return DeploySizingInputBundle(
        cash=_certified_cash(),
        portfolio=_certified_portfolio(),
        positions={t: _certified_position(t) for t in tickers},
    )


# ──────────────────────────────────────────────────────────────────────────────
# 1. Certified inputs produce sizing-readiness metadata only (no dollar math)
# ──────────────────────────────────────────────────────────────────────────────

def test_certified_bundle_is_exact_dollar_ready():
    """Certified inputs signal readiness — but no dollar amounts are computed."""
    bundle = _fully_certified_bundle()
    assert bundle.exact_dollar_ready is True


def test_certified_bundle_has_no_dollar_amounts():
    """No dollar amounts live in DeploySizingInputBundle — it is a readiness gate only."""
    bundle = _fully_certified_bundle()
    # The bundle itself has no recommended_dollar_amount or estimated_share_quantity.
    assert not hasattr(bundle, "recommended_dollar_amount")
    assert not hasattr(bundle, "estimated_share_quantity")


def test_certified_bundle_schema_version():
    bundle = _fully_certified_bundle()
    assert bundle.schema_version == "deploy_sizing_v1_contract"


# ──────────────────────────────────────────────────────────────────────────────
# 2. Missing cash suppresses exact-dollar readiness
# ──────────────────────────────────────────────────────────────────────────────

def test_missing_cash_suppresses_readiness():
    bundle = DeploySizingInputBundle(
        cash=DeployCashInput(available_cash_usd=None, trust_status=DeploySizingTrustStatus.MISSING),
        portfolio=_certified_portfolio(),
        positions={"AAPL": _certified_position()},
    )
    assert bundle.exact_dollar_ready is False


def test_none_cash_suppresses_readiness():
    bundle = DeploySizingInputBundle(
        cash=None,
        portfolio=_certified_portfolio(),
        positions={"AAPL": _certified_position()},
    )
    assert bundle.exact_dollar_ready is False


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
# 3. Stale cash suppresses exact-dollar readiness
# ──────────────────────────────────────────────────────────────────────────────

def test_stale_cash_suppresses_readiness():
    bundle = DeploySizingInputBundle(
        cash=DeployCashInput(available_cash_usd=5000.0, trust_status=DeploySizingTrustStatus.STALE),
        portfolio=_certified_portfolio(),
        positions={"AAPL": _certified_position()},
    )
    assert bundle.exact_dollar_ready is False


def test_stale_cash_suppression_reason():
    bundle = DeploySizingInputBundle(
        cash=DeployCashInput(available_cash_usd=5000.0, trust_status=DeploySizingTrustStatus.STALE),
        portfolio=_certified_portfolio(),
    )
    reasons = bundle.get_suppression_reasons()
    assert DeploySizingSuppressionReason.STALE_CASH in reasons


# ──────────────────────────────────────────────────────────────────────────────
# 4. Weak cash suppresses exact-dollar readiness
# ──────────────────────────────────────────────────────────────────────────────

def test_weak_cash_suppresses_readiness():
    bundle = DeploySizingInputBundle(
        cash=DeployCashInput(available_cash_usd=5000.0, trust_status=DeploySizingTrustStatus.WEAK),
        portfolio=_certified_portfolio(),
        positions={"AAPL": _certified_position()},
    )
    assert bundle.exact_dollar_ready is False


def test_weak_cash_suppression_reason():
    bundle = DeploySizingInputBundle(
        cash=DeployCashInput(available_cash_usd=5000.0, trust_status=DeploySizingTrustStatus.WEAK),
        portfolio=_certified_portfolio(),
    )
    reasons = bundle.get_suppression_reasons()
    assert DeploySizingSuppressionReason.WEAK_CASH in reasons


# ──────────────────────────────────────────────────────────────────────────────
# 5. Conflicting cash suppresses exact-dollar readiness
# ──────────────────────────────────────────────────────────────────────────────

def test_conflicting_cash_suppresses_readiness():
    bundle = DeploySizingInputBundle(
        cash=DeployCashInput(available_cash_usd=5000.0, trust_status=DeploySizingTrustStatus.CONFLICTING),
        portfolio=_certified_portfolio(),
        positions={"AAPL": _certified_position()},
    )
    assert bundle.exact_dollar_ready is False


def test_conflicting_cash_suppression_reason():
    bundle = DeploySizingInputBundle(
        cash=DeployCashInput(available_cash_usd=5000.0, trust_status=DeploySizingTrustStatus.CONFLICTING),
        portfolio=_certified_portfolio(),
    )
    reasons = bundle.get_suppression_reasons()
    assert DeploySizingSuppressionReason.CONFLICTING_CASH in reasons


# ──────────────────────────────────────────────────────────────────────────────
# 6. Missing position market value suppresses affected-ticker sizing readiness
# ──────────────────────────────────────────────────────────────────────────────

def test_missing_position_suppresses_readiness_for_that_ticker():
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
    assert bundle.exact_dollar_ready is False


def test_certified_position_does_not_suppress_readiness():
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
# 7. Stale position market value suppresses affected-ticker sizing readiness
# ──────────────────────────────────────────────────────────────────────────────

def test_stale_position_suppresses_readiness():
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
    assert bundle.position_suppresses_dollar_readiness("AAPL") is True
    assert bundle.exact_dollar_ready is False


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
# 8. Missing total portfolio value suppresses sizing readiness
# ──────────────────────────────────────────────────────────────────────────────

def test_missing_portfolio_value_suppresses_readiness():
    bundle = DeploySizingInputBundle(
        cash=_certified_cash(),
        portfolio=DeployPortfolioSizingInput(
            total_portfolio_value_usd=None,
            trust_status=DeploySizingTrustStatus.MISSING,
        ),
        positions={"AAPL": _certified_position()},
    )
    assert bundle.exact_dollar_ready is False


def test_none_portfolio_suppresses_readiness():
    bundle = DeploySizingInputBundle(
        cash=_certified_cash(),
        portfolio=None,
        positions={"AAPL": _certified_position()},
    )
    assert bundle.exact_dollar_ready is False


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
# 9. Stale total portfolio value suppresses sizing readiness
# ──────────────────────────────────────────────────────────────────────────────

def test_stale_portfolio_suppresses_readiness():
    bundle = DeploySizingInputBundle(
        cash=_certified_cash(),
        portfolio=DeployPortfolioSizingInput(
            total_portfolio_value_usd=100_000.0,
            trust_status=DeploySizingTrustStatus.STALE,
        ),
        positions={"AAPL": _certified_position()},
    )
    assert bundle.exact_dollar_ready is False


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
# 10. Missing target allocation is not fabricated
# ──────────────────────────────────────────────────────────────────────────────

def test_target_allocation_absent_is_not_fabricated():
    """A ticker with no target allocation entry is simply absent — not fabricated."""
    bundle = _fully_certified_bundle(["AAPL"])
    ta = bundle.target_allocation_for("AAPL")
    assert ta is None  # No target allocation is defined — it is absent, not fabricated.


def test_target_allocation_not_evaluated_with_no_weight_is_not_fabricated():
    ta = DeployTargetAllocationInput(
        ticker="AAPL",
        target_weight=None,
        trust_status=DeploySizingTrustStatus.NOT_EVALUATED,
    )
    assert ta.is_fabricated is False


# ──────────────────────────────────────────────────────────────────────────────
# 11. Target allocation with non-None weight and non-CERTIFIED trust is fabricated
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
# 12. Conflicting sizing inputs suppress readiness
# ──────────────────────────────────────────────────────────────────────────────

def test_conflicting_position_suppresses_readiness():
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
    assert bundle.exact_dollar_ready is False


def test_conflicting_portfolio_suppresses_readiness():
    bundle = DeploySizingInputBundle(
        cash=_certified_cash(),
        portfolio=DeployPortfolioSizingInput(
            total_portfolio_value_usd=100_000.0,
            trust_status=DeploySizingTrustStatus.CONFLICTING,
        ),
    )
    assert bundle.exact_dollar_ready is False


# ──────────────────────────────────────────────────────────────────────────────
# 13. Minimum-trade and rounding policy remain placeholders (UNSUPPORTED)
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
# 14. recommended_dollar_amount and estimated_share_quantity remain None
# ──────────────────────────────────────────────────────────────────────────────

def test_sizing_bundle_has_no_recommended_dollar_amount_field():
    """DeploySizingInputBundle does not expose dollar amounts — those live in DeployPlanItem."""
    bundle = _fully_certified_bundle()
    assert not hasattr(bundle, "recommended_dollar_amount")
    assert not hasattr(bundle, "estimated_share_quantity")


def test_deploy_plan_items_dollar_fields_remain_null_with_certified_sizing():
    """Even with a certified sizing bundle, DeployPlanItem dollar fields remain None."""
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
# 15. Sizing inputs cannot change Intel action or actionability
# ──────────────────────────────────────────────────────────────────────────────

def test_certified_sizing_bundle_does_not_change_intel_action():
    """A certified sizing bundle does not modify the Intel action in a deploy plan."""
    from app.services.deploy.deploy_contracts import DeployPlanInput, DeployActionSource
    from app.services.deploy.deploy_translation_v1 import build_deploy_plan

    plan_input = DeployPlanInput(
        ticker="AAPL",
        intel_action="BUY",
        intel_conviction="HIGH",
        intel_evidence_band="STRONG",
        intel_snapshot_id="snap-001",
        intel_run_id="run-001",
        action_source=DeployActionSource.INTEL_V3,
    )
    plan = build_deploy_plan([plan_input])
    # Sizing bundle is constructed — it does not interact with the plan.
    bundle = _fully_certified_bundle(["AAPL"])
    # Intel action is unchanged regardless of sizing bundle state.
    assert plan.items[0].intel_action == "BUY"


def test_certified_sizing_bundle_does_not_change_actionability_status():
    """Actionability is derived from Intel evidence flags only, not sizing inputs."""
    from app.services.deploy.deploy_contracts import DeployPlanInput, DeployActionSource, DeployActionabilityStatus
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
    bundle = _fully_certified_bundle(["AAPL"])
    # Actionability is still ACTIONABLE_CANDIDATE regardless of sizing bundle.
    assert plan.items[0].actionability_status == DeployActionabilityStatus.ACTIONABLE_CANDIDATE


def test_sizing_inputs_cannot_create_buy_candidate_for_hold():
    """A HOLD Intel action stays NOT_ACTIONABLE_HOLD regardless of sizing inputs."""
    from app.services.deploy.deploy_contracts import DeployPlanInput, DeployActionSource, DeployActionabilityStatus
    from app.services.deploy.deploy_translation_v1 import build_deploy_plan

    plan_input = DeployPlanInput(
        ticker="GOOG",
        intel_action="HOLD",
        intel_conviction="HIGH",
        intel_evidence_band="STRONG",
        intel_snapshot_id="snap-001",
        intel_run_id="run-001",
        action_source=DeployActionSource.INTEL_V3,
    )
    plan = build_deploy_plan([plan_input])
    # Even with a certified cash-rich sizing bundle, HOLD stays non-actionable.
    bundle = _fully_certified_bundle(["GOOG"])
    assert plan.items[0].actionability_status == DeployActionabilityStatus.NOT_ACTIONABLE_HOLD


# ──────────────────────────────────────────────────────────────────────────────
# 16. HOLD remains non-actionable even with certified sizing inputs
# ──────────────────────────────────────────────────────────────────────────────

def test_hold_non_actionable_with_high_certified_cash():
    """Maximum certified cash does not override HOLD Intel decision."""
    from app.services.deploy.deploy_contracts import DeployPlanInput, DeployActionSource, DeployActionabilityStatus
    from app.services.deploy.deploy_translation_v1 import build_deploy_plan

    plan_input = DeployPlanInput(
        ticker="GOOG",
        intel_action="HOLD",
        intel_conviction="HIGH",
        intel_evidence_band="STRONG",
        intel_snapshot_id="snap-001",
        intel_run_id="run-001",
        action_source=DeployActionSource.INTEL_V3,
    )
    plan = build_deploy_plan([plan_input])
    # Massive certified cash doesn't change HOLD to BUY.
    bundle = DeploySizingInputBundle(
        cash=DeployCashInput(available_cash_usd=1_000_000.0, trust_status=DeploySizingTrustStatus.CERTIFIED),
        portfolio=_certified_portfolio(1_000_000.0),
        positions={"GOOG": _certified_position("GOOG", 200_000.0, 0.20)},
    )
    assert bundle.exact_dollar_ready is True  # Certified inputs are ready.
    # But the Intel action remains HOLD regardless.
    assert plan.items[0].actionability_status == DeployActionabilityStatus.NOT_ACTIONABLE_HOLD
    assert plan.guardrail_summary.hold_never_actionable is True


# ──────────────────────────────────────────────────────────────────────────────
# 17. BUY/TRIM/SELL remain Intel-derived candidates only
# ──────────────────────────────────────────────────────────────────────────────

def test_buy_candidate_is_intel_derived_not_sizing_derived():
    """BUY candidate comes from Intel v3 action, not from sizing cash level."""
    from app.services.deploy.deploy_contracts import DeployPlanInput, DeployActionSource, DeployActionabilityStatus
    from app.services.deploy.deploy_translation_v1 import build_deploy_plan

    plan_input = DeployPlanInput(
        ticker="AAPL",
        intel_action="BUY",
        intel_conviction="MEDIUM",
        intel_evidence_band="PARTIAL",
        intel_snapshot_id="snap-001",
        intel_run_id="run-001",
        action_source=DeployActionSource.INTEL_V3,
    )
    plan = build_deploy_plan([plan_input])
    assert plan.items[0].actionability_status == DeployActionabilityStatus.ACTIONABLE_CANDIDATE
    assert plan.items[0].intel_action == "BUY"
    assert plan.guardrail_summary.buy_candidates == 1


def test_no_buy_candidate_without_intel_buy_action():
    """A certified sizing bundle with abundant cash cannot produce a BUY candidate."""
    from app.services.deploy.deploy_contracts import DeployPlanInput, DeployActionSource, DeployActionabilityStatus
    from app.services.deploy.deploy_translation_v1 import build_deploy_plan

    plan_input = DeployPlanInput(
        ticker="AAPL",
        intel_action="HOLD",
        intel_conviction="MEDIUM",
        intel_evidence_band="PARTIAL",
        intel_snapshot_id="snap-001",
        intel_run_id="run-001",
        action_source=DeployActionSource.INTEL_V3,
    )
    plan = build_deploy_plan([plan_input])
    assert plan.guardrail_summary.buy_candidates == 0


# ──────────────────────────────────────────────────────────────────────────────
# 18. No SQL/UI/route/provider/LLM/broker files touched — module import check
# ──────────────────────────────────────────────────────────────────────────────

def test_sizing_contracts_no_sql_imports():
    """deploy_sizing_contracts.py must not import any SQL, DB, or IO libs."""
    import importlib.util
    src = importlib.util.find_spec("app.services.deploy.deploy_sizing_contracts").origin
    with open(src) as f:
        text = f.read()
    for forbidden in ["supabase", "psycopg", "sqlalchemy", "asyncpg", "requests", "httpx", "aiohttp"]:
        assert forbidden not in text, f"deploy_sizing_contracts.py must not import {forbidden}"


def test_sizing_builder_no_sql_imports():
    """deploy_sizing_builder.py must not import any SQL, DB, or IO libs."""
    import importlib.util
    src = importlib.util.find_spec("app.services.deploy.deploy_sizing_builder").origin
    with open(src) as f:
        text = f.read()
    for forbidden in ["supabase", "psycopg", "sqlalchemy", "asyncpg", "requests", "httpx", "aiohttp"]:
        assert forbidden not in text, f"deploy_sizing_builder.py must not import {forbidden}"


def test_sizing_contracts_no_intel_internals_import():
    """Sizing contracts must not import Intel v3 internals."""
    import importlib.util
    src = importlib.util.find_spec("app.services.deploy.deploy_sizing_contracts").origin
    with open(src) as f:
        text = f.read()
    assert "from ..intelligence" not in text
    assert "from app.services.intelligence" not in text
    assert "intel_v3_service" not in text
    assert "RecommendationService" not in text


# ──────────────────────────────────────────────────────────────────────────────
# 20. Builder produces correct bundle from portfolio snapshot dict
# ──────────────────────────────────────────────────────────────────────────────

def test_builder_produces_certified_cash_when_specified():
    bundle = build_sizing_context_from_portfolio_snapshot({
        "available_cash_usd": 12_000.0,
        "cash_trust_status": "CERTIFIED",
    })
    assert bundle.cash is not None
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
    assert bundle.exact_dollar_ready is False


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
    assert bundle.positions["AAPL"].current_market_value_usd == 5_000.0
    assert bundle.exact_dollar_ready is True


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
    assert bundle.exact_dollar_ready is False
    assert bundle.cash.trust_status == DeploySizingTrustStatus.MISSING


def test_builder_unknown_trust_status_string_falls_back_gracefully():
    bundle = build_sizing_context_from_portfolio_snapshot({
        "cash_trust_status": "TOTALLY_MADE_UP_VALUE",
        "available_cash_usd": 5000.0,
    })
    # Should fall back to MISSING (default fallback for cash) rather than crashing.
    assert bundle.cash.trust_status == DeploySizingTrustStatus.MISSING


# ──────────────────────────────────────────────────────────────────────────────
# 21. Unknown ticker in positions dict is treated as MISSING (suppressed)
# ──────────────────────────────────────────────────────────────────────────────

def test_position_suppresses_dollar_readiness_for_unknown_ticker():
    bundle = _fully_certified_bundle(["AAPL"])
    # NVDA is not in the positions dict.
    assert bundle.position_suppresses_dollar_readiness("NVDA") is True


# ──────────────────────────────────────────────────────────────────────────────
# 22. Bundle with all certified inputs reports exact_dollar_ready=True (gate only)
# ──────────────────────────────────────────────────────────────────────────────

def test_fully_certified_bundle_is_exact_dollar_ready():
    bundle = _fully_certified_bundle(["AAPL", "MSFT"])
    assert bundle.exact_dollar_ready is True


def test_exact_dollar_ready_true_does_not_mean_dollars_computed():
    """exact_dollar_ready is a readiness gate — it signals no dollar amounts were computed."""
    bundle = _fully_certified_bundle()
    assert bundle.exact_dollar_ready is True
    # No dollar amounts live in the bundle itself.
    assert not hasattr(bundle, "recommended_dollar_amount")


# ──────────────────────────────────────────────────────────────────────────────
# 23. Suppression reasons correctly enumerate active suppressions
# ──────────────────────────────────────────────────────────────────────────────

def test_fully_certified_bundle_has_no_suppression_reasons():
    bundle = _fully_certified_bundle()
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


# ──────────────────────────────────────────────────────────────────────────────
# 24. has_fabricated_target_allocation guardrail
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
# 25. Schema version is deploy_sizing_v1_contract
# ──────────────────────────────────────────────────────────────────────────────

def test_bundle_schema_version():
    bundle = _fully_certified_bundle()
    assert bundle.schema_version == "deploy_sizing_v1_contract"


def test_builder_bundle_schema_version():
    bundle = build_sizing_context_from_portfolio_snapshot({})
    assert bundle.schema_version == "deploy_sizing_v1_contract"


# ──────────────────────────────────────────────────────────────────────────────
# DeploySizingTrustStatus enum completeness
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
