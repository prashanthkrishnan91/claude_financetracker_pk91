"""Alert Email Delivery Worker v1 — Resend provider.

Reads pending email outbox rows and delivers via Resend when explicitly enabled.

Hard safety:
- No email sent unless ALERT_EMAIL_DELIVERY_ENABLED=true AND all required config
  is present AND ALERT_EMAIL_DRY_RUN=false (which defaults to true).
- Row-level idempotency: only pending rows are fetched; sent rows are never resent.
- Failures are recorded per-row without crashing the worker.
- No Intel v3, Deploy, Watchtower, alert candidate, or feedback row mutations.
- No LLM calls. No SQL schema changes. No frontend changes.

Structured log key:
  alert_email_delivery_summary scanned=N sent=N failed=N skipped=N dry_run=... provider=resend
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from ...config import get_settings

logger = logging.getLogger(__name__)

_BATCH_LIMIT = 50
_BROKER_DISCLAIMER = "This is not broker execution."


class AlertEmailDeliveryWorker:
    """Process pending email outbox rows and deliver via Resend.

    Inject outbox_service and resend_client in tests to avoid DB/network calls.
    In production, leave them as None; they are built lazily from settings.
    """

    def __init__(
        self,
        *,
        enabled: bool,
        dry_run: bool,
        provider: str,
        api_key: Optional[str],
        from_addr: Optional[str],
        to_addr: Optional[str],
        outbox_service: Any = None,
        resend_client: Any = None,
    ) -> None:
        self._enabled = enabled
        self._dry_run = dry_run
        self._provider = (provider or "").strip().lower()
        self._api_key = api_key
        self._from_addr = from_addr
        self._to_addr = to_addr
        self._outbox_service = outbox_service
        self._resend_client = resend_client

    def run_delivery_pass(
        self,
        *,
        limit: int = _BATCH_LIMIT,
        now: Optional[datetime] = None,
    ) -> dict[str, Any]:
        """Process up to `limit` pending email outbox rows.

        Returns a compact summary dict with keys:
          scanned, sent, failed, skipped, dry_run, provider
        Never raises — all errors are caught and logged.
        """
        if now is None:
            now = datetime.now(timezone.utc)

        summary: dict[str, Any] = {
            "scanned": 0,
            "sent": 0,
            "failed": 0,
            "skipped": 0,
            "dry_run": self._dry_run,
            "provider": self._provider or "none",
        }

        if not self._enabled:
            self._emit_log(summary, note="delivery_disabled")
            return summary

        config_ok, skip_reason = self._check_config()
        if not config_ok:
            self._emit_log(summary, note=f"config_incomplete:{skip_reason}")
            return summary

        svc = self._outbox_service or self._build_outbox_service()
        rows = svc.fetch_pending_email_rows(limit=limit, now=now)
        summary["scanned"] = len(rows)

        if not rows:
            self._emit_log(summary)
            return summary

        if self._dry_run:
            summary["skipped"] = len(rows)
            self._emit_log(summary, note="rows_would_send")
            return summary

        client = self._resend_client or self._build_resend_client()
        for row in rows:
            row_id = str(row.get("id", ""))
            ticker = row.get("ticker", "?")
            try:
                body = self._build_email_body(row)
                result = client.send_email(
                    from_addr=self._from_addr,
                    to_addrs=[self._to_addr],
                    subject=row.get("subject") or f"Alert: {ticker}",
                    body=body,
                )
                if result.success:
                    svc.mark_sent(
                        row_id,
                        provider_message_id=result.provider_message_id,
                        sent_at=now,
                    )
                    summary["sent"] += 1
                else:
                    svc.mark_failed(
                        row_id,
                        failure_reason=result.failure_reason or "provider_failure",
                    )
                    summary["failed"] += 1
            except Exception as exc:
                logger.warning(
                    "alert_email_delivery.row_error row_id=%s ticker=%s error=%s",
                    row_id,
                    ticker,
                    exc,
                )
                try:
                    svc.mark_failed(row_id, failure_reason=str(exc)[:300])
                except Exception:
                    pass
                summary["failed"] += 1

        self._emit_log(summary)
        return summary

    # ── Private helpers ───────────────────────────────────────────────────────

    def _check_config(self) -> tuple[bool, str]:
        """Return (ok, reason_code). reason_code is '' when ok."""
        if not self._provider:
            return False, "no_provider_configured"
        if self._provider != "resend":
            return False, f"unsupported_provider={self._provider}"
        if not self._api_key:
            return False, "missing_resend_api_key"
        if not self._from_addr:
            return False, "missing_alert_email_from"
        if not self._to_addr:
            return False, "missing_alert_email_to"
        return True, ""

    def _build_email_body(self, row: dict[str, Any]) -> str:
        body = row.get("plain_english_body") or ""
        if _BROKER_DISCLAIMER not in body:
            body = f"{body}\n\n{_BROKER_DISCLAIMER}" if body else _BROKER_DISCLAIMER
        return body

    def _build_outbox_service(self) -> Any:
        from .alert_delivery_outbox_service import AlertDeliveryOutboxService
        return AlertDeliveryOutboxService()

    def _build_resend_client(self) -> Any:
        from .resend_client_v1 import ResendClient
        return ResendClient(api_key=self._api_key)  # type: ignore[arg-type]

    def _emit_log(self, summary: dict[str, Any], note: str = "") -> None:
        logger.info(
            "alert_email_delivery_summary scanned=%d sent=%d failed=%d skipped=%d "
            "dry_run=%s provider=%s%s",
            summary["scanned"],
            summary["sent"],
            summary["failed"],
            summary["skipped"],
            summary["dry_run"],
            summary["provider"],
            f" note={note}" if note else "",
        )


def build_alert_email_delivery_worker() -> AlertEmailDeliveryWorker:
    """Build a worker from application settings. Used by the entrypoint."""
    s = get_settings()
    return AlertEmailDeliveryWorker(
        enabled=s.alert_email_delivery_enabled,
        dry_run=s.alert_email_dry_run,
        provider=s.alert_email_provider or "",
        api_key=s.resend_api_key,
        from_addr=s.alert_email_from,
        to_addr=s.alert_email_to,
    )
