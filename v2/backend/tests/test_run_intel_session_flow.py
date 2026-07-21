"""Durable Run Intel sessions — flow-level tests over the REAL queue + worker.

These tests drive ``run_intel_session_request`` (the production request path
behind POST /intel/v3/run) against an in-memory Supabase fake that emulates
the migration-026 unique indexes. The REAL job store (enqueue/claim/count/
mark), the REAL AnalystRefreshWorker, and the REAL bounded on-demand drain
run unmodified; only the innermost analyst adapter (the LLM boundary) and the
deterministic prewarm build are substituted — the adapter substitute still
writes durable evidence rows and reports per-ticker success exactly like the
production adapter contract.

Every test also patches ``AgentOrchestrator._run_portfolio_synthesis`` (and
``AgentOrchestrator.run``) to raise immediately, so ANY code path that
reached portfolio synthesis or the full pipeline would fail the test.

Covers mission suites:
  4. session isolation (old snapshots / old jobs / cross-session / cross-user
     / same-day second click)
  5. interruption + resume with 16 tickers
  6. publication-only retry
  7. exact accounting for 32 stale tickers
  plus completion-truth and queue-only / writes-disabled honesty.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

import app.services.intelligence.v3.analyst_refresh_worker_v1 as worker_mod
import app.services.intelligence.v3.intel_run_session_flow_v1 as flow_mod
import app.services.intelligence.v3.intel_v3_fast_freshness_gate_v1 as gate_mod
import app.services.intelligence.v3.intel_v3_service as svc_mod
import app.services.intelligence.v3.watchtower_intel_republisher_v1 as repub_mod
from app.services.agents.orchestrator import AgentOrchestrator
from app.services.intelligence.v3.analyst_refresh_job_store_v1 import (
    JOB_CLAIMED,
    JOB_PENDING,
    JOB_SUCCEEDED,
    claim_due_jobs,
)
from app.services.intelligence.v3.intel_run_session_flow_v1 import (
    ACTION_COMPLETE,
    ACTION_CONTINUE,
    ACTION_QUEUE_ONLY,
    ACTION_RETRY_NEW_CLICK,
    ACTION_WRITES_DISABLED,
    SessionOwnershipError,
    run_intel_session_request,
)
from app.services.intelligence.v3.intel_run_session_store_v1 import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PUBLICATION_RETRY,
    STATUS_TICKER_REFRESH,
)
from app.services.intelligence.v3.intel_v3_service import IntelV3Service

from tests.run_intel_session_test_utils import (
    FakeSupabase,
    RecordingAnalystAdapter,
    UniqueViolation,
    seed_positions,
    write_ticker_evidence,
)

USER_A = "00000000-0000-0000-0000-0000000000aa"
USER_B = "00000000-0000-0000-0000-0000000000bb"

# Mirrors the frontend continuation ceiling (advisor-readiness.ts
# RUN_INTEL_MAX_CONTINUATIONS) — the honest bound a single click gets.
FRONTEND_MAX_CONTINUATIONS = 20


def _sid() -> str:
    return str(uuid.uuid4())


class _Env:
    def __init__(self, client, service, analyst_calls, publish_calls, request):
        self.client = client
        self.service = service
        self.analyst_calls = analyst_calls
        self.publish_calls = publish_calls
        self.request = request

    def session_rows(self):
        return self.client.rows("intel_run_sessions")

    def job_rows(self):
        return self.client.rows("analyst_refresh_jobs")

    def snapshot_rows(self):
        return self.client.rows("intel_v3_snapshots")

    def session_jobs(self, session_id):
        return [
            j for j in self.job_rows()
            if str(j.get("run_session_id") or "") == session_id
        ]

    async def run_to_completion(self, session_id, max_requests=25):
        """Drive bounded requests like the frontend's auto-continuation."""
        last = None
        for _ in range(max_requests):
            last = await self.request(session_id)
            if last["session_status"] in (STATUS_COMPLETED, STATUS_FAILED):
                return last
            if not last["retryable"]:
                return last
        return last


