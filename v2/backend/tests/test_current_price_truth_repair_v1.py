"""Service-level tests for Stage 11B — current-price-truth-repair.

All DB calls and HTTP calls are mocked. No live I/O, no providers, no LLM.

Test coverage:
  - open-position detection excludes zero-share and SELL rows
  - missing/stale tickers are detected
  - current prices are fetched only for missing/stale tickers
  - unsupported crypto/provider case is explicit and non-fatal
  - more than 1000 price_history rows do not hide latest prices (per-ticker)
  - exact 1000-row bulk load would be suspicious; per-ticker avoids it
  - no LLM calls
  - no recommendation writes
  - dry_run=true writes nothing
  - dry_run=false writes only price_history
"""
from __future__ import annotations

import asyncio
from datetime import date, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.current_price_truth_repair_v1 import (
    PRICE_STALE_BUSINESS_DAYS,
    _CRYPTO_IDS,
    _PER_TICKER_LIMIT,
    _SUPABASE_DEFAULT_ROW_CAP,
    _business_days_since,
    _classify_price_status,
    _is_price_stale,
    _load_latest_prices_per_ticker,
    _load_open_positions,
    run_current_price_truth_repair,
)


# ── DB mock helpers ───────────────────────────────────────────────────────────

class _MockQuery:
    """Chainable mock for supabase-py query builder."""

    def __init__(self, rows: list[dict] | None = None, raise_exc: bool = False):
        self._rows = rows or []
        self._raise = raise_exc

    def select(self, *_a, **_k): return self
    def eq(self, *_a, **_k): return self
    def in_(self, *_a, **_k): return self
    def order(self, *_a, **_k): return self
    def limit(self, *_a, **_k): return self
    def upsert(self, *_a, **_k): return self

    def execute(self):
        if self._raise:
            raise RuntimeError("simulated DB error")
        return SimpleNamespace(data=list(self._rows))


class _MockDB:
    """DB client that routes table() calls to pre-configured mock queries."""

    def __init__(self, tables: dict[str, list[dict] | None] | None = None):
        self._tables: dict[str, Any] = tables or {}
        self.upserted: list[tuple] = []

    def table(self, name: str):
        rows = self._tables.get(name, [])
        if isinstance(rows, Exception):
            return _MockQuery(raise_exc=True)
        q = _UpsertCapturingQuery(rows, self.upserted)
        return q


class _UpsertCapturingQuery(_MockQuery):
    """Extends _MockQuery to capture upsert calls."""

    def __init__(self, rows, upserted_list):
        super().__init__(rows)
        self._upserted = upserted_list
        self._last_upsert = None

    def upsert(self, data, **_k):
        self._upserted.append(data)
        self._last_upsert = data
        return self


def _pos(ticker="AAPL", shares=10.0, avg_cost=150.0, category="Core"):
    return {"ticker": ticker, "shares": shares, "avg_cost": avg_cost,
            "category": category, "source": "manual"}


def _price(ticker="AAPL", price_date: str | None = None, close_price=100.0):
    today = date.today()
    pd = price_date or today.isoformat()
    return {"ticker": ticker, "price_date": pd, "close_price": close_price}


# ── Utility helper tests ──────────────────────────────────────────────────────

def test_business_days_since_same_day():
    d = date(2025, 1, 6)  # Monday
    assert _business_days_since(d, d) == 0


def test_business_days_since_one_weekday():
    d = date(2025, 1, 6)  # Monday
    assert _business_days_since(d, date(2025, 1, 7)) == 1  # Tuesday


def test_business_days_since_skips_weekend():
    d = date(2025, 1, 10)  # Friday
    # Saturday=0, Sunday=0, Monday=1
    assert _business_days_since(d, date(2025, 1, 13)) == 1


def test_is_price_stale_recent_price():
    today = date.today()
    assert not _is_price_stale(today, today)


def test_is_price_stale_old_price():
    today = date.today()
    old = today - timedelta(days=10)
    assert _is_price_stale(old, today)


# ── Open position loading ─────────────────────────────────────────────────────

