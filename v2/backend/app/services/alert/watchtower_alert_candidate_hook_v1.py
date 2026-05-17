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
        newly_persisted_rows: list[dict] = []
        if policy_result.candidates:
            svc = AlertCandidateService()
            persisted = 0
            deduped = 0
            for candidate in policy_result.candidates:
                try:
                    row, created = await asyncio.to_thread(svc.persist_candidate, candidate)
                    if created:
                        persisted += 1
                        newly_persisted_rows.append(row)
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

        # Step 5: create delivery outbox entries for newly persisted candidates.
        # Fail-soft: outbox errors never block candidate generation or Watchtower.
        if newly_persisted_rows:
            outbox_summary = await _create_delivery_outbox_entries(
                user_id_str, newly_persisted_rows
            )
            summary["outbox_created"] = outbox_summary.get("created", 0)
            summary["outbox_suppressed"] = outbox_summary.get("suppressed", 0)
            summary["outbox_deduped"] = outbox_summary.get("deduped", 0)

    except Exception as exc:
        summary["error"] = str(exc)
        logger.warning(
            "alert_candidate_hook.error user_id=%s error=%s",
            user_id_str,
            exc,
        )

    _emit_log(summary)
    return summary


async def _create_delivery_outbox_entries(
    user_id_str: str,
    candidate_rows: list[dict],
) -> dict:
    """Create outbox entries for newly persisted candidates. Never raises."""
    from .alert_delivery_outbox_service import AlertDeliveryOutboxService

    counts: dict[str, int] = {"created": 0, "suppressed": 0, "deduped": 0, "errors": 0}
    try:
        outbox_svc = AlertDeliveryOutboxService()
        for row in candidate_rows:
            try:
                _, outcome = await asyncio.to_thread(
                    outbox_svc.create_pending_from_candidate, row
                )
                if outcome in counts:
                    counts[outcome] += 1
                else:
                    counts["errors"] += 1
            except Exception as row_exc:
                logger.warning(
                    "alert_candidate_hook.outbox_row_error user_id=%s ticker=%s error=%s",
                    user_id_str,
                    row.get("ticker", "?"),
                    row_exc,
                )
                counts["errors"] += 1
    except Exception as exc:
        logger.warning(
            "alert_candidate_hook.outbox_creation_error user_id=%s error=%s",
            user_id_str,
            exc,
        )
        counts["errors"] += len(candidate_rows)
    logger.info(
        "alert_delivery_outbox_creation_summary user_id=%s "
        "created=%d suppressed=%d deduped=%d errors=%d",
        user_id_str,
        counts["created"],
        counts["suppressed"],
        counts["deduped"],
        counts["errors"],
    )
    return counts


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
        "outbox_created=%s outbox_suppressed=%s outbox_deduped=%s "
        "skipped_reason=%s policy_version=%s error=%s",
        summary["user_id"],
        summary["evaluated"],
        summary["candidates"],
        summary["persisted"],
        summary["deduped"],
        summary["suppressions"],
        summary.get("outbox_created", 0),
        summary.get("outbox_suppressed", 0),
        summary.get("outbox_deduped", 0),
        summary.get("skipped_reason") or "none",
        summary["policy_version"],
        summary.get("error") or "none",
    )
