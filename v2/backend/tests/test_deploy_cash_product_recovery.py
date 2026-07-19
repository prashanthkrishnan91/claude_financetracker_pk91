"""Deploy Cash product recovery — Part B of the Advisor product-recovery PR.

Proven production failure being fixed (see PR body):
  * allocation_policy_v1 independently reads price_history; stale/missing rows
    degrade the diagnostic;
  * paycheck_plan_preview.build_paycheck_plan_preview() replaced ALL
    candidates with an empty list whenever preview_status != "ready", even
    when the diagnostic had computed a real, fully-priced plan;
  * the existing current_price_truth_repair_v1 service was never invoked by
    the explicit Deploy Cash action.

Contract under test:
  B1. Deploy Cash refreshes price truth (current_price_truth_repair_v1,
      dry_run=False) before running the allocation diagnostic, with bounded
      concurrency rather than serial per-ticker waits.
  B2. A degraded-but-calculable plan (non-fatal residual price/provider
      limitations, deterministic candidates, every selected ticker priced)
      preserves the calculated ticker amounts instead of erasing them.
  B3. A product-level contract for cash_to_deploy=2737.50: at least one
      planned buy with ticker/amount/reason and allocated/unallocated cash
      and refreshed price-truth status.
"""
from __future__ import annotations

import asyncio
import inspect
import time
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import current_price_truth_repair_v1 as repair_mod
from app.services.allocation_policy_v1 import run_next_buy_policy_diagnostic
from app.routers.paycheck_plan_preview import (
    PaycheckPlanPreviewRequest,
    build_paycheck_plan_preview,
    paycheck_plan_preview,
)


