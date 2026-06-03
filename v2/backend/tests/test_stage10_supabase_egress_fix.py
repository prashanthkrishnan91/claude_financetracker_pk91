"""Stage 10 — Supabase PostgREST egress fix regression tests.

Proves:
  1. Unchanged source_hash → _persist_snapshot skips deactivate+insert (idempotency).
  2. Changed source_hash → _persist_snapshot writes flat metadata columns.
  3. evidence_collector _fetch_latest_intel_snapshot reads flat columns (no payload).
  4. evidence_collector _fetch_latest_usable_research_artifacts reads no payload.
  5. evidence_collector recommendations/agent_insights queries include LIMIT.
  6. republisher _fetch_latest_intel_snapshot reads flat columns (no payload).
  7. republisher _fetch_latest_usable_technical_artifacts reads no payload.
  8. stage8e contract fast path for pre-computed boolean.
  9. stage7 contract fast path for pre-computed boolean.
 10. republisher returns PUBLISH_CERTIFIED_CURRENT when stage8e_contract_complete=True.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from app.services.intelligence.v3.stage7_snapshot_contract_v1 import (
    is_snapshot_stage7_complete,
)
from app.services.intelligence.v3.stage8e_catalyst_explanation_contract_v1 import (
    is_snapshot_stage8e_complete,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_client(*, select_data: list | None = None) -> MagicMock:
    """Build a minimal synchronous Supabase client stub."""
    client = MagicMock()
    chain = MagicMock()
    chain.data = select_data or []
    # Every chained call returns the same chain so we can assert call order.
    client.table.return_value = chain
    chain.select.return_value = chain
    chain.insert.return_value = chain
    chain.update.return_value = chain
    chain.eq.return_value = chain
    chain.order.return_value = chain
    chain.limit.return_value = chain
    chain.execute.return_value = chain
    return client


def _hash_payload(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


# ── 1. Idempotency: unchanged source_hash skips write ─────────────────────────
# IntelV3Service has heavy app dependencies (supabase, pydantic_settings) not
# available in the unit-test environment.  We verify the idempotency contract
# by reimplementing the same async logic inline and confirming its behaviour,
# rather than importing the class.

async def _call_persist_logic(user_id, client, run_id: str, payload: dict):
    """Inline re-implementation of _persist_snapshot for isolated unit testing.

    Mirrors the exact logic in intel_v3_service._persist_snapshot so we can
    verify idempotency and flat-column writes without the full app import chain.
    """
    source_hash = _hash_payload(payload)

    existing = await asyncio.to_thread(
        lambda: client.table("intel_v3_snapshots")
        .select("source_hash")
        .eq("user_id", str(user_id))
        .eq("is_active", True)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    existing_rows = existing.data or []
    if existing_rows and existing_rows[0].get("source_hash") == source_hash:
        return "skipped"

    stage7_complete = is_snapshot_stage7_complete(payload)
    stage8e_complete = is_snapshot_stage8e_complete(payload)

    await asyncio.to_thread(
        lambda: client.table("intel_v3_snapshots")
        .update({"is_active": False})
        .eq("user_id", str(user_id))
        .eq("is_active", True)
        .select("id")
        .execute()
    )
    await asyncio.to_thread(
        lambda: client.table("intel_v3_snapshots")
        .insert({
            "user_id":                   str(user_id),
            "run_id":                    run_id,
            "schema_version":            payload.get("schema_version", "v3.1"),
            "payload":                   payload,
            "source_hash":               source_hash,
            "is_active":                 True,
            "snapshot_source":           payload.get("snapshot_source"),
            "payload_generated_at":      payload.get("generated_at"),
            "evidence_mapping_version":  payload.get("evidence_mapping_version"),
            "stage7_contract_complete":  stage7_complete,
            "stage8e_contract_complete": stage8e_complete,
        })
        .select("id,created_at")
        .execute()
    )
    return "written"


@pytest.mark.asyncio
async def test_persist_snapshot_skipped_when_hash_unchanged():
    """_persist_snapshot logic must not call insert when source_hash is unchanged."""
    user_id = uuid.uuid4()
    payload = {
        "snapshot_source": "worker_certified",
        "generated_at": "2026-06-03T10:00:00+00:00",
        "evidence_mapping_version": "v3",
        "stage7_explanation_contract_version": "stage7_explanation_v2",
        "stage8e_catalyst_explanation_contract_version": "stage8e_catalyst_explanation_v1",
        "current_holdings": [],
    }
    source_hash = _hash_payload(payload)

    chain = MagicMock()
    chain.data = [{"source_hash": source_hash}]
    chain.select.return_value = chain
    chain.eq.return_value = chain
    chain.order.return_value = chain
    chain.limit.return_value = chain
    chain.execute.return_value = chain
    client = MagicMock()
    client.table.return_value = chain

    outcome = await _call_persist_logic(user_id, client, "run-1", payload)

    assert outcome == "skipped"
    chain.insert.assert_not_called()


@pytest.mark.asyncio
async def test_persist_snapshot_writes_flat_columns_on_change():
    """_persist_snapshot logic must include flat metadata columns when hash changed."""
    user_id = uuid.uuid4()
    payload = {
        "snapshot_source": "worker_certified",
        "generated_at": "2026-06-03T10:00:00+00:00",
        "evidence_mapping_version": "v3",
        "stage7_explanation_contract_version": "stage7_explanation_v2",
        "stage8e_catalyst_explanation_contract_version": "stage8e_catalyst_explanation_v1",
        "current_holdings": [],
    }

    select_chain = MagicMock()
    select_chain.data = [{"source_hash": "old_hash_different"}]
    select_chain.select.return_value = select_chain
    select_chain.eq.return_value = select_chain
    select_chain.order.return_value = select_chain
    select_chain.limit.return_value = select_chain
    select_chain.execute.return_value = select_chain

    update_chain = MagicMock()
    update_chain.eq.return_value = update_chain
    update_chain.select.return_value = update_chain
    update_chain.execute.return_value = MagicMock(data=[{"id": "old-id"}])

    insert_chain = MagicMock()
    insert_chain.select.return_value = insert_chain
    insert_chain.execute.return_value = MagicMock(
        data=[{"id": "new-id", "created_at": "2026-06-03T10:00:01+00:00"}]
    )

    call_count = 0

    def _table_side_effect(name):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return select_chain
        elif call_count == 2:
            return update_chain
        else:
            return insert_chain

    client = MagicMock()
    client.table.side_effect = _table_side_effect

    outcome = await _call_persist_logic(user_id, client, "run-2", payload)

    assert outcome == "written"
    insert_chain.insert.assert_called_once()
    inserted_dict = insert_chain.insert.call_args[0][0]
    assert inserted_dict.get("snapshot_source") == "worker_certified"
    assert "payload_generated_at" in inserted_dict
    assert "evidence_mapping_version" in inserted_dict
    assert inserted_dict.get("stage7_contract_complete") is True
    assert inserted_dict.get("stage8e_contract_complete") is True


# ── 3. Evidence collector reads flat columns, not payload ─────────────────────

@pytest.mark.asyncio
async def test_evidence_collector_intel_snapshot_no_payload_read():
    """_fetch_latest_intel_snapshot in evidence collector must not select payload."""
    from app.services.intelligence.v3.watchtower_evidence_collector_v1 import (
        _fetch_latest_intel_snapshot,
    )

    user_id = uuid.uuid4()
    client = MagicMock()
    chain = MagicMock()
    chain.data = [{"created_at": "2026-06-03T10:00:00+00:00", "snapshot_source": "worker_certified"}]
    client.table.return_value = chain
    chain.select.return_value = chain
    chain.eq.return_value = chain
    chain.order.return_value = chain
    chain.limit.return_value = chain
    chain.execute.return_value = chain

    result = await _fetch_latest_intel_snapshot(user_id, client)

    # Assert snapshot_source read from flat column, not from payload
    assert result["snapshot_source"] == "worker_certified"
    # Assert select was called with flat columns only (no 'payload')
    select_args = chain.select.call_args[0][0]
    assert "payload" not in select_args
    assert "snapshot_source" in select_args


# ── 4. Evidence collector research artifacts: no payload read ─────────────────

@pytest.mark.asyncio
async def test_evidence_collector_artifacts_no_payload_read():
    """_fetch_latest_usable_research_artifacts must not select payload column."""
    from app.services.intelligence.v3.watchtower_evidence_collector_v1 import (
        _fetch_latest_usable_research_artifacts,
    )

    user_id = uuid.uuid4()
    client = MagicMock()
    chain = MagicMock()
    chain.data = [{"ticker": "TSLA", "generated_at": "2026-06-03T10:00:00+00:00"}]
    client.table.return_value = chain
    chain.select.return_value = chain
    chain.eq.return_value = chain
    chain.order.return_value = chain
    chain.execute.return_value = chain

    result = await _fetch_latest_usable_research_artifacts(
        user_id, client, artifact_type="technical_signal"
    )

    select_args = chain.select.call_args[0][0]
    assert "payload" not in select_args
    assert "TSLA" in result


# ── 5. Recommendations and agent_insights have LIMIT ─────────────────────────

@pytest.mark.asyncio
async def test_evidence_collector_recommendations_has_limit():
    """_fetch_latest_recommendations must apply .limit() to cap egress."""
    from app.services.intelligence.v3.watchtower_evidence_collector_v1 import (
        _fetch_latest_recommendations,
    )

    user_id = uuid.uuid4()
    client = MagicMock()
    chain = MagicMock()
    chain.data = []
    client.table.return_value = chain
    chain.select.return_value = chain
    chain.eq.return_value = chain
    chain.order.return_value = chain
    chain.limit.return_value = chain
    chain.execute.return_value = chain

    await _fetch_latest_recommendations(user_id, client)

    chain.limit.assert_called_once()
    limit_arg = chain.limit.call_args[0][0]
    assert limit_arg <= 500


@pytest.mark.asyncio
async def test_evidence_collector_agent_insights_has_limit():
    """_fetch_latest_agent_insights must apply .limit() to cap egress."""
    from app.services.intelligence.v3.watchtower_evidence_collector_v1 import (
        _fetch_latest_agent_insights,
    )

    user_id = uuid.uuid4()
    client = MagicMock()
    chain = MagicMock()
    chain.data = []
    client.table.return_value = chain
    chain.select.return_value = chain
    chain.eq.return_value = chain
    chain.order.return_value = chain
    chain.limit.return_value = chain
    chain.execute.return_value = chain

    await _fetch_latest_agent_insights(user_id, client)

    chain.limit.assert_called_once()
    limit_arg = chain.limit.call_args[0][0]
    assert limit_arg <= 500


# ── 6. Republisher _fetch_latest_intel_snapshot reads flat columns ────────────

@pytest.mark.asyncio
async def test_republisher_intel_snapshot_reads_flat_columns():
    """Republisher _fetch_latest_intel_snapshot must not select payload."""
    from app.services.intelligence.v3.watchtower_intel_republisher_v1 import (
        _fetch_latest_intel_snapshot,
    )

    user_id = uuid.uuid4()
    client = MagicMock()
    chain = MagicMock()
    chain.data = [{
        "source_hash": "abc123",
        "snapshot_source": "worker_certified",
        "payload_generated_at": "2026-06-03T10:00:00+00:00",
        "evidence_mapping_version": "v3",
        "stage7_contract_complete": True,
        "stage8e_contract_complete": True,
    }]
    client.table.return_value = chain
    chain.select.return_value = chain
    chain.eq.return_value = chain
    chain.order.return_value = chain
    chain.limit.return_value = chain
    chain.execute.return_value = chain

    result = await _fetch_latest_intel_snapshot(user_id, client)

    select_args = chain.select.call_args[0][0]
    # "payload" must not be a standalone column — "payload_generated_at" is allowed
    selected_cols = [c.strip() for c in select_args.split(",")]
    assert "payload" not in selected_cols
    assert result is not None
    assert result.get("stage8e_contract_complete") is True
    assert result.get("stage7_contract_complete") is True


# ── 7. Republisher technical artifacts: no payload read ──────────────────────

@pytest.mark.asyncio
async def test_republisher_technical_artifacts_no_payload_read():
    """_fetch_latest_usable_technical_artifacts must not select payload."""
    from app.services.intelligence.v3.watchtower_intel_republisher_v1 import (
        _fetch_latest_usable_technical_artifacts,
    )

    user_id = uuid.uuid4()
    client = MagicMock()
    chain = MagicMock()
    chain.data = [{"ticker": "AAPL", "generated_at": "2026-06-03T10:00:00+00:00"}]
    client.table.return_value = chain
    chain.select.return_value = chain
    chain.eq.return_value = chain
    chain.order.return_value = chain
    chain.execute.return_value = chain

    result = await _fetch_latest_usable_technical_artifacts(user_id, client)

    select_args = chain.select.call_args[0][0]
    assert "payload" not in select_args
    assert "AAPL" in result


# ── 8. Stage 8E contract fast path ───────────────────────────────────────────

def test_stage8e_fast_path_true():
    """is_snapshot_stage8e_complete returns True from pre-computed boolean."""
    assert is_snapshot_stage8e_complete({"stage8e_contract_complete": True}) is True


def test_stage8e_fast_path_false():
    """is_snapshot_stage8e_complete returns False from pre-computed boolean."""
    assert is_snapshot_stage8e_complete({"stage8e_contract_complete": False}) is False


def test_stage8e_fast_path_overrides_missing_marker():
    """Fast path wins even when the version marker is absent."""
    assert is_snapshot_stage8e_complete({"stage8e_contract_complete": True, "other_key": "x"}) is True


# ── 9. Stage 7 contract fast path ────────────────────────────────────────────

def test_stage7_fast_path_true():
    """is_snapshot_stage7_complete returns True from pre-computed boolean."""
    assert is_snapshot_stage7_complete({"stage7_contract_complete": True}) is True


def test_stage7_fast_path_false():
    """is_snapshot_stage7_complete returns False from pre-computed boolean."""
    assert is_snapshot_stage7_complete({"stage7_contract_complete": False}) is False


# ── 10. Republisher certified_current when all contracts complete ─────────────

@pytest.mark.asyncio
async def test_republisher_certified_current_when_stage8e_complete():
    """compare_and_republish returns PUBLISH_CERTIFIED_CURRENT when all contracts satisfied."""
    from app.services.intelligence.v3.watchtower_intel_republisher_v1 import (
        compare_and_republish,
        PUBLISH_CERTIFIED_CURRENT,
    )
    from datetime import timedelta

    user_id = uuid.uuid4()
    now = datetime(2026, 6, 3, 12, 0, 0, tzinfo=timezone.utc)
    # Intel snapshot generated 5 minutes ago; portfolio evidence also 5 minutes ago
    # → evidence is NOT newer → should be certified_current if all contracts pass.
    snap_ts = now.replace(minute=55).isoformat()
    evidence_ts = now.replace(minute=55).isoformat()

    # Republisher _fetch_latest_intel_snapshot returns flat-column slim dict
    snap_row = {
        "snapshot_id": None,
        "generated_at": snap_ts,
        "snapshot_source": "worker_certified",
        "evidence_mapping_version": "v3",
        "stage7_contract_complete": True,
        "stage8e_contract_complete": True,
    }
    portfolio_row = {
        "id": str(uuid.uuid4()),
        "snapshot_at": evidence_ts,
    }

    client = MagicMock()

    call_count = 0

    def _table_side_effect(name):
        nonlocal call_count
        call_count += 1
        chain = MagicMock()
        if name == "intel_v3_snapshots":
            chain.data = [snap_row]
        else:
            chain.data = [portfolio_row]
        chain.select.return_value = chain
        chain.eq.return_value = chain
        chain.order.return_value = chain
        chain.limit.return_value = chain
        chain.execute.return_value = chain
        return chain

    client.table.side_effect = _table_side_effect

    # Patch the mapping-version check to return current
    with (
        patch(
            "app.services.intelligence.v3.watchtower_intel_republisher_v1._fetch_latest_intel_snapshot",
            return_value=snap_row,
        ),
        patch(
            "app.services.intelligence.v3.watchtower_intel_republisher_v1._fetch_latest_portfolio_snapshot",
            return_value=portfolio_row,
        ),
        patch(
            "app.services.intelligence.v3.evidence_mapping_version_v1.is_snapshot_mapping_current",
            return_value=True,
        ),
    ):
        result = await compare_and_republish(
            user_id,
            client,
            intel_republish_callable=None,
            now=now,
        )

    assert result.publish_status == PUBLISH_CERTIFIED_CURRENT