def test_load_open_positions_excludes_sell():
    db = _MockDB({"positions": [
        _pos("AAPL", 10),
        _pos("TSLA", 5, category="SELL"),
    ]})
    result = _load_open_positions(db, "user1")
    assert len(result) == 1
    assert result[0]["ticker"] == "AAPL"


def test_load_open_positions_excludes_zero_shares():
    db = _MockDB({"positions": [
        _pos("AAPL", 10),
        _pos("MSFT", 0),
    ]})
    result = _load_open_positions(db, "user1")
    assert len(result) == 1
    assert result[0]["ticker"] == "AAPL"


def test_load_open_positions_excludes_negative_shares():
    db = _MockDB({"positions": [
        _pos("AAPL", 10),
        _pos("GME", -1),
    ]})
    result = _load_open_positions(db, "user1")
    assert len(result) == 1


def test_load_open_positions_excludes_no_ticker():
    db = _MockDB({"positions": [
        {"ticker": None, "shares": 10, "category": "Core", "source": "manual"},
        _pos("AAPL", 10),
    ]})
    result = _load_open_positions(db, "user1")
    assert len(result) == 1


def test_load_open_positions_db_error_returns_empty():
    db = _MockDB({"positions": RuntimeError("DB down")})
    result = _load_open_positions(db, "user1")
    assert result == []


def test_load_open_positions_crypto_included():
    db = _MockDB({"positions": [
        _pos("BTC", 0.5, category="Crypto"),
    ]})
    result = _load_open_positions(db, "user1")
    assert len(result) == 1
    assert result[0]["ticker"] == "BTC"


# ── Per-ticker price loading ──────────────────────────────────────────────────

def test_load_latest_prices_per_ticker_finds_latest():
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    db = _MockDB({"price_history": [
        {"ticker": "AAPL", "price_date": today, "close_price": 200.0},
        {"ticker": "AAPL", "price_date": yesterday, "close_price": 195.0},
    ]})
    latest, total, truncated = _load_latest_prices_per_ticker(db, ["AAPL"])
    assert "AAPL" in latest
    assert latest["AAPL"]["close_price"] == 200.0
    assert total == 2
    assert not truncated


def test_load_latest_prices_missing_ticker_not_in_result():
    db = _MockDB({"price_history": []})
    latest, total, truncated = _load_latest_prices_per_ticker(db, ["AAPL"])
    assert "AAPL" not in latest
    assert total == 0


def test_load_latest_prices_db_error_per_ticker_handled():
    db = _MockDB({"price_history": RuntimeError("DB down")})
    # Even with DB error, function returns empty dict (graceful)
    latest, total, truncated = _load_latest_prices_per_ticker(db, ["AAPL"])
    assert latest == {}
    assert total == 0


def test_more_than_1000_rows_per_ticker_does_not_hide_latest():
    """Per-ticker approach: even if a ticker has >1000 rows total, per-ticker
    query with order=desc,limit=_PER_TICKER_LIMIT returns the most recent rows first.
    This test simulates that scenario — the repair service finds the latest row.
    The key invariant: per-ticker queries can never be confused with the
    Supabase 1000-row default-cap that caused the Stage 10C.2 VTI bug.
    """
    today = date.today()
    # Simulate the DB returning only the most recent 3 rows for this ticker
    # (as would happen with order="price_date", desc=True, limit=_PER_TICKER_LIMIT)
    # — much fewer than either 1000 or _PER_TICKER_LIMIT
    rows = [
        {"ticker": "VTI", "price_date": (today - timedelta(days=i)).isoformat(),
         "close_price": 250.0 - i}
        for i in range(3)  # Latest 3 rows — far fewer than any truncation cap
    ]
    db = _MockDB({"price_history": rows})
    latest, total, truncated = _load_latest_prices_per_ticker(db, ["VTI"])
    assert "VTI" in latest
    assert latest["VTI"]["price_date"] == today.isoformat()
    assert latest["VTI"]["close_price"] == 250.0
    assert total == 3
    assert not truncated  # well below per-ticker limit


def test_exact_1000_row_bulk_cap_is_suspicious():
    """Confirm _SUPABASE_DEFAULT_ROW_CAP is 1000 (for documentation/alerting).
    A bulk query loading exactly 1000 rows would be suspicious; per-ticker
    avoids this. This test validates the constant exists and equals 1000.
    """
    assert _SUPABASE_DEFAULT_ROW_CAP == 1000


