"""Watchtower Alert Candidate Generation Hook v1 (Stage 3C).

After a certified Intel v3 snapshot is newly published or republished,
this hook evaluates the transition against the prior snapshot and persists
deterministic alert candidates via Stage 3B policy/service.

Fail-soft: all errors are logged and reported in the summary dict; the hook
never raises so Intel/Watchtower publication is never blocked.

Boundary: this module does NOT mutate Intel v3 snapshots, Deploy sizing,
Watchtower refresh behavior, or action_feedback_events rows.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from .alert_trigger_policy_v1 import evaluate_snapshot_for_alert_candidates
from .alert_candidate_service import AlertCandidateService

logger = logging.getLogger(__name__)

_FEEDBACK_FETCH_LIMIT = 200


async def run_alert_candidate_generation(
    user_id: UUID,
    client: Any,
    *,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Load snapshots + feedback, evaluate, and persist alert candidates.

    Returns a compact summary dict. Never raises.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    user_id_str = str(user_id)
    summary: dict[str, Any] = {
        "user_id": user_id_str,
        "evaluated": 0,
        "candidates": 0,
        "persisted": 0,
        "deduped": 0,
        "suppressions": 0,
        "skipped_reason": None,
        "policy_version": "v1",
        "error": None,
    }

    try:
        # Step 1: load current + prior snapshots (2 most recent, no is_active filter).
        # _persist_snapshot deactivates the old snapshot before inserting the new one,
        # so the two most-recent rows are always [current, prior].
        snapshots = await _fetch_latest_two_intel_snapshots(user_id, client)
        if not snapshots:
            summary["skipped_reason"] = "no_snapshot"
            _emit_log(summary)
            return summary

        current_payload = snapshots[0]
        prior_payload = snapshots[1] if len(snapshots) > 1 else None

        current_cards = current_payload.get("current_holdings") or []
        prior_cards = prior_payload.get("current_holdings") if prior_payload is not None else None
        snapshot_id = current_payload.get("snapshot_id")

        if not current_cards:
            summary["skipped_reason"] = "empty_current_holdings"
            _emit_log(summary)
            return summary

        if prior_payload is None:
            # Policy will produce 0 candidates — log reason for observability.
            summary["skipped_reason"] = "no_prior_snapshot"

        # Step 2: load recent feedback rows for suppression checks.
        feedback_rows = await _fetch_feedback_rows(user_id, client)

        # Step 3: evaluate (pure function — no IO).
        policy_result = evaluate_snapshot_for_alert_candidates(
            user_id=user_id_str,
            current_snapshot_cards=current_cards,
            prior_snapshot_cards=prior_cards,
            feedback_rows=feedback_rows,
            snapshot_id=snapshot_id,
            now=now,
        )

        summary["evaluated"] = policy_result.evaluated_ticker_count
        summary["candidates"] = len(policy_result.candidates)
        summary["suppressions"] = len(policy_result.suppressions)
        summary["policy_version"] = policy_result.policy_version

        # Step 4: persist candidates (idempotent by dedupe_key).
        if policy_result.candidates:
            svc = AlertCandidateService()
            persisted = 0
            deduped = 0
            for candidate in policy_result.candidates:
                try:
                    _, created = await asyncio.to_thread(svc.persist_candidate, candidate)
                    if created:
                        persisted += 1
                    else:
                        deduped += 1
                except Exception as persist_exc:
                    logger.warning(
                        "alert_candidate_hook.persist_error user_id=%s ticker=%s error=%s",
                        user_id_str,
                        candidate.ticker,
                        persist_exc,
                    )
            summary["persisted"] = persisted
            summary["deduped"] = deduped

    except Exception as exc:
        summary["error"] = str(exc)
        logger.warning(
            "alert_candidate_hook.error user_id=%s error=%s",
            user_id_str,
            exc,
        )

    _emit_log(summary)
    return summary


async def _fetch_latest_two_intel_snapshots(
    user_id: UUID,
    client: Any,
) -> list[dict]:
    """Fetch the two most recent Intel v3 snapshot payloads for this user.

    No is_active filter — ensures we get the prior snapshot even when it was
    just deactivated by the current republish.
    """
    try:
        row = await asyncio.to_thread(
            lambda: client.table("intel_v3_snapshots")
            .select("payload")
            .eq("user_id", str(user_id))
            .order("created_at", desc=True)
            .limit(2)
            .execute()
        )
        rows = row.data or []
        return [r.get("payload") or {} for r in rows]
    except Exception as exc:
        logger.warning(
            "alert_candidate_hook.fetch_snapshots_failed user_id=%s error=%s",
            user_id,
            exc,
        )
        return []


async def _fetch_feedback_rows(user_id: UUID, client: Any) -> list[dict]:
    """Fetch recent action_feedback_events rows for suppression checks."""
    try:
        row = await asyncio.to_thread(
            lambda: client.table("action_feedback_events")
            .select("*")
            .eq("user_id", str(user_id))
            .order("created_at", desc=True)
            .limit(_FEEDBACK_FETCH_LIMIT)
            .execute()
        )
        return row.data or []
    except Exception as exc:
        logger.warning(
            "alert_candidate_hook.fetch_feedback_failed user_id=%s error=%s",
            user_id,
            exc,
        )
        return []


def _emit_log(summary: dict[str, Any]) -> None:
    logger.info(
        "alert_candidate_generation_summary user_id=%s evaluated=%s "
        "candidates=%s persisted=%s deduped=%s suppressions=%s "
        "skipped_reason=%s policy_version=%s error=%s",
        summary["user_id"],
        summary["evaluated"],
        summary["candidates"],
        summary["persisted"],
        summary["deduped"],
        summary["suppressions"],
        summary.get("skipped_reason") or "none",
        summary["policy_version"],
        summary.get("error") or "none",
    )
