"""Tests — Deploy v3 certified sizing source adapter v1 (Stage 2.5A).

Acceptance gates proven:
  A. No portfolio snapshot → adapter returns None (not_ready behavior preserved).
  B. Stale snapshot → STALE trust, sizing_values_ready False.
  C. Snapshot with missing per-position market values → MISSING trust, sizing_values_ready False.
  D. Valid fresh snapshot + market values + complete target allocations + certified policy
     → exact_dollar_ready True and dollar amounts can be evaluated.
  E. Missing target allocation for one ticker → target_allocation_ready False,
     exact_dollar_ready False.
  F. Missing/unsupported policy → policy_ready False, exact_dollar_ready False.
  G. Cost basis (shares * avg_cost) is never promoted to certified market value.
  H. Adapter does not call providers, LLM paths, or legacy allocation engine.
  I. _compute_age_hours correctly parses timestamps.
  J. Target allocations: invalid weight range is rejected and not certified.
  K. DB error on portfolio_snapshots read → returns None (fail-safe).
  L. DB error on target_allocations read → returns bundle with empty target allocations.
"""
from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
from uuid import UUID

import pytest

from app.services.deploy.deploy_sizing_source_adapter_v1 import (
    STALE_THRESHOLD_HOURS,
    _compute_age_hours,
    build_sizing_bundle_from_persisted_data,
)
from app.services.deploy.deploy_sizing_contracts import DeploySizingTrustStatus

# ── Fixtures and helpers ──────────────────────────────────────────────────────

_UID = UUID("00000000-0000-0000-0000-000000000099")

