"""Distributed Run Intel model cost routing.

Proves:
  1. standard specialist analysis routes to the configured Haiku model;
  2. a specialist failure never auto-escalates to Sonnet (no fallback);
  3. the conditional conflict-review agent routes to the configured Sonnet
     model;
  4. review may fall back to the configured Haiku model on primary failure;
  5. unrelated legacy `LLMClient` callers retain Sonnet 4.6 → Haiku 4.5
     failover, unchanged;
  6. a successful specialist output is never re-generated after a process
     restart (fresh `WorkerSupervisor`/`LLMClient` objects) — the persisted
     row is untouched;
  7. an unfinished task picked up after restart uses the newly configured
     model;
  8. model names are environment configurable (`INTEL_V3_DISTRIBUTED_*`);
  9. migration 027 no longer declares or references the buggy `session_user`
     PL/pgSQL variable;
  10. deterministic decision authority is unaffected by model routing.

Collectors, bundle construction, decisions and publication make zero LLM
calls in this workflow — only specialist/review tasks touch the Anthropic
client, so tests either exercise `LLMClient` directly (with `_single_call`
stubbed — no real network) or call the specialist/review executors with a
stubbed `ask_json` on a real `LLMClient` instance built through
`WorkerSupervisor.specialist_llm` / `.review_llm`.
"""
from __future__ import annotations

import inspect
import re
import uuid
from pathlib import Path

import pytest

import app.services.intelligence.v3.distributed.run_task_store_v1 as store
from app.services.agents.llm import FALLBACK_MODEL, PRIMARY_MODEL, LLMClient
from app.services.intelligence.v3.distributed import decision_tasks_v1
from app.services.intelligence.v3.distributed import run_scheduler_v1 as scheduler
from app.services.intelligence.v3.distributed import session_control_v1 as control
from app.services.intelligence.v3.distributed.specialist_agents_v1 import (
    execute_specialist_task,
    validate_specialist_result,
)
from app.services.intelligence.v3.distributed.task_contracts_v1 import (
    AXIS_CRYPTO_MARKET,
    AXIS_FUNDAMENTAL,
    TASK_SPECIALIST_ANALYSIS,
    TASK_SUCCEEDED,
)
from app.services.intelligence.v3.distributed.collectors_v1 import (
    execute_collector_task,
)
from app.services.intelligence.v3.distributed.evidence_bundle_v1 import (
    build_evidence_bundle,
)
from app.services.intelligence.v3.distributed.task_contracts_v1 import (
    TASK_COLLECT_EVIDENCE_LANE,
    TASK_DEGRADED,
)
from app.services.intelligence.v3.distributed.worker_supervisor_v1 import (
    WorkerSupervisor,
)
from tests.distributed_run_intel_test_utils import (
    FakeSupabase,
    ProviderRecorder,
    claim_task_row,
    make_settings,
    patch_providers,
    seed_position,
)

USER = str(uuid.uuid4())

MIGRATION_027_PATH = (
    Path(__file__).resolve().parents[2] / "database"
    / "027_intel_run_distributed_tasks.sql"
)


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


def _stub_single_call(monkeypatch, canned: dict[str, object]) -> list[str]:
    """Patch `LLMClient._single_call` (no real network). `canned` maps a
    model name to either raw JSON text (success) or an Exception (failure).
    Returns the list this stub appends every attempted model name to."""
    called_models: list[str] = []

    async def _fake_single_call(self, model, system, user, max_tokens):
        called_models.append(model)
        if model not in canned:
            raise AssertionError(f"unexpected model requested: {model}")
        outcome = canned[model]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(LLMClient, "_single_call", _fake_single_call)
    return called_models


# ── 1 & 3: WorkerSupervisor routes specialist vs review to distinct clients ──


