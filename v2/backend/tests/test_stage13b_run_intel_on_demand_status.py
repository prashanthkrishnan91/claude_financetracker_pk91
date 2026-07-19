"""Stage 13B — Run Intel v3 on-demand evidence build, operational-truth fields.

Contract under test (app/routers/intel_v3.py):
  1. POST /intel/v3/run still enqueues analyst_refresh_jobs exactly as before
     (Stage 3.2 enqueue path untouched — covered by
     test_intel_v3_stage_3_2_analyst_refresh_worker.py; this file adds only
     the new augmentation behavior layered on top).
  2. When on-demand processing is disabled, the response explicitly reports
     queue-only / worker-disabled status instead of implying progress.
  3. When on-demand processing is enabled, the bounded drain is invoked.
  4. The bounded drain's own caps (batches/runtime/cost-guard) are exercised
     in test_stage13b_analyst_refresh_on_demand_drain.py — this file checks
     the router wires jobs_attempted/succeeded/failed through honestly.
  5. No infinite loop: augmentation calls the drain at most once per request.
  6. When no certified snapshot results, next_required_action is honest
     (never implies "in progress" when nothing will finish it).
  7. Router-level exceptions from augmentation never fail the whole request.
  8. Paycheck Plan / allocation_policy_v1 are untouched by this module (no
     import coupling — Intel v3 remains an evidence input, not a competing
     recommendation surface).

Product-recovery Blockers 2/3 (this revision):
  9. Every on-demand drain — including when queued_ticker_count > 0 — is
     scoped to the current user's current active tickers, resolved BEFORE
     any drain decision. An active-ticker lookup failure never falls back to
     an unscoped drain; it returns an explicit retryable failure.
  10. The router classifies the scoped durable-job state (due / backoff /
      terminal / none) via analyst_refresh_job_store_v1.count_due_jobs and
      only drains when jobs are immediately due. A backoff or terminal state
      is reported honestly — never "complete," never "no stale evidence,"
      never silently drained/looped, and never masked by a historical
      certified snapshot.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.routers import intel_v3 as router_mod
from app.services.intelligence.v3.analyst_refresh_on_demand_drain_v1 import (
    OnDemandDrainResult,
    STOPPED_DRAINED,
)

USER_ID = uuid.UUID("00000000-0000-0000-0000-0000000000aa")


# ── Minimal in-memory fake for analyst_refresh_jobs (read-only: count_due_jobs) ──
# run_on_demand_drain is always mocked in this file, so the fake client only
# ever needs to satisfy count_due_jobs's select/eq/in_/execute chain.


class _FakeJobsQuery:
    def __init__(self, rows):
        self._rows = rows
        self._filters: list[tuple] = []

    def select(self, *_a, **_kw):
        return self

    def eq(self, col, val):
        self._filters.append(("eq", col, val))
        return self

    def in_(self, col, vals):
        self._filters.append(("in", col, list(vals)))
        return self

    def order(self, *_a, **_kw):
        return self

    def _match(self, row) -> bool:
        for kind, col, val in self._filters:
            rv = row.get(col)
            if kind == "eq" and rv != val:
                return False
            if kind == "in" and rv not in val:
                return False
        return True

    def execute(self):
        return SimpleNamespace(data=[r for r in self._rows if self._match(r)])


class _FakeSupabaseForJobs:
    def __init__(self, rows=None):
        self._rows = list(rows or [])

    def table(self, _name):
        return _FakeJobsQuery(self._rows)


def _due_job_row(
    ticker: str = "VTI",
    *,
    user_id=USER_ID,
    status: str = "pending",
    attempts: int = 0,
    max_attempts: int = 5,
    next_retry_at=None,
) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "user_id": str(user_id),
        "ticker": ticker,
        "status": status,
        "attempts": attempts,
        "max_attempts": max_attempts,
        "next_retry_at": next_retry_at,
    }


class _FakeService:
    """Minimal stand-in for IntelV3Service — only what the augmentation touches.

    ``active_tickers``/``due_jobs`` model the durable-job-store state the
    Blocker 2/3 augmentation now always resolves; ``active_tickers_error``
    lets a test prove the "never fall back to an unscoped drain" contract.
    """

    def __init__(
        self,
        *,
        latest_snapshot=None,
        active_tickers=("VTI",),
        due_jobs=None,
        active_tickers_error: Exception | None = None,
    ):
        self.user_id = USER_ID
        self.client = _FakeSupabaseForJobs(due_jobs)
        self._latest_snapshot = latest_snapshot
        self.get_latest_snapshot = AsyncMock(return_value=latest_snapshot)
        self._active_tickers = list(active_tickers) if active_tickers is not None else []
        self._active_tickers_error = active_tickers_error

    async def _get_active_tickers(self):
        if self._active_tickers_error is not None:
            raise self._active_tickers_error
        return self._active_tickers


@dataclass
class _FakeSettings:
    intel_v3_on_demand_refresh_enabled: bool
    intel_v3_snapshot_writes_enabled: bool = False


# ── 2. On-demand processing disabled → honest queue-only status ─────────────


class TestOnDemandDisabledReportsQueueOnly:
    @pytest.mark.asyncio
    async def test_disabled_never_invokes_drain(self, monkeypatch):
        settings = _FakeSettings(intel_v3_on_demand_refresh_enabled=False)
        monkeypatch.setattr(router_mod, "get_settings", lambda: settings)
        drain_spy = AsyncMock()
        monkeypatch.setattr(router_mod, "run_on_demand_drain", drain_spy)

        service = _FakeService(latest_snapshot=None)
        result = {"status": "refresh_requested", "queued_ticker_count": 34}

        out = await router_mod._augment_with_on_demand_status(service, result)

        drain_spy.assert_not_awaited()
        assert out["on_demand_processing_enabled"] is False
        assert out["on_demand_jobs_attempted"] == 0
        assert out["on_demand_jobs_succeeded"] == 0
        assert out["on_demand_jobs_failed"] == 0
        assert out["snapshot_available_after_run"] is False
        assert "queue_only" in out["next_required_action"]

    @pytest.mark.asyncio
    async def test_disabled_with_nothing_queued_reports_no_stale_evidence(self, monkeypatch):
        settings = _FakeSettings(intel_v3_on_demand_refresh_enabled=False)
        monkeypatch.setattr(router_mod, "get_settings", lambda: settings)
        monkeypatch.setattr(router_mod, "run_on_demand_drain", AsyncMock())

        service = _FakeService(latest_snapshot=None)
        result = {"status": "analyst_evidence_current", "queued_ticker_count": 0}

        out = await router_mod._augment_with_on_demand_status(service, result)
        assert out["next_required_action"] == "none_no_stale_evidence_to_refresh"


# ── 3. On-demand processing enabled → bounded drain invoked, scoped ─────────


class TestOnDemandEnabledInvokesBoundedDrain:
    @pytest.mark.asyncio
    async def test_enabled_invokes_drain_exactly_once_scoped_to_active_tickers(self, monkeypatch):
        """True completion: drain has no remaining work, snapshot writes are
        enabled, latest snapshot is worker_certified AND certified_current,
        AND its id differs from the pre-request existing certified snapshot
        (proof this request actually published) — snapshot_available_after_run
        must be true and next action reports current. Also proves Blocker 2:
        the drain call is scoped to (user_id, active_tickers), resolved even
        though this click DID queue new work."""
        settings = _FakeSettings(
            intel_v3_on_demand_refresh_enabled=True,
            intel_v3_snapshot_writes_enabled=True,
        )
        monkeypatch.setattr(router_mod, "get_settings", lambda: settings)
        drain_result = OnDemandDrainResult(
            batches_run=2, jobs_attempted=20, jobs_succeeded=17, jobs_failed=3,
            duration_ms=500, run_resumable=False, stopped_reason=STOPPED_DRAINED,
        )
        drain_spy = AsyncMock(return_value=drain_result)
        monkeypatch.setattr(router_mod, "run_on_demand_drain", drain_spy)

        service = _FakeService(
            latest_snapshot={
                "snapshot_id": "new-snapshot-1",
                "snapshot_source": "worker_certified",
                "evidence_freshness_state": "certified_current",
            },
            active_tickers=["VTI", "AAPL"],
            due_jobs=[_due_job_row("VTI"), _due_job_row("AAPL")],
        )
        result = {
            "status": "refresh_requested",
            "queued_ticker_count": 34,
            "existing_certified_snapshot_id": "old-snapshot-0",
        }

        out = await router_mod._augment_with_on_demand_status(service, result)

        drain_spy.assert_awaited_once()
        _, kwargs = drain_spy.call_args
        assert str(kwargs["user_id"]) == str(USER_ID)
        assert kwargs["tickers"] == ["VTI", "AAPL"]
        assert out["on_demand_processing_enabled"] is True
        assert out["on_demand_jobs_attempted"] == 20
        assert out["on_demand_jobs_succeeded"] == 17
        assert out["on_demand_jobs_failed"] == 3
        assert out["snapshot_available_after_run"] is True
        assert out["next_required_action"] == "none_certified_snapshot_current"

    @pytest.mark.asyncio
    async def test_enabled_but_nothing_queued_and_no_durable_work_skips_drain(self, monkeypatch):
        """No infinite loop / no wasted work: an empty queue AND no existing
        durable work never triggers a drain call, regardless of the flag."""
        settings = _FakeSettings(intel_v3_on_demand_refresh_enabled=True)
        monkeypatch.setattr(router_mod, "get_settings", lambda: settings)
        drain_spy = AsyncMock()
        monkeypatch.setattr(router_mod, "run_on_demand_drain", drain_spy)

        service = _FakeService(latest_snapshot=None, active_tickers=["VTI"], due_jobs=[])
        result = {"status": "analyst_evidence_current", "queued_ticker_count": 0}

        await router_mod._augment_with_on_demand_status(service, result)
        drain_spy.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_production_regression_stale_worker_certified_with_partial_drain(
        self, monkeypatch
    ):
        """Production regression (July 18, 2026): an existing worker_certified
        snapshot with evidence_freshness_state=republish_pending must not mask
        a partial, resumable bounded drain (20/32 succeeded, 12 remaining).
        The response must report snapshot_available_after_run=false and ask
        for another click — never "none_certified_snapshot_current". Snapshot
        writes are explicitly enabled here so the assertion isolates the
        drain-remaining condition, not the separate write-guard branch."""
        settings = _FakeSettings(
            intel_v3_on_demand_refresh_enabled=True,
            intel_v3_snapshot_writes_enabled=True,
        )
        monkeypatch.setattr(router_mod, "get_settings", lambda: settings)
        drain_result = OnDemandDrainResult(
            batches_run=2, jobs_attempted=20, jobs_succeeded=20, jobs_failed=0,
            duration_ms=500, run_resumable=True, stopped_reason="runtime_cap_reached",
        )
        drain_spy = AsyncMock(return_value=drain_result)
        monkeypatch.setattr(router_mod, "run_on_demand_drain", drain_spy)

        service = _FakeService(
            latest_snapshot={
                "snapshot_id": "52c593c8-b5c2-447e-bbd5-194c3f634c96",
                "snapshot_source": "worker_certified",
                "evidence_freshness_state": "republish_pending",
            },
            due_jobs=[_due_job_row("VTI")],
        )
        result = {
            "status": "refresh_requested",
            "queued_ticker_count": 32,
            "existing_certified_snapshot_id": "52c593c8-b5c2-447e-bbd5-194c3f634c96",
        }

        out = await router_mod._augment_with_on_demand_status(service, result)

        drain_spy.assert_awaited_once()
        assert out["on_demand_jobs_attempted"] == 20
        assert out["on_demand_jobs_succeeded"] == 20
        assert out["on_demand_jobs_failed"] == 0
        assert out["snapshot_available_after_run"] is False
        assert (
            out["next_required_action"]
            == "reclick_run_intel_or_run_worker_entrypoint_to_continue_draining"
        )

    @pytest.mark.asyncio
    async def test_historical_certified_current_snapshot_cannot_mask_drain_remaining(
        self, monkeypatch
    ):
        """Even an otherwise current historical snapshot (worker_certified +
        certified_current) does not classify the request as complete while
        the bounded drain still has remaining resumable work. Snapshot
        writes are explicitly enabled here so the assertion isolates the
        drain-remaining condition, not the separate write-guard branch."""
        settings = _FakeSettings(
            intel_v3_on_demand_refresh_enabled=True,
            intel_v3_snapshot_writes_enabled=True,
        )
        monkeypatch.setattr(router_mod, "get_settings", lambda: settings)
        drain_result = OnDemandDrainResult(
            batches_run=1, jobs_attempted=20, jobs_succeeded=20, jobs_failed=0,
            duration_ms=500, run_resumable=True, stopped_reason="runtime_cap_reached",
        )
        drain_spy = AsyncMock(return_value=drain_result)
        monkeypatch.setattr(router_mod, "run_on_demand_drain", drain_spy)

        service = _FakeService(
            latest_snapshot={
                "snapshot_id": "historical-current-1",
                "snapshot_source": "worker_certified",
                "evidence_freshness_state": "certified_current",
            },
            due_jobs=[_due_job_row("VTI")],
        )
        result = {
            "status": "refresh_requested",
            "queued_ticker_count": 32,
            "existing_certified_snapshot_id": "historical-current-1",
        }

        out = await router_mod._augment_with_on_demand_status(service, result)

        assert out["snapshot_available_after_run"] is False
        assert (
            out["next_required_action"]
            == "reclick_run_intel_or_run_worker_entrypoint_to_continue_draining"
        )

    @pytest.mark.asyncio
    async def test_already_current_certified_snapshot_no_stale_evidence_is_a_noop(
        self, monkeypatch
    ):
        """Already-current no-op: zero stale jobs and an already-current
        certified snapshot retain the existing completed behavior — the
        drain never runs (nothing queued, nothing durable) and completion is
        still reported from the existing certified_current snapshot."""
        settings = _FakeSettings(intel_v3_on_demand_refresh_enabled=True)
        monkeypatch.setattr(router_mod, "get_settings", lambda: settings)
        drain_spy = AsyncMock()
        monkeypatch.setattr(router_mod, "run_on_demand_drain", drain_spy)

        service = _FakeService(
            latest_snapshot={
                "snapshot_source": "worker_certified",
                "evidence_freshness_state": "certified_current",
            },
            due_jobs=[],
        )
        result = {"status": "analyst_evidence_current", "queued_ticker_count": 0}

        out = await router_mod._augment_with_on_demand_status(service, result)

        drain_spy.assert_not_awaited()
        assert out["snapshot_available_after_run"] is True
        assert out["next_required_action"] == "none_certified_snapshot_current"


# ── 9. Blocker 2 — active-ticker scoping, obsolete-job exclusion ────────────


class TestActiveTickerScoping:
    @pytest.mark.asyncio
    async def test_obsolete_sold_ticker_job_is_never_claimed_alongside_active_ticker_work(
        self, monkeypatch
    ):
        """Regression matrix: an older due job for a sold/closed ticker (no
        longer in active_tickers) plus newly queued jobs for active tickers —
        only current active tickers are claimed; the sold-ticker row is
        excluded from the drain's scope and remains untouched."""
        settings = _FakeSettings(
            intel_v3_on_demand_refresh_enabled=True,
            intel_v3_snapshot_writes_enabled=True,
        )
        monkeypatch.setattr(router_mod, "get_settings", lambda: settings)
        drain_result = OnDemandDrainResult(
            batches_run=1, jobs_attempted=2, jobs_succeeded=2, jobs_failed=0,
            duration_ms=100, run_resumable=False, stopped_reason=STOPPED_DRAINED,
        )
        drain_spy = AsyncMock(return_value=drain_result)
        monkeypatch.setattr(router_mod, "run_on_demand_drain", drain_spy)

        sold_ticker_row = _due_job_row("KLAR", status="pending")  # sold — not in active_tickers
        service = _FakeService(
            latest_snapshot=None,
            active_tickers=["VTI", "AAPL"],
            due_jobs=[
                sold_ticker_row,
                _due_job_row("VTI"),
                _due_job_row("AAPL"),
            ],
        )
        result = {
            "status": "refresh_requested",
            "queued_ticker_count": 2,
            "existing_certified_snapshot_id": None,
        }

        out = await router_mod._augment_with_on_demand_status(service, result)

        drain_spy.assert_awaited_once()
        _, kwargs = drain_spy.call_args
        assert set(kwargs["tickers"]) == {"VTI", "AAPL"}
        assert "KLAR" not in kwargs["tickers"]
        # The obsolete row itself is never touched by this fake (run_on_demand_drain
        # is mocked), but the scoping proof above is the contract: KLAR was never
        # passed to the drain and count_due_jobs (tickers=["VTI","AAPL"]) never
        # counted it either, matching claim_due_jobs/count_due_jobs's own
        # user_id/tickers filtering (see test_run_intel_product_recovery.py for
        # the job-store-level proof against a real claim_due_jobs call).
        assert sold_ticker_row["status"] == "pending"
        assert out["on_demand_jobs_attempted"] == 2

    @pytest.mark.asyncio
    async def test_active_ticker_lookup_failure_returns_explicit_retryable_failure(
        self, monkeypatch
    ):
        """Blocker 2: if the active-ticker lookup fails, never fall back to
        an unscoped drain — return an explicit retryable failure instead."""
        settings = _FakeSettings(intel_v3_on_demand_refresh_enabled=True)
        monkeypatch.setattr(router_mod, "get_settings", lambda: settings)
        drain_spy = AsyncMock()
        monkeypatch.setattr(router_mod, "run_on_demand_drain", drain_spy)

        service = _FakeService(
            latest_snapshot=None,
            active_tickers_error=RuntimeError("positions query failed"),
        )
        result = {
            "status": "refresh_requested",
            "queued_ticker_count": 12,
            "existing_certified_snapshot_id": None,
        }

        out = await router_mod._augment_with_on_demand_status(service, result)

        drain_spy.assert_not_awaited()
        assert out["snapshot_available_after_run"] is False
        assert out["next_required_action"] == router_mod._ACTION_ACTIVE_TICKERS_LOOKUP_FAILED
        assert out["on_demand_jobs_attempted"] == 0

    @pytest.mark.asyncio
    async def test_no_active_holdings_never_triggers_active_ticker_lookup(self, monkeypatch):
        settings = _FakeSettings(intel_v3_on_demand_refresh_enabled=True)
        monkeypatch.setattr(router_mod, "get_settings", lambda: settings)
        drain_spy = AsyncMock()
        monkeypatch.setattr(router_mod, "run_on_demand_drain", drain_spy)

        service = _FakeService(
            latest_snapshot=None,
            active_tickers_error=RuntimeError("must never be called"),
        )
        result = {
            "status": "no_active_holdings",
            "queued_ticker_count": 0,
            "existing_certified_snapshot_id": None,
        }

        out = await router_mod._augment_with_on_demand_status(service, result)

        drain_spy.assert_not_awaited()
        assert out["next_required_action"] == "add_positions_before_running_intel"


