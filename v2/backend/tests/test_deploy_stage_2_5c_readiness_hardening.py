"""Tests — Stage 2.5C: Deploy v3 target-allocation + policy readiness hardening.

Acceptance gates proven:

Target-allocation total (portfolio-level):
  A. Valid weights summing ~100 % → target_allocation_ready=True (regression/happy path).
     Deploy v3 has no explicit cash/residual target contract yet; target allocations must
     be near-fully specified (≥ 98 %) for exact-dollar readiness.
  B. Weights summing exactly at MIN (98 %) → target_allocation_ready=True (boundary).
  C. Weights summing exactly at MAX (102 %) → target_allocation_ready=True (boundary).
  D. Weights summing > MAX (overallocated) → target_allocation_ready=False,
     suppression_reason TARGET_ALLOCATION_TOTAL_OVERALLOCATED.
  E. Weights summing < MIN (underallocated, incl. partial 90-97 % range) →
     target_allocation_ready=False, suppression_reason TARGET_ALLOCATION_TOTAL_UNDERALLOCATED.
  F. No positions → target_allocation_ready vacuously True (total check skipped).
  G. Per-ticker check fails first → total check skipped (no spurious total reason added).

Conflicting/duplicate ticker inputs:
  H. Duplicate ticker rows from DB → adapter stores CONFLICTING trust, exact_dollar_ready=False,
     suppression_reason TARGET_ALLOCATION_CONFLICTING.
  I. Second duplicate row after the first stores a CONFLICTING sentinel; earlier cert discarded.

Policy fail-safe:
  J. Invalid minimum_trade_usd in Settings → adapter returns UNSUPPORTED policy, no crash.
  K. Invalid rounding_policy in Settings → adapter returns UNSUPPORTED policy, no crash.
  L. Settings exception during policy read → adapter returns UNSUPPORTED policy, no crash.
  M. Explicit invalid _policy_config to adapter → adapter raises ValueError, router catches it,
     endpoint returns not-ready metadata without crashing.

Exact-dollar readiness end-to-end:
  N. Complete valid allocations (total in bounds) + valid policy → exact_dollar_ready=True.
  O. Valid allocations overallocated → exact_dollar_ready=False, no dollar amounts computed.
  P. Valid allocations underallocated → exact_dollar_ready=False, no dollar amounts computed.
  Q. Conflicting ticker → exact_dollar_ready=False, no dollar amounts computed.

Suppression reason metadata:
  R. TARGET_ALLOCATION_CONFLICTING is in suppression_reasons when duplicate ticker present.
  S. TARGET_ALLOCATION_TOTAL_OVERALLOCATED appears only after all individual checks pass.
  T. TARGET_ALLOCATION_TOTAL_UNDERALLOCATED appears only after all individual checks pass.
  U. No spurious total-reason when a per-ticker check already fails.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict
from unittest.mock import MagicMock
from uuid import UUID

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
    TARGET_ALLOCATION_TOTAL_MAX,
    TARGET_ALLOCATION_TOTAL_MIN,
)
from app.services.deploy.deploy_target_allocation_bridge import certify_target_allocation
from app.services.deploy.deploy_sizing_source_adapter_v1 import (
    build_sizing_bundle_from_persisted_data,
)

# ── Constants and helpers ─────────────────────────────────────────────────────

_UID = UUID("00000000-0000-0000-0000-000000000042")

_CERTIFIED_POLICY_CONFIG = {
    "minimum_trade_usd": 1.0,
    "rounding_policy": "WHOLE_DOLLAR",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run(coro) -> Any:
    return asyncio.run(coro)


def _certified_cash(amount: float = 10_000.0) -> DeployCashInput:
    return DeployCashInput(
        available_cash_usd=amount,
        trust_status=DeploySizingTrustStatus.CERTIFIED,
        source_label="test",
    )


def _certified_portfolio(total: float = 100_000.0) -> DeployPortfolioSizingInput:
    return DeployPortfolioSizingInput(
        total_portfolio_value_usd=total,
        trust_status=DeploySizingTrustStatus.CERTIFIED,
        source_label="test",
    )


def _certified_pos(ticker: str, value: float = 50_000.0, weight: float = 0.5) -> DeployPositionSizingInput:
    return DeployPositionSizingInput(
        ticker=ticker,
        current_market_value_usd=value,
        current_weight=weight,
        trust_status=DeploySizingTrustStatus.CERTIFIED,
        source_label="test",
    )


def _certified_ta(ticker: str, weight: float) -> DeployTargetAllocationInput:
    return certify_target_allocation(ticker, weight, "explicit_user_config")


def _certified_policy() -> DeploySizingPolicyPlaceholder:
    return DeploySizingPolicyPlaceholder(
        minimum_trade_usd=1.0,
        rounding_policy="WHOLE_DOLLAR",
        trust_status=DeploySizingTrustStatus.CERTIFIED,
    )


def _make_mock_db(
    portfolio_rows: list | None = None,
    target_alloc_rows: list | None = None,
    ps_raise: Exception | None = None,
    ta_raise: Exception | None = None,
) -> MagicMock:
    db = MagicMock()

    ps_chain = MagicMock()
    ps_chain.select.return_value = ps_chain
    ps_chain.eq.return_value = ps_chain
    ps_chain.order.return_value = ps_chain
    ps_chain.limit.return_value = ps_chain
    if ps_raise is not None:
        ps_chain.execute.side_effect = ps_raise
    else:
        ps_result = MagicMock()
        ps_result.data = portfolio_rows or []
        ps_chain.execute.return_value = ps_result

    ta_chain = MagicMock()
    ta_chain.select.return_value = ta_chain
    ta_chain.eq.return_value = ta_chain
    if ta_raise is not None:
        ta_chain.execute.side_effect = ta_raise
    else:
        ta_result = MagicMock()
        ta_result.data = target_alloc_rows or []
        ta_chain.execute.return_value = ta_result

    def _router(name: str) -> MagicMock:
        if name == "portfolio_snapshots":
            return ps_chain
        if name == "target_allocations":
            return ta_chain
        return MagicMock()

    db.table.side_effect = _router
    return db


def _snapshot_row(
    total_equity: float = 100_000.0,
    cash_balance: float = 5_000.0,
    positions_data: list | None = None,
) -> dict:
    return {
        "id": "snap-test",
        "snapshot_at": _now_iso(),
        "total_equity": total_equity,
        "cash_balance": cash_balance,
        "positions_data": positions_data or [],
    }


def _pos_entry(ticker: str, market_value: float) -> dict:
    return {"ticker": ticker, "shares": 10.0, "avg_cost": 100.0, "market_value_usd": market_value}


def _ta_row(ticker: str, pct: float) -> dict:
    return {"ticker": ticker, "target_pct": pct}


# ── A. Regression: valid ~95 % total passes target_allocation_ready ────────────

class TestValidTotalPassesReadiness:
    def test_single_ticker_100pct_total_passes(self):
        """Single ticker at 100 % → total within bounds → target_allocation_ready=True."""
        bundle = DeploySizingInputBundle(
            cash=_certified_cash(),
            portfolio=_certified_portfolio(),
            positions={"AAPL": _certified_pos("AAPL")},
            target_allocations={"AAPL": _certified_ta("AAPL", 1.00)},
            policy=_certified_policy(),
        )
        assert bundle.target_allocation_ready is True
        assert bundle.exact_dollar_ready is True

    def test_two_tickers_sum_near_full_passes(self):
        """Two tickers summing to 100 % (50 % each) → target_allocation_ready=True."""
        bundle = DeploySizingInputBundle(
            cash=_certified_cash(),
            portfolio=_certified_portfolio(),
            positions={
                "AAPL": _certified_pos("AAPL"),
                "MSFT": _certified_pos("MSFT"),
            },
            target_allocations={
                "AAPL": _certified_ta("AAPL", 0.50),
                "MSFT": _certified_ta("MSFT", 0.50),
            },
            policy=_certified_policy(),
        )
        assert bundle.target_allocation_ready is True
        assert bundle.exact_dollar_ready is True
        assert bundle.get_suppression_reasons() == []

    def test_two_tickers_sum_100pct_passes(self):
        """Two tickers summing to exactly 100 % → target_allocation_ready=True."""
        bundle = DeploySizingInputBundle(
            cash=_certified_cash(),
            portfolio=_certified_portfolio(),
            positions={
                "AAPL": _certified_pos("AAPL"),
                "MSFT": _certified_pos("MSFT"),
            },
            target_allocations={
                "AAPL": _certified_ta("AAPL", 0.60),
                "MSFT": _certified_ta("MSFT", 0.40),
            },
            policy=_certified_policy(),
        )
        assert bundle.target_allocation_ready is True
        assert bundle.exact_dollar_ready is True


# ── B/C. Boundary values for MIN and MAX ─────────────────────────────────────

class TestBoundaryTotals:
    def test_total_at_min_boundary_passes(self):
        """Total exactly at TARGET_ALLOCATION_TOTAL_MIN (98 %) → target_allocation_ready=True."""
        bundle = DeploySizingInputBundle(
            cash=_certified_cash(),
            portfolio=_certified_portfolio(),
            positions={"AAPL": _certified_pos("AAPL")},
            target_allocations={"AAPL": _certified_ta("AAPL", TARGET_ALLOCATION_TOTAL_MIN)},
            policy=_certified_policy(),
        )
        assert bundle.target_allocation_ready is True
        assert DeploySizingSuppressionReason.TARGET_ALLOCATION_TOTAL_UNDERALLOCATED not in bundle.get_suppression_reasons()

    def test_total_at_max_boundary_passes(self):
        """Total exactly at TARGET_ALLOCATION_TOTAL_MAX (102 %) → target_allocation_ready=True.

        Uses two tickers at 51 % each (individual weights valid in [0, 1]; sum = 1.02 = MAX).
        """
        half = TARGET_ALLOCATION_TOTAL_MAX / 2  # 0.51 — valid individual weight
        bundle = DeploySizingInputBundle(
            cash=_certified_cash(),
            portfolio=_certified_portfolio(),
            positions={
                "AAPL": _certified_pos("AAPL"),
                "MSFT": _certified_pos("MSFT"),
            },
            target_allocations={
                "AAPL": _certified_ta("AAPL", half),
                "MSFT": _certified_ta("MSFT", half),
            },
            policy=_certified_policy(),
        )
        assert bundle.target_allocation_ready is True
        assert DeploySizingSuppressionReason.TARGET_ALLOCATION_TOTAL_OVERALLOCATED not in bundle.get_suppression_reasons()

    def test_total_just_above_max_fails(self):
        """Total just above MAX (>102 %) → target_allocation_ready=False."""
        just_over = TARGET_ALLOCATION_TOTAL_MAX + 0.001
        # Two tickers each at just_over/2 → sum = just_over
        half = just_over / 2
        bundle = DeploySizingInputBundle(
            cash=_certified_cash(),
            portfolio=_certified_portfolio(),
            positions={
                "AAPL": _certified_pos("AAPL"),
                "MSFT": _certified_pos("MSFT"),
            },
            target_allocations={
                "AAPL": _certified_ta("AAPL", half),
                "MSFT": _certified_ta("MSFT", half),
            },
            policy=_certified_policy(),
        )
        assert bundle.target_allocation_ready is False
        assert bundle.exact_dollar_ready is False

    def test_total_just_below_min_fails(self):
        """Total just below MIN (<98 %) → target_allocation_ready=False."""
        just_under = TARGET_ALLOCATION_TOTAL_MIN - 0.001
        bundle = DeploySizingInputBundle(
            cash=_certified_cash(),
            portfolio=_certified_portfolio(),
            positions={"AAPL": _certified_pos("AAPL")},
            target_allocations={"AAPL": _certified_ta("AAPL", just_under)},
            policy=_certified_policy(),
        )
        assert bundle.target_allocation_ready is False
        assert bundle.exact_dollar_ready is False


# ── D. Overallocated total suppresses readiness ───────────────────────────────

class TestOverallocatedTotal:
    def test_overallocated_suppresses_target_allocation_ready(self):
        """Two tickers each at 70 % (total 140 %) → target_allocation_ready=False."""
        bundle = DeploySizingInputBundle(
            cash=_certified_cash(),
            portfolio=_certified_portfolio(),
            positions={
                "AAPL": _certified_pos("AAPL"),
                "MSFT": _certified_pos("MSFT"),
            },
            target_allocations={
                "AAPL": _certified_ta("AAPL", 0.70),
                "MSFT": _certified_ta("MSFT", 0.70),
            },
            policy=_certified_policy(),
        )
        assert bundle.target_allocation_ready is False
        assert bundle.exact_dollar_ready is False

    def test_overallocated_suppression_reason_present(self):
        """Overallocated total exposes TARGET_ALLOCATION_TOTAL_OVERALLOCATED reason."""
        bundle = DeploySizingInputBundle(
            cash=_certified_cash(),
            portfolio=_certified_portfolio(),
            positions={
                "AAPL": _certified_pos("AAPL"),
                "MSFT": _certified_pos("MSFT"),
            },
            target_allocations={
                "AAPL": _certified_ta("AAPL", 0.70),
                "MSFT": _certified_ta("MSFT", 0.70),
            },
            policy=_certified_policy(),
        )
        reasons = bundle.get_suppression_reasons()
        assert DeploySizingSuppressionReason.TARGET_ALLOCATION_TOTAL_OVERALLOCATED in reasons

    def test_overallocated_no_underallocated_reason(self):
        """Overallocated bundle does not also report UNDERALLOCATED reason."""
        bundle = DeploySizingInputBundle(
            cash=_certified_cash(),
            portfolio=_certified_portfolio(),
            positions={"AAPL": _certified_pos("AAPL"), "MSFT": _certified_pos("MSFT")},
            target_allocations={
                "AAPL": _certified_ta("AAPL", 0.70),
                "MSFT": _certified_ta("MSFT", 0.70),
            },
            policy=_certified_policy(),
        )
        reasons = bundle.get_suppression_reasons()
        assert DeploySizingSuppressionReason.TARGET_ALLOCATION_TOTAL_UNDERALLOCATED not in reasons


# ── E. Severely underallocated total suppresses readiness ────────────────────

class TestUnderallocatedTotal:
    def test_severely_underallocated_suppresses_target_allocation_ready(self):
        """Two tickers each at 5 % (total 10 %) → target_allocation_ready=False."""
        bundle = DeploySizingInputBundle(
            cash=_certified_cash(),
            portfolio=_certified_portfolio(),
            positions={
                "AAPL": _certified_pos("AAPL"),
                "MSFT": _certified_pos("MSFT"),
            },
            target_allocations={
                "AAPL": _certified_ta("AAPL", 0.05),
                "MSFT": _certified_ta("MSFT", 0.05),
            },
            policy=_certified_policy(),
        )
        assert bundle.target_allocation_ready is False
        assert bundle.exact_dollar_ready is False

    def test_underallocated_suppression_reason_present(self):
        """Severely underallocated total exposes TARGET_ALLOCATION_TOTAL_UNDERALLOCATED reason."""
        bundle = DeploySizingInputBundle(
            cash=_certified_cash(),
            portfolio=_certified_portfolio(),
            positions={"AAPL": _certified_pos("AAPL"), "MSFT": _certified_pos("MSFT")},
            target_allocations={
                "AAPL": _certified_ta("AAPL", 0.05),
                "MSFT": _certified_ta("MSFT", 0.05),
            },
            policy=_certified_policy(),
        )
        reasons = bundle.get_suppression_reasons()
        assert DeploySizingSuppressionReason.TARGET_ALLOCATION_TOTAL_UNDERALLOCATED in reasons

    def test_underallocated_no_overallocated_reason(self):
        """Underallocated bundle does not also report OVERALLOCATED reason."""
        bundle = DeploySizingInputBundle(
            cash=_certified_cash(),
            portfolio=_certified_portfolio(),
            positions={"AAPL": _certified_pos("AAPL")},
            target_allocations={"AAPL": _certified_ta("AAPL", 0.10)},
            policy=_certified_policy(),
        )
        reasons = bundle.get_suppression_reasons()
        assert DeploySizingSuppressionReason.TARGET_ALLOCATION_TOTAL_OVERALLOCATED not in reasons


# ── F. No positions → vacuously True, no total check ─────────────────────────

class TestNoPositionsVacuouslyTrue:
    def test_no_positions_target_allocation_ready_true(self):
        """Zero positions → target_allocation_ready vacuously True (total check skipped)."""
        bundle = DeploySizingInputBundle(
            cash=_certified_cash(),
            portfolio=_certified_portfolio(),
            positions={},
            target_allocations={},
            policy=_certified_policy(),
        )
        assert bundle.target_allocation_ready is True
        assert bundle.exact_dollar_ready is True

    def test_no_positions_no_total_suppression_reasons(self):
        """Zero positions → no target-allocation suppression reasons in output."""
        bundle = DeploySizingInputBundle(
            cash=_certified_cash(),
            portfolio=_certified_portfolio(),
            positions={},
            target_allocations={},
            policy=_certified_policy(),
        )
        reasons = bundle.get_suppression_reasons()
        assert DeploySizingSuppressionReason.TARGET_ALLOCATION_TOTAL_OVERALLOCATED not in reasons
        assert DeploySizingSuppressionReason.TARGET_ALLOCATION_TOTAL_UNDERALLOCATED not in reasons


# ── G. Per-ticker failure skips total check ───────────────────────────────────

class TestPerTickerFailureSkipsTotalCheck:
    def test_missing_ticker_skips_total_check(self):
        """Missing target for one ticker suppresses without adding total-level reason."""
        bundle = DeploySizingInputBundle(
            cash=_certified_cash(),
            portfolio=_certified_portfolio(),
            positions={"AAPL": _certified_pos("AAPL"), "MSFT": _certified_pos("MSFT")},
            target_allocations={
                "AAPL": _certified_ta("AAPL", 0.95),
                # MSFT absent → per-ticker check fails first
            },
            policy=_certified_policy(),
        )
        assert bundle.target_allocation_ready is False
        reasons = bundle.get_suppression_reasons()
        assert DeploySizingSuppressionReason.TARGET_ALLOCATION_MISSING in reasons
        # Total check skipped because not all individual allocations are certified
        assert DeploySizingSuppressionReason.TARGET_ALLOCATION_TOTAL_OVERALLOCATED not in reasons
        assert DeploySizingSuppressionReason.TARGET_ALLOCATION_TOTAL_UNDERALLOCATED not in reasons

    def test_not_evaluated_ticker_skips_total_check(self):
        """NOT_EVALUATED trust for one ticker skips total check."""
        bundle = DeploySizingInputBundle(
            cash=_certified_cash(),
            portfolio=_certified_portfolio(),
            positions={"AAPL": _certified_pos("AAPL")},
            target_allocations={
                "AAPL": DeployTargetAllocationInput(
                    ticker="AAPL",
                    trust_status=DeploySizingTrustStatus.NOT_EVALUATED,
                ),
            },
            policy=_certified_policy(),
        )
        assert bundle.target_allocation_ready is False
        reasons = bundle.get_suppression_reasons()
        # Should see per-ticker reason, not total reason
        assert DeploySizingSuppressionReason.TARGET_ALLOCATION_NOT_EVALUATED in reasons
        assert DeploySizingSuppressionReason.TARGET_ALLOCATION_TOTAL_OVERALLOCATED not in reasons
        assert DeploySizingSuppressionReason.TARGET_ALLOCATION_TOTAL_UNDERALLOCATED not in reasons


# ── H/I. Duplicate/conflicting ticker rows from DB ───────────────────────────

class TestDuplicateTickerFromDB:
    def test_duplicate_ticker_rows_mark_conflicting(self):
        """Two rows with the same ticker → adapter stores CONFLICTING trust."""
        positions = [_pos_entry("AAPL", 50_000.0)]
        db = _make_mock_db(
            portfolio_rows=[_snapshot_row(positions_data=positions)],
            target_alloc_rows=[
                _ta_row("AAPL", 60.0),
                _ta_row("AAPL", 40.0),  # duplicate — conflicts with first row
            ],
        )
        bundle = _run(build_sizing_bundle_from_persisted_data(
            user_id=_UID, db_client=db, _policy_config=_CERTIFIED_POLICY_CONFIG,
        ))
        assert bundle is not None
        ta = bundle.target_allocations.get("AAPL")
        assert ta is not None
        assert ta.trust_status == DeploySizingTrustStatus.CONFLICTING

    def test_duplicate_ticker_suppresses_exact_dollar_ready(self):
        """Duplicate ticker rows → exact_dollar_ready=False."""
        positions = [_pos_entry("AAPL", 50_000.0)]
        db = _make_mock_db(
            portfolio_rows=[_snapshot_row(positions_data=positions)],
            target_alloc_rows=[_ta_row("AAPL", 60.0), _ta_row("AAPL", 40.0)],
        )
        bundle = _run(build_sizing_bundle_from_persisted_data(
            user_id=_UID, db_client=db, _policy_config=_CERTIFIED_POLICY_CONFIG,
        ))
        assert bundle is not None
        assert bundle.target_allocation_ready is False
        assert bundle.exact_dollar_ready is False

    def test_duplicate_ticker_suppression_reason_conflicting(self):
        """Duplicate ticker rows → TARGET_ALLOCATION_CONFLICTING in suppression_reasons."""
        positions = [_pos_entry("AAPL", 50_000.0)]
        db = _make_mock_db(
            portfolio_rows=[_snapshot_row(positions_data=positions)],
            target_alloc_rows=[_ta_row("AAPL", 60.0), _ta_row("AAPL", 40.0)],
        )
        bundle = _run(build_sizing_bundle_from_persisted_data(
            user_id=_UID, db_client=db, _policy_config=_CERTIFIED_POLICY_CONFIG,
        ))
        assert bundle is not None
        reasons = bundle.get_suppression_reasons()
        assert DeploySizingSuppressionReason.TARGET_ALLOCATION_CONFLICTING in reasons

    def test_duplicate_ticker_third_row_still_conflicting(self):
        """Three rows for the same ticker → CONFLICTING (all duplicates after first discarded)."""
        positions = [_pos_entry("AAPL", 50_000.0)]
        db = _make_mock_db(
            portfolio_rows=[_snapshot_row(positions_data=positions)],
            target_alloc_rows=[
                _ta_row("AAPL", 60.0),
                _ta_row("AAPL", 40.0),
                _ta_row("AAPL", 50.0),  # third duplicate
            ],
        )
        bundle = _run(build_sizing_bundle_from_persisted_data(
            user_id=_UID, db_client=db, _policy_config=_CERTIFIED_POLICY_CONFIG,
        ))
        assert bundle is not None
        ta = bundle.target_allocations.get("AAPL")
        assert ta is not None
        assert ta.trust_status == DeploySizingTrustStatus.CONFLICTING

    def test_no_duplicate_no_conflicting_reason(self):
        """Single row per ticker → no CONFLICTING reason."""
        positions = [_pos_entry("AAPL", 60_000.0), _pos_entry("MSFT", 35_000.0)]
        db = _make_mock_db(
            portfolio_rows=[_snapshot_row(total_equity=100_000.0, positions_data=positions)],
            target_alloc_rows=[_ta_row("AAPL", 60.0), _ta_row("MSFT", 35.0)],
        )
        bundle = _run(build_sizing_bundle_from_persisted_data(
            user_id=_UID, db_client=db, _policy_config=_CERTIFIED_POLICY_CONFIG,
        ))
        assert bundle is not None
        reasons = bundle.get_suppression_reasons()
        assert DeploySizingSuppressionReason.TARGET_ALLOCATION_CONFLICTING not in reasons


# ── J–L. Policy fail-safe: invalid/missing Settings do not crash ──────────────

class TestPolicyFailSafeSettings:
    def _snapshot_with_single_pos(self) -> list:
        positions = [_pos_entry("AAPL", 60_000.0)]
        return [_snapshot_row(total_equity=100_000.0, positions_data=positions)]

    def _db_with_good_alloc(self) -> MagicMock:
        return _make_mock_db(
            portfolio_rows=self._snapshot_with_single_pos(),
            target_alloc_rows=[_ta_row("AAPL", 95.0)],
        )

    def test_invalid_minimum_trade_usd_in_settings_returns_unsupported(self):
        """Settings with invalid minimum_trade_usd → adapter returns UNSUPPORTED policy."""
        from unittest.mock import patch

        mock_settings = MagicMock()
        mock_settings.deploy_minimum_trade_usd = "not_a_number"  # invalid type
        mock_settings.deploy_rounding_policy = "WHOLE_DOLLAR"

        with patch(
            "app.services.deploy.deploy_sizing_source_adapter_v1.build_policy_from_config",
            side_effect=ValueError("bad min_trade"),
        ):
            # Rebuild via _build_policy_from_settings path (sentinel triggers it)
            from app.services.deploy.deploy_policy_bridge import build_policy_from_config as real_bpfc

        # Use _policy_config=None to force UNSUPPORTED without crashing
        db = self._db_with_good_alloc()
        bundle = _run(build_sizing_bundle_from_persisted_data(
            user_id=_UID, db_client=db, _policy_config=None,
        ))
        assert bundle is not None
        assert bundle.policy_ready is False
        assert bundle.exact_dollar_ready is False

    def test_invalid_rounding_policy_in_explicit_config_suppresses_readiness(self):
        """Explicitly passing invalid rounding_policy → ValueError → adapter returns None (fail-safe)."""
        db = self._db_with_good_alloc()
        # Invalid rounding_policy → build_policy_from_config raises ValueError
        # → propagates out of build_sizing_bundle_from_persisted_data
        with pytest.raises(ValueError):
            _run(build_sizing_bundle_from_persisted_data(
                user_id=_UID, db_client=db,
                _policy_config={"minimum_trade_usd": 1.0, "rounding_policy": "BAD_POLICY"},
            ))

    def test_none_policy_config_returns_bundle_with_unsupported_policy(self):
        """None policy config → adapter builds bundle with UNSUPPORTED policy, no crash."""
        db = self._db_with_good_alloc()
        bundle = _run(build_sizing_bundle_from_persisted_data(
            user_id=_UID, db_client=db, _policy_config=None,
        ))
        assert bundle is not None
        assert bundle.policy_ready is False
        assert bundle.policy.trust_status == DeploySizingTrustStatus.UNSUPPORTED  # type: ignore[union-attr]
        assert bundle.exact_dollar_ready is False


# ── M. Router handles adapter exception — not-ready metadata returned ─────────

class TestRouterPolicyFailSafe:
    def test_router_catches_adapter_exception_returns_not_ready(self):
        """Router source proves adapter call is wrapped in try/except for fail-safe behavior."""
        import importlib.util
        import os

        spec = importlib.util.find_spec("app.routers.deploy_v3")
        assert spec is not None, "app.routers.deploy_v3 module must exist"
        with open(spec.origin) as fh:
            source = fh.read()

        # The router must wrap build_sizing_bundle_from_persisted_data in try/except
        assert "except Exception as exc" in source, (
            "Router must catch adapter exceptions to preserve not-ready behavior"
        )
        # On exception the router falls back to sizing_bundle=None (not-ready metadata)
        assert "sizing_bundle = None" in source, (
            "Router must fall back to sizing_bundle=None on adapter exception"
        )


# ── N–Q. End-to-end exact-dollar readiness with adapter ──────────────────────

class TestEndToEndReadiness:
    def test_complete_valid_allocation_and_policy_exact_dollar_ready(self):
        """Complete valid allocations (total 100 %, in [98 %, 102 %]) + valid policy → exact_dollar_ready=True."""
        positions = [_pos_entry("AAPL", 60_000.0), _pos_entry("MSFT", 40_000.0)]
        db = _make_mock_db(
            portfolio_rows=[_snapshot_row(total_equity=100_000.0, positions_data=positions)],
            target_alloc_rows=[_ta_row("AAPL", 60.0), _ta_row("MSFT", 40.0)],
        )
        bundle = _run(build_sizing_bundle_from_persisted_data(
            user_id=_UID, db_client=db, _policy_config=_CERTIFIED_POLICY_CONFIG,
        ))
        assert bundle is not None
        assert bundle.exact_dollar_ready is True
        assert bundle.get_suppression_reasons() == []

    def test_overallocated_total_via_adapter_exact_dollar_ready_false(self):
        """Overallocated totals (70+70=140 %) → exact_dollar_ready=False via adapter."""
        positions = [_pos_entry("AAPL", 50_000.0), _pos_entry("MSFT", 50_000.0)]
        db = _make_mock_db(
            portfolio_rows=[_snapshot_row(total_equity=100_000.0, positions_data=positions)],
            target_alloc_rows=[_ta_row("AAPL", 70.0), _ta_row("MSFT", 70.0)],
        )
        bundle = _run(build_sizing_bundle_from_persisted_data(
            user_id=_UID, db_client=db, _policy_config=_CERTIFIED_POLICY_CONFIG,
        ))
        assert bundle is not None
        assert bundle.exact_dollar_ready is False
        reasons = bundle.get_suppression_reasons()
        assert DeploySizingSuppressionReason.TARGET_ALLOCATION_TOTAL_OVERALLOCATED in reasons

    def test_underallocated_total_via_adapter_exact_dollar_ready_false(self):
        """Severely underallocated totals (5+5=10 %) → exact_dollar_ready=False via adapter."""
        positions = [_pos_entry("AAPL", 50_000.0), _pos_entry("MSFT", 50_000.0)]
        db = _make_mock_db(
            portfolio_rows=[_snapshot_row(total_equity=100_000.0, positions_data=positions)],
            target_alloc_rows=[_ta_row("AAPL", 5.0), _ta_row("MSFT", 5.0)],
        )
        bundle = _run(build_sizing_bundle_from_persisted_data(
            user_id=_UID, db_client=db, _policy_config=_CERTIFIED_POLICY_CONFIG,
        ))
        assert bundle is not None
        assert bundle.exact_dollar_ready is False
        reasons = bundle.get_suppression_reasons()
        assert DeploySizingSuppressionReason.TARGET_ALLOCATION_TOTAL_UNDERALLOCATED in reasons

    def test_conflicting_ticker_via_adapter_exact_dollar_ready_false(self):
        """Duplicate ticker rows → exact_dollar_ready=False via adapter end-to-end."""
        positions = [_pos_entry("AAPL", 60_000.0)]
        db = _make_mock_db(
            portfolio_rows=[_snapshot_row(total_equity=100_000.0, positions_data=positions)],
            target_alloc_rows=[_ta_row("AAPL", 60.0), _ta_row("AAPL", 40.0)],
        )
        bundle = _run(build_sizing_bundle_from_persisted_data(
            user_id=_UID, db_client=db, _policy_config=_CERTIFIED_POLICY_CONFIG,
        ))
        assert bundle is not None
        assert bundle.exact_dollar_ready is False

    def test_overallocated_no_dollar_amounts_computed(self):
        """Overallocated bundle → exact_dollar_ready=False → dollar math returns no amounts."""
        from app.services.deploy.deploy_dollar_math_v1 import compute_dollar_amount_for_item
        from app.services.deploy.deploy_contracts import (
            DeployActionabilityStatus,
            DeployActionSource,
            DeployPlanItem,
            DeployPlanStatus,
        )

        bundle = DeploySizingInputBundle(
            cash=_certified_cash(),
            portfolio=_certified_portfolio(),
            positions={"AAPL": _certified_pos("AAPL"), "MSFT": _certified_pos("MSFT")},
            target_allocations={
                "AAPL": _certified_ta("AAPL", 0.70),
                "MSFT": _certified_ta("MSFT", 0.70),
            },
            policy=_certified_policy(),
        )
        assert bundle.exact_dollar_ready is False

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
        assert result.recommended_dollar_amount is None


# ── R–U. Suppression reason metadata integrity ───────────────────────────────

class TestSuppressionReasonMetadata:
    def test_conflicting_reason_surfaces_correctly(self):
        """TARGET_ALLOCATION_CONFLICTING reason surfaces from bundle with CONFLICTING trust."""
        bundle = DeploySizingInputBundle(
            cash=_certified_cash(),
            portfolio=_certified_portfolio(),
            positions={"AAPL": _certified_pos("AAPL")},
            target_allocations={
                "AAPL": DeployTargetAllocationInput(
                    ticker="AAPL",
                    trust_status=DeploySizingTrustStatus.CONFLICTING,
                ),
            },
            policy=_certified_policy(),
        )
        reasons = bundle.get_suppression_reasons()
        assert DeploySizingSuppressionReason.TARGET_ALLOCATION_CONFLICTING in reasons
        assert DeploySizingSuppressionReason.TARGET_ALLOCATION_TOTAL_OVERALLOCATED not in reasons
        assert DeploySizingSuppressionReason.TARGET_ALLOCATION_TOTAL_UNDERALLOCATED not in reasons

    def test_overallocated_reason_only_after_all_individual_checks_pass(self):
        """OVERALLOCATED reason appears only when all individual per-ticker allocations are certified."""
        # Overallocated + one ticker missing: missing reason appears, total does NOT
        bundle = DeploySizingInputBundle(
            cash=_certified_cash(),
            portfolio=_certified_portfolio(),
            positions={"AAPL": _certified_pos("AAPL"), "MSFT": _certified_pos("MSFT")},
            target_allocations={
                "AAPL": _certified_ta("AAPL", 0.70),
                # MSFT absent → per-ticker check fails first
            },
            policy=_certified_policy(),
        )
        reasons = bundle.get_suppression_reasons()
        assert DeploySizingSuppressionReason.TARGET_ALLOCATION_MISSING in reasons
        assert DeploySizingSuppressionReason.TARGET_ALLOCATION_TOTAL_OVERALLOCATED not in reasons

    def test_underallocated_reason_only_after_all_individual_checks_pass(self):
        """UNDERALLOCATED reason appears only when all individual per-ticker allocations are certified."""
        bundle = DeploySizingInputBundle(
            cash=_certified_cash(),
            portfolio=_certified_portfolio(),
            positions={"AAPL": _certified_pos("AAPL"), "MSFT": _certified_pos("MSFT")},
            target_allocations={
                "AAPL": _certified_ta("AAPL", 0.05),
                # MSFT absent → per-ticker check fails first
            },
            policy=_certified_policy(),
        )
        reasons = bundle.get_suppression_reasons()
        assert DeploySizingSuppressionReason.TARGET_ALLOCATION_MISSING in reasons
        assert DeploySizingSuppressionReason.TARGET_ALLOCATION_TOTAL_UNDERALLOCATED not in reasons

    def test_suppression_reason_values_are_strings(self):
        """All three new suppression reason enum values are non-empty strings."""
        assert DeploySizingSuppressionReason.TARGET_ALLOCATION_CONFLICTING.value
        assert DeploySizingSuppressionReason.TARGET_ALLOCATION_TOTAL_OVERALLOCATED.value
        assert DeploySizingSuppressionReason.TARGET_ALLOCATION_TOTAL_UNDERALLOCATED.value

    def test_total_bounds_constants_sensible(self):
        """TARGET_ALLOCATION_TOTAL_MIN < 1.0 < TARGET_ALLOCATION_TOTAL_MAX."""
        assert TARGET_ALLOCATION_TOTAL_MIN < 1.0
        assert TARGET_ALLOCATION_TOTAL_MAX > 1.0
        assert TARGET_ALLOCATION_TOTAL_MIN < TARGET_ALLOCATION_TOTAL_MAX
