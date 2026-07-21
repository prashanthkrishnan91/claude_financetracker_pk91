"""Architecture boundary — the distributed Run Intel path cannot reach the
retired execution architecture.

Static source-level proofs (import graph + symbol references) plus module
deletion proofs:

  * the retired modules (bounded drain, session flow) are GONE;
  * the Run Intel router and every distributed module never reference
    AgentOrchestrator execution (`run()` / `run_analyst_refresh_only`),
    the drain, the full-portfolio adapter, or portfolio synthesis;
  * specialist agents import no provider machinery (pure analyzers);
  * collectors import no LLM machinery (pure data workers);
  * the retired session-scoped job enqueue is gone from the job store.
"""
from __future__ import annotations

from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
APP = BACKEND / "app"
DISTRIBUTED = APP / "services" / "intelligence" / "v3" / "distributed"

RETIRED_SYMBOLS = (
    "run_analyst_refresh_only",
    "run_on_demand_drain",
    "FullPortfolioAnalystRefreshAdapter",
    "default_full_portfolio_agent_orchestrator_backend",
    "AgentOrchestrator",
    "_run_portfolio_synthesis",
    "analyst_refresh_on_demand_drain",
    "intel_run_session_flow",
    "enqueue_session_jobs",
    "run_intel_session_request",
)


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestRetiredModulesDeleted:
    def test_session_flow_module_deleted(self):
        assert not (
            APP / "services" / "intelligence" / "v3" / "intel_run_session_flow_v1.py"
        ).exists()

    def test_bounded_drain_module_deleted(self):
        assert not (
            APP / "services" / "intelligence" / "v3"
            / "analyst_refresh_on_demand_drain_v1.py"
        ).exists()

    def test_session_enqueue_removed_from_job_store(self):
        source = _source(
            APP / "services" / "intelligence" / "v3"
            / "analyst_refresh_job_store_v1.py"
        )
        assert "def enqueue_session_jobs" not in source
        assert "def make_session_failed_jobs_due" not in source


class TestRouterBoundary:
    def test_router_never_references_retired_execution(self):
        source = _source(APP / "routers" / "intel_v3.py")
        for symbol in RETIRED_SYMBOLS:
            assert symbol not in source, (
                f"routers/intel_v3.py references retired symbol {symbol!r}"
            )

    def test_router_uses_distributed_control_plane(self):
        source = _source(APP / "routers" / "intel_v3.py")
        assert "create_distributed_session" in source
        assert "get_session_status" in source
        assert "ensure_supervisor_running" in source


class TestDistributedPackageBoundary:
    def test_no_module_references_retired_execution(self):
        for path in sorted(DISTRIBUTED.glob("*.py")):
            source = _source(path)
            for symbol in RETIRED_SYMBOLS:
                assert symbol not in source, (
                    f"{path.name} references retired symbol {symbol!r}"
                )

    def test_specialists_import_no_provider_machinery(self):
        source = _source(DISTRIBUTED / "specialist_agents_v1.py")
        for forbidden in (
            "data_sources", "io_layer", "market_data", "research_workers",
            "httpx", "yfinance", "requests",
        ):
            assert forbidden not in source, (
                f"specialist_agents_v1 must not reference {forbidden!r}"
            )

    def test_decision_plane_imports_no_provider_or_llm_machinery(self):
        source = _source(DISTRIBUTED / "decision_tasks_v1.py")
        for forbidden in (
            "data_sources", "io_layer", "research_workers", "httpx",
            "yfinance", "LLMClient", "ask_json", "anthropic",
        ):
            assert forbidden not in source

    def test_collectors_import_no_llm_machinery(self):
        source = _source(DISTRIBUTED / "collectors_v1.py")
        for forbidden in ("LLMClient", "ask_json", "anthropic"):
            assert forbidden not in source, (
                f"collectors_v1 must not reference {forbidden!r} — collectors "
                "never call an LLM"
            )

    def test_scheduler_and_control_plane_make_no_provider_or_llm_calls(self):
        for name in ("run_scheduler_v1.py", "session_control_v1.py"):
            source = _source(DISTRIBUTED / name)
            for forbidden in (
                "data_sources", "io_layer", "httpx", "yfinance",
                "LLMClient", "ask_json", "anthropic", "coingecko",
            ):
                assert forbidden not in source, (
                    f"{name} must not reference {forbidden!r}"
                )


class TestLegacyWorkerFencing:
    """The retained legacy background worker cannot see distributed work."""

    def test_legacy_claim_skips_session_scoped_rows(self):
        source = _source(
            APP / "services" / "intelligence" / "v3"
            / "analyst_refresh_job_store_v1.py"
        )
        # The both-ways session isolation filter in claim_due_jobs survives.
        assert "run_session_id" in source

    def test_distributed_flow_creates_no_analyst_refresh_jobs(self):
        for path in sorted(DISTRIBUTED.glob("*.py")):
            source = _source(path)
            assert "analyst_refresh_jobs" not in source, (
                f"{path.name} must never touch the legacy analyst_refresh_jobs "
                "queue — the distributed workflow has exactly one task queue"
            )