class TestSupervisorModelRouting:
    def test_specialist_llm_defaults_to_configured_haiku_with_no_fallback(self):
        settings = make_settings()
        supervisor = WorkerSupervisor(
            client=FakeSupabase(), settings=settings, worker_id="w",
        )
        llm = supervisor.specialist_llm
        assert llm.model == "claude-haiku-4-5-20251001"
        assert llm.fallback_model is None
        assert llm._fallback_enabled is False

    def test_review_llm_defaults_to_configured_sonnet_with_haiku_fallback(self):
        settings = make_settings()
        supervisor = WorkerSupervisor(
            client=FakeSupabase(), settings=settings, worker_id="w",
        )
        llm = supervisor.review_llm
        assert llm.model == "claude-sonnet-5"
        assert llm.fallback_model == "claude-haiku-4-5-20251001"
        assert llm._fallback_enabled is True

    def test_specialist_and_review_are_distinct_client_instances(self):
        settings = make_settings()
        supervisor = WorkerSupervisor(
            client=FakeSupabase(), settings=settings, worker_id="w",
        )
        assert supervisor.specialist_llm is not supervisor.review_llm
        assert supervisor.specialist_llm.model != supervisor.review_llm.model

    def test_explicit_llm_override_still_serves_both_roles(self):
        """Back-compat: tests/callers that inject one `llm=` kwarg (the
        pre-cost-routing shape) still get it for both specialist and review
        tasks — only the DEFAULT (no override) path splits by model."""
        settings = make_settings()
        sentinel = object()
        supervisor = WorkerSupervisor(
            client=FakeSupabase(), settings=settings, llm=sentinel,
            worker_id="w",
        )
        assert supervisor.specialist_llm is sentinel
        assert supervisor.review_llm is sentinel


# ── 2: specialist failure never escalates to Sonnet ──────────────────────────


class TestSpecialistNoEscalation:
    @pytest.mark.asyncio
    async def test_specialist_failure_returns_empty_without_trying_sonnet(
        self, monkeypatch
    ):
        called = _stub_single_call(monkeypatch, {
            "claude-haiku-4-5-20251001": RuntimeError(
                "insufficient_quota: account balance depleted"
            ),
        })
        client = LLMClient(
            api_key="key", model="claude-haiku-4-5-20251001",
            fallback_model=None,
        )
        result = await client.ask_json("system", "user")
        assert result == {}
        assert called == ["claude-haiku-4-5-20251001"]
        assert not any("sonnet" in m for m in called)


# ── 4: review may fall back to Haiku ──────────────────────────────────────────


class TestReviewFallback:
    @pytest.mark.asyncio
    async def test_review_falls_back_to_haiku_on_sonnet_failure(
        self, monkeypatch
    ):
        called = _stub_single_call(monkeypatch, {
            "claude-sonnet-5": RuntimeError(
                "insufficient_quota: account balance depleted"
            ),
            "claude-haiku-4-5-20251001": (
                '{"ticker": "AAPL", "stance": "neutral", "score": 0.0, '
                '"confidence": 0.6, "key_findings": ["reconciled"], '
                '"risks": [], "missing_evidence": [], "limitations": []}'
            ),
        })
        client = LLMClient(
            api_key="key", model="claude-sonnet-5",
            fallback_model="claude-haiku-4-5-20251001",
        )
        result = await client.ask_json("system", "Ticker: AAPL")
        assert result["ticker"] == "AAPL"
        assert called == ["claude-sonnet-5", "claude-haiku-4-5-20251001"]


# ── 5: unrelated legacy LLMClient callers are unaffected ─────────────────────


class TestLegacyLLMClientUnaffected:
    def test_legacy_defaults_are_sonnet46_and_haiku45(self):
        assert PRIMARY_MODEL == "claude-sonnet-4-6"
        assert FALLBACK_MODEL == "claude-haiku-4-5-20251001"
        client = LLMClient(api_key="key")
        assert client.model == PRIMARY_MODEL
        assert client.fallback_model == FALLBACK_MODEL
        assert client._fallback_enabled is True

    @pytest.mark.asyncio
    async def test_legacy_client_still_fails_over_sonnet_to_haiku(
        self, monkeypatch
    ):
        called = _stub_single_call(monkeypatch, {
            PRIMARY_MODEL: RuntimeError("boom"),
            FALLBACK_MODEL: '{"ok": true}',
        })
        client = LLMClient(api_key="key")  # unrelated legacy caller shape
        result = await client.ask_json("system", "user")
        assert result == {"ok": True}
        assert called == [PRIMARY_MODEL, FALLBACK_MODEL]


# ── 6 & 7: restart — succeeded outputs untouched, unfinished work re-routed ──


