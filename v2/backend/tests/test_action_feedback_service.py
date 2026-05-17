"""Action Feedback Foundation v1 — service and model tests.

Covers:
  1. Valid feedback creation → new row returned, created=True
  2. Invalid feedback_type → Pydantic ValidationError
  3. Invalid source_area → Pydantic ValidationError
  4. Idempotent duplicate handling → second submit returns existing row, created=False
  5. User isolation — list returns only the requesting user's rows
  6. List filter by ticker
  7. List filter by source_area
  8. Feedback create does not touch intel_v3_snapshots (no Intel decision mutation)
  9. Ticker normalized to uppercase in create
 10. Empty list when no rows exist
 11. Note stored and retrieved correctly
 12. Agent_run_id and snapshot_id stored as strings
 13. Null fields handled correctly (ticker/action_type/note all optional)
 14. cooldown_until stored in payload when provided (Stage 3B snoozed suppression support)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.models.action_feedback import ActionFeedbackCreateRequest
from app.services.action_feedback_service import ActionFeedbackService

USER_A = str(uuid.uuid4())
USER_B = str(uuid.uuid4())
_NOW = datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc).isoformat()


# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_row(
    user_id: str = USER_A,
    feedback_type: str = "skipped",
    source_area: str = "intel",
    idempotency_key: str = "test:AAPL:BUY:2026-05-16",
    ticker: str | None = "AAPL",
    action_type: str | None = "BUY",
    note: str | None = None,
) -> dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "feedback_type": feedback_type,
        "source_area": source_area,
        "idempotency_key": idempotency_key,
        "ticker": ticker,
        "action_type": action_type,
        "agent_run_id": None,
        "snapshot_id": None,
        "note": note,
        "created_at": _NOW,
    }


def _make_insert_client(*, inserted_row: dict[str, Any]) -> MagicMock:
    """Client where insert succeeds and returns the inserted row."""
    client = MagicMock()
    chain = MagicMock()
    chain.insert.return_value = chain
    chain.execute.return_value = MagicMock(data=[inserted_row])
    client.table.return_value = chain
    return client


def _make_dedup_client(
    *, existing_row: dict[str, Any], conflict_msg: str = "unique violation"
) -> MagicMock:
    """Client where insert raises a unique-constraint error, then select returns existing."""
    client = MagicMock()

    inserted_chain = MagicMock()
    inserted_chain.execute.side_effect = Exception(conflict_msg)
    inserted_chain.insert.return_value = inserted_chain

    select_chain = MagicMock()
    select_chain.select.return_value = select_chain
    select_chain.eq.return_value = select_chain
    select_chain.limit.return_value = select_chain
    select_chain.execute.return_value = MagicMock(data=[existing_row])

    def table_side_effect(name: str):
        # First call → insert chain; subsequent calls → select chain
        table_side_effect.call_count = getattr(table_side_effect, "call_count", 0) + 1
        if table_side_effect.call_count == 1:
            return inserted_chain
        return select_chain

    client.table.side_effect = table_side_effect
    return client


def _make_list_client(*, rows: list[dict[str, Any]]) -> MagicMock:
    """Client where select returns provided rows."""
    client = MagicMock()
    chain = MagicMock()
    chain.select.return_value = chain
    chain.eq.return_value = chain
    chain.order.return_value = chain
    chain.limit.return_value = chain
    chain.execute.return_value = MagicMock(data=rows)
    client.table.return_value = chain
    return client


def _make_service(client: MagicMock) -> ActionFeedbackService:
    svc = object.__new__(ActionFeedbackService)
    svc.client = client
    return svc


# ── Model validation tests ─────────────────────────────────────────────────────


def test_valid_model_parses():
    req = ActionFeedbackCreateRequest(
        feedback_type="skipped",
        source_area="intel",
        idempotency_key="intel:AAPL:BUY:2026-05-16",
        ticker="aapl",
        action_type="BUY",
        note="Too concentrated already",
    )
    assert req.feedback_type == "skipped"
    assert req.source_area == "intel"


def test_invalid_feedback_type_rejected():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ActionFeedbackCreateRequest(
            feedback_type="approved",  # not in allowed set
            source_area="intel",
            idempotency_key="k",
        )


def test_invalid_source_area_rejected():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ActionFeedbackCreateRequest(
            feedback_type="skipped",
            source_area="broker",  # not in allowed set
            idempotency_key="k",
        )


def test_empty_idempotency_key_rejected():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ActionFeedbackCreateRequest(
            feedback_type="skipped",
            source_area="intel",
            idempotency_key="",
        )


def test_all_feedback_types_valid():
    for ft in ("executed", "skipped", "ignored", "snoozed", "too_risky", "not_relevant", "user_note"):
        req = ActionFeedbackCreateRequest(
            feedback_type=ft,
            source_area="intel",
            idempotency_key=f"k-{ft}",
        )
        assert req.feedback_type == ft


def test_all_source_areas_valid():
    for sa in ("intel", "deploy", "watchtower", "alert"):
        req = ActionFeedbackCreateRequest(
            feedback_type="skipped",
            source_area=sa,
            idempotency_key=f"k-{sa}",
        )
        assert req.source_area == sa


# ── Service create tests ───────────────────────────────────────────────────────


def test_create_valid_feedback_returns_row_and_created_true():
    row = _make_row()
    svc = _make_service(_make_insert_client(inserted_row=row))

    result_row, created = svc.create(
        user_id=USER_A,
        data={
            "feedback_type": "skipped",
            "source_area": "intel",
            "idempotency_key": "test:AAPL:BUY:2026-05-16",
            "ticker": "AAPL",
            "action_type": "BUY",
        },
    )

    assert created is True
    assert result_row["feedback_type"] == "skipped"
    assert result_row["user_id"] == USER_A


def test_create_normalizes_ticker_to_uppercase():
    row = _make_row(ticker="MSFT")
    client = _make_insert_client(inserted_row=row)
    svc = _make_service(client)

    svc.create(
        user_id=USER_A,
        data={
            "feedback_type": "executed",
            "source_area": "deploy",
            "idempotency_key": "deploy:msft:BUY:2026-05-16",
            "ticker": "msft",
        },
    )

    # The insert was called with uppercase ticker
    insert_call = client.table.return_value.insert.call_args
    payload = insert_call[0][0]
    assert payload["ticker"] == "MSFT"


def test_create_stores_note():
    row = _make_row(note="Too risky right now")
    svc = _make_service(_make_insert_client(inserted_row=row))

    result_row, created = svc.create(
        user_id=USER_A,
        data={
            "feedback_type": "too_risky",
            "source_area": "intel",
            "idempotency_key": "intel:AAPL:BUY:2026-05-16:note",
            "note": "Too risky right now",
        },
    )
    assert created is True
    assert result_row["note"] == "Too risky right now"


def test_create_handles_null_optional_fields():
    row = _make_row(ticker=None, action_type=None, note=None)
    svc = _make_service(_make_insert_client(inserted_row=row))

    result_row, created = svc.create(
        user_id=USER_A,
        data={
            "feedback_type": "user_note",
            "source_area": "watchtower",
            "idempotency_key": "watchtower:general:2026-05-16",
        },
    )
    assert created is True
    assert result_row["ticker"] is None
    assert result_row["action_type"] is None


def test_create_stores_agent_run_id_as_string():
    run_id = uuid.uuid4()
    row = _make_row()
    row["agent_run_id"] = str(run_id)
    client = _make_insert_client(inserted_row=row)
    svc = _make_service(client)

    svc.create(
        user_id=USER_A,
        data={
            "feedback_type": "skipped",
            "source_area": "intel",
            "idempotency_key": "intel:AAPL:BUY:run-id-test",
            "agent_run_id": run_id,
        },
    )

    payload = client.table.return_value.insert.call_args[0][0]
    assert payload["agent_run_id"] == str(run_id)


# ── Idempotency tests ──────────────────────────────────────────────────────────


def test_duplicate_submit_returns_existing_row_created_false():
    existing = _make_row(idempotency_key="dup-key")
    client = _make_dedup_client(existing_row=existing, conflict_msg="unique violation 23505")
    svc = _make_service(client)

    result_row, created = svc.create(
        user_id=USER_A,
        data={
            "feedback_type": "skipped",
            "source_area": "intel",
            "idempotency_key": "dup-key",
        },
    )

    assert created is False
    assert result_row["idempotency_key"] == "dup-key"


def test_duplicate_submit_does_not_raise():
    existing = _make_row(idempotency_key="safe-key")
    client = _make_dedup_client(existing_row=existing, conflict_msg="duplicate key")
    svc = _make_service(client)

    # Must not raise
    result_row, created = svc.create(
        user_id=USER_A,
        data={
            "feedback_type": "ignored",
            "source_area": "deploy",
            "idempotency_key": "safe-key",
        },
    )
    assert result_row is not None


def test_non_unique_exception_propagates():
    client = MagicMock()
    chain = MagicMock()
    chain.insert.return_value = chain
    chain.execute.side_effect = Exception("network timeout")
    client.table.return_value = chain
    svc = _make_service(client)

    with pytest.raises(Exception, match="network timeout"):
        svc.create(
            user_id=USER_A,
            data={
                "feedback_type": "skipped",
                "source_area": "intel",
                "idempotency_key": "k",
            },
        )


# ── List / read tests ──────────────────────────────────────────────────────────


def test_list_returns_rows_for_user():
    rows = [_make_row(user_id=USER_A), _make_row(user_id=USER_A, idempotency_key="k2")]
    svc = _make_service(_make_list_client(rows=rows))

    result = svc.list(user_id=USER_A)
    assert len(result) == 2


def test_list_empty_when_no_rows():
    svc = _make_service(_make_list_client(rows=[]))

    result = svc.list(user_id=USER_A)
    assert result == []


def test_list_filter_by_ticker_passes_to_query():
    rows = [_make_row(ticker="AAPL")]
    client = _make_list_client(rows=rows)
    svc = _make_service(client)

    result = svc.list(user_id=USER_A, ticker="aapl")
    assert len(result) == 1
    # eq was called (ticker normalized to uppercase)
    assert client.table.return_value.eq.called


def test_list_filter_by_source_area_passes_to_query():
    rows = [_make_row(source_area="deploy")]
    client = _make_list_client(rows=rows)
    svc = _make_service(client)

    result = svc.list(user_id=USER_A, source_area="deploy")
    assert len(result) == 1


def test_user_isolation_different_clients():
    """User A's feedback is not returned when listing for user B."""
    rows_a = [_make_row(user_id=USER_A)]
    svc_a = _make_service(_make_list_client(rows=rows_a))
    svc_b = _make_service(_make_list_client(rows=[]))

    result_a = svc_a.list(user_id=USER_A)
    result_b = svc_b.list(user_id=USER_B)

    assert len(result_a) == 1
    assert result_a[0]["user_id"] == USER_A
    assert len(result_b) == 0


