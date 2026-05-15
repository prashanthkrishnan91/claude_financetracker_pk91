"""Watchtower Intel v3 republisher v1 (Build 2).

After a successful Watchtower price refresh persists a new portfolio_snapshots row,
this module determines whether the current visible Intel v3 snapshot predates the
fresh evidence. If so, it triggers a deterministic snapshot rebuild via a callable.

Design:
  - Reads latest intel_v3_snapshots.payload.generated_at (current visible snapshot).
  - Reads latest portfolio_snapshots.snapshot_at (freshest Watchtower evidence).
  - If evidence is newer: calls intel_republish_callable(user_id) — deterministic
    rebuild without analyst LLM jobs (wraps IntelV3Service.run_prewarm_snapshot).
  - Logs structured fields for observability.

Boundary: this module does NOT import decide() or IntelV3Service. Those are
injected as callables by watchtower_callables_v1.py to preserve the Watchtower
worker boundary (evidence freshness ≠ final action authority).
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional
from uuid import UUID

logger = logging.getLogger(__name__)

# Minimum age difference (seconds) for evidence to be considered "newer" than
# the certified snapshot. Avoids spurious republish when both timestamps differ
# by only a few seconds due to clock imprecision.
_EVIDENCE_NEWER_THRESHOLD_SECONDS = 10

# publish_status values — backend contract constants
PUBLISH_CERTIFIED_CURRENT = "certified_current"
PUBLISH_REBUILT_AND_PUBLISHED = "rebuilt_and_published"
PUBLISH_REPUBLISH_PENDING = "republish_pending"
PUBLISH_CERTIFICATION_BLOCKED = "certification_blocked"
PUBLISH_NO_SNAPSHOT_EXISTS = "no_snapshot_exists"


@dataclass
class WatchtowerRepublishResult:
    """Result of one Watchtower evidence → Intel republish comparison."""

    publish_status: str
    latest_certified_snapshot_id: Optional[str] = None
    latest_certified_snapshot_generated_at: Optional[str] = None
    latest_decision_evidence_snapshot_id: Optional[str] = None
    latest_decision_evidence_snapshot_at: Optional[str] = None
    evidence_newer_than_certified_snapshot: bool = False
    analyst_jobs_queued: int = 0
    watchtower_refresh_triggered: bool = True
    error: Optional[str] = None
    duration_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "publish_status": self.publish_status,
            "latest_certified_snapshot_id": self.latest_certified_snapshot_id,
            "latest_certified_snapshot_generated_at": self.latest_certified_snapshot_generated_at,
            "latest_decision_evidence_snapshot_id": self.latest_decision_evidence_snapshot_id,
            "latest_decision_evidence_snapshot_at": self.latest_decision_evidence_snapshot_at,
            "evidence_newer_than_certified_snapshot": self.evidence_newer_than_certified_snapshot,
            "analyst_jobs_queued": self.analyst_jobs_queued,
            "watchtower_refresh_triggered": self.watchtower_refresh_triggered,
            "error": self.error,
            "duration_ms": self.duration_ms,
        }


async def compare_and_republish(
    user_id: UUID,
    client: Any,
    *,
    intel_republish_callable: Optional[Callable] = None,
    now: Optional[datetime] = None,
) -> WatchtowerRepublishResult:
    """Compare Watchtower evidence freshness against the current Intel snapshot.

    Steps:
      1. Read latest intel_v3_snapshots row (current visible snapshot).
      2. Read latest portfolio_snapshots row (freshest Watchtower evidence).
      3. Compare timestamps.
      4. If evidence is newer and callable provided: trigger deterministic rebuild.
      5. Emit structured log.

    Does NOT import decide() or IntelV3Service — those are injected via callable.
    Analyst jobs remain 0 — price-only freshness never triggers LLM analysis.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    t0 = time.monotonic()
    result = WatchtowerRepublishResult(
        publish_status=PUBLISH_REPUBLISH_PENDING,
        watchtower_refresh_triggered=True,
        analyst_jobs_queued=0,
    )

    try:
        # Step 1: read current Intel v3 snapshot
        snap_row = await _fetch_latest_intel_snapshot(user_id, client)
        if snap_row is None:
            result.publish_status = PUBLISH_NO_SNAPSHOT_EXISTS
            result.duration_ms = int((time.monotonic() - t0) * 1000)
            _emit_log(user_id, result)
            return result

        result.latest_certified_snapshot_id = snap_row.get("snapshot_id")
        result.latest_certified_snapshot_generated_at = snap_row.get("generated_at")

        # Step 2: read latest portfolio_snapshots (Watchtower evidence)
        evidence_row = await _fetch_latest_portfolio_snapshot(user_id, client)
        if evidence_row:
            result.latest_decision_evidence_snapshot_id = str(evidence_row.get("id") or "")
            result.latest_decision_evidence_snapshot_at = _to_iso(evidence_row.get("snapshot_at"))

        # Step 3: compare timestamps
        intel_ts = _parse_iso(result.latest_certified_snapshot_generated_at)
        evidence_ts = _parse_iso(result.latest_decision_evidence_snapshot_at)

        if intel_ts is not None and evidence_ts is not None:
            diff_seconds = (evidence_ts - intel_ts).total_seconds()
            result.evidence_newer_than_certified_snapshot = (
                diff_seconds > _EVIDENCE_NEWER_THRESHOLD_SECONDS
            )
        else:
            # If evidence_ts exists but intel_ts is unparseable, assume evidence is newer.
            # If neither parses, default to False (no republish on ambiguous state).
            result.evidence_newer_than_certified_snapshot = (
                evidence_ts is not None and intel_ts is None
            )

        # Step 4: if evidence is not newer, snapshot already covers latest evidence
        if not result.evidence_newer_than_certified_snapshot:
            result.publish_status = PUBLISH_CERTIFIED_CURRENT
            result.duration_ms = int((time.monotonic() - t0) * 1000)
            _emit_log(user_id, result)
            return result

        # Evidence is newer — trigger deterministic rebuild if callable is provided.
        # analyst_jobs_queued stays 0: price-only refresh never enqueues analyst LLM jobs.
        if intel_republish_callable is None:
            result.publish_status = PUBLISH_REPUBLISH_PENDING
            result.duration_ms = int((time.monotonic() - t0) * 1000)
            _emit_log(user_id, result)
            return result

        try:
            await intel_republish_callable(user_id)
            result.publish_status = PUBLISH_REBUILT_AND_PUBLISHED
        except Exception as exc:
            result.publish_status = PUBLISH_CERTIFICATION_BLOCKED
            result.error = str(exc)
            logger.warning(
                "watchtower_intel_republisher.republish_callable_failed user_id=%s error=%s",
                user_id, exc,
            )

    except Exception as outer_exc:
        result.publish_status = PUBLISH_CERTIFICATION_BLOCKED
        result.error = str(outer_exc)
        logger.warning(
            "watchtower_intel_republisher.compare_failed user_id=%s error=%s",
            user_id, outer_exc,
        )

    result.duration_ms = int((time.monotonic() - t0) * 1000)
    _emit_log(user_id, result)
    return result


