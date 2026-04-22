"""Orchestrator single-LLM-call enforcement tests.

Hard guarantees required by Portfolio Engine v2 (Task 4):
  * The orchestrator makes AT MOST one LLM call per ``run(run_id)`` invocation.
  * A second in-run invocation of ``_single_llm_call`` is blocked and logged.
  * ``LLM_SEMAPHORE`` remains a binary semaphore (process-wide serialisation).
"""

from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_single_llm_call_increments_counter(monkeypatch):
    from app.services.agents import orchestrator as orch_mod

    mock_db = MagicMock()
    mock_db.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()
    monkeypatch.setattr(orch_mod, "get_supabase_client", lambda: mock_db)

    orch = orch_mod.AgentOrchestrator(
        user_id=uuid4(), anthropic_api_key="fake-key"
    )

    async def _fake_ask(**kwargs):
        return {"summary": "ok", "cards": []}

    orch._llm.ask_json = _fake_ask  # type: ignore[assignment]

    assert orch._llm_call_count == 0
    out = await orch._single_llm_call({"portfolio": [{"ticker": "AAPL"}]})
    assert orch._llm_call_count == 1
    assert out == {"summary": "ok", "cards": []}


@pytest.mark.asyncio
async def test_second_llm_call_is_blocked(monkeypatch):
    """A regression that re-enters the LLM must be blocked at the orchestrator level."""
    from app.services.agents import orchestrator as orch_mod

    mock_db = MagicMock()
    mock_db.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()
    monkeypatch.setattr(orch_mod, "get_supabase_client", lambda: mock_db)

    orch = orch_mod.AgentOrchestrator(
        user_id=uuid4(), anthropic_api_key="fake-key"
    )

    call_count = 0

    async def _fake_ask(**kwargs):
        nonlocal call_count
        call_count += 1
        return {"summary": "first", "cards": []}

    orch._llm.ask_json = _fake_ask  # type: ignore[assignment]

    await orch._single_llm_call({"portfolio": [{"ticker": "AAPL"}]})
    # Simulate a buggy re-entry — second call must NOT hit ask_json.
    out = await orch._single_llm_call({"portfolio": [{"ticker": "AAPL"}]})

    assert call_count == 1, "Second LLM call must not reach the HTTP layer"
    assert out == {}  # blocked → neutral empty dict
    assert orch._llm_call_count == 2  # counter records the attempted re-entry


def test_llm_semaphore_is_binary():
    """Sanity: the process-wide semaphore must serialise LLM calls (value=1)."""
    from app.services.agents import orchestrator as orch_mod

    assert orch_mod.LLM_SEMAPHORE._value == 1


@pytest.mark.asyncio
async def test_missing_api_key_does_not_bump_counter(monkeypatch):
    """If no API key is configured, the LLM stage is a no-op — counter stays zero."""
    from app.services.agents import orchestrator as orch_mod

    mock_db = MagicMock()
    mock_db.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()
    monkeypatch.setattr(orch_mod, "get_supabase_client", lambda: mock_db)

    orch = orch_mod.AgentOrchestrator(user_id=uuid4(), anthropic_api_key="")
    out = await orch._single_llm_call({"portfolio": [{"ticker": "AAPL"}]})
    assert out == {}
    assert orch._llm_call_count == 0
