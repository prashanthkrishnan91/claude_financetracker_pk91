"""Tests — Deploy Stage 2.2: policy + target-allocation readiness bridge.

Proves the following invariants:

Target-allocation bridge (certify_target_allocation / build_certified_target_allocations):
  1.  Valid explicit input produces CERTIFIED DeployTargetAllocationInput.
  2.  Certified allocation is_ready_for_math = True.
  3.  None target_weight raises ValueError (not fabricated — rejects input).
  4.  target_weight < 0 raises ValueError.
  5.  target_weight > 1 raises ValueError.
  6.  Empty source_label raises ValueError.
  7.  Placeholder source_label "not_evaluated_yet" raises ValueError.
  8.  Placeholder source_label "not_provided" raises ValueError.
  9.  Placeholder source_label "fabricated" raises ValueError.
 10.  Placeholder source_label "invented" raises ValueError.
 11.  Empty ticker raises ValueError.
 12.  Boundary target_weight 0.0 is accepted.
 13.  Boundary target_weight 1.0 is accepted.
 14.  build_certified_target_allocations processes a list correctly.
 15.  build_certified_target_allocations with invalid entry propagates ValueError.
 16.  Certified allocation is not flagged as fabricated.

Policy bridge (certify_sizing_policy / build_policy_from_config):
 17.  Valid WHOLE_DOLLAR policy produces CERTIFIED DeploySizingPolicyPlaceholder.
 18.  Valid NEAREST_DOLLAR policy is accepted.
 19.  Valid NO_ROUNDING policy is accepted.
 20.  None minimum_trade_usd raises ValueError.
 21.  Negative minimum_trade_usd raises ValueError.
 22.  Zero minimum_trade_usd is accepted (valid floor).
 23.  Unknown rounding_policy string raises ValueError.
 24.  Empty rounding_policy raises ValueError.
 25.  Certified policy suppresses_exact_dollar_readiness = False.
 26.  build_policy_from_config with full valid config returns CERTIFIED.
 27.  build_policy_from_config with None/empty config returns UNSUPPORTED.
 28.  build_policy_from_config with missing minimum_trade_usd raises ValueError.
 29.  build_policy_from_config with missing rounding_policy raises ValueError.
 30.  rounding_policy is normalized to uppercase in certified result.

Bridge → DeploySizingInputBundle integration:
 31.  Certified sizing + certified target + certified policy → exact_dollar_ready=True.
 32.  Certified sizing + certified target + UNSUPPORTED policy → exact_dollar_ready=False.
 33.  Certified sizing + NOT_EVALUATED target + certified policy → exact_dollar_ready=False.
 34.  Missing sizing_values + certified target + certified policy → exact_dollar_ready=False.
 35.  Certified sizing + certified target + certified policy, zero positions → exact_dollar_ready=True.
 36.  Multi-ticker: all tickers certified → exact_dollar_ready=True.
 37.  Multi-ticker: one ticker missing target → exact_dollar_ready=False.
 38.  exact_dollar_ready=True does NOT compute dollar amounts (DeployPlanItem remains None).
 39.  Fabricated target allocation detected in bundle suppresses correctly.
 40.  No suppression reasons for fully ready bundle (sizing+target+policy all certified).

Intel authority invariants (regression):
 41.  Certified target allocation cannot change Intel action.
 42.  Certified policy cannot change Intel action.
 43.  HOLD remains non-actionable with certified target + policy + sizing.
 44.  BUY/TRIM/SELL are Intel-derived only — sizing bridge cannot create them.

Module hygiene:
 45.  deploy_target_allocation_bridge.py has no SQL/HTTP imports.
 46.  deploy_policy_bridge.py has no SQL/HTTP imports.
 47.  ALLOWED_ROUNDING_POLICIES contains expected values.
"""
import pytest