_CERTIFIED_POLICY = {
    "minimum_trade_usd": 1.0,
    "rounding_policy": "WHOLE_DOLLAR",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stale_iso() -> str:
    stale = datetime.now(timezone.utc) - timedelta(hours=STALE_THRESHOLD_HOURS + 1)
    return stale.isoformat()


def _make_mock_db(
    portfolio_rows: list | None = None,
    target_alloc_rows: list | None = None,
    ps_raise: Exception | None = None,
    ta_raise: Exception | None = None,
) -> MagicMock:
    """Build a mock DB client for adapter tests."""
    db = MagicMock()

    # portfolio_snapshots chain
    ps_chain = MagicMock()
    ps_chain.select.return_value = ps_chain
    ps_chain.eq.return_value = ps_chain
    ps_chain.order.return_value = ps_chain
    ps_chain.limit.return_value = ps_chain
    if ps_raise is not None:
        ps_chain.execute.side_effect = ps_raise
    else:
        ps_result = MagicMock()
        ps_result.data = portfolio_rows if portfolio_rows is not None else []
        ps_chain.execute.return_value = ps_result

    # target_allocations chain
    ta_chain = MagicMock()
    ta_chain.select.return_value = ta_chain
    ta_chain.eq.return_value = ta_chain
    if ta_raise is not None:
        ta_chain.execute.side_effect = ta_raise
    else:
        ta_result = MagicMock()
        ta_result.data = target_alloc_rows if target_alloc_rows is not None else []
        ta_chain.execute.return_value = ta_result

    def _table_router(name: str) -> MagicMock:
        if name == "portfolio_snapshots":
            return ps_chain
        if name == "target_allocations":
            return ta_chain
        return MagicMock()

    db.table.side_effect = _table_router
    return db


def _snapshot_row(
    snapshot_at: str | None = None,
    total_equity: float = 100_000.0,
    cash_balance: float = 5_000.0,
    positions_data: list | None = None,
) -> dict:
    return {
        "id": "snap-abc12345",
        "snapshot_at": snapshot_at or _now_iso(),
        "total_equity": total_equity,
        "cash_balance": cash_balance,
        "positions_data": positions_data if positions_data is not None else [],
    }


def _pos_with_market_value(ticker: str, market_value_usd: float) -> dict:
    return {
        "ticker": ticker,
        "shares": 10.0,
        "avg_cost": 100.0,
        "market_value_usd": market_value_usd,
    }


def _pos_cost_basis_only(ticker: str) -> dict:
    """Position entry with only cost basis — no market_value_usd."""
    return {
        "ticker": ticker,
        "shares": 10.0,
        "avg_cost": 100.0,
        # deliberately no market_value_usd key
    }


def _target_alloc_row(ticker: str, target_pct: float) -> dict:
    return {"ticker": ticker, "target_pct": target_pct}


def _run(coro) -> object:
    return asyncio.run(coro)


# ── Gate A: No portfolio snapshot → returns None ──────────────────────────────

class TestNoPortfolioSnapshot:
    def test_returns_none_when_no_snapshot(self):
        """Adapter returns None when portfolio_snapshots has no rows."""
        db = _make_mock_db(portfolio_rows=[])
        result = _run(build_sizing_bundle_from_persisted_data(
            user_id=_UID, db_client=db, _policy_config=_CERTIFIED_POLICY,
        ))
        assert result is None

    def test_returns_none_preserves_not_ready_contract(self):
        """None return preserves not_ready downstream behavior (caller handles None)."""
        db = _make_mock_db(portfolio_rows=[])
        result = _run(build_sizing_bundle_from_persisted_data(
            user_id=_UID, db_client=db,
        ))
        assert result is None


# ── Gate B: Stale snapshot → not certified ────────────────────────────────────

class TestStaleSnapshot:
    def test_stale_snapshot_cash_is_stale_trust(self):
        """Cash trust is STALE when snapshot age exceeds threshold."""
        db = _make_mock_db(
            portfolio_rows=[_snapshot_row(snapshot_at=_stale_iso())],
        )
        bundle = _run(build_sizing_bundle_from_persisted_data(
            user_id=_UID, db_client=db, _policy_config=_CERTIFIED_POLICY,
        ))
        assert bundle is not None
        assert bundle.cash.trust_status == DeploySizingTrustStatus.STALE

    def test_stale_snapshot_portfolio_is_stale_trust(self):
        """Portfolio trust is STALE when snapshot age exceeds threshold."""
        db = _make_mock_db(
            portfolio_rows=[_snapshot_row(snapshot_at=_stale_iso())],
        )
        bundle = _run(build_sizing_bundle_from_persisted_data(
            user_id=_UID, db_client=db, _policy_config=_CERTIFIED_POLICY,
        ))
        assert bundle is not None
        assert bundle.portfolio.trust_status == DeploySizingTrustStatus.STALE

    def test_stale_snapshot_positions_are_stale_trust(self):
        """Position trust is STALE (not CERTIFIED) when snapshot is stale."""
        positions = [_pos_with_market_value("AAPL", 50_000.0)]
        db = _make_mock_db(
            portfolio_rows=[_snapshot_row(snapshot_at=_stale_iso(), positions_data=positions)],
        )
        bundle = _run(build_sizing_bundle_from_persisted_data(
            user_id=_UID, db_client=db, _policy_config=_CERTIFIED_POLICY,
        ))
        assert bundle is not None
        assert bundle.positions["AAPL"].trust_status == DeploySizingTrustStatus.STALE

    def test_stale_snapshot_suppresses_sizing_values_ready(self):
        """sizing_values_ready is False when snapshot is stale."""
        positions = [_pos_with_market_value("AAPL", 50_000.0)]
        db = _make_mock_db(
            portfolio_rows=[_snapshot_row(snapshot_at=_stale_iso(), positions_data=positions)],
            target_alloc_rows=[_target_alloc_row("AAPL", 60.0)],
        )
        bundle = _run(build_sizing_bundle_from_persisted_data(
            user_id=_UID, db_client=db, _policy_config=_CERTIFIED_POLICY,
        ))
        assert bundle is not None
        assert bundle.sizing_values_ready is False
        assert bundle.exact_dollar_ready is False


# ── Gate C: Missing per-position market values → not certified ────────────────

class TestMissingPerPositionMarketValues:
    def test_cost_basis_only_position_is_missing_trust(self):
        """Position with no market_value_usd gets MISSING trust — cost basis not certified."""
        positions = [_pos_cost_basis_only("AAPL")]
        db = _make_mock_db(
            portfolio_rows=[_snapshot_row(positions_data=positions)],
        )
        bundle = _run(build_sizing_bundle_from_persisted_data(
            user_id=_UID, db_client=db, _policy_config=_CERTIFIED_POLICY,
        ))
        assert bundle is not None
        assert bundle.positions["AAPL"].trust_status == DeploySizingTrustStatus.MISSING

    def test_cost_basis_only_position_has_null_market_value(self):
        """Position with no market_value_usd has None current_market_value_usd."""
        positions = [_pos_cost_basis_only("AAPL")]
        db = _make_mock_db(
            portfolio_rows=[_snapshot_row(positions_data=positions)],
        )
        bundle = _run(build_sizing_bundle_from_persisted_data(
            user_id=_UID, db_client=db, _policy_config=_CERTIFIED_POLICY,
        ))
        assert bundle is not None
        assert bundle.positions["AAPL"].current_market_value_usd is None

    def test_missing_market_value_suppresses_sizing_values_ready(self):
        """sizing_values_ready is False when positions lack market_value_usd."""
        positions = [_pos_cost_basis_only("AAPL"), _pos_cost_basis_only("MSFT")]
        db = _make_mock_db(
            portfolio_rows=[_snapshot_row(positions_data=positions)],
            target_alloc_rows=[
                _target_alloc_row("AAPL", 60.0),
                _target_alloc_row("MSFT", 40.0),
            ],
        )
        bundle = _run(build_sizing_bundle_from_persisted_data(
            user_id=_UID, db_client=db, _policy_config=_CERTIFIED_POLICY,
        ))
        assert bundle is not None
        assert bundle.sizing_values_ready is False
        assert bundle.exact_dollar_ready is False

    def test_suppression_reason_includes_missing_position_value(self):
        """Suppression reasons include MISSING_POSITION_VALUE when market values absent."""
        from app.services.deploy.deploy_sizing_contracts import DeploySizingSuppressionReason
        positions = [_pos_cost_basis_only("AAPL")]
        db = _make_mock_db(
            portfolio_rows=[_snapshot_row(positions_data=positions)],
        )
        bundle = _run(build_sizing_bundle_from_persisted_data(
            user_id=_UID, db_client=db, _policy_config=_CERTIFIED_POLICY,
        ))
        assert bundle is not None
        reasons = bundle.get_suppression_reasons()
        assert DeploySizingSuppressionReason.MISSING_POSITION_VALUE in reasons


# ── Gate D: Valid snapshot + complete allocations + certified policy → ready ──

class TestExactDollarReadyPath:
    def _build_ready_bundle(self):
        """Helper: build a bundle that should be exact_dollar_ready=True."""
        positions = [
            _pos_with_market_value("AAPL", 60_000.0),
            _pos_with_market_value("MSFT", 35_000.0),
        ]
        snap = _snapshot_row(
            total_equity=100_000.0,
            cash_balance=5_000.0,
            positions_data=positions,
        )
        db = _make_mock_db(
            portfolio_rows=[snap],
            target_alloc_rows=[
                _target_alloc_row("AAPL", 60.0),
                _target_alloc_row("MSFT", 35.0),
            ],
        )
        return _run(build_sizing_bundle_from_persisted_data(
            user_id=_UID, db_client=db, _policy_config=_CERTIFIED_POLICY,
        ))

    def test_exact_dollar_ready_is_true(self):
        """Valid snapshot + complete target allocations + certified policy → exact_dollar_ready."""
        bundle = self._build_ready_bundle()
        assert bundle is not None
        assert bundle.exact_dollar_ready is True

    def test_sizing_values_ready_is_true(self):
        bundle = self._build_ready_bundle()
        assert bundle.sizing_values_ready is True

    def test_target_allocation_ready_is_true(self):
        bundle = self._build_ready_bundle()
        assert bundle.target_allocation_ready is True

    def test_policy_ready_is_true(self):
        bundle = self._build_ready_bundle()
        assert bundle.policy_ready is True

    def test_cash_is_certified(self):
        bundle = self._build_ready_bundle()
        assert bundle.cash.trust_status == DeploySizingTrustStatus.CERTIFIED

    def test_portfolio_is_certified(self):
        bundle = self._build_ready_bundle()
        assert bundle.portfolio.trust_status == DeploySizingTrustStatus.CERTIFIED

    def test_positions_are_certified(self):
        bundle = self._build_ready_bundle()
        for pos in bundle.positions.values():
            assert pos.trust_status == DeploySizingTrustStatus.CERTIFIED

    def test_position_weights_computed_from_market_value(self):
        """Current weight is market_value / total_equity, not from cost basis."""
        bundle = self._build_ready_bundle()
        aapl = bundle.positions["AAPL"]
        assert aapl.current_market_value_usd == 60_000.0
        assert abs(aapl.current_weight - 0.6) < 1e-9

    def test_no_suppression_reasons_when_fully_certified(self):
        bundle = self._build_ready_bundle()
        assert bundle.get_suppression_reasons() == []

    def test_dollar_math_can_be_evaluated_by_downstream(self):
        """With exact_dollar_ready=True, dollar math pipeline can compute amounts."""
        from app.services.deploy.deploy_dollar_math_v1 import compute_dollar_amount_for_item
        from app.services.deploy.deploy_contracts import (
            DeployActionabilityStatus,
            DeployActionSource,
            DeployPlanItem,
            DeployPlanStatus,
        )
        bundle = self._build_ready_bundle()
        # AAPL target=60%, current=60k of 100k portfolio → delta=0, no trade
        item = DeployPlanItem(
            ticker="AAPL",
            intel_action="BUY",
            actionability_status=DeployActionabilityStatus.ACTIONABLE_CANDIDATE,
            action_source=DeployActionSource.INTEL_V3,
            intel_snapshot_id="snap-x",
            intel_run_id="run-x",
            plan_status=DeployPlanStatus.SCAFFOLD,
        )
        result = compute_dollar_amount_for_item(bundle, item)
        # AAPL target=60k, current=60k → delta=0, no trade
        assert result.recommended_dollar_amount is None  # delta=0 suppresses

    def test_buy_from_current_below_target_produces_dollar_amount(self):
        """BUY with current < target allocation produces a positive dollar amount."""
        from app.services.deploy.deploy_dollar_math_v1 import compute_dollar_amount_for_item
        from app.services.deploy.deploy_contracts import (
            DeployActionabilityStatus,
            DeployActionSource,
            DeployPlanItem,
            DeployPlanStatus,
        )
        # AAPL at $50k, target 95% of $100k = $95k → BUY $45k.
        # Target is 95 % to satisfy TARGET_ALLOCATION_TOTAL_MIN (90 %).
        positions = [_pos_with_market_value("AAPL", 50_000.0)]
        snap = _snapshot_row(total_equity=100_000.0, cash_balance=5_000.0, positions_data=positions)
        db = _make_mock_db(
            portfolio_rows=[snap],
            target_alloc_rows=[_target_alloc_row("AAPL", 95.0)],
        )
        bundle = _run(build_sizing_bundle_from_persisted_data(
            user_id=_UID, db_client=db, _policy_config=_CERTIFIED_POLICY,
        ))
        assert bundle is not None
        assert bundle.exact_dollar_ready is True

        item = DeployPlanItem(
            ticker="AAPL",
            intel_action="BUY",
            actionability_status=DeployActionabilityStatus.ACTIONABLE_CANDIDATE,
            action_source=DeployActionSource.INTEL_V3,
            intel_snapshot_id="snap-x",
            intel_run_id="run-x",
            plan_status=DeployPlanStatus.SCAFFOLD,
        )
        result = compute_dollar_amount_for_item(bundle, item)
        assert result.recommended_dollar_amount == 45_000.0


# ── Gate E: Missing target allocation for one ticker → exact_dollar_ready False

class TestMissingTargetAllocation:
    def test_missing_target_for_one_ticker_blocks_exact_dollar_ready(self):
        """target_allocation_ready is False when any position ticker lacks a target."""
        positions = [
            _pos_with_market_value("AAPL", 50_000.0),
            _pos_with_market_value("MSFT", 45_000.0),
        ]
        db = _make_mock_db(
            portfolio_rows=[_snapshot_row(total_equity=100_000.0, positions_data=positions)],
            target_alloc_rows=[_target_alloc_row("AAPL", 60.0)],  # MSFT missing
        )
        bundle = _run(build_sizing_bundle_from_persisted_data(
            user_id=_UID, db_client=db, _policy_config=_CERTIFIED_POLICY,
        ))
        assert bundle is not None
        assert bundle.target_allocation_ready is False
        assert bundle.exact_dollar_ready is False

    def test_suppression_reason_includes_target_allocation_missing(self):
        """Suppression reasons include TARGET_ALLOCATION_MISSING for untargeted tickers."""
        from app.services.deploy.deploy_sizing_contracts import DeploySizingSuppressionReason
        positions = [_pos_with_market_value("AAPL", 50_000.0)]
        db = _make_mock_db(
            portfolio_rows=[_snapshot_row(total_equity=100_000.0, positions_data=positions)],
            target_alloc_rows=[],  # no target allocations at all
        )
        bundle = _run(build_sizing_bundle_from_persisted_data(
            user_id=_UID, db_client=db, _policy_config=_CERTIFIED_POLICY,
        ))
        assert bundle is not None
        reasons = bundle.get_suppression_reasons()
        assert DeploySizingSuppressionReason.TARGET_ALLOCATION_MISSING in reasons


# ── Gate F: Missing/unsupported policy → policy_ready False ──────────────────

class TestMissingOrUnsupportedPolicy:
    def test_no_policy_config_policy_ready_false(self):
        """policy_ready is False when no policy config is provided."""
        positions = [_pos_with_market_value("AAPL", 50_000.0)]
        db = _make_mock_db(
            portfolio_rows=[_snapshot_row(total_equity=100_000.0, positions_data=positions)],
            target_alloc_rows=[_target_alloc_row("AAPL", 60.0)],
        )
        bundle = _run(build_sizing_bundle_from_persisted_data(
            user_id=_UID, db_client=db, _policy_config=None,  # no policy
        ))
        assert bundle is not None
        assert bundle.policy_ready is False
        assert bundle.exact_dollar_ready is False

    def test_empty_policy_config_policy_ready_false(self):
        """policy_ready is False when policy config dict is empty."""
        positions = [_pos_with_market_value("AAPL", 50_000.0)]
        db = _make_mock_db(
            portfolio_rows=[_snapshot_row(total_equity=100_000.0, positions_data=positions)],
            target_alloc_rows=[_target_alloc_row("AAPL", 60.0)],
        )
        bundle = _run(build_sizing_bundle_from_persisted_data(
            user_id=_UID, db_client=db, _policy_config={},  # empty → UNSUPPORTED
        ))
        assert bundle is not None
        assert bundle.policy_ready is False
        assert bundle.exact_dollar_ready is False

    def test_suppression_reason_includes_policy_unsupported(self):
        """Suppression reasons include policy reasons when policy is absent."""
        from app.services.deploy.deploy_sizing_contracts import DeploySizingSuppressionReason
        positions = [_pos_with_market_value("AAPL", 50_000.0)]
        db = _make_mock_db(
            portfolio_rows=[_snapshot_row(total_equity=100_000.0, positions_data=positions)],
            target_alloc_rows=[_target_alloc_row("AAPL", 60.0)],
        )
        bundle = _run(build_sizing_bundle_from_persisted_data(
            user_id=_UID, db_client=db, _policy_config=None,
        ))
        assert bundle is not None
        reasons = bundle.get_suppression_reasons()
        assert DeploySizingSuppressionReason.MINIMUM_TRADE_UNSUPPORTED in reasons
        assert DeploySizingSuppressionReason.ROUNDING_POLICY_UNSUPPORTED in reasons


# ── Gate G: Cost basis never certified ───────────────────────────────────────

class TestCostBasisNeverCertified:
    def test_avg_cost_not_used_as_market_value(self):
        """Position with avg_cost but no market_value_usd must have None market value."""
        positions = [
            {
                "ticker": "AAPL",
                "shares": 100.0,
                "avg_cost": 150.0,  # cost basis only — NEVER certified as market value
            }
        ]
        db = _make_mock_db(portfolio_rows=[_snapshot_row(positions_data=positions)])
        bundle = _run(build_sizing_bundle_from_persisted_data(
            user_id=_UID, db_client=db, _policy_config=_CERTIFIED_POLICY,
        ))
        assert bundle is not None
        pos = bundle.positions.get("AAPL")
        assert pos is not None
        # Market value must be None — cost basis is never promoted
        assert pos.current_market_value_usd is None
        assert pos.trust_status == DeploySizingTrustStatus.MISSING

    def test_cost_basis_position_suppresses_exact_dollar_readiness(self):
        """A position derived from cost basis only suppresses exact-dollar readiness."""
        positions = [{"ticker": "AAPL", "shares": 10.0, "avg_cost": 100.0}]
        db = _make_mock_db(portfolio_rows=[_snapshot_row(positions_data=positions)])
        bundle = _run(build_sizing_bundle_from_persisted_data(
            user_id=_UID, db_client=db, _policy_config=_CERTIFIED_POLICY,
        ))
        assert bundle is not None
        assert bundle.positions["AAPL"].suppresses_exact_dollar_readiness is True


# ── Gate H: No providers, no LLM, no legacy engine ───────────────────────────

class TestNoForbiddenImports:
    def test_adapter_module_does_not_import_providers(self):
        """Adapter module must not import price providers, LLM clients, or legacy engine."""
        import app.services.deploy.deploy_sizing_source_adapter_v1 as mod
        # Check imports at module level (not docstring mentions).
        source = inspect.getsource(mod)
        import_lines = [l for l in source.splitlines() if l.strip().startswith("import ") or l.strip().startswith("from ")]
        full_imports = "\n".join(import_lines)
        forbidden_imports = [
            "price_engine",
            "PriceService",
            "anthropic",
            "openai",
            "allocation_engine",
            "adaptive_deployment",
            "deployment_engine",
            "RecommendationService",
        ]
        for name in forbidden_imports:
            assert name not in full_imports, (
                f"Adapter must not import forbidden module/symbol: '{name}'"
            )

    def test_adapter_module_does_not_call_live_price_fetch(self):
        """Adapter source must not contain live price fetch call patterns."""
        import app.services.deploy.deploy_sizing_source_adapter_v1 as mod
        source = inspect.getsource(mod)
        # Exclude docstring mentions; look for actual call sites
        for pattern in ["fetch_prices(", "get_price(", "FinnhubClient", "AlpacaClient"]:
            assert pattern not in source, f"Adapter must not call live price fetch: '{pattern}'"


# ── Gate I: _compute_age_hours ────────────────────────────────────────────────

class TestComputeAgeHours:
    def test_fresh_timestamp_under_threshold(self):
        """A just-created timestamp returns age near zero."""
        age = _compute_age_hours(datetime.now(timezone.utc).isoformat())
        assert age is not None
        assert age < 0.1

    def test_stale_timestamp_over_threshold(self):
        """A timestamp 25h ago returns age > STALE_THRESHOLD_HOURS."""
        old = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        age = _compute_age_hours(old)
        assert age is not None
        assert age > STALE_THRESHOLD_HOURS

    def test_none_timestamp_returns_none(self):
        """None timestamp returns None (treated as stale)."""
        assert _compute_age_hours(None) is None

    def test_invalid_timestamp_returns_none(self):
        """Unparseable timestamp returns None (treated as stale)."""
        assert _compute_age_hours("not-a-date") is None

    def test_naive_datetime_treated_as_utc(self):
        """Naive datetime objects are treated as UTC."""
        naive_dt = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2)
        age = _compute_age_hours(naive_dt)
        assert age is not None
        assert 1.9 < age < 2.1

    def test_z_suffix_iso_string_parsed(self):
        """ISO strings with 'Z' suffix are parsed correctly."""
        ts = (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
        age = _compute_age_hours(ts)
        assert age is not None
        assert 2.9 < age < 3.1


# ── Gate J: Invalid target allocation rejected ────────────────────────────────

class TestInvalidTargetAllocation:
    def test_target_pct_over_100_not_certified(self):
        """target_pct > 100 must not be certified (weight > 1.0 is invalid)."""
        positions = [_pos_with_market_value("AAPL", 50_000.0)]
        db = _make_mock_db(
            portfolio_rows=[_snapshot_row(total_equity=100_000.0, positions_data=positions)],
            target_alloc_rows=[{"ticker": "AAPL", "target_pct": 110.0}],  # invalid
        )
        bundle = _run(build_sizing_bundle_from_persisted_data(
            user_id=_UID, db_client=db, _policy_config=_CERTIFIED_POLICY,
        ))
        assert bundle is not None
        # Invalid weight → certification fails → ticker absent from target_allocations
        ta = bundle.target_allocations.get("AAPL")
        assert ta is None
        assert bundle.exact_dollar_ready is False


# ── Gate K: DB error on portfolio_snapshots → returns None ───────────────────

class TestDbErrorPortfolioSnapshot:
    def test_db_error_returns_none(self):
        """DB error on portfolio_snapshots read returns None safely."""
        db = _make_mock_db(ps_raise=RuntimeError("DB connection failed"))
        result = _run(build_sizing_bundle_from_persisted_data(
            user_id=_UID, db_client=db, _policy_config=_CERTIFIED_POLICY,
        ))
        assert result is None


# ── Gate L: DB error on target_allocations → empty target_allocations ────────

class TestDbErrorTargetAllocations:
    def test_ta_db_error_returns_bundle_with_no_allocations(self):
        """DB error on target_allocations read returns bundle with empty target_allocations."""
        positions = [_pos_with_market_value("AAPL", 50_000.0)]
        db = _make_mock_db(
            portfolio_rows=[_snapshot_row(total_equity=100_000.0, positions_data=positions)],
            ta_raise=RuntimeError("target_allocations unavailable"),
        )
        bundle = _run(build_sizing_bundle_from_persisted_data(
            user_id=_UID, db_client=db, _policy_config=_CERTIFIED_POLICY,
        ))
        assert bundle is not None
        assert bundle.target_allocations == {}
        assert bundle.exact_dollar_ready is False
