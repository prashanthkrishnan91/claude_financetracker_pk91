"""Supervisor survives database outages — outage ≠ idle.

Proves (completion item 4):
  1. the active-session query failing for several passes does NOT exit the
     supervisor;
  2. when the query recovers with an active session, tasks resume WITHOUT any
     browser polling or another POST;
  3. a successful query returning zero sessions still exits cleanly;
  4. provider and LLM calls remain zero during the outage;
  5. process restart recovers expired leases and continues the same session
     (already covered by the golden-run restart test; re-proven here through
     the outage-shaped supervisor).
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

import app.services.intelligence.v3.distributed.worker_supervisor_v1 as sup_module
from app.services.intelligence.v3.distributed import session_control_v1 as control
from app.services.intelligence.v3.distributed.worker_supervisor_v1 import (
    ActiveSessionQueryFailed,
    WorkerSupervisor,
    list_active_distributed_sessions,
)
from tests.distributed_run_intel_test_utils import (
    FakeLLM,
    FakeQuery,
    FakeSupabase,
    ProviderRecorder,
    make_settings,
    patch_providers,
    seed_position,
)

USER = str(uuid.uuid4())


class OutageClient(FakeSupabase):
    """Fails every intel_run_sessions SELECT while the outage is armed."""

    def __init__(self):
        super().__init__()
        self.outage = False
        self.failed_queries = 0

    def table(self, name):
        outer = self

        class _Query(FakeQuery):
            def execute(self):
                if (
                    outer.outage
                    and self._table == "intel_run_sessions"
                    and self._op == "select"
                ):
                    outer.failed_queries += 1
                    raise ConnectionError("simulated database outage")
                return super().execute()

        return _Query(self.store, name)


def _fast_backoff(monkeypatch):
    monkeypatch.setattr(sup_module, "_OUTAGE_BACKOFF_BASE_SECONDS", 0.01)
    monkeypatch.setattr(sup_module, "_OUTAGE_BACKOFF_MAX_SECONDS", 0.02)
    monkeypatch.setattr(sup_module, "_IDLE_SLEEP_SECONDS", 0.01)
    monkeypatch.setattr(sup_module, "_BUSY_SLEEP_SECONDS", 0.01)


class TestDiscoveryOutcomes:
    def test_query_failure_raises_distinct_outcome(self):
        client = OutageClient()
        client.outage = True
        with pytest.raises(ActiveSessionQueryFailed):
            list_active_distributed_sessions(client)

    def test_zero_sessions_is_a_successful_empty_result(self):
        client = OutageClient()
        assert list_active_distributed_sessions(client) == []


class TestOutageLoop:
    @pytest.mark.asyncio
    async def test_outage_then_recovery_resumes_without_browser(
        self, monkeypatch
    ):
        """Query fails for several passes → supervisor retained → recovery
        sees the active session → the run completes to a published snapshot
        with ZERO browser participation and ZERO provider/LLM work during
        the outage."""
        _fast_backoff(monkeypatch)
        client = OutageClient()
        seed_position(client, USER, "AAPL")
        session_id = str(uuid.uuid4())
        await control.create_distributed_session(
            client=client, user_id=USER, session_id=session_id,
        )
        recorder = ProviderRecorder()
        patch_providers(monkeypatch, recorder)
        llm = FakeLLM()
        supervisor = WorkerSupervisor(
            client=client, settings=make_settings(), llm=llm,
            worker_id="outage-worker",
        )

        client.outage = True
        loop_task = asyncio.create_task(supervisor.run_until_idle())
        # Let several failing passes elapse.
        for _ in range(200):
            await asyncio.sleep(0.005)
            if client.failed_queries >= 4:
                break
        assert client.failed_queries >= 4, "outage passes did not occur"
        assert not loop_task.done(), (
            "supervisor exited during the outage — outage was treated as idle"
        )
        # Zero provider/LLM work during the outage.
        assert recorder.calls == []
        assert llm.calls == []

        # Recovery: the SAME loop discovers the session and finishes the run.
        client.outage = False
        for _ in range(2000):
            await asyncio.sleep(0.01)
            if loop_task.done():
                break
        assert loop_task.done(), "supervisor did not converge after recovery"
        session = client.rows("intel_run_sessions")[0]
        assert session["status"] in ("completed", "completed_with_gaps")
        snapshots = [
            s for s in client.rows("intel_v3_snapshots")
            if s.get("run_session_id") == session_id
        ]
        assert len(snapshots) == 1
        # Tasks resumed with zero browser polling / zero POSTs: the only
        # actors in this test were the supervisor loop and the database.

    @pytest.mark.asyncio
    async def test_successful_zero_session_query_still_exits(self, monkeypatch):
        _fast_backoff(monkeypatch)
        client = OutageClient()
        supervisor = WorkerSupervisor(
            client=client, settings=make_settings(), llm=FakeLLM(),
            worker_id="idle-worker",
        )
        await asyncio.wait_for(supervisor.run_until_idle(), timeout=5.0)

    @pytest.mark.asyncio
    async def test_outage_at_startup_probe_survives_then_exits_when_idle(
        self, monkeypatch
    ):
        """Startup probe: initial DB failure does not kill recovery — the
        probe stays alive, and once the database answers (zero sessions) it
        exits cleanly."""
        _fast_backoff(monkeypatch)
        sup_module._reset_supervisor_for_testing()
        client = OutageClient()
        client.outage = True
        started = await sup_module.recover_active_sessions_on_startup(client)
        assert started is True
        probe = sup_module._SUPERVISOR_TASK
        assert probe is not None
        await asyncio.sleep(0.1)
        assert not probe.done(), "probe died during initial DB outage"
        client.outage = False
        for _ in range(200):
            await asyncio.sleep(0.01)
            if probe.done():
                break
        assert probe.done(), "probe did not exit after successful idle query"
        sup_module._reset_supervisor_for_testing()

    @pytest.mark.asyncio
    async def test_restart_recovers_expired_leases_same_session(
        self, monkeypatch
    ):
        _fast_backoff(monkeypatch)
        import app.services.intelligence.v3.distributed.run_task_store_v1 as store

        client = OutageClient()
        seed_position(client, USER, "AAPL")
        session_id = str(uuid.uuid4())
        await control.create_distributed_session(
            client=client, user_id=USER, session_id=session_id,
        )
        # Process 1 claims work and dies (leases expire immediately).
        dead = store.claim_tasks(
            client, worker_id="dead-process", limit=10, lease_seconds=0,
        )
        assert dead
        recorder = ProviderRecorder()
        patch_providers(monkeypatch, recorder)
        # Process 2 (restart): same durable state, new supervisor.
        supervisor = WorkerSupervisor(
            client=client, settings=make_settings(), llm=FakeLLM(),
            worker_id="restarted-process",
        )
        await asyncio.wait_for(supervisor.run_until_idle(), timeout=30.0)
        session = client.rows("intel_run_sessions")[0]
        assert session["status"] in ("completed", "completed_with_gaps")
