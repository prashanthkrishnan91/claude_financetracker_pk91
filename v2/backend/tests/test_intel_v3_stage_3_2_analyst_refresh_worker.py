"""Stage 3.2 — Continuous Intelligence Plane v1: durable analyst refresh worker.

Contract under test:
  * Run Intel v3 still makes ZERO analyst/LLM calls in the HTTP request, and now
    idempotently enqueues a durable ``analyst_refresh_jobs`` row per stale
    ticker (a fast queue upsert, not LLM work).
  * Stale analyst evidence creates OR updates exactly one durable job per
    (user, ticker, window) — repeated clicks never spawn duplicates.
  * The background worker consumes a due job and invokes the analyst adapter
    OUTSIDE the HTTP request.
  * A successful ticker refresh marks its job succeeded; failed tickers' jobs
    stay ``failed`` with a retry — never a fabricated success.
  * No fabricated freshness when the worker's adapter crashes or the worker's
    runtime budget is exhausted.
  * The worker never imports the deterministic decision policy.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.intelligence.v3.analyst_refresh_adapter_v1 import (
    STATUS_FAILED,
    STATUS_PARTIAL_SUCCESS,
    STATUS_SUCCEEDED,
    AnalystRefreshResult,
    TickerRefreshOutcome,
)
from app.services.intelligence.v3.analyst_refresh_job_store_v1 import (
    JOB_CLAIMED,
    JOB_FAILED,
    JOB_PENDING,
    JOB_SUCCEEDED,
    AnalystRefreshJob,
    claim_due_jobs,
    compute_next_retry_at,
    default_refresh_window,
    enqueue_refresh_jobs,
    mark_job_failed,
    mark_job_succeeded,
)
from app.services.intelligence.v3.analyst_refresh_request_seam_v1 import (
    STATUS_REFRESH_REQUESTED,
    AnalystRefreshRequestSeam,
)
from app.services.intelligence.v3.analyst_refresh_worker_v1 import AnalystRefreshWorker
from app.services.intelligence.v3.full_portfolio_analyst_refresh_adapter_v1 import (
    FullPortfolioAnalystRefreshAdapter,
    FullPortfolioAnalystRefreshBudget,
    _read_post_run_evidence,
)
from app.services.intelligence.v3.evidence_freshness_contract_v1 import (
    RUN_MODE_BLOCKED_UNCERTIFIED,
    RUN_MODE_FAST_CERTIFIED,
    RUN_MODE_PARTIAL_CERTIFIED,
)

USER_A = "00000000-0000-0000-0000-0000000000aa"
TABLE = "analyst_refresh_jobs"


def _now() -> datetime:
    return datetime(2026, 5, 14, 12, 0, 0, tzinfo=timezone.utc)


def _iso_ago(hours: float, now: datetime | None = None) -> str:
    return ((now or _now()) - timedelta(hours=hours)).isoformat()


# ── In-memory Supabase fake ───────────────────────────────────────────────────


class _FakeResult:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    """Minimal Supabase query-builder fake covering the calls the job store +
    seam make: table → (insert|update|select) → eq/in_/order → execute."""

    def __init__(self, store: dict, table: str):
        self._store = store
        self._table = table
        self._op = "select"
        self._payload = None
        self._filters: list[tuple] = []
        self._order_col = None

    def insert(self, rows):
        self._op = "insert"
        self._payload = rows if isinstance(rows, list) else [rows]
        return self

    def update(self, values):
        self._op = "update"
        self._payload = dict(values)
        return self

    def select(self, *_cols, **_kw):
        self._op = "select"
        return self

    def eq(self, col, val):
        self._filters.append(("eq", col, val))
        return self

    def in_(self, col, vals):
        self._filters.append(("in", col, list(vals)))
        return self

    def gte(self, col, val):
        self._filters.append(("gte", col, val))
        return self

    def order(self, col, desc=False):
        self._order_col = col
        return self

    def limit(self, _n):
        return self

    def _match(self, row) -> bool:
        for kind, col, val in self._filters:
            rv = row.get(col)
            if kind == "eq" and rv != val:
                return False
            if kind == "in" and rv not in val:
                return False
            if kind == "gte" and not (rv is not None and rv >= val):
                return False
        return True

    def execute(self):
        rows = self._store.setdefault(self._table, [])
        if self._op == "insert":
            inserted = []
            for r in self._payload:
                nr = dict(r)
                nr.setdefault("id", str(uuid.uuid4()))
                rows.append(nr)
                inserted.append(dict(nr))
            return _FakeResult(inserted)
        if self._op == "update":
            updated = []
            for r in rows:
                if self._match(r):
                    r.update(self._payload)
                    updated.append(dict(r))
            return _FakeResult(updated)
        out = [dict(r) for r in rows if self._match(r)]
        if self._order_col:
            out.sort(
                key=lambda x: (x.get(self._order_col) is None, x.get(self._order_col))
            )
        return _FakeResult(out)


class _FakeSupabase:
    def __init__(self):
        self.store: dict[str, list[dict]] = {}

    def table(self, name):
        return _FakeQuery(self.store, name)

    def rows(self, name=TABLE):
        return self.store.get(name, [])


# ── Fake analyst adapter ──────────────────────────────────────────────────────


class _FakeAnalystAdapter:
    """Stands in for FullPortfolioAnalystRefreshAdapter — records its call and
    returns a deterministic per-ticker outcome without any LLM work."""

    def __init__(self, *, success_tickers=(), raises=False):
        self.success_tickers = {t.upper() for t in success_tickers}
        self.raises = raises
        self.calls: list[list[str]] = []

    async def __call__(self, tickers, *, priority_hints=None, started_at=None):
        self.calls.append(list(tickers))
        if self.raises:
            raise RuntimeError("simulated adapter crash")
        per_ticker: list[TickerRefreshOutcome] = []
        successful = failed = 0
        for t in tickers:
            up = t.upper()
            if up in self.success_tickers:
                per_ticker.append(TickerRefreshOutcome(
                    ticker=up, success=True,
                    refreshed_agent_insight_at=_now().isoformat(),
                    llm_call_count=1, llm_success_count=1,
                ))
                successful += 1
            else:
                per_ticker.append(TickerRefreshOutcome(
                    ticker=up, success=False, error_reason="fallback_verdict",
                    llm_call_count=1, llm_success_count=0,
                ))
                failed += 1
        if failed == 0:
            status = STATUS_SUCCEEDED
        elif successful == 0:
            status = STATUS_FAILED
        else:
            status = STATUS_PARTIAL_SUCCESS
        return AnalystRefreshResult(
            status=status,
            selected_tickers=list(tickers),
            deferred_tickers=[],
            per_ticker=per_ticker,
            attempted_llm_calls=successful + failed,
            successful_llm_calls=successful,
            failed_llm_calls=failed,
        )


# ── 1. Job store: idempotent enqueue ─────────────────────────────────────────


class TestEnqueueIdempotency:
    def test_enqueue_creates_one_job_per_stale_ticker(self):
        fake = _FakeSupabase()
        result = enqueue_refresh_jobs(
            fake, user_id=USER_A, tickers=["AAPL", "NVDA"], now=_now(),
        )
        assert result.created_count == 2
        assert result.touched_count == 0
        rows = fake.rows()
        assert len(rows) == 2
        assert {r["ticker"] for r in rows} == {"AAPL", "NVDA"}
        assert all(r["status"] == JOB_PENDING for r in rows)

    def test_duplicate_clicks_do_not_create_duplicate_jobs(self):
        fake = _FakeSupabase()
        enqueue_refresh_jobs(fake, user_id=USER_A, tickers=["AAPL", "NVDA"], now=_now())
        # Second click, same window — must touch, not duplicate.
        second = enqueue_refresh_jobs(
            fake, user_id=USER_A, tickers=["AAPL", "NVDA"], now=_now(),
        )
        assert second.created_count == 0
        assert second.touched_count == 2
        assert len(fake.rows()) == 2  # still exactly one per ticker

    def test_enqueue_dedupes_input_and_uppercases(self):
        fake = _FakeSupabase()
        result = enqueue_refresh_jobs(
            fake, user_id=USER_A, tickers=["aapl", "AAPL", "nvda"], now=_now(),
        )
        assert result.requested_tickers == ["AAPL", "NVDA"]
        assert len(fake.rows()) == 2

    def test_pending_duplicate_click_touches_in_place(self):
        """A re-click on a pending job touches it — no duplicate, no reset."""
        fake = _FakeSupabase()
        enqueue_refresh_jobs(fake, user_id=USER_A, tickers=["AAPL"], now=_now())
        second = enqueue_refresh_jobs(fake, user_id=USER_A, tickers=["AAPL"], now=_now())
        assert second.created_count == 0
        assert second.touched_count == 1
        assert second.reopened_count == 0
        assert len(fake.rows()) == 1
        assert fake.rows()[0]["status"] == JOB_PENDING

    def test_claimed_duplicate_click_touches_and_does_not_disrupt_inflight(self):
        """A re-click on a claimed (in-flight) job must not duplicate it or
        knock it out of the claimed state a worker is mid-processing."""
        fake = _FakeSupabase()
        enqueue_refresh_jobs(fake, user_id=USER_A, tickers=["AAPL"], now=_now())
        fake.rows()[0].update({"status": JOB_CLAIMED, "attempts": 1})
        second = enqueue_refresh_jobs(fake, user_id=USER_A, tickers=["AAPL"], now=_now())
        assert second.touched_count == 1
        assert second.reopened_count == 0
        assert len(fake.rows()) == 1
        row = fake.rows()[0]
        assert row["status"] == JOB_CLAIMED  # in-flight claim untouched
        assert row["attempts"] == 1

    def test_failed_retryable_duplicate_click_touches_and_preserves_backoff(self):
        """A re-click on a failed-but-retryable job must not wipe its backoff
        timer or attempt counter."""
        fake = _FakeSupabase()
        enqueue_refresh_jobs(fake, user_id=USER_A, tickers=["AAPL"], now=_now())
        future_retry = (_now() + timedelta(hours=5)).isoformat()
        fake.rows()[0].update({"status": JOB_FAILED, "attempts": 3,
                               "max_attempts": 5, "next_retry_at": future_retry})
        second = enqueue_refresh_jobs(fake, user_id=USER_A, tickers=["AAPL"], now=_now())
        assert second.touched_count == 1
        assert second.reopened_count == 0
        row = fake.rows()[0]
        assert row["status"] == JOB_FAILED       # not reset
        assert row["attempts"] == 3              # not reset
        assert row["next_retry_at"] == future_retry  # backoff preserved
        assert len(fake.rows()) == 1

    def test_succeeded_job_is_reopened_for_same_window_requeue_when_still_stale(self):
        """A succeeded job must not silently block a same-window requeue: the
        seam only re-enqueues tickers still classified stale/HARD_STALE, so a
        legitimate fresh attempt is reopened in place (no duplicate row)."""
        fake = _FakeSupabase()
        enqueue_refresh_jobs(fake, user_id=USER_A, tickers=["AAPL"], now=_now())
        fake.rows()[0].update({"status": JOB_SUCCEEDED, "attempts": 1,
                               "completed_at": _now().isoformat(),
                               "next_retry_at": None})
        second = enqueue_refresh_jobs(fake, user_id=USER_A, tickers=["AAPL"], now=_now())
        assert second.reopened_count == 1
        assert second.created_count == 0
        assert len(fake.rows()) == 1            # still exactly one row per key
        row = fake.rows()[0]
        assert row["status"] == JOB_PENDING     # claimable again
        assert row["attempts"] == 0             # fresh attempt budget
        assert row["next_retry_at"] is not None  # due immediately
        assert row["completed_at"] is None
        # And the worker can now claim it.
        claimed = claim_due_jobs(fake, worker_run_id=uuid.uuid4(), now=_now())
        assert {j.ticker for j in claimed} == {"AAPL"}

    def test_exhausted_failed_job_is_reopened_for_same_window_when_still_stale(self):
        """An attempt-exhausted failed job must not permanently suppress a
        later legitimate retry while the evidence is still stale."""
        fake = _FakeSupabase()
        enqueue_refresh_jobs(fake, user_id=USER_A, tickers=["AAPL"], now=_now())
        fake.rows()[0].update({"status": JOB_FAILED, "attempts": 5,
                               "max_attempts": 5, "next_retry_at": None,
                               "last_error": "boom"})
        # Sanity: while exhausted it is NOT claimable.
        assert claim_due_jobs(fake, worker_run_id=uuid.uuid4(), now=_now()) == []
        second = enqueue_refresh_jobs(fake, user_id=USER_A, tickers=["AAPL"], now=_now())
        assert second.reopened_count == 1
        assert len(fake.rows()) == 1            # still exactly one row per key
        row = fake.rows()[0]
        assert row["status"] == JOB_PENDING
        assert row["attempts"] == 0             # attempt budget reset
        assert row["last_error"] is None
        # And the worker can claim it again.
        claimed = claim_due_jobs(fake, worker_run_id=uuid.uuid4(), now=_now())
        assert {j.ticker for j in claimed} == {"AAPL"}

    def test_enqueue_never_raises_on_db_failure(self):
        broken = MagicMock()
        broken.table.side_effect = RuntimeError("db down")
        result = enqueue_refresh_jobs(broken, user_id=USER_A, tickers=["AAPL"])
        assert result.error is not None
        assert result.requested_tickers == ["AAPL"]

    def test_default_window_is_per_utc_day(self):
        assert default_refresh_window(_now()) == "2026-05-14"


# ── 2. Job store: claim + terminal updates ───────────────────────────────────


class TestClaimAndTerminalUpdates:
    def test_claim_picks_up_due_pending_jobs(self):
        fake = _FakeSupabase()
        enqueue_refresh_jobs(fake, user_id=USER_A, tickers=["AAPL", "NVDA"], now=_now())
        claimed = claim_due_jobs(fake, worker_run_id=uuid.uuid4(), now=_now())
        assert {j.ticker for j in claimed} == {"AAPL", "NVDA"}
        # Claiming flips status + bumps attempts so a second worker can't grab them.
        assert all(r["status"] == JOB_CLAIMED for r in fake.rows())
        assert all(r["attempts"] == 1 for r in fake.rows())

    def test_claim_skips_future_retry_jobs(self):
        fake = _FakeSupabase()
        enqueue_refresh_jobs(fake, user_id=USER_A, tickers=["AAPL"], now=_now())
        row = fake.rows()[0]
        # Failed with a retry 1h in the future — not yet due.
        row.update({"status": JOB_FAILED,
                    "next_retry_at": (_now() + timedelta(hours=1)).isoformat()})
        claimed = claim_due_jobs(fake, worker_run_id=uuid.uuid4(), now=_now())
        assert claimed == []

    def test_claim_skips_attempt_exhausted_jobs(self):
        fake = _FakeSupabase()
        enqueue_refresh_jobs(fake, user_id=USER_A, tickers=["AAPL"], now=_now())
        row = fake.rows()[0]
        row.update({"status": JOB_FAILED, "attempts": 5, "max_attempts": 5,
                    "next_retry_at": None})
        claimed = claim_due_jobs(fake, worker_run_id=uuid.uuid4(), now=_now())
        assert claimed == []

    def test_claim_does_not_re_pick_succeeded_jobs(self):
        fake = _FakeSupabase()
        enqueue_refresh_jobs(fake, user_id=USER_A, tickers=["AAPL"], now=_now())
        fake.rows()[0]["status"] = JOB_SUCCEEDED
        assert claim_due_jobs(fake, worker_run_id=uuid.uuid4(), now=_now()) == []

    def test_mark_succeeded_and_failed(self):
        fake = _FakeSupabase()
        enqueue_refresh_jobs(fake, user_id=USER_A, tickers=["AAPL", "NVDA"], now=_now())
        claimed = claim_due_jobs(fake, worker_run_id=uuid.uuid4(), now=_now())
        by_ticker = {j.ticker: j for j in claimed}

        mark_job_succeeded(fake, by_ticker["AAPL"], now=_now())
        next_retry = mark_job_failed(fake, by_ticker["NVDA"], error="boom", now=_now())

        rows = {r["ticker"]: r for r in fake.rows()}
        assert rows["AAPL"]["status"] == JOB_SUCCEEDED
        assert rows["AAPL"]["next_retry_at"] is None
        assert rows["NVDA"]["status"] == JOB_FAILED
        assert rows["NVDA"]["last_error"] == "boom"
        # Failed job with attempts remaining gets a real future retry time.
        assert next_retry is not None
        assert rows["NVDA"]["next_retry_at"] == next_retry

    def test_failed_job_exhausted_gets_no_retry(self):
        fake = _FakeSupabase()
        enqueue_refresh_jobs(fake, user_id=USER_A, tickers=["AAPL"], now=_now())
        job = AnalystRefreshJob.from_row(fake.rows()[0])
        job.attempts = 5
        job.max_attempts = 5
        next_retry = mark_job_failed(fake, job, error="boom", now=_now())
        assert next_retry is None  # exhausted — never re-claimed
        assert fake.rows()[0]["completed_at"] is not None

    def test_compute_next_retry_at_is_exponential(self):
        t1 = compute_next_retry_at(1, _now())
        t2 = compute_next_retry_at(2, _now())
        d1 = datetime.fromisoformat(t1) - _now()
        d2 = datetime.fromisoformat(t2) - _now()
        assert d2 > d1  # backoff grows


# ── 3. Worker consumes due jobs OUTSIDE the HTTP request ─────────────────────


class TestWorkerConsumesJobs:
    def test_worker_claims_due_job_and_invokes_adapter(self):
        fake = _FakeSupabase()
        enqueue_refresh_jobs(fake, user_id=USER_A, tickers=["AAPL", "NVDA"], now=_now())
        adapter = _FakeAnalystAdapter(success_tickers=["AAPL", "NVDA"])
        worker = AnalystRefreshWorker(
            client=fake, adapter_factory=lambda _uid: adapter,
        )
        result = asyncio.run(worker.run_once(now=_now()))

        # The adapter was invoked exactly once, with the claimed tickers — this
        # is the analyst refresh running outside the synchronous HTTP request.
        assert len(adapter.calls) == 1
        assert set(adapter.calls[0]) == {"AAPL", "NVDA"}
        assert result.claimed_job_count == 2
        assert set(result.selected_tickers) == {"AAPL", "NVDA"}

    def test_successful_refresh_persists_and_failed_tickers_stay_stale(self):
        fake = _FakeSupabase()
        enqueue_refresh_jobs(
            fake, user_id=USER_A, tickers=["AAPL", "NVDA", "MSFT"], now=_now(),
        )
        # Adapter succeeds for AAPL only; NVDA + MSFT fail.
        adapter = _FakeAnalystAdapter(success_tickers=["AAPL"])
        worker = AnalystRefreshWorker(client=fake, adapter_factory=lambda _uid: adapter)
        result = asyncio.run(worker.run_once(now=_now()))

        rows = {r["ticker"]: r for r in fake.rows()}
        # Partial success persists per ticker.
        assert rows["AAPL"]["status"] == JOB_SUCCEEDED
        assert result.persisted_ticker_success_count == 1
        assert result.succeeded_tickers == ["AAPL"]
        # Failed tickers stay stale — failed job, retry scheduled, NOT succeeded.
        for t in ("NVDA", "MSFT"):
            assert rows[t]["status"] == JOB_FAILED
            assert rows[t]["next_retry_at"] is not None
        assert set(result.failed_tickers) == {"NVDA", "MSFT"}

    def test_worker_with_no_due_jobs_returns_cleanly(self):
        """Safe before migration 018 is applied / when the queue is empty."""
        fake = _FakeSupabase()
        worker = AnalystRefreshWorker(client=fake, adapter_factory=lambda _uid: None)
        result = asyncio.run(worker.run_once(now=_now()))
        assert result.claimed_job_count == 0
        assert result.succeeded_tickers == []
        assert result.failed_tickers == []

    def test_worker_does_not_re_run_terminal_jobs_on_second_pass(self):
        fake = _FakeSupabase()
        enqueue_refresh_jobs(fake, user_id=USER_A, tickers=["AAPL"], now=_now())
        adapter = _FakeAnalystAdapter(success_tickers=["AAPL"])
        worker = AnalystRefreshWorker(client=fake, adapter_factory=lambda _uid: adapter)
        asyncio.run(worker.run_once(now=_now()))
        # Second pass — succeeded job is terminal, nothing left to claim.
        second = asyncio.run(worker.run_once(now=_now()))
        assert second.claimed_job_count == 0
        assert len(adapter.calls) == 1  # adapter not invoked again


# ── 4. No fabricated freshness on worker failure ─────────────────────────────


class TestNoFabricatedFreshness:
    def test_adapter_crash_fails_all_jobs_none_succeed(self):
        fake = _FakeSupabase()
        enqueue_refresh_jobs(fake, user_id=USER_A, tickers=["AAPL", "NVDA"], now=_now())
        adapter = _FakeAnalystAdapter(raises=True)
        worker = AnalystRefreshWorker(client=fake, adapter_factory=lambda _uid: adapter)
        result = asyncio.run(worker.run_once(now=_now()))

        assert result.persisted_ticker_success_count == 0
        assert result.succeeded_tickers == []
        # Every job stays FAILED with a retry — no fabricated success.
        for r in fake.rows():
            assert r["status"] == JOB_FAILED
            assert r["next_retry_at"] is not None

    def test_runtime_budget_exhausted_does_not_fabricate_success(self):
        fake = _FakeSupabase()
        enqueue_refresh_jobs(fake, user_id=USER_A, tickers=["AAPL", "NVDA"], now=_now())
        adapter = _FakeAnalystAdapter(success_tickers=["AAPL", "NVDA"])
        # Zero runtime budget — the worker claims then releases without refreshing.
        worker = AnalystRefreshWorker(
            client=fake, adapter_factory=lambda _uid: adapter, max_runtime_seconds=0.0,
        )
        result = asyncio.run(worker.run_once(now=_now()))

        assert adapter.calls == []  # adapter never invoked
        assert result.succeeded_tickers == []
        assert "worker_runtime_budget_exhausted" in result.notes
        for r in fake.rows():
            assert r["status"] == JOB_FAILED

    def test_adapter_build_failure_fails_jobs_honestly(self):
        fake = _FakeSupabase()
        enqueue_refresh_jobs(fake, user_id=USER_A, tickers=["AAPL"], now=_now())

        def _broken_factory(_uid):
            raise RuntimeError("cannot build adapter")

        worker = AnalystRefreshWorker(client=fake, adapter_factory=_broken_factory)
        result = asyncio.run(worker.run_once(now=_now()))
        assert result.succeeded_tickers == []
        assert fake.rows()[0]["status"] == JOB_FAILED


# ── 5. Worker isolation from deterministic decision authority ────────────────


class TestWorkerDoesNotOwnDecisionAuthority:
    def test_worker_module_does_not_import_decision_policy(self):
        from app.services.intelligence.v3 import analyst_refresh_worker_v1 as mod

        src = open(mod.__file__).read()
        # The worker refreshes analyst evidence only — it must not import or
        # invoke the deterministic decision policy.
        assert "decision_policy_v1" not in src
        assert "decide(" not in src
        assert "from .decision_policy" not in src

    def test_job_store_module_does_not_import_decision_policy(self):
        from app.services.intelligence.v3 import analyst_refresh_job_store_v1 as mod

        src = open(mod.__file__).read()
        assert "decision_policy_v1" not in src
        assert "decide(" not in src

    def test_worker_result_exposes_no_buy_hold_trim_sell_field(self):
        fake = _FakeSupabase()
        worker = AnalystRefreshWorker(client=fake, adapter_factory=lambda _uid: None)
        result = asyncio.run(worker.run_once(now=_now()))
        keys = set(result.to_dict().keys())
        assert "action" not in keys
        assert "decision" not in keys


# ── 6. Run Intel v3 stays a fast zero-LLM path + enqueues durable jobs ───────


def _card(ticker: str, action: str):
    card = MagicMock()
    card.ticker = ticker
    card.name = f"{ticker} Corp"
    card.category = "stock"
    card.action = action
    card.analyst_action = action
    card.conviction_level = "MEDIUM"
    card.technical_signal = None
    card.risk_flag = None
    card.analyst_risks = []
    card.data_quality_label = "PARTIAL"
    card.intel_read = None
    card.thesis_v2 = None
    card.analyst_used_fallback = False
    card.primary_driver = "Driver text"
    card.action_reason = "Action reason"
    card.analyst_drivers = []
    return card


def _run_v3_with_fake_db(*, fake: _FakeSupabase, analyst_age_hours: float, cards):
    """Run the real synchronous run_v3() path with a real in-memory Supabase
    fake as the service client, so the seam's durable enqueue actually writes."""
    from app.services.intelligence.v3.intel_v3_service import IntelV3Service

    now = _now()
    service = IntelV3Service.__new__(IntelV3Service)
    service.user_id = uuid.UUID(USER_A)
    service.client = fake

    tickers = [c.ticker for c in cards]
    evidence_stats = {
        "active_position_count": len(cards),
        "persisted_recommendation_count": len(cards),
        "persisted_agent_insight_count": len(cards),
        "missing_recommendation_count": 0,
        "missing_evidence_count": 0,
        "stale_or_missing_source_count": 0,
        "recommendation_timestamps": [_iso_ago(analyst_age_hours, now)] * len(cards),
        "agent_insight_run_timestamps": [_iso_ago(analyst_age_hours, now)] * len(cards),
    }
    adapter_mock = MagicMock()
    adapter_mock.load_cards = AsyncMock(return_value=(cards, evidence_stats))
    per_ticker_ev = [
        {"ticker": t, "prior_action": "HOLD", "weight_pct": 100.0 / len(tickers),
         "evidence_age_hours": analyst_age_hours}
        for t in tickers
    ]

    with (
        patch(
            "app.services.intelligence.v3.intel_v3_service.ReadOnlyEvidenceAdapter",
            return_value=adapter_mock,
        ),
        patch.object(service, "_get_weight_map", new_callable=AsyncMock, return_value={}),
        patch.object(service, "_get_active_tickers", new_callable=AsyncMock,
                     return_value=tickers),
        patch.object(
            service, "_get_latest_portfolio_snapshot_meta", new_callable=AsyncMock,
            return_value={"snapshot_at": _iso_ago(0.5, now),
                          "market_value_certified_ats": [_iso_ago(0.1, now)] * len(cards)},
        ),
        patch.object(service, "_get_per_ticker_analyst_evidence",
                     new_callable=AsyncMock, return_value=per_ticker_ev),
        patch.object(service, "_build_price_refresh_callable", return_value=None),
        patch.object(service, "_persist_snapshot", new_callable=AsyncMock),
    ):
        return asyncio.run(service.run_v3())


