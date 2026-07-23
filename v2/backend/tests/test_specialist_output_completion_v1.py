"""Haiku specialist output completion — production-failure fix regression.

Session 7c4069a1-cc07-4c1e-a7d4-3bea67dd206d froze 31 holdings and completed
with 14 decided / 17 NO CALL / 22 terminal task failures because Haiku
specialist batches (up to 5 tickers, ~350 tokens/ticker) frequently returned
verbose, Markdown-fenced, or truncated JSON, and the whole batch retried
through the durable task's full attempt budget instead of repairing just the
missing tickers.

Proves:
  * the compact-JSON prompt contract states its limits explicitly;
  * the Haiku-specific batch cap (2) is honored end to end and every
    eligible ticker is still scheduled exactly once;
  * the bounded per-call token budget scales 700..1800;
  * strict fenced-JSON extraction accepts clean/fenced/whitespace-padded
    JSON and rejects surrounding prose;
  * a batch with one valid + one malformed ticker persists the valid ticker
    and repairs ONLY the missing one;
  * a batch where every ticker is initially missing splits repair into
    individual per-ticker calls, bounded to at most 3 calls total for a
    two-ticker batch;
  * a quota/authentication failure makes exactly one provider call and
    triggers zero repair calls;
  * a peer ticker's successful output is never re-requested, even when
    another ticker in the same batch keeps failing (including via quota);
  * a specialist output produced by one model remains reusable under a
    different model's routing (Sonnet output reused under Haiku routing);
  * at scale, a malformed ticker never drags its batch peer into NO CALL;
  * against a REAL LLMClient (only `_single_call` stubbed, never `ask_json`
    itself): a fenced+truncated two-ticker batch never triggers LLMClient's
    own hidden same-model retry (`retry_truncated_response=False`), the
    complete batch is requested exactly once, every subsequent request is a
    single-ticker repair, at most 3 actual provider requests are made, and
    every max_tokens sent to the provider is within 700..1800; a legacy
    caller using the default argument still gets the internal retry;
  * an exhausted rate-limit/transient provider failure (not just
    quota/authentication) is never reinterpreted as ticker-level malformed
    JSON — it skips the repair loop and returns a retryable task outcome,
    without discarding an already-validated peer ticker.
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest

import app.services.agents.llm as llm_module
from app.services.agents.llm import (
    ERROR_CLASS_AUTHENTICATION,
    ERROR_CLASS_QUOTA,
    LLMClient,
    _extract_json,
)
from app.services.intelligence.v3.distributed import run_scheduler_v1 as scheduler
from app.services.intelligence.v3.distributed import run_task_store_v1 as store
from app.services.intelligence.v3.distributed import session_control_v1 as control
from app.services.intelligence.v3.distributed.specialist_agents_v1 import (
    SPECIALIST_MAX_TOKENS_PER_CALL,
    SPECIALIST_MIN_TOKENS_PER_CALL,
    SPECIALIST_SYSTEM_PROMPT,
    _specialist_token_budget,
    execute_specialist_task,
    validate_specialist_result,
)
from app.services.intelligence.v3.distributed.task_contracts_v1 import (
    AXIS_FUNDAMENTAL,
    SESSION_COMPLETED,
    SESSION_COMPLETED_WITH_GAPS,
    TASK_DEGRADED,
    TASK_SPECIALIST_ANALYSIS,
    TASK_SUCCEEDED,
    TICKER_DECIDED,
    TICKER_EVIDENCE_READY,
)
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
from tests.test_distributed_golden_run import _make_supervisor
from tests.test_distributed_specialists_and_review import (
    USER,
    _session_with_ready_bundles,
)


class TestCompactPromptContract:
    def test_states_no_markdown_and_no_commentary(self):
        prompt = SPECIALIST_SYSTEM_PROMPT
        assert "No markdown" in prompt
        assert "no code fences" in prompt
        assert "no commentary" in prompt

    def test_states_field_caps_and_string_length(self):
        prompt = SPECIALIST_SYSTEM_PROMPT
        # risks/missing_evidence/limitations are capped at 2, optionally empty.
        assert prompt.count("at most 2 items") == 3
        # key_findings has the same upper bound but must never be empty —
        # the validator rejects an entry outright if key_findings is empty,
        # so the prompt states a 1-2 range instead of a bare upper bound.
        assert "key_findings: 1-2 items" in prompt
        assert "never empty" in prompt
        assert "120 characters" in prompt
        assert "INVALID output" in prompt

    def test_forbids_visible_action_words(self):
        prompt = SPECIALIST_SYSTEM_PROMPT.lower()
        assert "buy/hold/trim/sell" in prompt


class TestTokenBudgetBounds:
    @pytest.mark.parametrize(
        "ticker_count,expected",
        [(1, 700), (2, 1300), (3, 1800), (10, 1800), (0, 700)],
    )
    def test_scales_and_clamps(self, ticker_count, expected):
        budget = _specialist_token_budget(ticker_count)
        assert budget == expected
        assert SPECIALIST_MIN_TOKENS_PER_CALL <= budget <= SPECIALIST_MAX_TOKENS_PER_CALL


class TestStrictFencedJsonExtraction:
    def test_clean_json(self):
        parsed, _ = _extract_json('{"results": []}', reject_prose=True)
        assert parsed == {"results": []}

    def test_json_language_fence(self):
        parsed, _ = _extract_json(
            '```json\n{"results": []}\n```', reject_prose=True,
        )
        assert parsed == {"results": []}

    def test_generic_fence(self):
        parsed, _ = _extract_json('```\n{"results": []}\n```', reject_prose=True)
        assert parsed == {"results": []}

    def test_whitespace_around_fence(self):
        parsed, _ = _extract_json(
            '  \n```json  \n  {"results": []}  \n\t```  \n', reject_prose=True,
        )
        assert parsed == {"results": []}

    def test_malformed_unfinished_fence_detected_as_truncated(self):
        parsed, debug = _extract_json(
            '```json\n{"results": [{"ticker": "AAPL"', reject_prose=True,
        )
        assert parsed is None
        assert debug["truncated_response_detected"] is True

    def test_surrounding_prose_is_rejected(self):
        parsed, _ = _extract_json(
            'Here is the result:\n{"results": []}\nHope that helps!',
            reject_prose=True,
        )
        assert parsed is None


class TestNoVisibleActionLeakage:
    def test_action_field_is_never_carried_through_validation(self):
        entry = {
            "ticker": "AAPL", "stance": "positive", "score": 0.5,
            "confidence": 0.7, "key_findings": ["f"], "risks": [],
            "missing_evidence": [], "limitations": [], "action": "buy",
        }
        normalized = validate_specialist_result(entry)
        assert normalized is not None
        assert "action" not in normalized


class TestHaikuBatchScheduling:
    @pytest.mark.asyncio
    async def test_haiku_batch_cap_covers_every_ticker_exactly_once(
        self, monkeypatch
    ):
        tickers = [f"EQ{i:02d}" for i in range(31)]
        client = FakeSupabase()
        session_id = await _session_with_ready_bundles(
            client, monkeypatch, tickers,
        )
        session = client.rows("intel_run_sessions")[0]
        scheduler.run_scheduler_pass(
            client, session=session, max_specialist_batch=2,
        )
        fundamental_batches = [
            t for t in client.rows("intel_run_tasks")
            if t["task_type"] == TASK_SPECIALIST_ANALYSIS
            and t["lane"] == AXIS_FUNDAMENTAL
        ]
        covered: list[str] = []
        for task in fundamental_batches:
            batch_tickers = scheduler.parse_batch_tickers(task["batch_key"])
            assert 1 <= len(batch_tickers) <= 2, "Haiku batch size exceeded"
            covered.extend(batch_tickers)
        assert sorted(covered) == sorted(tickers), (
            "scheduler must cover every eligible ticker exactly once"
        )
        assert len(covered) == len(set(covered)), "duplicate ticker scheduling"
        assert len(fundamental_batches) == 16  # ceil(31/2)


class TestPartialSuccessAndScopedRepair:
    @pytest.mark.asyncio
    async def test_one_valid_one_malformed_preserves_valid_and_repairs_only_missing(
        self, monkeypatch
    ):
        client = FakeSupabase()
        await _session_with_ready_bundles(client, monkeypatch, ["AAPL", "MSFT"])
        session = client.rows("intel_run_sessions")[0]
        scheduler.run_scheduler_pass(
            client, session=session, max_specialist_batch=2,
        )
        task = next(
            t for t in client.rows("intel_run_tasks")
            if t["task_type"] == TASK_SPECIALIST_ANALYSIS
            and t["lane"] == AXIS_FUNDAMENTAL
        )
        assert sorted(scheduler.parse_batch_tickers(task["batch_key"])) == [
            "AAPL", "MSFT",
        ]
        task = claim_task_row(client, task)
        llm = FakeLLM(script={(AXIS_FUNDAMENTAL, "MSFT"): None})
        outcome = await execute_specialist_task(client, task=task, llm=llm)

        assert outcome.persisted == ["AAPL"]
        assert outcome.malformed == ["MSFT"]
        assert outcome.final_state == TASK_DEGRADED
        assert outcome.repair_calls == 1
        assert outcome.llm_calls == 2
        assert llm.calls[0]["tickers"] == ["AAPL", "MSFT"]
        assert llm.calls[1]["tickers"] == ["MSFT"], (
            "repair call must request ONLY the missing ticker"
        )
        persisted_tickers = {
            o["ticker"] for o in client.rows("intel_run_specialist_outputs")
        }
        assert persisted_tickers == {"AAPL"}

    @pytest.mark.asyncio
    async def test_two_missing_tickers_split_into_individual_repair_calls(
        self, monkeypatch
    ):
        client = FakeSupabase()
        await _session_with_ready_bundles(client, monkeypatch, ["AAPL", "MSFT"])
        session = client.rows("intel_run_sessions")[0]
        scheduler.run_scheduler_pass(
            client, session=session, max_specialist_batch=2,
        )
        task = next(
            t for t in client.rows("intel_run_tasks")
            if t["task_type"] == TASK_SPECIALIST_ANALYSIS
            and t["lane"] == AXIS_FUNDAMENTAL
        )
        task = claim_task_row(client, task)
        llm = FakeLLM()
        real_ask_json = FakeLLM.ask_json
        state = {"n": 0}

        async def flaky_ask_json(self, system, user, max_tokens=1024,
                                  normalizer=None, metadata=None,
                                  reject_prose=False,
                                  retry_truncated_response=True,
                                  primary_max_attempts=4):
            state["n"] += 1
            if state["n"] == 1:
                # Initial batch call: both tickers missing/truncated.
                self.calls.append({
                    "axis": (metadata or {}).get("axis"), "tickers": ["AAPL", "MSFT"],
                    "metadata": metadata or {}, "prompt_chars": len(user),
                    "max_tokens": max_tokens,
                })
                return {"results": []}
            return await real_ask_json(
                self, system, user, max_tokens=max_tokens, normalizer=normalizer,
                metadata=metadata, reject_prose=reject_prose,
                retry_truncated_response=retry_truncated_response,
                primary_max_attempts=primary_max_attempts,
            )

        monkeypatch.setattr(FakeLLM, "ask_json", flaky_ask_json)
        outcome = await execute_specialist_task(client, task=task, llm=llm)

        assert outcome.final_state == TASK_SUCCEEDED
        assert sorted(outcome.persisted) == ["AAPL", "MSFT"]
        assert outcome.repair_calls == 2, "each missing ticker gets its own call"
        assert outcome.llm_calls == 3, "1 initial + 2 individual == bounded max"
        assert len(llm.calls[1]["tickers"]) == 1
        assert len(llm.calls[2]["tickers"]) == 1
        assert sorted(llm.calls[1]["tickers"] + llm.calls[2]["tickers"]) == [
            "AAPL", "MSFT",
        ]


class TestQuotaAuthFailureBehavior:
    @pytest.mark.asyncio
    async def test_quota_failure_makes_one_call_and_no_repair(self, monkeypatch):
        client = FakeSupabase()
        await _session_with_ready_bundles(client, monkeypatch, ["AAPL", "MSFT"])
        session = client.rows("intel_run_sessions")[0]
        scheduler.run_scheduler_pass(
            client, session=session, max_specialist_batch=2,
        )
        task = next(
            t for t in client.rows("intel_run_tasks")
            if t["task_type"] == TASK_SPECIALIST_ANALYSIS
            and t["lane"] == AXIS_FUNDAMENTAL
        )
        task = claim_task_row(client, task)
        llm = FakeLLM(error_classification_sequence=[ERROR_CLASS_QUOTA])
        outcome = await execute_specialist_task(client, task=task, llm=llm)

        assert outcome.llm_calls == 1
        assert outcome.repair_calls == 0
        assert outcome.quota_or_auth_failures == 1
        assert outcome.error == "specialist_provider_quota_or_auth_failure"
        assert outcome.final_state == store.TASK_FAILED_RETRYABLE
        assert client.rows("intel_run_specialist_outputs") == []

    @pytest.mark.asyncio
    async def test_authentication_failure_makes_one_call_and_no_repair(
        self, monkeypatch
    ):
        client = FakeSupabase()
        await _session_with_ready_bundles(client, monkeypatch, ["AAPL"])
        session = client.rows("intel_run_sessions")[0]
        scheduler.run_scheduler_pass(client, session=session)
        task = next(
            t for t in client.rows("intel_run_tasks")
            if t["task_type"] == TASK_SPECIALIST_ANALYSIS
            and t["lane"] == AXIS_FUNDAMENTAL
        )
        task = claim_task_row(client, task)
        llm = FakeLLM(
            error_classification_sequence=[ERROR_CLASS_AUTHENTICATION],
        )
        outcome = await execute_specialist_task(client, task=task, llm=llm)
        assert outcome.llm_calls == 1
        assert outcome.repair_calls == 0
        assert outcome.final_state == store.TASK_FAILED_RETRYABLE

    @pytest.mark.asyncio
    async def test_quota_failure_mid_repair_keeps_valid_peer_and_stops(
        self, monkeypatch
    ):
        client = FakeSupabase()
        await _session_with_ready_bundles(client, monkeypatch, ["AAPL", "MSFT"])
        session = client.rows("intel_run_sessions")[0]
        scheduler.run_scheduler_pass(
            client, session=session, max_specialist_batch=2,
        )
        task = next(
            t for t in client.rows("intel_run_tasks")
            if t["task_type"] == TASK_SPECIALIST_ANALYSIS
            and t["lane"] == AXIS_FUNDAMENTAL
        )
        task = claim_task_row(client, task)
        # MSFT malformed on the initial call; its individual repair hits quota.
        llm = FakeLLM(
            script={(AXIS_FUNDAMENTAL, "MSFT"): None},
            error_classification_sequence=[None, ERROR_CLASS_QUOTA],
        )
        outcome = await execute_specialist_task(client, task=task, llm=llm)

        assert outcome.persisted == ["AAPL"], "peer ticker's success is never lost"
        assert outcome.malformed == ["MSFT"]
        assert outcome.llm_calls == 2
        assert outcome.repair_calls == 1
        assert outcome.quota_or_auth_failures == 1
        assert outcome.final_state == TASK_DEGRADED, (
            "a peer ticker's success must never turn into a retryable "
            "whole-task failure"
        )
        assert llm.calls[1]["tickers"] == ["MSFT"], (
            "AAPL must never be re-requested by the repair call"
        )


class TestReuseAcrossModels:
    @pytest.mark.asyncio
    async def test_prior_output_reused_regardless_of_producing_model(
        self, monkeypatch
    ):
        client = FakeSupabase()
        session_id = await _session_with_ready_bundles(client, monkeypatch, ["AAPL"])
        session = client.rows("intel_run_sessions")[0]
        scheduler.run_scheduler_pass(client, session=session)
        task = next(
            t for t in client.rows("intel_run_tasks")
            if t["task_type"] == TASK_SPECIALIST_ANALYSIS
            and t["lane"] == AXIS_FUNDAMENTAL
        )
        task = claim_task_row(client, task)
        sonnet_llm = FakeLLM()
        sonnet_llm.primary_model = "claude-sonnet-4-6"
        first = await execute_specialist_task(client, task=task, llm=sonnet_llm)
        assert first.persisted == ["AAPL"]
        prior_output = next(
            o for o in client.rows("intel_run_specialist_outputs")
            if o["ticker"] == "AAPL"
        )
        assert prior_output["model"] == "claude-sonnet-4-6"

        # New session, unchanged evidence fingerprint, Haiku routing.
        client.table("intel_run_sessions").update({"status": "completed"}).eq(
            "id", session_id,
        ).execute()
        session2 = str(uuid.uuid4())
        await control.create_distributed_session(
            client=client, user_id=USER, session_id=session2,
        )
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
        haiku_llm = FakeLLM()
        haiku_llm.primary_model = "claude-haiku-4-5-20251001"
        second = await execute_specialist_task(client, task=task2, llm=haiku_llm)
        assert second.reused == ["AAPL"]
        assert second.llm_calls == 0, (
            "a Sonnet-produced output must remain reusable under Haiku routing"
        )


class TestNormalSpecialistsNeverEscalateToSonnet:
    @pytest.mark.asyncio
    async def test_specialist_llm_client_has_no_fallback_model(self, monkeypatch):
        client = FakeSupabase()
        settings = make_settings()
        supervisor = WorkerSupervisor(client=client, settings=settings)
        assert supervisor.specialist_llm.fallback_model is None
        assert "haiku" in supervisor.specialist_llm.model.lower()

    def test_effective_batch_cap_uses_haiku_setting_for_default_model(self):
        settings = make_settings()
        supervisor = WorkerSupervisor(client=FakeSupabase(), settings=settings)
        assert supervisor._effective_specialist_batch_cap() == 2

    def test_effective_batch_cap_falls_back_to_global_max_for_non_haiku_model(self):
        settings = make_settings(
            intel_v3_distributed_specialist_model="claude-sonnet-5",
            intel_v3_distributed_max_specialist_batch=5,
        )
        supervisor = WorkerSupervisor(client=FakeSupabase(), settings=settings)
        assert supervisor._effective_specialist_batch_cap() == 5

    def test_effective_batch_cap_never_exceeds_global_architectural_max(self):
        settings = make_settings(
            intel_v3_distributed_haiku_max_specialist_batch=99,
            intel_v3_distributed_max_specialist_batch=5,
        )
        supervisor = WorkerSupervisor(client=FakeSupabase(), settings=settings)
        assert supervisor._effective_specialist_batch_cap() == 5


class TestPeerIsolationAtScale:
    """A malformed specialist ticker must never drag its batch peer — or any
    unrelated ticker — into NO CALL. Golden-shaped at 30 equities (15 batches
    of 2 under the Haiku cap), half deliberately malformed on ONE axis."""

    @pytest.mark.asyncio
    async def test_malformed_peers_never_cause_clean_ticker_no_call(
        self, monkeypatch
    ):
        client = FakeSupabase()
        tickers = [f"EQ{i:02d}" for i in range(30)]
        for i, ticker in enumerate(tickers):
            seed_position(client, USER, ticker, close_price=100 + i)
        # Every EVEN-indexed ticker is permanently malformed on fundamental —
        # each 2-ticker batch pairs one malformed ticker with one clean peer.
        malformed = {t for i, t in enumerate(tickers) if i % 2 == 0}
        clean = [t for t in tickers if t not in malformed]

        recorder = ProviderRecorder()
        patch_providers(monkeypatch, recorder)
        script = {(AXIS_FUNDAMENTAL, t): None for t in malformed}
        llm = FakeLLM(script=script)
        supervisor = _make_supervisor(client, llm)

        session_id = str(uuid.uuid4())
        await control.create_distributed_session(
            client=client, user_id=USER, session_id=session_id,
        )
        await drive_supervisor_to_completion(supervisor)

        session = client.rows("intel_run_sessions")[0]
        assert session["status"] in (SESSION_COMPLETED, SESSION_COMPLETED_WITH_GAPS)

        by_ticker = {r["ticker"]: r for r in client.rows("intel_run_tickers")}
        for ticker in clean:
            assert by_ticker[ticker]["state"] == TICKER_DECIDED, (
                f"{ticker} must never be NO CALL solely because its batch "
                "peer's specialist output was malformed"
            )
        # Every ticker (including the deliberately-malformed ones) still
        # reaches a terminal state — no task graph is left stuck/lost.
        unfinished = [
            t for t in client.rows("intel_run_tasks")
            if t["state"] not in ("succeeded", "degraded", "failed", "cancelled")
        ]
        assert unfinished == []
        assert all(
            r["state"] in ("decided", "no_call", "failed")
            for r in client.rows("intel_run_tickers")
        )


class _SingleCallStub:
    """Records every REAL `LLMClient._single_call` invocation (the actual
    Anthropic provider-request seam) and replays a scripted sequence of
    responses. A response entry that is an exception instance is raised
    instead of returned. The last entry repeats for any call beyond the
    scripted length (so a single exception can stand in for an exhausted
    same-call backoff loop)."""

    def __init__(self, responses: list):
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, model, system, user, max_tokens):
        # Assigned directly as an instance attribute (not a class attribute),
        # so no descriptor/self-binding applies — `self` here is the stub,
        # and LLMClient's `self._single_call(model, system, user, max_tokens)`
        # call site passes exactly these four positional args.
        index = len(self.calls)
        self.calls.append({"model": model, "user": user, "max_tokens": max_tokens})
        item = self._responses[min(index, len(self._responses) - 1)]
        if isinstance(item, BaseException):
            raise item
        return item


def _patch_fast_backoff(monkeypatch):
    """LLMClient's 429/rate-limit backoff sleeps real seconds — patch it to
    a no-op so a test that exhausts the retry loop stays fast."""
    async def _fast_sleep(_seconds):
        return None

    monkeypatch.setattr(llm_module.asyncio, "sleep", _fast_sleep)


def _real_haiku_client() -> LLMClient:
    return LLMClient(
        api_key="test-key", model="claude-haiku-4-5-20251001",
        fallback_model=None,
    )


class TestRealProviderCallCounting:
    """Counts ACTUAL Anthropic provider requests (`LLMClient._single_call`),
    never `ask_json()` wrapper invocations — proves LLMClient's internal
    truncation retry cannot hide an extra full-batch provider call inside a
    specialist task's bounded repair budget."""

    @pytest.mark.asyncio
    async def test_truncated_batch_never_retries_internally_and_stays_bounded(
        self, monkeypatch
    ):
        client = FakeSupabase()
        await _session_with_ready_bundles(client, monkeypatch, ["AAPL", "MSFT"])
        session = client.rows("intel_run_sessions")[0]
        scheduler.run_scheduler_pass(
            client, session=session, max_specialist_batch=2,
        )
        task = next(
            t for t in client.rows("intel_run_tasks")
            if t["task_type"] == TASK_SPECIALIST_ANALYSIS
            and t["lane"] == AXIS_FUNDAMENTAL
        )
        task = claim_task_row(client, task)

        valid_aapl = (
            '{"results": [{"ticker": "AAPL", "stance": "positive", '
            '"score": 0.4, "confidence": 0.7, "key_findings": ["ok"], '
            '"risks": [], "missing_evidence": [], "limitations": []}]}'
        )
        valid_msft = (
            '{"results": [{"ticker": "MSFT", "stance": "neutral", '
            '"score": 0.0, "confidence": 0.6, "key_findings": ["ok"], '
            '"risks": [], "missing_evidence": [], "limitations": []}]}'
        )
        stub = _SingleCallStub([
            # Fenced AND truncated — the exact production failure shape.
            '```json\n{"results": [{"ticker": "AAPL"',
            valid_aapl,
            valid_msft,
        ])
        llm = _real_haiku_client()
        monkeypatch.setattr(llm, "_single_call", stub)

        outcome = await execute_specialist_task(client, task=task, llm=llm)

        assert outcome.final_state == TASK_SUCCEEDED
        assert sorted(outcome.persisted) == ["AAPL", "MSFT"]
        assert outcome.repair_calls == 2
        assert outcome.llm_calls == 3
        assert len(stub.calls) == 3, (
            "LLMClient's internal truncation retry must not add a hidden "
            "4th actual provider request"
        )
        for call in stub.calls:
            assert 700 <= call["max_tokens"] <= 1800, (
                f"provider request max_tokens {call['max_tokens']} outside "
                "the 700-1800 ceiling"
            )

        first_user = stub.calls[0]["user"]
        assert "AAPL" in first_user and "MSFT" in first_user, (
            "the complete batch must be requested exactly once, up front"
        )
        for call in stub.calls[1:]:
            has_aapl = "AAPL" in call["user"]
            has_msft = "MSFT" in call["user"]
            assert has_aapl != has_msft, (
                "every request after the first must be an individual "
                "single-ticker repair, never both tickers again"
            )

    @pytest.mark.asyncio
    async def test_legacy_caller_default_keeps_internal_truncation_retry(
        self, monkeypatch
    ):
        stub = _SingleCallStub([
            '{"ok": true, "value": "trunc',       # truncated first attempt
            '{"ok": true, "value": "complete"}',  # succeeds on internal retry
        ])
        llm = _real_haiku_client()
        monkeypatch.setattr(llm, "_single_call", stub)

        result = await llm.ask_json("system", "user prompt", max_tokens=1000)

        assert result == {"ok": True, "value": "complete"}
        assert len(stub.calls) == 2, (
            "the default retry_truncated_response=True must preserve the "
            "existing internal retry for non-specialist callers"
        )
        assert stub.calls[1]["max_tokens"] > stub.calls[0]["max_tokens"]


