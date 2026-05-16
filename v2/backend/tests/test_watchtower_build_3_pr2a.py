"""Build 3 PR 2A — Watchtower production loop tests.

Proves:
  1. Railway process routing: web / analyst worker / watchtower are all supported.
  2. Watchtower entrypoint does NOT import decide() directly.
  3. Kill switch: INTEL_V3_WATCHTOWER_ENABLED=0 exits cleanly without starting loop.
  4. Loop interval / backoff is bounded (invalid values fall back to safe default).
  5. Production callables are wired at entrypoint startup (price, analyst, republish).
  6. Analyst jobs are only enqueued when evidence is stale — not every cycle.
  7. Watchtower cycle result carries intel_republish_result after a successful price persist.
  8. Process type isolation: watchtower entrypoint module does not trigger analyst LLM logic.
"""
from __future__ import annotations

import os
import pathlib
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Shared fixtures ───────────────────────────────────────────────────────────

UID = uuid.UUID("aaaabbbb-0000-0000-0000-000000000001")
NOW = datetime(2026, 5, 16, 10, 0, 0, tzinfo=timezone.utc)

_BACKEND_DIR = pathlib.Path(__file__).parents[1]   # .../v2/backend


def _make_evidence_record(evidence_type: str, *, ticker: str = "AAPL", stale: bool):
    """Build an EvidenceRecord for testing without using build_evidence_record internals."""
    from app.services.intelligence.v3.watchtower_freshness_ledger_v1 import (
        FRESHNESS_FRESH,
        FRESHNESS_STALE,
        FRESHNESS_SLA_CONFIG,
        EvidenceRecord,
        is_deploy_eligible_for_type,
        is_decision_eligible_for_type,
    )
    sla = FRESHNESS_SLA_CONFIG.get(evidence_type)
    sla_seconds = sla.fresh_seconds if sla else 0
    freshness_status = FRESHNESS_STALE if stale else FRESHNESS_FRESH
    deploy_elig, deploy_reason = is_deploy_eligible_for_type(evidence_type, freshness_status)
    decision_elig, decision_reason = is_decision_eligible_for_type(evidence_type, freshness_status)
    ts = (
        NOW - timedelta(seconds=(sla.stale_seconds + 60) if stale else (sla.fresh_seconds // 2))
        if sla else NOW
    )
    return EvidenceRecord(
        evidence_type=evidence_type,
        ticker=ticker,
        scope="ticker",
        as_of=ts,
        collected_at=ts,
        source="test",
        freshness_status=freshness_status,
        freshness_sla_seconds=sla_seconds,
        deploy_eligible=deploy_elig,
        decision_eligible=decision_elig,
        reason=deploy_reason or decision_reason,
    )


# ── 1. Railway / Procfile process routing ────────────────────────────────────

class TestProcessRouting:
    """Verify that railway.toml and Procfile define all three process types."""

    def _read_railway_toml(self) -> str:
        return (_BACKEND_DIR / "railway.toml").read_text()

    def _read_procfile(self) -> str:
        return (_BACKEND_DIR / "Procfile").read_text()

    def test_railway_toml_web_process(self):
        content = self._read_railway_toml()
        assert "uvicorn app.main:app" in content, "web process must launch uvicorn"

    def test_railway_toml_analyst_worker_process(self):
        content = self._read_railway_toml()
        assert "analyst_refresh_worker_entrypoint" in content, \
            "PROCESS_TYPE=worker must route to analyst_refresh_worker_entrypoint"

    def test_railway_toml_watchtower_process(self):
        content = self._read_railway_toml()
        assert "watchtower_worker_entrypoint" in content, \
            "PROCESS_TYPE=watchtower must route to watchtower_worker_entrypoint"
        assert "watchtower" in content

    def test_railway_toml_watchtower_separate_from_worker(self):
        content = self._read_railway_toml()
        wt_pos = content.find("watchtower_worker_entrypoint")
        analyst_pos = content.find("analyst_refresh_worker_entrypoint")
        assert wt_pos >= 0 and analyst_pos >= 0
        assert wt_pos != analyst_pos, "watchtower and analyst must be separate routing branches"

    def test_procfile_web_process(self):
        content = self._read_procfile()
        assert "web:" in content
        assert "uvicorn" in content

    def test_procfile_worker_process(self):
        content = self._read_procfile()
        assert "worker:" in content
        assert "analyst_refresh_worker_entrypoint" in content

    def test_procfile_watchtower_process(self):
        content = self._read_procfile()
        assert "watchtower:" in content
        assert "watchtower_worker_entrypoint" in content

    def test_procfile_watchtower_uses_loop_flag(self):
        content = self._read_procfile()
        lines = {}
        for line in content.splitlines():
            if ":" in line:
                key = line.split(":")[0].strip()
                lines[key] = line
        wt_line = lines.get("watchtower", "")
        assert "--loop" in wt_line, "Watchtower Procfile process must include --loop"


# ── 2. Entrypoint boundary: no direct decide() import ────────────────────────

class TestEntrypointBoundary:
    """The Watchtower entrypoint must not directly import decide() or IntelV3Service."""

    def _entrypoint_source(self) -> str:
        return (_BACKEND_DIR / "app/services/intelligence/v3/watchtower_worker_entrypoint.py").read_text()

    def test_entrypoint_source_does_not_reference_decide_directly(self):
        import re
        source = self._entrypoint_source()
        direct = re.search(r"from\s+\S+\s+import\s+.*\bdecide\b", source)
        assert direct is None, \
            "watchtower_worker_entrypoint must not import decide() directly"

    def test_entrypoint_does_not_import_intel_v3_service_at_module_level(self):
        import re
        source = self._entrypoint_source()
        # Module-level (non-indented) import of IntelV3Service is forbidden
        direct = re.search(r"^from\s+\S+intel_v3_service\s+import", source, re.MULTILINE)
        assert direct is None, \
            "watchtower_worker_entrypoint must not import IntelV3Service at module level"


# ── 3. Opt-in gate: INTEL_V3_WATCHTOWER_ENABLED must be explicitly truthy ─────

class TestKillSwitch:
    """Loop must NOT start unless INTEL_V3_WATCHTOWER_ENABLED is explicitly truthy.

    Default-false: absent env var, empty string, or any non-truthy value all
    exit cleanly. Only 1/true/yes/on enables the loop.
    """

    @pytest.mark.parametrize("disabled_value", ["0", "false", "False", "FALSE", "no", "off", "", "random"])
    def test_not_enabled_exits_cleanly(self, disabled_value):
        from app.services.intelligence.v3 import watchtower_worker_entrypoint as mod
        with patch.dict(os.environ, {"INTEL_V3_WATCHTOWER_ENABLED": disabled_value}):
            with patch.object(mod, "asyncio") as mock_asyncio:
                result = mod.main(["--loop"])
        assert result == 0, f"main() must return 0 when not enabled ({disabled_value!r})"
        mock_asyncio.run.assert_not_called()

    def test_disabled_by_default_when_env_absent(self):
        """Absent env var means disabled — explicit opt-in required."""
        from app.services.intelligence.v3 import watchtower_worker_entrypoint as mod
        env = {k: v for k, v in os.environ.items() if k != "INTEL_V3_WATCHTOWER_ENABLED"}
        with patch.dict(os.environ, env, clear=True):
            assert mod._is_watchtower_enabled() is False

    @pytest.mark.parametrize("enabled_value", ["1", "true", "True", "TRUE", "yes", "on"])
    def test_explicit_truthy_values_enable(self, enabled_value):
        from app.services.intelligence.v3 import watchtower_worker_entrypoint as mod
        with patch.dict(os.environ, {"INTEL_V3_WATCHTOWER_ENABLED": enabled_value}):
            assert mod._is_watchtower_enabled() is True

    def test_empty_string_is_disabled(self):
        from app.services.intelligence.v3 import watchtower_worker_entrypoint as mod
        with patch.dict(os.environ, {"INTEL_V3_WATCHTOWER_ENABLED": ""}):
            assert mod._is_watchtower_enabled() is False

    def test_zero_is_disabled(self):
        from app.services.intelligence.v3 import watchtower_worker_entrypoint as mod
        with patch.dict(os.environ, {"INTEL_V3_WATCHTOWER_ENABLED": "0"}):
            assert mod._is_watchtower_enabled() is False


# ── 4. Interval / backoff bounds ─────────────────────────────────────────────

class TestIntervalBounds:
    """Loop interval must fall back to safe defaults for invalid or non-positive values."""

    def _resolve(self, env_value: str) -> float:
        from app.services.intelligence.v3 import watchtower_worker_entrypoint as mod
        with patch.dict(os.environ, {"INTEL_V3_WATCHTOWER_WORKER_INTERVAL_SECONDS": env_value}):
            return mod._resolve_interval_seconds()

    def test_default_interval_is_60_seconds(self):
        from app.services.intelligence.v3 import watchtower_worker_entrypoint as mod
        env = {k: v for k, v in os.environ.items()
               if k != "INTEL_V3_WATCHTOWER_WORKER_INTERVAL_SECONDS"}
        with patch.dict(os.environ, env, clear=True):
            assert mod._resolve_interval_seconds() == 60.0

    def test_valid_interval_accepted(self):
        assert self._resolve("120") == 120.0
        assert self._resolve("300") == 300.0

    def test_zero_falls_back_to_default(self):
        assert self._resolve("0") == 60.0

    def test_negative_falls_back_to_default(self):
        assert self._resolve("-10") == 60.0

    def test_non_numeric_falls_back_to_default(self):
        assert self._resolve("bad") == 60.0

    def test_empty_string_falls_back_to_default(self):
        assert self._resolve("") == 60.0

    def test_interval_has_minimum_safe_floor(self):
        from app.services.intelligence.v3 import watchtower_worker_entrypoint as mod
        default = mod.DEFAULT_INTERVAL_SECONDS
        assert default > 0, "DEFAULT_INTERVAL_SECONDS must be positive"
        assert default >= 30, "DEFAULT_INTERVAL_SECONDS must be >= 30s for production safety"


# ── 5. Production callables wired ────────────────────────────────────────────

class TestProductionCallablesWired:
    """All three production callables must be built and passed into the cycle."""

    @pytest.mark.asyncio
    async def test_callable_builders_called_once_per_cycle(self):
        from app.services.intelligence.v3 import watchtower_worker_entrypoint as mod
        mock_client = MagicMock()
        build_price = MagicMock(return_value=AsyncMock(return_value={}))
        build_analyst = MagicMock(return_value=AsyncMock(return_value=0))
        build_republish = MagicMock(return_value=AsyncMock(return_value={}))

        with patch.object(mod, "_build_default_price_refresh_callable", build_price):
            with patch.object(mod, "_build_default_analyst_enqueue_callable", build_analyst):
                with patch.object(mod, "_build_default_intel_republish_callable", build_republish):
                    with patch.object(mod, "_fetch_active_user_ids", return_value=[]):
                        await mod._run_cycle_for_all_users(mock_client)

        build_price.assert_called_once_with(mock_client)
        build_analyst.assert_called_once_with(mock_client)
        build_republish.assert_called_once_with(mock_client)

    def test_price_callable_builder_delegates_to_callables_module(self):
        from app.services.intelligence.v3 import watchtower_worker_entrypoint as mod
        mock_client = MagicMock()
        expected = object()
        with patch(
            "app.services.intelligence.v3.watchtower_callables_v1"
            ".build_default_price_refresh_callable",
            return_value=expected,
        ):
            result = mod._build_default_price_refresh_callable(mock_client)
        assert result is expected

    def test_analyst_callable_builder_delegates_to_callables_module(self):
        from app.services.intelligence.v3 import watchtower_worker_entrypoint as mod
        mock_client = MagicMock()
        expected = object()
        with patch(
            "app.services.intelligence.v3.watchtower_callables_v1"
            ".build_default_analyst_enqueue_callable",
            return_value=expected,
        ):
            result = mod._build_default_analyst_enqueue_callable(mock_client)
        assert result is expected

    def test_republish_callable_builder_delegates_to_callables_module(self):
        from app.services.intelligence.v3 import watchtower_worker_entrypoint as mod
        mock_client = MagicMock()
        expected = object()
        with patch(
            "app.services.intelligence.v3.watchtower_callables_v1"
            ".build_default_intel_republish_callable",
            return_value=expected,
        ):
            result = mod._build_default_intel_republish_callable(mock_client)
        assert result is expected


# ── 6. Analyst jobs only when evidence is stale ──────────────────────────────

class TestAnalystEnqueuePolicy:
    """Analyst enqueue callable should only fire when analyst evidence is stale."""

    @pytest.mark.asyncio
    async def test_no_analyst_jobs_when_analyst_evidence_fresh(self):
        from app.services.intelligence.v3.watchtower_background_refresh_worker_v1 import (
            WatchtowerBackgroundRefreshWorker,
        )
        from app.services.intelligence.v3.watchtower_freshness_ledger_v1 import (
            EVIDENCE_TYPE_ANALYST_LLM,
        )
        fresh_record = _make_evidence_record(EVIDENCE_TYPE_ANALYST_LLM, stale=False)
        mock_client = MagicMock()
        mock_analyst = AsyncMock(return_value=0)

        with patch(
            "app.services.intelligence.v3.watchtower_background_refresh_worker_v1.collect_evidence_records",
            return_value=[fresh_record],
        ):
            worker = WatchtowerBackgroundRefreshWorker(
                client=mock_client,
                analyst_job_enqueue_callable=mock_analyst,
            )
            result = await worker.run_refresh_cycle(UID, now=NOW)

        mock_analyst.assert_not_called()
        assert result.analyst_jobs_enqueued == 0

    @pytest.mark.asyncio
    async def test_analyst_jobs_enqueued_when_stale(self):
        from app.services.intelligence.v3.watchtower_background_refresh_worker_v1 import (
            WatchtowerBackgroundRefreshWorker,
        )
        from app.services.intelligence.v3.watchtower_freshness_ledger_v1 import (
            EVIDENCE_TYPE_ANALYST_LLM,
        )
        stale_record = _make_evidence_record(EVIDENCE_TYPE_ANALYST_LLM, stale=True)
        mock_client = MagicMock()
        mock_analyst = AsyncMock(return_value=1)

        with patch(
            "app.services.intelligence.v3.watchtower_background_refresh_worker_v1.collect_evidence_records",
            return_value=[stale_record],
        ):
            worker = WatchtowerBackgroundRefreshWorker(
                client=mock_client,
                analyst_job_enqueue_callable=mock_analyst,
            )
            result = await worker.run_refresh_cycle(UID, now=NOW)

        mock_analyst.assert_called_once()
        assert result.analyst_jobs_enqueued == 1


# ── 7. Watchtower cycle result carries intel_republish_result ─────────────────

class TestCycleResultIntegration:
    """WatchtowerRefreshCycleResult carries intel_republish_result after price persist."""

    @pytest.mark.asyncio
    async def test_intel_republish_result_present_after_price_refresh(self):
        from app.services.intelligence.v3.watchtower_background_refresh_worker_v1 import (
            WatchtowerBackgroundRefreshWorker,
        )
        from app.services.intelligence.v3.watchtower_freshness_ledger_v1 import (
            EVIDENCE_TYPE_PRICE,
        )
        from app.services.intelligence.v3.watchtower_intel_republisher_v1 import (
            PUBLISH_REBUILT_AND_PUBLISHED,
            WatchtowerRepublishResult,
        )

        stale_record = _make_evidence_record(EVIDENCE_TYPE_PRICE, stale=True)
        mock_client = MagicMock()
        mock_price = AsyncMock(return_value={"AAPL": 150.0})

        republish_result = WatchtowerRepublishResult(
            publish_status=PUBLISH_REBUILT_AND_PUBLISHED,
            analyst_jobs_queued=0,
        )
        persist_result = MagicMock()
        persist_result.persisted = True
        persist_result.certified_ticker_count = 1
        persist_result.carried_ticker_count = 0

        with patch(
            "app.services.intelligence.v3.watchtower_background_refresh_worker_v1.collect_evidence_records",
            return_value=[stale_record],
        ):
            with patch(
                "app.services.intelligence.v3.watchtower_price_snapshot_writer_v1.persist_watchtower_price_snapshot",
                return_value=persist_result,
            ):
                with patch(
                    "app.services.intelligence.v3.watchtower_intel_republisher_v1.compare_and_republish",
                    return_value=republish_result,
                ):
                    worker = WatchtowerBackgroundRefreshWorker(
                        client=mock_client,
                        price_refresh_callable=mock_price,
                        intel_republish_callable=AsyncMock(
                            return_value={"snapshot_source": "worker_certified"}
                        ),
                    )
                    result = await worker.run_refresh_cycle(UID, now=NOW)

        assert result.intel_republish_result is not None
        assert result.intel_republish_result["publish_status"] == PUBLISH_REBUILT_AND_PUBLISHED

    @pytest.mark.asyncio
    async def test_intel_republish_result_none_when_price_already_fresh(self):
        """When prices are already fresh, intel_republish_result stays None."""
        from app.services.intelligence.v3.watchtower_background_refresh_worker_v1 import (
            WatchtowerBackgroundRefreshWorker,
        )
        from app.services.intelligence.v3.watchtower_freshness_ledger_v1 import (
            EVIDENCE_TYPE_PRICE,
        )

        fresh_record = _make_evidence_record(EVIDENCE_TYPE_PRICE, stale=False)
        mock_client = MagicMock()
        mock_republish = AsyncMock()

        with patch(
            "app.services.intelligence.v3.watchtower_background_refresh_worker_v1.collect_evidence_records",
            return_value=[fresh_record],
        ):
            worker = WatchtowerBackgroundRefreshWorker(
                client=mock_client,
                intel_republish_callable=mock_republish,
            )
            result = await worker.run_refresh_cycle(UID, now=NOW)

        assert result.intel_republish_result is None
        mock_republish.assert_not_called()


# ── 8. Watchtower does not trigger analyst LLM ────────────────────────────────

class TestWatchtowerNoLLMInCycle:
    """The Watchtower loop must not run full analyst LLM research on every cycle."""

    def test_background_refresh_worker_does_not_import_agent_orchestrator(self):
        import re
        source = (
            _BACKEND_DIR / "app/services/intelligence/v3/watchtower_background_refresh_worker_v1.py"
        ).read_text()
        direct = re.search(r"^from\s+\S+\s+import\s+.*AgentOrchestrator", source, re.MULTILINE)
        assert direct is None, \
            "watchtower_background_refresh_worker_v1 must not import AgentOrchestrator directly"

    def test_entrypoint_does_not_reference_agent_orchestrator(self):
        import re
        source = (
            _BACKEND_DIR / "app/services/intelligence/v3/watchtower_worker_entrypoint.py"
        ).read_text()
        assert "AgentOrchestrator" not in source, \
            "watchtower_worker_entrypoint must not reference AgentOrchestrator"

    @pytest.mark.asyncio
    async def test_stale_analyst_evidence_enqueues_job_not_llm(self):
        """Stale analyst evidence enqueues a job to the analyst worker; no LLM inline."""
        from app.services.intelligence.v3.watchtower_background_refresh_worker_v1 import (
            WatchtowerBackgroundRefreshWorker,
        )
        from app.services.intelligence.v3.watchtower_freshness_ledger_v1 import (
            EVIDENCE_TYPE_ANALYST_LLM,
        )

        stale_record = _make_evidence_record(EVIDENCE_TYPE_ANALYST_LLM, stale=True)
        enqueue_calls: list = []

        async def _mock_enqueue(user_id, tickers):
            enqueue_calls.append((user_id, tickers))
            return len(tickers)

        mock_client = MagicMock()

        with patch(
            "app.services.intelligence.v3.watchtower_background_refresh_worker_v1.collect_evidence_records",
            return_value=[stale_record],
        ):
            worker = WatchtowerBackgroundRefreshWorker(
                client=mock_client,
                analyst_job_enqueue_callable=_mock_enqueue,
            )
            result = await worker.run_refresh_cycle(UID, now=NOW)

        assert len(enqueue_calls) == 1, "enqueue must be called once for stale analyst evidence"
        assert result.analyst_jobs_enqueued > 0