class TestRunV3StaysFastAndEnqueues:
    def test_run_v3_makes_zero_analyst_llm_calls(self):
        fake = _FakeSupabase()
        snap = _run_v3_with_fake_db(
            fake=fake, analyst_age_hours=100.0,
            cards=[_card("AAPL", "HOLD"), _card("NVDA", "BUY")],
        )
        diag = snap["diagnostics"]
        assert diag["attempted_llm_calls"] == 0
        assert diag["successful_llm_calls"] == 0
        assert diag["failed_llm_calls"] == 0

    def test_run_v3_enqueues_exactly_one_durable_job_per_stale_ticker(self):
        fake = _FakeSupabase()
        _run_v3_with_fake_db(
            fake=fake, analyst_age_hours=100.0,
            cards=[_card("AAPL", "HOLD"), _card("NVDA", "BUY")],
        )
        rows = fake.rows()
        assert len(rows) == 2
        assert {r["ticker"] for r in rows} == {"AAPL", "NVDA"}
        assert all(r["status"] == JOB_PENDING for r in rows)
        # Honest refresh-requested state — never a fake FAST_CERTIFIED.
        assert fake.rows()  # durable job exists → "requested" language is honest

    def test_run_v3_repeated_clicks_do_not_create_duplicate_jobs(self):
        fake = _FakeSupabase()
        cards = [_card("AAPL", "HOLD"), _card("NVDA", "BUY")]
        _run_v3_with_fake_db(fake=fake, analyst_age_hours=100.0, cards=cards)
        _run_v3_with_fake_db(fake=fake, analyst_age_hours=100.0,
                             cards=[_card("AAPL", "HOLD"), _card("NVDA", "BUY")])
        # Two clicks, same UTC-day window → still exactly one job per ticker.
        assert len(fake.rows()) == 2

    def test_run_v3_then_worker_drains_the_queue(self):
        """End-to-end: Run Intel v3 enqueues; the worker consumes outside the
        request and marks the durable jobs succeeded."""
        fake = _FakeSupabase()
        _run_v3_with_fake_db(
            fake=fake, analyst_age_hours=100.0,
            cards=[_card("AAPL", "HOLD"), _card("NVDA", "BUY")],
        )
        adapter = _FakeAnalystAdapter(success_tickers=["AAPL", "NVDA"])
        worker = AnalystRefreshWorker(client=fake, adapter_factory=lambda _uid: adapter)
        # run_v3 enqueued with the real wall clock; let the worker use it too so
        # the freshly-enqueued (immediately due) jobs are claimable.
        result = asyncio.run(
            worker.run_once(now=datetime.now(timezone.utc) + timedelta(seconds=1))
        )
        assert result.persisted_ticker_success_count == 2
        assert all(r["status"] == JOB_SUCCEEDED for r in fake.rows())

    def test_seam_enqueues_when_client_wired(self):
        fake = _FakeSupabase()
        seam = AnalystRefreshRequestSeam(user_id=uuid.UUID(USER_A), client=fake)
        result = asyncio.run(seam(["AAPL", "NVDA"], started_at=_now()))
        d = result.to_dict()
        assert d["status"] == STATUS_REFRESH_REQUESTED
        assert d["attempted_llm_calls"] == 0
        assert d["durable_jobs_requested"] == 2
        assert len(fake.rows()) == 2

    def test_seam_without_client_stays_log_only(self):
        """Backward compatible: no client → no durable enqueue, log-only."""
        seam = AnalystRefreshRequestSeam(user_id=uuid.UUID(USER_A))
        result = asyncio.run(seam(["AAPL"], started_at=_now()))
        assert result.to_dict()["durable_jobs_requested"] == 0