async def get_evidence_freshness_state(
    user_id: UUID,
    client: Any,
    *,
    intel_snapshot_generated_at: Optional[str],
) -> str:
    """Compute evidence_freshness_state for the GET /intel/v3/snapshot response.

    Returns one of the PUBLISH_* constants. Used by get_latest_snapshot() to
    embed honest freshness state in the API response without triggering republish.
    """
    try:
        evidence_row = await _fetch_latest_portfolio_snapshot(user_id, client)
        if evidence_row is None:
            return PUBLISH_CERTIFIED_CURRENT

        evidence_ts = _parse_iso(_to_iso(evidence_row.get("snapshot_at")))
        intel_ts = _parse_iso(intel_snapshot_generated_at)

        if intel_ts is not None and evidence_ts is not None:
            diff_seconds = (evidence_ts - intel_ts).total_seconds()
            if diff_seconds > _EVIDENCE_NEWER_THRESHOLD_SECONDS:
                return PUBLISH_REPUBLISH_PENDING
        elif evidence_ts is not None and intel_ts is None:
            return PUBLISH_REPUBLISH_PENDING

        return PUBLISH_CERTIFIED_CURRENT
    except Exception as exc:
        logger.warning(
            "watchtower_intel_republisher.get_evidence_freshness_state_failed "
            "user_id=%s error=%s",
            user_id, exc,
        )
        return PUBLISH_CERTIFIED_CURRENT


# ── Private helpers ───────────────────────────────────────────────────────────

async def _fetch_latest_intel_snapshot(user_id: UUID, client: Any) -> Optional[dict]:
    """Read the latest active Intel v3 snapshot (indexed fast read)."""
    try:
        row = await asyncio.to_thread(
            lambda: client.table("intel_v3_snapshots")
            .select("payload")
            .eq("user_id", str(user_id))
            .eq("is_active", True)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = row.data or []
        if not rows:
            return None
        payload = rows[0].get("payload") or {}
        return {
            "snapshot_id": payload.get("snapshot_id"),
            "generated_at": payload.get("generated_at"),
            "snapshot_source": payload.get("snapshot_source"),
        }
    except Exception as exc:
        logger.warning(
            "watchtower_intel_republisher.fetch_intel_snapshot_failed user_id=%s err=%s",
            user_id, exc,
        )
        return None


async def _fetch_latest_portfolio_snapshot(user_id: UUID, client: Any) -> Optional[dict]:
    """Read the latest portfolio_snapshots row for Watchtower evidence timestamp."""
    try:
        row = await asyncio.to_thread(
            lambda: client.table("portfolio_snapshots")
            .select("id,snapshot_at")
            .eq("user_id", str(user_id))
            .order("snapshot_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = row.data or []
        return rows[0] if rows else None
    except Exception as exc:
        logger.warning(
            "watchtower_intel_republisher.fetch_portfolio_snapshot_failed user_id=%s err=%s",
            user_id, exc,
        )
        return None


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return None


def _to_iso(val: Any) -> Optional[str]:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.isoformat()
    return str(val)


def _emit_log(user_id: UUID, result: WatchtowerRepublishResult) -> None:
    logger.info(
        "watchtower_intel_republisher.publish_decision user_id=%s "
        "publish_status=%s "
        "latest_certified_snapshot_id=%s "
        "latest_certified_snapshot_generated_at=%s "
        "latest_decision_evidence_snapshot_id=%s "
        "latest_decision_evidence_snapshot_at=%s "
        "evidence_newer_than_certified_snapshot=%s "
        "analyst_jobs_queued=%d "
        "watchtower_refresh_triggered=%s "
        "duration_ms=%d "
        "error=%s",
        user_id,
        result.publish_status,
        result.latest_certified_snapshot_id,
        result.latest_certified_snapshot_generated_at,
        result.latest_decision_evidence_snapshot_id,
        result.latest_decision_evidence_snapshot_at,
        result.evidence_newer_than_certified_snapshot,
        result.analyst_jobs_queued,
        result.watchtower_refresh_triggered,
        result.duration_ms,
        result.error or "none",
    )