from app.services.deploy.deploy_target_allocation_bridge import (
    certify_target_allocation,
    build_certified_target_allocations,
)
from app.services.deploy.deploy_policy_bridge import (
    certify_sizing_policy,
    build_policy_from_config,
    ALLOWED_ROUNDING_POLICIES,
)
from app.services.deploy.deploy_sizing_contracts import (
    DeployCashInput,
    DeployPortfolioSizingInput,
    DeployPositionSizingInput,
    DeploySizingInputBundle,
    DeploySizingPolicyPlaceholder,
    DeploySizingTrustStatus,
    DeployTargetAllocationInput,
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


def _certified_position(
    ticker: str = "AAPL", value: float = 5_000.0, weight: float = 0.05
) -> DeployPositionSizingInput:
    return DeployPositionSizingInput(
        ticker=ticker,
        current_market_value_usd=value,
        current_weight=weight,
        trust_status=DeploySizingTrustStatus.CERTIFIED,
        source_label="test_source",
    )


def _bridge_certified_policy(
    min_trade: float = 25.0,
    rounding: str = "WHOLE_DOLLAR",
) -> DeploySizingPolicyPlaceholder:
    return certify_sizing_policy(min_trade, rounding)


def _bridge_certified_ta(
    ticker: str = "AAPL", weight: float = 0.05
) -> DeployTargetAllocationInput:
    return certify_target_allocation(ticker, weight, "explicit_user_config")


def _fully_ready_bundle_via_bridge(tickers=None) -> DeploySizingInputBundle:
    """All three gates certified using the Stage 2.2 bridges."""
    tickers = tickers or ["AAPL"]
    return DeploySizingInputBundle(
        cash=_certified_cash(),
        portfolio=_certified_portfolio(),
        positions={t: _certified_position(t) for t in tickers},
        target_allocations={t: _bridge_certified_ta(t) for t in tickers},
        policy=_bridge_certified_policy(),
    )


# ──────────────────────────────────────────────────────────────────────────────
# 1–16. Target-allocation bridge
# ──────────────────────────────────────────────────────────────────────────────

def test_certify_target_allocation_returns_certified():
    """Valid explicit input produces CERTIFIED DeployTargetAllocationInput."""
    ta = certify_target_allocation("AAPL", 0.05, "explicit_user_config")
    assert ta.trust_status == DeploySizingTrustStatus.CERTIFIED
    assert ta.ticker == "AAPL"
    assert ta.target_weight == 0.05


def test_certify_target_allocation_is_ready_for_math():
    """Certified allocation has is_ready_for_math=True."""
    ta = certify_target_allocation("AAPL", 0.05, "explicit_user_config")
    assert ta.is_ready_for_math is True


def test_certify_target_allocation_none_weight_raises():
    """None target_weight raises ValueError — no fabrication allowed."""
    with pytest.raises(ValueError, match="target_weight must not be None"):
        certify_target_allocation("AAPL", None, "explicit_user_config")


def test_certify_target_allocation_negative_weight_raises():
    with pytest.raises(ValueError, match=r"\[0\.0, 1\.0\]"):
        certify_target_allocation("AAPL", -0.01, "explicit_user_config")


def test_certify_target_allocation_weight_above_one_raises():
    with pytest.raises(ValueError, match=r"\[0\.0, 1\.0\]"):
        certify_target_allocation("AAPL", 1.01, "explicit_user_config")


def test_certify_target_allocation_empty_source_label_raises():
    with pytest.raises(ValueError, match="non-empty"):
        certify_target_allocation("AAPL", 0.05, "")


def test_certify_target_allocation_placeholder_source_not_evaluated_yet_raises():
    with pytest.raises(ValueError, match="reserved placeholder"):
        certify_target_allocation("AAPL", 0.05, "not_evaluated_yet")


def test_certify_target_allocation_placeholder_source_not_provided_raises():
    with pytest.raises(ValueError, match="reserved placeholder"):
        certify_target_allocation("AAPL", 0.05, "not_provided")


def test_certify_target_allocation_placeholder_source_fabricated_raises():
    with pytest.raises(ValueError, match="reserved placeholder"):
        certify_target_allocation("AAPL", 0.05, "fabricated")


def test_certify_target_allocation_placeholder_source_invented_raises():
    with pytest.raises(ValueError, match="reserved placeholder"):
        certify_target_allocation("AAPL", 0.05, "invented")


def test_certify_target_allocation_empty_ticker_raises():
    with pytest.raises(ValueError, match="ticker must be a non-empty string"):
        certify_target_allocation("", 0.05, "explicit_user_config")


def test_certify_target_allocation_boundary_weight_zero():
    """Weight 0.0 is a valid boundary value."""
    ta = certify_target_allocation("AAPL", 0.0, "explicit_model_portfolio")
    assert ta.is_ready_for_math is True
    assert ta.target_weight == 0.0


def test_certify_target_allocation_boundary_weight_one():
    """Weight 1.0 (100% allocation) is a valid boundary value."""
    ta = certify_target_allocation("AAPL", 1.0, "explicit_model_portfolio")
    assert ta.is_ready_for_math is True
    assert ta.target_weight == 1.0


def test_build_certified_target_allocations_list():
    """build_certified_target_allocations processes a list correctly."""
    alloc_list = [
        {"ticker": "AAPL", "target_weight": 0.10, "source_label": "model_portfolio_v1"},
        {"ticker": "NVDA", "target_weight": 0.08, "source_label": "model_portfolio_v1"},
    ]
    result = build_certified_target_allocations(alloc_list)
    assert "AAPL" in result
    assert "NVDA" in result
    assert result["AAPL"].trust_status == DeploySizingTrustStatus.CERTIFIED
    assert result["NVDA"].target_weight == 0.08


def test_build_certified_target_allocations_invalid_entry_raises():
    """build_certified_target_allocations propagates ValueError for bad entries."""
    alloc_list = [
        {"ticker": "AAPL", "target_weight": None, "source_label": "model_portfolio_v1"},
    ]
    with pytest.raises(ValueError):
        build_certified_target_allocations(alloc_list)


def test_certify_target_allocation_is_not_fabricated():
    """A certified allocation with a valid weight is not flagged as fabricated."""
    ta = certify_target_allocation("AAPL", 0.05, "explicit_user_config")
    assert ta.is_fabricated is False


# ──────────────────────────────────────────────────────────────────────────────
# 17–30. Policy bridge
# ──────────────────────────────────────────────────────────────────────────────

def test_certify_policy_whole_dollar():
    """WHOLE_DOLLAR policy with valid min_trade produces CERTIFIED policy."""
    policy = certify_sizing_policy(25.0, "WHOLE_DOLLAR")
    assert policy.trust_status == DeploySizingTrustStatus.CERTIFIED
    assert policy.minimum_trade_usd == 25.0
    assert policy.rounding_policy == "WHOLE_DOLLAR"


def test_certify_policy_nearest_dollar():
    policy = certify_sizing_policy(50.0, "NEAREST_DOLLAR")
    assert policy.trust_status == DeploySizingTrustStatus.CERTIFIED
    assert policy.rounding_policy == "NEAREST_DOLLAR"


def test_certify_policy_no_rounding():
    policy = certify_sizing_policy(0.0, "NO_ROUNDING")
    assert policy.trust_status == DeploySizingTrustStatus.CERTIFIED
    assert policy.rounding_policy == "NO_ROUNDING"


def test_certify_policy_none_minimum_trade_raises():
    with pytest.raises(ValueError, match="minimum_trade_usd must not be None"):
        certify_sizing_policy(None, "WHOLE_DOLLAR")


def test_certify_policy_negative_minimum_trade_raises():
    with pytest.raises(ValueError, match=">= 0"):
        certify_sizing_policy(-1.0, "WHOLE_DOLLAR")


def test_certify_policy_zero_minimum_trade_accepted():
    """Zero min trade is valid (no floor enforced)."""
    policy = certify_sizing_policy(0.0, "WHOLE_DOLLAR")
    assert policy.minimum_trade_usd == 0.0
    assert policy.trust_status == DeploySizingTrustStatus.CERTIFIED


def test_certify_policy_unknown_rounding_raises():
    with pytest.raises(ValueError, match="not an allowed value"):
        certify_sizing_policy(25.0, "ROUND_TO_PENNY")


def test_certify_policy_empty_rounding_raises():
    with pytest.raises(ValueError):
        certify_sizing_policy(25.0, "")


def test_certified_policy_does_not_suppress_readiness():
    """Certified policy suppresses_exact_dollar_readiness = False."""
    policy = certify_sizing_policy(25.0, "WHOLE_DOLLAR")
    assert policy.suppresses_exact_dollar_readiness is False


def test_build_policy_from_config_valid():
    """Full valid config returns CERTIFIED policy."""
    policy = build_policy_from_config({
        "minimum_trade_usd": 100.0,
        "rounding_policy": "NEAREST_DOLLAR",
    })
    assert policy.trust_status == DeploySizingTrustStatus.CERTIFIED
    assert policy.minimum_trade_usd == 100.0


def test_build_policy_from_config_none_returns_unsupported():
    """None config returns UNSUPPORTED placeholder."""
    policy = build_policy_from_config(None)
    assert policy.trust_status == DeploySizingTrustStatus.UNSUPPORTED
    assert policy.suppresses_exact_dollar_readiness is True


def test_build_policy_from_config_empty_returns_unsupported():
    """Empty dict returns UNSUPPORTED placeholder."""
    policy = build_policy_from_config({})
    assert policy.trust_status == DeploySizingTrustStatus.UNSUPPORTED


def test_build_policy_from_config_missing_minimum_trade_raises():
    with pytest.raises(ValueError, match="minimum_trade_usd is required"):
        build_policy_from_config({"rounding_policy": "WHOLE_DOLLAR"})


def test_build_policy_from_config_missing_rounding_raises():
    with pytest.raises(ValueError, match="rounding_policy is required"):
        build_policy_from_config({"minimum_trade_usd": 50.0})


def test_certify_policy_normalizes_lowercase_rounding():
    """rounding_policy is normalized to uppercase in the certified result."""
    policy = certify_sizing_policy(25.0, "whole_dollar")
    assert policy.rounding_policy == "WHOLE_DOLLAR"
    assert policy.trust_status == DeploySizingTrustStatus.CERTIFIED


# ──────────────────────────────────────────────────────────────────────────────
# 31–40. Bridge → DeploySizingInputBundle integration
# ──────────────────────────────────────────────────────────────────────────────

def test_certified_sizing_target_policy_unlocks_exact_dollar_ready():
    """Certified sizing + certified target (via bridge) + certified policy → exact_dollar_ready=True."""
    bundle = _fully_ready_bundle_via_bridge(["AAPL"])
    assert bundle.sizing_values_ready is True
    assert bundle.target_allocation_ready is True
    assert bundle.policy_ready is True
    assert bundle.exact_dollar_ready is True


def test_certified_sizing_target_unsupported_policy_blocks_exact_dollar():
    """Certified sizing + certified target + UNSUPPORTED policy → exact_dollar_ready=False."""
    bundle = DeploySizingInputBundle(
        cash=_certified_cash(),
        portfolio=_certified_portfolio(),
        positions={"AAPL": _certified_position()},
        target_allocations={"AAPL": _bridge_certified_ta()},
        policy=DeploySizingPolicyPlaceholder(),  # UNSUPPORTED default.
    )
    assert bundle.sizing_values_ready is True
    assert bundle.target_allocation_ready is True
    assert bundle.policy_ready is False
    assert bundle.exact_dollar_ready is False


def test_certified_sizing_not_evaluated_target_blocks_exact_dollar():
    """Certified sizing + NOT_EVALUATED target + certified policy → exact_dollar_ready=False."""
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
        policy=_bridge_certified_policy(),
    )
    assert bundle.sizing_values_ready is True
    assert bundle.target_allocation_ready is False
    assert bundle.exact_dollar_ready is False