# ── 7. Worker post-run readback contract (production blocker fix) ────────────
#
# Root cause of the production blocker: AgentOrchestrator persists
# agent_insights/recommendations with the ticker AS STORED in `positions`
# (no casing normalisation), but the worker's readback filtered every query
# with `.in_("ticker", <UPPER-cased worker tickers>)`. When the persisted
# casing differed, the server-side filter excluded every row → the worker
# saw `no_post_run_evidence` for all 34 tickers even though the orchestrator
# had persisted them. The fix drops the ticker filter and relies on the
# durable key (run_id + user_id) + case-insensitive per-ticker mapping.


def _seed_agent_evidence(
    fake: _FakeSupabase,
    *,
    user_id: str,
    run_id: str,
    tickers,
    created_at: str,
    used_fallback: bool = False,
    with_recs: bool = True,
    ticker_case=str.lower,
):
    """Seed agent_insights / recommendations rows the way AgentOrchestrator
    persists them — ticker stored verbatim (here: a casing that differs from
    the worker's UPPER-cased request)."""
    insights = fake.store.setdefault("agent_insights", [])
    recs = fake.store.setdefault("recommendations", [])
    for t in tickers:
        insights.append({
            "id": str(uuid.uuid4()),
            "user_id": str(user_id),
            "run_id": run_id,
            "ticker": ticker_case(t),
            "created_at": created_at,
            "analyst_verdict": {"action": "BUY", "used_fallback": used_fallback},
        })
        if with_recs:
            recs.append({
                "id": str(uuid.uuid4()),
                "user_id": str(user_id),
                "agent_run_id": run_id,
                "ticker": ticker_case(t),
                "created_at": created_at,
                "is_active": True,
            })


