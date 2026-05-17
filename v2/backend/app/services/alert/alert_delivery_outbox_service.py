"""Alert Delivery Outbox Service — provider-neutral outbox persistence.

Creates and manages pending delivery rows from alert candidates.
Never sends emails, push notifications, or any external requests.
No Intel v3, Deploy, Watchtower, or candidate mutations.

Idempotency model:
  - dedupe_key = sha256(user_id:alert_candidate_id:channel:policy_version)
  - Re-processing the same candidate+channel is idempotent (unique constraint).
  - Noisy-repeat suppression: if a pending/sent outbox row exists for the same
    user+ticker+channel within the suppression window (24h), new rows are
    skipped with outcome='suppressed'.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from ...database import get_supabase_client
from .alert_delivery_policy_v1 import DeliverySpec, build_delivery_spec

logger = logging.getLogger(__name__)

_TABLE = "alert_delivery_outbox"
_SUPPRESS_WINDOW_HOURS = 24


class AlertDeliveryOutboxService:
    def __init__(self) -> None:
        self.client = get_supabase_client()

    # ── Core persistence ──────────────────────────────────────────────────────

    def persist_outbox_entry(self, spec: DeliverySpec) -> tuple[dict[str, Any], bool]:
        """Persist one outbox entry idempotently. Returns (row, created).

        created=False means the row already existed (dedupe hit).
        Never raises on unique-constraint conflicts.
        """
        payload: dict[str, Any] = {
            "user_id": spec.user_id,
            "alert_candidate_id": spec.alert_candidate_id,
            "ticker": spec.ticker,
            "channel": spec.channel,
            "delivery_mode": spec.delivery_mode,
            "severity": spec.severity,
            "subject": spec.subject,
            "plain_english_body": spec.plain_english_body,
            "status": "pending",
            "dedupe_key": spec.dedupe_key,
            "policy_version": spec.policy_version,
        }
        if spec.scheduled_for:
            payload["scheduled_for"] = spec.scheduled_for

        is_unique_conflict = False
        try:
            result = self.client.table(_TABLE).insert(payload).execute()
            if result.data:
                logger.info(
                    "alert_delivery_outbox.created user_id=%s ticker=%s "
                    "channel=%s delivery_mode=%s severity=%s",
                    spec.user_id,
                    spec.ticker,
                    spec.channel,
                    spec.delivery_mode,
                    spec.severity,
                )
                return result.data[0], True
            is_unique_conflict = False
        except Exception as exc:
            exc_str = str(exc).lower()
            is_unique_violation = any(
                marker in exc_str for marker in ("unique", "duplicate", "23505")
            )
            if not is_unique_violation:
                raise
            is_unique_conflict = True

        existing = self._fetch_by_dedupe_key(spec.user_id, spec.dedupe_key)
        if existing:
            logger.info(
                "alert_delivery_outbox.%s user_id=%s dedupe_key=%s",
                "dedup_hit" if is_unique_conflict else "insert_no_data_recovered",
                spec.user_id,
                spec.dedupe_key,
            )
            return existing, False

        if is_unique_conflict:
            raise RuntimeError(
                f"alert_delivery_outbox_dedup_lookup_failed "
                f"dedupe_key={spec.dedupe_key!r}"
            )
        raise RuntimeError(
            f"alert_delivery_outbox_create_no_row_returned "
            f"dedupe_key={spec.dedupe_key!r}"
        )

    def _fetch_by_dedupe_key(
        self, user_id: str, dedupe_key: str
    ) -> dict[str, Any] | None:
        result = (
            self.client.table(_TABLE)
            .select("*")
            .eq("user_id", user_id)
            .eq("dedupe_key", dedupe_key)
            .limit(1)
            .execute()
        )
        rows = result.data or []
        return rows[0] if rows else None

    # ── Noisy-repeat suppression ──────────────────────────────────────────────

    def has_recent_outbox(
        self,
        user_id: str,
        ticker: str,
        channel: str,
        *,
        window_hours: int = _SUPPRESS_WINDOW_HOURS,
        now: Optional[datetime] = None,
    ) -> bool:
        """Return True if a pending/sent outbox row exists for user+ticker+channel
        within the suppression window. Prevents repeat alerts for the same signal.
        """
        if now is None:
            now = datetime.now(timezone.utc)
        since = (now - timedelta(hours=window_hours)).isoformat()
        try:
            result = (
                self.client.table(_TABLE)
                .select("id")
                .eq("user_id", user_id)
                .eq("ticker", ticker)
                .eq("channel", channel)
                .in_("status", ["pending", "sent"])
                .gte("created_at", since)
                .limit(1)
                .execute()
            )
            return bool(result.data)
        except Exception as exc:
            logger.warning(
                "alert_delivery_outbox.has_recent_check_failed "
                "user_id=%s ticker=%s error=%s",
                user_id,
                ticker,
                exc,
            )
            # On error, return False (allow the entry) — fail-open is safer
            # here to avoid silently suppressing legitimate alerts.
            return False

    # ── High-level entry point ────────────────────────────────────────────────

    def create_pending_from_candidate(
        self,
        candidate_row: dict[str, Any],
        channel: str = "email",
        *,
        now: Optional[datetime] = None,
    ) -> tuple[dict[str, Any], str]:
        """Create a pending outbox entry from a persisted candidate row.

        Returns (row_or_empty_dict, outcome) where outcome is one of:
          created    — new outbox row created
          deduped    — row already existed (exact same candidate+channel dedupe key)
          suppressed — different candidate but recent pending/sent row for user+ticker+channel
          ineligible — candidate status is not 'candidate'
          error      — unexpected error (logged; does not raise)

        Order of checks:
          1. Build spec (eligibility check)
          2. Exact dedupe: fetch by dedupe_key — if found, return 'deduped'
          3. Noisy-repeat suppression: recent pending/sent for user+ticker+channel
          4. Insert new pending row
        This ordering ensures reprocessing the same candidate always returns
        'deduped' rather than 'suppressed', preserving the distinct semantics.
        """
        try:
            spec = build_delivery_spec(candidate_row, channel=channel)
            if spec is None:
                return {}, "ineligible"

            # Step 1: exact dedupe — same candidate+channel already has an outbox row.
            existing = self._fetch_by_dedupe_key(spec.user_id, spec.dedupe_key)
            if existing:
                logger.info(
                    "alert_delivery_outbox.deduped user_id=%s ticker=%s "
                    "channel=%s dedupe_key=%s",
                    spec.user_id,
                    spec.ticker,
                    spec.channel,
                    spec.dedupe_key,
                )
                return existing, "deduped"

            # Step 2: noisy-repeat suppression — a *different* candidate recently
            # created a pending/sent outbox row for the same user+ticker+channel.
            if self.has_recent_outbox(
                spec.user_id, spec.ticker, spec.channel, now=now
            ):
                logger.info(
                    "alert_delivery_outbox.suppressed user_id=%s ticker=%s "
                    "channel=%s reason=recent_pending_or_sent",
                    spec.user_id,
                    spec.ticker,
                    spec.channel,
                )
                return {}, "suppressed"

            # Step 3: insert new pending row.
            row, created = self.persist_outbox_entry(spec)
            return row, "created" if created else "deduped"

        except Exception as exc:
            logger.warning(
                "alert_delivery_outbox.create_error user_id=%s ticker=%s error=%s",
                candidate_row.get("user_id", "?"),
                candidate_row.get("ticker", "?"),
                exc,
            )
            return {}, "error"

    # ── Read path ─────────────────────────────────────────────────────────────

    def list_outbox_entries(
        self,
        user_id: str,
        *,
        limit: int = 50,
        channel: Optional[str] = None,
        status: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Return outbox entries for a user, newest first. Read-only."""
        query = self.client.table(_TABLE).select("*").eq("user_id", user_id)
        if channel:
            query = query.eq("channel", channel)
        if status:
            query = query.eq("status", status)
        result = query.order("created_at", desc=True).limit(limit).execute()
        return result.data or []