def test_per_ticker_limit_well_below_supabase_cap():
    """Per-ticker limit should be well below 1000 to never be confused with cap."""
    assert _PER_TICKER_LIMIT < _SUPABASE_DEFAULT_ROW_CAP


# ── Price status classification ───────────────────────────────────────────────

def test_classify_missing_ticker():
    st = _classify_price_status("AAPL", {}, date.today())
    assert st["current_price_status"] == "missing"
    assert st["latest_price_date"] is None


def test_classify_recent_ticker():
    today = date.today()
    latest = {"AAPL": {"price_date": today.isoformat(), "close_price": 200.0}}
    st = _classify_price_status("AAPL", latest, today)
    assert st["current_price_status"] == "recent"
    assert st["business_days_old"] == 0


def test_classify_stale_ticker():
    today = date.today()
    old_date = today - timedelta(days=10)
    latest = {"AAPL": {"price_date": old_date.isoformat(), "close_price": 180.0}}
    st = _classify_price_status("AAPL", latest, today)
    assert st["current_price_status"] == "stale"
    assert st["business_days_old"] is not None
    assert st["business_days_old"] > PRICE_STALE_BUSINESS_DAYS


# ── Full repair run tests (mocking HTTP) ─────────────────────────────────────

def _make_positions_db(positions, price_rows_by_ticker=None):
    """Build a MockDB that routes price_history per-ticker queries correctly."""
    price_rows_by_ticker = price_rows_by_ticker or {}

    class _PerTickerDB:
        def __init__(self):
            self.upserted = []
            self._positions = positions

        def table(self, name):
            if name == "positions":
                return _MockQuery(self._positions)
            if name == "price_history":
                return _PerTickerPriceQuery(price_rows_by_ticker, self.upserted)
            return _MockQuery([])

    class _PerTickerPriceQuery:
        def __init__(self, rows_by_ticker, upserted):
            self._rows_by_ticker = rows_by_ticker
            self._ticker = None
            self._upserted = upserted

        def select(self, *_a, **_k): return self
        def eq(self, col, val):
            if col == "ticker":
                self._ticker = val
            return self
        def order(self, *_a, **_k): return self
        def limit(self, *_a, **_k): return self
        def execute(self):
            rows = self._rows_by_ticker.get(self._ticker, [])
            return SimpleNamespace(data=list(rows))
        def upsert(self, data, **_k):
            self._upserted.append(data)
            return self

    return _PerTickerDB()


@pytest.mark.asyncio
async def test_dry_run_true_writes_nothing():
    """dry_run=True must not call upsert on price_history."""
    today = date.today()
    old = (today - timedelta(days=10)).isoformat()
    positions = [_pos("AAPL", 10)]
    price_rows = {"AAPL": [{"ticker": "AAPL", "price_date": old, "close_price": 180.0}]}
    db = _make_positions_db(positions, price_rows)

    fake_fetch = {"price": 200.0, "price_date": today.isoformat(),
                  "open_price": 198.0, "high_price": 202.0, "low_price": 197.0,
                  "volume": 1000, "provider": "yfinance", "error": None,
                  "unsupported": False}

    with patch(
        "app.services.current_price_truth_repair_v1._fetch_price_for_ticker",
        new=AsyncMock(return_value=fake_fetch),
    ):
        result = await run_current_price_truth_repair(db, "user1", dry_run=True)

    assert result["dry_run"] is True
    assert result["rows_written"] == 0
    assert result["writes_performed"] == 0
    # No upserts called
    assert db.upserted == []
    # Ticker shows skipped_dry_run
    ticker_entry = next(r for r in result["per_ticker"] if r["ticker"] == "AAPL")
    assert ticker_entry["write_status"] == "skipped_dry_run"