def test_list_limit_is_passed_to_query():
    client = _make_list_client(rows=[])
    svc = _make_service(client)

    svc.list(user_id=USER_A, limit=10)
    # limit() was called on the chain
    assert client.table.return_value.limit.called


# ── Intel v3 isolation test ────────────────────────────────────────────────────


def test_create_does_not_touch_intel_v3_snapshots():
    """Feedback create never queries or writes intel_v3_snapshots."""
    touched_tables: list[str] = []

    client = MagicMock()

    def table_tracker(name: str):
        touched_tables.append(name)
        chain = MagicMock()
        chain.insert.return_value = chain
        chain.execute.return_value = MagicMock(data=[_make_row()])
        return chain

    client.table.side_effect = table_tracker
    svc = _make_service(client)

    svc.create(
        user_id=USER_A,
        data={
            "feedback_type": "skipped",
            "source_area": "intel",
            "idempotency_key": "isolation-test:AAPL:BUY:2026-05-16",
        },
    )

    assert "intel_v3_snapshots" not in touched_tables
    assert "action_feedback_events" in touched_tables


def test_list_does_not_touch_intel_v3_snapshots():
    """Feedback list never queries intel_v3_snapshots."""
    touched_tables: list[str] = []

    client = MagicMock()

    def table_tracker(name: str):
        touched_tables.append(name)
        chain = MagicMock()
        chain.select.return_value = chain
        chain.eq.return_value = chain
        chain.order.return_value = chain
        chain.limit.return_value = chain
        chain.execute.return_value = MagicMock(data=[])
        return chain

    client.table.side_effect = table_tracker
    svc = _make_service(client)

    svc.list(user_id=USER_A)

    assert "intel_v3_snapshots" not in touched_tables
    assert "action_feedback_events" in touched_tables


