from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

from app.services.intelligence.v3.intel_v3_service import IntelV3Service


@pytest.mark.asyncio
async def test_get_weight_map_uses_snapshot_market_value_not_current_value():
    svc = IntelV3Service(user_id=uuid.uuid4())

    snap_chain = MagicMock()
    snap_chain.select.return_value = snap_chain
    snap_chain.eq.return_value = snap_chain
    snap_chain.order.return_value = snap_chain
    snap_chain.limit.return_value = snap_chain
    snap_chain.execute.return_value = MagicMock(data=[{
        "positions_data": [
            {"ticker": "AAPL", "market_value": 700.0},
            {"ticker": "MSFT", "market_value": 300.0},
        ]
    }])

    positions_chain = MagicMock()
    positions_chain.select.return_value = positions_chain
    positions_chain.eq.return_value = positions_chain
    positions_chain.execute.return_value = MagicMock(data=[])

    def _table(name: str):
        if name == "portfolio_snapshots":
            return snap_chain
        if name == "positions":
            return positions_chain
        raise AssertionError(f"Unexpected table: {name}")

    svc.client = MagicMock()
    svc.client.table.side_effect = _table

    out = await svc._get_weight_map()
    assert round(out["AAPL"], 1) == 70.0
    assert round(out["MSFT"], 1) == 30.0
    assert positions_chain.select.call_count == 0


@pytest.mark.asyncio
async def test_get_weight_map_falls_back_to_shares_avg_cost():
    svc = IntelV3Service(user_id=uuid.uuid4())

    snap_chain = MagicMock()
    snap_chain.select.return_value = snap_chain
    snap_chain.eq.return_value = snap_chain
    snap_chain.order.return_value = snap_chain
    snap_chain.limit.return_value = snap_chain
    snap_chain.execute.return_value = MagicMock(data=[])

    positions_chain = MagicMock()
    positions_chain.select.return_value = positions_chain
    positions_chain.eq.return_value = positions_chain
    positions_chain.execute.return_value = MagicMock(data=[
        {"ticker": "QQQ", "shares": 2, "avg_cost": 100},
        {"ticker": "VOO", "shares": 1, "avg_cost": 200},
    ])

    def _table(name: str):
        return snap_chain if name == "portfolio_snapshots" else positions_chain

    svc.client = MagicMock()
    svc.client.table.side_effect = _table

    out = await svc._get_weight_map()
    assert round(out["QQQ"], 1) == 50.0
    assert round(out["VOO"], 1) == 50.0
    assert positions_chain.select.call_args.args[0] == "ticker,shares,avg_cost"
