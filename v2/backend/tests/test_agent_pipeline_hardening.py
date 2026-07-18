"""SEV-1 regressions: LLM failover, JSON extraction, fallback shape.

Covers the hardening work on the agent analysis pipeline so future refactors
can't silently regress the guarantees the Intel tab depends on:

  * `LLMClient._trim_prompt` shortens prompts for Haiku fallback
  * `LLMClient.ask_json` falls back to the secondary model on primary failure
  * `LLMClient.ask_json` returns `{}` — never None — on total failure
  * portfolio-manager fallback summary/thesis are never blank

NOTE: The queue_agent_run lock/light-cache tests and the `_within_last`
helper tests were removed in the lean-product refactor — they exercised
`app.services.recommendation_engine`, which was deleted along with the
/recommendations refresh route. The agents pipeline itself (llm.py,
portfolio_manager.py, orchestrator.py) is kept and remains covered below.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest


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


    def test_extract_json_accepts_prose_wrapped_object(self):
        from app.services.agents.llm import _extract_json

        parsed, debug = _extract_json(
            'Here is the portfolio synthesis: {"portfolio_bias":"neutral","key_themes":[]} Thanks!'
        )
        assert parsed == {"portfolio_bias": "neutral", "key_themes": []}
        assert debug["candidate"].startswith('{')

    def test_extract_json_accepts_fenced_json(self):
        from app.services.agents.llm import _extract_json

        parsed, debug = _extract_json("""```json\n{\"ok\": true}\n```""")
        assert parsed == {"ok": True}
        assert debug["had_code_fence"] is True

    def test_extract_json_detects_truncated_candidate(self):
        from app.services.agents.llm import _extract_json

        parsed, debug = _extract_json('{"action":"BUY","summary":"abc')
        assert parsed is None
        assert debug["parse_error_type"] == "truncated_json"
        assert debug["truncated_response_detected"] is True

    def test_first_balanced_json_object_substring_handles_braces_in_strings(self):
        from app.services.agents.llm import _first_balanced_json_object_substring

        s = 'prefix {"summary":"brace } inside", "ok": true} suffix'
        out = _first_balanced_json_object_substring(s)
        assert out == '{"summary":"brace } inside", "ok": true}'

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

    @pytest.mark.asyncio
    async def test_ask_json_retries_once_when_primary_parse_is_truncated(self, monkeypatch):
        from app.services.agents.llm import LLMClient

        client = LLMClient(api_key="fake-key")
        calls: list[tuple[str, int]] = []

        async def _fake_single_call(model, system, user, max_tokens):
            calls.append((model, max_tokens))
            if len(calls) == 1:
                return '{"action":"BUY","summary":"cut'
            return '{"action":"BUY","summary":"ok"}'

        monkeypatch.setattr(client, "_single_call", _fake_single_call)

        metadata: dict = {}
        out = await client.ask_json("sys", "user", max_tokens=320, metadata=metadata)
        assert out == {"action": "BUY", "summary": "ok"}
        assert len(calls) >= 2
        assert calls[1][1] > calls[0][1]
        assert metadata.get("truncation_retry_used") is True


    @pytest.mark.asyncio
    async def test_single_call_collects_all_text_content_blocks(self, monkeypatch):
        from app.services.agents.llm import LLMClient

        client = LLMClient(api_key="fake-key")

        class _Msg:
            def __init__(self):
                self.content = [
                    type("Part", (), {"text": '{"a": 1,'})(),
                    type("Part", (), {"text": '"b": 2}'})(),
                ]

        class _Messages:
            def create(self, **kwargs):
                return _Msg()

        class _Client:
            def __init__(self):
                self.messages = _Messages()

        monkeypatch.setattr(client, "_ensure_client", lambda: _Client())

        out = await client._single_call(client.model, "sys", "user", 200)
        assert out == '{"a": 1,\n"b": 2}'

    @pytest.mark.asyncio
    async def test_single_call_retries_without_cache_control_on_400(self, monkeypatch):
        from app.services.agents.llm import LLMClient

        client = LLMClient(api_key="fake-key")

        class _Msg:
            def __init__(self, text):
                self.content = [type("Part", (), {"text": text})()]

        class _Messages:
            def __init__(self):
                self.calls = 0

            def create(self, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    err = RuntimeError("invalid_request_error: cache_control is not allowed")
                    err.status_code = 400
                    raise err
                # Fallback payload should pass plain string system
                assert isinstance(kwargs.get("system"), str)
                return _Msg('{"ok": true}')

        class _Client:
            def __init__(self):
                self.messages = _Messages()

        monkeypatch.setattr(client, "_ensure_client", lambda: _Client())

        out = await client._single_call(client.model, "sys", "user", 200)
        assert out == '{"ok": true}'

    @pytest.mark.asyncio
    async def test_single_call_does_not_retry_generic_400(self, monkeypatch):
        from app.services.agents.llm import LLMClient

        client = LLMClient(api_key="fake-key")

        class _Messages:
            def __init__(self):
                self.calls = 0

            def create(self, **kwargs):
                self.calls += 1
                err = RuntimeError("invalid_request_error: malformed JSON")
                err.status_code = 400
                raise err

        class _Client:
            def __init__(self):
                self.messages = _Messages()

        fake_client = _Client()
        monkeypatch.setattr(client, "_ensure_client", lambda: fake_client)

        with pytest.raises(RuntimeError):
            await client._single_call(client.model, "sys", "user", 200)
        assert fake_client.messages.calls == 1


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