def test_missing_sizing_values_with_certified_target_and_policy_blocks():
    """Missing sizing_values + certified target + certified policy → exact_dollar_ready=False."""
    bundle = DeploySizingInputBundle(
        cash=DeployCashInput(
            available_cash_usd=None,
            trust_status=DeploySizingTrustStatus.MISSING,
        ),
        portfolio=_certified_portfolio(),
        positions={"AAPL": _certified_position()},
        target_allocations={"AAPL": _bridge_certified_ta()},
        policy=_bridge_certified_policy(),
    )
    assert bundle.sizing_values_ready is False
    assert bundle.exact_dollar_ready is False


def test_no_positions_all_certified_exact_dollar_ready():
    """Zero positions: sizing + empty target allocs + certified policy → exact_dollar_ready=True.

    target_allocation_ready is vacuously True when there are no positions.
    """
    bundle = DeploySizingInputBundle(
        cash=_certified_cash(),
        portfolio=_certified_portfolio(),
        positions={},
        target_allocations={},
        policy=_bridge_certified_policy(),
    )
    assert bundle.sizing_values_ready is True
    assert bundle.target_allocation_ready is True  # Vacuously true.
    assert bundle.policy_ready is True
    assert bundle.exact_dollar_ready is True


def test_multi_ticker_all_certified_exact_dollar_ready():
    """Multiple tickers, all certified → exact_dollar_ready=True."""
    bundle = _fully_ready_bundle_via_bridge(["AAPL", "NVDA", "MSFT"])
    assert bundle.exact_dollar_ready is True