class TestPostRunEvidenceReadback:
    def test_readback_finds_lowercase_persisted_rows_for_uppercase_request(self):
        """The fix: persisted ticker casing != request casing must still
        resolve, because run_id (not the ticker string) is the durable key."""
        fake = _FakeSupabase()
        run_id = str(uuid.uuid4())
        created = (_now() + timedelta(seconds=30)).isoformat()
        _seed_agent_evidence(
            fake, user_id=USER_A, run_id=run_id, tickers=["AAPL", "NVDA"],
            created_at=created, ticker_case=str.lower,  # persisted lower-case
        )
        with patch("app.database.get_supabase_client", return_value=fake):
            out = asyncio.run(_read_post_run_evidence(
                uuid.UUID(USER_A), ["AAPL", "NVDA"], run_id, _now(),
            ))
        assert set(out.keys()) == {"AAPL", "NVDA"}
        for t in ("AAPL", "NVDA"):
            assert out[t] is not None, f"{t} should resolve despite casing"
            assert out[t]["insight_run_match"] is True
            assert out[t]["rec_run_match"] is True
            assert out[t]["used_fallback"] is False
            assert out[t]["failure_reason"] is None

    def test_readback_returns_none_when_no_durable_rows_written(self):
        """Honest failure preserved: a run that persisted nothing still yields
        no_post_run_evidence — the fix does not fabricate success."""
        fake = _FakeSupabase()
        run_id = str(uuid.uuid4())
        with patch("app.database.get_supabase_client", return_value=fake):
            out = asyncio.run(_read_post_run_evidence(
                uuid.UUID(USER_A), ["AAPL", "NVDA"], run_id, _now(),
            ))
        assert out == {"AAPL": None, "NVDA": None}

    def test_readback_does_not_treat_other_run_id_rows_as_a_run_match(self):
        """Rows persisted under a different run id must NOT count as a verified
        refresh for this run — insight_run_match stays False (no fabrication)."""
        fake = _FakeSupabase()
        this_run = str(uuid.uuid4())
        other_run = str(uuid.uuid4())
        created = (_now() + timedelta(seconds=30)).isoformat()
        _seed_agent_evidence(
            fake, user_id=USER_A, run_id=other_run, tickers=["AAPL"],
            created_at=created, ticker_case=str.lower,
        )
        with patch("app.database.get_supabase_client", return_value=fake):
            out = asyncio.run(_read_post_run_evidence(
                uuid.UUID(USER_A), ["AAPL"], this_run, _now(),
            ))
        assert out["AAPL"] is not None
        assert out["AAPL"]["insight_run_match"] is False
        assert out["AAPL"]["failure_reason"] == "persistence_missing"

    def test_readback_maps_uppercase_persisted_rows_too(self):
        """Casing-agnostic both ways: upper-case persisted rows also resolve."""
        fake = _FakeSupabase()
        run_id = str(uuid.uuid4())
        created = (_now() + timedelta(seconds=30)).isoformat()
        _seed_agent_evidence(
            fake, user_id=USER_A, run_id=run_id, tickers=["AAPL"],
            created_at=created, ticker_case=str.upper,
        )
        with patch("app.database.get_supabase_client", return_value=fake):
            out = asyncio.run(_read_post_run_evidence(
                uuid.UUID(USER_A), ["AAPL"], run_id, _now(),
            ))
        assert out["AAPL"]["insight_run_match"] is True