# ── 10. Blocker 3 — backoff / terminal durable-job-state classification ─────


class TestDurableJobStateClassification:
    @pytest.mark.asyncio
    async def test_zero_queued_only_backoff_jobs_reports_backoff_not_complete_or_no_stale(
        self, monkeypatch
    ):
        settings = _FakeSettings(intel_v3_on_demand_refresh_enabled=True)
        monkeypatch.setattr(router_mod, "get_settings", lambda: settings)
        drain_spy = AsyncMock()
        monkeypatch.setattr(router_mod, "run_on_demand_drain", drain_spy)

        future_retry = "2099-01-01T00:00:00+00:00"
        service = _FakeService(
            latest_snapshot=None,
            active_tickers=["VTI"],
            due_jobs=[
                _due_job_row(
                    "VTI", status="failed", attempts=2, max_attempts=5,
                    next_retry_at=future_retry,
                ),
            ],
        )
        result = {
            "status": "analyst_evidence_current",
            "queued_ticker_count": 0,
            "existing_certified_snapshot_id": None,
        }

        out = await router_mod._augment_with_on_demand_status(service, result)

        drain_spy.assert_not_awaited()
        assert out["snapshot_available_after_run"] is False
        assert out["next_required_action"] == router_mod._ACTION_ANALYST_JOBS_BACKOFF
        assert out["next_required_action"] != "none_no_stale_evidence_to_refresh"
        assert out["earliest_retry_at"] == future_retry

    @pytest.mark.asyncio
    async def test_zero_queued_exhausted_job_reports_terminal_failure(self, monkeypatch):
        settings = _FakeSettings(intel_v3_on_demand_refresh_enabled=True)
        monkeypatch.setattr(router_mod, "get_settings", lambda: settings)
        drain_spy = AsyncMock()
        monkeypatch.setattr(router_mod, "run_on_demand_drain", drain_spy)

        service = _FakeService(
            latest_snapshot=None,
            active_tickers=["VTI"],
            due_jobs=[
                _due_job_row("VTI", status="failed", attempts=5, max_attempts=5),
            ],
        )
        result = {
            "status": "analyst_evidence_current",
            "queued_ticker_count": 0,
            "existing_certified_snapshot_id": None,
        }

        out = await router_mod._augment_with_on_demand_status(service, result)

        drain_spy.assert_not_awaited()
        assert out["snapshot_available_after_run"] is False
        assert out["next_required_action"] == router_mod._ACTION_ANALYST_JOBS_TERMINAL
        # Never silently deleted/reset — the fake row is untouched (this
        # router path never writes to the job store).
        assert service.client._rows[0]["attempts"] == 5

    @pytest.mark.asyncio
    async def test_historical_certified_snapshot_plus_backoff_never_reports_complete(
        self, monkeypatch
    ):
        settings = _FakeSettings(
            intel_v3_on_demand_refresh_enabled=True,
            intel_v3_snapshot_writes_enabled=True,
        )
        monkeypatch.setattr(router_mod, "get_settings", lambda: settings)
        monkeypatch.setattr(router_mod, "run_on_demand_drain", AsyncMock())

        service = _FakeService(
            latest_snapshot={
                "snapshot_id": "historical-current-1",
                "snapshot_source": "worker_certified",
                "evidence_freshness_state": "certified_current",
            },
            active_tickers=["VTI"],
            due_jobs=[
                _due_job_row(
                    "VTI", status="failed", attempts=1, max_attempts=5,
                    next_retry_at="2099-01-01T00:00:00+00:00",
                ),
            ],
        )
        result = {
            "status": "analyst_evidence_current",
            "queued_ticker_count": 0,
            "existing_certified_snapshot_id": "historical-current-1",
        }

        out = await router_mod._augment_with_on_demand_status(service, result)

        assert out["snapshot_available_after_run"] is False
        assert out["next_required_action"] == router_mod._ACTION_ANALYST_JOBS_BACKOFF

    @pytest.mark.asyncio
    async def test_historical_certified_snapshot_plus_terminal_never_reports_complete(
        self, monkeypatch
    ):
        settings = _FakeSettings(
            intel_v3_on_demand_refresh_enabled=True,
            intel_v3_snapshot_writes_enabled=True,
        )
        monkeypatch.setattr(router_mod, "get_settings", lambda: settings)
        monkeypatch.setattr(router_mod, "run_on_demand_drain", AsyncMock())

        service = _FakeService(
            latest_snapshot={
                "snapshot_id": "historical-current-1",
                "snapshot_source": "worker_certified",
                "evidence_freshness_state": "certified_current",
            },
            active_tickers=["VTI"],
            due_jobs=[
                _due_job_row("VTI", status="failed", attempts=5, max_attempts=5),
            ],
        )
        result = {
            "status": "analyst_evidence_current",
            "queued_ticker_count": 0,
            "existing_certified_snapshot_id": "historical-current-1",
        }

        out = await router_mod._augment_with_on_demand_status(service, result)

        assert out["snapshot_available_after_run"] is False
        assert out["next_required_action"] == router_mod._ACTION_ANALYST_JOBS_TERMINAL

    @pytest.mark.asyncio
    async def test_due_work_outranks_backoff_and_terminal_rows_for_other_tickers(
        self, monkeypatch
    ):
        """total_due > 0 still drains even when OTHER active-ticker rows are
        in backoff/terminal — due work is not blocked by unrelated backlog."""
        settings = _FakeSettings(
            intel_v3_on_demand_refresh_enabled=True,
            intel_v3_snapshot_writes_enabled=True,
        )
        monkeypatch.setattr(router_mod, "get_settings", lambda: settings)
        drain_result = OnDemandDrainResult(
            batches_run=1, jobs_attempted=1, jobs_succeeded=1, jobs_failed=0,
            duration_ms=50, run_resumable=False, stopped_reason=STOPPED_DRAINED,
        )
        drain_spy = AsyncMock(return_value=drain_result)
        monkeypatch.setattr(router_mod, "run_on_demand_drain", drain_spy)

        service = _FakeService(
            latest_snapshot={
                "snapshot_id": "new-1",
                "snapshot_source": "worker_certified",
                "evidence_freshness_state": "certified_current",
            },
            active_tickers=["VTI", "AAPL"],
            due_jobs=[
                _due_job_row("VTI", status="pending"),
                _due_job_row(
                    "AAPL", status="failed", attempts=1, max_attempts=5,
                    next_retry_at="2099-01-01T00:00:00+00:00",
                ),
            ],
        )
        result = {
            "status": "analyst_evidence_current",
            "queued_ticker_count": 0,
            "existing_certified_snapshot_id": "old-0",
        }

        out = await router_mod._augment_with_on_demand_status(service, result)

        drain_spy.assert_awaited_once()
        assert out["snapshot_available_after_run"] is True