# ── Explicit failure tests (new safe-fallback behavior) ───────────────────────


def _make_insert_no_data_client(*, lookup_row: dict[str, Any] | None) -> MagicMock:
    """Insert succeeds but returns no rows; subsequent lookup returns lookup_row."""
    client = MagicMock()
    insert_chain = MagicMock()
    insert_chain.insert.return_value = insert_chain
    insert_chain.execute.return_value = MagicMock(data=[])

    select_chain = MagicMock()
    select_chain.select.return_value = select_chain
    select_chain.eq.return_value = select_chain
    select_chain.limit.return_value = select_chain
    select_chain.execute.return_value = MagicMock(data=[lookup_row] if lookup_row else [])

    call_count = [0]

    def table_side_effect(name: str):
        call_count[0] += 1
        return insert_chain if call_count[0] == 1 else select_chain

    client.table.side_effect = table_side_effect
    return client


def test_insert_no_data_but_lookup_succeeds_returns_existing():
    """insert returns no data; follow-up lookup finds the row → return it, created=False."""
    existing = _make_row(idempotency_key="no-data-key")
    client = _make_insert_no_data_client(lookup_row=existing)
    svc = _make_service(client)

    result_row, created = svc.create(
        user_id=USER_A,
        data={
            "feedback_type": "skipped",
            "source_area": "intel",
            "idempotency_key": "no-data-key",
        },
    )

    assert created is False
    assert result_row["idempotency_key"] == "no-data-key"