def make_env(
    monkeypatch,
    *,
    user_id: str = USER_A,
    tickers: Optional[list[str]] = None,
    stale_all: bool = True,
    on_demand: bool = True,
    writes_enabled: bool = True,
    publish_fail_times: int = 0,
    fail_tickers: Optional[set[str]] = None,
) -> _Env:
    client = FakeSupabase()
    tickers = tickers if tickers is not None else ["AAPL", "MSFT", "NVDA"]
    seed_positions(client, user_id, tickers)

    settings = SimpleNamespace(
        intel_v3_on_demand_refresh_enabled=on_demand,
        intel_v3_snapshot_writes_enabled=writes_enabled,
    )
    monkeypatch.setattr(flow_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(svc_mod, "get_settings", lambda: settings)

    # Any reach into the full pipeline or synthesis fails the test instantly.
    def _forbidden_synthesis(self, *a, **k):
        raise AssertionError(
            "portfolio synthesis must NEVER run on the Run Intel session path"
        )

    def _forbidden_full_run(self, *a, **k):
        raise AssertionError(
            "AgentOrchestrator.run() (full pipeline) must NEVER run on the "
            "Run Intel session path"
        )

    monkeypatch.setattr(
        AgentOrchestrator, "_run_portfolio_synthesis", _forbidden_synthesis,
    )
    monkeypatch.setattr(AgentOrchestrator, "run", _forbidden_full_run)

    # Freshness gate: stale_all → gate failure falls back to all holdings;
    # otherwise a gate result with zero stale analyst records.
    if stale_all:
        async def _gate(*a, **k):
            raise RuntimeError("gate unavailable in test — stale-all fallback")
    else:
        async def _gate(*a, **k):
            return SimpleNamespace(evidence_records=[])
    monkeypatch.setattr(gate_mod, "run_fast_freshness_gate", _gate)

    # Evidence lanes are out of scope here.
    monkeypatch.setattr(flow_mod, "_dispatch_evidence_lanes_safe", lambda *a, **k: None)

    # Analyst boundary: production-shaped recording adapter behind the REAL
    # worker. One adapter per user id so cross-user tests stay isolated.
    analyst_calls: list[str] = []
    adapters: dict[str, RecordingAnalystAdapter] = {}

    def _adapter_factory(uid):
        key = str(uid)
        if key not in adapters:
            adapters[key] = RecordingAnalystAdapter(
                client, key,
                fail_tickers=fail_tickers,
                analyst_calls=analyst_calls,
            )
        return adapters[key]

    monkeypatch.setattr(worker_mod, "_default_adapter_factory", _adapter_factory)

    # Deterministic certification + publication substitute: uses the REAL
    # _persist_snapshot (including its session idempotency + unique-index
    # behavior); certification is assumed to pass unless the test injects
    # failures via publish_fail_times.
    publish_calls = {"count": 0, "analyst_calls_at_publish": []}

    async def fake_prewarm(
        self,
        *,
        prewarm_run_id: str,
        skip_persist_on_fail: bool = False,
        run_session_id: Optional[str] = None,
        scope_tickers: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        publish_calls["count"] += 1
        publish_calls["analyst_calls_at_publish"].append(len(analyst_calls))
        if publish_calls["count"] <= publish_fail_times:
            raise RuntimeError("simulated publication failure")
        payload: dict[str, Any] = {
            "snapshot_id": prewarm_run_id,
            "schema_version": "v3.1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "snapshot_source": "worker_certified",
            "scope_ticker_count": len(scope_tickers or []),
        }
        if run_session_id is not None:
            payload["run_session_id"] = str(run_session_id)
        row_id = await self._persist_snapshot(
            run_id=prewarm_run_id,
            payload=payload,
            run_session_id=run_session_id,
        )
        if row_id is not None:
            payload["snapshot_row_id"] = row_id
        return payload

    monkeypatch.setattr(IntelV3Service, "run_prewarm_snapshot", fake_prewarm)
    monkeypatch.setattr(
        repub_mod,
        "get_evidence_freshness_state",
        AsyncMock(return_value="certified_current"),
    )

    def _service_for(uid: str) -> IntelV3Service:
        service = IntelV3Service.__new__(IntelV3Service)
        service.user_id = UUID(uid)
        service.client = client
        return service

    async def request(session_id: str, as_user: str = user_id):
        return await run_intel_session_request(
            user_id=as_user,
            run_session_id=session_id,
            service=_service_for(as_user),
        )

    return _Env(client, _service_for(user_id), analyst_calls, publish_calls, request)


# ═══ Creation + durable ownership ═════════════════════════════════════════════


class TestSessionCreation:
    @pytest.mark.asyncio
    async def test_first_request_creates_session_scope_and_jobs(self, monkeypatch):
        env = make_env(monkeypatch, tickers=["AAPL", "MSFT", "NVDA"])
        sid = _sid()
        result = await env.request(sid)

        sessions = env.session_rows()
        assert len(sessions) == 1
        session = sessions[0]
        assert session["id"] == sid
        assert session["user_id"] == USER_A
        assert sorted(session["holdings_scope"]) == ["AAPL", "MSFT", "NVDA"]
        assert sorted(session["stale_tickers"]) == ["AAPL", "MSFT", "NVDA"]
        assert session["expected_ticker_job_count"] == 3

        jobs = env.session_jobs(sid)
        assert len(jobs) == 3
        assert {j["ticker"] for j in jobs} == {"AAPL", "MSFT", "NVDA"}
        # Every session job carries the exact session id — no NULLs, no
        # sentinel/fake control tickers anywhere.
        assert all(j["run_session_id"] == sid for j in jobs)
        real = {"AAPL", "MSFT", "NVDA"}
        assert all(j["ticker"] in real for j in env.job_rows())

        assert result["run_session_id"] == sid
        assert result["expected_ticker_count"] == 3

    @pytest.mark.asyncio
    async def test_retry_of_first_request_never_duplicates_session_or_jobs(
        self, monkeypatch,
    ):
        env = make_env(monkeypatch, tickers=["AAPL", "MSFT", "NVDA", "AMZN"])
        sid = _sid()
        await env.request(sid)
        await env.request(sid)  # same id again (network retry / continuation)
        assert len(env.session_rows()) == 1
        assert len(env.session_jobs(sid)) == 4

    @pytest.mark.asyncio
    async def test_no_active_holdings_creates_no_session(self, monkeypatch):
        env = make_env(monkeypatch, tickers=[])
        result = await env.request(_sid())
        assert result["status"] == "no_active_holdings"
        assert env.session_rows() == []

    @pytest.mark.asyncio
    async def test_full_run_to_completion_single_session_single_snapshot(
        self, monkeypatch,
    ):
        env = make_env(monkeypatch, tickers=["AAPL", "MSFT", "NVDA", "AMZN", "TSLA"])
        sid = _sid()
        last = await env.run_to_completion(sid)
        assert last["session_status"] == STATUS_COMPLETED
        assert last["status"] == "completed"
        assert last["snapshot_available_after_run"] is True
        assert last["publication_status"] == "completed"
        assert last["next_required_action"] == ACTION_COMPLETE
        # 5 tickers, batch size 3 → exactly 5 analyst calls, one per ticker.
        assert sorted(env.analyst_calls) == ["AAPL", "AMZN", "MSFT", "NVDA", "TSLA"]
        jobs = env.session_jobs(sid)
        assert all(j["status"] == JOB_SUCCEEDED for j in jobs)
        linked = [
            s for s in env.snapshot_rows()
            if str(s.get("run_session_id") or "") == sid
        ]
        assert len(linked) == 1
        assert last["completed_snapshot_id"] == linked[0]["id"]
        assert linked[0]["payload"]["run_session_id"] == sid


# ═══ Suite 4 — session isolation ══════════════════════════════════════════════


class TestSessionIsolation:
    @pytest.mark.asyncio
    async def test_old_certified_snapshot_cannot_complete_new_session(
        self, monkeypatch,
    ):
        """A pre-existing worker_certified snapshot must never satisfy a new
        click — even a zero-stale-tickers run publishes its OWN snapshot."""
        env = make_env(monkeypatch, stale_all=False)
        old_snapshot_id = str(uuid.uuid4())
        env.client.store.setdefault("intel_v3_snapshots", []).append({
            "id": old_snapshot_id,
            "user_id": USER_A,
            "is_active": True,
            "created_at": "2020-01-01T00:00:00+00:00",
            "run_session_id": None,
            "payload": {"snapshot_source": "worker_certified"},
        })

        sid = _sid()
        last = await env.run_to_completion(sid)
        assert last["session_status"] == STATUS_COMPLETED
        # Zero analyst work was needed…
        assert env.analyst_calls == []
        # …but completion still required a NEW deterministic session-linked
        # snapshot, distinct from the pre-session snapshot.
        assert last["completed_snapshot_id"] != old_snapshot_id
        session = env.session_rows()[0]
        assert session["pre_session_snapshot_id"] == old_snapshot_id
        assert session["completed_snapshot_id"] != old_snapshot_id
        linked = [
            s for s in env.snapshot_rows()
            if str(s.get("run_session_id") or "") == sid
        ]
        assert len(linked) == 1

    @pytest.mark.asyncio
    async def test_old_queue_rows_neither_block_nor_satisfy_session(
        self, monkeypatch,
    ):
        env = make_env(monkeypatch, tickers=["AAPL", "MSFT"])
        # Legacy daily-window jobs (NULL session): one pending for an owned
        # ticker, one succeeded for the same ticker, one for a SOLD ticker no
        # longer held.
        legacy = [
            {"id": str(uuid.uuid4()), "user_id": USER_A, "ticker": "AAPL",
             "refresh_window": "2026-07-21", "status": JOB_PENDING,
             "attempts": 0, "max_attempts": 5, "run_session_id": None,
             "requested_at": "2026-07-21T00:00:00+00:00",
             "next_retry_at": "2026-07-21T00:00:00+00:00"},
            {"id": str(uuid.uuid4()), "user_id": USER_A, "ticker": "MSFT",
             "refresh_window": "2026-07-21", "status": JOB_SUCCEEDED,
             "attempts": 1, "max_attempts": 5, "run_session_id": None,
             "requested_at": "2026-07-21T00:00:00+00:00"},
            {"id": str(uuid.uuid4()), "user_id": USER_A, "ticker": "SOLD",
             "refresh_window": "2026-07-21", "status": JOB_PENDING,
             "attempts": 0, "max_attempts": 5, "run_session_id": None,
             "requested_at": "2026-07-21T00:00:00+00:00",
             "next_retry_at": "2026-07-21T00:00:00+00:00"},
        ]
        env.client.store.setdefault("analyst_refresh_jobs", []).extend(legacy)

        sid = _sid()
        last = await env.run_to_completion(sid)
        assert last["session_status"] == STATUS_COMPLETED

        # The legacy succeeded MSFT row did NOT satisfy the session (a fresh
        # session job was created and analysed)…
        assert sorted(env.analyst_calls) == ["AAPL", "MSFT"]
        # …and no legacy row was claimed, mutated, or credited by the session.
        by_id = {j["id"]: j for j in env.job_rows()}
        assert by_id[legacy[0]["id"]]["status"] == JOB_PENDING
        assert by_id[legacy[1]["id"]]["status"] == JOB_SUCCEEDED
        assert by_id[legacy[2]["id"]]["status"] == JOB_PENDING  # sold ticker untouched
        assert "SOLD" not in env.analyst_calls

    @pytest.mark.asyncio
    async def test_same_ticker_in_two_sessions_stays_isolated(self, monkeypatch):
        env = make_env(monkeypatch, tickers=["AAPL", "MSFT"])
        sid1 = _sid()
        # Create session 1 but stop before its work is done: create only.
        first = await env.request(sid1)
        assert first["session_status"] in (STATUS_TICKER_REFRESH, STATUS_COMPLETED)
        s1_jobs_before = {j["id"]: j["status"] for j in env.session_jobs(sid1)}

        # Second click — a different session over the SAME tickers.
        sid2 = _sid()
        last2 = await env.run_to_completion(sid2)
        assert last2["session_status"] == STATUS_COMPLETED

        # Session 2's completion did not mutate session 1's remaining jobs,
        # and session 1 is still not completed.
        s1 = next(s for s in env.session_rows() if s["id"] == sid1)
        assert s1["status"] != STATUS_COMPLETED or s1["completed_snapshot_id"]
        for job_id, status_before in s1_jobs_before.items():
            job_now = next(j for j in env.job_rows() if j["id"] == job_id)
            if status_before in (JOB_PENDING, JOB_CLAIMED):
                assert job_now["status"] != JOB_SUCCEEDED or job_now[
                    "run_session_id"
                ] == sid1

        # Distinct job rows per session for the same ticker.
        aapl_jobs = [j for j in env.job_rows() if j["ticker"] == "AAPL"]
        assert {j["run_session_id"] for j in aapl_jobs} == {sid1, sid2}

    @pytest.mark.asyncio
    async def test_two_users_with_overlapping_tickers_stay_isolated(
        self, monkeypatch,
    ):
        env = make_env(monkeypatch, tickers=["AAPL", "MSFT"])
        seed_positions(env.client, USER_B, ["AAPL", "NVDA"])

        sid_a = _sid()
        sid_b = _sid()
        last_a = await env.run_to_completion(sid_a)
        # Drive user B's session with user B's identity.
        last_b = None
        for _ in range(10):
            last_b = await env.request(sid_b, as_user=USER_B)
            if last_b["session_status"] in (STATUS_COMPLETED, STATUS_FAILED):
                break

        assert last_a["session_status"] == STATUS_COMPLETED
        assert last_b["session_status"] == STATUS_COMPLETED
        for j in env.session_jobs(sid_a):
            assert j["user_id"] == USER_A
        for j in env.session_jobs(sid_b):
            assert j["user_id"] == USER_B

    @pytest.mark.asyncio
    async def test_foreign_session_id_is_rejected(self, monkeypatch):
        env = make_env(monkeypatch)
        sid = _sid()
        await env.request(sid)
        with pytest.raises(SessionOwnershipError):
            await env.request(sid, as_user=USER_B)

    @pytest.mark.asyncio
    async def test_same_day_second_click_gets_distinct_session_and_snapshot(
        self, monkeypatch,
    ):
        env = make_env(monkeypatch, tickers=["AAPL"])
        sid1, sid2 = _sid(), _sid()
        last1 = await env.run_to_completion(sid1)
        last2 = await env.run_to_completion(sid2)
        assert last1["session_status"] == last2["session_status"] == STATUS_COMPLETED
        assert last1["completed_snapshot_id"] != last2["completed_snapshot_id"]
        assert len(env.session_rows()) == 2
        # Two same-day session job rows for the same (user, ticker) coexist —
        # the legacy daily-window uniqueness no longer applies to session jobs.
        aapl_jobs = [
            j for j in env.job_rows()
            if j["ticker"] == "AAPL" and j.get("run_session_id")
        ]
        assert len(aapl_jobs) == 2


# ═══ Suite 5 — interruption + resume (16 tickers) ═════════════════════════════


class TestInterruptionAndResume:
    @pytest.mark.asyncio
    async def test_16_tickers_interrupted_run_resumes_without_regeneration(
        self, monkeypatch,
    ):
        tickers = [f"T{i:02d}" for i in range(16)]
        env = make_env(monkeypatch, tickers=tickers)
        sid = _sid()

        # Two bounded requests: creation + first batch, then second batch.
        r1 = await env.request(sid)
        r2 = await env.request(sid)
        assert r2["session_status"] == STATUS_TICKER_REFRESH
        done_after_two = {
            j["ticker"] for j in env.session_jobs(sid)
            if j["status"] == JOB_SUCCEEDED
        }
        assert len(done_after_two) == 6

        # Simulate an interrupted third request: it claimed 3 jobs, persisted
        # durable evidence for TWO of them, then died before marking any job
        # succeeded — and before its claims could be released.
        now = datetime.now(timezone.utc)
        interrupted = claim_due_jobs(
            env.client,
            worker_run_id=str(uuid.uuid4()),
            now=now,
            limit=3,
            user_id=USER_A,
            run_session_id=sid,
        )
        assert len(interrupted) == 3
        evidence_run = str(uuid.uuid4())
        persisted = [interrupted[0].ticker, interrupted[1].ticker]
        for t in persisted:
            write_ticker_evidence(
                env.client, user_id=USER_A, ticker=t, agent_run_id=evidence_run,
            )
        # The third claimed ticker has NO evidence; age its claim past the
        # stale-claim timeout so resume recovers it (the request is dead).
        lost = interrupted[2].ticker
        stale_iso = (now - timedelta(seconds=700)).isoformat()
        for j in env.client.store["analyst_refresh_jobs"]:
            if j.get("run_session_id") == sid and j["status"] == JOB_CLAIMED:
                j["claimed_at"] = stale_iso

        calls_before_resume = len(env.analyst_calls)

        # Resume with the SAME session id until completion.
        last = await env.run_to_completion(sid)
        assert last["session_status"] == STATUS_COMPLETED

        # The two interrupted-but-persisted tickers were credited from their
        # durable evidence — never re-analysed.
        resumed_calls = env.analyst_calls[calls_before_resume:]
        for t in persisted:
            assert t not in resumed_calls
        # The genuinely-lost ticker was re-analysed exactly once.
        assert resumed_calls.count(lost) == 1
        # Exact accounting: every ticker analysed exactly once overall except
        # nothing was ever analysed twice.
        for t in tickers:
            expected = 0 if t in persisted else 1
            assert env.analyst_calls.count(t) == expected or (
                t in persisted and env.analyst_calls.count(t) == 0
            )
        assert all(
            j["status"] == JOB_SUCCEEDED for j in env.session_jobs(sid)
        )

    @pytest.mark.asyncio
    async def test_only_unfinished_jobs_are_claimed_on_resume(self, monkeypatch):
        tickers = [f"T{i:02d}" for i in range(16)]
        env = make_env(monkeypatch, tickers=tickers)
        sid = _sid()
        await env.request(sid)  # batch 1 (3 tickers)
        succeeded_first = {
            j["ticker"] for j in env.session_jobs(sid)
            if j["status"] == JOB_SUCCEEDED
        }
        assert len(succeeded_first) == 3
        await env.request(sid)  # batch 2
        # Succeeded jobs from batch 1 were never re-claimed or re-analysed.
        for t in succeeded_first:
            assert env.analyst_calls.count(t) == 1


# ═══ Suite 6 — publication-only retry ═════════════════════════════════════════


class TestPublicationRetry:
    @pytest.mark.asyncio
    async def test_publication_failure_retries_without_any_ticker_analysis(
        self, monkeypatch,
    ):
        env = make_env(
            monkeypatch, tickers=["AAPL", "MSFT", "NVDA"], publish_fail_times=1,
        )
        sid = _sid()
        # Drive until the publication failure surfaces.
        result = await env.request(sid)
        assert result["session_status"] == STATUS_PUBLICATION_RETRY
        assert result["publication_status"] == "retryable_failed"
        assert result["retryable"] is True
        assert result["next_required_action"] == ACTION_CONTINUE
        # Every ticker job stays succeeded through the failure.
        assert all(
            j["status"] == JOB_SUCCEEDED for j in env.session_jobs(sid)
        )
        analyst_calls_after_failure = len(env.analyst_calls)
        session = env.session_rows()[0]
        assert session["status"] == STATUS_PUBLICATION_RETRY
        assert "publication" in (session["last_error"] or "")

        # Continuation: publication-only retry.
        retry = await env.request(sid)
        assert retry["session_status"] == STATUS_COMPLETED
        assert retry["publication_status"] == "completed"
        # ZERO additional analyst calls during the retry…
        assert len(env.analyst_calls) == analyst_calls_after_failure
        # …zero synthesis calls is enforced globally by the raising patch.
        # Exactly one idempotent session-linked snapshot.
        linked = [
            s for s in env.snapshot_rows()
            if str(s.get("run_session_id") or "") == sid
        ]
        assert len(linked) == 1
        assert env.publish_calls["count"] == 2
        session = env.session_rows()[0]
        assert session["publication_attempts"] == 2
        assert session["completed_snapshot_id"] == linked[0]["id"]

    @pytest.mark.asyncio
    async def test_snapshot_inserted_but_session_update_lost_is_adopted(
        self, monkeypatch,
    ):
        """Crash between snapshot insert and session update: the retry must
        find the existing session-linked snapshot and complete the session —
        never insert a duplicate."""
        env = make_env(monkeypatch, tickers=["AAPL"])
        sid = _sid()
        result = await env.request(sid)
        assert result["session_status"] in (STATUS_TICKER_REFRESH, STATUS_COMPLETED)
        last = await env.run_to_completion(sid)
        assert last["session_status"] == STATUS_COMPLETED

        # Simulate the crash aftermath: session stuck in 'publishing' with no
        # completed_snapshot_id, but the snapshot row exists.
        session = env.session_rows()[0]
        session["status"] = "publishing"
        session["completed_snapshot_id"] = None
        publish_count_before = env.publish_calls["count"]

        recovered = await env.request(sid)
        assert recovered["session_status"] == STATUS_COMPLETED
        # Adopted the existing row — no new publication build, no duplicate.
        assert env.publish_calls["count"] == publish_count_before
        linked = [
            s for s in env.snapshot_rows()
            if str(s.get("run_session_id") or "") == sid
        ]
        assert len(linked) == 1
        assert env.session_rows()[0]["completed_snapshot_id"] == linked[0]["id"]

    @pytest.mark.asyncio
    async def test_duplicate_session_snapshot_insert_is_impossible(
        self, monkeypatch,
    ):
        env = make_env(monkeypatch, tickers=["AAPL"])
        sid = _sid()
        await env.run_to_completion(sid)
        with pytest.raises(UniqueViolation):
            env.client.table("intel_v3_snapshots").insert({
                "user_id": USER_A,
                "run_session_id": sid,
                "payload": {},
            }).execute()


# ═══ Suite 7 — exact accounting (32 stale tickers) ════════════════════════════


class TestExactAccounting:
    @pytest.mark.asyncio
    async def test_32_tickers_exact_calls_bounded_requests_one_snapshot(
        self, monkeypatch,
    ):
        tickers = [f"S{i:02d}" for i in range(32)]
        env = make_env(monkeypatch, tickers=tickers)
        sid = _sid()

        requests = 0
        last = None
        for _ in range(FRONTEND_MAX_CONTINUATIONS):
            requests += 1
            last = await env.request(sid)
            if last["session_status"] == STATUS_COMPLETED:
                break
        assert last is not None and last["session_status"] == STATUS_COMPLETED

        # Bounded requests: ceil(32/3) = 11 drain requests; publication rides
        # on the final drain request. Must fit the frontend's cap of 20 with
        # honest headroom.
        assert requests <= 12
        assert requests <= FRONTEND_MAX_CONTINUATIONS

        # Exactly 32 analyst calls — each ticker exactly once.
        assert len(env.analyst_calls) == 32
        for t in tickers:
            assert env.analyst_calls.count(t) == 1

        # One session; one completed session-linked snapshot; no unfinished
        # session jobs; zero synthesis calls (raising patch never fired).
        assert len(env.session_rows()) == 1
        jobs = env.session_jobs(sid)
        assert len(jobs) == 32
        assert all(j["status"] == JOB_SUCCEEDED for j in jobs)
        linked = [
            s for s in env.snapshot_rows()
            if str(s.get("run_session_id") or "") == sid
        ]
        assert len(linked) == 1
        assert env.publish_calls["count"] == 1
        assert last["completed_snapshot_id"] == linked[0]["id"]
        assert last["session_remaining_ticker_count"] == 0


# ═══ Completion truth + honesty states ════════════════════════════════════════


class TestCompletionTruth:
    @pytest.mark.asyncio
    async def test_completion_requires_payload_session_linkage(self, monkeypatch):
        env = make_env(monkeypatch, tickers=["AAPL"])
        sid = _sid()
        last = await env.run_to_completion(sid)
        assert last["snapshot_available_after_run"] is True

        # Tamper: strip the payload linkage — the API must stop reporting
        # completion even though the session row says completed.
        for s in env.snapshot_rows():
            if str(s.get("run_session_id") or "") == sid:
                s["payload"] = dict(s["payload"], run_session_id=None)
        rereport = await env.request(sid)
        assert rereport["snapshot_available_after_run"] is False
        assert rereport["status"] != "completed"

    @pytest.mark.asyncio
    async def test_completion_requires_certified_snapshot_source(self, monkeypatch):
        env = make_env(monkeypatch, tickers=["AAPL"])
        sid = _sid()
        await env.run_to_completion(sid)
        for s in env.snapshot_rows():
            if str(s.get("run_session_id") or "") == sid:
                s["payload"] = dict(s["payload"], snapshot_source="certification_failed")
        rereport = await env.request(sid)
        assert rereport["snapshot_available_after_run"] is False

    @pytest.mark.asyncio
    async def test_queue_only_when_on_demand_disabled(self, monkeypatch):
        env = make_env(monkeypatch, on_demand=False)
        sid = _sid()
        result = await env.request(sid)
        assert result["next_required_action"] == ACTION_QUEUE_ONLY
        assert result["snapshot_available_after_run"] is False
        assert env.analyst_calls == []

    @pytest.mark.asyncio
    async def test_writes_disabled_blocks_publication_honestly(self, monkeypatch):
        env = make_env(monkeypatch, tickers=["AAPL"], writes_enabled=False)
        sid = _sid()
        result = await env.request(sid)
        # Ticker work happened; publication is honestly blocked and retryable.
        assert result["session_status"] == STATUS_PUBLICATION_RETRY
        assert result["next_required_action"] == ACTION_WRITES_DISABLED
        assert result["snapshot_available_after_run"] is False

    @pytest.mark.asyncio
    async def test_terminal_job_failure_fails_session_honestly(self, monkeypatch):
        env = make_env(
            monkeypatch,
            tickers=["GOOD1", "GOOD2", "BAD"],
            fail_tickers={"BAD"},
        )
        sid = _sid()
        last = await env.run_to_completion(sid, max_requests=30)
        assert last["session_status"] == STATUS_FAILED
        assert last["retryable"] is False
        assert last["next_required_action"] == ACTION_RETRY_NEW_CLICK
        # Successfully persisted tickers stay succeeded — a failing sibling
        # never blanket-fails them.
        by_ticker = {j["ticker"]: j for j in env.session_jobs(sid)}
        assert by_ticker["GOOD1"]["status"] == JOB_SUCCEEDED
        assert by_ticker["GOOD2"]["status"] == JOB_SUCCEEDED
        assert by_ticker["BAD"]["status"] == "failed"
        # No snapshot was published for the failed session.
        assert not [
            s for s in env.snapshot_rows()
            if str(s.get("run_session_id") or "") == sid
        ]


# ═══ Adversarial-audit hardening (post-review fixes) ══════════════════════════


class TestGlobalWorkerCannotTouchSessionJobs:
    @pytest.mark.asyncio
    async def test_unscoped_legacy_claim_and_count_exclude_session_rows(
        self, monkeypatch,
    ):
        """The unscoped standalone worker must never claim (or count) a
        session's jobs — so it can never burn a session job's attempt budget
        from outside the session."""
        from app.services.intelligence.v3.analyst_refresh_job_store_v1 import (
            count_due_jobs,
        )

        env = make_env(monkeypatch, tickers=["AAPL", "MSFT"])
        sid = _sid()
        await env.request(sid)  # creates session + jobs (first batch runs)

        # Seed one legacy NULL-session pending job alongside the session rows.
        legacy_id = str(uuid.uuid4())
        env.client.store.setdefault("analyst_refresh_jobs", []).append({
            "id": legacy_id, "user_id": USER_A, "ticker": "LEGACY",
            "refresh_window": "2026-07-21", "status": JOB_PENDING,
            "attempts": 0, "max_attempts": 5, "run_session_id": None,
            "requested_at": "2026-07-21T00:00:00+00:00",
            "next_retry_at": "2026-07-21T00:00:00+00:00",
        })
        # Reset any remaining session jobs to pending so they'd be claimable
        # if the isolation filter were missing.
        session_pending = [
            j for j in env.session_jobs(sid) if j["status"] == JOB_PENDING
        ]

        claimed = claim_due_jobs(
            env.client, worker_run_id=str(uuid.uuid4()), limit=50,
        )
        assert [j.ticker for j in claimed] == ["LEGACY"]
        for j in env.session_jobs(sid):
            assert j["status"] != JOB_CLAIMED or j.get("worker_run_id") != claimed[0].id

        counts = count_due_jobs(env.client)
        # Only the legacy row counts for the unscoped path (now claimed → 0 due).
        assert counts["pending"] == 0
        assert counts["total_due"] == 0
        # Session-scoped count still sees the session's own jobs.
        if session_pending:
            from app.services.intelligence.v3.intel_run_session_store_v1 import (
                count_session_job_states,
            )
            scoped = count_session_job_states(env.client, run_session_id=sid)
            assert scoped["pending"] == len(session_pending)


class TestReconcileCreditsFailedJobsWithEvidence:
    @pytest.mark.asyncio
    async def test_failed_session_job_with_durable_evidence_is_credited(
        self, monkeypatch,
    ):
        """A transient readback failure can mark a genuinely persisted ticker
        failed. Resume must credit it from its durable evidence — zero
        re-analysis, no attempt-budget burn toward terminal failure."""
        tickers = ["AAPL", "MSFT", "NVDA", "AMZN", "TSLA", "GOOG"]
        env = make_env(monkeypatch, tickers=tickers)
        sid = _sid()
        await env.request(sid)  # batch 1: 3 of 6 succeed; session still open

        # Rewrite one SUCCEEDED job (its durable evidence rows exist) as
        # failed-with-attempts — the shape a readback failure would leave.
        target = next(
            j for j in env.session_jobs(sid) if j["status"] == JOB_SUCCEEDED
        )
        for j in env.client.store["analyst_refresh_jobs"]:
            if j["id"] == target["id"]:
                j["status"] = "failed"
                j["attempts"] = 4  # one attempt away from terminal
                j["next_retry_at"] = "2026-01-01T00:00:00+00:00"
                j["last_error"] = "recommendations_read_failed:TimeoutError"

        last = await env.run_to_completion(sid)
        assert last["session_status"] == STATUS_COMPLETED
        # Credited from evidence — never re-analysed (each ticker exactly once).
        assert env.analyst_calls.count(target["ticker"]) == 1
        assert len(env.analyst_calls) == len(tickers)
        revived = next(
            j for j in env.session_jobs(sid) if j["id"] == target["id"]
        )
        assert revived["status"] == JOB_SUCCEEDED
        assert revived["attempts"] == 4


class TestAdoptedSnapshotReactivation:
    @pytest.mark.asyncio
    async def test_recovered_session_snapshot_is_reactivated(self, monkeypatch):
        """Crash-recovery adoption must re-activate a session snapshot that a
        racing publication attempt deactivated — otherwise the completed
        session points at a row GET /snapshot (latest active) never serves."""
        env = make_env(monkeypatch, tickers=["AAPL"])
        sid = _sid()
        await env.run_to_completion(sid)

        linked = next(
            s for s in env.snapshot_rows()
            if str(s.get("run_session_id") or "") == sid
        )
        # Simulate the race aftermath: linked row deactivated, session stuck
        # in publishing without its completed_snapshot_id.
        linked_id = linked["id"]
        for s in env.client.store["intel_v3_snapshots"]:
            if s["id"] == linked_id:
                s["is_active"] = False
        session = env.session_rows()[0]
        session["status"] = "publishing"
        session["completed_snapshot_id"] = None

        recovered = await env.request(sid)
        assert recovered["session_status"] == STATUS_COMPLETED
        row = next(
            s for s in env.snapshot_rows() if s["id"] == linked_id
        )
        assert row["is_active"] is True
        # Still exactly one session-linked row (no duplicate insert).
        assert len([
            s for s in env.snapshot_rows()
            if str(s.get("run_session_id") or "") == sid
        ]) == 1

    @pytest.mark.asyncio
    async def test_terminal_unverifiable_completion_stops_continuation(
        self, monkeypatch,
    ):
        """An already-completed session whose snapshot can no longer be
        verified must return a stopping (non-retryable) response — never an
        auto-continue loop that no continuation can resolve."""
        env = make_env(monkeypatch, tickers=["AAPL"])
        sid = _sid()
        await env.run_to_completion(sid)
        for s in env.snapshot_rows():
            if str(s.get("run_session_id") or "") == sid:
                s["payload"] = dict(s["payload"], run_session_id=None)

        rereport = await env.request(sid)
        assert rereport["snapshot_available_after_run"] is False
        assert rereport["retryable"] is False
        assert not str(rereport["next_required_action"]).startswith("reclick_")


class TestCertificationScopeOverride:
    @pytest.mark.asyncio
    async def test_contract_certifies_over_immutable_scope_not_positions(self):
        """check_certified_intel_run_contract(scope_tickers=…) must use the
        session's captured scope and never read the positions table."""
        from app.services.intelligence.v3.certified_intel_run_contract_v1 import (
            check_certified_intel_run_contract,
        )

        client = FakeSupabase()
        # Positions table intentionally holds a DIFFERENT ticker — if the
        # contract read positions, the totals would reflect it.
        seed_positions(client, USER_A, ["OTHER"])

        result = await check_certified_intel_run_contract(
            user_id=UUID(USER_A),
            client=client,
            scope_tickers=["AAPL", "aapl", "MSFT"],  # case-dupe collapses
        )
        assert result.total_holding_count == 2
        assert result.certified is False  # no evidence rows exist
        assert set(result.failed_tickers) == {"AAPL", "MSFT"}
        assert "OTHER" not in result.failed_tickers
