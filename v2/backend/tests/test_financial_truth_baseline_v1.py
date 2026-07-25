"""Tests for Stage 11A — Financial Truth Baseline diagnostic.

All tests use fully mocked DB clients — no live Supabase, provider, or LLM calls.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.financial_truth_baseline_v1 import (
    DIAGNOSTIC_VERSION,
    PRICE_STALE_BUSINESS_DAYS,
    RECONCILIATION_CERTIFIED_PCT,
    RECONCILIATION_DEGRADED_PCT,
    SNAPSHOT_STALE_HOURS,
    FinancialTruthReadError,
    _business_days_since,
    _intel_truth,
    _is_price_stale,
    _position_truth,
    _price_truth,
    _reconciliation,
    _snapshot_truth,
    _transaction_truth,
    _verdict,
    run_financial_truth_baseline,
    run_financial_truth_baseline_strict,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _hours_ago_iso(hours: float) -> str:
    return (datetime.now(tz=timezone.utc) - timedelta(hours=hours)).isoformat()


def _days_ago_date(days: int) -> str:
    return (datetime.now(tz=timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")


def _snap(
    total_equity: float | None = 100_000.0,
    total_cost: float | None = 80_000.0,
    hours_old: float = 1.0,
    cash_balance: float | None = 0.0,
) -> dict:
    return {
        "id": str(uuid4()),
        "snapshot_at": _hours_ago_iso(hours_old),
        "total_equity": total_equity,
        "total_cost": total_cost,
        "total_pnl": None,
        "total_pnl_pct": None,
        "cash_balance": cash_balance,
        "created_at": _hours_ago_iso(hours_old),
    }


def _pos(
    ticker: str = "AAPL",
    shares: float | None = 10.0,
    avg_cost: float | None = 150.0,
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


def _price(ticker: str, close: float = 160.0, days_old: int = 0) -> dict:
    return {
        "ticker": ticker,
        "price_date": _days_ago_date(days_old),
        "close_price": close,
    }


def _tx(
    ticker: str = "AAPL",
    tx_type: str = "Buy",
    quantity: float = 10.0,
    price: float = 150.0,
    tx_date: str | None = None,
) -> dict:
    return {
        "ticker": ticker,
        "tx_type": tx_type,
        "quantity": quantity,
        "price": price,
        "amount": quantity * price,
        "tx_date": tx_date or _days_ago_date(30),
        "created_at": _hours_ago_iso(720),
    }


def _rec(hours_old: float = 12.0) -> dict:
    return {"id": str(uuid4()), "created_at": _hours_ago_iso(hours_old), "action": "HOLD", "is_active": True}


def _agent(hours_old: float = 10.0) -> dict:
    return {"id": str(uuid4()), "started_at": _hours_ago_iso(hours_old), "finished_at": _hours_ago_iso(hours_old - 0.5), "status": "completed"}


class _MockQuery:
    """Supabase-style query chain mock."""

    def __init__(self, rows: list, raise_on_execute: bool = False):
        self._rows = rows
        self._raise = raise_on_execute

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def in_(self, *_a, **_k):
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def execute(self):
        if self._raise:
            raise RuntimeError("simulated DB error")
        return SimpleNamespace(data=list(self._rows))


class _MockDB:
    """Mock Supabase client routing table names to preset row lists."""

    def __init__(
        self,
        snap_rows: list | None = None,
        pos_rows: list | None = None,
        price_rows: list | None = None,
        tx_rows: list | None = None,
        rec_rows: list | None = None,
        agent_rows: list | None = None,
        intel_snap_rows: list | None = None,
        intel_snap_raise: bool = True,
        raise_tables: frozenset[str] = frozenset(),
    ):
        self._snap = snap_rows or []
        self._pos = pos_rows or []
        self._price = price_rows or []
        self._tx = tx_rows or []
        self._rec = rec_rows or []
        self._agent = agent_rows or []
        self._intel_snap = intel_snap_rows
        self._intel_snap_raise = intel_snap_raise
        self._raise_tables = raise_tables

    def table(self, name: str) -> _MockQuery:
        mapping = {
            "portfolio_snapshots": self._snap,
            "positions": self._pos,
            "price_history": self._price,
            "transactions": self._tx,
            "recommendations": self._rec,
            "agent_runs": self._agent,
        }
        if name == "intel_snapshots":
            if self._intel_snap_raise:
                return _MockQuery([], raise_on_execute=True)
            return _MockQuery(self._intel_snap or [])
        return _MockQuery(mapping.get(name, []), raise_on_execute=name in self._raise_tables)


USER_ID = str(uuid4())


# ── Unit tests: _snapshot_truth ───────────────────────────────────────────────

class TestSnapshotTruth:
    def test_no_rows_returns_unavailable(self):
        result = _snapshot_truth([])
        assert result["status"] == "unavailable"
        assert result["reason"] == "no_snapshots_found"
        assert result["latest_portfolio_value"] is None

    def test_fresh_snapshot_is_ok(self):
        result = _snapshot_truth([_snap(hours_old=1.0)])
        assert result["status"] == "ok"
        assert result["latest_portfolio_value"] == 100_000.0
        assert result["snapshot_is_stale"] is False
        assert result["warnings"] == []

    def test_stale_snapshot_warns(self):
        result = _snapshot_truth([_snap(hours_old=SNAPSHOT_STALE_HOURS + 1)])
        assert result["snapshot_is_stale"] is True
        assert any("snapshot_stale" in w for w in result["warnings"])

    def test_null_total_equity_warns(self):
        result = _snapshot_truth([_snap(total_equity=None)])
        assert any("portfolio_value_null" in w for w in result["warnings"])

    def test_null_total_cost_warns(self):
        result = _snapshot_truth([_snap(total_cost=None)])
        assert any("cost_basis_null" in w for w in result["warnings"])

    def test_positive_cash_derives_invested_value(self):
        result = _snapshot_truth([_snap(total_equity=102_737.50, cash_balance=2_737.50)])
        assert result["latest_cash_balance"] == 2_737.50
        assert result["snapshot_invested_value"] == 100_000.0

    def test_zero_cash_derives_invested_value_equal_to_total_equity(self):
        result = _snapshot_truth([_snap(total_equity=100_000.0, cash_balance=0.0)])
        assert result["latest_cash_balance"] == 0.0
        assert result["snapshot_invested_value"] == 100_000.0

    def test_negative_finite_cash_is_preserved_not_coerced_to_zero(self):
        result = _snapshot_truth([_snap(total_equity=99_500.0, cash_balance=-500.0)])
        assert result["latest_cash_balance"] == -500.0
        assert result["snapshot_invested_value"] == 100_000.0

    def test_missing_cash_balance_leaves_invested_value_none(self):
        result = _snapshot_truth([_snap(cash_balance=None)])
        assert result["latest_cash_balance"] is None
        assert result["snapshot_invested_value"] is None
        assert any("cash_balance_null" in w for w in result["warnings"])

    def test_nan_cash_balance_rejected(self):
        result = _snapshot_truth([_snap(cash_balance=float("nan"))])
        assert result["latest_cash_balance"] is None
        assert result["snapshot_invested_value"] is None

    def test_infinite_cash_balance_rejected(self):
        result = _snapshot_truth([_snap(cash_balance=float("inf"))])
        assert result["latest_cash_balance"] is None
        assert result["snapshot_invested_value"] is None

    def test_nan_total_equity_rejected(self):
        result = _snapshot_truth([_snap(total_equity=float("nan"))])
        assert result["latest_portfolio_value"] is None
        assert result["snapshot_invested_value"] is None


# ── Unit tests: _position_truth ───────────────────────────────────────────────

class TestPositionTruth:
    def test_no_rows_returns_unavailable(self):
        result = _position_truth([], [])
        assert result["status"] == "unavailable"
        assert result["open_position_count"] == 0

    def test_open_positions_counted_correctly(self):
        rows = [_pos("AAPL"), _pos("GOOG"), _pos("SOLD", category="SELL")]
        result = _position_truth(rows, [])
        assert result["open_position_count"] == 2
        assert result["total_position_count"] == 3

    def test_cost_basis_sum_computed(self):
        # AAPL: 10 × 150 = 1500; GOOG: 5 × 200 = 1000 → total 2500
        rows = [_pos("AAPL", 10.0, 150.0), _pos("GOOG", 5.0, 200.0)]
        result = _position_truth(rows, [])
        assert result["cost_basis_feasible"] is True
        assert result["cost_basis_sum"] == 2500.0

    def test_missing_avg_cost_makes_cost_infeasible(self):
        rows = [_pos("AAPL", 10.0, None)]
        result = _position_truth(rows, [])
        assert result["cost_basis_feasible"] is False
        assert result["cost_basis_sum"] is None
        assert "AAPL" in result["missing_price_or_cost_basis_tickers"]

    def test_market_value_computed_with_prices(self):
        rows = [_pos("AAPL", 10.0, 150.0)]
        prices = [_price("AAPL", 160.0, days_old=0)]
        result = _position_truth(rows, prices)
        assert result["market_value_feasible"] is True
        assert result["market_value_sum"] == 1600.0

    def test_market_value_unavailable_when_price_missing(self):
        rows = [_pos("AAPL", 10.0, 150.0)]
        result = _position_truth(rows, [])
        assert result["market_value_feasible"] is False
        assert result["market_value_sum"] is None
        assert "AAPL" in result["missing_price_or_cost_basis_tickers"]

    def test_duplicate_active_tickers_detected(self):
        rows = [_pos("AAPL"), _pos("AAPL")]
        result = _position_truth(rows, [])
        assert "AAPL" in result["duplicate_active_tickers"]
        assert any("duplicate" in w for w in result["warnings"])


# ── Unit tests: _transaction_truth ────────────────────────────────────────────

class TestTransactionTruth:
    def test_no_transactions_returns_unavailable(self):
        result = _transaction_truth([])
        assert result["status"] == "unavailable"
        assert result["cost_basis_from_transactions_feasible"] is False

    def test_counts_by_type(self):
        txs = [
            _tx("AAPL", "Buy"), _tx("AAPL", "Buy"),
            _tx("AAPL", "Sell"),
            _tx("AAPL", "CDIV"), _tx("AAPL", "DRIP"),
            _tx("", "ACH"), _tx("", "RTP"),
        ]
        result = _transaction_truth(txs)
        assert result["buy_count"] == 2
        assert result["sell_count"] == 1
        assert result["dividend_count"] == 2
        assert result["deposit_count"] == 2
        assert result["cost_basis_from_transactions_feasible"] is True

    def test_no_deposits_triggers_proxy_warning(self):
        txs = [_tx("AAPL", "Buy")]
        result = _transaction_truth(txs)
        assert result["deposit_proxy_warning"] is not None
        assert "ACH/RTP" in result["deposit_proxy_warning"]


# ── Unit tests: _price_truth ──────────────────────────────────────────────────

class TestPriceTruth:
    def test_no_open_tickers_returns_unavailable(self):
        result = _price_truth([], [])
        assert result["status"] == "unavailable"

    def test_recent_prices_counted(self):
        tickers = ["AAPL", "GOOG"]
        prices = [_price("AAPL", days_old=0), _price("GOOG", days_old=1)]
        result = _price_truth(prices, tickers)
        assert result["tickers_with_recent_price"] == 2
        assert result["missing_price_tickers"] == []
        assert result["stale_price_tickers"] == []

    def test_missing_price_ticker_detected(self):
        tickers = ["AAPL", "MISSING"]
        prices = [_price("AAPL", days_old=0)]
        result = _price_truth(prices, tickers)
        assert "MISSING" in result["missing_price_tickers"]

    def test_stale_price_ticker_detected(self):
        # Monday 2026-06-22, price from Monday 2026-06-15 → 5 business days old (> PRICE_STALE_BUSINESS_DAYS=3)
        tickers = ["AAPL"]
        prices = [{"ticker": "AAPL", "price_date": "2026-06-15", "close_price": 160.0}]
        result = _price_truth(prices, tickers, _today=date(2026, 6, 22))
        assert len(result["stale_price_tickers"]) == 1
        assert result["stale_price_tickers"][0]["ticker"] == "AAPL"
        assert result["stale_price_tickers"][0]["business_days_old"] == 5


# ── Unit tests: _reconciliation ───────────────────────────────────────────────

class TestReconciliation:
    def test_pass_when_within_tolerance(self):
        result = _reconciliation(100_000.0, 0.0, 100_000.0, 100_200.0)
        assert result["reconciliation_status"] == "pass"
        assert result["absolute_difference"] == pytest.approx(200.0)

    def test_degraded_when_exceeds_certified_but_within_degraded(self):
        # 3% difference: between RECONCILIATION_CERTIFIED_PCT and RECONCILIATION_DEGRADED_PCT
        result = _reconciliation(100_000.0, 0.0, 100_000.0, 103_000.0)
        assert result["reconciliation_status"] == "degraded"

    def test_blocked_when_exceeds_degraded_pct(self):
        # 10% difference: above RECONCILIATION_DEGRADED_PCT
        result = _reconciliation(100_000.0, 0.0, 100_000.0, 110_000.0)
        assert result["reconciliation_status"] == "blocked"

    def test_unavailable_when_snapshot_invested_value_missing(self):
        result = _reconciliation(None, None, None, 100_000.0)
        assert result["reconciliation_status"] == "unavailable"
        assert any("snapshot_invested_value_unavailable" in b for b in result["blockers"])

    def test_unavailable_when_position_mv_missing(self):
        result = _reconciliation(100_000.0, 0.0, 100_000.0, None)
        assert result["reconciliation_status"] == "unavailable"
        assert any("position_market_value_unavailable" in b for b in result["blockers"])

    def test_unavailable_when_both_missing(self):
        result = _reconciliation(None, None, None, None)
        assert result["reconciliation_status"] == "unavailable"
        assert len(result["blockers"]) == 2

    def test_positive_cash_reconciles_against_invested_value_not_total_equity(self):
        # total_equity=102_737.50 (100_000 invested + 2_737.50 cash) must
        # reconcile against 100_000 of positions, never against total_equity.
        result = _reconciliation(102_737.50, 2_737.50, 100_000.0, 100_000.0)
        assert result["reconciliation_status"] == "pass"
        assert result["snapshot_invested_value"] == 100_000.0
        assert result["absolute_difference"] == 0.0

    def test_zero_cash_passes_normally(self):
        result = _reconciliation(100_000.0, 0.0, 100_000.0, 100_000.0)
        assert result["reconciliation_status"] == "pass"

    def test_negative_finite_cash_is_arithmetic_not_coerced_to_zero(self):
        # total_equity=99_500 (100_000 invested minus a legitimate -500
        # overdrawn cash balance) — must still reconcile, and the negative
        # cash must be reported verbatim, never clamped to 0.
        result = _reconciliation(99_500.0, -500.0, 100_000.0, 100_000.0)
        assert result["reconciliation_status"] == "pass"
        assert result["snapshot_cash_balance"] == -500.0
        assert result["snapshot_invested_value"] == 100_000.0

    def test_boundary_exactly_at_certified_threshold_passes(self):
        # Exactly RECONCILIATION_CERTIFIED_PCT (1%) still passes (<=, not <).
        result = _reconciliation(100_000.0, 0.0, 100_000.0, 101_000.0)
        assert result["reconciliation_status"] == "pass"

    def test_boundary_exactly_at_degraded_threshold_is_degraded_not_blocked(self):
        # Exactly RECONCILIATION_DEGRADED_PCT (5%) is still degraded (<=, not <).
        result = _reconciliation(100_000.0, 0.0, 100_000.0, 105_000.0)
        assert result["reconciliation_status"] == "degraded"


# ── Integration tests: run_financial_truth_baseline ───────────────────────────

class TestFinancialTruthBaseline:
    @pytest.mark.asyncio
    async def test_certified_when_values_match_within_tolerance(self):
        """truth_status=certified when snapshot and position-derived values match within 1%."""
        snap = [_snap(total_equity=100_000.0, total_cost=80_000.0, hours_old=1.0)]
        pos = [_pos("AAPL", 10.0, 150.0), _pos("GOOG", 5.0, 200.0)]
        # AAPL: 10 × 160 = 1600; GOOG: 5 × 195 = 975; total = 2575
        # But we need it close to 100_000 — let's use bigger numbers
        pos2 = [_pos("AAPL", 500.0, 150.0)]
        # 500 × 200 = 100_000 → perfect match
        prices = [_price("AAPL", 200.0, days_old=0)]

        db = _MockDB(snap_rows=snap, pos_rows=pos2, price_rows=prices, tx_rows=[_tx()], rec_rows=[_rec()], agent_rows=[_agent()])
        result = await run_financial_truth_baseline(db, USER_ID)

        assert result["diagnostic_version"] == DIAGNOSTIC_VERSION
        assert result["verdict"]["truth_status"] == "certified"
        assert result["reconciliation"]["reconciliation_status"] == "pass"
        assert result["verdict"]["canonical_portfolio_value_source"] == "portfolio_snapshots"
        assert result["verdict"]["recommendations_trusted"] is False

    @pytest.mark.asyncio
    async def test_certified_with_cash_reconciles_against_invested_value(self):
        """A snapshot with real cash on top of positions must reconcile the
        INVESTED value against positions, never total_equity (which
        includes cash) directly."""
        snap = [_snap(total_equity=102_737.50, total_cost=80_000.0, cash_balance=2_737.50, hours_old=1.0)]
        pos = [_pos("AAPL", 500.0, 150.0)]
        prices = [_price("AAPL", 200.0, days_old=0)]  # 500 × 200 = 100_000

        db = _MockDB(snap_rows=snap, pos_rows=pos, price_rows=prices, tx_rows=[_tx()], rec_rows=[_rec()], agent_rows=[_agent()])
        result = await run_financial_truth_baseline(db, USER_ID)

        assert result["reconciliation"]["reconciliation_status"] == "pass"
        assert result["reconciliation"]["snapshot_invested_value"] == 100_000.0
        assert result["reconciliation"]["snapshot_cash_balance"] == 2_737.50
        assert result["verdict"]["truth_status"] == "certified"

    @pytest.mark.asyncio
    async def test_missing_cash_balance_blocks_reconciliation_not_treated_as_zero(self):
        """A snapshot whose cash_balance is missing must never be silently
        treated as zero cash — reconciliation must report unavailable."""
        snap = [_snap(total_equity=100_000.0, cash_balance=None, hours_old=1.0)]
        pos = [_pos("AAPL", 500.0, 150.0)]
        prices = [_price("AAPL", 200.0, days_old=0)]

        db = _MockDB(snap_rows=snap, pos_rows=pos, price_rows=prices, tx_rows=[_tx()], rec_rows=[_rec()], agent_rows=[_agent()])
        result = await run_financial_truth_baseline(db, USER_ID)

        assert result["reconciliation"]["reconciliation_status"] == "unavailable"
        assert any(
            "snapshot_invested_value_unavailable" in b
            for b in result["reconciliation"]["blockers"]
        )

    @pytest.mark.asyncio
    async def test_degraded_when_values_differ_beyond_tolerance(self):
        """truth_status=degraded when snapshot and position-derived values differ 3%."""
        snap = [_snap(total_equity=100_000.0, hours_old=1.0)]
        pos = [_pos("AAPL", 500.0, 150.0)]
        prices = [_price("AAPL", 194.0, days_old=0)]  # 500×194 = 97_000 → 3% off

        db = _MockDB(snap_rows=snap, pos_rows=pos, price_rows=prices, tx_rows=[_tx()], rec_rows=[_rec()], agent_rows=[_agent()])
        result = await run_financial_truth_baseline(db, USER_ID)

        assert result["reconciliation"]["reconciliation_status"] == "degraded"
        assert result["verdict"]["truth_status"] == "degraded"

    @pytest.mark.asyncio
    async def test_blocked_when_no_usable_portfolio_value_source(self):
        """truth_status=blocked when snapshot has no value and prices are missing."""
        snap = [_snap(total_equity=None, total_cost=None, hours_old=1.0)]
        pos = [_pos("AAPL", 10.0, 150.0)]
        # No prices → market_value_feasible=False; no equity → snapshot_value=None

        db = _MockDB(snap_rows=snap, pos_rows=pos, price_rows=[], tx_rows=[], rec_rows=[], agent_rows=[])
        result = await run_financial_truth_baseline(db, USER_ID)

        assert result["verdict"]["truth_status"] == "blocked"
        assert "no_usable_portfolio_value_source" in result["verdict"]["blockers"]

    @pytest.mark.asyncio
    async def test_stale_snapshot_warning(self):
        """Snapshot older than SNAPSHOT_STALE_HOURS triggers warning."""
        snap = [_snap(total_equity=100_000.0, hours_old=SNAPSHOT_STALE_HOURS + 2)]

        db = _MockDB(snap_rows=snap, pos_rows=[], price_rows=[], tx_rows=[], rec_rows=[], agent_rows=[])
        result = await run_financial_truth_baseline(db, USER_ID)

        assert result["snapshot_truth"]["snapshot_is_stale"] is True
        assert any("snapshot_stale" in w for w in result["snapshot_truth"]["warnings"])

    @pytest.mark.asyncio
    async def test_duplicate_position_warning(self):
        """Duplicate active positions by ticker trigger a warning."""
        snap = [_snap(hours_old=1.0)]
        pos = [_pos("AAPL"), _pos("AAPL")]

        db = _MockDB(snap_rows=snap, pos_rows=pos, price_rows=[], tx_rows=[], rec_rows=[], agent_rows=[])
        result = await run_financial_truth_baseline(db, USER_ID)

        assert "AAPL" in result["position_derived_truth"]["duplicate_active_tickers"]
        assert any("duplicate" in w for w in result["position_derived_truth"]["warnings"])

    @pytest.mark.asyncio
    async def test_missing_price_warning(self):
        """Ticker with no price history triggers missing price warning."""
        snap = [_snap(hours_old=1.0)]
        pos = [_pos("NOPR", 10.0, 100.0)]  # no price rows

        db = _MockDB(snap_rows=snap, pos_rows=pos, price_rows=[], tx_rows=[], rec_rows=[], agent_rows=[])
        result = await run_financial_truth_baseline(db, USER_ID)

        assert "NOPR" in result["price_truth"]["missing_price_tickers"]
        assert any("missing_price_data" in w for w in result["price_truth"]["warnings"])

    @pytest.mark.asyncio
    async def test_recommendations_marked_unsafe_when_truth_degraded(self):
        """recommendations_trusted is always False; unsafe_sources_to_ignore includes recommendations."""
        snap = [_snap(total_equity=100_000.0, hours_old=1.0)]
        pos = [_pos("AAPL", 500.0, 150.0)]
        prices = [_price("AAPL", 90.0, days_old=0)]  # 500×90=45_000 vs 100_000 → >5% → blocked recon

        db = _MockDB(snap_rows=snap, pos_rows=pos, price_rows=prices, tx_rows=[_tx()], rec_rows=[_rec()], agent_rows=[_agent()])
        result = await run_financial_truth_baseline(db, USER_ID)

        assert result["verdict"]["recommendations_trusted"] is False
        assert any("recommendations" in s for s in result["verdict"]["unsafe_sources_to_ignore"])
        assert result["intelligence_layer"]["recommendations_unsafe_if_truth_degraded"] is True

    @pytest.mark.asyncio
    async def test_no_writes_occur(self):
        """DB mock tracks whether any mutating method is called — none should be."""
        write_calls: list[str] = []

        class _TrackingQuery(_MockQuery):
            def insert(self, *_a, **_k):
                write_calls.append("insert")
                return self

            def update(self, *_a, **_k):
                write_calls.append("update")
                return self

            def delete(self, *_a, **_k):
                write_calls.append("delete")
                return self

            def upsert(self, *_a, **_k):
                write_calls.append("upsert")
                return self

        class _TrackingDB(_MockDB):
            def table(self, name: str):
                q = super().table(name)
                tracking = _TrackingQuery(q._rows if hasattr(q, "_rows") else [])
                return tracking

        db = _TrackingDB(snap_rows=[_snap()], pos_rows=[_pos()], price_rows=[_price("AAPL")], tx_rows=[_tx()], rec_rows=[_rec()], agent_rows=[_agent()])
        await run_financial_truth_baseline(db, USER_ID)
        assert write_calls == [], f"Unexpected write calls: {write_calls}"

    @pytest.mark.asyncio
    async def test_no_provider_or_llm_calls(self):
        """Diagnostic runs end-to-end with no network/provider imports touched."""
        import sys
        modules_before = set(sys.modules.keys())
        db = _MockDB(snap_rows=[_snap()], pos_rows=[_pos()], price_rows=[_price("AAPL")], tx_rows=[_tx()], rec_rows=[_rec()], agent_rows=[_agent()])
        await run_financial_truth_baseline(db, USER_ID)
        new_modules = set(sys.modules.keys()) - modules_before
        provider_modules = [m for m in new_modules if any(p in m for p in ["yfinance", "plaid", "anthropic", "openai", "requests", "httpx"])]
        assert provider_modules == [], f"Provider modules imported: {provider_modules}"

    @pytest.mark.asyncio
    async def test_empty_database_returns_blocked(self):
        """All tables empty → blocked with clear unavailable status."""
        db = _MockDB()
        result = await run_financial_truth_baseline(db, USER_ID)
        assert result["diagnostic_version"] == DIAGNOSTIC_VERSION
        assert result["snapshot_truth"]["status"] == "unavailable"
        assert result["verdict"]["truth_status"] == "blocked"

    @pytest.mark.asyncio
    async def test_intel_snapshot_table_unavailable_handled_gracefully(self):
        """intel_snapshots query failure is handled gracefully (table may not exist)."""
        db = _MockDB(
            snap_rows=[_snap()], pos_rows=[_pos()], price_rows=[_price("AAPL")],
            tx_rows=[_tx()], rec_rows=[_rec()], agent_rows=[_agent()],
            intel_snap_raise=True,  # simulate table not found
        )
        result = await run_financial_truth_baseline(db, USER_ID)
        assert result["intelligence_layer"]["intel_snapshot_table_exists"] is False
        assert result["intelligence_layer"]["latest_intel_snapshot_at"] is None

    @pytest.mark.asyncio
    async def test_verdict_canonical_sources_identified(self):
        """Verdict correctly identifies canonical portfolio value and cost basis sources."""
        snap = [_snap(total_equity=100_000.0, hours_old=1.0)]
        pos = [_pos("AAPL", 500.0, 150.0)]
        prices = [_price("AAPL", 200.0, days_old=0)]

        db = _MockDB(snap_rows=snap, pos_rows=pos, price_rows=prices, tx_rows=[_tx()], rec_rows=[_rec()], agent_rows=[_agent()])
        result = await run_financial_truth_baseline(db, USER_ID)

        assert "portfolio_snapshots" in result["verdict"]["canonical_portfolio_value_source"]
        assert "positions" in result["verdict"]["canonical_cost_basis_source"]

    @pytest.mark.asyncio
    async def test_db_query_failure_handled_gracefully(self):
        """A DB query error for snapshots returns unavailable status without crashing."""

        class _BrokenDB:
            def table(self, name: str):
                return _MockQuery([], raise_on_execute=True)

        result = await run_financial_truth_baseline(_BrokenDB(), USER_ID)
        assert result["snapshot_truth"]["status"] == "unavailable"
        assert "query_failed" in result["snapshot_truth"]["reason"]


# ── Unit tests: business-day staleness helpers ────────────────────────────────

class TestBusinessDayStaleness:
    def test_friday_price_not_stale_on_monday(self):
        # Friday 2026-06-19 → Monday 2026-06-22: only Mon counts → 1 business day, not stale
        friday_price = date(2026, 6, 19)
        monday = date(2026, 6, 22)
        assert _business_days_since(friday_price, monday) == 1
        assert not _is_price_stale(friday_price, monday)

    def test_price_is_stale_after_many_business_days(self):
        # 2026-06-10 (Wed) → 2026-06-22 (Mon): Thu 11, Fri 12, Mon 15, Tue 16, Wed 17, Thu 18, Fri 19, Mon 22 = 8 bdays
        assert _business_days_since(date(2026, 6, 10), date(2026, 6, 22)) == 8
        assert _is_price_stale(date(2026, 6, 10), date(2026, 6, 22))

    def test_same_day_not_stale(self):
        today = date(2026, 6, 22)
        assert _business_days_since(today, today) == 0
        assert not _is_price_stale(today, today)

    def test_weekend_days_not_counted(self):
        # Sat 2026-06-20 → Mon 2026-06-22: only Mon counts = 1
        assert _business_days_since(date(2026, 6, 20), date(2026, 6, 22)) == 1

    def test_price_truth_friday_not_stale_on_monday(self):
        tickers = ["SPY"]
        prices = [{"ticker": "SPY", "price_date": "2026-06-19", "close_price": 500.0}]
        result = _price_truth(prices, tickers, _today=date(2026, 6, 22))
        assert result["stale_price_tickers"] == []
        assert result["tickers_with_recent_price"] == 1


# ── Unit tests: zero-share position filtering ─────────────────────────────────

class TestZeroSharePositionFiltering:
    def test_zero_share_rows_excluded_from_open_positions(self):
        rows = [
            _pos("AAPL", shares=10.0),   # open
            _pos("SOLD", shares=0.0),     # closed — zero shares
        ]
        result = _position_truth(rows, [])
        assert result["open_position_count"] == 1
        assert "AAPL" in result["open_tickers"]
        assert "SOLD" not in result["open_tickers"]

    def test_zero_share_duplicate_does_not_create_duplicate_warning(self):
        # AAPL held once with shares, once with 0 (sold/closed entry)
        rows = [_pos("AAPL", shares=10.0), _pos("AAPL", shares=0.0)]
        result = _position_truth(rows, [])
        assert result["open_position_count"] == 1
        assert result["duplicate_active_tickers"] == []

    def test_sell_category_excluded(self):
        rows = [_pos("AAPL", shares=10.0), _pos("TSLA", shares=5.0, category="SELL")]
        result = _position_truth(rows, [])
        assert result["open_position_count"] == 1
        assert "TSLA" not in result["open_tickers"]

    @pytest.mark.asyncio
    async def test_zero_share_position_excluded_from_open_tickers(self):
        snap = [_snap(hours_old=1.0)]
        pos = [_pos("AAPL", shares=10.0), _pos("CLOSED", shares=0.0)]
        prices = [{"ticker": "AAPL", "price_date": "2026-06-22", "close_price": 200.0}]

        db = _MockDB(snap_rows=snap, pos_rows=pos, price_rows=prices, tx_rows=[_tx()], rec_rows=[_rec()], agent_rows=[_agent()])
        result = await run_financial_truth_baseline(db, USER_ID)

        assert "CLOSED" not in result["price_truth"]["missing_price_tickers"]
        assert "CLOSED" not in result["position_derived_truth"]["open_tickers"]


# ── Unit tests: price_rows_loaded_count / truncation guard ────────────────────

class TestPriceTruncationGuard:
    def test_price_rows_loaded_count_in_output(self):
        tickers = ["AAPL"]
        prices = [{"ticker": "AAPL", "price_date": "2026-06-22", "close_price": 160.0}]
        result = _price_truth(prices, tickers, price_rows_loaded_count=1)
        assert result["price_rows_loaded"] == 1
        assert result["price_query_truncated"] is False

    def test_price_truncation_regression_gt_1000_rows(self):
        # Simulate 1001 rows — under our 10K limit, so not flagged as truncated.
        # The key invariant: even with many rows, latest price per ticker is still found
        # (desc ordering means row 0 is the newest).
        from app.services.financial_truth_baseline_v1 import _PRICE_HISTORY_FETCH_LIMIT
        tickers = ["VTI"]
        rows = [{"ticker": "VTI", "price_date": "2026-06-22", "close_price": 300.0}]
        rows += [{"ticker": "VTI", "price_date": f"2020-01-{i:02d}", "close_price": 100.0} for i in range(1, 1001)]
        result = _price_truth(rows, tickers, _today=date(2026, 6, 22), price_rows_loaded_count=len(rows))
        # Latest price should be found regardless of how many older rows follow
        assert result["tickers_with_recent_price"] == 1
        assert result["stale_price_tickers"] == []
        assert result["price_rows_loaded"] == 1001
        # 1001 < 10_000 limit → not truncated
        assert result["price_query_truncated"] is False

    def test_price_truncation_flag_when_at_limit(self):
        # When loaded rows == limit, flag as truncated (newer rows might have been cut off)
        from app.services.financial_truth_baseline_v1 import _PRICE_HISTORY_FETCH_LIMIT
        tickers = ["VTI"]
        rows = [{"ticker": "VTI", "price_date": "2026-06-22", "close_price": 300.0}]
        result = _price_truth(rows, tickers, _today=date(2026, 6, 22), price_rows_loaded_count=_PRICE_HISTORY_FETCH_LIMIT)
        assert result["price_query_truncated"] is True
        assert any("truncated" in w for w in result["warnings"])

    def test_no_truncation_warning_when_under_limit(self):
        from app.services.financial_truth_baseline_v1 import _PRICE_HISTORY_FETCH_LIMIT
        tickers = ["AAPL"]
        prices = [{"ticker": "AAPL", "price_date": "2026-06-22", "close_price": 160.0}]
        result = _price_truth(prices, tickers, price_rows_loaded_count=500)
        assert result["price_query_truncated"] is False
        assert not any("truncated" in w for w in result["warnings"])


# ── Strict mode (Run Intel preflight): core-read failures never silently
#    become empty results; exact raw open-position/price rows are exposed ──

class TestFinancialTruthBaselineStrict:
    @pytest.mark.asyncio
    async def test_positions_query_failure_raises_not_empty_portfolio(self):
        db = _MockDB(
            snap_rows=[_snap()], pos_rows=[_pos("AAPL")], price_rows=[_price("AAPL")],
            raise_tables=frozenset({"positions"}),
        )
        with pytest.raises(FinancialTruthReadError) as excinfo:
            await run_financial_truth_baseline_strict(db, USER_ID)
        assert "positions" in excinfo.value.failed_tables

    @pytest.mark.asyncio
    async def test_price_history_query_failure_raises_not_empty_portfolio(self):
        db = _MockDB(
            snap_rows=[_snap()], pos_rows=[_pos("AAPL")], price_rows=[_price("AAPL")],
            raise_tables=frozenset({"price_history"}),
        )
        with pytest.raises(FinancialTruthReadError) as excinfo:
            await run_financial_truth_baseline_strict(db, USER_ID)
        assert "price_history" in excinfo.value.failed_tables

    @pytest.mark.asyncio
    async def test_snapshot_query_failure_raises(self):
        db = _MockDB(
            pos_rows=[_pos("AAPL")], price_rows=[_price("AAPL")],
            raise_tables=frozenset({"portfolio_snapshots"}),
        )
        with pytest.raises(FinancialTruthReadError) as excinfo:
            await run_financial_truth_baseline_strict(db, USER_ID)
        assert "portfolio_snapshots" in excinfo.value.failed_tables

    @pytest.mark.asyncio
    async def test_legitimate_empty_portfolio_does_not_raise(self):
        db = _MockDB(snap_rows=[], pos_rows=[], price_rows=[])
        result = await run_financial_truth_baseline_strict(db, USER_ID)
        assert result["_core_read_failed"] is False
        assert result["position_derived_truth"]["open_position_count"] == 0

    @pytest.mark.asyncio
    async def test_strict_result_exposes_exact_open_positions_and_price_rows(self):
        pos = [_pos("AAPL", 500.0, 150.0)]
        prices = [_price("AAPL", 200.0, days_old=0)]
        db = _MockDB(snap_rows=[_snap(100_000.0, 80_000.0)], pos_rows=pos, price_rows=prices)
        result = await run_financial_truth_baseline_strict(db, USER_ID)
        assert result["_open_positions"] == pos
        assert result["_price_rows"] == prices

    @pytest.mark.asyncio
    async def test_public_diagnostic_never_exposes_underscore_fields(self):
        db = _MockDB(snap_rows=[_snap()], pos_rows=[_pos("AAPL")], price_rows=[_price("AAPL")])
        result = await run_financial_truth_baseline(db, USER_ID)
        assert not any(k.startswith("_") for k in result)

    @pytest.mark.asyncio
    async def test_duplicate_active_tickers_reported_not_deduplicated(self):
        pos = [_pos("AAPL", 10.0, 150.0), _pos("AAPL", 5.0, 140.0)]
        db = _MockDB(snap_rows=[_snap()], pos_rows=pos, price_rows=[_price("AAPL")])
        result = await run_financial_truth_baseline_strict(db, USER_ID)
        assert result["position_derived_truth"]["duplicate_active_tickers"] == ["AAPL"]
        # Both raw rows are preserved — never silently deduplicated to one.
        assert len(result["_open_positions"]) == 2