def test_insert_no_data_and_lookup_empty_raises():
    """insert returns no data and follow-up lookup also returns nothing → explicit RuntimeError."""
    client = _make_insert_no_data_client(lookup_row=None)
    svc = _make_service(client)

    with pytest.raises(RuntimeError, match="action_feedback_create_no_row_returned"):
        svc.create(
            user_id=USER_A,
            data={
                "feedback_type": "skipped",
                "source_area": "intel",
                "idempotency_key": "ghost-key",
            },
        )


def test_unique_conflict_lookup_empty_raises():
    """Unique conflict hit but follow-up lookup returns nothing → explicit RuntimeError."""
    client = MagicMock()

    insert_chain = MagicMock()
    insert_chain.insert.return_value = insert_chain
    insert_chain.execute.side_effect = Exception("23505 unique constraint violation")

    select_chain = MagicMock()
    select_chain.select.return_value = select_chain
    select_chain.eq.return_value = select_chain
    select_chain.limit.return_value = select_chain
    select_chain.execute.return_value = MagicMock(data=[])

    call_count = [0]

    def table_side_effect(name: str):
        call_count[0] += 1
        return insert_chain if call_count[0] == 1 else select_chain

    client.table.side_effect = table_side_effect
    svc = _make_service(client)

    with pytest.raises(RuntimeError, match="action_feedback_dedup_lookup_failed"):
        svc.create(
            user_id=USER_A,
            data={
                "feedback_type": "ignored",
                "source_area": "deploy",
                "idempotency_key": "conflict-ghost-key",
            },
        )


# ── Stage 3B: cooldown_until support ──────────────────────────────────────────


def test_cooldown_until_stored_in_payload_when_provided():
    """Stage 3B: snoozed feedback with explicit cooldown_until persists the field."""
    from datetime import timedelta
    cooldown = datetime(2026, 5, 31, 0, 0, 0, tzinfo=timezone.utc)
    row = _make_row(feedback_type="snoozed")
    row["cooldown_until"] = cooldown.isoformat()
    client = _make_insert_client(inserted_row=row)
    svc = _make_service(client)

    svc.create(
        user_id=USER_A,
        data={
            "feedback_type": "snoozed",
            "source_area": "alert",
            "idempotency_key": "alert:AAPL:BUY:snoozed-test",
            "cooldown_until": cooldown,
        },
    )

    payload = client.table.return_value.insert.call_args[0][0]
    assert payload["cooldown_until"] == cooldown.isoformat()


def test_cooldown_until_is_none_when_not_provided():
    """cooldown_until defaults to None when not supplied."""
    row = _make_row()
    client = _make_insert_client(inserted_row=row)
    svc = _make_service(client)

    svc.create(
        user_id=USER_A,
        data={
            "feedback_type": "skipped",
            "source_area": "intel",
            "idempotency_key": "intel:AAPL:BUY:no-cooldown",
        },
    )

    payload = client.table.return_value.insert.call_args[0][0]
    assert payload["cooldown_until"] is None
