"""Final Run Intel operational-reliability patch, item 6: literal cost/reuse
metrics. ``cache_hits``/``lanes_refreshed`` must count only a SUCCESSFUL
evidence-lane adoption/collection — never the session-level portfolio-context
DB read, and never a degraded/no-data/failed-retryable attempt that produced
no usable evidence. ``provider_calls`` stays unconditional (actual attempts).
"""
from __future__ import annotations

import uuid

import pytest

from app.services.intelligence.v3.distributed.collectors_v1 import CollectorResult
from app.services.intelligence.v3.distributed.task_contracts_v1 import (
    LANE_FUNDAMENTALS,
    TASK_COLLECT_EVIDENCE_LANE,
    TASK_COLLECT_MACRO_CONTEXT,
    TASK_COLLECT_PORTFOLIO_CONTEXT,
    TASK_DEGRADED,
    TASK_SUCCEEDED,
)
from app.services.intelligence.v3.distributed.run_task_store_v1 import (
    TASK_FAILED_RETRYABLE,
)
from app.services.intelligence.v3.distributed import session_control_v1 as control
from app.services.intelligence.v3.distributed.task_contracts_v1 import AXIS_REVIEW
from app.services.intelligence.v3.distributed.worker_supervisor_v1 import (
    WorkerSupervisor,
)
from tests.distributed_run_intel_test_utils import (
    FakeLLM,
    FakeSupabase,
    ProviderRecorder,
    claim_task_row,
    drive_supervisor_to_completion,
    make_settings,
    patch_providers,
    seed_position,
)

USER = str(uuid.uuid4())


def _seed_task(client: FakeSupabase, *, task_type: str, **overrides) -> dict:
    session_id = str(uuid.uuid4())
    client.table("intel_run_sessions").insert(
        {"id": session_id, "user_id": USER, "status": "running", "metrics": {}}
    ).execute()
    row = {
        "id": str(uuid.uuid4()), "run_session_id": session_id,
        "user_id": USER, "task_type": task_type, "ticker": "AAPL",
        "lane": LANE_FUNDAMENTALS, "asset_type": "equity",
        "state": "pending", "attempts": 0, "max_attempts": 3,
    }
    row.update(overrides)
    client.table("intel_run_tasks").insert(row).execute()
    return claim_task_row(client, row)


async def _run(monkeypatch, client, task, result: CollectorResult) -> WorkerSupervisor:
    import app.services.intelligence.v3.distributed.worker_supervisor_v1 as supervisor_mod

    async def _fake_execute_collector_task(_client, *, task, settings):
        return result

    monkeypatch.setattr(
        supervisor_mod, "execute_collector_task", _fake_execute_collector_task,
    )
    supervisor = WorkerSupervisor(client=client, settings=make_settings())
    await supervisor._execute_one(task, {})
    return supervisor


class TestLanesRefreshedReconciliation:
    @pytest.mark.asyncio
    async def test_successful_cache_hit_counts_as_cache_hit_not_refreshed(
        self, monkeypatch,
    ):
        client = FakeSupabase()
        task = _seed_task(client, task_type=TASK_COLLECT_EVIDENCE_LANE)
        supervisor = await _run(
            monkeypatch, client, task,
            CollectorResult(TASK_SUCCEEDED, output={"pe": 1.0}, cache_hit=True),
        )
        buffer = supervisor.metrics_buffer[task["run_session_id"]]
        assert buffer.get("cache_hits") == 1
        assert buffer.get("lanes_refreshed", 0) == 0

    @pytest.mark.asyncio
    async def test_successful_fresh_collection_counts_as_refreshed_not_cache_hit(
        self, monkeypatch,
    ):
        client = FakeSupabase()
        task = _seed_task(client, task_type=TASK_COLLECT_EVIDENCE_LANE)
        supervisor = await _run(
            monkeypatch, client, task,
            CollectorResult(
                TASK_SUCCEEDED, output={"pe": 1.0}, cache_hit=False, provider_calls=1,
            ),
        )
        buffer = supervisor.metrics_buffer[task["run_session_id"]]
        assert buffer.get("lanes_refreshed") == 1
        assert buffer.get("cache_hits", 0) == 0
        assert buffer.get("provider_calls") == 1

    @pytest.mark.asyncio
    async def test_degraded_no_data_lane_appears_in_neither_total(self, monkeypatch):
        client = FakeSupabase()
        task = _seed_task(client, task_type=TASK_COLLECT_EVIDENCE_LANE)
        supervisor = await _run(
            monkeypatch, client, task,
            CollectorResult(
                TASK_DEGRADED, output={"no_data": True}, cache_hit=False, provider_calls=1,
            ),
        )
        buffer = supervisor.metrics_buffer[task["run_session_id"]]
        assert buffer.get("lanes_refreshed", 0) == 0
        assert buffer.get("cache_hits", 0) == 0
        # provider_calls is unconditional — a real attempt was made.
        assert buffer.get("provider_calls") == 1

    @pytest.mark.asyncio
    async def test_failed_retryable_lane_appears_in_neither_total(self, monkeypatch):
        client = FakeSupabase()
        task = _seed_task(client, task_type=TASK_COLLECT_EVIDENCE_LANE)
        supervisor = await _run(
            monkeypatch, client, task,
            CollectorResult(TASK_FAILED_RETRYABLE, cache_hit=False, provider_calls=0),
        )
        buffer = supervisor.metrics_buffer[task["run_session_id"]]
        assert buffer.get("lanes_refreshed", 0) == 0
        assert buffer.get("cache_hits", 0) == 0
        assert buffer.get("provider_calls", 0) == 0

    @pytest.mark.asyncio
    async def test_portfolio_context_db_read_never_counts_as_a_lane(self, monkeypatch):
        client = FakeSupabase()
        task = _seed_task(
            client, task_type=TASK_COLLECT_PORTFOLIO_CONTEXT, lane=None,
        )
        supervisor = await _run(
            monkeypatch, client, task,
            CollectorResult(TASK_SUCCEEDED, output={"holding_count": 3}),
        )
        buffer = supervisor.metrics_buffer[task["run_session_id"]]
        assert buffer.get("lanes_refreshed", 0) == 0
        assert buffer.get("cache_hits", 0) == 0
        assert buffer.get("provider_calls", 0) == 0

    @pytest.mark.asyncio
    async def test_macro_reuse_hit_counts_as_cache_hit_consistent_with_lanes(
        self, monkeypatch,
    ):
        client = FakeSupabase()
        task = _seed_task(
            client, task_type=TASK_COLLECT_MACRO_CONTEXT, lane=None,
        )
        supervisor = await _run(
            monkeypatch, client, task,
            CollectorResult(TASK_SUCCEEDED, output={"artifact_id": "a1"}, cache_hit=True),
        )
        buffer = supervisor.metrics_buffer[task["run_session_id"]]
        assert buffer.get("cache_hits") == 1
        assert buffer.get("lanes_refreshed", 0) == 0

    @pytest.mark.asyncio
    async def test_macro_fresh_fetch_counts_as_refreshed_consistent_with_lanes(
        self, monkeypatch,
    ):
        client = FakeSupabase()
        task = _seed_task(
            client, task_type=TASK_COLLECT_MACRO_CONTEXT, lane=None,
        )
        supervisor = await _run(
            monkeypatch, client, task,
            CollectorResult(
                TASK_SUCCEEDED, output={"artifact_id": "a1"},
                cache_hit=False, provider_calls=1,
            ),
        )
        buffer = supervisor.metrics_buffer[task["run_session_id"]]
        assert buffer.get("lanes_refreshed") == 1
        assert buffer.get("cache_hits", 0) == 0