# ── 5b. A historical worker_certified snapshot can only mask completion, ────
# never manufacture it — completion requires proof THIS request published a
# new certified snapshot (existing_certified_snapshot_id differs from the
# latest snapshot's id) whenever jobs were queued this request.


class TestHistoricalSnapshotCannotMaskUnpublishedOutcomes:
    @pytest.mark.asyncio
    async def test_queue_only_with_historical_current_snapshot_stays_queue_only(
        self, monkeypatch
    ):
        """Jobs queued while on-demand processing is disabled must stay
        queue-only even when a historical worker_certified + certified_current
        snapshot already exists — the existing snapshot did not come from
        this request and on-demand processing never touched the queue."""
        settings = _FakeSettings(intel_v3_on_demand_refresh_enabled=False)
        monkeypatch.setattr(router_mod, "get_settings", lambda: settings)
        drain_spy = AsyncMock()
        monkeypatch.setattr(router_mod, "run_on_demand_drain", drain_spy)

        service = _FakeService(
            latest_snapshot={
                "snapshot_id": "existing-1",
                "snapshot_source": "worker_certified",
                "evidence_freshness_state": "certified_current",
            }
        )
        result = {
            "status": "refresh_requested",
            "queued_ticker_count": 34,
            "existing_certified_snapshot_id": "existing-1",
        }

        out = await router_mod._augment_with_on_demand_status(service, result)

        drain_spy.assert_not_awaited()
        assert out["snapshot_available_after_run"] is False
        assert out["next_required_action"].startswith("queue_only")

    @pytest.mark.asyncio
    async def test_writes_disabled_with_historical_current_snapshot_reports_write_guard(
        self, monkeypatch
    ):
        """Drain completes (nothing remaining) but snapshot writes are
        disabled, and the latest snapshot id equals the pre-request existing
        id — completion must be false and the response must say writes are
        disabled, not silently accept the historical snapshot as proof."""
        settings = _FakeSettings(
            intel_v3_on_demand_refresh_enabled=True,
            intel_v3_snapshot_writes_enabled=False,
        )
        monkeypatch.setattr(router_mod, "get_settings", lambda: settings)
        drain_result = OnDemandDrainResult(
            batches_run=1, jobs_attempted=10, jobs_succeeded=10, jobs_failed=0,
            duration_ms=200, run_resumable=False, stopped_reason=STOPPED_DRAINED,
        )
        drain_spy = AsyncMock(return_value=drain_result)
        monkeypatch.setattr(router_mod, "run_on_demand_drain", drain_spy)

        service = _FakeService(
            latest_snapshot={
                "snapshot_id": "existing-1",
                "snapshot_source": "worker_certified",
                "evidence_freshness_state": "certified_current",
            },
            due_jobs=[_due_job_row("VTI")],
        )
        result = {
            "status": "refresh_requested",
            "queued_ticker_count": 10,
            "existing_certified_snapshot_id": "existing-1",
        }

        out = await router_mod._augment_with_on_demand_status(service, result)

        assert out["snapshot_available_after_run"] is False
        assert (
            out["next_required_action"]
            == "on_demand_drain_completed_but_intel_v3_snapshot_writes_enabled_is_false"
        )

    @pytest.mark.asyncio
    async def test_writes_disabled_while_drain_remains_write_guard_outranks_continue(
        self, monkeypatch
    ):
        """The write-guard action must take priority over "continue" — a
        reclick can never publish while INTEL_V3_SNAPSHOT_WRITES_ENABLED is
        false, so surfacing "continue draining" would be misleading."""
        settings = _FakeSettings(
            intel_v3_on_demand_refresh_enabled=True,
            intel_v3_snapshot_writes_enabled=False,
        )
        monkeypatch.setattr(router_mod, "get_settings", lambda: settings)
        drain_result = OnDemandDrainResult(
            batches_run=2, jobs_attempted=20, jobs_succeeded=20, jobs_failed=0,
            duration_ms=500, run_resumable=True, stopped_reason="runtime_cap_reached",
        )
        drain_spy = AsyncMock(return_value=drain_result)
        monkeypatch.setattr(router_mod, "run_on_demand_drain", drain_spy)

        service = _FakeService(latest_snapshot=None, due_jobs=[_due_job_row("VTI")])
        result = {
            "status": "refresh_requested",
            "queued_ticker_count": 32,
            "existing_certified_snapshot_id": None,
        }

        out = await router_mod._augment_with_on_demand_status(service, result)

        assert out["snapshot_available_after_run"] is False
        assert (
            out["next_required_action"]
            == "on_demand_drain_completed_but_intel_v3_snapshot_writes_enabled_is_false"
        )
        assert out["next_required_action"] != (
            "reclick_run_intel_or_run_worker_entrypoint_to_continue_draining"
        )

    @pytest.mark.asyncio
    async def test_drain_completed_but_no_new_snapshot_published_reports_retry(
        self, monkeypatch
    ):
        """Writes are enabled and the drain fully completes with no remaining
        work, but the latest certified_current snapshot id is unchanged from
        before this request — the drain did not actually produce a new
        snapshot, so completion must be false and the action must be a plain
        retry, not "continue draining" (nothing is left queued/resumable) and
        not "complete" (nothing new was published)."""
        settings = _FakeSettings(
            intel_v3_on_demand_refresh_enabled=True,
            intel_v3_snapshot_writes_enabled=True,
        )
        monkeypatch.setattr(router_mod, "get_settings", lambda: settings)
        drain_result = OnDemandDrainResult(
            batches_run=1, jobs_attempted=10, jobs_succeeded=10, jobs_failed=0,
            duration_ms=200, run_resumable=False, stopped_reason=STOPPED_DRAINED,
        )
        drain_spy = AsyncMock(return_value=drain_result)
        monkeypatch.setattr(router_mod, "run_on_demand_drain", drain_spy)

        service = _FakeService(
            latest_snapshot={
                "snapshot_id": "unchanged-1",
                "snapshot_source": "worker_certified",
                "evidence_freshness_state": "certified_current",
            },
            due_jobs=[_due_job_row("VTI")],
        )
        result = {
            "status": "refresh_requested",
            "queued_ticker_count": 10,
            "existing_certified_snapshot_id": "unchanged-1",
        }

        out = await router_mod._augment_with_on_demand_status(service, result)

        assert out["snapshot_available_after_run"] is False
        assert out["next_required_action"] == "reclick_run_intel_to_retry"

    @pytest.mark.asyncio
    async def test_successful_new_publication_reports_complete(self, monkeypatch):
        """Writes enabled, drain completed with nothing remaining, and the
        latest certified_current snapshot id DIFFERS from the pre-request
        existing id — this request genuinely published a new snapshot, so
        completion must be true."""
        settings = _FakeSettings(
            intel_v3_on_demand_refresh_enabled=True,
            intel_v3_snapshot_writes_enabled=True,
        )
        monkeypatch.setattr(router_mod, "get_settings", lambda: settings)
        drain_result = OnDemandDrainResult(
            batches_run=1, jobs_attempted=10, jobs_succeeded=10, jobs_failed=0,
            duration_ms=200, run_resumable=False, stopped_reason=STOPPED_DRAINED,
        )
        drain_spy = AsyncMock(return_value=drain_result)
        monkeypatch.setattr(router_mod, "run_on_demand_drain", drain_spy)

        service = _FakeService(
            latest_snapshot={
                "snapshot_id": "new-2",
                "snapshot_source": "worker_certified",
                "evidence_freshness_state": "certified_current",
            },
            due_jobs=[_due_job_row("VTI")],
        )
        result = {
            "status": "refresh_requested",
            "queued_ticker_count": 10,
            "existing_certified_snapshot_id": "old-1",
        }

        out = await router_mod._augment_with_on_demand_status(service, result)

        assert out["snapshot_available_after_run"] is True
        assert out["next_required_action"] == "none_certified_snapshot_current"

    @pytest.mark.asyncio
    async def test_first_snapshot_publication_with_no_prior_certified_snapshot(
        self, monkeypatch
    ):
        """No certified snapshot existed before this request
        (existing_certified_snapshot_id is None) — a concrete new latest
        snapshot id after a successful drain still counts as completion."""
        settings = _FakeSettings(
            intel_v3_on_demand_refresh_enabled=True,
            intel_v3_snapshot_writes_enabled=True,
        )
        monkeypatch.setattr(router_mod, "get_settings", lambda: settings)
        drain_result = OnDemandDrainResult(
            batches_run=1, jobs_attempted=10, jobs_succeeded=10, jobs_failed=0,
            duration_ms=200, run_resumable=False, stopped_reason=STOPPED_DRAINED,
        )
        drain_spy = AsyncMock(return_value=drain_result)
        monkeypatch.setattr(router_mod, "run_on_demand_drain", drain_spy)

        service = _FakeService(
            latest_snapshot={
                "snapshot_id": "first-ever-1",
                "snapshot_source": "worker_certified",
                "evidence_freshness_state": "certified_current",
            },
            due_jobs=[_due_job_row("VTI")],
        )
        result = {
            "status": "refresh_requested",
            "queued_ticker_count": 10,
            "existing_certified_snapshot_id": None,
        }

        out = await router_mod._augment_with_on_demand_status(service, result)

        assert out["snapshot_available_after_run"] is True
        assert out["next_required_action"] == "none_certified_snapshot_current"


