"""Fail-closed, asset-scoped collector cache lookup + macro reuse (final Run
Intel operational-reliability PR, item 4). Unit-level tests against
``find_recent_lane_output`` / ``find_recent_macro_output`` directly.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.services.intelligence.v3.distributed.collectors_v1 import (
    CacheReadError,
    execute_collector_task,
    find_recent_lane_output,
    find_recent_macro_output,
)
from app.services.intelligence.v3.distributed.run_task_store_v1 import TASK_FAILED_RETRYABLE
from app.services.intelligence.v3.distributed.task_contracts_v1 import (
    LANE_FUNDAMENTALS,
    TASK_COLLECT_MACRO_CONTEXT,
    TASK_DEGRADED,
    TASK_FAILED,
    TASK_SUCCEEDED,
)
from tests.distributed_run_intel_test_utils import FakeSupabase, make_settings

USER = str(uuid.uuid4())
OTHER_USER = str(uuid.uuid4())
NOW = datetime.now(timezone.utc)


def _row(**overrides):
    row = {
        "user_id": USER, "ticker": "AAPL", "asset_type": "equity", "lane": LANE_FUNDAMENTALS,
        "state": TASK_SUCCEEDED, "completed_at": NOW.isoformat(),
        "output": {"pe": 1.0, "source": "yfinance", "normalized": {"schema_version": "financial_evidence_normalization_v2"}},
    }
    row.update(overrides)
    return row


def _client_with(*rows) -> FakeSupabase:
    client = FakeSupabase()
    client.store["intel_run_tasks"] = [dict(r) for r in rows]
    return client


def _lookup(client, **kw):
    kw.setdefault("user_id", USER)
    kw.setdefault("ticker", "AAPL")
    kw.setdefault("asset_type", "equity")
    kw.setdefault("lane", LANE_FUNDAMENTALS)
    kw.setdefault("ttl_hours", 24.0)
    kw.setdefault("now", NOW)
    return find_recent_lane_output(client, **kw)


def test_cross_user_row_never_reused():
    client = _client_with(_row(user_id=OTHER_USER))
    assert _lookup(client) is None


def test_cross_ticker_row_never_reused():
    client = _client_with(_row(ticker="MSFT"))
    assert _lookup(client) is None


def test_cross_asset_type_row_never_reused():
    client = _client_with(_row(asset_type="crypto"))
    assert _lookup(client) is None


def test_failed_output_never_reused():
    client = _client_with(_row(state=TASK_FAILED))
    assert _lookup(client) is None


def test_degraded_output_never_reused():
    client = _client_with(_row(state=TASK_DEGRADED))
    assert _lookup(client) is None


def test_no_data_structurally_unusable_output_never_reused():
    client = _client_with(_row(output=None))
    assert _lookup(client) is None


def test_missing_completed_at_never_reused():
    client = _client_with(_row(completed_at=None))
    assert _lookup(client) is None


def test_malformed_completed_at_never_reused():
    client = _client_with(_row(completed_at="not-a-date"))
    assert _lookup(client) is None


def test_future_completed_at_never_reused():
    client = _client_with(_row(completed_at=(NOW + timedelta(hours=2)).isoformat()))
    assert _lookup(client) is None


def test_incompatible_normalization_version_never_reused():
    client = _client_with(_row(output={"pe": 1.0, "source": "yfinance"}))  # no "normalized"
    assert _lookup(client) is None


def test_valid_row_is_reused():
    client = _client_with(_row())
    assert _lookup(client) is not None


def test_database_outage_raises_cache_read_error_not_a_miss():
    class _BoomClient:
        def table(self, _name):
            raise RuntimeError("db outage")

    with pytest.raises(CacheReadError):
        _lookup(_BoomClient())


@pytest.mark.asyncio
async def test_cache_read_failure_fails_closed_zero_provider_calls(monkeypatch):
    import app.services.intelligence.v3.distributed.collectors_v1 as collectors

    def _boom(*_a, **_kw):
        raise CacheReadError("lane:fundamentals")

    monkeypatch.setattr(collectors, "find_recent_lane_output", _boom)

    async def _forbidden_fetch(_ticker):
        raise AssertionError("provider must not be called on a cache read failure")

    monkeypatch.setattr(collectors, "fetch_fundamentals", _forbidden_fetch)
    task = {
        "task_type": "collect_evidence_lane", "user_id": USER, "run_session_id": str(uuid.uuid4()),
        "ticker": "AAPL", "lane": LANE_FUNDAMENTALS, "asset_type": "equity",
    }
    result = await execute_collector_task(FakeSupabase(), task=task, settings=make_settings())
    assert result.final_state == TASK_FAILED_RETRYABLE
    assert result.provider_calls == 0
    assert result.cache_hit is False


# ── Macro reuse (24h, user+session-scoped, no fabricated ticker) ────────────

def test_macro_reuse_hit_within_ttl():
    client = _client_with({
        "user_id": USER, "task_type": TASK_COLLECT_MACRO_CONTEXT, "ticker": None,
        "state": TASK_SUCCEEDED, "completed_at": NOW.isoformat(),
        "output": {"artifact_id": "art-1", "as_of": NOW.isoformat()},
    })
    out = find_recent_macro_output(client, user_id=USER, ttl_hours=24.0, now=NOW)
    assert out is not None and out["artifact_id"] == "art-1"


def test_macro_reuse_miss_when_expired():
    client = _client_with({
        "user_id": USER, "task_type": TASK_COLLECT_MACRO_CONTEXT, "ticker": None,
        "state": TASK_SUCCEEDED, "completed_at": (NOW - timedelta(hours=48)).isoformat(),
        "output": {"artifact_id": "art-1"},
    })
    assert find_recent_macro_output(client, user_id=USER, ttl_hours=24.0, now=NOW) is None


def test_macro_reuse_never_crosses_users():
    client = _client_with({
        "user_id": OTHER_USER, "task_type": TASK_COLLECT_MACRO_CONTEXT, "ticker": None,
        "state": TASK_SUCCEEDED, "completed_at": NOW.isoformat(),
        "output": {"artifact_id": "art-1"},
    })
    assert find_recent_macro_output(client, user_id=USER, ttl_hours=24.0, now=NOW) is None


def test_macro_reuse_ignores_no_artifact_output():
    client = _client_with({
        "user_id": USER, "task_type": TASK_COLLECT_MACRO_CONTEXT, "ticker": None,
        "state": TASK_SUCCEEDED, "completed_at": NOW.isoformat(),
        "output": {"artifact_id": None},
    })
    assert find_recent_macro_output(client, user_id=USER, ttl_hours=24.0, now=NOW) is None


@pytest.mark.asyncio
async def test_second_session_reuses_macro_with_zero_provider_calls():
    # No FRED patch needed: a genuine cache hit must short-circuit BEFORE
    # `_collect_macro` (and its `run_fred_macro_evidence` import) ever runs —
    # if reuse were broken, this would attempt a real network call and fail.
    client = _client_with({
        "user_id": USER, "task_type": TASK_COLLECT_MACRO_CONTEXT, "ticker": None,
        "state": TASK_SUCCEEDED, "completed_at": NOW.isoformat(),
        "output": {"artifact_id": "art-1", "as_of": NOW.isoformat()},
    })
    task = {
        "task_type": TASK_COLLECT_MACRO_CONTEXT, "user_id": USER,
        "run_session_id": str(uuid.uuid4()),
    }
    result = await execute_collector_task(client, task=task, settings=make_settings())
    assert result.final_state == TASK_SUCCEEDED
    assert result.cache_hit is True
    assert result.provider_calls == 0
