"""Tests for Stage 10C VTI DCA benchmark diagnostic.

All tests use fully mocked DB clients — no live Supabase/Plaid/market-data calls.
No writes. No provider calls. No Buy/Hold/Trim/Sell changes.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.vti_dca_benchmark_diagnostic_v1 import (
    DIAGNOSTIC_VERSION,
    VTI_TICKER,
    _extract_books_gate,
    _find_vti_price,
    run_vti_dca_benchmark_diagnostic,
)


# ── DB Mock helpers ───────────────────────────────────────────────────────────

def _make_deposit(deposit_date: str, amount: float, executed: bool = True) -> dict:
    return {
        "id": str(uuid4()),
        "deposit_date": deposit_date,
        "amount": amount,
        "executed": executed,
        "executed_at": deposit_date if executed else None,
    }


def _make_vti_price(price_date: str, close_price: float) -> dict:
    return {"price_date": price_date, "close_price": close_price}


def _make_tx(tx_type: str, quantity: float, price: float, tx_date: str, amount: float | None = None) -> dict:
    return {
        "tx_type": tx_type,
        "quantity": quantity,
        "price": price,
        "tx_date": tx_date,
        "amount": amount if amount is not None else quantity * price,
    }


def _make_snapshot(total_equity: float, total_cost: float) -> dict:
    return {
        "total_equity": total_equity,
        "total_cost": total_cost,
        "snapshot_at": "2025-01-01T00:00:00Z",
    }


class _TableQuery:
    """Chainable query mock that always returns the preset rows."""

    def __init__(self, rows: list):
        self._rows = rows

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def in_(self, *_a, **_k):
        return self

    def gte(self, *_a, **_k):
        return self

    def lte(self, *_a, **_k):
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def execute(self):
        return SimpleNamespace(data=list(self._rows))


class _MockDB:
    """Mock Supabase client routing by table name."""

    def __init__(
        self,
        deposit_plans: list | None = None,
        transactions: list | None = None,
        price_history: list | None = None,
        portfolio_snapshots: list | None = None,
        positions: list | None = None,
    ):
        self._routes: dict[str, list] = {
            "deposit_plans": deposit_plans or [],
            "transactions": transactions or [],
            "price_history": price_history or [],
            "portfolio_snapshots": portfolio_snapshots or [],
            "positions": positions or [],
        }

    def table(self, name: str) -> _TableQuery:
        return _TableQuery(self._routes.get(name, []))


def _books_gate_pass() -> dict:
    return {
        "benchmark_books_gate": "pass",
        "benchmark_books_gate_reason": "all_non_crypto_positions_facts_ready_or_not_evaluable",
        "next_recommended_stage": "stage10c_vti_benchmark",
        "per_ticker": [],
    }


def _books_gate_pass_with_exclusions(flagged: list[str] | None = None) -> dict:
    per_ticker = [
        {"ticker": t, "reconciliation_status": "blocked"} for t in (flagged or ["MSFT"])
    ]
    return {
        "benchmark_books_gate": "pass_with_exclusions",
        "benchmark_books_gate_reason": "blocked_degraded_tickers_appear_explainable",
        "next_recommended_stage": "stage10b_manual_review",
        "per_ticker": per_ticker,
    }


def _books_gate_blocked() -> dict:
    return {
        "benchmark_books_gate": "blocked",
        "benchmark_books_gate_reason": "hard_blocked_positions_missing_or_invalid_data",
        "next_recommended_stage": "stage10b_books_repair",
        "per_ticker": [],
    }


# ── Unit tests: _find_vti_price ────────────────────────────────────────────────

class TestFindVtiPrice:
    def test_exact_match(self):
        pm = {"2024-01-05": 230.0}
        price, date_used, reason = _find_vti_price("2024-01-05", pm)
        assert price == 230.0
        assert date_used == "2024-01-05"
        assert reason == "exact_match"

    def test_weekend_maps_to_next_trading_day(self):
        # 2024-01-06 is a Saturday
        pm = {"2024-01-08": 232.0}  # Monday
        price, date_used, reason = _find_vti_price("2024-01-06", pm)
        assert price == 232.0
        assert date_used == "2024-01-08"
        assert reason == "next_available_trading_day"

    def test_weekend_falls_back_to_previous_when_no_next(self):
        # Only Friday price available
        pm = {"2024-01-05": 230.0}
        price, date_used, reason = _find_vti_price("2024-01-06", pm)
        assert price == 230.0
        assert date_used == "2024-01-05"
        assert reason == "previous_available_trading_day"

    def test_missing_date_returns_none(self):
        pm = {"2024-01-05": 230.0}
        price, date_used, reason = _find_vti_price("2023-01-01", pm)
        assert price is None
        assert date_used is None
        assert reason == "missing_price_data"

    def test_empty_price_map_returns_missing(self):
        price, date_used, reason = _find_vti_price("2024-01-05", {})
        assert price is None
        assert reason == "missing_price_data"

    def test_invalid_date_format(self):
        price, date_used, reason = _find_vti_price("not-a-date", {"2024-01-05": 230.0})
        assert price is None
        assert reason == "invalid_date_format"


# ── Unit tests: _extract_books_gate ───────────────────────────────────────────

class TestExtractBooksGate:
    def test_none_returns_unavailable_with_warning(self):
        gate, blockers, warnings = _extract_books_gate(None)
        assert gate == "unavailable"
        assert blockers == []
        assert "books_gate_runtime_not_available" in warnings

    def test_pass_returns_pass_no_issues(self):
        gate, blockers, warnings = _extract_books_gate(_books_gate_pass())
        assert gate == "pass"
        assert blockers == []
        assert warnings == []

    def test_pass_with_exclusions_returns_warnings(self):
        gate, blockers, warnings = _extract_books_gate(_books_gate_pass_with_exclusions(["MSFT", "NVDA"]))
        assert gate == "pass_with_exclusions"
        assert blockers == []
        assert any("MSFT" in w and "NVDA" in w for w in warnings)

    def test_blocked_returns_blockers(self):
        gate, blockers, warnings = _extract_books_gate(_books_gate_blocked())
        assert gate == "blocked"
        assert any("books_gate_blocked" in b for b in blockers)
        assert warnings == []

    def test_unknown_returns_unavailable_with_warning(self):
        gate, blockers, warnings = _extract_books_gate({
            "benchmark_books_gate": "unknown",
            "benchmark_books_gate_reason": "insufficient_evidence",
            "per_ticker": [],
        })
        assert gate == "unavailable"
        assert warnings


# ── Integration tests: run_vti_dca_benchmark_diagnostic ──────────────────────

class TestSuccessfulComputation:
    @pytest.mark.asyncio
    async def test_full_benchmark_from_deposits_and_prices(self):
        deposits = [
            _make_deposit("2024-01-05", 900.0),
            _make_deposit("2024-01-19", 900.0),
        ]
        prices = [
            _make_vti_price("2024-01-05", 200.0),
            _make_vti_price("2024-01-19", 210.0),
            _make_vti_price("2025-01-01", 250.0),  # current
        ]
        snapshots = [_make_snapshot(total_equity=45000.0, total_cost=30000.0)]
        db = _MockDB(deposit_plans=deposits, price_history=prices, portfolio_snapshots=snapshots)

        result = await run_vti_dca_benchmark_diagnostic(
            db, str(uuid4()), books_gate_result=_books_gate_pass()
        )

        assert result["benchmark_status"] == "computed"
        assert result["deposits_detected_count"] == 2
        assert result["benchmark_contribution_count"] == 2
        assert result["vti_dca_units"] == pytest.approx(900 / 200.0 + 900 / 210.0, rel=1e-4)
        assert result["vti_dca_cost_basis"] == pytest.approx(1800.0, rel=1e-4)
        assert result["vti_dca_current_value"] is not None
        assert result["actual_portfolio_value"] == 45000.0
        assert result["actual_cost_basis"] == 30000.0
        assert result["actual_return_abs"] == pytest.approx(15000.0, rel=1e-4)
        assert result["relative_vs_vti_abs"] is not None

    @pytest.mark.asyncio
    async def test_invariants_always_present(self):
        db = _MockDB(
            deposit_plans=[_make_deposit("2024-01-05", 900.0)],
            price_history=[
                _make_vti_price("2024-01-05", 200.0),
                _make_vti_price("2025-01-01", 250.0),
            ],
            portfolio_snapshots=[_make_snapshot(50000.0, 35000.0)],
        )
        result = await run_vti_dca_benchmark_diagnostic(
            db, str(uuid4()), books_gate_result=_books_gate_pass()
        )
        assert result["diagnostics_only"] is True
        assert result["writes_performed"] == 0
        assert result["policy_unchanged"] is True
        assert result["visible_snapshot_unchanged"] is True


class TestMissingDeposits:
    @pytest.mark.asyncio
    async def test_no_deposits_returns_blocked(self):
        db = _MockDB(price_history=[_make_vti_price("2024-01-05", 200.0)])
        result = await run_vti_dca_benchmark_diagnostic(
            db, str(uuid4()), books_gate_result=_books_gate_pass()
        )
        assert result["benchmark_status"] == "blocked"
        assert any("no_executed_deposits" in b for b in result["benchmark_blockers"])
        assert result["benchmark_contribution_count"] == 0

    @pytest.mark.asyncio
    async def test_unexecuted_deposits_only_returns_blocked(self):
        deposits = [_make_deposit("2024-01-05", 900.0, executed=False)]
        db = _MockDB(deposit_plans=deposits, price_history=[_make_vti_price("2024-01-05", 200.0)])
        result = await run_vti_dca_benchmark_diagnostic(
            db, str(uuid4()), books_gate_result=_books_gate_pass()
        )
        assert result["benchmark_status"] == "blocked"
        assert result["benchmark_contribution_count"] == 0


class TestBuyTransactionsFallback:
    @pytest.mark.asyncio
    async def test_fallback_to_buy_transactions_when_no_deposits(self):
        txs = [
            _make_tx("Buy", 4.5, 200.0, "2024-01-05", amount=900.0),
            _make_tx("Buy", 4.2, 210.0, "2024-01-19", amount=882.0),
        ]
        prices = [
            _make_vti_price("2024-01-05", 200.0),
            _make_vti_price("2024-01-19", 210.0),
            _make_vti_price("2025-01-01", 250.0),
        ]
        db = _MockDB(transactions=txs, price_history=prices)
        result = await run_vti_dca_benchmark_diagnostic(
            db, str(uuid4()), books_gate_result=_books_gate_pass()
        )
        assert result["contribution_source_mode"] == "buy_transactions_fallback"
        assert any("buy_transactions" in w for w in result["benchmark_warnings"])
        # Should be degraded not computed (because this is fallback mode)
        assert result["benchmark_status"] == "degraded"


class TestMissingVtiPrices:
    @pytest.mark.asyncio
    async def test_no_vti_price_history_blocks(self):
        deposits = [_make_deposit("2024-01-05", 900.0)]
        db = _MockDB(deposit_plans=deposits, price_history=[])
        result = await run_vti_dca_benchmark_diagnostic(
            db, str(uuid4()), books_gate_result=_books_gate_pass()
        )
        assert result["benchmark_status"] == "blocked"
        assert any("vti_price_history_unavailable" in b for b in result["benchmark_blockers"])

    @pytest.mark.asyncio
    async def test_partial_vti_prices_degrades_not_blocks(self):
        deposits = [
            _make_deposit("2024-01-05", 900.0),
            _make_deposit("2023-01-01", 900.0),  # no VTI price near this date
        ]
        prices = [
            _make_vti_price("2024-01-05", 200.0),
            _make_vti_price("2025-01-01", 250.0),
        ]
        db = _MockDB(
            deposit_plans=deposits,
            price_history=prices,
            portfolio_snapshots=[_make_snapshot(50000.0, 35000.0)],
        )
        result = await run_vti_dca_benchmark_diagnostic(
            db, str(uuid4()), books_gate_result=_books_gate_pass()
        )
        # Has some prices but some missing → degraded
        assert result["benchmark_status"] == "degraded"
        assert len(result["missing_price_points"]) == 1
        assert result["available_price_points_count"] == 1
        assert result["required_price_points_count"] == 2

    @pytest.mark.asyncio
    async def test_all_vti_prices_missing_for_contribution_dates_blocks(self):
        deposits = [_make_deposit("2024-01-05", 900.0)]
        # Only a price for a date far from the deposit (> 7 days away) so mapping fails
        prices = [
            _make_vti_price("2023-06-01", 180.0),  # too far from 2024-01-05
            _make_vti_price("2025-01-01", 250.0),
        ]
        db = _MockDB(deposit_plans=deposits, price_history=prices)
        result = await run_vti_dca_benchmark_diagnostic(
            db, str(uuid4()), books_gate_result=_books_gate_pass()
        )
        assert result["benchmark_status"] == "blocked"
        assert any("no_vti_prices_available_for_any_contribution_date" in b for b in result["benchmark_blockers"])


class TestWeekendNonTradingDayMapping:
    @pytest.mark.asyncio
    async def test_saturday_deposit_maps_to_next_monday(self):
        # 2024-01-06 is Saturday; 2024-01-08 is Monday
        deposits = [_make_deposit("2024-01-06", 900.0)]
        prices = [
            _make_vti_price("2024-01-08", 232.0),
            _make_vti_price("2025-01-01", 250.0),
        ]
        db = _MockDB(
            deposit_plans=deposits,
            price_history=prices,
            portfolio_snapshots=[_make_snapshot(50000.0, 35000.0)],
        )
        result = await run_vti_dca_benchmark_diagnostic(
            db, str(uuid4()), books_gate_result=_books_gate_pass()
        )
        records = result["contribution_records"]
        assert len(records) == 1
        rec = records[0]
        assert rec["requested_date"] == "2024-01-06"
        assert rec["price_date_used"] == "2024-01-08"
        assert rec["mapping_reason"] == "next_available_trading_day"
        assert rec["price"] == pytest.approx(232.0, rel=1e-4)

    @pytest.mark.asyncio
    async def test_contribution_records_always_explicit_mapping(self):
        deposits = [
            _make_deposit("2024-01-05", 900.0),  # exact Friday match
            _make_deposit("2024-01-06", 900.0),  # Saturday → next Monday
        ]
        prices = [
            _make_vti_price("2024-01-05", 230.0),
            _make_vti_price("2024-01-08", 232.0),
            _make_vti_price("2025-01-01", 250.0),
        ]
        db = _MockDB(
            deposit_plans=deposits,
            price_history=prices,
            portfolio_snapshots=[_make_snapshot(50000.0, 35000.0)],
        )
        result = await run_vti_dca_benchmark_diagnostic(
            db, str(uuid4()), books_gate_result=_books_gate_pass()
        )
        records = result["contribution_records"]
        reasons = {r["requested_date"]: r["mapping_reason"] for r in records}
        assert reasons["2024-01-05"] == "exact_match"
        assert reasons["2024-01-06"] == "next_available_trading_day"


class TestActualPortfolioValueMissing:
    @pytest.mark.asyncio
    async def test_no_portfolio_snapshot_degrades(self):
        deposits = [_make_deposit("2024-01-05", 900.0)]
        prices = [
            _make_vti_price("2024-01-05", 200.0),
            _make_vti_price("2025-01-01", 250.0),
        ]
        db = _MockDB(deposit_plans=deposits, price_history=prices)
        result = await run_vti_dca_benchmark_diagnostic(
            db, str(uuid4()), books_gate_result=_books_gate_pass()
        )
        assert result["actual_portfolio_value"] is None
        assert result["relative_vs_vti_abs"] is None
        assert result["relative_vs_vti_pct"] is None
        assert result["benchmark_status"] == "degraded"
        assert any("actual_portfolio_value_unavailable" in w for w in result["benchmark_warnings"])

    @pytest.mark.asyncio
    async def test_zero_total_equity_snapshot_treated_as_missing(self):
        deposits = [_make_deposit("2024-01-05", 900.0)]
        prices = [
            _make_vti_price("2024-01-05", 200.0),
            _make_vti_price("2025-01-01", 250.0),
        ]
        db = _MockDB(
            deposit_plans=deposits,
            price_history=prices,
            portfolio_snapshots=[_make_snapshot(0.0, 0.0)],
        )
        result = await run_vti_dca_benchmark_diagnostic(
            db, str(uuid4()), books_gate_result=_books_gate_pass()
        )
        assert result["actual_portfolio_value"] is None


class TestBooksGateBehavior:
    @pytest.mark.asyncio
    async def test_books_gate_pass_allows_computed(self):
        deposits = [_make_deposit("2024-01-05", 900.0)]
        prices = [
            _make_vti_price("2024-01-05", 200.0),
            _make_vti_price("2025-01-01", 250.0),
        ]
        db = _MockDB(
            deposit_plans=deposits,
            price_history=prices,
            portfolio_snapshots=[_make_snapshot(50000.0, 35000.0)],
        )
        result = await run_vti_dca_benchmark_diagnostic(
            db, str(uuid4()), books_gate_result=_books_gate_pass()
        )
        assert result["benchmark_status"] == "computed"

    @pytest.mark.asyncio
    async def test_books_gate_unavailable_produces_degraded_not_computed(self):
        deposits = [_make_deposit("2024-01-05", 900.0)]
        prices = [
            _make_vti_price("2024-01-05", 200.0),
            _make_vti_price("2025-01-01", 250.0),
        ]
        db = _MockDB(
            deposit_plans=deposits,
            price_history=prices,
            portfolio_snapshots=[_make_snapshot(50000.0, 35000.0)],
        )
        result = await run_vti_dca_benchmark_diagnostic(
            db, str(uuid4()), books_gate_result=None
        )
        assert result["benchmark_status"] == "degraded"
        assert any("books_gate_runtime_not_available" in w for w in result["benchmark_warnings"])

    @pytest.mark.asyncio
    async def test_books_gate_blocked_returns_benchmark_blocked(self):
        deposits = [_make_deposit("2024-01-05", 900.0)]
        prices = [_make_vti_price("2024-01-05", 200.0)]
        db = _MockDB(deposit_plans=deposits, price_history=prices)
        result = await run_vti_dca_benchmark_diagnostic(
            db, str(uuid4()), books_gate_result=_books_gate_blocked()
        )
        assert result["benchmark_status"] == "blocked"
        assert any("books_gate_blocked" in b for b in result["benchmark_blockers"])

    @pytest.mark.asyncio
    async def test_books_gate_pass_with_exclusions_produces_degraded_with_warnings(self):
        deposits = [_make_deposit("2024-01-05", 900.0)]
        prices = [
            _make_vti_price("2024-01-05", 200.0),
            _make_vti_price("2025-01-01", 250.0),
        ]
        db = _MockDB(
            deposit_plans=deposits,
            price_history=prices,
            portfolio_snapshots=[_make_snapshot(50000.0, 35000.0)],
        )
        result = await run_vti_dca_benchmark_diagnostic(
            db, str(uuid4()), books_gate_result=_books_gate_pass_with_exclusions(["MSFT", "NVDA"])
        )
        assert result["benchmark_status"] == "degraded"
        assert any("pass_with_exclusions" in w for w in result["benchmark_warnings"])
        assert any("MSFT" in w for w in result["benchmark_warnings"])


class TestNoWritesAndNoProviderCalls:
    @pytest.mark.asyncio
    async def test_writes_performed_always_zero(self):
        db = _MockDB()
        result = await run_vti_dca_benchmark_diagnostic(db, str(uuid4()))
        assert result["writes_performed"] == 0

    @pytest.mark.asyncio
    async def test_policy_unchanged_always_true(self):
        db = _MockDB()
        result = await run_vti_dca_benchmark_diagnostic(db, str(uuid4()))
        assert result["policy_unchanged"] is True

    @pytest.mark.asyncio
    async def test_visible_snapshot_unchanged_always_true(self):
        db = _MockDB()
        result = await run_vti_dca_benchmark_diagnostic(db, str(uuid4()))
        assert result["visible_snapshot_unchanged"] is True

    @pytest.mark.asyncio
    async def test_diagnostics_only_always_true(self):
        db = _MockDB()
        result = await run_vti_dca_benchmark_diagnostic(db, str(uuid4()))
        assert result["diagnostics_only"] is True


class TestEndpointContractFields:
    @pytest.mark.asyncio
    async def test_all_required_fields_present(self):
        db = _MockDB()
        result = await run_vti_dca_benchmark_diagnostic(db, str(uuid4()))
        required = [
            "diagnostic_version", "user_id", "started_at", "completed_at",
            "start_date", "end_date", "deposits_detected_count",
            "benchmark_contribution_count", "actual_portfolio_value",
            "actual_cost_basis", "actual_return_abs", "actual_return_pct",
            "vti_dca_units", "vti_dca_cost_basis", "vti_dca_current_value",
            "vti_dca_return_abs", "vti_dca_return_pct",
            "relative_vs_vti_abs", "relative_vs_vti_pct",
            "benchmark_status", "benchmark_blockers", "benchmark_warnings",
            "required_price_points_count", "available_price_points_count",
            "missing_price_points", "contribution_source_mode",
            "contribution_records", "diagnostics_only", "writes_performed",
            "policy_unchanged", "visible_snapshot_unchanged",
        ]
        for field in required:
            assert field in result, f"Missing required field: {field}"

    @pytest.mark.asyncio
    async def test_diagnostic_version_correct(self):
        db = _MockDB()
        result = await run_vti_dca_benchmark_diagnostic(db, str(uuid4()))
        assert result["diagnostic_version"] == DIAGNOSTIC_VERSION

    @pytest.mark.asyncio
    async def test_include_position_breakdown_false_omits_contribution_records(self):
        deposits = [_make_deposit("2024-01-05", 900.0)]
        prices = [
            _make_vti_price("2024-01-05", 200.0),
            _make_vti_price("2025-01-01", 250.0),
        ]
        db = _MockDB(deposit_plans=deposits, price_history=prices)
        result = await run_vti_dca_benchmark_diagnostic(
            db,
            str(uuid4()),
            include_position_breakdown=False,
            books_gate_result=_books_gate_pass(),
        )
        assert result["contribution_records"] == []

    @pytest.mark.asyncio
    async def test_include_position_breakdown_true_includes_contribution_records(self):
        deposits = [_make_deposit("2024-01-05", 900.0)]
        prices = [
            _make_vti_price("2024-01-05", 200.0),
            _make_vti_price("2025-01-01", 250.0),
        ]
        db = _MockDB(deposit_plans=deposits, price_history=prices)
        result = await run_vti_dca_benchmark_diagnostic(
            db,
            str(uuid4()),
            include_position_breakdown=True,
            books_gate_result=_books_gate_pass(),
        )
        assert len(result["contribution_records"]) == 1
        rec = result["contribution_records"][0]
        assert "requested_date" in rec
        assert "price_date_used" in rec
        assert "price" in rec
        assert "mapping_reason" in rec
        assert "contribution_amount" in rec
        assert "units_purchased" in rec


class TestVtiDcaComputationAccuracy:
    @pytest.mark.asyncio
    async def test_units_and_cost_basis_correct(self):
        # $900 at $200 = 4.5 units; $900 at $225 = 4.0 units
        deposits = [
            _make_deposit("2024-01-05", 900.0),
            _make_deposit("2024-01-19", 900.0),
        ]
        prices = [
            _make_vti_price("2024-01-05", 200.0),
            _make_vti_price("2024-01-19", 225.0),
            _make_vti_price("2025-01-01", 250.0),  # current
        ]
        db = _MockDB(
            deposit_plans=deposits,
            price_history=prices,
            portfolio_snapshots=[_make_snapshot(50000.0, 35000.0)],
        )
        result = await run_vti_dca_benchmark_diagnostic(
            db, str(uuid4()), books_gate_result=_books_gate_pass()
        )
        expected_units = 900 / 200.0 + 900 / 225.0
        expected_cost = 1800.0
        expected_value = expected_units * 250.0
        assert result["vti_dca_units"] == pytest.approx(expected_units, rel=1e-4)
        assert result["vti_dca_cost_basis"] == pytest.approx(expected_cost, rel=1e-4)
        assert result["vti_dca_current_value"] == pytest.approx(expected_value, rel=1e-3)
        assert result["vti_dca_return_abs"] == pytest.approx(expected_value - expected_cost, rel=1e-3)

    @pytest.mark.asyncio
    async def test_relative_vs_vti_positive_when_portfolio_outperforms(self):
        deposits = [_make_deposit("2024-01-05", 900.0)]
        prices = [
            _make_vti_price("2024-01-05", 200.0),
            _make_vti_price("2025-01-01", 250.0),
        ]
        # Portfolio value much higher than VTI DCA value
        db = _MockDB(
            deposit_plans=deposits,
            price_history=prices,
            portfolio_snapshots=[_make_snapshot(10000.0, 900.0)],
        )
        result = await run_vti_dca_benchmark_diagnostic(
            db, str(uuid4()), books_gate_result=_books_gate_pass()
        )
        assert result["relative_vs_vti_abs"] is not None
        assert result["relative_vs_vti_abs"] > 0

    @pytest.mark.asyncio
    async def test_relative_vs_vti_negative_when_portfolio_underperforms(self):
        deposits = [_make_deposit("2024-01-05", 900.0)]
        prices = [
            _make_vti_price("2024-01-05", 200.0),
            _make_vti_price("2025-01-01", 1000.0),  # VTI 5x'd
        ]
        # Portfolio value much lower than VTI DCA value
        db = _MockDB(
            deposit_plans=deposits,
            price_history=prices,
            portfolio_snapshots=[_make_snapshot(900.0, 900.0)],  # flat portfolio
        )
        result = await run_vti_dca_benchmark_diagnostic(
            db, str(uuid4()), books_gate_result=_books_gate_pass()
        )
        assert result["relative_vs_vti_abs"] is not None
        assert result["relative_vs_vti_abs"] < 0