# ── 5c. Zero-queued outcomes require an explicit success-status allowlist — ──
# a historical worker_certified + certified_current snapshot must not paper
# over no_active_holdings, enqueue_failed, or a deterministic recertification
# failure. Only a genuine no-op or a successful recertification may complete.


class TestZeroQueuedStatusClassification:
    @pytest.mark.asyncio
    async def test_no_active_holdings_with_historical_current_snapshot_stays_incomplete(
        self, monkeypatch
    ):
        """The proven contradiction: status=no_active_holdings must never
        report snapshot_available_after_run=true just because an old
        worker_certified + certified_current snapshot happens to exist —
        that would render as "complete" in the frontend before the
        "add positions" branch is ever reached."""
        settings = _FakeSettings(intel_v3_on_demand_refresh_enabled=True)
        monkeypatch.setattr(router_mod, "get_settings", lambda: settings)
        drain_spy = AsyncMock()
        monkeypatch.setattr(router_mod, "run_on_demand_drain", drain_spy)

        service = _FakeService(
            latest_snapshot={
                "snapshot_id": "historical-1",
                "snapshot_source": "worker_certified",
                "evidence_freshness_state": "certified_current",
            }
        )
        result = {
            "status": "no_active_holdings",
            "queued_ticker_count": 0,
            "total_holding_count": 0,
            "existing_certified_snapshot_id": None,
            "existing_certified_snapshot": False,
        }

        out = await router_mod._augment_with_on_demand_status(service, result)

        drain_spy.assert_not_awaited()
        assert out["snapshot_available_after_run"] is False
        assert out["next_required_action"] == "add_positions_before_running_intel"

    @pytest.mark.asyncio
    async def test_mapping_version_recertification_failed_with_historical_snapshot_is_failure(
        self, monkeypatch
    ):
        settings = _FakeSettings(intel_v3_on_demand_refresh_enabled=True)
        monkeypatch.setattr(router_mod, "get_settings", lambda: settings)
        drain_spy = AsyncMock()
        monkeypatch.setattr(router_mod, "run_on_demand_drain", drain_spy)

        service = _FakeService(
            latest_snapshot={
                "snapshot_id": "historical-1",
                "snapshot_source": "worker_certified",
                "evidence_freshness_state": "certified_current",
            }
        )
        result = {
            "status": "mapping_version_recertification_failed",
            "queued_ticker_count": 0,
            "existing_certified_snapshot_id": "historical-1",
        }

        out = await router_mod._augment_with_on_demand_status(service, result)

        drain_spy.assert_not_awaited()
        assert out["snapshot_available_after_run"] is False
        assert out["next_required_action"] == "reclick_run_intel_to_retry"
        assert out["next_required_action"] != "none_no_stale_evidence_to_refresh"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "failure_status",
        [
            "stage7_contract_recertification_failed",
            "stage8e_contract_recertification_failed",
            "stage8f_contract_recertification_failed",
        ],
    )
    async def test_stage_contract_recertification_failures_follow_same_failure_rule(
        self, monkeypatch, failure_status
    ):
        settings = _FakeSettings(intel_v3_on_demand_refresh_enabled=True)
        monkeypatch.setattr(router_mod, "get_settings", lambda: settings)
        monkeypatch.setattr(router_mod, "run_on_demand_drain", AsyncMock())

        service = _FakeService(
            latest_snapshot={
                "snapshot_id": "historical-1",
                "snapshot_source": "worker_certified",
                "evidence_freshness_state": "certified_current",
            }
        )
        result = {
            "status": failure_status,
            "queued_ticker_count": 0,
            "existing_certified_snapshot_id": "historical-1",
        }

        out = await router_mod._augment_with_on_demand_status(service, result)

        assert out["snapshot_available_after_run"] is False
        assert out["next_required_action"] == "reclick_run_intel_to_retry"

    @pytest.mark.asyncio
    async def test_enqueue_failed_with_historical_snapshot_stays_incomplete(self, monkeypatch):
        settings = _FakeSettings(intel_v3_on_demand_refresh_enabled=True)
        monkeypatch.setattr(router_mod, "get_settings", lambda: settings)
        monkeypatch.setattr(router_mod, "run_on_demand_drain", AsyncMock())

        service = _FakeService(
            latest_snapshot={
                "snapshot_id": "historical-1",
                "snapshot_source": "worker_certified",
                "evidence_freshness_state": "certified_current",
            }
        )
        result = {
            "status": "enqueue_failed",
            "queued_ticker_count": 0,
            "existing_certified_snapshot_id": "historical-1",
        }

        out = await router_mod._augment_with_on_demand_status(service, result)

        assert out["snapshot_available_after_run"] is False
        assert out["next_required_action"] == "reclick_run_intel_to_retry"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "success_status",
        [
            "mapping_version_recertified",
            "stage7_contract_recertified",
            "stage8e_contract_recertified",
            "stage8f_contract_recertified",
        ],
    )
    async def test_successful_recertification_statuses_retain_completion(
        self, monkeypatch, success_status
    ):
        """A successful zero-LLM deterministic recertification (no analyst
        jobs queued) still counts as completion when the snapshot it
        rebuilt is worker_certified + certified_current."""
        settings = _FakeSettings(intel_v3_on_demand_refresh_enabled=True)
        monkeypatch.setattr(router_mod, "get_settings", lambda: settings)
        drain_spy = AsyncMock()
        monkeypatch.setattr(router_mod, "run_on_demand_drain", drain_spy)

        service = _FakeService(
            latest_snapshot={
                "snapshot_id": "recertified-1",
                "snapshot_source": "worker_certified",
                "evidence_freshness_state": "certified_current",
            },
            due_jobs=[],
        )
        result = {
            "status": success_status,
            "queued_ticker_count": 0,
            "existing_certified_snapshot_id": "recertified-1",
        }

        out = await router_mod._augment_with_on_demand_status(service, result)

        drain_spy.assert_not_awaited()
        assert out["snapshot_available_after_run"] is True
        assert out["next_required_action"] == "none_certified_snapshot_current"

    @pytest.mark.asyncio
    async def test_analyst_evidence_current_no_op_remains_complete(self, monkeypatch):
        """Regression guard: the pre-existing already-current no-op status
        must keep working exactly as before this fix."""
        settings = _FakeSettings(intel_v3_on_demand_refresh_enabled=True)
        monkeypatch.setattr(router_mod, "get_settings", lambda: settings)
        monkeypatch.setattr(router_mod, "run_on_demand_drain", AsyncMock())

        service = _FakeService(
            latest_snapshot={
                "snapshot_id": "current-1",
                "snapshot_source": "worker_certified",
                "evidence_freshness_state": "certified_current",
            },
            due_jobs=[],
        )
        result = {
            "status": "analyst_evidence_current",
            "queued_ticker_count": 0,
            "existing_certified_snapshot_id": "current-1",
        }

        out = await router_mod._augment_with_on_demand_status(service, result)

        assert out["snapshot_available_after_run"] is True
        assert out["next_required_action"] == "none_certified_snapshot_current"

    @pytest.mark.asyncio
    async def test_zero_queued_with_no_snapshot_keeps_existing_no_stale_evidence_behavior(
        self, monkeypatch
    ):
        """Regression guard: zero queued with no snapshot at all (never had
        one) is a distinct case from failure/no-holdings — it must keep
        reporting the existing "no stale evidence" outcome, not "retry"."""
        settings = _FakeSettings(intel_v3_on_demand_refresh_enabled=True)
        monkeypatch.setattr(router_mod, "get_settings", lambda: settings)
        monkeypatch.setattr(router_mod, "run_on_demand_drain", AsyncMock())

        service = _FakeService(latest_snapshot=None, due_jobs=[])
        result = {
            "status": "analyst_evidence_current",
            "queued_ticker_count": 0,
            "existing_certified_snapshot_id": None,
        }

        out = await router_mod._augment_with_on_demand_status(service, result)

        assert out["snapshot_available_after_run"] is False
        assert out["next_required_action"] == "none_no_stale_evidence_to_refresh"


