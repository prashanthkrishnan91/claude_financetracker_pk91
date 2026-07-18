"""Watchlist router — authenticated CRUD, duplicate policy, price-unknown state,
cross-user isolation, and migration-missing operational truth."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

USER_A = SimpleNamespace(id=uuid4(), email="a@example.com")
USER_B = SimpleNamespace(id=uuid4(), email="b@example.com")


# ── Fake Supabase table ───────────────────────────────────────────────────────


class _FakeQuery:
    def __init__(self, store: list[dict], table_exists: bool):
        self._store = store
        self._exists = table_exists
        self._filters: list[tuple[str, str, object]] = []
        self._select = None
        self._insert = None
        self._update = None
        self._delete = False
        self._order = None

    def select(self, *_):
        self._select = True
        return self

    def insert(self, row):
        self._insert = row
        return self

    def update(self, updates):
        self._update = updates
        return self

    def delete(self):
        self._delete = True
        return self

    def eq(self, col, val):
        self._filters.append((col, "eq", str(val)))
        return self

    def neq(self, col, val):
        self._filters.append((col, "neq", str(val)))
        return self

    def order(self, *_a, **_k):
        return self

    def _match(self, row):
        for col, op, val in self._filters:
            actual = str(row.get(col))
            if op == "eq" and actual != val:
                return False
            if op == "neq" and actual == val:
                return False
        return True

    def execute(self):
        if not self._exists:
            raise Exception(
                'relation "public.watchlist_items" does not exist (PGRST205)'
            )
        if self._insert is not None:
            row = {
                **self._insert,
                "id": str(uuid4()),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": None,
            }
            self._store.append(row)
            return SimpleNamespace(data=[row])
        if self._update is not None:
            updated = []
            for row in self._store:
                if self._match(row):
                    row.update(self._update)
                    updated.append(row)
            return SimpleNamespace(data=updated)
        if self._delete:
            keep = [r for r in self._store if not self._match(r)]
            deleted = [r for r in self._store if self._match(r)]
            self._store[:] = keep
            return SimpleNamespace(data=deleted)
        return SimpleNamespace(data=[r for r in self._store if self._match(r)])


class _FakeClient:
    def __init__(self, table_exists: bool = True):
        self.store: list[dict] = []
        self.exists = table_exists

    def table(self, name):
        assert name == "watchlist_items"
        return _FakeQuery(self.store, self.exists)


@pytest.fixture()
def fake_db(monkeypatch):
    client = _FakeClient()
    monkeypatch.setattr("app.routers.watchlist.get_supabase_client", lambda: client)

    async def _no_prices(self, tickers):
        return {}

    monkeypatch.setattr(
        "app.services.price_engine.PriceService.fetch_prices", _no_prices, raising=True
    )
    return client


def _app():
    from app.main import app
    return app


def _get_current_user_dep():
    from app.middleware.auth import get_current_user
    return get_current_user


@pytest.fixture()
def as_user_a():
    app = _app()
    dep = _get_current_user_dep()
    app.dependency_overrides[dep] = lambda: USER_A
    yield TestClient(app)
    app.dependency_overrides.pop(dep, None)


def _create(client, ticker="vti", criteria="price_below", threshold=250.0, notes=None):
    return client.post(
        "/api/v1/watchlist",
        json={"ticker": ticker, "criteria_type": criteria, "threshold": threshold, "notes": notes},
    )


# ── Auth ──────────────────────────────────────────────────────────────────────


def test_all_endpoints_require_auth(fake_db):
    client = TestClient(_app())
    assert client.get("/api/v1/watchlist").status_code in (401, 403)
    assert _create(client).status_code in (401, 403)
    assert client.patch(f"/api/v1/watchlist/{uuid4()}", json={"threshold": 1}).status_code in (401, 403)
    assert client.delete(f"/api/v1/watchlist/{uuid4()}").status_code in (401, 403)


# ── Create / list ─────────────────────────────────────────────────────────────


def test_create_normalizes_ticker_case_and_lists(fake_db, as_user_a):
    r = _create(as_user_a, ticker="vti", threshold=250, notes="  entry point  ")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["ticker"] == "VTI"
    assert body["criteria_type"] == "price_below"
    assert body["threshold"] == 250.0
    assert body["notes"] == "entry point"

    listed = as_user_a.get("/api/v1/watchlist")
    assert listed.status_code == 200
    assert [e["ticker"] for e in listed.json()] == ["VTI"]


def test_create_validates_ticker_shape(fake_db, as_user_a):
    for bad in ("", "   ", "THIS_TICKER_IS_WAY_TOO_LONG", "BAD TICKER", "A..B"):
        r = _create(as_user_a, ticker=bad)
        assert r.status_code == 422, f"{bad!r} should be rejected"


def test_create_validates_criteria_and_threshold(fake_db, as_user_a):
    r = as_user_a.post(
        "/api/v1/watchlist",
        json={"ticker": "VTI", "criteria_type": "moon_phase", "threshold": 10},
    )
    assert r.status_code == 422
    assert _create(as_user_a, threshold=0).status_code == 422
    assert _create(as_user_a, threshold=-5).status_code == 422


def test_duplicate_same_direction_rejected_but_other_direction_allowed(fake_db, as_user_a):
    assert _create(as_user_a, ticker="VTI", criteria="price_below").status_code == 201
    dup = _create(as_user_a, ticker="VTI", criteria="price_below", threshold=99)
    assert dup.status_code == 409
    assert dup.json()["detail"]["error"] == "duplicate_watchlist_entry"
    other = _create(as_user_a, ticker="VTI", criteria="price_above", threshold=400)
    assert other.status_code == 201


# ── Price enrichment / unknown state ─────────────────────────────────────────


def test_unknown_criteria_state_when_no_trusted_price(fake_db, as_user_a):
    _create(as_user_a, ticker="VTI")
    entry = as_user_a.get("/api/v1/watchlist").json()[0]
    assert entry["current_price"] is None
    assert entry["price_as_of"] is None
    assert entry["criteria_met"] is None  # unknown, never fabricated


def test_criteria_met_computed_from_batched_prices(fake_db, as_user_a, monkeypatch):
    _create(as_user_a, ticker="VTI", criteria="price_below", threshold=300)
    _create(as_user_a, ticker="QQQ", criteria="price_above", threshold=100)
    _create(as_user_a, ticker="KLAR", criteria="price_above", threshold=50)

    calls = []

    async def _prices(self, tickers):
        calls.append(sorted(tickers))
        now = datetime.now(timezone.utc).timestamp()
        mk = lambda t, p: SimpleNamespace(
            ticker=t, mid_price=p, timestamp=now, error=None, is_valid=True
        )
        return {"VTI": mk("VTI", 250.0), "QQQ": mk("QQQ", 450.0)}

    monkeypatch.setattr(
        "app.services.price_engine.PriceService.fetch_prices", _prices, raising=True
    )
    entries = {e["ticker"]: e for e in as_user_a.get("/api/v1/watchlist").json()}
    # Exactly one batched call for all tickers — no per-row provider calls.
    assert calls == [["KLAR", "QQQ", "VTI"]]
    assert entries["VTI"]["criteria_met"] is True  # 250 < 300
    assert entries["VTI"]["current_price"] == 250.0
    assert entries["VTI"]["price_as_of"] is not None
    assert entries["QQQ"]["criteria_met"] is True  # 450 > 100
    assert entries["KLAR"]["criteria_met"] is None  # no price → unknown


# ── Edit ──────────────────────────────────────────────────────────────────────


def test_edit_threshold_and_notes(fake_db, as_user_a):
    created = _create(as_user_a, ticker="VTI").json()
    r = as_user_a.patch(
        f"/api/v1/watchlist/{created['id']}",
        json={"threshold": 275.5, "notes": "updated"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["threshold"] == 275.5
    assert r.json()["notes"] == "updated"
    assert r.json()["updated_at"] is not None


def test_edit_direction_conflict_rejected(fake_db, as_user_a):
    _create(as_user_a, ticker="VTI", criteria="price_below")
    above = _create(as_user_a, ticker="VTI", criteria="price_above", threshold=400).json()
    r = as_user_a.patch(
        f"/api/v1/watchlist/{above['id']}", json={"criteria_type": "price_below"}
    )
    assert r.status_code == 409


def test_edit_empty_payload_rejected(fake_db, as_user_a):
    created = _create(as_user_a).json()
    assert as_user_a.patch(f"/api/v1/watchlist/{created['id']}", json={}).status_code == 422


def test_edit_invalid_uuid_rejected(fake_db, as_user_a):
    assert as_user_a.patch("/api/v1/watchlist/not-a-uuid", json={"threshold": 1}).status_code == 422


# ── Delete ────────────────────────────────────────────────────────────────────


def test_delete_own_entry(fake_db, as_user_a):
    created = _create(as_user_a).json()
    assert as_user_a.delete(f"/api/v1/watchlist/{created['id']}").status_code == 204
    assert as_user_a.get("/api/v1/watchlist").json() == []


# ── Cross-user isolation ──────────────────────────────────────────────────────


def test_cross_user_isolation(fake_db):
    app = _app()
    dep = _get_current_user_dep()
    app.dependency_overrides[dep] = lambda: USER_A
    client = TestClient(app)
    created = _create(client, ticker="VTI").json()

    app.dependency_overrides[dep] = lambda: USER_B
    client_b = TestClient(app)
    # B cannot see A's rows
    assert client_b.get("/api/v1/watchlist").json() == []
    # B cannot edit or delete A's row (404, not 403 — no existence leak)
    assert client_b.patch(
        f"/api/v1/watchlist/{created['id']}", json={"threshold": 1}
    ).status_code == 404
    assert client_b.delete(f"/api/v1/watchlist/{created['id']}").status_code == 404
    # A's row survives untouched
    app.dependency_overrides[dep] = lambda: USER_A
    assert len(client.get("/api/v1/watchlist").json()) == 1
    app.dependency_overrides.pop(dep, None)


# ── Migration-missing operational truth ──────────────────────────────────────


def test_missing_table_returns_migration_required(monkeypatch, as_user_a):
    client = _FakeClient(table_exists=False)
    monkeypatch.setattr("app.routers.watchlist.get_supabase_client", lambda: client)
    r = as_user_a.get("/api/v1/watchlist")
    assert r.status_code == 503
    assert r.json()["detail"]["error"] == "watchlist_migration_required"
    assert "025_watchlist.sql" in r.json()["detail"]["message"]
    r = _create(as_user_a)
    assert r.status_code == 503


# ── Product boundaries ────────────────────────────────────────────────────────


def test_watchlist_module_never_imports_advisor_or_llm_paths():
    """Watchlist tickers must never enter the Paycheck Advisor candidate set,
    and the router must not trigger LLM or alert work."""
    import app.routers.watchlist as w
    import inspect

    src = inspect.getsource(w)
    assert "allocation_policy" not in src
    assert "run_next_buy_policy_diagnostic" not in src
    assert "anthropic" not in src.lower()
    assert "orchestrator" not in src.lower()
    assert "alert" not in src.lower().replace("alerts, email, push", "")


def test_watchlist_router_registered_in_app(fake_db, as_user_a):
    # Route inclusion is lazy in this FastAPI version — assert through the
    # served OpenAPI schema rather than app.routes introspection.
    schema = as_user_a.get("/openapi.json").json()
    assert "/api/v1/watchlist" in schema["paths"]
    assert "/api/v1/watchlist/{item_id}" in schema["paths"]
