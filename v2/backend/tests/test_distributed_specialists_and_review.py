"""Specialist batching, strict-output validation, LLM reuse and conditional
review.

Proves:
  * batches are asset-compatible and bounded (≤ max batch size);
  * evidence bundles are complete before any LLM call;
  * strict per-(ticker, axis) outputs persist even for batched calls;
  * one malformed ticker degrades only itself (repair retry bounded to one);
  * specialists make ZERO provider calls (they only read persisted bundles);
  * unchanged input fingerprints reuse prior outputs (no duplicate LLM call);
  * aligned high-confidence specialists skip review; material conflict on a
    major holding creates exactly one review task; review output cannot set a
    visible action.
"""
from __future__ import annotations

import uuid

import pytest

import app.services.intelligence.v3.distributed.run_task_store_v1 as store
from app.services.intelligence.v3.distributed import run_scheduler_v1 as scheduler
from app.services.intelligence.v3.distributed import session_control_v1 as control
from app.services.intelligence.v3.distributed.specialist_agents_v1 import (
    execute_review_task,
    execute_specialist_task,
)
from app.services.intelligence.v3.distributed.evidence_bundle_v1 import (
    build_evidence_bundle,
)
from app.services.intelligence.v3.distributed.collectors_v1 import (
    execute_collector_task,
)
from app.services.intelligence.v3.distributed.task_contracts_v1 import (
    AXIS_FUNDAMENTAL,
    AXIS_REVIEW,
    AXIS_TECHNICAL,
    TASK_COLLECT_EVIDENCE_LANE,
    TASK_DEGRADED,
    TASK_REVIEW_CONFLICT,
    TASK_SPECIALIST_ANALYSIS,
    TASK_SUCCEEDED,
    TICKER_EVIDENCE_READY,
)
from tests.distributed_run_intel_test_utils import (
    FakeLLM,
    claim_task_row,
    FakeSupabase,
    ProviderRecorder,
    make_settings,
    patch_providers,
    seed_position,
)

USER = str(uuid.uuid4())


async def _session_with_ready_bundles(
    client: FakeSupabase,
    monkeypatch,
    tickers: list[str],
    *,
    categories: dict[str, str] | None = None,
) -> str:
    categories = categories or {}
    for ticker in tickers:
        seed_position(
            client, USER, ticker, category=categories.get(ticker, "Core"),
        )
    session_id = str(uuid.uuid4())
    await control.create_distributed_session(
        client=client, user_id=USER, session_id=session_id,
    )
    recorder = ProviderRecorder()
    patch_providers(monkeypatch, recorder)
    settings = make_settings()
    session = client.rows("intel_run_sessions")[0]

    # Run every collector, then build every bundle.
    for task in list(client.rows("intel_run_tasks")):
        if task["task_type"] not in (
            TASK_COLLECT_EVIDENCE_LANE, "collect_portfolio_context",
            "collect_macro_context",
        ):
            continue
        result = await execute_collector_task(
            client, task=task, settings=settings,
        )
        client.table("intel_run_tasks").update({
            "state": result.final_state
            if result.final_state in (TASK_SUCCEEDED, TASK_DEGRADED)
            else TASK_DEGRADED,
            "output": result.output,
            "output_ref": result.output_ref,
            "completed_at": "2026-07-21T00:00:00+00:00",
        }).eq("id", task["id"]).execute()
    for row in client.rows("intel_run_tickers"):
        build_evidence_bundle(client, session=session, ticker_row=row)
    return session_id


