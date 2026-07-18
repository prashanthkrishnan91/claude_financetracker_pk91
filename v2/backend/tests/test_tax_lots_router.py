"""GET /positions/tax-lots — auth, reconciliation gating, route precedence."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

USER = SimpleNamespace(id=uuid4(), email="owner@example.com")


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def select(self, *_):
        return self

    def eq(self, col, val):
        self._rows = [r for r in self._rows if str(r.get(col, "")) == str(val)]
        return self

    def limit(self, *_):
        return self

    def order(self, *_a, **_k):
        return self

    def execute(self):
        return SimpleNamespace(data=list(self._rows))


class _FakeClient:
    def __init__(self, positions, transactions):
        self._tables = {"positions": positions, "transactions": transactions}

    def table(self, name):
        return _FakeQuery(list(self._tables[name]))


def _app():
    from app.main import app
    return app


@pytest.fixture()
def client(monkeypatch):
    from app.middleware.auth import get_current_user

    app = _app()
    app.dependency_overrides[get_current_user] = lambda: USER

    async def _no_prices(self, tickers):
        return {}

    monkeypatch.setattr(
        "app.services.price_engine.PriceService.fetch_prices", _no_prices, raising=True
    )
    yield TestClient(app)
    app.dependency_overrides.pop(get_current_user, None)


def _wire_db(monkeypatch, positions, transactions):
    fake = _FakeClient(positions, transactions)
    monkeypatch.setattr("app.routers.positions.get_supabase_client", lambda: fake)


def _pos(ticker, shares, avg_cost):
    return {"user_id": str(USER.id), "ticker": ticker, "shares": shares, "avg_cost": avg_cost}


def _buy(ticker, qty, price, tx_date):
    return {
        "user_id": str(USER.id), "ticker": ticker, "tx_type": "Buy",
        "quantity": qty, "price": price, "amount": None, "tx_date": tx_date,
    }


def test_requires_auth(monkeypatch):
    _wire_db(monkeypatch, [], [])
    r = TestClient(_app()).get("/api/v1/positions/tax-lots")
    assert r.status_code in (401, 403)


def test_literal_path_wins_over_ticker_route(monkeypatch, client):
    """/positions/tax-lots must not be swallowed by /positions/{ticker}."""
    _wire_db(monkeypatch, [], [])
    r = client.get("/api/v1/positions/tax-lots")
    assert r.status_code == 200
    assert r.json()["engine_version"] == "tax_lot_engine_v1"


def test_reconciled_holding_shows_lots_with_disclaimer(monkeypatch, client):
    _wire_db(
        monkeypatch,
        [_pos("VTI", 10.0, 100.0)],
        [_buy("VTI", 10, 100.0, "2025-01-10")],
    )
    body = client.get("/api/v1/positions/tax-lots").json()
    assert "not tax advice" in body["disclaimer"]
    assert "US federal" in body["jurisdiction_note"]
    holding = body["holdings"][0]
    assert holding["ticker"] == "VTI"
    assert holding["authoritative"] is True
    assert holding["reconciliation"]["status"] == "reconciled"
    assert len(holding["lots"]) == 1
    assert holding["lots"][0]["remaining_shares"] == 10.0
    # No price wired → market fields null, never fabricated.
    assert holding["lots"][0]["current_value"] is None


def test_unreconciled_holding_blocks_lots_with_message(monkeypatch, client):
    _wire_db(
        monkeypatch,
        [_pos("NVDA", 20.0, 50.0)],
        [_buy("NVDA", 10, 50.0, "2025-01-10")],  # 10 lot shares vs 20 position shares
    )
    holding = client.get("/api/v1/positions/tax-lots").json()["holdings"][0]
    assert holding["authoritative"] is False
    assert holding["lots"] is None
    assert holding["reconciliation"]["status"] == "quantity_mismatch"
    assert holding["message"] == "Tax-lot details need reconciliation before they can be relied on."


def test_no_transactions_holding_reports_no_history(monkeypatch, client):
    _wire_db(monkeypatch, [_pos("BTC", 0.5, 30000.0)], [])
    holding = client.get("/api/v1/positions/tax-lots").json()["holdings"][0]
    assert holding["authoritative"] is False
    assert holding["reconciliation"]["status"] == "no_transaction_history"


def test_unsupported_event_surfaces_in_diagnostics(monkeypatch, client):
    _wire_db(
        monkeypatch,
        [_pos("AAPL", 40.0, 50.0)],
        [
            _buy("AAPL", 20, 50.0, "2025-01-10"),
            {"user_id": str(USER.id), "ticker": "AAPL", "tx_type": "SPL",
             "quantity": 20, "price": None, "amount": None, "tx_date": "2025-02-10"},
        ],
    )
    holding = client.get("/api/v1/positions/tax-lots").json()["holdings"][0]
    assert holding["authoritative"] is False
    assert holding["reconciliation"]["status"] == "blocked_unsupported_events"
    assert holding["unsupported_events"][0]["tx_type"] == "SPL"