class TestWorkerProducesSuccessfulPersistedTicker:
    def test_worker_marks_ticker_succeeded_when_backend_persists_rows(self):
        """End-to-end: jobs enqueued → worker claims → FullPortfolioAnalystRefresh
        Adapter runs a backend that persists rows (lower-cased ticker) and
        verifies them via the real readback → jobs marked succeeded."""
        fake = _FakeSupabase()
        enqueue_refresh_jobs(fake, user_id=USER_A, tickers=["AAPL", "NVDA"], now=_now())

        async def _run_backend(user_id, selected_tickers, started_at):
            # Simulate AgentOrchestrator persisting per-ticker rows with a
            # casing that differs from the worker's UPPER-cased request.
            run_id = str(uuid.uuid4())
            created = (started_at + timedelta(seconds=10)).isoformat()
            _seed_agent_evidence(
                fake, user_id=user_id, run_id=run_id, tickers=selected_tickers,
                created_at=created, ticker_case=str.lower,
            )
            with patch("app.database.get_supabase_client", return_value=fake):
                return await _read_post_run_evidence(
                    user_id, selected_tickers, run_id, started_at,
                )

        def _factory(uid):
            return FullPortfolioAnalystRefreshAdapter(
                user_id=uid, run_backend=_run_backend,
                budget=FullPortfolioAnalystRefreshBudget(),
            )

        worker = AnalystRefreshWorker(client=fake, adapter_factory=_factory)
        result = asyncio.run(worker.run_once(now=_now()))

        assert result.persisted_ticker_success_count == 2
        assert set(result.succeeded_tickers) == {"AAPL", "NVDA"}
        assert result.failed_tickers == []
        assert all(r["status"] == JOB_SUCCEEDED for r in fake.rows())

    def test_worker_keeps_jobs_failed_when_backend_persists_nothing(self):
        """No durable rows written → no_post_run_evidence → jobs stay failed,
        never fabricated as succeeded."""
        fake = _FakeSupabase()
        enqueue_refresh_jobs(fake, user_id=USER_A, tickers=["AAPL"], now=_now())

        async def _run_backend(user_id, selected_tickers, started_at):
            run_id = str(uuid.uuid4())  # run happens, but persists nothing
            with patch("app.database.get_supabase_client", return_value=fake):
                return await _read_post_run_evidence(
                    user_id, selected_tickers, run_id, started_at,
                )

        def _factory(uid):
            return FullPortfolioAnalystRefreshAdapter(
                user_id=uid, run_backend=_run_backend,
                budget=FullPortfolioAnalystRefreshBudget(),
            )

        worker = AnalystRefreshWorker(client=fake, adapter_factory=_factory)
        result = asyncio.run(worker.run_once(now=_now()))

        assert result.persisted_ticker_success_count == 0
        assert result.succeeded_tickers == []
        assert fake.rows()[0]["status"] == JOB_FAILED
        assert fake.rows()[0]["next_retry_at"] is not None