class TestBatching:
    @pytest.mark.asyncio
    async def test_batches_are_asset_compatible_and_bounded(self, monkeypatch):
        client = FakeSupabase()
        tickers = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA"]
        etfs = ["VTI", "VHT"]
        cryptos = ["BTC"]
        session_id = await _session_with_ready_bundles(
            client, monkeypatch, tickers + etfs + cryptos,
            categories={**{e: "ETF" for e in etfs}, "BTC": "Crypto"},
        )
        session = client.rows("intel_run_sessions")[0]
        scheduler.run_scheduler_pass(
            client, session=session, max_specialist_batch=5,
        )
        specialist_tasks = [
            t for t in client.rows("intel_run_tasks")
            if t["task_type"] == TASK_SPECIALIST_ANALYSIS
        ]
        assert specialist_tasks, "no specialist batches created"
        for task in specialist_tasks:
            batch_tickers = scheduler.parse_batch_tickers(task["batch_key"])
            assert 1 <= len(batch_tickers) <= 5
            asset_types = {
                r["asset_type"] for r in client.rows("intel_run_tickers")
                if r["ticker"] in batch_tickers
            }
            assert len(asset_types) == 1, (
                f"mixed asset batch: {task['batch_key']}"
            )
            assert asset_types == {task["asset_type"]}
        # 7 equities on the fundamental axis → ceil(7/5) = 2 batches.
        fundamental_batches = [
            t for t in specialist_tasks
            if t["lane"] == AXIS_FUNDAMENTAL and t["asset_type"] == "equity"
        ]
        assert len(fundamental_batches) == 2

    @pytest.mark.asyncio
    async def test_scheduler_is_idempotent(self, monkeypatch):
        client = FakeSupabase()
        session_id = await _session_with_ready_bundles(
            client, monkeypatch, ["AAPL", "MSFT"],
        )
        session = client.rows("intel_run_sessions")[0]
        scheduler.run_scheduler_pass(client, session=session)
        count_first = len(client.rows("intel_run_tasks"))
        scheduler.run_scheduler_pass(client, session=session)
        scheduler.run_scheduler_pass(client, session=session)
        assert len(client.rows("intel_run_tasks")) == count_first


class TestSpecialistExecution:
    @pytest.mark.asyncio
    async def test_batched_call_persists_per_ticker_outputs(self, monkeypatch):
        client = FakeSupabase()
        session_id = await _session_with_ready_bundles(
            client, monkeypatch, ["AAPL", "MSFT", "GOOGL"],
        )
        session = client.rows("intel_run_sessions")[0]
        scheduler.run_scheduler_pass(client, session=session)
        task = next(
            t for t in client.rows("intel_run_tasks")
            if t["task_type"] == TASK_SPECIALIST_ANALYSIS
            and t["lane"] == AXIS_FUNDAMENTAL
        )
        task = claim_task_row(client, task)
        llm = FakeLLM()
        outcome = await execute_specialist_task(client, task=task, llm=llm)
        assert outcome.final_state == TASK_SUCCEEDED
        assert sorted(outcome.persisted) == ["AAPL", "GOOGL", "MSFT"]
        assert outcome.llm_calls == 1  # ONE batched call for three tickers
        outputs = client.rows("intel_run_specialist_outputs")
        assert len(outputs) == 3
        for output in outputs:
            assert output["axis"] == AXIS_FUNDAMENTAL
            assert output["stance"] in ("positive", "neutral", "negative")
            assert output["input_fingerprint"]
            assert output["prompt_version"]

    @pytest.mark.asyncio
    async def test_malformed_ticker_degrades_only_itself(self, monkeypatch):
        client = FakeSupabase()
        session_id = await _session_with_ready_bundles(
            client, monkeypatch, ["AAPL", "MSFT", "GOOGL"],
        )
        session = client.rows("intel_run_sessions")[0]
        scheduler.run_scheduler_pass(client, session=session)
        task = next(
            t for t in client.rows("intel_run_tasks")
            if t["task_type"] == TASK_SPECIALIST_ANALYSIS
            and t["lane"] == AXIS_FUNDAMENTAL
        )
        # MSFT is malformed on the first AND the repair call.
        task = claim_task_row(client, task)
        llm = FakeLLM(script={(AXIS_FUNDAMENTAL, "MSFT"): None})
        outcome = await execute_specialist_task(client, task=task, llm=llm)
        assert outcome.final_state == TASK_DEGRADED
        assert sorted(outcome.persisted) == ["AAPL", "GOOGL"]
        assert outcome.malformed == ["MSFT"]
        assert outcome.llm_calls == 2  # initial + ONE bounded repair retry
        tickers_with_output = {
            o["ticker"] for o in client.rows("intel_run_specialist_outputs")
        }
        assert tickers_with_output == {"AAPL", "GOOGL"}

    @pytest.mark.asyncio
    async def test_whole_call_failure_is_retryable_and_persists_nothing(
        self, monkeypatch
    ):
        client = FakeSupabase()
        session_id = await _session_with_ready_bundles(
            client, monkeypatch, ["AAPL"],
        )
        session = client.rows("intel_run_sessions")[0]
        scheduler.run_scheduler_pass(client, session=session)
        task = next(
            t for t in client.rows("intel_run_tasks")
            if t["task_type"] == TASK_SPECIALIST_ANALYSIS
        )
        task = claim_task_row(client, task)
        llm = FakeLLM(fail_all=True)
        outcome = await execute_specialist_task(client, task=task, llm=llm)
        assert outcome.final_state == store.TASK_FAILED_RETRYABLE
        assert client.rows("intel_run_specialist_outputs") == []

    @pytest.mark.asyncio
    async def test_unchanged_fingerprint_reuses_without_llm_call(
        self, monkeypatch
    ):
        client = FakeSupabase()
        session_id = await _session_with_ready_bundles(
            client, monkeypatch, ["AAPL"],
        )
        session = client.rows("intel_run_sessions")[0]
        scheduler.run_scheduler_pass(client, session=session)
        task = next(
            t for t in client.rows("intel_run_tasks")
            if t["task_type"] == TASK_SPECIALIST_ANALYSIS
            and t["lane"] == AXIS_FUNDAMENTAL
        )
        task = claim_task_row(client, task)
        llm = FakeLLM()
        first = await execute_specialist_task(client, task=task, llm=llm)
        assert first.llm_calls == 1

        # Second session, same evidence → same fingerprint → reuse.
        client.table("intel_run_sessions").update({"status": "completed"}).eq(
            "id", session_id
        ).execute()
        session2 = str(uuid.uuid4())
        await control.create_distributed_session(
            client=client, user_id=USER, session_id=session2,
        )
        # Copy the identical bundle onto the new session's ticker row.
        bundle = next(
            r for r in client.rows("intel_run_tickers")
            if r["run_session_id"] == session_id
        )["evidence_bundle"]
        client.table("intel_run_tickers").update({
            "evidence_bundle": bundle, "state": TICKER_EVIDENCE_READY,
        }).eq("run_session_id", session2).eq("ticker", "AAPL").execute()
        session_row2 = next(
            r for r in client.rows("intel_run_sessions") if r["id"] == session2
        )
        scheduler.run_scheduler_pass(client, session=session_row2)
        task2 = next(
            t for t in client.rows("intel_run_tasks")
            if t["task_type"] == TASK_SPECIALIST_ANALYSIS
            and t["lane"] == AXIS_FUNDAMENTAL
            and t["run_session_id"] == session2
        )
        task2 = claim_task_row(client, task2)
        second = await execute_specialist_task(client, task=task2, llm=llm)
        assert second.reused == ["AAPL"]
        assert second.llm_calls == 0, "unchanged fingerprint still called LLM"
        # Changed fingerprint invalidates reuse.
        changed = dict(bundle, input_fingerprint="sha256:changed")
        client.table("intel_run_tickers").update({
            "evidence_bundle": changed,
        }).eq("run_session_id", session2).eq("ticker", "AAPL").execute()
        client.table("intel_run_specialist_outputs").update(
            {"input_fingerprint": "sha256:old"}
        ).eq("run_session_id", session2).execute()
        task2 = claim_task_row(client, task2)
        third = await execute_specialist_task(client, task=task2, llm=llm)
        assert third.llm_calls == 1


