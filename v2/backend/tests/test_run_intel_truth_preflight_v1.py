"""Portfolio financial-truth preflight (final Run Intel operational-reliability
PR, section 1) — the ONE session-start path gates on the existing
``financial_truth_baseline_v1`` contract before any session/task/provider/LLM
work exists. Acceptance matrix rows 1-5.
"""
from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from app.services.intelligence.v3.distributed import session_control_v1 as control
from tests.distributed_run_intel_test_utils import (
    FakeSupabase,
    GOLDEN_34,
    seed_golden_portfolio,
    seed_position,
    seed_reconciled_snapshot,
    now_utc,
)

USER = str(uuid.uuid4())


def _forbidden(*_args, **_kwargs):
    raise AssertionError("forbidden call reached during this scenario")


class _FakeRefreshService:
    calls = 0

    def __init__(self, *, user_id, client=None, **_kw):
        self._user_id = str(user_id)
        self._client = client

    async def create_snapshot(self):
        _FakeRefreshService.calls += 1
        seed_reconciled_snapshot(self._client, self._user_id)
        return {}


class _FakeFailingRefreshService:
    def __init__(self, *, user_id, client=None, **_kw):
        pass

    async def create_snapshot(self):
        raise RuntimeError("refresh boom")


class _FakeNoOpRefreshService:
    def __init__(self, *, user_id, client=None, **_kw):
        pass

    async def create_snapshot(self):
        return {}


@pytest.mark.asyncio
async def test_active_session_skips_preflight_entirely(monkeypatch):
    client = FakeSupabase()
    seed_golden_portfolio(client, USER)
    first_id = str(uuid.uuid4())
    first = await control.create_distributed_session(
        client=client, user_id=USER, session_id=first_id,
    )
    assert first["created"] is True

    monkeypatch.setattr(control, "run_financial_truth_baseline_strict", _forbidden)
    monkeypatch.setattr(control, "PortfolioService", _forbidden)

    result = await control.create_distributed_session(
        client=client, user_id=USER, session_id=str(uuid.uuid4()),
    )
    assert result["created"] is False
    assert result.get("adopted_active_session") is True
    assert result["run_session_id"] == first_id
    assert len(client.rows("intel_run_sessions")) == 1


@pytest.mark.asyncio
async def test_fresh_reconciled_baseline_creates_one_session_without_refresh(monkeypatch):
    client = FakeSupabase()
    seed_golden_portfolio(client, USER)
    monkeypatch.setattr(control, "PortfolioService", _forbidden)

    session_id = str(uuid.uuid4())
    result = await control.create_distributed_session(
        client=client, user_id=USER, session_id=session_id,
    )
    assert result["created"] is True
    sessions = client.rows("intel_run_sessions")
    assert len(sessions) == 1
    preflight = sessions[0]["metrics"]["preflight"]
    assert preflight["schema_version"] == "run_intel_preflight_v1"
    assert preflight["status"] == "passed"
    assert preflight["snapshot_refreshed"] is False
    assert preflight["reconciliation_status"] == "pass"
    assert sorted(sessions[0]["holdings_scope"]) == sorted(GOLDEN_34)


@pytest.mark.asyncio
async def test_stale_snapshot_refresh_succeeds_then_creates_session(monkeypatch):
    client = FakeSupabase()
    seed_golden_portfolio(client, USER)
    client.rows("portfolio_snapshots")[0]["snapshot_at"] = (
        now_utc() - timedelta(hours=48)
    ).isoformat()
    _FakeRefreshService.calls = 0
    monkeypatch.setattr(control, "PortfolioService", _FakeRefreshService)

    result = await control.create_distributed_session(
        client=client, user_id=USER, session_id=str(uuid.uuid4()),
    )
    assert result["created"] is True
    assert _FakeRefreshService.calls == 1
    preflight = client.rows("intel_run_sessions")[0]["metrics"]["preflight"]
    assert preflight["snapshot_refreshed"] is True
    assert preflight["status"] == "passed"


@pytest.mark.asyncio
async def test_stale_snapshot_refresh_failure_blocks_honestly(monkeypatch):
    client = FakeSupabase()
    seed_golden_portfolio(client, USER)
    client.rows("portfolio_snapshots")[0]["snapshot_at"] = (
        now_utc() - timedelta(hours=48)
    ).isoformat()
    monkeypatch.setattr(control, "PortfolioService", _FakeFailingRefreshService)

    result = await control.create_distributed_session(
        client=client, user_id=USER, session_id=str(uuid.uuid4()),
    )
    assert result["session_status"] == "not_created"
    assert result["status"] == "blocked"
    assert result["code"] == "portfolio_refresh_failed"
    assert result["provider_calls"] == 0
    assert result["llm_calls"] == 0
    assert "could not" in result["message"].lower() or "did not start" in result["message"].lower()
    assert client.rows("intel_run_sessions") == []
    assert client.rows("intel_run_tasks") == []


@pytest.mark.asyncio
async def test_snapshot_unavailable_after_refresh_blocks(monkeypatch):
    client = FakeSupabase()
    seed_golden_portfolio(client, USER)
    client.store["portfolio_snapshots"] = []  # unavailable, not merely stale
    monkeypatch.setattr(control, "PortfolioService", _FakeNoOpRefreshService)

    result = await control.create_distributed_session(
        client=client, user_id=USER, session_id=str(uuid.uuid4()),
    )
    assert result["code"] == "portfolio_truth_unavailable"
    assert client.rows("intel_run_sessions") == []


