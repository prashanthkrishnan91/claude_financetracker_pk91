"""Full golden run + live-regression shape — the REAL worker supervisor
drives a deterministic 34-holding portfolio (equities + ETFs + crypto) from
session creation to a session-linked published snapshot, with exact provider
and LLM call accounting. Plus the production-regression fixture shaped like
session 83f28044-f19c-4640-ab2d-14991db4e29d.

The only fakes are at the outermost seams: provider fetchers (recorded),
the Anthropic client (recorded) and the certified publication build. The
session control plane, scheduler, task store, collectors, bundle builder,
specialist executors, decision plane and supervisor are all REAL.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

import app.services.intelligence.v3.distributed.run_task_store_v1 as store
from app.services.intelligence.v3.distributed import session_control_v1 as control
from app.services.intelligence.v3.distributed.run_scheduler_v1 import (
    parse_batch_tickers,
)
from app.services.intelligence.v3.distributed.task_contracts_v1 import (
    SESSION_COMPLETED,
    SESSION_COMPLETED_WITH_GAPS,
    TICKER_DECIDED,
    TICKER_NO_CALL,
)
from app.services.intelligence.v3.distributed.worker_supervisor_v1 import (
    WorkerSupervisor,
)
from tests.distributed_run_intel_test_utils import (
    FakeLLM,
    FakePublicationService,
    FakeSupabase,
    GOLDEN_34,
    GOLDEN_CRYPTO,
    GOLDEN_EQUITIES,
    GOLDEN_ETFS,
    ProviderRecorder,
    drive_supervisor_to_completion,
    make_settings,
    patch_providers,
    seed_golden_portfolio,
    seed_position,
)

USER = str(uuid.uuid4())


def _immediate_retries(monkeypatch):
    monkeypatch.setattr(
        store, "compute_task_retry_at",
        lambda attempts, now=None: datetime.now(timezone.utc).isoformat(),
    )


def _make_supervisor(client, llm, publication_service, *, collector_limit=200):
    settings = make_settings(
        intel_v3_distributed_max_collector_concurrency=collector_limit,
        intel_v3_distributed_max_llm_concurrency=2,
        intel_v3_distributed_max_specialist_batch=5,
    )
    return WorkerSupervisor(
        client=client,
        settings=settings,
        llm=llm,
        worker_id="test-worker",
        service_factory=lambda user_id: publication_service,
    )


class TestGoldenRun:
    @pytest.mark.asyncio
    async def test_34_holding_golden_run_exact_accounting(self, monkeypatch):
        client = FakeSupabase()
        seed_golden_portfolio(client, USER)
        recorder = ProviderRecorder()
        patch_providers(monkeypatch, recorder)
        llm = FakeLLM()
        publication = FakePublicationService(client, USER)
        supervisor = _make_supervisor(client, llm, publication)

        session_id = str(uuid.uuid4())
        result = await control.create_distributed_session(
            client=client, user_id=USER, session_id=session_id,
        )
        assert result["total_tickers"] == 34

        passes = await drive_supervisor_to_completion(supervisor)

        # ── Session terminal, one linked snapshot, no unfinished tasks ──────
        session = client.rows("intel_run_sessions")[0]
        assert session["status"] == SESSION_COMPLETED
        snapshots = [
            s for s in client.rows("intel_v3_snapshots")
            if s.get("run_session_id") == session_id
        ]
        assert len(snapshots) == 1
        assert session["completed_snapshot_id"] == snapshots[0]["id"]
        unfinished = [
            t for t in client.rows("intel_run_tasks")
            if t["state"] not in ("succeeded", "degraded", "failed", "cancelled")
        ]
        assert unfinished == []

        # ── Every active ticker has a session row and reached terminal ──────
        ticker_rows = client.rows("intel_run_tickers")
        assert {r["ticker"] for r in ticker_rows} == set(GOLDEN_34)
        assert all(r["state"] == TICKER_DECIDED for r in ticker_rows)

        # ── Every required lane reached a terminal state ─────────────────────
        lane_states = {
            (t["ticker"], t["lane"]): t["state"]
            for t in client.rows("intel_run_tasks")
            if t["task_type"] == "collect_evidence_lane"
        }
        assert all(
            state in ("succeeded", "degraded", "failed")
            for state in lane_states.values()
        )

        # ── EXACT provider-call accounting ───────────────────────────────────
        # equity: price(1 history) + technicals(1 history) + fundamentals(1)
        #         + news(1) = 4; SEC lanes are flag-off → degraded, 0 calls.
        # etf:    price + technicals + news = 3; NPORT flag-off → 0.
        # crypto: price(coingecko) + crypto_market(coingecko) = 2.
        expected_calls = (
            len(GOLDEN_EQUITIES) * 4 + len(GOLDEN_ETFS) * 3 + len(GOLDEN_CRYPTO) * 2
        )
        assert len(recorder.calls) == expected_calls, (
            f"provider calls {len(recorder.calls)} != expected {expected_calls}"
        )
        assert recorder.tickers_called() == set(GOLDEN_34)

        # ── EXACT LLM accounting: each (ticker, axis) analyzed exactly once ──
        analyzed: dict[str, list[str]] = {}
        for call in llm.calls:
            assert len(call["tickers"]) <= 5, "batch size exceeded"
            for ticker in call["tickers"]:
                analyzed.setdefault(call["axis"], []).append(ticker)
        for axis, tickers in analyzed.items():
            assert len(tickers) == len(set(tickers)), (
                f"duplicate LLM analysis on axis {axis}"
            )
        assert sorted(analyzed["fundamental"]) == sorted(GOLDEN_EQUITIES)
        assert sorted(analyzed["technical"]) == sorted(
            GOLDEN_EQUITIES + GOLDEN_ETFS
        )
        assert sorted(analyzed["sentiment"]) == sorted(
            GOLDEN_EQUITIES + GOLDEN_ETFS
        )
        assert sorted(analyzed["etf_exposure"]) == sorted(GOLDEN_ETFS)
        assert sorted(analyzed["crypto_market"]) == sorted(GOLDEN_CRYPTO)
        assert "risk_filing" not in analyzed  # no SEC evidence → no LLM call
        assert "review" not in analyzed       # aligned outputs → no review
        # Exact batching: ceil(28/5)*3 + 1*3 + 1 = 22 total LLM calls.
        assert len(llm.calls) == 22

        # ── Batches were asset-compatible ────────────────────────────────────
        for task in client.rows("intel_run_tasks"):
            if task["task_type"] != "specialist_analysis":
                continue
            batch = parse_batch_tickers(task["batch_key"])
            types = {
                r["asset_type"] for r in ticker_rows if r["ticker"] in batch
            }
            assert len(types) == 1

        # ── Publication ran exactly once over the full decided scope ────────
        assert len(publication.calls) == 1
        assert publication.calls[0]["scope_tickers"] == sorted(GOLDEN_34)

        # ── Cost metrics persisted ───────────────────────────────────────────
        metrics = session["metrics"]
        assert metrics["provider_calls"] == expected_calls
        assert metrics["llm_calls"] == 22
        assert passes < 30


class TestLiveRegressionShape:
    """Shaped like production session 83f28044-f19c-4640-ab2d-14991db4e29d:
    32 active tickers; ALK, GOOGL, VHT prioritized first; their provider
    failures never stop the remaining 29; no browser continuation exists."""

    def _seed_32(self, client: FakeSupabase) -> list[str]:
        equities = [t for t in GOLDEN_EQUITIES if t not in ("ALK", "GOOGL")]
        equities.append("ORCL")  # 26 + 1 = 27 healthy equities
        tickers: list[str] = []
        for i, ticker in enumerate(equities):
            seed_position(client, USER, ticker, close_price=100 + i)
            tickers.append(ticker)
        for ticker in ("VTI", "QQQ"):
            seed_position(client, USER, ticker, category="ETF")
            tickers.append(ticker)
        # The three production-failure tickers — no prior recommendation, so
        # priority puts them FIRST (missing current recommendation bucket).
        for ticker, category in (
            ("ALK", "Core"), ("GOOGL", "Core"), ("VHT", "ETF"),
        ):
            seed_position(client, USER, ticker, category=category)
            tickers.append(ticker)
        # Everyone else has a current active recommendation.
        for ticker in tickers:
            if ticker in ("ALK", "GOOGL", "VHT"):
                continue
            client.store.setdefault("recommendations", []).append({
                "id": str(uuid.uuid4()), "user_id": USER, "ticker": ticker,
                "suggested_action": "HOLD", "is_active": True,
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
        assert len(tickers) == 32
        return tickers

    @pytest.mark.asyncio
    async def test_priority_selects_failure_tickers_first_and_scope_stays_ticker_scoped(
        self, monkeypatch
    ):
        client = FakeSupabase()
        self._seed_32(client)
        recorder = ProviderRecorder()
        patch_providers(monkeypatch, recorder)
        llm = FakeLLM()
        publication = FakePublicationService(client, USER)
        # Small claim quantum: only the highest-priority work runs first.
        supervisor = _make_supervisor(
            client, llm, publication, collector_limit=10,
        )
        session_id = str(uuid.uuid4())
        result = await control.create_distributed_session(
            client=client, user_id=USER, session_id=session_id,
        )
        assert result["total_tickers"] == 32

        await supervisor.run_pass()
        # First bounded pass touched ONLY the prioritized tickers — the old
        # architecture's full-portfolio preprocessing is impossible.
        assert recorder.tickers_called() <= {"ALK", "GOOGL", "VHT"}
        assert recorder.tickers_called(), "no prioritized collector ran"

    @pytest.mark.asyncio
    async def test_three_ticker_failure_never_stops_the_other_29(
        self, monkeypatch
    ):
        client = FakeSupabase()
        tickers = self._seed_32(client)
        _immediate_retries(monkeypatch)
        recorder = ProviderRecorder(
            fail_price={"ALK", "GOOGL", "VHT"},
            fail_fundamentals={"ALK", "GOOGL"},
            fail_history={"ALK", "GOOGL", "VHT"},
        )
        patch_providers(monkeypatch, recorder)
        llm = FakeLLM()
        publication = FakePublicationService(client, USER)
        supervisor = _make_supervisor(client, llm, publication)

        session_id = str(uuid.uuid4())
        await control.create_distributed_session(
            client=client, user_id=USER, session_id=session_id,
        )
        await drive_supervisor_to_completion(supervisor)

        session = client.rows("intel_run_sessions")[0]
        # The 29 healthy tickers ALL decided; the 3 failed ones are honest
        # NO CALL — and the session still published with gaps. The frontend
        # never issued a second request: the supervisor alone finished it.
        by_ticker = {r["ticker"]: r for r in client.rows("intel_run_tickers")}
        for ticker in ("ALK", "GOOGL", "VHT"):
            assert by_ticker[ticker]["state"] == TICKER_NO_CALL
        healthy = [t for t in tickers if t not in ("ALK", "GOOGL", "VHT")]
        for ticker in healthy:
            assert by_ticker[ticker]["state"] == TICKER_DECIDED, (
                f"{ticker} was blocked by an unrelated ticker's failure"
            )
        assert session["status"] == SESSION_COMPLETED_WITH_GAPS
        assert sorted(
            (session["metrics"]["publication"]["no_call_tickers"])
        ) == ["ALK", "GOOGL", "VHT"]
        snapshots = [
            s for s in client.rows("intel_v3_snapshots")
            if s.get("run_session_id") == session_id
        ]
        assert len(snapshots) == 1
        assert publication.calls[-1]["scope_tickers"] == sorted(healthy)

    @pytest.mark.asyncio
    async def test_worker_restart_resumes_from_leases(self, monkeypatch):
        client = FakeSupabase()
        self._seed_32(client)
        recorder = ProviderRecorder()
        patch_providers(monkeypatch, recorder)
        publication = FakePublicationService(client, USER)

        session_id = str(uuid.uuid4())
        await control.create_distributed_session(
            client=client, user_id=USER, session_id=session_id,
        )
        # Worker A claims work then "crashes" (never completes).
        dead = store.claim_tasks(
            client, worker_id="dead-worker", limit=10, lease_seconds=0,
        )
        assert dead
        # Worker B (fresh supervisor, new identity) finishes the whole run.
        supervisor = _make_supervisor(client, FakeLLM(), publication)
        await drive_supervisor_to_completion(supervisor)
        session = client.rows("intel_run_sessions")[0]
        assert session["status"] in (
            SESSION_COMPLETED, SESSION_COMPLETED_WITH_GAPS,
        )
        unfinished = [
            t for t in client.rows("intel_run_tasks")
            if t["state"] not in ("succeeded", "degraded", "failed", "cancelled")
        ]
        assert unfinished == []