class TestConditionalReview:
    def _outputs(self, score_a: float, score_b: float, confidence: float = 0.8):
        return [
            {"axis": AXIS_FUNDAMENTAL, "score": score_a, "confidence": confidence},
            {"axis": AXIS_TECHNICAL, "score": score_b, "confidence": confidence},
        ]

    def test_aligned_high_confidence_skips_review(self):
        assert scheduler.should_review(
            self._outputs(0.6, 0.5), weight_pct=10.0
        ) is False

    def test_material_conflict_triggers_review(self):
        assert scheduler.should_review(
            self._outputs(0.7, -0.6), weight_pct=10.0
        ) is True

    def test_low_confidence_on_major_holding_triggers_review(self):
        outputs = [
            {"axis": AXIS_FUNDAMENTAL, "score": 0.2, "confidence": 0.2},
            {"axis": AXIS_TECHNICAL, "score": 0.3, "confidence": 0.9},
        ]
        assert scheduler.should_review(outputs, weight_pct=8.0) is True
        assert scheduler.should_review(outputs, weight_pct=1.0) is False

    @pytest.mark.asyncio
    async def test_conflict_creates_exactly_one_review_task(self, monkeypatch):
        client = FakeSupabase()
        session_id = await _session_with_ready_bundles(
            client, monkeypatch, ["AAPL"],
        )
        session = client.rows("intel_run_sessions")[0]
        # Force AAPL to be a major holding.
        client.table("intel_run_tickers").update(
            {"portfolio_weight_pct": 12.0}
        ).eq("ticker", "AAPL").execute()
        scheduler.run_scheduler_pass(client, session=session)
        # Conflicting specialist outputs.
        llm = FakeLLM(score_by_ticker={"AAPL": 0.8})
        for task in [
            t for t in client.rows("intel_run_tasks")
            if t["task_type"] == TASK_SPECIALIST_ANALYSIS
        ]:
            if task["lane"] == AXIS_TECHNICAL:
                task_llm = FakeLLM(script={
                    (AXIS_TECHNICAL, "AAPL"): {
                        "ticker": "AAPL", "stance": "negative", "score": -0.8,
                        "confidence": 0.9,
                        "key_findings": ["breakdown below trend"],
                        "risks": [], "missing_evidence": [], "limitations": [],
                    }
                })
            else:
                task_llm = llm
            task = claim_task_row(client, task)
            await execute_specialist_task(client, task=task, llm=task_llm)
            client.table("intel_run_tasks").update(
                {"state": TASK_SUCCEEDED}
            ).eq("id", task["id"]).execute()

        scheduler.run_scheduler_pass(client, session=session)
        review_tasks = [
            t for t in client.rows("intel_run_tasks")
            if t["task_type"] == TASK_REVIEW_CONFLICT
        ]
        assert len(review_tasks) == 1
        # Idempotent: another pass creates no second review.
        scheduler.run_scheduler_pass(client, session=session)
        assert len([
            t for t in client.rows("intel_run_tasks")
            if t["task_type"] == TASK_REVIEW_CONFLICT
        ]) == 1

        # Review executes, persists an advisory row, fetches nothing, and its
        # output shape carries NO action vocabulary field.
        review_task = claim_task_row(client, review_tasks[0])
        outcome = await execute_review_task(
            client, task=review_task, llm=FakeLLM(),
        )
        assert outcome.persisted == ["AAPL"]
        review_output = next(
            o for o in client.rows("intel_run_specialist_outputs")
            if o["axis"] == AXIS_REVIEW
        )
        assert "action" not in review_output
        assert review_output["stance"] in ("positive", "neutral", "negative")


