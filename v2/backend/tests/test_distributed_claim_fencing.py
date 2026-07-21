"""Claim-generation fencing — a stale worker whose task was reclaimed cannot
mutate ANY task-owned durable output.

The required two-worker shape (completion item 5):
  1. worker A claims;
  2. A's lease expires;
  3. worker B reclaims with a NEW claim token;
  4. B writes and completes;
  5. A finishes late;
  6. A can alter NOTHING — not the task row, not the specialist outputs, not
     the ticker decision, not the session terminal state. The side-effect
     rows themselves are asserted unchanged, not just the completion guard.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

import app.services.intelligence.v3.distributed.run_task_store_v1 as store
from app.services.intelligence.v3.distributed import session_control_v1 as control
from app.services.intelligence.v3.distributed.decision_tasks_v1 import (
    execute_ticker_decision_task,
)
from app.services.intelligence.v3.distributed.publication_v1 import (
    execute_publication_task,
)
from app.services.intelligence.v3.distributed.specialist_agents_v1 import (
    execute_specialist_task,
)
from app.services.intelligence.v3.distributed.task_contracts_v1 import (
    AXIS_FUNDAMENTAL,
    TASK_PORTFOLIO_JOIN_PUBLISH,
    TASK_SPECIALIST_ANALYSIS,
    TASK_TICKER_DECISION,
    TICKER_DECIDED,
    TICKER_EVIDENCE_READY,
)
from tests.distributed_run_intel_test_utils import (
    FakeLLM,
    FakeSupabase,
    make_claimed_task,
    make_settings,
    seed_position,
)

USER = str(uuid.uuid4())


def _expire_lease(client: FakeSupabase, task_id: str) -> None:
    expired = (
        datetime.now(timezone.utc) - timedelta(seconds=5)
    ).isoformat()
    client.table("intel_run_tasks").update(
        {"lease_expires_at": expired}
    ).eq("id", task_id).execute()


async def _ready_session(client: FakeSupabase, ticker: str = "AAPL") -> str:
    seed_position(client, USER, ticker)
    session_id = str(uuid.uuid4())
    await control.create_distributed_session(
        client=client, user_id=USER, session_id=session_id,
    )
    client.table("intel_run_tickers").update({
        "state": TICKER_EVIDENCE_READY,
        "evidence_bundle": {
            "ticker": ticker, "input_fingerprint": "sha256:test",
            "usable_lanes": ["price", "technicals", "fundamentals"],
            "required_lanes_missing": [],
            "fundamental": {"pe": 20.0}, "technical": {"pct_30d": 2.0},
            "market": {"price": 100.0},
        },
    }).eq("run_session_id", session_id).eq("ticker", ticker).execute()
    return session_id


class TestClaimTokenBasics:
    @pytest.mark.asyncio
    async def test_every_claim_issues_a_fresh_token(self):
        client = FakeSupabase()
        session_id = await _ready_session(client)
        first = store.claim_tasks(client, worker_id="A", limit=1, lease_seconds=0)[0]
        assert first.get("claim_token")
        _expire_lease(client, first["id"])
        second = next(
            t for t in store.claim_tasks(client, worker_id="B", limit=50)
            if t["id"] == first["id"]
        )
        assert second["claim_token"] != first["claim_token"]

    @pytest.mark.asyncio
    async def test_completion_requires_current_claim_token(self):
        client = FakeSupabase()
        session_id = await _ready_session(client)
        stale = store.claim_tasks(client, worker_id="A", limit=1, lease_seconds=0)[0]
        _expire_lease(client, stale["id"])
        fresh = next(
            t for t in store.claim_tasks(client, worker_id="B", limit=50)
            if t["id"] == stale["id"]
        )
        # A's late completion: same task id, same-ish shape, OLD token.
        assert store.complete_task(
            client, task=stale, worker_id="A", final_state="succeeded",
        ) is False
        # Even a stale row claiming B's worker id fails on the token.
        forged = dict(stale, claim_owner="B")
        assert store.complete_task(
            client, task=forged, worker_id="B", final_state="succeeded",
        ) is False
        # B completes with the current token.
        assert store.complete_task(
            client, task=fresh, worker_id="B", final_state="succeeded",
        ) is True


class TestTwoWorkerSpecialistFence:
    @pytest.mark.asyncio
    async def test_stale_specialist_worker_cannot_overwrite_outputs(self):
        client = FakeSupabase()
        session_id = await _ready_session(client)
        batch_key = "equity:fundamental:b000:AAPL"
        task_row = store.create_task(
            client, run_session_id=session_id, user_id=USER,
            task_type=TASK_SPECIALIST_ANALYSIS, lane=AXIS_FUNDAMENTAL,
            batch_key=batch_key, asset_type="equity",
        )
        # 1-2. Worker A claims; lease expires mid-LLM-call.
        a_task = store.claim_tasks(
            client, worker_id="worker-A", limit=50, lease_seconds=0,
        )
        a_task = next(t for t in a_task if t["id"] == task_row["id"])
        _expire_lease(client, a_task["id"])
        # 3-4. Worker B reclaims (new token), writes and completes.
        b_task = next(
            t for t in store.claim_tasks(client, worker_id="worker-B", limit=50)
            if t["id"] == a_task["id"]
        )
        b_llm = FakeLLM(score_by_ticker={"AAPL": 0.7})
        b_outcome = await execute_specialist_task(client, task=b_task, llm=b_llm)
        assert b_outcome.persisted == ["AAPL"]
        assert store.complete_task(
            client, task=b_task, worker_id="worker-B", final_state="succeeded",
        )
        b_output = next(
            o for o in client.rows("intel_run_specialist_outputs")
            if o["ticker"] == "AAPL" and o["axis"] == AXIS_FUNDAMENTAL
        )
        b_snapshot = dict(b_output)

        # 5. Worker A finishes late with DIFFERENT results.
        a_llm = FakeLLM(score_by_ticker={"AAPL": -0.9})
        a_outcome = await execute_specialist_task(client, task=a_task, llm=a_llm)
        # 6. A wrote nothing and completed nothing.
        assert a_outcome.error == "claim_lost"
        assert store.complete_task(
            client, task=a_task, worker_id="worker-A", final_state="succeeded",
        ) is False
        after = next(
            o for o in client.rows("intel_run_specialist_outputs")
            if o["ticker"] == "AAPL" and o["axis"] == AXIS_FUNDAMENTAL
        )
        assert after["score"] == b_snapshot["score"] == 0.7
        assert after["stance"] == b_snapshot["stance"]
        task_after = next(
            t for t in client.rows("intel_run_tasks")
            if t["id"] == a_task["id"]
        )
        assert task_after["state"] == "succeeded"
        assert task_after["claim_owner"] == "worker-B"


class TestTwoWorkerDecisionFence:
    @pytest.mark.asyncio
    async def test_stale_decision_worker_cannot_change_ticker_decision(self):
        client = FakeSupabase()
        session_id = await _ready_session(client)
        for axis in (AXIS_FUNDAMENTAL, "technical"):
            store.upsert_specialist_output(
                client, run_session_id=session_id, user_id=USER,
                ticker="AAPL", axis=axis,
                output={
                    "stance": "positive", "score": 0.6, "confidence": 0.8,
                    "key_findings": ["finding"], "risks": [],
                    "evidence_refs": [], "missing_evidence": [],
                    "limitations": [],
                    "valid_until": "2027-01-01T00:00:00+00:00",
                    "model": "fake", "prompt_version": "test",
                    "input_fingerprint": "sha256:test", "batch_key": None,
                },
            )
        decision_row = store.create_task(
            client, run_session_id=session_id, user_id=USER,
            task_type=TASK_TICKER_DECISION, ticker="AAPL",
        )
        a_task = next(
            t for t in store.claim_tasks(
                client, worker_id="worker-A", limit=50, lease_seconds=0,
            )
            if t["id"] == decision_row["id"]
        )
        _expire_lease(client, a_task["id"])
        b_task = next(
            t for t in store.claim_tasks(client, worker_id="worker-B", limit=50)
            if t["id"] == a_task["id"]
        )
        b_outcome = await execute_ticker_decision_task(client, task=b_task)
        assert b_outcome.final_ticker_state == TICKER_DECIDED
        decided_row = next(
            r for r in client.rows("intel_run_tickers")
            if r["ticker"] == "AAPL"
        )
        b_decision = dict(decided_row["decision"])
        assert store.complete_task(
            client, task=b_task, worker_id="worker-B", final_state="succeeded",
        )

        # A finishes late: refused at the ownership fence; the persisted
        # decision (and its agent_run_id) is byte-for-byte B's.
        a_outcome = await execute_ticker_decision_task(client, task=a_task)
        assert a_outcome.error == "claim_lost"
        after = next(
            r for r in client.rows("intel_run_tickers")
            if r["ticker"] == "AAPL"
        )
        assert after["decision"] == b_decision
        assert after["state"] == TICKER_DECIDED


class TestTwoWorkerPublicationFence:
    @pytest.mark.asyncio
    async def test_stale_publication_worker_cannot_alter_terminal_state(self):
        client = FakeSupabase()
        session_id = await _ready_session(client)
        # Decide the single ticker.
        for axis in (AXIS_FUNDAMENTAL, "technical"):
            store.upsert_specialist_output(
                client, run_session_id=session_id, user_id=USER,
                ticker="AAPL", axis=axis,
                output={
                    "stance": "positive", "score": 0.6, "confidence": 0.8,
                    "key_findings": ["finding"], "risks": [],
                    "evidence_refs": [], "missing_evidence": [],
                    "limitations": [],
                    "valid_until": "2027-01-01T00:00:00+00:00",
                    "model": "fake", "prompt_version": "test",
                    "input_fingerprint": "sha256:test", "batch_key": None,
                },
            )
        await execute_ticker_decision_task(
            client,
            task=make_claimed_task(
                client, run_session_id=session_id, user_id=USER,
                task_type=TASK_TICKER_DECISION, ticker="AAPL",
            ),
        )
        publish_row = store.create_task(
            client, run_session_id=session_id, user_id=USER,
            task_type=TASK_PORTFOLIO_JOIN_PUBLISH,
        )
        a_task = next(
            t for t in store.claim_tasks(
                client, worker_id="worker-A", limit=50, lease_seconds=0,
            )
            if t["id"] == publish_row["id"]
        )
        _expire_lease(client, a_task["id"])
        b_task = next(
            t for t in store.claim_tasks(client, worker_id="worker-B", limit=50)
            if t["id"] == a_task["id"]
        )
        b_outcome = await execute_publication_task(
            client, task=b_task, settings=make_settings(),
        )
        assert b_outcome.final_state == "succeeded"
        assert store.complete_task(
            client, task=b_task, worker_id="worker-B", final_state="succeeded",
        )
        session_after_b = dict(client.rows("intel_run_sessions")[0])
        snapshots_after_b = [
            dict(s) for s in client.rows("intel_v3_snapshots")
        ]

        # A finishes late: adoption path returns the SAME snapshot, terminal
        # session state and snapshot set are unchanged, completion refused.
        a_outcome = await execute_publication_task(
            client, task=a_task, settings=make_settings(),
        )
        assert a_outcome.snapshot_row_id == b_outcome.snapshot_row_id
        assert store.complete_task(
            client, task=a_task, worker_id="worker-A", final_state="succeeded",
        ) is False
        assert client.rows("intel_run_sessions")[0] == session_after_b
        assert [dict(s) for s in client.rows("intel_v3_snapshots")] == (
            snapshots_after_b
        )
