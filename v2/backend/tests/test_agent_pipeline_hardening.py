"""SEV-1 regressions: single-run lock, light cache, LLM failover, normalisation.

Covers the hardening work on the agent analysis pipeline so future refactors
can't silently regress the guarantees the Intel tab depends on:

  * `queue_agent_run` reuses an in-flight run for the same user (lock)
  * `queue_agent_run` reuses a <2 min old completed run (light cache)
  * `queue_agent_run` creates a new run otherwise
  * `LLMClient._trim_prompt` shortens prompts for Haiku fallback
  * `LLMClient.ask_json` falls back to the secondary model on primary failure
  * `LLMClient.ask_json` returns `{}` — never None — on total failure
  * `_force_fail_run` always writes a summary so the UI never sees blanks
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4
from unittest.mock import MagicMock, patch

import pytest


# ── helpers ──────────────────────────────────────────────────────────────────


def _mock_supabase(agent_runs_rows=None, users_row=None):
    """Build a MagicMock that mimics the supabase-py fluent interface used
    by RecommendationService.queue_agent_run.
    """
    client = MagicMock()

    def _table(name):
        tbl = MagicMock()
        if name == "agent_runs":
            # Select chain → .eq(user_id).order(...).limit(1).execute()
            exec_mock = MagicMock()
            exec_mock.data = agent_runs_rows or []
            (
                tbl.select.return_value
                .eq.return_value
                .order.return_value
                .limit.return_value
                .execute.return_value
            ) = exec_mock
            # Insert chain → .execute() returns the inserted row
            new_id = str(uuid4())
            (
                tbl.insert.return_value
                .execute.return_value
            ).data = [{"id": new_id}]
            tbl._new_id = new_id
        elif name == "users":
            exec_mock = MagicMock()
            exec_mock.data = users_row or {"deposit_amount": 900.0}
            (
                tbl.select.return_value
                .eq.return_value
                .single.return_value
                .execute.return_value
            ) = exec_mock
        return tbl

    client.table.side_effect = _table
    return client


# ── Single-run lock + light cache ────────────────────────────────────────────


class TestQueueAgentRunLock:
    """queue_agent_run must short-circuit concurrent/recent runs."""

    @pytest.mark.asyncio
    async def test_reuses_running_run(self, monkeypatch):
        from app.services.recommendation_engine import RecommendationService

        existing_id = str(uuid4())
        existing_rows = [{
            "id": existing_id,
            "status": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
        }]
        mock_client = _mock_supabase(agent_runs_rows=existing_rows)
        monkeypatch.setattr(
            "app.services.recommendation_engine.get_supabase_client",
            lambda: mock_client,
        )

        svc = RecommendationService(user_id=uuid4())
        job_id, is_new = await svc.queue_agent_run(deposit_amount=500.0)

        assert job_id == existing_id
        assert is_new is False

    @pytest.mark.asyncio
    async def test_reuses_queued_run(self, monkeypatch):
        from app.services.recommendation_engine import RecommendationService

        existing_id = str(uuid4())
        existing_rows = [{
            "id": existing_id,
            "status": "queued",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
        }]
        mock_client = _mock_supabase(agent_runs_rows=existing_rows)
        monkeypatch.setattr(
            "app.services.recommendation_engine.get_supabase_client",
            lambda: mock_client,
        )

        svc = RecommendationService(user_id=uuid4())
        job_id, is_new = await svc.queue_agent_run(deposit_amount=500.0)

        assert job_id == existing_id
        assert is_new is False

    @pytest.mark.asyncio
    async def test_reuses_recently_completed_run(self, monkeypatch):
        from app.services.recommendation_engine import RecommendationService

        existing_id = str(uuid4())
        finished_at = datetime.now(timezone.utc).isoformat()
        existing_rows = [{
            "id": existing_id,
            "status": "completed",
            "started_at": finished_at,
            "finished_at": finished_at,
        }]
        mock_client = _mock_supabase(agent_runs_rows=existing_rows)
        monkeypatch.setattr(
            "app.services.recommendation_engine.get_supabase_client",
            lambda: mock_client,
        )

        svc = RecommendationService(user_id=uuid4())
        job_id, is_new = await svc.queue_agent_run(deposit_amount=500.0)

        assert job_id == existing_id
        assert is_new is False

    @pytest.mark.asyncio
    async def test_creates_new_run_when_cache_stale(self, monkeypatch):
        from app.services.recommendation_engine import RecommendationService

        # Last completed run is 5 minutes old — outside the 2-minute cache.
        stale_id = str(uuid4())
        stale_time = (
            datetime.now(timezone.utc) - timedelta(minutes=5)
        ).isoformat()
        existing_rows = [{
            "id": stale_id,
            "status": "completed",
            "started_at": stale_time,
            "finished_at": stale_time,
        }]
        mock_client = _mock_supabase(agent_runs_rows=existing_rows)
        monkeypatch.setattr(
            "app.services.recommendation_engine.get_supabase_client",
            lambda: mock_client,
        )
        # Prevent real orchestrator construction
        orch = MagicMock()
        orch.create_run = MagicMock(return_value=_async_value("new-run-id"))
        monkeypatch.setattr(
            "app.services.agents.job_runner.build_orchestrator",
            lambda **_: orch,
        )

        svc = RecommendationService(user_id=uuid4())
        job_id, is_new = await svc.queue_agent_run(deposit_amount=500.0)

        assert job_id == "new-run-id"
        assert is_new is True

    @pytest.mark.asyncio
    async def test_creates_new_run_after_failed(self, monkeypatch):
        from app.services.recommendation_engine import RecommendationService

        failed_id = str(uuid4())
        existing_rows = [{
            "id": failed_id,
            "status": "failed",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }]
        mock_client = _mock_supabase(agent_runs_rows=existing_rows)
        monkeypatch.setattr(
            "app.services.recommendation_engine.get_supabase_client",
            lambda: mock_client,
        )
        orch = MagicMock()
        orch.create_run = MagicMock(return_value=_async_value("retry-id"))
        monkeypatch.setattr(
            "app.services.agents.job_runner.build_orchestrator",
            lambda **_: orch,
        )

        svc = RecommendationService(user_id=uuid4())
        job_id, is_new = await svc.queue_agent_run(deposit_amount=500.0)

        assert job_id == "retry-id"
        assert is_new is True

    @pytest.mark.asyncio
    async def test_creates_new_run_when_no_prior_run(self, monkeypatch):
        from app.services.recommendation_engine import RecommendationService

        mock_client = _mock_supabase(agent_runs_rows=[])
        monkeypatch.setattr(
            "app.services.recommendation_engine.get_supabase_client",
            lambda: mock_client,
        )
        orch = MagicMock()
        orch.create_run = MagicMock(return_value=_async_value("first-run"))
        monkeypatch.setattr(
            "app.services.agents.job_runner.build_orchestrator",
            lambda **_: orch,
        )

        svc = RecommendationService(user_id=uuid4())
        job_id, is_new = await svc.queue_agent_run(deposit_amount=500.0)

        assert job_id == "first-run"
        assert is_new is True


# ── _within_last helper ──────────────────────────────────────────────────────


class TestWithinLast:
    def test_recent_timestamp(self):
        from app.services.recommendation_engine import _within_last
        now = datetime.now(timezone.utc).isoformat()
        assert _within_last(now, seconds=120) is True

    def test_old_timestamp(self):
        from app.services.recommendation_engine import _within_last
        past = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        assert _within_last(past, seconds=120) is False

    def test_naive_isoformat_coerced_to_utc(self):
        from app.services.recommendation_engine import _within_last
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        # Naive timestamps are treated as UTC.
        assert _within_last(now, seconds=120) is True

    def test_empty_string(self):
        from app.services.recommendation_engine import _within_last
        assert _within_last("", seconds=120) is False

    def test_malformed_timestamp(self):
        from app.services.recommendation_engine import _within_last
        assert _within_last("not-a-date", seconds=120) is False


# ── LLMClient hardening ──────────────────────────────────────────────────────


class TestLLMClientHardening:
    def test_trim_prompt_shortens(self):
        from app.services.agents.llm import _trim_prompt
        long = "A" * 5000
        out = _trim_prompt(long, ratio=0.5)
        assert len(out) < len(long)
        assert "trimmed" in out.lower()

    def test_trim_prompt_noop_for_short_text(self):
        from app.services.agents.llm import _trim_prompt
        short = "hello"
        assert _trim_prompt(short, ratio=0.5) == short

    def test_trim_prompt_noop_when_ratio_full(self):
        from app.services.agents.llm import _trim_prompt
        text = "A" * 2000
        assert _trim_prompt(text, ratio=1.0) == text

    def test_status_code_from_exc_on_attr(self):
        from app.services.agents.llm import _status_code_from_exc
        exc = Exception("rate limited")
        exc.status_code = 429
        assert _status_code_from_exc(exc) == 429

    def test_status_code_from_exc_on_response(self):
        from app.services.agents.llm import _status_code_from_exc
        resp = MagicMock()
        resp.status_code = 529
        exc = Exception("overloaded")
        exc.response = resp
        assert _status_code_from_exc(exc) == 529

    def test_status_code_missing_returns_none(self):
        from app.services.agents.llm import _status_code_from_exc
        assert _status_code_from_exc(Exception("boom")) is None

    @pytest.mark.asyncio
    async def test_ask_json_returns_empty_without_api_key(self):
        from app.services.agents.llm import LLMClient
        client = LLMClient(api_key="")
        out = await client.ask_json("sys", "user")
        assert out == {}

    @pytest.mark.asyncio
    async def test_ask_json_falls_back_on_primary_failure(self, monkeypatch):
        """When the primary model raises a non-retryable error, the client
        must fall back to the secondary model automatically."""
        from app.services.agents.llm import LLMClient

        client = LLMClient(api_key="fake-key")

        calls: list[str] = []

        async def _fake_single_call(model, system, user, max_tokens):
            calls.append(model)
            if model == client.model:
                raise RuntimeError("primary exploded")
            return '{"ok": true, "source": "fallback"}'

        monkeypatch.setattr(client, "_single_call", _fake_single_call)

        out = await client.ask_json("sys", "user", max_tokens=800)

        assert out == {"ok": True, "source": "fallback"}
        assert calls[0] == client.model
        assert calls[-1] == client.fallback_model

    @pytest.mark.asyncio
    async def test_ask_json_returns_empty_when_both_models_fail(self, monkeypatch):
        from app.services.agents.llm import LLMClient

        client = LLMClient(api_key="fake-key")

        async def _fake_single_call(model, system, user, max_tokens):
            raise RuntimeError("both broken")

        monkeypatch.setattr(client, "_single_call", _fake_single_call)

        out = await client.ask_json("sys", "user")
        # Must be a dict — the contract is NEVER return None.
        assert out == {}

    @pytest.mark.asyncio
    async def test_ask_json_retries_on_429_then_succeeds(self, monkeypatch):
        """A 429 should trigger an exponential-backoff retry on the same model."""
        from app.services.agents.llm import LLMClient
        import app.services.agents.llm as llm_mod

        # Shrink the backoff schedule so the test runs fast.
        monkeypatch.setattr(llm_mod, "_BACKOFF_SCHEDULE_S", (0.01, 0.01, 0.01, 0.01))

        client = LLMClient(api_key="fake-key")
        attempts: list[int] = []

        class RateLimit(Exception):
            pass

        async def _fake_single_call(model, system, user, max_tokens):
            attempts.append(len(attempts))
            if len(attempts) == 1:
                exc = RateLimit("rate_limit_error")
                exc.status_code = 429
                raise exc
            return '{"ok": true}'

        monkeypatch.setattr(client, "_single_call", _fake_single_call)

        out = await client.ask_json("sys", "user")
        assert out == {"ok": True}
        assert len(attempts) >= 2

    @pytest.mark.asyncio
    async def test_ask_json_primary_timeout_triggers_fallback(self, monkeypatch):
        from app.services.agents.llm import LLMClient
        import app.services.agents.llm as llm_mod

        # Make primary time out instantly
        monkeypatch.setattr(llm_mod, "PRIMARY_TIMEOUT_S", 0.01)
        monkeypatch.setattr(llm_mod, "FALLBACK_TIMEOUT_S", 5.0)

        client = LLMClient(api_key="fake-key")
        calls: list[str] = []

        async def _fake_single_call(model, system, user, max_tokens):
            calls.append(model)
            if model == client.model:
                await asyncio.sleep(1.0)  # force the wait_for timeout
                return '{"primary": true}'
            return '{"ok": true, "source": "fallback"}'

        monkeypatch.setattr(client, "_single_call", _fake_single_call)

        out = await client.ask_json("sys", "user")
        assert out == {"ok": True, "source": "fallback"}
        assert client.fallback_model in calls


# ── Shape guarantees — ensure fallback dict matches the JSON contract ────────


class TestFallbackShape:
    """The agent run row must always provide the shape the UI expects."""

    def test_fallback_pm_summary_is_non_empty(self):
        from app.services.agents.portfolio_manager import _fallback_summary
        from app.services.agents.state import AgentState

        state = AgentState(
            user_id="u", run_id="r", tickers=["AAPL"],
        )
        # Empty insights is allowed — summary must still be a non-empty string.
        summary = _fallback_summary(state)
        assert isinstance(summary, str)
        assert len(summary) > 0

    def test_fallback_thesis_non_empty_for_insightless_ticker(self):
        from app.services.agents.portfolio_manager import _fallback_thesis
        from app.services.agents.state import TickerInsight

        t = TickerInsight(ticker="NVDA")
        out = _fallback_thesis(t)
        assert isinstance(out, str)
        assert len(out) > 0


# ── Utility ──────────────────────────────────────────────────────────────────


def _async_value(value):
    """Return an awaitable that resolves to ``value`` — used to mock async
    methods on MagicMocks."""
    fut: asyncio.Future = asyncio.get_event_loop().create_future() \
        if asyncio.get_event_loop().is_running() else asyncio.new_event_loop().create_future()
    # Simpler: return a coroutine factory via a lambda stored on the MagicMock.
    async def _coro():
        return value
    return _coro()
