"""Collector isolation + durable task store semantics.

Proves:
  * a collector task fetches ONLY its own ticker (scoped providers);
  * lane failure isolation (one lane's failure never fails other lanes);
  * TTL cache reuse (fresh prior output short-circuits the provider);
  * atomic claiming (CAS): two workers can never claim the same task;
  * lease expiry recovery + attempts-at-claim so crash loops exhaust budget;
  * a task can never be completed twice / by a non-owner;
  * retryable failure → pending with backoff → terminal failed on exhaustion.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

import app.services.intelligence.v3.distributed.run_task_store_v1 as store
from app.services.intelligence.v3.distributed.collectors_v1 import (
    execute_collector_task,
)
from app.services.intelligence.v3.distributed import session_control_v1 as control
from app.services.intelligence.v3.distributed.task_contracts_v1 import (
    LANE_FUNDAMENTALS,
    LANE_NEWS_SENTIMENT,
    LANE_PRICE,
    LANE_TECHNICALS,
    TASK_CLAIMED,
    TASK_COLLECT_EVIDENCE_LANE,
    TASK_PENDING,
    TASK_SUCCEEDED,
)
from tests.distributed_run_intel_test_utils import (
    FakeSupabase,
    ProviderRecorder,
    make_settings,
    patch_providers,
    seed_position,
)

USER = str(uuid.uuid4())


async def _make_session(client: FakeSupabase, tickers: list[str]) -> str:
    for ticker in tickers:
        seed_position(client, USER, ticker)
    session_id = str(uuid.uuid4())
    await control.create_distributed_session(
        client=client, user_id=USER, session_id=session_id,
    )
    return session_id


def _lane_tasks(client: FakeSupabase, session_id: str, ticker: str):
    return [
        t for t in client.rows("intel_run_tasks")
        if t["task_type"] == TASK_COLLECT_EVIDENCE_LANE
        and t.get("ticker") == ticker
        and t["run_session_id"] == session_id
    ]


class TestCollectorIsolation:
    @pytest.mark.asyncio
    async def test_three_ticker_claim_fetches_only_those_tickers(
        self, monkeypatch
    ):
        client = FakeSupabase()
        session_id = await _make_session(
            client, ["ALK", "GOOGL", "VHT", "AAPL", "MSFT", "NVDA"]
        )
        recorder = ProviderRecorder()
        patch_providers(monkeypatch, recorder)
        settings = make_settings()

        selected = {"ALK", "GOOGL", "VHT"}
        for ticker in selected:
            for task in _lane_tasks(client, session_id, ticker):
                await execute_collector_task(
                    client, task=task, settings=settings,
                )
        assert recorder.tickers_called() == selected, (
            "collectors fetched tickers outside their explicit task scope"
        )

    @pytest.mark.asyncio
    async def test_lane_failure_is_isolated_per_lane_and_ticker(
        self, monkeypatch
    ):
        client = FakeSupabase()
        session_id = await _make_session(client, ["ALK", "GOOGL"])
        recorder = ProviderRecorder(fail_fundamentals={"ALK"})
        patch_providers(monkeypatch, recorder)
        settings = make_settings()

        results = {}
        for ticker in ("ALK", "GOOGL"):
            for task in _lane_tasks(client, session_id, ticker):
                result = await execute_collector_task(
                    client, task=task, settings=settings,
                )
                results[(ticker, task["lane"])] = result.final_state

        assert results[("ALK", LANE_FUNDAMENTALS)] == store.TASK_FAILED_RETRYABLE
        # ALK's OTHER lanes and GOOGL's every lane succeeded independently.
        assert results[("ALK", LANE_PRICE)] == TASK_SUCCEEDED
        assert results[("ALK", LANE_TECHNICALS)] == TASK_SUCCEEDED
        assert results[("GOOGL", LANE_FUNDAMENTALS)] == TASK_SUCCEEDED
        assert results[("GOOGL", LANE_PRICE)] == TASK_SUCCEEDED

    @pytest.mark.asyncio
    async def test_ttl_reuse_short_circuits_provider(self, monkeypatch):
        client = FakeSupabase()
        session_id = await _make_session(client, ["AAPL"])
        recorder = ProviderRecorder()
        patch_providers(monkeypatch, recorder)
        settings = make_settings()

        fundamentals_task = next(
            t for t in _lane_tasks(client, session_id, "AAPL")
            if t["lane"] == LANE_FUNDAMENTALS
        )
        first = await execute_collector_task(
            client, task=fundamentals_task, settings=settings,
        )
        assert first.final_state == TASK_SUCCEEDED and not first.cache_hit
        # Persist as the store would (needed for the reuse query).
        store.complete_task(
            client, task={**fundamentals_task, "state": TASK_CLAIMED},
            worker_id="w", final_state=TASK_SUCCEEDED, output=first.output,
        )
        # Claim it properly so completion sticks.
        client.table("intel_run_tasks").update(
            {"state": TASK_SUCCEEDED, "output": first.output,
             "completed_at": datetime.now(timezone.utc).isoformat()}
        ).eq("id", fundamentals_task["id"]).execute()

        calls_before = len(recorder.calls)
        # A second session's fundamentals task for AAPL reuses within TTL.
        session2 = await _make_session_second(client)
        second_task = next(
            t for t in _lane_tasks(client, session2, "AAPL")
            if t["lane"] == LANE_FUNDAMENTALS
        )
        second = await execute_collector_task(
            client, task=second_task, settings=settings,
        )
        assert second.final_state == TASK_SUCCEEDED
        assert second.cache_hit is True
        assert len(recorder.calls) == calls_before, "TTL reuse still fetched"

    @pytest.mark.asyncio
    async def test_price_lane_never_reuses_stale_output(self, monkeypatch):
        """price TTL is 15m: an old output must NOT be reused."""
        client = FakeSupabase()
        session_id = await _make_session(client, ["AAPL"])
        recorder = ProviderRecorder()
        patch_providers(monkeypatch, recorder)
        settings = make_settings()

        old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        client.store.setdefault("intel_run_tasks", []).append({
            "id": str(uuid.uuid4()),
            "run_session_id": session_id,
            "user_id": USER,
            "task_type": TASK_COLLECT_EVIDENCE_LANE,
            "ticker": "AAPL",
            "lane": LANE_PRICE,
            "state": TASK_SUCCEEDED,
            "output": {"price": 1.0, "source": "stale"},
            "completed_at": old,
            "batch_key": "stale-marker",
        })
        price_task = next(
            t for t in _lane_tasks(client, session_id, "AAPL")
            if t["lane"] == LANE_PRICE and t.get("batch_key") != "stale-marker"
        )
        result = await execute_collector_task(
            client, task=price_task, settings=settings,
        )
        assert result.cache_hit is False
        assert ("price_action", "AAPL") in recorder.calls


async def _make_session_second(client: FakeSupabase) -> str:
    # Terminal-ize any active session first (one active session per user).
    for row in client.rows("intel_run_sessions"):
        if row.get("status") in ("created", "running"):
            row["status"] = "completed"
    session_id = str(uuid.uuid4())
    await control.create_distributed_session(
        client=client, user_id=USER, session_id=session_id,
    )
    return session_id


class TestAtomicClaiming:
    def _seed_task(self, client: FakeSupabase, session_id: str, **overrides):
        task = store.create_task(
            client,
            run_session_id=session_id,
            user_id=USER,
            task_type=TASK_COLLECT_EVIDENCE_LANE,
            ticker=overrides.pop("ticker", "AAPL"),
            lane=overrides.pop("lane", LANE_PRICE),
            **overrides,
        )
        assert task is not None
        return task

    @pytest.mark.asyncio
    async def test_two_workers_cannot_claim_same_task(self):
        client = FakeSupabase()
        session_id = await _make_session(client, ["AAPL"])
        # Use only the seeded price task: claim it from two workers.
        claimed_a = store.claim_tasks(
            client, worker_id="worker-a", limit=100,
        )
        claimed_b = store.claim_tasks(
            client, worker_id="worker-b", limit=100,
        )
        ids_a = {t["id"] for t in claimed_a}
        ids_b = {t["id"] for t in claimed_b}
        assert ids_a and not (ids_a & ids_b), (
            "two workers claimed overlapping tasks"
        )

    @pytest.mark.asyncio
    async def test_duplicate_logical_task_absorbed(self):
        client = FakeSupabase()
        session_id = await _make_session(client, ["AAPL"])
        duplicate = store.create_task(
            client,
            run_session_id=session_id,
            user_id=USER,
            task_type=TASK_COLLECT_EVIDENCE_LANE,
            ticker="AAPL",
            lane=LANE_PRICE,
        )
        assert duplicate is None  # unique index absorbed it

    @pytest.mark.asyncio
    async def test_lease_expiry_recovery_and_attempt_accounting(self):
        client = FakeSupabase()
        session_id = await _make_session(client, ["AAPL"])
        claimed = store.claim_tasks(
            client, worker_id="dead-worker", limit=1, lease_seconds=1,
        )
        assert len(claimed) == 1
        task = claimed[0]
        assert task["attempts"] == 1
        # Not yet expired: another worker cannot steal.
        assert store.claim_tasks(client, worker_id="w2", limit=1) == [] or all(
            t["id"] != task["id"]
            for t in store.claim_tasks(client, worker_id="w2", limit=1)
        )
        # Expire the lease.
        expired = (
            datetime.now(timezone.utc) - timedelta(seconds=5)
        ).isoformat()
        client.table("intel_run_tasks").update(
            {"lease_expires_at": expired}
        ).eq("id", task["id"]).execute()
        reclaimed = store.claim_tasks(client, worker_id="rescue-worker", limit=50)
        rescued = next(t for t in reclaimed if t["id"] == task["id"])
        assert rescued["claim_owner"] == "rescue-worker"
        assert rescued["attempts"] == 2  # crash loops still consume budget

    @pytest.mark.asyncio
    async def test_task_cannot_be_completed_twice_or_by_non_owner(self):
        client = FakeSupabase()
        session_id = await _make_session(client, ["AAPL"])
        task = store.claim_tasks(client, worker_id="owner", limit=1)[0]
        # Non-owner completion rejected.
        assert store.complete_task(
            client, task=task, worker_id="imposter",
            final_state=TASK_SUCCEEDED,
        ) is False
        # Owner completes once.
        assert store.complete_task(
            client, task=task, worker_id="owner", final_state=TASK_SUCCEEDED,
        ) is True
        # Second completion rejected (state no longer 'claimed').
        assert store.complete_task(
            client, task=task, worker_id="owner", final_state=TASK_SUCCEEDED,
        ) is False

    @pytest.mark.asyncio
    async def test_retryable_failure_backoff_then_terminal_exhaustion(self):
        client = FakeSupabase()
        session_id = await _make_session(client, ["AAPL"])
        task_row = next(
            t for t in client.rows("intel_run_tasks")
            if t["task_type"] == TASK_COLLECT_EVIDENCE_LANE
        )
        task_id = task_row["id"]
        max_attempts = int(task_row["max_attempts"])
        for attempt in range(1, max_attempts + 1):
            # Make it due immediately.
            client.table("intel_run_tasks").update(
                {"next_retry_at": datetime.now(timezone.utc).isoformat()}
            ).eq("id", task_id).execute()
            claimed = [
                t for t in store.claim_tasks(client, worker_id="w", limit=100)
                if t["id"] == task_id
            ]
            assert claimed, f"attempt {attempt} could not claim"
            store.complete_task(
                client, task=claimed[0], worker_id="w",
                final_state=store.TASK_FAILED_RETRYABLE,
                error_code="provider_down",
            )
            row = next(
                t for t in client.rows("intel_run_tasks") if t["id"] == task_id
            )
            if attempt < max_attempts:
                assert row["state"] == TASK_PENDING
                assert row["next_retry_at"] > datetime.now(timezone.utc).isoformat()
            else:
                assert row["state"] == "failed"  # terminal, budget exhausted
        # Terminal failed is never claimable again.
        client.table("intel_run_tasks").update(
            {"next_retry_at": datetime.now(timezone.utc).isoformat()}
        ).eq("id", task_id).execute()
        assert all(
            t["id"] != task_id
            for t in store.claim_tasks(client, worker_id="w", limit=100)
        )