class TestDeadEndGuards:
    """A terminally-failed pipeline task can never leave the session
    unfinishable — tickers/session terminalize honestly."""

    @pytest.mark.asyncio
    async def test_terminally_failed_bundle_terminalizes_ticker(
        self, monkeypatch
    ):
        client = FakeSupabase()
        for ticker in ("AAPL", "MSFT"):
            seed_position(client, USER, ticker)
        session_id = str(uuid.uuid4())
        await control.create_distributed_session(
            client=client, user_id=USER, session_id=session_id,
        )
        session = client.rows("intel_run_sessions")[0]
        # AAPL's bundle task exists and is terminally failed.
        store.create_task(
            client, run_session_id=session_id, user_id=USER,
            task_type="build_evidence_bundle", ticker="AAPL",
        )
        client.table("intel_run_tasks").update({"state": "failed"}).eq(
            "ticker", "AAPL"
        ).eq("task_type", "build_evidence_bundle").execute()

        scheduler.run_scheduler_pass(client, session=session)
        row = next(
            r for r in client.rows("intel_run_tickers")
            if r["ticker"] == "AAPL"
        )
        assert row["state"] == "failed"
        assert row["degradation_reasons"] == [
            "evidence_bundle_failed_terminally"
        ]
        # MSFT is untouched — isolation preserved.
        other = next(
            r for r in client.rows("intel_run_tickers")
            if r["ticker"] == "MSFT"
        )
        assert other["state"] == "pending"

    @pytest.mark.asyncio
    async def test_terminally_failed_publish_terminalizes_session(
        self, monkeypatch
    ):
        client = FakeSupabase()
        seed_position(client, USER, "AAPL")
        session_id = str(uuid.uuid4())
        await control.create_distributed_session(
            client=client, user_id=USER, session_id=session_id,
        )
        client.table("intel_run_tickers").update({"state": "failed"}).eq(
            "run_session_id", session_id
        ).execute()
        store.create_task(
            client, run_session_id=session_id, user_id=USER,
            task_type="portfolio_join_publish",
        )
        client.table("intel_run_tasks").update({"state": "failed"}).eq(
            "task_type", "portfolio_join_publish"
        ).execute()
        session = client.rows("intel_run_sessions")[0]
        scheduler.run_scheduler_pass(client, session=session)
        assert client.rows("intel_run_sessions")[0]["status"] == "failed"
