from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import BackgroundTasks


@pytest.mark.asyncio
async def test_refresh_dispatches_pipeline_for_new_jobs(monkeypatch):
    from app.routers.recommendations import refresh_recommendations

    queued_id = str(uuid4())

    class _Svc:
        async def queue_agent_run(self, **kwargs):
            return queued_id, True

    monkeypatch.setattr(
        "app.routers.recommendations.RecommendationService",
        lambda user_id: _Svc(),
    )

    bg = BackgroundTasks()
    user = SimpleNamespace(id=uuid4())
    out = await refresh_recommendations(background_tasks=bg, payload=None, user=user)

    assert str(out.job_id) == queued_id
    assert out.status == "queued"
    assert len(bg.tasks) == 1


@pytest.mark.asyncio
async def test_run_agent_pipeline_uses_same_job_id(monkeypatch):
    from app.services.agents.job_runner import run_agent_pipeline

    captured: dict[str, str] = {}

    class _Orch:
        async def run(self, run_id: str):
            captured["run_id"] = run_id
            return SimpleNamespace(status="completed")

    monkeypatch.setattr(
        "app.services.agents.job_runner.build_orchestrator",
        lambda *_args, **_kwargs: _Orch(),
    )

    run_id = str(uuid4())
    await run_agent_pipeline(uuid4(), run_id, 900.0, 0.0)
    assert captured["run_id"] == run_id


@pytest.mark.asyncio
async def test_queue_agent_run_does_not_reuse_stale_active(monkeypatch):
    from app.services.recommendation_engine import RecommendationService

    stale_started = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()

    class _AgentRunsTable:
        def select(self, *_args, **_kwargs):
            return self

        def eq(self, *_args, **_kwargs):
            return self

        def order(self, *_args, **_kwargs):
            return self

        def limit(self, *_args, **_kwargs):
            return self

        def update(self, *_args, **_kwargs):
            return self

        def execute(self):
            return SimpleNamespace(
                data=[{
                    "id": str(uuid4()),
                    "status": "running",
                    "started_at": stale_started,
                    "finished_at": None,
                }]
            )

    class _UsersTable:
        def select(self, *_args, **_kwargs):
            return self

        def eq(self, *_args, **_kwargs):
            return self

        @property
        def single(self):
            return self

        def execute(self):
            return SimpleNamespace(data={"deposit_amount": 900.0})

    class _Client:
        def table(self, name: str):
            if name == "agent_runs":
                return _AgentRunsTable()
            return _UsersTable()

    monkeypatch.setattr(
        "app.services.recommendation_engine.get_supabase_client",
        lambda: _Client(),
    )

    monkeypatch.setattr(
        "app.services.recommendation_engine.RecommendationService._mark_stale_run_failed",
        lambda self, _run_id: None,
    )

    class _Orch:
        async def create_run(self):
            return "new-job-id"

    monkeypatch.setattr(
        "app.services.agents.job_runner.build_orchestrator",
        lambda **_kwargs: _Orch(),
    )

    svc = RecommendationService(user_id=uuid4())
    job_id, is_new = await svc.queue_agent_run(deposit_amount=500.0)

    assert job_id == "new-job-id"
    assert is_new is True


@pytest.mark.asyncio
async def test_get_job_status_marks_stale_active_as_failed(monkeypatch):
    from app.services.recommendation_engine import RecommendationService

    stale_started = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
    svc = RecommendationService(user_id=uuid4())

    stale_row = {
        "id": str(uuid4()),
        "status": "running",
        "current_agent": "Working",
        "progress_pct": 40,
        "tickers": [],
        "deposit_amount": 900,
        "sale_proceeds": 0,
        "allocation": {},
        "summary": None,
        "error_message": None,
        "started_at": stale_started,
        "finished_at": None,
    }

    monkeypatch.setattr(svc, "_db", lambda _name, fn: SimpleNamespace(data=stale_row.copy()))
    monkeypatch.setattr(svc, "_mark_stale_run_failed", lambda _run_id, **_kwargs: None)

    out = await svc.get_job_status(uuid4())
    assert out.status == "failed"
    assert out.progress_pct == 100


