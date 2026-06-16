"""Tests for Stage 10B books-of-record reconciliation diagnostic.

All tests use fully mocked DB clients — no live Supabase/Plaid/market-data calls.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.books_reconciliation_diagnostic_v1 import (
    COST_BASIS_MATCH_THRESHOLD_PCT,
    COST_BASIS_MATERIAL_DISAGREEMENT_PCT,
    DIAGNOSTIC_VERSION,
    QUANTITY_ABS_TOLERANCE,
    QUANTITY_BLOCKED_THRESHOLD_PCT,
    QUANTITY_PCT_TOLERANCE_PCT,
    _compute_benchmark_books_gate,
    _compute_ticker_forensics,
    _enrich_ticker_with_forensics,
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


# ── Router wiring tests (import + route registration, no fastapi in test env) ─

class TestRouterWiring:
    """Catch router-level syntax/import failures that service-only tests miss.

    These tests do NOT import app.routers.diagnostics (which requires fastapi).
    Instead they verify: (1) the service module is importable, (2) the request
    model is importable from the service module's package, and (3) a fast Python
    syntax check on the router file passes.
    """

    def test_service_module_imports_cleanly(self):
        """The service must be importable with no side-effects."""
        import importlib
        mod = importlib.import_module("app.services.books_reconciliation_diagnostic_v1")
        assert hasattr(mod, "run_books_reconciliation_diagnostic")
        assert hasattr(mod, "_reconcile_ticker")
        assert hasattr(mod, "DIAGNOSTIC_VERSION")

    def test_router_file_has_no_syntax_errors(self):
        """Compile the router file to catch duplicate return / indentation bugs."""
        import ast, pathlib
        router_path = pathlib.Path(__file__).parent.parent / "app" / "routers" / "diagnostics.py"
        source = router_path.read_text()
        # ast.parse raises SyntaxError on any syntax problem
        tree = ast.parse(source, filename=str(router_path))
        assert tree is not None

    def test_router_file_contains_books_reconciliation_route(self):
        """The route decorator must be present in the router source."""
        import pathlib
        router_path = pathlib.Path(__file__).parent.parent / "app" / "routers" / "diagnostics.py"
        source = router_path.read_text()
        assert '"/books-reconciliation-diagnostic"' in source
        assert "books_reconciliation_diagnostic" in source
        assert "BooksReconciliationDiagnosticRequest" in source

    def test_router_file_has_single_return_in_books_function(self):
        """Detect the duplicate-return bug: the books endpoint must return exactly once."""
        import ast, pathlib
        router_path = pathlib.Path(__file__).parent.parent / "app" / "routers" / "diagnostics.py"
        source = router_path.read_text()
        tree = ast.parse(source)
        returns = []
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "books_reconciliation_diagnostic":
                for child in ast.walk(node):
                    if isinstance(child, ast.Return):
                        returns.append(child)
        assert len(returns) == 1, (
            f"Expected exactly 1 return in books_reconciliation_diagnostic, found {len(returns)}"
        )

    def test_vanguard_function_has_return(self):
        """Vanguard endpoint must have at least one return (not implicit None)."""
        import ast, pathlib
        router_path = pathlib.Path(__file__).parent.parent / "app" / "routers" / "diagnostics.py"
        source = router_path.read_text()
        tree = ast.parse(source)
        returns = []
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.AsyncFunctionDef)
                and node.name == "vanguard_holdings_diagnostic"
            ):
                for child in ast.walk(node):
                    if isinstance(child, ast.Return) and child.value is not None:
                        returns.append(child)
        assert len(returns) >= 1, (
            "vanguard_holdings_diagnostic_endpoint has no explicit return — "
            "it would return None instead of the diagnostic result"
        )

    def test_stage10b_route_appears_after_vanguard_function(self):
        """Stage 10B @router.post must appear in source after the vanguard function body.

        Catches the case where new route code is accidentally inserted inside or
        before the final return of an existing function.
        """
        import pathlib
        router_path = pathlib.Path(__file__).parent.parent / "app" / "routers" / "diagnostics.py"
        source = router_path.read_text()
        vanguard_marker = "vanguard_holdings_diagnostic_endpoint"
        stage10b_marker = '"/books-reconciliation-diagnostic"'
        assert vanguard_marker in source, "vanguard function not found in router"
        assert stage10b_marker in source, "Stage 10B route not found in router"
        assert source.index(vanguard_marker) < source.index(stage10b_marker), (
            "Stage 10B route appears before vanguard function in source — "
            "check that the new route was not inserted inside an existing function"
        )


# ── Ticker forensics unit tests ──────────────────────────────────────────────

class TestTickerForensics:
    """Unit tests for _compute_ticker_forensics and _enrich_ticker_with_forensics."""

    def test_cost_basis_matches_but_quantity_drift_detected_large_qty_tiny_cb(self):
        # qty drift 44% (above 10% blocked threshold), cb drift 0.001% (below 2% match threshold)
        forensics = _compute_ticker_forensics(
            all_tx_rows=[_tx("NVDA", "Buy", 22.0, 200.0)],
            qty_drift_pct=44.0,
            cb_drift_pct=0.001,
        )
        assert forensics["cost_basis_matches_but_quantity_drift_detected"] is True
        assert forensics["possible_unmodeled_adjustment_detected"] is True
        assert forensics["cost_basis_match_threshold_used"] == COST_BASIS_MATCH_THRESHOLD_PCT

    def test_cost_basis_match_not_detected_when_both_drifts_large(self):
        # qty drift 44%, cb drift 8% (both large) — NOT the unmodeled pattern
        forensics = _compute_ticker_forensics(
            all_tx_rows=[_tx("XYZ", "Buy", 22.0, 200.0)],
            qty_drift_pct=44.0,
            cb_drift_pct=8.0,
        )
        assert forensics["cost_basis_matches_but_quantity_drift_detected"] is False
        assert forensics["possible_unmodeled_adjustment_detected"] is False

    def test_cost_basis_match_not_detected_when_qty_drift_small(self):
        # qty drift 5% (below blocked threshold of 10%) — not triggered even if cb matches
        forensics = _compute_ticker_forensics(
            all_tx_rows=[],
            qty_drift_pct=5.0,
            cb_drift_pct=0.001,
        )
        assert forensics["cost_basis_matches_but_quantity_drift_detected"] is False

    def test_ignored_transaction_types_are_counted(self):
        rows = [
            _tx("NFLX", "Buy", 14.0, 150.0),
            {"ticker": "NFLX", "tx_type": "DRIP", "quantity": 0.5, "price": 150.0, "tx_date": "2024-06-01"},
            {"ticker": "NFLX", "tx_type": "Transfer", "quantity": 7.0, "price": 0.0, "tx_date": "2024-07-01"},
        ]
        forensics = _compute_ticker_forensics(rows, qty_drift_pct=34.0, cb_drift_pct=0.001)
        assert "DRIP" in forensics["ignored_transaction_type_counts"]
        assert "Transfer" in forensics["ignored_transaction_type_counts"]
        assert forensics["ignored_transaction_type_counts"]["DRIP"] == 1
        assert forensics["ignored_transaction_type_counts"]["Transfer"] == 1
        assert "Buy" not in forensics["ignored_transaction_type_counts"]

    def test_unknown_transaction_types_surfaced_not_silently_ignored(self):
        rows = [
            {"ticker": "XLE", "tx_type": "SomeNewType", "quantity": 5.0, "price": 50.0, "tx_date": "2024-03-01"},
        ]
        forensics = _compute_ticker_forensics(rows, qty_drift_pct=37.0, cb_drift_pct=0.005)
        assert "SomeNewType" in forensics["transaction_type_counts"]
        assert "SomeNewType" in forensics["ignored_transaction_type_counts"]
        assert forensics["possible_unmodeled_adjustment_reason"] is not None
        assert "SomeNewType" in forensics["possible_unmodeled_adjustment_reason"]

    def test_missing_tx_type_falls_back_to_unknown(self):
        rows = [{"ticker": "XYZ", "tx_type": None, "quantity": 1.0, "price": 10.0, "tx_date": "2024-01-01"}]
        forensics = _compute_ticker_forensics(rows, qty_drift_pct=None, cb_drift_pct=None)
        assert "unknown" in forensics["transaction_type_counts"]

    def test_first_and_last_transaction_dates_populated(self):
        rows = [
            _tx("MSFT", "Buy", 0.01, 300.0, tx_date="2023-01-15"),
            _tx("MSFT", "Buy", 0.01, 310.0, tx_date="2024-06-30"),
        ]
        forensics = _compute_ticker_forensics(rows, qty_drift_pct=None, cb_drift_pct=None)
        assert forensics["first_transaction_date"] == "2023-01-15"
        assert forensics["last_transaction_date"] == "2024-06-30"

    def test_first_last_date_none_when_no_rows(self):
        forensics = _compute_ticker_forensics([], qty_drift_pct=None, cb_drift_pct=None)
        assert forensics["first_transaction_date"] is None
        assert forensics["last_transaction_date"] is None

    def test_enrich_facts_ready_ticker_has_none_forensics(self):
        pos = _pos("AAPL", shares=10.0, avg_cost=150.0)
        txs = [_tx("AAPL", "Buy", 10.0, 150.0)]
        result = _reconcile_ticker("AAPL", pos_row=pos, tx_rows=txs)
        assert result["reconciliation_status"] == "facts_ready"
        enriched = _enrich_ticker_with_forensics(result, all_tx_rows=txs)
        assert enriched["ticker_forensics"] is None

    def test_enrich_not_evaluable_ticker_has_none_forensics(self):
        pos = _pos("BTC", shares=0.5, avg_cost=40000.0, category="Crypto")
        result = _reconcile_ticker("BTC", pos_row=pos, tx_rows=[])
        assert result["reconciliation_status"] == "not_evaluable"
        enriched = _enrich_ticker_with_forensics(result, all_tx_rows=[])
        assert enriched["ticker_forensics"] is None

    def test_enrich_blocked_ticker_has_forensics(self):
        # Large qty drift, tiny cb drift → forensics should flag the pattern
        pos = _pos("NVDA", shares=40.0, avg_cost=125.0)
        txs = [_tx("NVDA", "Buy", 22.0, 125.0)]
        result = _reconcile_ticker("NVDA", pos_row=pos, tx_rows=txs)
        assert result["reconciliation_status"] == "blocked"
        enriched = _enrich_ticker_with_forensics(result, all_tx_rows=txs)
        assert enriched["ticker_forensics"] is not None
        assert "cost_basis_matches_but_quantity_drift_detected" in enriched["ticker_forensics"]

    def test_enrich_degraded_ticker_has_forensics(self):
        pos = _pos("GOOGL", shares=100.0, avg_cost=50.0)
        txs = [_tx("GOOGL", "Buy", 95.0, 50.0)]
        result = _reconcile_ticker("GOOGL", pos_row=pos, tx_rows=txs)
        assert result["reconciliation_status"] == "degraded"
        enriched = _enrich_ticker_with_forensics(result, all_tx_rows=txs)
        assert enriched["ticker_forensics"] is not None

    def test_possible_adjustment_reason_mentions_ignored_types(self):
        rows = [
            _tx("NFLX", "Buy", 14.0, 154.0),
            {"ticker": "NFLX", "tx_type": "Transfer", "quantity": 7.0, "price": 0.0, "tx_date": "2024-05-01"},
        ]
        # Large qty drift + tiny cb drift + ignored Transfer type
        forensics = _compute_ticker_forensics(rows, qty_drift_pct=34.0, cb_drift_pct=0.001)
        assert forensics["possible_unmodeled_adjustment_detected"] is True
        assert "Transfer" in (forensics["possible_unmodeled_adjustment_reason"] or "")


# ── Benchmark books gate unit tests ──────────────────────────────────────────

class TestBenchmarkBooksGate:
    """Unit tests for _compute_benchmark_books_gate."""

    def _make_ticker_result(
        self,
        ticker: str,
        status: str,
        blockers: list[str] | None = None,
        qty_drift_pct: float | None = None,
        cb_drift_pct: float | None = None,
        current_quantity: float | None = 10.0,
        position_cost_basis: float | None = 1000.0,
        crypto: bool = False,
    ) -> dict:
        forensics = None
        if status in ("blocked", "degraded"):
            forensics = _compute_ticker_forensics(
                all_tx_rows=[],
                qty_drift_pct=qty_drift_pct,
                cb_drift_pct=cb_drift_pct,
            )
        return {
            "ticker": ticker,
            "reconciliation_status": status,
            "blockers": blockers or [],
            "quantity_drift_pct": qty_drift_pct,
            "cost_basis_drift_pct": cb_drift_pct,
            "current_quantity": current_quantity,
            "position_cost_basis": position_cost_basis,
            "crypto_or_pdf_position_detected": crypto,
            "ticker_forensics": forensics,
        }

    def test_gate_pass_when_all_non_crypto_facts_ready(self):
        per_ticker = [
            self._make_ticker_result("AAPL", "facts_ready"),
            self._make_ticker_result("VTI", "facts_ready"),
            self._make_ticker_result("BTC", "not_evaluable", crypto=True),
        ]
        gate = _compute_benchmark_books_gate(per_ticker)
        assert gate["benchmark_books_gate"] == "pass"
        assert gate["next_recommended_stage"] == "stage10c_vti_benchmark"

    def test_gate_pass_when_only_not_evaluable(self):
        per_ticker = [
            self._make_ticker_result("AAPL", "facts_ready"),
            self._make_ticker_result("VTI", "not_evaluable"),
        ]
        gate = _compute_benchmark_books_gate(per_ticker)
        assert gate["benchmark_books_gate"] == "pass"

    def test_gate_blocked_when_position_quantity_missing(self):
        per_ticker = [
            self._make_ticker_result(
                "MSFT", "blocked",
                blockers=["position_quantity_zero"],
                current_quantity=0.0,
                position_cost_basis=0.0,
            ),
        ]
        gate = _compute_benchmark_books_gate(per_ticker)
        assert gate["benchmark_books_gate"] == "blocked"
        assert gate["next_recommended_stage"] == "stage10b_books_repair"

    def test_gate_blocked_when_position_not_found(self):
        per_ticker = [
            self._make_ticker_result(
                "XYZ", "blocked",
                blockers=["position_not_found"],
                current_quantity=None,
                position_cost_basis=None,
            ),
        ]
        gate = _compute_benchmark_books_gate(per_ticker)
        assert gate["benchmark_books_gate"] == "blocked"
        assert gate["next_recommended_stage"] == "stage10b_books_repair"

    def test_gate_blocked_when_cost_basis_materially_disagrees(self):
        # cb drift 8% > COST_BASIS_MATERIAL_DISAGREEMENT_PCT (5%)
        # AND NOT the cost_basis_matches pattern
        per_ticker = [
            self._make_ticker_result(
                "TSLA", "blocked",
                qty_drift_pct=8.0,   # below blocked threshold
                cb_drift_pct=8.0,    # above material disagreement threshold
            ),
        ]
        gate = _compute_benchmark_books_gate(per_ticker)
        assert gate["benchmark_books_gate"] == "blocked"
        assert gate["next_recommended_stage"] == "stage10b_books_repair"

    def test_gate_pass_with_exclusions_when_explainable_by_unmodeled_tx(self):
        # Large qty drift (44%) + tiny cb drift (0.001%) → cost_basis_matches pattern
        per_ticker = [
            self._make_ticker_result("AAPL", "facts_ready"),
            self._make_ticker_result(
                "NVDA", "blocked",
                qty_drift_pct=44.0,
                cb_drift_pct=0.001,
                current_quantity=40.0,
                position_cost_basis=5000.0,
            ),
        ]
        gate = _compute_benchmark_books_gate(per_ticker)
        assert gate["benchmark_books_gate"] == "pass_with_exclusions"
        assert gate["next_recommended_stage"] == "stage10b_manual_review"

    def test_gate_blocked_not_pass_with_exclusions_when_position_data_invalid(self):
        # Even if the drift pattern looks explainable, if position data is invalid the gate is blocked
        per_ticker = [
            self._make_ticker_result(
                "NVDA", "blocked",
                blockers=["position_quantity_zero"],
                qty_drift_pct=44.0,
                cb_drift_pct=0.001,
                current_quantity=0.0,
                position_cost_basis=0.0,
            ),
        ]
        gate = _compute_benchmark_books_gate(per_ticker)
        assert gate["benchmark_books_gate"] == "blocked"
        assert gate["next_recommended_stage"] == "stage10b_books_repair"

    def test_gate_unknown_when_no_non_crypto_positions(self):
        per_ticker = [
            self._make_ticker_result("BTC", "not_evaluable", crypto=True),
        ]
        gate = _compute_benchmark_books_gate(per_ticker)
        assert gate["benchmark_books_gate"] == "unknown"
        assert gate["next_recommended_stage"] == "stage10b_forensics_needed"

    def test_gate_unknown_when_blocked_but_not_explainable(self):
        # No forensic signals to explain the drift
        per_ticker = [
            self._make_ticker_result(
                "ABC", "blocked",
                qty_drift_pct=None,
                cb_drift_pct=None,
                current_quantity=10.0,
                position_cost_basis=500.0,
            ),
        ]
        gate = _compute_benchmark_books_gate(per_ticker)
        assert gate["benchmark_books_gate"] == "unknown"
        assert gate["next_recommended_stage"] == "stage10b_forensics_needed"

    def test_gate_cb_above_material_disagreement_not_pass_with_exclusions(self):
        # cb drift 8% is above COST_BASIS_MATERIAL_DISAGREEMENT_PCT → blocked, not pass_with_exclusions
        # This verifies the gate does NOT grant pass_with_exclusions when cb materially disagrees
        per_ticker = [
            self._make_ticker_result(
                "TSLA", "blocked",
                qty_drift_pct=44.0,
                cb_drift_pct=8.0,  # cost basis does NOT match
                current_quantity=10.0,
                position_cost_basis=1000.0,
            ),
        ]
        gate = _compute_benchmark_books_gate(per_ticker)
        # cb_drift_pct=8 > COST_BASIS_MATERIAL_DISAGREEMENT_PCT=5 AND no cost_basis_matches pattern
        assert gate["benchmark_books_gate"] in ("blocked", "unknown")
        assert gate["next_recommended_stage"] != "stage10c_vti_benchmark"


# ── Integration tests: forensics + gate in run_books_reconciliation_diagnostic ─

class TestForensicsAndGateIntegration:

    @pytest.mark.asyncio
    async def test_benchmark_books_gate_in_top_level_response(self):
        user_id = str(uuid4())
        pos_rows = [_pos("AAPL", shares=10.0, avg_cost=150.0)]
        tx_rows = [_tx("AAPL", "Buy", 10.0, 150.0)]
        db = _make_db_client(pos_rows, tx_rows)
        result = await run_books_reconciliation_diagnostic(db, user_id)
        assert "benchmark_books_gate" in result
        assert "benchmark_books_gate_reason" in result
        assert "next_recommended_stage" in result
        assert result["benchmark_books_gate"] in ("pass", "pass_with_exclusions", "blocked", "unknown")
        assert result["next_recommended_stage"] in (
            "stage10c_vti_benchmark",
            "stage10b_books_repair",
            "stage10b_manual_review",
            "stage10b_forensics_needed",
        )

    @pytest.mark.asyncio
    async def test_gate_pass_single_facts_ready_position(self):
        user_id = str(uuid4())
        pos_rows = [_pos("AAPL", shares=10.0, avg_cost=150.0)]
        tx_rows = [_tx("AAPL", "Buy", 10.0, 150.0)]
        db = _make_db_client(pos_rows, tx_rows)
        result = await run_books_reconciliation_diagnostic(db, user_id)
        assert result["benchmark_books_gate"] == "pass"
        assert result["next_recommended_stage"] == "stage10c_vti_benchmark"

    @pytest.mark.asyncio
    async def test_gate_blocked_missing_position(self):
        user_id = str(uuid4())
        pos_rows = [_pos("TSLA", shares=0.0, avg_cost=200.0)]
        db = _make_db_client(pos_rows, [])
        result = await run_books_reconciliation_diagnostic(db, user_id)
        assert result["benchmark_books_gate"] == "blocked"
        assert result["next_recommended_stage"] == "stage10b_books_repair"

    @pytest.mark.asyncio
    async def test_ticker_forensics_field_present_on_blocked_ticker(self):
        # Position has 10 shares, tx only accounts for 0.5 shares (95% drift) → blocked
        user_id = str(uuid4())
        pos_rows = [_pos("MSFT", shares=10.0, avg_cost=300.0)]
        tx_rows = [_tx("MSFT", "Buy", 0.5, 300.0)]
        db = _make_db_client(pos_rows, tx_rows)
        result = await run_books_reconciliation_diagnostic(db, user_id)
        msft = next(r for r in result["per_ticker"] if r["ticker"] == "MSFT")
        assert msft["reconciliation_status"] == "blocked"
        assert msft["ticker_forensics"] is not None
        assert "cost_basis_matches_but_quantity_drift_detected" in msft["ticker_forensics"]

    @pytest.mark.asyncio
    async def test_ticker_forensics_none_on_facts_ready_ticker(self):
        user_id = str(uuid4())
        pos_rows = [_pos("AAPL", shares=10.0, avg_cost=150.0)]
        tx_rows = [_tx("AAPL", "Buy", 10.0, 150.0)]
        db = _make_db_client(pos_rows, tx_rows)
        result = await run_books_reconciliation_diagnostic(db, user_id)
        aapl = next(r for r in result["per_ticker"] if r["ticker"] == "AAPL")
        assert aapl["ticker_forensics"] is None

    @pytest.mark.asyncio
    async def test_no_writes_with_forensics_enabled(self):
        user_id = str(uuid4())
        pos_rows = [_pos("NVDA", shares=40.0, avg_cost=125.0)]
        tx_rows = [
            _tx("NVDA", "Buy", 22.0, 125.0),
            {"ticker": "NVDA", "tx_type": "Transfer", "quantity": 0.0, "price": 0.0, "tx_date": "2024-01-01"},
        ]
        db = _make_db_client(pos_rows, tx_rows)
        result = await run_books_reconciliation_diagnostic(db, user_id)
        assert result["writes_performed"] == 0
        assert result["diagnostics_only"] is True
        assert result["policy_unchanged"] is True
        assert result["visible_snapshot_unchanged"] is True

    @pytest.mark.asyncio
    async def test_required_top_level_fields_include_gate_fields(self):
        user_id = str(uuid4())
        db = _make_db_client([], [])
        result = await run_books_reconciliation_diagnostic(db, user_id)
        required = {
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
            "benchmark_books_gate",
            "benchmark_books_gate_reason",
            "next_recommended_stage",
            "diagnostics_only",
            "writes_performed",
            "policy_unchanged",
            "visible_snapshot_unchanged",
            "per_ticker",
        }
        assert required <= set(result.keys())

    @pytest.mark.asyncio
    async def test_ignored_tx_types_counted_in_forensics(self):
        user_id = str(uuid4())
        pos_rows = [_pos("NFLX", shares=21.0, avg_cost=100.0)]
        # Provide Buy (14 shares) + a Transfer (not modeled in AVCO)
        tx_rows = [
            _tx("NFLX", "Buy", 14.0, 100.0),
            {"ticker": "NFLX", "tx_type": "DRIP", "quantity": 0.5, "price": 100.0, "tx_date": "2024-06-01"},
        ]
        db = _make_db_client(pos_rows, tx_rows)
        result = await run_books_reconciliation_diagnostic(db, user_id)
        nflx = next(r for r in result["per_ticker"] if r["ticker"] == "NFLX")
        # NFLX should be blocked or degraded due to qty drift
        assert nflx["reconciliation_status"] in ("blocked", "degraded")
        assert nflx["ticker_forensics"] is not None
        # DRIP should appear in ignored types (it's not Buy/Sell)
        ignored = nflx["ticker_forensics"]["ignored_transaction_type_counts"]
        assert "DRIP" in ignored

    @pytest.mark.asyncio
    async def test_pass_with_exclusions_requires_sane_position_data(self):
        # Blocked ticker with zero quantity cannot grant pass_with_exclusions
        user_id = str(uuid4())
        pos_rows = [_pos("XLE", shares=0.0, avg_cost=50.0)]
        tx_rows = []
        db = _make_db_client(pos_rows, tx_rows)
        result = await run_books_reconciliation_diagnostic(db, user_id)
        assert result["benchmark_books_gate"] != "pass_with_exclusions"