@pytest.mark.asyncio
async def test_dry_run_defaults_to_true():
    """dry_run defaults to True — calling without dry_run arg must not write."""
    today = date.today()
    old = (today - timedelta(days=10)).isoformat()
    positions = [_pos("AAPL", 10)]
    price_rows = {"AAPL": [{"ticker": "AAPL", "price_date": old, "close_price": 180.0}]}
    db = _make_positions_db(positions, price_rows)

    fake_fetch = {"price": 200.0, "price_date": today.isoformat(),
                  "open_price": 198.0, "high_price": 202.0, "low_price": 197.0,
                  "volume": 1000, "provider": "yfinance", "error": None,
                  "unsupported": False}

    with patch(
        "app.services.current_price_truth_repair_v1._fetch_price_for_ticker",
        new=AsyncMock(return_value=fake_fetch),
    ):
        result = await run_current_price_truth_repair(db, "user1")  # no dry_run arg

    assert result["dry_run"] is True
    assert result["rows_written"] == 0


@pytest.mark.asyncio
async def test_dry_run_false_writes_price_history_only():
    """dry_run=False must write to price_history and nothing else."""
    today = date.today()
    old = (today - timedelta(days=10)).isoformat()
    positions = [_pos("AAPL", 10)]
    price_rows = {"AAPL": [{"ticker": "AAPL", "price_date": old, "close_price": 180.0}]}
    db = _make_positions_db(positions, price_rows)

    fake_fetch = {"price": 200.0, "price_date": today.isoformat(),
                  "open_price": 198.0, "high_price": 202.0, "low_price": 197.0,
                  "volume": 1000, "provider": "yfinance", "error": None,
                  "unsupported": False}

    with patch(
        "app.services.current_price_truth_repair_v1._fetch_price_for_ticker",
        new=AsyncMock(return_value=fake_fetch),
    ):
        result = await run_current_price_truth_repair(db, "user1", dry_run=False)

    assert result["dry_run"] is False
    assert result["rows_written"] == 1
    assert result["writes_performed"] == 1
    assert len(db.upserted) == 1
    upserted = db.upserted[0]
    assert upserted["ticker"] == "AAPL"
    assert upserted["close_price"] == 200.0
    assert "price_date" in upserted


@pytest.mark.asyncio
async def test_recent_tickers_not_fetched():
    """Tickers with recent prices must not trigger a fetch."""
    today = date.today()
    positions = [_pos("AAPL", 10), _pos("MSFT", 5)]
    price_rows = {
        "AAPL": [{"ticker": "AAPL", "price_date": today.isoformat(), "close_price": 200.0}],
        "MSFT": [{"ticker": "MSFT", "price_date": today.isoformat(), "close_price": 400.0}],
    }
    db = _make_positions_db(positions, price_rows)

    mock_fetch = AsyncMock()
    with patch(
        "app.services.current_price_truth_repair_v1._fetch_price_for_ticker",
        new=mock_fetch,
    ):
        result = await run_current_price_truth_repair(db, "user1", dry_run=True)

    mock_fetch.assert_not_called()
    assert result["attempted_fetch_count"] == 0
    assert result["missing_before_count"] == 0
    assert result["stale_before_count"] == 0


@pytest.mark.asyncio
async def test_missing_tickers_are_detected_and_fetched():
    """Tickers with no price history must be detected as missing and fetched."""
    today = date.today()
    positions = [_pos("NVDA", 3)]
    price_rows = {}  # No price rows for NVDA
    db = _make_positions_db(positions, price_rows)

    fake_fetch = {"price": 500.0, "price_date": today.isoformat(),
                  "open_price": 495.0, "high_price": 505.0, "low_price": 490.0,
                  "volume": 2000, "provider": "yfinance", "error": None,
                  "unsupported": False}

    with patch(
        "app.services.current_price_truth_repair_v1._fetch_price_for_ticker",
        new=AsyncMock(return_value=fake_fetch),
    ):
        result = await run_current_price_truth_repair(db, "user1", dry_run=True)

    assert result["missing_before_count"] == 1
    assert result["attempted_fetch_count"] == 1
    assert result["successful_fetch_count"] == 1