class TestRestartRecovery:
    @pytest.mark.asyncio
    async def test_restart_preserves_succeeded_output_and_routes_unfinished_task(
        self, monkeypatch
    ):
        client = FakeSupabase()
        await _session_with_ready_bundles(
            client, monkeypatch, ["AAPL", "BTC"], categories={"BTC": "Crypto"},
        )
        session = client.rows("intel_run_sessions")[0]
        scheduler.run_scheduler_pass(client, session=session)

        fundamental_task = next(
            t for t in client.rows("intel_run_tasks")
            if t["task_type"] == TASK_SPECIALIST_ANALYSIS
            and t["lane"] == AXIS_FUNDAMENTAL
        )
        crypto_task = next(
            t for t in client.rows("intel_run_tasks")
            if t["task_type"] == TASK_SPECIALIST_ANALYSIS
            and t["lane"] == AXIS_CRYPTO_MARKET
        )
        assert crypto_task["state"] == "pending"  # not yet claimed

        # ── Before "restart": only AAPL's fundamental task is processed ────
        settings_before = make_settings(
            intel_v3_distributed_specialist_model="claude-haiku-4-5-20251001",
        )
        supervisor_before = WorkerSupervisor(
            client=client, settings=settings_before,
            worker_id="worker-before-restart",
        )
        llm_before = supervisor_before.specialist_llm
        assert llm_before.model == "claude-haiku-4-5-20251001"
        assert llm_before.fallback_model is None

        async def ask_json_before(
            system, user, max_tokens=1024, normalizer=None, metadata=None,
            reject_prose=False, retry_truncated_response=True,
        ):
            if isinstance(metadata, dict):
                metadata["model_used"] = llm_before.model
            return {"results": [{
                "ticker": "AAPL", "stance": "positive", "score": 0.4,
                "confidence": 0.7, "key_findings": ["fundamentals solid"],
                "risks": [], "missing_evidence": [], "limitations": [],
            }]}

        monkeypatch.setattr(llm_before, "ask_json", ask_json_before)

        fundamental_task = claim_task_row(
            client, fundamental_task, worker_id="worker-before-restart",
        )
        outcome_before = await execute_specialist_task(
            client, task=fundamental_task, llm=llm_before,
        )
        assert outcome_before.final_state == TASK_SUCCEEDED
        assert outcome_before.models_used == ["claude-haiku-4-5-20251001"]
        store.complete_task(
            client, task=fundamental_task, worker_id="worker-before-restart",
            final_state=outcome_before.final_state,
        )
        aapl_output_before = next(
            o for o in client.rows("intel_run_specialist_outputs")
            if o["ticker"] == "AAPL"
        )
        assert aapl_output_before["model"] == "claude-haiku-4-5-20251001"

        # BTC's task is left exactly where a crash before claim would leave it.
        btc_task_row = next(
            t for t in client.rows("intel_run_tasks")
            if t["id"] == crypto_task["id"]
        )
        assert btc_task_row["state"] == "pending"

        # ── "Restart": brand-new supervisor, reconfigured specialist model ──
        settings_after = make_settings(
            intel_v3_distributed_specialist_model="claude-haiku-4-5-RESTARTED",
        )
        supervisor_after = WorkerSupervisor(
            client=client, settings=settings_after,
            worker_id="worker-after-restart",
        )
        llm_after = supervisor_after.specialist_llm
        assert llm_after is not llm_before
        assert llm_after.model == "claude-haiku-4-5-RESTARTED"
        assert llm_after.fallback_model is None

        async def ask_json_after(
            system, user, max_tokens=1024, normalizer=None, metadata=None,
            reject_prose=False, retry_truncated_response=True,
        ):
            if isinstance(metadata, dict):
                metadata["model_used"] = llm_after.model
            return {"results": [{
                "ticker": "BTC", "stance": "neutral", "score": 0.1,
                "confidence": 0.6, "key_findings": ["momentum flat"],
                "risks": [], "missing_evidence": [], "limitations": [],
            }]}

        monkeypatch.setattr(llm_after, "ask_json", ask_json_after)

        crypto_task = claim_task_row(
            client, btc_task_row, worker_id="worker-after-restart",
        )
        outcome_after = await execute_specialist_task(
            client, task=crypto_task, llm=llm_after,
        )
        assert outcome_after.final_state == TASK_SUCCEEDED
        assert outcome_after.models_used == ["claude-haiku-4-5-RESTARTED"]
        store.complete_task(
            client, task=crypto_task, worker_id="worker-after-restart",
            final_state=outcome_after.final_state,
        )

        btc_output = next(
            o for o in client.rows("intel_run_specialist_outputs")
            if o["ticker"] == "BTC"
        )
        assert btc_output["model"] == "claude-haiku-4-5-RESTARTED"

        # AAPL's pre-restart output is byte-for-byte unchanged — no re-run.
        aapl_output_after = next(
            o for o in client.rows("intel_run_specialist_outputs")
            if o["ticker"] == "AAPL"
        )
        assert aapl_output_after == aapl_output_before


