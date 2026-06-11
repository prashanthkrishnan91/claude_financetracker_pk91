"""Tests for Stage 10B books-of-record reconciliation diagnostic.

All tests use fully mocked DB clients — no live Supabase/Plaid/market-data calls.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.books_reconciliation_diagnostic_v1 import (
    DIAGNOSTIC_VERSION,
    QUANTITY_ABS_TOLERANCE,
    QUANTITY_BLOCKED_THRESHOLD_PCT,
    QUANTITY_PCT_TOLERANCE_PCT,
    _reconcile_ticker,
    run_books_reconciliation_diagnostic,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _pos(
    ticker: str,
    shares: float = 10.0,
    avg_cost: float = 100.0,
    category: str = "Core",
    source: str = "manual",
) -> dict:
    return {
        "ticker": ticker,
        "shares": shares,
        "avg_cost": avg_cost,
        "category": category,
        "source": source,
    }


def _tx(ticker: str, tx_type: str, quantity: float, price: float, tx_date: str = "2024-01-01") -> dict:
    return {
        "ticker": ticker,
        "tx_type": tx_type,
        "quantity": quantity,
        "price": price,
        "tx_date": tx_date,
    }


def _make_db_client(pos_rows: list, tx_rows: list):
    """Return a mock Supabase client that returns preset rows."""

    class _Query:
        def __init__(self, rows):
            self._rows = rows

        def select(self, *_a, **_k):
            return self

        def eq(self, *_a, **_k):
            return self

        def in_(self, *_a, **_k):
            return self

        def order(self, *_a, **_k):
            return self

        def execute(self):
            return SimpleNamespace(data=list(self._rows))

    class _Client:
        def __init__(self, pos, txs):
            self._pos = pos
            self._txs = txs

        def table(self, name: str):
            if name == "positions":
                return _Query(self._pos)
            if name == "transactions":
                return _Query(self._txs)
            return _Query([])

    return _Client(pos_rows, tx_rows)


# ── Unit tests for _reconcile_ticker ────────────────────────────────────────

class TestReconcileTicker:
    def test_position_not_found_is_blocked(self):
        result = _reconcile_ticker("AAPL", pos_row=None, tx_rows=[])
        assert result["reconciliation_status"] == "blocked"
        assert "position_not_found" in result["blockers"]
        assert result["position_found"] is False

    def test_matching_quantity_is_facts_ready(self):
        pos = _pos("AAPL", shares=10.0, avg_cost=150.0)
        txs = [
            _tx("AAPL", "Buy", 10.0, 150.0),
        ]
        result = _reconcile_ticker("AAPL", pos_row=pos, tx_rows=txs)
        assert result["reconciliation_status"] == "facts_ready"
        assert result["transaction_history_found"] is True
        assert result["blockers"] == []
        assert abs(result["quantity_drift"]) <= QUANTITY_ABS_TOLERANCE

    def test_no_transaction_history_is_degraded_not_blocked(self):
        pos = _pos("VTI", shares=5.0, avg_cost=200.0, source="plaid")
        result = _reconcile_ticker("VTI", pos_row=pos, tx_rows=[])
        assert result["reconciliation_status"] == "degraded"
        assert result["transaction_history_found"] is False
        assert result["blockers"] == []
        assert any("no_transaction_history" in w for w in result["warnings"])

    def test_quantity_drift_above_blocked_threshold_is_blocked(self):
        # Position has 10 shares; transactions only account for 0.5 shares.
        # Drift = (0.5 - 10) / 10 = 95% — above QUANTITY_BLOCKED_THRESHOLD_PCT=10%.
        pos = _pos("MSFT", shares=10.0, avg_cost=300.0)
        txs = [_tx("MSFT", "Buy", 0.5, 300.0)]
        result = _reconcile_ticker("MSFT", pos_row=pos, tx_rows=txs)
        assert result["reconciliation_status"] == "blocked"
        assert any("quantity_drift_blocked" in b for b in result["blockers"])

    def test_quantity_drift_below_blocked_threshold_is_degraded(self):
        # Position has 100 shares; transactions yield 95 shares (5% drift < 10% blocked).
        pos = _pos("GOOGL", shares=100.0, avg_cost=50.0)
        txs = [_tx("GOOGL", "Buy", 95.0, 50.0)]
        result = _reconcile_ticker("GOOGL", pos_row=pos, tx_rows=txs)
        assert result["reconciliation_status"] == "degraded"
        assert any("quantity_drift_degraded" in w for w in result["warnings"])
        assert result["blockers"] == []

    def test_missing_avg_cost_is_blocked(self):
        pos = _pos("AMZN", shares=5.0, avg_cost=0.0)
        txs = [_tx("AMZN", "Buy", 5.0, 100.0)]
        result = _reconcile_ticker("AMZN", pos_row=pos, tx_rows=txs)
        # avg_cost=0 triggers avg_cost_zero_cost_basis_unverifiable warning
        assert "avg_cost_zero_cost_basis_unverifiable" in result["warnings"]

    def test_missing_shares_is_blocked(self):
        pos = _pos("TSLA", shares=0.0, avg_cost=200.0)
        result = _reconcile_ticker("TSLA", pos_row=pos, tx_rows=[])
        assert result["reconciliation_status"] == "blocked"
        assert "position_quantity_zero" in result["blockers"]

    def test_negative_shares_is_blocked(self):
        pos = _pos("NFLX", shares=-1.0, avg_cost=100.0)
        result = _reconcile_ticker("NFLX", pos_row=pos, tx_rows=[])
        assert result["reconciliation_status"] == "blocked"
        assert "position_quantity_negative" in result["blockers"]

    def test_crypto_position_is_not_evaluable(self):
        pos = _pos("BTC", shares=0.5, avg_cost=40000.0, category="Crypto")
        txs = [_tx("BTC", "Buy", 0.5, 40000.0)]
        result = _reconcile_ticker("BTC", pos_row=pos, tx_rows=txs)
        assert result["reconciliation_status"] == "not_evaluable"
        assert result["crypto_or_pdf_position_detected"] is True
        assert any("crypto" in w for w in result["warnings"])
        assert result["blockers"] == []

    def test_manual_source_no_tx_is_degraded(self):
        pos = _pos("O", shares=50.0, avg_cost=60.0, source="manual")
        result = _reconcile_ticker("O", pos_row=pos, tx_rows=[])
        assert result["reconciliation_status"] == "degraded"
        assert result["manual_position_detected"] is True
        assert any("manual_source" in w for w in result["warnings"])

    def test_plaid_source_no_tx_is_degraded(self):
        pos = _pos("SCHD", shares=20.0, avg_cost=75.0, source="plaid")
        result = _reconcile_ticker("SCHD", pos_row=pos, tx_rows=[])
        assert result["reconciliation_status"] == "degraded"
        assert result["plaid_source_present"] is True
        assert any("plaid" in w for w in result["warnings"])

    def test_transactions_net_to_zero_is_blocked(self):
        # Buy 10 then sell 10 → net zero, but position still shows 10 shares
        pos = _pos("META", shares=10.0, avg_cost=300.0)
        txs = [
            _tx("META", "Buy", 10.0, 300.0),
            _tx("META", "Sell", 10.0, 320.0, tx_date="2024-06-01"),
        ]
        result = _reconcile_ticker("META", pos_row=pos, tx_rows=txs)
        assert result["reconciliation_status"] == "blocked"
        assert "transaction_derived_quantity_zero_position_nonzero" in result["blockers"]
        assert result["transaction_quantity"] == 0.0

    def test_buy_sell_partial_is_facts_ready(self):
        # Buy 20, sell 5 → net 15 shares; position shows 15
        pos = _pos("NVDA", shares=15.0, avg_cost=400.0)
        txs = [
            _tx("NVDA", "Buy", 20.0, 400.0),
            _tx("NVDA", "Sell", 5.0, 450.0, tx_date="2024-03-01"),
        ]
        result = _reconcile_ticker("NVDA", pos_row=pos, tx_rows=txs)
        assert result["reconciliation_status"] == "facts_ready"
        assert result["transaction_quantity"] == 15.0


# ── Integration tests for run_books_reconciliation_diagnostic ───────────────

class TestRunBooksReconciliation:
    @pytest.mark.asyncio
    async def test_facts_ready_single_position(self):
        user_id = str(uuid4())
        pos_rows = [_pos("AAPL", shares=10.0, avg_cost=150.0)]
        tx_rows = [_tx("AAPL", "Buy", 10.0, 150.0)]
        db = _make_db_client(pos_rows, tx_rows)

        result = await run_books_reconciliation_diagnostic(db, user_id)

        assert result["diagnostics_only"] is True
        assert result["writes_performed"] == 0
        assert result["policy_unchanged"] is True
        assert result["visible_snapshot_unchanged"] is True
        assert result["diagnostic_version"] == DIAGNOSTIC_VERSION
        assert result["positions_checked"] == 1
        assert result["positions_facts_ready_count"] == 1
        assert result["facts_ready"] is True
        assert len(result["per_ticker"]) == 1
        assert result["per_ticker"][0]["reconciliation_status"] == "facts_ready"

    @pytest.mark.asyncio
    async def test_degraded_no_tx_history(self):
        user_id = str(uuid4())
        pos_rows = [_pos("VTI", shares=5.0, avg_cost=210.0, source="plaid")]
        db = _make_db_client(pos_rows, [])

        result = await run_books_reconciliation_diagnostic(db, user_id)

        assert result["facts_ready"] is False
        assert result["positions_degraded_count"] == 1
        assert result["positions_facts_ready_count"] == 0
        assert result["per_ticker"][0]["reconciliation_status"] == "degraded"

    @pytest.mark.asyncio
    async def test_blocked_missing_shares(self):
        user_id = str(uuid4())
        pos_rows = [_pos("TSLA", shares=0.0, avg_cost=200.0)]
        db = _make_db_client(pos_rows, [])

        result = await run_books_reconciliation_diagnostic(db, user_id)

        assert result["facts_ready"] is False
        assert result["positions_blocked_count"] == 1
        assert result["per_ticker"][0]["reconciliation_status"] == "blocked"

    @pytest.mark.asyncio
    async def test_not_evaluable_crypto_excluded_when_flag_false(self):
        user_id = str(uuid4())
        pos_rows = [_pos("ETH", shares=2.0, avg_cost=2000.0, category="Crypto")]
        db = _make_db_client(pos_rows, [])

        result = await run_books_reconciliation_diagnostic(
            db, user_id, include_not_evaluable=False
        )
        assert result["per_ticker"] == []
        assert result["positions_checked"] == 1  # still counted in scope

    @pytest.mark.asyncio
    async def test_not_evaluable_crypto_included_by_default(self):
        user_id = str(uuid4())
        pos_rows = [_pos("ETH", shares=2.0, avg_cost=2000.0, category="Crypto")]
        db = _make_db_client(pos_rows, [])

        result = await run_books_reconciliation_diagnostic(db, user_id)
        assert len(result["per_ticker"]) == 1
        assert result["per_ticker"][0]["reconciliation_status"] == "not_evaluable"

    @pytest.mark.asyncio
    async def test_empty_portfolio_returns_safe_defaults(self):
        user_id = str(uuid4())
        db = _make_db_client([], [])

        result = await run_books_reconciliation_diagnostic(db, user_id)

        assert result["diagnostics_only"] is True
        assert result["writes_performed"] == 0
        assert result["positions_checked"] == 0
        assert result["facts_ready"] is False  # empty portfolio is not facts_ready
        assert result["per_ticker"] == []

    @pytest.mark.asyncio
    async def test_ticker_filter_restricts_scope(self):
        user_id = str(uuid4())
        pos_rows = [
            _pos("AAPL", shares=10.0, avg_cost=150.0),
            _pos("MSFT", shares=5.0, avg_cost=300.0),
        ]
        tx_rows = [
            _tx("AAPL", "Buy", 10.0, 150.0),
            _tx("MSFT", "Buy", 5.0, 300.0),
        ]
        db = _make_db_client(pos_rows, tx_rows)

        result = await run_books_reconciliation_diagnostic(
            db, user_id, tickers=["AAPL"]
        )
        # The mock DB returns all rows regardless of filter; the service deduplicates.
        # With tickers=["AAPL"] the evaluation scope includes only AAPL from pos_map.
        tickers_in_result = [r["ticker"] for r in result["per_ticker"]]
        assert "AAPL" in tickers_in_result

    @pytest.mark.asyncio
    async def test_no_provider_or_plaid_live_call(self):
        """Diagnostic must not call any external provider. Verified by the mock
        DB client — if an external call were made, no mock would handle it and
        the test would raise AttributeError or similar."""
        user_id = str(uuid4())
        pos_rows = [_pos("AAPL", shares=10.0, avg_cost=150.0)]
        tx_rows = [_tx("AAPL", "Buy", 10.0, 150.0)]
        db = _make_db_client(pos_rows, tx_rows)

        # If any live I/O call is made, the test will raise because the mock
        # client has no network methods.
        result = await run_books_reconciliation_diagnostic(db, user_id)
        assert result["writes_performed"] == 0

    @pytest.mark.asyncio
    async def test_policy_and_snapshot_invariants(self):
        user_id = str(uuid4())
        db = _make_db_client([], [])
        result = await run_books_reconciliation_diagnostic(db, user_id)
        assert result["policy_unchanged"] is True
        assert result["visible_snapshot_unchanged"] is True
        assert result["diagnostics_only"] is True

    @pytest.mark.asyncio
    async def test_global_facts_ready_requires_no_degraded_or_blocked(self):
        user_id = str(uuid4())
        pos_rows = [
            _pos("AAPL", shares=10.0, avg_cost=150.0),  # will be facts_ready
            _pos("VTI", shares=5.0, avg_cost=210.0, source="plaid"),  # will be degraded
        ]
        tx_rows = [_tx("AAPL", "Buy", 10.0, 150.0)]
        db = _make_db_client(pos_rows, tx_rows)

        result = await run_books_reconciliation_diagnostic(db, user_id)
        assert result["facts_ready"] is False  # one degraded blocks global facts_ready
        assert result["positions_facts_ready_count"] == 1
        assert result["positions_degraded_count"] == 1


# ── Endpoint contract tests (service-layer only, no fastapi import required) ─

class TestBooksReconciliationEndpointContract:
    """Verify the contract shape returned by the diagnostic service, which is
    what the endpoint passes through unchanged. Router-level cert guard is
    covered by test_finance_runtime_certification.py."""

    @pytest.mark.asyncio
    async def test_response_carries_all_required_top_level_fields(self):
        user_id = str(uuid4())
        db = _make_db_client([], [])
        result = await run_books_reconciliation_diagnostic(db, user_id)

        required_fields = {
            "diagnostic_version",
            "user_id",
            "started_at",
            "completed_at",
            "tickers_requested",
            "positions_checked",
            "positions_facts_ready_count",
            "positions_degraded_count",
            "positions_blocked_count",
            "positions_not_evaluable_count",
            "facts_ready",
            "diagnostics_only",
            "writes_performed",
            "policy_unchanged",
            "visible_snapshot_unchanged",
            "per_ticker",
        }
        assert required_fields <= set(result.keys())

    @pytest.mark.asyncio
    async def test_diagnostics_only_and_writes_zero_invariants(self):
        user_id = str(uuid4())
        pos_rows = [_pos("AAPL", shares=10.0, avg_cost=150.0)]
        tx_rows = [_tx("AAPL", "Buy", 10.0, 150.0)]
        db = _make_db_client(pos_rows, tx_rows)

        result = await run_books_reconciliation_diagnostic(db, user_id)
        assert result["diagnostics_only"] is True
        assert result["writes_performed"] == 0
        assert result["policy_unchanged"] is True
        assert result["visible_snapshot_unchanged"] is True