@pytest.mark.asyncio
async def test_all_fallback_cards_do_not_count_llm_enriched(monkeypatch):
    from app.services.agents.orchestrator import AgentOrchestrator
    from app.services.intelligence import RunMode

    orch = AgentOrchestrator(user_id=uuid4(), anthropic_api_key="")
    orch._mode_decision = SimpleNamespace(mode=RunMode.DEGRADED, reason="test")
    orch._snapshots = {"AAPL": object(), "MSFT": object()}
    orch._features = {"AAPL": object(), "MSFT": object()}

    verdict = SimpleNamespace(used_fallback=True)
    monkeypatch.setattr(
        "app.services.agents.orchestrator.build_degraded_verdicts",
        lambda snapshots, decision: {k: verdict for k in snapshots.keys()},
    )

    await orch._run_per_ticker_analyst()
    assert orch._analyst_stage_stats["fallback_cards"] == 2
    assert orch._analyst_stage_stats["llm_enriched_cards"] == 0


@pytest.mark.asyncio
async def test_queue_agent_run_continues_when_stale_cleanup_fails(monkeypatch):
    from app.services.recommendation_engine import RecommendationService

    stale_started = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()

    class _AgentRunsTable:
        def select(self, *_args, **_kwargs):
            return self
        def eq(self, *_args, **_kwargs):
            return self
        def order(self, *_args, **_kwargs):
            return self
        def limit(self, *_args, **_kwargs):
            return self
        def execute(self):
            return SimpleNamespace(data=[{
                "id": str(uuid4()),
                "status": "running",
                "started_at": stale_started,
                "finished_at": None,
            }])

    class _UsersTable:
        def select(self, *_args, **_kwargs):
            return self
        def eq(self, *_args, **_kwargs):
            return self
        @property
        def single(self):
            return self
        def execute(self):
            return SimpleNamespace(data={"deposit_amount": 900.0})

    class _Client:
        def table(self, name: str):
            return _AgentRunsTable() if name == "agent_runs" else _UsersTable()

    monkeypatch.setattr("app.services.recommendation_engine.get_supabase_client", lambda: _Client())

    def _fail_mark(*_args, **_kwargs):
        raise RuntimeError("constraint failure")

    monkeypatch.setattr(
        "app.services.recommendation_engine.RecommendationService._mark_stale_run_failed",
        _fail_mark,
    )

    class _Orch:
        async def create_run(self):
            return "fresh-job"

    monkeypatch.setattr("app.services.agents.job_runner.build_orchestrator", lambda **_kwargs: _Orch())
    svc = RecommendationService(user_id=uuid4())
    job_id, is_new = await svc.queue_agent_run(deposit_amount=500.0)
    assert job_id == "fresh-job"
    assert is_new is True


def test_mark_stale_run_failed_writes_supported_status_only(monkeypatch):
    from app.services.recommendation_engine import RecommendationService

    updates: list[dict] = []

    class _AgentRunsTable:
        def update(self, patch):
            updates.append(patch)
            return self
        def eq(self, *_args, **_kwargs):
            return self
        def execute(self):
            return SimpleNamespace(data=[{"id": str(uuid4())}])

    class _Client:
        def table(self, _name: str):
            return _AgentRunsTable()

    svc = RecommendationService(user_id=uuid4())
    svc.client = _Client()
    monkeypatch.setattr(svc, "_db", lambda _name, fn: fn())
    svc._mark_stale_run_failed(str(uuid4()), old_status="running", reason="stale_timeout")
    assert updates
    assert updates[0]["status"] == "failed"
    assert updates[0]["status"] != "stale_failed"


def test_llm_metrics_invariant_attempted_without_success_or_failure_is_fixed():
    from app.services.agents.orchestrator import AgentOrchestrator

    orch = AgentOrchestrator(user_id=uuid4(), anthropic_api_key="")
    orch._analyst_stage_stats["attempted_llm_calls"] = 1
    orch._analyst_stage_stats["successful_llm_calls"] = 0
    orch._analyst_stage_stats["failed_llm_calls"] = 0

    orch._enforce_llm_metric_invariants()

    assert orch._analyst_stage_stats["failed_llm_calls"] == 1


def test_fallback_cards_require_failed_calls_or_skipped_reason():
    from app.services.agents.orchestrator import AgentOrchestrator

    orch = AgentOrchestrator(user_id=uuid4(), anthropic_api_key="")
    orch._analyst_stage_stats["fallback_cards"] = 34
    orch._analyst_stage_stats["failed_llm_calls"] = 0
    orch._analyst_stage_stats["skipped_llm_reason"] = None

    orch._enforce_llm_metric_invariants()

    assert orch._analyst_stage_stats["skipped_llm_reason"] is not None