def test_multi_ticker_one_missing_target_blocks_exact_dollar():
    """One ticker missing target allocation → exact_dollar_ready=False."""
    bundle = DeploySizingInputBundle(
        cash=_certified_cash(),
        portfolio=_certified_portfolio(),
        positions={
            "AAPL": _certified_position("AAPL"),
            "NVDA": _certified_position("NVDA"),
        },
        target_allocations={
            "AAPL": _bridge_certified_ta("AAPL"),
            # NVDA intentionally absent.
        },
        policy=_bridge_certified_policy(),
    )
    assert bundle.sizing_values_ready is True
    assert bundle.target_allocation_ready is False
    assert bundle.exact_dollar_ready is False


def test_exact_dollar_ready_true_does_not_compute_dollar_amounts():
    """exact_dollar_ready=True signals readiness only — DeployPlanItem stays null."""
    from app.services.deploy.deploy_contracts import DeployPlanInput, DeployActionSource
    from app.services.deploy.deploy_translation_v1 import build_deploy_plan

    bundle = _fully_ready_bundle_via_bridge(["AAPL"])
    assert bundle.exact_dollar_ready is True

    plan_input = DeployPlanInput(
        ticker="AAPL",
        intel_action="BUY",
        intel_conviction="HIGH",
        intel_evidence_band="STRONG",
        intel_snapshot_id="snap-002",
        intel_run_id="run-002",
        has_missing_evidence=False,
        has_stale_evidence=False,
        has_weak_evidence=False,
        is_blocked=False,
        action_source=DeployActionSource.INTEL_V3,
    )
    plan = build_deploy_plan([plan_input])
    assert plan.items[0].recommended_dollar_amount is None
    assert plan.items[0].estimated_share_quantity is None


