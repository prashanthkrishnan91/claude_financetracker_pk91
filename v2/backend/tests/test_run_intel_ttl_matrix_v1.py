"""Controlled-clock hour/day/week repeat-run matrix (final Run Intel
operational-reliability patch, item 8). Drives the REAL scheduler, collector
dispatch, specialist executor, and supervisor across two sessions per
interval — the second session's durable rows are shifted backward in time
(the same technique ``test_one_expired_lane_refreshes_selectively`` uses) so
the exact TTL boundaries in ``task_contracts_v1.LANE_TTL_HOURS`` and
``specialist_agents_v1.OUTPUT_VALID_HOURS`` are exercised with real system
clock reads, never by changing those constants.

Covers equities (AAPL), an ETF (VTI), and crypto (BTC) — including the
SEC/ETF artifact-lane TTL paths — via deterministic fixtures at the provider
boundary (no paid/unavailable providers ever enabled).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.services.intelligence.v3.distributed import session_control_v1 as control
import app.services.intelligence.v3.distributed.collectors_v1 as collectors_mod
from app.services.intelligence.v3.distributed.task_contracts_v1 import (
    AXIS_CRYPTO_MARKET,
    AXIS_ETF_EXPOSURE,
    AXIS_FUNDAMENTAL,
    AXIS_SENTIMENT,
    AXIS_TECHNICAL,
    SESSION_COMPLETED,
)
from app.services.intelligence.v3.distributed.specialist_agents_v1 import (
    OUTPUT_VALID_HOURS,
)
from app.services.intelligence.v3.distributed.worker_supervisor_v1 import WorkerSupervisor
from tests.distributed_run_intel_test_utils import (
    FakeLLM,
    FakeSupabase,
    ProviderRecorder,
    drive_supervisor_to_completion,
    make_settings,
    patch_providers,
    seed_position,
)

USER = str(uuid.uuid4())
EQUITY, ETF, CRYPTO = "AAPL", "VTI", "BTC"


def _make_supervisor(client, llm):
    settings = make_settings(
        intel_v3_distributed_max_collector_concurrency=50,
        intel_v3_distributed_max_llm_concurrency=4,
        intel_v3_distributed_max_specialist_batch=5,
    )
    return WorkerSupervisor(client=client, settings=settings, llm=llm, worker_id="test-worker")


def _patch_varying_news(monkeypatch, recorder: ProviderRecorder, call_count: dict):
    """News content genuinely changes on each real fetch (a "day" counter) —
    the fixed-content ``ProviderRecorder`` fixture would refetch identical
    articles, which correctly would NOT change the sentiment axis's
    fingerprint. This is what lets a lane-level refresh be distinguished from
    an axis-level rerun in the assertions below. Still recorded on the SAME
    ``recorder.calls`` list (as a real ``("news", ticker)`` entry) so call
    counting stays consistent with the other patched providers."""

    async def _fresh_news(ticker: str, limit: int = 6):
        recorder.calls.append(("news", ticker.upper()))
        call_count["news"] = call_count.get("news", 0) + 1
        day = call_count["news"]
        return [{
            "headline": f"{ticker.upper()} day {day} update", "source": "test",
            "datetime": datetime.now(timezone.utc).timestamp(),
            "id": f"{ticker.upper()}-day{day}", "related_tickers": [ticker.upper()],
        }]

    monkeypatch.setattr(collectors_mod, "fetch_yfinance_news", _fresh_news)


def _patch_artifact_lanes(monkeypatch, call_count: dict):
    """Deterministic fixed-artifact-id fixture for the SEC/ETF research-
    worker lanes — proves their own (168h / 24h / 2160h) TTL reuse paths
    with the real collector/cache-lookup dispatch, without enabling any
    real research-worker provider."""

    def _fake_artifact_lane_sync(lane, **_kw):
        call_count[lane] = call_count.get(lane, 0) + 1
        return f"art-{lane}-{call_count[lane]}"

    monkeypatch.setattr(collectors_mod, "_run_artifact_lane_sync", _fake_artifact_lane_sync)


def _seed_portfolio(client):
    seed_position(client, USER, EQUITY, category="Core")
    seed_position(client, USER, ETF, category="ETF")
    seed_position(client, USER, CRYPTO, category="Crypto")


def _shift_durable_rows_back(client, *, hours: float) -> None:
    """Simulate ``hours`` of elapsed real time between session 1 and session
    2 by shifting every durable timestamp session 1 produced backward — the
    same technique already proven in
    ``test_one_expired_lane_refreshes_selectively``. Session 2 still runs
    against the real system clock; only the STORED ages change."""
    delta = timedelta(hours=hours)
    for task in client.rows("intel_run_tasks"):
        completed_at = task.get("completed_at")
        if isinstance(completed_at, str):
            dt = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
            task["completed_at"] = (dt - delta).isoformat()
    for output in client.rows("intel_run_specialist_outputs"):
        for key in ("created_at", "valid_until"):
            value = output.get(key)
            if isinstance(value, str):
                dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
                output[key] = (dt - delta).isoformat()


async def _terminate_active_and_start(client) -> str:
    for row in client.rows("intel_run_sessions"):
        if row.get("status") in ("created", "running"):
            row["status"] = SESSION_COMPLETED
    session_id = str(uuid.uuid4())
    await control.create_distributed_session(client=client, user_id=USER, session_id=session_id)
    return session_id


async def _run_two_sessions(monkeypatch, *, hours_elapsed: float):
    """Session 1 → shift all durable rows back ``hours_elapsed`` → session 2.
    Returns (recorder, artifact_calls, llm, session_1, session_2, snap_1, snap_2)."""
    client = FakeSupabase()
    _seed_portfolio(client)
    recorder = ProviderRecorder()
    patch_providers(monkeypatch, recorder)
    news_calls: dict = {}
    artifact_calls: dict = {}
    _patch_varying_news(monkeypatch, recorder, news_calls)
    _patch_artifact_lanes(monkeypatch, artifact_calls)
    llm = FakeLLM()
    supervisor = _make_supervisor(client, llm)

    session_1 = str(uuid.uuid4())
    await control.create_distributed_session(client=client, user_id=USER, session_id=session_1)
    await drive_supervisor_to_completion(supervisor)
    calls_after_1 = list(recorder.calls)
    llm_calls_after_1 = list(llm.calls)
    artifact_after_1 = dict(artifact_calls)
    snap_1 = next(s for s in client.rows("intel_v3_snapshots") if s["run_session_id"] == session_1)

    _shift_durable_rows_back(client, hours=hours_elapsed)

    session_2 = await _terminate_active_and_start(client)
    await drive_supervisor_to_completion(supervisor)

    session_2_row = next(s for s in client.rows("intel_run_sessions") if s["id"] == session_2)
    assert session_2_row["status"] == SESSION_COMPLETED, "each interval must reach a fresh completed session"
    snap_2 = next(s for s in client.rows("intel_v3_snapshots") if s["run_session_id"] == session_2)
    assert snap_2["id"] != snap_1["id"], "each rerun must publish its OWN session-native snapshot"
    assert snap_2["run_session_id"] == session_2, "the published snapshot must never be a copy of a prior session's"

    new_provider_calls = recorder.calls[len(calls_after_1):]
    new_llm_calls = llm.calls[len(llm_calls_after_1):]
    new_artifact_calls = {
        lane: artifact_calls.get(lane, 0) - artifact_after_1.get(lane, 0)
        for lane in set(artifact_calls) | set(artifact_after_1)
    }
    return new_provider_calls, new_llm_calls, new_artifact_calls, session_2_row


def _fns_called(calls: list[tuple[str, str]]) -> set[str]:
    return {fn for fn, _ in calls}


def _axes_called(calls: list[dict]) -> set[str]:
    return {c["axis"] for c in calls}


@pytest.mark.asyncio
async def test_immediate_rerun_zero_provider_and_llm_calls(monkeypatch):
    new_calls, new_llm, new_artifacts, _session = await _run_two_sessions(
        monkeypatch, hours_elapsed=0.0,
    )
    assert new_calls == [], "immediate rerun must reuse every direct-lane evidence"
    assert new_llm == [], "immediate rerun must reuse every specialist axis"
    assert all(v == 0 for v in new_artifacts.values()), (
        "immediate rerun must reuse every SEC/ETF artifact lane too"
    )


@pytest.mark.asyncio
async def test_one_hour_later_only_price_crypto_and_news_lanes_refresh(monkeypatch):
    # 1h05m: safely past the 1h news_sentiment / 0.25h price+crypto_market
    # TTLs, safely under every 24h+ TTL — never an exact-boundary read.
    new_calls, new_llm, new_artifacts, _session = await _run_two_sessions(
        monkeypatch, hours_elapsed=1.0 + 1 / 12,
    )
    fns = _fns_called(new_calls)
    assert fns == {"price_action", "news", "coingecko"}, (
        f"only the 0.25h/1h-TTL lanes may refresh at +1h05m, got {fns}"
    )
    # technicals/fundamentals (24h TTL) must NOT have been refetched.
    assert "fundamentals" not in fns
    # SEC (24h/168h) and ETF (2160h) artifact lanes are all still fresh.
    assert all(v == 0 for v in new_artifacts.values()), (
        f"no artifact lane may refresh at +1h05m, got {new_artifacts}"
    )
    # News content genuinely changed → only the sentiment axis reruns.
    axes = _axes_called(new_llm)
    assert axes == {AXIS_SENTIMENT}, (
        f"only the sentiment axis may call the LLM again at +1h05m, got {axes}"
    )
    # The refreshed price/crypto_market lanes never reach any axis prompt
    # (contract item 5) — so technical/fundamental/etf_exposure/crypto_market
    # axes correctly stay reused despite their OWN lane having refreshed.
    assert AXIS_TECHNICAL not in axes
    assert AXIS_CRYPTO_MARKET not in axes
    assert AXIS_ETF_EXPOSURE not in axes


@pytest.mark.asyncio
async def test_one_day_later_all_direct_lanes_refresh_and_every_axis_reruns(monkeypatch):
    # 24h05m: past every direct-lane TTL (<=24h) AND past the specialist
    # OUTPUT_VALID_HOURS ceiling — every axis must recompute, evidence
    # content aside, because a stale specialist output is never reusable
    # past its own validity window.
    assert OUTPUT_VALID_HOURS == 24.0
    new_calls, new_llm, new_artifacts, _session = await _run_two_sessions(
        monkeypatch, hours_elapsed=24.0 + 1 / 12,
    )
    fns = _fns_called(new_calls)
    assert fns == {"price_action", "fundamentals", "news", "coingecko"}, (
        f"every direct evidence lane must refresh at +24h05m, got {fns}"
    )
    axes = _axes_called(new_llm)
    assert axes == {
        AXIS_FUNDAMENTAL, AXIS_TECHNICAL, AXIS_SENTIMENT,
        AXIS_ETF_EXPOSURE, AXIS_CRYPTO_MARKET,
    }, f"every specialist axis must rerun once its output ages past the {OUTPUT_VALID_HOURS}h validity ceiling, got {axes}"
    # sec_catalyst (24h TTL) also expires at +24h05m; sec_company_facts
    # (168h) and etf_fund_data (2160h) are still within their own TTL.
    assert new_artifacts.get("sec_catalyst_sentiment", 0) >= 1
    assert new_artifacts.get("sec_company_facts", 0) == 0
    assert new_artifacts.get("etf_fund_data", 0) == 0


@pytest.mark.asyncio
async def test_one_week_later_full_refresh_including_long_ttl_sec_lane(monkeypatch):
    # 168h05m: past every lane TTL EXCEPT etf_fund_data (2160h/90d), and past
    # the specialist validity ceiling — proves the long-TTL ETF lane still
    # correctly reuses at a full week while everything shorter refreshes.
    new_calls, new_llm, new_artifacts, _session = await _run_two_sessions(
        monkeypatch, hours_elapsed=168.0 + 1 / 12,
    )
    fns = _fns_called(new_calls)
    assert fns == {"price_action", "fundamentals", "news", "coingecko"}
    axes = _axes_called(new_llm)
    assert axes == {
        AXIS_FUNDAMENTAL, AXIS_TECHNICAL, AXIS_SENTIMENT,
        AXIS_ETF_EXPOSURE, AXIS_CRYPTO_MARKET,
    }
    # sec_company_facts (168h) has now also crossed its own TTL boundary.
    assert new_artifacts.get("sec_company_facts", 0) >= 1
    assert new_artifacts.get("sec_catalyst_sentiment", 0) >= 1
    # etf_fund_data's 90-day TTL is untouched by a single week.
    assert new_artifacts.get("etf_fund_data", 0) == 0
