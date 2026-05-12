"""Tests — Stage 2.5B: portfolio snapshot market-value enrichment.

Acceptance gates:
  A. _enrich_position_entry: valid fresh price → market_value_usd written
  B. _enrich_position_entry: None price → market_value_usd absent
  C. _enrich_position_entry: invalid price (mid=0) → market_value_usd absent
  D. _enrich_position_entry: stale price → market_value_usd absent
  E. _enrich_position_entry: shares=0 → market_value_usd absent
  F. _enrich_position_entry: cost basis never stored as market_value_usd
  G. _enrich_position_entry: all original fields preserved regardless
  H. create_snapshot: valid prices → positions_data entries include market_value_usd
  I. create_snapshot: missing price → positions_data entry omits market_value_usd
  J. create_snapshot: price fetch failure → falls back, no market_value_usd (no crash)
  K. adapter sees enriched snapshot as position-certified (sizing_values_ready True)
  L. adapter fails safe for legacy snapshot missing market_value_usd (sizing_values_ready False)
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from app.services.portfolio_service import PortfolioService
from app.services.price_engine import PriceResult
from app.services.deploy.deploy_sizing_source_adapter_v1 import (
    STALE_THRESHOLD_HOURS,
    build_sizing_bundle_from_persisted_data,
)
from app.services.deploy.deploy_sizing_contracts import DeploySizingTrustStatus

# ── Helpers ───────────────────────────────────────────────────────────────────

_UID = UUID("00000000-0000-0000-0000-000000000001")
_CERTIFIED_AT = "2026-05-12T10:00:00+00:00"
_CERTIFIED_POLICY = {"minimum_trade_usd": 1.0, "rounding_policy": "WHOLE_DOLLAR"}


def _fresh_price(ticker: str, mid: float) -> PriceResult:
    return PriceResult(
        ticker=ticker,
        mid_price=mid,
        bid=None,
        ask=None,
        last_trade=mid,
        source="yfinance",
        timestamp=time.time(),
    )


def _stale_price(ticker: str, mid: float) -> PriceResult:
    return PriceResult(
        ticker=ticker,
        mid_price=mid,
        bid=None,
        ask=None,
        last_trade=mid,
        source="cache(yfinance)",  # is_stale=True because source starts with "cache"
        timestamp=time.time(),
    )


def _invalid_price(ticker: str) -> PriceResult:
    return PriceResult(
        ticker=ticker,
        mid_price=0.0,
        bid=None,
        ask=None,
        last_trade=0.0,
        source="yfinance",
        timestamp=time.time(),
        error="no data",
    )


def _make_mock_db_for_snapshot(positions_data: list) -> tuple[MagicMock, dict]:
    """Build a mock DB client that captures snapshot inserts."""
    db = MagicMock()
    captured: dict = {}

    # positions table mock
    pos_chain = MagicMock()
    pos_result = MagicMock()
    pos_result.data = positions_data
    pos_chain.select.return_value = pos_chain
    pos_chain.eq.return_value = pos_chain
    pos_chain.execute.return_value = pos_result

    # portfolio_snapshots insert capture
    ps_chain = MagicMock()

    def _capture_insert(data: dict) -> MagicMock:
        captured["snapshot"] = data
        ins = MagicMock()
        ins.execute.return_value = MagicMock(data=[{"id": "snap-test-001", **data}])
        return ins

    ps_chain.insert.side_effect = _capture_insert

    # All other tables (users, plaid_sync_log, price_history) used by get_summary()
    def _other_table() -> MagicMock:
        m = MagicMock()
        m.select.return_value = m
        m.eq.return_value = m
        m.order.return_value = m
        m.limit.return_value = m
        m.single.return_value = m
        m.in_.return_value = m
        empty = MagicMock()
        empty.data = []
        m.execute.return_value = empty
        return m

    def _table_router(name: str) -> MagicMock:
        if name == "positions":
            return pos_chain
        if name == "portfolio_snapshots":
            return ps_chain
        return _other_table()

    db.table.side_effect = _table_router
    return db, captured


def _make_service_with_mocks(positions_data: list, price_results: dict) -> tuple[PortfolioService, dict]:
    """Build a PortfolioService with mocked DB and price service for snapshot tests."""
    service = PortfolioService.__new__(PortfolioService)
    service.user_id = _UID

    mock_price_svc = MagicMock()
    mock_price_svc.fetch_prices = AsyncMock(return_value=price_results)
    service._price_service = mock_price_svc

    db, captured = _make_mock_db_for_snapshot(positions_data)
    service.client = db

    return service, captured


def _run(coro) -> object:
    return asyncio.run(coro)


# ── Gates A–G: _enrich_position_entry unit tests ─────────────────────────────

class TestEnrichPositionEntryUnit:

    def test_gate_a_valid_fresh_price_adds_market_value_usd(self):
        pos = {"ticker": "AAPL", "shares": 10.0, "avg_cost": 150.0}
        pr = _fresh_price("AAPL", 200.0)
        enriched = PortfolioService._enrich_position_entry(pos, pr, _CERTIFIED_AT)
        assert "market_value_usd" in enriched
        assert enriched["market_value_usd"] == pytest.approx(2000.0)

    def test_gate_a_valid_fresh_price_adds_market_price_usd(self):
        pos = {"ticker": "AAPL", "shares": 10.0, "avg_cost": 150.0}
        pr = _fresh_price("AAPL", 200.0)
        enriched = PortfolioService._enrich_position_entry(pos, pr, _CERTIFIED_AT)
        assert "market_price_usd" in enriched
        assert enriched["market_price_usd"] == pytest.approx(200.0)

    def test_gate_a_valid_fresh_price_adds_source_and_certified_at(self):
        pos = {"ticker": "AAPL", "shares": 10.0, "avg_cost": 150.0}
        pr = _fresh_price("AAPL", 200.0)
        enriched = PortfolioService._enrich_position_entry(pos, pr, _CERTIFIED_AT)
        assert "market_value_source" in enriched
        assert "market_value_certified_at" in enriched
        assert enriched["market_value_certified_at"] == _CERTIFIED_AT

    def test_gate_b_none_price_omits_market_value_usd(self):
        pos = {"ticker": "AAPL", "shares": 10.0, "avg_cost": 150.0}
        enriched = PortfolioService._enrich_position_entry(pos, None, _CERTIFIED_AT)
        assert "market_value_usd" not in enriched
        assert "market_price_usd" not in enriched

    def test_gate_c_invalid_price_omits_market_value_usd(self):
        pos = {"ticker": "AAPL", "shares": 10.0, "avg_cost": 150.0}
        pr = _invalid_price("AAPL")
        assert not pr.is_valid
        enriched = PortfolioService._enrich_position_entry(pos, pr, _CERTIFIED_AT)
        assert "market_value_usd" not in enriched

    def test_gate_d_stale_price_omits_market_value_usd(self):
        pos = {"ticker": "AAPL", "shares": 10.0, "avg_cost": 150.0}
        pr = _stale_price("AAPL", 200.0)
        assert pr.is_valid
        assert pr.is_stale
        enriched = PortfolioService._enrich_position_entry(pos, pr, _CERTIFIED_AT)
        assert "market_value_usd" not in enriched

    def test_gate_e_zero_shares_omits_market_value_usd(self):
        pos = {"ticker": "AAPL", "shares": 0.0, "avg_cost": 150.0}
        pr = _fresh_price("AAPL", 200.0)
        enriched = PortfolioService._enrich_position_entry(pos, pr, _CERTIFIED_AT)
        assert "market_value_usd" not in enriched

    def test_gate_f_market_value_differs_from_cost_basis(self):
        """market_value_usd must be price-based, never cost-basis (shares * avg_cost)."""
        pos = {"ticker": "NVDA", "shares": 5.0, "avg_cost": 600.0}
        pr = _fresh_price("NVDA", 800.0)
        enriched = PortfolioService._enrich_position_entry(pos, pr, _CERTIFIED_AT)
        cost_basis = 5.0 * 600.0  # 3000.0
        assert enriched["market_value_usd"] == pytest.approx(5.0 * 800.0)  # 4000.0
        assert enriched["market_value_usd"] != pytest.approx(cost_basis)

    def test_gate_f_no_market_value_usd_when_price_missing(self):
        """When price is absent, market_value_usd must not appear (not set to cost basis)."""
        pos = {"ticker": "NVDA", "shares": 5.0, "avg_cost": 600.0}
        enriched = PortfolioService._enrich_position_entry(pos, None, _CERTIFIED_AT)
        assert "market_value_usd" not in enriched

    def test_gate_g_original_fields_preserved_with_valid_price(self):
        pos = {"ticker": "AAPL", "shares": 10.0, "avg_cost": 150.0, "category": "Core", "id": 42}
        pr = _fresh_price("AAPL", 200.0)
        enriched = PortfolioService._enrich_position_entry(pos, pr, _CERTIFIED_AT)
        assert enriched["ticker"] == "AAPL"
        assert enriched["shares"] == 10.0
        assert enriched["avg_cost"] == 150.0
        assert enriched["category"] == "Core"
        assert enriched["id"] == 42

    def test_gate_g_original_fields_preserved_without_price(self):
        pos = {"ticker": "AAPL", "shares": 10.0, "avg_cost": 150.0, "category": "ETF"}
        enriched = PortfolioService._enrich_position_entry(pos, None, _CERTIFIED_AT)
        assert enriched["ticker"] == "AAPL"
        assert enriched["avg_cost"] == 150.0
        assert enriched["category"] == "ETF"

    def test_gate_g_input_pos_not_mutated(self):
        pos = {"ticker": "AAPL", "shares": 10.0, "avg_cost": 150.0}
        original_keys = set(pos.keys())
        pr = _fresh_price("AAPL", 200.0)
        PortfolioService._enrich_position_entry(pos, pr, _CERTIFIED_AT)
        assert set(pos.keys()) == original_keys


# ── Gates H–J: create_snapshot enrichment integration tests ──────────────────

class TestCreateSnapshotEnrichment:

    def test_gate_h_valid_prices_write_market_value_usd(self):
        """create_snapshot stores market_value_usd when valid prices are available."""
        positions = [{"ticker": "AAPL", "shares": 10.0, "avg_cost": 150.0, "user_id": str(_UID)}]
        price_results = {"AAPL": _fresh_price("AAPL", 200.0)}

        service, captured = _make_service_with_mocks(positions, price_results)

        # Patch get_summary to avoid full DB setup for summary computation
        from app.models.portfolio import PortfolioSummary
        mock_summary = PortfolioSummary(
            total_equity=2500.0, total_cost=1500.0, total_pnl=1000.0, total_pnl_pct=66.67,
            cash_balance=500.0, day_change=0.0, day_change_pct=0.0,
            stocks_value=2000.0, etfs_value=0.0, crypto_value=0.0,
            positions_count=1, prices_fresh=1, prices_stale=0,
        )
        with patch.object(service, "get_summary", AsyncMock(return_value=mock_summary)):
            _run(service.create_snapshot())

        stored = captured["snapshot"]["positions_data"]
        assert len(stored) == 1
        assert "market_value_usd" in stored[0]
        assert stored[0]["market_value_usd"] == pytest.approx(2000.0)
        assert stored[0]["market_price_usd"] == pytest.approx(200.0)

    def test_gate_h_multiple_positions_each_enriched(self):
        """All positions with valid prices are independently enriched."""
        positions = [
            {"ticker": "AAPL", "shares": 10.0, "avg_cost": 150.0},
            {"ticker": "NVDA", "shares": 5.0, "avg_cost": 600.0},
        ]
        price_results = {
            "AAPL": _fresh_price("AAPL", 200.0),
            "NVDA": _fresh_price("NVDA", 800.0),
        }

        service, captured = _make_service_with_mocks(positions, price_results)

        from app.models.portfolio import PortfolioSummary
        mock_summary = PortfolioSummary(
            total_equity=6000.0, total_cost=4500.0, total_pnl=1500.0, total_pnl_pct=33.3,
            cash_balance=0.0, day_change=0.0, day_change_pct=0.0,
            stocks_value=6000.0, etfs_value=0.0, crypto_value=0.0,
            positions_count=2, prices_fresh=2, prices_stale=0,
        )
        with patch.object(service, "get_summary", AsyncMock(return_value=mock_summary)):
            _run(service.create_snapshot())

        stored = captured["snapshot"]["positions_data"]
        stored_map = {p["ticker"]: p for p in stored}
        assert stored_map["AAPL"]["market_value_usd"] == pytest.approx(2000.0)
        assert stored_map["NVDA"]["market_value_usd"] == pytest.approx(4000.0)

    def test_gate_i_missing_price_omits_market_value_usd(self):
        """create_snapshot omits market_value_usd when no price is available."""
        positions = [{"ticker": "AAPL", "shares": 10.0, "avg_cost": 150.0}]
        price_results = {}  # no price for AAPL

        service, captured = _make_service_with_mocks(positions, price_results)

        from app.models.portfolio import PortfolioSummary
        mock_summary = PortfolioSummary(
            total_equity=1500.0, total_cost=1500.0, total_pnl=0.0, total_pnl_pct=0.0,
            cash_balance=0.0, day_change=0.0, day_change_pct=0.0,
            stocks_value=1500.0, etfs_value=0.0, crypto_value=0.0,
            positions_count=1, prices_fresh=0, prices_stale=1,
        )
        with patch.object(service, "get_summary", AsyncMock(return_value=mock_summary)):
            _run(service.create_snapshot())

        stored = captured["snapshot"]["positions_data"]
        assert "market_value_usd" not in stored[0]
        assert "market_price_usd" not in stored[0]

    def test_gate_i_stale_price_omits_market_value_usd(self):
        """create_snapshot omits market_value_usd for stale prices."""
        positions = [{"ticker": "AAPL", "shares": 10.0, "avg_cost": 150.0}]
        price_results = {"AAPL": _stale_price("AAPL", 200.0)}

        service, captured = _make_service_with_mocks(positions, price_results)

        from app.models.portfolio import PortfolioSummary
        mock_summary = PortfolioSummary(
            total_equity=1500.0, total_cost=1500.0, total_pnl=0.0, total_pnl_pct=0.0,
            cash_balance=0.0, day_change=0.0, day_change_pct=0.0,
            stocks_value=1500.0, etfs_value=0.0, crypto_value=0.0,
            positions_count=1, prices_fresh=0, prices_stale=1,
        )
        with patch.object(service, "get_summary", AsyncMock(return_value=mock_summary)):
            _run(service.create_snapshot())

        stored = captured["snapshot"]["positions_data"]
        assert "market_value_usd" not in stored[0]

    def test_gate_j_price_fetch_failure_falls_back_gracefully(self):
        """If price fetch raises, snapshot is stored without market values — no crash."""
        positions = [{"ticker": "AAPL", "shares": 10.0, "avg_cost": 150.0}]

        service = PortfolioService.__new__(PortfolioService)
        service.user_id = _UID

        failing_price_svc = MagicMock()
        failing_price_svc.fetch_prices = AsyncMock(side_effect=RuntimeError("network error"))
        service._price_service = failing_price_svc

        db, captured = _make_mock_db_for_snapshot(positions)
        service.client = db

        from app.models.portfolio import PortfolioSummary
        mock_summary = PortfolioSummary(
            total_equity=1500.0, total_cost=1500.0, total_pnl=0.0, total_pnl_pct=0.0,
            cash_balance=0.0, day_change=0.0, day_change_pct=0.0,
            stocks_value=1500.0, etfs_value=0.0, crypto_value=0.0,
            positions_count=1, prices_fresh=0, prices_stale=1,
        )
        with patch.object(service, "get_summary", AsyncMock(return_value=mock_summary)):
            _run(service.create_snapshot())  # must not raise

        stored = captured["snapshot"]["positions_data"]
        assert "market_value_usd" not in stored[0]

    def test_gate_j_summary_fields_unaffected_by_enrichment(self):
        """Snapshot summary fields (total_equity, etc.) are not changed by enrichment."""
        positions = [{"ticker": "AAPL", "shares": 10.0, "avg_cost": 150.0}]
        price_results = {"AAPL": _fresh_price("AAPL", 200.0)}

        service, captured = _make_service_with_mocks(positions, price_results)

        from app.models.portfolio import PortfolioSummary
        mock_summary = PortfolioSummary(
            total_equity=2500.0, total_cost=1500.0, total_pnl=1000.0, total_pnl_pct=66.67,
            cash_balance=500.0, day_change=0.0, day_change_pct=0.0,
            stocks_value=2000.0, etfs_value=0.0, crypto_value=0.0,
            positions_count=1, prices_fresh=1, prices_stale=0,
        )
        with patch.object(service, "get_summary", AsyncMock(return_value=mock_summary)):
            _run(service.create_snapshot())

        snap = captured["snapshot"]
        assert snap["total_equity"] == pytest.approx(2500.0)
        assert snap["total_cost"] == pytest.approx(1500.0)
        assert snap["cash_balance"] == pytest.approx(500.0)


# ── Gates K–L: adapter integration with enriched vs legacy snapshots ──────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stale_iso() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=STALE_THRESHOLD_HOURS + 1)).isoformat()


def _make_adapter_db(snapshot_rows: list, target_rows: list | None = None) -> MagicMock:
    db = MagicMock()

    ps_chain = MagicMock()
    ps_chain.select.return_value = ps_chain
    ps_chain.eq.return_value = ps_chain
    ps_chain.order.return_value = ps_chain
    ps_chain.limit.return_value = ps_chain
    ps_result = MagicMock()
    ps_result.data = snapshot_rows
    ps_chain.execute.return_value = ps_result

    ta_chain = MagicMock()
    ta_chain.select.return_value = ta_chain
    ta_chain.eq.return_value = ta_chain
    ta_result = MagicMock()
    ta_result.data = target_rows or []
    ta_chain.execute.return_value = ta_result

    def _router(name: str) -> MagicMock:
        if name == "portfolio_snapshots":
            return ps_chain
        if name == "target_allocations":
            return ta_chain
        return MagicMock()

    db.table.side_effect = _router
    return db


class TestAdapterWithEnrichedSnapshot:

    def test_gate_k_enriched_snapshot_positions_are_certified(self):
        """Adapter certifies positions when fresh snapshot has market_value_usd."""
        snapshot_row = {
            "id": "snap-enriched-001",
            "snapshot_at": _now_iso(),
            "total_equity": 100_000.0,
            "cash_balance": 5_000.0,
            "positions_data": [
                {
                    "ticker": "AAPL",
                    "shares": 10.0,
                    "avg_cost": 150.0,
                    "market_price_usd": 200.0,
                    "market_value_usd": 2000.0,
                    "market_value_source": "yfinance",
                    "market_value_certified_at": _now_iso(),
                },
                {
                    "ticker": "NVDA",
                    "shares": 5.0,
                    "avg_cost": 600.0,
                    "market_price_usd": 800.0,
                    "market_value_usd": 4000.0,
                    "market_value_source": "yfinance",
                    "market_value_certified_at": _now_iso(),
                },
            ],
        }
        db = _make_adapter_db(
            [snapshot_row],
            [{"ticker": "AAPL", "target_pct": 60.0}, {"ticker": "NVDA", "target_pct": 40.0}],
        )
        bundle = _run(build_sizing_bundle_from_persisted_data(
            user_id=_UID, db_client=db, _policy_config=_CERTIFIED_POLICY,
        ))
        assert bundle is not None
        assert bundle.positions["AAPL"].trust_status == DeploySizingTrustStatus.CERTIFIED
        assert bundle.positions["NVDA"].trust_status == DeploySizingTrustStatus.CERTIFIED

    def test_gate_k_enriched_snapshot_sizing_values_ready_true(self):
        """sizing_values_ready is True when all positions have certified market values."""
        snapshot_row = {
            "id": "snap-enriched-002",
            "snapshot_at": _now_iso(),
            "total_equity": 100_000.0,
            "cash_balance": 5_000.0,
            "positions_data": [
                {
                    "ticker": "AAPL",
                    "shares": 10.0,
                    "avg_cost": 150.0,
                    "market_value_usd": 60_000.0,
                },
                {
                    "ticker": "MSFT",
                    "shares": 20.0,
                    "avg_cost": 350.0,
                    "market_value_usd": 35_000.0,
                },
            ],
        }
        db = _make_adapter_db(
            [snapshot_row],
            [{"ticker": "AAPL", "target_pct": 60.0}, {"ticker": "MSFT", "target_pct": 35.0}],
        )
        bundle = _run(build_sizing_bundle_from_persisted_data(
            user_id=_UID, db_client=db, _policy_config=_CERTIFIED_POLICY,
        ))
        assert bundle is not None
        assert bundle.sizing_values_ready is True

    def test_gate_k_enriched_snapshot_exact_dollar_ready_true(self):
        """exact_dollar_ready is True with enriched snapshot + allocations + policy."""
        snapshot_row = {
            "id": "snap-enriched-003",
            "snapshot_at": _now_iso(),
            "total_equity": 100_000.0,
            "cash_balance": 5_000.0,
            "positions_data": [
                {"ticker": "AAPL", "shares": 10.0, "avg_cost": 150.0, "market_value_usd": 60_000.0},
                {"ticker": "MSFT", "shares": 20.0, "avg_cost": 350.0, "market_value_usd": 35_000.0},
            ],
        }
        db = _make_adapter_db(
            [snapshot_row],
            [{"ticker": "AAPL", "target_pct": 60.0}, {"ticker": "MSFT", "target_pct": 35.0}],
        )
        bundle = _run(build_sizing_bundle_from_persisted_data(
            user_id=_UID, db_client=db, _policy_config=_CERTIFIED_POLICY,
        ))
        assert bundle is not None
        assert bundle.exact_dollar_ready is True

    def test_gate_k_exact_dollar_ready_false_when_target_allocations_missing(self):
        """exact_dollar_ready stays False even with enriched snapshot if allocations absent."""
        snapshot_row = {
            "id": "snap-enriched-004",
            "snapshot_at": _now_iso(),
            "total_equity": 100_000.0,
            "cash_balance": 5_000.0,
            "positions_data": [
                {"ticker": "AAPL", "shares": 10.0, "avg_cost": 150.0, "market_value_usd": 60_000.0},
            ],
        }
        db = _make_adapter_db([snapshot_row], target_rows=[])  # no target allocations
        bundle = _run(build_sizing_bundle_from_persisted_data(
            user_id=_UID, db_client=db, _policy_config=_CERTIFIED_POLICY,
        ))
        assert bundle is not None
        assert bundle.exact_dollar_ready is False
        assert bundle.target_allocation_ready is False

    def test_gate_l_legacy_snapshot_positions_are_missing_trust(self):
        """Adapter fails safe: legacy snapshot without market_value_usd → MISSING trust."""
        snapshot_row = {
            "id": "snap-legacy-001",
            "snapshot_at": _now_iso(),
            "total_equity": 100_000.0,
            "cash_balance": 5_000.0,
            "positions_data": [
                # Legacy: only cost basis, no market_value_usd
                {"ticker": "AAPL", "shares": 10.0, "avg_cost": 150.0},
                {"ticker": "MSFT", "shares": 20.0, "avg_cost": 350.0},
            ],
        }
        db = _make_adapter_db(
            [snapshot_row],
            [{"ticker": "AAPL", "target_pct": 60.0}, {"ticker": "MSFT", "target_pct": 35.0}],
        )
        bundle = _run(build_sizing_bundle_from_persisted_data(
            user_id=_UID, db_client=db, _policy_config=_CERTIFIED_POLICY,
        ))
        assert bundle is not None
        assert bundle.positions["AAPL"].trust_status == DeploySizingTrustStatus.MISSING
        assert bundle.positions["MSFT"].trust_status == DeploySizingTrustStatus.MISSING

    def test_gate_l_legacy_snapshot_sizing_values_ready_false(self):
        """sizing_values_ready is False for legacy snapshots (no market_value_usd)."""
        snapshot_row = {
            "id": "snap-legacy-002",
            "snapshot_at": _now_iso(),
            "total_equity": 100_000.0,
            "cash_balance": 5_000.0,
            "positions_data": [
                {"ticker": "AAPL", "shares": 10.0, "avg_cost": 150.0},
            ],
        }
        db = _make_adapter_db(
            [snapshot_row],
            [{"ticker": "AAPL", "target_pct": 100.0}],
        )
        bundle = _run(build_sizing_bundle_from_persisted_data(
            user_id=_UID, db_client=db, _policy_config=_CERTIFIED_POLICY,
        ))
        assert bundle is not None
        assert bundle.sizing_values_ready is False
        assert bundle.exact_dollar_ready is False

    def test_gate_l_legacy_position_market_value_is_none_not_cost_basis(self):
        """Legacy position has None market value — cost basis was never promoted."""
        snapshot_row = {
            "id": "snap-legacy-003",
            "snapshot_at": _now_iso(),
            "total_equity": 100_000.0,
            "cash_balance": 5_000.0,
            "positions_data": [
                {"ticker": "AAPL", "shares": 10.0, "avg_cost": 150.0},
            ],
        }
        db = _make_adapter_db([snapshot_row])
        bundle = _run(build_sizing_bundle_from_persisted_data(
            user_id=_UID, db_client=db, _policy_config=_CERTIFIED_POLICY,
        ))
        assert bundle is not None
        assert bundle.positions["AAPL"].current_market_value_usd is None
