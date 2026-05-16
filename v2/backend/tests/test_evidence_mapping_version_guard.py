"""PR 3B Activation Guard — evidence mapping version guard tests.

Proves:
  1. Missing evidence_mapping_version on worker_certified snapshot → stale.
  2. Current-version worker_certified snapshot → not stale.
  3. Run Intel: current analyst evidence + stale mapping → does NOT return terminal no-op.
     run_prewarm_snapshot is called; status=mapping_version_recertified.
  4. Run Intel: current analyst evidence + current mapping → no-op / analyst_evidence_current.
  5. Watchtower compare_and_republish: mapping mismatch → republish even when
     evidence_newer_than_certified_snapshot=False.
  6. Watchtower idempotency: current-version snapshot → certified_current (no repeat republish).
  7. republish_after_analyst_eligibility: mapping mismatch → republish trigger.
  8. republish_after_analyst_eligibility: current mapping + no new evidence → skipped.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.intelligence.v3.evidence_mapping_version_v1 import (
    EVIDENCE_MAPPING_VERSION,
    get_snapshot_mapping_version,
    is_snapshot_mapping_current,
)
from app.services.intelligence.v3.watchtower_intel_republisher_v1 import (
    PUBLISH_CERTIFICATION_BLOCKED,
    PUBLISH_CERTIFIED_CURRENT,
    PUBLISH_NO_SNAPSHOT_EXISTS,
    PUBLISH_REBUILT_AND_PUBLISHED,
    PUBLISH_REPUBLISH_PENDING,
    PUBLISH_SKIPPED_NO_NEW_EVIDENCE,
    compare_and_republish,
    republish_after_analyst_eligibility,
)

USER_ID = uuid.uuid4()
_NOW = datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc)
_STALE_TS = "2026-05-15T10:00:00+00:00"   # evidence older than snapshot
_FRESH_TS = "2026-05-16T12:05:00+00:00"   # evidence newer than snapshot
_SNAP_TS = "2026-05-16T12:00:00+00:00"    # snapshot generated_at


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_client(
    *,
    intel_generated_at: str = _SNAP_TS,
    intel_mapping_version: Optional[str] = EVIDENCE_MAPPING_VERSION,
    portfolio_snapshot_at: str = _STALE_TS,
    no_intel_snapshot: bool = False,
    no_portfolio_snapshot: bool = False,
) -> MagicMock:
    client = MagicMock()

    snap_payload: dict[str, Any] = {
        "snapshot_id": str(uuid.uuid4()),
        "generated_at": intel_generated_at,
        "snapshot_source": "worker_certified",
    }
    if intel_mapping_version is not None:
        snap_payload["evidence_mapping_version"] = intel_mapping_version

    intel_rows = [] if no_intel_snapshot else [{"payload": snap_payload}]
    portfolio_rows = [] if no_portfolio_snapshot else [
        {"id": str(uuid.uuid4()), "snapshot_at": portfolio_snapshot_at}
    ]

    def table(name: str):
        chain = MagicMock()
        chain.select.return_value = chain
        chain.eq.return_value = chain
        chain.order.return_value = chain
        chain.limit.return_value = chain
        if name == "intel_v3_snapshots":
            chain.execute.return_value = MagicMock(data=intel_rows)
        elif name == "portfolio_snapshots":
            chain.execute.return_value = MagicMock(data=portfolio_rows)
        return chain

    client.table = table
    return client


def _republish_callable(*, raises: bool = False) -> AsyncMock:
    if raises:
        return AsyncMock(side_effect=RuntimeError("cert_failed"))
    return AsyncMock(return_value={"snapshot_source": "worker_certified"})


# ── 1 & 2: is_snapshot_mapping_current helpers ────────────────────────────────

class TestMappingVersionHelpers:

    def test_missing_version_is_stale(self):
        """Snapshot payload without evidence_mapping_version is stale."""
        payload = {"snapshot_id": "abc", "generated_at": _SNAP_TS}
        assert is_snapshot_mapping_current(payload) is False

    def test_wrong_version_is_stale(self):
        """Snapshot payload with an old version string is stale."""
        payload = {"evidence_mapping_version": "legacy_v0"}
        assert is_snapshot_mapping_current(payload) is False

    def test_current_version_is_current(self):
        """Snapshot payload with the current EVIDENCE_MAPPING_VERSION is current."""
        payload = {"evidence_mapping_version": EVIDENCE_MAPPING_VERSION}
        assert is_snapshot_mapping_current(payload) is True

    def test_none_payload_is_stale(self):
        assert is_snapshot_mapping_current(None) is False

    def test_get_mapping_version_absent(self):
        assert get_snapshot_mapping_version({}) is None

    def test_get_mapping_version_present(self):
        assert get_snapshot_mapping_version(
            {"evidence_mapping_version": EVIDENCE_MAPPING_VERSION}
        ) == EVIDENCE_MAPPING_VERSION


# ── 5: compare_and_republish mapping mismatch → republish ─────────────────────

class TestCompareAndRepublishMappingGuard:

    @pytest.mark.asyncio
    async def test_mapping_version_stale_triggers_republish(self):
        """Stale mapping version triggers republish even when evidence is NOT newer."""
        # evidence (portfolio snapshot) is OLDER than the intel snapshot → no-op normally.
        # But mapping version is missing → must republish.
        client = _make_client(
            intel_generated_at=_SNAP_TS,
            intel_mapping_version=None,       # missing → stale
            portfolio_snapshot_at=_STALE_TS,  # portfolio older than intel → not newer
        )
        callable_ = _republish_callable()

        result = await compare_and_republish(
            USER_ID, client, intel_republish_callable=callable_, now=_NOW
        )

        # evidence_newer_than_certified_snapshot is False (portfolio is older)
        assert result.evidence_newer_than_certified_snapshot is False
        # but mapping mismatch forces republish
        assert result.publish_status == PUBLISH_REBUILT_AND_PUBLISHED
        callable_.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_wrong_mapping_version_triggers_republish(self):
        """Explicit old mapping version string also triggers republish."""
        client = _make_client(
            intel_generated_at=_SNAP_TS,
            intel_mapping_version="legacy_v0",
            portfolio_snapshot_at=_STALE_TS,
        )
        callable_ = _republish_callable()

        result = await compare_and_republish(
            USER_ID, client, intel_republish_callable=callable_, now=_NOW
        )

        assert result.publish_status == PUBLISH_REBUILT_AND_PUBLISHED
        callable_.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_current_mapping_version_and_not_newer_returns_certified_current(self):
        """Current mapping version + evidence not newer → idempotent no-op."""
        client = _make_client(
            intel_generated_at=_SNAP_TS,
            intel_mapping_version=EVIDENCE_MAPPING_VERSION,
            portfolio_snapshot_at=_STALE_TS,  # older → not newer
        )
        callable_ = _republish_callable()

        result = await compare_and_republish(
            USER_ID, client, intel_republish_callable=callable_, now=_NOW
        )

        assert result.publish_status == PUBLISH_CERTIFIED_CURRENT
        callable_.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_current_mapping_and_newer_evidence_still_republishes(self):
        """Current mapping + evidence NEWER → still republishes (existing behavior)."""
        client = _make_client(
            intel_generated_at=_SNAP_TS,
            intel_mapping_version=EVIDENCE_MAPPING_VERSION,
            portfolio_snapshot_at=_FRESH_TS,  # newer
        )
        callable_ = _republish_callable()

        result = await compare_and_republish(
            USER_ID, client, intel_republish_callable=callable_, now=_NOW
        )

        assert result.evidence_newer_than_certified_snapshot is True
        assert result.publish_status == PUBLISH_REBUILT_AND_PUBLISHED


# ── 7 & 8: republish_after_analyst_eligibility mapping guard ──────────────────

class TestRepublishAfterAnalystEligibilityMappingGuard:

    @pytest.mark.asyncio
    async def test_mapping_stale_triggers_republish_even_no_new_evidence(self):
        """Stale mapping version triggers republish in eligibility path too."""
        client = _make_client(
            intel_generated_at=_SNAP_TS,
            intel_mapping_version=None,  # stale
        )
        callable_ = _republish_callable()
        # latest_evidence_at < intel snapshot → no new evidence
        evidence_at = _NOW - timedelta(hours=2)

        result = await republish_after_analyst_eligibility(
            USER_ID,
            client,
            intel_republish_callable=callable_,
            latest_evidence_at=evidence_at,
            now=_NOW,
        )

        assert result.publish_status == PUBLISH_REBUILT_AND_PUBLISHED
        callable_.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_current_mapping_and_no_new_evidence_skips(self):
        """Current mapping + no new evidence → skipped (idempotent)."""
        client = _make_client(
            intel_generated_at=_SNAP_TS,
            intel_mapping_version=EVIDENCE_MAPPING_VERSION,
        )
        callable_ = _republish_callable()
        evidence_at = _NOW - timedelta(hours=2)

        result = await republish_after_analyst_eligibility(
            USER_ID,
            client,
            intel_republish_callable=callable_,
            latest_evidence_at=evidence_at,
            now=_NOW,
        )

        assert result.publish_status == PUBLISH_SKIPPED_NO_NEW_EVIDENCE
        callable_.assert_not_awaited()


# ── 3 & 4: enqueue_run_v3 mapping version guard via IntelV3Service ─────────────

class TestEnqueueRunV3MappingGuard:
    """Tests for Run Intel no-op override when mapping version is stale."""

    def _make_service(self):
        from app.services.intelligence.v3.intel_v3_service import IntelV3Service
        svc = IntelV3Service.__new__(IntelV3Service)
        svc.user_id = USER_ID
        svc.client = MagicMock()
        return svc

    @pytest.mark.asyncio
    async def test_stale_mapping_version_triggers_prewarm(self):
        """analyst_evidence_current + stale mapping → run_prewarm_snapshot called."""
        svc = self._make_service()

        # Snapshot exists but has no evidence_mapping_version (pre-PR #347).
        stale_snapshot = {
            "snapshot_id": str(uuid.uuid4()),
            "snapshot_source": "worker_certified",
            # no evidence_mapping_version key
        }
        prewarm_result = {
            "snapshot_source": "worker_certified",
            "evidence_mapping_version": EVIDENCE_MAPPING_VERSION,
        }

        prewarm_called = []

        async def fake_prewarm(*, prewarm_run_id: str) -> dict:
            prewarm_called.append(prewarm_run_id)
            return prewarm_result

        svc.get_latest_snapshot = AsyncMock(return_value=stale_snapshot)
        svc.run_prewarm_snapshot = fake_prewarm
        svc._get_active_tickers = AsyncMock(return_value=["AAPL", "MSFT"])

        # Fast freshness gate: analyst evidence current (no stale tickers).
        gate_result = MagicMock()
        gate_result.intel_status = "current"
        gate_result.deploy_status = "eligible"
        gate_result.deploy_blockers = []
        gate_result.refresh_plan = MagicMock(
            urgent_refresh_count=0, deploy_blockers=[]
        )
        gate_result.gate_check_ms = 5

        # run_fast_freshness_gate is lazily imported inside enqueue_run_v3 — patch at source.
        # _stale_analyst_tickers_from_gate is a module-level function — patch directly.
        with patch(
            "app.services.intelligence.v3.intel_v3_fast_freshness_gate_v1.run_fast_freshness_gate",
            new=AsyncMock(return_value=gate_result),
        ), patch(
            "app.services.intelligence.v3.intel_v3_service._stale_analyst_tickers_from_gate",
            return_value=[],  # all analyst evidence current
        ):
            result = await svc.enqueue_run_v3()

        assert result["status"] == "mapping_version_recertified"
        assert len(prewarm_called) == 1

    @pytest.mark.asyncio
    async def test_current_mapping_version_returns_noop(self):
        """analyst_evidence_current + current mapping → analyst_evidence_current (no prewarm)."""
        svc = self._make_service()

        current_snapshot = {
            "snapshot_id": str(uuid.uuid4()),
            "snapshot_source": "worker_certified",
            "evidence_mapping_version": EVIDENCE_MAPPING_VERSION,
        }

        prewarm_called = []

        async def fake_prewarm(*, prewarm_run_id: str) -> dict:
            prewarm_called.append(prewarm_run_id)
            return {}

        svc.get_latest_snapshot = AsyncMock(return_value=current_snapshot)
        svc.run_prewarm_snapshot = fake_prewarm
        svc._get_active_tickers = AsyncMock(return_value=["AAPL", "MSFT"])

        gate_result = MagicMock()
        gate_result.intel_status = "current"
        gate_result.deploy_status = "eligible"
        gate_result.deploy_blockers = []
        gate_result.refresh_plan = MagicMock(
            urgent_refresh_count=0, deploy_blockers=[]
        )
        gate_result.gate_check_ms = 5

        with patch(
            "app.services.intelligence.v3.intel_v3_fast_freshness_gate_v1.run_fast_freshness_gate",
            new=AsyncMock(return_value=gate_result),
        ), patch(
            "app.services.intelligence.v3.intel_v3_service._stale_analyst_tickers_from_gate",
            return_value=[],
        ):
            result = await svc.enqueue_run_v3()

        assert result["status"] == "analyst_evidence_current"
        assert prewarm_called == []


# ── build_snapshot includes evidence_mapping_version ─────────────────────────

class TestSnapshotBuilderIncludesVersion:

    def test_build_snapshot_includes_evidence_mapping_version(self):
        """build_snapshot() always includes evidence_mapping_version in payload."""
        from app.services.intelligence.v3.snapshot_builder import build_snapshot

        payload = build_snapshot(
            run_id="test-run-1",
            decisions=[],
            card_metas=[],
        )

        assert payload["evidence_mapping_version"] == EVIDENCE_MAPPING_VERSION

    def test_evidence_mapping_version_is_current(self):
        """The version stamped in new snapshots matches the module constant."""
        from app.services.intelligence.v3.snapshot_builder import build_snapshot

        payload = build_snapshot(
            run_id="test-run-2",
            decisions=[],
            card_metas=[],
        )

        assert is_snapshot_mapping_current(payload) is True