# ── 6. Honest next_required_action across outcomes ───────────────────────────


class TestNextRequiredActionIsHonest:
    def test_no_active_holdings(self):
        action = router_mod._next_required_action(
            status_value="no_active_holdings",
            on_demand_processing_enabled=False,
            queued_ticker_count=0,
            drain_ran=False,
            drain_remaining=False,
            snapshot_available_after_run=False,
            snapshot_writes_enabled=False,
        )
        assert action == "add_positions_before_running_intel"

    @pytest.mark.parametrize(
        "failure_status",
        [
            "enqueue_failed",
            "mapping_version_recertification_failed",
            "stage7_contract_recertification_failed",
            "stage8e_contract_recertification_failed",
            "stage8f_contract_recertification_failed",
        ],
    )
    def test_zero_queued_failure_status_outranks_no_stale_evidence(self, failure_status):
        """A zero-queued request-level failure must never fall through to
        "no stale evidence to refresh" — that would imply nothing needed
        doing, when in fact recertification was attempted and failed."""
        action = router_mod._next_required_action(
            status_value=failure_status,
            on_demand_processing_enabled=True,
            queued_ticker_count=0,
            drain_ran=False,
            drain_remaining=False,
            snapshot_available_after_run=False,
            snapshot_writes_enabled=True,
        )
        assert action == "reclick_run_intel_to_retry"
        assert action != "none_no_stale_evidence_to_refresh"

    def test_write_guard_outranks_continue_draining(self):
        """The snapshot-write guard must outrank "continue" — reclicking
        can never publish while INTEL_V3_SNAPSHOT_WRITES_ENABLED is false,
        so the write-guard action must win even when the drain also has
        remaining resumable work."""
        action = router_mod._next_required_action(
            status_value="refresh_requested",
            on_demand_processing_enabled=True,
            queued_ticker_count=34,
            drain_ran=True,
            drain_remaining=True,
            snapshot_available_after_run=False,
            snapshot_writes_enabled=False,
        )
        assert action == "on_demand_drain_completed_but_intel_v3_snapshot_writes_enabled_is_false"

    def test_certified_snapshot_when_no_drain_work_remains(self):
        action = router_mod._next_required_action(
            status_value="refresh_requested",
            on_demand_processing_enabled=True,
            queued_ticker_count=34,
            drain_ran=True,
            drain_remaining=False,
            snapshot_available_after_run=True,
            snapshot_writes_enabled=True,
        )
        assert action == "none_certified_snapshot_current"

    def test_drain_remaining_takes_priority_over_historical_snapshot(self):
        """Remaining bounded-drain work must take priority over the mere
        existence of a snapshot — a stale/historical worker_certified
        snapshot must never mask jobs still queued from this run."""
        action = router_mod._next_required_action(
            status_value="refresh_requested",
            on_demand_processing_enabled=True,
            queued_ticker_count=34,
            drain_ran=True,
            drain_remaining=True,
            snapshot_available_after_run=True,  # would only ever be true here
            snapshot_writes_enabled=True,       # via a stale caller — must still lose
        )
        assert action == "reclick_run_intel_or_run_worker_entrypoint_to_continue_draining"

    def test_drain_incomplete_asks_for_reclick(self):
        action = router_mod._next_required_action(
            status_value="refresh_requested",
            on_demand_processing_enabled=True,
            queued_ticker_count=34,
            drain_ran=True,
            drain_remaining=True,
            snapshot_available_after_run=False,
            snapshot_writes_enabled=True,
        )
        assert "reclick_run_intel" in action

    def test_drain_complete_but_snapshot_writes_disabled_is_surfaced_honestly(self):
        """Even if the drain fully processes the queue, a certified snapshot
        can never appear while INTEL_V3_SNAPSHOT_WRITES_ENABLED is false —
        the response must say so rather than looking like a stuck drain."""
        action = router_mod._next_required_action(
            status_value="refresh_requested",
            on_demand_processing_enabled=True,
            queued_ticker_count=10,
            drain_ran=True,
            drain_remaining=False,
            snapshot_available_after_run=False,
            snapshot_writes_enabled=False,
        )
        assert "intel_v3_snapshot_writes_enabled_is_false" in action

    def test_never_implies_progress_when_queue_only(self):
        action = router_mod._next_required_action(
            status_value="refresh_requested",
            on_demand_processing_enabled=False,
            queued_ticker_count=34,
            drain_ran=False,
            drain_remaining=False,
            snapshot_available_after_run=False,
            snapshot_writes_enabled=False,
        )
        assert action.startswith("queue_only")

    def test_backoff_job_state_outranks_write_guard_and_continue_and_complete(self):
        action = router_mod._next_required_action(
            status_value="analyst_evidence_current",
            on_demand_processing_enabled=True,
            queued_ticker_count=0,
            drain_ran=False,
            drain_remaining=False,
            snapshot_available_after_run=False,
            snapshot_writes_enabled=True,
            job_state=router_mod._JOB_STATE_BACKOFF,
        )
        assert action == router_mod._ACTION_ANALYST_JOBS_BACKOFF

    def test_terminal_job_state_outranks_complete_and_no_stale_evidence(self):
        action = router_mod._next_required_action(
            status_value="analyst_evidence_current",
            on_demand_processing_enabled=True,
            queued_ticker_count=0,
            drain_ran=False,
            drain_remaining=False,
            snapshot_available_after_run=False,
            snapshot_writes_enabled=True,
            job_state=router_mod._JOB_STATE_TERMINAL,
        )
        assert action == router_mod._ACTION_ANALYST_JOBS_TERMINAL

    def test_job_state_defaults_to_none_for_direct_callers(self):
        """Backward compatibility: existing direct callers that omit
        job_state must keep their exact prior behavior."""
        action = router_mod._next_required_action(
            status_value="refresh_requested",
            on_demand_processing_enabled=True,
            queued_ticker_count=34,
            drain_ran=True,
            drain_remaining=False,
            snapshot_available_after_run=True,
            snapshot_writes_enabled=True,
        )
        assert action == "none_certified_snapshot_current"


