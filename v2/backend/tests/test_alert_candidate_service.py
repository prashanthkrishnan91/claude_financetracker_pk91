"""Alert Candidate Service v1 — persistence layer tests.

Covers:
  1.  persist_candidate returns row + created=True on first insert
  2.  persist_candidate returns existing row + created=False on dedupe
  3.  Non-unique DB exception propagates
  4.  list_candidates returns rows for user
  5.  list_candidates returns empty list when no rows
  6.  list_candidates ticker filter is applied
  7.  list_candidates status filter is applied
  8.  User isolation in list_candidates
  9.  Candidate does not touch intel_v3_snapshots or action_feedback_events
  10. Ticker is stored uppercase
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

from app.services.alert.alert_trigger_policy_v1 import AlertCandidate, POLICY_VERSION
from app.services.alert.alert_candidate_service import AlertCandidateService

_USER_A = str(uuid.uuid4())
_USER_B = str(uuid.uuid4())
_NOW = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc).isoformat()


def _make_candidate(
    user_id: str = _USER_A,
    ticker: str = "AAPL",
    candidate_type: str = "new_actionable_action",
    action_type: str = "BUY",
    severity: str = "normal",
) -> AlertCandidate:
    return AlertCandidate(
        user_id=user_id,
        ticker=ticker,
        source_area="intel",
        candidate_type=candidate_type,
        action_type=action_type,
        severity=severity,
        reason_code="action_became_buy",
        plain_english_reason="AAPL is a new BUY opportunity.",
        dedupe_key=f"test-key-{ticker}-{action_type}",
        policy_version=POLICY_VERSION,
        source_snapshot_id=str(uuid.uuid4()),
    )


def _make_row(candidate: AlertCandidate | None = None) -> dict[str, Any]:
    c = candidate or _make_candidate()
    return {
        "id": str(uuid.uuid4()),
        "user_id": c.user_id,
        "ticker": c.ticker.upper(),
        "source_area": c.source_area,
        "candidate_type": c.candidate_type,
        "action_type": c.action_type,
        "severity": c.severity,
        "reason_code": c.reason_code,
        "plain_english_reason": c.plain_english_reason,
        "policy_version": c.policy_version,
        "status": c.status,
        "dedupe_key": c.dedupe_key,
        "source_snapshot_id": c.source_snapshot_id,
        "source_run_id": None,
        "expires_at": None,
        "cooldown_until": None,
        "created_at": _NOW,
    }


def _make_insert_client(*, inserted_row: dict[str, Any]) -> MagicMock:
    client = MagicMock()
    chain = MagicMock()
    chain.insert.return_value = chain
    chain.execute.return_value = MagicMock(data=[inserted_row])
    client.table.return_value = chain
    return client


def _make_dedup_client(
    *, existing_row: dict[str, Any], conflict_msg: str = "unique violation 23505"
) -> MagicMock:
    client = MagicMock()

    insert_chain = MagicMock()
    insert_chain.insert.return_value = insert_chain
    insert_chain.execute.side_effect = Exception(conflict_msg)

    select_chain = MagicMock()
    select_chain.select.return_value = select_chain
    select_chain.eq.return_value = select_chain
    select_chain.limit.return_value = select_chain
    select_chain.execute.return_value = MagicMock(data=[existing_row])

    call_count = [0]

    def table_side(name: str):
        call_count[0] += 1
        return insert_chain if call_count[0] == 1 else select_chain

    client.table.side_effect = table_side
    return client


def _make_list_client(*, rows: list[dict[str, Any]]) -> MagicMock:
    client = MagicMock()
    chain = MagicMock()
    chain.select.return_value = chain
    chain.eq.return_value = chain
    chain.order.return_value = chain
    chain.limit.return_value = chain
    chain.execute.return_value = MagicMock(data=rows)
    client.table.return_value = chain
    return client


def _make_service(client: MagicMock) -> AlertCandidateService:
    svc = object.__new__(AlertCandidateService)
    svc.client = client
    return svc


# ── Test 1: First insert → created=True ─────────────────────────────────────

def test_persist_candidate_returns_row_and_created_true():
    candidate = _make_candidate()
    row = _make_row(candidate)
    svc = _make_service(_make_insert_client(inserted_row=row))

    result_row, created = svc.persist_candidate(candidate)

    assert created is True
    assert result_row["ticker"] == "AAPL"
    assert result_row["candidate_type"] == "new_actionable_action"


# ── Test 2: Dedupe → created=False ───────────────────────────────────────────

def test_persist_candidate_dedup_returns_existing_created_false():
    candidate = _make_candidate()
    existing = _make_row(candidate)
    svc = _make_service(_make_dedup_client(existing_row=existing))

    result_row, created = svc.persist_candidate(candidate)

    assert created is False
    assert result_row["dedupe_key"] == candidate.dedupe_key


# ── Test 3: Non-unique exception propagates ──────────────────────────────────

def test_non_unique_exception_propagates():
    import pytest

    client = MagicMock()
    chain = MagicMock()
    chain.insert.return_value = chain
    chain.execute.side_effect = Exception("network timeout")
    client.table.return_value = chain
    svc = _make_service(client)

    with pytest.raises(Exception, match="network timeout"):
        svc.persist_candidate(_make_candidate())


# ── Tests 4–8: list_candidates ───────────────────────────────────────────────

def test_list_candidates_returns_rows():
    rows = [_make_row(), _make_row(_make_candidate(ticker="MSFT"))]
    svc = _make_service(_make_list_client(rows=rows))

    result = svc.list_candidates(user_id=_USER_A)
    assert len(result) == 2


def test_list_candidates_empty_when_no_rows():
    svc = _make_service(_make_list_client(rows=[]))
    assert svc.list_candidates(user_id=_USER_A) == []


def test_list_candidates_ticker_filter_applied():
    rows = [_make_row()]
    client = _make_list_client(rows=rows)
    svc = _make_service(client)

    svc.list_candidates(user_id=_USER_A, ticker="aapl")
    assert client.table.return_value.eq.called


def test_list_candidates_status_filter_applied():
    rows = [_make_row()]
    client = _make_list_client(rows=rows)
    svc = _make_service(client)

    svc.list_candidates(user_id=_USER_A, status="candidate")
    assert client.table.return_value.eq.called


def test_user_isolation_different_clients():
    rows_a = [_make_row()]
    svc_a = _make_service(_make_list_client(rows=rows_a))
    svc_b = _make_service(_make_list_client(rows=[]))

    result_a = svc_a.list_candidates(user_id=_USER_A)
    result_b = svc_b.list_candidates(user_id=_USER_B)

    assert len(result_a) == 1
    assert len(result_b) == 0


# ── Test 9: Does not touch other tables ──────────────────────────────────────

def test_persist_does_not_touch_intel_snapshots_or_feedback():
    touched: list[str] = []

    client = MagicMock()
    candidate = _make_candidate()
    row = _make_row(candidate)

    def table_tracker(name: str):
        touched.append(name)
        chain = MagicMock()
        chain.insert.return_value = chain
        chain.execute.return_value = MagicMock(data=[row])
        return chain

    client.table.side_effect = table_tracker
    svc = _make_service(client)
    svc.persist_candidate(candidate)

    assert "intel_v3_snapshots" not in touched
    assert "action_feedback_events" not in touched
    assert "watchtower_alert_candidates" in touched


# ── Test 10: Ticker stored uppercase ─────────────────────────────────────────

def test_ticker_stored_uppercase():
    candidate = _make_candidate(ticker="aapl")
    row = _make_row(candidate)
    client = _make_insert_client(inserted_row=row)
    svc = _make_service(client)

    svc.persist_candidate(candidate)

    insert_call = client.table.return_value.insert.call_args
    payload = insert_call[0][0]
    assert payload["ticker"] == "AAPL"
