"""Stage 8A.3 — Post-evidence-lane deterministic snapshot republish.

Tests verify:
  1. compare_and_republish_after_evidence_lanes triggers republish when usable technical
     artifact is newer than the active certified snapshot.
  2. compare_and_republish_after_evidence_lanes skips republish when artifact is not newer.
  3. analyst_jobs_queued stays 0 — no LLM jobs enqueued.
  4. Idempotency: after a successful republish the new snapshot timestamp is newer than any
     artifact → next call returns skipped_no_new_evidence.
  5. No snapshot exists → returns publish_status=no_snapshot_exists (no crash).
  6. No usable technical artifacts (e.g. BTC/XRP have no is_usable=True rows) → skips.
  7. enqueue_run_v3 background task captures the post-lane republish callable before dispatch.
  8. Snapshot built after republish includes technical_signals_status=LIMITED when
     USABLE_WITH_LIMITATIONS technicals are present (synthetic Stage 6 off path).
  9. Sentiment SUPPRESSED_INCOMPLETE remains non-usable after republish.
 10. Republish called after lane completion, NOT only before lanes start.

No production Supabase dependency — all fakes defined locally.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Constants ─────────────────────────────────────────────────────────────────

UID = uuid.UUID("ccccdddd-0002-0000-0000-000000000008")
USER_ID_STR = str(UID)
NOW = datetime(2026, 5, 21, 5, 28, 10, tzinfo=timezone.utc)
SNAPSHOT_GEN_AT = datetime(2026, 5, 21, 3, 0, 0, tzinfo=timezone.utc)   # old snapshot
ARTIFACT_AT_NEWER = datetime(2026, 5, 21, 5, 28, 2, tzinfo=timezone.utc)  # fresh — newer
ARTIFACT_AT_OLDER = datetime(2026, 5, 21, 2, 0, 0, tzinfo=timezone.utc)   # older than snapshot

# Exact version constants from the codebase
_MAPPING_VERSION = "analyst_verdict_synthesis_v1"
_STAGE7_VERSION = "stage7_explanation_v2"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_current_snap_payload(generated_at: Optional[str] = None) -> dict:
    """Build a snapshot payload that passes all version checks (mapping + stage7)."""
    return {
        "snapshot_id": "test-snap-001",
        "generated_at": generated_at or SNAPSHOT_GEN_AT.isoformat(),
        "snapshot_source": "worker_certified",
        "evidence_mapping_version": _MAPPING_VERSION,
        "stage7_explanation_contract_version": _STAGE7_VERSION,
        "current_holdings": [],  # empty — stage7 complete with no cards
    }


def _make_artifact_row(ticker: str, generated_at: datetime, is_usable: bool = True) -> dict:
    # Stage 5A: production queries research_artifacts with eq(is_active=True) as the
    # usability proxy and no longer reads payload usability — a not-usable artifact
    # is represented as an inactive row (excluded by the query).
    return {
        "ticker": ticker,
        "generated_at": generated_at.isoformat(),
        "is_active": is_usable,
        "payload": {
            "truth_usability_assessment": {
                "usability_label": "USABLE_WITH_LIMITATIONS" if is_usable else "SUPPRESSED_INCOMPLETE",
                "is_usable": is_usable,
                "suppression_reason": None if is_usable else "insufficient_data",
            },
        },
    }


def _make_client_with_snap_and_artifacts(
    snap_payload: Optional[dict],
    artifact_rows: list[dict],
) -> MagicMock:
    client = MagicMock()

    def _table_side_effect(table_name: str):
        tbl = MagicMock()
        q = MagicMock()
        q.select.return_value = q
        q.eq.return_value = q
        q.order.return_value = q
        q.limit.return_value = q

        if table_name == "intel_v3_snapshots":
            if snap_payload:
                # Migration 024: the republisher reads flat metadata columns from
                # intel_v3_snapshots, not the payload JSONB. Contract booleans are
                # pre-computed at write time; derive them from the test payload.
                intel_row = {
                    "source_hash": "hash-test-snap",
                    "snapshot_source": snap_payload.get("snapshot_source"),
                    "payload_generated_at": snap_payload.get("generated_at"),
                    "evidence_mapping_version": snap_payload.get("evidence_mapping_version"),
                    "stage7_contract_complete": (
                        snap_payload.get("stage7_explanation_contract_version") == _STAGE7_VERSION
                    ),
                    "stage8e_contract_complete": True,  # Stage 8E not under test here
                }
                q.execute.return_value = MagicMock(data=[intel_row])
            else:
                q.execute.return_value = MagicMock(data=[])
        elif table_name == "research_artifacts":
            # Emulate the DB-side eq(is_active=True) usability filter (Stage 5A).
            q.execute.return_value = MagicMock(
                data=[r for r in artifact_rows if r.get("is_active", True)]
            )
        else:
            q.execute.return_value = MagicMock(data=[])

        tbl.select.return_value = q
        return tbl

    client.table.side_effect = _table_side_effect
    return client


# ── Tests: compare_and_republish_after_evidence_lanes ─────────────────────────

class TestPostLaneRepublishTriggered:
    """Republish fires when usable technical artifact is newer than certified snapshot."""

    @pytest.mark.asyncio
    async def test_rebuild_when_artifact_newer(self):
        from app.services.intelligence.v3.watchtower_intel_republisher_v1 import (
            compare_and_republish_after_evidence_lanes,
            PUBLISH_REBUILT_AND_PUBLISHED,
        )
        client = _make_client_with_snap_and_artifacts(
            snap_payload=_make_current_snap_payload(),
            artifact_rows=[_make_artifact_row("MSFT", ARTIFACT_AT_NEWER)],
        )

        republish_callable = AsyncMock(
            return_value={"snapshot_source": "worker_certified", "snapshot_id": "new-snap"}
        )

        result = await compare_and_republish_after_evidence_lanes(
            USER_ID_STR,
            client,
            intel_republish_callable=republish_callable,
        )

        assert result.publish_status == PUBLISH_REBUILT_AND_PUBLISHED
        republish_callable.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_analyst_jobs_queued_stays_zero(self):
        from app.services.intelligence.v3.watchtower_intel_republisher_v1 import (
            compare_and_republish_after_evidence_lanes,
        )
        client = _make_client_with_snap_and_artifacts(
            snap_payload=_make_current_snap_payload(),
            artifact_rows=[_make_artifact_row("MSFT", ARTIFACT_AT_NEWER)],
        )
        republish_callable = AsyncMock(return_value={"snapshot_source": "worker_certified"})

        result = await compare_and_republish_after_evidence_lanes(
            USER_ID_STR, client, intel_republish_callable=republish_callable
        )

        assert result.analyst_jobs_queued == 0, (
            "compare_and_republish_after_evidence_lanes must never enqueue analyst LLM jobs"
        )

    @pytest.mark.asyncio
    async def test_evidence_newer_flag_set(self):
        from app.services.intelligence.v3.watchtower_intel_republisher_v1 import (
            compare_and_republish_after_evidence_lanes,
        )
        client = _make_client_with_snap_and_artifacts(
            snap_payload=_make_current_snap_payload(),
            artifact_rows=[_make_artifact_row("AAPL", ARTIFACT_AT_NEWER)],
        )
        republish_callable = AsyncMock(return_value={"snapshot_source": "worker_certified"})

        result = await compare_and_republish_after_evidence_lanes(
            USER_ID_STR, client, intel_republish_callable=republish_callable
        )

        assert result.evidence_newer_than_certified_snapshot is True


class TestPostLaneRepublishSkipped:
    """Republish skips when artifact is not newer than the certified snapshot."""

    @pytest.mark.asyncio
    async def test_skip_when_artifact_older(self):
        from app.services.intelligence.v3.watchtower_intel_republisher_v1 import (
            compare_and_republish_after_evidence_lanes,
            PUBLISH_SKIPPED_NO_NEW_EVIDENCE,
        )
        client = _make_client_with_snap_and_artifacts(
            snap_payload=_make_current_snap_payload(),
            artifact_rows=[_make_artifact_row("MSFT", ARTIFACT_AT_OLDER)],
        )
        republish_callable = AsyncMock(return_value={"snapshot_source": "worker_certified"})

        result = await compare_and_republish_after_evidence_lanes(
            USER_ID_STR, client, intel_republish_callable=republish_callable
        )

        assert result.publish_status == PUBLISH_SKIPPED_NO_NEW_EVIDENCE
        republish_callable.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skip_when_no_usable_artifacts(self):
        """No usable artifacts (e.g. all suppressed/non-equity) → skips republish."""
        from app.services.intelligence.v3.watchtower_intel_republisher_v1 import (
            compare_and_republish_after_evidence_lanes,
            PUBLISH_SKIPPED_NO_NEW_EVIDENCE,
        )
        client = _make_client_with_snap_and_artifacts(
            snap_payload=_make_current_snap_payload(),
            artifact_rows=[_make_artifact_row("BTC", ARTIFACT_AT_NEWER, is_usable=False)],
        )
        republish_callable = AsyncMock(return_value={"snapshot_source": "worker_certified"})

        result = await compare_and_republish_after_evidence_lanes(
            USER_ID_STR, client, intel_republish_callable=republish_callable
        )

        assert result.publish_status == PUBLISH_SKIPPED_NO_NEW_EVIDENCE
        republish_callable.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_snapshot_returns_no_snapshot_exists(self):
        from app.services.intelligence.v3.watchtower_intel_republisher_v1 import (
            compare_and_republish_after_evidence_lanes,
            PUBLISH_NO_SNAPSHOT_EXISTS,
        )
        client = _make_client_with_snap_and_artifacts(
            snap_payload=None,
            artifact_rows=[_make_artifact_row("MSFT", ARTIFACT_AT_NEWER)],
        )
        republish_callable = AsyncMock()

        result = await compare_and_republish_after_evidence_lanes(
            USER_ID_STR, client, intel_republish_callable=republish_callable
        )

        assert result.publish_status == PUBLISH_NO_SNAPSHOT_EXISTS
        republish_callable.assert_not_awaited()


class TestPostLaneRepublishIdempotency:
    """After a successful republish, the new snapshot is current → next call skips."""

    @pytest.mark.asyncio
    async def test_idempotent_after_republish(self):
        """Simulate post-republish state: snapshot.generated_at is AFTER artifact timestamp."""
        from app.services.intelligence.v3.watchtower_intel_republisher_v1 import (
            compare_and_republish_after_evidence_lanes,
            PUBLISH_SKIPPED_NO_NEW_EVIDENCE,
        )
        # After republish the snapshot was rebuilt at NOW, newer than any artifact
        post_republish_snap = _make_current_snap_payload(generated_at=NOW.isoformat())

        client = _make_client_with_snap_and_artifacts(
            snap_payload=post_republish_snap,
            artifact_rows=[_make_artifact_row("MSFT", ARTIFACT_AT_NEWER)],
        )
        republish_callable = AsyncMock(return_value={"snapshot_source": "worker_certified"})

        result = await compare_and_republish_after_evidence_lanes(
            USER_ID_STR, client, intel_republish_callable=republish_callable
        )

        # Artifact (05:28:02) is older than post-republish snapshot (05:28:10) → skip
        assert result.publish_status == PUBLISH_SKIPPED_NO_NEW_EVIDENCE
        republish_callable.assert_not_awaited()


class TestPostLaneRepublishMultipleTickers:
    """Multiple tickers: republish if ANY usable artifact is newer."""

    @pytest.mark.asyncio
    async def test_republish_if_any_newer(self):
        from app.services.intelligence.v3.watchtower_intel_republisher_v1 import (
            compare_and_republish_after_evidence_lanes,
            PUBLISH_REBUILT_AND_PUBLISHED,
        )
        client = _make_client_with_snap_and_artifacts(
            snap_payload=_make_current_snap_payload(),
            artifact_rows=[
                _make_artifact_row("AAPL", ARTIFACT_AT_OLDER),   # older — would skip alone
                _make_artifact_row("MSFT", ARTIFACT_AT_NEWER),   # newer — triggers republish
            ],
        )
        republish_callable = AsyncMock(return_value={"snapshot_source": "worker_certified"})

        result = await compare_and_republish_after_evidence_lanes(
            USER_ID_STR, client, intel_republish_callable=republish_callable
        )

        assert result.publish_status == PUBLISH_REBUILT_AND_PUBLISHED
        republish_callable.assert_awaited_once()


# ── Tests: enqueue_run_v3 dispatches post-lane republish ─────────────────────
# Source-read tests: read the module file directly to avoid runtime import issues.

def _read_service_source() -> str:
    import importlib, sys
    module_path = None
    # Find the module file without importing the full service (which requires pydantic_settings)
    import os
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(
        base, "app", "services", "intelligence", "v3", "intel_v3_service.py"
    )
    with open(path) as f:
        return f.read()


class TestEnqueueRunV3PostLaneDispatch:
    """enqueue_run_v3 source must reference compare_and_republish_after_evidence_lanes."""

    def test_service_source_has_post_lane_republish_call(self):
        src = _read_service_source()
        assert "compare_and_republish_after_evidence_lanes" in src, (
            "enqueue_run_v3 must call compare_and_republish_after_evidence_lanes "
            "after evidence lanes complete"
        )

    def test_post_lane_republish_inside_lane_task(self):
        """The republish call must be inside _run_evidence_lanes_safe, not before it."""
        src = _read_service_source()
        lane_fn_start = src.find("async def _run_evidence_lanes_safe")
        assert lane_fn_start > 0, "_run_evidence_lanes_safe not found"
        republish_pos = src.find("compare_and_republish_after_evidence_lanes", lane_fn_start)
        assert republish_pos > lane_fn_start, (
            "compare_and_republish_after_evidence_lanes must be inside "
            "_run_evidence_lanes_safe (post-lane), not before it"
        )

    def test_post_lane_republish_after_lane_runner(self):
        """The republish call must come after run_enabled_evidence_lanes_for_portfolio."""
        src = _read_service_source()
        lane_fn_start = src.find("async def _run_evidence_lanes_safe")
        lanes_pos = src.find("run_enabled_evidence_lanes_for_portfolio", lane_fn_start)
        republish_pos = src.find("compare_and_republish_after_evidence_lanes", lane_fn_start)
        assert republish_pos > lanes_pos, (
            "compare_and_republish_after_evidence_lanes must be called AFTER "
            "run_enabled_evidence_lanes_for_portfolio inside _run_evidence_lanes_safe"
        )


# ── Tests: no analyst jobs via source inspection ──────────────────────────────

class TestPostLaneRepublishNoLLMJobs:
    """compare_and_republish_after_evidence_lanes must never enqueue analyst jobs."""

    def test_source_no_analyst_enqueue(self):
        import os
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(
            base, "app", "services", "intelligence", "v3",
            "watchtower_intel_republisher_v1.py"
        )
        with open(path) as f:
            full_src = f.read()

        # Locate function body
        fn_start = full_src.find("async def compare_and_republish_after_evidence_lanes")
        assert fn_start > 0
        # Read to next async def at same indent level
        fn_body = full_src[fn_start:fn_start + 6000]

        forbidden = [
            "enqueue_refresh_jobs",
            "analyst_refresh_jobs",
            "analyst_enqueue",
            "decide(",
        ]
        for f in forbidden:
            assert f not in fn_body, (
                f"compare_and_republish_after_evidence_lanes must not reference '{f}'"
            )


# ── Tests: snapshot explanation contract after republish ─────────────────────

class TestSnapshotExplanationAfterRepublish:
    """Snapshot built after post-lane republish includes correct technical_signals_status."""

    def test_snapshot_builder_limited_when_usable_with_limitations(self):
        """When Stage 5J reports STATUS_LIMITED, snapshot_builder sets technical_signals_status=LIMITED."""
        from app.services.intelligence.v3.snapshot_builder import _build_held_card
        from app.services.intelligence.v3.decision_contracts import (
            DecisionOutputV3,
            ActionV3,
            ConvictionV3,
            FitBand,
            RiskBand,
            AxisBand,
            PriceBand,
        )

        decision = DecisionOutputV3(
            ticker="MSFT",
            action=ActionV3.HOLD,
            conviction=ConvictionV3.MEDIUM,
            evidence_quality=AxisBand.OK,
            attractiveness=AxisBand.OK,
            price_context=PriceBand.FAIR,
            portfolio_fit=FitBand.ON_TARGET,
            risk_band=RiskBand.MEDIUM,
            rationale_plain_english="Holding current position.",
            why_now="",
            why_not_now="",
            blockers=[],
            suppression_reasons={},
            source_signal_summary={},
        )

        # card_meta with research_axis_readiness → technical_signals=LIMITED
        card_meta = {
            "ticker": "MSFT",
            "name": "Microsoft",
            "category": "stock",
            "governance_result": None,  # Stage 6 inactive → synthetic path
            "research_axis_readiness": {
                "technical_signals": "LIMITED",
                "sentiment": "SUPPRESSED",
            },
        }

        card = _build_held_card(
            decision=decision,
            card_meta=card_meta,
            snapshot_id="test-snap",
            run_id="test-run",
        )

        ex = card["detail_drawer_payload"]["evidence_explanation"]
        assert ex is not None, "evidence_explanation must be non-None"
        assert ex["technical_signals_status"] == "LIMITED", (
            f"Expected technical_signals_status=LIMITED, got {ex['technical_signals_status']!r}"
        )

    def test_snapshot_builder_sentiment_suppressed_not_usable(self):
        """Sentiment SUPPRESSED remains non-usable in evidence_explanation."""
        from app.services.intelligence.v3.snapshot_builder import _build_held_card
        from app.services.intelligence.v3.decision_contracts import (
            DecisionOutputV3,
            ActionV3,
            ConvictionV3,
            FitBand,
            RiskBand,
            AxisBand,
            PriceBand,
        )

        decision = DecisionOutputV3(
            ticker="MSFT",
            action=ActionV3.HOLD,
            conviction=ConvictionV3.MEDIUM,
            evidence_quality=AxisBand.OK,
            attractiveness=AxisBand.OK,
            price_context=PriceBand.FAIR,
            portfolio_fit=FitBand.ON_TARGET,
            risk_band=RiskBand.MEDIUM,
            rationale_plain_english="Holding.",
            why_now="",
            why_not_now="",
            blockers=[],
            suppression_reasons={},
            source_signal_summary={},
        )
        card_meta = {
            "ticker": "MSFT",
            "name": "Microsoft",
            "category": "stock",
            "governance_result": None,
            "research_axis_readiness": {
                "technical_signals": "LIMITED",
                "sentiment": "SUPPRESSED",
            },
        }

        card = _build_held_card(
            decision=decision,
            card_meta=card_meta,
            snapshot_id="test-snap",
            run_id="test-run",
        )

        ex = card["detail_drawer_payload"]["evidence_explanation"]
        assert ex["sentiment_status"] == "SUPPRESSED", (
            f"Expected sentiment_status=SUPPRESSED, got {ex['sentiment_status']!r}"
        )