@pytest.mark.asyncio
async def test_portfolio_synthesis_success_counts_even_without_cost_tracker(monkeypatch):
    from app.services.agents.orchestrator import AgentOrchestrator
    from app.services.intelligence.portfolio_synthesis import PortfolioSynthesis
    from app.services.intelligence.run_mode import RunMode

    orch = AgentOrchestrator(user_id=uuid4(), anthropic_api_key="fake-key")
    orch._snapshots = {"AAPL": object()}
    orch._features = {"AAPL": object()}
    orch._verdicts = {"AAPL": object()}
    orch._mode_decision = SimpleNamespace(mode=RunMode.FULL, reason="ok")
    orch._cost_tracker = None

    async def _fake_synthesize(**_kwargs):
        return PortfolioSynthesis(
            portfolio_bias="balanced",
            key_themes=["theme1", "theme2"],
            risk_concentrations=["risk1"],
            overexposure_flags=[],
            rebalancing_suggestions=[],
            summary="Live synthesis",
            used_fallback=False,
        )

    monkeypatch.setattr(
        "app.services.agents.orchestrator.synthesize_portfolio",
        _fake_synthesize,
    )

    await orch._run_portfolio_synthesis(context={"portfolio": [{"ticker": "AAPL"}], "macro": {}})

    assert orch._analyst_stage_stats["attempted_llm_calls"] == 1
    assert orch._analyst_stage_stats["successful_llm_calls"] == 1
    assert orch._analyst_stage_stats["failed_llm_calls"] == 0


@pytest.mark.asyncio
async def test_queue_agent_run_latest_query_avoids_heartbeat_column(monkeypatch):
    from app.services.recommendation_engine import RecommendationService

    captured = {"select": ""}

    class _AgentRunsTable:
        def select(self, cols):
            captured["select"] = cols
            return self

        def eq(self, *_args, **_kwargs):
            return self

        def order(self, *_args, **_kwargs):
            return self

        def limit(self, *_args, **_kwargs):
            return self

        def execute(self):
            return SimpleNamespace(data=[])

    class _UsersTable:
        def select(self, *_args, **_kwargs):
            return self
        def eq(self, *_args, **_kwargs):
            return self
        @property
        def single(self):
            return self
        def execute(self):
            return SimpleNamespace(data={"deposit_amount": 900.0})

    class _Client:
        def table(self, name: str):
            if name == "agent_runs":
                return _AgentRunsTable()
            return _UsersTable()

    monkeypatch.setattr(
        "app.services.recommendation_engine.get_supabase_client",
        lambda: _Client(),
    )

    class _Orch:
        async def create_run(self):
            return "job-1"

    monkeypatch.setattr(
        "app.services.agents.job_runner.build_orchestrator",
        lambda **_kwargs: _Orch(),
    )

    svc = RecommendationService(user_id=uuid4())
    await svc.queue_agent_run(deposit_amount=500.0)
    assert "heartbeat_at" not in captured["select"]


def test_terminal_statuses_match_frontend_contract():
    from app.services.agent_run_status import TERMINAL_RUN_STATUSES

    assert TERMINAL_RUN_STATUSES == {"completed", "failed", "cancelled"}

def test_run_agent_runs_update_uses_separate_verify_select(monkeypatch):
    from app.services.agents.orchestrator import AgentOrchestrator

    class _UpdateBuilder:
        def __init__(self, client):
            self._client = client

        def eq(self, key, value):
            self._client.update_filters.append((key, value))
            return self

        def execute(self):
            self._client.updated = True
            return SimpleNamespace(data=[])

    class _SelectBuilder:
        def __init__(self, client):
            self._client = client

        def eq(self, key, value):
            self._client.select_filters.append((key, value))
            return self

        def limit(self, value):
            self._client.select_limit = value
            return self

        def execute(self):
            return SimpleNamespace(data=[{"id": self._client.run_id, "status": "running"}])

    class _AgentRunsTable:
        def __init__(self, client):
            self._client = client

        def update(self, payload):
            self._client.update_payload = payload
            return _UpdateBuilder(self._client)

        def select(self, columns):
            self._client.select_columns = columns
            return _SelectBuilder(self._client)

    class _Client:
        def __init__(self):
            self.run_id = str(uuid4())
            self.update_payload = None
            self.update_filters = []
            self.select_columns = None
            self.select_filters = []
            self.select_limit = None
            self.updated = False

        def table(self, _name: str):
            return _AgentRunsTable(self)

    client = _Client()
    monkeypatch.setattr(
        "app.services.agents.orchestrator.get_supabase_client",
        lambda: client,
    )

    orch = AgentOrchestrator(user_id=uuid4(), anthropic_api_key="")
    matched = orch._run_agent_runs_update(client.run_id, {"status": "running"})

    assert matched == 1
    assert client.updated is True
    assert client.select_columns == "id,status"
    assert client.select_limit == 1
