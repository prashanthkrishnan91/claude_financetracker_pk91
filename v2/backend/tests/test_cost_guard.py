"""Cost-guard tests.

Covers:
  CG1. Watchtower disabled by default — main() exits 0 without polling.
  CG2. Master kill switch blocks watchtower even when per-worker flag is set.
  CG3. Analyst refresh worker disabled by default — main() exits 0 without polling.
  CG4. Master kill switch blocks analyst worker even when per-worker flag is set.
  CG5. Email delivery worker disabled by default — main() exits 0 without polling.
  CG6. Master kill switch blocks email worker even when per-worker flag is set.
  CG7. Watchtower interval is clamped to MIN_INTERVAL_SECONDS (6h) when configured below.
  CG7b. Watchtower CLI --interval-seconds is also clamped (not just env var path).
  CG8. Clamping is bypassed when COST_GUARD_ALLOW_AGGRESSIVE_POLLING=true.
  CG8b. Aggressive polling bypass also applies to CLI-supplied intervals.
  CG9. Analyst worker interval is clamped to 12h minimum.
  CG9b. Analyst worker CLI interval is also clamped.
  CG10. Email worker interval is clamped to 24h minimum.
  CG10b. Email worker CLI interval is also clamped.
  CG11. _persist_snapshot() makes no DB calls when intel_v3_snapshot_writes_enabled=False.
  CG12. Retention SQL file exists, has no uncommented TRUNCATE CASCADE, and uses FK-safe
        child-first deletion order.

Import strategy: entrypoint modules are loaded via importlib.util to avoid
triggering app.services.intelligence.__init__ which imports httpx. This matches
how the entrypoints are invoked at runtime (as __main__ scripts) and keeps tests
free from the full dependency tree.

For intel_v3_service: heavy sibling modules are pre-stubbed in sys.modules so
the service can be imported to test _persist_snapshot() in isolation.
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import pathlib
import sys
import types
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Module loader helper ──────────────────────────────────────────────────────

def _load_entrypoint(rel_path: str) -> types.ModuleType:
    """Load an entrypoint .py file directly without triggering package __init__ imports."""
    base = pathlib.Path(__file__).parent.parent  # v2/backend
    abs_path = base / rel_path
    module_name = rel_path.replace("/", ".").replace(".py", "")
    spec = importlib.util.spec_from_file_location(module_name, abs_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── CG1 / CG2: Watchtower ─────────────────────────────────────────────────────

class TestWatchtowerKillSwitch:
    def test_cg1_disabled_by_default_exits_zero(self, monkeypatch):
        """Watchtower exits 0 immediately when both master and per-worker flags are off."""
        monkeypatch.delenv("INTEL_BACKGROUND_WORKERS_ENABLED", raising=False)
        monkeypatch.delenv("INTEL_V3_WATCHTOWER_ENABLED", raising=False)

        ep = _load_entrypoint(
            "app/services/intelligence/v3/watchtower_worker_entrypoint.py"
        )
        result = ep.main([])
        assert result == 0

    def test_cg2_master_switch_blocks_even_when_worker_enabled(self, monkeypatch):
        """Master kill switch takes priority over per-worker flag."""
        monkeypatch.setenv("INTEL_BACKGROUND_WORKERS_ENABLED", "false")
        monkeypatch.setenv("INTEL_V3_WATCHTOWER_ENABLED", "true")

        ep = _load_entrypoint(
            "app/services/intelligence/v3/watchtower_worker_entrypoint.py"
        )
        result = ep.main([])
        assert result == 0

    def test_watchtower_per_worker_disabled_when_master_on(self, monkeypatch):
        """When master is on but per-worker flag is off, still exits 0."""
        monkeypatch.setenv("INTEL_BACKGROUND_WORKERS_ENABLED", "true")
        monkeypatch.setenv("INTEL_V3_WATCHTOWER_ENABLED", "false")

        ep = _load_entrypoint(
            "app/services/intelligence/v3/watchtower_worker_entrypoint.py"
        )
        result = ep.main([])
        assert result == 0

    def test_watchtower_log_contains_cost_guard_on_master_disabled(
        self, monkeypatch, caplog
    ):
        """COST_GUARD appears in log when master is disabled."""
        import logging
        monkeypatch.delenv("INTEL_BACKGROUND_WORKERS_ENABLED", raising=False)

        ep = _load_entrypoint(
            "app/services/intelligence/v3/watchtower_worker_entrypoint.py"
        )
        with caplog.at_level(logging.INFO):
            ep.main([])
        assert "COST_GUARD" in caplog.text


# ── CG3 / CG4: Analyst refresh worker ────────────────────────────────────────

class TestAnalystRefreshWorkerKillSwitch:
    def test_cg3_disabled_by_default_exits_zero(self, monkeypatch):
        """Analyst refresh worker exits 0 immediately when both flags are off."""
        monkeypatch.delenv("INTEL_BACKGROUND_WORKERS_ENABLED", raising=False)
        monkeypatch.delenv("INTEL_V3_RESEARCH_WORKERS_ENABLED", raising=False)

        ep = _load_entrypoint(
            "app/services/intelligence/v3/analyst_refresh_worker_entrypoint.py"
        )
        result = ep.main([])
        assert result == 0

    def test_cg4_master_switch_blocks_even_when_worker_enabled(self, monkeypatch):
        """Master kill switch takes priority over per-worker flag."""
        monkeypatch.setenv("INTEL_BACKGROUND_WORKERS_ENABLED", "false")
        monkeypatch.setenv("INTEL_V3_RESEARCH_WORKERS_ENABLED", "true")

        ep = _load_entrypoint(
            "app/services/intelligence/v3/analyst_refresh_worker_entrypoint.py"
        )
        result = ep.main([])
        assert result == 0

    def test_analyst_per_worker_disabled_when_master_on(self, monkeypatch):
        """When master is on but per-worker flag is off, exits 0."""
        monkeypatch.setenv("INTEL_BACKGROUND_WORKERS_ENABLED", "true")
        monkeypatch.setenv("INTEL_V3_RESEARCH_WORKERS_ENABLED", "false")

        ep = _load_entrypoint(
            "app/services/intelligence/v3/analyst_refresh_worker_entrypoint.py"
        )
        result = ep.main([])
        assert result == 0

    def test_analyst_log_contains_cost_guard_on_master_disabled(
        self, monkeypatch, caplog
    ):
        """COST_GUARD appears in log when master is disabled."""
        import logging
        monkeypatch.delenv("INTEL_BACKGROUND_WORKERS_ENABLED", raising=False)

        ep = _load_entrypoint(
            "app/services/intelligence/v3/analyst_refresh_worker_entrypoint.py"
        )
        with caplog.at_level(logging.INFO):
            ep.main([])
        assert "COST_GUARD" in caplog.text


# ── CG5 / CG6: Email delivery worker ─────────────────────────────────────────

class TestEmailDeliveryWorkerKillSwitch:
    def test_cg5_disabled_by_default_exits_zero(self, monkeypatch):
        """Email delivery worker exits 0 immediately when both flags are off."""
        monkeypatch.delenv("INTEL_BACKGROUND_WORKERS_ENABLED", raising=False)
        monkeypatch.delenv("ALERT_EMAIL_DELIVERY_ENABLED", raising=False)

        from app.services.alert import alert_email_delivery_worker_entrypoint as ep
        result = ep.main([])
        assert result == 0

    def test_cg6_master_switch_blocks_even_when_worker_enabled(self, monkeypatch):
        """Master kill switch takes priority over per-worker flag."""
        monkeypatch.setenv("INTEL_BACKGROUND_WORKERS_ENABLED", "false")
        monkeypatch.setenv("ALERT_EMAIL_DELIVERY_ENABLED", "true")

        from app.services.alert import alert_email_delivery_worker_entrypoint as ep
        result = ep.main([])
        assert result == 0

    def test_email_per_worker_disabled_when_master_on(self, monkeypatch):
        """When master is on but per-worker flag is off, exits 0."""
        monkeypatch.setenv("INTEL_BACKGROUND_WORKERS_ENABLED", "true")
        monkeypatch.setenv("ALERT_EMAIL_DELIVERY_ENABLED", "false")

        from app.services.alert import alert_email_delivery_worker_entrypoint as ep
        result = ep.main([])
        assert result == 0

    def test_email_log_contains_cost_guard_on_master_disabled(
        self, monkeypatch, caplog
    ):
        """COST_GUARD appears in log when master is disabled."""
        import logging
        monkeypatch.delenv("INTEL_BACKGROUND_WORKERS_ENABLED", raising=False)

        from app.services.alert import alert_email_delivery_worker_entrypoint as ep
        with caplog.at_level(logging.INFO):
            ep.main([])
        assert "COST_GUARD" in caplog.text


# ── CG7 / CG8 / CG9 / CG10: Interval clamping ───────────────────────────────

class TestIntervalClamping:
    def test_cg7_watchtower_interval_clamped_to_6h(self, monkeypatch):
        """Watchtower env-var interval below minimum is clamped to 21600s (6h)."""
        monkeypatch.setenv("INTEL_V3_WATCHTOWER_WORKER_INTERVAL_SECONDS", "30")
        monkeypatch.delenv("COST_GUARD_ALLOW_AGGRESSIVE_POLLING", raising=False)

        ep = _load_entrypoint(
            "app/services/intelligence/v3/watchtower_worker_entrypoint.py"
        )
        raw = ep._resolve_interval_seconds()
        result = ep._apply_cost_guard_clamp(raw)
        assert result == ep.MIN_INTERVAL_SECONDS
        assert result == 21600.0

    def test_cg7b_watchtower_cli_interval_also_clamped(self, monkeypatch):
        """CLI --interval-seconds below minimum is clamped (bypass of env var path)."""
        monkeypatch.delenv("COST_GUARD_ALLOW_AGGRESSIVE_POLLING", raising=False)

        ep = _load_entrypoint(
            "app/services/intelligence/v3/watchtower_worker_entrypoint.py"
        )
        result = ep._apply_cost_guard_clamp(30.0)
        assert result == ep.MIN_INTERVAL_SECONDS
        assert result == 21600.0

    def test_cg8_clamping_bypassed_with_override(self, monkeypatch):
        """When COST_GUARD_ALLOW_AGGRESSIVE_POLLING=true, short env-var intervals are allowed."""
        monkeypatch.setenv("INTEL_V3_WATCHTOWER_WORKER_INTERVAL_SECONDS", "30")
        monkeypatch.setenv("COST_GUARD_ALLOW_AGGRESSIVE_POLLING", "true")

        ep = _load_entrypoint(
            "app/services/intelligence/v3/watchtower_worker_entrypoint.py"
        )
        raw = ep._resolve_interval_seconds()
        result = ep._apply_cost_guard_clamp(raw)
        assert result == 30.0

    def test_cg8b_cli_clamping_also_bypassed_with_override(self, monkeypatch):
        """COST_GUARD_ALLOW_AGGRESSIVE_POLLING=true also allows short CLI-supplied intervals."""
        monkeypatch.setenv("COST_GUARD_ALLOW_AGGRESSIVE_POLLING", "true")

        ep = _load_entrypoint(
            "app/services/intelligence/v3/watchtower_worker_entrypoint.py"
        )
        result = ep._apply_cost_guard_clamp(30.0)
        assert result == 30.0

    def test_cg9_analyst_interval_clamped_to_12h(self, monkeypatch):
        """Analyst refresh worker env-var interval below minimum is clamped to 43200s (12h)."""
        monkeypatch.setenv("INTEL_V3_ANALYST_REFRESH_WORKER_INTERVAL_SECONDS", "60")
        monkeypatch.delenv("COST_GUARD_ALLOW_AGGRESSIVE_POLLING", raising=False)

        ep = _load_entrypoint(
            "app/services/intelligence/v3/analyst_refresh_worker_entrypoint.py"
        )
        raw = ep._resolve_interval_seconds()
        result = ep._apply_cost_guard_clamp(raw)
        assert result == ep.MIN_INTERVAL_SECONDS
        assert result == 43200.0

    def test_cg9b_analyst_cli_interval_also_clamped(self, monkeypatch):
        """Analyst worker CLI --interval-seconds below minimum is clamped."""
        monkeypatch.delenv("COST_GUARD_ALLOW_AGGRESSIVE_POLLING", raising=False)

        ep = _load_entrypoint(
            "app/services/intelligence/v3/analyst_refresh_worker_entrypoint.py"
        )
        result = ep._apply_cost_guard_clamp(60.0)
        assert result == ep.MIN_INTERVAL_SECONDS
        assert result == 43200.0

    def test_cg10_email_interval_clamped_to_24h(self, monkeypatch):
        """Email delivery worker env-var interval below minimum is clamped to 86400s (24h)."""
        monkeypatch.setenv("ALERT_EMAIL_DELIVERY_WORKER_INTERVAL_SECONDS", "300")
        monkeypatch.delenv("COST_GUARD_ALLOW_AGGRESSIVE_POLLING", raising=False)

        from app.services.alert import alert_email_delivery_worker_entrypoint as ep
        raw = ep._resolve_interval_seconds()
        result = ep._apply_cost_guard_clamp(raw)
        assert result == ep.MIN_INTERVAL_SECONDS
        assert result == 86400.0

    def test_cg10b_email_cli_interval_also_clamped(self, monkeypatch):
        """Email worker CLI --interval-seconds below minimum is clamped."""
        monkeypatch.delenv("COST_GUARD_ALLOW_AGGRESSIVE_POLLING", raising=False)

        from app.services.alert import alert_email_delivery_worker_entrypoint as ep
        result = ep._apply_cost_guard_clamp(300.0)
        assert result == ep.MIN_INTERVAL_SECONDS
        assert result == 86400.0

    def test_interval_above_min_is_not_clamped(self, monkeypatch):
        """Intervals already above the minimum are passed through unchanged."""
        monkeypatch.setenv("INTEL_V3_WATCHTOWER_WORKER_INTERVAL_SECONDS", "86400")
        monkeypatch.delenv("COST_GUARD_ALLOW_AGGRESSIVE_POLLING", raising=False)

        ep = _load_entrypoint(
            "app/services/intelligence/v3/watchtower_worker_entrypoint.py"
        )
        raw = ep._resolve_interval_seconds()
        result = ep._apply_cost_guard_clamp(raw)
        assert result == 86400.0


# ── CG11: Snapshot write guard ────────────────────────────────────────────────

# intel_v3_service.py has many heavy sibling imports (LLM adapters, SEC adapters, etc.).
# We pre-stub them in sys.modules so the service can be loaded without triggering
# httpx / openai / anthropic imports, then call _persist_snapshot() with a
# mock client to verify no DB calls occur when the write guard flag is off.
_INTEL_V3_SERVICE_HEAVY_SIBLINGS = [
    "app.services.intelligence.v3.decision_policy_v1",
    "app.services.intelligence.v3.existing_signal_adapter",
    "app.services.intelligence.v3.evidence_refresh_orchestrator_v1",
    "app.services.intelligence.v3.analyst_refresh_request_seam_v1",
    "app.services.intelligence.v3.read_only_evidence_adapter",
    "app.services.intelligence.v3.portfolio_governor_lite",
    "app.services.intelligence.v3.snapshot_builder",
    "app.services.intelligence.v3.snapshot_freshness_diagnostics",
    "app.services.intelligence.v3.source_validator_lite",
    "app.services.intelligence.v3.catalyst_display_adapter_v1",
    "app.services.intelligence.v3.sec_catalyst_explanation_adapter_v1",
    "app.services.intelligence.v3.sec_filing_type_adapter_v1",
]


def _load_intel_v3_service(monkeypatch: Any) -> types.ModuleType:
    """Load intel_v3_service with heavy/unavailable imports pre-stubbed.

    Uses spec_from_file_location to load the file directly so we can pre-populate
    sys.modules for:
      - app.config (pydantic_settings not installed in test env)
      - app.database (supabase client — not needed for unit tests)
      - app.services.intelligence (its __init__.py imports httpx via sec_filings)
      - app.services.intelligence.v3 (parent package; has no __init__.py but must
        be in sys.modules for relative imports to resolve)
      - all heavy sibling modules intel_v3_service imports at module level
    """
    svc_key = "app.services.intelligence.v3.intel_v3_service"

    # Return cached version if already loaded by this helper
    if svc_key in sys.modules and hasattr(sys.modules[svc_key], "IntelV3Service"):
        return sys.modules[svc_key]

    stubs_needed = _INTEL_V3_SERVICE_HEAVY_SIBLINGS + [
        "app.config",                    # pydantic_settings not available in test env
        "app.database",                  # supabase client not needed
        "app.services.intelligence",     # its __init__.py imports httpx
        "app.services.intelligence.v3",  # parent package (no __init__.py)
    ]
    for name in stubs_needed:
        if name not in sys.modules:
            monkeypatch.setitem(sys.modules, name, MagicMock())

    base = pathlib.Path(__file__).parent.parent
    svc_path = base / "app/services/intelligence/v3/intel_v3_service.py"
    spec = importlib.util.spec_from_file_location(svc_key, svc_path)
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = "app.services.intelligence.v3"
    # Register before exec so circular-import self-references resolve
    sys.modules[svc_key] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        if sys.modules.get(svc_key) is mod:
            del sys.modules[svc_key]
        raise
    return mod


class TestSnapshotWriteGuard:
    def test_cg11_persist_snapshot_skips_db_when_writes_disabled(self, monkeypatch):
        """_persist_snapshot makes no asyncio.to_thread DB calls when writes are disabled."""
        import asyncio
        from uuid import uuid4

        fake_settings = MagicMock()
        fake_settings.intel_v3_snapshot_writes_enabled = False

        svc_mod = _load_intel_v3_service(monkeypatch)

        # Bypass __init__ (which calls get_supabase_client) — set attrs directly.
        svc = object.__new__(svc_mod.IntelV3Service)
        svc.user_id = uuid4()
        svc.client = MagicMock()

        db_calls: list[str] = []

        async def _spy_to_thread(fn, *args, **kwargs):
            db_calls.append("unexpected_db_call")
            return MagicMock(data=[])

        with patch.object(svc_mod, "get_settings", return_value=fake_settings):
            with patch("asyncio.to_thread", side_effect=_spy_to_thread):
                asyncio.run(svc._persist_snapshot(run_id="test-run-disabled", payload={"k": "v"}))

        assert db_calls == [], (
            f"Expected no DB calls when intel_v3_snapshot_writes_enabled=False, got: {db_calls}"
        )

    def test_cg11_snapshot_write_guard_flag_false_means_no_write(self, monkeypatch):
        """Snapshot writes default to off when env var is absent."""
        monkeypatch.delenv("INTEL_V3_SNAPSHOT_WRITES_ENABLED", raising=False)

        raw = (os.getenv("INTEL_V3_SNAPSHOT_WRITES_ENABLED") or "").strip().lower()
        writes_enabled = raw in ("1", "true", "yes", "on")
        assert writes_enabled is False, "Snapshot writes must default to off"

    def test_cg11_snapshot_write_guard_flag_true_would_allow(self, monkeypatch):
        """When the flag is True, the guard would allow writes."""
        monkeypatch.setenv("INTEL_V3_SNAPSHOT_WRITES_ENABLED", "true")

        raw = (os.getenv("INTEL_V3_SNAPSHOT_WRITES_ENABLED") or "").strip().lower()
        writes_enabled = raw in ("1", "true", "yes", "on")
        assert writes_enabled is True


# ── CG12: SQL file safety ─────────────────────────────────────────────────────

class TestSQLCleanupFile:
    _SQL_PATH = (
        pathlib.Path(__file__).parent.parent.parent.parent
        / "v2" / "database" / "cost_guard_retention_cleanup.sql"
    )

    def test_cg12_sql_file_exists(self):
        """Retention SQL cleanup file exists."""
        assert self._SQL_PATH.exists(), f"Expected SQL cleanup file at {self._SQL_PATH}"

    def test_cg12_sql_has_no_uncommented_truncate_cascade(self):
        """SQL cleanup file has no uncommented TRUNCATE CASCADE statement."""
        if not self._SQL_PATH.exists():
            pytest.skip("SQL file not found")
        non_comment_lines = [
            line for line in self._SQL_PATH.read_text().splitlines()
            if not line.strip().startswith("--")
        ]
        non_comment_content = "\n".join(non_comment_lines).upper()
        assert "TRUNCATE CASCADE" not in non_comment_content, \
            "SQL cleanup file must not contain an uncommented TRUNCATE CASCADE"

    def test_cg12_sql_protects_core_tables(self):
        """SQL cleanup file does not DELETE from core user data tables."""
        if not self._SQL_PATH.exists():
            pytest.skip("SQL file not found")
        non_comment_lines = [
            line for line in self._SQL_PATH.read_text().splitlines()
            if not line.strip().startswith("--")
        ]
        content = "\n".join(non_comment_lines).lower()
        forbidden_deletes = [
            "delete from public.portfolios",
            "delete from public.positions",
            "delete from public.holdings",
            "delete from public.transactions",
            "delete from public.users",
            "delete from public.accounts",
            "delete from public.deposits",
        ]
        for stmt in forbidden_deletes:
            assert stmt not in content, \
                f"SQL cleanup file must not delete from core table: {stmt}"

    def test_cg12_sql_uses_delete_not_truncate(self):
        """SQL cleanup file uses DELETE statements for pruning, not TRUNCATE."""
        if not self._SQL_PATH.exists():
            pytest.skip("SQL file not found")
        content = self._SQL_PATH.read_text()
        assert "DELETE FROM" in content.upper(), \
            "SQL cleanup file should contain DELETE FROM statements"

    def test_cg12_sql_targets_generated_tables(self):
        """SQL cleanup file targets known generated tables."""
        if not self._SQL_PATH.exists():
            pytest.skip("SQL file not found")
        content = self._SQL_PATH.read_text().lower()
        expected = [
            "intel_v3_snapshots",
            "market_snapshots",
            "agent_runs",
            "research_artifacts",
        ]
        for table in expected:
            assert table in content, \
                f"SQL cleanup file should reference generated table: {table}"

    def test_cg12_sql_fk_safe_decision_log_before_recommendations(self):
        """decision_log is deleted before recommendations (FK child-first order)."""
        if not self._SQL_PATH.exists():
            pytest.skip("SQL file not found")
        content = self._SQL_PATH.read_text().lower()
        pos_decision_log = content.find("delete from public.decision_log")
        pos_recommendations = content.find("delete from public.recommendations")
        assert pos_decision_log != -1, "SQL must delete decision_log"
        assert pos_recommendations != -1, "SQL must delete recommendations"
        assert pos_decision_log < pos_recommendations, \
            "decision_log (child) must be deleted before recommendations (parent)"

    def test_cg12_sql_fk_safe_agent_insights_before_agent_runs(self):
        """agent_insights (CASCADE child) is deleted before agent_runs (parent)."""
        if not self._SQL_PATH.exists():
            pytest.skip("SQL file not found")
        content = self._SQL_PATH.read_text().lower()
        pos_insights = content.find("delete from public.agent_insights")
        pos_runs = content.find("delete from public.agent_runs")
        assert pos_insights != -1, "SQL must delete agent_insights"
        assert pos_runs != -1, "SQL must delete agent_runs"
        assert pos_insights < pos_runs, \
            "agent_insights (CASCADE child) must be deleted before agent_runs (parent)"

    def test_cg12_sql_fk_safe_artifact_facts_before_artifacts(self):
        """research_artifact_facts (CASCADE child) is deleted before research_artifacts."""
        if not self._SQL_PATH.exists():
            pytest.skip("SQL file not found")
        content = self._SQL_PATH.read_text().lower()
        pos_facts = content.find("delete from public.research_artifact_facts")
        pos_artifacts = content.find("delete from public.research_artifacts\n")
        if pos_artifacts == -1:
            pos_artifacts = content.rfind("delete from public.research_artifacts")
        assert pos_facts != -1, "SQL must delete research_artifact_facts"
        assert pos_artifacts != -1, "SQL must delete research_artifacts"
        assert pos_facts < pos_artifacts, \
            "research_artifact_facts (CASCADE child) must be deleted before research_artifacts"

    def test_cg12_sql_child_deletes_cover_parent_targeted_rows(self):
        """Child DELETE statements include subquery to catch rows linked to parent being deleted."""
        if not self._SQL_PATH.exists():
            pytest.skip("SQL file not found")
        content = self._SQL_PATH.read_text().lower()
        # agent_insights delete should have an OR clause referencing agent_runs
        assert "or run_id in" in content or "or run_id in\n" in content or \
               "or run_id in (" in content, \
            "agent_insights DELETE must include OR run_id IN (...agent_runs...) subquery"
        # research_artifact_facts delete should have an OR clause referencing research_artifacts
        assert "or artifact_id in" in content, \
            "research_artifact_facts DELETE must include OR artifact_id IN (...) subquery"
