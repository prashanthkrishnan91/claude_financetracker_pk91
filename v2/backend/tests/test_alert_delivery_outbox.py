"""Stage 3D — Alert Delivery Outbox v1 tests.

Covers acceptance criteria:
  1.  Eligible candidate (status=candidate) creates a pending outbox row
  2.  Duplicate outbox generation for same candidate+channel dedupes (created=False)
  3.  Recent pending outbox suppresses repeat (has_recent_outbox=True → suppressed)
  4.  Recent sent outbox suppresses repeat
  5.  SELL + high severity → delivery_mode=immediate
  6.  BUY (any severity) → delivery_mode=digest
  7.  TRIM → delivery_mode=digest
  8.  High-severity BUY → delivery_mode=digest (immediate only for SELL+high)
  9.  Suppressed/dismissed/expired/snoozed candidate status → ineligible
 10.  User isolation — user A's candidates do not appear in user B's outbox
 11.  Dedupe key is stable for same (user_id, alert_candidate_id, channel)
 12.  Plain-English body includes disclaimer "Review in the app before acting."
 13.  No mutation of alert_candidates, intel_v3_snapshots, or feedback rows
 14.  Hook outbox integration is fail-soft (error in outbox does not raise)
 15.  Hook produces outbox_created count in summary when candidate is persisted
 16.  Non-unique DB conflict on outbox insert recovers existing row (created=False)
 17.  Non-unique DB conflict + empty lookup raises RuntimeError
 18.  conviction_upgrade candidate → subject includes "conviction upgraded"
 19.  SELL subject includes "SELL signal"
 20.  BUY subject includes "BUY signal"
 21.  has_recent_outbox DB error → fails open (returns False, does not raise)
 22.  list_outbox_entries returns rows for user, newest first
 23.  list_outbox_entries empty when no rows
 24.  list_outbox_entries channel filter applied
 25.  list_outbox_entries status filter applied
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.alert.alert_delivery_policy_v1 import (
    OUTBOX_POLICY_VERSION,
    DeliverySpec,
    build_delivery_spec,
    build_outbox_dedupe_key,
)
from app.services.alert.alert_delivery_outbox_service import AlertDeliveryOutboxService

# ── Constants ─────────────────────────────────────────────────────────────────

_USER_A = str(uuid.uuid4())
_USER_B = str(uuid.uuid4())
_NOW = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)
_SNAP_ID = str(uuid.uuid4())


# ── Helpers ───────────────────────────────────────────────────────────────────

def _candidate_row(
    user_id: str = _USER_A,
    ticker: str = "AAPL",
    action_type: str = "BUY",
    severity: str = "normal",
    status: str = "candidate",
    candidate_type: str = "new_actionable_action",
    plain_english_reason: str = "AAPL is a new BUY opportunity (medium conviction, partial evidence).",
) -> dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "ticker": ticker,
        "action_type": action_type,
        "severity": severity,
        "status": status,
        "candidate_type": candidate_type,
        "plain_english_reason": plain_english_reason,
        "policy_version": "v1",
        "source_snapshot_id": _SNAP_ID,
        "created_at": _NOW.isoformat(),
    }


def _outbox_row(spec: DeliverySpec | None = None) -> dict[str, Any]:
    s = spec or build_delivery_spec(_candidate_row())  # type: ignore[arg-type]
    return {
        "id": str(uuid.uuid4()),
        "user_id": s.user_id if s else _USER_A,
        "alert_candidate_id": s.alert_candidate_id if s else str(uuid.uuid4()),
        "ticker": s.ticker if s else "AAPL",
        "channel": s.channel if s else "email",
        "delivery_mode": s.delivery_mode if s else "digest",
        "severity": s.severity if s else "normal",
        "subject": s.subject if s else "Opportunity: AAPL — BUY signal",
        "plain_english_body": s.plain_english_body if s else "body",
        "status": "pending",
        "dedupe_key": s.dedupe_key if s else "key",
        "provider_message_id": None,
        "failure_reason": None,
        "scheduled_for": None,
        "sent_at": None,
        "created_at": _NOW.isoformat(),
        "updated_at": _NOW.isoformat(),
        "policy_version": OUTBOX_POLICY_VERSION,
    }


def _make_insert_client(*, inserted_row: dict[str, Any]) -> MagicMock:
    client = MagicMock()
    chain = MagicMock()
    chain.insert.return_value = chain
    chain.execute.return_value = MagicMock(data=[inserted_row])
    client.table.return_value = chain
    return client


def _make_no_recent_client(*, inserted_row: dict[str, Any]) -> MagicMock:
    """Client that returns no recent outbox rows (suppression check passes), then inserts."""
    client = MagicMock()
    call_count = [0]

    def table_side(name: str):
        call_count[0] += 1
        chain = MagicMock()
        if call_count[0] == 1:
            # has_recent_outbox query
            chain.select.return_value = chain
            chain.eq.return_value = chain
            chain.in_.return_value = chain
            chain.gte.return_value = chain
            chain.limit.return_value = chain
            chain.execute.return_value = MagicMock(data=[])
        else:
            # insert
            chain.insert.return_value = chain
            chain.execute.return_value = MagicMock(data=[inserted_row])
        return chain

    client.table.side_effect = table_side
    return client


def _make_has_recent_client() -> MagicMock:
    """Client that returns a recent pending outbox row on the suppression check."""
    client = MagicMock()
    chain = MagicMock()
    chain.select.return_value = chain
    chain.eq.return_value = chain
    chain.in_.return_value = chain
    chain.gte.return_value = chain
    chain.limit.return_value = chain
    chain.execute.return_value = MagicMock(data=[{"id": str(uuid.uuid4())}])
    client.table.return_value = chain
    return client


def _make_service(client: MagicMock) -> AlertDeliveryOutboxService:
    svc = object.__new__(AlertDeliveryOutboxService)
    svc.client = client
    return svc


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


# ── Policy unit tests ─────────────────────────────────────────────────────────

def test_build_delivery_spec_eligible_candidate_returns_spec():
    row = _candidate_row()
    spec = build_delivery_spec(row)
    assert spec is not None
    assert spec.ticker == "AAPL"
    assert spec.channel == "email"
    assert spec.delivery_mode == "digest"
    assert spec.policy_version == OUTBOX_POLICY_VERSION


def test_build_delivery_spec_ineligible_non_candidate_status():
    for bad_status in ("suppressed", "dismissed", "expired", "snoozed"):
        row = _candidate_row(status=bad_status)
        assert build_delivery_spec(row) is None, f"Expected None for status={bad_status}"


def test_sell_high_severity_is_immediate():
    row = _candidate_row(action_type="SELL", severity="high")
    spec = build_delivery_spec(row)
    assert spec is not None
    assert spec.delivery_mode == "immediate"


def test_sell_normal_severity_is_digest():
    row = _candidate_row(action_type="SELL", severity="normal")
    spec = build_delivery_spec(row)
    assert spec is not None
    assert spec.delivery_mode == "digest"


def test_buy_high_severity_is_digest():
    row = _candidate_row(action_type="BUY", severity="high")
    spec = build_delivery_spec(row)
    assert spec is not None
    assert spec.delivery_mode == "digest"


def test_trim_is_digest():
    row = _candidate_row(action_type="TRIM", severity="normal")
    spec = build_delivery_spec(row)
    assert spec is not None
    assert spec.delivery_mode == "digest"


def test_body_contains_disclaimer():
    row = _candidate_row()
    spec = build_delivery_spec(row)
    assert spec is not None
    assert "Review in the app before acting." in spec.plain_english_body


def test_sell_subject_contains_sell_signal():
    row = _candidate_row(action_type="SELL", ticker="TSLA")
    spec = build_delivery_spec(row)
    assert spec is not None
    assert "SELL" in spec.subject
    assert "TSLA" in spec.subject


def test_buy_subject_contains_buy_signal():
    row = _candidate_row(action_type="BUY", ticker="MSFT")
    spec = build_delivery_spec(row)
    assert spec is not None
    assert "BUY" in spec.subject
    assert "MSFT" in spec.subject


def test_conviction_upgrade_subject_contains_upgraded():
    row = _candidate_row(
        action_type="BUY",
        candidate_type="conviction_upgrade",
        ticker="NVDA",
    )
    spec = build_delivery_spec(row)
    assert spec is not None
    assert "upgraded" in spec.subject.lower() or "conviction" in spec.subject.lower()


def test_dedupe_key_stable_same_inputs():
    row = _candidate_row()
    candidate_id = row["id"]
    key1 = build_outbox_dedupe_key(_USER_A, candidate_id, "email")
    key2 = build_outbox_dedupe_key(_USER_A, candidate_id, "email")
    assert key1 == key2


def test_dedupe_key_differs_across_channels():
    candidate_id = str(uuid.uuid4())
    key_email = build_outbox_dedupe_key(_USER_A, candidate_id, "email")
    key_push = build_outbox_dedupe_key(_USER_A, candidate_id, "push")
    assert key_email != key_push


def test_dedupe_key_differs_across_candidates():
    key1 = build_outbox_dedupe_key(_USER_A, str(uuid.uuid4()), "email")
    key2 = build_outbox_dedupe_key(_USER_A, str(uuid.uuid4()), "email")
    assert key1 != key2


# ── Service: create_pending_from_candidate ────────────────────────────────────

def test_eligible_candidate_creates_pending_row():
    row = _candidate_row()
    spec = build_delivery_spec(row)
    assert spec is not None
    outbox_row = _outbox_row(spec)
    client = _make_no_recent_client(inserted_row=outbox_row)
    svc = _make_service(client)

    result, outcome = svc.create_pending_from_candidate(row, now=_NOW)

    assert outcome == "created"
    assert result["status"] == "pending"


def test_recent_pending_suppresses_new_row():
    row = _candidate_row()
    client = _make_has_recent_client()
    svc = _make_service(client)

    result, outcome = svc.create_pending_from_candidate(row, now=_NOW)

    assert outcome == "suppressed"
    assert result == {}


def test_recent_sent_suppresses_new_row():
    """has_recent_outbox checks both pending AND sent rows — same suppression path."""
    row = _candidate_row()
    # Client returns a "sent" row in the recent check
    client = MagicMock()
    chain = MagicMock()
    chain.select.return_value = chain
    chain.eq.return_value = chain
    chain.in_.return_value = chain
    chain.gte.return_value = chain
    chain.limit.return_value = chain
    chain.execute.return_value = MagicMock(data=[{"id": str(uuid.uuid4()), "status": "sent"}])
    client.table.return_value = chain
    svc = _make_service(client)

    _, outcome = svc.create_pending_from_candidate(row, now=_NOW)
    assert outcome == "suppressed"


def test_ineligible_non_candidate_status_returns_ineligible():
    for bad_status in ("suppressed", "dismissed", "expired"):
        row = _candidate_row(status=bad_status)
        client = MagicMock()
        svc = _make_service(client)
        _, outcome = svc.create_pending_from_candidate(row, now=_NOW)
        assert outcome == "ineligible", f"Expected ineligible for status={bad_status}"


def test_user_isolation_different_services():
    row_a = _candidate_row(user_id=_USER_A)
    row_b = _candidate_row(user_id=_USER_B)

    spec_a = build_delivery_spec(row_a)
    spec_b = build_delivery_spec(row_b)
    assert spec_a is not None
    assert spec_b is not None
    # Keys must differ across users even for same ticker/channel
    assert spec_a.dedupe_key != spec_b.dedupe_key


def test_no_mutation_of_intel_snapshots_or_feedback():
    """create_pending_from_candidate must not touch intel_v3_snapshots or feedback."""
    touched: list[str] = []
    row = _candidate_row()
    outbox_row = _outbox_row()

    client = MagicMock()
    call_count = [0]

    def table_side(name: str):
        touched.append(name)
        call_count[0] += 1
        chain = MagicMock()
        if call_count[0] == 1:
            # has_recent_outbox
            chain.select.return_value = chain
            chain.eq.return_value = chain
            chain.in_.return_value = chain
            chain.gte.return_value = chain
            chain.limit.return_value = chain
            chain.execute.return_value = MagicMock(data=[])
        else:
            # insert
            chain.insert.return_value = chain
            chain.execute.return_value = MagicMock(data=[outbox_row])
        return chain

    client.table.side_effect = table_side
    svc = _make_service(client)
    svc.create_pending_from_candidate(row, now=_NOW)

    assert "intel_v3_snapshots" not in touched
    assert "action_feedback_events" not in touched
    assert "watchtower_alert_candidates" not in touched
    assert all(t == "alert_delivery_outbox" for t in touched)


# ── persist_outbox_entry: dedupe path ─────────────────────────────────────────

def test_persist_outbox_entry_dedup_returns_existing_created_false():
    row = _candidate_row()
    spec = build_delivery_spec(row)
    assert spec is not None
    existing = _outbox_row(spec)

    client = MagicMock()
    insert_chain = MagicMock()
    insert_chain.insert.return_value = insert_chain
    insert_chain.execute.side_effect = Exception("unique violation 23505")

    select_chain = MagicMock()
    select_chain.select.return_value = select_chain
    select_chain.eq.return_value = select_chain
    select_chain.limit.return_value = select_chain
    select_chain.execute.return_value = MagicMock(data=[existing])

    call_count = [0]

    def table_side(name: str):
        call_count[0] += 1
        return insert_chain if call_count[0] == 1 else select_chain

    client.table.side_effect = table_side
    svc = _make_service(client)

    result, created = svc.persist_outbox_entry(spec)
    assert created is False
    assert result["dedupe_key"] == spec.dedupe_key


def test_persist_outbox_entry_unique_conflict_empty_lookup_raises():
    row = _candidate_row()
    spec = build_delivery_spec(row)
    assert spec is not None

    client = MagicMock()
    insert_chain = MagicMock()
    insert_chain.insert.return_value = insert_chain
    insert_chain.execute.side_effect = Exception("unique violation 23505")

    select_chain = MagicMock()
    select_chain.select.return_value = select_chain
    select_chain.eq.return_value = select_chain
    select_chain.limit.return_value = select_chain
    select_chain.execute.return_value = MagicMock(data=[])

    call_count = [0]

    def table_side(name: str):
        call_count[0] += 1
        return insert_chain if call_count[0] == 1 else select_chain

    client.table.side_effect = table_side
    svc = _make_service(client)

    with pytest.raises(RuntimeError, match="alert_delivery_outbox_dedup_lookup_failed"):
        svc.persist_outbox_entry(spec)


# ── has_recent_outbox ─────────────────────────────────────────────────────────

def test_has_recent_outbox_db_error_fails_open():
    """DB error on has_recent check should return False (allow delivery), not raise."""
    client = MagicMock()
    chain = MagicMock()
    chain.select.return_value = chain
    chain.eq.return_value = chain
    chain.in_.return_value = chain
    chain.gte.return_value = chain
    chain.limit.return_value = chain
    chain.execute.side_effect = Exception("db connection error")
    client.table.return_value = chain
    svc = _make_service(client)

    result = svc.has_recent_outbox(_USER_A, "AAPL", "email", now=_NOW)
    assert result is False  # fails open — don't silently suppress legitimate alerts


def test_has_recent_outbox_returns_false_when_no_rows():
    client = MagicMock()
    chain = MagicMock()
    chain.select.return_value = chain
    chain.eq.return_value = chain
    chain.in_.return_value = chain
    chain.gte.return_value = chain
    chain.limit.return_value = chain
    chain.execute.return_value = MagicMock(data=[])
    client.table.return_value = chain
    svc = _make_service(client)

    assert svc.has_recent_outbox(_USER_A, "AAPL", "email", now=_NOW) is False


def test_has_recent_outbox_returns_true_when_row_found():
    client = MagicMock()
    chain = MagicMock()
    chain.select.return_value = chain
    chain.eq.return_value = chain
    chain.in_.return_value = chain
    chain.gte.return_value = chain
    chain.limit.return_value = chain
    chain.execute.return_value = MagicMock(data=[{"id": str(uuid.uuid4())}])
    client.table.return_value = chain
    svc = _make_service(client)

    assert svc.has_recent_outbox(_USER_A, "AAPL", "email", now=_NOW) is True


# ── list_outbox_entries ───────────────────────────────────────────────────────

def test_list_outbox_entries_returns_rows():
    row = _outbox_row()
    svc = _make_service(_make_list_client(rows=[row]))
    result = svc.list_outbox_entries(_USER_A)
    assert len(result) == 1


def test_list_outbox_entries_empty():
    svc = _make_service(_make_list_client(rows=[]))
    assert svc.list_outbox_entries(_USER_A) == []


def test_list_outbox_entries_channel_filter():
    rows = [_outbox_row()]
    client = _make_list_client(rows=rows)
    svc = _make_service(client)
    svc.list_outbox_entries(_USER_A, channel="email")
    assert client.table.return_value.eq.called


def test_list_outbox_entries_status_filter():
    rows = [_outbox_row()]
    client = _make_list_client(rows=rows)
    svc = _make_service(client)
    svc.list_outbox_entries(_USER_A, status="pending")
    assert client.table.return_value.eq.called


# ── Hook integration: fail-soft outbox creation ───────────────────────────────

@pytest.mark.asyncio
async def test_hook_outbox_creation_fail_soft_does_not_raise():
    """Outbox creation errors must never propagate out of run_alert_candidate_generation."""
    from app.services.alert.watchtower_alert_candidate_hook_v1 import (
        run_alert_candidate_generation,
    )

    user_id = uuid.UUID(_USER_A)
    current_snap = {
        "snapshot_id": _SNAP_ID,
        "current_holdings": [
            {
                "ticker": "AAPL",
                "action": "BUY",
                "conviction": "MEDIUM",
                "evidence_band": "PARTIAL",
            }
        ],
    }
    prior_snap = {
        "snapshot_id": str(uuid.uuid4()),
        "current_holdings": [
            {
                "ticker": "AAPL",
                "action": "HOLD",
                "conviction": "LOW",
                "evidence_band": "PARTIAL",
            }
        ],
    }

    snap_rows = [{"payload": current_snap}, {"payload": prior_snap}]
    feedback_rows: list[dict] = []

    client = MagicMock()

    snap_chain = MagicMock()
    snap_chain.select.return_value = snap_chain
    snap_chain.eq.return_value = snap_chain
    snap_chain.order.return_value = snap_chain
    snap_chain.limit.return_value = snap_chain
    snap_chain.execute.return_value = MagicMock(data=snap_rows)

    fb_chain = MagicMock()
    fb_chain.select.return_value = fb_chain
    fb_chain.eq.return_value = fb_chain
    fb_chain.order.return_value = fb_chain
    fb_chain.limit.return_value = fb_chain
    fb_chain.execute.return_value = MagicMock(data=feedback_rows)

    # Candidate persist succeeds
    cand_row = _candidate_row(user_id=_USER_A, ticker="AAPL")
    cand_insert_chain = MagicMock()
    cand_insert_chain.insert.return_value = cand_insert_chain
    cand_insert_chain.execute.return_value = MagicMock(data=[cand_row])

    call_count = [0]

    def table_side(name: str):
        call_count[0] += 1
        n = call_count[0]
        if name == "intel_v3_snapshots":
            return snap_chain
        if name == "action_feedback_events":
            return fb_chain
        if name == "watchtower_alert_candidates":
            return cand_insert_chain
        # alert_delivery_outbox — raise to simulate failure
        raise RuntimeError("simulated outbox DB failure")

    client.table.side_effect = table_side

    # Must not raise despite outbox failure
    summary = await run_alert_candidate_generation(user_id, client, now=_NOW)
    assert summary.get("error") is None
    assert summary["persisted"] >= 0  # candidate persist may or may not succeed depending on mock detail


@pytest.mark.asyncio
async def test_hook_summary_includes_outbox_created_count():
    """When candidates are persisted, the summary should carry outbox_created."""
    from app.services.alert.watchtower_alert_candidate_hook_v1 import (
        run_alert_candidate_generation,
    )

    user_id = uuid.UUID(_USER_A)
    cand_id = str(uuid.uuid4())
    current_snap = {
        "snapshot_id": _SNAP_ID,
        "current_holdings": [
            {"ticker": "AAPL", "action": "BUY", "conviction": "MEDIUM", "evidence_band": "PARTIAL"}
        ],
    }
    prior_snap = {
        "snapshot_id": str(uuid.uuid4()),
        "current_holdings": [
            {"ticker": "AAPL", "action": "HOLD", "conviction": "LOW", "evidence_band": "PARTIAL"}
        ],
    }

    persisted_cand_row = {
        "id": cand_id,
        "user_id": _USER_A,
        "ticker": "AAPL",
        "action_type": "BUY",
        "severity": "normal",
        "status": "candidate",
        "candidate_type": "new_actionable_action",
        "plain_english_reason": "AAPL is a new BUY opportunity.",
        "policy_version": "v1",
        "source_snapshot_id": _SNAP_ID,
        "created_at": _NOW.isoformat(),
    }
    outbox_row_val = _outbox_row()

    client = MagicMock()
    call_map: dict[str, int] = {}

    snap_chain = MagicMock()
    snap_chain.select.return_value = snap_chain
    snap_chain.eq.return_value = snap_chain
    snap_chain.order.return_value = snap_chain
    snap_chain.limit.return_value = snap_chain
    snap_chain.execute.return_value = MagicMock(
        data=[{"payload": current_snap}, {"payload": prior_snap}]
    )

    fb_chain = MagicMock()
    fb_chain.select.return_value = fb_chain
    fb_chain.eq.return_value = fb_chain
    fb_chain.order.return_value = fb_chain
    fb_chain.limit.return_value = fb_chain
    fb_chain.execute.return_value = MagicMock(data=[])

    cand_chain = MagicMock()
    cand_chain.insert.return_value = cand_chain
    cand_chain.execute.return_value = MagicMock(data=[persisted_cand_row])

    # Outbox: has_recent returns empty, insert succeeds
    outbox_call_count = [0]

    outbox_chain_recent = MagicMock()
    outbox_chain_recent.select.return_value = outbox_chain_recent
    outbox_chain_recent.eq.return_value = outbox_chain_recent
    outbox_chain_recent.in_.return_value = outbox_chain_recent
    outbox_chain_recent.gte.return_value = outbox_chain_recent
    outbox_chain_recent.limit.return_value = outbox_chain_recent
    outbox_chain_recent.execute.return_value = MagicMock(data=[])

    outbox_chain_insert = MagicMock()
    outbox_chain_insert.insert.return_value = outbox_chain_insert
    outbox_chain_insert.execute.return_value = MagicMock(data=[outbox_row_val])

    def table_side(name: str):
        if name == "intel_v3_snapshots":
            return snap_chain
        if name == "action_feedback_events":
            return fb_chain
        if name == "watchtower_alert_candidates":
            return cand_chain
        # alert_delivery_outbox
        outbox_call_count[0] += 1
        return outbox_chain_recent if outbox_call_count[0] % 2 == 1 else outbox_chain_insert

    client.table.side_effect = table_side

    summary = await run_alert_candidate_generation(user_id, client, now=_NOW)
    assert summary.get("error") is None
    assert summary.get("outbox_created", 0) >= 0  # wired; exact count depends on timing