class TestProviderFailureRepairGuard:
    """An exhausted rate-limit/transient provider failure — not just
    quota/authentication — must never be reinterpreted as ticker-level
    malformed JSON eligible for the specialist's per-ticker repair loop."""

    @pytest.mark.asyncio
    async def test_exhausted_rate_limit_skips_repair_and_returns_retryable(
        self, monkeypatch
    ):
        _patch_fast_backoff(monkeypatch)
        client = FakeSupabase()
        await _session_with_ready_bundles(client, monkeypatch, ["AAPL"])
        session = client.rows("intel_run_sessions")[0]
        scheduler.run_scheduler_pass(client, session=session)
        task = next(
            t for t in client.rows("intel_run_tasks")
            if t["task_type"] == TASK_SPECIALIST_ANALYSIS
            and t["lane"] == AXIS_FUNDAMENTAL
        )
        task = claim_task_row(client, task)

        stub = _SingleCallStub([
            Exception("rate_limit_error: rate limited, slow down"),
        ])
        llm = _real_haiku_client()
        monkeypatch.setattr(llm, "_single_call", stub)

        outcome = await execute_specialist_task(client, task=task, llm=llm)

        assert outcome.repair_calls == 0, (
            "an exhausted rate-limit failure must never trigger a "
            "ticker-level repair call"
        )
        assert outcome.llm_calls == 1
        assert len(stub.calls) == 1, (
            "a specialist ask_json() call must cost exactly ONE actual "
            "provider request on a rate-limit/transient failure — "
            "primary_max_attempts=1 hands retry authority to the durable "
            "task, not LLMClient's own 4-attempt backoff loop"
        )
        assert outcome.quota_or_auth_failures == 0
        assert outcome.error == "specialist_provider_call_failed"
        assert outcome.final_state == store.TASK_FAILED_RETRYABLE
        assert client.rows("intel_run_specialist_outputs") == []

    @pytest.mark.asyncio
    async def test_provider_failure_during_repair_preserves_valid_peer(
        self, monkeypatch
    ):
        _patch_fast_backoff(monkeypatch)
        client = FakeSupabase()
        await _session_with_ready_bundles(client, monkeypatch, ["AAPL", "MSFT"])
        session = client.rows("intel_run_sessions")[0]
        scheduler.run_scheduler_pass(
            client, session=session, max_specialist_batch=2,
        )
        task = next(
            t for t in client.rows("intel_run_tasks")
            if t["task_type"] == TASK_SPECIALIST_ANALYSIS
            and t["lane"] == AXIS_FUNDAMENTAL
        )
        task = claim_task_row(client, task)

        valid_aapl_only = (
            '{"results": [{"ticker": "AAPL", "stance": "positive", '
            '"score": 0.4, "confidence": 0.7, "key_findings": ["ok"], '
            '"risks": [], "missing_evidence": [], "limitations": []}]}'
        )
        rate_limit_exc = Exception("rate_limit_error: rate limited, slow down")
        stub = _SingleCallStub([valid_aapl_only, rate_limit_exc])
        llm = _real_haiku_client()
        monkeypatch.setattr(llm, "_single_call", stub)

        outcome = await execute_specialist_task(client, task=task, llm=llm)

        assert outcome.persisted == ["AAPL"], "peer ticker's success is never lost"
        assert outcome.malformed == ["MSFT"]
        assert outcome.repair_calls == 1
        assert outcome.llm_calls == 2
        assert len(stub.calls) == 2, (
            "exactly two actual provider requests total: the initial batch "
            "call plus ONE failed repair attempt — primary_max_attempts=1 "
            "means the failed repair never retries internally"
        )
        assert outcome.error is None
        assert outcome.final_state == TASK_DEGRADED
        assert outcome.quota_or_auth_failures == 0
        # AAPL's already-valid result must never be re-requested by the
        # MSFT repair's (failing) provider calls.
        for call in stub.calls[1:]:
            assert "AAPL" not in call["user"]

    @pytest.mark.asyncio
    async def test_quota_failure_makes_exactly_one_actual_provider_request(
        self, monkeypatch
    ):
        client = FakeSupabase()
        await _session_with_ready_bundles(client, monkeypatch, ["AAPL"])
        session = client.rows("intel_run_sessions")[0]
        scheduler.run_scheduler_pass(client, session=session)
        task = next(
            t for t in client.rows("intel_run_tasks")
            if t["task_type"] == TASK_SPECIALIST_ANALYSIS
            and t["lane"] == AXIS_FUNDAMENTAL
        )
        task = claim_task_row(client, task)

        stub = _SingleCallStub([
            Exception("authentication_error: insufficient_quota, credit balance too low"),
        ])
        llm = _real_haiku_client()
        monkeypatch.setattr(llm, "_single_call", stub)

        outcome = await execute_specialist_task(client, task=task, llm=llm)

        assert len(stub.calls) == 1, (
            "a quota/authentication failure must never be retried against "
            "the same model — exactly one actual provider request"
        )
        assert outcome.repair_calls == 0
        assert outcome.quota_or_auth_failures == 1
        assert outcome.error == "specialist_provider_quota_or_auth_failure"
        assert outcome.final_state == store.TASK_FAILED_RETRYABLE


class TestLegacyCallerRetainsDefaultBackoff:
    """`primary_max_attempts` defaults to 4 (legacy behavior) — only
    distributed specialists opt into `primary_max_attempts=1`."""

    @pytest.mark.asyncio
    async def test_legacy_default_keeps_multiple_backoff_attempts(
        self, monkeypatch
    ):
        _patch_fast_backoff(monkeypatch)
        stub = _SingleCallStub([
            Exception("rate_limit_error: rate limited, slow down"),
        ])
        llm = _real_haiku_client()
        monkeypatch.setattr(llm, "_single_call", stub)

        result = await llm.ask_json("system", "user prompt", max_tokens=1000)

        assert result == {}
        assert len(stub.calls) == 4, (
            "a legacy caller that does not pass primary_max_attempts must "
            "retain the existing 4-attempt same-model backoff loop"
        )