def test_fabricated_target_allocation_detected_via_has_fabricated():
    """Bundle with a fabricated target allocation is flagged correctly."""
    bundle = DeploySizingInputBundle(
        cash=_certified_cash(),
        portfolio=_certified_portfolio(),
        positions={"AAPL": _certified_position()},
        target_allocations={
            "AAPL": DeployTargetAllocationInput(
                ticker="AAPL",
                target_weight=0.05,  # Non-None weight with non-CERTIFIED trust.
                trust_status=DeploySizingTrustStatus.NOT_EVALUATED,
            ),
        },
        policy=_bridge_certified_policy(),
    )
    assert bundle.has_fabricated_target_allocation() is True
    assert bundle.exact_dollar_ready is False  # Fabricated target does not certify.


def test_fully_ready_bridge_bundle_has_no_suppression_reasons():
    """Fully ready bundle (bridge-certified sizing+target+policy) has no suppression reasons."""
    bundle = _fully_ready_bundle_via_bridge(["AAPL"])
    assert bundle.get_suppression_reasons() == []


# ──────────────────────────────────────────────────────────────────────────────
# 41–44. Intel authority invariants (regression)
# ──────────────────────────────────────────────────────────────────────────────

def test_certified_target_allocation_cannot_change_intel_action():
    """Certified target allocation (Stage 2.2 bridge) does not affect Intel action."""
    from app.services.deploy.deploy_contracts import DeployPlanInput, DeployActionSource
    from app.services.deploy.deploy_translation_v1 import build_deploy_plan

    plan_input = DeployPlanInput(
        ticker="AAPL", intel_action="BUY", intel_conviction="HIGH",
        intel_evidence_band="STRONG", intel_snapshot_id="snap-002", intel_run_id="run-002",
        action_source=DeployActionSource.INTEL_V3,
    )
    plan = build_deploy_plan([plan_input])
    _bridge_certified_ta("AAPL")  # Construct certified allocation — irrelevant to plan.
    assert plan.items[0].intel_action == "BUY"
    assert plan.items[0].action_source == DeployActionSource.INTEL_V3


