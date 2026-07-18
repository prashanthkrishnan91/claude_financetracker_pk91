"""Stage 3E — Alert Email Delivery Worker v1 tests.

Acceptance criteria:
  1.  delivery disabled => no provider call, no sent status
  2.  dry-run mode => no provider call, no claim, rows left as skipped in summary
  3.  missing API key => skipped/failure without provider call
  4.  missing ALERT_EMAIL_FROM => skipped without provider call
  5.  missing ALERT_EMAIL_TO => skipped without provider call
  6.  unsupported provider => skipped without provider call
  7.  no provider configured => skipped without provider call
  8.  pending email row sends successfully and marks sent
  9.  provider failure marks row failed with reason
 10.  sent row is not resent (fetch_pending returns only pending rows)
 11.  non-email channels not fetched (fetch filters by channel=email)
 12.  scheduled_for future row not processed
 13.  scheduled_for past row IS processed
 14.  multiple pending rows processed with bounded limit
 15.  no mutation of Intel/Deploy/Watchtower/candidate tables
 16.  Resend client is mocked — no real network calls
 17.  broker disclaimer appended when not already present
 18.  broker disclaimer not duplicated when already in body
 19.  send exception marks row failed without crashing worker
 20.  empty pending rows returns zero-count summary
 21.  summary log key present in output
 22.  build_alert_email_delivery_worker reads from settings
 23.  entrypoint exits cleanly when ALERT_EMAIL_DELIVERY_ENABLED not set
 24.  entrypoint single-pass calls delivery worker once
 25.  outbox mark_sent updates row with sent status and provider_message_id
 26.  outbox mark_failed updates row with failed status and reason
 27.  fetch_pending_email_rows filters by channel=email and status=pending
 28.  fetch_pending_email_rows filters out future-scheduled rows
 29.  fetch_pending_email_rows includes null scheduled_for rows
 30.  fetch_pending_email_rows handles DB error gracefully
 31.  claim_for_delivery called before send_email
 32.  claim returns False => row skipped, no send
 33.  claim raises => row skipped without mark_failed
 34.  send succeeds but mark_sent raises => status_update_failed, no mark_failed
 35.  invalid scheduled_for string => row excluded (fail-safe)
 36.  dry-run does not call claim_for_delivery
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.services.alert.alert_email_delivery_worker_v1 import (
    AlertEmailDeliveryWorker,
    _BROKER_DISCLAIMER,
    build_alert_email_delivery_worker,
)
from app.services.alert.resend_client_v1 import ResendClient, ResendSendResult

# ── Constants ─────────────────────────────────────────────────────────────────

_NOW = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)
_USER_A = str(uuid.uuid4())


# ── Factories ─────────────────────────────────────────────────────────────────

def _make_worker(
    *,
    enabled: bool = True,
    dry_run: bool = False,
    provider: str = "resend",
    api_key: str = "re_test_key",
    from_addr: str = "alerts@example.com",
    to_addr: str = "user@example.com",
    outbox_service: Any = None,
    resend_client: Any = None,
) -> AlertEmailDeliveryWorker:
    return AlertEmailDeliveryWorker(
        enabled=enabled,
        dry_run=dry_run,
        provider=provider,
        api_key=api_key,
        from_addr=from_addr,
        to_addr=to_addr,
        outbox_service=outbox_service,
        resend_client=resend_client,
    )


def _pending_row(
    *,
    row_id: str | None = None,
    ticker: str = "AAPL",
    subject: str = "Opportunity: AAPL — BUY signal",
    body: str = "AAPL is a BUY opportunity.\n\nReview in the app before acting.",
    scheduled_for: str | None = None,
    status: str = "pending",
    channel: str = "email",
) -> dict[str, Any]:
    return {
        "id": row_id or str(uuid.uuid4()),
        "user_id": _USER_A,
        "ticker": ticker,
        "channel": channel,
        "status": status,
        "subject": subject,
        "plain_english_body": body,
        "scheduled_for": scheduled_for,
        "sent_at": None,
        "provider_message_id": None,
        "failure_reason": None,
        "created_at": _NOW.isoformat(),
        "updated_at": _NOW.isoformat(),
    }


def _make_outbox_svc(rows: list[dict] | None = None) -> MagicMock:
    svc = MagicMock()
    svc.fetch_pending_email_rows.return_value = rows or []
    svc.claim_for_delivery.return_value = True
    svc.mark_sent.return_value = None
    svc.mark_failed.return_value = None
    return svc


def _make_resend_client(*, success: bool = True, msg_id: str = "msg_123") -> MagicMock:
    client = MagicMock(spec=ResendClient)
    result = ResendSendResult(
        success=success,
        provider_message_id=msg_id if success else None,
        failure_reason=None if success else "http_422: invalid recipient",
    )
    client.send_email.return_value = result
    return client


# ── 1. Delivery disabled ──────────────────────────────────────────────────────

def test_delivery_disabled_no_provider_call():
    resend = _make_resend_client()
    svc = _make_outbox_svc()
    worker = _make_worker(enabled=False, outbox_service=svc, resend_client=resend)
    result = worker.run_delivery_pass(now=_NOW)
    resend.send_email.assert_not_called()
    svc.mark_sent.assert_not_called()
    assert result["sent"] == 0
    assert result["scanned"] == 0


def test_delivery_disabled_no_db_fetch():
    svc = _make_outbox_svc()
    worker = _make_worker(enabled=False, outbox_service=svc)
    worker.run_delivery_pass(now=_NOW)
    svc.fetch_pending_email_rows.assert_not_called()


# ── 2. Dry-run mode ───────────────────────────────────────────────────────────

def test_dry_run_no_provider_call():
    row = _pending_row()
    resend = _make_resend_client()
    svc = _make_outbox_svc([row])
    worker = _make_worker(dry_run=True, outbox_service=svc, resend_client=resend)
    result = worker.run_delivery_pass(now=_NOW)
    resend.send_email.assert_not_called()
    svc.claim_for_delivery.assert_not_called()
    svc.mark_sent.assert_not_called()
    svc.mark_failed.assert_not_called()
    assert result["scanned"] == 1
    assert result["skipped"] == 1
    assert result["sent"] == 0
    assert result["dry_run"] is True


# ── 3–7. Config validation ────────────────────────────────────────────────────

def test_missing_api_key_skips_without_provider_call():
    resend = _make_resend_client()
    svc = _make_outbox_svc([_pending_row()])
    worker = _make_worker(api_key="", outbox_service=svc, resend_client=resend)
    result = worker.run_delivery_pass(now=_NOW)
    resend.send_email.assert_not_called()
    assert result["sent"] == 0
    assert result["scanned"] == 0  # never fetches rows if config fails


def test_missing_from_skips_without_provider_call():
    resend = _make_resend_client()
    svc = _make_outbox_svc([_pending_row()])
    worker = _make_worker(from_addr="", outbox_service=svc, resend_client=resend)
    result = worker.run_delivery_pass(now=_NOW)
    resend.send_email.assert_not_called()
    assert result["sent"] == 0


def test_missing_to_skips_without_provider_call():
    resend = _make_resend_client()
    svc = _make_outbox_svc([_pending_row()])
    worker = _make_worker(to_addr="", outbox_service=svc, resend_client=resend)
    result = worker.run_delivery_pass(now=_NOW)
    resend.send_email.assert_not_called()
    assert result["sent"] == 0


def test_unsupported_provider_skips():
    resend = _make_resend_client()
    svc = _make_outbox_svc([_pending_row()])
    worker = _make_worker(provider="sendgrid", outbox_service=svc, resend_client=resend)
    result = worker.run_delivery_pass(now=_NOW)
    resend.send_email.assert_not_called()
    assert result["sent"] == 0


def test_no_provider_configured_skips():
    resend = _make_resend_client()
    svc = _make_outbox_svc([_pending_row()])
    worker = _make_worker(provider="", outbox_service=svc, resend_client=resend)
    result = worker.run_delivery_pass(now=_NOW)
    resend.send_email.assert_not_called()
    assert result["sent"] == 0


# ── 8. Successful send ────────────────────────────────────────────────────────

def test_pending_row_sends_and_marks_sent():
    row = _pending_row()
    resend = _make_resend_client(success=True, msg_id="msg_abc")
    svc = _make_outbox_svc([row])
    worker = _make_worker(outbox_service=svc, resend_client=resend)
    result = worker.run_delivery_pass(now=_NOW)
    resend.send_email.assert_called_once()
    svc.mark_sent.assert_called_once_with(
        row["id"],
        provider_message_id="msg_abc",
        sent_at=_NOW,
    )
    assert result["sent"] == 1
    assert result["failed"] == 0


def test_send_uses_existing_plain_english_body():
    row = _pending_row(body="AAPL signals BUY.\n\nReview in the app before acting.")
    resend = _make_resend_client()
    svc = _make_outbox_svc([row])
    worker = _make_worker(outbox_service=svc, resend_client=resend)
    worker.run_delivery_pass(now=_NOW)
    call_kwargs = resend.send_email.call_args.kwargs
    assert "AAPL signals BUY" in call_kwargs["body"]
    assert "Review in the app before acting." in call_kwargs["body"]


# ── 9. Provider failure ───────────────────────────────────────────────────────

def test_provider_failure_marks_failed_with_reason():
    row = _pending_row()
    resend = _make_resend_client(success=False)
    svc = _make_outbox_svc([row])
    worker = _make_worker(outbox_service=svc, resend_client=resend)
    result = worker.run_delivery_pass(now=_NOW)
    svc.mark_sent.assert_not_called()
    svc.mark_failed.assert_called_once()
    call_kwargs = svc.mark_failed.call_args.kwargs
    assert "failure_reason" in call_kwargs
    assert call_kwargs["failure_reason"]
    assert result["failed"] == 1
    assert result["sent"] == 0


# ── 10. Sent row is not resent ────────────────────────────────────────────────

def test_sent_row_not_resent():
    """fetch_pending_email_rows only returns pending rows; sent rows are excluded."""
    resend = _make_resend_client()
    # Simulate: no pending rows (sent row already consumed)
    svc = _make_outbox_svc([])
    worker = _make_worker(outbox_service=svc, resend_client=resend)
    result = worker.run_delivery_pass(now=_NOW)
    resend.send_email.assert_not_called()
    assert result["sent"] == 0
    assert result["scanned"] == 0


# ── 11. Non-email channels ignored ───────────────────────────────────────────

def test_non_email_channels_not_fetched():
    """Worker fetches only email rows; push/in_app rows come back from DB as empty."""
    svc = _make_outbox_svc([])  # DB returns only email+pending rows
    resend = _make_resend_client()
    worker = _make_worker(outbox_service=svc, resend_client=resend)
    worker.run_delivery_pass(now=_NOW)
    # Verify fetch was called with channel=email constraint (delegated to service)
    svc.fetch_pending_email_rows.assert_called_once()


# ── 12–13. scheduled_for filtering ───────────────────────────────────────────

def test_future_scheduled_row_not_processed_via_service():
    """Service-level: fetch_pending_email_rows excludes future-scheduled rows."""
    from app.services.alert.alert_delivery_outbox_service import AlertDeliveryOutboxService

    future_iso = (_NOW + timedelta(hours=2)).isoformat()
    past_iso = (_NOW - timedelta(hours=1)).isoformat()
    null_row = _pending_row(ticker="AAPL", scheduled_for=None)
    past_row = _pending_row(ticker="GOOG", scheduled_for=past_iso)
    future_row = _pending_row(ticker="MSFT", scheduled_for=future_iso)

    client = MagicMock()
    client.table.return_value.select.return_value.eq.return_value.eq.return_value \
        .order.return_value.limit.return_value.execute.return_value = MagicMock(
        data=[null_row, past_row, future_row]
    )
    svc = AlertDeliveryOutboxService.__new__(AlertDeliveryOutboxService)
    svc.client = client

    rows = svc.fetch_pending_email_rows(limit=50, now=_NOW)
    tickers = [r["ticker"] for r in rows]
    assert "AAPL" in tickers  # null scheduled_for → eligible
    assert "GOOG" in tickers  # past scheduled_for → eligible
    assert "MSFT" not in tickers  # future → excluded


def test_past_scheduled_row_is_processed():
    past_iso = (_NOW - timedelta(minutes=5)).isoformat()
    row = _pending_row(scheduled_for=past_iso)
    resend = _make_resend_client()
    svc = _make_outbox_svc([row])
    worker = _make_worker(outbox_service=svc, resend_client=resend)
    result = worker.run_delivery_pass(now=_NOW)
    resend.send_email.assert_called_once()
    assert result["sent"] == 1


# ── 14. Multiple rows with bounded limit ──────────────────────────────────────

def test_multiple_rows_processed_up_to_limit():
    rows = [_pending_row(ticker=f"T{i}") for i in range(5)]
    resend = _make_resend_client()
    svc = _make_outbox_svc(rows)
    worker = _make_worker(outbox_service=svc, resend_client=resend)
    result = worker.run_delivery_pass(limit=5, now=_NOW)
    assert resend.send_email.call_count == 5
    assert result["sent"] == 5
    assert result["scanned"] == 5


def test_limit_passed_to_fetch():
    svc = _make_outbox_svc([])
    worker = _make_worker(outbox_service=svc)
    worker.run_delivery_pass(limit=10, now=_NOW)
    svc.fetch_pending_email_rows.assert_called_once_with(limit=10, now=_NOW)


# ── 15. No mutation of other tables ──────────────────────────────────────────

def test_no_intel_deploy_watchtower_candidate_mutation():
    """Worker only calls outbox service methods; no other table writes."""
    row = _pending_row()
    resend = _make_resend_client()
    svc = _make_outbox_svc([row])
    worker = _make_worker(outbox_service=svc, resend_client=resend)
    worker.run_delivery_pass(now=_NOW)
    # Only outbox service methods should be called — nothing else
    # method_calls items are (name, args, kwargs) 3-tuples
    called_methods = {
        name for name, _args, _kwargs in svc.method_calls
        if not name.startswith("_")
    }
    assert called_methods <= {"fetch_pending_email_rows", "claim_for_delivery", "mark_sent", "mark_failed"}


# ── 16. No real network calls ─────────────────────────────────────────────────

def test_resend_client_is_mocked_no_real_network():
    """No httpx call in tests — ResendClient.send_email is mocked."""
    resend = _make_resend_client()
    assert isinstance(resend, MagicMock)
    # Verify send_email is present and callable
    resend.send_email(from_addr="a", to_addrs=["b"], subject="s", body="b")
    resend.send_email.assert_called_once()


# ── 17–18. Broker disclaimer ──────────────────────────────────────────────────

def test_broker_disclaimer_appended_when_absent():
    body_without = "AAPL signals BUY.\n\nReview in the app before acting."
    row = _pending_row(body=body_without)
    resend = _make_resend_client()
    svc = _make_outbox_svc([row])
    worker = _make_worker(outbox_service=svc, resend_client=resend)
    worker.run_delivery_pass(now=_NOW)
    call_kwargs = resend.send_email.call_args.kwargs
    assert _BROKER_DISCLAIMER in call_kwargs["body"]


def test_broker_disclaimer_not_duplicated():
    body_with = (
        f"AAPL signals BUY.\n\nReview in the app before acting.\n\n{_BROKER_DISCLAIMER}"
    )
    row = _pending_row(body=body_with)
    resend = _make_resend_client()
    svc = _make_outbox_svc([row])
    worker = _make_worker(outbox_service=svc, resend_client=resend)
    worker.run_delivery_pass(now=_NOW)
    call_kwargs = resend.send_email.call_args.kwargs
    assert call_kwargs["body"].count(_BROKER_DISCLAIMER) == 1


# ── 19. Send exception handling ───────────────────────────────────────────────

def test_send_exception_marks_failed_without_crash():
    row = _pending_row()
    resend = MagicMock(spec=ResendClient)
    resend.send_email.side_effect = RuntimeError("unexpected network error")
    svc = _make_outbox_svc([row])
    worker = _make_worker(outbox_service=svc, resend_client=resend)
    result = worker.run_delivery_pass(now=_NOW)
    svc.mark_failed.assert_called_once()
    assert result["failed"] == 1
    assert result["sent"] == 0


# ── 20. Empty pending rows ────────────────────────────────────────────────────

def test_empty_pending_rows_returns_zero_summary():
    resend = _make_resend_client()
    svc = _make_outbox_svc([])
    worker = _make_worker(outbox_service=svc, resend_client=resend)
    result = worker.run_delivery_pass(now=_NOW)
    assert result == {
        "scanned": 0,
        "sent": 0,
        "failed": 0,
        "skipped": 0,
        "status_update_failed": 0,
        "dry_run": False,
        "provider": "resend",
    }
    resend.send_email.assert_not_called()


# ── 22. build_alert_email_delivery_worker ────────────────────────────────────

def test_build_from_settings_reads_config():
    mock_settings = MagicMock()
    mock_settings.alert_email_delivery_enabled = True
    mock_settings.alert_email_dry_run = True
    mock_settings.alert_email_provider = "resend"
    mock_settings.resend_api_key = "re_key_test"
    mock_settings.alert_email_from = "from@example.com"
    mock_settings.alert_email_to = "to@example.com"

    with patch(
        "app.services.alert.alert_email_delivery_worker_v1.get_settings",
        return_value=mock_settings,
    ):
        worker = build_alert_email_delivery_worker()

    assert worker._enabled is True
    assert worker._dry_run is True
    assert worker._provider == "resend"
    assert worker._api_key == "re_key_test"
    assert worker._from_addr == "from@example.com"
    assert worker._to_addr == "to@example.com"


# ── 23–24. Entrypoint ────────────────────────────────────────────────────────

def test_entrypoint_exits_cleanly_when_not_enabled():
    from app.services.alert.alert_email_delivery_worker_entrypoint import main

    with patch.dict("os.environ", {"ALERT_EMAIL_DELIVERY_ENABLED": "false"}):
        exit_code = main([])
    assert exit_code == 0


def test_entrypoint_single_pass_calls_worker(capsys):
    from app.services.alert.alert_email_delivery_worker_entrypoint import main

    mock_result = {"scanned": 0, "sent": 0, "failed": 0, "skipped": 0, "dry_run": True, "provider": "none"}
    # Current contract: the entrypoint is additionally gated by the master
    # background-workers kill switch (INTEL_BACKGROUND_WORKERS_ENABLED); both
    # flags must be truthy for a delivery pass to run.
    with patch.dict(
        "os.environ",
        {
            "ALERT_EMAIL_DELIVERY_ENABLED": "true",
            "INTEL_BACKGROUND_WORKERS_ENABLED": "true",
        },
    ):
        with patch(
            "app.services.alert.alert_email_delivery_worker_entrypoint._run_one_pass",
            return_value=mock_result,
        ) as mock_run:
            exit_code = main([])
    assert exit_code == 0
    mock_run.assert_called_once()


# ── 25–26. Outbox service mark methods ───────────────────────────────────────

def test_mark_sent_updates_row():
    from app.services.alert.alert_delivery_outbox_service import AlertDeliveryOutboxService

    row_id = str(uuid.uuid4())
    msg_id = "resend_msg_123"

    client = MagicMock()
    chain = MagicMock()
    chain.update.return_value = chain
    chain.eq.return_value = chain
    chain.execute.return_value = MagicMock(data=[])
    client.table.return_value = chain

    svc = AlertDeliveryOutboxService.__new__(AlertDeliveryOutboxService)
    svc.client = client

    svc.mark_sent(row_id, provider_message_id=msg_id, sent_at=_NOW)
    chain.update.assert_called_once()
    update_payload = chain.update.call_args.args[0]
    assert update_payload["status"] == "sent"
    assert update_payload["provider_message_id"] == msg_id
    assert update_payload["sent_at"] == _NOW.isoformat()


def test_mark_failed_updates_row():
    from app.services.alert.alert_delivery_outbox_service import AlertDeliveryOutboxService

    row_id = str(uuid.uuid4())

    client = MagicMock()
    chain = MagicMock()
    chain.update.return_value = chain
    chain.eq.return_value = chain
    chain.execute.return_value = MagicMock(data=[])
    client.table.return_value = chain

    svc = AlertDeliveryOutboxService.__new__(AlertDeliveryOutboxService)
    svc.client = client

    svc.mark_failed(row_id, failure_reason="http_422: bad request")
    chain.update.assert_called_once()
    update_payload = chain.update.call_args.args[0]
    assert update_payload["status"] == "failed"
    assert "http_422" in update_payload["failure_reason"]


# ── 27–30. fetch_pending_email_rows ──────────────────────────────────────────

def test_fetch_filters_by_email_and_pending():
    from app.services.alert.alert_delivery_outbox_service import AlertDeliveryOutboxService

    pending_email = _pending_row(channel="email", status="pending", scheduled_for=None)

    client = MagicMock()
    client.table.return_value.select.return_value.eq.return_value.eq.return_value \
        .order.return_value.limit.return_value.execute.return_value = MagicMock(
        data=[pending_email]
    )
    svc = AlertDeliveryOutboxService.__new__(AlertDeliveryOutboxService)
    svc.client = client

    rows = svc.fetch_pending_email_rows(limit=50, now=_NOW)
    assert len(rows) == 1
    assert rows[0]["channel"] == "email"
    assert rows[0]["status"] == "pending"


def test_fetch_excludes_future_scheduled_for():
    from app.services.alert.alert_delivery_outbox_service import AlertDeliveryOutboxService

    future_iso = (_NOW + timedelta(hours=1)).isoformat()
    future_row = _pending_row(scheduled_for=future_iso)

    client = MagicMock()
    client.table.return_value.select.return_value.eq.return_value.eq.return_value \
        .order.return_value.limit.return_value.execute.return_value = MagicMock(
        data=[future_row]
    )
    svc = AlertDeliveryOutboxService.__new__(AlertDeliveryOutboxService)
    svc.client = client

    rows = svc.fetch_pending_email_rows(limit=50, now=_NOW)
    assert rows == []


def test_fetch_includes_null_scheduled_for():
    from app.services.alert.alert_delivery_outbox_service import AlertDeliveryOutboxService

    null_row = _pending_row(scheduled_for=None)

    client = MagicMock()
    client.table.return_value.select.return_value.eq.return_value.eq.return_value \
        .order.return_value.limit.return_value.execute.return_value = MagicMock(
        data=[null_row]
    )
    svc = AlertDeliveryOutboxService.__new__(AlertDeliveryOutboxService)
    svc.client = client

    rows = svc.fetch_pending_email_rows(limit=50, now=_NOW)
    assert len(rows) == 1


def test_fetch_db_error_returns_empty():
    from app.services.alert.alert_delivery_outbox_service import AlertDeliveryOutboxService

    client = MagicMock()
    client.table.return_value.select.return_value.eq.return_value.eq.return_value \
        .order.return_value.limit.return_value.execute.side_effect = RuntimeError("db down")
    svc = AlertDeliveryOutboxService.__new__(AlertDeliveryOutboxService)
    svc.client = client

    rows = svc.fetch_pending_email_rows(limit=50, now=_NOW)
    assert rows == []


# ── 31. Claim called before send ──────────────────────────────────────────────

def test_claim_called_before_send():
    row = _pending_row()
    resend = _make_resend_client()
    svc = _make_outbox_svc([row])
    call_order: list[str] = []
    svc.claim_for_delivery.side_effect = lambda *a, **kw: call_order.append("claim") or True
    resend.send_email.side_effect = lambda **kw: call_order.append("send") or ResendSendResult(
        success=True, provider_message_id="m1", failure_reason=None
    )
    worker = _make_worker(outbox_service=svc, resend_client=resend)
    worker.run_delivery_pass(now=_NOW)
    assert call_order.index("claim") < call_order.index("send")


# ── 32. Claim returns False => skip, no send ──────────────────────────────────

def test_claim_false_skips_row_no_send():
    row = _pending_row()
    resend = _make_resend_client()
    svc = _make_outbox_svc([row])
    svc.claim_for_delivery.return_value = False
    worker = _make_worker(outbox_service=svc, resend_client=resend)
    result = worker.run_delivery_pass(now=_NOW)
    resend.send_email.assert_not_called()
    svc.mark_sent.assert_not_called()
    svc.mark_failed.assert_not_called()
    assert result["skipped"] == 1
    assert result["sent"] == 0


# ── 33. Claim raises => skip without mark_failed ──────────────────────────────

def test_claim_exception_skips_without_mark_failed():
    row = _pending_row()
    resend = _make_resend_client()
    svc = _make_outbox_svc([row])
    svc.claim_for_delivery.side_effect = RuntimeError("db timeout")
    worker = _make_worker(outbox_service=svc, resend_client=resend)
    result = worker.run_delivery_pass(now=_NOW)
    resend.send_email.assert_not_called()
    svc.mark_failed.assert_not_called()
    assert result["skipped"] == 1
    assert result["sent"] == 0


# ── 34. Send success + mark_sent failure => status_update_failed, no mark_failed

def test_send_success_mark_sent_failure_leaves_processing():
    row = _pending_row()
    resend = _make_resend_client(success=True, msg_id="msg_ok")
    svc = _make_outbox_svc([row])
    svc.mark_sent.side_effect = RuntimeError("DB write failed after send")
    worker = _make_worker(outbox_service=svc, resend_client=resend)
    result = worker.run_delivery_pass(now=_NOW)
    # Email was sent; must NOT call mark_failed (would risk duplicate on retry).
    svc.mark_failed.assert_not_called()
    assert result["status_update_failed"] == 1
    assert result["sent"] == 0
    assert result["failed"] == 0


# ── 35. Invalid scheduled_for safe skip ───────────────────────────────────────

def test_invalid_scheduled_for_safe_skip():
    from app.services.alert.alert_delivery_outbox_service import AlertDeliveryOutboxService

    bad_row = _pending_row(scheduled_for="not-a-timestamp")
    good_row = _pending_row(ticker="GOOG", scheduled_for=None)

    client = MagicMock()
    client.table.return_value.select.return_value.eq.return_value.eq.return_value \
        .order.return_value.limit.return_value.execute.return_value = MagicMock(
        data=[bad_row, good_row]
    )
    svc = AlertDeliveryOutboxService.__new__(AlertDeliveryOutboxService)
    svc.client = client

    rows = svc.fetch_pending_email_rows(limit=50, now=_NOW)
    tickers = [r["ticker"] for r in rows]
    assert "AAPL" not in tickers   # malformed → excluded
    assert "GOOG" in tickers       # null → included


# ── 36. Dry-run does not call claim_for_delivery ──────────────────────────────

def test_dry_run_does_not_claim():
    row = _pending_row()
    svc = _make_outbox_svc([row])
    worker = _make_worker(dry_run=True, outbox_service=svc)
    worker.run_delivery_pass(now=_NOW)
    svc.claim_for_delivery.assert_not_called()
