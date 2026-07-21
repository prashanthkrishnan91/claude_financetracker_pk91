"""Fast session creation — POST /intel/v3/run's control plane does scope
freezing and task-graph creation ONLY.

Proves, for a 34-ticker portfolio:
  * one durable session row (workflow_version=2);
  * 34 frozen intel_run_tickers rows (every active holding — full scope);
  * the seed task graph (portfolio context + macro + per-ticker lane
    collectors);
  * ZERO provider calls, ZERO LLM calls, ZERO decision-policy calls, ZERO
    snapshot writes;
  * prompt return (no execution inside the request);
  * idempotent retry of the same session id;
  * one active session per user (overlap returns the active session);
  * ownership and no-holdings handling.
"""
from __future__ import annotations

import time
import uuid

import pytest

from app.services.intelligence.v3.distributed import session_control_v1 as control
from app.services.intelligence.v3.distributed.task_contracts_v1 import (
    TASK_COLLECT_EVIDENCE_LANE,
    TASK_COLLECT_MACRO_CONTEXT,
    TASK_COLLECT_PORTFOLIO_CONTEXT,
    lanes_for_asset,
)
from tests.distributed_run_intel_test_utils import (
    FakeSupabase,
    GOLDEN_34,
    forbid_providers,
    seed_golden_portfolio,
    seed_position,
)

USER = str(uuid.uuid4())
OTHER_USER = str(uuid.uuid4())


def _forbid_llm_and_policy(monkeypatch):
    import app.services.agents.llm as llm_module
    import app.services.intelligence.v3.decision_policy_v1 as policy_module

    def _forbidden(*args, **kwargs):
        raise AssertionError("forbidden during session creation")

    monkeypatch.setattr(llm_module.LLMClient, "ask_json", _forbidden)
    monkeypatch.setattr(policy_module, "decide", _forbidden)


@pytest.mark.asyncio
async def test_34_ticker_creation_is_fast_and_execution_free(monkeypatch):
    client = FakeSupabase()
    seed_golden_portfolio(client, USER)
    forbid_providers(monkeypatch)
    _forbid_llm_and_policy(monkeypatch)

    session_id = str(uuid.uuid4())
    started = time.monotonic()
    result = await control.create_distributed_session(
        client=client, user_id=USER, session_id=session_id,
    )
    elapsed = time.monotonic() - started

    assert result["created"] is True
    assert result["run_session_id"] == session_id
    assert result["session_status"] == "running"
    assert result["total_tickers"] == 34
    # Prompt return: in-memory fake, so anything slow means real work leaked in.
    assert elapsed < 2.0

    sessions = client.rows("intel_run_sessions")
    assert len(sessions) == 1
    assert sessions[0]["workflow_version"] == 2
    assert sorted(sessions[0]["holdings_scope"]) == sorted(GOLDEN_34)

    ticker_rows = client.rows("intel_run_tickers")
    assert len(ticker_rows) == 34
    assert {r["ticker"] for r in ticker_rows} == set(GOLDEN_34)
    by_ticker = {r["ticker"]: r for r in ticker_rows}
    assert by_ticker["AAPL"]["asset_type"] == "equity"
    assert by_ticker["VTI"]["asset_type"] == "etf"
    assert by_ticker["BTC"]["asset_type"] == "crypto"
    # Frozen truth is present.
    assert by_ticker["AAPL"]["quantity"] > 0
    assert by_ticker["AAPL"]["market_value"] is not None
    assert by_ticker["AAPL"]["portfolio_weight_pct"] is not None

    tasks = client.rows("intel_run_tasks")
    types = {t["task_type"] for t in tasks}
    assert TASK_COLLECT_PORTFOLIO_CONTEXT in types
    assert TASK_COLLECT_MACRO_CONTEXT in types
    lane_tasks = [t for t in tasks if t["task_type"] == TASK_COLLECT_EVIDENCE_LANE]
    expected_lane_count = sum(
        len(lanes_for_asset(by_ticker[t]["asset_type"])) for t in GOLDEN_34
    )
    assert len(lane_tasks) == expected_lane_count
    # Zero downstream tasks are created upfront (readiness unknown).
    assert not any(
        t["task_type"] in (
            "build_evidence_bundle", "specialist_analysis", "review_conflict",
            "ticker_decision", "portfolio_join_publish",
        )
        for t in tasks
    )
    # Zero snapshot writes, zero legacy queue writes.
    assert client.rows("intel_v3_snapshots") == []
    assert client.rows("analyst_refresh_jobs") == []