# ── 8: model names are environment configurable ──────────────────────────────


class TestEnvironmentConfigurable:
    def test_settings_read_model_names_from_env(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_URL", "http://fake")
        monkeypatch.setenv("SUPABASE_ANON_KEY", "anon")
        monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc")
        monkeypatch.setenv("SUPABASE_JWT_SECRET", "secret")
        monkeypatch.setenv("ENCRYPTION_KEY", "a" * 32)
        monkeypatch.setenv(
            "INTEL_V3_DISTRIBUTED_SPECIALIST_MODEL", "claude-haiku-ENV",
        )
        monkeypatch.setenv(
            "INTEL_V3_DISTRIBUTED_REVIEW_MODEL", "claude-sonnet-ENV",
        )
        monkeypatch.setenv(
            "INTEL_V3_DISTRIBUTED_REVIEW_FALLBACK_MODEL",
            "claude-haiku-ENV-FALLBACK",
        )
        from app.config import Settings

        settings = Settings(_env_file=None)
        assert settings.intel_v3_distributed_specialist_model == (
            "claude-haiku-ENV"
        )
        assert settings.intel_v3_distributed_review_model == (
            "claude-sonnet-ENV"
        )
        assert settings.intel_v3_distributed_review_fallback_model == (
            "claude-haiku-ENV-FALLBACK"
        )

    def test_settings_defaults_without_any_env_vars(self):
        from app.config import Settings

        settings = Settings(
            _env_file=None,
            supabase_url="http://fake",
            supabase_anon_key="anon",
            supabase_service_role_key="svc",
            supabase_jwt_secret="secret",
            encryption_key="a" * 32,
        )
        assert settings.intel_v3_distributed_specialist_model == (
            "claude-haiku-4-5-20251001"
        )
        assert settings.intel_v3_distributed_review_model == "claude-sonnet-5"
        assert settings.intel_v3_distributed_review_fallback_model == (
            "claude-haiku-4-5-20251001"
        )


# ── 9: migration 027 no longer declares/references session_user ─────────────


class TestMigration027Corrected:
    def test_trigger_functions_use_v_session_user_id(self):
        sql = MIGRATION_027_PATH.read_text()

        # The buggy declaration/usages are gone (the identifier still appears
        # once in an explanatory prose comment — assert on the CODE shapes).
        assert "session_user UUID;" not in sql
        assert "INTO session_user" not in sql
        assert "IF session_user" not in sql
        assert "session_user <>" not in sql

        assert sql.count("v_session_user_id UUID;") == 2
        assert sql.count("IF v_session_user_id IS DISTINCT FROM NEW.user_id") == 2
        assert sql.count("SELECT s.user_id INTO v_session_user_id") == 2
        assert "FROM public.intel_run_sessions s" in sql

    def test_owner_guard_functions_use_is_distinct_from(self):
        sql = MIGRATION_027_PATH.read_text()
        guard_fns = re.findall(
            r"CREATE OR REPLACE FUNCTION public\.intel_run_(?:ticker|task)"
            r"_owner_guard\(\).*?\$\$ LANGUAGE plpgsql;",
            sql, flags=re.DOTALL,
        )
        assert len(guard_fns) == 2
        for fn_body in guard_fns:
            assert re.search(r"\bsession_user\b", fn_body) is None
            assert "v_session_user_id" in fn_body
            assert "IS DISTINCT FROM" in fn_body


# ── 10: deterministic decision authority is unaffected ───────────────────────


class TestDecisionAuthorityUnaffected:
    def test_ticker_decision_task_takes_no_llm_dependency(self):
        sig = inspect.signature(decision_tasks_v1.execute_ticker_decision_task)
        assert "llm" not in sig.parameters

    def test_specialist_output_never_carries_a_visible_action(self):
        result = validate_specialist_result({
            "ticker": "AAPL", "stance": "positive", "score": 0.5,
            "confidence": 0.6, "key_findings": ["x"],
        })
        assert result is not None
        assert "action" not in result

    def test_worker_supervisor_decision_dispatch_has_no_llm_argument(self):
        source = inspect.getsource(WorkerSupervisor._execute_one)
        branch = source.split("TASK_TICKER_DECISION")[1].split("elif")[0]
        assert "llm=" not in branch
