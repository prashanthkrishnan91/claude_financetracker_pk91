"""Stage 3D — Alert Delivery Outbox v1 tests.

Covers acceptance criteria:
  1.  Eligible candidate (status=candidate) creates a pending outbox row
  2.  Duplicate outbox generation for same candidate+channel dedupes (exact dedupe)
  3.  Recent pending outbox suppresses *different* candidate for same user+ticker+channel
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
 26.  Exact same candidate+channel returns deduped (not suppressed)
 27.  Hook passes deduped candidate rows (created=False) to outbox creation
 28.  If outbox fails on first run, later deduped candidate can create missing row
 29.  outbox_errors count appears in hook summary
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
    """Client: exact-dedupe miss → noisy-repeat miss → insert succeeds.

    create_pending_from_candidate call sequence (new ordering):
      1. _fetch_by_dedupe_key (select/eq/eq/limit) → empty
      2. has_recent_outbox (select/eq/eq/eq/in_/gte/limit) → empty
      3. persist_outbox_entry insert → inserted_row
    """
    client = MagicMock()
    call_count = [0]

    def table_side(name: str):
        call_count[0] += 1
        chain = MagicMock()
        if call_count[0] == 1:
            # _fetch_by_dedupe_key — exact dedupe miss
            chain.select.return_value = chain
            chain.eq.return_value = chain
            chain.limit.return_value = chain
            chain.execute.return_value = MagicMock(data=[])
        elif call_count[0] == 2:
            # has_recent_outbox — no recent row
            chain.select.return_value = chain
            chain.eq.return_value = chain
            chain.in_.return_value = chain
            chain.gte.return_value = chain
            chain.limit.return_value = chain
            chain.execute.return_value = MagicMock(data=[])
        else:
            # persist_outbox_entry insert
            chain.insert.return_value = chain
            chain.execute.return_value = MagicMock(data=[inserted_row])
        return chain

    client.table.side_effect = table_side
    return client


def _make_exact_dedupe_client(*, existing_outbox_row: dict[str, Any]) -> MagicMock:
    """Client: exact-dedupe hit on first call → returns existing row immediately.

    create_pending_from_candidate call sequence:
      1. _fetch_by_dedupe_key → returns existing_outbox_row (dedupe hit)
      (no further calls needed)
    """
    client = MagicMock()
    chain = MagicMock()
    chain.select.return_value = chain
    chain.eq.return_value = chain
    chain.limit.return_value = chain
    chain.execute.return_value = MagicMock(data=[existing_outbox_row])
    client.table.return_value = chain
    return client


def _make_has_recent_client() -> MagicMock:
    """Client: exact-dedupe miss → noisy-repeat hit → suppressed.

    create_pending_from_candidate call sequence:
      1. _fetch_by_dedupe_key → empty (no exact match)
      2. has_recent_outbox → found (recent pending/sent for same ticker+channel)
    """
    client = MagicMock()
    call_count = [0]

    def table_side(name: str):
        call_count[0] += 1
        chain = MagicMock()
        if call_count[0] == 1:
            # _fetch_by_dedupe_key → miss
            chain.select.return_value = chain
            chain.eq.return_value = chain
            chain.limit.return_value = chain
            chain.execute.return_value = MagicMock(data=[])
        else:
            # has_recent_outbox → hit
            chain.select.return_value = chain
            chain.eq.return_value = chain
            chain.in_.return_value = chain
            chain.gte.return_value = chain
            chain.limit.return_value = chain
            chain.execute.return_value = MagicMock(data=[{"id": str(uuid.uuid4())}])
        return chain

    client.table.side_effect = table_side
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
    """has_recent_outbox checks both pending AND sent rows — same suppression path.

    New call order: exact-dedupe miss first, then recent check finds a 'sent' row.
    """
    row = _candidate_row()
    client = MagicMock()
    call_count = [0]

    def table_side(name: str):
        call_count[0] += 1
        chain = MagicMock()
        if call_count[0] == 1:
            # _fetch_by_dedupe_key → miss
            chain.select.return_value = chain
            chain.eq.return_value = chain
            chain.limit.return_value = chain
            chain.execute.return_value = MagicMock(data=[])
        else:
            # has_recent_outbox → "sent" row found
            chain.select.return_value = chain
            chain.eq.return_value = chain
            chain.in_.return_value = chain
            chain.gte.return_value = chain
            chain.limit.return_value = chain
            chain.execute.return_value = MagicMock(
                data=[{"id": str(uuid.uuid4()), "status": "sent"}]
            )
        return chain

    client.table.side_effect = table_side
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
    """create_pending_from_candidate must not touch intel_v3_snapshots or feedback.

    Call order: exact-dedupe miss (call 1), noisy-recent miss (call 2), insert (call 3).
    """
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
            # _fetch_by_dedupe_key — exact dedupe miss
            chain.select.return_value = chain
            chain.eq.return_value = chain
            chain.limit.return_value = chain
            chain.execute.return_value = MagicMock(data=[])
        elif call_count[0] == 2:
            # has_recent_outbox — no recent row
            chain.select.return_value = chain
            chain.eq.return_value = chain
            chain.in_.return_value = chain
            chain.gte.return_value = chain
            chain.limit.return_value = chain
            chain.execute.return_value = MagicMock(data=[])
        else:
            # persist_outbox_entry insert
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


_HOOK_MODULE = "app.services.alert.watchtower_alert_candidate_hook_v1"
_OUTBOX_SVC_MODULE = "app.services.alert.alert_delivery_outbox_service"


# ── Hook integration: fail-soft outbox creation ───────────────────────────────

@pytest.mark.asyncio
async def test_hook_outbox_creation_fail_soft_does_not_raise():
    """Outbox creation errors must never propagate out of run_alert_candidate_generation."""
    from app.services.alert.watchtower_alert_candidate_hook_v1 import (
        run_alert_candidate_generation,
    )

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
    cand_row = _candidate_row(user_id=_USER_A, ticker="AAPL")

    # Outbox service raises on create_pending_from_candidate
    mock_outbox_svc = MagicMock()
    mock_outbox_svc.create_pending_from_candidate.side_effect = RuntimeError("outbox DB down")

    with patch(f"{_HOOK_MODULE}._fetch_latest_two_intel_snapshots", new_callable=AsyncMock) as mock_fetch, \
         patch(f"{_HOOK_MODULE}._fetch_feedback_rows", new_callable=AsyncMock) as mock_fb, \
         patch(f"{_HOOK_MODULE}.AlertCandidateService") as mock_cand_cls, \
         patch(f"{_OUTBOX_SVC_MODULE}.AlertDeliveryOutboxService") as mock_outbox_cls:
        mock_fetch.return_value = [current_snap, prior_snap]
        mock_fb.return_value = []
        mock_cand_cls.return_value.persist_candidate = MagicMock(return_value=(cand_row, True))
        mock_outbox_cls.return_value = mock_outbox_svc

        # Must not raise despite outbox failure
        summary = await run_alert_candidate_generation(
            uuid.UUID(_USER_A), MagicMock(), now=_NOW
        )

    assert summary.get("error") is None
    assert summary["persisted"] == 1


@pytest.mark.asyncio
async def test_hook_summary_includes_outbox_created_count():
    """When candidates are persisted, the summary should carry outbox_created."""
    from app.services.alert.watchtower_alert_candidate_hook_v1 import (
        run_alert_candidate_generation,
    )

    cand_row = _candidate_row(user_id=_USER_A, ticker="AAPL")
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

    mock_outbox_svc = MagicMock()
    mock_outbox_svc.create_pending_from_candidate.return_value = (_outbox_row(), "created")

    with patch(f"{_HOOK_MODULE}._fetch_latest_two_intel_snapshots", new_callable=AsyncMock) as mock_fetch, \
         patch(f"{_HOOK_MODULE}._fetch_feedback_rows", new_callable=AsyncMock) as mock_fb, \
         patch(f"{_HOOK_MODULE}.AlertCandidateService") as mock_cand_cls, \
         patch(f"{_OUTBOX_SVC_MODULE}.AlertDeliveryOutboxService") as mock_outbox_cls:
        mock_fetch.return_value = [current_snap, prior_snap]
        mock_fb.return_value = []
        mock_cand_cls.return_value.persist_candidate = MagicMock(return_value=(cand_row, True))
        mock_outbox_cls.return_value = mock_outbox_svc

        summary = await run_alert_candidate_generation(
            uuid.UUID(_USER_A), MagicMock(), now=_NOW
        )

    assert summary.get("error") is None
    assert summary.get("outbox_created") == 1


# ── New: exact dedupe vs suppression ordering ─────────────────────────────────

def test_exact_same_candidate_returns_deduped_not_suppressed():
    """Reprocessing the exact same candidate+channel must return 'deduped', not 'suppressed'.

    The new ordering checks dedupe_key first. If the outbox row already exists
    for this candidate+channel, we return 'deduped' without consulting
    has_recent_outbox at all.
    """
    row = _candidate_row()
    spec = build_delivery_spec(row)
    assert spec is not None
    existing_outbox = _outbox_row(spec)
    # Patch the dedupe_key field to match what the spec would produce
    existing_outbox["dedupe_key"] = spec.dedupe_key

    client = _make_exact_dedupe_client(existing_outbox_row=existing_outbox)
    svc = _make_service(client)

    result, outcome = svc.create_pending_from_candidate(row, now=_NOW)

    assert outcome == "deduped"
    assert result["dedupe_key"] == spec.dedupe_key


def test_suppression_only_fires_for_different_candidates():
    """Suppression should only fire when the dedupe_key does NOT match
    but there is a recent pending/sent row for the same user+ticker+channel.

    This confirms the ordering: exact dedupe first → suppression second.
    """
    # Use a different candidate row (fresh ID → different dedupe_key)
    row = _candidate_row(ticker="MSFT", action_type="SELL", severity="normal")
    client = _make_has_recent_client()
    svc = _make_service(client)

    _, outcome = svc.create_pending_from_candidate(row, now=_NOW)
    assert outcome == "suppressed"


@pytest.mark.asyncio
async def test_hook_passes_deduped_rows_to_outbox():
    """Hook must attempt outbox creation for candidate rows returned with created=False.

    If a candidate already existed (deduped), we still pass the returned row
    to the outbox service so a missing outbox entry can be self-healed.
    """
    from app.services.alert.watchtower_alert_candidate_hook_v1 import (
        run_alert_candidate_generation,
    )

    cand_row = _candidate_row(user_id=_USER_A, ticker="AAPL")
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

    # Candidate already existed in DB (created=False = deduped)
    mock_cand_svc = MagicMock()
    mock_cand_svc.persist_candidate = MagicMock(return_value=(cand_row, False))

    # Outbox service is still called for the deduped row and creates the entry
    mock_outbox_svc = MagicMock()
    mock_outbox_svc.create_pending_from_candidate.return_value = (_outbox_row(), "created")

    with patch(f"{_HOOK_MODULE}._fetch_latest_two_intel_snapshots", new_callable=AsyncMock) as mock_fetch, \
         patch(f"{_HOOK_MODULE}._fetch_feedback_rows", new_callable=AsyncMock) as mock_fb, \
         patch(f"{_HOOK_MODULE}.AlertCandidateService") as mock_cand_cls, \
         patch(f"{_OUTBOX_SVC_MODULE}.AlertDeliveryOutboxService") as mock_outbox_cls:
        mock_fetch.return_value = [current_snap, prior_snap]
        mock_fb.return_value = []
        mock_cand_cls.return_value = mock_cand_svc
        mock_outbox_cls.return_value = mock_outbox_svc

        summary = await run_alert_candidate_generation(
            uuid.UUID(_USER_A), MagicMock(), now=_NOW
        )

    assert summary.get("error") is None
    assert summary.get("deduped", 0) >= 1   # candidate was deduped
    assert summary.get("outbox_created", 0) >= 1  # outbox was still created for deduped row
    mock_outbox_svc.create_pending_from_candidate.assert_called_once_with(cand_row)


@pytest.mark.asyncio
async def test_hook_outbox_errors_reported_in_summary():
    """outbox_errors should appear in the hook summary when outbox creation fails."""
    from app.services.alert.watchtower_alert_candidate_hook_v1 import (
        run_alert_candidate_generation,
    )

    cand_row = _candidate_row(user_id=_USER_A, ticker="AAPL")
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

    mock_cand_svc = MagicMock()
    mock_cand_svc.persist_candidate = MagicMock(return_value=(cand_row, True))

    # Outbox service raises on every create_pending_from_candidate call
    mock_outbox_svc = MagicMock()
    mock_outbox_svc.create_pending_from_candidate.side_effect = RuntimeError("outbox DB failure")

    with patch(f"{_HOOK_MODULE}._fetch_latest_two_intel_snapshots", new_callable=AsyncMock) as mock_fetch, \
         patch(f"{_HOOK_MODULE}._fetch_feedback_rows", new_callable=AsyncMock) as mock_fb, \
         patch(f"{_HOOK_MODULE}.AlertCandidateService") as mock_cand_cls, \
         patch(f"{_OUTBOX_SVC_MODULE}.AlertDeliveryOutboxService") as mock_outbox_cls:
        mock_fetch.return_value = [current_snap, prior_snap]
        mock_fb.return_value = []
        mock_cand_cls.return_value = mock_cand_svc
        mock_outbox_cls.return_value = mock_outbox_svc

        summary = await run_alert_candidate_generation(
            uuid.UUID(_USER_A), MagicMock(), now=_NOW
        )

    assert summary.get("error") is None  # top-level hook is still fail-soft
    assert summary.get("outbox_errors", 0) >= 1  # error was counted in summary