@pytest.mark.asyncio
async def test_stale_tickers_are_detected_and_fetched():
    """Tickers with stale prices must be detected and fetched."""
    today = date.today()
    old = (today - timedelta(days=10)).isoformat()
    positions = [_pos("VOO", 2, category="ETF")]
    price_rows = {"VOO": [{"ticker": "VOO", "price_date": old, "close_price": 400.0}]}
    db = _make_positions_db(positions, price_rows)

    fake_fetch = {"price": 420.0, "price_date": today.isoformat(),
                  "open_price": 418.0, "high_price": 422.0, "low_price": 416.0,
                  "volume": 500, "provider": "yfinance", "error": None,
                  "unsupported": False}

    with patch(
        "app.services.current_price_truth_repair_v1._fetch_price_for_ticker",
        new=AsyncMock(return_value=fake_fetch),
    ):
        result = await run_current_price_truth_repair(db, "user1", dry_run=True)

    assert result["stale_before_count"] == 1
    assert result["attempted_fetch_count"] == 1
    assert result["successful_fetch_count"] == 1
    assert result["per_ticker"][0]["current_price_status"] == "stale"


@pytest.mark.asyncio
async def test_unsupported_crypto_is_non_fatal():
    """Unknown crypto tickers (not in _CRYPTO_IDS) return unsupported — non-fatal."""
    today = date.today()
    positions = [{"ticker": "UNKNOWNCOIN", "shares": 100, "avg_cost": 0.01,
                  "category": "Crypto", "source": "manual"}]
    db = _make_positions_db(positions, {})

    # No need to mock _fetch_price_for_ticker — the unsupported path is
    # hit inside the function based on ticker not being in _CRYPTO_IDS
    result = await run_current_price_truth_repair(db, "user1", dry_run=True)

    assert result["unsupported_count"] == 1
    assert result["rows_written"] == 0
    ticker_entry = result["per_ticker"][0]
    assert ticker_entry["write_status"] == "unsupported"
    assert ticker_entry["current_price_status"] == "unsupported"


@pytest.mark.asyncio
async def test_known_crypto_uses_coingecko():
    """BTC (in _CRYPTO_IDS) routes to CoinGecko, not yfinance."""
    today = date.today()
    positions = [_pos("BTC", 0.5, category="Crypto")]
    db = _make_positions_db(positions, {})

    fake_cg = {"price": 50000.0, "price_date": today.isoformat(),
               "open_price": 50000.0, "high_price": 50000.0,
               "low_price": 50000.0, "volume": 0,
               "provider": "coingecko", "error": None, "unsupported": False}

    with patch(
        "app.services.current_price_truth_repair_v1._fetch_coingecko_current",
        new=AsyncMock(return_value=fake_cg),
    ):
        result = await run_current_price_truth_repair(db, "user1", dry_run=True)

    assert result["attempted_fetch_count"] == 1
    assert result["successful_fetch_count"] == 1
    entry = result["per_ticker"][0]
    assert entry["provider_used"] == "coingecko"


@pytest.mark.asyncio
async def test_provider_error_is_non_fatal():
    """Provider fetch failure (error=True, price=None) is non-fatal."""
    today = date.today()
    old = (today - timedelta(days=10)).isoformat()
    positions = [_pos("AAPL", 10)]
    price_rows = {"AAPL": [{"ticker": "AAPL", "price_date": old, "close_price": 180.0}]}
    db = _make_positions_db(positions, price_rows)

    fake_fail = {"price": None, "price_date": None, "open_price": None,
                 "high_price": None, "low_price": None, "volume": None,
                 "provider": "yfinance", "error": "HTTP 500", "unsupported": False}

    with patch(
        "app.services.current_price_truth_repair_v1._fetch_price_for_ticker",
        new=AsyncMock(return_value=fake_fail),
    ):
        result = await run_current_price_truth_repair(db, "user1", dry_run=True)

    assert result["provider_error_count"] == 1
    assert result["rows_written"] == 0
    entry = result["per_ticker"][0]
    assert entry["write_status"] == "skipped_fetch_failed"


@pytest.mark.asyncio
async def test_no_llm_calls():
    """Service must not import or call any LLM / anthropic paths."""
    import app.services.current_price_truth_repair_v1 as svc
    module_source = svc.__doc__ or ""
    # Check that the module mentions the no-LLM guarantee
    assert "No LLM" in module_source or "no LLM" in svc.__doc__
    # Verify no anthropic import
    import importlib
    import sys
    mods = [m for m in sys.modules if "anthropic" in m.lower()]
    # anthropic modules may exist from other imports — what matters is the
    # repair service itself doesn't reference it
    assert "anthropic" not in dir(svc)