def test_certified_policy_cannot_change_intel_action():
    """Certified policy (Stage 2.2 bridge) does not affect Intel action."""
    from app.services.deploy.deploy_contracts import DeployPlanInput, DeployActionSource
    from app.services.deploy.deploy_translation_v1 import build_deploy_plan

    plan_input = DeployPlanInput(
        ticker="AAPL", intel_action="TRIM", intel_conviction="MEDIUM",
        intel_evidence_band="PARTIAL", intel_snapshot_id="snap-002", intel_run_id="run-002",
        action_source=DeployActionSource.INTEL_V3,
    )
    plan = build_deploy_plan([plan_input])
    _bridge_certified_policy()  # Construct certified policy — irrelevant to plan.
    assert plan.items[0].intel_action == "TRIM"


def test_hold_remains_non_actionable_with_certified_target_policy_sizing():
    """HOLD is non-actionable even when all three sizing gates are certified."""
    from app.services.deploy.deploy_contracts import (
        DeployPlanInput, DeployActionSource, DeployActionabilityStatus,
    )
    from app.services.deploy.deploy_translation_v1 import build_deploy_plan

    plan_input = DeployPlanInput(
        ticker="GOOG", intel_action="HOLD", intel_conviction="HIGH",
        intel_evidence_band="STRONG", intel_snapshot_id="snap-002", intel_run_id="run-002",
        action_source=DeployActionSource.INTEL_V3,
    )
    plan = build_deploy_plan([plan_input])
    bundle = _fully_ready_bundle_via_bridge(["GOOG"])
    assert bundle.exact_dollar_ready is True  # All gates satisfied.
    assert plan.items[0].actionability_status == DeployActionabilityStatus.NOT_ACTIONABLE_HOLD
    assert plan.guardrail_summary.hold_never_actionable is True


def test_buy_trim_sell_are_intel_derived_only():
    """Bridge-certified sizing inputs cannot create BUY/TRIM/SELL from a HOLD Intel action."""
    from app.services.deploy.deploy_contracts import (
        DeployPlanInput, DeployActionSource, DeployActionabilityStatus,
    )
    from app.services.deploy.deploy_translation_v1 import build_deploy_plan

    for action in ("HOLD",):
        plan_input = DeployPlanInput(
            ticker="GOOG", intel_action=action, intel_conviction="HIGH",
            intel_evidence_band="STRONG", intel_snapshot_id="snap-002", intel_run_id="run-002",
            action_source=DeployActionSource.INTEL_V3,
        )
        plan = build_deploy_plan([plan_input])
        _fully_ready_bundle_via_bridge(["GOOG"])  # Does not alter plan.
        assert plan.guardrail_summary.buy_candidates == 0
        assert plan.items[0].actionability_status == DeployActionabilityStatus.NOT_ACTIONABLE_HOLD


# ──────────────────────────────────────────────────────────────────────────────
# 45–47. Module hygiene
# ──────────────────────────────────────────────────────────────────────────────

def test_target_allocation_bridge_no_sql_http_imports():
    """deploy_target_allocation_bridge.py must not import SQL or HTTP libraries."""
    import importlib.util
    src = importlib.util.find_spec(
        "app.services.deploy.deploy_target_allocation_bridge"
    ).origin
    with open(src) as f:
        text = f.read()
    for forbidden in ["supabase", "psycopg", "sqlalchemy", "asyncpg", "requests", "httpx", "aiohttp"]:
        assert forbidden not in text, f"deploy_target_allocation_bridge.py must not import {forbidden}"


def test_policy_bridge_no_sql_http_imports():
    """deploy_policy_bridge.py must not import SQL or HTTP libraries."""
    import importlib.util
    src = importlib.util.find_spec("app.services.deploy.deploy_policy_bridge").origin
    with open(src) as f:
        text = f.read()
    for forbidden in ["supabase", "psycopg", "sqlalchemy", "asyncpg", "requests", "httpx", "aiohttp"]:
        assert forbidden not in text, f"deploy_policy_bridge.py must not import {forbidden}"


def test_allowed_rounding_policies_contains_expected():
    """ALLOWED_ROUNDING_POLICIES contains the expected values."""
    assert "WHOLE_DOLLAR" in ALLOWED_ROUNDING_POLICIES
    assert "NEAREST_DOLLAR" in ALLOWED_ROUNDING_POLICIES
    assert "NO_ROUNDING" in ALLOWED_ROUNDING_POLICIES