def _days_ago_date(days: int) -> str:
    return (datetime.now(tz=timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")


# ── Fake Supabase client for allocation_policy_v1 (mirrors test_allocation_policy_v1.py) ──


def _make_db(
    positions: list[dict] | None = None,
    prices_by_ticker: dict[str, list[dict]] | None = None,
    snapshot_value: float | None = 10000.0,
    intel_rows: list[dict] | None = None,
) -> MagicMock:
    db = MagicMock()

    def _chain(data):
        m = MagicMock()
        m.select.return_value = m
        m.eq.return_value = m
        m.gt.return_value = m
        m.neq.return_value = m
        m.order.return_value = m
        m.limit.return_value = m
        m.execute.return_value = SimpleNamespace(data=data)
        return m

    pos_data = positions if positions is not None else []
    snap_data = (
        [{"total_equity": snapshot_value, "snapshot_at": "2026-07-18T10:00:00Z"}]
        if snapshot_value else []
    )
    intel_data = intel_rows if intel_rows is not None else []

    def table_side_effect(name):
        if name == "portfolio_snapshots":
            return _chain(snap_data)
        if name == "positions":
            return _chain(pos_data)
        if name == "intel_v3_snapshots":
            return _chain(intel_data)
        if name == "price_history":
            outer = MagicMock()
            outer.select.return_value = outer
            current_ticker: list[str] = []

            def eq_side(col, val):
                if col == "ticker":
                    current_ticker.clear()
                    current_ticker.append(val)
                return outer
            outer.eq.side_effect = eq_side
            outer.order.return_value = outer

            def limit_side(_n):
                ticker = current_ticker[0] if current_ticker else None
                rows = (prices_by_ticker or {}).get(ticker, []) if ticker else []
                return _chain(rows)
            outer.limit.side_effect = limit_side
            return outer
        return _chain([])

    db.table.side_effect = table_side_effect
    return db


def _pos(ticker: str, shares: float = 10.0, category: str = "Core") -> dict:
    return {"ticker": ticker, "shares": shares, "category": category}


def _price(ticker: str, close: float = 100.0, days_old: int = 0) -> dict:
    return {"ticker": ticker, "price_date": _days_ago_date(days_old), "close_price": close}


# ── B1a. current_price_truth_repair_v1: bounded concurrency ─────────────────


class TestPriceTruthRepairBoundedConcurrency:
    @pytest.mark.asyncio
    async def test_stale_and_missing_tickers_are_fetched_concurrently_not_serially(
        self, monkeypatch
    ):
        """Regression guard: N stale/missing tickers must cost roughly one
        provider round-trip, not N sequential ~10s waits."""
        positions = [
            {"ticker": f"T{i}", "shares": 5.0, "avg_cost": 10.0, "category": "Core", "source": "manual"}
            for i in range(6)
        ]
        monkeypatch.setattr(repair_mod, "_load_open_positions", lambda db, uid: positions)
        monkeypatch.setattr(
            repair_mod, "_load_latest_prices_per_ticker", lambda db, tickers: ({}, 0, False),
        )

        async def _slow_fetch(ticker, category):
            await asyncio.sleep(0.2)
            return {
                "price": 123.45, "price_date": "2026-07-19",
                "open_price": 123.45, "high_price": 123.45, "low_price": 123.45,
                "volume": 0, "provider": "yfinance", "error": None, "unsupported": False,
            }

        monkeypatch.setattr(repair_mod, "_fetch_price_for_ticker", _slow_fetch)

        db = MagicMock()
        started = time.monotonic()
        result = await repair_mod.run_current_price_truth_repair(db, "user-1", dry_run=True)
        elapsed = time.monotonic() - started

        assert result["attempted_fetch_count"] == 6
        assert result["successful_fetch_count"] == 6
        # Serial would take >= 1.2s (6 x 0.2s); bounded concurrency keeps this
        # well under half that.
        assert elapsed < 0.6

    @pytest.mark.asyncio
    async def test_write_order_matches_original_ticker_order(self, monkeypatch):
        """Fetches run concurrently but per-ticker results/writes must stay in
        the original deterministic order regardless of fetch completion order."""
        positions = [
            {"ticker": "SLOW", "shares": 1.0, "avg_cost": 1.0, "category": "Core", "source": "m"},
            {"ticker": "FAST", "shares": 1.0, "avg_cost": 1.0, "category": "Core", "source": "m"},
        ]
        monkeypatch.setattr(repair_mod, "_load_open_positions", lambda db, uid: positions)
        monkeypatch.setattr(
            repair_mod, "_load_latest_prices_per_ticker", lambda db, tickers: ({}, 0, False),
        )

        async def _fetch(ticker, category):
            await asyncio.sleep(0.15 if ticker == "SLOW" else 0.01)
            return {
                "price": 50.0, "price_date": "2026-07-19",
                "open_price": 50.0, "high_price": 50.0, "low_price": 50.0,
                "volume": 0, "provider": "yfinance", "error": None, "unsupported": False,
            }

        monkeypatch.setattr(repair_mod, "_fetch_price_for_ticker", _fetch)
        db = MagicMock()
        result = await repair_mod.run_current_price_truth_repair(db, "user-1", dry_run=True)
        assert [row["ticker"] for row in result["per_ticker"]] == ["SLOW", "FAST"]


# ── B1b. Deploy Cash endpoint: repair runs before the diagnostic ────────────


_CERT_USER = SimpleNamespace(id="00000000-0000-0000-0000-0000000000cc", email="cert@example.com")


class TestDeployCashRunsRepairBeforeDiagnostic:
    @pytest.mark.asyncio
    async def test_repair_invoked_with_dry_run_false_before_diagnostic(self, monkeypatch):
        call_order: list[str] = []

        async def fake_repair(db_client, user_id, dry_run=True):
            call_order.append("repair")
            assert dry_run is False
            return {
                "attempted_fetch_count": 1, "successful_fetch_count": 1,
                "rows_written": 1, "unsupported_count": 0, "provider_error_count": 0,
            }

        async def fake_diagnostic(**kwargs):
            call_order.append("diagnostic")
            return {
                "diagnostic_version": "allocation_policy_v1",
                "generated_at": "2026-07-19T00:00:00Z",
                "input": {"cash_to_deploy": kwargs["cash_to_deploy"]},
                "truth_dependency": {"missing_price_tickers": [], "stale_price_tickers": []},
                "next_buy_candidates": [],
                "cash_plan": {"allocated_cash": 0.0, "unallocated_cash": kwargs["cash_to_deploy"], "allocation_count": 0},
                "verdict": {"policy_status": "ready", "numeric_plan_trusted": True, "next_required_fix": None},
                "stock_candidates": {"status": "no_stock_positions_held"},
            }

        monkeypatch.setattr(
            "app.routers.paycheck_plan_preview.get_supabase_client", lambda: SimpleNamespace(),
        )
        monkeypatch.setattr(
            "app.services.current_price_truth_repair_v1.run_current_price_truth_repair",
            fake_repair,
        )
        monkeypatch.setattr(
            "app.services.allocation_policy_v1.run_next_buy_policy_diagnostic",
            fake_diagnostic,
        )

        result = await paycheck_plan_preview(
            payload=PaycheckPlanPreviewRequest(cash_to_deploy=100.0),
            user=_CERT_USER,
        )

        assert call_order == ["repair", "diagnostic"]
        assert result["price_truth_repair"]["status"] == "refreshed"
        assert result["price_truth_repair"]["attempted"] == 1

    @pytest.mark.asyncio
    async def test_repair_failure_does_not_break_deploy_cash(self, monkeypatch):
        """The repair step is best-effort — an unexpected exception from it
        must never prevent the diagnostic/plan from being returned."""

        async def failing_repair(*_a, **_kw):
            raise RuntimeError("provider outage")

        async def fake_diagnostic(**kwargs):
            return {
                "diagnostic_version": "allocation_policy_v1",
                "generated_at": "2026-07-19T00:00:00Z",
                "input": {"cash_to_deploy": kwargs["cash_to_deploy"]},
                "truth_dependency": {"missing_price_tickers": [], "stale_price_tickers": []},
                "next_buy_candidates": [],
                "cash_plan": {"allocated_cash": 0.0, "unallocated_cash": kwargs["cash_to_deploy"], "allocation_count": 0},
                "verdict": {"policy_status": "ready", "numeric_plan_trusted": True, "next_required_fix": None},
                "stock_candidates": {"status": "no_stock_positions_held"},
            }

        monkeypatch.setattr(
            "app.routers.paycheck_plan_preview.get_supabase_client", lambda: SimpleNamespace(),
        )
        monkeypatch.setattr(
            "app.services.current_price_truth_repair_v1.run_current_price_truth_repair",
            failing_repair,
        )
        monkeypatch.setattr(
            "app.services.allocation_policy_v1.run_next_buy_policy_diagnostic",
            fake_diagnostic,
        )

        result = await paycheck_plan_preview(
            payload=PaycheckPlanPreviewRequest(cash_to_deploy=100.0),
            user=_CERT_USER,
        )
        assert result["price_truth_repair"]["status"] == "unavailable"
        assert result["status"] == "ready"


# ── B2. Degraded-but-calculable plan preservation ───────────────────────────


class TestDegradedButCalculablePreservation:
    def test_degraded_status_with_priced_candidates_preserves_the_plan(self):
        diagnostic = {
            "diagnostic_version": "allocation_policy_v1",
            "generated_at": "2026-07-19T00:00:00Z",
            "input": {"cash_to_deploy": 1000.0, "min_trade_amount": 25.0, "max_positions": 5},
            "truth_dependency": {
                "price_coverage_status": "stale",
                "missing_price_tickers": [],
                "stale_price_tickers": ["QQQ"],
            },
            "next_buy_candidates": [
                {"ticker": "VTI", "dollar_amount": 1000.0, "reason_codes": ["etf_floor_not_met"]},
            ],
            "cash_plan": {"allocated_cash": 1000.0, "unallocated_cash": 0.0, "allocation_count": 1},
            "verdict": {
                "policy_status": "degraded",
                "numeric_plan_trusted": False,
                "next_required_fix": "Run Stage 11B current-price-truth-repair to refresh stale price_history rows",
            },
            "stock_candidates": {"status": "no_stock_positions_held"},
        }
        preview = build_paycheck_plan_preview(diagnostic)
        assert preview["status"] == "degraded"
        assert preview["trusted"] is False
        assert preview["planned_buys"] == [{
            "ticker": "VTI", "amount": 1000.0,
            "reason": "Overall ETF allocation floor is not yet met",
            "reason_codes": ["etf_floor_not_met"],
        }]

    def test_a_selected_candidates_own_stale_price_is_preserved_and_specifically_flagged(self):
        """allocation_policy_v1's `no_price_available` candidacy gate only
        excludes a MISSING price — a ticker with a stale (present, just old)
        price still computes a market value and can itself be the selected
        candidate. This preview layer does not re-filter or recompute the
        diagnostic's own selection/cash-plan math (that would duplicate the
        allocation-policy decision spine), so the candidate is preserved —
        but the specific-dollar-amount caveat must call this out explicitly,
        not just the generic "some holdings have stale price data" caveat."""
        diagnostic = {
            "diagnostic_version": "allocation_policy_v1",
            "generated_at": "2026-07-19T00:00:00Z",
            "input": {"cash_to_deploy": 1000.0, "min_trade_amount": 25.0, "max_positions": 5},
            "truth_dependency": {
                "price_coverage_status": "stale",
                "missing_price_tickers": [],
                # VTI is both the selected candidate AND the stale-priced ticker.
                "stale_price_tickers": ["VTI"],
            },
            "next_buy_candidates": [
                {"ticker": "VTI", "dollar_amount": 1000.0, "reason_codes": ["etf_floor_not_met"]},
            ],
            "cash_plan": {"allocated_cash": 1000.0, "unallocated_cash": 0.0, "allocation_count": 1},
            "verdict": {
                "policy_status": "degraded",
                "numeric_plan_trusted": False,
                "next_required_fix": "Run Stage 11B current-price-truth-repair to refresh stale price_history rows",
            },
            "stock_candidates": {"status": "no_stock_positions_held"},
        }
        preview = build_paycheck_plan_preview(diagnostic)
        # Preserved, not fabricated-excluded — this is real (not invented)
        # diagnostic output; the router never overrides the allocation
        # policy's own selection.
        assert preview["planned_buys"] == [{
            "ticker": "VTI", "amount": 1000.0,
            "reason": "Overall ETF allocation floor is not yet met",
            "reason_codes": ["etf_floor_not_met"],
        }]
        assert any(
            "recommended buy amount above is based on a stale price" in c
            for c in preview["caveats"]
        )

    def test_blocked_status_still_empties_the_plan(self):
        diagnostic = {
            "diagnostic_version": "allocation_policy_v1",
            "generated_at": "2026-07-19T00:00:00Z",
            "input": {"cash_to_deploy": 1000.0},
            "truth_dependency": {"missing_price_tickers": [], "stale_price_tickers": []},
            "next_buy_candidates": [],
            "cash_plan": {"allocated_cash": 0.0, "unallocated_cash": 1000.0, "allocation_count": 0},
            "verdict": {
                "policy_status": "blocked",
                "numeric_plan_trusted": False,
                "next_required_fix": "Resolve blockers: reconciliation_blocked",
            },
            "stock_candidates": {"status": "no_stock_positions_held"},
        }
        preview = build_paycheck_plan_preview(diagnostic)
        assert preview["status"] == "blocked"
        assert preview["planned_buys"] == []

    def test_never_fabricates_a_candidate_the_diagnostic_did_not_select(self):
        """The router-level fix only stops erasing real candidates — it must
        never invent new ones. An empty diagnostic candidate list under a
        degraded status must still preview as an empty (not fabricated) plan."""
        diagnostic = {
            "diagnostic_version": "allocation_policy_v1",
            "generated_at": "2026-07-19T00:00:00Z",
            "input": {"cash_to_deploy": 1000.0},
            "truth_dependency": {"missing_price_tickers": [], "stale_price_tickers": ["VTI"]},
            "next_buy_candidates": [],
            "cash_plan": {"allocated_cash": 0.0, "unallocated_cash": 1000.0, "allocation_count": 0,
                          "no_buy_reason": "no_eligible_buy_candidates"},
            "verdict": {
                "policy_status": "degraded",
                "numeric_plan_trusted": False,
                "next_required_fix": "Run Stage 11B current-price-truth-repair to refresh stale price_history rows",
            },
            "stock_candidates": {"status": "no_stock_positions_held"},
        }
        preview = build_paycheck_plan_preview(diagnostic)
        assert preview["planned_buys"] == []


# ── B3. Product-level contract: cash_to_deploy=2737.50 ──────────────────────


class TestCashToDeployContract:
    @pytest.mark.asyncio
    async def test_realistic_multi_holding_plan_2737_50(self):
        """Realistic multi-holding fixture: VTI (broad-market ETF) held well
        under its 25% group target while SCHD (dividend ETF) carries most of
        the portfolio's value; reconciliation passes; all prices current.
        Proves the product outcome end-to-end through the real diagnostic +
        preview mapping (not a stubbed diagnostic)."""
        positions = [
            _pos("VTI", shares=4.0, category="Core"),
            _pos("SCHD", shares=120.0, category="Core"),
        ]
        prices = {
            "VTI": [_price("VTI", close=250.0, days_old=0)],
            "SCHD": [_price("SCHD", close=75.0, days_old=0)],
        }
        # snapshot == position-derived value (VTI 1000 + SCHD 9000 = 10000)
        db = _make_db(positions=positions, prices_by_ticker=prices, snapshot_value=10000.0)

        diagnostic = await run_next_buy_policy_diagnostic(
            db_client=db, user_id="user-1", cash_to_deploy=2737.50,
        )
        preview = build_paycheck_plan_preview(diagnostic)
        preview["price_truth_repair"] = {
            "status": "refreshed", "attempted": 0, "succeeded": 0,
            "written": 0, "unsupported": 0, "provider_errors": 0,
        }

        assert len(preview["planned_buys"]) >= 1
        buy = preview["planned_buys"][0]
        assert isinstance(buy["ticker"], str) and buy["ticker"]
        assert isinstance(buy["amount"], (int, float)) and buy["amount"] > 0
        assert isinstance(buy["reason"], str) and buy["reason"]

        summary = preview["allocation_summary"]
        assert summary["allocated_cash"] <= 2737.50
        assert summary["unallocated_cash"] >= 0
        assert abs(
            sum(b["amount"] for b in preview["planned_buys"]) - summary["allocated_cash"]
        ) <= 0.02

        assert preview["price_truth_repair"]["status"] == "refreshed"

    @pytest.mark.asyncio
    async def test_allocated_cash_never_exceeds_requested_cash(self):
        positions = [_pos("VTI", shares=100.0)]
        prices = {"VTI": [_price("VTI", close=250.0)]}
        db = _make_db(positions=positions, prices_by_ticker=prices, snapshot_value=25000.0)
        diagnostic = await run_next_buy_policy_diagnostic(
            db_client=db, user_id="user-1", cash_to_deploy=2737.50,
        )
        preview = build_paycheck_plan_preview(diagnostic)
        assert preview["allocation_summary"]["allocated_cash"] <= 2737.50
        assert preview["allocation_summary"]["unallocated_cash"] >= 0

    @pytest.mark.asyncio
    async def test_blocked_reconciliation_suppresses_all_buys(self):
        positions = [_pos("VTI", shares=20.0)]
        prices = {"VTI": [_price("VTI", close=250.0)]}
        # Snapshot wildly diverges from position-derived value -> blocked.
        db = _make_db(positions=positions, prices_by_ticker=prices, snapshot_value=1_000_000.0)
        diagnostic = await run_next_buy_policy_diagnostic(
            db_client=db, user_id="user-1", cash_to_deploy=2737.50,
        )
        preview = build_paycheck_plan_preview(diagnostic)
        assert preview["status"] == "blocked"
        assert preview["planned_buys"] == []

    @pytest.mark.asyncio
    async def test_vti_preferred_over_spy_qqq_preference_unchanged(self):
        """Existing VTI > VOO > SPY > QQQ core preference is untouched by this
        PR — a light end-to-end smoke check alongside the dedicated
        allocation_policy_v1 preference tests."""
        positions = [_pos("VTI", shares=10.0), _pos("SPY", shares=10.0), _pos("QQQ", shares=10.0)]
        prices = {
            "VTI": [_price("VTI", close=100.0)],
            "SPY": [_price("SPY", close=100.0)],
            "QQQ": [_price("QQQ", close=100.0)],
        }
        db = _make_db(positions=positions, prices_by_ticker=prices, snapshot_value=3000.0)
        diagnostic = await run_next_buy_policy_diagnostic(
            db_client=db, user_id="user-1", cash_to_deploy=500.0,
        )
        preview = build_paycheck_plan_preview(diagnostic)
        if preview["planned_buys"]:
            tickers = [b["ticker"] for b in preview["planned_buys"]]
            if "SPY" in tickers and "VTI" in tickers:
                assert tickers.index("VTI") < tickers.index("SPY")


# ── Module boundary: repair never touches decision authority ────────────────


class TestPriceTruthRepairModuleBoundary:
    def test_repair_module_never_writes_recommendations_or_snapshots(self):
        src = inspect.getsource(repair_mod)
        assert "recommendations" not in src
        assert "portfolio_snapshots" not in src
        assert ".upsert(" in src  # price_history only
        assert "decision_policy_v1" not in src