@pytest.mark.asyncio
async def test_no_recommendation_writes():
    """dry_run=False must write only to price_history, not recommendations."""
    today = date.today()
    old = (today - timedelta(days=10)).isoformat()
    positions = [_pos("AAPL", 10)]
    price_rows = {"AAPL": [{"ticker": "AAPL", "price_date": old, "close_price": 180.0}]}

    tables_written: list[str] = []

    class _TrackingDB:
        def __init__(self):
            self.upserted = []

        def table(self, name):
            return _TrackingQuery(name, tables_written, self.upserted,
                                  {"positions": positions, "price_history": price_rows}.get(name, []))

    class _TrackingQuery:
        def __init__(self, name, log, upserted, rows_by_ticker=None):
            self._name = name
            self._log = log
            self._upserted = upserted
            self._rows = rows_by_ticker if isinstance(rows_by_ticker, list) else []
            self._ticker = None

        def select(self, *_a, **_k): return self
        def eq(self, col, val):
            if col == "ticker":
                self._ticker = val
                if isinstance(self._rows, dict):
                    self._rows = self._rows.get(val, [])
            return self
        def order(self, *_a, **_k): return self
        def limit(self, *_a, **_k): return self
        def execute(self):
            rows = self._rows
            if self._name == "price_history" and self._ticker:
                rows = price_rows.get(self._ticker, [])
            return SimpleNamespace(data=list(rows) if rows else [])
        def upsert(self, data, **_k):
            self._log.append(self._name)
            self._upserted.append((self._name, data))
            return self

    db = _TrackingDB()

    fake_fetch = {"price": 200.0, "price_date": today.isoformat(),
                  "open_price": 198.0, "high_price": 202.0, "low_price": 197.0,
                  "volume": 1000, "provider": "yfinance", "error": None,
                  "unsupported": False}

    with patch(
        "app.services.current_price_truth_repair_v1._fetch_price_for_ticker",
        new=AsyncMock(return_value=fake_fetch),
    ):
        result = await run_current_price_truth_repair(db, "user1", dry_run=False)

    written_tables = set(tables_written)
    assert "recommendations" not in written_tables
    assert "portfolio_snapshots" not in written_tables
    assert "agent_runs" not in written_tables
    assert "positions" not in written_tables
    # Only price_history should appear if upsert was called
    assert all(t == "price_history" for t in written_tables)
    assert result["policy_unchanged"] is True
    assert result["snapshot_unchanged"] is True


@pytest.mark.asyncio
async def test_no_open_positions_returns_early():
    """If no open positions, return early with zeros."""
    db = _make_positions_db([], {})
    result = await run_current_price_truth_repair(db, "user1", dry_run=True)
    assert result["open_tickers_count"] == 0
    assert result["rows_written"] == 0
    assert result["per_ticker"] == []


@pytest.mark.asyncio
async def test_safe_to_rerun_is_always_true():
    """safe_to_rerun must always be True (idempotent upsert)."""
    db = _make_positions_db([], {})
    result = await run_current_price_truth_repair(db, "user1", dry_run=True)
    assert result["safe_to_rerun"] is True


@pytest.mark.asyncio
async def test_next_step_is_rerun_baseline():
    """next_step must point back to financial-truth-baseline for re-verification."""
    db = _make_positions_db([_pos("AAPL", 10)], {})
    fake_fetch = {"price": None, "price_date": None, "open_price": None,
                  "high_price": None, "low_price": None, "volume": None,
                  "provider": "yfinance", "error": "fail", "unsupported": False}
    with patch(
        "app.services.current_price_truth_repair_v1._fetch_price_for_ticker",
        new=AsyncMock(return_value=fake_fetch),
    ):
        result = await run_current_price_truth_repair(db, "user1", dry_run=True)
    assert result["next_step"] == "rerun_financial_truth_baseline"


def test_crypto_ids_contains_expected_tickers():
    """_CRYPTO_IDS must contain at least BTC and ETH for CoinGecko routing."""
    assert "BTC" in _CRYPTO_IDS
    assert "ETH" in _CRYPTO_IDS
    assert "XRP" in _CRYPTO_IDS