@pytest.mark.asyncio
async def test_reconciliation_failure_blocks_without_refresh(monkeypatch):
    client = FakeSupabase()
    seed_golden_portfolio(client, USER)
    snap = client.rows("portfolio_snapshots")[0]
    snap["total_equity"] = snap["total_equity"] * 3  # >5% divergence
    monkeypatch.setattr(control, "PortfolioService", _forbidden)

    result = await control.create_distributed_session(
        client=client, user_id=USER, session_id=str(uuid.uuid4()),
    )
    assert result["code"] == "portfolio_reconciliation_failed"
    assert client.rows("intel_run_sessions") == []


@pytest.mark.asyncio
async def test_empty_scope_blocks_with_code_and_legacy_reason():
    client = FakeSupabase()
    result = await control.create_distributed_session(
        client=client, user_id=USER, session_id=str(uuid.uuid4()),
    )
    assert result["code"] == "portfolio_scope_empty"
    assert result["reason"] == "no_active_holdings"
    assert client.rows("intel_run_sessions") == []


@pytest.mark.asyncio
async def test_database_read_failure_fails_closed(monkeypatch):
    client = FakeSupabase()
    seed_golden_portfolio(client, USER)

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(control, "run_financial_truth_baseline_strict", _boom)
    result = await control.create_distributed_session(
        client=client, user_id=USER, session_id=str(uuid.uuid4()),
    )
    assert result["code"] == "portfolio_truth_unavailable"
    assert result["provider_calls"] == 0
    assert result["llm_calls"] == 0
    assert client.rows("intel_run_sessions") == []


@pytest.mark.asyncio
async def test_duplicate_active_tickers_block_instead_of_deduplicating():
    client = FakeSupabase()
    seed_position(client, USER, "AAPL")
    seed_position(client, USER, "AAPL", allow_duplicate=True)  # a truth defect
    result = await control.create_distributed_session(
        client=client, user_id=USER, session_id=str(uuid.uuid4()),
    )
    assert result["code"] == "portfolio_reconciliation_failed"
    assert "AAPL" in result["repair_action"]
    assert client.rows("intel_run_sessions") == []


@pytest.mark.asyncio
async def test_position_inserted_during_refresh_is_reflected_not_missed(monkeypatch):
    """TOCTOU proof: a position that appears only after the canonical refresh
    (the FINAL strict read) must be part of the frozen scope — the session
    must never freeze from the stale first read."""
    client = FakeSupabase()
    seed_position(client, USER, "AAPL")
    client.rows("portfolio_snapshots")[0]["snapshot_at"] = (
        now_utc() - timedelta(hours=48)
    ).isoformat()

    class _RefreshAndInsert:
        def __init__(self, *, user_id, client=None, **_kw):
            self._user_id = str(user_id)
            self._client = client

        async def create_snapshot(self):
            # Simulate a concurrent import landing between the first read
            # and the refresh.
            seed_position(self._client, self._user_id, "MSFT")
            return {}

    monkeypatch.setattr(control, "PortfolioService", _RefreshAndInsert)
    session_id = str(uuid.uuid4())
    result = await control.create_distributed_session(
        client=client, user_id=USER, session_id=session_id,
    )
    assert result["created"] is True
    session = client.rows("intel_run_sessions")[0]
    assert sorted(session["holdings_scope"]) == ["AAPL", "MSFT"]
    ticker_rows = {r["ticker"] for r in client.rows("intel_run_tickers")}
    assert ticker_rows == {"AAPL", "MSFT"}


@pytest.mark.asyncio
async def test_position_query_failure_blocks_not_reported_as_empty_scope(monkeypatch):
    client = FakeSupabase()
    seed_golden_portfolio(client, USER)

    def _raise_on_positions(name):
        if name == "positions":
            raise RuntimeError("positions query failed")
        return FakeSupabase.table(client, name)

    monkeypatch.setattr(client, "table", _raise_on_positions)
    result = await control.create_distributed_session(
        client=client, user_id=USER, session_id=str(uuid.uuid4()),
    )
    # Must be reported as an unverifiable truth read, never as an empty
    # portfolio (which would otherwise render the misleading "Add positions"
    # idle state instead of an honest failure).
    assert result["code"] == "portfolio_truth_unavailable"
    assert result["reason"] != "no_active_holdings"
    assert client.rows("intel_run_sessions") == []


@pytest.mark.asyncio
async def test_frozen_scope_values_come_from_the_passed_preflight_result():
    client = FakeSupabase()
    seed_position(client, USER, "AAPL", shares=10.0, avg_cost=100.0, close_price=150.0)
    session_id = str(uuid.uuid4())
    result = await control.create_distributed_session(
        client=client, user_id=USER, session_id=session_id,
    )
    assert result["created"] is True
    row = next(r for r in client.rows("intel_run_tickers") if r["ticker"] == "AAPL")
    assert row["quantity"] == 10.0
    assert row["market_value"] == 1500.0
    assert row["cost_basis"] == 1000.0
