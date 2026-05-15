"""Build 2 — Evidence-grade certification + publish contract tests.

Proves:
  1. When Watchtower evidence is newer than certified Intel snapshot,
     evidence_newer_than_certified_snapshot=True and republish is triggered.
  2. When snapshot is already current (evidence not newer), publish_status=certified_current
     and the republish callable is NOT called.
  3. Deterministic rebuild path: callable called → publish_status=rebuilt_and_published.
  4. Honest pending state: evidence newer but no callable → publish_status=republish_pending.
  5. Certification blocked: callable raises → publish_status=certification_blocked.
  6. No Intel snapshot exists → publish_status=no_snapshot_exists.
  7. analyst_jobs_queued always 0 for price-only freshness (never triggers LLM jobs).
  8. get_evidence_freshness_state returns correct state for API response embedding.
  9. WatchtowerRefreshCycleResult carries intel_republish_result after price persist.
  10. Worker integration: intel_republish_callable wired through run_watchtower_cycle_for_user.
  11. Stale analyst evidence does NOT affect price-only republish path.
  12. API snapshot response includes evidence_freshness_state field.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.intelligence.v3.watchtower_intel_republisher_v1 import (
    PUBLISH_CERTIFICATION_BLOCKED,
    PUBLISH_CERTIFIED_CURRENT,
    PUBLISH_NO_SNAPSHOT_EXISTS,
    PUBLISH_REBUILT_AND_PUBLISHED,
    PUBLISH_REPUBLISH_PENDING,
    WatchtowerRepublishResult,
    compare_and_republish,
    get_evidence_freshness_state,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_client(
    *,
    intel_snapshot_generated_at: Optional[str] = None,
    intel_snapshot_id: Optional[str] = None,
    portfolio_snapshot_at: Optional[str] = None,
    portfolio_snapshot_id: Optional[str] = None,
    no_intel_snapshot: bool = False,
    no_portfolio_snapshot: bool = False,
) -> MagicMock:
    """Build a minimal Supabase client mock with controlled responses."""
    client = MagicMock()
    uid = str(uuid.uuid4())

    def _make_table_chain(intel_rows, portfolio_rows):
        """Return a mock .table() that distinguishes intel_v3_snapshots vs portfolio_snapshots."""
        def table(name):
            chain = MagicMock()
            if name == "intel_v3_snapshots":
                chain.select.return_value = chain
                chain.eq.return_value = chain
                chain.order.return_value = chain
                chain.limit.return_value = chain
                chain.execute.return_value = MagicMock(data=intel_rows)
            elif name == "portfolio_snapshots":
                chain.select.return_value = chain
                chain.eq.return_value = chain
                chain.order.return_value = chain
                chain.limit.return_value = chain
                chain.execute.return_value = MagicMock(data=portfolio_rows)
            return chain
        return table

    snap_id = intel_snapshot_id or str(uuid.uuid4())
    gen_at = intel_snapshot_generated_at or "2026-05-15T10:00:00+00:00"
    intel_rows = [] if no_intel_snapshot else [
        {"payload": {"snapshot_id": snap_id, "generated_at": gen_at, "snapshot_source": "worker_certified"}}
    ]

    port_id = portfolio_snapshot_id or str(uuid.uuid4())
    port_at = portfolio_snapshot_at or "2026-05-15T11:00:00+00:00"
    portfolio_rows = [] if no_portfolio_snapshot else [
        {"id": port_id, "snapshot_at": port_at}
    ]

    client.table = _make_table_chain(intel_rows, portfolio_rows)
    return client


def _make_republish_callable(*, raises: bool = False) -> AsyncMock:
    if raises:
        coro = AsyncMock(side_effect=ValueError("certification_failed"))
    else:
        coro = AsyncMock(return_value={"snapshot_source": "worker_certified"})
    return coro


USER_ID = uuid.uuid4()


# ── compare_and_republish tests ───────────────────────────────────────────────

class TestCompareAndRepublish:

    @pytest.mark.asyncio
    async def test_evidence_newer_triggers_republish(self):
        """Fresh Watchtower evidence (60min newer) triggers deterministic rebuild."""
        intel_ts = "2026-05-15T10:00:00+00:00"
        port_ts = "2026-05-15T11:00:00+00:00"  # 60 min newer
        client = _make_client(
            intel_snapshot_generated_at=intel_ts,
            portfolio_snapshot_at=port_ts,
        )
        callable_ = _make_republish_callable()

        result = await compare_and_republish(USER_ID, client, intel_republish_callable=callable_)

        assert result.evidence_newer_than_certified_snapshot is True
        assert result.publish_status == PUBLISH_REBUILT_AND_PUBLISHED
        callable_.assert_awaited_once_with(USER_ID)

    @pytest.mark.asyncio
    async def test_evidence_not_newer_skips_republish(self):
        """When snapshot is already current, publish_status=certified_current, callable not called."""
        intel_ts = "2026-05-15T11:00:00+00:00"
        port_ts = "2026-05-15T10:55:00+00:00"  # 5 min OLDER than Intel snapshot
        client = _make_client(
            intel_snapshot_generated_at=intel_ts,
            portfolio_snapshot_at=port_ts,
        )
        callable_ = _make_republish_callable()

        result = await compare_and_republish(USER_ID, client, intel_republish_callable=callable_)

        assert result.evidence_newer_than_certified_snapshot is False
        assert result.publish_status == PUBLISH_CERTIFIED_CURRENT
        callable_.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_evidence_within_threshold_skips_republish(self):
        """Evidence less than 10s newer is considered already current (clock imprecision guard)."""
        intel_ts = "2026-05-15T10:00:00+00:00"
        port_ts = "2026-05-15T10:00:05+00:00"  # 5s newer — under threshold
        client = _make_client(
            intel_snapshot_generated_at=intel_ts,
            portfolio_snapshot_at=port_ts,
        )
        callable_ = _make_republish_callable()

        result = await compare_and_republish(USER_ID, client, intel_republish_callable=callable_)

        assert result.evidence_newer_than_certified_snapshot is False
        assert result.publish_status == PUBLISH_CERTIFIED_CURRENT
        callable_.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_intel_snapshot_returns_no_snapshot_exists(self):
        """No prior Intel snapshot → publish_status=no_snapshot_exists; callable not called."""
        client = _make_client(no_intel_snapshot=True)
        callable_ = _make_republish_callable()

        result = await compare_and_republish(USER_ID, client, intel_republish_callable=callable_)

        assert result.publish_status == PUBLISH_NO_SNAPSHOT_EXISTS
        callable_.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_callable_returns_republish_pending(self):
        """Evidence newer but no callable → publish_status=republish_pending."""
        intel_ts = "2026-05-15T10:00:00+00:00"
        port_ts = "2026-05-15T11:00:00+00:00"  # 60 min newer
        client = _make_client(
            intel_snapshot_generated_at=intel_ts,
            portfolio_snapshot_at=port_ts,
        )

        result = await compare_and_republish(USER_ID, client, intel_republish_callable=None)

        assert result.evidence_newer_than_certified_snapshot is True
        assert result.publish_status == PUBLISH_REPUBLISH_PENDING

    @pytest.mark.asyncio
    async def test_callable_raises_returns_certification_blocked(self):
        """Republish callable raises → publish_status=certification_blocked with error."""
        intel_ts = "2026-05-15T10:00:00+00:00"
        port_ts = "2026-05-15T11:00:00+00:00"
        client = _make_client(
            intel_snapshot_generated_at=intel_ts,
            portfolio_snapshot_at=port_ts,
        )
        callable_ = _make_republish_callable(raises=True)

        result = await compare_and_republish(USER_ID, client, intel_republish_callable=callable_)

        assert result.publish_status == PUBLISH_CERTIFICATION_BLOCKED
        assert result.error is not None
        assert result.evidence_newer_than_certified_snapshot is True
        callable_.assert_awaited_once_with(USER_ID)

    @pytest.mark.asyncio
    async def test_analyst_jobs_always_zero(self):
        """analyst_jobs_queued is always 0: price-only refresh never triggers LLM jobs."""
        client = _make_client(
            intel_snapshot_generated_at="2026-05-15T10:00:00+00:00",
            portfolio_snapshot_at="2026-05-15T11:00:00+00:00",
        )

        result = await compare_and_republish(USER_ID, client, intel_republish_callable=_make_republish_callable())

        assert result.analyst_jobs_queued == 0

    @pytest.mark.asyncio
    async def test_watchtower_refresh_triggered_always_true(self):
        """watchtower_refresh_triggered is always True in this context."""
        client = _make_client(
            intel_snapshot_generated_at="2026-05-15T10:00:00+00:00",
            portfolio_snapshot_at="2026-05-15T11:00:00+00:00",
        )

        result = await compare_and_republish(USER_ID, client)

        assert result.watchtower_refresh_triggered is True

    @pytest.mark.asyncio
    async def test_result_contains_provenance_fields(self):
        """Result includes snapshot_id, generated_at, evidence snapshot_at, and portfolio snapshot_id."""
        snap_id = "snap-abc"
        port_id = "port-xyz"
        intel_ts = "2026-05-15T10:00:00+00:00"
        port_ts = "2026-05-15T11:00:00+00:00"

        client = _make_client(
            intel_snapshot_generated_at=intel_ts,
            intel_snapshot_id=snap_id,
            portfolio_snapshot_at=port_ts,
            portfolio_snapshot_id=port_id,
        )

        result = await compare_and_republish(USER_ID, client)

        assert result.latest_certified_snapshot_id == snap_id
        assert result.latest_certified_snapshot_generated_at == intel_ts
        assert port_id in (result.latest_decision_evidence_snapshot_id or "")
        assert result.latest_decision_evidence_snapshot_at == port_ts

    @pytest.mark.asyncio
    async def test_to_dict_serializes_all_fields(self):
        """to_dict() includes all required observability fields."""
        client = _make_client(
            intel_snapshot_generated_at="2026-05-15T10:00:00+00:00",
            portfolio_snapshot_at="2026-05-15T11:00:00+00:00",
        )

        result = await compare_and_republish(USER_ID, client, intel_republish_callable=_make_republish_callable())

        d = result.to_dict()
        required = [
            "publish_status",
            "latest_certified_snapshot_id",
            "latest_certified_snapshot_generated_at",
            "latest_decision_evidence_snapshot_id",
            "latest_decision_evidence_snapshot_at",
            "evidence_newer_than_certified_snapshot",
            "analyst_jobs_queued",
            "watchtower_refresh_triggered",
            "error",
            "duration_ms",
        ]
        for field in required:
            assert field in d, f"Missing field: {field}"

    @pytest.mark.asyncio
    async def test_no_portfolio_snapshot_returns_certified_current(self):
        """No Watchtower portfolio snapshot yet → snapshot is already current (no comparison possible)."""
        client = _make_client(
            intel_snapshot_generated_at="2026-05-15T10:00:00+00:00",
            no_portfolio_snapshot=True,
        )
        callable_ = _make_republish_callable()

        result = await compare_and_republish(USER_ID, client, intel_republish_callable=callable_)

        # evidence_newer requires evidence_ts to be parseable; with None evidence_ts and
        # a parseable intel_ts, the default is False → certified_current
        assert result.publish_status == PUBLISH_CERTIFIED_CURRENT
        callable_.assert_not_awaited()


# ── get_evidence_freshness_state tests ────────────────────────────────────────

class TestGetEvidenceFreshnessState:

    @pytest.mark.asyncio
    async def test_returns_republish_pending_when_evidence_newer(self):
        """Returns PUBLISH_REPUBLISH_PENDING when portfolio snapshot is newer than Intel."""
        intel_ts = "2026-05-15T10:00:00+00:00"
        port_ts = "2026-05-15T11:00:00+00:00"
        client = _make_client(portfolio_snapshot_at=port_ts)

        state = await get_evidence_freshness_state(
            USER_ID, client, intel_snapshot_generated_at=intel_ts
        )

        assert state == PUBLISH_REPUBLISH_PENDING

    @pytest.mark.asyncio
    async def test_returns_certified_current_when_not_newer(self):
        """Returns PUBLISH_CERTIFIED_CURRENT when Intel snapshot is current."""
        intel_ts = "2026-05-15T11:00:00+00:00"
        port_ts = "2026-05-15T10:30:00+00:00"
        client = _make_client(portfolio_snapshot_at=port_ts)

        state = await get_evidence_freshness_state(
            USER_ID, client, intel_snapshot_generated_at=intel_ts
        )

        assert state == PUBLISH_CERTIFIED_CURRENT

    @pytest.mark.asyncio
    async def test_returns_certified_current_when_no_portfolio_snapshot(self):
        """No portfolio snapshot → certified_current (no evidence to compare)."""
        client = _make_client(no_portfolio_snapshot=True)

        state = await get_evidence_freshness_state(
            USER_ID, client, intel_snapshot_generated_at="2026-05-15T10:00:00+00:00"
        )

        assert state == PUBLISH_CERTIFIED_CURRENT

    @pytest.mark.asyncio
    async def test_returns_republish_pending_on_db_error(self):
        """DB errors must return republish_pending — not certified_current (honest failure state)."""
        client = MagicMock()
        client.table.side_effect = RuntimeError("db down")

        state = await get_evidence_freshness_state(
            USER_ID, client, intel_snapshot_generated_at="2026-05-15T10:00:00+00:00"
        )

        assert state == PUBLISH_REPUBLISH_PENDING


# ── Worker integration tests ───────────────────────────────────────────────────

class TestWatchtowerWorkerIntegration:

    def test_cycle_result_has_intel_republish_result_field(self):
        """WatchtowerRefreshCycleResult carries intel_republish_result field (Build 2 contract)."""
        from app.services.intelligence.v3.watchtower_background_refresh_worker_v1 import (
            WatchtowerRefreshCycleResult,
        )
        result = WatchtowerRefreshCycleResult()
        assert hasattr(result, "intel_republish_result")
        assert result.intel_republish_result is None

    def test_cycle_result_to_dict_includes_intel_republish_result(self):
        """to_dict() serializes intel_republish_result for structured logging."""
        from app.services.intelligence.v3.watchtower_background_refresh_worker_v1 import (
            WatchtowerRefreshCycleResult,
        )
        result = WatchtowerRefreshCycleResult()
        result.intel_republish_result = {"publish_status": PUBLISH_REBUILT_AND_PUBLISHED}
        d = result.to_dict()
        assert "intel_republish_result" in d
        assert d["intel_republish_result"]["publish_status"] == PUBLISH_REBUILT_AND_PUBLISHED

    def test_worker_accepts_intel_republish_callable(self):
        """WatchtowerBackgroundRefreshWorker accepts intel_republish_callable param."""
        from app.services.intelligence.v3.watchtower_background_refresh_worker_v1 import (
            WatchtowerBackgroundRefreshWorker,
        )
        client = MagicMock()
        republish_callable = AsyncMock()
        worker = WatchtowerBackgroundRefreshWorker(
            client=client,
            intel_republish_callable=republish_callable,
        )
        assert worker._intel_republish is republish_callable

    def test_run_watchtower_cycle_for_user_accepts_republish_callable(self):
        """run_watchtower_cycle_for_user convenience function accepts intel_republish_callable."""
        import inspect
        from app.services.intelligence.v3.watchtower_background_refresh_worker_v1 import (
            run_watchtower_cycle_for_user,
        )
        sig = inspect.signature(run_watchtower_cycle_for_user)
        assert "intel_republish_callable" in sig.parameters

    def test_worker_source_calls_compare_and_republish_after_persist(self):
        """Source inspection: worker must call compare_and_republish after persist succeeds."""
        import pathlib
        source = pathlib.Path(
            "app/services/intelligence/v3/watchtower_background_refresh_worker_v1.py"
        ).read_text()
        assert "compare_and_republish" in source, (
            "Worker must import and call compare_and_republish after durable persist"
        )
        assert "watchtower_intel_republisher_v1" in source, (
            "Worker must import from watchtower_intel_republisher_v1"
        )
        # compare_and_republish call must appear AFTER persist_watchtower_price_snapshot
        persist_pos = source.find("persist_watchtower_price_snapshot")
        republish_pos = source.find("compare_and_republish")
        assert persist_pos > 0 and republish_pos > 0
        assert republish_pos > persist_pos, (
            "compare_and_republish must be called AFTER persist_watchtower_price_snapshot"
        )

    def test_worker_source_passes_intel_republish_callable(self):
        """Source inspection: compare_and_republish call must pass intel_republish_callable."""
        import pathlib
        source = pathlib.Path(
            "app/services/intelligence/v3/watchtower_background_refresh_worker_v1.py"
        ).read_text()
        assert "intel_republish_callable=self._intel_republish" in source, (
            "compare_and_republish must receive the injected intel_republish_callable"
        )

    def test_worker_source_no_decide_import(self):
        """Hard boundary: watchtower worker must never import decide() directly."""
        import pathlib
        source = pathlib.Path(
            "app/services/intelligence/v3/watchtower_background_refresh_worker_v1.py"
        ).read_text()
        assert "from .decision_policy_v1 import decide" not in source, (
            "Worker boundary violation: worker must not import decide()"
        )
        assert "import decide" not in source


# ── API snapshot response serialization test (source inspection) ──────────────

class TestSnapshotAPIResponseFreshnessState:

    def test_get_latest_snapshot_source_embeds_evidence_freshness_state(self):
        """Source inspection: get_latest_snapshot must embed evidence_freshness_state."""
        import pathlib
        source = pathlib.Path(
            "app/services/intelligence/v3/intel_v3_service.py"
        ).read_text()
        assert "evidence_freshness_state" in source, (
            "get_latest_snapshot() must embed evidence_freshness_state in the response"
        )
        assert "get_evidence_freshness_state" in source, (
            "get_latest_snapshot() must call get_evidence_freshness_state from republisher"
        )

    def test_get_latest_snapshot_uses_copy_not_mutation(self):
        """Source inspection: get_latest_snapshot must use dict(payload) to avoid mutation."""
        import pathlib
        source = pathlib.Path(
            "app/services/intelligence/v3/intel_v3_service.py"
        ).read_text()
        # The response payload must be a copy, not the original dict
        assert "response_payload = dict(payload)" in source, (
            "get_latest_snapshot() must copy the payload dict before adding evidence_freshness_state"
        )

    def test_get_latest_snapshot_imports_republisher(self):
        """Source inspection: get_latest_snapshot must import from watchtower_intel_republisher_v1."""
        import pathlib
        source = pathlib.Path(
            "app/services/intelligence/v3/intel_v3_service.py"
        ).read_text()
        assert "watchtower_intel_republisher_v1" in source, (
            "intel_v3_service must import from watchtower_intel_republisher_v1 "
            "to embed evidence_freshness_state in the snapshot response"
        )

    def test_get_latest_snapshot_logs_evidence_freshness_state(self):
        """Source inspection: the snapshot response summary log includes evidence_freshness_state."""
        import pathlib
        source = pathlib.Path(
            "app/services/intelligence/v3/intel_v3_service.py"
        ).read_text()
        # Find the snapshot_response_summary log line
        assert "evidence_freshness_state=%s" in source, (
            "intel_v3_snapshot_response_summary log must include evidence_freshness_state"
        )

    def test_get_latest_snapshot_returns_response_payload_not_raw_payload(self):
        """Source inspection: return value must be response_payload (copy), not raw payload."""
        import pathlib
        source = pathlib.Path(
            "app/services/intelligence/v3/intel_v3_service.py"
        ).read_text()
        # Source must declare and return response_payload (the copied dict with freshness state)
        assert "response_payload = dict(payload)" in source, (
            "get_latest_snapshot() must build a copy: response_payload = dict(payload)"
        )
        assert "return response_payload" in source, (
            "get_latest_snapshot() must return response_payload, not raw payload"
        )


# ── Analyst evidence current — no analyst jobs queued ────────────────────────

class TestAnalystEvidenceCurrent:

    @pytest.mark.asyncio
    async def test_analyst_jobs_zero_regardless_of_price_freshness(self):
        """Price-only republish path always queues 0 analyst jobs regardless of analyst evidence state."""
        # This test verifies the contract: compare_and_republish() never increments
        # analyst_jobs_queued, even when analyst evidence might be stale.
        # Analyst job enqueuing is a separate concern handled by enqueue_run_v3.
        client = _make_client(
            intel_snapshot_generated_at="2026-05-15T10:00:00+00:00",
            portfolio_snapshot_at="2026-05-15T11:30:00+00:00",
        )

        result = await compare_and_republish(
            USER_ID, client, intel_republish_callable=_make_republish_callable()
        )

        assert result.analyst_jobs_queued == 0


# ── Patch blocker tests ───────────────────────────────────────────────────────

class TestPatchBlocker1UrgentRepublishCallable:
    """Blocker 1: enqueue_run_v3 urgent path must wire intel_republish_callable."""

    def test_enqueue_run_v3_urgent_path_imports_republish_callable(self):
        """Source: enqueue_run_v3 urgent path imports build_default_intel_republish_callable."""
        import pathlib
        source = pathlib.Path(
            "app/services/intelligence/v3/intel_v3_service.py"
        ).read_text()
        assert "build_default_intel_republish_callable" in source, (
            "enqueue_run_v3 urgent path must import build_default_intel_republish_callable "
            "to wire the republish callable into the Watchtower cycle"
        )

    def test_enqueue_run_v3_urgent_path_passes_republish_callable(self):
        """Source: run_watchtower_cycle_for_user call includes intel_republish_callable=."""
        import pathlib
        source = pathlib.Path(
            "app/services/intelligence/v3/intel_v3_service.py"
        ).read_text()
        assert "intel_republish_callable=build_default_intel_republish_callable" in source, (
            "urgent Watchtower cycle must receive intel_republish_callable; "
            "without it compare_and_republish is a no-op"
        )


class TestPatchBlocker2RepublishResultInspection:
    """Blocker 2: compare_and_republish must inspect snapshot_source before claiming rebuilt_and_published."""

    @pytest.mark.asyncio
    async def test_callable_returns_non_certified_gives_certification_blocked(self):
        """If republish callable returns certification_failed snapshot, status=certification_blocked."""
        intel_ts = "2026-05-15T10:00:00+00:00"
        port_ts = "2026-05-15T11:00:00+00:00"
        client = _make_client(
            intel_snapshot_generated_at=intel_ts,
            portfolio_snapshot_at=port_ts,
        )
        non_certified_callable = AsyncMock(
            return_value={"snapshot_source": "certification_failed"}
        )

        result = await compare_and_republish(
            USER_ID, client, intel_republish_callable=non_certified_callable
        )

        assert result.publish_status == PUBLISH_CERTIFICATION_BLOCKED
        assert result.error is not None
        assert "snapshot_source" in result.error

    @pytest.mark.asyncio
    async def test_callable_returns_worker_certified_gives_rebuilt_and_published(self):
        """If callable returns worker_certified snapshot, status=rebuilt_and_published."""
        intel_ts = "2026-05-15T10:00:00+00:00"
        port_ts = "2026-05-15T11:00:00+00:00"
        client = _make_client(
            intel_snapshot_generated_at=intel_ts,
            portfolio_snapshot_at=port_ts,
        )
        certified_callable = AsyncMock(
            return_value={"snapshot_source": "worker_certified"}
        )

        result = await compare_and_republish(
            USER_ID, client, intel_republish_callable=certified_callable
        )

        assert result.publish_status == PUBLISH_REBUILT_AND_PUBLISHED

    @pytest.mark.asyncio
    async def test_callable_returns_none_gives_certification_blocked(self):
        """If callable returns None (unexpected), status=certification_blocked."""
        intel_ts = "2026-05-15T10:00:00+00:00"
        port_ts = "2026-05-15T11:00:00+00:00"
        client = _make_client(
            intel_snapshot_generated_at=intel_ts,
            portfolio_snapshot_at=port_ts,
        )
        none_callable = AsyncMock(return_value=None)

        result = await compare_and_republish(
            USER_ID, client, intel_republish_callable=none_callable
        )

        assert result.publish_status == PUBLISH_CERTIFICATION_BLOCKED


class TestPatchBlocker3SkipPersistOnFail:
    """Blocker 3: failed Watchtower-triggered republish must not replace previous worker_certified snapshot."""

    def test_run_prewarm_snapshot_has_skip_persist_on_fail_param(self):
        """Source: run_prewarm_snapshot signature includes skip_persist_on_fail parameter."""
        import pathlib
        source = pathlib.Path(
            "app/services/intelligence/v3/intel_v3_service.py"
        ).read_text()
        assert "skip_persist_on_fail" in source, (
            "run_prewarm_snapshot must accept skip_persist_on_fail=False parameter "
            "to preserve previous worker_certified snapshot on certification failure"
        )

    def test_callables_passes_skip_persist_on_fail_true(self):
        """Source: build_default_intel_republish_callable passes skip_persist_on_fail=True."""
        import pathlib
        source = pathlib.Path(
            "app/services/intelligence/v3/watchtower_callables_v1.py"
        ).read_text()
        assert "skip_persist_on_fail=True" in source, (
            "Watchtower callable must pass skip_persist_on_fail=True to run_prewarm_snapshot "
            "so failed certification never overwrites a previous worker_certified snapshot"
        )

    def test_prewarm_guards_persist_on_skip_persist_on_fail(self):
        """Source: run_prewarm_snapshot guards persist step when skip_persist_on_fail and not certified."""
        import pathlib
        source = pathlib.Path(
            "app/services/intelligence/v3/intel_v3_service.py"
        ).read_text()
        assert "skip_persist_on_fail and not contract_certified" in source, (
            "run_prewarm_snapshot must skip _persist_snapshot when "
            "skip_persist_on_fail=True and contract_certified is False"
        )


class TestPatchBlocker4HonestErrorState:
    """Blocker 4: get_evidence_freshness_state must not return certified_current on errors."""

    def test_source_error_path_returns_republish_pending(self):
        """Source: exception handler in get_evidence_freshness_state returns PUBLISH_REPUBLISH_PENDING."""
        import pathlib
        source = pathlib.Path(
            "app/services/intelligence/v3/watchtower_intel_republisher_v1.py"
        ).read_text()
        # Find the get_evidence_freshness_state function
        fn_start = source.find("async def get_evidence_freshness_state")
        fn_end = source.find("\nasync def ", fn_start + 1)
        fn_source = source[fn_start:fn_end if fn_end > 0 else fn_start + 2000]
        # Verify the exception handler returns PUBLISH_REPUBLISH_PENDING, not PUBLISH_CERTIFIED_CURRENT
        assert "return PUBLISH_REPUBLISH_PENDING" in fn_source, (
            "get_evidence_freshness_state exception handler must return PUBLISH_REPUBLISH_PENDING "
            "not PUBLISH_CERTIFIED_CURRENT — errors must not silently report a green state"
        )