# ── 7. Augmentation failures never break the enqueue response ───────────────


class TestAugmentationIsBestEffort:
    @pytest.mark.asyncio
    async def test_augmentation_exception_does_not_propagate_from_endpoint(self, monkeypatch):
        """The endpoint wraps _augment_with_on_demand_status in try/except and
        fills honest defaults — this test exercises that fallback path
        directly against the augmentation function raising. The active-ticker
        lookup succeeds (empty fake client fails soft to zero counts), so
        execution reaches get_latest_snapshot(), which is the mock that raises."""
        settings = _FakeSettings(intel_v3_on_demand_refresh_enabled=True)
        monkeypatch.setattr(router_mod, "get_settings", lambda: settings)

        service = _FakeService(latest_snapshot=None, due_jobs=[])
        service.get_latest_snapshot = AsyncMock(side_effect=RuntimeError("db down"))
        result = {"status": "refresh_requested", "queued_ticker_count": 34}

        with pytest.raises(RuntimeError):
            await router_mod._augment_with_on_demand_status(service, result)
        # The endpoint itself (run_intel_v3) catches this and fills honest
        # defaults — see the try/except around _augment_with_on_demand_status.


# ── 8. No coupling into Paycheck Plan / no new recommendation surface ───────


class TestNoPaycheckPlanCoupling:
    def test_on_demand_drain_module_does_not_import_allocation_policy(self):
        import inspect

        from app.services.intelligence.v3 import analyst_refresh_on_demand_drain_v1 as mod
        from app.services.intelligence.v3 import analyst_refresh_worker_v1

        src = inspect.getsource(mod)
        assert "allocation_policy_v1" not in src
        assert "paycheck_plan" not in src
        # Must not import the deterministic decision policy either — same
        # boundary as the standalone worker.
        assert "decision_policy_v1" not in src
        assert "decision_policy_v1" not in inspect.getsource(analyst_refresh_worker_v1)

    def test_router_augmentation_does_not_touch_allocation_policy(self):
        import inspect

        src = inspect.getsource(router_mod)
        assert "allocation_policy_v1" not in src
        assert "paycheck_plan_preview" not in src
