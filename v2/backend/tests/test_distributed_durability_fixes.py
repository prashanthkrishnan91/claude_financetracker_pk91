"""Durability fixes from the adversarial review.

D1 — a worker crash on the FINAL claim attempt (task stuck 'claimed' with an
expired lease and zero attempts remaining) must terminalize instead of
wedging the session forever.
D2 — a create that crashed between the session insert and the scope freeze
(zombie 'created' session) must be repaired on the next click and
terminalized by the scheduler if nobody re-clicks.
D4 — the bundle fingerprint must be stable across runs when the analytical
substance is unchanged (timestamps / intraday price / mark-to-market noise
must not invalidate LLM reuse).
D5 — losing the two-tab create race adopts the winner's session instead of
returning a misleading migration error.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

import app.services.intelligence.v3.distributed.run_task_store_v1 as store
from app.services.intelligence.v3.distributed import run_scheduler_v1 as scheduler
from app.services.intelligence.v3.distributed import session_control_v1 as control
from app.services.intelligence.v3.distributed.evidence_bundle_v1 import (
    _fingerprint_source,
)
from app.services.intelligence.v3.distributed.task_contracts_v1 import (
    stable_fingerprint,
)
from app.services.intelligence.v3.distributed.worker_supervisor_v1 import (
    WorkerSupervisor,
)
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


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TestD1ExhaustedClaimSweep:
    @pytest.mark.asyncio
    async def test_final_attempt_crash_is_swept_terminal(self):
        client = FakeSupabase()
        seed_position(client, USER, "AAPL")
        session_id = str(uuid.uuid4())
        await control.create_distributed_session(
            client=client, user_id=USER, session_id=session_id,
        )
        task = store.claim_tasks(client, worker_id="doomed", limit=1)[0]
        # Simulate: worker died holding the FINAL attempt, lease expired.
        expired = (_now() - timedelta(seconds=10)).isoformat()
        client.table("intel_run_tasks").update({
            "attempts": task["max_attempts"],
            "lease_expires_at": expired,
        }).eq("id", task["id"]).execute()

        # Not claimable (attempts exhausted) — the pre-fix wedge condition.
        assert all(
            t["id"] != task["id"]
            for t in store.claim_tasks(client, worker_id="w2", limit=100)
        )
        swept = store.sweep_exhausted_expired_claims(client)
        assert swept == 1
        row = next(
            t for t in client.rows("intel_run_tasks") if t["id"] == task["id"]
        )
        assert row["state"] == "failed"
        assert row["error_code"] == "lease_expired_attempts_exhausted"

    @pytest.mark.asyncio
    async def test_sweep_never_touches_live_leases(self):
        client = FakeSupabase()
        seed_position(client, USER, "AAPL")
        session_id = str(uuid.uuid4())
        await control.create_distributed_session(
            client=client, user_id=USER, session_id=session_id,
        )
        task = store.claim_tasks(
            client, worker_id="alive", limit=1, lease_seconds=300,
        )[0]
        client.table("intel_run_tasks").update({
            "attempts": task["max_attempts"],
        }).eq("id", task["id"]).execute()
        assert store.sweep_exhausted_expired_claims(client) == 0
        row = next(
            t for t in client.rows("intel_run_tasks") if t["id"] == task["id"]
        )
        assert row["state"] == "claimed"

    @pytest.mark.asyncio
    async def test_session_with_final_attempt_crash_still_terminates(
        self, monkeypatch
    ):
        """End-to-end: the supervisor sweeps the wedged task and the session
        reaches a terminal state instead of running forever."""
        client = FakeSupabase()
        for ticker in ("AAPL", "MSFT"):
            seed_position(client, USER, ticker)
        recorder = ProviderRecorder()
        patch_providers(monkeypatch, recorder)
        session_id = str(uuid.uuid4())
        await control.create_distributed_session(
            client=client, user_id=USER, session_id=session_id,
        )
        # Wedge AAPL's fundamentals lane on its final attempt.
        target = next(
            t for t in client.rows("intel_run_tasks")
            if t.get("ticker") == "AAPL" and t.get("lane") == "fundamentals"
        )
        expired = (_now() - timedelta(seconds=10)).isoformat()
        client.table("intel_run_tasks").update({
            "state": "claimed", "claim_owner": "dead",
            "attempts": target["max_attempts"],
            "lease_expires_at": expired,
        }).eq("id", target["id"]).execute()

        supervisor = WorkerSupervisor(
            client=client,
            settings=make_settings(
                intel_v3_distributed_max_collector_concurrency=50,
            ),
            llm=FakeLLM(),
            worker_id="rescuer",
        )
        await drive_supervisor_to_completion(supervisor)
        session = client.rows("intel_run_sessions")[0]
        assert session["status"] in (
            "completed", "completed_with_gaps", "failed",
        )
        unfinished = [
            t for t in client.rows("intel_run_tasks")
            if t["state"] not in ("succeeded", "degraded", "failed", "cancelled")
        ]
        assert unfinished == []


class TestD2ZombieCreatedSession:
    def _zombie(self, client: FakeSupabase, *, age_seconds: int = 300) -> str:
        session_id = str(uuid.uuid4())
        stamp = (_now() - timedelta(seconds=age_seconds)).isoformat()
        client.table("intel_run_sessions").insert({
            "id": session_id, "user_id": USER, "status": "created",
            "workflow_version": 2, "current_stage": "preparing",
            "holdings_scope": [], "stale_tickers": [],
            "expected_ticker_job_count": 0, "metrics": {},
            "created_at": stamp, "updated_at": stamp,
        }).execute()
        return session_id

    @pytest.mark.asyncio
    async def test_same_id_retry_fails_zombie_with_no_frozen_scope(self):
        """A zombie session with ZERO frozen ticker rows means the crash
        happened before scope freeze ever persisted — repair must fail it
        rather than reconstructing scope from the CURRENT portfolio (which
        may have changed since). The user must click Run Intel again for a
        fresh preflight and a new session."""
        client = FakeSupabase()
        seed_position(client, USER, "AAPL")
        zombie_id = self._zombie(client)
        result = await control.create_distributed_session(
            client=client, user_id=USER, session_id=zombie_id,
        )
        assert result["session_status"] == "failed"
        assert client.rows("intel_run_tickers") == []

    @pytest.mark.asyncio
    async def test_new_click_adopting_zombie_fails_it_not_reconstructs(self):
        client = FakeSupabase()
        seed_position(client, USER, "AAPL")
        zombie_id = self._zombie(client)
        result = await control.create_distributed_session(
            client=client, user_id=USER, session_id=str(uuid.uuid4()),
        )
        assert result["run_session_id"] == zombie_id
        assert result["adopted_active_session"] is True
        assert result["session_status"] == "failed"
        assert client.rows("intel_run_tickers") == []

    @pytest.mark.asyncio
    async def test_supervisor_fails_zombie_without_browser_traffic(
        self, monkeypatch
    ):
        """A crashed create with no frozen scope is failed by the
        SUPERVISOR pass alone — no click, no poll, no POST required — and
        is never repaired by reconstructing from current portfolio state."""
        from tests.distributed_run_intel_test_utils import (
            ProviderRecorder, patch_providers,
        )

        client = FakeSupabase()
        seed_position(client, USER, "AAPL")
        self._zombie(client, age_seconds=600)
        patch_providers(monkeypatch, ProviderRecorder())
        supervisor = WorkerSupervisor(
            client=client, settings=make_settings(), llm=FakeLLM(),
            worker_id="repairer",
        )
        await supervisor.run_pass()
        session = client.rows("intel_run_sessions")[0]
        assert session["status"] == "failed"
        assert client.rows("intel_run_tickers") == []

    @pytest.mark.asyncio
    async def test_repair_terminalizes_session_with_no_scope(self):
        """When no frozen scope was ever persisted, repair terminalizes
        honestly instead of looping forever OR reconstructing a scope from
        current (possibly nonexistent, possibly changed) portfolio data."""
        client = FakeSupabase()
        self._zombie(client, age_seconds=600)
        supervisor = WorkerSupervisor(
            client=client, settings=make_settings(), llm=FakeLLM(),
            worker_id="repairer",
        )
        await supervisor.run_pass()
        session = client.rows("intel_run_sessions")[0]
        assert session["status"] == "failed"
        assert "scope_freeze_incomplete_restart_required" in str(session.get("last_error"))


class TestD4FingerprintStability:
    def _bundle(self, *, price: float, as_of: str, weight: float) -> dict:
        return {
            "run_session_id": str(uuid.uuid4()),
            "ticker": "AAPL", "asset_type": "equity", "as_of": as_of,
            "portfolio_context": {
                "portfolio_weight_pct": weight, "market_value": price * 10,
                "prior_action": "HOLD", "tax_summary": {"lt_eligible": True},
                "portfolio": {"cash_available": price},
            },
            "market": {"price": price, "as_of": as_of},
            "technical": {"pct_30d": 3.4, "sma20": 100.0, "as_of": as_of},
            "fundamental": {"pe": 21.0, "as_of": as_of},
            "valuation": {"pe": 21.0},
            "sentiment": {"items": [], "as_of": as_of},
            "sec": {}, "catalysts": [], "macro": None, "asset_specific": {},
            "source_refs": [], "usable_lanes": ["price", "technicals"],
            "missing_lanes": [], "degraded_lanes": [],
            "required_lanes_missing": [], "quality": {},
        }

    def test_intraday_noise_does_not_change_fingerprint(self):
        first = self._bundle(price=100.0, as_of="2026-07-21T10:00:00+00:00",
                             weight=5.2)
        second = self._bundle(price=100.7, as_of="2026-07-22T15:30:00+00:00",
                              weight=5.4)
        assert stable_fingerprint(_fingerprint_source(first)) == \
            stable_fingerprint(_fingerprint_source(second))

    def test_analytical_change_does_change_fingerprint(self):
        base = self._bundle(price=100.0, as_of="t", weight=5.2)
        changed = self._bundle(price=100.0, as_of="t", weight=5.2)
        changed["fundamental"]["pe"] = 35.0
        assert stable_fingerprint(_fingerprint_source(base)) != \
            stable_fingerprint(_fingerprint_source(changed))
        reweighted = self._bundle(price=100.0, as_of="t", weight=12.0)
        assert stable_fingerprint(_fingerprint_source(base)) != \
            stable_fingerprint(_fingerprint_source(reweighted))


class TestD5RaceLoserAdoptsWinner:
    @pytest.mark.asyncio
    async def test_active_per_user_race_loser_adopts(self, monkeypatch):
        client = FakeSupabase()
        seed_position(client, USER, "AAPL")
        winner_id = str(uuid.uuid4())
        await control.create_distributed_session(
            client=client, user_id=USER, session_id=winner_id,
        )
        # Simulate the loser tab passing the pre-check simultaneously: the
        # FIRST find_active_session call (pre-check) sees nothing, the insert
        # then hits uq_intel_run_sessions_active_per_user, and the recovery
        # path's SECOND call sees the winner and adopts it.
        real_find = control.find_active_session
        call_count = {"n": 0}

        async def _first_none_then_real(*, client, user_id):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return None
            return await real_find(client=client, user_id=user_id)

        monkeypatch.setattr(
            control, "find_active_session", _first_none_then_real,
        )
        result = await control.create_distributed_session(
            client=client, user_id=USER, session_id=str(uuid.uuid4()),
        )
        assert result["run_session_id"] == winner_id
        assert result["adopted_active_session"] is True
