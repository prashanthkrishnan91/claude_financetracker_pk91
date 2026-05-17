"""Stage 3C — Watchtower Alert Candidate Generation Hook v1 tests.

Covers:
  1.  Hook creates candidate when current snapshot changes HOLD→BUY (PARTIAL evidence)
  2.  Hook creates candidate when current snapshot changes HOLD→BUY (STRONG evidence)
  3.  No prior snapshot → 0 candidates, skipped_reason=no_prior_snapshot
  4.  No snapshot at all → 0 candidates, skipped_reason=no_snapshot
  5.  Empty current holdings → 0 candidates, skipped_reason=empty_current_holdings
  6.  Duplicate re-run for same snapshot dedupes (persist_candidate returns created=False)
  7.  Snoozed feedback suppresses candidate (within cooldown)
  8.  Ignored feedback suppresses candidate (within 7d)
  9.  Executed feedback suppresses candidate (indefinitely)
  10. THIN evidence band does not persist candidate (suppression logged)
  11. SUPPRESSED evidence band does not persist candidate
  12. Hook does not mutate input snapshot payloads
  13. Hook error (DB failure on snapshot fetch) is fail-soft: returns summary, no raise
  14. Hook error (AlertCandidateService.persist raises) is fail-soft: continues to next candidate
  15. compare_and_republish calls hook only on rebuilt_and_published, not on other statuses
  16. republish_after_analyst_eligibility calls hook only on rebuilt_and_published
  17. Hook call failure does not break compare_and_republish result
  18. build_default_alert_candidate_hook_callable returns a callable
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from app.services.alert.watchtower_alert_candidate_hook_v1 import (
    run_alert_candidate_generation,
)
from app.services.intelligence.v3.watchtower_intel_republisher_v1 import (
    PUBLISH_CERTIFICATION_BLOCKED,
    PUBLISH_CERTIFIED_CURRENT,
    PUBLISH_NO_SNAPSHOT_EXISTS,
    PUBLISH_REBUILT_AND_PUBLISHED,
    PUBLISH_SKIPPED_NO_NEW_EVIDENCE,
    compare_and_republish,
    republish_after_analyst_eligibility,
)

# ── Constants ─────────────────────────────────────────────────────────────────

_USER_A = str(uuid.uuid4())
_SNAP_ID = str(uuid.uuid4())
_NOW = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)

# Evidence bands
_PARTIAL = "PARTIAL"
_STRONG = "STRONG"
_THIN = "THIN"
_SUPPRESSED = "SUPPRESSED"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _card(
    ticker: str,
    action: str = "HOLD",
    conviction: str = "MEDIUM",
    band: str = _PARTIAL,
) -> dict:
    return {
        "ticker": ticker,
        "action": action,
        "conviction": conviction,
        "evidence_band": band,
    }


def _snap_payload(
    cards: list[dict],
    snapshot_id: str = _SNAP_ID,
) -> dict:
    return {
        "snapshot_id": snapshot_id,
        "snapshot_source": "worker_certified",
        "current_holdings": cards,
    }


def _feedback_row(
    user_id: str,
    ticker: str,
    fb_type: str,
    action_type: str = "BUY",
    created_at: Optional[datetime] = None,
    cooldown_until: Optional[datetime] = None,
) -> dict:
    ts = (created_at or _NOW).isoformat()
    row = {
        "user_id": user_id,
        "ticker": ticker,
        "feedback_type": fb_type,
        "action_type": action_type,
        "created_at": ts,
        "cooldown_until": cooldown_until.isoformat() if cooldown_until else None,
    }
    return row


def _make_mock_client() -> MagicMock:
    """Build a chainable MagicMock for the Supabase client (not used in most tests,
    which patch the helper functions directly)."""
    client = MagicMock()
    return client


# ── Hook unit tests (patch helpers) ──────────────────────────────────────────

_HOOK_MODULE = "app.services.alert.watchtower_alert_candidate_hook_v1"


@pytest.mark.asyncio
async def test_hook_creates_candidate_hold_to_buy_partial():
    """Hook creates a candidate when action transitions HOLD→BUY with PARTIAL evidence."""
    current_snap = _snap_payload([_card("AAPL", action="BUY", band=_PARTIAL)])
    prior_snap = _snap_payload([_card("AAPL", action="HOLD")], snapshot_id="prior-id")

    mock_persist = MagicMock(return_value=({"id": "row1"}, True))

    with patch(f"{_HOOK_MODULE}._fetch_latest_two_intel_snapshots", new_callable=AsyncMock) as mock_fetch, \
         patch(f"{_HOOK_MODULE}._fetch_feedback_rows", new_callable=AsyncMock) as mock_fb, \
         patch(f"{_HOOK_MODULE}.AlertCandidateService") as mock_svc_cls:
        mock_fetch.return_value = [current_snap, prior_snap]
        mock_fb.return_value = []
        mock_svc_cls.return_value.persist_candidate = mock_persist

        summary = await run_alert_candidate_generation(
            uuid.UUID(_USER_A), _make_mock_client(), now=_NOW
        )

    assert summary["evaluated"] == 1
    assert summary["candidates"] == 1
    assert summary["persisted"] == 1
    assert summary["deduped"] == 0
    assert summary["suppressions"] == 0
    assert summary["skipped_reason"] is None
    assert summary["error"] is None
    assert mock_persist.call_count == 1


@pytest.mark.asyncio
async def test_hook_creates_candidate_hold_to_buy_strong():
    """Hook creates a candidate when action transitions HOLD→BUY with STRONG evidence."""
    current_snap = _snap_payload([_card("MSFT", action="BUY", band=_STRONG, conviction="HIGH")])
    prior_snap = _snap_payload([_card("MSFT", action="HOLD")], snapshot_id="prior-id")

    mock_persist = MagicMock(return_value=({"id": "row2"}, True))

    with patch(f"{_HOOK_MODULE}._fetch_latest_two_intel_snapshots", new_callable=AsyncMock) as mock_fetch, \
         patch(f"{_HOOK_MODULE}._fetch_feedback_rows", new_callable=AsyncMock) as mock_fb, \
         patch(f"{_HOOK_MODULE}.AlertCandidateService") as mock_svc_cls:
        mock_fetch.return_value = [current_snap, prior_snap]
        mock_fb.return_value = []
        mock_svc_cls.return_value.persist_candidate = mock_persist

        summary = await run_alert_candidate_generation(
            uuid.UUID(_USER_A), _make_mock_client(), now=_NOW
        )

    assert summary["candidates"] == 1
    assert summary["persisted"] == 1
    assert summary["error"] is None


@pytest.mark.asyncio
async def test_hook_no_prior_snapshot_skips_candidates():
    """No prior snapshot → 0 candidates, skipped_reason=no_prior_snapshot."""
    current_snap = _snap_payload([_card("AAPL", action="BUY", band=_PARTIAL)])

    with patch(f"{_HOOK_MODULE}._fetch_latest_two_intel_snapshots", new_callable=AsyncMock) as mock_fetch, \
         patch(f"{_HOOK_MODULE}._fetch_feedback_rows", new_callable=AsyncMock) as mock_fb, \
         patch(f"{_HOOK_MODULE}.AlertCandidateService") as mock_svc_cls:
        mock_fetch.return_value = [current_snap]  # only one snapshot
        mock_fb.return_value = []
        mock_svc_cls.return_value.persist_candidate = MagicMock()

        summary = await run_alert_candidate_generation(
            uuid.UUID(_USER_A), _make_mock_client(), now=_NOW
        )

    assert summary["candidates"] == 0
    assert summary["persisted"] == 0
    assert summary["skipped_reason"] == "no_prior_snapshot"
    assert summary["error"] is None
    mock_svc_cls.return_value.persist_candidate.assert_not_called()


@pytest.mark.asyncio
async def test_hook_no_snapshot_returns_early():
    """No snapshot at all → 0 candidates, skipped_reason=no_snapshot."""
    with patch(f"{_HOOK_MODULE}._fetch_latest_two_intel_snapshots", new_callable=AsyncMock) as mock_fetch, \
         patch(f"{_HOOK_MODULE}.AlertCandidateService") as mock_svc_cls:
        mock_fetch.return_value = []
        mock_svc_cls.return_value.persist_candidate = MagicMock()

        summary = await run_alert_candidate_generation(
            uuid.UUID(_USER_A), _make_mock_client(), now=_NOW
        )

    assert summary["candidates"] == 0
    assert summary["skipped_reason"] == "no_snapshot"
    mock_svc_cls.return_value.persist_candidate.assert_not_called()


@pytest.mark.asyncio
async def test_hook_empty_current_holdings_returns_early():
    """Empty current_holdings → skipped_reason=empty_current_holdings."""
    current_snap = _snap_payload([])
    prior_snap = _snap_payload([_card("AAPL")], snapshot_id="prior-id")

    with patch(f"{_HOOK_MODULE}._fetch_latest_two_intel_snapshots", new_callable=AsyncMock) as mock_fetch, \
         patch(f"{_HOOK_MODULE}.AlertCandidateService") as mock_svc_cls:
        mock_fetch.return_value = [current_snap, prior_snap]
        mock_svc_cls.return_value.persist_candidate = MagicMock()

        summary = await run_alert_candidate_generation(
            uuid.UUID(_USER_A), _make_mock_client(), now=_NOW
        )

    assert summary["candidates"] == 0
    assert summary["skipped_reason"] == "empty_current_holdings"
    mock_svc_cls.return_value.persist_candidate.assert_not_called()


@pytest.mark.asyncio
async def test_hook_dedupes_same_snapshot():
    """Duplicate re-run for same snapshot dedupes (persist returns created=False)."""
    current_snap = _snap_payload([_card("AAPL", action="BUY", band=_PARTIAL)])
    prior_snap = _snap_payload([_card("AAPL", action="HOLD")], snapshot_id="prior-id")

    # persist_candidate returns created=False (deduped)
    mock_persist = MagicMock(return_value=({"id": "existing"}, False))

    with patch(f"{_HOOK_MODULE}._fetch_latest_two_intel_snapshots", new_callable=AsyncMock) as mock_fetch, \
         patch(f"{_HOOK_MODULE}._fetch_feedback_rows", new_callable=AsyncMock) as mock_fb, \
         patch(f"{_HOOK_MODULE}.AlertCandidateService") as mock_svc_cls:
        mock_fetch.return_value = [current_snap, prior_snap]
        mock_fb.return_value = []
        mock_svc_cls.return_value.persist_candidate = mock_persist

        summary = await run_alert_candidate_generation(
            uuid.UUID(_USER_A), _make_mock_client(), now=_NOW
        )

    assert summary["candidates"] == 1
    assert summary["persisted"] == 0
    assert summary["deduped"] == 1
    assert summary["error"] is None


@pytest.mark.asyncio
async def test_hook_snoozed_feedback_suppresses():
    """Snoozed feedback within cooldown suppresses candidate."""
    current_snap = _snap_payload([_card("AAPL", action="BUY", band=_PARTIAL)])
    prior_snap = _snap_payload([_card("AAPL", action="HOLD")], snapshot_id="prior-id")

    # Snoozed 5 days ago (within 14d default cooldown)
    snoozed_at = _NOW - timedelta(days=5)
    feedback = [_feedback_row(_USER_A, "AAPL", "snoozed", created_at=snoozed_at)]

    with patch(f"{_HOOK_MODULE}._fetch_latest_two_intel_snapshots", new_callable=AsyncMock) as mock_fetch, \
         patch(f"{_HOOK_MODULE}._fetch_feedback_rows", new_callable=AsyncMock) as mock_fb, \
         patch(f"{_HOOK_MODULE}.AlertCandidateService") as mock_svc_cls:
        mock_fetch.return_value = [current_snap, prior_snap]
        mock_fb.return_value = feedback
        mock_svc_cls.return_value.persist_candidate = MagicMock()

        summary = await run_alert_candidate_generation(
            uuid.UUID(_USER_A), _make_mock_client(), now=_NOW
        )

    assert summary["candidates"] == 0
    assert summary["suppressions"] == 1
    assert summary["persisted"] == 0
    mock_svc_cls.return_value.persist_candidate.assert_not_called()


@pytest.mark.asyncio
async def test_hook_ignored_feedback_suppresses():
    """Ignored feedback within 7d suppresses candidate."""
    current_snap = _snap_payload([_card("AAPL", action="BUY", band=_PARTIAL)])
    prior_snap = _snap_payload([_card("AAPL", action="HOLD")], snapshot_id="prior-id")

    feedback = [_feedback_row(_USER_A, "AAPL", "ignored", created_at=_NOW - timedelta(days=3))]

    with patch(f"{_HOOK_MODULE}._fetch_latest_two_intel_snapshots", new_callable=AsyncMock) as mock_fetch, \
         patch(f"{_HOOK_MODULE}._fetch_feedback_rows", new_callable=AsyncMock) as mock_fb, \
         patch(f"{_HOOK_MODULE}.AlertCandidateService") as mock_svc_cls:
        mock_fetch.return_value = [current_snap, prior_snap]
        mock_fb.return_value = feedback
        mock_svc_cls.return_value.persist_candidate = MagicMock()

        summary = await run_alert_candidate_generation(
            uuid.UUID(_USER_A), _make_mock_client(), now=_NOW
        )

    assert summary["candidates"] == 0
    assert summary["suppressions"] == 1


@pytest.mark.asyncio
async def test_hook_executed_feedback_suppresses():
    """Executed feedback suppresses indefinitely."""
    current_snap = _snap_payload([_card("AAPL", action="BUY", band=_STRONG)])
    prior_snap = _snap_payload([_card("AAPL", action="HOLD")], snapshot_id="prior-id")

    # Executed long ago — still suppresses
    feedback = [_feedback_row(_USER_A, "AAPL", "executed", created_at=_NOW - timedelta(days=60))]

    with patch(f"{_HOOK_MODULE}._fetch_latest_two_intel_snapshots", new_callable=AsyncMock) as mock_fetch, \
         patch(f"{_HOOK_MODULE}._fetch_feedback_rows", new_callable=AsyncMock) as mock_fb, \
         patch(f"{_HOOK_MODULE}.AlertCandidateService") as mock_svc_cls:
        mock_fetch.return_value = [current_snap, prior_snap]
        mock_fb.return_value = feedback
        mock_svc_cls.return_value.persist_candidate = MagicMock()

        summary = await run_alert_candidate_generation(
            uuid.UUID(_USER_A), _make_mock_client(), now=_NOW
        )

    assert summary["candidates"] == 0
    assert summary["suppressions"] == 1
    mock_svc_cls.return_value.persist_candidate.assert_not_called()


@pytest.mark.asyncio
async def test_hook_thin_evidence_does_not_persist():
    """THIN evidence band suppresses actionable candidate — no persistence."""
    current_snap = _snap_payload([_card("AAPL", action="BUY", band=_THIN)])
    prior_snap = _snap_payload([_card("AAPL", action="HOLD")], snapshot_id="prior-id")

    with patch(f"{_HOOK_MODULE}._fetch_latest_two_intel_snapshots", new_callable=AsyncMock) as mock_fetch, \
         patch(f"{_HOOK_MODULE}._fetch_feedback_rows", new_callable=AsyncMock) as mock_fb, \
         patch(f"{_HOOK_MODULE}.AlertCandidateService") as mock_svc_cls:
        mock_fetch.return_value = [current_snap, prior_snap]
        mock_fb.return_value = []
        mock_svc_cls.return_value.persist_candidate = MagicMock()

        summary = await run_alert_candidate_generation(
            uuid.UUID(_USER_A), _make_mock_client(), now=_NOW
        )

    assert summary["candidates"] == 0
    assert summary["suppressions"] == 1
    assert summary["persisted"] == 0
    mock_svc_cls.return_value.persist_candidate.assert_not_called()


@pytest.mark.asyncio
async def test_hook_suppressed_evidence_does_not_persist():
    """SUPPRESSED evidence band suppresses actionable candidate — no persistence."""
    current_snap = _snap_payload([_card("AAPL", action="BUY", band=_SUPPRESSED)])
    prior_snap = _snap_payload([_card("AAPL", action="HOLD")], snapshot_id="prior-id")

    with patch(f"{_HOOK_MODULE}._fetch_latest_two_intel_snapshots", new_callable=AsyncMock) as mock_fetch, \
         patch(f"{_HOOK_MODULE}._fetch_feedback_rows", new_callable=AsyncMock) as mock_fb, \
         patch(f"{_HOOK_MODULE}.AlertCandidateService") as mock_svc_cls:
        mock_fetch.return_value = [current_snap, prior_snap]
        mock_fb.return_value = []
        mock_svc_cls.return_value.persist_candidate = MagicMock()

        summary = await run_alert_candidate_generation(
            uuid.UUID(_USER_A), _make_mock_client(), now=_NOW
        )

    assert summary["candidates"] == 0
    assert summary["suppressions"] == 1
    mock_svc_cls.return_value.persist_candidate.assert_not_called()


@pytest.mark.asyncio
async def test_hook_does_not_mutate_input_payloads():
    """Hook must not mutate the input snapshot payloads."""
    import copy

    original_current_cards = [_card("AAPL", action="BUY", band=_PARTIAL)]
    original_prior_cards = [_card("AAPL", action="HOLD")]
    current_snap = _snap_payload(original_current_cards)
    prior_snap = _snap_payload(original_prior_cards, snapshot_id="prior-id")

    current_cards_before = copy.deepcopy(current_snap)
    prior_cards_before = copy.deepcopy(prior_snap)

    with patch(f"{_HOOK_MODULE}._fetch_latest_two_intel_snapshots", new_callable=AsyncMock) as mock_fetch, \
         patch(f"{_HOOK_MODULE}._fetch_feedback_rows", new_callable=AsyncMock) as mock_fb, \
         patch(f"{_HOOK_MODULE}.AlertCandidateService") as mock_svc_cls:
        mock_fetch.return_value = [current_snap, prior_snap]
        mock_fb.return_value = []
        mock_svc_cls.return_value.persist_candidate = MagicMock(return_value=({"id": "r"}, True))

        await run_alert_candidate_generation(uuid.UUID(_USER_A), _make_mock_client(), now=_NOW)

    assert current_snap == current_cards_before, "current_snap payload was mutated"
    assert prior_snap == prior_cards_before, "prior_snap payload was mutated"


@pytest.mark.asyncio
async def test_hook_db_failure_on_fetch_is_fail_soft():
    """DB failure during snapshot fetch returns summary with error, does not raise."""
    with patch(f"{_HOOK_MODULE}._fetch_latest_two_intel_snapshots", new_callable=AsyncMock) as mock_fetch, \
         patch(f"{_HOOK_MODULE}.AlertCandidateService") as mock_svc_cls:
        mock_fetch.side_effect = RuntimeError("simulated DB failure")
        mock_svc_cls.return_value.persist_candidate = MagicMock()

        # Must not raise — fail-soft contract
        summary = await run_alert_candidate_generation(
            uuid.UUID(_USER_A), _make_mock_client(), now=_NOW
        )

    assert summary["error"] is not None
    assert "simulated DB failure" in summary["error"]
    assert summary["candidates"] == 0


@pytest.mark.asyncio
async def test_hook_persist_error_is_fail_soft_continues():
    """If persist_candidate raises for one candidate, hook continues and does not raise."""
    current_snap = _snap_payload([
        _card("AAPL", action="BUY", band=_PARTIAL),
        _card("MSFT", action="BUY", band=_PARTIAL),
    ])
    prior_snap = _snap_payload([
        _card("AAPL", action="HOLD"),
        _card("MSFT", action="HOLD"),
    ], snapshot_id="prior-id")

    # First persist fails, second succeeds
    call_count = 0
    def _failing_persist(candidate):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("DB unavailable")
        return {"id": "row"}, True

    with patch(f"{_HOOK_MODULE}._fetch_latest_two_intel_snapshots", new_callable=AsyncMock) as mock_fetch, \
         patch(f"{_HOOK_MODULE}._fetch_feedback_rows", new_callable=AsyncMock) as mock_fb, \
         patch(f"{_HOOK_MODULE}.AlertCandidateService") as mock_svc_cls:
        mock_fetch.return_value = [current_snap, prior_snap]
        mock_fb.return_value = []
        mock_svc_cls.return_value.persist_candidate = _failing_persist

        summary = await run_alert_candidate_generation(
            uuid.UUID(_USER_A), _make_mock_client(), now=_NOW
        )

    assert summary["candidates"] == 2
    # One persisted, one failed but hook continued
    assert summary["persisted"] == 1
    assert summary["error"] is None  # outer error is None; per-candidate error is logged


# ── Republisher integration tests ─────────────────────────────────────────────

def _make_republisher_client(
    *,
    intel_generated_at: str,
    portfolio_snapshot_at: str,
    intel_mapping_version: str = "analyst_verdict_synthesis_v1",
) -> MagicMock:
    """Build a minimal client mock for compare_and_republish tests."""
    client = MagicMock()
    uid = str(uuid.uuid4())

    intel_payload = {
        "snapshot_id": _SNAP_ID,
        "generated_at": intel_generated_at,
        "snapshot_source": "worker_certified",
        "evidence_mapping_version": intel_mapping_version,
    }

    def _make_chain(return_data: list[dict]):
        chain = MagicMock()
        chain.select.return_value = chain
        chain.eq.return_value = chain
        chain.order.return_value = chain
        chain.limit.return_value = chain
        chain.execute.return_value = MagicMock(data=return_data)
        return chain

    intel_chain = _make_chain([{"payload": intel_payload}])
    portfolio_chain = _make_chain([{"id": uid, "snapshot_at": portfolio_snapshot_at}])

    def _table_router(table_name: str):
        if table_name == "intel_v3_snapshots":
            return intel_chain
        if table_name == "portfolio_snapshots":
            return portfolio_chain
        return MagicMock()

    client.table.side_effect = _table_router
    return client


@pytest.mark.asyncio
async def test_compare_and_republish_calls_hook_on_rebuilt():
    """compare_and_republish calls alert_candidate_hook_callable on rebuilt_and_published."""
    now = _NOW
    older = (now - timedelta(hours=2)).isoformat()
    newer = now.isoformat()

    client = _make_republisher_client(
        intel_generated_at=older,
        portfolio_snapshot_at=newer,
    )

    republish_callable = AsyncMock(return_value={"snapshot_source": "worker_certified"})
    hook_callable = AsyncMock(return_value={"persisted": 1})

    result = await compare_and_republish(
        uuid.UUID(_USER_A),
        client,
        intel_republish_callable=republish_callable,
        alert_candidate_hook_callable=hook_callable,
        now=now,
    )

    assert result.publish_status == PUBLISH_REBUILT_AND_PUBLISHED
    hook_callable.assert_awaited_once()


@pytest.mark.asyncio
async def test_compare_and_republish_does_not_call_hook_on_certified_current():
    """compare_and_republish does NOT call hook when publish_status=certified_current."""
    now = _NOW
    # Evidence is NOT newer than Intel snapshot
    snap_at = (now - timedelta(hours=2)).isoformat()

    client = _make_republisher_client(
        intel_generated_at=now.isoformat(),
        portfolio_snapshot_at=snap_at,
    )

    hook_callable = AsyncMock()

    result = await compare_and_republish(
        uuid.UUID(_USER_A),
        client,
        intel_republish_callable=AsyncMock(),
        alert_candidate_hook_callable=hook_callable,
        now=now,
    )

    assert result.publish_status == PUBLISH_CERTIFIED_CURRENT
    hook_callable.assert_not_awaited()


@pytest.mark.asyncio
async def test_compare_and_republish_does_not_call_hook_on_certification_blocked():
    """compare_and_republish does NOT call hook when certification fails."""
    now = _NOW
    older = (now - timedelta(hours=2)).isoformat()
    newer = now.isoformat()

    client = _make_republisher_client(
        intel_generated_at=older,
        portfolio_snapshot_at=newer,
    )

    # Callable returns certification_failed snapshot_source
    republish_callable = AsyncMock(return_value={"snapshot_source": "certification_failed"})
    hook_callable = AsyncMock()

    result = await compare_and_republish(
        uuid.UUID(_USER_A),
        client,
        intel_republish_callable=republish_callable,
        alert_candidate_hook_callable=hook_callable,
        now=now,
    )

    assert result.publish_status == PUBLISH_CERTIFICATION_BLOCKED
    hook_callable.assert_not_awaited()


@pytest.mark.asyncio
async def test_compare_and_republish_hook_failure_does_not_break_result():
    """Hook error does not break compare_and_republish — result still rebuilt_and_published."""
    now = _NOW
    older = (now - timedelta(hours=2)).isoformat()
    newer = now.isoformat()

    client = _make_republisher_client(
        intel_generated_at=older,
        portfolio_snapshot_at=newer,
    )

    republish_callable = AsyncMock(return_value={"snapshot_source": "worker_certified"})
    hook_callable = AsyncMock(side_effect=RuntimeError("hook exploded"))

    result = await compare_and_republish(
        uuid.UUID(_USER_A),
        client,
        intel_republish_callable=republish_callable,
        alert_candidate_hook_callable=hook_callable,
        now=now,
    )

    # Result is still rebuilt_and_published — hook failure is fail-soft
    assert result.publish_status == PUBLISH_REBUILT_AND_PUBLISHED
    assert result.error is None


@pytest.mark.asyncio
async def test_compare_and_republish_no_hook_callable_still_works():
    """compare_and_republish works fine with no hook callable (backward compat)."""
    now = _NOW
    older = (now - timedelta(hours=2)).isoformat()
    newer = now.isoformat()

    client = _make_republisher_client(
        intel_generated_at=older,
        portfolio_snapshot_at=newer,
    )

    republish_callable = AsyncMock(return_value={"snapshot_source": "worker_certified"})

    result = await compare_and_republish(
        uuid.UUID(_USER_A),
        client,
        intel_republish_callable=republish_callable,
        # No alert_candidate_hook_callable
        now=now,
    )

    assert result.publish_status == PUBLISH_REBUILT_AND_PUBLISHED


# ── republish_after_analyst_eligibility tests ─────────────────────────────────

def _make_analyst_eligibility_client(
    *,
    intel_generated_at: str,
    intel_mapping_version: str = "analyst_verdict_synthesis_v1",
) -> MagicMock:
    """Build a minimal client mock for republish_after_analyst_eligibility tests."""
    client = MagicMock()
    intel_payload = {
        "snapshot_id": _SNAP_ID,
        "generated_at": intel_generated_at,
        "snapshot_source": "worker_certified",
        "evidence_mapping_version": intel_mapping_version,
    }

    chain = MagicMock()
    chain.select.return_value = chain
    chain.eq.return_value = chain
    chain.order.return_value = chain
    chain.limit.return_value = chain
    chain.execute.return_value = MagicMock(data=[{"payload": intel_payload}])
    client.table.return_value = chain
    return client


@pytest.mark.asyncio
async def test_republish_after_analyst_eligibility_calls_hook_on_rebuilt():
    """republish_after_analyst_eligibility calls hook on rebuilt_and_published."""
    now = _NOW
    # Evidence newer than snapshot
    latest_evidence_at = now
    intel_generated_at = (now - timedelta(hours=2)).isoformat()

    client = _make_analyst_eligibility_client(intel_generated_at=intel_generated_at)
    republish_callable = AsyncMock(return_value={"snapshot_source": "worker_certified"})
    hook_callable = AsyncMock(return_value={"persisted": 1})

    result = await republish_after_analyst_eligibility(
        uuid.UUID(_USER_A),
        client,
        intel_republish_callable=republish_callable,
        alert_candidate_hook_callable=hook_callable,
        latest_evidence_at=latest_evidence_at,
        now=now,
    )

    assert result.publish_status == PUBLISH_REBUILT_AND_PUBLISHED
    hook_callable.assert_awaited_once()


@pytest.mark.asyncio
async def test_republish_after_analyst_eligibility_no_hook_on_skipped():
    """republish_after_analyst_eligibility does NOT call hook when skipped_no_new_evidence."""
    now = _NOW
    # Intel snapshot is NEWER than latest_evidence — no republish needed
    intel_generated_at = now.isoformat()
    latest_evidence_at = now - timedelta(hours=2)

    client = _make_analyst_eligibility_client(intel_generated_at=intel_generated_at)
    hook_callable = AsyncMock()

    result = await republish_after_analyst_eligibility(
        uuid.UUID(_USER_A),
        client,
        intel_republish_callable=AsyncMock(),
        alert_candidate_hook_callable=hook_callable,
        latest_evidence_at=latest_evidence_at,
        now=now,
    )

    assert result.publish_status == PUBLISH_SKIPPED_NO_NEW_EVIDENCE
    hook_callable.assert_not_awaited()


# ── Callable builder test ─────────────────────────────────────────────────────

def test_build_default_alert_candidate_hook_callable_returns_callable():
    """build_default_alert_candidate_hook_callable returns a callable."""
    from app.services.intelligence.v3.watchtower_callables_v1 import (
        build_default_alert_candidate_hook_callable,
    )

    client = MagicMock()
    hook = build_default_alert_candidate_hook_callable(client)

    assert callable(hook), "Expected a callable"
    # Returned callable should be a coroutine function
    assert asyncio.iscoroutinefunction(hook), "Expected a coroutine function"


# ── Worker integration test ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_worker_passes_hook_to_compare_and_republish():
    """WatchtowerBackgroundRefreshWorker passes alert_candidate_hook through to compare_and_republish."""
    from app.services.intelligence.v3.watchtower_background_refresh_worker_v1 import (
        WatchtowerBackgroundRefreshWorker,
    )

    hook_callable = AsyncMock(return_value={"persisted": 0})

    # Minimal worker with no real callables — just verify hook attr is stored
    client = MagicMock()
    worker = WatchtowerBackgroundRefreshWorker(
        client=client,
        alert_candidate_hook_callable=hook_callable,
    )

    assert worker._alert_candidate_hook is hook_callable
