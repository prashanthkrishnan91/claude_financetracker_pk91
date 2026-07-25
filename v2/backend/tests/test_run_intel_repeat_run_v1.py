"""Repeat-run and selective-refresh semantics (final Run Intel operational-
reliability PR, section 4) — the REAL scheduler/collectors/specialists/
supervisor drive two sessions back to back over the same durable store.
Acceptance matrix rows 16, 17, 22.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.services.intelligence.v3.distributed import session_control_v1 as control
from app.services.intelligence.v3.distributed.task_contracts_v1 import (
    LANE_NEWS_SENTIMENT,
    SESSION_COMPLETED,
    TASK_COLLECT_EVIDENCE_LANE,
)
from app.services.intelligence.v3.distributed.worker_supervisor_v1 import WorkerSupervisor
from tests.distributed_run_intel_test_utils import (
    FakeLLM,
    FakeSupabase,
    ProviderRecorder,
    drive_supervisor_to_completion,
    make_settings,
    patch_providers,
    seed_position,
)

USER = str(uuid.uuid4())
TICKERS = ["AAPL", "MSFT"]


def _make_supervisor(client, llm):
    settings = make_settings(
        intel_v3_distributed_max_collector_concurrency=50,
        intel_v3_distributed_max_llm_concurrency=2,
        intel_v3_distributed_max_specialist_batch=5,
    )
    return WorkerSupervisor(client=client, settings=settings, llm=llm, worker_id="test-worker")


async def _terminate_active_and_start(client) -> str:
    for row in client.rows("intel_run_sessions"):
        if row.get("status") in ("created", "running"):
            row["status"] = SESSION_COMPLETED
    session_id = str(uuid.uuid4())
    await control.create_distributed_session(client=client, user_id=USER, session_id=session_id)
    return session_id


@pytest.mark.asyncio
async def test_immediate_rerun_reuses_everything_with_zero_new_calls(monkeypatch):
    client = FakeSupabase()
    for t in TICKERS:
        seed_position(client, USER, t)
    recorder = ProviderRecorder()
    patch_providers(monkeypatch, recorder)
    llm = FakeLLM()
    supervisor = _make_supervisor(client, llm)

    session_1 = str(uuid.uuid4())
    await control.create_distributed_session(client=client, user_id=USER, session_id=session_1)
    await drive_supervisor_to_completion(supervisor)
    calls_after_1 = len(recorder.calls)
    llm_calls_after_1 = len(llm.calls)
    snapshot_1 = next(s for s in client.rows("intel_v3_snapshots") if s["run_session_id"] == session_1)

    session_2 = await _terminate_active_and_start(client)
    await drive_supervisor_to_completion(supervisor)

    assert len(recorder.calls) == calls_after_1, "immediate rerun must make zero new provider calls"
    assert len(llm.calls) == llm_calls_after_1, "immediate rerun must make zero new LLM calls"
    session_2_row = next(s for s in client.rows("intel_run_sessions") if s["id"] == session_2)
    assert session_2_row["status"] == SESSION_COMPLETED
    snapshot_2 = next(s for s in client.rows("intel_v3_snapshots") if s["run_session_id"] == session_2)
    assert snapshot_2["id"] != snapshot_1["id"], "rerun must publish its own session-native snapshot"

    status_2 = await control.get_session_status(client=client, user_id=USER, session_id=session_2)
    assert "evidence_summary_line" in status_2
    assert "lanes reused" in status_2["evidence_summary_line"]
    assert "reused" in status_2["evidence_summary_line"].split("Specialist analysis:")[1]


@pytest.mark.asyncio
async def test_one_expired_lane_refreshes_selectively(monkeypatch):
    client = FakeSupabase()
    for t in TICKERS:
        seed_position(client, USER, t)
    recorder = ProviderRecorder()
    patch_providers(monkeypatch, recorder)
    # A second day's news fetch returns genuinely new articles (real
    # providers do too) — this is what should legitimately invalidate the
    # sentiment axis's fingerprint, as opposed to a same-content re-fetch.
    news_call_count = {"n": 0}
    import app.services.intelligence.v3.distributed.collectors_v1 as collectors_mod

    async def _fresh_news(ticker: str, limit: int = 6):
        news_call_count["n"] += 1
        recorder.calls.append(("news", ticker.upper()))
        day = news_call_count["n"]
        return [{
            "headline": f"{ticker.upper()} day {day} update", "source": "test",
            "datetime": datetime.now(timezone.utc).timestamp(),
            "id": f"{ticker.upper()}-day{day}", "related_tickers": [ticker.upper()],
        }]

    monkeypatch.setattr(collectors_mod, "fetch_yfinance_news", _fresh_news)
    llm = FakeLLM()
    supervisor = _make_supervisor(client, llm)

    session_1 = str(uuid.uuid4())
    await control.create_distributed_session(client=client, user_id=USER, session_id=session_1)
    await drive_supervisor_to_completion(supervisor)
    calls_after_1 = list(recorder.calls)
    llm_calls_after_1 = len(llm.calls)

    # Expire only the news_sentiment lane (1h TTL) for every ticker; every
    # other lane's completed_at stays fresh from run 1.
    expired = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    for task in client.rows("intel_run_tasks"):
        if task.get("task_type") == TASK_COLLECT_EVIDENCE_LANE and task.get("lane") == LANE_NEWS_SENTIMENT:
            task["completed_at"] = expired

    session_2 = await _terminate_active_and_start(client)
    await drive_supervisor_to_completion(supervisor)

    new_calls = recorder.calls[len(calls_after_1):]
    assert new_calls, "the expired lane must trigger at least one refresh"
    assert all(fn == "news" for fn, _ in new_calls), (
        f"only the expired news_sentiment lane may refresh, got {new_calls}"
    )
    # Only the sentiment axis (whose supplied evidence changed) may call the
    # LLM again — technical/fundamental axes reuse their prior output.
    new_llm_calls = llm.calls[llm_calls_after_1:]
    assert new_llm_calls, "the sentiment axis must rerun for the refreshed evidence"
    assert all(c["axis"] == "sentiment" for c in new_llm_calls), (
        f"only the sentiment axis may call the LLM again, got {[c['axis'] for c in new_llm_calls]}"
    )
    session_2_row = next(s for s in client.rows("intel_run_sessions") if s["id"] == session_2)
    assert session_2_row["status"] == SESSION_COMPLETED


@pytest.mark.asyncio
async def test_failed_previous_session_reuses_evidence_not_failure_or_snapshot(monkeypatch):
    client = FakeSupabase()
    for t in TICKERS:
        seed_position(client, USER, t)
    recorder = ProviderRecorder()
    patch_providers(monkeypatch, recorder)
    llm = FakeLLM()
    supervisor = _make_supervisor(client, llm)

    session_1 = str(uuid.uuid4())
    await control.create_distributed_session(client=client, user_id=USER, session_id=session_1)
    await drive_supervisor_to_completion(supervisor)
    calls_after_1 = len(recorder.calls)
    # Simulate a session that ultimately failed despite collecting valid
    # evidence (e.g. a publication-stage failure) — no NO_CALL/failure state
    # should be inheritable by the next session.
    for row in client.rows("intel_run_sessions"):
        if row["id"] == session_1:
            row["status"] = "failed"

    session_2 = str(uuid.uuid4())
    await control.create_distributed_session(client=client, user_id=USER, session_id=session_2)
    await drive_supervisor_to_completion(supervisor)

    assert len(recorder.calls) == calls_after_1, "valid lane evidence from the failed session must be reused"
    session_2_row = next(s for s in client.rows("intel_run_sessions") if s["id"] == session_2)
    assert session_2_row["status"] == SESSION_COMPLETED
    snapshot_2 = next(s for s in client.rows("intel_v3_snapshots") if s["run_session_id"] == session_2)
    assert snapshot_2["run_session_id"] != session_1