class TestMetricsReconcileWithPersistedRows:
    """End-to-end proof (real scheduler/collectors/specialists/supervisor):
    persisted ``intel_run_sessions.metrics`` literally reconcile with the
    durable rows they claim to summarize — never a plausible-looking but
    unverified number."""

    @pytest.mark.asyncio
    async def test_llm_calls_plus_reused_reconciles_with_persisted_non_review_rows(
        self, monkeypatch,
    ):
        client = FakeSupabase()
        seed_position(client, USER, "AAPL")
        recorder = ProviderRecorder()
        patch_providers(monkeypatch, recorder)
        llm = FakeLLM()
        supervisor = WorkerSupervisor(
            client=client, settings=make_settings(), llm=llm, worker_id="test-worker",
        )
        session_id = str(uuid.uuid4())
        await control.create_distributed_session(
            client=client, user_id=USER, session_id=session_id,
        )
        await drive_supervisor_to_completion(supervisor)

        session_row = next(
            s for s in client.rows("intel_run_sessions") if s["id"] == session_id
        )
        metrics = session_row.get("metrics") or {}
        non_review_rows = [
            o for o in client.rows("intel_run_specialist_outputs")
            if o["run_session_id"] == session_id and o["axis"] != AXIS_REVIEW
        ]
        assert metrics.get("llm_calls", 0) + metrics.get("llm_reused", 0) == len(
            non_review_rows
        )
        # A first-ever session makes zero reuse and only real calls.
        assert metrics.get("llm_reused", 0) == 0
        assert metrics.get("llm_calls", 0) == len(non_review_rows)

    @pytest.mark.asyncio
    async def test_refreshed_plus_cache_hits_reconciles_with_successful_lane_tasks(
        self, monkeypatch,
    ):
        client = FakeSupabase()
        seed_position(client, USER, "AAPL")
        recorder = ProviderRecorder()
        patch_providers(monkeypatch, recorder)
        llm = FakeLLM()
        supervisor = WorkerSupervisor(
            client=client, settings=make_settings(), llm=llm, worker_id="test-worker",
        )
        session_id = str(uuid.uuid4())
        await control.create_distributed_session(
            client=client, user_id=USER, session_id=session_id,
        )
        await drive_supervisor_to_completion(supervisor)

        session_row = next(
            s for s in client.rows("intel_run_sessions") if s["id"] == session_id
        )
        metrics = session_row.get("metrics") or {}
        successful_lane_tasks = [
            t for t in client.rows("intel_run_tasks")
            if t["run_session_id"] == session_id
            and t["task_type"] == TASK_COLLECT_EVIDENCE_LANE
            and t["state"] == TASK_SUCCEEDED
        ]
        assert metrics.get("cache_hits", 0) + metrics.get("lanes_refreshed", 0) == len(
            successful_lane_tasks
        )
        # A first-ever session has nothing to reuse — every successful lane
        # is a fresh collection.
        assert metrics.get("cache_hits", 0) == 0
        assert metrics.get("lanes_refreshed", 0) == len(successful_lane_tasks)