@pytest.mark.asyncio
async def test_same_session_id_retry_is_idempotent(monkeypatch):
    client = FakeSupabase()
    seed_golden_portfolio(client, USER)
    forbid_providers(monkeypatch)
    session_id = str(uuid.uuid4())

    first = await control.create_distributed_session(
        client=client, user_id=USER, session_id=session_id,
    )
    second = await control.create_distributed_session(
        client=client, user_id=USER, session_id=session_id,
    )
    assert first["created"] is True
    assert second["created"] is False
    assert second["run_session_id"] == session_id
    assert len(client.rows("intel_run_sessions")) == 1
    assert len(client.rows("intel_run_tickers")) == 34


@pytest.mark.asyncio
async def test_new_click_while_active_adopts_active_session(monkeypatch):
    client = FakeSupabase()
    seed_golden_portfolio(client, USER)
    forbid_providers(monkeypatch)

    first_id = str(uuid.uuid4())
    await control.create_distributed_session(
        client=client, user_id=USER, session_id=first_id,
    )
    second_id = str(uuid.uuid4())
    result = await control.create_distributed_session(
        client=client, user_id=USER, session_id=second_id,
    )
    assert result["created"] is False
    assert result.get("adopted_active_session") is True
    assert result["run_session_id"] == first_id
    assert len(client.rows("intel_run_sessions")) == 1


@pytest.mark.asyncio
async def test_ownership_mismatch_raises():
    client = FakeSupabase()
    seed_golden_portfolio(client, USER)
    session_id = str(uuid.uuid4())
    await control.create_distributed_session(
        client=client, user_id=USER, session_id=session_id,
    )
    with pytest.raises(control.SessionOwnershipError):
        await control.create_distributed_session(
            client=client, user_id=OTHER_USER, session_id=session_id,
        )
    with pytest.raises(control.SessionOwnershipError):
        await control.get_session_status(
            client=client, user_id=OTHER_USER, session_id=session_id,
        )


@pytest.mark.asyncio
async def test_no_holdings_is_honest_and_creates_nothing():
    client = FakeSupabase()
    result = await control.create_distributed_session(
        client=client, user_id=USER, session_id=str(uuid.uuid4()),
    )
    assert result["session_status"] == "not_created"
    assert result["reason"] == "no_active_holdings"
    assert result["retryable"] is False
    assert client.rows("intel_run_sessions") == []


@pytest.mark.asyncio
async def test_sell_and_zero_share_positions_excluded():
    client = FakeSupabase()
    seed_position(client, USER, "AAPL")
    seed_position(client, USER, "OLD", category="SELL")
    seed_position(client, USER, "EMPTY", shares=0.0)
    session_id = str(uuid.uuid4())
    result = await control.create_distributed_session(
        client=client, user_id=USER, session_id=session_id,
    )
    assert result["total_tickers"] == 1
    assert {r["ticker"] for r in client.rows("intel_run_tickers")} == {"AAPL"}


@pytest.mark.asyncio
async def test_status_endpoint_is_read_only(monkeypatch):
    client = FakeSupabase()
    seed_golden_portfolio(client, USER)
    forbid_providers(monkeypatch)
    _forbid_llm_and_policy(monkeypatch)
    session_id = str(uuid.uuid4())
    await control.create_distributed_session(
        client=client, user_id=USER, session_id=session_id,
    )
    task_count_before = len(client.rows("intel_run_tasks"))
    for _ in range(5):
        status = await control.get_session_status(
            client=client, user_id=USER, session_id=session_id,
        )
    assert status["session_status"] == "running"
    assert status["total_tickers"] == 34
    assert status["plain_status"]
    # Polling observed; it never created/advanced work.
    assert len(client.rows("intel_run_tasks")) == task_count_before


@pytest.mark.asyncio
async def test_find_active_session_recovery():
    client = FakeSupabase()
    seed_golden_portfolio(client, USER)
    session_id = str(uuid.uuid4())
    await control.create_distributed_session(
        client=client, user_id=USER, session_id=session_id,
    )
    active = await control.find_active_session(client=client, user_id=USER)
    assert active is not None and str(active["id"]) == session_id
    # Terminal sessions are not "active".
    client.table("intel_run_sessions").update({"status": "completed"}).eq(
        "id", session_id
    ).execute()
    assert await control.find_active_session(client=client, user_id=USER) is None
